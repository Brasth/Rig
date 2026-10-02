#!/usr/bin/env python3
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import routing_policy as policy  # noqa: E402
import routing_report as report  # noqa: E402


def _job(**fields):
    base = {
        "job_id": "j1",
        "dir": "/tmp/missing-job",
        "status": "ok",
        "exit_code": 0,
        "attempt_id": "att-1",
        "model": "grok-4.6",
        "effort": "high",
        "model_source": "selected",
        "model_inferred": False,
        "execution_mode": "live",
        "elapsed_s": 12,
        "started_at": "2026-09-01T00:00:00Z",
        "ended_at": "2026-09-01T00:00:12Z",
        "mtime": 1_778_000_000.0,
    }
    base.update(fields)
    return base


class RoutingReport(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.repo = Path(self.td.name)
        (self.repo / ".rig" / "jobs").mkdir(parents=True)
        (self.repo / ".git").mkdir()
        os.environ.pop("RIG_JOB_ID", None)
        os.environ.pop("RIG_JOB_DIR", None)
        os.environ.pop("RIG_LIVE", None)

    def tearDown(self):
        self.td.cleanup()

    def _build(self, jobs, *, sidecar=None, assessed=None, now=1_778_300_000.0):
        def fake_sidecar(folder, expected_attempt_id=None):
            if sidecar is None:
                return None
            if expected_attempt_id is not None and str(expected_attempt_id) != str(sidecar.get("attempt_id") or ""):
                return None
            return sidecar

        def fake_assessment(root, job, refresh=False, cache=None):
            self.assertTrue(refresh)
            if assessed is None:
                return {"state": "unknown", "acceptance": "pending", "freshness": "not_checked", "reason": ""}
            return dict(assessed)

        with patch.object(report.rig_jobs, "list_jobs", return_value=jobs), patch.object(
            policy, "read_sidecar", side_effect=fake_sidecar
        ), patch.object(report.verification, "assessment", side_effect=fake_assessment):
            before = {p: p.stat().st_mtime for p in self.repo.rglob("*") if p.is_file()}
            result = report.build_report(self.repo, days=30, now=now)
            after = {p: p.stat().st_mtime for p in self.repo.rglob("*") if p.is_file()}
        self.assertEqual(before, after)
        return result

    def test_preparation_cohorts_coverage_and_effort_codes(self):
        prep = {"status": "valid", "version": 1, "fingerprint": "f" * 64, "ready": True, "execution_ready": True,
                "remaining_work": "low", "gap_codes": []}
        effort = {"pilot": True, "baseline": "medium", "requested": "low", "effective": "low",
                  "reason": "adjusted", "floor": ""}
        cases = [
            ({"policy_mode": "smart", "policy_version": 2}, ("unknown", "unknown")),
            ({"policy_mode": "smart", "policy_version": 3}, ("absent", "off")),
            ({"policy_mode": "manual", "policy_version": 3, "preparation": prep}, ("present", "n/a")),
            ({"policy_mode": "smart", "policy_version": 3, "preparation": prep, "effort": effort}, ("present", "on")),
        ]
        for routing, expected in cases:
            sidecar = {"schema_version": 1, "attempt_id": "att-1", "written_at": "2026-09-01T00:00:00Z",
                       "routing": {"execution_strategy": "wrapper", "required_tier": "standard", **routing}}
            result = self._build([_job()], sidecar=sidecar)
            cohorts = result["preparation"]["cohorts"]
            with self.subTest(expected=expected):
                self.assertEqual([(row["preparation"], row["pilot"], row["attempts"]) for row in cohorts],
                                 [(*expected, 1)])
                known = 0 if expected[0] == "unknown" else 1
                self.assertEqual(result["preparation"]["coverage"]["known"], known)
                self.assertEqual(result["preparation"]["coverage"]["unknown"], 1 - known)
        self.assertEqual([(row["baseline"], row["effective"], row["reason"]) for row in result["preparation"]["efforts"]],
                         [("medium", "low", "adjusted")])
        self.assertIn("preparation=present pilot=on n=1", report.format_report(result))
        self.assertNotIn("Fix saving", json.dumps(result))

    def test_running_exit0_is_not_exit0(self):
        result = self._build([_job(status="running", exit_code=0, ended_at="")])
        self.assertEqual(result["totals"]["exit0"], 0)
        self.assertEqual(result["totals"]["attempts"], 1)

    def test_rejected_unverified_and_strict_denominator(self):
        sidecar = {
            "schema_version": 1,
            "attempt_id": "att-1",
            "routing": {"policy_mode": "smart", "policy_version": 1, "required_tier": "standard",
                        "selected_profile": {"id": "grok-4.6-high"}},
        }
        pending = self._build(
            [_job()], sidecar=sidecar,
            assessed={"state": "pending", "acceptance": "pending", "freshness": "current", "reason": ""},
        )
        self.assertEqual(pending["totals"]["accepted_denominator"], 0)
        self.assertEqual(pending["totals"]["accepted_numerator"], 0)
        rejected = self._build(
            [_job()], sidecar=sidecar,
            assessed={"state": "failed", "acceptance": "rejected", "freshness": "current", "reason": "nope"},
        )
        self.assertEqual(rejected["totals"]["accepted_denominator"], 1)
        self.assertEqual(rejected["totals"]["accepted_numerator"], 0)
        self.assertEqual(rejected["groups"][0]["rejected"], 1)
        self.assertEqual(rejected["groups"][0]["unverified"], 1)

    def test_stale_changed_content_not_accepted(self):
        sidecar = {
            "schema_version": 1,
            "attempt_id": "att-1",
            "routing": {"policy_mode": "smart", "policy_version": 1, "required_tier": "fast",
                        "selected_profile": {"id": "grok-4.5-low"}},
        }
        result = self._build(
            [_job()], sidecar=sidecar,
            assessed={
                "state": "pending", "acceptance": "accepted", "freshness": "current",
                "reason": "content_changed",
            },
        )
        self.assertEqual(result["totals"]["accepted_numerator"], 0)
        self.assertEqual(result["totals"]["accepted_denominator"], 1)
        self.assertEqual(result["groups"][0]["stale"], 1)

    def test_unknown_timestamps_excluded_not_silently_included(self):
        result = self._build([_job(started_at="", ended_at="", mtime=None)], now=1_778_300_000.0)
        self.assertEqual(result["totals"]["attempts"], 0)
        self.assertEqual(result["totals"]["unknown_timestamps"], 1)

    def test_legacy_and_mismatch_are_not_invented_smart(self):
        legacy = {
            "schema_version": 1,
            "attempt_id": "att-1",
            "routing": {"policy_mode": "legacy", "policy_version": 1},
        }
        result = self._build([_job()], sidecar=legacy)
        self.assertEqual(result["totals"]["smart_attempts"], 0)
        self.assertEqual(result["totals"]["legacy_attempts"], 1)
        self.assertEqual(result["groups"], [])
        mismatch = dict(legacy)
        mismatch["attempt_id"] = "other"
        missing = self._build([_job()], sidecar=mismatch)
        self.assertEqual(missing["totals"]["missing_provenance"], 1)
        self.assertEqual(missing["totals"]["smart_attempts"], 0)

    def test_inferred_model_not_counted_actual(self):
        sidecar = {
            "schema_version": 1,
            "attempt_id": "att-1",
            "routing": {"policy_mode": "smart", "policy_version": 1, "required_tier": "standard",
                        "selected_profile": {"id": "grok-4.6-high"}},
        }
        result = self._build(
            [_job(model="grok-4.6", model_inferred=True, model_source="unknown")],
            sidecar=sidecar,
        )
        self.assertEqual(result["groups"][0]["model"], "")
        self.assertEqual(result["groups"][0]["effort"], "")

    def test_folder_fallback_uses_repo_jobs_dir(self):
        job_dir = self.repo / ".rig" / "jobs" / "fallback"
        job_dir.mkdir(parents=True)
        policy.write_sidecar(job_dir, "att-1", {"policy_mode": "manual", "assessment": {
            "complexity": "", "risk": "", "uncertainty": "", "reason": "",
            "defaulted": ["complexity", "risk", "uncertainty"], "supplied": False,
        }})
        job = _job(job_id="fallback", dir="", attempt_id="att-1")
        with patch.object(report.rig_jobs, "list_jobs", return_value=[job]), patch.object(
            report.verification, "assessment",
            return_value={"state": "unknown", "acceptance": "pending", "freshness": "not_checked", "reason": ""},
        ):
            result = report.build_report(self.repo, days=30, now=1_778_300_000.0)
        self.assertEqual(result["totals"]["manual_attempts"], 1)
        self.assertEqual(result["totals"]["missing_provenance"], 0)

    def test_completed_independent_of_duration_and_cancel_separate(self):
        sidecar = {
            "schema_version": 1, "attempt_id": "att-1",
            "routing": {"policy_mode": "smart", "policy_version": 1, "required_tier": "fast",
                        "selected_profile": {"id": "x"}},
        }
        jobs = [
            _job(elapsed_s=None, status="ok", exit_code=0),
            _job(job_id="c", attempt_id="att-1", status="cancelled", exit_code=130, elapsed_s=3),
            _job(job_id="n", attempt_id="att-1", status="fail", execution_mode="not_started", elapsed_s=9),
        ]
        result = self._build(jobs, sidecar=sidecar)
        row = result["groups"][0]
        self.assertEqual(row["completed"], 1)
        self.assertEqual(row["cancels"], 1)
        self.assertEqual(row["launch_failures"], 1)
        self.assertEqual(result["totals"]["cancels"], 1)

    def test_group_sort_includes_effort(self):
        sidecar = {
            "schema_version": 1, "attempt_id": "att-1",
            "routing": {"policy_mode": "smart", "policy_version": 1, "required_tier": "fast",
                        "selected_profile": {"id": "p"}},
        }
        jobs = [
            _job(job_id="b", model="m", effort="high"),
            _job(job_id="a", model="m", effort="low"),
        ]
        result = self._build(jobs, sidecar=sidecar)
        efforts = [row["effort"] for row in result["groups"]]
        self.assertEqual(efforts, ["high", "low"] if efforts[0] == "high" else ["high", "low"])
        self.assertEqual(sorted(efforts), ["high", "low"])

    def test_direct_parent_and_wrapper_token_coverage(self):
        usage = {"input": 10, "output": 4, "reasoning": 2, "cached_input": 1, "total": 17}
        jobs = [
            _job(job_id="direct", dir="/tmp/direct", token_usage=usage, elapsed_s=5),
            _job(job_id="wrap", dir="/tmp/wrap", token_usage=None, elapsed_s=9, model="grok-4.5", effort="low"),
            _job(job_id="unknown", dir="/tmp/wrap-unknown", elapsed_s=3),
        ]
        direct = {
            "schema_version": 1, "attempt_id": "att-1",
            "routing": {"policy_mode": "smart", "policy_version": 1, "required_tier": "fast",
                        "execution_strategy": "direct-parent"},
        }
        wrapper = {
            "schema_version": 1, "attempt_id": "att-1",
            "routing": {"policy_mode": "smart", "policy_version": 1, "required_tier": "fast",
                        "execution_strategy": "wrapper",
                        "selected_profile": {"id": "grok-4.5-low"}},
        }

        def fake_sidecar(folder, expected_attempt_id=None):
            name = Path(folder).name if folder else ""
            if name == "direct":
                return direct
            return wrapper

        assessed = {"state": "verified", "acceptance": "accepted", "freshness": "current", "reason": ""}
        with patch.object(report.rig_jobs, "list_jobs", return_value=jobs), patch.object(
            policy, "read_sidecar", side_effect=fake_sidecar
        ), patch.object(report.verification, "assessment", return_value=assessed):
            result = report.build_report(self.repo, days=30, now=1_778_300_000.0)
        self.assertEqual(result["totals"]["direct_parent_attempts"], 1)
        self.assertEqual(result["totals"]["wrapper_attempts"], 2)
        self.assertEqual(result["totals"]["token_coverage"]["known"], 1)
        self.assertEqual(result["totals"]["token_coverage"]["unknown"], 2)
        self.assertEqual(result["totals"]["token_components"]["total"]["sum"], 17)
        self.assertEqual(result["totals"]["token_components"]["total"]["median"], 17.0)
        self.assertIsNone(result["strategies"]["wrapper"]["token_components"]["total"]["sum"])
        self.assertEqual(result["strategies"]["direct-parent"]["token_coverage"]["known"], 1)
        self.assertEqual(result["strategies"]["wrapper"]["token_coverage"]["unknown"], 2)
        self.assertEqual(result["strategies"]["wrapper"]["token_coverage"]["known"], 0)
        self.assertEqual(result["strategies"]["direct-parent"]["accepted"], 1)
        strategies = {row["execution_strategy"] for row in result["groups"]}
        self.assertEqual(strategies, {"direct-parent", "wrapper"})

    def test_partial_usage_aggregates_per_component(self):
        jobs = [
            _job(job_id="full", token_usage={
                "input": 10, "output": 4, "reasoning": 2, "cached_input": 1, "total": 17,
            }),
            _job(job_id="partial", token_usage={"input": 6, "output": 2}),
            _job(job_id="unknown", token_usage=None),
        ]
        sidecar = {
            "schema_version": 1, "attempt_id": "att-1",
            "routing": {"policy_mode": "smart", "policy_version": 1, "required_tier": "fast",
                        "execution_strategy": "wrapper", "selected_profile": {"id": "p"}},
        }
        result = self._build(jobs, sidecar=sidecar)
        totals = result["totals"]
        self.assertEqual(totals["token_coverage"]["known"], 2)
        self.assertEqual(totals["token_coverage"]["unknown"], 1)
        self.assertEqual(totals["token_components"]["input"]["n"], 2)
        self.assertEqual(totals["token_components"]["input"]["sum"], 16)
        self.assertEqual(totals["token_components"]["output"]["n"], 2)
        self.assertEqual(totals["token_components"]["output"]["sum"], 6)
        self.assertEqual(totals["token_components"]["reasoning"]["n"], 1)
        self.assertEqual(totals["token_components"]["reasoning"]["sum"], 2)
        self.assertEqual(totals["token_components"]["cached_input"]["n"], 1)
        self.assertEqual(totals["token_components"]["total"]["n"], 1)
        self.assertEqual(totals["token_components"]["total"]["sum"], 17)
        self.assertEqual(totals["token_components"]["total"]["median"], 17.0)
        row = result["groups"][0]
        self.assertEqual(row["token_components"]["input"]["n"], 2)
        self.assertEqual(row["token_components"]["total"]["n"], 1)
        self.assertEqual(row["token_components"]["reasoning"]["n"], 1)
        self.assertEqual(row["token_components"]["cached_input"]["n"], 1)
        self.assertNotEqual(row["token_components"]["input"]["sum"], 0)

    def test_unknown_usage_is_not_zero(self):
        sidecar = {
            "schema_version": 1, "attempt_id": "att-1",
            "routing": {"policy_mode": "smart", "policy_version": 1, "required_tier": "fast",
                        "execution_strategy": "wrapper", "selected_profile": {"id": "p"}},
        }
        result = self._build(
            [_job(token_usage={"input": -1, "output": 0, "reasoning": 0, "cached_input": 0, "total": 0})],
            sidecar=sidecar,
        )
        self.assertEqual(result["totals"]["token_coverage"]["known"], 0)
        self.assertEqual(result["totals"]["token_coverage"]["unknown"], 1)
        self.assertIsNone(result["totals"]["token_components"]["total"]["sum"])
        self.assertIsNone(result["groups"][0]["token_components"]["input"]["median"])

    def test_domain_slices_latency_samples_and_cancellation(self):
        domain = {"name": "backend", "source": "explicit", "rule": "explicit:backend",
                  "policy": "builtin:backend", "fallback": "scored", "reason": "fixture",
                  "selection": "selected", "parent_only": False,
                  "preferred_profiles": [], "requirements": [], "research_sources": []}
        sidecar = {"schema_version": 1, "attempt_id": "att-1", "routing": {
            "policy_mode": "smart", "policy_version": 2, "required_tier": "standard",
            "selected_profile": {"id": "grok-4.6-high"}, "task_domain": domain}}
        jobs = [_job(token_usage={"input": 0}),
                _job(job_id="j2", ended_at="bad", elapsed_s=float("nan")),
                _job(job_id="j3", status="cancelled", continues_job_id="prior")]
        result = self._build(jobs, sidecar=sidecar)
        row = result["domains"]["backend"]
        self.assertEqual(row["attempts"], 3)
        self.assertEqual(row["execution_failures"], 0)
        self.assertEqual(row["cancels"], 1)
        self.assertEqual(row["continuations"], 1)
        self.assertEqual(row["execution_latency"]["eligible_count"], 2)
        self.assertEqual(row["execution_latency"]["sample_count"], 1)
        self.assertEqual(row["execution_latency"]["missing_count"], 1)
        self.assertEqual(result["totals"]["execution_latency"], row["execution_latency"])
        self.assertEqual(row["token_components"]["input"]["known"], 1)
        self.assertEqual(row["token_components"]["input"]["unknown"], 2)
        self.assertEqual(row["token_components"]["input"]["sum"], 0)
        self.assertEqual(row["token_components"]["total"]["unknown"], 3)
        self.assertIsNone(row["token_components"]["total"]["sum"])
        self.assertIn("domain=backend", report.format_report(result))

    def test_domain_absence_is_unknown_not_retrospectively_inferred(self):
        result = self._build([_job(task="backend API", role="review")])
        self.assertEqual(set(result["domains"]), {"unknown"})
        self.assertEqual(result["domains"]["unknown"]["attempts"], 1)

    def test_invalid_duration_samples_are_never_zero_filled(self):
        jobs = [_job(ended_at="", elapsed_s=value, job_id=str(index))
                for index, value in enumerate([-1, float("nan"), float("inf"), True, None])]
        result = self._build(jobs)
        row = result["totals"]["execution_latency"]
        self.assertEqual(row["eligible_count"], 5)
        self.assertEqual(row["sample_count"], 0)
        self.assertIsNone(row["p50_s"])
        self.assertIsNone(result["totals"]["median_duration_s"])



if __name__ == "__main__":
    unittest.main()
