#!/usr/bin/env python3
"""Pilot evaluator correctness on synthetic fixtures only. Not provider evidence."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import outcome_metrics  # noqa: E402
import preparation_pilot as pilot  # noqa: E402
import routing_evidence  # noqa: E402

FP = "a" * 64


def routing(cohort, version=3):
    value = {"policy_mode": "smart", "policy_version": version}
    if cohort in {"B", "C"}:
        value["preparation"] = {"status": "valid", "version": 1, "fingerprint": FP, "ready": True,
                                "execution_ready": True, "remaining_work": "low", "gap_codes": []}
    if cohort == "C" or cohort == "B":
        value["effort"] = {"pilot": cohort == "C", "baseline": "medium", "requested": "low" if cohort == "C" else "",
                           "effective": "low" if cohort == "C" else "medium",
                           "reason": "adjusted" if cohort == "C" else "pilot-off", "floor": ""}
    return value


class PilotEvaluator(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name).resolve()
        (self.repo / ".git").mkdir()
        (self.repo / ".rig/jobs").mkdir(parents=True)
        (self.repo / ".rig/harness.toml").write_text("[project]\nenabled=true\n")
        self.outcomes = {}
        patcher = mock.patch.object(outcome_metrics, "attempt", side_effect=lambda repo, job: self.outcomes[job["job_id"]])
        patcher.start()
        self.addCleanup(patcher.stop)

    def job(self, job_id, cohort, *, accepted=True, first_pass=True, seconds=100, tokens=1000, version=3,
            pending=False, status="ok", parent_started_at="2026-10-01T00:00:00Z"):
        folder = self.repo / ".rig/jobs" / job_id
        folder.mkdir()
        meta = {"job_id": job_id, "status": status, "exit_code": 0 if status == "ok" else 1, "attempt_id": "att-" + job_id,
                "reservation_id": "res-" + job_id, "started_at": "2026-10-01T00:01:00Z",
                "ended_at": f"2026-10-01T00:{1 + seconds // 60:02d}:{seconds % 60:02d}Z",
                "model": "m", "effort": "low", "token_usage": {"total": tokens}}
        (folder / "meta.json").write_text(json.dumps(meta))
        routing_evidence.write_sidecar(folder, meta["attempt_id"], routing(cohort, version))
        self.outcomes[job_id] = {"latency_s": seconds + 20 if accepted else None, "first_pass": first_pass,
                                 "pending": pending, "admitted_at": "2026-10-01T00:00:30Z",
                                 "accepted_at": "2026-10-01T00:05:00Z" if accepted else None}
        return {"task_id": job_id.split("-")[0], "cohort": cohort, "job_id": job_id,
                "parent_started_at": parent_started_at}

    def evaluate(self, runs, min_pairs=2):
        result = pilot.evaluate(self.repo, {"schema_version": 1, "pilot_id": "p", "synthetic": True,
                                            "min_pairs": min_pairs, "runs": runs})
        return result, {(row["candidate"], row["baseline"]): row for row in result["comparisons"]}

    def pairs(self, count, base, cand):
        runs = []
        for index in range(count):
            runs.append(self.job(f"t{index}-a", "A", **base))
            runs.append(self.job(f"t{index}-b", "B", **cand))
        return runs

    def test_pending_unknown_and_failed_pairs_never_claim_quality(self):
        cases = {
            "all-pending": dict(accepted=False, first_pass=None, pending=True, status="running"),
            "all-unknown": dict(accepted=False, first_pass=None),
            "all-failed": dict(accepted=False, first_pass=False, status="fail"),
        }
        for name, outcome in cases.items():
            with self.subTest(name=name):
                self.setUp()
                result, verdicts = self.evaluate(self.pairs(3, outcome, outcome))
                row = verdicts[("B", "A")]
                self.assertEqual(row["verdict"], "insufficient-evidence")
                self.assertEqual(row["comparable_pairs"], 0)
                self.assertEqual((row["acceptance_regressions"], row["first_pass_regressions"]), ([], []))
                self.assertNotIn("deltas", row)
                expected = {"all-pending": "pending", "all-unknown": "unknown", "all-failed": "failed"}[name]
                self.assertEqual(result["cohorts"]["B"]["outcomes"][expected], 3)
                self.assertEqual(row["adjudicated_pairs"], 3 if name == "all-failed" else 0)
        # Pending/unknown candidates against accepted baselines are unresolved, not regressions.
        self.setUp()
        _result, verdicts = self.evaluate(self.pairs(3, {}, dict(accepted=False, first_pass=None)), min_pairs=1)
        self.assertEqual(verdicts[("B", "A")]["verdict"], "insufficient-evidence")
        self.assertEqual(verdicts[("B", "A")]["unresolved_pairs"], 3)

    def test_partial_first_pass_reduces_comparable_pairs(self):
        runs = self.pairs(2, {}, {})
        runs.append(self.job("t5-a", "A", first_pass=None))
        runs.append(self.job("t5-b", "B"))
        _result, verdicts = self.evaluate(runs, min_pairs=3)
        row = verdicts[("B", "A")]
        self.assertEqual((row["pairs"], row["comparable_pairs"], row["verdict"]), (3, 2, "insufficient-evidence"))
        _result, verdicts = self.evaluate(runs, min_pairs=2)
        self.assertEqual(verdicts[("B", "A")]["verdict"], "quality-held")
        self.assertEqual(verdicts[("B", "A")]["deltas"]["worker_execution_s"]["pairs"], 2)

    def test_known_regression_visible_below_min_pairs(self):
        runs = [self.job("t1-a", "A"), self.job("t1-b", "B", accepted=False, first_pass=False)]
        _result, verdicts = self.evaluate(runs, min_pairs=10)
        row = verdicts[("B", "A")]
        self.assertEqual(row["verdict"], "quality-regression")
        self.assertEqual(row["acceptance_regressions"], ["t1"])
        self.assertNotIn("deltas", row)
        runs = [self.job("t2-a", "A"), self.job("t2-b", "B", first_pass=False)]
        _result, verdicts = self.evaluate(runs, min_pairs=10)
        self.assertEqual((verdicts[("B", "A")]["verdict"], verdicts[("B", "A")]["first_pass_regressions"]),
                         ("quality-regression", ["t2"]))

    def test_parent_start_after_admission_invalidates_total(self):
        late = self.job("t1-a", "A", parent_started_at="2026-10-01T00:01:00Z")
        result, _ = self.evaluate([late, self.job("t1-b", "B")], min_pairs=1)
        row = next(item for item in result["runs"] if item["job_id"] == "t1-a")
        self.assertEqual(row["timing"], "parent-start-after-admission")
        self.assertIsNone(row["total_s"])
        self.assertIsNone(row["parent_pre_admission_s"])
        self.assertEqual(result["cohorts"]["A"]["timing_invalid"], 1)
        self.assertEqual(result["cohorts"]["A"]["total_s"]["known"], 0)

    def test_quality_first_verdicts_and_separate_times(self):
        runs = []
        for index in range(3):
            runs.append(self.job(f"t{index}-a", "A", seconds=120))
            runs.append(self.job(f"t{index}-b", "B", seconds=100))
            runs.append(self.job(f"t{index}-c", "C", accepted=index != 0, first_pass=index != 0, seconds=60))
        result = pilot.evaluate(self.repo, {"schema_version": 1, "pilot_id": "p1", "synthetic": True,
                                            "min_pairs": 3, "runs": runs})
        self.assertEqual(result["evidence"], "synthetic-fixture")
        verdicts = {(row["candidate"], row["baseline"]): row for row in result["comparisons"]}
        self.assertEqual(verdicts[("B", "A")]["verdict"], "quality-held")
        self.assertEqual(verdicts[("B", "A")]["deltas"]["worker_execution_s"]["median_delta"], -20.0)
        self.assertEqual(verdicts[("C", "B")]["verdict"], "quality-regression")
        self.assertEqual(verdicts[("C", "B")]["acceptance_regressions"], ["t0"])
        self.assertEqual(verdicts[("C", "B")]["first_pass_regressions"], ["t0"])
        self.assertNotIn("deltas", verdicts[("C", "B")])
        cohort = result["cohorts"]["A"]
        self.assertEqual(cohort["parent_pre_admission_s"]["median"], 30.0)
        self.assertEqual(cohort["total_s"]["median"], 300.0)
        self.assertEqual(cohort["worker_tokens_total"]["median"], 1000.0)
        self.assertIn("not real-provider quality proof", result["note"])
        self.assertNotIn("brief", json.dumps(result["runs"]))

    def test_insufficient_mismatched_unknown_and_missing_evidence(self):
        runs = [self.job("t1-a", "A"), self.job("t1-b", "A"), self.job("t2-a", "B", version=2)]
        runs[1]["cohort"] = "B"
        runs.append({"task_id": "t3", "cohort": "C", "job_id": "missing"})
        result = pilot.evaluate(self.repo, {"schema_version": 1, "pilot_id": "p2", "runs": runs})
        statuses = {row["job_id"]: row["status"] for row in result["runs"]}
        self.assertEqual(statuses, {"t1-a": "valid", "t1-b": "cohort-mismatch", "t2-a": "cohort-unverifiable",
                                    "missing": "missing-job"})
        self.assertTrue(all(row["verdict"] == "insufficient-evidence" for row in result["comparisons"]))
        self.assertEqual(result["evidence"], "recorded-local-jobs")
        no_parent = self.job("t9-a", "A")
        no_parent.pop("parent_started_at")
        unknown = pilot.evaluate(self.repo, {"schema_version": 1, "pilot_id": "p3", "runs": [no_parent]})
        self.assertIsNone(unknown["runs"][0]["total_s"])
        self.assertEqual(unknown["cohorts"]["A"]["total_s"]["known"], 0)

    def test_input_is_bounded_and_strict(self):
        good = {"schema_version": 1, "pilot_id": "p", "runs": [{"task_id": "t", "cohort": "A", "job_id": "j"}]}
        for bad in ({**good, "schema_version": 2}, {**good, "extra": 1}, {**good, "runs": []},
                    {**good, "runs": [{"task_id": "t", "cohort": "D", "job_id": "j"}]},
                    {**good, "runs": [{"task_id": "../t", "cohort": "A", "job_id": "j"}]},
                    {**good, "runs": good["runs"] * 2},
                    {**good, "runs": [{**good["runs"][0], "parent_started_at": "yesterday"}]},
                    {**good, "runs": [{**good["runs"][0], "parent_tokens": {"total": -1}}]},
                    {**good, "min_pairs": 0}, {**good, "runs": good["runs"] * 0 + [
                        {"task_id": f"t{i}", "cohort": "A", "job_id": "j"} for i in range(301)]}):
            with self.assertRaises(ValueError):
                pilot.normalize(bad)

    def test_cli_reads_bounded_file(self):
        path = self.repo / "pilot.json"
        path.write_text(json.dumps({"schema_version": 1, "pilot_id": "p", "runs": [
            {"task_id": "t", "cohort": "A", "job_id": "absent"}]}))
        result = subprocess.run([sys.executable, str(ROOT / "scripts/preparation_pilot.py"), "evaluate",
                                 "--file", str(path), "--repo", str(self.repo), "--json"],
                                capture_output=True, text=True, env={**os.environ, "RIG_JOB_ID": ""})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["runs"][0]["status"], "missing-job")
        path.write_text("x" * (pilot.MAX_INPUT_BYTES + 1))
        result = subprocess.run([sys.executable, str(ROOT / "scripts/preparation_pilot.py"), "evaluate",
                                 "--file", str(path), "--repo", str(self.repo)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)


if __name__ == "__main__":
    unittest.main()
