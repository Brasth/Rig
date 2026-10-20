#!/usr/bin/env python3
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import doctor  # noqa: E402
import rig_shell  # noqa: E402

SCRIPT = ROOT / "scripts" / "install-shell.py"
# Fake go: logs argv, writes the -o target, can be told to fail or hang.
FAKE_GO = r'''#!/bin/sh
echo "$*" >> "$FAKE_GO_LOG"
echo "cwd=$(pwd)" >> "$FAKE_GO_LOG"
echo "goflags=$GOFLAGS" >> "$FAKE_GO_LOG"
if [ -n "$FAKE_GO_FAIL" ]; then echo "undefined: nope" >&2; echo "build error line 2" >&2; exit 2; fi
out=""; prev=""
for a in "$@"; do if [ "$prev" = "-o" ]; then out="$a"; fi; prev="$a"; done
printf '#!/bin/sh\necho "rig-shell fake"\n' > "$out"
'''


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True).stdout.strip()


class Installer(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.base = Path(self.td.name)
        self.src = self.base / "src"
        (self.src / "shell" / "cmd" / "rig-shell").mkdir(parents=True)
        (self.src / "shell" / "go.mod").write_text("module x\n")
        git(self.src, "init", "-q") if False else subprocess.run(["git", "init", "-q", str(self.src)], check=True)
        subprocess.run(["git", "-C", str(self.src), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "x"], check=True)
        self.commit = git(self.src, "rev-parse", "HEAD")
        self.home = self.base / "home"
        self.bin = self.base / "bin"
        self.bin.mkdir()
        self.log = self.base / "go.log"
        go = self.bin / "go"
        go.write_text(FAKE_GO)
        go.chmod(0o755)
        self.env = {**os.environ, "RIG_HOME": str(self.home), "FAKE_GO_LOG": str(self.log),
                    "PATH": f"{self.bin}:{os.environ['PATH']}"}
        self.env.pop("RIG_SKIP_SHELL_BUILD", None)
        self.env.pop("FAKE_GO_FAIL", None)

    def tearDown(self):
        self.td.cleanup()

    def run_script(self, *args, env=None):
        return subprocess.run([sys.executable, str(SCRIPT), "--src", str(self.src), *args], env=env or self.env,
                              capture_output=True, text=True, timeout=60)

    def test_build_stamps_the_commit_and_installs_an_executable_atomically(self):
        done = self.run_script("--shell")
        self.assertEqual(done.returncode, 0, done.stderr)
        target = self.home / "bin" / "rig-shell"
        self.assertTrue(target.is_file())
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o755)
        log = self.log.read_text()
        self.assertIn(f"-X main.Version={self.commit}", log)
        self.assertIn("./cmd/rig-shell", log)
        self.assertIn("-trimpath", log)
        self.assertIn(f"cwd={(self.src / 'shell').resolve()}", log)
        self.assertIn("goflags=-mod=readonly", log)
        self.assertEqual([p.name for p in (self.home / "bin").iterdir()], ["rig-shell"], "no temporary files left")
        self.assertIn(f"({self.commit[:12]})", done.stdout)

    def test_failed_build_keeps_the_previous_binary_and_reports_why(self):
        self.run_script("--shell")
        target = self.home / "bin" / "rig-shell"
        before = target.read_bytes()
        failing = {**self.env, "FAKE_GO_FAIL": "1"}
        soft = self.run_script(env=failing)
        self.assertEqual(soft.returncode, 0, "optional component: setup must not fail")
        self.assertIn("previous rig-shell kept", soft.stdout)
        self.assertIn("build error line 2", soft.stdout)
        hard = self.run_script("--shell", env=failing)
        self.assertEqual(hard.returncode, 1)
        self.assertEqual(target.read_bytes(), before)
        self.assertEqual([p.name for p in (self.home / "bin").iterdir()], ["rig-shell"])

    def test_missing_go_is_a_soft_skip_unless_explicitly_requested(self):
        empty = self.base / "empty"
        empty.mkdir()
        bare = {**self.env, "PATH": f"{empty}:/usr/bin:/bin"}
        if subprocess.run(["sh", "-c", "command -v go"], env=bare, capture_output=True).returncode == 0:
            self.skipTest("a real go is on the system path")
        soft = self.run_script(env=bare)
        self.assertEqual(soft.returncode, 0)
        self.assertIn("Go toolchain not found", soft.stdout)
        self.assertEqual(self.run_script("--shell", env=bare).returncode, 1)
        self.assertFalse((self.home / "bin" / "rig-shell").exists())

    def test_missing_source_and_explicit_skip(self):
        (self.src / "shell" / "go.mod").unlink()
        self.assertIn("shell source not found", self.run_script().stdout)
        self.assertEqual(self.run_script("--shell").returncode, 1)
        (self.src / "shell" / "go.mod").write_text("module x\n")
        skipped = self.run_script("--no-shell")
        self.assertIn("skipped", skipped.stdout)
        self.assertFalse(self.log.exists(), "go must not run when skipped")

    def test_skip_variable_applies_to_setup_but_not_to_an_explicit_build(self):
        env = {**self.env, "RIG_SKIP_SHELL_BUILD": "1"}
        self.assertIn("RIG_SKIP_SHELL_BUILD", self.run_script(env=env).stdout)
        self.assertFalse(self.log.exists())
        self.assertEqual(self.run_script("--shell", env=env).returncode, 0)
        self.assertTrue((self.home / "bin" / "rig-shell").is_file())

    def test_non_git_source_builds_a_dev_version(self):
        import shutil
        shutil.rmtree(self.src / ".git")
        self.assertEqual(self.run_script("--shell").returncode, 0)
        self.assertIn("-X main.Version=dev", self.log.read_text())

    def test_conflicting_flags_are_rejected(self):
        self.assertNotEqual(self.run_script("--shell", "--no-shell").returncode, 0)


