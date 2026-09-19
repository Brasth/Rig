#!/usr/bin/env python3
"""Workflow spec, persistence, DAG, immutability, and derived status."""
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(ROOT / "scripts"))
import work_queue
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


class WorkflowState(unittest.TestCase):
    def setUp(self):
        self.temp, self.repo = _repo()
        self.addCleanup(self.temp.cleanup)
        self.env = mock.patch.dict(os.environ, {"RIG_OWNER_SESSION": "wf-tests"})
        self.env.start()
        self.addCleanup(self.env.stop)

    def create(self, nodes, **kwargs):
        return wf.create_workflow(self.repo, {"title": "t", "case": "c", "nodes": nodes, **kwargs},
                                  owner_session="wf-tests")

    def test_schema_unique_ids_dag_and_hash(self):
        with self.assertRaisesRegex(wf.WorkflowError, "unique"):
            wf.normalize_spec({"nodes": [
                {"id": "a", "role": "implement", "files": ["a.py"]},
                {"id": "a", "role": "mini", "files": ["b.py"]},
            ]})
        with self.assertRaisesRegex(wf.WorkflowError, "cycle"):
            wf.normalize_spec({"nodes": [
                {"id": "a", "role": "implement", "files": ["a.py"], "depends_on": ["b"]},
                {"id": "b", "role": "mini", "files": ["b.py"], "depends_on": ["a"]},
            ]})
        with self.assertRaisesRegex(wf.WorkflowError, "unknown dependency"):
            wf.normalize_spec({"nodes": [{"id": "a", "role": "implement", "files": ["a.py"], "depends_on": ["missing"]}]})
        spec = wf.normalize_spec({"nodes": [{"id": "one", "role": "implement", "files": ["a.py"]}]})
        self.assertEqual(spec["version"], 1)
        self.assertEqual(spec["spec_hash"], wf.spec_hash(spec))
        self.assertNotIn("owner_token", spec)

    def test_write_nodes_need_files_and_reject_overlap(self):
        with self.assertRaisesRegex(wf.WorkflowError, "concrete files"):
            wf.normalize_spec({"nodes": [{"id": "a", "role": "implement", "files": []}]})
        with self.assertRaisesRegex(wf.WorkflowError, "overlapping workflow writer"):
            wf.normalize_spec({"nodes": [
                {"id": "a", "role": "implement", "files": ["a.py"]},
                {"id": "b", "role": "mini", "files": ["a.py"]},
            ]})

    def test_max_nodes_default_and_final_verify_for_multiple_writers(self):
        nodes = [{"id": f"n{i}", "role": "implement", "files": [f"f{i}.py"]} for i in range(12)]
        with self.assertRaisesRegex(wf.WorkflowError, "max_nodes"):
            wf.normalize_spec({"nodes": nodes})
        created = self.create([
            {"id": "w1", "role": "implement", "files": ["a.py"]},
            {"id": "w2", "role": "mini", "files": ["b.py"]},
        ])
        ids = [node["id"] for node in created["nodes"]]
        self.assertIn("final-verify", ids)
        final = next(node for node in created["nodes"] if node["id"] == "final-verify")
        self.assertEqual(final["role"], "verify")
        self.assertTrue(final["final"])
        self.assertEqual(set(final["depends_on"]), {"w1", "w2"})

    def test_side_effects_require_final_verify(self):
        spec = wf.normalize_spec({"nodes": [
            {"id": "w1", "role": "implement", "files": ["a.py"], "effects": "local"},
        ]})
        self.assertTrue(any(node.get("final") for node in spec["nodes"]))

    def test_caller_supplied_final_verify_cannot_bypass_policy(self):
        with self.assertRaisesRegex(wf.WorkflowError, "role verify"):
            wf.normalize_spec({"nodes": [
                {"id": "w1", "role": "implement", "files": ["a.py"]},
                {"id": "w2", "role": "mini", "files": ["b.py"]},
                {"id": "final-verify", "role": "implement", "files": ["c.py"], "final": True},
            ]})
        spec = wf.normalize_spec({"nodes": [
            {"id": "w1", "role": "implement", "files": ["a.py"],
             "resources": [{"name": "db.main", "access": "write"}]},
            {"id": "seed", "role": "bulk", "files": ["b.py"]},
            {"id": "rev", "role": "review", "files": ["a.py"], "depends_on": ["w1"]},
            {"id": "opt", "role": "mini", "files": ["c.py"], "required": False},
            {"id": "sneaky", "role": "verify", "files": ["unrelated.py"], "depends_on": [],
             "required": False, "kind": "final-verify"},
        ]})
        final = next(node for node in spec["nodes"] if node.get("final") or node.get("kind") == "final-verify")
        self.assertEqual(final["role"], "verify")
        self.assertTrue(final["required"])
        self.assertTrue(final["final"])
        self.assertEqual(final["kind"], "final-verify")
        self.assertEqual(set(final["depends_on"]), {"w1", "seed", "rev"})
        self.assertTrue({"a.py", "b.py"}.issubset(set(final["files"])))
        self.assertIn({"name": "db.main", "access": "read"}, final["resources"])
        self.assertNotIn("opt", final["depends_on"])
        state = wf._new_state(spec, owner={})
        self.assertFalse(wf.node_ready(spec, state, final))
        state["nodes"]["w1"].update(status="accepted", accepted=True)
        state["nodes"]["seed"].update(status="accepted", accepted=True)
        self.assertFalse(wf.node_ready(spec, state, final))
        state["nodes"]["rev"].update(status="accepted", accepted=True)
        self.assertTrue(wf.node_ready(spec, state, final))

    def test_persistence_credentials_events_and_no_public_token(self):
        created = self.create([{"id": "w1", "role": "implement", "files": ["a.py"]}])
        folder = wf.workflow_dir(self.repo, created["workflow_id"])
        self.assertTrue((folder / "spec.json").is_file())
        self.assertTrue((folder / "state.json").is_file())
        creds = folder / "owner-credentials.json"
        self.assertEqual(stat.S_IMODE(creds.stat().st_mode), 0o600)
        public = json.dumps(wf.list_workflows(self.repo))
        self.assertNotIn(created["owner_token"], public)
        self.assertNotIn("owner_token", json.loads((folder / "state.json").read_text()))
        events = wf.list_events(self.repo, created["workflow_id"])
        self.assertEqual(events[0]["kind"], "created")
        self.assertEqual(events[0]["seq"], 1)
        listed = wf.list_workflows(self.repo)
        self.assertEqual(listed[0]["status"], "planned")
        self.assertIn("spec_hash", listed[0])

    def test_legacy_readers_tolerate_missing_fields(self):
        created = self.create([{"id": "w1", "role": "implement", "files": ["a.py"]}])
        folder = wf.workflow_dir(self.repo, created["workflow_id"])
        spec = json.loads((folder / "spec.json").read_text())
        spec.pop("version")
        spec.pop("spec_hash")
        (folder / "spec.json").write_text(json.dumps(spec))
        loaded = wf.load_spec(self.repo, created["workflow_id"])
        self.assertEqual(loaded["version"], 1)
        self.assertTrue(loaded["spec_hash"])

    def test_shared_context_freezes_and_extension_is_append_only(self):
        created = self.create([
            {"id": "w1", "role": "implement", "files": ["a.py"]},
            {"id": "w2", "role": "mini", "files": ["b.py"], "depends_on": ["w1"]},
        ], shared_context="ctx-one")
        token, wid = created["owner_token"], created["workflow_id"]
        spec, state = wf.load_pair(self.repo, wid, required=True)
        wf.mark_launched(state, "w1")
        wf.commit_launch(state, spec)
        wf.save_state(self.repo, state)
        with self.assertRaisesRegex(wf.WorkflowError, "frozen"):
            wf.extend_workflow(self.repo, wid, {
                "shared_context": "ctx-two",
                "nodes": [{"id": "ctx-change", "role": "review", "files": ["c.py"], "depends_on": ["w1"]}],
            }, owner_token=token)
        spec, state = wf.load_pair(self.repo, wid, required=True)
        with self.assertRaisesRegex(wf.WorkflowError, "cannot alter launched"):
            wf.extend_workflow(self.repo, wid, [
                {"id": "w1", "role": "hard", "files": ["a.py"]},
            ], owner_token=token)
        extended = wf.extend_workflow(self.repo, wid, [
            {"id": "w3", "role": "review", "files": ["a.py"], "depends_on": ["w1"]},
        ], owner_token=token)
        ids = [node["id"] for node in extended["nodes"]]
        self.assertIn("w3", ids)
        self.assertTrue(extended["shared_context_frozen"])
        wf.mark_launched(state, "final-verify")
        spec, state = wf.load_pair(self.repo, wid, required=True)
        state["nodes"]["final-verify"]["launched"] = True
        wf.save_state(self.repo, state)
        with self.assertRaisesRegex(wf.WorkflowError, "final verify"):
            wf.extend_workflow(self.repo, wid, [
                {"id": "late", "role": "review", "files": ["b.py"], "depends_on": ["w2"]},
            ], owner_token=token)

    def test_readiness_uses_current_parent_acceptance(self):
        spec = wf.normalize_spec({"nodes": [
            {"id": "w1", "role": "implement", "files": ["a.py"]},
            {"id": "v1", "role": "verify", "files": ["a.py"], "depends_on": ["w1"]},
        ]})
        state = wf._new_state(spec, owner={})
        self.assertTrue(wf.node_ready(spec, state, spec["nodes"][0]))
        self.assertFalse(wf.node_ready(spec, state, spec["nodes"][1]))
        state["nodes"]["w1"].update(status="completed-unverified", launched=True, ran=True)
        self.assertFalse(wf.node_ready(spec, state, spec["nodes"][1]))
        state["nodes"]["w1"].update(status="accepted", accepted=True)
        self.assertTrue(wf.node_ready(spec, state, spec["nodes"][1]))

    def test_queue_claimed_on_create_and_done_only_verified(self):
        item = work_queue.add_item(self.repo, "parked workflow")
        created = self.create([{"id": "w1", "role": "implement", "files": ["a.py"]}], queue_id=item["id"])
        queued = json.loads((self.repo / ".rig" / "queue" / f"{item['id']}.json").read_text())
        self.assertEqual(queued["status"], "claimed")
        self.assertEqual(queued["workflow_id"], created["workflow_id"])
        spec, state = wf.load_pair(self.repo, created["workflow_id"], required=True)
        state["nodes"]["w1"].update(status="accepted", accepted=True, launched=True, ran=True)
        wf.refresh_locked(self.repo, spec, state)
        queued = json.loads((self.repo / ".rig" / "queue" / f"{item['id']}.json").read_text())
        self.assertEqual(queued["status"], "done")
        self.assertEqual(state["status"], "verified")

    def test_acceptance_invalidates_when_snapshot_changes(self):
        created = self.create([{"id": "w1", "role": "implement", "files": ["a.py"]}])
        spec, state = wf.load_pair(self.repo, created["workflow_id"], required=True)
        job_id = "job-w1"
        folder = self.repo / ".rig" / "jobs" / job_id
        folder.mkdir(parents=True)
        import change_evidence
        snap = change_evidence.snapshot(self.repo, ["a.py"])["snapshot_id"]
        (folder / "meta.json").write_text(json.dumps({
            "job_id": job_id, "status": "ok", "role": "implement", "files": ["a.py"],
        }) + "\n")
        (folder / "verification.json").write_text(json.dumps({
            "acceptance": "accepted", "snapshot_id": snap, "next": "complete",
        }) + "\n")
        state["nodes"]["w1"].update(
            job_id=job_id, launched=True, ran=True, accepted=True, status="accepted",
            acceptance_snapshot=snap,
        )
        wf.refresh_locked(self.repo, spec, state)
        self.assertTrue(state["nodes"]["w1"]["accepted"])
        self.assertEqual(state["status"], "verified")
        (self.repo / "a.py").write_text("changed\n")
        wf.refresh_locked(self.repo, spec, state)
        self.assertFalse(state["nodes"]["w1"]["accepted"])
        self.assertEqual(state["nodes"]["w1"]["status"], "completed-unverified")
        self.assertEqual(state["status"], "completed-unverified")

    def test_acceptance_freshness_fails_closed_on_snapshot_error(self):
        created = self.create([{"id": "w1", "role": "implement", "files": ["a.py"]}])
        spec, state = wf.load_pair(self.repo, created["workflow_id"], required=True)
        job_id = "job-freshness"
        folder = self.repo / ".rig" / "jobs" / job_id
        folder.mkdir(parents=True)
        import change_evidence
        snap = change_evidence.snapshot(self.repo, ["a.py"])["snapshot_id"]
        (folder / "meta.json").write_text(json.dumps({
            "job_id": job_id, "status": "ok", "role": "implement", "files": ["a.py"],
        }) + "\n")
        (folder / "verification.json").write_text(json.dumps({
            "acceptance": "accepted", "snapshot_id": snap, "next": "complete",
        }) + "\n")
        state["nodes"]["w1"].update(
            job_id=job_id, launched=True, ran=True, accepted=True, status="accepted",
            acceptance_snapshot=snap,
        )
        wf.refresh_locked(self.repo, spec, state)
        self.assertTrue(state["nodes"]["w1"]["accepted"])
        with mock.patch("change_evidence.snapshot", side_effect=OSError("snapshot unavailable")):
            wf.refresh_locked(self.repo, spec, state)
        self.assertFalse(state["nodes"]["w1"]["accepted"])
        self.assertEqual(state["nodes"]["w1"]["status"], "completed-unverified")
        self.assertEqual(state["nodes"]["w1"]["acceptance_snapshot"], "")
        self.assertNotEqual(state["status"], "verified")

    def test_parent_rejected_ok_job_fails_and_blocks_dependent(self):
        created = self.create([
            {"id": "w1", "role": "implement", "files": ["a.py"]},
            {"id": "v1", "role": "verify", "files": ["a.py"], "depends_on": ["w1"]},
        ])
        spec, state = wf.load_pair(self.repo, created["workflow_id"], required=True)
        job_id = "job-rejected"
        folder = self.repo / ".rig" / "jobs" / job_id
        folder.mkdir(parents=True)
        (folder / "meta.json").write_text(json.dumps({
            "job_id": job_id, "status": "ok", "role": "implement", "files": ["a.py"],
        }) + "\n")
        (folder / "verification.json").write_text(json.dumps({
            "acceptance": "rejected", "state": "failed", "reason": "parent_rejected",
            "rationale": "scoped output is wrong",
        }) + "\n")
        state["nodes"]["w1"].update(
            job_id=job_id, launched=True, ran=True, accepted=False, status="completed-unverified",
        )
        wf.refresh_locked(self.repo, spec, state)
        self.assertEqual(state["nodes"]["w1"]["status"], "failed")
        self.assertFalse(state["nodes"]["w1"]["accepted"])
        self.assertIn("parent_rejected", state["nodes"]["w1"]["blocker"])
        self.assertEqual(state["failure"]["node_id"], "w1")
        self.assertIn("parent_rejected", state["failure"]["reason"])
        self.assertEqual(state["status"], "blocked")
        v1 = next(node for node in spec["nodes"] if node["id"] == "v1")
        self.assertFalse(wf.node_ready(spec, state, v1))
        self.assertFalse(state["nodes"]["v1"]["launched"])
        self.assertEqual(state["nodes"]["v1"]["status"], "pending")

    def test_failed_nodes_are_not_silently_relaunched(self):
        spec = wf.normalize_spec({"nodes": [{"id": "w1", "role": "implement", "files": ["a.py"]}]})
        state = wf._new_state(spec, owner={})
        state["nodes"]["w1"].update(status="failed", ran=False)
        self.assertFalse(wf.node_ready(spec, state, spec["nodes"][0]))

    def test_verified_waits_for_live_accepted_nodes(self):
        created = self.create([{"id": "w1", "role": "implement", "files": ["a.py"]}])
        spec, state = wf.load_pair(self.repo, created["workflow_id"], required=True)
        job_id = "job-live"
        folder = self.repo / ".rig" / "jobs" / job_id
        folder.mkdir(parents=True)
        import change_evidence
        snap = change_evidence.snapshot(self.repo, ["a.py"])["snapshot_id"]
        (folder / "meta.json").write_text(json.dumps({
            "job_id": job_id, "status": "running", "role": "implement", "files": ["a.py"],
        }) + "\n")
        (folder / "verification.json").write_text(json.dumps({
            "acceptance": "accepted", "snapshot_id": snap, "next": "complete",
        }) + "\n")
        state["nodes"]["w1"].update(
            job_id=job_id, launched=True, ran=True, accepted=True, status="accepted",
            acceptance_snapshot=snap,
        )
        wf.refresh_locked(self.repo, spec, state)
        self.assertTrue(state["nodes"]["w1"]["accepted"])
        self.assertEqual(state["status"], "running")

    def test_cancelled_queue_never_pending(self):
        item = work_queue.add_item(self.repo, "parked")
        created = self.create([{"id": "w1", "role": "implement", "files": ["a.py"]}], queue_id=item["id"])
        spec, state = wf.load_pair(self.repo, created["workflow_id"], required=True)
        wf._bind_queue(self.repo, spec, state, "cancelled")
        with self.assertRaisesRegex(wf.WorkflowError, "never pending"):
            wf._bind_queue(self.repo, spec, state, "pending")
        queued = json.loads((self.repo / ".rig" / "queue" / f"{item['id']}.json").read_text())
        self.assertEqual(queued["status"], "cancelled")

    def test_unconfirmed_job_is_attention_not_running(self):
        created = self.create([{"id": "w1", "role": "implement", "files": ["a.py"]}])
        spec, state = wf.load_pair(self.repo, created["workflow_id"], required=True)
        job_id = "job-unconfirmed"
        folder = self.repo / ".rig" / "jobs" / job_id
        folder.mkdir(parents=True)
        (folder / "meta.json").write_text(json.dumps({
            "job_id": job_id, "status": "unconfirmed", "role": "implement", "files": ["a.py"],
        }) + "\n")
        state["nodes"]["w1"].update(job_id=job_id, launched=True, ran=True, status="running")
        wf.refresh_locked(self.repo, spec, state)
        self.assertEqual(state["nodes"]["w1"]["status"], "unconfirmed")
        self.assertEqual(state["status"], "attention")
        self.assertNotEqual(state["status"], "running")
        self.assertIsNone(state.get("failure") or None)
        action = wf.next_parent_action(spec, state)
        self.assertEqual(action.get("kind"), "reconcile")
        self.assertEqual(action.get("node_id"), "w1")
        self.assertFalse(wf.node_ready(spec, state, spec["nodes"][0]))


if __name__ == "__main__":
    unittest.main()
