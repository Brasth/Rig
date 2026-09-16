#!/usr/bin/env python3
"""Locked unique-tmp job metadata: races, fail-closed JSON, wrapper handshake retention."""
from __future__ import annotations

import inspect
import json
import multiprocessing
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import admission  # noqa: E402
import child_mcp  # noqa: E402
import job_metadata  # noqa: E402
import jobs  # noqa: E402
import mcp_test_support  # noqa: E402


def _legacy_shared_tmp_replace(job_dir: str, fields: dict, barrier, errors: list) -> None:
    path = Path(job_dir) / "meta.json"
    obj = json.loads(path.read_text())
    obj.update(fields)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2) + "\n")
    barrier.wait(timeout=5)
    try:
        tmp.replace(path)
    except FileNotFoundError as error:
        errors.append(repr(error))


def _process_patch(job_dir: str, scripts: str, key: str, value, started, go, results) -> None:
    sys.path.insert(0, scripts)
    import jobs as jobs_mod

    started.set()
    if not go.wait(timeout=10):
        results.put(("err", key, "go timeout"))
        return
    try:
        meta = jobs_mod.patch_meta(Path(job_dir), **{key: value})
        results.put(("ok", key, meta.get(key)))
    except Exception as error:
        results.put(("err", key, str(error)))


