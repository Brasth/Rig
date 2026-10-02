#!/usr/bin/env python3
"""Preparation objects bind brief, scope, acceptance and source bytes before any admission write."""
import copy
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))
import admission  # noqa: E402
import jobs  # noqa: E402
import mcp_test_support  # noqa: E402
import preparation_binding as binding  # noqa: E402
import rig_mcp  # noqa: E402
import route  # noqa: E402
import routing_evidence  # noqa: E402
import task_preparation  # noqa: E402
import worker_launch  # noqa: E402
import workflow_recipes  # noqa: E402
import workflow_scheduler as scheduler  # noqa: E402
import workflow_state as wf  # noqa: E402

ARGV_WRAPPER = """#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
(Path(os.environ["RIG_JOB_DIR"]) / "wrapper-argv.json").write_text(json.dumps(sys.argv[1:]))
"""


def selection(**delta):
    return {"task": "Fix saving", "files": ["app.py", "new.py"],
            "checks": [{"id": "tests", "argv": ["python3", "-m", "unittest"]}],
            "references": [{"path": "design.md", "reason": "Design constraints"}],
            "findings": [{"statement": "save() is a stub", "status": "verified", "evidence": [{"path": "app.py"}]}],
            "changes": [{"path": "app.py", "change": "Write data"}, {"path": "new.py", "change": "Add helper"}],
            "reading_order": [{"path": "app.py", "reason": "entrypoint"}],
            "decisions": [], "unknowns": [], "remaining_work": "low", **delta}


class Fixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name).resolve()
        self.repo, self.home, self.rig_home, self.bins = (root / name for name in ("repo", "home", "rig-home", "bins"))
        self.repo.mkdir()
        (self.repo / ".git").mkdir()
        (self.repo / ".rig").mkdir()
        (self.repo / ".rig/harness.toml").write_text(
            'parent = "codex"\n[workers]\ngrok = true\nclaude = true\ncodex = true\ncursor = false\n')
        (self.repo / "app.py").write_text("def save():\n    pass\n")
        (self.repo / "design.md").write_text("Keep the API.\n")
        self.home.mkdir()
        (self.rig_home / "scripts").mkdir(parents=True)
        wrapper = self.rig_home / "scripts/run-worker.sh"
        wrapper.write_text(ARGV_WRAPPER)
        wrapper.chmod(0o755)
        mcp_test_support.fake_bin(self.bins, "grok")
        mcp_test_support.seed_installed_mcp(self.home)
        env = mock.patch.dict(os.environ, {
            "HOME": str(self.home), "PATH": mcp_test_support.stub_path(self.bins), "RIG_HOME": str(self.rig_home),
            "RIG_PARENT": "codex", "RIG_OWNER_SESSION": "prep-tests", "RIG_SKIP_MODEL_CATALOG": "1",
            "RIG_JOB_ID": "", "RIG_JOB_DIR": ""})
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("RIG_JOB_ID", None)
        os.environ.pop("RIG_JOB_DIR", None)

    def prepared(self, **delta):
        return task_preparation.prepare(self.repo, selection(**delta))

    def inventory(self):
        return {str(p.relative_to(self.repo)): (p.read_bytes() if p.is_file() else b"", p.lstat().st_mtime_ns)
                for p in self.repo.rglob("*")}


