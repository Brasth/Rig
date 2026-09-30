"""Task-domain transport contracts across CLI, MCP, launch, and workflows."""
from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import admission
import jobs
import rig_mcp
import route
import routing_domains
import routing_policy
import worker_launch
import workflow_scheduler as scheduler
import workflow_state as workflow
import test_routing_interfaces as interface_fixtures
import test_worker_launch as launch_fixtures
import test_workflow_scheduler as workflow_fixtures


class TaskDomainInterfaces(unittest.TestCase):
    setUp = interface_fixtures.RoutingInterfaces.setUp
    cli = interface_fixtures.RoutingInterfaces.cli
    mcp = interface_fixtures.RoutingInterfaces.mcp

    def test_schema_exposes_separate_domain_and_sources(self):
        for name in ("rig_pick", "rig_session", "rig_job_start", "rig_job_launch"):
            tool = next(item for item in rig_mcp.TOOLS if item["name"] == name)
            props = tool["inputSchema"]["properties"]
            self.assertEqual(props["task_domain"]["enum"], list(routing_domains.DOMAINS))
            self.assertEqual(props["research_sources"]["type"], "array")
            self.assertEqual(props["research_sources"]["items"], {"type": "string"})

    def test_cli_mcp_pick_session_domain_parity(self):
        for domain in ("frontend", "ui-design", "ui-verification", "backend"):
            with self.subTest(domain=domain):
                args = ("--case", "scoped task", "--task-domain", domain, "--json")
                response = self.cli("pick", "implement", *args)
                self.assertEqual(response.returncode, 0, response.stderr)
                picked = json.loads(response.stdout)
                self.assertEqual(picked["kind"], "implement")
                self.assertEqual(picked["routing"]["task_domain"]["name"], domain)
                self.assertEqual(picked["routing"]["task_domain"]["source"], "explicit")
                for tool in ("rig_pick", "rig_session"):
                    result = self.mcp(tool, role="implement", task_domain=domain, compact=True)
                    self.assertFalse(result.get("isError"), result)
                    body = json.loads(result["content"][0]["text"])
                    self.assertEqual(body["pick"] if tool == "rig_session" else body, picked)
                session = self.cli("session", "--role", "implement", "--compact", *args)
                self.assertEqual(session.returncode, 0, session.stderr)
                self.assertEqual(json.loads(session.stdout)["pick"], picked)

    def test_repeatable_research_sources_cli_mcp_parity(self):
        names = ["source one.md", "source-two.txt"]
        for name in names:
            (self.repo / name).write_text("Repository-local evidence.\n")
        args = ("--case", "scoped task", "--task-domain", "research",
                "--research-source", names[0], "--research-source", names[1], "--json")
        response = self.cli("pick", "explore", *args)
        self.assertEqual(response.returncode, 0, response.stderr)
        picked = json.loads(response.stdout)
        self.assertEqual(picked["routing"]["task_domain"]["research_sources"], names)
        for tool in ("rig_pick", "rig_session"):
            result = self.mcp(tool, role="explore", task_domain="research", research_sources=names, compact=True)
            self.assertFalse(result.get("isError"), result)
            body = json.loads(result["content"][0]["text"])
            self.assertEqual(body["pick"] if tool == "rig_session" else body, picked)
        session = self.cli("session", "--role", "explore", "--compact", *args)
        self.assertEqual(session.returncode, 0, session.stderr)
        self.assertEqual(json.loads(session.stdout)["pick"], picked)

    def test_bad_domains_sources_and_legacy_fail_closed(self):
        for tool in ("rig_pick", "rig_session", "rig_job_launch", "rig_job_start"):
            for bad in ({"task_domain": "made-up"}, {"task_domain": 42},
                        {"research_sources": "source.md"}, {"research_sources": [42]},
                        {"research_sources": [""]}):
                with self.subTest(tool=tool, bad=bad):
                    result = self.mcp(tool, role="explore", brief="Read supplied sources", **bad)
                    self.assertTrue(result.get("isError"), result)
                    self.assertRegex(result["content"][0]["text"], "task_domain|research_sources")
        for command in ("pick", "session"):
            for flag, value in (("--task-domain", "frontend"), ("--research-source", "source.md")):
                result = self.cli(command, "explore", "--case", "scoped task", "--policy-mode", "legacy", flag, value)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("smart routing", result.stderr)

    def test_mcp_launch_and_native_forward_fields(self):
        fields = {"task_domain": "research", "research_sources": ["source.md"]}
        with mock.patch.object(rig_mcp.rig_launch, "launch", return_value={"job_id": "j"}) as launch:
            result = self.mcp("rig_job_launch", role="explore", brief="Read sources", **fields)
            self.assertFalse(result.get("isError"), result)
            for key, value in fields.items():
                self.assertEqual(launch.call_args.kwargs[key], value)
        with mock.patch.object(rig_mcp.rig_jobs, "start_job", return_value={"job_id": "j"}) as start:
            result = self.mcp("rig_job_start", role="explore", **fields)
            self.assertFalse(result.get("isError"), result)
            for key, value in fields.items():
                self.assertEqual(start.call_args.kwargs[key], value)


