"""Real native CLI/MCP ownership transitions and competing parent processes."""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import admission
import jobs
import rig_mcp


class NativeHarness(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        (self.repo / ".rig").mkdir()
        (self.repo / ".rig" / "harness.toml").write_text('parent = "codex"\n[workers]\ncodex = false\n')
        (self.repo / ".gitignore").write_text('.rig/\n')
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True, capture_output=True)
        (self.repo / "subject.txt").write_text("before\n")
        self.enterContext(patch.dict(os.environ, {
            "RIG_PARENT": "codex", "RIG_THREAD": "native-test-parent", "RIG_HOME": str(ROOT), "RIG_INSTALL_TRANSACTION": "1",
            "RIG_JOB_ID": "", "RIG_JOB_DIR": "", "RIG_SKIP_MODEL_CATALOG": "1",
            "RIG_SKIP_UPDATE_CHECK": "1", "RIG_OWNER_SESSION": "", "RIG_OWNER_TOKEN": "",
            "RIG_RESERVATION_ID": "", "RIG_ATTEMPT_ID": "", "RIG_JOB_FILES_JSON": "", "RIG_JOB_FILES": "",
        }))

    def call(self, name, **args):
        return rig_mcp.call_tool(name, {"repo": str(self.repo), **args})

    def start(self, name="writer", **args):
        result = self.call("rig_job_start", **{"id": name, "role": "parent", "files": ["subject.txt"], **args})
        self.assertFalse(result.get("isError"), result)
        return result["structuredContent"]

    def auth(self, lease):
        return {key: lease[key] for key in ("reservation_id", "attempt_id", "owner_token")}

    def finish(self, lease, **args):
        return self.call("rig_job_finish", id=lease["job_id"], **self.auth(lease),
                         **{"completion": {"kind": "parent_task", "completed": True}, **args})


