"""Optional pre-work contracts reuse admission and parent verification gates."""
import copy
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

from test_native_admission import NativeHarness, ROOT
import acceptance_contract as contracts
import admission
import change_evidence as evidence
import jobs
import rig_mcp
import verification


class AcceptanceContracts(NativeHarness):
    def contract(self, *, review=True):
        criteria = [{"id": "tests-pass", "description": "Required tests pass on current content",
                     "scope": ["subject.txt"], "evidence_type": "check", "verifier_role": "parent",
                     "check": {"id": "tests", "argv": [sys.executable, "-c", "print('passed')"]}}]
        if review:
            criteria.append({"id": "behavior-reviewed", "description": "Parent inspects the requested behavior",
                             "scope": ["subject.txt"], "evidence_type": "review_assertion",
                             "verifier_role": "parent", "artifact_kind": "review-note"})
        return {"schema_version": 1, "contract_id": "task-contract", "revision": 1, "criteria": criteria}

    def begin(self, contract=None):
        self.spec = contract if contract is not None else self.contract()
        self.lease = self.start(acceptance_contract=self.spec)
        self.folder = self.repo / ".rig/jobs/writer"
        self.frozen = contracts.load(self.repo, self.folder)
        self.assertFalse(self.finish(self.lease).get("isError"))
        return self.lease

    def snapshot(self):
        return evidence.snapshot(self.repo, ["subject.txt"])["snapshot_id"]

    def check(self, name="tests", argv=None):
        return verification.run_check(self.repo, self.folder, name,
                                      argv or [sys.executable, "-c", "print('passed')"], **self.auth(self.lease))

    def refs(self):
        path = self.folder / "evidence/review.txt"
        path.parent.mkdir(exist_ok=True)
        path.write_text("Inspected behavior using scoped source and local evidence.\n")
        return [{"path": "evidence/review.txt", "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                 "kind": "review-note"}]

    def assertion(self, **changes):
        values = {"criterion_id": "behavior-reviewed", "contract_fingerprint": self.frozen["contract_fingerprint"],
                  "snapshot_id": self.snapshot(), "result": "pass", "rationale": "Reviewed each requested behavior.",
                  "evidence_refs": self.refs()}
        values.update(changes)
        return verification.record_criterion(self.repo, self.folder, **values, **self.auth(self.lease))

    def accept(self, next="review"):
        return verification.accept(self.repo, self.folder, "accept", self.snapshot(),
                                   rationale="All declared criteria verified or explicitly reviewed.", next=next,
                                   **self.auth(self.lease))

    def test_frozen_before_work_and_success_remains_unaccepted(self):
        lease = self.start(acceptance_contract=self.contract())
        folder = self.repo / ".rig/jobs/writer"
        frozen = contracts.load(self.repo, folder)
        self.assertEqual(frozen["attempt_id"], lease["attempt_id"])
        self.assertEqual(frozen["contract_fingerprint"], lease["contract_fingerprint"])
        manifest = evidence.read_json(folder / "requirements.json")
        self.assertEqual(manifest["contract_fingerprint"], lease["contract_fingerprint"])
        self.assertEqual(manifest["requirements"][0]["id"], "tests")
        self.assertFalse(self.finish(lease).get("isError"))
        self.assertNotEqual(verification.assessment(self.repo, folder, refresh=True)["state"], "verified")

    def test_checks_and_review_assertions_are_distinct_and_all_required(self):
        self.begin()
        checked = self.check()
        with self.assertRaisesRegex(ValueError, "missing or stale review assertion"):
            self.accept()
        assertion = self.assertion()
        accepted = self.accept()
        self.assertEqual(accepted["contract_fingerprint"], self.frozen["contract_fingerprint"])
        outcomes = {row["criterion_id"]: row for row in accepted["criterion_outcomes"]}
        self.assertEqual(outcomes["tests-pass"]["provenance"], "computed_check")
        self.assertEqual(outcomes["tests-pass"]["check_id"], checked["check_id"])
        self.assertEqual(outcomes["behavior-reviewed"]["provenance"], "parent_assertion")
        self.assertEqual(outcomes["behavior-reviewed"]["assertion_id"], assertion["assertion_id"])
        self.assertEqual(verification.assessment(self.repo, self.folder, refresh=True)["state"], "verified")
        self.assertEqual(len(list((self.folder / "criteria/history").glob("*.json"))), 1)

    def test_review_cannot_supply_objective_pass(self):
        self.begin()
        with self.assertRaisesRegex(ValueError, "actual execution"):
            self.assertion(criterion_id="tests-pass")
        self.assertion()
        with self.assertRaisesRegex(ValueError, "missing required check"):
            self.accept()

    def test_failed_criterion_cannot_be_omitted(self):
        self.begin()
        self.check()
        self.assertion(result="fail")
        with self.assertRaisesRegex(ValueError, "did not pass"):
            self.accept()
        self.assertion(result="pass")
        self.assertEqual(self.accept()["state"], "verified")

    def test_failed_check_and_partial_contract_remain_unverified(self):
        spec = self.contract()
        spec["criteria"][0]["check"]["argv"] = [sys.executable, "-c", "raise SystemExit(2)"]
        self.begin(spec)
        self.assertion()
        self.check(argv=spec["criteria"][0]["check"]["argv"])
        with self.assertRaisesRegex(ValueError, "did not pass"):
            self.accept()

    def test_contract_requirements_cannot_be_rewritten_or_appended(self):
        self.begin()
        manifest = evidence.read_json(self.folder / "requirements.json")
        unchanged = verification.record_requirements(self.repo, self.folder, manifest["requirements"],
                                                     manifest["manual_criteria"], **self.auth(self.lease))
        self.assertEqual(unchanged, manifest)
        for requirements, manual in (([], manifest["manual_criteria"]),
                                     (manifest["requirements"], [*manifest["manual_criteria"], "extra"])):
            with self.assertRaisesRegex(ValueError, "frozen"):
                verification.record_requirements(self.repo, self.folder, requirements, manual, **self.auth(self.lease))

    def test_fingerprint_changes_on_revision_and_old_checks_cannot_pass(self):
        self.begin(self.contract(review=False))
        checked = self.check()
        checked["contract_fingerprint"] = "a" * 64
        evidence.write_json(self.folder / "checks" / (checked["check_id"] + ".json"), checked)
        with self.assertRaisesRegex(ValueError, "another contract"):
            self.accept()
        revised = copy.deepcopy(self.spec)
        revised["revision"] += 1
        self.assertNotEqual(contracts.fingerprint(contracts.normalize(self.repo, revised, ["subject.txt"])),
                            self.frozen["contract_fingerprint"])

    def test_stale_code_stale_assertion_and_changed_artifacts_fail_closed(self):
        self.begin()
        self.check()
        self.assertion()
        self.accept()
        (self.repo / "subject.txt").write_text("new content\n")
        self.assertNotEqual(verification.assessment(self.repo, self.folder, refresh=True)["state"], "verified")
        self.check()
        with self.assertRaisesRegex(ValueError, "stale review assertion"):
            self.accept()
        self.assertion()
        self.accept()
        (self.folder / "evidence/review.txt").write_text("changed")
        projected = verification.assessment(self.repo, self.folder, refresh=True)
        self.assertEqual(projected["state"], "pending")
        self.assertIn("evidence reference changed", projected["reason"])

    def test_contract_tamper_or_deletion_invalidates_acceptance(self):
        self.begin(self.contract(review=False))
        self.check()
        self.accept()
        path = self.folder / "acceptance-contract.json"
        saved = evidence.read_json(path)
        changed = copy.deepcopy(saved)
        changed["contract"]["revision"] = 2
        evidence.write_json(path, changed)
        self.assertNotEqual(verification.assessment(self.repo, self.folder, refresh=True)["state"], "verified")
        evidence.write_json(path, saved)
        path.unlink()
        self.assertNotEqual(verification.assessment(self.repo, self.folder, refresh=True)["state"], "verified")

    def test_old_schema_jobs_remain_optional_and_unchanged(self):
        lease = self.start()
        folder = self.repo / ".rig/jobs/writer"
        self.assertIsNone(contracts.load(self.repo, folder))
        self.assertFalse(self.finish(lease).get("isError"))
        verification.record_requirements(self.repo, folder, [], ["Legacy manual review"], **self.auth(lease))
        accepted = verification.accept(self.repo, folder, "accept", self.snapshot(), rationale="Legacy behavior remains",
                                       **self.auth(lease))
        self.assertEqual(accepted["state"], "verified")
        self.assertNotIn("contract_fingerprint", accepted)

    def test_malformed_duplicate_unknown_and_scope_contracts_rejected_before_admission(self):
        cases = []
        for field, value in (("schema_version", 0), ("schema_version", True), ("revision", False),
                             ("revision", 0), ("criteria", []), ("contract_id", "../x")):
            item = self.contract(); item[field] = value; cases.append(item)
        item = self.contract(); item["criteria"].append(copy.deepcopy(item["criteria"][0])); cases.append(item)
        item = self.contract(); item["permissions"] = ["network"]; cases.append(item)
        for scope in (["../secret"], [".git/config"], [".rig/jobs/writer/meta.json"], ["unknown.txt"], [str(self.repo / "subject.txt")]):
            item = self.contract(); item["criteria"][0]["scope"] = scope; cases.append(item)
        for item in cases:
            with self.subTest(item=item):
                result = self.call("rig_job_start", id="invalid", files=["subject.txt"], role="parent", acceptance_contract=item)
                self.assertTrue(result.get("isError"), result)
                self.assertFalse((self.repo / ".rig/jobs/invalid").exists())
                self.assertEqual(admission.list_reservations(self.repo), [])

    def test_writer_cannot_declare_independent_review_stage(self):
        spec = self.contract()
        spec["criteria"][1]["verifier_role"] = "independent-review"
        rejected = self.call("rig_job_start", id="writer", role="parent", files=["subject.txt"], acceptance_contract=spec)
        self.assertTrue(rejected.get("isError"))
        self.assertIn("separately admitted independent reviewer stage", rejected["content"][0]["text"])
        self.assertEqual(admission.list_reservations(self.repo), [])

    def test_independent_review_contract_uses_existing_protected_handoff(self):
        writer = self.start(model="gpt-6-astra")
        self.assertFalse(self.finish(writer).get("isError"))
        folder = self.repo / ".rig/jobs/writer"
        verification.record_requirements(self.repo, folder, [], ["Inspect changes"], **self.auth(writer))
        verification.accept(self.repo, folder, "accept", self.snapshot(), rationale="Inspected", next="review", **self.auth(writer))
        harness = self.repo / ".rig/harness.toml"
        harness.write_text(harness.read_text() + "claude = true\n")
        spec = self.contract(review=False)
        spec["criteria"][0]["verifier_role"] = "independent-review"
        common = {"job_id": "reviewer", "worker": "claude", "role": "review", "files": ["subject.txt"],
                  "access": "read", "owner": admission.caller_owner("native_child"), "writer_job_id": "writer",
                  "writer_snapshot_id": self.snapshot(), "acceptance_contract": spec, **self.auth(writer)}
        with self.assertRaisesRegex(ValueError, "provider"):
            admission.reserve(self.repo, model="gpt-5.4", **common)
        reviewer = admission.reserve(self.repo, model="claude-opus-5", **common)
        self.assertEqual(reviewer["acceptance_contract"]["criteria"][0]["verifier_role"], "independent-review")
        self.assertNotEqual(reviewer["attempt_id"], writer["attempt_id"])

    def test_evidence_references_reject_missing_symlink_hardlink_and_outside_paths(self):
        self.begin()
        ref = self.refs()[0]
        outside = self.repo / "outside.txt"; outside.write_text("no")
        (self.folder / "evidence/symlink.txt").symlink_to(outside)
        os.link(outside, self.folder / "evidence/hardlink.txt")
        for path in ("../outside.txt", "owner-credentials.json", "evidence/missing.txt", "evidence/symlink.txt", "evidence/hardlink.txt"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.assertion(evidence_refs=[{**ref, "path": path}])
        with self.assertRaisesRegex(ValueError, "1 to 16"):
            self.assertion(evidence_refs=[])
        with self.assertRaisesRegex(ValueError, "kind"):
            self.assertion(evidence_refs=[{**ref, "kind": "screenshot"}])

    def test_check_holds_existing_guard_against_concurrent_assertion(self):
        self.begin()
        refusals = []
        def during(_):
            try:
                self.assertion()
            except ValueError as error:
                refusals.append(str(error))
        verification.run_check(self.repo, self.folder, "tests", [sys.executable, "-c", "print('passed')"],
                               on_tick=during, **self.auth(self.lease))
        self.assertTrue(refusals)
        self.assertFalse((self.folder / "criteria/behavior-reviewed.json").exists())

    def test_child_and_wrong_owner_cannot_record_assertions(self):
        self.begin()
        with patch.dict(os.environ, {"RIG_JOB_ID": "writer"}):
            with self.assertRaisesRegex(ValueError, "parent-only"):
                self.assertion()
        wrong = self.call("rig_job_criterion", id="writer", criterion_id="behavior-reviewed",
                          contract_fingerprint=self.frozen["contract_fingerprint"], snapshot_id=self.snapshot(),
                          result="pass", rationale="wrong owner", evidence_refs=self.refs(), owner_token="wrong")
        self.assertTrue(wrong.get("isError"))

    def test_mcp_schema_and_cli_contract_roundtrip(self):
        schema = {tool["name"]: tool for tool in rig_mcp.TOOLS}
        self.assertIn("acceptance_contract", schema["rig_job_start"]["inputSchema"]["properties"])
        self.assertIn("acceptance_contract", schema["rig_job_launch"]["inputSchema"]["properties"])
        self.assertNotIn("rig_job_criterion", rig_mcp.CHILD_TOOL_NAMES)
        path = self.repo / "contract.json"; path.write_text(json.dumps(self.contract()))
        process = subprocess.run([sys.executable, str(ROOT / "scripts/jobs.py"), "--repo", str(self.repo),
                                  "start", "cli-job", "--role", "parent", "--files-json", '["subject.txt"]',
                                  "--acceptance-contract", str(path), "--json"], capture_output=True, text=True)
        self.assertEqual(process.returncode, 0, process.stderr)
        lease = json.loads(process.stdout)
        self.assertTrue(lease["contract_fingerprint"])
        self.assertTrue((self.repo / ".rig/jobs/cli-job/acceptance-contract.json").exists())

    def test_deleting_contract_markers_cannot_downgrade_to_legacy(self):
        self.begin(self.contract(review=False))
        self.check()
        self.accept()
        (self.folder / "acceptance-contract.json").unlink()
        meta = evidence.read_json(self.folder / "meta.json")
        meta.pop("contract_fingerprint")
        evidence.write_json(self.folder / "meta.json", meta)
        with self.assertRaisesRegex(ValueError, "admitted acceptance contract is missing"):
            self.accept()
        self.assertNotEqual(verification.assessment(self.repo, self.folder, refresh=True)["state"], "verified")

    def test_review_only_contract_does_not_need_a_fake_check(self):
        spec = self.contract()
        spec["criteria"] = spec["criteria"][1:]
        self.begin(spec)
        self.assertion()
        accepted = self.accept(next="complete")
        self.assertEqual(accepted["method"], "manual")
        self.assertEqual(accepted["check_ids"], [])
        self.assertEqual(admission.list_reservations(self.repo), [])
        self.assertEqual(self.accept(next="complete")["state"], "verified")

    def test_duplicate_descriptions_keep_distinct_required_criterion_ids(self):
        spec = self.contract()
        duplicate = copy.deepcopy(spec["criteria"][1]); duplicate["id"] = "other-review"
        spec["criteria"].append(duplicate)
        self.begin(spec)
        self.check()
        self.assertion()
        with self.assertRaisesRegex(ValueError, "other-review"):
            self.accept()
        self.assertion(criterion_id="other-review")
        self.assertEqual(len(self.accept()["criterion_outcomes"]), 3)

    def test_contractual_check_binding_rejects_old_attempt_evidence(self):
        self.begin(self.contract(review=False))
        checked = self.check()
        checked["attempt_id"] = "prior-attempt"
        evidence.write_json(self.folder / "checks" / (checked["check_id"] + ".json"), checked)
        with self.assertRaisesRegex(ValueError, "another contract or attempt"):
            self.accept()

    def test_review_assertion_cannot_write_through_control_directory_symlink(self):
        self.begin()
        outside = self.repo / "outside-dir"; outside.mkdir()
        (self.folder / "criteria").symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "cannot be symlinks"):
            self.assertion()
        self.assertEqual(list(outside.iterdir()), [])

    def test_mcp_criterion_and_cli_criterion_use_same_contract(self):
        self.begin()
        payload = {"criterion_id": "behavior-reviewed", "contract_fingerprint": self.frozen["contract_fingerprint"],
                   "snapshot_id": self.snapshot(), "result": "pass", "rationale": "Explicit review",
                   "evidence_refs": self.refs()}
        response = self.call("rig_job_criterion", id="writer", **payload, **self.auth(self.lease))
        self.assertFalse(response.get("isError"), response)
        self.assertEqual(json.loads(response["content"][0]["text"])["provenance"]["kind"], "parent_assertion")
        path = self.repo / "assertion.json"; path.write_text(json.dumps(payload))
        process = subprocess.run([sys.executable, str(ROOT / "scripts/verification.py"), "--repo", str(self.repo),
                                  "criterion", "writer", "--file", str(path),
                                  "--reservation-id", self.lease["reservation_id"], "--attempt-id", self.lease["attempt_id"]],
                                 capture_output=True, text=True,
                                 env={**os.environ, "RIG_OWNER_TOKEN": self.lease["owner_token"]})
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(json.loads(process.stdout)["criterion_id"], "behavior-reviewed")

    def test_assertion_rationale_provenance_or_receipt_drift_invalidates_acceptance(self):
        spec = self.contract(); spec["criteria"] = spec["criteria"][1:]
        self.begin(spec)
        assertion = self.assertion()
        self.accept()
        pointer = self.folder / "criteria/behavior-reviewed.json"
        history = self.folder / "criteria/history" / (assertion["assertion_id"] + ".json")
        for field in ("rationale", "provenance"):
            changed = copy.deepcopy(assertion)
            changed[field] = "Another review" if field == "rationale" else {**assertion[field], "owner_session": "someone-else"}
            evidence.write_json(pointer, changed)
            with self.subTest(field=field):
                self.assertNotEqual(verification.assessment(self.repo, self.folder, refresh=True)["state"], "verified")
                with self.assertRaisesRegex(ValueError, "immutable receipt"):
                    self.accept()
            evidence.write_json(pointer, assertion)
        changed = copy.deepcopy(assertion); changed["rationale"] = "Changed both stored copies"
        evidence.write_json(pointer, changed); evidence.write_json(history, changed)
        assessment = verification.assessment(self.repo, self.folder, refresh=True)
        self.assertEqual(assessment["state"], "pending")
        self.assertIn("accepted criterion evidence changed", assessment["reason"])

    def test_resolved_scope_and_leaf_symlinks_cannot_target_control_files(self):
        (self.repo / "alias").symlink_to(self.repo / ".rig", target_is_directory=True)
        (self.repo / "leaf").symlink_to(self.repo / ".rig/harness.toml")
        (self.repo / "git-alias").symlink_to(self.repo / ".git", target_is_directory=True)
        for scope in ("alias/harness.toml", "leaf", "git-alias/config"):
            spec = self.contract(review=False); spec["criteria"][0]["scope"] = [scope]
            with self.subTest(scope=scope), self.assertRaisesRegex(ValueError, "control|Git internals"):
                contracts.normalize(self.repo, spec, [scope])
        result = self.call("rig_job_start", id="unsafe", role="parent", files=["alias/harness.toml"],
                          acceptance_contract={**self.contract(review=False), "criteria": [{
                              **self.contract(review=False)["criteria"][0], "scope": ["alias/harness.toml"]}]})
        self.assertTrue(result.get("isError"), result)
        self.assertEqual(admission.list_reservations(self.repo), [])
        self.assertFalse((self.repo / ".rig/jobs/unsafe").exists())
