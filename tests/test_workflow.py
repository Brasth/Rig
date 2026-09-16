#!/usr/bin/env python3
"""Workflow facade and CLI: create/list/show/advance/wait/extend/resolve/approve/cancel/report."""
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(ROOT / "scripts"))
import workflow
import workflow_state as wf


def _repo():
    temp = tempfile.TemporaryDirectory()
    repo = Path(temp.name).resolve()
    (repo / ".git").mkdir()
    (repo / ".rig").mkdir()
    for name in ("a.py", "b.py", "c.py"):
        (repo / name).write_text(name + "\n")
    (repo / ".rig" / "harness.toml").write_text(
        'parent = "codex"\n[workers]\ngrok = true\n[orchestration]\nmode = "adaptive"\nmax_nodes = 12\n'
        "[queue]\nmax_running = 3\n"
    )
    return temp, repo


def _spec(nodes=None, **extra):
    return {
        "title": "t",
        "case": "c",
        "nodes": nodes or [{"id": "w1", "role": "implement", "files": ["a.py"]}],
        **extra,
    }


class WorkflowFacade(unittest.TestCase):
    def setUp(self):
        self.temp, self.repo = _repo()
        self.addCleanup(self.temp.cleanup)
        self.env = mock.patch.dict(os.environ, {"RIG_OWNER_SESSION": "wf-cli"})
        self.env.start()
        self.addCleanup(self.env.stop)

    def create(self, nodes=None, **kwargs):
        return workflow.create(self.repo, _spec(nodes, **kwargs), owner_session="wf-cli")

    def pick(self, parent_writes=False):
        def _pick(node, spec, state, exclude=""):
            return {
                "worker": "parent" if parent_writes else "grok",
                "spawn": "native" if parent_writes else "run-worker",
                "parent_writes": parent_writes,
                "model": "grok-4.6",
                "effort": "high",
                "routing": {"policy_mode": "smart"},
            }
        return _pick

    def launch(self, launches):
        def _launch(repo, *, node, spec, state, choice, **kwargs):
            job_id = f"job-{node['id']}-{len(launches)}"
            folder = Path(repo) / ".rig" / "jobs" / job_id
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "meta.json").write_text(json.dumps({
                "job_id": job_id, "status": "running", "role": node["role"],
                "files": node.get("files") or [], "worker": choice.get("worker") or "grok",
                "reservation_id": f"res-{job_id}", "attempt_id": f"att-{job_id}",
            }))
            launches.append(node["id"])
            kind = "parent_writes" if choice.get("parent_writes") else "wrapper"
            return {"kind": kind, "job": {"job_id": job_id, "reservation_id": f"res-{job_id}",
                                          "attempt_id": f"att-{job_id}"},
                    "workflow_attempt": 1}
        return _launch

    def test_public_payload_strips_nested_tokens_keeps_credentials_path(self):
        raw = {
            "workflow_id": "w",
            "owner_token": "secret-token",
            "credentials_path": "/tmp/creds.json",
            "owner": {"owner_token": "nested", "session_id": "s"},
            "items": [{"owner_token": "x", "ok": True}],
        }
        safe = workflow.public_payload(raw)
        blob = json.dumps(safe)
        self.assertNotIn("secret-token", blob)
        self.assertNotIn("owner_token", blob)
        self.assertEqual(safe["credentials_path"], "/tmp/creds.json")
        self.assertEqual(safe["owner"]["session_id"], "s")

    def test_create_list_show_report_never_expose_token(self):
        created = self.create()
        token = created["owner_token"]
        self.assertTrue(created["credentials_path"])
        listed = workflow.listing(self.repo)
        shown = workflow.show(self.repo, created["workflow_id"])
        reported = workflow.report(self.repo, created["workflow_id"])
        for payload in (listed, shown, reported):
            blob = json.dumps(payload)
            self.assertNotIn(token, blob)
            self.assertNotIn("owner_token", blob)
        self.assertEqual(listed[0]["workflow_id"], created["workflow_id"])
        self.assertIn("events", shown)
        self.assertEqual(reported["workflow_id"], created["workflow_id"])
        self.assertIn("node_times", reported)

    def test_format_list_empty_and_rows(self):
        self.assertEqual(workflow.format_list([]), "no workflows")
        created = self.create()
        text = workflow.format_list(workflow.listing(self.repo))
        self.assertIn(created["workflow_id"], text)
        self.assertIn("STATUS", text)

    def test_extend_resolve_approve_cancel_auth(self):
        created = self.create([
            {"id": "w1", "role": "implement", "files": ["a.py"], "effects": "production"},
        ])
        wid, token = created["workflow_id"], created["owner_token"]
        with self.assertRaisesRegex(wf.WorkflowError, "credentials"):
            workflow.extend(self.repo, wid, [{"id": "extra", "role": "review", "files": ["b.py"]}])
        extended = workflow.extend(
            self.repo, wid,
            [{"id": "extra", "role": "review", "files": ["b.py"], "depends_on": ["w1"]}],
            owner_token=token, owner_session="wf-cli",
        )
        self.assertTrue(any(node["id"] == "extra" for node in extended["nodes"]))
        approved = workflow.approve(
            self.repo, wid, "w1", owner_token=token, owner_session="wf-cli",
            rationale="ship to production",
        )
        self.assertTrue((approved["node_state"]["w1"].get("approval") or {}).get("granted"))
        spec, state = wf.load_pair(self.repo, wid, required=True)
        state["nodes"]["w1"].update(status="failed", ran=True, launched=True)
        state["failure"] = {"node_id": "w1", "reason": "boom"}
        wf.save_state(self.repo, state)
        resolved = workflow.resolve(
            self.repo, wid, "w1", action="retry", owner_token=token, owner_session="wf-cli",
        )
        self.assertEqual(resolved["node_state"]["w1"]["status"], "pending")
        cancelled = workflow.cancel(
            self.repo, wid, owner_token=token, owner_session="wf-cli", rationale="stop",
        )
        self.assertTrue(cancelled["cancel_requested"] or cancelled["status"] in {"cancelled", "cancel-requested"})
        self.assertNotIn("owner_token", json.dumps(cancelled))

    def test_advance_and_wait_use_scheduler(self):
        created = self.create()
        wid, token = created["workflow_id"], created["owner_token"]
        launches = []
        advanced = workflow.advance(
            self.repo, wid, owner_token=token, owner_session="wf-cli",
            pick_fn=self.pick(), launch_fn=self.launch(launches),
        )
        self.assertEqual(launches, ["w1"])
        self.assertNotIn("owner_token", json.dumps(advanced))
        code, text = workflow.wait(self.repo, wid, 0)
        self.assertIn(code, {0, 2, 124})
        self.assertNotIn(token, text)

    def test_cli_create_file_and_stdin_omit_token(self):
        spec_path = self.repo / "spec.json"
        spec_path.write_text(json.dumps(_spec()))
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = workflow.main(["create", "--file", str(spec_path), "--repo", str(self.repo), "--json"])
        self.assertEqual(rc, 0, err.getvalue())
        payload = json.loads(out.getvalue())
        self.assertIn("credentials_path", payload)
        self.assertNotIn("owner_token", payload)
        creds = json.loads(Path(payload["credentials_path"]).read_text())
        token = creds["owner_token"]
        self.assertNotIn(token, out.getvalue())
        self.assertNotIn(token, err.getvalue())
        listed_out, listed_err = io.StringIO(), io.StringIO()
        with redirect_stdout(listed_out), redirect_stderr(listed_err):
            rc = workflow.main(["list", "--repo", str(self.repo)])
        self.assertEqual(rc, 0, listed_err.getvalue())
        self.assertIn(payload["workflow_id"], listed_out.getvalue())
        stdin_spec = json.dumps(_spec([{"id": "w2", "role": "mini", "files": ["b.py"]}]))
        stdin_out, stdin_err = io.StringIO(), io.StringIO()
        with redirect_stdout(stdin_out), redirect_stderr(stdin_err), mock.patch("sys.stdin", io.StringIO(stdin_spec)):
            rc = workflow.main(["create", "--repo", str(self.repo)])
        self.assertEqual(rc, 0, stdin_err.getvalue())
        self.assertIn("credentials", stdin_out.getvalue())
        self.assertNotIn("owner_token", stdin_out.getvalue())

    def test_cli_show_wait_report_cancel_and_missing_id(self):
        spec_path = self.repo / "spec.json"
        spec_path.write_text(json.dumps(_spec()))
        out = io.StringIO()
        with redirect_stdout(out):
            workflow.main(["create", "--file", str(spec_path), "--repo", str(self.repo), "--json"])
        created = json.loads(out.getvalue())
        wid = created["workflow_id"]
        token = json.loads(Path(created["credentials_path"]).read_text())["owner_token"]
        shown = io.StringIO()
        with redirect_stdout(shown):
            rc = workflow.main(["show", wid, "--repo", str(self.repo), "--json"])
        self.assertEqual(rc, 0)
        self.assertNotIn(token, shown.getvalue())
        waited = io.StringIO()
        with redirect_stdout(waited):
            rc = workflow.main(["wait", wid, "--repo", str(self.repo), "--timeout", "0"])
        self.assertIn(rc, {0, 1, 2, 124, 130})
        reported = io.StringIO()
        with redirect_stdout(reported):
            rc = workflow.main(["report", wid, "--repo", str(self.repo)])
        self.assertEqual(rc, 0)
        self.assertIn("wall_time_s", reported.getvalue())
        cancelled = io.StringIO()
        err = io.StringIO()
        with redirect_stdout(cancelled), redirect_stderr(err), mock.patch.dict(os.environ, {"RIG_OWNER_TOKEN": token}):
            rc = workflow.main(["cancel", wid, "--repo", str(self.repo), "--json"])
        self.assertEqual(rc, 0, err.getvalue())
        self.assertNotIn(token, cancelled.getvalue())
        missing = io.StringIO()
        missing_err = io.StringIO()
        with redirect_stdout(missing), redirect_stderr(missing_err):
            rc = workflow.main(["show", "--repo", str(self.repo)])
        self.assertEqual(rc, 1)
        self.assertIn("workflow id", missing_err.getvalue())

    def test_cli_invalid_spec_and_include_terminal(self):
        bad = self.repo / "bad.json"
        bad.write_text("[]")
        err = io.StringIO()
        with redirect_stdout(io.StringIO()), redirect_stderr(err):
            rc = workflow.main(["create", "--file", str(bad), "--repo", str(self.repo)])
        self.assertEqual(rc, 1)
        self.assertIn("object", err.getvalue())
        created = self.create()
        cancelled = workflow.cancel(
            self.repo, created["workflow_id"], owner_token=created["owner_token"], owner_session="wf-cli",
        )
        self.assertIn(cancelled["status"], {"cancelled", "cancel-requested"})
        out = io.StringIO()
        with redirect_stdout(out):
            workflow.main(["list", "--repo", str(self.repo), "--json", "--no-include-terminal"])
        rows = json.loads(out.getvalue())
        self.assertTrue(all(row.get("status") not in wf.TERMINAL for row in rows))

    def _set_orchestration_mode(self, mode):
        (self.repo / ".rig" / "harness.toml").write_text(
            f'parent = "codex"\n[workers]\ngrok = true\n[orchestration]\nmode = "{mode}"\nmax_nodes = 12\n'
            "[queue]\nmax_running = 3\n"
        )

    def test_single_mode_refuses_create(self):
        self._set_orchestration_mode("single")
        with self.assertRaisesRegex(wf.WorkflowError, "orchestration mode is single"):
            self.create()
        self.assertEqual(workflow.listing(self.repo), [])
        self.assertFalse((self.repo / ".rig" / "workflows").exists())

    def test_single_mode_refuses_advance_keeps_historical_show_cancel(self):
        created = self.create()
        wid, token = created["workflow_id"], created["owner_token"]
        _, state = wf.load_pair(self.repo, wid, required=True)
        generation = int(state.get("advance_generation") or 0)
        self._set_orchestration_mode("single")
        with self.assertRaisesRegex(wf.WorkflowError, "orchestration mode is single"):
            self.create([{"id": "later", "role": "mini", "files": ["b.py"]}])
        launches = []
        with self.assertRaisesRegex(wf.WorkflowError, "orchestration mode is single"):
            workflow.advance(
                self.repo, wid, owner_token=token, owner_session="wf-cli",
                pick_fn=self.pick(), launch_fn=self.launch(launches),
            )
        self.assertEqual(launches, [])
        _, state = wf.load_pair(self.repo, wid, required=True)
        self.assertEqual(int(state.get("advance_generation") or 0), generation)
        self.assertEqual(state.get("status"), "planned")
        listed = workflow.listing(self.repo)
        self.assertEqual([row["workflow_id"] for row in listed], [wid])
        shown = workflow.show(self.repo, wid)
        self.assertEqual(shown["workflow_id"], wid)
        self.assertEqual(shown["status"], "planned")
        reported = workflow.report(self.repo, wid)
        self.assertEqual(reported["workflow_id"], wid)
        cancelled = workflow.cancel(
            self.repo, wid, owner_token=token, owner_session="wf-cli", rationale="historical",
        )
        self.assertTrue(cancelled["cancel_requested"] or cancelled["status"] in {"cancelled", "cancel-requested"})
        self.assertEqual(cancelled["workflow_id"], wid)

    def test_single_mode_refuses_extend_approve_resolve_before_mutation(self):
        created = self.create([
            {"id": "w1", "role": "implement", "files": ["a.py"], "effects": "production"},
        ])
        wid, token = created["workflow_id"], created["owner_token"]
        spec, state = wf.load_pair(self.repo, wid, required=True)
        spec_hash = spec["spec_hash"]
        node_ids = [node["id"] for node in spec["nodes"]]
        self._set_orchestration_mode("single")
        with self.assertRaisesRegex(wf.WorkflowError, "orchestration mode is single"):
            workflow.extend(
                self.repo, wid, [{"id": "extra", "role": "mini", "files": ["b.py"]}],
                owner_token=token, owner_session="wf-cli",
            )
        with self.assertRaisesRegex(wf.WorkflowError, "orchestration mode is single"):
            workflow.approve(
                self.repo, wid, "w1", owner_token=token, owner_session="wf-cli", rationale="nope",
            )
        with self.assertRaisesRegex(wf.WorkflowError, "orchestration mode is single"):
            workflow.resolve(
                self.repo, wid, "w1", action="fail", owner_token=token, owner_session="wf-cli",
                rationale="nope",
            )
        spec, state = wf.load_pair(self.repo, wid, required=True)
        self.assertEqual(spec["spec_hash"], spec_hash)
        self.assertEqual([node["id"] for node in spec["nodes"]], node_ids)
        self.assertFalse((state["nodes"]["w1"] or {}).get("approval"))
        self.assertNotEqual(state.get("status"), "failed")
        listed = workflow.listing(self.repo)
        self.assertEqual([row["workflow_id"] for row in listed], [wid])
        shown = workflow.show(self.repo, wid)
        self.assertEqual(shown["workflow_id"], wid)
        reported = workflow.report(self.repo, wid)
        self.assertEqual(reported["workflow_id"], wid)
        cancelled = workflow.cancel(
            self.repo, wid, owner_token=token, owner_session="wf-cli", rationale="historical",
        )
        self.assertTrue(cancelled["cancel_requested"] or cancelled["status"] in {"cancelled", "cancel-requested"})


if __name__ == "__main__":
    unittest.main()
