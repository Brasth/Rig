#!/usr/bin/env python3
"""/doctor in rig shell: plain findings, and only the fixes Rig can verify are safe."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import admission  # noqa: E402
import shell_doctor  # noqa: E402

ORIGINAL_FROM_DOCTOR = shell_doctor.from_doctor


class ShellDoctor(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        self.repo = Path(self.td.name).resolve() / "repo"
        (self.repo / ".git").mkdir(parents=True)
        (self.repo / ".rig" / "jobs").mkdir(parents=True)
        (self.repo / ".rig" / "harness.toml").write_text('parent = "codex"\n\n[project]\nenabled = true\n')
        patch = mock.patch.object(shell_doctor, "from_doctor", return_value=[])
        patch.start()
        self.addCleanup(patch.stop)

    def old(self, folder: Path):
        past = time.time() - 3600
        for path in [folder, *folder.iterdir()]:
            os.utime(path, (past, past))

    def ids(self):
        return [row["id"] for row in shell_doctor.findings(self.repo)["findings"]]

    def test_a_clean_project_has_nothing_to_report(self):
        result = shell_doctor.findings(self.repo)
        self.assertEqual((result["findings"], result["problems"], result["warnings"]), ([], 0, 0))

    def test_a_blank_thread_file_is_found_and_removed_and_a_real_one_is_not(self):
        thread = self.repo / ".rig" / "thread"
        thread.write_text("\n")
        found = shell_doctor.findings(self.repo)["findings"][0]
        self.assertEqual((found["id"], found["level"], found["fix"]), ("blank-thread", "problem", "remove-thread"))
        shell_doctor.apply(self.repo, "remove-thread")
        self.assertFalse(thread.exists())
        thread.write_text("session-1\n")
        self.assertEqual(self.ids(), [])
        with self.assertRaisesRegex(ValueError, "not empty any more"):
            shell_doctor.apply(self.repo, "remove-thread")
        self.assertTrue(thread.exists(), "a real thread is never removed")

    def test_a_job_that_never_started_is_found_after_a_while_and_removed(self):
        folder = self.repo / ".rig" / "jobs" / "hero-continue"
        folder.mkdir()
        (folder / "brief.md").write_text("brief")
        self.assertEqual(self.ids(), [], "a folder just made may still be being made")
        self.old(folder)
        self.assertEqual(self.ids(), ["stale-hero-continue"])
        shell_doctor.apply(self.repo, "remove-job:hero-continue")
        self.assertFalse(folder.exists())

    def test_a_job_with_a_meta_json_or_a_held_slot_is_never_removed(self):
        done = self.repo / ".rig" / "jobs" / "done-job"
        done.mkdir()
        (done / "meta.json").write_text("{}")
        self.old(done)
        held = self.repo / ".rig" / "jobs" / "held-job"
        held.mkdir()
        (held / "brief.md").write_text("b")
        self.old(held)
        with mock.patch.object(admission, "list_reservations", return_value=[{"job_id": "held-job", "slot_held": True}]):
            found = {row["id"]: row for row in shell_doctor.findings(self.repo)["findings"]}
            self.assertEqual(list(found), ["held-held-job"])
            self.assertEqual((found["held-held-job"]["level"], found["held-held-job"]["fix"]), ("problem", ""))
            for name in ("held-job", "done-job"):
                with self.assertRaisesRegex(ValueError, "not a stale job"):
                    shell_doctor.apply(self.repo, "remove-job:" + name)
        self.assertTrue(held.exists() and done.exists())
        for bad in ("remove-job:../x", "remove-job:a/b", "remove-job:", "rm-rf"):
            with self.assertRaises(ValueError):
                shell_doctor.apply(self.repo, bad)

    def test_unexpected_errors_are_listed_with_their_last_line_and_can_be_cleared(self):
        log = self.repo / ".rig" / "mcp-errors.log"
        log.write_text("--- 2026-10-22T00:00:00Z rig_session\nTraceback (most recent call last):\n  File x\nIndexError: list index out of range\n\n")
        found = shell_doctor.findings(self.repo)["findings"][0]
        self.assertEqual((found["id"], found["level"], found["fix"]), ("mcp-errors", "warning", "clear-errors"))
        self.assertIn("rig_session: IndexError: list index out of range", found["detail"])
        shell_doctor.apply(self.repo, "clear-errors")
        self.assertEqual(self.ids(), [])

    def test_doctors_own_checks_come_in_as_findings_problems_first(self):
        rows = [{"name": "parent", "status": "missing", "required": True, "detail": "Select an installed parent CLI"},
                {"name": "adapters", "status": "failed", "required": False, "detail": "1 adapter package is invalid"},
                {"name": "sandbox", "status": "missing", "required": False, "detail": "devcontainer provider needs dc-cli"},
                {"name": "browser", "status": "optional-disabled", "required": False, "detail": "opt-in unchanged"},
                {"name": "auth", "status": "missing-evidence", "required": True, "detail": "not tested"},
                {"name": "project", "status": "ready", "required": True, "detail": "enabled"}]
        import doctor
        with mock.patch.object(doctor, "build_report", return_value={"checks": rows}):
            out = ORIGINAL_FROM_DOCTOR(self.repo)
        self.assertEqual([(r["id"], r["level"]) for r in out], [("check-parent", "problem"), ("check-adapters", "warning")])
        self.assertEqual(out[0]["title"], "parent: missing")
        self.assertEqual(out[0]["fix"], "", "Rig never fixes a missing parent by itself")
        with mock.patch.object(doctor, "build_report", side_effect=ValueError("bad harness.toml")):
            self.assertEqual(ORIGINAL_FROM_DOCTOR(self.repo)[0]["detail"], "bad harness.toml")
        with mock.patch.object(shell_doctor, "from_doctor", return_value=out):
            levels = [r["level"] for r in shell_doctor.findings(self.repo)["findings"]]
        self.assertEqual(levels, ["problem", "warning"], "sorted problems first")


class SignedOut(ShellDoctor):
    def test_claude_signed_out_is_a_problem_and_only_claude_is_asked(self):
        import subprocess
        (self.repo / ".rig" / "harness.toml").write_text('parent = "claude"\n\n[project]\nenabled = true\n')
        out_ok = mock.Mock(stdout=json.dumps({"loggedIn": True}))
        out_no = mock.Mock(stdout=json.dumps({"loggedIn": False}))
        with mock.patch("shutil.which", return_value="/usr/bin/claude"), mock.patch.object(subprocess, "run", return_value=out_no):
            found = shell_doctor.signed_out(self.repo)
        self.assertEqual((found["id"], found["level"], found["fix"]), ("claude-signed-out", "problem", ""))
        self.assertIn("/login", found["detail"])
        with mock.patch("shutil.which", return_value="/usr/bin/claude"), mock.patch.object(subprocess, "run", return_value=out_ok):
            self.assertIsNone(shell_doctor.signed_out(self.repo))
        with mock.patch("shutil.which", return_value="/usr/bin/claude"), mock.patch.object(subprocess, "run", side_effect=subprocess.TimeoutExpired("claude", 8)):
            self.assertIsNone(shell_doctor.signed_out(self.repo), "no answer is not an accusation")
        with mock.patch("shutil.which", return_value="/usr/bin/claude"), mock.patch.object(subprocess, "run", return_value=mock.Mock(stdout="not json")):
            self.assertIsNone(shell_doctor.signed_out(self.repo))
        (self.repo / ".rig" / "harness.toml").write_text('parent = "codex"\n\n[project]\nenabled = true\n')
        with mock.patch("shutil.which", return_value="/usr/bin/codex"), mock.patch.object(subprocess, "run") as run:
            self.assertIsNone(shell_doctor.signed_out(self.repo))
            run.assert_not_called()


class CodexServers(ShellDoctor):
    LISTING = ("Name    Url                         Bearer Token Env Var  Status   Auth\n"
               "asana   https://mcp.asana.com/v2/mcp  -                     enabled  Not logged in\n"
               "github  https://api.example/mcp       GH_TOKEN              enabled  Bearer token\n"
               "figma   https://mcp.figma.com/mcp     -                     enabled  OAuth\n")

    def codex_project(self):
        (self.repo / ".rig" / "harness.toml").write_text('parent = "codex"\n\n[project]\nenabled = true\n')

    def test_a_server_codex_lists_as_not_logged_in_is_named_with_the_command_to_sign_in(self):
        import subprocess
        self.codex_project()
        with mock.patch("shutil.which", return_value="/usr/bin/codex"), \
                mock.patch.object(subprocess, "run", return_value=mock.Mock(stdout=self.LISTING)) as run:
            found = shell_doctor.codex_servers_signed_out(self.repo)
        self.assertEqual(run.call_args.args[0], ["/usr/bin/codex", "mcp", "list"])
        self.assertEqual([(f["id"], f["level"], f["fix"]) for f in found], [("codex-mcp-asana", "warning", "")])
        self.assertIn("/mcp login asana", found[0]["detail"])

    def test_nothing_is_reported_for_an_unknown_format_another_parent_or_no_codex(self):
        import subprocess
        self.codex_project()
        with mock.patch("shutil.which", return_value="/usr/bin/codex"), \
                mock.patch.object(subprocess, "run", return_value=mock.Mock(stdout="something else entirely\nasana ok\n")):
            self.assertEqual(shell_doctor.codex_servers_signed_out(self.repo), [])
        with mock.patch("shutil.which", return_value="/usr/bin/codex"), \
                mock.patch.object(subprocess, "run", side_effect=subprocess.TimeoutExpired("codex", 10)):
            self.assertEqual(shell_doctor.codex_servers_signed_out(self.repo), [])
        with mock.patch("shutil.which", return_value=None):
            self.assertEqual(shell_doctor.codex_servers_signed_out(self.repo), [])
        (self.repo / ".rig" / "harness.toml").write_text('parent = "claude"\n\n[project]\nenabled = true\n')
        with mock.patch("shutil.which", return_value="/usr/bin/codex"), mock.patch.object(subprocess, "run") as run:
            self.assertEqual(shell_doctor.codex_servers_signed_out(self.repo), [])
            run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