class TaskDomainLaunches(unittest.TestCase):
    setUp = launch_fixtures.WorkerLaunchTests.setUp
    tearDown = launch_fixtures.WorkerLaunchTests.tearDown
    configure = launch_fixtures.WorkerLaunchTests.configure
    _write_wrapper = launch_fixtures.WorkerLaunchTests._write_wrapper
    _stop = launch_fixtures.WorkerLaunchTests._stop
    _launch = launch_fixtures.WorkerLaunchTests._launch
    _held = launch_fixtures.WorkerLaunchTests._held

    def test_wrapper_auto_and_explicit_keep_domain(self):
        for index, (worker, model, effort) in enumerate((("", "", ""), ("grok", "", ""))):
            with self.subTest(worker=worker, model=model):
                result = self._launch(f"domain-{index}", role="explore", worker=worker,
                                      model=model, effort=effort, access="read", files=[],
                                      task_domain="research", research_sources=["a.py"])
                sidecar = json.loads((self.repo / ".rig" / "jobs" / result["job_id"] / "routing.json").read_text())
                meta = json.loads((self.repo / ".rig" / "jobs" / result["job_id"] / "meta.json").read_text())
                self.assertEqual(meta["files"], ["a.py"])
                brief = (self.repo / ".rig" / "jobs" / result["job_id"] / "brief.md").read_text()
                self.assertTrue(brief.startswith("do the listed files\n\n"))
                self.assertIn("Read-only analysis", brief)
                self.assertIn('["a.py"]', brief)
                self.assertIn("ask the parent to acquire it", brief)
                self.assertIn("no browser, vision, Figma, computer-use", brief)
                domain = sidecar["routing"]["task_domain"]
                self.assertEqual(domain["name"], "research")
                self.assertEqual(domain["research_sources"], ["a.py"])

    def test_automatic_wrapper_normalizes_equivalent_research_sources(self):
        for index, sources in enumerate((["./a.py"], ["a.py", "a.py"], ["./a.py", "a.py"])):
            with self.subTest(sources=sources):
                result = self._launch(f"normalized-{index}", role="explore", worker="", model="", effort="",
                                      task_domain="research", research_sources=sources, access="read", files=[])
                folder = self.repo / ".rig" / "jobs" / result["job_id"]
                domain = json.loads((folder / "routing.json").read_text())["routing"]["task_domain"]
                self.assertEqual(domain["research_sources"], ["a.py"])
                self.assertEqual(json.loads((folder / "meta.json").read_text())["files"], ["a.py"])
                self.assertIn('["a.py"]', (folder / "brief.md").read_text())

    def test_explicit_model_requires_and_preserves_smart_metadata(self):
        with self.assertRaisesRegex(worker_launch.LaunchError, "smart routing metadata"):
            self._launch("manual-domain", role="explore", model="grok-4.5", effort="low",
                         task_domain="research", research_sources=["a.py"], access="read")
        picked = route.pick("codex", ["grok"], "explore", "Read supplied sources", repo=self.repo,
                            task_domain="research", research_sources=["a.py"])
        result = self._launch("explicit-domain", role="explore", worker=picked["worker"],
                              model=picked["model"], effort=picked["effort"], routing=picked["routing"],
                              task_domain="research", research_sources=["a.py"], access="read", files=[])
        folder = self.repo / ".rig" / "jobs" / result["job_id"]
        self.assertEqual(json.loads((folder / "meta.json").read_text())["files"], ["a.py"])
        self.assertEqual(json.loads((folder / "routing.json").read_text())["routing"]["task_domain"]["name"], "research")

    def test_routing_only_metadata_reaches_model_selection_and_validation(self):
        picked = route.pick("codex", ["grok"], "explore", "Read supplied sources", repo=self.repo,
                            task_domain="research", research_sources=["a.py"])
        result = self._launch("routing-only", role="explore", worker="grok", model="", effort="",
                              access="read", files=["a.py"], routing=picked["routing"])
        sidecar = json.loads((self.repo / ".rig" / "jobs" / result["job_id"] / "routing.json").read_text())
        self.assertEqual(sidecar["routing"]["task_domain"]["name"], "research")
        self.assertEqual(sidecar["routing"]["task_domain"]["research_sources"], ["a.py"])

    def test_native_selection_and_validation_receive_fields(self):
        fields = {"task_domain": "research", "research_sources": ["a.py"]}
        with mock.patch.object(routing_policy, "resolve_explicit_worker_choice", wraps=routing_policy.resolve_explicit_worker_choice) as pick, \
             mock.patch.object(routing_policy, "validate_launch_tuple", side_effect=ValueError("stop before admission")) as validate:
            with self.assertRaisesRegex(SystemExit, "stop before admission"):
                jobs.start_job(self.repo, worker="grok", role="explore", executor_kind="native_child",
                               files=["a.py"], access="read", **fields)
            for key, value in fields.items():
                self.assertEqual(pick.call_args.kwargs[key], value)
                self.assertEqual(validate.call_args.kwargs[key], value)
        self.assertFalse(self._held())

    def test_native_returns_validated_research_contract(self):
        picked = route.pick("codex", ["grok"], "explore", "Read supplied sources", repo=self.repo,
                            task_domain="research", research_sources=["a.py"])
        result = jobs.start_job(self.repo, worker="grok", role="explore", executor_kind="native_child",
                                model=picked["model"], effort=picked["effort"], files=[], access="read",
                                routing=picked["routing"], return_details=True)
        contract = result["research_source_instructions"]
        self.assertIn("Read-only analysis", contract)
        self.assertIn('["a.py"]', contract)
        self.assertIn("no browser, vision, Figma, computer-use", contract)
        self.assertEqual(jobs.research_source_instructions({}), "")
        self.assertEqual(jobs.research_source_instructions({"task_domain": {
            "name": "research", "research_sources": [], "parent_only": True}}), "")

    def test_native_admission_includes_verified_source_scope(self):
        picked = route.pick("codex", ["grok"], "explore", "Read supplied sources", repo=self.repo,
                            task_domain="research", research_sources=["a.py"])
        with mock.patch.object(admission, "reserve", side_effect=admission.AdmissionError("stop before reserve")) as reserve:
            with self.assertRaisesRegex(admission.AdmissionError, "stop before reserve"):
                jobs.start_job(self.repo, worker="grok", role="explore", executor_kind="native_child",
                               model=picked["model"], effort=picked["effort"], files=[], access="read",
                               routing=picked["routing"])
        self.assertEqual(reserve.call_args.kwargs["files"], ["a.py"])
        self.assertEqual(reserve.call_args.kwargs["access"], "read")
        self.assertFalse(self._held())

    def test_parent_only_domains_refuse_wrapper_before_admission(self):
        for domain in ("ui-design", "ui-verification", "research"):
            with self.subTest(domain=domain):
                with self.assertRaises(worker_launch.LaunchError):
                    self._launch("blocked-" + domain, task_domain=domain)
        self.assertFalse(self._held())


