"""One recovery instruction survives job, workflow, MCP and human displays."""
import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import admission
import jobs
import rig_mcp
import tui_view
import ui_popup_view
import ui_snapshot
import workflow_state as wf


def job_row(status="unconfirmed", *, kind="wrapper", operation=None):
    record = {
        "job_id": "task", "reservation_id": "reservation", "attempt_id": "attempt",
        "stage": "verifying" if status in {"ok", "fail", "cancelled"} else "running",
        "stopped": status in {"ok", "fail", "cancelled"}, "execution_status": status,
        "launch_started": True, "files": ["a.py"], "slot_held": status == "unconfirmed",
        "owner": {"kind": kind},
    }
    if operation:
        record["operation"] = operation
    return {"job_id": "task", "status": status, "effective": status,
            "worker": "codex", "role": "implement", "task": "Task", "doing": "",
            "executor_kind": kind, "reservation_id": "reservation", "attempt_id": "attempt",
            "files": ["a.py"], "reservation": record}


class JobActions(unittest.TestCase):
    def test_same_guidance_in_compact_mcp_tui_popup_and_job_table(self):
        cases = [("unconfirmed", "wrapper", None), ("unconfirmed", "native_child", None),
                 ("fail", "wrapper", None), ("cancelled", "wrapper", None),
                 ("ok", "wrapper", None), ("fail", "wrapper", {"operation": "check"})]
        for status, kind, operation in cases:
            with self.subTest(status=status, kind=kind, operation=operation):
                source = job_row(status, kind=kind, operation=operation)
                before = copy.deepcopy(source)
                row = jobs.project_job(source)
                action = row["ownership_next_action"]
                self.assertTrue(action["reason"])
                compact = rig_mcp._compact_rows([source], 10, repo=Path("/synthetic"))[0]
                self.assertEqual(compact["ownership_next_action"], action)
                displays = [" ".join(ui_popup_view.detail_lines(row, 2000)),
                            " ".join(tui_view._detail_lines(row)), jobs.format_table([row])]
                for display in displays:
                    self.assertIn(action["instruction"], display)
                    for line in admission.ownership_action_details(action):
                        self.assertIn(line, display)
                self.assertEqual(source, before)

    def test_ask_priority_and_native_cancel_instruction_are_preserved(self):
        source = job_row("ask")
        source["reservation"]["needs_reconciliation"] = True
        source["ask"] = {"tool_name": "Bash", "preview": "inspect requested command"}
        row = jobs.project_job(source)
        self.assertIsNone(row["ownership_next_action"])
        for display in (" ".join(ui_popup_view.detail_lines(row, 2000)),
                        " ".join(tui_view._detail_lines(row))):
            self.assertIn("rig job allow task", display)
            self.assertIn("rig job deny task", display)
            self.assertNotIn("rig_job_finish", display)
            self.assertNotIn("rig_job_close", display)
        source = job_row("unconfirmed", kind="native_child")
        source["cancellation"] = {"state": "native-cancel-required"}
        # The existing cancellation parser owns precedence; this feature does not alter it.
        with patch("cancellation.state", return_value="native-cancel-required"):
            row = jobs.project_job(source)
        self.assertIn("Owning host: interrupt agent", row["display_action"])
        self.assertEqual(row["ownership_next_action"]["actor"], "owning host, then parent")

    def test_live_and_released_scopes_never_offer_mutations(self):
        for status, stage in (("running", "running"), ("ok", "released")):
            row = job_row(status)
            row["reservation"]["stage"] = stage
            projected = jobs.project_job(row)
            self.assertIsNone(projected["ownership_next_action"])
            for display in (" ".join(ui_popup_view.detail_lines(projected)),
                            " ".join(tui_view._detail_lines(projected))):
                self.assertNotIn("rig_job_close", display)
                self.assertNotIn("rig_job_finish", display)

    def test_popup_redacts_credentials_but_retains_required_evidence(self):
        row = jobs.project_job(job_row("fail"))
        row["ownership_next_action"]["owner_token"] = "secret-token-value"
        row["ownership_next_action"]["credentials_path"] = "/private/receipt"
        text = " ".join(ui_popup_view.detail_lines(row, 2000))
        self.assertNotIn("secret-token-value", text)
        self.assertNotIn("/private/receipt", text)
        self.assertIn("saved credentials_path or exact owner credentials", text)


