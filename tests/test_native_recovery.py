#!/usr/bin/env python3
"""Replacement-parent cancelled native recovery."""
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

if not hasattr(unittest.TestCase, "enterContext"):
    def _enter_context(self, cm):
        self.addCleanup(cm.__exit__, None, None, None)
        return cm.__enter__()
    unittest.TestCase.enterContext = _enter_context

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import admission
import cancellation
import jobs
import native_recovery
import rig_mcp
import work_queue


def _iso(stamp):
    return datetime.fromtimestamp(stamp, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _close_jsonl(parent, child, created, *, output="success", resume=False):
    iso = _iso(created)
    rows = [
        {"timestamp": iso, "type": "session_meta", "payload": {"id": parent}},
        {"timestamp": iso, "type": "response_item", "payload": {
            "type": "function_call", "name": "close_agent", "namespace": "multi_agent_v1",
            "call_id": "call-close", "arguments": json.dumps({"target": child}),
        }},
    ]
    if output == "success":
        rows.append({"timestamp": iso, "type": "response_item", "payload": {
            "type": "function_call_output", "call_id": "call-close",
            "output": json.dumps({"previous_status": "running"}),
        }})
    elif output == "error":
        rows.append({"timestamp": iso, "type": "response_item", "payload": {
            "type": "function_call_output", "call_id": "call-close",
            "output": "agent with id %s is closed" % child,
        }})
    if resume:
        later = _iso(created + 8)
        rows.append({"timestamp": later, "type": "response_item", "payload": {
            "type": "function_call", "name": "resume_agent", "namespace": "multi_agent_v1",
            "call_id": "call-resume", "arguments": json.dumps({"id": child}),
        }})
        rows.append({"timestamp": later, "type": "response_item", "payload": {
            "type": "function_call_output", "call_id": "call-resume",
            "output": json.dumps({"status": "running"}),
        }})
    return rows


def _write_registry(home, parent, child, created, status="closed", extra_child=None, cwd="/tmp/repo",
                    output="success", resume=False):
    home.mkdir(parents=True, exist_ok=True)
    db = home / "state_5.sqlite"
    if db.exists():
        db.unlink()
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE threads (id TEXT, rollout_path TEXT, cwd TEXT, created_at INTEGER, updated_at INTEGER, agent_path TEXT)")
    conn.execute("CREATE TABLE thread_spawn_edges (parent_thread_id TEXT, child_thread_id TEXT, status TEXT)")
    parent_rollout = "sessions/parent.jsonl"
    rows = [
        (parent, parent_rollout, cwd, created - 1, created, ""),
        (child, "sessions/child.jsonl", cwd, created, created + 1, "/root/worker"),
    ]
    edges = [(parent, child, status)]
    if extra_child:
        rows.append(extra_child[0])
        edges.append(extra_child[1])
    conn.executemany("INSERT INTO threads VALUES (?, ?, ?, ?, ?, ?)", rows)
    conn.executemany("INSERT INTO thread_spawn_edges VALUES (?, ?, ?)", edges)
    conn.commit()
    conn.close()
    path = home / parent_rollout
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in _close_jsonl(parent, child, created, output=output, resume=resume)))