class DoctorShellCheck(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.base = Path(self.td.name)
        (self.base / "home").mkdir()
        self.fake = self.base / "rig-shell"
        self.env = mock.patch.dict(os.environ, {"RIG_HOME": str(self.base / "home"), "RIG_SHELL_BIN": str(self.fake),
                                                "PATH": str(self.base / "nowhere")})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.td.cleanup()

    def shell(self, output: str):
        self.fake.write_text(f"#!/bin/sh\n{output}\n")
        self.fake.chmod(0o755)

    def core(self, text: str):
        (self.base / "home" / "VERSION").write_text(text)

    def test_core_version_parsing(self):
        self.assertEqual(rig_shell.core_version(), "")
        self.core("v1 abc1234\n")
        self.assertEqual(rig_shell.core_version(), "abc1234")
        self.core("v1\n")
        self.assertEqual(rig_shell.core_version(), "")

    def test_states(self):
        full = "0123456789abcdef0123456789abcdef01234567"
        cases = [
            ("echo rig-shell " + full, "v1 " + full[:7] + "\n", "match", "ready"),
            ("echo rig-shell " + full[:7], "v1 " + full + "\n", "match", "ready"),
            ("echo rig-shell " + full, "v1 fedcba9\n", "mismatch", "failed"),
            ("echo rig-shell dev", "v1 abc1234\n", "unverified", "ready"),
            ("echo rig-shell " + full, "v1\n", "unverified", "ready"),
            ("exit 0", "v1 abc1234\n", "unreadable", "failed"),
        ]
        for output, core, state, status in cases:
            self.shell(output)
            self.core(core)
            info = rig_shell.shell_status()
            check = doctor.shell_check()
            self.assertEqual((info["state"], check["status"], check["required"]), (state, status, False), (output, core))
            if state == "mismatch":
                self.assertIn("rig shell build", check["detail"])

    def test_missing_binary_is_optional(self):
        with mock.patch.object(rig_shell, "ROOT", self.base / "no-checkout"):
            check = doctor.shell_check()
        self.assertEqual((check["status"], check["required"], check["state"]), ("missing", False, "missing"))
        self.assertIn("rig shell build", check["detail"])


if __name__ == "__main__":
    unittest.main()