class Binding(Fixture):
    def test_deterministic_read_only_versioned_object(self):
        before = self.inventory()
        with mock.patch("admission.transaction", side_effect=AssertionError("no admission")):
            first, second = self.prepared(), self.prepared()
        self.assertEqual(first, second)
        self.assertEqual(self.inventory(), before)
        prep = first["preparation"]
        self.assertEqual((prep["schema_version"], prep["kind"]), (1, "rig-task-preparation"))
        rows = {row["path"]: row for row in prep["sources"]}
        self.assertEqual(rows["new.py"], {"path": "new.py", "role": "writer", "state": "absent"})
        self.assertEqual((rows["app.py"]["role"], rows["design.md"]["role"]), ("writer", "evidence"))
        _draft, summary = binding.inspect(self.repo, prep)
        self.assertEqual(summary["fingerprint"], prep["fingerprint"])
        self.assertNotIn("Fix saving", json.dumps(summary))

    def test_stale_sources_including_new_writer_creation(self):
        for name, action in (("app.py", lambda: (self.repo / "app.py").write_text("x\n")),
                             ("design.md", lambda: (self.repo / "design.md").write_text("y\n")),
                             ("new.py", lambda: (self.repo / "new.py").write_text("z\n"))):
            prep = self.prepared()["preparation"]
            action()
            with self.subTest(name=name), self.assertRaisesRegex(binding.PreparationError, "stale.*" + name) as caught:
                binding.inspect(self.repo, prep)
            self.assertEqual(caught.exception.code, "preparation-stale")
            # Structural checks for stored workflow specs do not depend on current bytes.
            binding.inspect(self.repo, prep, fresh=False)
            for path in ("app.py", "design.md"):
                (self.repo / path).write_text({"app.py": "def save():\n    pass\n", "design.md": "Keep the API.\n"}[path])
            (self.repo / "new.py").unlink(missing_ok=True)

    def test_tampered_or_malformed_objects_fail(self):
        prep = self.prepared()["preparation"]
        mutations = (
            lambda p: p.update(extra=1),
            lambda p: p.update(schema_version=2),
            lambda p: p["handoff"].update(remaining_work="high"),
            lambda p: p["readiness"].update(execution_ready=False),
            lambda p: p.update(brief_sha256="0" * 64),
        )
        for mutate in mutations:
            value = copy.deepcopy(prep)
            mutate(value)
            with self.assertRaises(binding.PreparationError):
                binding.inspect(self.repo, value)
        rehashed = copy.deepcopy(prep)
        rehashed["readiness"]["execution_ready"] = False
        body = {key: item for key, item in rehashed.items() if key != "fingerprint"}
        rehashed["fingerprint"] = binding._sha(binding._canonical(body))
        with self.assertRaisesRegex(binding.PreparationError, "recomputed") as caught:
            binding.inspect(self.repo, rehashed)
        self.assertEqual(caught.exception.code, "preparation-tampered")
        for value in (None, [], "x", {"kind": "rig-task-preparation"}):
            with self.assertRaises(binding.PreparationError):
                binding.inspect(self.repo, value)

    def test_launch_binding_exact_brief_scope_access_and_contract(self):
        result = self.prepared()
        prep, brief, contract = result["preparation"], result["brief"], result["acceptance_contract"]
        ok = dict(brief=brief, files=["new.py", "app.py"], acceptance_contract=contract, access="write")
        self.assertEqual(binding.validate_launch(self.repo, prep, **ok)["status"], "valid")
        cases = {
            "preparation-brief-mismatch": {"brief": brief + "\n\nShared context:\nspoofed"},
            "preparation-scope-mismatch": {"files": ["app.py"]},
            "preparation-acceptance-mismatch": {"acceptance_contract": None},
        }
        for code, delta in cases.items():
            with self.subTest(code=code), self.assertRaises(binding.PreparationError) as caught:
                binding.validate_launch(self.repo, prep, **{**ok, **delta})
            self.assertEqual(caught.exception.code, code)
        with self.assertRaises(binding.PreparationError):
            binding.validate_launch(self.repo, prep, **{**ok, "access": "read"})
        other = copy.deepcopy(contract)
        other["criteria"][0]["description"] = "Something else"
        with self.assertRaises(binding.PreparationError):
            binding.validate_launch(self.repo, prep, **{**ok, "acceptance_contract": other})
        unready = task_preparation.prepare(self.repo, {"task": "x", "files": ["app.py"]})["preparation"]
        with self.assertRaisesRegex(binding.PreparationError, "not ready"):
            binding.validate_launch(self.repo, unready, **ok)


