#!/usr/bin/env python3
"""Read-only Codex registry evidence for cancelled native recovery."""
import json
import os
import sqlite3
import tempfile
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
import sys
sys.path.insert(0, str(ROOT / "scripts"))
import codex_native_evidence as evidence


def _iso(stamp):
    return datetime.fromtimestamp(stamp, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _write_jsonl(path, rows):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=True) + "\n" for row in rows))


def _close_rows(parent, child, stamp, *, output="success", resume=False, secret="hello secret"):
    iso = _iso(stamp)
    rows = [
        {"timestamp": iso, "type": "session_meta", "payload": {"id": parent, "cwd": "/tmp/repo"}},
        {"timestamp": iso, "type": "response_item", "payload": {
            "type": "message", "role": "user",
            "content": [{"type": "input_text", "text": secret}],
        }},
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
            "output": "collab tool failed: shutdown exploded",
        }})
    elif output == "event_only":
        rows.append({"timestamp": iso, "type": "event_msg", "payload": {
            "type": "item_completed", "item": {"type": "collab_agent_tool_call", "tool": "close_agent"},
        }})
    if resume:
        later = _iso(stamp + 8)
        rows.append({"timestamp": later, "type": "response_item", "payload": {
            "type": "function_call", "name": "resume_agent", "namespace": "multi_agent_v1",
            "call_id": "call-resume", "arguments": json.dumps({"id": child}),
        }})
        rows.append({"timestamp": later, "type": "response_item", "payload": {
            "type": "function_call_output", "call_id": "call-resume",
            "output": json.dumps({"status": "running"}),
        }})
    return rows


def _write_registry(home, threads, edges, *, extra_thread_sql="", skip_agent_path=False, jsonl=None):
    home.mkdir(parents=True, exist_ok=True)
    path = home / "state_5.sqlite"
    conn = sqlite3.connect(path)
    thread_cols = "id TEXT, rollout_path TEXT, cwd TEXT, created_at INTEGER, updated_at INTEGER"
    if not skip_agent_path:
        thread_cols += ", agent_path TEXT"
    conn.execute(f"CREATE TABLE threads ({thread_cols}{extra_thread_sql})")
    conn.execute("CREATE TABLE thread_spawn_edges (parent_thread_id TEXT, child_thread_id TEXT, status TEXT)")
    placeholders = "(?, ?, ?, ?, ?" + ("" if skip_agent_path else ", ?") + ")"
    conn.executemany(
        "INSERT INTO threads (id, rollout_path, cwd, created_at, updated_at"
        + ("" if skip_agent_path else ", agent_path") + ") VALUES " + placeholders,
        threads,
    )
    conn.executemany("INSERT INTO thread_spawn_edges VALUES (?, ?, ?)", edges)
    conn.commit()
    conn.close()
    if jsonl:
        for rollout, rows in jsonl.items():
            _write_jsonl(home / rollout, rows)
    return path


