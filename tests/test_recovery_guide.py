"""Recovery guidance preserves state and never turns missing evidence into authority."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import recovery_guide as guide
import recovery_view
import rig_mcp


class RecoveryGuide(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name).resolve()
        self.enterContext(patch.dict(os.environ, {"RIG_JOB_ID": "", "RIG_JOB_DIR": ""}))
        self.binding = {"job_id": "work", "attempt_id": "attempt", "reservation_id": "reservation"}
        self.reservation = {**self.binding, "stage": "verifying", "stopped": True, "slot_held": False,
                            "execution_status": "ok", "files": ["app.py"], "resources": []}
        self.write(".rig/jobs/work/meta.json", {**self.binding, "status": "ok"})
        self.write(".rig/reservations/reservation.json", self.reservation)

    def write(self, name, value):
        path = self.repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))
        return path

    def inventory(self):
        return {str(p.relative_to(self.repo)): (p.read_bytes(), p.stat().st_mtime_ns)
                for p in self.repo.rglob("*") if p.is_file()}

    def test_read_only_and_cli_mcp_parity(self):
        before = self.inventory()
        with patch("workflow.show", side_effect=AssertionError("no refresh")), \
             patch("jobs.resolve_job", side_effect=AssertionError("no job refresh")), \
             patch("subprocess.Popen", side_effect=AssertionError("no process")), \
             patch("admission.transaction", side_effect=AssertionError("no admission")):
            expected = guide.build(self.repo, job_id="work")
            actual = rig_mcp.call_tool("rig_recovery_guide", {"repo": str(self.repo), "job_id": "work"})
        self.assertEqual(actual["structuredContent"], expected)
        cli = subprocess.run([sys.executable, str(ROOT / "scripts/recovery_guide.py"), "guide",
                              "--repo", str(self.repo), "--job", "work", "--json"], capture_output=True, text=True)
        self.assertEqual(json.loads(cli.stdout), expected)
        self.assertEqual(self.inventory(), before)
        self.assertIn("parent-acceptance-required", expected["blockers"])

    def test_all_blockers_and_prerequisites(self):
        cases = [("ask", False, {}, {}, "pending-ask"),
                 ("unconfirmed", False, {}, {}, "stop-unconfirmed"),
                 ("native-cancel-required", False, {}, {}, "stop-unconfirmed"),
                 ("fail", True, {}, {}, "execution-unverified"),
                 ("ok", True, {"operation": {"private": "never-copy"}}, {}, "verification-operation"),
                 ("ok", True, {}, {"state": "failed"}, "failed-verification"),
                 ("ok", True, {}, {"acceptance": "rejected"}, "failed-verification"),
                 ("ok", True, {}, {"acceptance": "accepted"}, "acceptance-freshness-unchecked"),
                 ("ok", True, {}, {"acceptance": "accepted", "next": "review"}, "independent-review-required")]
        for state, stopped, extra, verification, blocker in cases:
            with self.subTest(state=state, blocker=blocker):
                self.write(".rig/reservations/reservation.json", {**self.reservation,
                    "execution_status": state, "stopped": stopped, **extra})
                self.write(".rig/jobs/work/verification.json", {**self.binding, **verification})
                result = guide.build(self.repo, job_id="work")
                self.assertIn(blocker, result["blockers"])
                self.assertTrue(all(not item["automatic"] for item in result["steps"]))
                self.assertNotIn("never-copy", json.dumps(result))

    def test_missing_malformed_replaced_or_linked_evidence_stays_unknown(self):
        self.assertEqual(guide.build(self.repo, job_id="missing")["coverage"], "unknown")
        path = self.repo / ".rig/reservations/reservation.json"
        path.write_text("bad JSON")
        self.assertIn("incomplete-evidence", guide.build(self.repo, job_id="work")["blockers"])
        path.unlink()
        path.symlink_to(self.repo / ".rig/jobs/work/meta.json")
        self.assertIn("incomplete-evidence", guide.build(self.repo, job_id="work")["blockers"])
        self.write(".rig/workflows/flow/state.json", {"workflow_id": "flow", "status": "blocked", "nodes": {
            "node": {"job_id": "work", "attempt_id": "old"}}})
        result = guide.build(self.repo, workflow_id="flow")
        self.assertIn("workflow-blocked", result["blockers"])
        self.assertEqual(result["held"], [])

    def test_private_fields_and_identifiers_are_not_echoed(self):
        self.write(".rig/reservations/reservation.json", {**self.reservation,
            "owner_token": "never-copy", "files": [".env", "app.py"], "resources": [
                {"name": "secret-db", "access": "write"}]})
        self.write(".rig/jobs/work/verification.json", {**self.binding, "rationale": "never-copy"})
        result = guide.build(self.repo, job_id="work")
        self.assertNotIn("never-copy", json.dumps(result))
        self.assertNotIn("secret-db", json.dumps(result))
        self.assertEqual(result["held"][0]["files"], ["app.py"])
        for arguments in ({}, {"job_id": "work", "workflow_id": "flow"},
                          {"job_id": "work", "workflow_id": ""}, {"job_id": "../work"}):
            with self.assertRaises(ValueError):
                guide.build(self.repo, **arguments)

    def test_malformed_completion_never_suggests_acceptance(self):
        for value in ("true", 1, None):
            self.write(".rig/reservations/reservation.json", {**self.reservation, "stopped": value})
            result = guide.build(self.repo, job_id="work")
            self.assertIn("incomplete-evidence", result["blockers"])
            self.assertNotIn("rig_job_accept", [row["operation"] for row in result["steps"]])

    def test_verification_uses_persisted_attempt_reservation_contract(self):
        self.write(".rig/jobs/work/verification.json", {"attempt_id": "attempt",
                   "reservation_id": "reservation", "acceptance": "accepted", "next": "review"})
        result = guide.build(self.repo, job_id="work")
        self.assertIn("independent-review-required", result["blockers"])
        self.assertEqual(result["coverage"], "recorded")

    def test_recorded_stop_overrides_running_and_pending_ask(self):
        self.write(".rig/reservations/reservation.json", {**self.reservation,
                   "execution_status": "running", "stopped": False, "slot_held": True})
        self.write(".rig/jobs/work/ask.json", {"attempt_id": "attempt", "reservation_id": "reservation",
                   "ask_id": "question", "preview": "private never-copy"})
        self.assertIn("pending-ask", guide.build(self.repo, job_id="work")["blockers"])
        self.write(".rig/jobs/work/cancellation/attempt.json", self.binding)
        result = guide.build(self.repo, job_id="work")
        self.assertIn("stop-unconfirmed", result["blockers"])
        self.assertNotIn("rig_job_wait", [row["operation"] for row in result["steps"]])
        self.assertNotIn("never-copy", json.dumps(result))

    def test_answered_prompt_and_old_stop_marker_cannot_bind_new_attempt(self):
        self.write(".rig/reservations/reservation.json", {**self.reservation,
                   "execution_status": "running", "stopped": False})
        request = {"attempt_id": "attempt", "reservation_id": "reservation", "ask_id": "question"}
        self.write(".rig/jobs/work/ask.json", request)
        self.write(".rig/jobs/work/ask-reply.json", {**request, "behavior": "allow"})
        self.assertNotIn("pending-ask", guide.build(self.repo, job_id="work")["blockers"])
        self.write(".rig/jobs/work/cancellation/attempt.json", {**self.binding, "attempt_id": "old"})
        self.assertIn("incomplete-evidence", guide.build(self.repo, job_id="work")["blockers"])

    def test_workflow_stop_record_prevents_wait_advice_before_scheduler_updates(self):
        self.write(".rig/reservations/reservation.json", {**self.reservation,
                   "execution_status": "running", "stopped": False})
        self.write(".rig/workflows/flow/state.json", {"workflow_id": "flow", "status": "running",
                   "created_at": "created", "nodes": {"node": {"job_id": "work", "attempt_id": "attempt"}}})
        self.write(".rig/workflows/flow/cancel.json", {"workflow_id": "flow", "created_at": "created"})
        result = guide.build(self.repo, workflow_id="flow")
        self.assertIn("recorded-workflow-stop-intent", result["blockers"])
        self.assertNotIn("rig_job_wait", [row["operation"] for row in result["steps"]])

    def test_child_cannot_read_or_list_guidance(self):
        with patch.dict(os.environ, {"RIG_JOB_ID": "child"}):
            with self.assertRaisesRegex(ValueError, "parent-only"):
                guide.build(self.repo, job_id="work")
            self.assertTrue(rig_mcp.call_tool("rig_recovery_guide", {"job_id": "work"})["isError"])
            self.assertNotIn("rig_recovery_guide", {t["name"] for t in rig_mcp.listed_tools()})