class Launch(Fixture):
    def launch(self, result, **delta):
        kwargs = dict(id="job-p", brief=result["brief"], worker="grok", role="implement", model="grok-4.6",
                      effort="high", files=result["files"], acceptance_contract=result["acceptance_contract"],
                      preparation=result["preparation"], owner_session="prep-tests")
        kwargs.update(delta)
        return worker_launch.launch(self.repo, **kwargs)

    def assert_no_admission_writes(self):
        self.assertFalse((self.repo / ".rig/jobs/job-p").exists())
        self.assertEqual(admission.list_reservations(self.repo, include_released=True), [])

    def test_prepared_launch_writes_exact_brief_argv_and_bounded_sidecar(self):
        result = self.prepared()
        launched = self.launch(result)
        folder = self.repo / ".rig/jobs/job-p"
        for _ in range(200):
            if (folder / "wrapper-argv.json").exists():
                break
            import time
            time.sleep(0.02)
        argv = json.loads((folder / "wrapper-argv.json").read_text())
        self.assertEqual(argv, ["grok", "job-p", str(folder / "brief.md")])
        self.assertEqual((folder / "brief.md").read_text(), result["brief"] + "\n")
        sidecar = routing_evidence.read_sidecar(folder, expected_attempt_id=launched["attempt_id"])
        self.assertEqual(sidecar["routing"]["preparation"]["fingerprint"], result["preparation"]["fingerprint"])
        self.assertNotIn("save() is a stub", json.dumps(sidecar))

    def test_bad_or_stale_preparation_never_reaches_admission(self):
        result = self.prepared()
        for delta in ({"brief": result["brief"] + "\nextra"}, {"files": ["app.py"]},
                      {"acceptance_contract": None}, {"preparation": {"kind": "forged"}}, {"access": "read"}):
            with self.subTest(delta=sorted(delta)), self.assertRaises(worker_launch.LaunchError):
                self.launch(result, **delta)
            self.assert_no_admission_writes()
        (self.repo / "design.md").write_text("changed\n")
        with self.assertRaisesRegex(worker_launch.LaunchError, "stale"):
            self.launch(result)
        self.assert_no_admission_writes()

    def test_recheck_at_ownership_boundary_blocks_reserve(self):
        result = self.prepared()
        with mock.patch.object(binding, "recheck", side_effect=binding.PreparationError("preparation-stale", "stale now")), \
             mock.patch.object(admission, "reserve", side_effect=AssertionError("must not reserve")):
            with self.assertRaisesRegex(worker_launch.LaunchError, "stale now"):
                self.launch(result)
        self.assert_no_admission_writes()

    def test_smart_routing_requires_the_picked_preparation(self):
        (self.repo / ".rig/harness.toml").write_text(
            'parent = "codex"\n[workers]\ngrok = true\ncursor = false\n[routing]\npreparation_aware_effort = true\n')
        result = self.prepared()
        choice = route.pick("codex", ["grok"], "implement", "Fix saving", repo=self.repo, catalogs={},
                            preparation=result["preparation"])
        self.assertEqual(choice["routing"]["effort"]["reason"], "effort-unsupported")
        with self.assertRaisesRegex(worker_launch.LaunchError, "same preparation"):
            self.launch(result, preparation=None, model=choice["model"], effort=choice["effort"],
                        routing=choice["routing"], brief="plain brief")
        self.assert_no_admission_writes()
        launched = self.launch(result, model=choice["model"], effort=choice["effort"], routing=choice["routing"])
        sidecar = routing_evidence.read_sidecar(self.repo / ".rig/jobs/job-p", expected_attempt_id=launched["attempt_id"])
        self.assertEqual(sidecar["routing"]["effort"]["effective"], choice["effort"])

    def test_native_start_binds_raw_summary_before_shared_suffix(self):
        result = self.prepared()
        common = dict(worker="parent", role="implement", executor_kind="parent", summary=result["brief"],
                      files=result["files"], acceptance_contract=result["acceptance_contract"],
                      preparation=result["preparation"], owner_session="prep-tests", return_details=True)
        with self.assertRaises(SystemExit):
            jobs.start_job(self.repo, job_id="job-p", **{**common, "summary": result["brief"] + "\nmore"})
        self.assert_no_admission_writes()
        details = jobs.start_job(self.repo, job_id="job-p", **common)
        self.assertEqual(details["job_id"], "job-p")
        routing = routing_evidence.read_sidecar(self.repo / ".rig/jobs/job-p")["routing"]
        self.assertEqual(routing["preparation"]["fingerprint"], result["preparation"]["fingerprint"])

    def test_mcp_pick_and_launch_parity(self):
        result = self.prepared()
        expected = route.pick("codex", ["grok"], "implement", "Fix saving", repo=self.repo,
                              preparation=result["preparation"])
        with mock.patch.object(rig_mcp.rig_harness, "live_parent", return_value="codex"), \
             mock.patch.object(rig_mcp.rig_harness, "effective_workers", return_value=["grok"]):
            actual = rig_mcp.call_tool("rig_pick", {"repo": str(self.repo), "role": "implement", "case": "Fix saving",
                                                    "preparation": result["preparation"]})
        self.assertEqual(json.loads(actual["content"][0]["text"])["routing"], expected["routing"])
        bad = rig_mcp.call_tool("rig_pick", {"repo": str(self.repo), "case": "x", "preparation": "nope"})
        self.assertTrue(bad["isError"])


