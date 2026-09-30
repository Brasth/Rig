#!/usr/bin/env python3
"""Small, offline correctness checks, not latency assertions or a performance gate."""
import hashlib
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from unittest.mock import patch

import benchmark_workflows as bench


class RuntimeBenchmark(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def fixture(self, history=0):
        fixture = bench.Fixture(self.root / "fixture")
        fixture.seed(history)
        return fixture

    def run_cli(self, *args):
        output = self.root / "report.json"
        result = subprocess.run(
            [sys.executable, str(bench.ROOT / "tests" / "benchmark_workflows.py"),
             "--history-sizes", "0,2", "--samples", "2", "--warmup", "1",
             "--output", str(output), *args],
            cwd=bench.ROOT, text=True, capture_output=True, timeout=45,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Saved", result.stdout)
        return json.loads(output.read_text())

    def assert_distribution(self, row, samples=2, warmup=1):
        self.assertEqual(row["sample_count"], samples)
        self.assertEqual(row["warmup_count"], warmup)
        self.assertEqual(len(row["samples_ms"]), samples)
        self.assertEqual(len(row["samples_payload_bytes"]), samples)
        self.assertGreater(row["payload_bytes"]["min"], 0)
        self.assertGreaterEqual(row["min_ms"], 0)
        self.assertLessEqual(row["min_ms"], row["median_ms"])
        self.assertLessEqual(row["median_ms"], row["max_ms"])
        self.assertEqual(row["p95_ms"], bench.nearest_rank(row["samples_ms"]))

    def test_measure_excludes_warmup_and_counts_utf8_bytes(self):
        calls = []

        def operation():
            calls.append(1)
            return "é"

        row = bench.measure(self.fixture(), operation, warmup=2, samples=3)
        self.assert_distribution(row, samples=3, warmup=2)
        self.assertEqual(len(calls), 5)
        self.assertEqual(row["samples_payload_bytes"], [2, 2, 2])
        self.assertEqual(bench.nearest_rank(list(range(1, 21))), 19)

    def test_in_process_isolation_blocks_process_network_and_host_environment(self):
        fixture = self.fixture()
        original_popen, original_socket = subprocess.Popen, socket.socket
        with patch.dict(os.environ, {"OPENAI_API_KEY": "synthetic-secret", "RIG_JOB_ID": "host-job"}):
            with fixture.isolated():
                self.assertNotIn("OPENAI_API_KEY", os.environ)
                self.assertNotIn("RIG_JOB_ID", os.environ)
                self.assertEqual(os.environ["HOME"], str(fixture.home))
                self.assertEqual(os.environ["RIG_HOME"], str(fixture.home / "rig"))
                for operation in (
                    lambda: subprocess.Popen(["grok"]), socket.socket,
                    lambda: socket.getaddrinfo("example.invalid", 443),
                    lambda: bench.catalog.probe_worker("grok"),
                    lambda: bench.jobs.time.sleep(1),
                ):
                    with self.subTest(operation=operation), self.assertRaisesRegex(RuntimeError, "isolation"):
                        operation()
            self.assertEqual(os.environ["OPENAI_API_KEY"], "synthetic-secret")
        self.assertIs(subprocess.Popen, original_popen)
        self.assertIs(socket.socket, original_socket)

    def test_runtime_surfaces_are_read_only_with_synthetic_job_history(self):
        fixture = self.fixture(3)
        fixture.seed_jobs(3)

        def snapshot():
            return {str(path.relative_to(fixture.repo)): hashlib.sha256(path.read_bytes()).hexdigest()
                    for path in fixture.repo.rglob("*") if path.is_file()}

        before = snapshot()
        with fixture.isolated():
            status = bench.runtime_function(fixture, "status")()
            listing = bench.runtime_function(fixture, "jobs")()
            routing = bench.runtime_function(fixture, "routing")()
        self.assertIn("jobs: 4", status)
        self.assertEqual(len(listing), 4)
        self.assertEqual(routing["worker"], "grok")
        self.assertEqual(snapshot(), before)

    def test_startup_reuses_runner_guards_without_forwarding_host_secrets(self):
        fixture = self.fixture()
        with patch.dict(os.environ, {"OPENAI_API_KEY": "synthetic-secret", "RIG_JOB_ID": "host-job"}):
            env = bench.startup_environment(fixture)
        self.assertNotIn("OPENAI_API_KEY", env)
        self.assertNotIn("RIG_JOB_ID", env)
        self.assertEqual(env["PATH"], str(fixture.bins))
        self.assertEqual(env["HOME"], str(fixture.home))
        self.assertEqual(env["GIT_ALLOW_PROTOCOL"], "file")
        self.assertEqual((Path(env["PYTHONPATH"]) / "sitecustomize.py").read_text(),
                         bench.run_tests.PYTHON_GUARD)
        # These attempted calls never reach a socket or child process. Auditing
        # preserves socket's class shape, so ordinary ssl imports still work.
        code = bench.STARTUP_GUARD + '''
import os, ssl
attempts = [lambda: subprocess.Popen(["grok"]), socket.socket,
            lambda: socket.getaddrinfo("example.invalid", 443),
            lambda: os.system("grok")]
for attempt in attempts:
    try:
        attempt()
    except RuntimeError as error:
        assert "isolation" in str(error)
    else:
        raise AssertionError("process/network guard escaped")
print(os.getpid())
'''
        result = subprocess.run([sys.executable, "-B", "-s", "-c", code], env=env,
                                cwd=fixture.repo, text=True, capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotEqual(int(result.stdout), os.getpid())

    def test_default_cli_preserves_scenario_ids_and_opt_in(self):
        report = self.run_cli()
        self.assertEqual(report["schema_version"], 1)
        self.assertEqual(report["parameters"]["surfaces"], ["session", "workflows"])
        self.assertEqual([row["id"] for row in report["scenarios"]], [
            "session/n=0/compact-json", "workflows/n=0",
            "session/n=2/compact-json", "workflows/n=2",
        ])
        self.assertIsNone(report["comparison"])
        self.assertIsNone(report["within_threshold"])
        for row in report["scenarios"]:
            self.assert_distribution(row)
            self.assertEqual(row["fixture_job_count"], 1)

    def test_opt_in_cli_reports_all_surfaces_and_true_startup_scope(self):
        report = self.run_cli("--surfaces", ",".join(bench.SURFACES))
        self.assertEqual(len(report["scenarios"]), 12)
        self.assertEqual(set(report["parameters"]["surfaces"]), set(bench.SURFACES))
        self.assertIn("scripts/route.py", report["production_sha256"])
        self.assertIn("tests/run_tests.py", report["evaluation_sha256"])
        self.assertIn("teardown", report["measurement_scope"]["startup"])
        self.assertIn("not flushed", report["measurement_scope"]["startup_cache"])
        self.assertIn("model inference", " ".join(report["measurement_scope"]["unmeasured"]))
        for row in report["scenarios"]:
            self.assert_distribution(row)
            if row["surface"] == "startup":
                self.assertEqual(row["measurement_mode"], "fresh-process-initialize-and-teardown")
            if row["surface"] in {"status", "jobs", "routing"}:
                self.assertEqual(row["fixture_job_count"], row["history_size"] + 1)

    def test_invalid_arguments_fail_before_measurements_or_provenance(self):
        cases = [
            ["--samples", "0"], ["--warmup", "-1"], ["--surfaces", ""],
            ["--surfaces", "provider"], ["--history-sizes", "-1"],
            ["--threshold", "nan"], ["--threshold", "inf"],
            ["--surfaces", "jobs", "--history-sizes", str(bench.MAX_RUNTIME_HISTORY + 1)],
            ["--surfaces", "jobs", "--history-sizes", ",".join(map(str, range(11)))],
            ["--surfaces", "startup", "--samples", str(bench.MAX_RUNTIME_ITERATIONS)],
            ["--surfaces", ",".join(bench.SURFACES), "--samples", "600"],
        ]
        for args in cases:
            with self.subTest(args=args), patch.object(bench, "provenance") as provenance, \
                    redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
                bench.main(["--output", str(self.root / "bad.json"), *args])
            self.assertEqual(error.exception.code, 2)
            provenance.assert_not_called()
        self.assertFalse((self.root / "bad.json").exists())

    def test_startup_rejects_invalid_response_and_sets_bounded_timeout(self):
        fixture = self.fixture()
        response = subprocess.CompletedProcess([], 0, stdout='{"id": 1, "result": {}}', stderr="")
        with patch.object(bench, "startup_environment", return_value={}), \
                patch.object(subprocess, "run", return_value=response) as run, \
                self.assertRaisesRegex(RuntimeError, "initialize response"):
            bench.measure_startup(fixture, warmup=0, samples=1)
        self.assertEqual(run.call_args.kwargs["timeout"], bench.STARTUP_TIMEOUT)
        self.assertEqual(run.call_args.args[0],
                         [sys.executable, "-B", "-s", "-c", bench.STARTUP_BOOTSTRAP])
        self.assertEqual(run.call_args.kwargs["input"], bench.STARTUP_REQUEST)

    def test_explicit_comparison_does_not_invent_new_surface_baselines(self):
        current = [{"id": "jobs/n=2", "median_ms": 2, "p95_ms": 4},
                   {"id": "startup/n=2/fresh-python-mcp-initialize", "median_ms": 9, "p95_ms": 10}]
        baseline = {"scenarios": [{"id": "jobs/n=2", "median_ms": 1, "p95_ms": 2}]}
        rows, regressions = bench.comparison(current, baseline, threshold=0.2)
        self.assertEqual(len(regressions), 1)
        self.assertFalse(rows[0]["within_threshold"])
        self.assertFalse(rows[1]["baseline_available"])
        self.assertIn("No savings claimed", rows[1]["note"])


if __name__ == "__main__":
    unittest.main()
