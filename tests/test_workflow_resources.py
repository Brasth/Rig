#!/usr/bin/env python3
"""Opaque workflow/admission resources: compatibility, races, lifecycle."""
import json
import multiprocessing
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(ROOT / "scripts"))
import admission
import workflow_state as wf


def _owner(session="resource-tests"):
    identity = admission.process_identity(os.getpid())
    parent = admission.process_identity(os.getppid())
    return {**identity, "kind": "parent", "session_id": session, "parent_cli": "codex",
            "parent_pid": parent.get("pid"), "parent_start_id": parent.get("start_id", "")}


def _race(repo, barrier, release, results, number, resource, access):
    try:
        barrier.wait(timeout=15)
        lease = admission.reserve(
            repo, job_id=f"res-{number}", worker="grok", files=[f"file-{number}.py"],
            resources=[{"name": resource, "access": access}], owner=_owner(f"race-{number}"),
        )
        results.put((True, lease["reservation_id"]))
    except BaseException as error:
        results.put((False, str(error)))
    finally:
        release.wait(timeout=20)


class WorkflowResources(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.repo = Path(self.temp.name).resolve()
        (self.repo / ".git").mkdir()
        (self.repo / ".rig").mkdir()
        for name in ("a.py", "b.py", "file-0.py", "file-1.py"):
            (self.repo / name).write_text("x\n")
        (self.repo / ".rig" / "harness.toml").write_text(
            'parent = "codex"\n[workers]\ngrok = true\n[queue]\nmax_running = 3\n'
        )
        self.env = mock.patch.dict(os.environ, {"RIG_OWNER_SESSION": "resource-tests"})
        self.env.start()
        self.owner = _owner()

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def test_canonical_validation_and_secret_rejection(self):
        self.assertEqual(admission.canonical_resources(None), [])
        with self.assertRaisesRegex(admission.AdmissionError, "array"):
            admission.canonical_resources("db")
        self.assertEqual(admission.canonical_resources(["db"]), [{"name": "db", "access": "read"}])
        self.assertEqual(
            admission.canonical_resources([{"name": "db.primary", "access": "write"}]),
            [{"name": "db.primary", "access": "write"}],
        )
        with self.assertRaisesRegex(admission.AdmissionError, "secret"):
            admission.canonical_resources(["api_token"])
        with self.assertRaisesRegex(admission.AdmissionError, "secret"):
            admission.canonical_resources(["ghp_abcdefghijklmnopqrstuvwxyz0123"])
        with self.assertRaisesRegex(admission.AdmissionError, "opaque"):
            admission.canonical_resources(["has space"])
        self.assertIsNone(admission.resources_conflict(
            [{"name": "db", "access": "read"}], [{"name": "db", "access": "read"}]))
        self.assertEqual(admission.resources_conflict(
            [{"name": "db", "access": "read"}], [{"name": "db", "access": "write"}]), "db")

    def test_read_read_allowed_write_conflicts_disjoint_files(self):
        first = admission.reserve(self.repo, job_id="r1", worker="grok", files=["a.py"], access="read",
                                  resources=[{"name": "db.main", "access": "read"}], owner=self.owner)
        admission.reserve(self.repo, job_id="r2", worker="grok", files=["b.py"], access="read",
                          resources=[{"name": "db.main", "access": "read"}], owner=self.owner)
        with self.assertRaisesRegex(admission.AdmissionError, "resource overlap"):
            admission.reserve(self.repo, job_id="w1", worker="grok", files=["file-0.py"],
                              resources=[{"name": "db.main", "access": "write"}], owner=self.owner)
        admission.release(self.repo, **admission.credentials(first), owner=self.owner, rationale="done")

    def test_legacy_records_without_resources_remain_valid(self):
        lease = admission.reserve(self.repo, job_id="old", worker="grok", files=["a.py"], owner=self.owner)
        path = self.repo / ".rig" / "reservations" / f"{lease['reservation_id']}.json"
        record = json.loads(path.read_text())
        record.pop("resources", None)
        path.write_text(json.dumps(record) + "\n")
        rows = admission._accounting(self.repo)
        self.assertEqual(len(rows), 1)
        admission.reserve(self.repo, job_id="other", worker="grok", files=["b.py"], owner=self.owner)

    def test_legacy_job_malformed_resources_do_not_break_accounting(self):
        job = self.repo / ".rig" / "jobs" / "legacy-job"
        job.mkdir(parents=True)
        (job / "meta.json").write_text(json.dumps({
            "job_id": "legacy-job", "status": "running", "worker": "grok", "files": ["a.py"],
            "resources": "not-a-list",
        }) + "\n")
        rows = admission._accounting(self.repo)
        self.assertTrue(any(row.get("job_id") == "legacy-job" for row in rows))

    def test_stored_additively_and_held_through_lifecycle(self):
        lease = admission.reserve(
            self.repo, job_id="held", worker="grok", files=["a.py"],
            resources=[{"name": "vm.build", "access": "write"}], owner=self.owner,
            workflow_id="wf1", workflow_node_id="n1", workflow_spec_hash="abc", workflow_attempt=2,
        )
        self.assertEqual(lease["resources"], [{"name": "vm.build", "access": "write"}])
        self.assertEqual(lease["workflow_id"], "wf1")
        stored = json.loads((self.repo / ".rig" / "reservations" / f"{lease['reservation_id']}.json").read_text())
        self.assertEqual(stored["resources"], lease["resources"])
        self.assertNotIn("owner_token", admission._public(stored) or admission._public(lease))
        activated = admission.activate(
            self.repo, **admission.credentials(lease), job_id="held", worker="grok",
            files=["a.py"], access="write", owner=self.owner,
        )
        self.assertEqual(activated["resources"], lease["resources"])
        finished = admission.finish(
            self.repo, **admission.credentials(lease), status="ok", owner=self.owner,
            completion={"kind": "parent_task", "completed": True},
        )
        self.assertEqual(finished["resources"], lease["resources"])
        self.assertFalse(finished["slot_held"])
        with self.assertRaisesRegex(admission.AdmissionError, "resource overlap"):
            admission.reserve(self.repo, job_id="next", worker="grok", files=["b.py"],
                              resources=[{"name": "vm.build", "access": "write"}], owner=self.owner)

    def test_allowed_read_overlap_does_not_skip_resource_conflicts(self):
        writer = admission.reserve(
            self.repo, job_id="writer", worker="grok", files=["a.py"],
            resources=[{"name": "db.main", "access": "write"}], owner=self.owner,
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
        peer = admission.reserve(
            self.repo, job_id="peer", worker="grok", files=["b.py"],
            resources=[{"name": "cache.main", "access": "write"}], owner=self.owner,
        )
        rows = admission._accounting(self.repo)
        with self.assertRaisesRegex(admission.AdmissionError, "resource overlap"):
            admission._capacity(
                self.repo, rows, "grok", "write", ["file-0.py"],
                resources=[{"name": "cache.main", "access": "write"}],
                allow_read_overlap_reservations=[peer["reservation_id"]],
            )

    def test_workflow_writer_resource_overlap_rejected(self):
        with self.assertRaisesRegex(wf.WorkflowError, "resource"):
            wf.normalize_spec({"nodes": [
                {"id": "a", "role": "implement", "files": ["a.py"],
                 "resources": [{"name": "db.main", "access": "write"}]},
                {"id": "b", "role": "mini", "files": ["b.py"],
                 "resources": [{"name": "db.main", "access": "write"}]},
            ]})

    def test_multiprocess_write_resource_race(self):
        ctx = multiprocessing.get_context("spawn")
        barrier, release, results = ctx.Barrier(2), ctx.Event(), ctx.Queue()
        children = [
            ctx.Process(target=_race, args=(str(self.repo), barrier, release, results, n, "db.main", "write"))
            for n in range(2)
        ]
        try:
            for child in children:
                child.start()
            answers = [results.get(timeout=25) for _ in children]
            self.assertEqual(sum(answer[0] for answer in answers), 1, answers)
        finally:
            release.set()
            for child in children:
                child.join(timeout=5)
                if child.is_alive():
                    child.terminate()
                    child.join()


if __name__ == "__main__":
    unittest.main()
