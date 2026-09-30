"""Workflow Stop is authenticated, incarnation-bound, durable, and not EOF."""
import json
import os
import queue
import shutil
import sys
import threading
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import admission
import cancellation
import rig_mcp
import workflow_cancellation as stop
import workflow_scheduler as sched
import workflow_state as wf
from mcp_runtime import Runtime
from test_workflow import _repo, _spec


class WorkflowCancellation(unittest.TestCase):
    def setUp(self):
        self.temp, self.repo = _repo()
        self.addCleanup(self.temp.cleanup)
        env = mock.patch.dict(os.environ, {"RIG_OWNER_SESSION": "stop-tests", "RIG_JOB_ID": "", "RIG_JOB_DIR": ""})
        env.start()
        self.addCleanup(env.stop)
        self.created = wf.create_workflow(self.repo, _spec(), owner_session="stop-tests")
        self.wid = self.created["workflow_id"]
        self.token = self.created["owner_token"]
        self.folder = wf.workflow_dir(self.repo, self.wid)
        self.target = stop.capture(self.repo, self.wid, owner_token=self.token, owner_session="stop-tests")

    def job(self, jid="attached", *, status="running", admitted=False):
        folder = self.repo / ".rig" / "jobs" / jid
        folder.mkdir(parents=True)
        if admitted:
            record = admission.reserve(self.repo, job_id=jid, worker="parent", files=["a.py"],
                                       owner=admission.caller_owner("parent", owner_session="stop-tests"))
            rid, aid = record["reservation_id"], record["attempt_id"]
        else:
            rid, aid = "res-" + jid, "att-" + jid
        meta = {"job_id": jid, "reservation_id": rid, "attempt_id": aid,
                "status": status, "worker": "codex", "executor_kind": "parent"}
        (folder / "meta.json").write_text(json.dumps(meta))
        return folder, meta

    def attach(self, meta):
        spec, state = wf.load_pair(self.repo, self.wid, required=True)
        state["nodes"]["w1"].update({key: meta[key] for key in ("job_id", "reservation_id", "attempt_id", "status")})
        state["nodes"]["w1"]["launched"] = True
        state["status"] = "running"
        wf.save_state(self.repo, state)

    def test_wrong_owner_session_and_cross_repo_cannot_bind(self):
        for args in ({"owner_token": "wrong"}, {"owner_token": self.token, "owner_session": "other"}):
            with self.subTest(args={key: "redacted" for key in args}), self.assertRaises(wf.WorkflowError):
                stop.capture(self.repo, self.wid, **args)
        other_temp, other_repo = _repo()
        self.addCleanup(other_temp.cleanup)
        wf.create_workflow(other_repo, _spec(workflow_id=self.wid), owner_session="stop-tests")
        with self.assertRaises(wf.WorkflowError):
            stop.capture(other_repo, self.wid, owner_token=self.token)
        self.assertFalse((self.folder / "cancel.json").exists())

    def test_cross_repo_workflow_symlink_is_rejected_before_binding(self):
        other_temp, other_repo = _repo()
        self.addCleanup(other_temp.cleanup)
        external = other_repo / ".rig" / "workflows" / self.wid
        external.parent.mkdir()
        self.folder.rename(external)
        self.folder.symlink_to(external, target_is_directory=True)
        with self.assertRaisesRegex(wf.WorkflowError, "another repository"):
            stop.capture(self.repo, self.wid, owner_token=self.token)
        self.assertFalse((external / "cancel.json").exists())

    def test_replaced_workflow_directory_and_credentials_are_rejected(self):
        moved = self.folder.with_name(self.wid + "-old")
        self.folder.rename(moved)
        shutil.copytree(moved, self.folder)
        with self.assertRaisesRegex(wf.WorkflowError, "identity changed"):
            stop.publish(self.target)
        self.assertFalse((self.folder / "cancel.json").exists())
        new_target = stop.capture(self.repo, self.wid, owner_token=self.token)
        creds = json.loads((self.folder / "owner-credentials.json").read_text())
        creds["owner_token"] = "replacement"
        (self.folder / "owner-credentials.json").write_text(json.dumps(creds))
        with self.assertRaisesRegex(wf.WorkflowError, "credentials mismatch"):
            stop.publish(new_target)
        self.assertFalse((self.folder / "cancel.json").exists())

    def test_replaced_attempt_is_not_cancelled_and_other_jobs_untouched(self):
        folder, meta = self.job()
        self.attach(meta)
        other, _ = self.job("other")
        meta["attempt_id"] = "replacement-attempt"
        (folder / "meta.json").write_text(json.dumps(meta))
        results = stop.publish(self.target)
        self.assertEqual(results[0]["state"], "stop-unconfirmed")
        self.assertFalse(cancellation.requested(folder))
        self.assertFalse(cancellation.requested(other))
        refreshed = wf.refresh(self.repo, self.wid)
        self.assertEqual(refreshed["status"], "cancel-requested")
        self.assertEqual(refreshed["node_state"]["w1"]["attempt_id"], "att-attached")
        wf.refresh(self.repo, self.wid)
        self.assertFalse(cancellation.requested(folder))

    def test_intent_is_idempotent_secret_free_and_independent_of_admission_lock(self):
        folder, meta = self.job()
        self.attach(meta)
        locked, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        def holder():
            with admission.transaction(self.repo):
                locked.set()
                release.wait(5)
        thread = threading.Thread(target=holder)
        thread.start()
        self.assertTrue(locked.wait(1))
        try:
            stop.publish(self.target, "first")
            first = (self.folder / "cancel.json").read_bytes()
            stop.publish(self.target, "second")
            self.assertEqual((self.folder / "cancel.json").read_bytes(), first)
            self.assertNotIn(self.token, first.decode())
            self.assertTrue(cancellation.requested(folder))
        finally:
            release.set()
            thread.join(1)

    def test_refresh_replays_intent_after_interrupted_publisher(self):
        folder, meta = self.job()
        self.attach(meta)
        with mock.patch.object(stop, "cancel_jobs", return_value=[]):
            stop.publish(self.target)
        self.assertFalse(cancellation.requested(folder))
        public = wf.refresh(self.repo, self.wid)
        self.assertTrue(cancellation.requested(folder))
        self.assertEqual(public["status"], "cancel-requested")
        code, text = sched.wait_workflow(self.repo, self.wid)
        self.assertEqual(code, 130)
        self.assertIn("cancel-requested", text)

    def test_read_only_listing_shows_durable_stop_before_refresh(self):
        saved = (self.folder / "state.json").read_bytes()
        stop.publish(self.target)
        with mock.patch.object(admission, "transaction", side_effect=AssertionError("listing acquired admission")), mock.patch.object(stop, "cancel_jobs", side_effect=AssertionError("listing dispatched cancellation")):
            result = rig_mcp.call_tool("rig_workflows", {"repo": str(self.repo)})
        self.assertFalse(result.get("isError"), result)
        public = result["structuredContent"]["workflows"][0]
        self.assertEqual(public["status"], "cancel-requested")
        self.assertEqual(public["next_parent_action"], {"kind": "confirm_stop"})
        self.assertEqual((self.folder / "state.json").read_bytes(), saved)

    def test_unknown_admitted_execution_retains_slot_files_until_confirmed_stop(self):
        folder, meta = self.job(status="unconfirmed", admitted=True)
        self.attach(meta)
        stop.publish(self.target)
        for display in ("unconfirmed", "cancelled"):
            meta["status"] = display
            (folder / "meta.json").write_text(json.dumps(meta))
            public = wf.refresh(self.repo, self.wid)
            self.assertEqual(public["status"], "cancel-requested")
            record = admission.get_reservation(self.repo, meta["reservation_id"])
            self.assertFalse(record["stopped"])
            self.assertNotEqual(record["stage"], "released")
            with self.assertRaisesRegex(admission.AdmissionError, "conflict|overlap"):
                admission.reserve(self.repo, job_id="conflict", worker="parent", files=["a.py"],
                                  owner=admission.caller_owner("parent", owner_session="stop-tests"))
        # Simulate the authenticated executor's confirmed outcome, not Stop.
        record = admission.get_reservation(self.repo, meta["reservation_id"])
        record.update(stopped=True, execution_status="cancelled")
        admission._save(self.repo, record)
        self.assertEqual(wf.refresh(self.repo, self.wid)["status"], "cancelled")
        self.assertNotEqual(admission.get_reservation(self.repo, meta["reservation_id"])["stage"], "released")

    def test_cancelled_workflow_cannot_retry_extend_or_advance(self):
        stop.publish(self.target)
        self.assertEqual(wf.refresh(self.repo, self.wid)["status"], "cancelled")
        with self.assertRaisesRegex(wf.WorkflowError, "cannot resume"):
            sched.resolve_node(self.repo, self.wid, "w1", owner_token=self.token)
        with self.assertRaisesRegex(wf.WorkflowError, "cannot extend"):
            wf.extend_workflow(self.repo, self.wid, [{"id": "w2", "role": "mini", "files": ["b.py"]}], owner_token=self.token)
        launcher = mock.Mock()
        result = sched.advance(self.repo, self.wid, owner_token=self.token, launch_fn=launcher)
        self.assertEqual(result["reason"], "terminal")
        launcher.assert_not_called()

    def test_stop_during_picker_does_not_launch_work(self):
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        errors = queue.Queue()
        launcher = mock.Mock()
        def picker(*args):
            entered.set()
            self.assertTrue(release.wait(3))
            return {"spawn": "run-worker", "worker": "grok"}
        def advance():
            try:
                sched.advance(self.repo, self.wid, owner_token=self.token, pick_fn=picker, launch_fn=launcher)
            except BaseException as error:
                errors.put(error)
        thread = threading.Thread(target=advance)
        thread.start()
        self.assertTrue(entered.wait(1))
        stop.publish(self.target)
        self.assertEqual(wf.refresh(self.repo, self.wid)["status"], "cancel-requested")
        release.set()
        thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertTrue(errors.empty(), list(errors.queue))
        launcher.assert_not_called()
        self.assertEqual(wf.refresh(self.repo, self.wid)["status"], "cancelled")

    def test_stop_during_launch_keeps_workflow_pending_until_actual_stop(self):
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        errors = queue.Queue()
        def launcher(*args, **kwargs):
            entered.set()
            self.assertTrue(release.wait(3))
            _, meta = self.job()
            return {"job": meta}
        def advance():
            try:
                sched.advance(self.repo, self.wid, owner_token=self.token,
                              pick_fn=lambda *args: {"spawn": "run-worker", "worker": "grok"}, launch_fn=launcher)
            except BaseException as error:
                errors.put(error)
        thread = threading.Thread(target=advance)
        thread.start()
        self.assertTrue(entered.wait(1))
        result = sched.cancel_workflow(self.repo, self.wid, owner_token=self.token)
        self.assertEqual(result["status"], "cancel-requested")
        self.assertEqual(result["node_state"]["w1"]["status"], "launching")
        release.set()
        thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertTrue(errors.empty(), list(errors.queue))
        self.assertTrue(cancellation.requested(self.repo / ".rig" / "jobs" / "attached"))
        self.assertEqual(wf.refresh(self.repo, self.wid)["status"], "cancel-requested")

    def reserve_workflow_job(self):
        spec, state = wf.load_pair(self.repo, self.wid, required=True)
        wf.mark_launched(state, "w1")
        wf.save_state(self.repo, state)
        return admission.reserve(
            self.repo, job_id="reserved-node", worker="parent", files=["a.py"],
            owner=admission.caller_owner("parent", owner_session="stop-tests"),
            workflow_id=self.wid, workflow_node_id="w1", workflow_spec_hash=spec["spec_hash"], workflow_attempt=1,
        )

    def test_admission_pins_node_before_scheduler_returns_and_worker_consumes_marker(self):
        record = self.reserve_workflow_job()
        _, state = wf.load_pair(self.repo, self.wid, required=True)
        self.assertEqual(state["nodes"]["w1"]["job_id"], record["job_id"])
        self.assertEqual(state["nodes"]["w1"]["attempt_id"], record["attempt_id"])
        self.assertEqual(state["nodes"]["w1"]["status"], "launching")
        folder = self.repo / ".rig" / "jobs" / record["job_id"]
        folder.mkdir(parents=True)
        meta = {key: record[key] for key in ("job_id", "reservation_id", "attempt_id")}
        meta.update(status="running", executor_kind="parent")
        (folder / "meta.json").write_text(json.dumps(meta))
        # Interrupt publisher before individual job markers and never return to scheduler.
        with mock.patch.object(stop, "cancel_jobs", return_value=[]):
            stop.publish(self.target)
        self.assertFalse((folder / "cancellation").exists())
        self.assertTrue(cancellation.requested(folder))
        with self.assertRaisesRegex(admission.AdmissionError, "cancellation was requested"):
            admission.activate(self.repo, **admission.credentials(record), job_id=record["job_id"],
                               worker="parent", files=["a.py"], access="write", owner=record["owner"])
        self.assertEqual(wf.refresh(self.repo, self.wid)["status"], "cancel-requested")
        with mock.patch.object(stop, "cancel_jobs", return_value=[]):
            record.update(stopped=True, execution_status="cancelled")
            admission._save(self.repo, record)
            self.assertEqual(wf.refresh(self.repo, self.wid)["status"], "cancelled")

    def test_valid_workflow_stop_refuses_new_reservation(self):
        stop.publish(self.target)
        with self.assertRaisesRegex(admission.AdmissionError, "workflow cancellation"):
            self.reserve_workflow_job()
        self.assertEqual(admission._records(self.repo), [])

    def test_workflow_binding_never_adopts_wrong_owner_or_replaced_incarnation(self):
        record = self.reserve_workflow_job()
        wrong = dict(record, owner=admission.caller_owner("parent", owner_session="someone-else"))
        with self.assertRaisesRegex(wf.WorkflowError, "owner mismatch"):
            stop.prepare_admission(self.repo, wrong)
        creds = json.loads((self.folder / "owner-credentials.json").read_text())
        creds["owner_token"] = "new-incarnation"
        (self.folder / "owner-credentials.json").write_text(json.dumps(creds))
        replacement = stop.capture(self.repo, self.wid, owner_token="new-incarnation")
        with mock.patch.object(stop, "cancel_jobs", return_value=[]):
            stop.publish(replacement)
        self.assertFalse(stop.requested_for_record(self.repo, record))

    def test_cross_repo_job_symlink_is_not_a_cancellation_target(self):
        other_temp, other_repo = _repo()
        self.addCleanup(other_temp.cleanup)
        folder, meta = self.job()
        self.attach(meta)
        moved = other_repo / "attached"
        folder.rename(moved)
        folder.symlink_to(moved, target_is_directory=True)
        result = stop.publish(self.target)
        self.assertEqual(result[0]["state"], "stop-unconfirmed")
        self.assertFalse(cancellation.requested(moved))

    def test_missing_reservation_cancelled_display_preserves_workflow_writer_scope(self):
        _, meta = self.job(status="cancelled")
        self.attach(meta)
        stop.publish(self.target)
        public = wf.refresh(self.repo, self.wid)
        self.assertEqual(public["status"], "cancel-requested")
        self.assertEqual(public["node_state"]["w1"]["status"], "unconfirmed")
        with self.assertRaisesRegex(admission.AdmissionError, "overlap|exclusive"):
            admission.reserve(self.repo, job_id="direct-conflict", worker="parent", files=["a.py"],
                              owner=admission.caller_owner("parent", owner_session="stop-tests"))
        with self.assertRaisesRegex(wf.WorkflowError, "overlapping workflow writer scopes"):
            wf.create_workflow(self.repo, _spec(workflow_id="conflict"), owner_session="stop-tests")

    def test_replacement_ledger_cannot_deduplicate_original_orphan_scope(self):
        # A damaged/replaced job ID must not erase the original attempt's scope.
        replacement = admission.reserve(self.repo, job_id="attached", worker="parent", files=["b.py"],
                                        owner=admission.caller_owner("parent", owner_session="stop-tests"))
        folder, old_meta = self.job()
        self.attach(old_meta)
        meta = {key: replacement[key] for key in ("job_id", "reservation_id", "attempt_id")}
        meta.update(status="running", files=["b.py"], worker="parent")
        (folder / "meta.json").write_text(json.dumps(meta))
        stop.publish(self.target)
        wf.refresh(self.repo, self.wid)
        rows = admission._accounting(self.repo)
        self.assertEqual(len([row for row in rows if row.get("job_id") == "attached"]), 2)
        for filename in ("a.py", "b.py"):
            with self.subTest(filename=filename), self.assertRaisesRegex(admission.AdmissionError, "overlap"):
                admission.reserve(self.repo, job_id="conflict", worker="parent", files=[filename],
                                  owner=admission.caller_owner("parent", owner_session="stop-tests"))
        self.assertFalse(cancellation.requested(folder))

    def test_early_bound_never_started_launch_keeps_one_safe_repick(self):
        launches = []
        def launcher(repo, *, node, spec, state, **kwargs):
            jid = "launch-" + str(len(launches))
            record = admission.reserve(
                repo, job_id=jid, worker="parent", files=["a.py"],
                owner=admission.caller_owner("parent", owner_session="stop-tests"),
                workflow_id=self.wid, workflow_node_id="w1", workflow_spec_hash=spec["spec_hash"],
                workflow_attempt=int(state["nodes"]["w1"].get("workflow_attempt") or 0) + 1,
            )
            launches.append(jid)
            folder = repo / ".rig" / "jobs" / jid
            folder.mkdir(parents=True)
            meta = {key: record[key] for key in ("job_id", "reservation_id", "attempt_id")}
            if len(launches) == 1:
                meta.update(status="fail", execution_mode="not_started")
                (folder / "meta.json").write_text(json.dumps(meta))
                admission.release(repo, **admission.credentials(record), owner=record["owner"],
                                  rationale="could not start wrapper", mode="launch_failed")
                raise OSError("could not start wrapper")
            meta["status"] = "running"
            (folder / "meta.json").write_text(json.dumps(meta))
            return {"job": meta, "workflow_attempt": record["workflow_attempt"]}
        result = sched.advance(self.repo, self.wid, owner_token=self.token,
                               pick_fn=lambda *args: {"spawn": "run-worker", "worker": "grok"}, launch_fn=launcher)
        self.assertEqual(len(launches), 2)
        self.assertEqual(result["node_state"]["w1"]["exclude"], ["grok"])
        self.assertEqual(result["node_state"]["w1"]["workflow_attempt"], 2)
        self.assertEqual(result["node_state"]["w1"]["job_id"], launches[1])

    def test_stop_overrides_stale_parent_action_and_terminal_cancel_has_no_retry(self):
        spec, state = wf.load_pair(self.repo, self.wid, required=True)
        state["parent_action"] = {"kind": "parent_writes", "node_id": "w1"}
        state["failure"] = {"node_id": "w1", "reason": "stale failure"}
        state["cancel_requested"] = True
        state["status"] = "cancel-requested"
        self.assertEqual(wf.next_parent_action(spec, state), {"kind": "confirm_stop"})
        state["status"] = "cancelled"
        self.assertIsNone(wf.next_parent_action(spec, state))

    def runtime_wait(self, *, credentials=True):
        replies, entered = queue.Queue(), threading.Event()
        runtime = Runtime(rig_mcp._execute_request, replies.put, rig_mcp._abort_request)
        self.addCleanup(lambda: runtime.shutdown(timeout=.15))
        def wait(*args, cancel_event, **kwargs):
            entered.set()
            cancel_event.wait(3)
            return 130, "observer detached"
        patch = mock.patch.object(rig_mcp.rig_workflow, "wait", side_effect=wait)
        patch.start()
        self.addCleanup(patch.stop)
        patch = mock.patch.object(rig_mcp, "_runtime", runtime)
        patch.start()
        self.addCleanup(patch.stop)
        args = {"repo": str(self.repo), "id": self.wid}
        if credentials:
            args.update(owner_token=self.token, owner_session="stop-tests")
        runtime.start(1, "rig_workflow_wait", args, {})
        self.assertTrue(entered.wait(1))
        with runtime.lock:
            request = runtime.requests[runtime.key(1)]
        return runtime, request, replies

    def test_credentialed_host_stop_publishes_intent_and_suppresses_response(self):
        runtime, request, replies = self.runtime_wait()
        runtime.cancel(1)
        request.thread.join(2)
        self.assertFalse(request.thread.is_alive())
        self.assertTrue(stop.requested(self.repo, wf.load_spec(self.repo, self.wid)))
        self.assertTrue(replies.empty())

    def test_credentialfree_stop_only_ends_observation(self):
        runtime, request, replies = self.runtime_wait(credentials=False)
        runtime.cancel(1)
        request.thread.join(2)
        self.assertFalse((self.folder / "cancel.json").exists())
        self.assertTrue(replies.empty())

    def test_eof_detaches_credentialed_wait_without_stop_and_reconnect_keeps_owner(self):
        before = (self.folder / "owner-credentials.json").read_bytes()
        runtime, request, replies = self.runtime_wait()
        runtime.shutdown(timeout=.15)
        request.thread.join(1)
        self.assertTrue(request.detached)
        self.assertFalse(request.cancelled)
        self.assertFalse((self.folder / "cancel.json").exists())
        self.assertEqual((self.folder / "owner-credentials.json").read_bytes(), before)
        self.assertEqual(wf.refresh(self.repo, self.wid)["status"], "planned")
        stop.capture(self.repo, self.wid, owner_token=self.token, owner_session="stop-tests")
        self.assertTrue(replies.empty())

    def test_credentialfree_result_explicitly_labels_observation_only(self):
        result = rig_mcp.call_tool("rig_workflow_wait", {"repo": str(self.repo), "id": self.wid, "timeout": 0})
        self.assertFalse(result.get("isError"))
        self.assertIn("Observation-only", result["content"][0]["text"])


if __name__ == "__main__":
    unittest.main()
