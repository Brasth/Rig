#!/usr/bin/env python3
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import doctor  # noqa: E402

FAKE_DC = r'''#!/bin/sh
echo "$*" >> "$FAKE_DC_LOG"
case "$1" in
  --version) echo "dc ${FAKE_DC_VERSION:-0.23.0}" ;;
  doctor) printf '%s\n' "$FAKE_DC_DOCTOR"; exit "${FAKE_DC_DOCTOR_EXIT:-0}" ;;
  *) exit 64 ;;
esac
'''


def rows(report, name):
    return [c for c in report["checks"] if c["name"] == name]


class WorkspaceChecks(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.base = Path(self.td.name)
        self.repo = self.base / "repo"
        (self.repo / ".git").mkdir(parents=True)
        (self.repo / ".rig").mkdir()
        self.write_harness("")
        self.log = self.base / "dc.log"
        self.fake = self.base / "dc"
        self.fake.write_text(FAKE_DC)
        self.fake.chmod(0o755)
        self.empty = self.base / "empty"
        self.empty.mkdir()
        self.env = mock.patch.dict(os.environ, {"RIG_DC_BIN": str(self.fake), "FAKE_DC_LOG": str(self.log),
                                                "RIG_HOME": str(self.base / "home")})
        self.env.start()
        for key in ("FAKE_DC_VERSION", "FAKE_DC_DOCTOR", "FAKE_DC_DOCTOR_EXIT"):
            os.environ.pop(key, None)

    def tearDown(self):
        self.env.stop()
        self.td.cleanup()

    def write_harness(self, extra):
        (self.repo / ".rig" / "harness.toml").write_text("[project]\nenabled = true\n" + extra)

    def pin(self, version):
        folder = self.base / "home" / "templates"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "dc-cli.json").write_text(json.dumps({"version": version}))

    def calls(self):
        return self.log.read_text().splitlines() if self.log.exists() else []

    def test_default_report_shows_workspace_and_dc_but_never_runs_the_engine_check(self):
        report = doctor.build_report(self.repo)
        workspace, dc = rows(report, "workspace")[0], rows(report, "dc-cli")[0]
        self.assertEqual((workspace["status"], workspace["kind"], workspace["required"]), ("ready", "none", False))
        self.assertEqual((dc["status"], dc["pin_state"], dc["required"]), ("ready", "unpinned", False))
        self.assertEqual(rows(report, "engine"), [])
        self.assertEqual(self.calls(), ["--version"])

    def test_missing_dc_is_optional_by_default_and_required_when_asked_or_configured(self):
        with mock.patch.dict(os.environ, {"RIG_DC_BIN": "", "PATH": str(self.empty)}):
            default = rows(doctor.build_report(self.repo), "dc-cli")[0]
            deep = doctor.build_report(self.repo, workspace=True)
            self.write_harness('\n[sandbox]\nprovider = "devcontainer"\n')
            configured = rows(doctor.build_report(self.repo), "dc-cli")[0]
        self.assertEqual((default["status"], default["required"]), ("missing", False))
        self.assertEqual((rows(deep, "dc-cli")[0]["required"], rows(deep, "engine")), (True, []))
        self.assertTrue(configured["required"])

    def test_pin_mismatch_fails_only_where_dc_is_needed(self):
        self.pin("9.9.9")
        os.environ["FAKE_DC_DOCTOR"] = '{"checks": []}'
        optional = rows(doctor.build_report(self.repo), "dc-cli")[0]
        needed = rows(doctor.build_report(self.repo, workspace=True), "dc-cli")[0]
        self.assertEqual((optional["status"], optional["required"]), ("failed", False))
        self.assertEqual((needed["status"], needed["required"]), ("failed", True))
        self.assertIn("pinned 9.9.9", needed["detail"])
        self.pin("0.23.0")
        self.assertIn("matches the pin", rows(doctor.build_report(self.repo), "dc-cli")[0]["detail"])

    def test_engine_ready_when_dc_reports_no_problems(self):
        os.environ["FAKE_DC_DOCTOR"] = json.dumps({"checks": [{"name": "docker", "status": "ok"}, {"name": "disk", "ok": True}]})
        engine = rows(doctor.build_report(self.repo, workspace=True), "engine")[0]
        self.assertEqual((engine["status"], engine["required"]), ("ready", True))
        self.assertIn("doctor --json", self.calls())

    def test_engine_problems_name_the_dc_recover_fix(self):
        os.environ["FAKE_DC_DOCTOR"] = json.dumps({"checks": [
            {"name": "engine", "status": "fail", "recover": "engine-down"},
            {"name": "ports", "ok": False},
            {"name": "disk", "status": "ok"}]})
        os.environ["FAKE_DC_DOCTOR_EXIT"] = "1"
        report = doctor.build_report(self.repo, workspace=True)
        engine = rows(report, "engine")[0]
        self.assertEqual(engine["status"], "failed")
        self.assertIn("engine (fix: dc recover engine-down)", engine["detail"])
        self.assertIn("ports", engine["detail"])
        self.assertNotIn("disk", engine["detail"])
        self.assertEqual([p["recover"] for p in engine["problems"]], ["engine-down", ""])
        self.assertEqual(report["status"], "failed")

    def test_engine_accepts_a_bare_list_and_other_key_names(self):
        os.environ["FAKE_DC_DOCTOR"] = json.dumps([{"check": "x", "state": "unhealthy", "diagnosis_id": "d7"}])
        engine = rows(doctor.build_report(self.repo, workspace=True), "engine")[0]
        self.assertIn("x (fix: dc recover d7)", engine["detail"])

    def test_engine_unusable_output_fails_clearly(self):
        os.environ["FAKE_DC_DOCTOR"] = "docker daemon unreachable"
        os.environ["FAKE_DC_DOCTOR_EXIT"] = "2"
        engine = rows(doctor.build_report(self.repo, workspace=True), "engine")[0]
        self.assertEqual(engine["status"], "failed")
        self.assertIn("did not print JSON", engine["detail"])
        os.environ["FAKE_DC_DOCTOR"] = '{"checks": []}'
        engine = rows(doctor.build_report(self.repo, workspace=True), "engine")[0]
        self.assertIn("exited 2 without naming a problem", engine["detail"])

    def test_cli_workspace_flag_sets_exit_status_and_json(self):
        os.environ["FAKE_DC_DOCTOR"] = json.dumps({"checks": [{"name": "engine", "status": "fail"}]})
        script = str(ROOT / "scripts" / "doctor.py")
        failed = subprocess.run([sys.executable, script, "--repo", str(self.repo), "--workspace", "--json"],
                                env=dict(os.environ), capture_output=True, text=True, timeout=60)
        self.assertEqual(failed.returncode, 1, failed.stderr)
        self.assertEqual(rows(json.loads(failed.stdout), "engine")[0]["status"], "failed")
        plain = subprocess.run([sys.executable, script, "--repo", str(self.repo), "--json"],
                               env=dict(os.environ), capture_output=True, text=True, timeout=60)
        self.assertEqual(rows(json.loads(plain.stdout), "engine"), [])


if __name__ == "__main__":
    unittest.main()
