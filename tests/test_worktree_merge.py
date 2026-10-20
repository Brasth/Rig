#!/usr/bin/env python3
"""The merge gate: overlapping worktree writers run together and are merged when the parent accepts them."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import admission  # noqa: E402
import change_evidence as evidence  # noqa: E402
import verification  # noqa: E402
import worktree_merge  # noqa: E402
import worktrees  # noqa: E402
from test_worktree_jobs import git, make_repo  # noqa: E402

BASE = "one\ntwo\nthree\nfour\nfive\nsix\nseven\neight\nnine\nten\n"


class Gate(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.enterContext(patch.dict(os.environ, {"RIG_JOB_ID": "", "RIG_JOB_DIR": "", "RIG_PARENT": "codex"}))
        self.repo = make_repo(self.temp.name).resolve()
        (self.repo / "a.txt").write_text(BASE)
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "ten lines")
        self.jobs = {}

    def start(self, job_id, files=("a.txt",), edit=None, finish=True):
        """A worktree writer: reserved, activated, worktree made, edits applied and committed."""
        session = f"owner-{job_id}"
        owner = admission.caller_owner("parent", owner_session=session)
        reservation = admission.reserve(self.repo, job_id=job_id, worker="parent", role="implement",
                                        files=list(files), access="write", owner=owner)
        auth = {**admission.credentials(reservation), "owner_session": session}
        admission.activate(self.repo, job_id=job_id, worker="parent", files=list(files), access="write", **auth)
        folder = self.repo / ".rig" / "jobs" / job_id
        folder.mkdir(parents=True, exist_ok=True)
        meta = {"job_id": job_id, "worker": "parent", "role": "implement", "files": list(files), "status": "running",
                "execution_mode": "parent", "dir": str(folder), "access": "write", "ownership_established": True,
                "owner": reservation["owner"], "reservation_id": reservation["reservation_id"],
                "attempt_id": reservation["attempt_id"]}
        (folder / "meta.json").write_text(json.dumps(meta))
        record = worktrees.create_job(self.repo, folder)
        for name, text in (edit or {}).items():
            (Path(record["path"]) / name).write_text(text)
        if finish:
            admission.finish(self.repo, status="ok", completion={"kind": "parent_task", "completed": True}, **auth)
            meta["status"] = "ok"
            (folder / "meta.json").write_text(json.dumps(meta))
            worktrees.commit_job(self.repo, folder)
        self.jobs[job_id] = (folder, auth, meta)
        return folder

    def accept(self, job_id, **kwargs):
        folder, auth, meta = self.jobs[job_id]
        verification.record_requirements(self.repo, folder, [], ["Reviewed by the parent"], **auth)
        snap = evidence.snapshot(worktrees.work_root(self.repo, folder), meta["files"])["snapshot_id"]
        return verification.accept(self.repo, folder, "accept", snap, rationale="Criteria satisfied.", **auth, **kwargs)

    def lines(self, **replace):
        rows = BASE.splitlines()
        for number, text in replace.items():
            rows[int(number[1:]) - 1] = text
        return "\n".join(rows) + "\n"

    def test_two_writers_on_the_same_file_run_together_and_merge_after_acceptance(self):
        first = self.start("w1", edit={"a.txt": self.lines(l1="ONE")})
        second = self.start("w2", edit={"a.txt": self.lines(l10="TEN")})
        self.assertEqual((self.repo / "a.txt").read_text(), BASE, "nothing merges before acceptance")
        accepted = self.accept("w1")
        self.assertEqual(accepted["state"], "verified")
        self.assertTrue(accepted["merge_commit"])
        self.assertEqual((self.repo / "a.txt").read_text(), self.lines(l1="ONE"))
        self.assertFalse((self.repo / ".rig" / "worktrees" / "w1").exists(), "merged worktree is removed")
        self.assertEqual(git(self.repo, "branch", "--list", "rig/w1"), "")
        self.assertEqual(git(self.repo, "log", "-1", "--format=%P").count(" "), 1, "a real merge commit")
        self.accept("w2")
        self.assertEqual((self.repo / "a.txt").read_text(), self.lines(l1="ONE", l10="TEN"))
        self.assertEqual(admission.list_reservations(self.repo), [], "accepted jobs release their reservations")
        self.assertEqual(json.loads((first / "worktree.json").read_text())["merged"], True)
        self.assertTrue(json.loads((second / "worktree.json").read_text())["merge_commit"])

    def test_overlapping_hunks_are_refused_at_the_gate_and_change_nothing(self):
        self.start("w1", edit={"a.txt": self.lines(l5="FIVE from w1")})
        self.start("w2", edit={"a.txt": self.lines(l5="FIVE from w2")})
        self.accept("w1")
        before = git(self.repo, "rev-parse", "HEAD")
        with self.assertRaisesRegex(verification.VerificationError, r"merge_gate:conflict.*a\.txt"):
            self.accept("w2")
        self.assertEqual(git(self.repo, "rev-parse", "HEAD"), before)
        self.assertEqual(git(self.repo, "status", "--porcelain", "--", "a.txt"), "")
        folder = self.jobs["w2"][0]
        self.assertTrue(Path(json.loads((folder / "worktree.json").read_text())["path"]).is_dir(), "worktree kept")
        self.assertEqual([r["job_id"] for r in admission.list_reservations(self.repo)], ["w2"], "still reserved")
        self.assertFalse((folder / "verification.json").exists() and
                         json.loads((folder / "verification.json").read_text()).get("acceptance") == "accepted")

    def test_files_outside_the_declared_scope_are_refused(self):
        self.start("w1", edit={"a.txt": self.lines(l1="ONE"), "stray.txt": "oops\n"})
        with self.assertRaisesRegex(verification.VerificationError, r"merge_gate:out_of_scope.*stray\.txt"):
            self.accept("w1")
        self.assertFalse((self.repo / "stray.txt").exists())

    def test_uncommitted_base_changes_in_a_touched_file_are_refused(self):
        self.start("w1", edit={"a.txt": self.lines(l1="ONE")})
        (self.repo / "a.txt").write_text(self.lines(l9="local edit"))
        with self.assertRaisesRegex(verification.VerificationError, r"merge_gate:base_dirty.*a\.txt"):
            self.accept("w1")
        self.assertEqual((self.repo / "a.txt").read_text(), self.lines(l9="local edit"), "the local edit survives")
        git(self.repo, "checkout", "--", "a.txt")
        (self.repo / "other.txt").write_text("unrelated\n")
        git(self.repo, "add", "other.txt")
        with self.assertRaisesRegex(verification.VerificationError, "staged changes"):
            self.accept("w1")

    def test_a_moved_base_branch_is_refused(self):
        self.start("w1", edit={"a.txt": self.lines(l1="ONE")})
        git(self.repo, "checkout", "-q", "-b", "elsewhere")
        with self.assertRaisesRegex(verification.VerificationError, r"merge_gate:base_moved.*elsewhere"):
            self.accept("w1")

    def test_a_repeated_accept_does_not_merge_twice(self):
        self.start("w1", edit={"a.txt": self.lines(l1="ONE")})
        first = self.accept("w1")
        head = git(self.repo, "rev-parse", "HEAD")
        folder, auth, meta = self.jobs["w1"]
        snap = first["snapshot_id"]
        again = verification.accept(self.repo, folder, "accept", snap, rationale="Criteria satisfied.", **auth)
        self.assertEqual(again["merge_commit"], first["merge_commit"])
        self.assertEqual(git(self.repo, "rev-parse", "HEAD"), head)

    def test_a_crash_between_the_merge_and_the_acceptance_is_recovered_by_accepting_again(self):
        self.start("w1", edit={"a.txt": self.lines(l1="ONE")})
        folder, auth, meta = self.jobs["w1"]
        verification.record_requirements(self.repo, folder, [], ["Reviewed by the parent"], **auth)
        snap = evidence.snapshot(worktrees.work_root(self.repo, folder), meta["files"])["snapshot_id"]
        merged = worktree_merge.merge(self.repo, folder, snap)  # ...and the process dies before verification.json is written
        self.assertFalse((folder / "verification.json").exists() and
                         json.loads((folder / "verification.json").read_text()).get("acceptance") == "accepted")
        head = git(self.repo, "rev-parse", "HEAD")
        accepted = verification.accept(self.repo, folder, "accept", snap, rationale="Criteria satisfied.", **auth)
        self.assertEqual((accepted["state"], accepted["merge_commit"]), ("verified", merged["merge_commit"]))
        self.assertEqual(git(self.repo, "rev-parse", "HEAD"), head, "no second merge")
        self.assertEqual((self.repo / "a.txt").read_text(), self.lines(l1="ONE"))
        self.assertEqual(admission.list_reservations(self.repo), [])

    def test_a_job_that_changed_nothing_is_accepted_without_a_merge(self):
        self.start("w1", edit={})
        head = git(self.repo, "rev-parse", "HEAD")
        accepted = self.accept("w1")
        self.assertEqual((accepted["state"], accepted["merge_commit"]), ("verified", ""))
        self.assertEqual(git(self.repo, "rev-parse", "HEAD"), head)
        self.assertFalse((self.repo / ".rig" / "worktrees" / "w1").exists())

    def test_reject_and_review_handoff_merge_nothing(self):
        self.start("w1", edit={"a.txt": self.lines(l1="ONE")})
        folder, auth, meta = self.jobs["w1"]
        verification.record_requirements(self.repo, folder, [], ["Reviewed by the parent"], **auth)
        snap = evidence.snapshot(worktrees.work_root(self.repo, folder), meta["files"])["snapshot_id"]
        verification.accept(self.repo, folder, "accept", snap, rationale="Send to review.", next="review", **auth)
        self.assertEqual((self.repo / "a.txt").read_text(), BASE)
        self.assertTrue((self.repo / ".rig" / "worktrees" / "w1").is_dir())

    def test_gate_without_a_worktree_does_not_apply(self):
        folder = self.repo / ".rig" / "jobs" / "plain"
        folder.mkdir(parents=True)
        self.assertEqual(worktree_merge.gate(self.repo, folder), {"applies": False, "ok": True})


if __name__ == "__main__":
    unittest.main()
