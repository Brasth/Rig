"""Task timelines join only recorded scoped evidence without lifecycle mutations."""
import copy
import hashlib
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
import rig_mcp
import task_timeline as timeline

T0 = "2026-09-30T10:00:00+00:00"
T1 = "2026-09-30T10:01:00+00:00"
T2 = "2026-09-30T10:02:00+00:00"


class TaskTimeline(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name).resolve()
        self.enterContext(patch.dict(os.environ, {"RIG_JOB_ID": "", "RIG_JOB_DIR": "", "RIG_PARENT": "codex",
                                                "RIG_HOME": str(ROOT), "RIG_INSTALL_TRANSACTION": "1",
                                                "RIG_SKIP_UPDATE_CHECK": "1", "RIG_SKIP_MODEL_CATALOG": "1"}))
        self.binding = {"job_id": "writer", "attempt_id": "attempt-one", "reservation_id": "reservation-one",
                        "contract_fingerprint": "a" * 64}
        self.write(".rig/harness.toml", "[project]\nenabled = false\n", raw=True)
        self.write(".rig/jobs/writer/meta.json", {**self.binding, "workflow_id": "work", "status": "ok",
                                                "owner_token": "never-copy-credential"})
        self.write(".rig/workflows/work/spec.json", {"workflow_id": "work", "created_at": T0,
                                                    "shared_context": "never-copy-private-context"})
        self.write(".rig/workflows/work/state.json", {"workflow_id": "work", "status": "verified", "nodes": {
            "implement": {"job_id": "writer", "attempt_id": "attempt-one", "status": "accepted"}}})
        self.event(1, "created", T0)
        self.event(2, "launched", T1, job_id="writer", attempt_id="attempt-one", node_id="implement")
        self.check()
        self.assertion()
        self.write(".rig/jobs/writer/verification.json", {"version": 1, **self.binding,
            "assessed_at": T2, "acceptance": "accepted", "snapshot_id": "b" * 64,
            "rationale": "never-copy-private-rationale", "history": []})

    def write(self, relative, value, raw=False):
        path = self.repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value if raw else json.dumps(value))
        return path

    def event(self, seq, kind, at, **fields):
        return self.write(f".rig/workflows/work/events/{seq:06d}-{kind}.json",
                          {"version": 1, "workflow_id": "work", "seq": seq, "kind": kind, "at": at, **fields})

    def check(self, name="tests-123", seq=1, **fields):
        return self.write(f".rig/jobs/writer/checks/{name}.json", {"version": 1, **self.binding,
            "check_id": name, "requirement_id": "tests", "sequence": seq, "started_at": T1,
            "ended_at": T2, "status": "passed", "exit_code": 0,
            "argv": ["never-copy-private-command"], **fields})

    def assertion(self, identifier="c" * 32, **fields):
        return self.write(f".rig/jobs/writer/criteria/history/{identifier}.json", {"schema_version": 1,
            **self.binding, "assertion_id": identifier, "criterion_id": "layout", "recorded_at": T2,
            "result": "pass", "provenance": {"kind": "parent_assertion", "owner_session": "never-copy-private-session"},
            "rationale": "never-copy-private-rationale", "evidence_refs": [
                {"path": "evidence/ui-packs/manifest.json", "sha256": "d" * 64, "kind": "ui-pack"}], **fields})

    def all_rows(self, result):
        return result["entries"] + result["untimed_entries"]

    def inventory(self):
        return {path.relative_to(self.repo).as_posix(): (path.read_bytes() if path.is_file() else None,
                                                        path.stat().st_mtime_ns)
                for path in self.repo.rglob("*") if not path.is_symlink()}

    def test_job_current_attempt_selected_and_provenance_sanitized(self):
        result = timeline.build(self.repo, job_id="writer")
        self.assertEqual(result["scope"], {"job_id": "writer", "attempt_id": "attempt-one", "attempt_selection": "persisted_current"})
        self.assertEqual(len(result["entries"]), 3)
        self.assertEqual({row["kind"] for row in result["entries"]},
                         {"check_record", "criterion_assertion_record", "acceptance_record"})
        self.assertNotIn("never-copy", json.dumps(result))
        for row in result["entries"]:
            source = self.repo / row["source"]["path"]
            self.assertEqual(row["source"]["sha256"], hashlib.sha256(source.read_bytes()).hexdigest())
        assertion = next(row for row in result["entries"] if row["kind"] == "criterion_assertion_record")
        self.assertEqual(assertion["details"]["provenance"], "parent_assertion")
        self.assertEqual(assertion["details"]["evidence_refs"][0]["validation"], "not_revalidated")

    def test_bytes_mtimes_and_no_probes_or_state_transitions(self):
        before = self.inventory()
        with patch("workflow.show", side_effect=AssertionError("no show")), \
             patch("workflow_state.refresh", side_effect=AssertionError("no refresh")), \
             patch("workflow_state.save_state", side_effect=AssertionError("no state write")), \
             patch("workflow_state.append_event", side_effect=AssertionError("no events")), \
             patch("admission.transaction", side_effect=AssertionError("no admission")), \
             patch("change_evidence.snapshot", side_effect=AssertionError("no snapshot")), \
             patch("subprocess.Popen", side_effect=AssertionError("no subprocess")):
            first = timeline.build(self.repo, workflow_id="work")
            second = timeline.build(self.repo, workflow_id="work")
        self.assertEqual(first, second)
        self.assertEqual(self.inventory(), before)

    def test_no_credential_log_screenshot_or_latest_ask_reads(self):
        seen, original = [], timeline.Reader._open
        def inspect(reader, relative, **options):
            seen.append(relative)
            self.assertFalse(any(word in relative for word in ("owner-credentials", "stdout", "ask.json", "inbox.json", ".png")))
            return original(reader, relative, **options)
        with patch.object(timeline.Reader, "_open", inspect):
            timeline.build(self.repo, workflow_id="work")
        self.assertTrue(seen)

    def test_old_attempt_cannot_borrow_new_job_records(self):
        result = timeline.build(self.repo, job_id="writer", attempt_id="older-attempt")
        self.assertEqual(result["scope"]["attempt_id"], "older-attempt")
        self.assertEqual(self.all_rows(result), [])
        self.assertEqual(result["coverage"]["state"], "partial")
        self.assertEqual(result["coverage"]["issues"]["attempt_unavailable"], 1)
        self.check(name="old-check", seq=2, attempt_id="older-attempt")
        current = timeline.build(self.repo, job_id="writer")
        self.assertNotIn("old-check", json.dumps(current))
        self.assertEqual(current["coverage"]["issues"]["unbound_record"], 1)

    def test_workflow_event_never_rebinds_unscoped_legacy_attempt(self):
        self.event(2, "launched", T1, job_id="writer", node_id="implement")
        self.write(".rig/workflows/work/state.json", {"workflow_id": "work", "nodes": {}})
        result = timeline.build(self.repo, workflow_id="work")
        self.assertEqual(len(result["entries"]), 2)
        self.assertEqual(result["entries"][1]["details"]["attempt_binding"], "unavailable_legacy")
        self.assertNotIn("check_record", json.dumps(result))

    def test_incomplete_saved_and_event_bindings_mark_partial_without_rebinding(self):
        (self.repo / ".rig/workflows/work/events/000002-launched.json").unlink()
        bindings = [{"job_id": "writer", "status": "accepted"}, {"attempt_id": "attempt-one"},
                    {"job_id": "ghp_" + "Q" * 28, "attempt_id": "attempt-one"},
                    {"job_id": [], "attempt_id": "attempt-one"}, "malformed"]
        for binding in bindings:
            for source in ("state", "event"):
                with self.subTest(source=source, binding_type=type(binding).__name__):
                    self.write(".rig/workflows/work/state.json", {"workflow_id": "work", "nodes":
                        {"legacy": binding} if source == "state" else {}})
                    event = self.repo / ".rig/workflows/work/events/000002-runtime-state.json"
                    event.unlink(missing_ok=True)
                    if source == "event":
                        self.event(2, "runtime-state", T1, attempts=[binding])
                    result = timeline.build(self.repo, workflow_id="work")
                    self.assertEqual(result["coverage"]["state"], "partial")
                    self.assertIn("incomplete_or_redacted_attempt_binding", result["coverage"]["issues"])
                    self.assertFalse(any(row["kind"] == "check_record" for row in self.all_rows(result)))
                    self.assertNotIn("QQQ", json.dumps(result))

    def test_direct_event_falsy_or_orphan_identity_is_not_silently_dropped(self):
        self.write(".rig/workflows/work/state.json", {"workflow_id": "work", "nodes": {}})
        for fields in ({"job_id": 0}, {"attempt_id": False}, {"job_id": []}, {"attempt_id": "orphan-attempt"}):
            self.event(2, "launched", T1, **fields)
            result = timeline.build(self.repo, workflow_id="work")
            self.assertEqual(result["coverage"]["state"], "partial")
            self.assertTrue(result["coverage"]["issues"])
            self.assertFalse(any(row["kind"] == "check_record" for row in self.all_rows(result)))

    def test_never_launched_bindings_do_not_invent_missing_history(self):
        (self.repo / ".rig/workflows/work/events/000002-launched.json").unlink()
        self.write(".rig/workflows/work/state.json", {"workflow_id": "work", "nodes": {
            "pending": {"job_id": "", "attempt_id": "", "status": "pending"}}})
        self.event(2, "runtime-state", T1, attempts=[{"job_id": "", "attempt_id": "", "status": "pending"}])
        result = timeline.build(self.repo, workflow_id="work")
        self.assertEqual(result["coverage"]["state"], "recorded")
        self.assertEqual(result["coverage"]["issues"], {})

    def test_wrong_workflow_cannot_import_job_attempt(self):
        meta = {**self.binding, "workflow_id": "another-workflow"}
        self.write(".rig/jobs/writer/meta.json", meta)
        result = timeline.build(self.repo, workflow_id="work")
        self.assertEqual(len(result["entries"]), 2)
        self.assertEqual(result["coverage"]["issues"]["attempt_unavailable"], 1)

    def test_clock_reversal_preserves_source_sequence_and_untimed_separate(self):
        self.event(1, "created", T2)
        self.event(2, "launched", T0, job_id="writer", attempt_id="attempt-one")
        self.event(3, "approved", "malformed", node_id="implement")
        result = timeline.build(self.repo, workflow_id="work")
        events = [row for row in result["entries"] if row["lane"] == "workflow-events"]
        self.assertEqual([row["source_order"] for row in events], [1, 2])
        self.assertEqual(result["untimed_entries"][0]["source_order"], 3)
        self.assertTrue(next(lane for lane in result["lanes"] if lane["id"] == "workflow-events")["clock_reversal"])
        self.assertEqual(result, timeline.build(self.repo, workflow_id="work"))

    def test_gaps_huge_sequences_invalid_and_same_timestamps_are_bounded(self):
        self.event(999999999999999999999, "runtime-state", T2, runtime_revision=999999999999999999999)
        self.event(4, "runtime-ask", T2, job_id="writer", attempt_id="attempt-one", ask_id="ask-one", phase="open")
        self.event(5, "runtime-ask", T2, job_id="writer", attempt_id="attempt-one", ask_id="ask-one", phase="close")
        self.write(".rig/workflows/work/events/000006-created.json", "malformed", raw=True)
        result = timeline.build(self.repo, workflow_id="work")
        self.assertIn("missing_event_sequence", result["coverage"]["issues"])
        self.assertIn("runtime_revision_gap", result["coverage"]["issues"])
        self.assertIn("invalid_or_unsafe_source", result["coverage"]["issues"])
        asks = [row for row in result["entries"] if row["kind"] == "runtime-ask"]
        self.assertEqual([row["details"]["phase"] for row in asks], ["open", "close"])

    def test_stop_intent_is_not_confirmed_termination(self):
        self.write(".rig/workflows/work/cancel.json", {"workflow_id": "work", "created_at": T0, "at": T2,
                                                       "credentials_digest": "never-copy-private-hash"})
        self.write(".rig/jobs/writer/cancellation/attempt-one.json", {**self.binding, "at": T2})
        result = timeline.build(self.repo, workflow_id="work")
        stops = [row for row in result["entries"] if row["kind"] == "recorded_stop_intent"]
        self.assertEqual(len(stops), 2)
        self.assertTrue(all(row["details"]["termination"] == "not_established_by_intent" for row in stops))
        self.assertNotIn("never-copy", json.dumps(result))

    def test_current_state_and_latest_only_ask_do_not_invent_history(self):
        for path in self.repo.glob(".rig/workflows/work/events/*.json"):
            path.unlink()
        self.write(".rig/workflows/work/state.json", {"workflow_id": "work", "status": "verified", "nodes": {}})
        self.write(".rig/jobs/writer/ask.json", {"preview": "never-copy-private-prompt", "asked_at": T2})
        result = timeline.build(self.repo, workflow_id="work")
        self.assertEqual(self.all_rows(result), [])
        self.assertEqual(result["coverage"]["state"], "unknown")

    def test_limits_report_output_and_input_truncation(self):
        result = timeline.build(self.repo, job_id="writer", limit=1)
        self.assertEqual(len(self.all_rows(result)), 1)
        self.assertEqual(result["coverage"]["omitted_records"], 2)
        self.assertEqual(result["coverage"]["state"], "partial")
        with patch.object(timeline, "MAX_TOTAL_BYTES", 10):
            result = timeline.build(self.repo, workflow_id="work")
        self.assertIn("input_budget_exceeded", result["coverage"]["issues"])
        with patch.object(timeline, "MAX_DIRECTORY_ENTRIES", 1):
            result = timeline.build(self.repo, workflow_id="work")
        self.assertIn("directory_limit_exceeded", result["coverage"]["issues"])

    def test_unsafe_paths_links_and_identifiers_fail_closed(self):
        with self.assertRaises(ValueError):
            timeline.build(self.repo, job_id="../writer")
        with self.assertRaises(ValueError) as ctx:
            timeline.build(self.repo, job_id="ghp_" + "Q" * 28)
        self.assertNotIn("QQQ", str(ctx.exception))
        self.check(name="ghp_" + "Q" * 28, seq=2)
        self.assertion(evidence_refs=[{"path": "evidence/../../outside", "sha256": "d" * 64, "kind": "ui-pack"}])
        result = timeline.build(self.repo, job_id="writer")
        self.assertNotIn("QQQ", json.dumps(result))
        self.assertNotIn("../../outside", json.dumps(result))
        path = self.repo / ".rig/jobs/writer/checks/tests-123.json"
        original = self.repo / "outside.json"
        original.write_text(path.read_text())
        path.unlink()
        path.symlink_to(original)
        self.assertIn("invalid_or_unsafe_source", timeline.build(self.repo, job_id="writer")["coverage"]["issues"])
        path.unlink()
        os.link(original, path)
        self.assertIn("unsafe_source", timeline.build(self.repo, job_id="writer")["coverage"]["issues"])

    def test_mcp_parent_readonly_available_while_disabled_and_cli(self):
        result = rig_mcp.call_tool("rig_task_timeline", {"repo": str(self.repo), "job_id": "writer"})
        self.assertFalse(result.get("isError"), result)
        self.assertEqual(result["structuredContent"]["scope"]["attempt_id"], "attempt-one")
        self.assertNotIn("rig_task_timeline", rig_mcp.CHILD_TOOL_NAMES)
        with patch.dict(os.environ, {"RIG_JOB_ID": "child"}):
            with self.assertRaisesRegex(ValueError, "parent-only"):
                timeline.build(self.repo, job_id="writer")
            self.assertTrue(rig_mcp.call_tool("rig_task_timeline", {"repo": str(self.repo), "job_id": "writer"}).get("isError"))
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        result = subprocess.run([str(ROOT / "bin/rig"), "timeline", "--job", "writer", "--json"], cwd=self.repo,
                                text=True, capture_output=True, env=os.environ.copy())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["scope"]["attempt_id"], "attempt-one")

    def test_uninitialized_and_malformed_workflow_identity_stay_read_only(self):
        (self.repo / ".rig/harness.toml").unlink()
        before = self.inventory()
        result = rig_mcp.call_tool("rig_task_timeline", {"repo": str(self.repo), "job_id": "writer"})
        self.assertFalse(result.get("isError"), result)
        self.assertEqual(self.inventory(), before)
        self.write(".rig/workflows/work/state.json", {"workflow_id": "another", "nodes": {}})
        result = timeline.build(self.repo, workflow_id="work")
        self.assertIn("workflow_identity_mismatch", result["coverage"]["issues"])

    def test_scoped_directory_symlink_cannot_read_outside(self):
        outside = self.repo / "outside"
        outside.mkdir()
        for path in (self.repo / ".rig/jobs/writer/checks").iterdir():
            path.unlink()
        (self.repo / ".rig/jobs/writer/checks").rmdir()
        (outside / "private.json").write_text('{"private":"never-copy-outside"}')
        (self.repo / ".rig/jobs/writer/checks").symlink_to(outside, target_is_directory=True)
        result = timeline.build(self.repo, job_id="writer")
        self.assertIn("invalid_or_unsafe_source", result["coverage"]["issues"])
        self.assertNotIn("never-copy", json.dumps(result))

    def test_request_validation_and_missing_legacy_attempt(self):
        for args in ({}, {"job_id": "writer", "workflow_id": "work"}, {"workflow_id": "work", "attempt_id": "one"},
                     {"job_id": "writer", "limit": True}, {"job_id": "writer", "limit": 201}):
            with self.assertRaises(ValueError):
                timeline.build(self.repo, **args)
        self.write(".rig/jobs/writer/meta.json", {"job_id": "writer", "status": "ok"})
        result = timeline.build(self.repo, job_id="writer")
        self.assertEqual(result["scope"]["attempt_id"], "")
        self.assertEqual(self.all_rows(result), [])
        self.assertIn("attempt_unavailable", result["coverage"]["issues"])