class TaskDomainWorkflows(unittest.TestCase):
    def node(self, **updates):
        return {"id": "sources", "role": "explore", "task_domain": "research",
                "research_sources": ["source.md"], "resources": [{"name": "sources", "access": "read"}], **updates}

    def test_normalizes_freezes_and_protects_read_source_scope(self):
        spec = workflow.normalize_spec({"nodes": [self.node()]})
        node = spec["nodes"][0]
        self.assertEqual(node["task_domain"], "research")
        self.assertEqual(node["files"], ["source.md"])
        self.assertEqual(scheduler._node_access(node), "read")
        self.assertIn("task_domain", workflow.LAUNCHED_CONTRACT)
        self.assertIn("research_sources", workflow.LAUNCHED_CONTRACT)
        writer = {"id": "writer", "role": "implement", "files": ["source.md"]}
        with self.assertRaisesRegex(workflow.WorkflowError, "read overlap"):
            workflow.normalize_spec({"nodes": [writer, self.node()]})
        writer["files"] = ["other.py"]
        writer["resources"] = [{"name": "sources", "access": "write"}]
        with self.assertRaisesRegex(workflow.WorkflowError, "resource.*overlaps"):
            workflow.normalize_spec({"nodes": [writer, self.node()]})
        allowed = workflow.normalize_spec({"nodes": [writer, self.node(depends_on=["writer"])]})
        self.assertEqual(next(n for n in allowed["nodes"] if n["id"] == "sources")["resources"][0]["access"], "read")

    def test_normalized_source_paths_share_workflow_scope_and_contract(self):
        expected = workflow.normalize_spec({"nodes": [self.node()]})
        for sources in (["./source.md"], ["source.md", "source.md"], ["./source.md", "source.md"]):
            with self.subTest(sources=sources):
                actual = workflow.normalize_spec({"nodes": [self.node(research_sources=sources)]})
                self.assertEqual(actual, expected)
                self.assertEqual(actual["nodes"][0]["research_sources"], ["source.md"])
                self.assertEqual(actual["nodes"][0]["files"], ["source.md"])
                with self.assertRaisesRegex(workflow.WorkflowError, "read overlap"):
                    workflow.normalize_spec({"nodes": [
                        {"id": "writer", "role": "implement", "files": ["source.md"]},
                        self.node(research_sources=sources),
                    ]})

    def test_final_verification_waits_for_required_source_analysis(self):
        spec = workflow.normalize_spec({"nodes": [self.node(),
            {"id": "writer", "role": "implement", "files": ["out.py"], "effects": "local"},
        ]})
        final = next(node for node in spec["nodes"] if node.get("final"))
        self.assertEqual(final["role"], "verify")
        self.assertEqual(set(final["depends_on"]), {"sources", "writer"})
        self.assertIn("source.md", final["files"])
        self.assertTrue(all(item["access"] == "read" for item in final["resources"]))

    def test_explore_rejects_effects_write_resources_and_bad_fields(self):
        for bad in ({"effects": "local"}, {"resources": [{"name": "db", "access": "write"}]},
                    {"task_domain": "unknown"}, {"task_domain": True},
                    {"research_sources": "source.md"}, {"research_sources": [1]}):
            with self.subTest(bad=bad), self.assertRaises(workflow.WorkflowError):
                workflow.normalize_spec({"nodes": [self.node(**bad)]})

    def test_workflow_pick_and_launch_forward_domain_without_changing_role(self):
        node = workflow.normalize_spec({"nodes": [self.node()]})["nodes"][0]
        spec = {"workflow_id": "wf", "nodes": [node]}
        state = {"nodes": {"sources": {}}}
        with mock.patch.object(scheduler.harness, "live_parent", return_value="codex"), \
             mock.patch.object(scheduler.harness, "effective_workers", return_value=["grok"]), \
             mock.patch.object(scheduler.rig_route, "pick", return_value={}) as pick:
            scheduler._pick_node(Path("."), node, spec, state)
        self.assertEqual(pick.call_args.args[2], "explore")
        self.assertEqual(pick.call_args.kwargs["task_domain"], "research")
        self.assertEqual(pick.call_args.kwargs["research_sources"], ["source.md"])
        for parent in (False, True):
            choice = {"spawn": "stay" if parent else "run-worker", "worker": "parent" if parent else "grok",
                      "routing": {"execution_strategy": "stay" if parent else "wrapper"}}
            target = scheduler.rig_jobs if parent else worker_launch
            method = "start_job" if parent else "launch"
            with mock.patch.object(target, method, return_value={"job_id": "j"}) as launch:
                scheduler._default_launch(Path("."), node=node, spec=spec, state=state, choice=choice,
                                          owner={}, owner_session="tests", resources=node["resources"], allow_read=[])
            self.assertEqual(launch.call_args.kwargs["task_domain"], "research")
            self.assertEqual(launch.call_args.kwargs["research_sources"], ["source.md"])
            self.assertEqual(launch.call_args.kwargs["access"], "read")
            self.assertEqual(launch.call_args.kwargs["files"], ["source.md"])


class TaskDomainWorkflowContract(unittest.TestCase):
    setUp = workflow_fixtures.WorkflowScheduler.setUp
    create = workflow_fixtures.WorkflowScheduler.create
    pick = workflow_fixtures.WorkflowScheduler.pick
    launch = workflow_fixtures.WorkflowScheduler.launch
    advance = workflow_fixtures.WorkflowScheduler.advance

    def test_parent_action_preserves_research_contract(self):
        created = self.create([{"id": "research", "role": "explore", "task_domain": "research",
                                "research_sources": ["a.py"], "files": []}])
        base_launch = self.launch()
        contract = 'Read-only analysis of local sources: ["a.py"]'

        def launch(*args, **kwargs):
            result = base_launch(*args, **kwargs)
            result["job"]["research_source_instructions"] = contract
            return result

        result = self.advance(created, pick_fn=self.pick(parent_writes=True), launch_fn=launch)
        self.assertEqual(result["parent_action"]["research_source_instructions"], contract)
        self.assertEqual(result["parent_action"]["files"], ["a.py"])
        self.assertEqual(result["parent_action"]["role"], "explore")


if __name__ == "__main__":
    unittest.main()
