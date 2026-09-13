#!/usr/bin/env python3
"""Measure local harness overhead only; never launches vendor processes or networks."""
from __future__ import annotations

import argparse
import hashlib
import inspect
import json
import math
import os
import platform
import socket
import statistics
import subprocess
import sys
import tempfile
import time
from collections import Counter
from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import ask
import catalog
import harness
import jobs
import rig_mcp
import route
import change_evidence

CORPUS = Path(__file__).with_name("fixtures") / "normal_prompt_cases.json"
BRIEF = "# Task\nFix the synthetic fixture handler.\n\n# Files\n- src/example.py\n"
LOG = '{"type":"text","text":"Checking the synthetic fixture handler."}\n' * 4
SESSION_MIX = ("ok",) * 6 + ("fail", "cancelled", "running", "ask")
WAIT_MIX = ("ok", "ok", "ok", "fail", "cancelled")
PRODUCTION = ("scripts/route.py", "scripts/jobs.py", "scripts/harness.py",
              "scripts/rig_mcp.py", "scripts/work_queue.py", "scripts/catalog.py", "bin/rig",
              "scripts/admission.py", "scripts/change_evidence.py", "scripts/verification.py")
COUNT_KEYS = ("directory_scans", "job_directory_scans", "load_job_calls", "catalog_probes",
              "catalog_refresh_schedules", "subject_hashes", "max_hashes_per_subject")


def routing_observations():
    """Record declared parent roles separately from deterministic English fallback."""
    rows = json.loads(CORPUS.read_text())
    observations = []
    for row in rows:
        observations.append({**row, "observed_kind": route.classify(row["role"], row["case"]),
                             "parent_semantic_role": row["expected_kind"],
                             "parent_role_result": route.classify(row["expected_kind"], row["case"])})
    return observations


def native_mini_observation():
    choice = route.pick("codex", [], "mini", "Documentation only", catalogs={})
    adapter = ROOT / "adapters" / "codex" / "agents" / (choice["native_agent"] + ".toml")
    import tomllib
    spec = tomllib.loads(adapter.read_text())
    return {"choice": choice, "adapter": str(adapter.relative_to(ROOT)),
            "sandbox_mode": spec["sandbox_mode"], "adapter_model": spec["model"],
            "write_capable": spec["sandbox_mode"] == "workspace-write"}


def seed_job(repo, name, status, *, labeled=True):
    folder = repo / ".rig" / "jobs" / name
    folder.mkdir(parents=True)
    meta = {"job_id": name, "worker": "codex", "role": "implement",
            "status": "running" if status == "ask" else status,
            "pid": os.getpid() if status in {"running", "ask"} else None,
            "model": "gpt-5.6-luna", "effort": "low", "elapsed_s": 7,
            "task": "Fix the synthetic fixture handler.", "doing": "Checking fixture",
            "summary": "Synthetic fixture result", "thread": "benchmark-thread",
            "files": ["src/example.py"] if labeled else []}
    (folder / "meta.json").write_text(json.dumps(meta))
    (folder / "brief.md").write_text(BRIEF)
    (folder / "stdout.log").write_text(LOG)
    if status == "ask":
        ask.write_ask(folder, "Bash", {"command": "git status"}, "fixture-ask")
    return folder