class Workflow(Fixture):
    def spec(self, result, **node):
        return {"shared_context": "Shared notes", "nodes": [
            {"id": "build", "role": "implement", "files": result["files"], "brief": result["brief"],
             "acceptance_contract": result["acceptance_contract"], "preparation": result["preparation"], **node}]}

    def test_node_preparation_is_hashed_and_must_match_brief_scope_contract(self):
        result = self.prepared()
        created = wf.create_workflow(self.repo, self.spec(result), owner_session="prep-tests")
        spec, _state = wf.load_pair(self.repo, created["workflow_id"], required=True)
        node = spec["nodes"][0]
        self.assertEqual(node["preparation"], result["preparation"])
        self.assertEqual(spec["spec_hash"], wf.spec_hash(spec))
        self.assertIn("preparation", wf.LAUNCHED_CONTRACT)
        for delta in ({"brief": result["brief"] + "\nmore"}, {"files": ["app.py"]},
                      {"acceptance_contract": None}, {"role": "review"}):
            with self.subTest(delta=sorted(delta)), self.assertRaises(wf.WorkflowError):
                raw = self.spec(result, **delta)
                if delta.get("acceptance_contract", 1) is None:
                    raw["nodes"][0].pop("acceptance_contract")
                wf.create_workflow(self.repo, raw, owner_session="prep-tests")

    def test_scheduler_passes_preparation_and_appends_shared_context_after_validation(self):
        result = self.prepared()
        created = wf.create_workflow(self.repo, self.spec(result), owner_session="prep-tests")
        spec, state = wf.load_pair(self.repo, created["workflow_id"], required=True)
        node = spec["nodes"][0]
        with mock.patch.object(route, "pick", return_value={"spawn": "run-worker"}) as pick:
            scheduler._pick_node(self.repo, node, spec, state)
        self.assertEqual(pick.call_args.kwargs["preparation"], result["preparation"])
        choice = {"worker": "grok", "spawn": "run-worker", "model": "grok-4.6"}
        with mock.patch.object(worker_launch, "launch", return_value={"job_id": "j"}) as launch:
            scheduler._default_launch(self.repo, node=node, spec=spec, state=state, choice=choice,
                                      owner={}, owner_session="prep-tests", resources=[], allow_read=[])
        kwargs = launch.call_args.kwargs
        self.assertEqual(kwargs["brief"], result["brief"])
        self.assertEqual(kwargs["preparation"], result["preparation"])
        self.assertEqual(kwargs["workflow_shared_context"], "Shared notes")

    def test_dependency_changed_source_fails_stale_then_authenticated_rebind(self):
        result = self.prepared()
        created = wf.create_workflow(self.repo, self.spec(result), owner_session="prep-tests")
        wid, token = created["workflow_id"], created["owner_token"]
        (self.repo / "app.py").write_text("changed by an accepted dependency\n")
        outcome = scheduler.advance(self.repo, wid, owner_token=token, owner_session="prep-tests")
        self.assertTrue(any("stale" in row["reason"] for row in outcome["skipped"]))
        self.assertEqual(admission.list_reservations(self.repo, include_released=True), [])
        fresh = self.prepared()
        with self.assertRaises(wf.WorkflowError):
            wf.extend_workflow(self.repo, wid, {"preparations": {"build": fresh["preparation"]}}, owner_token="wrong")
        with self.assertRaisesRegex(wf.WorkflowError, "never-executed"):
            wf.extend_workflow(self.repo, wid, {"preparations": {"build": fresh["preparation"]}}, owner_token=token)
        import workflow
        workflow.resolve(self.repo, wid, "build", action="retry", owner_token=token, owner_session="prep-tests")
        wf.extend_workflow(self.repo, wid, {"preparations": {"build": fresh["preparation"]}}, owner_token=token)
        spec, state = wf.load_pair(self.repo, wid, required=True)
        build = next(node for node in spec["nodes"] if node["id"] == "build")
        self.assertEqual(build["preparation"], fresh["preparation"])
        self.assertEqual(build["brief"], fresh["brief"])
        self.assertIsNone(state["nodes"]["build"]["routing"])
        state["nodes"]["build"].update(launched=True, ran=True, status="running", job_id="j1")
        wf.save_state(self.repo, state)
        with self.assertRaises(wf.WorkflowError):
            wf.extend_workflow(self.repo, wid, {"preparations": {"build": fresh["preparation"]}}, owner_token=token)

    def test_recipe_preparations_keep_prepared_brief(self):
        result = self.prepared(recipe="bugfix")
        params = result["recipe_parameters"]
        self.assertEqual(set(params["preparations"]), {"implement"})
        preview = result["recipe_preview"]
        node = next(row for row in preview["spec"]["nodes"] if row["id"] == "implement")
        self.assertEqual(node["brief"], result["brief"])
        review = next(row for row in preview["spec"]["nodes"] if row["id"] == "review")
        self.assertNotIn("preparation", review)
        self.assertNotIn("Task parameter", node["brief"])
        with self.assertRaises(wf.WorkflowError):
            workflow_recipes.preview(self.repo, "bugfix", {**params, "preparations": {"review": result["preparation"]}})
        self.assertIn("preparations", workflow_recipes.show("bugfix")["parameter_schema"]["properties"])
        self.assertNotIn("preparations", workflow_recipes.show("ui-validation")["parameter_schema"]["properties"])


if __name__ == "__main__":
    unittest.main()
