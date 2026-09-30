"""UI packs assemble local reviewed evidence without granting capture privileges."""
import copy
import hashlib
import json
import os
from pathlib import Path
import stat
import struct
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch
import zlib

from repo_test_support import initialize_project

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import admission
import change_evidence
import rig_mcp
import ui_evidence as ui


def png(color=0):
    def chunk(kind, value):
        return struct.pack(">I", len(value)) + kind + value + struct.pack(">I", zlib.crc32(kind + value) & 0xffffffff)
    return (ui.PNG_MAGIC + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes([0, color, color, color]))) + chunk(b"IEND", b""))


def digest(data):
    return hashlib.sha256(data).hexdigest()


class UIPacks(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name).resolve()
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        initialize_project(self.repo)
        (self.repo / ".gitignore").write_text(".rig/\n")
        self.file = self.repo / "subject.txt"
        self.file.write_text("current content\n")
        self.enterContext(patch.dict(os.environ, {"RIG_PARENT": "codex", "RIG_JOB_ID": "", "RIG_JOB_DIR": "",
                                                "RIG_THREAD": "ui-evidence-test"}))
        self.session = "ui-evidence-test"
        owner = admission.caller_owner("parent", owner_session=self.session)
        record = admission.reserve(self.repo, job_id="ui-check", worker="parent", role="implement",
                                   files=["subject.txt"], access="write", owner=owner)
        self.auth = {**admission.credentials(record), "owner_session": self.session}
        admission.activate(self.repo, job_id="ui-check", worker="parent", files=["subject.txt"], access="write", **self.auth)
        self.folder = self.repo / ".rig/jobs/ui-check"
        self.folder.mkdir(parents=True, exist_ok=True)
        self.meta = {"job_id": "ui-check", "files": ["subject.txt"], "status": "ok", "worker": "parent",
                     "role": "implement", "ownership_established": True, "owner": record["owner"],
                     "reservation_id": record["reservation_id"], "attempt_id": record["attempt_id"]}
        (self.folder / "meta.json").write_text(json.dumps(self.meta))
        admission.finish(self.repo, status="ok", completion={"kind": "parent_task", "completed": True}, **self.auth)
        self.contract = {"job_id": "ui-check", "reservation_id": record["reservation_id"], "attempt_id": record["attempt_id"],
                         "contract_fingerprint": "a" * 64, "contract": {"criteria": [
                             {"id": "layout", "evidence_type": "review_assertion", "artifact_kind": "ui-pack"}]}}
        self.contract_module = types.SimpleNamespace(load=lambda *args, **kwargs: copy.deepcopy(self.contract))
        self.enterContext(patch.dict(sys.modules, {"acceptance_contract": self.contract_module}))
        self.image_path = self.repo / ".rig/cu-evidence/capture.png"
        self.image_path.parent.mkdir()
        self.image_path.write_bytes(png())
        self.pack = {"privacy_reviewed": True, "contract_fingerprint": "a" * 64,
                     "content_snapshot_id": change_evidence.snapshot(self.repo, ["subject.txt"])["snapshot_id"],
                     "criterion_ids": ["layout"], "steps": [{"id": "open", "receipt": {
                         "version": "rig.cu.v1", "operation": "capture", "status": "captured", "effect": "captured",
                         "snapshot": {"id": "observed-window-1", "freshness": "fresh"},
                         "image": {"available": True, "sha256": digest(png())},
                     }, "image": {"path": ".rig/cu-evidence/capture.png", "sha256": digest(png()), "privacy": "reviewed"},
                         "viewport": {"width": 320, "height": 480, "coordinate_space": "viewport_css_px"},
                         "parent_observation": {"expected": "Button is visible", "observed": "Button is visible"}}]}

    def record(self, pack=None, **auth):
        return ui.record_pack(self.repo, self.folder, self.pack if pack is None else pack, **(auth or self.auth))

    def manifest(self, result):
        return json.loads((self.folder / result["evidence_ref"]["path"]).read_text())

    def test_private_immutable_roundtrip_and_idempotence(self):
        before = self.image_path.read_bytes()
        result = self.record()
        manifest = self.manifest(result)
        self.assertEqual(result["acceptance"], "unchanged")
        self.assertFalse((self.folder / "verification.json").exists())
        self.assertEqual(manifest["steps"][0]["receipt"]["provenance"]["type"], "supplied_unverified")
        self.assertFalse(manifest["steps"][0]["receipt"]["provenance"]["backend_authenticated"])
        self.assertEqual(manifest["steps"][0]["receipt"]["record"]["observation_snapshot_id"], "observed-window-1")
        self.assertNotEqual(manifest["content_snapshot_id"], "observed-window-1")
        path = self.folder / result["evidence_ref"]["path"]
        stamp = path.stat().st_mtime_ns
        self.assertEqual(self.record(), result)
        self.assertEqual(path.stat().st_mtime_ns, stamp)
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(path.parent.stat().st_mode), 0o700)
        self.assertEqual(self.image_path.read_bytes(), before)
        self.assertEqual(ui.validate_pack_reference(self.folder, result["evidence_ref"]), manifest)

    def test_staged_job_input_is_allowed(self):
        target = self.folder / "evidence/inputs/shot.png"
        target.parent.mkdir(parents=True)
        target.write_bytes(png())
        self.pack["steps"][0]["image"]["path"] = target.relative_to(self.repo).as_posix()
        self.assertEqual(self.record()["coverage"]["available_images"], 1)

    def test_recorded_receipt_hash_and_projection_omit_private_fields(self):
        receipt = self.pack["steps"][0].pop("receipt")
        receipt.update(observation={"outline": "password=never-persist-this https://example.test/?key=abc"},
                       typed_text="do-not-copy", url="https://example.test/?key=abc")
        raw = json.dumps({"receipt": receipt}).encode()
        target = self.repo / ".rig/cu-evidence/receipt.json"
        target.write_bytes(raw)
        self.pack["steps"][0]["receipt_file"] = {"path": target.relative_to(self.repo).as_posix(), "sha256": digest(raw)}
        result = self.record()
        manifest = self.manifest(result)
        output = json.dumps(manifest)
        self.assertNotIn("never-persist", output)
        self.assertNotIn("do-not-copy", output)
        self.assertNotIn("example.test", output)
        provenance = manifest["steps"][0]["receipt"]["provenance"]
        self.assertEqual(provenance["type"], "recorded_receipt_file")
        self.assertEqual(provenance["source_sha256"], digest(raw))
        target.write_text("changed")
        with self.assertRaisesRegex(ValueError, "hash"):
            self.record()

    def test_bsk_receipt_without_image_hash(self):
        self.pack["steps"][0]["receipt"] = {"version": "rig.bsk.v1", "operation": "observe", "status": "observed",
            "effect": "observed", "snapshot": {"id": "bsk-1", "freshness": "fresh"}}
        self.assertEqual(self.record()["coverage"]["available_images"], 1)

    def test_redaction_retains_original_hash_without_original_bytes(self):
        self.image_path.write_bytes(png(255))
        image = self.pack["steps"][0]["image"]
        image.update(privacy="redacted", sha256=digest(png(255)), original_sha256=digest(png()))
        result = self.record()
        stored = self.manifest(result)["steps"][0]["image"]
        self.assertEqual(stored["original_sha256"], digest(png()))
        self.assertEqual(stored["sha256"], digest(png(255)))
        self.assertEqual(self.image_path.read_bytes(), png(255))

    def test_redaction_cannot_relabel_unchanged_or_unrelated_bytes(self):
        image = self.pack["steps"][0]["image"]
        image.update(privacy="redacted", original_sha256=digest(png()))
        with self.assertRaisesRegex(ValueError, "redaction"):
            self.record()
        image.update(original_sha256="b" * 64)
        with self.assertRaisesRegex(ValueError, "redaction"):
            self.record()

    def test_missing_image_is_explicitly_unavailable_not_replaced(self):
        self.image_path.unlink()
        result = self.record()
        self.assertEqual(result["coverage"], {"steps": 1, "available_images": 0, "unavailable_images": 1})
        manifest = ui.validate_pack_reference(self.folder, result["evidence_ref"])
        self.assertEqual(manifest["steps"][0]["image"]["reason"], "missing_at_recording")
        self.assertNotIn("pass", json.dumps(manifest))

    def test_no_image_is_explicit(self):
        del self.pack["steps"][0]["image"]
        result = self.record()
        self.assertEqual(result["coverage"]["unavailable_images"], 1)

    def test_available_image_deletion_or_tampering_invalidates_reference(self):
        result = self.record()
        path = (self.folder / result["evidence_ref"]["path"]).parent / "images/open.png"
        path.write_bytes(png(1))
        with self.assertRaisesRegex(ValueError, "changed"):
            ui.validate_pack_reference(self.folder, result["evidence_ref"])
        path.unlink()
        with self.assertRaisesRegex(ValueError, "missing"):
            ui.validate_pack_reference(self.folder, result["evidence_ref"])

    def test_manifest_tampering_and_current_content_change_fail(self):
        result = self.record()
        self.file.write_text("new content")
        with self.assertRaisesRegex(ValueError, "snapshot"):
            ui.validate_pack_reference(self.folder, result["evidence_ref"])
        self.file.write_text("current content\n")
        path = self.folder / result["evidence_ref"]["path"]
        path.write_text("{}")
        with self.assertRaisesRegex(ValueError, "changed"):
            ui.validate_pack_reference(self.folder, result["evidence_ref"])

    def test_attempt_and_contract_cannot_be_reused(self):
        result = self.record()
        self.contract["attempt_id"] = "replacement-attempt"
        with self.assertRaisesRegex(ValueError, "different attempt"):
            ui.validate_pack_reference(self.folder, result["evidence_ref"])
        self.pack["contract_fingerprint"] = "b" * 64
        with self.assertRaisesRegex(ValueError, "contract"):
            self.record()

    def test_unauthenticated_wrong_attempt_and_child_refused(self):
        with self.assertRaises(ValueError):
            self.record(owner_token="wrong", reservation_id=self.auth["reservation_id"], attempt_id=self.auth["attempt_id"], owner_session=self.session)
        with self.assertRaises(ValueError):
            self.record(**{**self.auth, "attempt_id": "wrong"})
        with patch.dict(os.environ, {"RIG_JOB_ID": "child"}):
            with self.assertRaisesRegex(ValueError, "parent-only"):
                self.record()
        self.assertFalse((self.folder / "evidence/ui-packs").exists())

    def test_running_attempt_refused(self):
        with admission.transaction(self.repo):
            record = admission._read(admission._reservation_path(self.repo, self.auth["reservation_id"]), required=True)
            record["stopped"] = False
            admission._save(self.repo, record)
        with self.assertRaisesRegex(ValueError, "stopped"):
            self.record()

    def test_source_hash_must_match_input_and_receipt(self):
        self.image_path.write_bytes(png(1))
        with self.assertRaisesRegex(ValueError, "hash"):
            self.record()
        self.pack["steps"][0]["image"]["sha256"] = digest(png(1))
        with self.assertRaisesRegex(ValueError, "receipt"):
            self.record()

    def test_paths_symlinks_and_hardlinks_rejected(self):
        image = self.pack["steps"][0]["image"]
        for path in ("subject.txt", "../outside.png", "/tmp/capture.png", ".rig/cu-evidence/../capture.png",
                     ".rig/jobs/other/evidence/inputs/shot.png"):
            image["path"] = path
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.record()
        image["path"] = ".rig/cu-evidence/capture.png"
        self.image_path.unlink()
        self.image_path.symlink_to(self.file)
        with self.assertRaises(ValueError):
            self.record()
        self.image_path.unlink()
        sibling = self.repo / "sibling.png"
        sibling.write_bytes(png())
        os.link(sibling, self.image_path)
        with self.assertRaisesRegex(ValueError, "hardlinked"):
            self.record()

    def test_destination_symlink_rejected(self):
        (self.folder / "evidence").symlink_to(self.repo, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "symlink"):
            self.record()
        self.assertFalse((self.repo / "ui-packs").exists())

    def test_privacy_and_sensitive_metadata_do_not_echo_values(self):
        self.pack["privacy_reviewed"] = False
        with self.assertRaisesRegex(ValueError, "privacy"):
            self.record()
        self.pack["privacy_reviewed"] = True
        for value in ("password=unique-private-value", "https://example.test/?key=unique-private-value",
                      "sk-unique-private-value"):
            self.pack["steps"][0]["parent_observation"]["observed"] = value
            with self.assertRaises(ValueError) as ctx:
                self.record()
            self.assertNotIn("unique-private-value", str(ctx.exception))

    def test_sensitive_identifiers_are_rejected_without_echo_or_files(self):
        value = "ghp_" + "Q" * 28
        self.pack["steps"][0]["id"] = value
        with self.assertRaises(ValueError) as ctx:
            self.record()
        self.assertNotIn(value, str(ctx.exception))
        self.assertFalse((self.folder / "evidence/ui-packs").exists())
        self.pack["steps"][0]["id"] = "open"
        self.contract["contract"]["criteria"][0]["id"] = value
        self.pack["criterion_ids"] = [value]
        with self.assertRaises(ValueError) as ctx:
            self.record()
        self.assertNotIn(value, str(ctx.exception))
        self.assertFalse((self.folder / "evidence/ui-packs").exists())
        self.contract["contract"]["criteria"][0]["id"] = "layout"
        self.pack["criterion_ids"] = ["layout"]
        self.contract["job_id"] = value
        with self.assertRaises(ValueError) as ctx:
            self.record()
        self.assertNotIn(value, str(ctx.exception))

    def test_bounds_unknown_fields_and_malformed_images(self):
        for mutate in (lambda p: p.update(steps=[]), lambda p: p.update(steps=p["steps"] * 33),
                       lambda p: p.update(criterion_ids=[{}]), lambda p: p.update(capture=True),
                       lambda p: p["steps"][0].update(viewport={"width": True}),
                       lambda p: p["steps"][0]["receipt"].update(version=[]),
                       lambda p: p["steps"][0]["image"].update(privacy={}),
                       lambda p: p["steps"][0]["parent_observation"].update(observed="x" * 1001)):
            p = copy.deepcopy(self.pack)
            mutate(p)
            with self.assertRaises(ValueError):
                self.record(p)
        self.image_path.write_bytes(b"not png")
        self.pack["steps"][0]["image"]["sha256"] = digest(b"not png")
        self.pack["steps"][0]["receipt"]["image"]["sha256"] = digest(b"not png")
        with self.assertRaisesRegex(ValueError, "PNG"):
            self.record()
        with patch.object(ui, "MAX_IMAGE_BYTES", 2):
            with self.assertRaisesRegex(ValueError, "limit"):
                self.record()

    def test_no_capture_provider_shell_or_acceptance_side_effects(self):
        with patch("computer_use.invoke_driver", side_effect=AssertionError("no capture")), \
             patch("browser_skill.invoke_bsk", side_effect=AssertionError("no browser")), \
             patch("worker_launch.launch", side_effect=AssertionError("no provider")), \
             patch("verification.accept", side_effect=AssertionError("no acceptance")):
            self.record()
        self.assertNotIn("rig_job_ui_evidence", rig_mcp.CHILD_TOOL_NAMES)
        self.assertIn("rig_job_ui_evidence", rig_mcp.PARENT_TOOL_NAMES)

    def test_interrupted_check_and_released_attempt_refused(self):
        marker = self.folder / "check-running.json"
        marker.write_text("{}")
        with self.assertRaisesRegex(ValueError, "active or interrupted"):
            self.record()
        marker.unlink()
        admission.release(self.repo, rationale="Conclude fixture", **self.auth)
        with self.assertRaisesRegex(ValueError, "released"):
            self.record()

    def test_step_and_total_budgets(self):
        self.pack["steps"].append(copy.deepcopy(self.pack["steps"][0]))
        with self.assertRaisesRegex(ValueError, "unique"):
            self.record()
        self.pack["steps"][1]["id"] = "second"
        with patch.object(ui, "MAX_TOTAL_BYTES", len(png()) + 1):
            with self.assertRaisesRegex(ValueError, "total byte limit"):
                self.record()
        with patch.object(ui, "MAX_MANIFEST_BYTES", 64):
            with self.assertRaisesRegex(ValueError, "metadata"):
                self.record()

    def test_png_metadata_and_trailing_data_refused(self):
        raw = png()
        payload = b"Comment\x00private"
        chunk = (struct.pack(">I", len(payload)) + b"tEXt" + payload
                 + struct.pack(">I", zlib.crc32(b"tEXt" + payload) & 0xffffffff))
        for data in (raw[:-12] + chunk + raw[-12:], raw + b"private trailing bytes", raw[:-2]):
            self.image_path.write_bytes(data)
            self.pack["steps"][0]["image"]["sha256"] = digest(data)
            self.pack["steps"][0]["receipt"]["image"]["sha256"] = digest(data)
            with self.assertRaisesRegex(ValueError, "PNG"):
                self.record()

    def test_transitive_hardlink_and_symlink_refused(self):
        result = self.record()
        path = (self.folder / result["evidence_ref"]["path"]).parent / "images/open.png"
        path.unlink()
        path.symlink_to(self.image_path)
        with self.assertRaises(ValueError):
            ui.validate_pack_reference(self.folder, result["evidence_ref"])
        path.unlink()
        os.link(self.image_path, path)
        with self.assertRaises(ValueError):
            ui.validate_pack_reference(self.folder, result["evidence_ref"])

    def test_mcp_dispatch_and_disabled_project(self):
        result = rig_mcp.call_tool("rig_job_ui_evidence", {"repo": str(self.repo), "id": "ui-check", "pack": self.pack, **self.auth})
        self.assertFalse(result.get("isError"), result)
        self.assertEqual(result["structuredContent"]["coverage"]["available_images"], 1)
        (self.repo / ".rig/harness.toml").write_text("[project]\nenabled = false\n")
        blocked = rig_mcp.call_tool("rig_job_ui_evidence", {"repo": str(self.repo), "id": "ui-check", "pack": self.pack, **self.auth})
        self.assertTrue(blocked.get("isError"))


