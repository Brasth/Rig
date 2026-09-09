#!/usr/bin/env python3
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import agy_job_permissions as agy  # noqa: E402

HELPER = ROOT / "scripts" / "agy_job_permissions.py"


class MergeRestore(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.home = Path(self.td.name)
        self.settings = self.home / "settings.json"
        self.job = self.home / "job"
        self.job.mkdir()

    def tearDown(self):
        self.td.cleanup()

    def test_merge_creates_settings_and_restore_deletes_when_missing(self):
        agy.merge(self.settings, self.job)
        data = json.loads(self.settings.read_text())
        self.assertEqual(data["permissions"]["allow"], ["command(*)"])
        self.assertNotIn("skip-permissions", json.dumps(data))
        bak = self.job / "agy-settings.bak"
        self.assertEqual(bak.read_text().strip(), "missing")
        agy.restore(self.settings, self.job)
        self.assertFalse(self.settings.exists())

    def test_merge_preserves_other_keys_and_does_not_duplicate(self):
        original = {
            "keep": True,
            "permissions": {"allow": ["read(*)"], "deny": ["rm"]},
        }
        self.settings.write_text(json.dumps(original, indent=2) + "\n")
        agy.merge(self.settings, self.job)
        data = json.loads(self.settings.read_text())
        self.assertTrue(data["keep"])
        self.assertEqual(data["permissions"]["deny"], ["rm"])
        self.assertEqual(data["permissions"]["allow"], ["read(*)", "command(*)"])
        agy.merge(self.settings, self.job)
        again = json.loads(self.settings.read_text())
        self.assertEqual(again["permissions"]["allow"], ["read(*)", "command(*)"])
        agy.restore(self.settings, self.job)
        restored = json.loads(self.settings.read_text())
        self.assertEqual(restored, original)

    def test_already_present_still_backups(self):
        original = '{"permissions":{"allow":["command(*)"]}}\n'
        self.settings.write_text(original)
        agy.merge(self.settings, self.job)
        self.assertEqual(self.settings.read_text(), original)
        self.assertEqual((self.job / "agy-settings.bak").read_text(), original)
        agy.restore(self.settings, self.job)
        self.assertEqual(self.settings.read_text(), original)

    def test_cli_merge_restore_uses_passed_path_not_home(self):
        real_home = Path.home() / ".gemini" / "antigravity-cli" / "settings.json"
        before = real_home.read_bytes() if real_home.is_file() else None
        proc = subprocess.run(
            [
                sys.executable,
                str(HELPER),
                "merge",
                "--settings",
                str(self.settings),
                "--job-dir",
                str(self.job),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue(self.settings.is_file())
        after = real_home.read_bytes() if real_home.is_file() else None
        self.assertEqual(before, after)
        subprocess.run(
            [
                sys.executable,
                str(HELPER),
                "restore",
                "--settings",
                str(self.settings),
                "--job-dir",
                str(self.job),
            ],
            check=True,
        )
        self.assertFalse(self.settings.exists())


class DeniedActions(unittest.TestCase):
    def test_nonempty_denied_actions_is_fail(self):
        raw = json.dumps(
            {
                "status": "SUCCESS",
                "response": "ok",
                "denied_actions": [{"action": "command", "display_name": "RunCommand"}],
            }
        )
        acts = agy.denied_actions_from_log(raw)
        self.assertEqual(len(acts), 1)
        self.assertEqual(agy.response_text(agy.last_json_object(raw)), "ok")
        proc = subprocess.run(
            [sys.executable, str(HELPER), "denied-actions"],
            input=raw,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 1)
        self.assertIn("command", proc.stdout)

    def test_empty_denied_actions_ok(self):
        raw = json.dumps({"status": "SUCCESS", "response": "done", "denied_actions": []})
        self.assertEqual(agy.denied_actions_from_log(raw), [])
        proc = subprocess.run(
            [sys.executable, str(HELPER), "denied-actions"],
            input=raw,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout.strip(), "")

    def test_missing_denied_actions_key_is_ok(self):
        raw = json.dumps({"status": "SUCCESS", "response": "done"})
        self.assertEqual(agy.denied_actions_from_log(raw), [])


if __name__ == "__main__":
    unittest.main()