class Fixture:
    """Real on-disk inputs plus deterministic external-service isolation."""
    def __init__(self, repo):
        repo = repo.resolve()
        self.repo, self.counts, self.probed = repo, Counter(), []
        self.subject_paths, self.subject_reads = set(), Counter()
        self.accepted_clock = None
        self.cache = repo / "model-catalogs.json"
        self.bins = repo / "bins"
        self.bins.mkdir()
        (repo / ".git").mkdir()
        (repo / ".rig" / "jobs").mkdir(parents=True)
        (repo / ".rig" / "queue").mkdir()
        (repo / ".rig" / "MEMORY.md").write_text("# Memory\n\n- Synthetic fixture fact.\n")
        flags = []
        for worker in harness.WORKERS:
            enabled = worker in catalog.CATALOG_WORKERS
            flags.append(f"{worker} = {'true' if enabled else 'false'}")
            if enabled:
                binary = self.bins / worker
                binary.write_text("#!/bin/sh\nexit 97\n")
                binary.chmod(0o755)
        (repo / ".rig" / "harness.toml").write_text('parent = "codex"\n[workers]\n' + "\n".join(flags))

    def seed(self, history, width=0, *, surface="session"):
        mix = WAIT_MIX if surface == "wait" else SESSION_MIX
        for index in range(history):
            seed_job(self.repo, f"history-{index:06d}", mix[index % len(mix)], labeled=index % 10 != 8)
        names = [f"target-{index:06d}" for index in range(width)]
        for name in names:
            seed_job(self.repo, name, "running")
        return names

    def seed_accepted(self, history, layout):
        """Synthetic accepted sidecars exercise real readers; no model-quality claim."""
        self.accepted_clock = time.time()
        stamp = datetime.fromtimestamp(self.accepted_clock - 1, timezone.utc).isoformat()
        criteria = ["Synthetic benchmark content inspected"]
        for index in range(history):
            name = f"accepted-{index:06d}"
            subject = self.repo / "subjects" / ("shared.txt" if layout == "shared" else f"{index:06d}.txt")
            subject.parent.mkdir(exist_ok=True)
            subject.write_text("Synthetic accepted content.\n")
            self.subject_paths.add(str(subject))
            folder = seed_job(self.repo, name, "ok")
            meta = json.loads((folder / "meta.json").read_text())
            meta.update(files=[str(subject.relative_to(self.repo))], execution_mode="parent",
                        ownership_established=True, reservation_id="reservation-" + name,
                        attempt_id="attempt-" + name, ended_at=stamp)
            change_evidence.write_json(folder / "meta.json", meta)
            change_evidence.write_json(folder / "requirements.json", {
                "version": 1, "requirements": [], "manual_criteria": criteria})
            change_evidence.write_json(folder / "verification.json", {
                "version": 1, "state": "verified", "acceptance": "accepted", "method": "manual",
                "snapshot_id": change_evidence.snapshot(self.repo, meta["files"])["snapshot_id"],
                "rationale": criteria[0], "manual_criteria": criteria, "check_ids": [],
                "accepted_at": stamp, "reservation_id": meta["reservation_id"], "attempt_id": meta["attempt_id"]})

    @contextmanager
    def isolated(self):
        real_iterdir, real_load = Path.iterdir, jobs.load_job
        real_open = os.open
        def open_subject(path, *args, **kwargs):
            if str(path) in self.subject_paths:
                self.subject_reads[str(path)] += 1
                self.counts["subject_hashes"] += 1
                self.counts["max_hashes_per_subject"] = max(self.subject_reads.values())
            return real_open(path, *args, **kwargs)
        def scan(path):
            self.counts["directory_scans"] += 1
            if path == self.repo / ".rig" / "jobs":
                self.counts["job_directory_scans"] += 1
            return real_iterdir(path)
        def load(path):
            self.counts["load_job_calls"] += 1
            return real_load(path)
        def probe(worker, timeout=catalog.PROBE_TIMEOUT):
            self.counts["catalog_probes"] += 1
            self.probed.append(worker)
            return [route.model_for(worker, "review")[0]]
        def refresh(worker, timeout):
            self.counts["catalog_refresh_schedules"] += 1
            self.probed.append("refresh:" + worker)
        def forbidden(*args, **kwargs):
            raise RuntimeError("benchmark setup error: a process/network call escaped isolation")
        env = {"PATH": str(self.bins), "RIG_PARENT": "codex", "RIG_THREAD": "benchmark-thread",
               "RIG_MODEL_CATALOG_CACHE": str(self.cache), "RIG_REFRESH_MODELS": "0",
               "RIG_SKIP_MODEL_CATALOG": "0", "RIG_JOB_ID": "", "RIG_JOB_DIR": "",
               "CLAUDECODE": "", "CLAUDE_CODE": ""}
        with ExitStack() as stack:
            stack.enter_context(patch.dict(os.environ, env))
            stack.enter_context(patch.object(Path, "iterdir", scan))
            stack.enter_context(patch.object(jobs, "load_job", load))
            stack.enter_context(patch.object(os, "open", open_subject))
            if self.accepted_clock is not None:
                stack.enter_context(patch.object(time, "time", lambda: self.accepted_clock))
            stack.enter_context(patch.object(catalog, "probe_worker", probe))
            stack.enter_context(patch.object(catalog, "_schedule_refresh", refresh))
            stack.enter_context(patch.object(subprocess, "Popen", forbidden))
            stack.enter_context(patch.object(socket, "socket", forbidden))
            stack.enter_context(patch.object(jobs.time, "sleep", forbidden))
            yield self

    def prepare_catalog(self, mode):
        os.environ["RIG_SKIP_MODEL_CATALOG"] = "1" if mode == "static" else "0"
        if mode == "cold":
            self.cache.unlink(missing_ok=True)
        else:
            fetched = time.time() - (catalog.TTL_SECONDS + 60 if mode == "stale" else 0)
            self.cache.write_text(json.dumps({"opencode": {
                "ids": [route.model_for("opencode", "review")[0]], "fetched_at": fetched}}))
        self.counts.clear()
        self.subject_reads.clear()
        self.probed.clear()