class JobMetadataHelper(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        self.repo = Path(self.td.name).resolve()
        (self.repo / ".git").mkdir()
        (self.repo / ".rig" / "jobs").mkdir(parents=True)
        (self.repo / ".rig" / "harness.toml").write_text('parent = "codex"\n[workers]\ngrok = true\n')
        self.job_id = "meta-job"
        self.job_dir = self.repo / ".rig" / "jobs" / self.job_id

    def _seed(self, **fields):
        self.job_dir.mkdir(parents=True, exist_ok=True)
        base = {
            "job_id": self.job_id,
            "worker": "codex",
            "role": "implement",
            "status": "running",
            "doing": "starting",
        }
        base.update(fields)
        (self.job_dir / "meta.json").write_text(json.dumps(base, indent=2) + "\n")
        return base

    def test_job_repo_standard_and_rejects_root_or_legacy(self):
        self.assertEqual(job_metadata.job_repo(self.job_dir), self.repo)
        self.assertIsNone(job_metadata.job_repo(Path("/.rig/jobs/rooted")))
        orphan = Path(self.td.name) / "orphan-job"
        self.assertIsNone(job_metadata.job_repo(orphan))
        bare = Path(self.td.name) / "bare" / ".rig" / "jobs" / "x"
        bare.mkdir(parents=True)
        self.assertIsNone(job_metadata.job_repo(bare))

    def test_legacy_dir_does_not_lock_filesystem_root(self):
        orphan = Path(self.td.name) / "orphan-job"
        jobs.patch_meta(orphan, doing="legacy")
        self.assertIsNone(job_metadata.job_repo(orphan))
        self.assertFalse(Path("/.rig/queue/.lock").exists())
        self.assertFalse((Path("/") / ".rig" / "queue" / ".lock").exists())
        self.assertEqual(json.loads((orphan / "meta.json").read_text())["doing"], "legacy")

    def test_shared_tmp_barrier_is_filenotfound_unique_tmp_is_not(self):
        self._seed()
        errors = []
        barrier = threading.Barrier(2)
        threads = [
            threading.Thread(
                target=_legacy_shared_tmp_replace,
                args=(str(self.job_dir), {"doing": "a"}, barrier, errors),
            ),
            threading.Thread(
                target=_legacy_shared_tmp_replace,
                args=(str(self.job_dir), {"child_mcp_status": "connected"}, barrier, errors),
            ),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertTrue(errors, "shared meta.json.tmp replace must FileNotFoundError")

        self._seed(doing="starting")
        ready = threading.Barrier(2)

        def patch_doing():
            ready.wait(timeout=5)
            jobs.patch_meta(self.job_dir, doing="editing a.py")

        def patch_handshake():
            ready.wait(timeout=5)
            jobs.patch_meta(
                self.job_dir,
                child_mcp_status=child_mcp.CONNECTED,
                child_mcp_connected_at="2026-09-16T00:00:00Z",
                child_mcp_protocol=child_mcp.PROTOCOL,
            )

        workers = [threading.Thread(target=patch_doing), threading.Thread(target=patch_handshake)]
        for thread in workers:
            thread.start()
        for thread in workers:
            thread.join()
        meta = json.loads((self.job_dir / "meta.json").read_text())
        self.assertEqual(meta["doing"], "editing a.py")
        self.assertEqual(meta["child_mcp_status"], child_mcp.CONNECTED)
        self.assertEqual(int(meta["child_mcp_protocol"]), child_mcp.PROTOCOL)
        self.assertEqual(meta["job_id"], self.job_id)
        self.assertEqual(meta["worker"], "codex")

    def test_process_concurrency_retains_handshake_and_doing(self):
        self._seed()
        ctx = multiprocessing.get_context("spawn")
        started_a, started_b = ctx.Event(), ctx.Event()
        go = ctx.Event()
        results = ctx.Queue()
        proc_a = ctx.Process(
            target=_process_patch,
            args=(str(self.job_dir), str(ROOT / "scripts"), "doing", "editing a.py", started_a, go, results),
        )
        proc_b = ctx.Process(
            target=_process_patch,
            args=(
                str(self.job_dir),
                str(ROOT / "scripts"),
                "child_mcp_status",
                child_mcp.CONNECTED,
                started_b,
                go,
                results,
            ),
        )
        proc_a.start()
        proc_b.start()
        self.assertTrue(started_a.wait(timeout=10))
        self.assertTrue(started_b.wait(timeout=10))
        go.set()
        proc_a.join(timeout=15)
        proc_b.join(timeout=15)
        self.assertEqual(proc_a.exitcode, 0, proc_a.exitcode)
        self.assertEqual(proc_b.exitcode, 0, proc_b.exitcode)
        got = [results.get(timeout=2), results.get(timeout=2)]
        self.assertTrue(all(item[0] == "ok" for item in got), got)
        meta = json.loads((self.job_dir / "meta.json").read_text())
        self.assertEqual(meta["doing"], "editing a.py")
        self.assertEqual(meta["child_mcp_status"], child_mcp.CONNECTED)
        self.assertEqual(meta["job_id"], self.job_id)

    def test_startup_finish_keeps_handshake_workflow_resources(self):
        jobs.write_job_files(
            self.job_dir, self.job_id, "codex", "implement", "running", 0,
            "2026-09-16T00:00:00Z", "", "start",
            kind="wrapper", executor_kind="wrapper", execution_mode="live",
            files=["scoped.py"], capture_evidence=False,
            workflow_id="wf-meta", workflow_node_id="n1", workflow_spec_hash="abc",
            workflow_attempt=2, resources=[{"name": "db.main", "access": "read"}],
        )
        child_mcp.record_handshake(self.job_dir)
        jobs.patch_meta(self.job_dir, doing="editing scoped.py")
        jobs.write_job_files(
            self.job_dir, self.job_id, "codex", "implement", "ok", 0,
            "2026-09-16T00:00:00Z", "2026-09-16T00:00:05Z", "done",
            kind="wrapper", executor_kind="wrapper", execution_mode="live",
            files=["scoped.py"], capture_evidence=False,
        )
        meta = json.loads((self.job_dir / "meta.json").read_text())
        self.assertEqual(meta["status"], "ok")
        self.assertEqual(meta["child_mcp_status"], child_mcp.CONNECTED)
        self.assertEqual(int(meta["child_mcp_protocol"]), child_mcp.PROTOCOL)
        self.assertEqual(meta["doing"], "editing scoped.py")
        self.assertEqual(meta["workflow_id"], "wf-meta")
        self.assertEqual(meta["resources"], [{"name": "db.main", "access": "read"}])
        jobs.write_job_files(
            self.job_dir, self.job_id, "codex", "implement", "running", 0,
            "2026-09-16T00:01:00Z", "", "restart",
            kind="wrapper", executor_kind="wrapper", execution_mode="live",
            files=["scoped.py"], capture_evidence=False,
        )
        again = json.loads((self.job_dir / "meta.json").read_text())
        self.assertEqual(again["child_mcp_status"], child_mcp.CONNECTED)
        self.assertEqual(again["workflow_id"], "wf-meta")
        self.assertEqual(again["resources"], [{"name": "db.main", "access": "read"}])
        self.assertEqual(again["doing"], "editing scoped.py")

    def test_invalid_metadata_fails_closed(self):
        self.job_dir.mkdir(parents=True)
        (self.job_dir / "meta.json").write_text("{not-json")
        with self.assertRaises(job_metadata.MetadataError):
            jobs.patch_meta(self.job_dir, doing="nope")
        self.assertEqual((self.job_dir / "meta.json").read_text(), "{not-json")
        self.assertEqual(jobs._read_meta_dict(self.job_dir), {})
        with self.assertRaises(job_metadata.MetadataError):
            jobs.write_job_files(
                self.job_dir, self.job_id, "codex", "implement", "ok", 0,
                "2026-09-16T00:00:00Z", "2026-09-16T00:00:01Z", "done",
                capture_evidence=False,
            )
        self.assertEqual((self.job_dir / "meta.json").read_text(), "{not-json")

    def test_missing_meta_may_create_and_lock_is_reentrant(self):
        jobs.patch_meta(self.job_dir, doing="created")
        meta = json.loads((self.job_dir / "meta.json").read_text())
        self.assertEqual(meta["job_id"], self.job_id)
        self.assertEqual(meta["doing"], "created")
        with admission.transaction(self.repo):
            jobs.patch_meta(self.job_dir, worker="codex")
            with admission.transaction(self.repo):
                jobs.patch_meta(self.job_dir, status="running")
                child_mcp.record_handshake(self.job_dir)
        held = json.loads((self.job_dir / "meta.json").read_text())
        self.assertEqual(held["worker"], "codex")
        self.assertEqual(held["status"], "running")
        self.assertEqual(held["child_mcp_status"], child_mcp.CONNECTED)
        self.assertEqual(held["doing"], "created")

    def test_legacy_write_job_files_fixture_still_valid(self):
        legacy = Path(self.td.name) / "elapsed-90"
        jobs.write_job_files(
            legacy, "elapsed-90", "grok", "implement", "ok", 0,
            "2026-09-10T07:00:00Z", "2026-09-10T07:01:30Z", "done",
            capture_evidence=False,
        )
        meta = json.loads((legacy / "meta.json").read_text())
        self.assertEqual(meta["elapsed_s"], 90)
        self.assertIsNone(job_metadata.job_repo(legacy))
        self.assertFalse((Path(self.td.name) / ".rig" / "queue" / ".lock").exists())

    def test_write_job_files_signature_unchanged(self):
        params = inspect.signature(jobs.write_job_files).parameters
        for name in (
            "job_dir", "job_id", "worker", "role", "status", "exit_code",
            "capture_evidence", "token_usage", "resources", "workflow_id",
        ):
            self.assertIn(name, params)

    def test_persist_token_usage_keeps_handshake(self):
        usage = {"input": 3, "output": 1, "reasoning": 0, "cached_input": 0, "total": 4}
        jobs.write_job_files(
            self.job_dir, self.job_id, "grok", "implement", "ok", 0,
            "2026-09-16T00:00:00Z", "2026-09-16T00:00:01Z", "done",
            kind="wrapper", executor_kind="wrapper", capture_evidence=False,
        )
        child_mcp.record_handshake(self.job_dir)
        (self.job_dir / "stdout.log").write_text(json.dumps({"type": "result", "usage": usage}) + "\n")
        jobs.persist_token_usage(self.job_dir)
        meta = json.loads((self.job_dir / "meta.json").read_text())
        result = json.loads((self.job_dir / "result.json").read_text())
        self.assertEqual(meta["token_usage"], usage)
        self.assertEqual(result["token_usage"], usage)
        self.assertEqual(meta["child_mcp_status"], child_mcp.CONNECTED)


class RunWorkerMetadataIntegration(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        self.root = Path(self.td.name)
        self.repo = self.root / "repo"
        self.home = self.root / "home"
        self.bins = self.root / "bins"
        self.repo.mkdir()
        self.home.mkdir()
        self.bins.mkdir()
        mcp_test_support.seed_installed_mcp(self.home)
        for args in (
            ["init", "-q"],
            ["config", "user.email", "test@example.invalid"],
            ["config", "user.name", "Rig Test"],
        ):
            subprocess.run(["git", "-C", str(self.repo), *args], check=True, capture_output=True)
        (self.repo / ".gitignore").write_text(".rig/\n")
        (self.repo / "scoped.py").write_text("original\n")
        subprocess.run(["git", "-C", str(self.repo), "add", "."], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(self.repo), "commit", "-qm", "fixture"], check=True, capture_output=True)
        (self.repo / ".rig").mkdir()
        (self.repo / ".rig" / "harness.toml").write_text(
            'parent = "grok"\n[workers]\ncodex = true\ngrok = false\n'
        )
        self.job_id = "meta-wrapper"
        self.job_dir = self.repo / ".rig" / "jobs" / self.job_id
        self.job_dir.mkdir(parents=True)
        self.brief = self.job_dir / "brief.md"
        self.brief.write_text("You are a worker, not the orchestrator.\nEdit scoped.py.\n")
        script = (
            f"#!{sys.executable}\n"
            "import json, os, pathlib, sys, time\n"
            f"sys.path.insert(0, {str(ROOT / 'scripts')!r})\n"
            "import jobs\n"
            + mcp_test_support.inbox_handshake_prelude(ROOT)
            + "job_dir = pathlib.Path(os.environ['RIG_JOB_DIR'])\n"
            "jobs.patch_meta(job_dir, workflow_id='wf-meta', workflow_node_id='n1',\n"
            "                workflow_spec_hash='abc', workflow_attempt=1,\n"
            "                resources=[{'name': 'db.main', 'access': 'read'}])\n"
            "jobs.set_doing(job_dir, 'editing scoped.py')\n"
            "print(json.dumps({'type': 'text', 'data': 'working scoped.py'}))\n"
            "time.sleep(0.05)\n"
        )
        worker = self.bins / "codex"
        worker.write_text(script)
        worker.chmod(0o755)

    def test_run_worker_retains_connected_scope_and_workflow(self):
        env = os.environ.copy()
        for key in (
            "RIG_JOB_ID", "RIG_JOB_DIR", "RIG_REPO", "RIG_ACCESS", "RIG_QUEUE_ID",
            "RIG_RESERVATION_ID", "RIG_ATTEMPT_ID", "RIG_OWNER_TOKEN", "RIG_OWNER_SESSION",
            "RIG_JOB_FILES", "RIG_WRITER_JOB_ID", "RIG_WRITER_SNAPSHOT_ID",
        ):
            env.pop(key, None)
        env.update({
            "RIG_HOME": str(ROOT),
            "PATH": mcp_test_support.stub_path(self.bins),
            "HOME": str(self.home),
            "RIG_PARENT": "grok",
            "RIG_LIVE": "1",
            "RIG_SKIP_MODEL_CATALOG": "1",
            "RIG_ROLE": "implement",
            "RIG_MODEL": "gpt-5.6-luna",
            "RIG_EFFORT": "low",
            "RIG_JOB_FILES_JSON": json.dumps(["scoped.py"]),
            "RIG_WORKFLOW_ID": "wf-meta",
            "RIG_WORKFLOW_NODE_ID": "n1",
            "RIG_WORKFLOW_SPEC_HASH": "abc",
            "RIG_WORKFLOW_ATTEMPT": "1",
            "RIG_JOB_RESOURCES_JSON": json.dumps([{"name": "db.main", "access": "read"}]),
        })
        proc = subprocess.run(
            [str(ROOT / "scripts" / "run-worker.sh"), "codex", self.job_id, str(self.brief)],
            cwd=self.repo,
            env=env,
            text=True,
            capture_output=True,
            timeout=45,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        meta = json.loads((self.job_dir / "meta.json").read_text())
        result = json.loads((self.job_dir / "result.json").read_text())
        self.assertEqual(meta.get("child_mcp_status"), child_mcp.CONNECTED)
        self.assertEqual(int(meta.get("child_mcp_protocol") or 0), child_mcp.PROTOCOL)
        self.assertTrue(child_mcp.handshake_connected(self.job_dir))
        self.assertEqual(meta.get("doing"), "editing scoped.py")
        self.assertEqual(meta.get("files"), ["scoped.py"])
        self.assertEqual(result.get("files"), ["scoped.py"])
        self.assertEqual(meta.get("workflow_id"), "wf-meta")
        self.assertEqual(meta.get("resources"), [{"name": "db.main", "access": "read"}])
        self.assertEqual(result.get("workflow_id"), "wf-meta")
        self.assertEqual(result.get("status"), "ok")
        self.assertNotEqual(meta.get("child_mcp_status"), child_mcp.UNKNOWN)
        self.assertNotEqual(child_mcp.require_success(self.job_dir), child_mcp.MISSING_REASON)