class WorkflowActions(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        (self.repo / ".git").mkdir()
        (self.repo / ".rig" / "jobs" / "task").mkdir(parents=True)
        (self.repo / ".rig" / "reservations").mkdir()
        (self.repo / ".rig" / "harness.toml").write_text('parent="codex"\n[workers]\ncodex=false\n')
        (self.repo / "a.py").write_text("pass\n")
        self.spec = {"workflow_id": "flow", "title": "Task", "nodes": [
            {"id": "node", "role": "implement", "files": ["a.py"], "required": True}]}
        self.state = {"workflow_id": "flow", "status": "running", "nodes": {
            "node": {"status": "running", "job_id": "task", "launched": True}}, "metrics": {}}

    def seed(self, source):
        meta = {key: value for key, value in source.items() if key not in {"reservation", "effective"}}
        folder = self.repo / ".rig" / "jobs" / "task"
        (folder / "meta.json").write_text(json.dumps(meta))
        (self.repo / ".rig" / "reservations" / "reservation.json").write_text(json.dumps(source["reservation"]))
        if source["status"] == "ask":
            # ASK is detected from a running job's actual pending request.
            meta["status"] = "running"
            (folder / "meta.json").write_text(json.dumps(meta))
            (folder / "ask.json").write_text(json.dumps({"tool_name": "Bash", "tool_use_id": "ask-id"}))
        wf.refresh_locked(self.repo, self.spec, self.state)

    def test_current_job_guidance_reaches_workflow_displays_and_session(self):
        for status, expected in (("unconfirmed", "reconcile"), ("fail", "resolve"), ("ok", "accept")):
            with self.subTest(status=status):
                self.state = {"workflow_id": "flow", "status": "running", "nodes": {
                    "node": {"status": "running", "job_id": "task", "launched": True}}, "metrics": {}}
                source = job_row(status, kind="native_child")
                self.seed(source)
                action = wf.next_parent_action(self.spec, self.state)
                job_action = jobs.project_job(source)["ownership_next_action"]
                self.assertEqual(action["kind"], expected)  # existing workflow kinds remain compatible
                self.assertEqual(action["ownership_next_action"], job_action)
                self.assertEqual(action["instruction"], job_action["instruction"])
                public = wf.public_record(self.spec, self.state)
                summary = wf.summary_row(self.spec, self.state)
                self.assertEqual(public["next_parent_action"], summary["next_parent_action"])
                ui = ui_snapshot.public_workflow_row(summary)
                for display in (" ".join(ui_popup_view.workflow_detail_lines(ui, 2000)),
                                " ".join(tui_view._workflow_detail_lines(ui))):
                    self.assertIn(job_action["instruction"], display)
                    for line in admission.ownership_action_details(job_action):
                        self.assertIn(line, display)
                wf.save_spec(self.repo, self.spec)
                wf.save_state(self.repo, self.state)
                self.assertEqual(wf.list_workflows(self.repo)[0]["next_parent_action"], action)
                with patch.object(rig_mcp.rig_harness, "live_parent", return_value="codex"), \
                     patch.object(rig_mcp.rig_harness, "effective_workers", return_value={}), \
                     patch.object(rig_mcp.rig_harness, "format_status", return_value=""), \
                     patch.object(rig_mcp.rig_route, "pick", return_value={"spawn": "stay"}), \
                     patch.object(rig_mcp.rig_memory, "show_memory", return_value=""), \
                     patch.object(rig_mcp.rig_jobs, "list_jobs", return_value=[source]):
                    for compact in (False, True):
                        session = json.loads(rig_mcp.format_session(self.repo, "Inspect", "stay", as_json=True, compact=compact))
                        self.assertEqual(session["workflows"][0]["next_parent_action"], action)

    def test_reading_unchanged_unconfirmed_workflow_is_repeatable_not_a_reconcile_loop(self):
        self.seed(job_row())
        wf.save_spec(self.repo, self.spec)
        wf.save_state(self.repo, self.state)
        before = {p: p.read_bytes() for p in (self.repo / ".rig").rglob("*.json")}
        first = wf.list_workflows(self.repo)[0]["next_parent_action"]
        second = wf.list_workflows(self.repo)[0]["next_parent_action"]
        self.assertEqual(first, second)
        self.assertEqual(first["tool"], "rig_job_finish")
        self.assertFalse(first["report_only"])
        self.assertIn("do not repeat", first["instruction"])
        self.assertIn("termination", "; ".join(first["requires"]))
        self.assertEqual(before, {p: p.read_bytes() for p in (self.repo / ".rig").rglob("*.json")})

    def _saved_unconfirmed(self):
        self.seed(job_row("unconfirmed"))
        wf.save_spec(self.repo, self.spec)
        wf.save_state(self.repo, self.state)
        self.assertEqual(ui_snapshot.collect_workflows(self.repo)[0]["next_parent_action"]["tool"], "rig_job_finish")

    def _current_read_actions(self):
        before = {p: p.read_bytes() for p in (self.repo / ".rig").rglob("*.json")}
        actions = [wf.list_workflows(self.repo)[0]["next_parent_action"],
                   ui_snapshot.collect_workflows(self.repo)[0]["next_parent_action"],
                   ui_snapshot.workflow_detail(self.repo, "flow")["next_parent_action"]]
        with patch.object(rig_mcp.rig_harness, "live_parent", return_value="codex"), \
             patch.object(rig_mcp.rig_harness, "effective_workers", return_value={}), \
             patch.object(rig_mcp.rig_harness, "format_status", return_value=""), \
             patch.object(rig_mcp.rig_route, "pick", return_value={"spawn": "stay"}), \
             patch.object(rig_mcp.rig_memory, "show_memory", return_value=""):
            for compact in (False, True):
                session = json.loads(rig_mcp.format_session(self.repo, "Inspect", "stay", as_json=True, compact=compact))
                actions.append(session["workflows"][0]["next_parent_action"])
        self.assertTrue(all(action == actions[0] for action in actions))
        self.assertEqual(before, {p: p.read_bytes() for p in (self.repo / ".rig").rglob("*.json")})
        return actions[0]

    def test_reads_drop_released_guidance_without_workflow_refresh(self):
        self._saved_unconfirmed()
        path = self.repo / ".rig" / "reservations" / "reservation.json"
        record = json.loads(path.read_text())
        record.update(stage="released", stopped=True, execution_status="fail")
        path.write_text(json.dumps(record))
        action = self._current_read_actions()
        self.assertEqual(action["tool"], "rig_job_show")
        self.assertTrue(action["report_only"])
        self.assertNotIn("ownership_next_action", action)

    def test_reads_drop_replaced_attempt_without_workflow_refresh(self):
        self._saved_unconfirmed()
        path = self.repo / ".rig" / "reservations" / "reservation.json"
        record = json.loads(path.read_text())
        record["attempt_id"] = "replacement"
        path.write_text(json.dumps(record))
        action = self._current_read_actions()
        self.assertEqual(action["tool"], "rig_job_show")
        meta_path = self.repo / ".rig" / "jobs" / "task" / "meta.json"
        meta = json.loads(meta_path.read_text())
        meta["attempt_id"] = "replacement"
        meta_path.write_text(json.dumps(meta))
        action = self._current_read_actions()
        self.assertEqual(action["tool"], "rig_job_show")
        self.assertTrue(action["report_only"])

    def test_reads_prioritize_new_ask_without_workflow_refresh(self):
        self._saved_unconfirmed()
        folder = self.repo / ".rig" / "jobs" / "task"
        meta = json.loads((folder / "meta.json").read_text())
        meta["status"] = "running"
        (folder / "meta.json").write_text(json.dumps(meta))
        (folder / "ask.json").write_text(json.dumps({"tool_name": "Bash", "tool_use_id": "new-ask"}))
        action = self._current_read_actions()
        self.assertEqual(action["kind"], "allow_or_deny")
        self.assertNotIn("rig_job_finish", json.dumps(action))
        self.assertNotIn("rig_job_close", json.dumps(action))

    def test_reads_inspect_new_check_without_workflow_refresh(self):
        self._saved_unconfirmed()
        path = self.repo / ".rig" / "reservations" / "reservation.json"
        record = json.loads(path.read_text())
        record["operation"] = {"operation": "check"}
        path.write_text(json.dumps(record))
        action = self._current_read_actions()
        self.assertEqual(action["tool"], "rig_job_show")
        self.assertEqual(action["ownership_next_action"]["kind"], "inspect_verification")
        self.assertTrue(action["report_only"])
        self.assertNotIn("rig_job_finish", action["instruction"])

    def test_answered_ask_does_not_survive_in_saved_workflow_guidance(self):
        self.seed(job_row("ask"))
        wf.save_spec(self.repo, self.spec)
        wf.save_state(self.repo, self.state)
        self.assertEqual(self._current_read_actions()["kind"], "allow_or_deny")
        (self.repo / ".rig" / "jobs" / "task" / "ask.json").unlink()
        self.assertNotEqual(self._current_read_actions()["kind"], "allow_or_deny")

    def test_sibling_ask_has_same_priority_in_refreshed_and_read_only_views(self):
        self.seed(job_row("fail"))
        self.spec["nodes"].append({"id": "sibling", "role": "implement", "files": ["b.py"], "required": True})
        self.state["nodes"]["sibling"] = {"status": "running", "job_id": "asking", "launched": True}
        folder = self.repo / ".rig" / "jobs" / "asking"
        folder.mkdir()
        (folder / "meta.json").write_text(json.dumps({"job_id": "asking", "status": "running", "worker": "codex"}))
        (folder / "ask.json").write_text(json.dumps({"tool_name": "Bash", "tool_use_id": "sibling-ask"}))
        self.state["parent_action"] = {"kind": "parent_writes", "node_id": "node", "job_id": "task"}
        wf.refresh_locked(self.repo, self.spec, self.state)
        wf.save_spec(self.repo, self.spec)
        wf.save_state(self.repo, self.state)
        action = wf.public_record(self.spec, self.state)["next_parent_action"]
        self.assertEqual(action, self._current_read_actions())
        self.assertEqual(action["kind"], "allow_or_deny")
        self.assertEqual(action["job_id"], "asking")
        self.assertNotIn("rig_job_close", json.dumps(action))
        self.state["cancel_requested"] = True
        wf.save_state(self.repo, self.state)
        self.assertEqual(wf.next_parent_action(self.spec, self.state)["kind"], "confirm_stop")
        self.assertEqual(self._current_read_actions()["kind"], "confirm_stop")

    def test_refresh_does_not_rebind_guidance_to_a_replaced_attempt(self):
        self._saved_unconfirmed()
        for path in (self.repo / ".rig" / "reservations" / "reservation.json",
                     self.repo / ".rig" / "jobs" / "task" / "meta.json"):
            data = json.loads(path.read_text())
            data["attempt_id"] = "replacement"
            path.write_text(json.dumps(data))
        wf.refresh_locked(self.repo, self.spec, self.state)
        action = wf.next_parent_action(self.spec, self.state)
        self.assertEqual(self.state["nodes"]["node"]["attempt_id"], "attempt")
        self.assertEqual(action["tool"], "rig_job_show")
        self.assertTrue(action["report_only"])
        wf.save_state(self.repo, self.state)
        self.assertEqual(action, self._current_read_actions())

    def test_missing_or_malformed_ask_metadata_is_diagnostic_only(self):
        self.seed(job_row("ask"))
        wf.save_spec(self.repo, self.spec)
        wf.save_state(self.repo, self.state)
        path = self.repo / ".rig" / "jobs" / "task" / "meta.json"
        for malformed in (False, True):
            if malformed:
                path.write_text("{")
            else:
                path.unlink()
            action = self._current_read_actions()
            self.assertEqual(action["tool"], "rig_job_show")
            self.assertTrue(action["report_only"])
            self.assertNotEqual(action["kind"], "allow_or_deny")
            if malformed:
                # Refresh retains its existing fail-closed malformed-record error.
                with self.assertRaises(admission.AdmissionError):
                    wf.refresh_locked(self.repo, self.spec, self.state)
            else:
                wf.refresh_locked(self.repo, self.spec, self.state)
                self.assertEqual(wf.next_parent_action(self.spec, self.state), action)

    def test_replacement_ask_never_becomes_workflow_approval(self):
        self._saved_unconfirmed()
        meta_path = self.repo / ".rig" / "jobs" / "task" / "meta.json"
        for path in (self.repo / ".rig" / "reservations" / "reservation.json", meta_path):
            data = json.loads(path.read_text())
            data["attempt_id"] = "replacement"
            if path == meta_path:
                data["status"] = "running"
            path.write_text(json.dumps(data))
        (meta_path.parent / "ask.json").write_text(json.dumps({"tool_name": "Bash", "tool_use_id": "replacement-ask"}))
        before = self._current_read_actions()
        wf.refresh_locked(self.repo, self.spec, self.state)
        wf.save_state(self.repo, self.state)
        after = self._current_read_actions()
        self.assertEqual(before, after)
        self.assertEqual(wf.next_parent_action(self.spec, self.state), after)
        self.assertEqual(after["tool"], "rig_job_show")
        self.assertTrue(after["report_only"])
        self.assertNotEqual(after["kind"], "allow_or_deny")
        self.assertEqual(self.state["nodes"]["node"]["attempt_id"], "attempt")

    def test_individual_native_cancellation_guidance_is_consistent(self):
        self.seed(job_row("running", kind="parent"))
        self.state["parent_action"] = {"kind": "parent_writes", "node_id": "node", "job_id": "task"}
        folder = self.repo / ".rig" / "jobs" / "task" / "cancellation"
        folder.mkdir()
        (folder / "attempt.json").write_text(json.dumps({"job_id": "task", "attempt_id": "attempt", "reservation_id": "reservation"}))
        wf.refresh_locked(self.repo, self.spec, self.state)
        wf.save_spec(self.repo, self.spec)
        wf.save_state(self.repo, self.state)
        action = wf.next_parent_action(self.spec, self.state)
        self.assertEqual(action, self._current_read_actions())
        self.assertEqual(action["ownership_next_action"]["kind"], "confirm_completion")
        self.assertEqual(action["actor"], "owning host, then parent")
        self.assertIn("owning host", "; ".join(action["requires"]))

    def test_parent_action_keeps_priority_but_gets_current_job_guidance(self):
        self.state["parent_action"] = {"kind": "parent_writes", "node_id": "node", "job_id": "task"}
        self.seed(job_row("unconfirmed", kind="parent"))
        action = wf.next_parent_action(self.spec, self.state)
        self.assertEqual(action["kind"], "parent_writes")
        self.assertEqual(action["ownership_next_action"]["kind"], "confirm_completion")
        self.assertEqual(action["actor"], "owning host, then parent")

    def test_mismatched_attempt_does_not_supply_ownership_guidance(self):
        source = job_row("unconfirmed")
        source["reservation"]["attempt_id"] = "other-attempt"
        self.seed(source)
        action = wf.next_parent_action(self.spec, self.state)
        self.assertEqual(action["tool"], "rig_job_show")
        self.assertTrue(action["report_only"])
        self.assertNotIn("ownership_next_action", action)

    def test_no_receipt_metadata_uses_diagnostic_inspection_not_invented_completion(self):
        self.state["nodes"]["node"]["status"] = "unconfirmed"
        action = wf.next_parent_action(self.spec, self.state)
        self.assertEqual(action["kind"], "reconcile")
        self.assertEqual(action["tool"], "rig_job_show")
        self.assertTrue(action["report_only"])
        self.assertIn("Do not repeat", action["instruction"])
        self.assertNotIn("rig_job_finish", action["instruction"])

    def test_ask_suppresses_recovery_and_completed_checks_refresh_guidance(self):
        source = job_row("ask")
        source["reservation"]["needs_reconciliation"] = True
        self.seed(source)
        self.assertEqual(wf.next_parent_action(self.spec, self.state)["kind"], "allow_or_deny")
        self.assertIsNone(self.state["nodes"]["node"]["ownership_next_action"])
        (self.repo / ".rig" / "jobs" / "task" / "ask.json").unlink()
        self.seed(job_row("fail", operation={"operation": "check"}))
        action = wf.next_parent_action(self.spec, self.state)
        self.assertEqual(action["ownership_next_action"]["kind"], "inspect_verification")
        self.assertEqual(action["tool"], "rig_job_show")
        self.seed(job_row("fail"))
        self.assertEqual(wf.next_parent_action(self.spec, self.state)["tool"], "rig_job_close")
        (self.repo / ".rig" / "jobs" / "task" / "meta.json").unlink()
        wf.refresh_locked(self.repo, self.spec, self.state)
        self.assertNotIn("ownership_next_action", self.state["nodes"]["node"])
