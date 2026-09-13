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
                                    ("running", "working"), ("reserved", "reserved"), ("ok", "verified")):
            with self.subTest(effective=effective):
                self.assertEqual(jobs.job_display_state({"effective": effective}, verification=accepted), expected)
        self.assertEqual(jobs.job_display_state({"effective": "ok"}, {"needs_reconciliation": True}, accepted), "needs-input")

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


if __name__ == "__main__":
    unittest.main()