class NativeAdmission(NativeHarness):
    def test_start_establishes_scope_before_edits_and_keeps_credentials_private(self):
        lease = self.start()
        folder = self.repo / ".rig" / "jobs" / "writer"
        self.assertTrue((folder / "change-before.json").is_file())
        self.assertEqual((folder / "owner-credentials.json").stat().st_mode & 0o777, 0o600)
        meta = json.loads((folder / "meta.json").read_text())
        self.assertTrue(meta["ownership_established"])
        self.assertNotIn("owner_token", meta)
        for surface in (jobs.format_show(jobs.load_job(folder)), json.dumps(admission.list_reservations(self.repo)),
                        self.call("rig_session", role="stay", case="status", compact=True)["content"][0]["text"]):
            self.assertNotIn(lease["owner_token"], surface)

    def test_compact_session_includes_unlaunched_claim_and_held_completed_work(self):
        import work_queue

        item = work_queue.add_item(self.repo, "Queued scope")
        claimed = self.call("rig_queue_claim", id=item["id"], worker="codex", files=["subject.txt"])
        self.assertFalse(claimed.get("isError"), claimed)
        payload = json.loads(self.call("rig_session", role="stay", case="status", compact=True, terminal_limit=0)["content"][0]["text"])
        self.assertEqual(payload["status"]["reserved"], 1)
        self.assertEqual(payload["history"]["invalid_directories"], 0)
        self.assertEqual(len(payload["jobs"]), 1)
        self.assertEqual(payload["jobs"][0]["effective"], "reserved")
        self.assertNotIn(claimed["structuredContent"]["owner_token"], json.dumps(payload))
        unclaimed = self.call("rig_queue_unclaim", id=item["id"], **self.auth(claimed["structuredContent"]))
        self.assertFalse(unclaimed.get("isError"), unclaimed)
        lease = self.start()
        self.assertFalse(self.finish(lease).get("isError"))
        payload = json.loads(self.call("rig_session", role="stay", case="status", compact=True, terminal_limit=0)["content"][0]["text"])
        self.assertEqual([row["job_id"] for row in payload["jobs"]], ["writer"])
        self.assertFalse(payload["jobs"][0]["reservation"]["slot_held"])

    def test_wrong_finish_credentials_cannot_change_execution_metadata(self):
        lease = self.start()
        path = self.repo / ".rig" / "jobs" / "writer" / "meta.json"
        before = path.read_bytes()
        for auth in ({}, {**self.auth(lease), "owner_token": "wrong-owner-token"},
                     {**self.auth(lease), "owner_session": "another-parent"}):
            result = self.call("rig_job_finish", id="writer", completion={"kind": "parent_task", "completed": True}, **auth)
            self.assertTrue(result.get("isError"), result)
            self.assertEqual(path.read_bytes(), before)

    def test_status_without_completion_keeps_slot_then_parent_completion_keeps_files(self):
        lease = self.start()
        result = self.finish(lease, completion=None)
        self.assertFalse(result.get("isError"), result)
        held = admission.list_reservations(self.repo)[0]
        self.assertTrue(held["slot_held"])
        self.assertTrue(held["needs_reconciliation"])
        self.assertEqual(jobs.load_job(self.repo / ".rig/jobs/writer")["status"], "running")
        self.assertFalse(self.finish(lease).get("isError"))
        held = admission.list_reservations(self.repo)[0]
        self.assertFalse(held["slot_held"])
        self.assertTrue(held["stopped"])
        blocked = self.call("rig_job_start", id="overlap", role="parent", files=["./subject.txt"])
        self.assertTrue(blocked.get("isError"), blocked)
        closed = self.call("rig_job_close", id="writer", rationale="Conclude without acceptance", **self.auth(lease))
        self.assertFalse(closed.get("isError"), closed)
        self.start("replacement")
        self.assertNotEqual(jobs.resolve_job(self.repo, "writer")["verification"]["state"], "verified")

    def test_native_child_completion_requires_its_specific_agent_and_outcome(self):
        lease = self.start(role="mini", executor_kind="native_child", native_agent_id="agent-A", model="gpt-5.6-luna")
        result = self.finish(lease, completion={"kind": "native_child", "agent_id": "agent-B", "terminal": True, "outcome": "ok"})
        self.assertFalse(result.get("isError"), result)
        self.assertTrue(admission.list_reservations(self.repo)[0]["slot_held"])
        result = self.finish(lease, completion={"kind": "native_child", "agent_id": "agent-A", "terminal": True, "outcome": "ok"})
        self.assertFalse(result.get("isError"), result)
        self.assertFalse(admission.list_reservations(self.repo)[0]["slot_held"])

    def test_completed_failure_cannot_be_promoted_without_another_execution(self):
        lease = self.start()
        self.assertFalse(self.finish(lease, status="fail").get("isError"))
        self.assertTrue(self.finish(lease, status="ok").get("isError"))
        self.assertEqual(jobs.resolve_job(self.repo, "writer")["status"], "fail")

    def test_failed_review_launch_keeps_scope_until_explicit_close_or_fresh_retry(self):
        import change_evidence

        harness = self.repo / ".rig" / "harness.toml"
        harness.write_text(harness.read_text() + "claude = true\n")
        writer = self.start(model="gpt-6-astra")
        self.assertFalse(self.finish(writer).get("isError"))
        required = self.call("rig_job_requirements", id="writer", requirements=[], manual_criteria=["Read scoped content"], **self.auth(writer))
        self.assertFalse(required.get("isError"), required)
        snapshot = change_evidence.snapshot(self.repo, ["subject.txt"])["snapshot_id"]
        accepted = self.call("rig_job_accept", id="writer", decision="accept", snapshot_id=snapshot,
                             rationale="Scoped content inspected", next="review", **self.auth(writer))
        self.assertFalse(accepted.get("isError"), accepted)
        common = {"worker": "claude", "role": "review", "model": "claude-opus-5", "access": "read", "files": ["subject.txt"],
                  "owner": admission.caller_owner("native_child"), "writer_job_id": "writer", "writer_snapshot_id": snapshot}
        review = admission.reserve(self.repo, job_id="unlaunched-review", **common, **self.auth(writer))
        admission.release(self.repo, mode="launch_failed", rationale="Reviewer unavailable before launch", **self.auth(review))
        held = admission.list_reservations(self.repo)
        self.assertEqual(len(held), 1)
        self.assertFalse(held[0]["slot_held"])
        self.assertTrue(held[0]["review_launch_failed"])
        self.assertTrue(self.call("rig_job_start", id="conflicting", role="parent", files=["subject.txt"]).get("isError"))
        retry = admission.reserve(self.repo, job_id="review-retry", **common, **self.auth(review))
        self.assertNotEqual(retry["attempt_id"], review["attempt_id"])
        self.assertNotEqual(retry["owner_token"], review["owner_token"])
        closed = self.call("rig_job_close", id="review-retry", rationale="Parent concludes review without launching", **self.auth(retry))
        self.assertFalse(closed.get("isError"), closed)
        self.start("replacement")

    def test_late_queue_spawn_acknowledgement_preserves_completed_execution(self):
        import work_queue

        item = work_queue.add_item(self.repo, "Queued native task")
        claimed = self.call("rig_queue_claim", id=item["id"], worker="codex", files=["subject.txt"])
        self.assertFalse(claimed.get("isError"), claimed)
        lease = self.start(queue_id=item["id"], **self.auth(claimed["structuredContent"]))
        self.assertFalse(self.finish(lease).get("isError"))
        self.assertEqual(work_queue.load_item(self.repo, item["id"])["status"], "done")
        acknowledged = self.call("rig_queue_spawned", id=item["id"], job_id="writer", files=["subject.txt"], **self.auth(lease))
        self.assertFalse(acknowledged.get("isError"), acknowledged)
        self.assertEqual(work_queue.load_item(self.repo, item["id"])["status"], "done")

    def test_interrupted_check_recovery_requires_credentials_through_mcp_and_cli(self):
        lease = self.start()
        self.assertFalse(self.finish(lease).get("isError"))
        process = subprocess.Popen([sys.executable, "-c", "pass"])
        process.wait(timeout=5)
        folder = self.repo / ".rig" / "jobs" / "writer"
        path = self.repo / ".rig" / "reservations" / (lease["reservation_id"] + ".json")
        record = json.loads(path.read_text())
        record["operation"] = {"id": "interrupted", "operation": "check", "pid": process.pid, "start_id": ""}
        path.write_text(json.dumps(record))
        (folder / "check-running.json").write_text(json.dumps({"pid": process.pid, "process_pid": process.pid}))
        options = {"id": "writer", "apply": True, "completion": {"checks_stopped": True}, "rationale": "Observed checker and descendants stopped"}
        refused = self.call("rig_job_reconcile", **options)
        self.assertTrue(refused.get("isError"), refused)
        recovered = self.call("rig_job_reconcile", **options, **self.auth(lease))
        self.assertFalse(recovered.get("isError"), recovered)
        self.assertEqual(json.loads(recovered["content"][0]["text"])["applied"], 1)
        self.assertFalse((folder / "check-running.json").exists())
        self.assertTrue((folder / "check-interrupted-interrupted.json").exists())
        self.assertEqual(len(admission.list_reservations(self.repo)), 1)
        repeat = subprocess.run([str(ROOT / "bin" / "rig"), "job", "reconcile", "writer", "--apply",
            "--reservation-id", lease["reservation_id"], "--attempt-id", lease["attempt_id"],
            "--completion-json", '{"checks_stopped":true}', "--rationale", "Already reconciled"],
            env={**os.environ, "RIG_OWNER_TOKEN": lease["owner_token"]}, cwd=self.repo, text=True, capture_output=True)
        self.assertEqual(repeat.returncode, 0, repeat.stderr)
        self.assertEqual(json.loads(repeat.stdout)["applied"], 0)

    def test_close_cannot_release_live_task_and_cancel_cannot_be_overwritten(self):
        lease = self.start()
        closed = self.call("rig_job_close", id="writer", rationale="Still running", **self.auth(lease))
        self.assertTrue(closed.get("isError"), closed)
        self.assertFalse(self.call("rig_job_cancel", id="writer").get("isError"))
        self.assertTrue(self.finish(lease, status="ok").get("isError"))
        self.assertFalse(self.finish(lease, status="cancelled").get("isError"))
        self.assertFalse(self.call("rig_job_close", id="writer", rationale="Cancelled task stopped", **self.auth(lease)).get("isError"))
        self.assertEqual(jobs.resolve_job(self.repo, "writer")["effective"], "cancelled")

    def test_repeated_start_cannot_authorize_another_execution(self):
        lease = self.start()
        before = (self.repo / ".rig" / "jobs" / "writer" / "meta.json").read_bytes()
        repeated = self.call("rig_job_start", id="writer", role="parent", files=["subject.txt"], **self.auth(lease))
        self.assertTrue(repeated.get("isError"), repeated)
        self.assertEqual((self.repo / ".rig" / "jobs" / "writer" / "meta.json").read_bytes(), before)

    def test_registration_failure_compensates_partial_metadata_and_scope(self):
        with patch.object(admission, "activate", side_effect=OSError("activation persistence failed")):
            failed = self.call("rig_job_start", id="unlaunched", role="parent", files=["subject.txt"])
        self.assertTrue(failed.get("isError"), failed)
        meta = json.loads((self.repo / ".rig" / "jobs" / "unlaunched" / "meta.json").read_text())
        self.assertEqual(meta["execution_mode"], "not_started")
        self.assertFalse(meta["ownership_established"])
        self.assertEqual(meta["status"], "fail")
        self.assertEqual(admission.list_reservations(self.repo), [])
        self.start("replacement")

    def test_native_cancellation_never_signals_a_recorded_parent_pid(self):
        self.start()
        path = self.repo / ".rig" / "jobs" / "writer" / "meta.json"
        meta = json.loads(path.read_text())
        meta["pid"] = os.getpid()
        path.write_text(json.dumps(meta))
        with patch.object(jobs.cancellation, "dispatch", side_effect=jobs.cancellation.signal_wrapper), patch.object(jobs.os, "kill") as signal:
            self.assertFalse(self.call("rig_job_cancel", id="writer").get("isError"))
        self.assertFalse(any(call.args[1] != 0 for call in signal.call_args_list))

    def test_wrapper_cancellation_requires_matching_process_incarnation(self):
        owner = admission.caller_owner("wrapper")
        owner["start_id"] = "previous-process-incarnation"
        lease = admission.reserve(self.repo, job_id="wrapper", worker="codex", role="worker",
                                  model="gpt-5.6-luna", files=["subject.txt"], owner=owner)
        folder = self.repo / ".rig" / "jobs" / "wrapper"
        jobs.write_job_files(folder, "wrapper", "codex", "worker", "running", 0, jobs.iso_now(), "", "",
                             kind="wrapper", executor_kind="wrapper", model="gpt-5.6-luna", reservation=lease)
        meta = json.loads((folder / "meta.json").read_text())
        meta["pid"] = os.getpid()
        (folder / "meta.json").write_text(json.dumps(meta))
        with patch.object(admission, "process_identity", return_value={"pid": os.getpid(), "start_id": "new-incarnation"}), \
                patch.object(jobs.cancellation, "dispatch", side_effect=jobs.cancellation.signal_wrapper), patch.object(jobs.os, "kill") as signal:
            self.assertFalse(self.call("rig_job_cancel", id="wrapper").get("isError"))
        self.assertFalse(any(call.args[1] != 0 for call in signal.call_args_list))

    def test_matching_wrapper_is_signalled_when_metadata_identifies_its_child(self):
        owner = admission.caller_owner("wrapper")
        owner["start_id"] = "matching-wrapper"
        lease = admission.reserve(self.repo, job_id="wrapper", worker="codex", role="worker",
                                  model="gpt-5.6-luna", files=["subject.txt"], owner=owner)
        folder = self.repo / ".rig" / "jobs" / "wrapper"
        jobs.write_job_files(folder, "wrapper", "codex", "worker", "running", 0, jobs.iso_now(), "", "",
                             kind="wrapper", executor_kind="wrapper", model="gpt-5.6-luna", reservation=lease)
        meta = json.loads((folder / "meta.json").read_text())
        meta["pid"] = owner["pid"] + 100000
        (folder / "meta.json").write_text(json.dumps(meta))
        with patch.object(admission, "process_identity", return_value={"pid": owner["pid"], "start_id": "matching-wrapper"}), \
                patch.object(jobs.cancellation, "dispatch", side_effect=jobs.cancellation.signal_wrapper), patch.object(jobs.os, "kill") as signal:
            self.assertFalse(self.call("rig_job_cancel", id="wrapper").get("isError"))
        sent = [call.args for call in signal.call_args_list if call.args[1] != 0]
        self.assertEqual(sent, [(owner["pid"], jobs.signal.SIGTERM)])

    def test_two_mcp_parents_and_cli_share_atomic_scope_admission(self):
        code = '''
import json, os, subprocess, sys, time
from pathlib import Path
sys.path.insert(0, sys.argv[1] + '/scripts')
repo, name, mode = Path(sys.argv[2]), sys.argv[3], sys.argv[4]
(repo / (name + '.ready')).touch()
while not (repo / 'launch-gate').exists(): time.sleep(.005)
if mode == 'cli':
    result = subprocess.run([sys.argv[1] + '/bin/rig', 'job', 'start', name, '--role', 'parent', '--files-json', '["subject.txt"]', '--json'], cwd=repo, capture_output=True, text=True)
    print(json.dumps({'accepted': result.returncode == 0}))
else:
    import rig_mcp
    result = rig_mcp.call_tool('rig_job_start', {'repo': str(repo), 'id': name, 'role': 'parent', 'files': ['subject.txt']})
    print(json.dumps({'accepted': not result.get('isError', False)}))
'''
        processes = []
        try:
            for index, mode in enumerate(("mcp", "mcp", "cli")):
                name = f"contender-{index}"
                env = {**os.environ, "RIG_THREAD": name}
                processes.append(subprocess.Popen([sys.executable, "-c", code, str(ROOT), str(self.repo), name, mode],
                                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env))
            deadline = time.monotonic() + 10
            while len(list(self.repo.glob("*.ready"))) != 3 and time.monotonic() < deadline:
                time.sleep(.01)
            self.assertEqual(len(list(self.repo.glob("*.ready"))), 3)
            (self.repo / "launch-gate").touch()
            accepted = []
            for process in processes:
                stdout, stderr = process.communicate(timeout=10)
                self.assertEqual(process.returncode, 0, stderr)
                accepted.append(json.loads(stdout)["accepted"])
            self.assertEqual(sum(accepted), 1)
            self.assertEqual(len(admission.list_reservations(self.repo)), 1)
            self.assertEqual(len(list((self.repo / ".rig" / "jobs").glob("*/meta.json"))), 1)
        finally:
            for process in processes:
                if process.poll() is None:
                    process.kill()
                process.communicate()


