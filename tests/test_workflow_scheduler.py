#!/usr/bin/env python3
"""Workflow scheduler: order, barriers, parent_writes, retry, cancel, approval."""
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(ROOT / "scripts"))
import admission
import ask as rig_ask
import work_queue
import workflow_scheduler as sched
import workflow_state as wf


def _repo(cap=3):
    temp = tempfile.TemporaryDirectory()
    repo = Path(temp.name).resolve()
    (repo / ".git").mkdir()
    (repo / ".rig").mkdir()
    for name in ("a.py", "b.py", "c.py", "d.py"):
        (repo / name).write_text(name + "\n")
    (repo / ".rig" / "harness.toml").write_text(
        'parent = "codex"\n[workers]\ngrok = true\nclaude = true\ncodex = true\n'
        "[orchestration]\nmode = \"adaptive\"\nmax_nodes = 12\n"
        f"[queue]\nmax_running = {cap}\n"
    )
    return temp, repo


class WorkflowScheduler(unittest.TestCase):
    def setUp(self):
        self.temp, self.repo = _repo()
        self.addCleanup(self.temp.cleanup)
        self.env = mock.patch.dict(os.environ, {"RIG_OWNER_SESSION": "sched-tests"})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.launches = []

    def create(self, nodes, **kwargs):
        return wf.create_workflow(self.repo, {"title": "t", "case": "c", "nodes": nodes, **kwargs},
                                  owner_session="sched-tests")

    def pick(self, parent_writes=False, worker="grok"):
        def _pick(node, spec, state, exclude=""):
            return {
                "worker": "parent" if parent_writes else worker,
                "spawn": "native" if parent_writes else "run-worker",
                "parent_writes": parent_writes,
                "model": "grok-4.6",
                "effort": "high",
                "routing": {"policy_mode": "smart", "selected_profile": {"id": "grok-4.6-high"}},
            }
        return _pick

    def launch(self, fail=None):
        def _launch(repo, *, node, spec, state, choice, **kwargs):
            if fail and fail(node):
                raise sched.SchedulerError("could not start wrapper")
            job_id = f"job-{node['id']}-{len(self.launches)}"
            reservation_id = f"res-{job_id}"
            attempt_id = f"att-{job_id}"
            folder = Path(repo) / ".rig" / "jobs" / job_id
            folder.mkdir(parents=True, exist_ok=True)
            meta = {
                "job_id": job_id, "status": "running", "role": node["role"],
                "files": node.get("files") or [], "worker": choice.get("worker") or "grok",
                "reservation_id": reservation_id, "attempt_id": attempt_id,
            }
            (folder / "meta.json").write_text(json.dumps(meta) + "\n")
            self.launches.append(node["id"])
            kind = "parent_writes" if choice.get("parent_writes") else "wrapper"
            return {"kind": kind, "job": {"job_id": job_id, "reservation_id": reservation_id,
                                          "attempt_id": attempt_id},
                    "workflow_attempt": 1}
        return _launch

    def advance(self, created, **kwargs):
        return sched.advance(
            self.repo, created["workflow_id"], owner_session="sched-tests",
            owner_token=created["owner_token"], pick_fn=kwargs.get("pick_fn", self.pick()),
            launch_fn=kwargs.get("launch_fn", self.launch()),
        )

    def test_order_priority_depth_declaration(self):
        created = self.create([
            {"id": "late", "role": "implement", "files": ["a.py"], "priority": 0},
            {"id": "deep", "role": "mini", "files": ["b.py"], "priority": 0},
            {"id": "child", "role": "review", "files": ["b.py"], "depends_on": ["deep"]},
            {"id": "hot", "role": "implement", "files": ["c.py"], "priority": 5},
        ])
        result = self.advance(created)
        self.assertEqual([row["node_id"] for row in result["launched"]], ["hot", "deep", "late"])

    def test_capacity_and_partial_launches(self):
        self.temp.cleanup()
        self.temp, self.repo = _repo(cap=1)
        self.addCleanup(self.temp.cleanup)
        created = self.create([
            {"id": "a", "role": "implement", "files": ["a.py"]},
            {"id": "b", "role": "mini", "files": ["b.py"]},
        ])
        # Fake launch does not consume admission slots; use conflict via real reserve.
        admission.reserve(self.repo, job_id="held", worker="grok", files=["c.py"],
                          owner=admission.caller_owner("parent", owner_session="sched-tests"))
        result = self.advance(created)
        self.assertTrue(result["skipped"])
        self.assertTrue(any("live+reserved" in row["reason"] or "slot" in row["reason"] for row in result["skipped"]))

    def test_dependency_requires_acceptance_not_completion(self):
        created = self.create([
            {"id": "w1", "role": "implement", "files": ["a.py"]},
            {"id": "v1", "role": "verify", "files": ["a.py"], "depends_on": ["w1"]},
        ])
        first = self.advance(created)
        self.assertEqual([row["node_id"] for row in first["launched"]], ["w1"])
        job = self.repo / ".rig" / "jobs" / first["launched"][0]["job_id"]
        meta = json.loads((job / "meta.json").read_text())
        meta["status"] = "ok"
        (job / "meta.json").write_text(json.dumps(meta) + "\n")
        second = self.advance(created)
        self.assertEqual(second["launched"], [])
        import change_evidence
        snap = change_evidence.snapshot(self.repo, ["a.py"])["snapshot_id"]
        (job / "verification.json").write_text(json.dumps({
            "acceptance": "accepted", "snapshot_id": snap, "next": "complete",
        }) + "\n")
        spec, state = wf.load_pair(self.repo, created["workflow_id"], required=True)
        state["nodes"]["w1"].update(accepted=True, status="accepted",
                                    acceptance_snapshot=snap)
        wf.save_state(self.repo, state)
        third = self.advance(created)
        self.assertEqual([row["node_id"] for row in third["launched"]], ["v1"])

    def test_rejected_verification_isolates_and_does_not_launch_dependent(self):
        created = self.create([
            {"id": "w1", "role": "implement", "files": ["a.py"]},
            {"id": "v1", "role": "verify", "files": ["a.py"], "depends_on": ["w1"]},
        ])
        first = self.advance(created)
        self.assertEqual([row["node_id"] for row in first["launched"]], ["w1"])
        job = self.repo / ".rig" / "jobs" / first["launched"][0]["job_id"]
        meta = json.loads((job / "meta.json").read_text())
        meta["status"] = "ok"
        (job / "meta.json").write_text(json.dumps(meta) + "\n")
        (job / "verification.json").write_text(json.dumps({
            "acceptance": "rejected", "state": "failed", "reason": "parent_rejected",
            "rationale": "scoped output is wrong",
        }) + "\n")
        second = self.advance(created)
        self.assertEqual(second["launched"], [])
        self.assertEqual(second["reason"], "unresolved failure")
        self.assertEqual(second["node_state"]["w1"]["status"], "failed")
        self.assertFalse(second["node_state"]["w1"]["accepted"])
        self.assertIn("parent_rejected", second["failure"]["reason"])
        self.assertEqual(second["status"], "blocked")
        self.assertFalse(second["node_state"]["v1"]["launched"])
        self.assertEqual(second["node_state"]["v1"]["status"], "pending")

    def test_repo_ask_coordination_and_failure_barriers(self):
        created = self.create([{"id": "w1", "role": "implement", "files": ["a.py"]}])
        ask_job = self.repo / ".rig" / "jobs" / "other-ask"
        ask_job.mkdir(parents=True)
        (ask_job / "meta.json").write_text(json.dumps({
            "job_id": "other-ask", "status": "running", "worker": "grok", "role": "implement",
        }) + "\n")
        rig_ask.write_ask(ask_job, "Bash", {"command": "ls"}, preview_text="ls")
        blocked = self.advance(created)
        self.assertEqual(blocked["reason"], "repo ASK")
        (ask_job / "ask.json").unlink()
        spec, state = wf.load_pair(self.repo, created["workflow_id"], required=True)
        state["failure"] = {"node_id": "w1", "reason": "boom"}
        wf.save_state(self.repo, state)
        failed = self.advance(created)
        self.assertEqual(failed["reason"], "unresolved failure")
        state["failure"] = None
        state["coordination"] = [{"id": "c1", "status": "pending", "kind": "scope"}]
        wf.save_state(self.repo, state)
        coord = self.advance(created)
        self.assertEqual(coord["reason"], "coordination")

    def test_parent_writes_returns_one_action(self):
        created = self.create([
            {"id": "a", "role": "implement", "files": ["a.py"]},
            {"id": "b", "role": "mini", "files": ["b.py"]},
        ])
        result = self.advance(created, pick_fn=self.pick(parent_writes=True))
        self.assertEqual(len(result["launched"]), 1)
        self.assertTrue(result["parent_action"]["kind"] == "parent_writes")
        self.assertEqual(result["parent_action"]["node_id"], result["launched"][0]["node_id"])

    def test_concurrent_advance_does_not_duplicate(self):
        created = self.create([{"id": "w1", "role": "implement", "files": ["a.py"]}])
        started = threading.Event()
        release = threading.Event()
        seen = []
        guard = threading.Lock()

        def _launch(repo, *, node, spec, state, choice, **kwargs):
            with guard:
                seen.append(node["id"])
            started.set()
            self.assertTrue(release.wait(timeout=5), "in-flight launch was not released")
            job_id = f"job-{node['id']}-{len(seen)}"
            folder = Path(repo) / ".rig" / "jobs" / job_id
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "meta.json").write_text(json.dumps({
                "job_id": job_id, "status": "running",
                "reservation_id": f"res-{job_id}", "attempt_id": f"att-{job_id}",
            }) + "\n")
            return {"kind": "wrapper", "job": {
                "job_id": job_id, "reservation_id": f"res-{job_id}", "attempt_id": f"att-{job_id}",
            }}

        results = []
        errors = []

        def worker():
            try:
                results.append(self.advance(created, launch_fn=_launch))
            except Exception as error:
                errors.append(error)

        first = threading.Thread(target=worker)
        second = threading.Thread(target=worker)
        first.start()
        self.assertTrue(started.wait(timeout=5), "first launch never started")
        second.start()
        second.join(timeout=5)
        self.assertFalse(second.is_alive(), "second advance held the admission lock across launch")
        release.set()
        first.join(timeout=5)
        self.assertFalse(first.is_alive())
        self.assertEqual(errors, [])
        launched = [row["node_id"] for item in results for row in item.get("launched") or []]
        self.assertEqual(launched.count("w1"), 1)
        self.assertEqual(seen, ["w1"])

    def test_approval_invalidation_and_gated_effects(self):
        created = self.create([
            {"id": "w1", "role": "implement", "files": ["a.py"], "effects": "production"},
        ])
        pending = self.advance(created)
        self.assertEqual(pending["launched"], [])
        self.assertEqual(pending["next_parent_action"]["kind"], "approve")
        with self.assertRaisesRegex(wf.WorkflowError, "rationale"):
            sched.approve_node(self.repo, created["workflow_id"], "w1", owner_token=created["owner_token"])
        sched.approve_node(self.repo, created["workflow_id"], "w1", owner_token=created["owner_token"],
                           owner_session="sched-tests", rationale="ship it")
        launched = self.advance(created)
        self.assertEqual([row["node_id"] for row in launched["launched"]], ["w1"])
        spec, state = wf.load_pair(self.repo, created["workflow_id"], required=True)
        state["nodes"]["w1"]["launched"] = False
        state["nodes"]["w1"]["ran"] = False
        state["nodes"]["w1"]["job_id"] = ""
        state["nodes"]["w1"]["status"] = "pending"
        wf.save_state(self.repo, state)
        wf.extend_workflow(self.repo, created["workflow_id"], [
            {"id": "notes", "role": "review", "files": ["a.py"], "depends_on": ["w1"]},
        ], owner_token=created["owner_token"])
        again = self.advance(created)
        self.assertEqual(again["launched"], [])
        self.assertEqual(again["next_parent_action"]["kind"], "approve")

    def test_retry_skip_fail_and_accepted_not_retried(self):
        created = self.create([
            {"id": "w1", "role": "implement", "files": ["a.py"]},
            {"id": "opt", "role": "mini", "files": ["b.py"], "required": False},
        ])
        self.advance(created)
        spec, state = wf.load_pair(self.repo, created["workflow_id"], required=True)
        state["nodes"]["w1"].update(status="failed", ran=True, launched=True)
        state["failure"] = {"node_id": "w1", "reason": "boom"}
        wf.save_state(self.repo, state)
        with self.assertRaisesRegex(wf.WorkflowError, "accepted"):
            state["nodes"]["w1"]["accepted"] = True
            wf.save_state(self.repo, state)
            sched.resolve_node(self.repo, created["workflow_id"], "w1", action="retry",
                               owner_token=created["owner_token"])
        spec, state = wf.load_pair(self.repo, created["workflow_id"], required=True)
        state["nodes"]["w1"]["accepted"] = False
        state["nodes"]["w1"].update(status="running", ran=True, launched=True)
        wf.save_state(self.repo, state)
        with self.assertRaisesRegex(wf.WorkflowError, "stopped/released"):
            sched.resolve_node(self.repo, created["workflow_id"], "w1", action="retry",
                               owner_token=created["owner_token"])
        job_id = state["nodes"]["w1"]["job_id"]
        job_dir = self.repo / ".rig" / "jobs" / job_id
        meta = json.loads((job_dir / "meta.json").read_text())
        meta["status"] = "fail"
        (job_dir / "meta.json").write_text(json.dumps(meta) + "\n")
        state["nodes"]["w1"].update(status="failed", ran=True)
        state["failure"] = {"node_id": "w1"}
        wf.save_state(self.repo, state)
        retried = sched.resolve_node(self.repo, created["workflow_id"], "w1", action="retry",
                                     owner_token=created["owner_token"])
        self.assertEqual(retried["node_state"]["w1"]["status"], "pending")
        with self.assertRaisesRegex(wf.WorkflowError, "rationale"):
            sched.resolve_node(self.repo, created["workflow_id"], "opt", action="skip",
                               owner_token=created["owner_token"])
        skipped = sched.resolve_node(self.repo, created["workflow_id"], "opt", action="skip",
                                     rationale="not needed", owner_token=created["owner_token"])
        self.assertEqual(skipped["node_state"]["opt"]["status"], "skipped")
        failed = sched.resolve_node(self.repo, created["workflow_id"], "w1", action="fail",
                                    rationale="give up", owner_token=created["owner_token"])
        self.assertEqual(failed["status"], "failed")

    def test_cancel_isolates_unstarted_and_active_workflow_jobs(self):
        created = self.create([
            {"id": "w1", "role": "implement", "files": ["a.py"]},
            {"id": "w2", "role": "mini", "files": ["b.py"]},
        ])
        other = self.repo / ".rig" / "jobs" / "unrelated"
        other.mkdir(parents=True)
        (other / "meta.json").write_text(json.dumps({
            "job_id": "unrelated", "status": "running", "worker": "grok",
        }) + "\n")
        first = self.advance(created)
        cancelled = sched.cancel_workflow(self.repo, created["workflow_id"],
                                          owner_token=created["owner_token"], rationale="stop")
        self.assertIn(cancelled["status"], {"cancel-requested", "cancelled"})
        spec, state = wf.load_pair(self.repo, created["workflow_id"], required=True)
        unstarted = [nid for nid, row in state["nodes"].items() if not row.get("job_id")]
        for nid in unstarted:
            self.assertEqual(state["nodes"][nid]["status"], "cancelled")
        leftover = json.loads((other / "meta.json").read_text())
        self.assertEqual(leftover["status"], "running")
        self.assertFalse((other / "cancel.json").exists())

    def test_concurrent_advance_does_not_hold_lock_across_pick(self):
        created = self.create([{"id": "w1", "role": "implement", "files": ["a.py"]}])
        started = threading.Event()
        release = threading.Event()

        def _pick(node, spec, state, exclude=""):
            started.set()
            self.assertTrue(release.wait(timeout=5), "in-flight pick was not released")
            return self.pick()(node, spec, state, exclude)

        results, errors = [], []

        def worker():
            try:
                results.append(self.advance(created, pick_fn=_pick))
            except Exception as error:
                errors.append(error)

        first = threading.Thread(target=worker)
        second = threading.Thread(target=worker)
        first.start()
        self.assertTrue(started.wait(timeout=5), "first pick never started")
        second.start()
        second.join(timeout=5)
        self.assertFalse(second.is_alive(), "second advance held the admission lock across pick")
        release.set()
        first.join(timeout=5)
        self.assertFalse(first.is_alive())
        self.assertEqual(errors, [])
        launched = [row["node_id"] for item in results for row in item.get("launched") or []]
        self.assertEqual(launched.count("w1"), 1)

    def test_spawn_never_started_retries_once_then_isolates(self):
        created = self.create([
            {"id": "w1", "role": "implement", "files": ["a.py"]},
            {"id": "w2", "role": "mini", "files": ["b.py"]},
        ])
        calls = {"n": 0}

        def _launch(repo, *, node, spec, state, choice, **kwargs):
            calls["n"] += 1
            if calls["n"] > 5:
                raise AssertionError("advance retried launch indefinitely")
            raise sched.SchedulerError("could not start wrapper")

        result = self.advance(created, launch_fn=_launch)
        self.assertEqual(calls["n"], 2)
        self.assertEqual(result["reason"], "unresolved failure")
        self.assertEqual(result["node_state"]["w1"]["status"], "failed")
        self.assertEqual(result["node_state"]["w1"]["exclude"], ["grok"])
        self.assertFalse(result["launched"])
        self.assertEqual(result["node_state"]["w2"]["status"], "pending")

    def test_launch_error_does_not_retry_indefinitely(self):
        created = self.create([{"id": "w1", "role": "implement", "files": ["a.py"]}])
        calls = {"n": 0}

        def _launch(repo, *, node, spec, state, choice, **kwargs):
            calls["n"] += 1
            if calls["n"] > 5:
                raise AssertionError("advance retried launch indefinitely")
            raise sched.SchedulerError("admission refused: boom")

        result = self.advance(created, launch_fn=_launch)
        self.assertEqual(calls["n"], 1)
        self.assertEqual(result["reason"], "unresolved failure")
        self.assertEqual(result["node_state"]["w1"]["status"], "failed")

    def test_inflight_launch_respects_cancel(self):
        created = self.create([{"id": "w1", "role": "implement", "files": ["a.py"]}])
        started = threading.Event()
        release = threading.Event()

        def _launch(repo, *, node, spec, state, choice, **kwargs):
            started.set()
            self.assertTrue(release.wait(timeout=5), "in-flight launch was not released")
            job_id = "job-cancel-race"
            folder = Path(repo) / ".rig" / "jobs" / job_id
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "meta.json").write_text(json.dumps({
                "job_id": job_id, "status": "running",
                "reservation_id": "res-cancel", "attempt_id": "att-cancel",
            }) + "\n")
            return {"kind": "wrapper", "job": {
                "job_id": job_id, "reservation_id": "res-cancel", "attempt_id": "att-cancel",
            }}

        errors = []
        result_holder = {}

        def worker():
            try:
                result_holder["advance"] = self.advance(created, launch_fn=_launch)
            except Exception as error:
                errors.append(error)

        thread = threading.Thread(target=worker)
        thread.start()
        self.assertTrue(started.wait(timeout=5), "launch never started")
        cancelled = sched.cancel_workflow(
            self.repo, created["workflow_id"], owner_token=created["owner_token"], rationale="stop",
        )
        self.assertIn(cancelled["status"], {"cancel-requested", "cancelled"})
        release.set()
        thread.join(timeout=5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        spec, state = wf.load_pair(self.repo, created["workflow_id"], required=True)
        self.assertTrue(state.get("cancel_requested"))
        self.assertIn(state["status"], {"cancel-requested", "cancelled"})
        job_id = state["nodes"]["w1"].get("job_id") or "job-cancel-race"
        job_dir = self.repo / ".rig" / "jobs" / job_id
        import cancellation
        if job_id:
            self.assertTrue(cancellation.requested(job_dir), "in-flight job was not asked to stop")
        if state["nodes"]["w1"]["status"] == "running":
            self.assertEqual(state["status"], "cancel-requested")
        else:
            self.assertEqual(state["nodes"]["w1"]["status"], "cancelled")

    def _writer_job(self, job_id, *, model="grok-4.6", model_source="selected", provider="xai",
                    provider_source="selected", inferred=False, worker="grok"):
        folder = self.repo / ".rig" / "jobs" / job_id
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "meta.json").write_text(json.dumps({
            "job_id": job_id, "status": "ok", "role": "implement", "files": ["a.py"],
            "worker": worker, "model": model, "model_source": model_source,
            "model_inferred": inferred, "provider": provider, "provider_source": provider_source,
        }) + "\n")
        (folder / "routing.json").write_text(json.dumps({
            "schema_version": 1, "attempt_id": "att-writer",
            "written_at": "2026-01-01T00:00:00Z",
            "routing": {"selected_profile": {"model": model, "provider": provider}},
        }) + "\n")
        return folder

    def test_review_launch_propagates_independent_writer_handoff(self):
        created = self.create([
            {"id": "w1", "role": "implement", "files": ["a.py"]},
            {"id": "r1", "role": "review", "files": ["a.py"], "depends_on": ["w1"]},
        ])
        spec, state = wf.load_pair(self.repo, created["workflow_id"], required=True)
        self._writer_job("writer-a")
        state["nodes"]["w1"].update(
            job_id="writer-a", accepted=True, status="accepted",
            acceptance_snapshot="snap-a", reservation_id="res-writer-a",
        )
        wf.save_state(self.repo, state)
        review = next(node for node in spec["nodes"] if node["id"] == "r1")
        import jobs as rig_jobs
        import worker_launch
        native = mock.Mock(return_value={
            "job_id": "rev-native", "reservation_id": "res-n", "attempt_id": "att-n",
        })
        wrapper = mock.Mock(return_value={
            "job_id": "rev-wrap", "reservation_id": "res-w", "attempt_id": "att-w",
        })
        with mock.patch.object(rig_jobs, "start_job", native):
            sched._default_launch(
                self.repo, node=review, spec=spec, state=state,
                choice={"parent_writes": True, "spawn": "native", "worker": "parent",
                        "routing": {"policy_mode": "smart"}},
                owner={}, owner_session="sched-tests", resources=[], allow_read=[],
            )
        native_kwargs = native.call_args.kwargs
        self.assertEqual(native_kwargs.get("review_mode"), "independent")
        self.assertEqual(native_kwargs.get("writer_job_id"), "writer-a")
        self.assertEqual(native_kwargs.get("writer_snapshot_id"), "snap-a")
        self.assertEqual(native_kwargs.get("writer_job_ids"), ["writer-a"])
        self.assertEqual(native_kwargs.get("writer_snapshot_ids"), ["snap-a"])
        self.assertEqual(native_kwargs.get("writer_providers"), ["xai"])
        with mock.patch.object(worker_launch, "launch", wrapper):
            sched._default_launch(
                self.repo, node=review, spec=spec, state=state,
                choice={"spawn": "run-worker", "worker": "claude", "model": "claude-opus-5",
                        "effort": "high", "routing": {"policy_mode": "smart"}},
                owner={}, owner_session="sched-tests", resources=[], allow_read=[],
            )
        wrap_kwargs = wrapper.call_args.kwargs
        self.assertEqual(wrap_kwargs.get("review_mode"), "independent")
        self.assertEqual(wrap_kwargs.get("writer_job_id"), "writer-a")
        self.assertEqual(wrap_kwargs.get("writer_snapshot_id"), "snap-a")
        self.assertEqual(wrap_kwargs.get("writer_job_ids"), ["writer-a"])
        self.assertEqual(wrap_kwargs.get("writer_snapshot_ids"), ["snap-a"])
        self.assertEqual(wrap_kwargs.get("writer_providers"), ["xai"])
        with mock.patch.object(sched.rig_route, "pick") as pick:
            pick.return_value = {"worker": "claude", "spawn": "run-worker"}
            sched._pick_node(self.repo, review, spec, state)
            pick_kwargs = pick.call_args.kwargs
        self.assertEqual(pick_kwargs.get("review_mode"), "independent")
        self.assertEqual(pick_kwargs.get("writer_job_ids"), ["writer-a"])
        self.assertEqual(pick_kwargs.get("writer_snapshot_ids"), ["snap-a"])
        self.assertEqual(pick_kwargs.get("writer_providers"), ["xai"])
        self.assertNotIn("writer_snapshot_id", pick_kwargs)

    def test_review_handoff_does_not_fabricate_writer_provider(self):
        created = self.create([
            {"id": "w1", "role": "implement", "files": ["a.py"]},
            {"id": "r1", "role": "review", "files": ["a.py"], "depends_on": ["w1"]},
        ])
        spec, state = wf.load_pair(self.repo, created["workflow_id"], required=True)
        self._writer_job(
            "writer-unknown", model="", model_source="unknown", provider="",
            provider_source="unknown", inferred=True, worker="grok",
        )
        (self.repo / ".rig" / "jobs" / "writer-unknown" / "routing.json").write_text("{}\n")
        state["nodes"]["w1"].update(
            job_id="writer-unknown", accepted=True, status="accepted",
            acceptance_snapshot="snap-u",
        )
        review = next(node for node in spec["nodes"] if node["id"] == "r1")
        _fields, writers, snapshots, providers = sched._review_handoff_fields(
            self.repo, spec, state, review,
        )
        self.assertEqual(writers, ["writer-unknown"])
        self.assertEqual(snapshots, ["snap-u"])
        self.assertEqual(providers, [])
        self.assertEqual(_fields.get("writer_providers"), [])
        self.assertEqual(_fields.get("writer_job_id"), "writer-unknown")

    def test_queue_spawned_on_first_node(self):
        item = work_queue.add_item(self.repo, "parked")
        created = self.create([{"id": "w1", "role": "implement", "files": ["a.py"]}], queue_id=item["id"])
        self.advance(created)
        queued = json.loads((self.repo / ".rig" / "queue" / f"{item['id']}.json").read_text())
        self.assertEqual(queued["status"], "spawned")


if __name__ == "__main__":
    unittest.main()