class NativeRecovery(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name) / "repo"
        self.home = Path(self.temp.name) / "codex-home"
        self.repo.mkdir()
        (self.repo / ".rig").mkdir()
        (self.repo / ".rig" / "harness.toml").write_text('parent = "codex"\n[workers]\ncodex = false\n')
        (self.repo / ".gitignore").write_text(".rig/\n")
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True, capture_output=True)
        (self.repo / "subject.txt").write_text("before\n")
        self.thread = "parent-thread-recovery"
        self.agent = "child-agent-recovery"
        self.enterContext(patch.dict(os.environ, {
            "RIG_PARENT": "codex", "RIG_THREAD": self.thread, "RIG_HOME": str(ROOT),
            "RIG_INSTALL_TRANSACTION": "1", "RIG_JOB_ID": "", "RIG_JOB_DIR": "",
            "RIG_SKIP_MODEL_CATALOG": "1", "RIG_SKIP_UPDATE_CHECK": "1",
            "RIG_OWNER_SESSION": self.thread, "RIG_OWNER_TOKEN": "",
            "RIG_RESERVATION_ID": "", "RIG_ATTEMPT_ID": "", "RIG_JOB_FILES_JSON": "",
            "CODEX_HOME": str(self.home), "CODEX_SQLITE_HOME": "",
        }))
        self.dead = subprocess.Popen([sys.executable, "-c", "pass"])
        self.dead_ident = admission.process_identity(self.dead.pid)
        self.dead.wait(timeout=5)

    def call(self, name, **args):
        return rig_mcp.call_tool(name, {"repo": str(self.repo), **args})

    def auth(self, lease):
        return {key: lease[key] for key in ("reservation_id", "attempt_id", "owner_token")}

    def start_native(self, name="writer", files=None):
        files = list(files or ["subject.txt"])
        for rel in files:
            path = self.repo / rel
            if not path.exists():
                path.write_text("before\n")
        result = self.call("rig_job_start", id=name, role="mini", executor_kind="native_child",
                           native_agent_id=self.agent, model="gpt-5.6-luna", files=files)
        self.assertFalse(result.get("isError"), result)
        return result["structuredContent"]

    def mark_original_dead(self, lease, session=None):
        path = self.repo / ".rig" / "reservations" / (lease["reservation_id"] + ".json")
        record = json.loads(path.read_text())
        record["owner"]["parent_pid"] = self.dead_ident.get("pid")
        record["owner"]["parent_start_id"] = self.dead_ident.get("start_id", "")
        if session is not None:
            record["owner"]["session_id"] = session
        path.write_text(json.dumps(record, indent=2) + "\n")

    def cancel(self, job_id):
        text = jobs.cancel_job(self.repo, job_id)
        self.assertTrue("cancel" in text, text)

    def registry(self, status="closed", created=None, extra_child=None, job="writer", output="success", resume=False):
        meta = json.loads((self.repo / ".rig" / "jobs" / job / "meta.json").read_text())
        started = jobs.parse_job_ts(meta["started_at"]).timestamp()
        _write_registry(self.home, self.thread, self.agent, int(created if created is not None else started + 2),
                        status=status, extra_child=extra_child, cwd=str(self.repo), output=output, resume=resume)

    def recover(self, lease, apply=False, **args):
        return self.call("rig_job_recover_cancelled", id=lease["job_id"], rationale=args.pop("rationale", "Original parent died after cancel"),
                         apply=apply, owner_session="replacement-parent", **self.auth(lease), **args)

    def test_verified_recovery_releases_without_acceptance(self):
        lease = self.start_native()
        self.cancel("writer")
        self.mark_original_dead(lease)
        self.registry()
        dry = self.recover(lease)
        self.assertFalse(dry.get("isError"), dry)
        payload = json.loads(dry["content"][0]["text"])
        self.assertTrue(payload["dry_run"])
        self.assertEqual(payload["applied"], 0)
        self.assertEqual(jobs.load_job(self.repo / ".rig" / "jobs" / "writer")["status"], "running")
        applied = self.recover(lease, apply=True)
        self.assertFalse(applied.get("isError"), applied)
        result = applied["structuredContent"]
        self.assertEqual(result["applied"], 1)
        job = jobs.resolve_job(self.repo, "writer")
        self.assertEqual(job["status"], "cancelled")
        self.assertEqual(job["effective"], "cancelled")
        held = admission.list_reservations(self.repo)
        self.assertEqual(held, [])
        released = admission.list_reservations(self.repo, include_released=True)
        self.assertEqual(released[0]["execution_status"], "cancelled")
        self.assertEqual(released[0]["stage"], "released")
        self.assertFalse(released[0]["slot_held"])
        self.assertNotEqual(job.get("verification", {}).get("state"), "verified")
        audit = json.loads((self.repo / ".rig" / "jobs" / "writer" / "recovery-audit.json").read_text())
        self.assertEqual(audit["outcome"], "cancelled")
        self.assertEqual(audit["attempt_id"], lease["attempt_id"])
        self.assertTrue(audit["evidence"]["digest"].startswith("sha256:"))
        self.assertEqual(audit["replacement"]["session_id"], "replacement-parent")
        self.assertNotIn("owner_token", json.dumps(audit))
        repeat = self.recover(lease, apply=True)
        self.assertFalse(repeat.get("isError"), repeat)
        self.assertEqual(repeat["structuredContent"]["applied"], 0)
        self.assertEqual(jobs.resolve_job(self.repo, "writer")["status"], "cancelled")

    def test_dry_run_default_writes_nothing(self):
        lease = self.start_native()
        self.cancel("writer")
        self.mark_original_dead(lease)
        self.registry()
        before = (self.repo / ".rig" / "reservations" / (lease["reservation_id"] + ".json")).read_bytes()
        meta = (self.repo / ".rig" / "jobs" / "writer" / "meta.json").read_bytes()
        out = native_recovery.recover_cancelled(self.repo, job_id="writer", rationale="check", **self.auth(lease))
        self.assertTrue(out["dry_run"])
        self.assertEqual(out["applied"], 0)
        self.assertEqual((self.repo / ".rig" / "reservations" / (lease["reservation_id"] + ".json")).read_bytes(), before)
        self.assertEqual((self.repo / ".rig" / "jobs" / "writer" / "meta.json").read_bytes(), meta)
        self.assertFalse((self.repo / ".rig" / "jobs" / "writer" / "recovery-audit.json").exists())

    def test_empty_session_old_owner_uses_parent_pid_start(self):
        lease = self.start_native()
        self.cancel("writer")
        self.mark_original_dead(lease, session="")
        path = self.repo / ".rig" / "reservations" / (lease["reservation_id"] + ".json")
        record = json.loads(path.read_text())
        record["owner"].pop("initiating_identity", None)
        path.write_text(json.dumps(record, indent=2) + "\n")
        self.registry()
        out = self.recover(lease, apply=True)
        self.assertFalse(out.get("isError"), out)
        self.assertEqual(out["structuredContent"]["applied"], 1)

    def test_bad_credentials_agent_and_generation(self):
        lease = self.start_native()
        self.cancel("writer")
        self.mark_original_dead(lease)
        self.registry()
        bad = self.recover({**lease, "owner_token": "nope"})
        self.assertTrue(bad.get("isError"), bad)
        missing_agent = json.loads((self.repo / ".rig" / "reservations" / (lease["reservation_id"] + ".json")).read_text())
        missing_agent["owner"]["native_agent_id"] = ""
        (self.repo / ".rig" / "reservations" / (lease["reservation_id"] + ".json")).write_text(json.dumps(missing_agent, indent=2) + "\n")
        (self.repo / ".rig" / "jobs" / "writer" / "meta.json").write_text(
            json.dumps({**json.loads((self.repo / ".rig" / "jobs" / "writer" / "meta.json").read_text()), "native_agent_id": ""}) + "\n"
        )
        self.assertTrue(self.recover(lease).get("isError"))
        lease = self.start_native("gen", files=["gen.txt"])
        self.cancel("gen")
        self.mark_original_dead(lease)
        meta = json.loads((self.repo / ".rig" / "jobs" / "gen" / "meta.json").read_text())
        started = jobs.parse_job_ts(meta["started_at"]).timestamp()
        _write_registry(self.home, self.thread, self.agent, int(started - 50), status="closed", cwd=str(self.repo))
        out = self.call("rig_job_recover_cancelled", id="gen", rationale="too old", apply=False,
                        owner_session="replacement-parent", **self.auth(lease))
        self.assertTrue(out.get("isError"), out)

    def test_live_and_unknown_parent_or_child_block(self):
        lease = self.start_native()
        self.cancel("writer")
        self.registry()
        live = self.recover(lease)
        self.assertTrue(live.get("isError"), live)
        path = self.repo / ".rig" / "reservations" / (lease["reservation_id"] + ".json")
        record = json.loads(path.read_text())
        record["owner"]["parent_pid"] = self.dead_ident.get("pid")
        record["owner"]["parent_start_id"] = ""
        path.write_text(json.dumps(record, indent=2) + "\n")
        unknown = self.recover(lease)
        self.assertTrue(unknown.get("isError"), unknown)
        self.mark_original_dead(lease)
        self.registry(status="open")
        child = self.recover(lease)
        self.assertTrue(child.get("isError"), child)

    def test_missing_evidence_and_later_resume(self):
        lease = self.start_native()
        self.cancel("writer")
        self.mark_original_dead(lease)
        missing = self.recover(lease)
        self.assertTrue(missing.get("isError"), missing)
        meta = json.loads((self.repo / ".rig" / "jobs" / "writer" / "meta.json").read_text())
        started = int(jobs.parse_job_ts(meta["started_at"]).timestamp())
        _write_registry(self.home, self.thread, self.agent, started + 2, status="closed", cwd=str(self.repo),
                        extra_child=(("child-2", "sessions/child2.jsonl", str(self.repo), started + 20, started + 21, "/root/worker"),
                                     (self.thread, "child-2", "open")))
        resume = self.recover(lease)
        self.assertTrue(resume.get("isError"), resume)

    def test_public_api_rejects_evidence_path_and_terminal_attestation(self):
        lease = self.start_native()
        with self.assertRaisesRegex(native_recovery.NativeRecoveryError, "evidence file paths"):
            native_recovery.recover_cancelled(self.repo, job_id="writer", rationale="x", evidence_path="/tmp/db", **self.auth(lease))
        with self.assertRaisesRegex(native_recovery.NativeRecoveryError, "evidence file paths"):
            native_recovery.recover_cancelled(self.repo, job_id="writer", rationale="x",
                                              completion={"kind": "native_child", "terminal": True, "outcome": "cancelled"},
                                              **self.auth(lease))

    def test_duplicate_and_concurrent_recovery(self):
        lease = self.start_native()
        self.cancel("writer")
        self.mark_original_dead(lease)
        self.registry()
        results = []
        barrier = threading.Barrier(2)

        def run():
            barrier.wait(timeout=5)
            results.append(native_recovery.recover_cancelled(
                self.repo, job_id="writer", rationale="race", apply=True,
                owner_session="replacement-parent", **self.auth(lease)))

        threads = [threading.Thread(target=run) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        self.assertEqual(len(results), 2)
        self.assertEqual(sorted(item["applied"] for item in results), [0, 1])
        self.assertEqual(jobs.resolve_job(self.repo, "writer")["status"], "cancelled")

    def test_fault_injection_resumes_same_cancelled_outcome(self):
        lease = self.start_native()
        self.cancel("writer")
        self.mark_original_dead(lease)
        self.registry()
        original = admission._save

        def flaky(root, record):
            original(root, record)
            if record.get("recovery", {}).get("phase") == "started" and not getattr(flaky, "fired", False):
                flaky.fired = True
                raise OSError("injected crash")

        with patch.object(admission, "_save", side_effect=flaky):
            with self.assertRaises(OSError):
                native_recovery.recover_cancelled(self.repo, job_id="writer", rationale="crash", apply=True,
                                                  owner_session="replacement-parent", **self.auth(lease))
        record = json.loads((self.repo / ".rig" / "reservations" / (lease["reservation_id"] + ".json")).read_text())
        self.assertEqual(record["recovery"]["phase"], "started")
        self.assertEqual(record["recovery"]["outcome"], "cancelled")
        resumed = native_recovery.recover_cancelled(self.repo, job_id="writer", rationale="crash", apply=True,
                                                    owner_session="replacement-parent", **self.auth(lease))
        self.assertEqual(resumed["applied"], 1)
        self.assertEqual(jobs.resolve_job(self.repo, "writer")["status"], "cancelled")
        self.assertNotEqual(jobs.resolve_job(self.repo, "writer")["status"], "ok")

    def test_audit_source_and_queue_preservation(self):
        pending = work_queue.add_item(self.repo, "unrelated parked work")
        claimed = work_queue.add_item(self.repo, "bound native work")
        claimed_lease = self.call("rig_queue_claim", id=claimed["id"], worker="codex", files=["subject.txt"])
        self.assertFalse(claimed_lease.get("isError"), claimed_lease)
        claim = claimed_lease["structuredContent"]
        lease = self.call("rig_job_start", id="writer", role="mini", executor_kind="native_child",
                          native_agent_id=self.agent, model="gpt-5.6-luna", files=["subject.txt"],
                          queue_id=claimed["id"], **self.auth(claim))
        self.assertFalse(lease.get("isError"), lease)
        lease = lease["structuredContent"]
        before = (self.repo / ".rig" / "jobs" / "writer" / "change-before.json").read_bytes()
        self.cancel("writer")
        self.mark_original_dead(lease)
        self.registry()
        out = self.recover(lease, apply=True)
        self.assertFalse(out.get("isError"), out)
        self.assertEqual(work_queue.load_item(self.repo, pending["id"])["status"], "pending")
        bound = work_queue.load_item(self.repo, claimed["id"])
        self.assertEqual(bound["status"], "cancelled")
        self.assertTrue((self.repo / ".rig" / "reservations" / (lease["reservation_id"] + ".json")).is_file())
        self.assertEqual((self.repo / ".rig" / "jobs" / "writer" / "change-before.json").read_bytes(), before)
        self.assertTrue((self.repo / ".rig" / "jobs" / "writer" / "change-after.json").is_file())

    def test_cli_and_mcp_finish_cancelled_parity_and_auth_unchanged(self):
        lease = self.start_native()
        finish = self.call("rig_job_finish", id="writer", status="cancelled", **self.auth(lease),
                           completion={"kind": "native_child", "agent_id": self.agent, "terminal": True, "outcome": "cancelled"})
        self.assertFalse(finish.get("isError"), finish)
        self.assertFalse(admission.list_reservations(self.repo)[0]["slot_held"])
        other = self.call("rig_job_finish", id="writer", status="cancelled",
                          reservation_id=lease["reservation_id"], attempt_id=lease["attempt_id"],
                          owner_token=lease["owner_token"], owner_session="replacement-parent",
                          completion={"kind": "native_child", "agent_id": self.agent, "terminal": True, "outcome": "cancelled"})
        self.assertTrue(other.get("isError"), other)
        self.assertIn("session mismatch", other["content"][0]["text"])
        cli = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "jobs.py"), "record", "--status", "cancelled", "--repo", str(self.repo)],
            text=True, capture_output=True, env={**os.environ, "RIG_JOB_ID": "", "RIG_JOB_DIR": ""},
        )
        self.assertNotEqual(cli.returncode, 0)
        self.assertIn("ok|fail|timeout", cli.stderr)
        schema = {tool["name"]: tool for tool in rig_mcp.TOOLS}
        self.assertIn("cancelled", schema["rig_job_finish"]["inputSchema"]["properties"]["status"]["enum"])
        self.assertNotIn("cancelled", schema["rig_job_record"]["inputSchema"]["properties"]["status"]["enum"])
        child_dir = self.repo / ".rig" / "jobs" / "writer"
        with patch.dict(os.environ, {"RIG_JOB_ID": "writer", "RIG_JOB_DIR": str(child_dir)}):
            denied = self.call("rig_job_recover_cancelled", id="writer", rationale="child", **self.auth(lease))
        self.assertTrue(denied.get("isError"), denied)
        self.assertIn("not a child tool", denied["content"][0]["text"])

    def test_cli_recover_cancelled_matches_mcp(self):
        lease = self.start_native()
        self.cancel("writer")
        self.mark_original_dead(lease)
        self.registry()
        env = {**os.environ, "RIG_OWNER_TOKEN": lease["owner_token"], "RIG_JOB_ID": "", "RIG_JOB_DIR": "",
               "RIG_OWNER_SESSION": "replacement-parent"}
        dry = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "jobs.py"), "recover-cancelled", "writer",
             "--reservation-id", lease["reservation_id"], "--attempt-id", lease["attempt_id"],
             "--rationale", "cli dry-run", "--repo", str(self.repo)],
            text=True, capture_output=True, env=env,
        )
        self.assertEqual(dry.returncode, 0, dry.stderr)
        self.assertEqual(json.loads(dry.stdout)["applied"], 0)
        applied = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "jobs.py"), "recover-cancelled", "writer",
             "--reservation-id", lease["reservation_id"], "--attempt-id", lease["attempt_id"],
             "--rationale", "cli apply", "--apply", "--repo", str(self.repo)],
            text=True, capture_output=True, env=env,
        )
        self.assertEqual(applied.returncode, 0, applied.stderr)
        self.assertEqual(json.loads(applied.stdout)["applied"], 1)
        self.assertEqual(jobs.resolve_job(self.repo, "writer")["status"], "cancelled")

    def test_active_check_blocks_recovery(self):
        lease = self.start_native()
        self.cancel("writer")
        self.mark_original_dead(lease)
        self.registry()
        path = self.repo / ".rig" / "reservations" / (lease["reservation_id"] + ".json")
        record = json.loads(path.read_text())
        record["operation"] = {"id": "check-1", "operation": "check", "pid": os.getpid()}
        path.write_text(json.dumps(record, indent=2) + "\n")
        out = self.recover(lease, apply=True)
        self.assertTrue(out.get("isError"), out)

    def test_closed_without_successful_or_error_output_blocks(self):
        lease = self.start_native()
        self.cancel("writer")
        self.mark_original_dead(lease)
        self.registry(output="missing")
        self.assertTrue(self.recover(lease).get("isError"))
        self.registry(output="error")
        self.assertTrue(self.recover(lease).get("isError"))

    def test_resume_after_close_blocks(self):
        lease = self.start_native()
        self.cancel("writer")
        self.mark_original_dead(lease)
        self.registry(resume=True)
        self.assertTrue(self.recover(lease).get("isError"))

    def test_no_cancellation_blocks(self):
        lease = self.start_native()
        self.mark_original_dead(lease)
        self.registry()
        self.assertTrue(self.recover(lease).get("isError"))

    def test_ok_outcome_is_not_rewritten_cancelled(self):
        lease = self.start_native()
        self.cancel("writer")
        self.mark_original_dead(lease)
        self.registry()
        path = self.repo / ".rig" / "reservations" / (lease["reservation_id"] + ".json")
        record = json.loads(path.read_text())
        record.update(stopped=True, execution_status="ok", stage="verifying")
        path.write_text(json.dumps(record, indent=2) + "\n")
        out = self.recover(lease, apply=True)
        self.assertTrue(out.get("isError"), out)
        record = json.loads(path.read_text())
        self.assertEqual(record["execution_status"], "ok")
        self.assertNotEqual(record["execution_status"], "cancelled")

    def test_crash_after_each_phase_resumes_same_audit(self):
        original = admission._save
        for phase in native_recovery.PHASES[:-1]:
            name = "ph-" + phase.replace("_", "-")
            lease = self.start_native(name)
            self.cancel(name)
            self.mark_original_dead(lease)
            self.registry(job=name)
            fired = {"done": False}

            def flaky(root, record, target=phase, box=fired):
                original(root, record)
                if record.get("recovery", {}).get("phase") == target and not box["done"]:
                    box["done"] = True
                    raise OSError("injected crash after " + target)

            with patch.object(admission, "_save", side_effect=flaky):
                with self.assertRaises(OSError):
                    native_recovery.recover_cancelled(self.repo, job_id=name, rationale="crash", apply=True,
                                                      owner_session="replacement-parent", **self.auth(lease))
            record = json.loads((self.repo / ".rig" / "reservations" / (lease["reservation_id"] + ".json")).read_text())
            self.assertEqual(record["recovery"]["phase"], phase)
            started_at = record["recovery"]["at"]
            resumed = native_recovery.recover_cancelled(self.repo, job_id=name, rationale="crash", apply=True,
                                                        owner_session="replacement-parent", **self.auth(lease))
            self.assertEqual(resumed["applied"], 1)
            audit = json.loads((self.repo / ".rig" / "jobs" / name / "recovery-audit.json").read_text())
            self.assertEqual(audit["at"], started_at)
            self.assertEqual(audit["outcome"], "cancelled")
            self.assertEqual(jobs.resolve_job(self.repo, name)["status"], "cancelled")

    def test_released_without_audit_resumes_and_writes_audit(self):
        lease = self.start_native()
        self.cancel("writer")
        self.mark_original_dead(lease)
        self.registry()
        original = admission._save

        def flaky(root, record):
            original(root, record)
            if record.get("recovery", {}).get("phase") == "released" and not getattr(flaky, "fired", False):
                flaky.fired = True
                raise OSError("injected crash after released")

        with patch.object(admission, "_save", side_effect=flaky):
            with self.assertRaises(OSError):
                native_recovery.recover_cancelled(self.repo, job_id="writer", rationale="crash", apply=True,
                                                  owner_session="replacement-parent", **self.auth(lease))
        audit_path = self.repo / ".rig" / "jobs" / "writer" / "recovery-audit.json"
        if audit_path.exists():
            audit_path.unlink()
        record_path = self.repo / ".rig" / "reservations" / (lease["reservation_id"] + ".json")
        record = json.loads(record_path.read_text())
        self.assertEqual(record["recovery"]["phase"], "released")
        self.assertEqual(record["stage"], "released")
        started_at = record["recovery"]["at"]
        dry = self.recover(lease)
        self.assertFalse(dry.get("isError"), dry)
        self.assertFalse(json.loads(dry["content"][0]["text"]).get("already_complete"))
        resumed = native_recovery.recover_cancelled(self.repo, job_id="writer", rationale="crash", apply=True,
                                                    owner_session="replacement-parent", **self.auth(lease))
        self.assertEqual(resumed["applied"], 1)
        audit = json.loads(audit_path.read_text())
        self.assertEqual(audit["at"], started_at)
        self.assertEqual(audit["attempt_id"], lease["attempt_id"])
        repeat = native_recovery.recover_cancelled(self.repo, job_id="writer", rationale="crash", apply=True,
                                                   owner_session="replacement-parent", **self.auth(lease))
        self.assertEqual(repeat["applied"], 0)
        self.assertEqual(jobs.resolve_job(self.repo, "writer")["status"], "cancelled")


if __name__ == "__main__":
    unittest.main()
