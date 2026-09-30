"""Recipe/node contracts reach real admission and current workflow acceptance."""
import copy
import hashlib
import json
from pathlib import Path
import sys
from unittest.mock import patch

from test_native_admission import NativeHarness
import acceptance_contract as contracts
import admission
import change_evidence as evidence
import context_packages
import jobs
import verification
import workflow_recipes as recipes
import workflow_scheduler as sched
import workflow_state as wf


class WorkflowAcceptanceContracts(NativeHarness):
    def contract(self, scope=None, *, assertion=False):
        scope = scope or ["subject.txt"]
        criterion = {"id": "checked", "description": "Required behavior verified", "scope": scope,
                     "evidence_type": "check", "verifier_role": "parent",
                     "check": {"id": "tests", "argv": [sys.executable, "-c", "print('passed')"], "cwd": "."}}
        if assertion:
            criterion.update(evidence_type="review_assertion", artifact_kind="review-note")
            criterion.pop("check")
        return {"schema_version": 1, "contract_id": "workflow-contract", "revision": 1, "criteria": [criterion]}

    def parent_pick(self, node, spec, state, exclude=""):
        # Real offline routing evidence, with no configured child providers.
        # Fabricated parent choices must not bypass task-domain admission.
        return sched._pick_node(self.repo, node, spec, state, exclude=exclude)

    def create_recipe(self, name="bugfix", stage="implement", contract=None, **extra):
        params = {"task": "Verify the bounded feature", "files": ["subject.txt"],
                  "acceptance_contracts": {stage: contract or self.contract()}, **extra}
        compiled = recipes.preview(self.repo, name, params)
        self.created = wf.create_workflow(self.repo, compiled["spec"])
        return compiled

    def advance(self, **options):
        return sched.advance(self.repo, self.created["workflow_id"], owner_token=self.created["owner_token"], **options)

    def parent_launch(self):
        result = self.advance(pick_fn=self.parent_pick)
        self.assertIsNotNone(result.get("parent_action"), result)
        self.lease = evidence.read_json(Path(result["parent_action"]["credentials_path"]))
        self.folder = self.repo / ".rig/jobs" / self.lease["job_id"]
        return result

    def snapshot(self):
        meta = evidence.read_json(self.folder / "meta.json")
        return evidence.snapshot(self.repo, meta["files"])["snapshot_id"]

    def accept(self, next="complete"):
        return verification.accept(self.repo, self.folder, "accept", self.snapshot(),
                                   rationale="Parent verified the required behavior", next=next, **self.auth(self.lease))

    def check(self):
        return verification.run_check(self.repo, self.folder, "tests", [sys.executable, "-c", "print('passed')"], **self.auth(self.lease))

    def assertion(self):
        artifact = self.folder / "evidence/review.txt"
        artifact.parent.mkdir(exist_ok=True)
        artifact.write_text("Parent inspected behavior.\n")
        frozen = contracts.load(self.repo, self.folder)
        return verification.record_criterion(
            self.repo, self.folder, "checked", frozen["contract_fingerprint"], self.snapshot(), "pass",
            "Parent inspected requested behavior",
            [{"path": "evidence/review.txt", "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(), "kind": "review-note"}],
            **self.auth(self.lease),
        )

    def test_create_advance_freezes_contract_and_enforces_required_check(self):
        preview = self.create_recipe()
        self.parent_launch()
        normalized = preview["spec"]["nodes"][0]["acceptance_contract"]
        frozen = contracts.load(self.repo, self.folder)
        self.assertEqual(frozen["contract"], normalized)
        self.assertEqual(frozen["contract_fingerprint"], contracts.fingerprint(normalized))
        self.assertEqual(frozen["attempt_id"], self.lease["attempt_id"])
        self.assertEqual(evidence.read_json(self.folder / "requirements.json")["requirements"][0]["id"], "tests")
        self.assertFalse(self.finish(self.lease).get("isError"))
        with self.assertRaisesRegex(ValueError, "missing required check"):
            self.accept(next="review")
        waiting = self.advance(pick_fn=self.parent_pick)
        self.assertEqual(waiting["launched"], [])
        self.assertNotEqual(waiting["status"], "verified")
        self.check()
        self.accept(next="review")
        state = wf.refresh(self.repo, self.created["workflow_id"])
        self.assertEqual(state["node_state"]["implement"]["status"], "accepted")
        # The existing picker must expose unavailable independence instead of
        # substituting the parent or silently skipping review/final verification.
        with patch("harness.effective_workers", return_value={name: False for name in ("codex", "grok", "claude", "cursor", "devin", "mimo", "opencode", "omp", "pi", "agy")}):
            blocked = self.advance()
        self.assertEqual(blocked["launched"], [])
        self.assertNotEqual(blocked["status"], "verified")
        self.assertTrue(any("review" in row["reason"] for row in blocked["skipped"]), blocked)
        self.assertEqual(len(list((self.repo / ".rig/jobs").iterdir())), 1)

    def test_wrapper_forwarding_preserves_exact_node_contract(self):
        preview = self.create_recipe()
        spec, state = wf.load_pair(self.repo, self.created["workflow_id"], required=True)
        node = spec["nodes"][0]
        with patch("worker_launch.launch", return_value={"job_id": "fake"}) as launch:
            result = sched._default_launch(self.repo, node=node, spec=spec, state=state,
                                           choice={"worker": "grok", "spawn": "run-worker", "model": "grok-4.6"},
                                           owner=None, owner_session="", resources=[], allow_read=[])
        self.assertEqual(result["kind"], "wrapper")
        self.assertEqual(launch.call_args.kwargs["acceptance_contract"], preview["spec"]["nodes"][0]["acceptance_contract"])

    def test_accepted_ui_receipt_changed_later_downgrades_workflow(self):
        self.create_recipe("ui-validation", "ui-validate", self.contract(assertion=True))
        self.parent_launch()
        self.assertFalse(self.finish(self.lease).get("isError"))
        self.assertion()
        self.accept()
        verified = wf.refresh(self.repo, self.created["workflow_id"])
        self.assertEqual(verified["status"], "verified")
        receipt_path = self.folder / "criteria/checked.json"
        receipt = evidence.read_json(receipt_path)
        receipt["rationale"] = "Changed after acceptance"
        receipt_path.write_text(json.dumps(receipt))
        # assessment(refresh=True) reads freshness; it never rewrites receipts.
        before = {str(p): (p.read_bytes(), p.stat().st_mtime_ns) for p in self.folder.rglob("*") if p.is_file()}
        accepted, _ = wf._job_acceptance(self.repo, self.lease["job_id"], ["subject.txt"], self.contract(assertion=True))
        self.assertFalse(accepted)
        self.assertEqual(before, {str(p): (p.read_bytes(), p.stat().st_mtime_ns) for p in self.folder.rglob("*") if p.is_file()})
        stale = wf.refresh(self.repo, self.created["workflow_id"])
        self.assertNotEqual(stale["status"], "verified")
        self.assertFalse(stale["node_state"]["ui-validate"]["accepted"])

    def test_changed_source_and_missing_contract_cannot_preserve_acceptance(self):
        self.create_recipe("ui-validation", "ui-validate")
        self.parent_launch()
        self.assertFalse(self.finish(self.lease).get("isError"))
        self.check()
        self.accept()
        self.assertEqual(wf.refresh(self.repo, self.created["workflow_id"])["status"], "verified")
        (self.repo / "subject.txt").write_text("changed\n")
        self.assertNotEqual(wf.refresh(self.repo, self.created["workflow_id"])["status"], "verified")

        (self.repo / "subject.txt").write_text("before\n")
        (self.folder / "acceptance-contract.json").unlink()
        self.assertNotEqual(wf.refresh(self.repo, self.created["workflow_id"])["status"], "verified")

    def test_parent_action_clears_only_after_current_acceptance_and_exact_stopped_attempt(self):
        self.create_recipe("ui-validation", "ui-validate")
        self.parent_launch()
        running = wf.refresh(self.repo, self.created["workflow_id"])
        self.assertIsNotNone(running["parent_action"])
        self.assertFalse(self.finish(self.lease).get("isError"))
        stopped = wf.refresh(self.repo, self.created["workflow_id"])
        self.assertIsNotNone(stopped["parent_action"])
        self.check()
        self.accept()
        spec, original = wf.load_pair(self.repo, self.created["workflow_id"], required=True)
        for key in ("job_id", "reservation_id", "attempt_id"):
            for value in ("mismatch", ""):
                state = copy.deepcopy(original)
                state["parent_action"][key] = value
                result = wf.refresh_locked(self.repo, spec, state)
                self.assertIsNotNone(result["parent_action"])
                self.assertNotEqual(result["status"], "verified")
        with patch.object(wf, "_node_stop_unconfirmed", return_value=True):
            result = wf.refresh_locked(self.repo, spec, copy.deepcopy(original))
        self.assertIsNotNone(result["parent_action"])
        self.assertNotEqual(result["status"], "verified")
        result = wf.refresh_locked(self.repo, spec, copy.deepcopy(original))
        self.assertIsNone(result["parent_action"])
        self.assertEqual(result["status"], "verified")

    def test_context_and_contract_reach_admission_without_context_write_scope(self):
        (self.repo / "design.md").write_text("Selected design evidence\n")
        ref = context_packages.build(self.repo, {"files": [{"path": "design.md", "reason": "Context only"}]})["context_package"]
        self.create_recipe(context_packages={"implement": ref})
        self.parent_launch()
        meta = evidence.read_json(self.folder / "meta.json")
        self.assertEqual(meta["files"], ["subject.txt"])
        _, state = wf.load_pair(self.repo, self.created["workflow_id"], required=True)
        self.assertEqual(state["nodes"]["implement"]["parent_action"]["job"]["context_package"], ref)
        self.assertEqual(contracts.load(self.repo, self.folder)["contract"], self.contract())
        self.assertIn("Selected design evidence", (self.folder / "brief.md").read_text())

    def test_contract_normalization_requires_repo_scope_and_protected_review_stage(self):
        node = {"id": "implement", "role": "implement", "files": ["subject.txt"], "acceptance_contract": self.contract()}
        with self.assertRaisesRegex(ValueError, "require a repository"):
            wf.normalize_spec({"nodes": [node]})
        bad = copy.deepcopy(node)
        bad["acceptance_contract"]["criteria"][0]["scope"] = ["outside.txt"]
        with self.assertRaisesRegex(ValueError, "exceeds admitted"):
            wf.create_workflow(self.repo, {"nodes": [bad]})
        bad = copy.deepcopy(node)
        bad["acceptance_contract"]["criteria"][0]["verifier_role"] = "independent-review"
        with self.assertRaisesRegex(ValueError, "independent reviewer"):
            wf.create_workflow(self.repo, {"nodes": [bad]})
        bad["role"] = "review"
        with self.assertRaisesRegex(ValueError, "independent reviewer"):
            wf.create_workflow(self.repo, {"nodes": [bad]})
        self.assertFalse((self.repo / ".rig/workflows").exists())

    def test_extend_preserves_launched_and_current_attempt_contracts(self):
        self.create_recipe()
        self.parent_launch()
        spec, _ = wf.load_pair(self.repo, self.created["workflow_id"], required=True)
        writer = copy.deepcopy(spec["nodes"][0])
        writer["acceptance_contract"]["revision"] = 2
        with self.assertRaisesRegex(ValueError, "launched nodes or contracts"):
            wf.extend_workflow(self.repo, self.created["workflow_id"], [writer], owner_token=self.created["owner_token"])
        (self.repo / "extra.txt").write_text("additional scope\n")
        wf.extend_workflow(self.repo, self.created["workflow_id"], [
            {"id": "extra", "role": "explore", "files": ["extra.txt"], "acceptance_contract": self.contract(["extra.txt"])}
        ], owner_token=self.created["owner_token"])
        extended, _ = wf.load_pair(self.repo, self.created["workflow_id"], required=True)
        self.assertEqual(extended["nodes"][0]["acceptance_contract"], spec["nodes"][0]["acceptance_contract"])
        self.assertNotEqual(extended["spec_hash"], spec["spec_hash"])
        self.assertIn("acceptance_contract", wf.LAUNCHED_CONTRACT)

    def test_research_example_runs_complete_existing_workflow(self):
        (self.repo / "docs").mkdir()
        (self.repo / "docs/design.md").write_text("Design evidence\n")
        params = {"task": "Use design evidence to fix subject", "files": ["subject.txt"],
                  "research_sources": ["docs/design.md"],
                  "acceptance_contracts": {"research": self.contract(["docs/design.md"]),
                                           "implement": self.contract(),
                                           "final-verify": self.contract(["subject.txt", "docs/design.md"])}}
        preview = recipes.preview(self.repo, "research-implement", params)
        self.created = wf.create_workflow(self.repo, preview["spec"])
        actual = []
        for expected in ("research", "implement", "final-verify"):
            self.parent_launch()
            meta = evidence.read_json(self.folder / "meta.json")
            actual.append(meta["workflow_node_id"])
            self.assertEqual(actual[-1], expected)
            self.assertEqual(contracts.load(self.repo, self.folder)["contract"]["criteria"][0]["scope"],
                             sorted(params["acceptance_contracts"][expected]["criteria"][0]["scope"]))
            self.assertFalse(self.finish(self.lease).get("isError"))
            self.check()
            self.accept()
            wf.refresh(self.repo, self.created["workflow_id"])
        self.assertEqual(actual, ["research", "implement", "final-verify"])
        self.assertEqual(wf.refresh(self.repo, self.created["workflow_id"])["status"], "verified")
