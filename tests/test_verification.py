#!/usr/bin/env python3
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import change_evidence as evidence
import admission
import verification


class ParentVerification(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name).resolve()
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        self.enterContext(patch.dict(os.environ, {"RIG_JOB_ID": "", "RIG_JOB_DIR": "", "RIG_PARENT": "codex"}))
        self.subject = self.repo / "subject.txt"
        self.subject.write_text("checked content\n")
        self.create_job()

    def create_job(self, job_id="writer", files=None, stopped=True):
        files = ["subject.txt"] if files is None else files
        self.owner_session = "verification-test-owner"
        owner = admission.caller_owner("parent", owner_session=self.owner_session)
        reservation = admission.reserve(self.repo, job_id=job_id, worker="parent", role="implement",
                                        files=files, access="write", owner=owner)
        self.auth = {**admission.credentials(reservation), "owner_session": self.owner_session}
        admission.activate(self.repo, job_id=job_id, worker="parent", files=files, access="write", **self.auth)
        self.job = self.repo / ".rig" / "jobs" / job_id
        self.job.mkdir(parents=True, exist_ok=True)
        self.meta = {"job_id": job_id, "worker": "parent", "role": "implement", "files": files,
                     "status": "running", "execution_mode": "parent", "dir": str(self.job),
                     "access": "write", "ownership_established": True, "owner": reservation["owner"],
                     "reservation_id": reservation["reservation_id"], "attempt_id": reservation["attempt_id"]}
        self.write_meta()
        if stopped:
            self.finish()

    def finish(self):
        admission.finish(self.repo, status="ok", completion={"kind": "parent_task", "completed": True}, **self.auth)
        self.meta["status"] = "ok"
        self.write_meta()

    def close(self):
        return admission.release(self.repo, rationale="Test owner closes completed attempt.", **self.auth)

    def reservation(self):
        return next(row for row in admission.list_reservations(self.repo, include_released=True)
                    if row["reservation_id"] == self.auth["reservation_id"])

    def cli_ownership(self):
        return ["--reservation-id", self.auth["reservation_id"], "--attempt-id", self.auth["attempt_id"],
                "--owner-session", self.owner_session]

    def cli_environment(self):
        return {**os.environ, "RIG_OWNER_TOKEN": self.auth["owner_token"]}

    def write_meta(self):
        (self.job / "meta.json").write_text(json.dumps(self.meta))

    def command(self, script="print('real stdout'); import sys; print('real stderr', file=sys.stderr)"):
        return [sys.executable, "-c", script]

    def requirements(self, rows=None, criteria=None):
        return verification.record_requirements(self.repo, self.job,
            rows if rows is not None else [{"id": "tests", "argv": self.command()}], criteria or [], **self.auth)

    def snapshot_id(self):
        return evidence.snapshot(self.repo, self.meta["files"])["snapshot_id"]

    def accept(self, **kwargs):
        return verification.accept(self.repo, self.job, "accept", kwargs.pop("snapshot_id", self.snapshot_id()),
                                   rationale=kwargs.pop("rationale", "All declared criteria satisfied."), **self.auth, **kwargs)

    def check(self, name, argv, **kwargs):
        return verification.run_check(self.repo, self.job, name, argv, **self.auth, **kwargs)

    def test_real_check_artifacts_progress_and_parent_acceptance(self):
        self.requirements()
        ticks = []
        def progress(job):
            ticks.append(job)
            self.assertTrue((self.job / "check-running.json").is_file())
        checked = self.check("tests", self.command(), on_tick=progress)
        self.assertEqual(checked["exit_code"], 0)
        self.assertEqual(checked["before_snapshot_id"], checked["after_snapshot_id"])
        self.assertEqual(ticks[0]["effective"], "verifying")
        self.assertEqual(ticks[0]["doing"], "tests")
        self.assertFalse((self.job / "check-running.json").exists())
        self.assertIn("real stdout", (self.job / checked["stdout"]["path"]).read_text())
        self.assertIn("real stderr", (self.job / checked["stderr"]["path"]).read_text())
        self.assertEqual(verification.assessment(self.repo, self.meta, refresh=True)["state"], "pending")
        accepted = self.accept(next="review")
        self.assertEqual(accepted["state"], "verified")
        self.assertEqual(accepted["check_ids"], [checked["check_id"]])
        projected = verification.assessment(self.repo, self.meta, refresh=True)
        self.assertEqual((projected["state"], projected["acceptance"], projected["freshness"]), ("verified", "accepted", "current"))

    def test_missing_or_failed_required_check_cannot_be_omitted(self):
        passing, failing = self.command(), self.command("import sys; sys.exit(7)")
        self.requirements([{"id": "lint", "argv": passing}, {"id": "tests", "argv": failing}])
        first = self.check("lint", passing)
        with self.assertRaisesRegex(verification.VerificationError, "missing required check"):
            self.accept(check_ids=[first["check_id"]])
        failed = self.check("tests", failing)
        self.assertEqual(failed["exit_code"], 7)
        with self.assertRaisesRegex(verification.VerificationError, "did not pass"):
            self.accept(check_ids=[first["check_id"]])

    def test_requirements_cannot_remove_redefine_or_rename_failed_check(self):
        failing = self.command("raise SystemExit(1)")
        self.requirements([{"id": "required", "argv": failing}], ["Manual criterion"])
        self.check("required", failing)
        for rows, criteria in (([], ["Manual criterion"]),
                               ([{"id": "renamed", "argv": failing}], ["Manual criterion"]),
                               ([{"id": "required", "argv": self.command()}], ["Manual criterion"]),
                               ([{"id": "required", "argv": failing}], [])):
            with self.subTest(rows=rows), self.assertRaisesRegex(verification.VerificationError, "only append"):
                self.requirements(rows, criteria)
        appended = self.requirements([{"id": "required", "argv": failing}, {"id": "extra", "argv": self.command()}],
                                     ["Manual criterion", "Additional criterion"])
        self.assertEqual(len(appended["requirements"]), 2)

    def test_identical_retry_supersedes_failure_and_preserves_history(self):
        control = self.repo / "control.txt"
        control.write_text("1")
        command = self.command("from pathlib import Path; raise SystemExit(int(Path('control.txt').read_text()))")
        self.requirements([{"id": "tests", "argv": command}])
        failed = self.check("tests", command)
        control.write_text("0")
        passed = self.check("tests", command)
        accepted = self.accept()
        self.assertIn(passed["check_id"], accepted["check_ids"])
        self.assertTrue((self.job / "checks" / (failed["check_id"] + ".json")).exists())
        self.assertTrue(any(value.get("state") == "failed" for value in accepted["history"]))

    def test_distinct_pass_and_appended_requirements_preserve_binding_failure(self):
        import jobs

        control = self.repo / "control.txt"
        control.write_text("1")
        failing = self.command("from pathlib import Path; raise SystemExit(int(Path('control.txt').read_text()))")
        passing = self.command()
        required = [{"id": "required-a", "argv": failing}, {"id": "required-b", "argv": passing}]
        self.requirements(required)
        self.check("required-a", failing)
        self.check("required-b", passing)
        for append in (False, True):
            if append:
                self.requirements(required, ["Inspect the final wording"])
            assessment = verification.assessment(self.repo, self.meta, refresh=True)
            self.assertEqual(assessment["state"], "failed")
            self.assertIn("required-a", assessment["reason"])
            self.assertNotIn("content_changed", assessment["reason"])
            self.assertEqual(jobs.job_display_state(self.meta, verification=assessment), "failed")
            with self.assertRaisesRegex(verification.VerificationError, "required check did not pass: required-a"):
                self.accept()
        control.write_text("0")
        self.check("required-a", failing)
        self.assertEqual(verification.assessment(self.repo, self.meta, refresh=True)["state"], "pending")
        self.assertEqual(self.accept()["state"], "verified")

    def test_exact_argv_cwd_and_declared_requirement_are_required(self):
        (self.repo / "subdir").mkdir()
        command = self.command()
        self.requirements([{"id": "tests", "argv": command, "cwd": "subdir"}])
        for name, argv, cwd in (("tests", command, "."), ("tests", self.command("print('different')"), "subdir"),
                                ("renamed", command, "subdir"), ("tests", command, "../")):
            with self.subTest(name=name, cwd=cwd), self.assertRaises(ValueError):
                self.check(name, argv, cwd=cwd)
        self.assertEqual(self.check("tests", command, cwd="subdir")["exit_code"], 0)

    def test_check_that_changes_subject_does_not_verify(self):
        command = self.command("from pathlib import Path; Path('subject.txt').write_text('changed during check')")
        self.requirements([{"id": "tests", "argv": command}])
        checked = self.check("tests", command)
        self.assertEqual(checked["exit_code"], 0)
        self.assertNotEqual(checked["before_snapshot_id"], checked["after_snapshot_id"])
        with self.assertRaisesRegex(verification.VerificationError, "unchanged current content"):
            self.accept()

    def test_content_changes_after_check_and_acceptance_invalidate(self):
        self.requirements()
        self.check("tests", self.command())
        accepted = self.accept(next="review")
        self.subject.write_text("edited after acceptance")
        result = verification.assessment(self.repo, self.meta, refresh=True)
        self.assertEqual((result["state"], result["reason"]), ("pending", "content_changed"))
        with self.assertRaisesRegex(verification.VerificationError, "content_changed"):
            self.accept(snapshot_id=accepted["snapshot_id"])
        with self.assertRaisesRegex(verification.VerificationError, "unchanged current content"):
            self.accept()

    def test_low_level_assessment_never_hashes_or_rereads_supplied_metadata(self):
        self.requirements([], ["Reviewed documentation"])
        self.accept()
        real_reader = evidence.read_json
        def reader(path):
            self.assertNotEqual(Path(path).name, "meta.json")
            return real_reader(path)
        with patch.object(evidence, "snapshot", side_effect=AssertionError("history hashing")), patch.object(evidence, "read_json", reader):
            result = verification.assessment(self.repo, self.meta)
        self.assertEqual(result["freshness"], "not_checked")

    def test_manual_acceptance_requires_manifest_rationale_and_current_subject(self):
        with self.assertRaises(verification.VerificationError):
            self.accept()
        with self.assertRaises(verification.VerificationError):
            self.requirements([], [])
        self.requirements([], ["Reviewed wording against the requested documentation change"])
        with self.assertRaisesRegex(verification.VerificationError, "rationale"):
            self.accept(rationale=" ")
        accepted = self.accept(rationale="Wording matches the requested documentation change.")
        self.assertEqual(accepted["method"], "manual")
        self.assertEqual(accepted["check_ids"], [])
        self.assertEqual(accepted, self.accept(rationale=accepted["rationale"]))

    def test_mixed_manual_criteria_do_not_bypass_automated_requirements(self):
        self.requirements(criteria=["Review documentation wording"])
        with self.assertRaisesRegex(verification.VerificationError, "missing required check"):
            self.accept()
        self.check("tests", self.command())
        self.assertEqual(self.accept()["method"], "mixed")

    def test_legacy_dry_run_and_failed_execution_never_verify(self):
        self.requirements([], ["Manual criterion"])
        for mode, status in ((None, "ok"), ("dry_run", "ok"), ("live", "fail"),
                             ("live", "timeout"), ("native", "cancelled"), ("parent", "running")):
            self.meta.update(execution_mode=mode, status=status)
            self.write_meta()
            with self.subTest(mode=mode, status=status), self.assertRaises(verification.VerificationError):
                self.accept()
            self.assertNotEqual(verification.assessment(self.repo, self.meta, refresh=True)["state"], "verified")

    def test_child_claims_are_parsed_but_never_executed(self):
        marker = self.repo / "unexpected"
        (self.job / "worker-evidence.json").write_text(json.dumps({"version": 1, "summary": "passed",
            "claimed_checks": [f"touch {marker}"], "claimed_files": ["subject.txt"]}))
        with patch.object(subprocess, "Popen", side_effect=AssertionError("executed a child claim")):
            claims = verification.worker_claims(self.job)
            verification.assessment(self.repo, self.meta, refresh=True)
        self.assertFalse(claims["trusted"])
        self.assertFalse(marker.exists())

    def test_changed_check_artifact_downgrades_accepted_projection(self):
        self.requirements()
        checked = self.check("tests", self.command())
        self.accept()
        (self.job / checked["stdout"]["path"]).write_text("different output")
        result = verification.assessment(self.repo, self.meta, refresh=True)
        self.assertNotEqual(result["state"], "verified")
        self.assertIn("artifact changed", result["reason"])

    def test_active_check_record_clears_and_evidence_survives_callback_exception(self):
        command = self.command("import time; time.sleep(10)")
        self.requirements([{"id": "tests", "argv": command}])
        with self.assertRaisesRegex(RuntimeError, "callback stopped"):
            self.check("tests", command,
                on_tick=lambda job: (_ for _ in ()).throw(RuntimeError("callback stopped")))
        self.assertFalse((self.job / "check-running.json").exists())
        records = list((self.job / "checks").glob("*.json"))
        self.assertEqual(len(records), 1)
        self.assertEqual(json.loads(records[0].read_text())["status"], "error")
        self.assertNotIn("operation", self.reservation())
        self.assertEqual(self.reservation()["stage"], "verifying")

    def test_missing_executable_leaves_error_artifact_and_clears_active_check(self):
        command = [str(self.repo / "missing-program")]
        self.requirements([{"id": "tests", "argv": command}])
        with self.assertRaises(FileNotFoundError):
            self.check("tests", command)
        self.assertFalse((self.job / "check-running.json").exists())
        row = json.loads(next((self.job / "checks").glob("*.json")).read_text())
        self.assertIsNone(row["exit_code"])
        self.assertEqual(row["status"], "error")
        self.assertNotIn("operation", self.reservation())
        self.assertEqual(self.reservation()["stage"], "verifying")

    def test_parent_only_mutations_and_cli_reject_child_environment(self):
        with patch.dict(os.environ, {"RIG_JOB_ID": "writer"}):
            with self.assertRaisesRegex(verification.VerificationError, "parent-only"):
                self.requirements()
            result = subprocess.run([sys.executable, str(ROOT / "scripts" / "verification.py"), "--repo", str(self.repo),
                                     "check", "writer", "--name", "tests", "--", *self.command()], text=True, capture_output=True)
            self.assertEqual(result.returncode, 2)
            self.assertIn("parent-only", result.stderr)

    def test_cli_check_accepts_declared_subdirectory(self):
        (self.repo / "subdir").mkdir()
        command = self.command()
        self.requirements([{"id": "tests", "argv": command, "cwd": "subdir"}])
        result = subprocess.run([sys.executable, str(ROOT / "scripts" / "verification.py"), "--repo", str(self.repo),
                                 "check", "writer", "--name", "tests", "--cwd", "subdir", *self.cli_ownership(), "--", *command],
                                text=True, capture_output=True, env=self.cli_environment())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["cwd"], "subdir")

    def test_empty_subject_cannot_be_accepted(self):
        self.close()
        self.create_job("unknown-writer", files=[])
        with self.assertRaises(evidence.EvidenceError):
            self.requirements([], ["Remote command succeeded"])

    def test_malformed_accepted_state_and_missing_sidecars_never_verify(self):
        self.requirements([], ["Manual criterion"])
        accepted = self.accept()
        for key, value in (("acceptance", None), ("acceptance", "pending"), ("acceptance", "invalid"),
                           ("method", ""), ("rationale", ""), ("accepted_at", None), ("check_ids", None)):
            malformed = {**accepted, key: value}
            (self.job / "verification.json").write_text(json.dumps(malformed))
            with self.subTest(key=key, value=value):
                self.assertNotEqual(verification.assessment(self.repo, self.meta, refresh=True)["state"], "verified")
                self.assertNotEqual(verification.assessment(self.repo, self.meta)["state"], "verified")
        (self.job / "verification.json").write_text(json.dumps(accepted))
        (self.job / "requirements.json").unlink()
        self.assertNotEqual(verification.assessment(self.repo, self.meta, refresh=True)["state"], "verified")

    def test_symlink_target_edit_invalidates_manual_acceptance(self):
        (self.repo / "link").symlink_to("subject.txt")
        self.close()
        self.create_job("linked-writer", files=["link"])
        self.requirements([], ["Manual criterion"])
        self.accept()
        self.subject.write_text("different target content")
        result = verification.assessment(self.repo, self.meta, refresh=True)
        self.assertEqual((result["state"], result["reason"]), ("pending", "content_changed"))

    def test_interrupted_or_malformed_check_activity_is_unverified(self):
        self.requirements([], ["Manual criterion"])
        self.accept()
        (self.job / "check-running.json").write_text(json.dumps({"version": 1, "pid": 2147483647}))
        result = verification.assessment(self.repo, self.meta, refresh=True)
        self.assertEqual((result["state"], result["reason"]), ("pending", "interrupted_check"))
        (self.job / "check-running.json").write_text("{")
        result = verification.assessment(self.repo, self.meta, refresh=True)
        self.assertEqual((result["state"], result["reason"]), ("pending", "malformed_check_activity"))

    def test_active_marker_clears_if_initial_assessment_write_fails(self):
        self.requirements()
        with patch.object(verification, "_save_assessment", side_effect=OSError("write failed")):
            with self.assertRaises(OSError):
                self.check("tests", self.command())
        self.assertFalse((self.job / "check-running.json").exists())
        self.assertNotIn("operation", self.reservation())

    def test_cli_preserves_safe_underscore_job_ids_and_unique_partial_lookup(self):
        self.close()
        self.create_job("_writer")
        self.requirements()
        for job_id in ("_writer", "_writ"):
            with self.subTest(job_id=job_id):
                result = subprocess.run([sys.executable, str(ROOT / "scripts" / "verification.py"),
                    "--repo", str(self.repo), "check", job_id, "--name", "tests", *self.cli_ownership(), "--", *self.command()],
                    text=True, capture_output=True, env=self.cli_environment())
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout)["job_id"], "_writer")

    def test_malformed_execution_status_never_projects_verified(self):
        self.requirements([], ["Manual criterion"])
        self.accept()
        for status in (None, [], {}, True, "unknown"):
            self.meta["status"] = status
            self.write_meta()
            with self.subTest(status=status):
                self.assertNotEqual(verification.assessment(self.repo, self.meta, refresh=True)["state"], "verified")

    def test_mutations_require_explicit_matching_attempt_and_owner(self):
        self.requirements([], ["Manual criterion"])
        before = (self.job / "verification.json").read_bytes()
        invalid = ({}, {**self.auth, "owner_token": ""}, {**self.auth, "owner_token": "wrong-token"},
                   {**self.auth, "attempt_id": "different-attempt"},
                   {**self.auth, "owner_session": "another-parent-session"})
        for credentials in invalid:
            for operation in (lambda: verification.record_requirements(self.repo, self.job, [], ["Changed criterion"], **credentials),
                              lambda: verification.run_check(self.repo, self.job, "tests", self.command(), **credentials),
                              lambda: verification.accept(self.repo, self.job, "accept", self.snapshot_id(),
                                                          rationale="Criterion satisfied", **credentials)):
                with self.subTest(credentials=list(credentials)), self.assertRaises(admission.AdmissionError):
                    operation()
                self.assertEqual((self.job / "verification.json").read_bytes(), before)
        self.assertFalse((self.job / "checks").exists())
        self.assertNotIn("operation", self.reservation())

    def test_cli_never_recovers_owner_token_from_job_or_credential_artifact(self):
        self.requirements()
        # The caller artifact may exist, but mutation authorization is supplied
        # explicitly; knowing the job ID and public attempt IDs is insufficient.
        admission.write_credentials(self.repo, {**self.reservation(), "owner_token": self.auth["owner_token"]})
        environment = {**os.environ, "RIG_OWNER_TOKEN": ""}
        result = subprocess.run([sys.executable, str(ROOT / "scripts" / "verification.py"),
            "--repo", str(self.repo), "check", self.job.name, "--name", "tests",
            *self.cli_ownership(), "--", *self.command()], text=True, capture_output=True, env=environment)
        self.assertEqual(result.returncode, 2)
        self.assertNotIn(self.auth["owner_token"], result.stdout + result.stderr)
        self.assertFalse((self.job / "checks").exists())

    def test_checks_and_acceptance_require_confirmed_completion(self):
        self.close()
        self.create_job("active-writer", stopped=False)
        self.requirements([], ["Manual criterion"])
        for operation in (lambda: self.check("tests", self.command()), self.accept):
            with self.assertRaisesRegex(admission.AdmissionError, "confirmed stopped"):
                operation()
        # A status file alone never proves execution completion.
        self.meta["status"] = "ok"
        self.write_meta()
        with self.assertRaisesRegex(admission.AdmissionError, "confirmed stopped"):
            self.accept()
        self.finish()
        self.assertEqual(self.accept()["state"], "verified")

    def test_running_check_protects_scope_without_holding_admission_lock(self):
        self.requirements()
        def progress(job):
            reservation = self.reservation()
            self.assertEqual(reservation["operation"]["operation"], "check")
            self.assertFalse(reservation["slot_held"])
            with self.assertRaisesRegex(admission.AdmissionError, "operation"):
                self.close()
            with self.assertRaises(admission.AdmissionError):
                self.accept()
            with self.assertRaises(admission.AdmissionError):
                admission.reserve(self.repo, job_id="conflicting-writer", worker="parent", files=["subject.txt"],
                                  access="write", owner_session=self.owner_session)
            script = "import sys; sys.path.insert(0, sys.argv[1]); import admission; " \
                     "guard=admission.transaction(sys.argv[2]); guard.__enter__(); print('acquired'); guard.__exit__(None,None,None)"
            unlocked = subprocess.run([sys.executable, "-c", script, str(ROOT / "scripts"), str(self.repo)],
                                      text=True, capture_output=True, timeout=3)
            self.assertEqual(unlocked.returncode, 0, unlocked.stderr)
            self.assertEqual(unlocked.stdout.strip(), "acquired")
        self.check("tests", self.command(), on_tick=progress)
        self.assertNotIn("operation", self.reservation())
        self.assertEqual(self.reservation()["stage"], "verifying")

    def test_accepted_completion_releases_and_only_identical_repeat_is_allowed(self):
        self.requirements([], ["Manual criterion"])
        accepted = self.accept()
        self.assertEqual(self.reservation()["stage"], "released")
        self.assertEqual(accepted["parent_identity"]["thread"], self.owner_session)
        self.assertEqual(self.accept(), accepted)
        for changes in ({"rationale": "Changed parent rationale"}, {"next": "review"}):
            with self.subTest(changes=changes), self.assertRaisesRegex(verification.VerificationError, "identical"):
                self.accept(**changes)
        with self.assertRaises(admission.AdmissionError):
            self.requirements([], ["New criterion"])
        with self.assertRaises(admission.AdmissionError):
            self.check("tests", self.command())
        self.subject.write_text("changed after ownership release")
        with self.assertRaises(ValueError):
            self.accept()
        self.assertEqual(evidence.read_json(self.job / "verification.json"), accepted)

    def test_review_and_rejection_retain_file_protection_until_explicit_close(self):
        self.requirements([], ["Manual criterion"])
        self.accept(next="review")
        reservation = self.reservation()
        self.assertEqual(reservation["stage"], "verifying")
        self.assertTrue(reservation["stopped"])
        self.assertFalse(reservation["slot_held"])
        with self.assertRaises(admission.AdmissionError):
            admission.reserve(self.repo, job_id="overlapping-review", worker="parent", files=["subject.txt"],
                              access="write", owner_session=self.owner_session)
        rejected = verification.accept(self.repo, self.job, "reject", self.snapshot_id(),
                                       rationale="Further changes needed.", **self.auth)
        self.assertEqual(rejected["acceptance"], "rejected")
        self.assertEqual(self.reservation()["stage"], "verifying")
        self.assertEqual(self.close()["stage"], "released")

    def test_retrospective_and_unreserved_metadata_cannot_inherit_acceptance(self):
        self.requirements([], ["Manual criterion"])
        self.accept(next="review")
        original = dict(self.meta)
        for updates, reason in (({"execution_mode": "retrospective"}, "retrospective_execution"),
                                ({"ownership_established": False}, "unreserved_execution"),
                                ({"reservation_id": ""}, "unreserved_execution"),
                                ({"attempt_id": ""}, "unreserved_execution")):
            self.meta = {**original, **updates}
            self.write_meta()
            with self.subTest(updates=updates):
                for refresh in (False, True):
                    result = verification.assessment(self.repo, self.meta, refresh=refresh)
                    self.assertEqual((result["state"], result["reason"]), ("pending", reason))
                with self.assertRaises(ValueError):
                    self.accept()

    def test_job_metadata_and_accepted_sidecar_must_match_owned_attempt(self):
        self.requirements([], ["Manual criterion"])
        accepted = self.accept(next="review")
        self.meta["attempt_id"] = "different-attempt"
        self.write_meta()
        with self.assertRaises(admission.AdmissionError):
            self.accept()
        self.assertEqual(verification.assessment(self.repo, self.meta)["reason"], "acceptance_attempt_changed")
        self.meta["attempt_id"] = self.auth["attempt_id"]
        self.write_meta()
        evidence.write_json(self.job / "verification.json", {**accepted, "reservation_id": "another-reservation"})
        self.assertEqual(verification.assessment(self.repo, self.meta, refresh=True)["reason"], "acceptance_attempt_changed")


if __name__ == "__main__":
    unittest.main()
