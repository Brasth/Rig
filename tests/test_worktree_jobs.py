#!/usr/bin/env python3
"""Per-job worktrees: lifecycle, admission of overlapping writers, and the worker flow in run-worker.sh."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import admission  # noqa: E402
import mcp_test_support  # noqa: E402
import worktrees  # noqa: E402
from container_test_support import fake_agent  # noqa: E402

RUN = ROOT / "scripts" / "run-worker.sh"
CLEAR = ("RIG_JOB_ID", "RIG_JOB_DIR", "RIG_OWNER_TOKEN", "RIG_LIVE", "RIG_REPO", "RIG_ROLE", "RIG_CHILD_PYTHON",
         "RIG_PARENT", "RIG_OWNER_SESSION", "RIG_TIMEOUT", "RIG_ISOLATION", "RIG_WORKDIR", "RIG_ACCESS")


def git(repo, *args):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    return subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *args],
                          capture_output=True, text=True, check=True, env=env).stdout.strip()


def make_repo(base, mode="per-job", extra=""):
    repo = Path(base) / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    (repo / ".rig").mkdir()
    (repo / ".rig" / "harness.toml").write_text(
        f'parent = "grok"\n\n[project]\nenabled = true\n\n[workers]\nclaude = true\ncodex = true\n\n[worktrees]\nmode = "{mode}"\n{extra}')
    (repo / "a.txt").write_text("one\ntwo\nthree\n")
    (repo / "b.txt").write_text("b\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "init")
    return repo


class Base(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.base = Path(self.td.name)
        self.repo = make_repo(self.base)

    def tearDown(self):
        self.td.cleanup()

    def job(self, job_id):
        folder = self.repo / ".rig" / "jobs" / job_id
        folder.mkdir(parents=True, exist_ok=True)
        return folder


class Lifecycle(Base):
    def test_plan_isolates_write_jobs_only_in_per_job_mode(self):
        plan = worktrees.plan(self.repo, "j1")
        self.assertEqual((plan["isolated"], plan["branch"]), (True, "rig/j1"))
        self.assertEqual(plan["path"], str(self.repo.resolve() / ".rig" / "worktrees" / "j1"))
        review = worktrees.plan(self.repo, "j2", role="reviewer")
        self.assertEqual((review["isolated"], review["path"]), (False, str(self.repo.resolve())))
        self.assertTrue(worktrees.plan(self.repo, "j3", role="reviewer", access="write")["isolated"])
        (self.repo / ".rig" / "harness.toml").write_text('[project]\nenabled = true\n')
        self.assertFalse(worktrees.plan(self.repo, "j1")["isolated"])
        (self.repo / ".rig" / "harness.toml").write_text('[project]\nenabled = true\n\n[worktrees]\nmode = "sometimes"\n')
        self.assertIn("unknown [worktrees] mode", worktrees.plan(self.repo, "j1")["error"])

    def test_a_reviewer_reads_the_writers_worktree_until_it_is_merged(self):
        folder = self.job("w1")
        record = worktrees.create_job(self.repo, folder)
        plan = worktrees.plan(self.repo, "r1", role="reviewer", writer_job="w1")
        self.assertEqual((plan["isolated"], plan["path"]), (False, record["path"]))
        self.assertEqual(worktrees.plan(self.repo, "r2", role="reviewer", writer_job="nope")["path"], str(self.repo.resolve()))
        record.update(merged=True)
        (folder / "worktree.json").write_text(json.dumps(record))
        self.assertEqual(worktrees.plan(self.repo, "r3", role="reviewer", writer_job="w1")["path"], str(self.repo.resolve()))

    def test_create_makes_a_branch_and_worktree_and_is_idempotent(self):
        folder = self.job("j1")
        record = worktrees.create_job(self.repo, folder)
        self.assertEqual((record["branch"], record["base_branch"]), ("rig/j1", "main"))
        self.assertTrue((Path(record["path"]) / "a.txt").is_file())
        self.assertEqual(worktrees.create_job(self.repo, folder), record)
        self.assertEqual(worktrees.work_root(self.repo, folder), Path(record["path"]))
        self.assertEqual(worktrees.work_root(self.repo, self.job("other")), self.repo.resolve())
        self.assertEqual(git(self.repo, "status", "--porcelain", "--", "a.txt", "b.txt"), "")
        self.assertIn("/.rig/worktrees/", (self.repo / ".git" / "info" / "exclude").read_text())
        self.assertNotIn(".rig/worktrees", git(self.repo, "status", "--porcelain"))

    def test_refusals(self):
        folder = self.job("j1")
        (self.repo / ".rig" / "worktrees" / "j1").mkdir(parents=True)
        with self.assertRaisesRegex(worktrees.WorktreeError, "already exists"):
            worktrees.create_job(self.repo, folder)
        (self.repo / ".rig" / "worktrees" / "j1").rmdir()
        git(self.repo, "branch", "rig/j1")
        with self.assertRaisesRegex(worktrees.WorktreeError, "branch rig/j1 already exists"):
            worktrees.create_job(self.repo, folder)
        git(self.repo, "checkout", "-q", "--detach")
        with self.assertRaisesRegex(worktrees.WorktreeError, "detached HEAD"):
            worktrees.create_job(self.repo, self.job("j2"))

    def test_a_repository_without_commits_is_refused(self):
        bare = Path(self.base) / "empty"
        bare.mkdir()
        git(bare, "init", "-q", "-b", "main")
        (bare / ".rig").mkdir()
        (bare / ".rig" / "harness.toml").write_text('[project]\nenabled = true\n\n[worktrees]\nmode = "per-job"\n')
        folder = bare / ".rig" / "jobs" / "j1"
        folder.mkdir(parents=True)
        with self.assertRaisesRegex(worktrees.WorktreeError, "at least one commit"):
            worktrees.create_job(bare, folder)

    def test_commit_records_the_branch_head_and_leaves_main_alone(self):
        folder = self.job("j1")
        record = worktrees.create_job(self.repo, folder)
        (Path(record["path"]) / "a.txt").write_text("ONE\ntwo\nthree\n")
        (Path(record["path"]) / "new.txt").write_text("new\n")
        committed = worktrees.commit_job(self.repo, folder)
        self.assertNotEqual(committed["branch_head"], record["base_head"])
        self.assertEqual(git(self.repo, "log", "-1", "--format=%an %s", "rig/j1"), "rig rig job j1")
        self.assertEqual((self.repo / "a.txt").read_text(), "one\ntwo\nthree\n")
        again = worktrees.commit_job(self.repo, folder)
        self.assertEqual(again["branch_head"], committed["branch_head"], "nothing new: no second commit")

    def test_remove_refuses_live_processes_foreign_worktrees_and_keeps_unmerged_work(self):
        folder = self.job("j1")
        record = worktrees.create_job(self.repo, folder)
        holder = subprocess.Popen(["sleep", "30"], cwd=record["path"])
        try:
            if Path("/proc").is_dir():
                with self.assertRaisesRegex(worktrees.WorktreeError, "still running"):
                    worktrees.remove_job(self.repo, folder)
        finally:
            holder.kill()
            holder.wait()
        (Path(record["path"]) / "x.txt").write_text("dirty\n")
        with self.assertRaisesRegex(worktrees.WorktreeError, "uncommitted"):
            worktrees.remove_job(self.repo, folder)
        worktrees.commit_job(self.repo, folder)
        with self.assertRaisesRegex(worktrees.WorktreeError, "branch rig/j1 was kept"):
            worktrees.remove_job(self.repo, folder)
        self.assertFalse(Path(record["path"]).exists())
        self.assertTrue(git(self.repo, "rev-parse", "--verify", "rig/j1"))
        worktrees.remove_job(self.repo, folder, discard=True)
        self.assertEqual(git(self.repo, "branch", "--list", "rig/j1"), "")
        foreign = self.job("j2")
        with self.assertRaisesRegex(worktrees.WorktreeError, "only removes worktrees it created"):
            worktrees.remove_job(self.repo, foreign)

    def test_a_forged_record_cannot_point_removal_elsewhere(self):
        folder = self.job("j1")
        record = worktrees.create_job(self.repo, folder)
        record["path"] = str(self.base)
        (folder / "worktree.json").write_text(json.dumps(record))
        with self.assertRaisesRegex(worktrees.WorktreeError, "not the job's own path"):
            worktrees.remove_job(self.repo, folder)
        self.assertTrue(self.base.is_dir())


class Doctor(Base):
    def rows(self):
        import doctor
        return [c for c in doctor.build_report(self.repo)["checks"] if c["name"] == "worktrees"]

    def test_reported_only_when_the_section_is_used(self):
        self.assertEqual([r["status"] for r in self.rows()], ["ready"])
        (self.repo / ".rig" / "harness.toml").write_text("[project]\nenabled = true\n")
        self.assertEqual(self.rows(), [])
        (self.repo / ".rig" / "harness.toml").write_text('[project]\nenabled = true\n\n[worktrees]\nmode = "later"\n')
        row = self.rows()[0]
        self.assertEqual((row["status"], row["required"]), ("failed", True))

    def test_an_old_git_fails_the_check_because_the_merge_gate_needs_merge_tree(self):
        from unittest import mock
        old = subprocess.CompletedProcess([], 0, "git version 2.30.1\n", "")
        with mock.patch("subprocess.run", return_value=old):
            row = self.rows()[0]
        self.assertEqual(row["status"], "failed")
        self.assertIn("2.38", row["detail"])


class Cli(Base):
    def run_cli(self, *args):
        return subprocess.run([sys.executable, str(ROOT / "scripts" / "worktrees.py"), *args, "--repo", str(self.repo)],
                              capture_output=True, text=True)

    def test_plan_create_commit_remove_through_the_cli(self):
        self.job("j1")
        plan = json.loads(self.run_cli("plan", "--job", "j1").stdout)
        self.assertTrue(plan["isolated"])
        created = json.loads(self.run_cli("create", "--job", "j1").stdout)
        (Path(created["path"]) / "a.txt").write_text("changed\n")
        self.assertNotEqual(json.loads(self.run_cli("commit", "--job", "j1").stdout)["branch_head"], "")
        removed = self.run_cli("remove", "--job", "j1", "--discard")
        self.assertEqual(removed.returncode, 0, removed.stderr)
        self.assertFalse(Path(created["path"]).exists())

    def test_errors_exit_nonzero_with_a_reason(self):
        done = self.run_cli("remove", "--job", "never")
        self.assertEqual(done.returncode, 1)
        self.assertIn("only removes worktrees it created", done.stderr)
        (self.repo / ".rig" / "harness.toml").write_text('[project]\nenabled = true\n\n[worktrees]\nmode = "x"\n')
        self.assertEqual(self.run_cli("plan", "--job", "j2").returncode, 1)


class Admit(Base):
    def reserve(self, job_id, files, **extra):
        return admission.reserve(self.repo, job_id=job_id, worker="claude", role="worker", files=files, access="write",
                                 owner=admission.caller_owner("wrapper", owner_pid=os.getpid()), **extra)

    def test_overlapping_writers_are_admitted_and_marked_isolated(self):
        first = self.reserve("w1", ["a.txt"])
        second = self.reserve("w2", ["a.txt", "b.txt"])
        third = self.reserve("w3", [])
        self.assertEqual((first["isolation"], second["isolation"], third["isolation"]), ("worktree",) * 3)

    def test_off_mode_keeps_file_reservations(self):
        (self.repo / ".rig" / "harness.toml").write_text('[project]\nenabled = true\n\n[workers]\nclaude = true\n')
        self.reserve("w1", ["a.txt"])
        with self.assertRaisesRegex(admission.AdmissionError, "files overlap: a.txt"):
            self.reserve("w2", ["a.txt"])

    def test_a_non_isolated_writer_stays_exclusive_with_an_isolated_one(self):
        self.reserve("w1", ["a.txt"])
        (self.repo / ".rig" / "harness.toml").write_text('[project]\nenabled = true\n\n[workers]\nclaude = true\n')
        with self.assertRaisesRegex(admission.AdmissionError, "files overlap"):
            self.reserve("w2", ["a.txt"])

    def test_resources_and_capacity_still_apply(self):
        self.reserve("w1", ["a.txt"], resources=[{"name": "port-3000", "access": "write"}])
        with self.assertRaisesRegex(admission.AdmissionError, "resource overlap"):
            self.reserve("w2", ["a.txt"], resources=[{"name": "port-3000", "access": "write"}])
        (self.repo / ".rig" / "harness.toml").write_text(
            '[project]\nenabled = true\n\n[workers]\nclaude = true\n\n[queue]\nmax_running = 1\n\n[worktrees]\nmode = "per-job"\n')
        with self.assertRaisesRegex(admission.AdmissionError, "wait for an execution slot"):
            self.reserve("w3", ["b.txt"])

    def test_a_reader_of_this_checkout_does_not_conflict_with_an_isolated_writer(self):
        self.reserve("w1", ["a.txt"])
        admission.reserve(self.repo, job_id="r1", worker="claude", role="explore", files=["a.txt"], access="read",
                          owner=admission.caller_owner("wrapper", owner_pid=os.getpid()))


class FlowBase(Base):
    def setUp(self):
        super().setUp()
        self.bin = self.base / "bin"
        self.home = self.base / "home"
        mcp_test_support.seed_installed_mcp(self.home)
        self.brief = self.base / "brief.md"
        self.brief.write_text("Edit a.txt.\n")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "rig config", "--allow-empty")

    def env(self, **extra):
        env = os.environ.copy()
        for key in CLEAR:
            env.pop(key, None)
        env.update({"PATH": mcp_test_support.stub_path(self.bin), "HOME": str(self.home), "RIG_HOME": str(ROOT),
                    "RIG_PARENT": "grok", "RIG_SKIP_MODEL_CATALOG": "1", "RIG_SKIP_UPDATE_CHECK": "1",
                    "RIG_ROLE": "worker", "RIG_OWNER_SESSION": "wt-parent", "RIG_JOB_FILES_JSON": '["a.txt"]'})
        env.update(extra)
        return env

    def run_worker(self, worker, job, **extra):
        folder = self.job(job)
        (folder / "brief.md").write_text("Edit a.txt.\n")
        return subprocess.run([str(RUN), worker, job, str(folder / "brief.md")], cwd=self.repo,
                              env=self.env(**extra), text=True, capture_output=True, timeout=120)


class Flow(FlowBase):
    def test_dry_run_points_the_worker_at_the_worktree_and_creates_nothing(self):
        fake_agent(self.bin, "codex", "")
        proc = self.run_worker("codex", "d1")
        out = proc.stdout + proc.stderr
        self.assertEqual(proc.returncode, 0, out)
        work = self.repo.resolve() / ".rig" / "worktrees" / "d1"
        self.assertIn(f"-C {work}", out.split("would run:", 1)[1])
        self.assertFalse(work.exists())
        self.assertFalse((self.job("d1") / "worktree.json").exists())

    def test_off_mode_dry_run_is_unchanged(self):
        (self.repo / ".rig" / "harness.toml").write_text(
            'parent = "grok"\n\n[project]\nenabled = true\n\n[workers]\nclaude = true\ncodex = true\n')
        fake_agent(self.bin, "codex", "")
        out = self.run_worker("codex", "d2")
        self.assertIn(f"-C {self.repo.resolve()}", out.stdout.split("would run:", 1)[1])

    def test_a_live_job_edits_its_worktree_and_leaves_the_base_checkout_alone(self):
        fake_agent(self.bin, "claude", mcp_test_support.inbox_handshake_prelude(ROOT)
                   + "import os\n_rig_inbox_handshake()\n"
                   "open('a.txt', 'a').write('worker line\\n')\n"
                   "open(os.path.join(os.environ['RIG_JOB_DIR'], 'cwd.txt'), 'w').write(os.getcwd() + ' ' + os.environ['RIG_REPO'])\n"
                   "print('{\"result\": \"edited\"}')\n")
        proc = self.run_worker("claude", "l1", RIG_LIVE="1", RIG_TIMEOUT="60")
        out = proc.stdout + proc.stderr
        self.assertEqual(proc.returncode, 0, out)
        folder = self.job("l1")
        record = json.loads((folder / "worktree.json").read_text())
        self.assertEqual((folder / "cwd.txt").read_text(), f"{record['path']} {self.repo.resolve()}")
        self.assertEqual((self.repo / "a.txt").read_text(), "one\ntwo\nthree\n")
        self.assertEqual(git(self.repo, "show", "rig/l1:a.txt"), "one\ntwo\nthree\nworker line")
        self.assertNotEqual(record["branch_head"], "")
        evidence = json.loads((folder / "change-evidence.json").read_text())
        self.assertEqual(evidence["scoped_changed_paths"], ["a.txt"])
        self.assertEqual(evidence["out_of_scope_observations"], [])
        self.assertEqual(json.loads((folder / "result.json").read_text())["status"], "ok", out)

    def test_two_overlapping_writers_run_at_the_same_time(self):
        marker = self.base / "second-started"
        fake_agent(self.bin, "claude", mcp_test_support.inbox_handshake_prelude(ROOT)
                   + "import os, time, pathlib\n_rig_inbox_handshake()\n"
                   f"marker = pathlib.Path({str(marker)!r})\n"
                   "me = os.environ['RIG_JOB_ID']\n"
                   "pathlib.Path(os.environ['RIG_JOB_DIR'], 'up').write_text('1')\n"
                   "other = pathlib.Path(os.environ['RIG_REPO'], '.rig', 'jobs', 'p2' if me == 'p1' else 'p1', 'up')\n"
                   "deadline = time.time() + 40\n"
                   "while not other.exists() and time.time() < deadline:\n    time.sleep(0.1)\n"
                   "assert other.exists(), 'the other writer never ran beside this one'\n"
                   "open('a.txt', 'a').write(me + '\\n')\n")
        procs = [subprocess.Popen([str(RUN), "claude", job, str(self.job(job) / "brief.md")], cwd=self.repo,
                                  env=self.env(RIG_LIVE="1", RIG_TIMEOUT="80"), text=True,
                                  stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
                 for job in ("p1", "p2") if (self.job(job) / "brief.md").write_text("x\n") or True]
        outs = [p.communicate(timeout=120)[0] for p in procs]
        for proc, out in zip(procs, outs):
            self.assertEqual(proc.returncode, 0, out)
        for job in ("p1", "p2"):
            self.assertTrue(git(self.repo, "show", f"rig/{job}:a.txt").endswith(job))

    def test_both_overlapping_writers_merge_after_acceptance(self):
        """The Phase 3 exit check end to end: run together through run-worker.sh, accepted, merged."""
        import change_evidence as evidence
        import verification
        fake_agent(self.bin, "claude", mcp_test_support.inbox_handshake_prelude(ROOT)
                   + "import os, time, pathlib\n_rig_inbox_handshake()\n"
                   "me = os.environ['RIG_JOB_ID']\n"
                   "pathlib.Path(os.environ['RIG_JOB_DIR'], 'up').write_text('1')\n"
                   "other = pathlib.Path(os.environ['RIG_REPO'], '.rig', 'jobs', 'm2' if me == 'm1' else 'm1', 'up')\n"
                   "deadline = time.time() + 40\n"
                   "while not other.exists() and time.time() < deadline:\n    time.sleep(0.1)\n"
                   "rows = open('a.txt').read().splitlines()\n"
                   "rows[0 if me == 'm1' else 2] = me.upper()\n"
                   "open('a.txt', 'w').write('\\n'.join(rows) + '\\n')\n")
        for job in ("m1", "m2"):
            (self.job(job) / "brief.md").write_text("x\n")
        procs = [subprocess.Popen([str(RUN), "claude", job, str(self.job(job) / "brief.md")], cwd=self.repo,
                                  env=self.env(RIG_LIVE="1", RIG_TIMEOUT="80"), text=True,
                                  stdout=subprocess.PIPE, stderr=subprocess.STDOUT) for job in ("m1", "m2")]
        for proc in procs:
            out = proc.communicate(timeout=120)[0]
            self.assertEqual(proc.returncode, 0, out)
        self.assertEqual((self.repo / "a.txt").read_text(), "one\ntwo\nthree\n", "nothing merges before acceptance")
        os.environ["RIG_PARENT"] = "grok"
        for job in ("m1", "m2"):
            folder = self.job(job)
            saved = json.loads((folder / "owner-credentials.json").read_text())
            auth = {**admission.credentials(saved), "owner_session": "wt-parent"}
            verification.record_requirements(self.repo, folder, [], ["Parent reviewed it"], **auth)
            snap = evidence.job_snapshot(self.repo, folder, ["a.txt"])["snapshot_id"]
            accepted = verification.accept(self.repo, folder, "accept", snap, rationale="Reviewed.", **auth)
            self.assertEqual(accepted["state"], "verified", job)
            self.assertTrue(accepted["merge_commit"], job)
        self.assertEqual((self.repo / "a.txt").read_text(), "M1\ntwo\nM2\n")
        self.assertEqual(git(self.repo, "branch", "--list", "rig/m1", "rig/m2"), "")
        self.assertFalse((self.repo / ".rig" / "worktrees" / "m1").exists())


class PerJobContainer(FlowBase):
    """[worktrees] container = "per-job": dc-cli keys the container by the job's worktree."""

    def setUp(self):
        super().setUp()
        from container_test_support import install_fake_dc
        self.dc = install_fake_dc(self.base / "dcbin")
        self.dclog = self.base / "dc.log"
        (self.repo / ".rig" / "harness.toml").write_text(
            'parent = "grok"\n\n[project]\nenabled = true\n\n[workers]\nclaude = true\n\n'
            '[sandbox]\nprovider = "devcontainer"\n\n[sandbox.workers]\nclaude = "mount-ro"\n\n'
            '[worktrees]\nmode = "per-job"\ncontainer = "per-job"\n')
        (self.home / ".claude").mkdir(exist_ok=True)

    def env(self, **extra):
        return super().env(RIG_DC_BIN=str(self.dc), FAKE_DC_LOG=str(self.dclog), **extra)

    def calls(self):
        return self.dclog.read_text().splitlines() if self.dclog.exists() else []

    def test_dry_run_targets_the_worktree_as_the_dc_workspace_and_starts_nothing(self):
        proc = self.run_worker("claude", "c1")
        out = proc.stdout + proc.stderr
        self.assertEqual(proc.returncode, 0, out)
        work = self.repo.resolve() / ".rig" / "worktrees" / "c1"
        self.assertIn(f"exec --no-start {work} -- sh -c", out.split("would run:", 1)[1])
        self.assertEqual([c for c in self.calls() if c.startswith(("up", "exec"))], [])

    def test_a_live_job_starts_its_own_container_and_runs_in_the_worktree(self):
        fake_agent(self.bin, "claude", mcp_test_support.inbox_handshake_prelude(ROOT)
                   + "import os\n_rig_inbox_handshake()\nopen(os.path.join(os.environ['RIG_JOB_DIR'], 'cwd.txt'), 'w').write(os.getcwd())\n"
                   "print('{\"result\": \"ok\"}')\n")
        proc = self.run_worker("claude", "c2", RIG_LIVE="1", RIG_TIMEOUT="60")
        out = proc.stdout + proc.stderr
        self.assertEqual(proc.returncode, 0, out)
        work = self.repo.resolve() / ".rig" / "worktrees" / "c2"
        calls = self.calls()
        self.assertIn(f"up {work}", calls)
        self.assertTrue(all(f"--no-start {work} " in c for c in calls if c.startswith("exec")), calls)
        self.assertEqual((self.job("c2") / "cwd.txt").read_text(), str(work))
        self.assertIn(f"up {work}", calls[0] if calls[0].startswith("up") else " ".join(calls[:3]), "the container starts before the probe")

    def test_a_container_that_cannot_start_refuses_the_launch(self):
        marker = self.base / "agent-ran"
        fake_agent(self.bin, "claude", f"open({str(marker)!r}, 'w').write('x')\n")
        proc = self.run_worker("claude", "c3", RIG_LIVE="1", RIG_TIMEOUT="30", FAKE_DC_UP_FAIL="1")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("dc up", proc.stdout + proc.stderr)
        self.assertFalse(marker.exists())


if __name__ == "__main__":
    unittest.main()