class CodexNativeEvidence(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name) / "codex-home"
        self.enterContext(patch.dict(os.environ, {"CODEX_HOME": str(self.home), "CODEX_SQLITE_HOME": ""}))
        self.parent = "parent-thread-1"
        self.child = "child-agent-1"
        self.cwd = "/tmp/repo"
        self.launch = int(time.time()) - 20
        self.created = self.launch + 5

    def report(self, **args):
        return evidence.inspect_native_child(
            parent_thread_id=args.get("parent_thread_id", self.parent),
            agent_id=args.get("agent_id", self.child),
            agent_path=args.get("agent_path", "/root/worker"),
            launched_at=args.get("launched_at", self.launch),
            cwd=args.get("cwd", self.cwd),
        )

    def closed_registry(self, **overrides):
        parent_rollout = "sessions/parent.jsonl"
        threads = overrides.get("threads") or [
            (self.parent, parent_rollout, self.cwd, self.created - 1, self.created, ""),
            (self.child, "sessions/child.jsonl", self.cwd, self.created, self.created + 1, "/root/worker"),
        ]
        edges = overrides.get("edges") or [(self.parent, self.child, "closed")]
        jsonl = overrides.get("jsonl")
        if jsonl is None:
            jsonl = {parent_rollout: _close_rows(self.parent, self.child, self.created, output=overrides.get("output", "success"),
                                                 resume=overrides.get("resume", False))}
        return _write_registry(self.home, threads, edges, extra_thread_sql=overrides.get("extra_thread_sql", ""),
                               skip_agent_path=overrides.get("skip_agent_path", False), jsonl=jsonl)

    def test_closed_edge_with_successful_close_is_verified_digest_without_contents(self):
        self.closed_registry()
        report = self.report()
        self.assertEqual(report["status"], "verified")
        self.assertEqual(report["edge_status"], "closed")
        self.assertTrue(report["digest"].startswith("sha256:"))
        self.assertEqual(report["identity"]["child_thread_id"], self.child)
        blob = str(report)
        self.assertNotIn("hello secret", blob)
        self.assertNotIn("sessions/parent.jsonl", blob)
        self.assertNotIn("running", blob)

    def test_public_api_rejects_evidence_file_paths(self):
        with self.assertRaisesRegex(evidence.EvidenceError, "extra paths"):
            evidence.inspect_native_child(parent_thread_id=self.parent, agent_id=self.child,
                                          evidence_path="/tmp/state_5.sqlite")

    def test_missing_registry_and_schema_are_unavailable(self):
        self.assertEqual(self.report()["reason"], evidence.UNAVAILABLE)
        _write_registry(self.home, [(self.child, "r", "/tmp", self.created, self.created)],
                        [(self.parent, self.child, "closed")], skip_agent_path=True)
        self.assertEqual(self.report()["reason"], evidence.UNAVAILABLE)

    def test_open_edge_is_live_not_terminal(self):
        self.closed_registry(edges=[(self.parent, self.child, "open")])
        report = self.report()
        self.assertEqual(report["status"], "live")
        self.assertNotEqual(report["status"], "verified")

    def test_interrupt_agent_previous_status_is_not_terminal(self):
        parent_rollout = "sessions/parent.jsonl"
        _write_registry(
            self.home,
            [
                (self.parent, parent_rollout, self.cwd, self.created - 1, self.created, ""),
                (self.child, "sessions/child.jsonl", self.cwd, self.created, self.created, "/root/worker"),
            ],
            [(self.parent, self.child, "open")],
            jsonl={parent_rollout: [
                {"timestamp": _iso(self.created), "type": "response_item", "payload": {
                    "type": "function_call", "name": "interrupt_agent", "call_id": "call-int",
                    "arguments": json.dumps({"target": self.child}),
                }},
                {"timestamp": _iso(self.created), "type": "response_item", "payload": {
                    "type": "function_call_output", "call_id": "call-int",
                    "output": json.dumps({"previous_status": "completed"}),
                }},
            ]},
        )
        report = self.report()
        self.assertNotEqual(report["status"], "verified")
        self.assertIn(report["status"], {"live", "unavailable"})

    def test_closed_edge_without_successful_output_is_unavailable(self):
        self.closed_registry(output="event_only")
        self.assertEqual(self.report()["reason"], evidence.UNAVAILABLE)

    def test_error_close_output_is_unavailable(self):
        self.closed_registry(output="error")
        self.assertEqual(self.report()["reason"], evidence.UNAVAILABLE)

    def test_later_resume_or_start_is_unavailable(self):
        later = self.created + 50
        parent_rollout = "sessions/parent.jsonl"
        self.closed_registry(
            threads=[
                (self.parent, parent_rollout, self.cwd, self.created - 1, later, ""),
                (self.child, "sessions/child.jsonl", self.cwd, self.created, self.created, "/root/worker"),
                ("child-agent-2", "sessions/child2.jsonl", self.cwd, later, later, "/root/worker"),
            ],
            edges=[(self.parent, self.child, "closed"), (self.parent, "child-agent-2", "open")],
        )
        self.assertEqual(self.report()["reason"], evidence.UNAVAILABLE)

    def test_resume_after_close_is_unavailable(self):
        self.closed_registry(resume=True)
        self.assertEqual(self.report()["reason"], evidence.UNAVAILABLE)

    def test_unrelated_threads_are_not_scanned(self):
        parent_rollout = "sessions/parent.jsonl"
        self.closed_registry(
            threads=[
                (self.parent, parent_rollout, self.cwd, self.created - 1, self.created, ""),
                (self.child, "sessions/child.jsonl", self.cwd, self.created, self.created + 1, "/root/worker"),
                ("other-parent", "sessions/other.jsonl", "/elsewhere", self.created, self.created, ""),
                ("other-child", "sessions/other-child.jsonl", "/elsewhere", self.created + 80, self.created + 80, "/root/worker"),
            ],
            edges=[(self.parent, self.child, "closed"), ("other-parent", "other-child", "open")],
        )
        self.assertEqual(self.report()["status"], "verified")

    def test_generation_before_launch_is_unavailable(self):
        self.closed_registry()
        report = self.report(launched_at=self.created + 100)
        self.assertEqual(report["reason"], evidence.UNAVAILABLE)

    def test_task_complete_event_is_not_terminal_without_closed_edge(self):
        parent_rollout = "sessions/parent.jsonl"
        _write_registry(
            self.home,
            [
                (self.parent, parent_rollout, self.cwd, self.created - 1, self.created, ""),
                (self.child, "sessions/child.jsonl", self.cwd, self.created, self.created, "/root/worker"),
            ],
            [(self.parent, self.child, "open")],
            jsonl={parent_rollout: [
                {"timestamp": _iso(self.created), "type": "event_msg", "payload": {"type": "task_complete", "status": "ok"}},
            ]},
        )
        report = self.report()
        self.assertNotEqual(report["status"], "verified")

    def test_missing_child_is_unavailable(self):
        self.closed_registry()
        report = self.report(agent_id="missing-agent")
        self.assertEqual(report["reason"], evidence.UNAVAILABLE)

    def test_unsupported_edge_status_is_unavailable(self):
        self.closed_registry(edges=[(self.parent, self.child, "interrupted")])
        self.assertEqual(self.report()["reason"], evidence.UNAVAILABLE)

    def test_cwd_mismatch_is_unavailable(self):
        self.closed_registry()
        self.assertEqual(self.report(cwd="/other/repo")["reason"], evidence.UNAVAILABLE)

    def test_code_mode_format_is_unavailable(self):
        parent_rollout = "sessions/parent.jsonl"
        self.closed_registry(jsonl={parent_rollout: [
            {"timestamp": _iso(self.created), "type": "code_mode", "payload": {
                "tool": "close_agent", "target": self.child, "previous_status": "running",
            }},
        ]})
        self.assertEqual(self.report()["reason"], evidence.UNAVAILABLE)

    def test_closed_edge_alone_is_not_verified(self):
        parent_rollout = "sessions/parent.jsonl"
        self.closed_registry(jsonl={parent_rollout: [
            {"timestamp": _iso(self.created), "type": "session_meta", "payload": {"id": self.parent}},
        ]})
        self.assertEqual(self.report()["reason"], evidence.UNAVAILABLE)

    def test_path_agent_id_resolves_by_agent_path(self):
        self.closed_registry()
        report = self.report(agent_id="/root/worker", agent_path="")
        self.assertEqual(report["status"], "verified")
        self.assertEqual(report["identity"]["child_thread_id"], self.child)

    def test_explicit_child_id_and_path_must_both_match(self):
        self.closed_registry()
        self.assertEqual(self.report(agent_id="missing-agent", agent_path="/root/worker")["reason"],
                         evidence.UNAVAILABLE)
        self.assertEqual(self.report(agent_id=self.child, agent_path="/other/worker")["reason"],
                         evidence.UNAVAILABLE)

    def test_close_call_before_launch_or_creation_is_unavailable(self):
        parent_rollout = "sessions/parent.jsonl"
        early = self.launch - 10
        self.closed_registry(jsonl={parent_rollout: [
            {"timestamp": _iso(early), "type": "response_item", "payload": {
                "type": "function_call", "name": "close_agent", "namespace": "multi_agent_v1",
                "call_id": "call-close", "arguments": json.dumps({"target": self.child}),
            }},
            {"timestamp": _iso(self.created), "type": "response_item", "payload": {
                "type": "function_call_output", "call_id": "call-close",
                "output": json.dumps({"previous_status": "running"}),
            }},
        ]})
        self.assertEqual(self.report()["reason"], evidence.UNAVAILABLE)

    def test_close_output_before_call_is_unavailable(self):
        parent_rollout = "sessions/parent.jsonl"
        self.closed_registry(jsonl={parent_rollout: [
            {"timestamp": _iso(self.created + 8), "type": "response_item", "payload": {
                "type": "function_call", "name": "close_agent", "namespace": "multi_agent_v1",
                "call_id": "call-close", "arguments": json.dumps({"target": self.child}),
            }},
            {"timestamp": _iso(self.created + 2), "type": "response_item", "payload": {
                "type": "function_call_output", "call_id": "call-close",
                "output": json.dumps({"previous_status": "running"}),
            }},
        ]})
        self.assertEqual(self.report()["reason"], evidence.UNAVAILABLE)

    def test_wrong_namespace_is_unavailable(self):
        parent_rollout = "sessions/parent.jsonl"
        self.closed_registry(jsonl={parent_rollout: [
            {"timestamp": _iso(self.created), "type": "response_item", "payload": {
                "type": "function_call", "name": "close_agent", "namespace": "mcp_other",
                "call_id": "call-close", "arguments": json.dumps({"target": self.child}),
            }},
            {"timestamp": _iso(self.created), "type": "response_item", "payload": {
                "type": "function_call_output", "call_id": "call-close",
                "output": json.dumps({"previous_status": "running"}),
            }},
        ]})
        self.assertEqual(self.report()["reason"], evidence.UNAVAILABLE)

    def test_pending_later_resume_call_without_output_is_unavailable(self):
        parent_rollout = "sessions/parent.jsonl"
        rows = _close_rows(self.parent, self.child, self.created, output="success")
        rows.append({"timestamp": _iso(self.created + 8), "type": "response_item", "payload": {
            "type": "function_call", "name": "resume_agent", "namespace": "multi_agent_v1",
            "call_id": "call-resume", "arguments": json.dumps({"id": self.child}),
        }})
        self.closed_registry(jsonl={parent_rollout: rows})
        self.assertEqual(self.report()["reason"], evidence.UNAVAILABLE)

    def test_later_code_mode_lifecycle_is_unavailable(self):
        parent_rollout = "sessions/parent.jsonl"
        rows = _close_rows(self.parent, self.child, self.created, output="success")
        rows.append({"timestamp": _iso(self.created + 8), "type": "code_mode", "payload": {
            "tool": "start_agent", "target": self.child,
        }})
        self.closed_registry(jsonl={parent_rollout: rows})
        self.assertEqual(self.report()["reason"], evidence.UNAVAILABLE)

    def test_resume_call_without_timestamp_after_close_is_unavailable(self):
        parent_rollout = "sessions/parent.jsonl"
        rows = _close_rows(self.parent, self.child, self.created, output="success")
        rows.append({"type": "response_item", "payload": {
            "type": "function_call", "name": "resume_agent", "namespace": "multi_agent_v1",
            "call_id": "call-resume", "arguments": json.dumps({"id": self.child}),
        }})
        self.closed_registry(jsonl={parent_rollout: rows})
        self.assertEqual(self.report()["reason"], evidence.UNAVAILABLE)

    def test_unknown_target_call_without_timestamp_is_unavailable(self):
        parent_rollout = "sessions/parent.jsonl"
        rows = _close_rows(self.parent, self.child, self.created, output="success")
        rows.append({"type": "response_item", "payload": {
            "type": "function_call", "name": "wait_agent",
            "call_id": "call-wait", "arguments": json.dumps({"target": self.child}),
        }})
        self.closed_registry(jsonl={parent_rollout: rows})
        self.assertEqual(self.report()["reason"], evidence.UNAVAILABLE)

    def test_close_envelope_error_true_is_unavailable(self):
        self.assertEqual(self._report_close_envelope({"error": True})["reason"], evidence.UNAVAILABLE)

    def test_close_envelope_is_error_true_is_unavailable(self):
        self.assertEqual(self._report_close_envelope({"is_error": True})["reason"], evidence.UNAVAILABLE)

    def test_close_envelope_isError_true_is_unavailable(self):
        self.assertEqual(self._report_close_envelope({"isError": True})["reason"], evidence.UNAVAILABLE)

    def test_close_envelope_success_false_is_unavailable(self):
        self.assertEqual(self._report_close_envelope({"success": False})["reason"], evidence.UNAVAILABLE)

    def test_close_parsed_output_error_true_is_unavailable(self):
        self.assertEqual(self._report_close_parsed({"error": True})["reason"], evidence.UNAVAILABLE)

    def test_close_parsed_output_is_error_true_is_unavailable(self):
        self.assertEqual(self._report_close_parsed({"is_error": True})["reason"], evidence.UNAVAILABLE)

    def test_close_parsed_output_isError_true_is_unavailable(self):
        self.assertEqual(self._report_close_parsed({"isError": True})["reason"], evidence.UNAVAILABLE)

    def test_close_parsed_output_success_false_is_unavailable(self):
        self.assertEqual(self._report_close_parsed({"success": False})["reason"], evidence.UNAVAILABLE)

    def _report_close_envelope(self, extra):
        parent_rollout = "sessions/parent.jsonl"
        iso = _iso(self.created)
        payload = {
            "type": "function_call_output", "call_id": "call-close",
            "output": json.dumps({"previous_status": "running"}),
            **extra,
        }
        self.closed_registry(jsonl={parent_rollout: [
            {"timestamp": iso, "type": "response_item", "payload": {
                "type": "function_call", "name": "close_agent", "namespace": "multi_agent_v1",
                "call_id": "call-close", "arguments": json.dumps({"target": self.child}),
            }},
            {"timestamp": iso, "type": "response_item", "payload": payload},
        ]})
        return self.report()

    def _report_close_parsed(self, extra):
        parent_rollout = "sessions/parent.jsonl"
        iso = _iso(self.created)
        body = {"previous_status": "running", **extra}
        self.closed_registry(jsonl={parent_rollout: [
            {"timestamp": iso, "type": "response_item", "payload": {
                "type": "function_call", "name": "close_agent", "namespace": "multi_agent_v1",
                "call_id": "call-close", "arguments": json.dumps({"target": self.child}),
            }},
            {"timestamp": iso, "type": "response_item", "payload": {
                "type": "function_call_output", "call_id": "call-close",
                "output": json.dumps(body),
            }},
        ]})
        return self.report()


if __name__ == "__main__":
    unittest.main()