def nearest_rank(values, percentile=95):
    return sorted(values)[max(0, math.ceil(percentile / 100 * len(values)) - 1)]


def measure(fixture, function, mode, warmup, samples):
    timings, sizes, counts, probes = [], [], [], []
    with fixture.isolated():
        for index in range(warmup + samples):
            fixture.prepare_catalog(mode)
            started = time.perf_counter_ns()
            payload = function()
            elapsed = (time.perf_counter_ns() - started) / 1_000_000
            if not isinstance(payload, str):
                payload = json.dumps(payload, ensure_ascii=False)
            if index >= warmup:
                timings.append(elapsed)
                sizes.append(len(payload.encode("utf-8")))
                counts.append({key: fixture.counts[key] for key in COUNT_KEYS})
                probes.append(list(fixture.probed))
    return {"median_ms": statistics.median(timings), "p95_ms": nearest_rank(timings),
            "payload_bytes": {"median": statistics.median(sizes), "min": min(sizes), "max": max(sizes)},
            "counts": {key: {"min": min(row[key] for row in counts),
                              "max": max(row[key] for row in counts)} for key in counts[0]},
            "samples_ms": timings, "samples_payload_bytes": sizes,
            "samples_counts": counts, "samples_catalog_workers": probes}


def provenance():
    revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True,
                              capture_output=True, check=True).stdout.strip()
    dirty = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, text=True,
                           capture_output=True, check=True).stdout.splitlines()
    return {"source_revision": revision, "working_tree_status": dirty,
            "production_sha256": {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                                  for name in PRODUCTION},
            "evaluation_sha256": {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
                                  for path in (Path(__file__), CORPUS, Path(__file__).with_name("test_normal_prompt.py"))},
            "python": sys.version, "python_executable": sys.executable,
            "os": platform.platform(), "machine": platform.machine(),
            "recorded_at": datetime.now(timezone.utc).isoformat()}


def compatibility_snapshot():
    """Retain representative full output; additive fields remain compatible."""
    with tempfile.TemporaryDirectory(prefix="rig-compatibility-") as temporary:
        fixture = Fixture(Path(temporary))
        for status in ("ok", "fail", "timeout", "cancelled", "running", "ask"):
            seed_job(fixture.repo, "legacy-" + status, status)
        with fixture.isolated():
            fixture.prepare_catalog("static")
            return {
                "full_json": json.loads(rig_mcp.format_session(fixture.repo, "Fix the fixture", "implement", as_json=True)),
                "full_text": rig_mcp.format_session(fixture.repo, "Fix the fixture", "implement"),
                "wait_exit_codes": {status: jobs.wait_job(fixture.repo, "legacy-" + status, timeout=0)[0]
                                    for status in ("ok", "fail", "timeout", "cancelled", "running", "ask")},
                "additive_fields": "Additional pick/job metadata allowed; existing full-mode fields and types retained.",
                "volatile_fields": ["pid", "alive", "mtime", "dir", "log", "ask timestamps", "temporary repo paths"],
            }


def category_results(rows):
    return {category: {"total": sum(row["category"] == category for row in rows),
                       "fallback_mismatches": sum(row["category"] == category and
                                                  row["observed_kind"] != row["expected_kind"] for row in rows),
                       "parent_role_mismatches": sum(row["category"] == category and
                                                     row["parent_role_result"] != row["expected_kind"] for row in rows)}
            for category in sorted({row["category"] for row in rows})}