from test_native_admission import NativeHarness
import change_evidence
import verification


class TimelineRecordedIntegration(NativeHarness):
    def test_real_check_and_acceptance_are_historical_without_revalidation(self):
        argv = [sys.executable, "-c", "print('checked')"]
        contract = {"schema_version": 1, "contract_id": "timeline-check", "revision": 1, "criteria": [
            {"id": "tests-pass", "description": "The parent runs the required check", "scope": ["subject.txt"],
             "evidence_type": "check", "verifier_role": "parent", "check": {"id": "tests", "argv": argv}}]}
        lease = self.start(acceptance_contract=contract)
        self.assertFalse(self.finish(lease).get("isError"))
        folder = self.repo / ".rig/jobs/writer"
        check = verification.run_check(self.repo, folder, "tests", argv, **self.auth(lease))
        verification.accept(self.repo, folder, "accept", check["after_snapshot_id"],
                            rationale="Actual required check passed", next="review", **self.auth(lease))
        (self.repo / "subject.txt").write_text("changed after recorded acceptance")
        before = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in folder.rglob("*") if path.is_file()}
        with patch("change_evidence.snapshot", side_effect=AssertionError("no fresh snapshot")):
            result = timeline.build(self.repo, job_id="writer")
        self.assertEqual(result["scope"]["attempt_id"], lease["attempt_id"])
        self.assertEqual({row["kind"] for row in result["entries"]}, {"check_record", "acceptance_record"})
        acceptance = next(row for row in result["entries"] if row["kind"] == "acceptance_record")
        self.assertEqual(acceptance["details"]["recorded_decision"], "accepted")
        self.assertEqual(acceptance["details"]["current_validity"], "not_revalidated")
        self.assertEqual(before, {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in folder.rglob("*") if path.is_file()})


if __name__ == "__main__":
    unittest.main()
