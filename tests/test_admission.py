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
        self.env = mock.patch.dict(os.environ, {
            "RIG_OWNER_SESSION": "admission-tests",
            "RIG_JOB_ID": "",
            "RIG_JOB_DIR": "",
            "RIG_OWNER_TOKEN": "",
        })
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

    def test_allowed_read_overlap_still_enforces_resource_conflicts(self):
        writer = self.reserve(
            "writer", files=["a.py"],
            resources=[{"name": "db.main", "access": "write"}],
        )
        rows = admission._accounting(self.repo)
        allowed = [writer["reservation_id"]]
        admission._capacity(
            self.repo, rows, "grok", "read", ["a.py"], resources=[],
            allow_read_overlap_reservations=allowed,
        )
        with self.assertRaisesRegex(admission.AdmissionError, "resource overlap"):
            admission._capacity(
                self.repo, rows, "grok", "read", ["a.py"],
                resources=[{"name": "db.main", "access": "read"}],
                allow_read_overlap_reservations=allowed,
            )
        with self.assertRaisesRegex(admission.AdmissionError, "resource overlap"):
            admission._capacity(
                self.repo, rows, "grok", "write", ["b.py"],
                resources=[{"name": "db.main", "access": "write"}],
                allow_read_overlap_reservations=allowed,
            )
        with self.assertRaisesRegex(admission.AdmissionError, "overlap"):
            admission._capacity(
                self.repo, rows, "grok", "write", ["a.py"],
                resources=[],
                allow_read_overlap_reservations=allowed,
            )

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

    def test_late_cancellation_preserves_confirmed_queue_outcome(self):
        for outcome in ("ok", "fail", "timeout"):
            with self.subTest(outcome=outcome):
                item = work_queue.add_item(self.repo, "work " + outcome)
                lease = self.reserve("late-" + outcome, queue_id=item["id"], files=[outcome + ".py"])
                self.activate(lease)
                # The marker arrives after the confirmed reservation commit but
                # before the queue projection can be replayed.
                with mock.patch.object(admission, "_queue_update", side_effect=OSError("crash")):
                    with self.assertRaises(OSError):
                        self.finish(lease, status=outcome, completion={"kind": "parent_task", "completed": True})
                folder = self.repo / ".rig" / "jobs" / lease["job_id"] / "cancellation"
                folder.mkdir(parents=True, exist_ok=True)
                (folder / (lease["attempt_id"] + ".json")).write_text(json.dumps({
                    "attempt_id": lease["attempt_id"], "reservation_id": lease["reservation_id"]}))
                result = self.finish(lease, status=outcome)
                self.assertEqual(result["execution_status"], outcome)
                self.assertEqual(work_queue.load_item(self.repo, item["id"])["status"], "done")
                self.close(lease)
                self.assertEqual(work_queue.load_item(self.repo, item["id"])["status"], "done")

    def test_finish_replays_queue_projection_after_write_failure(self):
        lease = self.reserve()
        self.activate(lease)
        with mock.patch.object(admission, "_queue_update", side_effect=OSError("projection unavailable")):
            with self.assertRaises(OSError):
                self.finish(lease, completion={"kind": "parent_task", "completed": True})
        record = admission.get_reservation(self.repo, lease["reservation_id"])
        self.assertTrue(record["stopped"])
        self.assertFalse(record["slot_held"])
        self.assertEqual(record["pending_operation"], "queue_done")
        with mock.patch.object(admission, "_queue_update") as projection, \
             mock.patch.object(admission, "_stopped", side_effect=AssertionError("terminal state must not be reopened")):
            retried = self.finish(lease)
        projection.assert_called_once()
        self.assertNotIn("pending_operation", retried)
        self.assertTrue(retried["stopped"])

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

    def stopped_wrapper(self, job="wrap", status="fail", *, session="wrapper-owner", queue_id="", files=None):
        owner = _owner("wrapper", session)
        files = ["a.py"] if files is None else list(files)
        extra = {}
        if queue_id:
            claim = work_queue.claim_next(self.repo, item_id=queue_id, files=files, owner=owner)
            extra.update(queue_id=queue_id, **admission.credentials(claim))
        lease = self.reserve(job, files=files, owner=owner, **extra)
        folder = self.repo / ".rig" / "jobs" / job
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "meta.json").write_text(json.dumps({
            "job_id": job, "worker": "grok", "role": "implement", "files": files,
            "status": status, "executor_kind": "wrapper", "kind": "wrapper",
            "reservation_id": lease["reservation_id"], "attempt_id": lease["attempt_id"],
            "ownership_established": True,
        }))
        path = admission.write_credentials(self.repo, lease)
        record = admission._read(admission._reservation_path(self.repo, lease["reservation_id"]), required=True)
        record.update(stopped=True, execution_status=status, stage="verifying", slot_held=False)
        admission._save(self.repo, record)
        return lease, path, owner

    def breakglass(self, job, credentials_path, **options):
        return admission.breakglass_close_stopped_wrapper(
            self.repo, job_id=job, credentials_path=str(credentials_path),
            confirmed_stopped=options.pop("confirmed_stopped", True),
            rationale=options.pop("rationale", "audited break-glass close of stopped wrapper"),
            **options)

    def test_breakglass_close_stopped_wrapper_success_and_idempotency(self):
        lease, path, _wrapper = self.stopped_wrapper()
        caller = _owner("parent", "breakglass-caller")
        result = self.breakglass("wrap", path, owner=caller)
        self.assertEqual(result["applied"], 1)
        recovery = result["recovery"]
        self.assertEqual(recovery["action"], "breakglass_close_stopped_wrapper")
        self.assertEqual(recovery["outcome"], "released")
        self.assertEqual(recovery["observed_status"], "fail")
        self.assertTrue(recovery["confirmed_stopped"])
        self.assertEqual(recovery["job_id"], "wrap")
        self.assertEqual(recovery["reservation_id"], lease["reservation_id"])
        self.assertEqual(recovery["attempt_id"], lease["attempt_id"])
        self.assertEqual(recovery["reason"], "audited break-glass close of stopped wrapper")
        self.assertEqual(recovery["caller"]["session_id"], "breakglass-caller")
        public = admission.get_reservation(self.repo, lease["reservation_id"])
        self.assertEqual(public["stage"], "released")
        self.assertFalse(public["slot_held"])
        self.assertEqual(admission._accounting(self.repo), [])
        audit_path = self.repo / ".rig" / "jobs" / "wrap" / "breakglass-recovery.json"
        self.assertTrue(audit_path.is_file())
        self.assertEqual(json.loads(audit_path.read_text()), recovery)
        repeat = self.breakglass("wrap", path, owner=caller, rationale="repeat must not rewrite")
        self.assertEqual(repeat["applied"], 0)
        self.assertEqual(repeat["recovery"], recovery)
        self.assertEqual(json.loads(audit_path.read_text()), recovery)
        self.reserve("replacement")

    def test_breakglass_close_stopped_wrapper_cli_and_cancelled_status(self):
        lease, path, _wrapper = self.stopped_wrapper("wrap-cli", status="cancelled")
        payload = {
            "job_id": "wrap-cli", "credentials_path": str(path),
            "confirmed_stopped": True, "rationale": "CLI audited close",
        }
        proc = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "admission.py"),
             "breakglass_close_stopped_wrapper", "--repo", str(self.repo),
             "--input-json", json.dumps(payload)],
            capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        result = json.loads(proc.stdout)
        self.assertEqual(result["applied"], 1)
        self.assertEqual(result["recovery"]["observed_status"], "cancelled")
        self.assertNotIn(lease["owner_token"], proc.stdout)
        self.assertNotIn(lease["owner_token"], proc.stderr)
        again = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "admission.py"),
             "breakglass_close_stopped_wrapper", "--repo", str(self.repo),
             "--input-json", json.dumps(payload)],
            capture_output=True, text=True)
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertEqual(json.loads(again.stdout)["applied"], 0)
        self.assertEqual(json.loads(again.stdout)["recovery"], result["recovery"])

    def test_breakglass_close_stopped_wrapper_updates_queue(self):
        item = self.queue()
        lease, path, _wrapper = self.stopped_wrapper("queued-wrap", status="cancelled", queue_id=item["id"])
        result = self.breakglass("queued-wrap", path)
        self.assertEqual(result["applied"], 1)
        self.assertEqual(work_queue.load_item(self.repo, item["id"])["status"], "cancelled")
        self.assertNotEqual(work_queue.load_item(self.repo, item["id"])["status"], "pending")
        self.assertEqual(admission._accounting(self.repo), [])

    def test_breakglass_close_stopped_wrapper_audit_is_redacted(self):
        lease, path, wrapper_owner = self.stopped_wrapper()
        result = self.breakglass("wrap", path, owner=_owner("parent", "breakglass-caller"))
        dumped = json.dumps(result)
        audit = (self.repo / ".rig" / "jobs" / "wrap" / "breakglass-recovery.json").read_text()
        public = json.dumps(admission.list_reservations(self.repo, include_released=True))
        for blob in (dumped, audit, json.dumps(result["recovery"])):
            self.assertNotIn(lease["owner_token"], blob)
            self.assertNotIn("owner_token", blob)
            self.assertNotIn(wrapper_owner["session_id"], blob)
            self.assertNotIn("owner_session", blob)
        self.assertNotIn(lease["owner_token"], public)
        self.assertNotIn("owner_token", public)
        creds = json.loads(path.read_text())
        self.assertNotIn(json.dumps(creds), dumped)
        self.assertNotIn(json.dumps(creds), audit)

    def test_ordinary_close_still_requires_original_owner_session(self):
        lease, path, owner = self.stopped_wrapper("ordinary")
        with self.assertRaisesRegex(admission.AdmissionError, "session mismatch"):
            admission.release(self.repo, **admission.credentials(lease),
                              owner=_owner("wrapper", "other-session"),
                              rationale="parent explicitly closed task")
        held = admission.get_reservation(self.repo, lease["reservation_id"])
        self.assertNotEqual(held["stage"], "released")
        closed = admission.release(self.repo, **admission.credentials(lease), owner=owner,
                                   rationale="parent explicitly closed task")
        self.assertEqual(closed["stage"], "released")
        with self.assertRaisesRegex(admission.AdmissionError, "already released"):
            self.breakglass("ordinary", path)

    def test_breakglass_close_stopped_wrapper_rejections(self):
        lease, path, owner = self.stopped_wrapper()
        args = dict(job_id="wrap", credentials_path=str(path), confirmed_stopped=True,
                    rationale="audited break-glass close of stopped wrapper")

        with self.assertRaisesRegex(admission.AdmissionError, "confirmed_stopped=true"):
            self.breakglass("wrap", path, confirmed_stopped=False)
        with self.assertRaisesRegex(admission.AdmissionError, "rationale"):
            self.breakglass("wrap", path, rationale="   ")
        with self.assertRaisesRegex(admission.AdmissionError, "raw owner tokens"):
            self.breakglass("wrap", path, owner_token=lease["owner_token"])
        with self.assertRaisesRegex(admission.AdmissionError, "raw owner tokens"):
            self.breakglass("wrap", path, reservation_id=lease["reservation_id"])

        missing = path.with_name("missing-owner-credentials.json")
        with self.assertRaisesRegex(admission.AdmissionError, "canonical owner-credentials"):
            self.breakglass("wrap", missing)
        path.unlink()
        with self.assertRaisesRegex(admission.AdmissionError, "missing"):
            self.breakglass("wrap", path)
        admission.write_credentials(self.repo, lease)
        path.write_text("{")
        os.chmod(path, 0o600)
        with self.assertRaisesRegex(admission.AdmissionError, "malformed"):
            self.breakglass("wrap", path)
        admission.write_credentials(self.repo, lease)
        os.chmod(path, 0o644)
        with self.assertRaisesRegex(admission.AdmissionError, "0600"):
            self.breakglass("wrap", path)
        os.chmod(path, 0o600)
        real = path.with_name("real-credentials.json")
        os.rename(path, real)
        path.symlink_to(real)
        os.chmod(real, 0o600)
        with self.assertRaisesRegex(admission.AdmissionError, "regular mode 0600"):
            self.breakglass("wrap", path)
        path.unlink()
        os.rename(real, path)
        os.chmod(path, 0o600)
        alias = path.parent / "alias-credentials.json"
        alias.symlink_to(path)
        with self.assertRaisesRegex(admission.AdmissionError, "canonical owner-credentials"):
            self.breakglass("wrap", alias)

        other = self.stopped_wrapper("other", session="other-wrapper", files=["b.py"])
        with self.assertRaisesRegex(admission.AdmissionError, "canonical owner-credentials"):
            self.breakglass("wrap", other[1])
        creds = json.loads(path.read_text())
        creds["reservation_id"] = other[0]["reservation_id"]
        creds["attempt_id"] = other[0]["attempt_id"]
        creds["owner_token"] = other[0]["owner_token"]
        path.write_text(json.dumps(creds))
        os.chmod(path, 0o600)
        with self.assertRaisesRegex(admission.AdmissionError, "mismatch|another job"):
            self.breakglass("wrap", path)
        admission.write_credentials(self.repo, lease)

        rec_path = admission._reservation_path(self.repo, lease["reservation_id"])
        record = admission._read(rec_path, required=True)
        record["stopped"] = False
        record["execution_status"] = "fail"
        admission._save(self.repo, record)
        with self.assertRaisesRegex(admission.AdmissionError, "active work"):
            self.breakglass("wrap", path)
        record.update(stopped=True, execution_status="ok")
        admission._save(self.repo, record)
        with self.assertRaisesRegex(admission.AdmissionError, "ok or unknown"):
            self.breakglass("wrap", path)
        record["execution_status"] = ""
        admission._save(self.repo, record)
        with self.assertRaisesRegex(admission.AdmissionError, "ok or unknown"):
            self.breakglass("wrap", path)
        record["execution_status"] = "timeout"
        admission._save(self.repo, record)
        with self.assertRaisesRegex(admission.AdmissionError, "ok or unknown"):
            self.breakglass("wrap", path)
        record.update(execution_status="fail", operation={"id": "check", "operation": "check"})
        admission._save(self.repo, record)
        with self.assertRaisesRegex(admission.AdmissionError, "active work"):
            self.breakglass("wrap", path)
        record.pop("operation")
        admission._save(self.repo, record)
        folder = self.repo / ".rig" / "jobs" / "wrap"
        (folder / "check-running.json").write_text(json.dumps({"pid": os.getpid()}))
        with self.assertRaisesRegex(admission.AdmissionError, "active work"):
            self.breakglass("wrap", path)
        (folder / "check-running.json").unlink()
        (folder / "verification.json").write_text(json.dumps({"acceptance": "accepted", "next": "complete"}))
        with self.assertRaisesRegex(admission.AdmissionError, "accepted"):
            self.breakglass("wrap", path)
        (folder / "verification.json").unlink()

        meta = json.loads((folder / "meta.json").read_text())
        meta["executor_kind"] = "parent"
        meta["kind"] = "parent"
        (folder / "meta.json").write_text(json.dumps(meta))
        with self.assertRaisesRegex(admission.AdmissionError, "non-wrapper"):
            self.breakglass("wrap", path)
        meta["executor_kind"] = "wrapper"
        meta["kind"] = "wrapper"
        (folder / "meta.json").write_text(json.dumps(meta))
        (folder / "meta.json").unlink()
        with self.assertRaisesRegex(admission.AdmissionError, "non-wrapper"):
            self.breakglass("wrap", path)
        (folder / "meta.json").write_text(json.dumps(meta))

        child_owner = _owner("native_child", "native-child")
        child = self.reserve("child", owner=child_owner, files=["c.py"], model="grok-4")
        child_folder = self.repo / ".rig" / "jobs" / "child"
        child_folder.mkdir(parents=True)
        (child_folder / "meta.json").write_text(json.dumps({
            "job_id": "child", "executor_kind": "native_child", "kind": "native_child",
            "reservation_id": child["reservation_id"], "attempt_id": child["attempt_id"],
        }))
        child_path = admission.write_credentials(self.repo, child)
        child_record = admission._read(admission._reservation_path(self.repo, child["reservation_id"]), required=True)
        child_record.update(stopped=True, execution_status="fail", stage="verifying", slot_held=False)
        admission._save(self.repo, child_record)
        with self.assertRaisesRegex(admission.AdmissionError, "non-wrapper"):
            self.breakglass("child", child_path)

        with mock.patch.dict(os.environ, {"RIG_JOB_ID": "child-job", "RIG_JOB_DIR": str(folder)}):
            with self.assertRaisesRegex(admission.AdmissionError, "child environment"):
                self.breakglass("wrap", path)

        self.assertEqual(admission.get_reservation(self.repo, lease["reservation_id"])["stage"], "verifying")
        admission.release(self.repo, **admission.credentials(lease), owner=owner,
                          rationale="parent explicitly closed task")

    def test_dead_isolated_root_with_live_orphan_is_stopped(self):
        owner = _owner("wrapper")
        lease = self.reserve(owner=owner)
        ready = self.repo / "leader-ready"
        leftover = self.repo / "orphan-ready"
        code = (
            "import os, pathlib, sys, time\n"
            "os.setsid()\n"
            "child = os.fork()\n"
            "if child == 0:\n"
            "    pathlib.Path(sys.argv[2]).write_text(str(os.getpid()))\n"
            "    while True:\n"
            "        time.sleep(0.1)\n"
            "pathlib.Path(sys.argv[1]).write_text(str(os.getpid()))\n"
            "while True:\n"
            "    time.sleep(0.1)\n"
        )
        proc = subprocess.Popen([sys.executable, "-c", code, str(ready), str(leftover)])
        leftover_pid = None
        try:
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and not (ready.exists() and leftover.exists()):
                time.sleep(0.02)
            self.assertTrue(ready.exists() and leftover.exists(), proc.poll())
            leftover_pid = int(leftover.read_text())
            identity = admission.process_identity(proc.pid)
            self.assertEqual(identity.get("pgid"), proc.pid)
            self.activate(lease, owner=owner, process=identity)
            admission.observe_process(self.repo, **admission.credentials(lease), owner=owner)
            os.kill(proc.pid, signal.SIGKILL)
            proc.wait(timeout=2)
            os.kill(leftover_pid, 0)
            result = self.finish(lease, owner=owner)
            self.assertTrue(result["stopped"], result)
            self.assertFalse(result["slot_held"], result)
            self.assertFalse(result.get("needs_reconciliation"), result)
            os.kill(leftover_pid, 0)
            orphans = (result.get("process") or {}).get("orphans") or []
            self.assertTrue(any(item.get("pid") == leftover_pid for item in orphans), orphans)
        finally:
            for pid in [leftover_pid, proc.pid]:
                if pid:
                    try:
                        os.kill(pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
            proc.wait(timeout=2)

    def test_live_in_tree_child_blocks_wrapper_stop(self):
        owner = _owner("wrapper")
        lease = self.reserve(owner=owner)
        ready = self.repo / "live-ready"
        code = (
            "import os, pathlib, sys, time\n"
            "os.setsid()\n"
            "if os.fork() == 0:\n"
            "    while True:\n"
            "        time.sleep(0.1)\n"
            "pathlib.Path(sys.argv[1]).write_text(str(os.getpid()))\n"
            "while True:\n"
            "    time.sleep(0.1)\n"
        )
        proc = subprocess.Popen([sys.executable, "-c", code, str(ready)])
        try:
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and not ready.exists():
                time.sleep(0.02)
            self.assertTrue(ready.exists(), proc.poll())
            self.activate(lease, owner=owner, process=admission.process_identity(proc.pid))
            admission.observe_process(self.repo, **admission.credentials(lease), owner=owner)
            result = self.finish(lease, owner=owner)
            self.assertFalse(result["stopped"], result)
            self.assertTrue(result["slot_held"], result)
            self.assertTrue(result.get("needs_reconciliation"), result)
        finally:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.wait(timeout=2)


    def _terminal(self, job="prior", files=None, status="ok", worker="grok", owner=None, **fields):
        owner = owner or self.owner
        files = ["a.py"] if files is None else files
        lease = self.reserve(job, files=files, worker=worker, owner=owner)
        folder = self.repo / ".rig" / "jobs" / job
        folder.mkdir(parents=True, exist_ok=True)
        payload = {
            "job_id": job, "worker": worker, "status": status, "files": files,
            "reservation_id": lease["reservation_id"], "executor_kind": "wrapper",
            "owner_session": (owner or {}).get("session_id") or "admission-tests",
        }
        payload.update({key: value for key, value in fields.items() if key != "session_id"})
        if fields.get("session_id"):
            payload["session_id"] = fields["session_id"]
            payload["resumable"] = True
        (folder / "meta.json").write_text(json.dumps(payload))
        (folder / "result.json").write_text(json.dumps({"status": status, **payload}))
        record = admission._read(admission._reservation_path(self.repo, lease["reservation_id"]), required=True)
        record.update(stopped=True, execution_status=status, stage="verifying", slot_held=False)
        for key in ("continues_job_id", "continuation_root_id", "continuation_depth"):
            if key in fields:
                record[key] = fields[key]
        admission._save(self.repo, record)
        return lease

    def test_continuation_requires_terminal_predecessor(self):
        self.reserve("live")
        with self.assertRaisesRegex(admission.AdmissionError, "not terminal"):
            self.reserve("next", continues_job_id="live")

    def test_continuation_rejects_cancelled_predecessor(self):
        self._terminal("prior-cancel", status="cancelled")
        with self.assertRaisesRegex(admission.AdmissionError, "cancelled predecessor"):
            self.reserve("next-cancel", continues_job_id="prior-cancel")

    def test_continuation_rejects_worker_owner_and_file_mismatch(self):
        self._terminal("prior-grok", worker="grok", files=["g.py"])
        with self.assertRaisesRegex(admission.AdmissionError, "worker mismatch"):
            self.reserve("next-claude", worker="claude", files=["g.py"], continues_job_id="prior-grok")
        self._terminal("prior-owner", files=["o.py"])
        with self.assertRaisesRegex(admission.AdmissionError, "owner/session"):
            self.reserve("next-owner", files=["o.py"], owner=_owner(session="other-session"),
                         continues_job_id="prior-owner")
        self._terminal("prior-files", files=["f.py"])
        with self.assertRaisesRegex(admission.AdmissionError, "equal or narrower"):
            self.reserve("next-wide", files=["f.py", "x.py"], continues_job_id="prior-files")

    def test_continuation_handoff_lineage_and_launch_failed_restore(self):
        prior = self._terminal("prior", files=["a.py", "b.py"])
        nxt = self.reserve("next", files=["a.py"], continues_job_id="prior")
        self.assertEqual(nxt["continues_job_id"], "prior")
        self.assertEqual(nxt["continuation_root_id"], "prior")
        self.assertEqual(nxt["continuation_depth"], 1)
        pred = admission.get_reservation(self.repo, prior["reservation_id"])
        self.assertTrue(pred.get("superseded"))
        self.assertEqual(pred.get("continued_by"), "next")
        self.assertEqual(pred.get("continuation_status"), "superseded")
        self.assertEqual(pred.get("stage"), "verifying")
        self.assertFalse(pred.get("slot_held"))
        with self.assertRaisesRegex(admission.AdmissionError, "files overlap"):
            self.reserve("other", files=["a.py"])
        with self.assertRaisesRegex(admission.AdmissionError, "files overlap"):
            self.reserve("other-omitted", files=["b.py"])
        admission.release(
            self.repo, **admission.credentials(nxt), owner=self.owner,
            rationale="setup failed", mode="launch_failed",
        )
        restored = admission.get_reservation(self.repo, prior["reservation_id"])
        self.assertFalse(restored.get("superseded"))
        self.assertNotEqual(restored.get("stage"), "released")
        released = admission.get_reservation(self.repo, nxt["reservation_id"])
        self.assertEqual(released["stage"], "released")

    def test_continuation_cap_and_resume_mode(self):
        self._terminal("root", files=["root.py"])
        self._terminal("deep", continues_job_id="root", continuation_root_id="root", continuation_depth=3)
        with self.assertRaisesRegex(admission.AdmissionError, r"inconsistent continuation depth"):
            self.reserve("too-deep", continues_job_id="deep")
        self.assertEqual(
            admission.continuation_resume_mode(
                {"worker": "grok", "session_id": "s1", "resumable": True}, "grok",
            ),
            "native",
        )
        self.assertEqual(
            admission.continuation_resume_mode({"worker": "grok", "resumable": True}, "grok"),
            "fresh",
        )
        self.assertEqual(
            admission.continuation_resume_mode(
                {"worker": "grok", "session_id": "s1", "resumable": True, "executor_kind": "wrapper"},
                "grok", executor_kind="native_child",
            ),
            "fresh-fallback",
        )

    def test_continuation_requires_authenticated_completion(self):
        lease = self.reserve("not-stopped")
        result = admission.finish(self.repo, **admission.credentials(lease),
                                  owner=self.owner, status="ok")
        self.assertFalse(result["stopped"])
        with self.assertRaisesRegex(admission.AdmissionError, "confirmed stopped"):
            self.reserve("unsafe", continues_job_id="not-stopped")

    def test_continuation_root_budget_counts_siblings_and_failed_launches(self):
        self._terminal("root")
        for i in range(3):
            nxt = self.reserve(f"next-{i}", continues_job_id="root")
            admission.release(self.repo, **admission.credentials(nxt), owner=self.owner,
                              rationale="failed setup", mode="launch_failed")
        with self.assertRaisesRegex(admission.AdmissionError, "cap reached"):
            self.reserve("fourth", continues_job_id="root")

    def test_continuation_retains_predecessor_resources(self):
        prior = self._terminal("root", files=["a.py", "b.py"])
        record = admission._read(admission._reservation_path(self.repo, prior["reservation_id"]))
        record["resources"] = [{"name": "test-database", "access": "write"}]
        admission._save(self.repo, record)
        self.reserve("narrow", files=["a.py"], continues_job_id="root")
        with self.assertRaisesRegex(admission.AdmissionError, "resource overlap"):
            self.reserve("other", files=["c.py"], resources=record["resources"])

    def test_continuation_rejects_cyclic_and_negative_lineage(self):
        self._terminal("cycle", continues_job_id="cycle")
        with self.assertRaisesRegex(admission.AdmissionError, "cyclic"):
            self.reserve("next-cycle", continues_job_id="cycle")
        self._terminal("negative", files=["b.py"], continuation_root_id="negative", continuation_depth=-1)
        with self.assertRaisesRegex(admission.AdmissionError, "invalid continuation depth"):
            self.reserve("next-negative", files=["b.py"], continues_job_id="negative")

if __name__ == "__main__":
    unittest.main()
