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

import benchmark_reporting as bench  # noqa: E402
import billing_ledger as ledger  # noqa: E402
import jobs  # noqa: E402
import rig_mcp  # noqa: E402


ACCEPTED = {"state": "verified", "acceptance": "accepted", "freshness": "current", "reason": "", "snapshot_id": "ab" * 32}
STALE = {"state": "pending", "acceptance": "accepted", "freshness": "current", "reason": "content_changed", "snapshot_id": "ab" * 32}
PENDING = {"state": "pending", "acceptance": "pending", "freshness": "not_checked", "reason": "", "snapshot_id": ""}


def _invoice(receipt_id, provider, amount, cohort, identity):
    return {
        "receipt_id": receipt_id,
        "provider": provider,
        "amount_usd": amount,
        "currency": "USD",
        "period": {"start": "2026-09-01", "end": "2026-09-30"},
        "source_identity": identity,
        "cohort": cohort,
    }


class BenchmarkReporting(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.repo = Path(self.td.name)
        (self.repo / ".git").mkdir()
        (self.repo / ".rig" / "jobs").mkdir(parents=True)
        os.environ.pop("RIG_JOB_ID", None)
        os.environ.pop("RIG_JOB_DIR", None)
        self.spec = {
            "id": "opus-openai-editor",
            "title": "editor",
            "tasks": [f"t{i:02d}" for i in range(20)],
            "arms": {
                "baseline": {"label": "Opus editor", "kind": "prose-only", "cohort": "baseline"},
                "rig": {"label": "OpenAI via Rig", "kind": "rig", "cohort": "rig"},
            },
        }

    def tearDown(self):
        self.td.cleanup()

    def _job(self, job_id, *, usage=None, source="", executor="wrapper"):
        folder = self.repo / ".rig" / "jobs" / job_id
        folder.mkdir(parents=True, exist_ok=True)
        meta = {
            "job_id": job_id, "status": "ok", "executor_kind": executor, "worker": "grok",
            "role": "implement", "exit_code": 0,
        }
        if usage:
            meta["token_usage"] = usage
            meta["token_usage_source"] = source
        (folder / "meta.json").write_text(json.dumps(meta) + "\n")
        return folder

    def _assess(self, mapping):
        def fake(repo, job, refresh=False, cache=None):
            job_id = job.get("job_id") if isinstance(job, dict) else Path(job).name
            return dict(mapping.get(job_id, PENDING))
        return fake

    def test_stale_and_unaccepted_outcomes_rejected(self):
        bench.create_spec(self.repo, self.spec)
        self._job("ok-job")
        self._job("stale-job")
        self._job("open-job")
        with patch.object(bench.verification, "assessment", side_effect=self._assess({"ok-job": ACCEPTED})):
            bench.record_outcome(self.repo, "opus-openai-editor", job_id="ok-job", task="t00", arm="rig")
        with patch.object(bench.verification, "assessment", side_effect=self._assess({"stale-job": STALE})):
            with self.assertRaisesRegex(bench.BenchmarkError, "currently accepted"):
                bench.record_outcome(self.repo, "opus-openai-editor", job_id="stale-job", task="t01", arm="rig")
        with patch.object(bench.verification, "assessment", side_effect=self._assess({"open-job": PENDING})):
            with self.assertRaisesRegex(bench.BenchmarkError, "currently accepted"):
                bench.record_outcome(self.repo, "opus-openai-editor", job_id="open-job", task="t01", arm="baseline")

    def test_opaque_parent_token_coverage_is_unknown(self):
        bench.create_spec(self.repo, {**self.spec, "id": "small", "tasks": ["t00"]})
        self._job("parent-job", executor="parent")
        with patch.object(bench.verification, "assessment", return_value=ACCEPTED):
            bench.record_outcome(self.repo, "small", job_id="parent-job", task="t00", arm="rig")
            report = bench.build_report(self.repo, "small")
        self.assertEqual(report["token_coverage"]["unknown"], 1)
        self.assertEqual(report["token_coverage"]["known"], 0)
        self.assertFalse(report["savings"]["claimed"])
        self.assertFalse(report["savings"]["eligible"])
        self.assertEqual(report["quality"]["gate"], "incomplete")

    def test_incomplete_dollars_and_ineligible_savings(self):
        bench.create_spec(self.repo, self.spec)
        accepted = {}
        for i in range(20):
            rig_id, base_id = f"r{i:02d}", f"b{i:02d}"
            self._job(rig_id, usage={"input": 2, "output": 1}, source="wrapper")
            self._job(base_id, usage={"input": 4, "output": 1}, source="wrapper")
            accepted[rig_id] = ACCEPTED
            accepted[base_id] = ACCEPTED
        with patch.object(bench.verification, "assessment", side_effect=self._assess(accepted)):
            for i in range(20):
                bench.record_outcome(self.repo, "opus-openai-editor", job_id=f"r{i:02d}", task=f"t{i:02d}", arm="rig")
                bench.record_outcome(self.repo, "opus-openai-editor", job_id=f"b{i:02d}", task=f"t{i:02d}", arm="baseline")
            incomplete = bench.build_report(self.repo, "opus-openai-editor")
            self.assertFalse(incomplete["dollar_coverage"]["complete"])
            self.assertEqual(incomplete["savings"]["reason"], "incomplete receipt coverage")
            self.assertFalse(incomplete["savings"]["claimed"])
            self.assertEqual(incomplete["pairs"], 20)
            self.assertEqual(incomplete["quality"]["gate"], "non_inferior")
            self.assertEqual(incomplete["dollar_coverage"]["job_attribution"], "unavailable")
            self.assertIn("individual jobs", incomplete["dollar_coverage"]["note"])
            ledger.import_receipt(self.repo, _invoice("rr", "openai", "20.001", "rig", "openai-org-demo"))
            ledger.import_receipt(self.repo, _invoice("bb", "anthropic", "40.002", "baseline", "anthropic-org-demo"))
            eligible = bench.build_report(self.repo, "opus-openai-editor")
        self.assertTrue(eligible["dollar_coverage"]["complete"])
        self.assertTrue(eligible["same_task_matrix"])
        self.assertEqual(eligible["pairs"], 20)
        self.assertTrue(eligible["savings"]["eligible"])
        self.assertTrue(eligible["savings"]["claimed"])
        self.assertEqual(eligible["savings"]["delta_usd"], "20.001")
        self.assertEqual(eligible["dollar_coverage"]["totals_usd"]["rig"], "20.001")
        self.assertEqual(eligible["dollar_coverage"]["totals_usd"]["baseline"], "40.002")
        self.assertEqual(eligible["quality"]["gate"], "non_inferior")
        self.assertEqual(eligible["dollar_coverage"]["job_attribution"], "unavailable")

    def _paired_report(self, n, spec_id):
        spec = {**self.spec, "id": spec_id, "tasks": [f"t{i:02d}" for i in range(n)]}
        bench.create_spec(self.repo, spec)
        accepted = {}
        for i in range(n):
            self._job(f"r{i:02d}", usage={"input": 1}, source="wrapper")
            self._job(f"b{i:02d}", usage={"input": 1}, source="wrapper")
            accepted[f"r{i:02d}"] = ACCEPTED
            accepted[f"b{i:02d}"] = ACCEPTED
        with patch.object(bench.verification, "assessment", side_effect=self._assess(accepted)):
            for i in range(n):
                bench.record_outcome(self.repo, spec_id, job_id=f"r{i:02d}", task=f"t{i:02d}", arm="rig")
                bench.record_outcome(self.repo, spec_id, job_id=f"b{i:02d}", task=f"t{i:02d}", arm="baseline")
            ledger.import_receipt(self.repo, _invoice("rr", "openai", "1.00", "rig", "openai-org-demo"))
            ledger.import_receipt(self.repo, _invoice("bb", "anthropic", "2.00", "baseline", "anthropic-org-demo"))
            return bench.build_report(self.repo, spec_id)

    def test_nineteen_pairs_cannot_claim_savings(self):
        report = self._paired_report(19, "short")
        self.assertEqual(report["pairs"], 19)
        self.assertFalse(report["savings"]["claimed"])
        self.assertFalse(report["savings"]["eligible"])
        self.assertIn("exactly 20", report["quality"]["reason"])
        self.assertEqual(report["quality"]["gate"], "incomplete")
        self.assertTrue(report["dollar_coverage"]["complete"])

    def test_twenty_pairs_can_claim_savings_when_quality_and_dollars_ok(self):
        report = self._paired_report(20, "exact")
        self.assertEqual(report["pairs"], 20)
        self.assertEqual(report["savings"]["required_pairs"], 20)
        self.assertTrue(report["same_task_matrix"])
        self.assertTrue(report["dollar_coverage"]["complete"])
        self.assertEqual(report["quality"]["gate"], "non_inferior")
        self.assertTrue(report["savings"]["eligible"])
        self.assertTrue(report["savings"]["claimed"])
        self.assertEqual(report["savings"]["delta_usd"], "1.00")
        text = bench.format_report(report)
        self.assertIn("actual provider cohort dollars cannot be allocated to individual jobs", text)
        self.assertEqual(report["dollar_coverage"]["job_attribution"], "unavailable")
        self.assertIn("individual jobs", report["dollar_coverage"]["note"])

    def test_twenty_one_pairs_cannot_claim_savings(self):
        report = self._paired_report(21, "long")
        self.assertEqual(report["pairs"], 21)
        self.assertTrue(report["dollar_coverage"]["complete"])
        self.assertEqual(report["quality"]["gate"], "non_inferior")
        self.assertFalse(report["savings"]["claimed"])
        self.assertFalse(report["savings"]["eligible"])
        self.assertIn("exactly 20", report["savings"]["reason"])
        text = bench.format_report(report)
        self.assertIn("actual provider cohort dollars cannot be allocated to individual jobs", text)

    def test_quality_inferior_blocks_savings_even_with_dollars(self):
        bench.create_spec(self.repo, self.spec)
        accepted = {}
        for i in range(20):
            self._job(f"r{i:02d}")
            self._job(f"b{i:02d}")
            accepted[f"b{i:02d}"] = ACCEPTED
            if i < 10:
                accepted[f"r{i:02d}"] = ACCEPTED
        with patch.object(bench.verification, "assessment", side_effect=self._assess(accepted)):
            for i in range(20):
                bench.record_outcome(self.repo, "opus-openai-editor", job_id=f"b{i:02d}", task=f"t{i:02d}", arm="baseline")
            for i in range(10):
                bench.record_outcome(self.repo, "opus-openai-editor", job_id=f"r{i:02d}", task=f"t{i:02d}", arm="rig")
            ledger.import_receipt(self.repo, _invoice("rr", "openai", "1.00", "rig", "openai-org-demo"))
            ledger.import_receipt(self.repo, _invoice("bb", "anthropic", "9.00", "baseline", "anthropic-org-demo"))
            report = bench.build_report(self.repo, "opus-openai-editor")
        self.assertEqual(report["quality"]["gate"], "incomplete")
        self.assertFalse(report["savings"]["claimed"])
        self.assertLess(report["pairs"], 20)

    def test_editor_baseline_must_be_prose_only(self):
        with self.assertRaisesRegex(bench.BenchmarkError, "prose-only"):
            bench.create_spec(self.repo, {
                **self.spec,
                "arms": {"baseline": {"label": "Opus", "kind": "tools"}, "rig": {"label": "Rig", "kind": "rig"}},
            })

    def test_mcp_create_outcome_and_report(self):
        created = rig_mcp.call_tool("rig_benchmark_create", {"repo": str(self.repo), "spec": {
            "id": "demo", "tasks": ["edit"],
            "arms": {"baseline": {"label": "Opus", "kind": "prose-only"}, "rig": {"label": "Rig", "kind": "rig"}},
        }})
        self.assertNotIn("isError", created)
        self._job("j1", usage={"input": 1}, source="wrapper")
        with patch.object(bench.verification, "assessment", return_value=ACCEPTED):
            out = rig_mcp.call_tool("rig_benchmark_outcome", {
                "repo": str(self.repo), "id": "demo", "job_id": "j1", "task": "edit", "arm": "rig",
            })
            self.assertNotIn("isError", out)
            report = rig_mcp.call_tool("rig_benchmark_report", {"repo": str(self.repo), "id": "demo"})
        self.assertEqual(report["structuredContent"]["pairs"], 0)
        self.assertFalse(report["structuredContent"]["savings"]["claimed"])
        self.assertEqual(report["structuredContent"]["dollar_coverage"]["job_attribution"], "unavailable")
        self.assertIn("individual jobs", report["content"][0]["text"])
        self.assertIn(
            "actual provider cohort dollars cannot be allocated to individual jobs",
            report["content"][0]["text"],
        )
        self.assertIn("individual jobs", report["structuredContent"]["dollar_coverage"]["note"])


if __name__ == "__main__":
    unittest.main()