def comparison(current, baseline):
    previous = {row["id"]: row for row in baseline["scenarios"]}
    rows = []
    for row in current:
        old = previous.get(row["id"])
        if old is None:
            rows.append({"id": row["id"], "baseline_available": False,
                         "note": "New scenario; absolute metrics only."})
            continue
        rows.append({"id": row["id"], "baseline_available": True,
                     "median_ms_delta": row["median_ms"] - old["median_ms"],
                     "p95_ms_delta": row["p95_ms"] - old["p95_ms"],
                     "payload_bytes_delta": row["payload_bytes"]["median"] - old["payload_bytes"]["median"],
                     "counts_before": old["counts"], "counts_after": row["counts"]})
    return rows


def csv_ints(value, *, positive=False):
    try:
        values = [int(part) for part in value.split(",")]
    except ValueError as error:
        raise argparse.ArgumentTypeError("expected comma-separated integers") from error
    if not values or any(number < (1 if positive else 0) for number in values):
        raise argparse.ArgumentTypeError("values must be positive" if positive else "values must be nonnegative")
    return list(dict.fromkeys(values))


def accepted_history_scenarios(args):
    """Bound acceptance hashing to the chosen HUD item or emitted compact rows."""
    rows = []
    for history in (number for number in args.history_sizes if number):
        for layout in ("shared", "disjoint"):
            with tempfile.TemporaryDirectory(prefix="rig-accepted-benchmark-") as temporary:
                fixture = Fixture(Path(temporary))
                fixture.seed_accepted(history, layout)
                for output_format in ("full-json", "compact-json", "compact-text", "hud", "hud-foreground", "hud-expired"):
                    if output_format.startswith("hud") and "hud" not in args.surfaces.split(","):
                        continue
                    if not output_format.startswith("hud") and "session" not in args.surfaces.split(","):
                        continue
                    foreground = None
                    if output_format == "hud-foreground":
                        foreground = seed_job(fixture.repo, "foreground", "ask")
                    if output_format == "hud-expired":
                        fixture.accepted_clock += 61
                    if output_format.startswith("hud"):
                        function = lambda: jobs.hud_snapshot(repo=fixture.repo)
                        expected_hashes = 1 if output_format == "hud" else 0
                    else:
                        function = lambda: rig_mcp.format_session(
                            fixture.repo, "Report current work", "stay", as_json=output_format.endswith("json"),
                            compact=output_format.startswith("compact"), terminal_limit=10)
                        expected_hashes = (1 if layout == "shared" else min(10, history)) if output_format.startswith("compact") else 0
                    identity = f"accepted/{layout}/n={history}/{output_format}"
                    row = {"id": identity, "surface": output_format, "history_size": history,
                           "scope_layout": layout, **measure(fixture, function, "static", args.warmup, args.samples)}
                    row["structural_gate"] = {
                        "expected_subject_hashes": expected_hashes,
                        "passed": row["counts"]["subject_hashes"] == {"min": expected_hashes, "max": expected_hashes}
                                  and row["counts"]["max_hashes_per_subject"]["max"] <= 1,
                    }
                    rows.append(row)
                    print(f"{identity}: median={row['median_ms']:.3f}ms p95={row['p95_ms']:.3f}ms hashes={row['counts']['subject_hashes']['max']}", file=sys.stderr)
                    if foreground:
                        # The foreground artifact is fixture-owned and never launched.
                        import shutil
                        shutil.rmtree(foreground)
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--history-sizes", type=csv_ints, default=[0, 100, 1000])
    parser.add_argument("--wait-widths", type=lambda value: csv_ints(value, positive=True), default=[1, 3])
    parser.add_argument("--warmup", type=int, default=10)
    parser.add_argument("--samples", type=int, default=50)
    parser.add_argument("--surfaces", default="session,wait")
    parser.add_argument("--include-compact", action="store_true",
                        help="Add compact JSON/text sessions with terminal_limit=10 when supported.")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--compare", type=Path)
    parser.add_argument("--accepted-histories", action="store_true",
                        help="Measure accepted shared/disjoint subjects and enforce displayed-only hashing.")
    args = parser.parse_args()
    surfaces = list(dict.fromkeys(args.surfaces.split(",")))
    if args.samples < 1 or args.warmup < 0 or not set(surfaces) <= {"session", "wait", "hud"}:
        parser.error("samples must be positive, warmup nonnegative; surfaces: session,wait,hud")
    if args.include_compact and "compact" not in inspect.signature(rig_mcp.format_session).parameters:
        parser.error("this source revision does not support compact sessions")
    report = {"schema_version": 1, **provenance(),
              "parameters": {"history_sizes": args.history_sizes, "wait_widths": args.wait_widths,
                             "warmup": args.warmup, "samples": args.samples, "surfaces": surfaces,
                             "include_compact": args.include_compact, "accepted_histories": args.accepted_histories},
              "fixture": {"session_status_cycle": SESSION_MIX, "wait_history_status_cycle": WAIT_MIX,
                          "active_targets": "running, current benchmark PID", "brief_bytes": len(BRIEF.encode()),
                          "stdout_log_bytes": len(LOG.encode()), "memory": "one fixed synthetic fact",
                          "ownership": "session running rows unlabeled; ASK rows label src/example.py",
                          "wait_refresh": "timeout=0; sleep guarded; one refresh, no worker lifetime",
                          "catalog": "review selects opencode only; deterministic provider; stale scheduling counted, no threads",
                          "directory_scan_instrumentation": "Path.iterdir calls; job_directory_scans restricts to .rig/jobs; queue glob calls excluded",
                          "timing": "perf_counter_ns; nearest-rank p95; setup excluded; instrumentation included"},
              "routing": routing_observations(), "native_mini": native_mini_observation(), "scenarios": []}
    report["category_counts"] = dict(Counter(row["category"] for row in report["routing"]))
    report["category_results"] = category_results(report["routing"])
    report["compatibility"] = compatibility_snapshot()
    for surface in surfaces:
        for history in args.history_sizes:
            for width in (args.wait_widths if surface == "wait" else [0]):
                with tempfile.TemporaryDirectory(prefix="rig-benchmark-") as temporary:
                    fixture = Fixture(Path(temporary))
                    names = fixture.seed(history, width, surface=surface)
                    formats = ["json", "text"] if surface == "session" else ["text"]
                    if surface == "session" and args.include_compact:
                        formats += ["compact-json", "compact-text"]
                    modes = ["static", "warm", "stale", "cold"] if surface == "session" else ["static"]
                    for output_format in formats:
                        for mode in modes:
                            if surface == "session":
                                session_options = {"compact": True, "terminal_limit": 10} if output_format.startswith("compact-") else {}
                                function = lambda: rig_mcp.format_session(fixture.repo, "Review the staged diff",
                                                                          "review", as_json=output_format.endswith("json"),
                                                                          **session_options)
                            elif surface == "wait":
                                function = lambda: jobs.wait_job(fixture.repo, None, ids=names, timeout=0)[1]
                            else:
                                function = lambda: jobs.hud_snapshot(repo=fixture.repo)
                            identity = f"{surface}/n={history}/k={width}/{output_format}/{mode}"
                            row = {"id": identity, "surface": surface, "history_size": history,
                                   "wait_width": width, "format": output_format, "catalog": mode,
                                   **measure(fixture, function, mode, args.warmup, args.samples)}
                            report["scenarios"].append(row)
                            print(f"{identity}: median={row['median_ms']:.3f}ms p95={row['p95_ms']:.3f}ms", file=sys.stderr)
    if args.accepted_histories:
        report["accepted_history_scenarios"] = accepted_history_scenarios(args)
        report["structural_gates_passed"] = all(row["structural_gate"]["passed"] for row in report["accepted_history_scenarios"])
    if args.compare:
        report["comparison"] = comparison(report["scenarios"], json.loads(args.compare.read_text()))
        report["compared_with"] = str(args.compare)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(f"Saved {len(report['scenarios'])} scenarios to {args.output}")
    if report.get("structural_gates_passed") is False:
        raise SystemExit("accepted-history hash budget failed; inspect the saved measurements")


if __name__ == "__main__":
    main()
