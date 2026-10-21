#!/usr/bin/env python3
"""Done today, undo, redo, hold and ship: the owner's say over what the parent applied, on real git repositories."""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import applies  # noqa: E402
import local_api  # noqa: E402
import worktree_merge  # noqa: E402
import worktrees  # noqa: E402
from test_worktree_merge import Gate  # noqa: E402
from test_worktree_jobs import git  # noqa: E402


class Applies(Gate):
    def text(self):
        return (self.repo / "a.txt").read_text()

    def test_an_applied_job_is_listed_undone_and_redone(self):
        self.start("w1", edit={"a.txt": self.lines(l1="ONE")})
        self.accept("w1")
        self.assertIn("ONE", self.text())
        rows = applies.today(self.repo)["rows"]
        self.assertEqual([(r["job_id"], r["state"], r["files"], r["added"], r["deleted"]) for r in rows], [("w1", "applied", 1, 1, 1)])
        self.assertTrue(rows[0]["commit"])
        undone = applies.undo(self.repo, "w1")
        self.assertEqual(undone["state"], "undone")
        self.assertNotIn("ONE", self.text())
        self.assertTrue(git(self.repo, "log", "-1", "--format=%s").startswith("Revert"), "a revert: history is kept")
        self.assertEqual(applies.today(self.repo)["rows"][0]["state"], "undone")
        with self.assertRaisesRegex(applies.ApplyError, "already undone"):
            applies.undo(self.repo, "w1")
        applies.redo(self.repo, "w1")
        self.assertIn("ONE", self.text())
        self.assertEqual(applies.today(self.repo)["rows"][0]["state"], "applied")

    def test_undo_refuses_a_dirty_checkout_or_another_branch(self):
        self.start("w1", edit={"a.txt": self.lines(l1="ONE")})
        self.accept("w1")
        (self.repo / "a.txt").write_text(self.text() + "local edit\n")
        with self.assertRaisesRegex(applies.ApplyError, "uncommitted changes"):
            applies.undo(self.repo, "w1")
        git(self.repo, "checkout", "-q", "--", "a.txt")
        git(self.repo, "checkout", "-q", "-b", "elsewhere")
        with self.assertRaisesRegex(applies.ApplyError, "switch to"):
            applies.undo(self.repo, "w1")
        with self.assertRaisesRegex(applies.ApplyError, "no such job"):
            applies.undo(self.repo, "../w1")

    def test_a_held_job_is_not_merged_until_released(self):
        self.start("w1", edit={"a.txt": self.lines(l1="ONE")})
        applies.hold(self.repo, "w1", True)
        self.assertEqual(applies.today(self.repo)["rows"][0]["state"], "held")
        with self.assertRaisesRegex(worktree_merge.GateRefused, "holding"):
            worktree_merge.gate(self.repo, self.jobs["w1"][0])
        applies.hold(self.repo, "w1", False)
        self.accept("w1")
        self.assertIn("ONE", self.text())
        with self.assertRaisesRegex(applies.ApplyError, "already applied"):
            applies.hold(self.repo, "w1", True)

    def test_ship_pushes_what_is_ahead(self):
        remote = Path(self.temp.name) / "remote.git"
        subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
        branch = git(self.repo, "symbolic-ref", "--short", "HEAD").strip()
        git(self.repo, "remote", "add", "origin", str(remote))
        git(self.repo, "push", "-q", "-u", "origin", branch)
        self.assertEqual(applies.ship_status(self.repo)["ahead"], [])
        with self.assertRaisesRegex(applies.ApplyError, "nothing to ship"):
            applies.ship(self.repo, "push")
        self.start("w1", edit={"a.txt": self.lines(l1="ONE")})
        self.accept("w1")
        status = applies.ship_status(self.repo)
        self.assertEqual(status["upstream"], f"origin/{branch}")
        self.assertTrue(status["ahead"])
        self.assertTrue(status["pr_branch"].startswith("rig/"))
        self.assertEqual(applies.ship(self.repo, "push")["shipped"], "push")
        self.assertEqual(applies.ship_status(self.repo)["ahead"], [])

    def test_over_the_api(self):
        self.start("w1", edit={"a.txt": self.lines(l1="ONE")})
        self.accept("w1")
        api = local_api.Api(self.repo, "t" * 64)
        call = lambda method, params=None: api.dispatch({"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {},
                                                          "token": "t" * 64})
        rows = {r["method"]: r["mutates"] for r in call("api.methods")["result"]["methods"]}
        self.assertEqual([rows[m] for m in ("applies.today", "applies.undo", "applies.redo", "applies.hold", "ship.status", "ship.run")],
                         [False, True, True, True, False, True])
        self.assertEqual(call("applies.today")["result"]["rows"][0]["job_id"], "w1")
        self.assertEqual(call("applies.undo", {"id": "w1"})["result"]["state"], "undone")
        self.assertEqual(call("applies.undo", {"id": ""})["error"]["code"], local_api.INVALID_PARAMS)
        self.assertEqual(call("ship.run", {"mode": "force"})["error"]["code"], local_api.INVALID_PARAMS)


if __name__ == "__main__":
    import unittest
    unittest.main()
