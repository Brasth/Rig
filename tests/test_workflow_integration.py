#!/usr/bin/env python3
"""End-to-end adaptive workflow smoke: real persisted workflow/admission/queue state.

Fakes only the external worker/process spawn. Launch still reserves, writes job
meta, and binds workflow identity through admission.
"""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))
import admission
import ask as rig_ask
import cancellation
import change_evidence
import coordination
import rig_mcp
import verification
import work_queue
import workflow
import workflow_scheduler as sched
import workflow_state as wf
from test_mcp_dispatch import CHILD_TOOLS, WORKFLOW_TOOLS


SESSION = "wf-int"


def _repo():
    temp = tempfile.TemporaryDirectory()
    repo = Path(temp.name).resolve()
    (repo / ".git").mkdir()
    (repo / ".rig").mkdir()
    for name in ("a.py", "b.py", "c.py", "d.py", "e.py"):
        (repo / name).write_text(name + "\n")
    (repo / ".rig" / "harness.toml").write_text(
        'parent = "codex"\n[workers]\ngrok = true\nclaude = true\n'
        "[orchestration]\nmode = \"adaptive\"\nmax_nodes = 12\n"
        "[queue]\nmax_running = 3\n"
    )
    return temp, repo


class WorkflowIntegration(unittest.TestCase):
    def setUp(self):
        self.temp, self.repo = _repo()
        self.addCleanup(self.temp.cleanup)
        self.env = mock.patch.dict(os.environ, {
            "RIG_OWNER_SESSION": SESSION, "RIG_PARENT": "codex",
            "RIG_SKIP_MODEL_CATALOG": "1", "RIG_JOB_ID": "", "RIG_JOB_DIR": "",
        }, clear=False)
        self.env.start()
        self.addCleanup(self.env.stop)
        os.environ.pop("RIG_JOB_ID", None)
        os.environ.pop("RIG_JOB_DIR", None)
        self.launches = []
        self.jobs = {}

    def create(self, nodes, **kwargs):
        return workflow.create(
            self.repo, {"title": "integration", "case": "adaptive workflow", "nodes": nodes, **kwargs},
            owner_session=SESSION,
        )

    def pick(self, parent_writes=False, worker="grok"):
        def _pick(node, spec, state, exclude=""):
            return {
                "worker": "parent" if parent_writes else worker,
                "spawn": "native" if parent_writes else "run-worker",
                "parent_writes": parent_writes,
                "model": "grok-4.6",
                "effort": "high",
                "routing": {"policy_mode": "smart"},
            }
        return _pick

    def launch_fn(self):
        def _launch(repo, *, node, spec, state, choice, owner_session="", resources=None,
                    allow_read=None, **kwargs):
            job_id = f"job-{node['id']}-{len(self.launches)}"
            access = "write" if node.get("role") in wf.WRITE_ROLES else "read"
            owner = admission.caller_owner("parent", owner_session=owner_session or SESSION)
            record = admission.reserve(
                repo, job_id=job_id, worker=choice.get("worker") or "grok",
                role=node.get("role") or "implement", files=list(node.get("files") or []),
                access=access, owner=owner, owner_session=owner_session or SESSION,
                resources=resources if resources is not None else node.get("resources") or [],
                workflow_id=spec.get("workflow_id") or "", workflow_node_id=node["id"],
                workflow_spec_hash=spec.get("spec_hash") or "",
                workflow_attempt=int(((state.get("nodes") or {}).get(node["id"]) or {}).get("workflow_attempt") or 0) + 1,
                allow_read_overlap_reservations=allow_read,
            )
            folder = Path(repo) / ".rig" / "jobs" / job_id
            folder.mkdir(parents=True, exist_ok=True)
            meta = {
                "job_id": job_id, "status": "running", "role": node.get("role"),
                "files": list(node.get("files") or []), "worker": choice.get("worker") or "grok",
                "reservation_id": record["reservation_id"], "attempt_id": record["attempt_id"],
                "workflow_id": spec.get("workflow_id") or "", "workflow_node_id": node["id"],
                "workflow_spec_hash": spec.get("spec_hash") or "",
                "resources": record.get("resources") or [], "access": access,
                "ownership_established": True, "execution_mode": "parent",
                "owner": record.get("owner") or owner,
            }
            (folder / "meta.json").write_text(json.dumps(meta) + "\n")
            self.jobs[job_id] = {
                **admission.credentials(record),
                "files": list(node.get("files") or []),
                "owner_session": owner_session or SESSION,
                "node_id": node["id"],
            }
            self.launches.append(node["id"])
            return {
                "kind": "wrapper",
                "job": {
                    "job_id": job_id, "reservation_id": record["reservation_id"],
                    "attempt_id": record["attempt_id"],
                },
                "workflow_attempt": record.get("workflow_attempt") or 1,
            }
        return _launch

    def advance(self, created, **kwargs):
        return workflow.advance(
            self.repo, created["workflow_id"], owner_session=SESSION,
            owner_token=created["owner_token"], pick_fn=kwargs.get("pick_fn", self.pick()),
            launch_fn=kwargs.get("launch_fn", self.launch_fn()),
        )

    def queue_status(self, queue_id):
        return json.loads((self.repo / ".rig" / "queue" / f"{queue_id}.json").read_text())["status"]

    def node_job(self, created, node_id):
        spec, state = wf.load_pair(self.repo, created["workflow_id"], required=True)
        return spec, state, state["nodes"][node_id].get("job_id") or ""

    def finish_job(self, job_id, status="ok"):
        creds = self.jobs[job_id]
        admission.finish(
            self.repo, status=status, owner_session=creds["owner_session"],
            completion={"kind": "parent_task", "completed": True},
            **{key: creds[key] for key in ("reservation_id", "attempt_id", "owner_token")},
        )
        folder = self.repo / ".rig" / "jobs" / job_id
        meta = json.loads((folder / "meta.json").read_text())
        meta["status"] = status
        (folder / "meta.json").write_text(json.dumps(meta) + "\n")

    def accept_job(self, created, node_id, *, next_action="complete"):
        spec, state, job_id = self.node_job(created, node_id)
        self.assertTrue(job_id, f"{node_id} has no job")
        node = next(item for item in spec["nodes"] if item["id"] == node_id)
        creds = self.jobs[job_id]
        auth = {key: creds[key] for key in ("reservation_id", "attempt_id", "owner_token", "owner_session")}
        self.finish_job(job_id, "ok")
        folder = self.repo / ".rig" / "jobs" / job_id
        verification.record_requirements(
            self.repo, folder, [], ["Inspect scoped workflow node output"], **auth,
        )
        snap = change_evidence.snapshot(self.repo, node.get("files") or [])["snapshot_id"]
        verification.accept(
            self.repo, folder, "accept", snap,
            rationale="Parent inspected scoped workflow node output",
            next=next_action, **auth,
        )
        return wf.refresh(self.repo, created["workflow_id"])

    def test_dependency_chain_requires_current_parent_acceptance(self):
        created = self.create([
            {"id": "w1", "role": "implement", "files": ["a.py"]},
            {"id": "v1", "role": "verify", "files": ["a.py"], "depends_on": ["w1"]},
        ])
        first = self.advance(created)
        self.assertEqual([row["node_id"] for row in first["launched"]], ["w1"])
        spec, state, job_id = self.node_job(created, "w1")
        self.assertTrue((self.repo / ".rig" / "reservations" / f"{state['nodes']['w1']['reservation_id']}.json").is_file())
        self.finish_job(job_id, "ok")
        second = self.advance(created)
        self.assertEqual(second["launched"], [])
        self.assertEqual(second["node_state"]["w1"]["status"], "completed-unverified")
        self.assertFalse(second["node_state"]["w1"]["accepted"])
        self.accept_job(created, "w1")
        third = self.advance(created)
        self.assertEqual([row["node_id"] for row in third["launched"]], ["v1"])
        held = json.loads((self.repo / ".rig" / "reservations" / f"{self.jobs[job_id]['reservation_id']}.json").read_text())
        self.assertFalse(held["slot_held"])

    def test_three_disjoint_ready_nodes_fourth_stays_ready(self):
        created = self.create([
            {"id": "w1", "role": "implement", "files": ["a.py"]},
            {"id": "w2", "role": "mini", "files": ["b.py"]},
            {"id": "w3", "role": "implement", "files": ["c.py"]},
            {"id": "w4", "role": "mini", "files": ["d.py"]},
        ])
        ids = [node["id"] for node in created["nodes"]]
        self.assertIn("final-verify", ids)
        result = self.advance(created)
        launched = [row["node_id"] for row in result["launched"]]
        self.assertEqual(launched, ["w1", "w2", "w3"])
        self.assertTrue(any("live+reserved" in row["reason"] or "slot" in row["reason"] for row in result["skipped"]))
        spec, state = wf.load_pair(self.repo, created["workflow_id"], required=True)
        ready = [node["id"] for node in wf.ready_nodes(spec, state)]
        self.assertIn("w4", ready)
        self.assertEqual(state["nodes"]["w4"]["status"], "pending")
        self.assertFalse(state["nodes"]["w4"]["launched"])
        self.assertNotIn("final-verify", launched)
        slots = [row for row in admission.list_reservations(self.repo) if row.get("slot_held")]
        self.assertEqual(len(slots), 3)

    def test_coordination_request_reply_and_ask_barriers(self):
        created = self.create([{"id": "w1", "role": "implement", "files": ["a.py"]}])
        ask_job = self.repo / ".rig" / "jobs" / "other-ask"
        ask_job.mkdir(parents=True)
        (ask_job / "meta.json").write_text(json.dumps({
            "job_id": "other-ask", "status": "running", "worker": "grok", "role": "implement",
            "files": ["e.py"], "access": "write",
        }) + "\n")
        rig_ask.write_ask(ask_job, "Bash", {"command": "ls"}, preview_text="ls")
        blocked = self.advance(created)
        self.assertEqual(blocked["reason"], "repo ASK")
        self.assertEqual(blocked["launched"], [])
        (ask_job / "ask.json").unlink()
        first = self.advance(created)
        self.assertEqual([row["node_id"] for row in first["launched"]], ["w1"])
        spec, state, job_id = self.node_job(created, "w1")
        asked = coordination.request(self.repo, job_id, "scope", "need sibling order")
        request_id = asked["request"]["id"]
        coord_block = self.advance(created)
        self.assertEqual(coord_block["reason"], "coordination")
        code, text = workflow.wait(self.repo, created["workflow_id"], 0)
        self.assertEqual(code, 2)
        self.assertIn("COORDINATION", text)
        self.assertIn("w1", text)
        coordination.reply(
            self.repo, created["workflow_id"], request_id, decision="reply",
            text="keep current scope", owner_token=created["owner_token"], owner_session=SESSION,
        )
        after_reply = self.advance(created)
        self.assertNotEqual(after_reply.get("reason"), "coordination")
        folder = self.repo / ".rig" / "jobs" / job_id
        rig_ask.write_ask(folder, "Bash", {"command": "git status"}, preview_text="git status")
        ask_block = self.advance(created)
        self.assertEqual(ask_block["reason"], "repo ASK")
        wait_code, wait_text = workflow.wait(self.repo, created["workflow_id"], 0)
        self.assertEqual(wait_code, 2)
        self.assertIn("ASK", wait_text)
        self.assertIn("w1", wait_text)

    def test_failure_isolation_and_explicit_resolution(self):
        created = self.create([
            {"id": "w1", "role": "implement", "files": ["a.py"]},
            {"id": "w2", "role": "mini", "files": ["b.py"]},
            {"id": "opt", "role": "mini", "files": ["c.py"], "required": False},
        ])
        first = self.advance(created)
        launched = [row["node_id"] for row in first["launched"]]
        self.assertEqual(set(launched), {"w1", "w2", "opt"})
        spec, state, job_w1 = self.node_job(created, "w1")
        job_w2 = state["nodes"]["w2"]["job_id"]
        self.finish_job(job_w1, "fail")
        refreshed = wf.refresh(self.repo, created["workflow_id"])
        self.assertEqual(refreshed["node_state"]["w1"]["status"], "failed")
        self.assertEqual(refreshed["node_state"]["w2"]["status"], "running")
        self.assertEqual(refreshed["status"], "blocked")
        blocked = self.advance(created)
        self.assertEqual(blocked["reason"], "unresolved failure")
        self.assertEqual(blocked["launched"], [])
        self.assertEqual(blocked["node_state"]["w2"]["job_id"], job_w2)
        retried = workflow.resolve(
            self.repo, created["workflow_id"], "w1", action="retry",
            owner_token=created["owner_token"], owner_session=SESSION,
        )
        self.assertEqual(retried["node_state"]["w1"]["status"], "pending")
        self.assertEqual(retried["node_state"]["w2"]["status"], "running")
        with self.assertRaisesRegex(wf.WorkflowError, "silently waived|required"):
            workflow.resolve(
                self.repo, created["workflow_id"], "w1", action="skip", rationale="nope",
                owner_token=created["owner_token"], owner_session=SESSION,
            )
        skipped = workflow.resolve(
            self.repo, created["workflow_id"], "opt", action="skip", rationale="optional seed not needed",
            owner_token=created["owner_token"], owner_session=SESSION,
        )
        self.assertEqual(skipped["node_state"]["opt"]["status"], "skipped")
        failed = workflow.resolve(
            self.repo, created["workflow_id"], "w1", action="fail", rationale="give up on w1",
            owner_token=created["owner_token"], owner_session=SESSION,
        )
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(failed["node_state"]["w2"]["status"], "running")

    def test_review_seed_file_resource_ordering_and_final_verify(self):
        with self.assertRaisesRegex(wf.WorkflowError, "overlapping workflow writer"):
            wf.normalize_spec({"nodes": [
                {"id": "w1", "role": "implement", "files": ["a.py"]},
                {"id": "seed", "role": "mini", "files": ["a.py"]},
            ]})
        with self.assertRaisesRegex(wf.WorkflowError, "resource"):
            wf.normalize_spec({"nodes": [
                {"id": "w1", "role": "implement", "files": ["a.py"],
                 "resources": [{"name": "db.main", "access": "write"}]},
                {"id": "seed", "role": "mini", "files": ["c.py"],
                 "resources": [{"name": "db.main", "access": "write"}]},
            ]})
        with self.assertRaisesRegex(wf.WorkflowError, "only after accepted dependency"):
            wf.normalize_spec({"nodes": [
                {"id": "w1", "role": "implement", "files": ["a.py"]},
                {"id": "review", "role": "review", "files": ["a.py"]},
            ]})
        created = self.create([
            {"id": "w1", "role": "implement", "files": ["a.py"],
             "resources": [{"name": "db.main", "access": "write"}]},
            {"id": "seed", "role": "mini", "files": ["c.py"],
             "resources": [{"name": "db.other", "access": "write"}]},
            {"id": "review", "role": "review", "files": ["a.py"], "depends_on": ["w1"]},
        ])
        ids = [node["id"] for node in created["nodes"]]
        self.assertIn("final-verify", ids)
        final = next(node for node in created["nodes"] if node["id"] == "final-verify")
        self.assertEqual(final["role"], "verify")
        self.assertTrue(final["final"])
        self.assertEqual(set(final["depends_on"]), {"w1", "seed", "review"})
        first = self.advance(created)
        launched = [row["node_id"] for row in first["launched"]]
        self.assertEqual(set(launched), {"w1", "seed"})
        self.assertNotIn("review", launched)
        self.assertNotIn("final-verify", launched)
        _, _, writer_job = self.node_job(created, "w1")
        self.accept_job(created, "w1", next_action="review")
        second = self.advance(created)
        self.assertEqual([row["node_id"] for row in second["launched"]], ["review"])
        held = json.loads(
            (self.repo / ".rig" / "reservations" / f"{self.jobs[writer_job]['reservation_id']}.json").read_text()
        )
        self.assertNotEqual(held.get("stage"), "released")
        self.assertFalse(held.get("slot_held"))
        self.accept_job(created, "seed")
        self.accept_job(created, "review")
        self.accept_job(created, "w1")
        third = self.advance(created)
        self.assertEqual([row["node_id"] for row in third["launched"]], ["final-verify"])
        verified = self.accept_job(created, "final-verify")
        self.assertEqual(verified["status"], "verified")
        self.assertTrue(verified["node_state"]["final-verify"]["accepted"])

    def test_cancellation_affects_only_workflow_attempts(self):
        created = self.create([
            {"id": "w1", "role": "implement", "files": ["a.py"]},
            {"id": "w2", "role": "mini", "files": ["b.py"]},
        ])
        other = self.repo / ".rig" / "jobs" / "unrelated"
        other.mkdir(parents=True)
        (other / "meta.json").write_text(json.dumps({
            "job_id": "unrelated", "status": "running", "worker": "grok", "role": "implement",
            "files": ["e.py"], "access": "write",
        }) + "\n")
        first = self.advance(created)
        self.assertTrue(first["launched"])
        cancelled = workflow.cancel(
            self.repo, created["workflow_id"], owner_token=created["owner_token"],
            owner_session=SESSION, rationale="stop workflow only",
        )
        self.assertIn(cancelled["status"], {"cancel-requested", "cancelled"})
        spec, state = wf.load_pair(self.repo, created["workflow_id"], required=True)
        for node_id, row in state["nodes"].items():
            if row.get("job_id"):
                self.assertTrue(
                    cancellation.requested(self.repo / ".rig" / "jobs" / row["job_id"]),
                    f"workflow job {node_id} was not asked to stop",
                )
            elif row.get("status") in {"pending", "ready", "blocked", "launching"}:
                self.assertEqual(row["status"], "cancelled")
        leftover = json.loads((other / "meta.json").read_text())
        self.assertEqual(leftover["status"], "running")
        self.assertFalse(cancellation.requested(other))

    def test_queue_claimed_spawned_done_and_cancelled(self):
        done_item = work_queue.add_item(self.repo, "verified path")
        done_wf = self.create([{"id": "w1", "role": "implement", "files": ["a.py"]}], queue_id=done_item["id"])
        self.assertEqual(self.queue_status(done_item["id"]), "claimed")
        self.assertEqual(
            json.loads((self.repo / ".rig" / "queue" / f"{done_item['id']}.json").read_text())["workflow_id"],
            done_wf["workflow_id"],
        )
        self.advance(done_wf)
        self.assertEqual(self.queue_status(done_item["id"]), "spawned")
        self.accept_job(done_wf, "w1")
        shown = workflow.show(self.repo, done_wf["workflow_id"])
        self.assertEqual(shown["status"], "verified")
        self.assertEqual(self.queue_status(done_item["id"]), "done")

        cancel_item = work_queue.add_item(self.repo, "cancel path")
        cancel_wf = self.create(
            [{"id": "w2", "role": "mini", "files": ["b.py"]}], queue_id=cancel_item["id"],
        )
        self.assertEqual(self.queue_status(cancel_item["id"]), "claimed")
        self.advance(cancel_wf)
        self.assertEqual(self.queue_status(cancel_item["id"]), "spawned")
        workflow.cancel(
            self.repo, cancel_wf["workflow_id"], owner_token=cancel_wf["owner_token"],
            owner_session=SESSION, rationale="stop queued workflow",
        )
        self.assertEqual(self.queue_status(cancel_item["id"]), "cancelled")
        spec, state = wf.load_pair(self.repo, cancel_wf["workflow_id"], required=True)
        with self.assertRaisesRegex(wf.WorkflowError, "never pending"):
            wf._bind_queue(self.repo, spec, state, "pending")
        self.assertEqual(self.queue_status(cancel_item["id"]), "cancelled")

    def test_final_acceptance_invalidated_after_later_file_change(self):
        created = self.create([{"id": "w1", "role": "implement", "files": ["a.py"]}])
        self.advance(created)
        accepted = self.accept_job(created, "w1")
        self.assertEqual(accepted["status"], "verified")
        self.assertTrue(accepted["node_state"]["w1"]["accepted"])
        (self.repo / "a.py").write_text("changed after acceptance\n")
        refreshed = wf.refresh(self.repo, created["workflow_id"])
        self.assertFalse(refreshed["node_state"]["w1"]["accepted"])
        self.assertEqual(refreshed["node_state"]["w1"]["status"], "completed-unverified")
        self.assertEqual(refreshed["status"], "completed-unverified")

    def test_parent_child_surface_visibility(self):
        created = self.create([{"id": "w1", "role": "implement", "files": ["a.py"]}])
        parent_names = [tool["name"] for tool in rig_mcp.listed_tools()]
        for name in WORKFLOW_TOOLS:
            self.assertIn(name, parent_names)
        self.assertNotIn("rig_job_coordination_request", parent_names)
        session = json.loads(rig_mcp.format_session(
            self.repo, "Report current work", "stay", as_json=True, compact=True, terminal_limit=10,
        ))
        self.assertTrue(any(row.get("workflow_id") == created["workflow_id"] for row in session.get("workflows") or []))
        listed = rig_mcp.call_tool("rig_workflows", {"repo": str(self.repo)})
        self.assertNotIn("isError", listed)
        self.assertIn(created["workflow_id"], listed["content"][0]["text"])
        denied_child = rig_mcp.call_tool(
            "rig_job_coordination_request",
            {"kind": "scope", "text": "parent must not request", "repo": str(self.repo)},
        )
        self.assertTrue(denied_child.get("isError"))
        self.assertIn("not a parent tool", denied_child["content"][0]["text"])

        self.advance(created)
        spec, state, job_id = self.node_job(created, "w1")
        job_dir = self.repo / ".rig" / "jobs" / job_id
        with mock.patch.dict(os.environ, {
            "RIG_JOB_ID": job_id, "RIG_JOB_DIR": str(job_dir), "RIG_REPO": str(self.repo),
        }):
            child_names = [tool["name"] for tool in rig_mcp.listed_tools()]
            for name in CHILD_TOOLS:
                self.assertIn(name, child_names)
            for name in WORKFLOW_TOOLS:
                self.assertNotIn(name, child_names)
            inbox = rig_mcp.call_tool("rig_job_inbox", {})
            self.assertNotIn("isError", inbox, inbox)
            hidden = rig_mcp.call_tool("rig_workflows", {})
            self.assertTrue(hidden.get("isError"))
            self.assertIn("not a child tool", hidden["content"][0]["text"])
            own = rig_mcp.call_tool("rig_job_show", {})
            self.assertNotIn("isError", own, own)
            self.assertIn(job_id, own["content"][0]["text"])
            requested = rig_mcp.call_tool(
                "rig_job_coordination_request",
                {"kind": "scope", "text": "need parent steer"},
            )
            self.assertNotIn("isError", requested, requested)
        pending = coordination.pending(self.repo, created["workflow_id"])
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["job_id"], job_id)


if __name__ == "__main__":
    unittest.main()
