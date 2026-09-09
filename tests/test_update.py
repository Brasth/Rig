#!/usr/bin/env python3
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RIG = ROOT / "bin" / "rig"


def _run(
    cwd: Path,
    *args: str,
    env: dict | None = None,
) -> subprocess.CompletedProcess:
    merged = os.environ.copy()
    merged["PATH"] = f"{ROOT / 'bin'}:{merged.get('PATH', '')}"
    if env:
        merged.update(env)
    return subprocess.run(
        [str(RIG), *args],
        cwd=cwd,
        env=merged,
        text=True,
        capture_output=True,
        check=False,
    )


def _git_repo(path: Path) -> str:
    path.mkdir(parents=True)
    subprocess.run(
        ["git", "init"],
        cwd=path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "t@t"],
        cwd=path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "t"],
        cwd=path,
        check=True,
        capture_output=True,
    )
    (path / "f").write_text("x\n")
    subprocess.run(["git", "add", "f"], cwd=path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "i"],
        cwd=path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "branch", "-M", "main"],
        cwd=path,
        check=True,
        capture_output=True,
    )
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"],
        cwd=path,
        text=True,
    ).strip()


class RigUpdate(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.root = Path(self.td.name)
        self.home = self.root / "home"
        self.rig_home = self.root / "kit"
        self.repo = self.root / "proj"
        self.home.mkdir()
        self.rig_home.mkdir()
        self.repo.mkdir()
        (self.repo / ".git").mkdir()
        self.stub = self.root / "install-stub.sh"
        self.stub.write_text(
            "#!/bin/bash\n"
            "echo install-ok\n"
            "touch \"$RIG_HOME/install-ran\"\n"
        )
        self.base_env = {
            "HOME": str(self.home),
            "RIG_HOME": str(self.rig_home),
            "RIG_INSTALL_SH": str(self.stub),
            "RIG_SKIP_UPDATE_CHECK": "1",
            "RIG_REPO_URL": str(self.root / "no-such-remote.git"),
        }

    def tearDown(self):
        self.td.cleanup()

    def test_usage_includes_update(self):
        proc = _run(self.repo, "-h", env=self.base_env)
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertIn("update", proc.stdout)

    def test_update_refuses_inside_worker(self):
        env = dict(self.base_env)
        env["RIG_LIVE"] = "1"
        proc = _run(self.repo, "update", env=env)
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertIn("refuse inside a worker", proc.stderr)
        self.assertFalse((self.rig_home / "install-ran").exists())
        self.assertNotIn("install-ok", proc.stdout)

    def test_update_runs_stub_installer(self):
        proc = _run(self.repo, "update", env=self.base_env)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("install-ok", proc.stdout)
        self.assertIn(str(self.rig_home), proc.stdout)
        self.assertIn("fully quit the parent CLI once", proc.stdout)
        self.assertTrue((self.rig_home / "install-ran").exists())
        self.assertFalse((self.repo / ".rig" / "harness.toml").exists())

    def test_update_curl_failure(self):
        env = dict(self.base_env)
        env["RIG_INSTALL_SH"] = str(self.root / "missing-install.sh")
        proc = _run(self.repo, "update", env=env)
        self.assertNotEqual(proc.returncode, 0, proc.stdout)
        self.assertIn("could not fetch installer", proc.stderr)
        self.assertFalse((self.rig_home / "install-ran").exists())

    def test_doctor_prints_version(self):
        (self.rig_home / "VERSION").write_text("v1 abc1234\n")
        proc = _run(self.repo, "doctor", env=self.base_env)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("version:  v1 abc1234", proc.stdout)

    def test_doctor_unknown_version_when_missing(self):
        proc = _run(self.repo, "doctor", env=self.base_env)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("version:  (unknown — run: rig update)", proc.stdout)

    def test_doctor_skip_omits_update_line(self):
        (self.rig_home / "VERSION").write_text("v1 abc1234\n")
        proc = _run(self.repo, "doctor", env=self.base_env)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertNotIn("  update:", proc.stdout)

    def test_doctor_behind_main(self):
        _git_repo(self.root / "upstream")
        (self.rig_home / "VERSION").write_text("v1 deadbee\n")
        env = dict(self.base_env)
        env["RIG_SKIP_UPDATE_CHECK"] = ""
        env["RIG_REPO_URL"] = str(self.root / "upstream")
        proc = _run(self.repo, "doctor", env=env)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("behind", proc.stdout)
        self.assertIn("rig update", proc.stdout)

    def test_doctor_current(self):
        sha = _git_repo(self.root / "upstream")
        (self.rig_home / "VERSION").write_text(f"v1 {sha[:7]}\n")
        env = dict(self.base_env)
        env["RIG_SKIP_UPDATE_CHECK"] = ""
        env["RIG_REPO_URL"] = str(self.root / "upstream")
        proc = _run(self.repo, "doctor", env=env)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("update:   current", proc.stdout)
        self.assertNotIn("behind", proc.stdout)


if __name__ == "__main__":
    unittest.main()
