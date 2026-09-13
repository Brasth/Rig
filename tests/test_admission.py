#!/usr/bin/env python3
"""Admission invariants, including real competing processes and crash boundaries."""
import json
import multiprocessing
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import admission
import jobs
import work_queue


def _owner(kind="parent", session="admission-tests"):
    identity = admission.process_identity(os.getpid())
    parent = admission.process_identity(os.getppid())
    return {**identity, "kind": kind, "session_id": session, "parent_cli": "codex",
            "parent_pid": parent.get("pid"), "parent_start_id": parent.get("start_id", "")}


def _race(repo, barrier, release, results, number, mode, same_scope):
    try:
        barrier.wait(timeout=15)
        files = ["shared.py" if same_scope else f"file-{number}.py"]
        if mode == "mcp":
            import rig_mcp
            value = rig_mcp.call_tool("rig_job_start", {
                "repo": repo, "id": f"race-{number}", "worker": "grok", "role": "parent",
                "files": files, "owner_session": f"race-owner-{number}"})
            if value.get("isError"):
                raise admission.AdmissionError(value["content"][0]["text"])
            lease = value["structuredContent"]
        elif mode == "queue":
            lease = work_queue.claim_next(Path(repo), item_id=f"queue-{number}", worker="grok",
                                           files=files, owner=_owner(session=f"race-owner-{number}"))
        else:
            lease = admission.reserve(repo, job_id=f"race-{number}", worker="grok", files=files,
                                      owner=_owner("wrapper" if mode == "wrapper" else "parent",
                                                   f"race-owner-{number}"))
        results.put((True, lease["reservation_id"]))
    except BaseException as error:
        results.put((False, str(error)))
    finally:
        release.wait(timeout=20)


def _crash_reservation(repo, output, queue_id="", launched=False):
    lease = admission.reserve(repo, job_id="crashed" if not queue_id else "", queue_id=queue_id,
                              worker="grok", files=["crash.py"], owner=_owner())
    if launched:
        admission.activate(repo, **admission.credentials(lease), job_id="crashed", worker="grok",
                           access="write", files=["crash.py"], owner=_owner())
    Path(output).write_text(json.dumps(lease))
    os._exit(23)


def _lock_probe(repo, connection):
    with admission.transaction(repo):
        connection.send("acquired")
    connection.close()


class AdmissionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.repo = Path(self.temp.name).resolve()
        (self.repo / ".git").mkdir()
        (self.repo / ".rig").mkdir()
        self.configure()
        self.env = mock.patch.dict(os.environ, {"RIG_OWNER_SESSION": "admission-tests"})
        self.env.start()
        self.owner = _owner()

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def configure(self, cap=3, per_worker=0):
        (self.repo / ".rig" / "harness.toml").write_text(
            'parent = "codex"\n[workers]\ngrok = true\nclaude = true\ncodex = true\n'
            f'[queue]\nmax_running = {cap}\nmax_per_worker = {per_worker}\n')

    def reserve(self, job="job", files=None, **options):
        return admission.reserve(self.repo, job_id=job, worker=options.pop("worker", "grok"),
                                 files=["a.py"] if files is None else files,
                                 owner=options.pop("owner", self.owner), **options)

    def activate(self, lease, **options):
        return admission.activate(self.repo, **admission.credentials(lease), job_id=lease["job_id"],
                                  worker=lease["worker"], files=lease["declared_files"], access=lease["access"],
                                  owner=options.pop("owner", self.owner), **options)

    def finish(self, lease, status="ok", **options):
        return admission.finish(self.repo, **admission.credentials(lease), status=status,
                                owner=options.pop("owner", self.owner), **options)

    def close(self, lease, **options):
        return admission.release(self.repo, **admission.credentials(lease), owner=self.owner,
                                 rationale="parent explicitly closed task", **options)

    def queue(self, key="queued"):
        item = work_queue.add_item(self.repo, "preserve this brief")
        path = self.repo / ".rig" / "queue" / f'{item["id"]}.json'
        item["id"] = key
        target = path.with_name(key + ".json")
        target.write_text(json.dumps(item))
        path.unlink()
        return item

    def test_multiprocess_shared_scope_has_one_owner(self):
        self._run_race(cap=3, same_scope=True, modes=["wrapper", "mcp", "queue"], expected=1)

    def test_multiprocess_global_cap_shared_by_mcp_queue_wrapper(self):
        self._run_race(cap=2, same_scope=False, modes=["mcp", "wrapper", "queue", "mcp"], expected=2)

    def test_multiprocess_worker_cap(self):
        self._run_race(cap=3, per_worker=1, same_scope=False, modes=["mcp", "mcp", "wrapper"], expected=1)

    def _run_race(self, *, cap, same_scope, modes, expected, per_worker=0):
        self.configure(cap, per_worker)
        ctx = multiprocessing.get_context("spawn")
        barrier, release, results = ctx.Barrier(len(modes)), ctx.Event(), ctx.Queue()
        for number, mode in enumerate(modes):
            if mode == "queue":
                self.queue(f"queue-{number}")
        children = [ctx.Process(target=_race, args=(str(self.repo), barrier, release, results,
                                                   number, mode, same_scope))
                    for number, mode in enumerate(modes)]
        try:
            for child in children:
                child.start()
            answers = [results.get(timeout=25) for _ in children]
            self.assertEqual(sum(answer[0] for answer in answers), expected, answers)
            records = admission.list_reservations(self.repo)
            self.assertEqual(len(records), expected)
            self.assertEqual(sum(record["slot_held"] for record in records), expected)
            self.assertEqual(len(admission._accounting(self.repo)), expected)
            self.assertNotIn("owner_token", json.dumps(records))
        finally:
            release.set()
            for child in children:
                child.join(timeout=5)
                if child.is_alive():
                    child.terminate()
                    child.join()

    def test_flock_failure_refuses_all_mutation(self):
        with mock.patch.object(admission.fcntl, "flock", side_effect=OSError("unsupported filesystem")):
            with self.assertRaisesRegex(admission.AdmissionError, "lock unavailable"):
                self.reserve()
            with self.assertRaisesRegex(admission.AdmissionError, "lock unavailable"):
                work_queue.add_item(self.repo, "not persisted")
        self.assertEqual(admission.list_reservations(self.repo), [])
        self.assertEqual(work_queue.list_items(self.repo), [])

    def test_threads_serialize_reentrant_transactions(self):
        entered, release, second = threading.Event(), threading.Event(), threading.Event()
        def holder():
            with admission.transaction(self.repo):
                with admission.transaction(self.repo):
                    entered.set()
                    release.wait(5)
        def contender():
            entered.wait(5)
            with admission.transaction(self.repo):
                second.set()
        first, other = threading.Thread(target=holder), threading.Thread(target=contender)
        first.start(); other.start()
        self.assertTrue(entered.wait(5))
        self.assertFalse(second.wait(.1))
        release.set(); first.join(5); other.join(5)
        self.assertTrue(second.is_set())

    def test_fork_does_not_inherit_reentrant_authority(self):
        if "fork" not in multiprocessing.get_all_start_methods():
            self.skipTest("fork is unavailable")
        ctx = multiprocessing.get_context("fork")
        reader, writer = ctx.Pipe(duplex=False)
        with admission.transaction(self.repo):
            child = ctx.Process(target=_lock_probe, args=(str(self.repo), writer))
            child.start()
            self.assertFalse(reader.poll(.15), "forked child bypassed parent's held lock")
        try:
            self.assertTrue(reader.poll(5))
            self.assertEqual(reader.recv(), "acquired")
        finally:
            child.join(5)
            if child.is_alive():
                child.terminate(); child.join()
            reader.close(); writer.close()

    def test_aliases_symlinks_spaces_and_literal_brackets(self):
        (self.repo / "dir").mkdir()
        (self.repo / "a.py").write_text("a")
        (self.repo / "alias").symlink_to(self.repo / "a.py")
        lease = self.reserve(files=["./a.py"])
        for alias in ["dir/../a.py", str(self.repo / "a.py"), "alias"]:
            with self.subTest(alias=alias), self.assertRaisesRegex(admission.AdmissionError, "overlap"):
                self.reserve("collision", files=[alias])
        self.close(lease)
        names = [" leading.py", "trailing.py ", "app/[id]/page.tsx", "日本語.py"]
        lease = self.reserve("literal", files=names)
        self.assertEqual(set(lease["declared_files"]), set(names))
        self.assertEqual(set(lease["files"]), set(names))
        for name in ["../escape", str(self.repo.parent / "escape"), "dir", "new*.py"]:
            with self.subTest(name=name), self.assertRaises((admission.AdmissionError, ValueError)):
                admission.canonical_files(self.repo, [name])

    def test_read_read_allowed_and_write_conflicts_both_directions(self):
        first = self.reserve(access="read")
        self.reserve("read-two", access="read")
        with self.assertRaisesRegex(admission.AdmissionError, "overlap"):
            self.reserve("writer")
        self.close(first)
        self.reserve("disjoint", files=["b.py"])
        with self.assertRaisesRegex(admission.AdmissionError, "overlap"):
            self.reserve("read-disjoint", files=["b.py"], access="read")

    def test_unknown_scope_is_exclusive_parent_writer(self):
        lease = self.reserve(files=[], role="parent")
        for access in ["read", "write"]:
            with self.assertRaisesRegex(admission.AdmissionError, "exclusive"):
                self.reserve("other", files=["b.py"], access=access)
        self.close(lease)
        self.reserve("reader", access="read")
        with self.assertRaisesRegex(admission.AdmissionError, "exclusive"):
            self.reserve("unknown", files=[])

    def test_exact_credentials_and_owner_required_for_transitions(self):
        lease = self.reserve()
        wrong = admission.credentials(lease)
        wrong["owner_token"] = "incorrect"
        with self.assertRaisesRegex(admission.AdmissionError, "credentials mismatch"):
            admission.release(self.repo, **wrong, owner=self.owner, rationale="no")
        with self.assertRaisesRegex(admission.AdmissionError, "session mismatch"):
            admission.release(self.repo, **admission.credentials(lease), owner=_owner(session="other"), rationale="no")
        with self.assertRaisesRegex(admission.AdmissionError, "job or worker mismatch"):
            self.reserve("another", **admission.credentials(lease))
        self.activate(lease)
        with self.assertRaisesRegex(admission.AdmissionError, "already launched"):
            self.reserve(**admission.credentials(lease))
        self.assertEqual(self.activate(lease)["attempt_id"], lease["attempt_id"])

    def test_bound_attempt_model_and_role_are_immutable(self):
        lease = self.reserve(model="grok-4", role="implement")
        for update in [{"model": "grok-5", "role": "implement"}, {"model": "grok-4", "role": "hard"}]:
            with self.subTest(update=update), self.assertRaisesRegex(admission.AdmissionError, "immutable"):
                self.reserve(**admission.credentials(lease), **update)

    def test_native_parent_completion_frees_slot_but_holds_files(self):
        self.configure(cap=1)
        lease = self.reserve(role="parent")
        self.activate(lease)
        unknown = self.finish(lease)
        self.assertTrue(unknown["slot_held"])
        self.assertTrue(unknown["needs_reconciliation"])
        completed = self.finish(lease, completion={"kind": "parent_task", "completed": True})
        self.assertEqual(completed["stage"], "verifying")
        self.assertFalse(completed["slot_held"])
        os.kill(self.owner["pid"], 0)  # Task completion does not require parent exit.
        with self.assertRaisesRegex(admission.AdmissionError, "overlap"):
            self.reserve("overlap")
        next_job = self.reserve("disjoint", files=["b.py"])
        self.close(next_job)
        self.close(lease)
        self.reserve("replacement")
        self.assertEqual(self.close(lease)["stage"], "released")

    def test_native_child_completion_binds_specific_observed_agent(self):
        child_owner = _owner("native_child")
        lease = self.reserve(owner=child_owner, model="grok-4")
        self.activate(lease, owner=child_owner, native_agent_id="agent-one")
        unknown = self.finish(lease, owner=child_owner,
                              completion={"kind": "native_child", "agent_id": "agent-two", "terminal": True, "outcome": "ok"})
        self.assertFalse(unknown["stopped"])
        completed = self.finish(lease, owner=child_owner,
                                completion={"kind": "native_child", "agent_id": "agent-one", "terminal": True, "outcome": "ok"})
        self.assertTrue(completed["stopped"])

    def test_claim_consumption_and_accounting_are_single_attempt(self):
        self.configure(cap=1)
        item = self.queue()
        claim = work_queue.claim_next(self.repo, item_id=item["id"], files=["a.py"], owner=self.owner)
        lease = self.reserve("queued-job", queue_id=item["id"], model="grok-4", role="implement",
                             **admission.credentials(claim))
        self.assertEqual(lease["model"], "grok-4")
        self.assertEqual(len(admission._accounting(self.repo)), 1)
        self.activate(lease)
        for _ in range(2):
            row = work_queue.mark_spawned(self.repo, item["id"], "queued-job", **admission.credentials(lease), owner=self.owner)
            self.assertEqual(row["status"], "spawned")
        with self.assertRaisesRegex(admission.AdmissionError, "unconsumed"):
            work_queue.unclaim(self.repo, item["id"], **admission.credentials(lease), owner=self.owner)
        self.finish(lease, completion={"kind": "parent_task", "completed": True})
        self.assertEqual(work_queue.load_item(self.repo, item["id"])["status"], "done")
        self.assertEqual(len(admission._accounting(self.repo)), 1)
        self.close(lease)
        self.assertEqual(admission._accounting(self.repo), [])

    def test_unknown_worker_claim_counts_against_each_worker_cap(self):
        self.configure(per_worker=1)
        item = self.queue()
        work_queue.claim_next(self.repo, item_id=item["id"], files=["a.py"], owner=self.owner)
        for worker in ["grok", "claude"]:
            with self.subTest(worker=worker), self.assertRaisesRegex(admission.AdmissionError, "max_per_worker"):
                self.reserve("another", worker=worker, files=["b.py"])

    def test_dry_run_has_no_reservation_and_consumes_no_claim(self):
        item = self.queue()
        value = admission.reserve(self.repo, job_id="preview", queue_id=item["id"], worker="grok",
                                  files=["a.py"], owner=self.owner, dry_run=True)
        self.assertEqual(value["stage"], "dry_run")
        self.assertEqual(admission.list_reservations(self.repo), [])
        self.assertEqual(work_queue.load_item(self.repo, item["id"])["status"], "pending")
        self.reserve("active")
        with self.assertRaisesRegex(admission.AdmissionError, "already belongs"):
            self.reserve("active", dry_run=True)

    def test_dead_unlaunched_owner_reconciles_but_launched_owner_keeps_scope(self):
        ctx = multiprocessing.get_context("spawn")
        item = self.queue()
        output = self.repo / "claim-result.json"
        child = ctx.Process(target=_crash_reservation, args=(str(self.repo), str(output), item["id"]))
        child.start(); child.join(10)
        self.assertEqual(child.exitcode, 23)
        self.assertEqual(admission.reconcile(self.repo)["applied"], 0)
        self.assertEqual(admission.reconcile(self.repo, apply=True)["applied"], 1)
        self.assertEqual(admission.reconcile(self.repo, apply=True)["applied"], 0)
        self.assertEqual(work_queue.load_item(self.repo, item["id"])["status"], "pending")
        child = ctx.Process(target=_crash_reservation, args=(str(self.repo), str(output), "", True))
        child.start(); child.join(10)
        self.assertEqual(child.exitcode, 23)
        self.assertEqual(admission.reconcile(self.repo, apply=True)["applied"], 0)
        with self.assertRaisesRegex(admission.AdmissionError, "overlap"):
            self.reserve("replacement", files=["crash.py"])

    def test_pid_reuse_dead_and_unknown_owner_never_expires(self):
        reused = {"pid": os.getpid(), "start_id": "different-incarnation"}
        with mock.patch.object(admission, "process_identity", return_value={"pid": os.getpid(), "start_id": "current-incarnation"}):
            self.assertEqual(admission._process_state(reused), "dead")
        lease = self.reserve(owner={**self.owner, "pid": None, "start_id": ""})
        admission.reconcile(self.repo, apply=True)
        self.assertTrue(admission.get_reservation(self.repo, lease["reservation_id"])["slot_held"])

    def test_cancelled_unlaunched_queue_is_not_retried(self):
        item = self.queue()
        claim = work_queue.claim_next(self.repo, item_id=item["id"], files=["a.py"], owner=self.owner)
        lease = self.reserve("cancelled", queue_id=item["id"], **admission.credentials(claim))
        folder = self.repo / ".rig" / "jobs" / "cancelled"
        folder.mkdir(parents=True)
        (folder / "cancel.json").write_text("{}")
        with self.assertRaisesRegex(admission.AdmissionError, "cancellation"):
            self.activate(lease)
        self.close(lease, mode="launch_failed")
        self.assertEqual(work_queue.load_item(self.repo, item["id"])["status"], "cancelled")
        self.assertEqual(admission._accounting(self.repo), [])

    def test_release_queue_write_failure_replays_idempotently(self):
        item = self.queue()
        lease = work_queue.claim_next(self.repo, item_id=item["id"], files=["a.py"], owner=self.owner)
        with mock.patch.object(admission, "_queue_update", side_effect=OSError("disk interrupted")):
            with self.assertRaises(OSError):
                self.close(lease, mode="launch_failed")
        self.assertEqual(work_queue.load_item(self.repo, item["id"])["status"], "claimed")
        admission.reconcile(self.repo, apply=True)
        self.assertEqual(work_queue.load_item(self.repo, item["id"])["status"], "pending")
        admission.reconcile(self.repo, apply=True)
        self.assertEqual(work_queue.load_item(self.repo, item["id"])["text"], "preserve this brief")

    def test_cancelled_legacy_live_or_unknown_process_keeps_scope(self):
        folder = self.repo / ".rig" / "jobs" / "legacy"
        folder.mkdir(parents=True)
        meta = {"status": "cancelled", "role": "implement", "worker": "grok", "files": ["a.py"],
                "legacy_cancel_requested": True, "pid": os.getpid()}
        (folder / "meta.json").write_text(json.dumps(meta))
        with self.assertRaisesRegex(admission.AdmissionError, "overlap"):
            self.reserve("replacement")
        with self.assertRaisesRegex(admission.AdmissionError, "live processes"):
            admission.reconcile(self.repo, job_id="legacy", apply=True, action="adopt", worker="grok",
                                owner=self.owner, files=["a.py"], rationale="checked", completion={"confirmed_stopped": True})
        meta["pid"] = 0
        (folder / "meta.json").write_text(json.dumps(meta))
        recovery = admission.reconcile(self.repo, job_id="legacy", apply=True, action="adopt", worker="grok",
                                        owner=self.owner, files=["a.py"], rationale="checked", completion={"confirmed_stopped": True})
        self.assertFalse(recovery["items"][0]["ownership_established"])
        self.close(recovery)
        self.reserve("replacement")
        self.assertNotIn("reservation_id", json.loads((folder / "meta.json").read_text()))

    def test_legacy_claim_adoption_is_atomic_and_prospective(self):
        item = self.queue()
        path = self.repo / ".rig" / "queue" / (item["id"] + ".json")
        item.update(status="claimed", files=["a.py"])
        path.write_text(json.dumps(item))
        with self.assertRaisesRegex(admission.AdmissionError, "overlap"):
            self.reserve()
        original = admission._write
        def fail_queue(target, value):
            if target == path:
                raise OSError("queue persistence interrupted")
            return original(target, value)
        with mock.patch.object(admission, "_write", side_effect=fail_queue):
            with self.assertRaises(OSError):
                admission.reconcile(self.repo, queue_id=item["id"], apply=True, action="adopt", owner=self.owner,
                                    worker="grok", files=["a.py"], rationale="new explicit parent")
        self.assertEqual(json.loads(path.read_text())["status"], "claimed")
        self.assertEqual(len(admission.list_reservations(self.repo)), 1)
        with self.assertRaisesRegex(admission.AdmissionError, "overlap"):
            self.reserve()

    def test_legacy_claim_release_requires_attestation_and_keeps_text(self):
        item = self.queue()
        item.update(status="claimed", files=["a.py"])
        (self.repo / ".rig" / "queue" / (item["id"] + ".json")).write_text(json.dumps(item))
        args = dict(queue_id=item["id"], apply=True, action="release", owner=self.owner, rationale="nothing launched")
        with self.assertRaisesRegex(admission.AdmissionError, "attestation"):
            admission.reconcile(self.repo, **args)
        result = admission.reconcile(self.repo, **args, completion={"confirmed_stopped": True})
        self.assertEqual(result["items"][0]["status"], "pending")
        self.assertEqual(result["items"][0]["text"], item["text"])
        self.assertEqual(admission.reconcile(self.repo, **args, completion={"confirmed_stopped": True})["applied"], 0)

    def test_released_bound_metadata_does_not_reappear_as_legacy(self):
        lease = self.reserve()
        folder = self.repo / ".rig" / "jobs" / "job"
        folder.mkdir(parents=True)
        (folder / "meta.json").write_text(json.dumps({"job_id": "job", "status": "running", "worker": "grok",
                                                      "files": ["a.py"], **{key: lease[key] for key in ["reservation_id", "attempt_id"]}}))
        self.close(lease, mode="launch_failed")
        self.assertEqual(admission._accounting(self.repo), [])
        self.reserve("replacement")

    def test_credentials_artifact_is_private_and_read_projection_redacted(self):
        lease = self.reserve()
        path = admission.write_credentials(self.repo, lease)
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(json.loads(path.read_text())["owner_token"], lease["owner_token"])
        public = admission.get_reservation(self.repo, lease["reservation_id"])
        self.assertNotIn("owner_token", public)
        self.assertNotIn(lease["owner_token"], work_queue.format_block(self.repo))


    def accepted_writer(self):
        import verification
        import change_evidence
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        (self.repo / "a.py").write_text("protected content")
        lease = jobs.start_job(self.repo, job_id="writer", worker="parent", role="parent", model="gpt-6",
                               files=["a.py"], owner_session="admission-tests", live="codex", return_details=True)
        auth = {**admission.credentials(lease), "owner_session": "admission-tests"}
        jobs.finish_job(self.repo, "writer", status="ok", **auth,
                        completion={"kind": "parent_task", "completed": True})
        folder = self.repo / ".rig" / "jobs" / "writer"
        verification.record_requirements(self.repo, folder, [], ["Subject is ready for review"], **auth)
        snapshot = change_evidence.snapshot(self.repo, ["a.py"])["snapshot_id"]
        verification.accept(self.repo, folder, "accept", snapshot, rationale="Parent checked subject", next="review", **auth)
        return lease, snapshot

    def reviewer(self, writer, snapshot, job="reviewer"):
        return self.reserve(job, worker="claude", role="review", model="claude-sonnet-4-6",
                            access="read", writer_job_id="writer", writer_snapshot_id=snapshot,
                            **admission.credentials(writer))

    def test_review_handoff_has_no_gap_and_rotates_attempt(self):
        writer, snapshot = self.accepted_writer()
        reviewer = self.reviewer(writer, snapshot)
        self.assertEqual(reviewer["reservation_id"], writer["reservation_id"])
        self.assertNotEqual(reviewer["attempt_id"], writer["attempt_id"])
        self.assertNotEqual(reviewer["owner_token"], writer["owner_token"])
        self.assertEqual(reviewer["access"], "read")
        with self.assertRaisesRegex(admission.AdmissionError, "overlap"):
            self.reserve("unrelated-writer")
        with self.assertRaisesRegex(admission.AdmissionError, "credentials mismatch"):
            self.close(writer)

    def test_failed_review_launch_retains_scope_and_supports_fresh_retry(self):
        writer, snapshot = self.accepted_writer()
        reviewer = self.reviewer(writer, snapshot)
        failed = self.close(reviewer, mode="launch_failed")
        self.assertTrue(failed["stopped"])
        self.assertFalse(failed["slot_held"])
        self.assertTrue(failed["review_launch_failed"])
        self.assertEqual(failed["files"], ["a.py"])
        with self.assertRaisesRegex(admission.AdmissionError, "overlap"):
            self.reserve("unrelated")
        next_reviewer = self.reviewer(reviewer, snapshot, "retry-reviewer")
        self.assertNotEqual(next_reviewer["attempt_id"], reviewer["attempt_id"])
        self.close(next_reviewer, mode="launch_failed")
        self.close(next_reviewer)
        self.reserve("explicitly-released")

    def test_review_waiting_for_full_cap_keeps_writer_scope(self):
        writer, snapshot = self.accepted_writer()
        for number in range(3):
            self.reserve("other-" + str(number), files=[f"other-{number}.py"])
        with self.assertRaisesRegex(admission.AdmissionError, "3/3"):
            self.reviewer(writer, snapshot)
        held = admission.get_reservation(self.repo, writer["reservation_id"])
        self.assertEqual(held["attempt_id"], writer["attempt_id"])
        self.assertEqual(held["files"], ["a.py"])
        self.assertFalse(held["slot_held"])

    def test_review_launch_rechecks_current_snapshot(self):
        writer, snapshot = self.accepted_writer()
        reviewer = self.reviewer(writer, snapshot)
        (self.repo / "a.py").write_text("external edit invalidates review")
        with self.assertRaises((admission.AdmissionError, ValueError)):
            self.activate(reviewer)
        self.close(reviewer, mode="launch_failed")
        self.assertTrue(admission.get_reservation(self.repo, reviewer["reservation_id"])["review_launch_failed"])
        with self.assertRaisesRegex(admission.AdmissionError, "overlap"):
            self.reserve("intruder")

    def test_finished_execution_result_cannot_be_promoted(self):
        lease = self.reserve()
        self.activate(lease)
        self.finish(lease, status="fail", completion={"kind": "parent_task", "completed": True})
        for closed in [False, True]:
            if closed:
                self.close(lease)
            with self.assertRaisesRegex(admission.AdmissionError, "immutable"):
                self.finish(lease, status="ok", completion={"kind": "parent_task", "completed": True})
            self.assertEqual(self.finish(lease, status="fail")["execution_status"], "fail")

    def test_interrupted_check_recovery_needs_exact_owner_and_stopped_checker(self):
        lease = self.reserve()
        self.activate(lease)
        self.finish(lease, completion={"kind": "parent_task", "completed": True})
        folder = self.repo / ".rig" / "jobs" / lease["job_id"]
        folder.mkdir(parents=True)
        record = admission._read(admission._reservation_path(self.repo, lease["reservation_id"]), required=True)
        record["operation"] = {"id": "interrupted-check", "operation": "check", **admission.process_identity(os.getpid())}
        admission._save(self.repo, record)
        args = dict(job_id=lease["job_id"], apply=True, completion={"checks_stopped": True},
                    rationale="Parent inspected and confirmed stopped checks", **admission.credentials(lease))
        with self.assertRaisesRegex(admission.AdmissionError, "still alive"):
            admission.reconcile(self.repo, **args, owner=self.owner)
        with self.assertRaisesRegex(admission.AdmissionError, "session mismatch"):
            admission.reconcile(self.repo, **args, owner=_owner(session="different"))
        # A real terminated checker, with no surviving recorded child, can be recovered.
        child = subprocess.Popen([sys.executable, "-c", "pass"])
        record["operation"].update(admission.process_identity(child.pid))
        child.wait()
        admission._save(self.repo, record)
        (folder / "check-running.json").write_text(json.dumps({"pid": child.pid, "process_pid": os.getpid()}))
        with self.assertRaisesRegex(admission.AdmissionError, "still alive"):
            admission.reconcile(self.repo, **args, owner=self.owner)
        (folder / "check-running.json").write_text(json.dumps({"pid": child.pid, "process_pid": child.pid}))
        result = admission.reconcile(self.repo, **args, owner=self.owner)
        self.assertEqual(result["applied"], 1)
        self.assertTrue(result["items"][0]["needs_reconciliation"])
        self.assertFalse((folder / "check-running.json").exists())
        self.assertEqual(admission.reconcile(self.repo, **args, owner=self.owner)["applied"], 0)
        with self.assertRaisesRegex(admission.AdmissionError, "overlap"):
            self.reserve("another")
        self.close(lease)

    def test_external_cancel_requires_process_death_and_group_visibility(self):
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], start_new_session=True)
        owner = _owner("wrapper")
        lease = self.reserve(owner=owner)
        process = admission.process_identity(child.pid)
        try:
            self.activate(lease, owner=owner, process=process)
            result = self.finish(lease, status="cancelled", owner=owner)
            self.assertFalse(result["stopped"])
            with self.assertRaisesRegex(admission.AdmissionError, "overlap"):
                self.reserve("overlap")
            child.terminate(); child.wait(5)
            result = self.finish(lease, status="cancelled", owner=owner)
            if not process.get("start_id"):
                # Sandboxed process-table denial must retain ownership, not fake death.
                self.assertTrue(result["needs_reconciliation"])
                self.assertFalse(result["stopped"])
            else:
                self.assertTrue(result["stopped"], result)
                self.assertFalse(result["slot_held"])
                admission.release(self.repo, **admission.credentials(lease), owner=owner, rationale="Stopped cancellation")
                self.reserve("replacement")
        finally:
            if child.poll() is None:
                child.kill(); child.wait()


if __name__ == "__main__":
    unittest.main()