from test_native_admission import NativeHarness
import acceptance_contract
import verification


class UIPackAcceptance(NativeHarness):
    def prepare(self, record=True):
        spec = {"schema_version": 1, "contract_id": "ui", "revision": 1, "criteria": [
            {"id": criterion, "description": "Parent visually inspects " + criterion,
             "scope": ["subject.txt"], "evidence_type": "review_assertion", "verifier_role": "parent",
             "artifact_kind": "ui-pack"} for criterion in ("layout", "navigation")]}
        self.lease = self.start(acceptance_contract=spec)
        self.assertFalse(self.finish(self.lease).get("isError"))
        self.folder = self.repo / ".rig/jobs/writer"
        self.frozen = acceptance_contract.load(self.repo, self.folder)
        self.snapshot = change_evidence.snapshot(self.repo, ["subject.txt"])["snapshot_id"]
        source = self.repo / ".rig/bsk-evidence/shot.png"
        source.parent.mkdir()
        source.write_bytes(png())
        self.pack = {"privacy_reviewed": True, "contract_fingerprint": self.frozen["contract_fingerprint"],
                     "content_snapshot_id": self.snapshot, "criterion_ids": ["layout"], "steps": [
                         {"id": "screen", "receipt": {"version": "rig.bsk.v1", "operation": "observe",
                           "status": "observed", "effect": "observed", "snapshot": {"id": "obs-1", "freshness": "fresh"}},
                          "image": {"path": ".rig/bsk-evidence/shot.png", "sha256": digest(png()), "privacy": "reviewed"}}]}
        return ui.record_pack(self.repo, self.folder, self.pack, **self.auth(self.lease)) if record else None

    def assertion(self, ref, criterion="layout"):
        return verification.record_criterion(self.repo, self.folder, criterion,
            self.frozen["contract_fingerprint"], self.snapshot, "pass", "Parent inspected the visual evidence",
            [ref], **self.auth(self.lease))

    def test_crashed_partial_pack_recovers_only_exact_dead_owned_operation(self):
        self.prepare(record=False)
        request = self.repo / "crash-pack.json"
        request.write_text(json.dumps(self.pack))
        script = """
import json, os, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import admission, ui_evidence
repo, folder = Path(sys.argv[2]), Path(sys.argv[2]) / '.rig/jobs/writer'
auth = admission.resolve_ownership(repo, job_id='writer', credentials_path=sys.argv[3])
original = ui_evidence._private_write
def crash(path, data):
    original(path, data)
    os._exit(23)
ui_evidence._private_write = crash
ui_evidence.record_pack(repo, folder, json.loads(Path(sys.argv[4]).read_text()), **auth)
"""
        proc = subprocess.run([sys.executable, "-c", script, str(ROOT / "scripts"), str(self.repo),
                               self.lease["credentials_path"], str(request)], text=True, capture_output=True,
                              env=os.environ.copy(), timeout=15)
        self.assertEqual(proc.returncode, 23, proc.stderr)
        record = admission.get_reservation(self.repo, self.lease["reservation_id"])
        self.assertEqual(record["operation"]["operation"], "evidence")
        self.assertEqual(admission._process_state(record["operation"]), "dead")
        base = self.folder / "evidence/ui-packs"
        self.assertFalse(list(base.glob("*/manifest.json")))
        self.assertTrue(list(base.glob(".pack-*/images/*.png")))
        action = admission.reconcile(self.repo, job_id="writer")["items"][0]["next_action"]
        self.assertEqual(action["kind"], "recover_evidence_operation")
        self.assertIn("current", action["instruction"])
        with self.assertRaisesRegex(ValueError, "operation"):
            ui.record_pack(self.repo, self.folder, self.pack, **self.auth(self.lease))
        with self.assertRaisesRegex(ValueError, "operation"):
            verification.accept(self.repo, self.folder, "accept", self.snapshot,
                rationale="Cannot accept partial evidence", **self.auth(self.lease))
        admission.reconcile(self.repo, apply=True)
        self.assertIn("operation", admission.get_reservation(self.repo, self.lease["reservation_id"]))
        for auth in ({}, {**self.auth(self.lease), "owner_token": "incorrect"},
                     {**self.auth(self.lease), "attempt_id": "wrong-attempt"},
                     {**self.auth(self.lease), "owner_session": "another-owner"}):
            with self.assertRaises(ValueError):
                admission.reconcile(self.repo, job_id="writer", apply=True, rationale="Recover interrupted pack", **auth)
        for observed in ("alive", "unknown"):
            with patch.object(admission, "_process_state", return_value=observed):
                with self.assertRaisesRegex(ValueError, "live or unknown"):
                    admission.reconcile(self.repo, job_id="writer", apply=True,
                        rationale="Recover interrupted pack", **self.auth(self.lease))
        result = admission.reconcile(self.repo, job_id="writer", apply=True,
            rationale="Evidence recording process confirmed exited", **self.auth(self.lease))
        self.assertEqual(result["applied"], 1)
        record = admission.get_reservation(self.repo, self.lease["reservation_id"])
        self.assertNotIn("operation", record)
        self.assertNotEqual(record["stage"], "released")
        self.assertFalse(record["slot_held"])
        blocked = self.call("rig_job_start", id="overlap", role="parent", files=["subject.txt"])
        self.assertTrue(blocked.get("isError"), blocked)
        self.assertEqual(record["interrupted_operation"]["operation"], "evidence")
        with self.assertRaisesRegex(ValueError, "missing or stale review assertion"):
            verification.accept(self.repo, self.folder, "accept", self.snapshot,
                rationale="No complete reviewed evidence yet", **self.auth(self.lease))
        completed = ui.record_pack(self.repo, self.folder, self.pack, **self.auth(self.lease))
        self.assertEqual(completed["coverage"]["available_images"], 1)
        self.assertion(completed["evidence_ref"])
        self.pack["criterion_ids"] = ["navigation"]
        navigation = ui.record_pack(self.repo, self.folder, self.pack, **self.auth(self.lease))
        self.assertion(navigation["evidence_ref"], "navigation")
        accepted = verification.accept(self.repo, self.folder, "accept", self.snapshot,
            rationale="Parent separately reviewed both criteria after complete retry", next="review", **self.auth(self.lease))
        self.assertEqual(accepted["state"], "verified")
        self.assertEqual((self.repo / ".rig/bsk-evidence/shot.png").read_bytes(), png())

    def test_real_contract_assertion_and_transitive_validation(self):
        result = self.prepare()
        ref = result["evidence_ref"]
        self.assertion(ref)
        with self.assertRaisesRegex(ValueError, "asserted criterion"):
            self.assertion(ref, "navigation")
        image = (self.folder / ref["path"]).parent / "images/screen.png"
        image.write_bytes(png(99))
        with self.assertRaisesRegex(ValueError, "changed"):
            self.assertion(ref)
        with self.assertRaisesRegex(ValueError, "changed"):
            acceptance_contract.artifact_refs(self.folder, [ref], "ui-pack", criterion_id="layout")

    def test_pack_does_not_accept_and_missing_coverage_remains_parent_judgment(self):
        result = self.prepare()
        with self.assertRaisesRegex(ValueError, "missing or stale review assertion"):
            verification.accept(self.repo, self.folder, "accept", self.snapshot,
                rationale="Attempt explicit acceptance", **self.auth(self.lease))
        (self.repo / ".rig/bsk-evidence/shot.png").unlink()
        missing = ui.record_pack(self.repo, self.folder, self.pack, **self.auth(self.lease))
        self.assertEqual(missing["coverage"]["unavailable_images"], 1)
        assertion = self.assertion(missing["evidence_ref"])
        self.assertEqual(assertion["provenance"]["kind"], "parent_assertion")
        self.assertNotEqual(verification.assessment(self.repo, self.folder)["state"], "verified")

    def test_cli_authenticated_pack_and_observation_snapshot_refused(self):
        self.prepare()
        request = self.repo / "pack.json"
        request.write_text(json.dumps(self.pack))
        proc = subprocess.run([str(ROOT / "bin/rig"), "job", "ui-evidence", "writer", "--file", str(request),
                               "--credentials-path", self.lease["credentials_path"]], cwd=self.repo,
                              text=True, capture_output=True, env=os.environ.copy())
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout)["coverage"]["available_images"], 1)
        self.pack["content_snapshot_id"] = "obs-1"
        with self.assertRaisesRegex(ValueError, "snapshot"):
            ui.record_pack(self.repo, self.folder, self.pack, **self.auth(self.lease))


if __name__ == "__main__":
    unittest.main()
