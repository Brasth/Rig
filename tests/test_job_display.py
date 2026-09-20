"""Truthful status precedence and bounded content validation on user surfaces."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from benchmark_normal_prompt import Fixture, seed_job
import change_evidence
import jobs
import rig_mcp


class DisplayPrecedence(unittest.TestCase):
    def test_execution_and_owner_states_override_old_acceptance(self):
        accepted = {"state": "verified", "acceptance": "accepted", "freshness": "current"}
        for effective, expected in (("ask", "needs-input"), ("cancelled", "cancelled"),
                                    ("fail", "failed"), ("timeout", "failed"), ("stale", "failed"),
                                    ("unconfirmed", "needs-input"),
                                    ("running", "working"), ("reserved", "reserved"), ("ok", "verified")):
            with self.subTest(effective=effective):
                self.assertEqual(jobs.job_display_state({"effective": effective}, verification=accepted), expected)
        self.assertEqual(jobs.job_display_state({"effective": "ok"}, {"needs_reconciliation": True}, accepted), "needs-input")
        self.assertEqual(jobs.job_display_state({"effective": "unconfirmed"}, verification=accepted), "needs-input")
        self.assertEqual(jobs.job_display_state({"effective": "stale"}, {"needs_reconciliation": True}, accepted), "needs-input")

    def test_held_scope_alone_does_not_claim_verification_is_running(self):
        held = {"stage": "verifying", "stopped": True}
        self.assertEqual(jobs.job_display_state({"effective": "ok"}, held), "completed-unverified")
        self.assertEqual(jobs.job_display_state({"effective": "running", "role": "review"}, held), "verifying")
        self.assertEqual(jobs.job_display_state({"effective": "ok"}, held,
                         {"state": "verifying", "active_check": {"name": "unit"}}), "verifying")
        self.assertEqual(jobs.job_display_state({"effective": "ok"}, held,
                         {"state": "verified", "acceptance": "accepted", "freshness": "not_checked"}), "completed-unverified")

    def test_cancelled_scope_and_unknown_owner_have_explicit_actions(self):
        row = jobs.project_job({"job_id": "task", "effective": "cancelled", "reservation": {
            "stage": "running", "stopped": False}})
        self.assertIn("stopping; files held", row["display_reason"])
        row["reservation"]["needs_reconciliation"] = True
        row = jobs.project_job(row)
        self.assertEqual(row["display_state"], "cancelled")
        self.assertEqual(row["display_action"], "rig job reconcile task")
        self.assertIn("stopping; files held", row["display_reason"])

    def test_provider_independence_does_not_claim_review_completion(self):
        row = {"effective": "running", "role": "review", "writer_job_id": "writer",
               "writer_provider": "openai", "model": "claude-sonnet-4-6", "model_source": "selected"}
        projected = jobs.project_job(row)
        self.assertEqual(projected["independence"], "confirmed")
        self.assertFalse(projected["review_completed"])
        self.assertEqual(jobs.project_job({**row, "model_inferred": True})["independence"], "unknown")
        self.assertEqual(jobs.project_job({**row, "model": "gpt-5.6-luna"})["independence"], "unavailable")


class HudAndHashBudget(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="rig-display-")
        self.addCleanup(temp.cleanup)
        self.fixture = Fixture(Path(temp.name))
        self.repo = self.fixture.repo

    def test_recent_terminal_expires_without_releasing_or_hashing_history(self):
        self.fixture.seed_accepted(100, "disjoint")
        with self.fixture.isolated():
            self.fixture.prepare_catalog("static")
            snap = jobs.hud_snapshot(repo=self.repo)
            self.assertEqual(snap["display_state"], "verified")
            self.assertEqual(snap["status"], "idle")
            self.assertEqual(self.fixture.counts["subject_hashes"], 1)
            self.fixture.accepted_clock += 61
            self.fixture.prepare_catalog("static")
            expired = jobs.hud_snapshot(repo=self.repo)
            self.assertEqual(expired["display_state"], "idle")
            self.assertEqual(self.fixture.counts["subject_hashes"], 0)

    def test_ask_priority_actions_and_read_only_refresh(self):
        self.fixture.seed_accepted(100, "disjoint")
        folder = seed_job(self.repo, "ask-worker", "ask")
        seed_job(self.repo, "working-worker", "running")
        before = {path: path.read_bytes() for path in (self.repo / ".rig").rglob("*.json")}
        with self.fixture.isolated():
            self.fixture.prepare_catalog("static")
            snap = jobs.hud_snapshot(repo=self.repo)
            self.assertEqual(snap["status"], "ask")
            self.assertEqual(snap["display_state"], "needs-input")
            short = "\n".join(snap["lines"][:2])
            self.assertIn("ask-worker", short)
            self.assertIn("rig job allow ask-worker", short)
            self.assertIn("rig job deny ask-worker", short)
            self.assertEqual(snap["remaining"], 1)
            self.assertEqual(self.fixture.counts["subject_hashes"], 0)
            self.assertEqual(self.fixture.counts["job_directory_scans"], 1)
            self.assertEqual(self.fixture.counts["load_job_calls"], 102)
        self.assertEqual(before, {path: path.read_bytes() for path in (self.repo / ".rig").rglob("*.json")})
        self.assertTrue((folder / "ask.json").is_file())

    def test_compact_hashes_only_emitted_unique_subjects(self):
        for layout, expected in (("shared", 1), ("disjoint", 10)):
            with self.subTest(layout=layout), tempfile.TemporaryDirectory() as temporary:
                fixture = Fixture(Path(temporary))
                fixture.seed_accepted(1000, layout)
                with fixture.isolated():
                    for compact, expected_hashes in ((False, 0), (True, expected)):
                        fixture.prepare_catalog("static")
                        response = json.loads(rig_mcp.format_session(fixture.repo, "Report", "stay", as_json=True, compact=compact))
                        self.assertEqual(len(response["jobs"]), 10 if compact else 1000)
                        self.assertEqual(fixture.counts["subject_hashes"], expected_hashes)
                        self.assertLessEqual(fixture.counts["max_hashes_per_subject"], 1)
                        self.assertTrue(all(row["display_state"] == ("verified" if compact else "completed-unverified")
                                            for row in response["jobs"]))

    def test_changed_selected_subject_is_unverified_everywhere(self):
        self.fixture.seed_accepted(1, "shared")
        subject = self.repo / "subjects" / "shared.txt"
        subject.write_text("changed\n")
        with self.fixture.isolated():
            self.fixture.prepare_catalog("static")
            snap = jobs.hud_snapshot(repo=self.repo)
            self.assertEqual(snap["display_state"], "completed-unverified")
            self.assertIn("content_changed", snap["text"])
            job = jobs.resolve_job(self.repo, "accepted-000000")
            self.assertIn("display completed-unverified", jobs.format_show(job))
            self.assertIn("completed-unverified", jobs.format_table([job]))
            self.assertIn("completed-unverified", jobs.wait_job(self.repo, job["job_id"])[1])


class WorkflowHudDisplay(unittest.TestCase):
    def test_display_fields_are_factual_without_estimates(self):
        from tui_view import _workflow_detail_lines
        from ui_snapshot import format_parent_action, public_workflow_row, scrub_secrets
        secret = "0123456789abcdef0123456789abcdef"
        row = public_workflow_row({
            "workflow_id": "wf-display", "status": "attention", "accepted": 2, "required": 4,
            "running": 0, "ask": 1, "blocker": "parent acceptance required",
            "next_parent_action": {"kind": "allow_or_deny", "node_id": "n2", "job_id": "job-1",
                                   "owner_token": secret},
            "owner_token": secret, "percent": 50, "eta": "3m", "estimated_savings": "2h",
        })
        self.assertEqual(row["workflow_id"], "wf-display")
        self.assertEqual((row["accepted"], row["required"], row["running"], row["ask"]), (2, 4, 0, 1))
        self.assertEqual(row["blocker"], "parent acceptance required")
        self.assertEqual(format_parent_action(row["next_parent_action"]),
                         "allow_or_deny node_id n2 job_id job-1")
        blob = json.dumps(row)
        self.assertNotIn(secret, blob)
        self.assertNotIn("owner_token", blob)
        self.assertNotIn("percent", blob)
        self.assertNotIn("eta", blob)
        self.assertNotIn("savings", blob)
        detail = "\n".join(_workflow_detail_lines(row))
        self.assertIn("wf-display", detail)
        self.assertIn("accepted/required 2/4", detail)
        self.assertIn("running 0  ask 1", detail)
        self.assertIn("blocker parent acceptance required", detail)
        self.assertIn("next parent action allow_or_deny", detail)
        self.assertNotIn("%", detail)
        self.assertNotIn("ETA", detail)
        self.assertEqual(scrub_secrets({"owner_token": secret, "ok": 1}), {"ok": 1})


class TokenAndContinueDisplay(unittest.TestCase):
    def test_format_tokens_compacts_present_fields_and_never_synthesizes_total(self):
        self.assertEqual(jobs.format_tokens(None), "")
        self.assertEqual(jobs.format_tokens({}), "")
        self.assertEqual(jobs.format_tokens({"input": 12, "output": 34}), "12 in / 34 out")
        self.assertNotIn("total", jobs.format_tokens({"input": 12, "output": 34}))
        self.assertEqual(
            jobs.format_tokens({"input": 12, "output": 34, "cached_input": 5, "reasoning": 8}),
            "12 in / 34 out / 5 cached / 8 reasoning",
        )
        self.assertEqual(
            jobs.format_tokens({"input": 12, "output": 34, "total": 46}),
            "12 in / 34 out / 46 total",
        )
        self.assertEqual(jobs.format_tokens({"input": 999}), "999 in")
        self.assertEqual(jobs.format_tokens({"input": 1000}), "1k in")
        self.assertEqual(jobs.format_tokens({"output": 1500}), "1.5k out")
        self.assertEqual(jobs.format_tokens({"total": 1_000_000}), "1m total")
        self.assertEqual(jobs.format_tokens({"total": 1_500_000}), "1.5m total")

    def test_format_show_includes_continues_line(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        job_dir = Path(td.name) / "cont-show"
        jobs.write_job_files(
            job_dir,
            "cont-show",
            "grok",
            "implement",
            "ok",
            0,
            "2026-09-10T07:00:00Z",
            "2026-09-10T07:01:00Z",
            "done",
            continues_job_id="prior-ok",
        )
        loaded = jobs.load_job(job_dir)
        shown = jobs.format_show(loaded)
        self.assertIn("continues prior-ok", shown)
        self.assertNotIn("tokens", shown)


if __name__ == "__main__":
    unittest.main()