class ParentWriteRecovery(NativeHarness):
    def recover(self, job_id="writer", **args):
        return self.call("rig_job_recover_parent_write", id=job_id,
                         **{"confirmed_stopped": True, "rationale": "Owning parent turn was cancelled", **args})

    def payload(self, result):
        self.assertFalse(result.get("isError"), result)
        text = result["content"][0]["text"]
        return json.loads(text), json.dumps(result)

    def test_schema_documents_attestation_and_omits_owner_token(self):
        tool = next(item for item in rig_mcp.TOOLS if item["name"] == "rig_job_recover_parent_write")
        props = tool["inputSchema"]["properties"]
        self.assertNotIn("owner_token", props)
        self.assertIn("confirmed_stopped", props)
        self.assertEqual(set(tool["inputSchema"]["required"]), {"id", "confirmed_stopped", "rationale"})
        self.assertIn("allow/approve is not completion", tool["description"].lower())
        self.assertIn("Codex/Pi", tool["description"])

    def test_same_owner_recovery_releases_only_that_parent_attempt(self):
        (self.repo / "other.txt").write_text("other\n")
        writer = self.start()
        other = self.start("other", files=["other.txt"])
        missing = self.recover(confirmed_stopped=False, rationale="still running")
        self.assertTrue(missing.get("isError"), missing)
        self.assertIn("confirmed_stopped=true", missing["content"][0]["text"])
        empty = self.recover(rationale="   ")
        self.assertTrue(empty.get("isError"), empty)
        result = self.recover()
        payload, dumped = self.payload(result)
        self.assertEqual(payload["applied"], 1)
        self.assertTrue(payload["recovery"]["confirmed_stopped"])
        self.assertEqual(payload["items"][0]["execution_status"], "cancelled")
        self.assertEqual(payload["items"][0]["stage"], "released")
        self.assertFalse(payload["items"][0]["slot_held"])
        self.assertFalse(payload["items"][0]["needs_reconciliation"])
        self.assertEqual(payload["items"][0]["completion"]["kind"], "parent_write_abandonment")
        self.assertNotIn(writer["owner_token"], dumped)
        self.assertNotIn(other["owner_token"], dumped)
        self.assertNotIn(writer["owner_token"], json.dumps(admission.list_reservations(self.repo, include_released=True)))
        self.assertEqual({row["job_id"] for row in admission.list_reservations(self.repo)}, {"other"})
        job = jobs.resolve_job(self.repo, "writer")
        self.assertEqual(job["status"], "cancelled")
        self.assertNotEqual(job["verification"]["state"], "verified")
        self.assertNotEqual(job["verification"].get("acceptance"), "accepted")
        self.assertTrue((self.repo / ".rig" / "jobs" / "writer" / "parent-write-recovery.json").is_file())
        repeat = self.payload(self.recover())[0]
        self.assertEqual(repeat["applied"], 0)
        self.assertEqual(repeat["items"][0]["attempt_id"], writer["attempt_id"])
        self.assertEqual(len(admission.list_reservations(self.repo)), 1)
        self.start("replacement")

    def test_missing_job_artifacts_recover_from_reservation_without_token(self):
        import shutil

        lease = self.start()
        shutil.rmtree(self.repo / ".rig" / "jobs" / "writer")
        result = self.recover()
        payload, dumped = self.payload(result)
        self.assertEqual(payload["applied"], 1)
        self.assertTrue(payload["artifacts_missing"])
        self.assertTrue(payload["recovery"]["artifacts_missing"])
        self.assertNotIn(lease["owner_token"], dumped)
        self.assertFalse((self.repo / ".rig" / "jobs" / "writer").exists())
        rows = admission.list_reservations(self.repo, include_released=True)
        self.assertEqual(rows[0]["execution_status"], "cancelled")
        self.assertEqual(rows[0]["stage"], "released")
        self.assertFalse(rows[0]["slot_held"])
        self.assertTrue(rows[0]["parent_write_recovery"]["artifacts_missing"])
        self.assertNotIn(lease["owner_token"], json.dumps(rows))
        self.start("replacement")

    def test_wrong_session_job_active_operation_and_child_attempts_are_rejected(self):
        lease = self.start()
        wrong_session = self.recover(owner_session="another-parent")
        self.assertTrue(wrong_session.get("isError"), wrong_session)
        current_session = os.environ.get("RIG_THREAD", "")
        try:
            os.environ["RIG_THREAD"] = "different-current-parent"
            spoofed_session = self.recover(owner_session=current_session)
            self.assertTrue(spoofed_session.get("isError"), spoofed_session)
            self.assertIn("current parent session", spoofed_session["content"][0]["text"])
        finally:
            os.environ["RIG_THREAD"] = current_session
        missing_job = self.recover("unbound-job")
        self.assertTrue(missing_job.get("isError"), missing_job)
        path = self.repo / ".rig" / "reservations" / (lease["reservation_id"] + ".json")
        record = json.loads(path.read_text())
        record["operation"] = {"id": "active", "operation": "check", "pid": os.getpid(), "start_id": "live"}
        path.write_text(json.dumps(record))
        active = self.recover()
        self.assertTrue(active.get("isError"), active)
        self.assertIn("verification", active["content"][0]["text"])
        record.pop("operation")
        path.write_text(json.dumps(record))
        self.assertEqual([row["job_id"] for row in admission.list_reservations(self.repo)], ["writer"])
        (self.repo / "child.txt").write_text("child\n")
        self.start("child", role="mini", executor_kind="native_child", native_agent_id="agent-A",
                   model="gpt-5.6-luna", files=["child.txt"])
        child = self.recover("child")
        self.assertTrue(child.get("isError"), child)
        self.assertIn("native_child", child["content"][0]["text"])
        (self.repo / "wrap.txt").write_text("wrap\n")
        owner = admission.caller_owner("wrapper")
        wrapper = admission.reserve(self.repo, job_id="wrapper", worker="codex", role="worker",
                                    model="gpt-5.6-luna", files=["wrap.txt"], owner=owner)
        folder = self.repo / ".rig" / "jobs" / "wrapper"
        jobs.write_job_files(folder, "wrapper", "codex", "worker", "running", 0, jobs.iso_now(), "", "",
                             kind="wrapper", executor_kind="wrapper", model="gpt-5.6-luna", reservation=wrapper)
        wrapped = self.recover("wrapper")
        self.assertTrue(wrapped.get("isError"), wrapped)
        self.assertIn("wrapper", wrapped["content"][0]["text"])
        self.assertTrue(any(row["job_id"] == "writer" and row["stage"] != "released"
                            for row in admission.list_reservations(self.repo)))
        closed = self.call("rig_job_close", id="writer", rationale="still live", **self.auth(lease))
        self.assertTrue(closed.get("isError"), closed)

    def test_released_scope_and_successful_finish_cannot_use_abandonment(self):
        lease = self.start()
        self.assertFalse(self.finish(lease).get("isError"))
        refused = self.recover()
        self.assertTrue(refused.get("isError"), refused)
        closed = self.call("rig_job_close", id="writer", rationale="Conclude without acceptance", **self.auth(lease))
        self.assertFalse(closed.get("isError"), closed)
        released = self.recover()
        self.assertTrue(released.get("isError"), released)
        self.assertIn("released", released["content"][0]["text"])

    def test_pi_parent_session_recovers_without_owner_token(self):
        os.environ["RIG_PARENT"] = "pi"
        os.environ["RIG_THREAD"] = "pi-parent-session"
        lease = self.start()
        result = self.recover(owner_session="pi-parent-session")
        payload, dumped = self.payload(result)
        self.assertEqual(payload["applied"], 1)
        self.assertNotIn(lease["owner_token"], dumped)
        self.assertEqual(payload["items"][0]["owner"]["session_id"], "pi-parent-session")
        self.start("replacement")

    def test_queued_parent_write_recovery_cancels_queue_item(self):
        import work_queue

        item = work_queue.add_item(self.repo, "Queued parent write")
        claimed = self.call("rig_queue_claim", id=item["id"], worker="codex", files=["subject.txt"])
        self.assertFalse(claimed.get("isError"), claimed)
        self.start(queue_id=item["id"], **self.auth(claimed["structuredContent"]))
        self.payload(self.recover())
        self.assertEqual(work_queue.load_item(self.repo, item["id"])["status"], "cancelled")
        self.assertNotEqual(work_queue.load_item(self.repo, item["id"])["status"], "pending")

    def test_cli_recover_parent_write_matches_mcp(self):
        lease = self.start()
        cli = subprocess.run(
            [str(ROOT / "bin" / "rig"), "job", "recover-parent-write", "writer",
             "--confirmed-stopped", "--rationale", "Owning parent turn was cancelled"],
            cwd=self.repo, text=True, capture_output=True,
        )
        self.assertEqual(cli.returncode, 0, cli.stderr)
        payload = json.loads(cli.stdout)
        self.assertEqual(payload["applied"], 1)
        self.assertNotIn(lease["owner_token"], cli.stdout)
        self.assertNotIn(lease["owner_token"], cli.stderr)
        repeat = subprocess.run(
            [str(ROOT / "bin" / "rig"), "job", "recover-parent-write", "writer",
             "--confirmed-stopped", "--rationale", "already recovered"],
            cwd=self.repo, text=True, capture_output=True,
        )
        self.assertEqual(repeat.returncode, 0, repeat.stderr)
        self.assertEqual(json.loads(repeat.stdout)["applied"], 0)


if __name__ == "__main__":
    unittest.main()
