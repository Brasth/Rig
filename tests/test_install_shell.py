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
import shell_dist  # noqa: E402

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
        self.env["RIG_SHELL_NO_DOWNLOAD"] = "1"  # no network in tests; the download has its own test

    def add_prebuilt(self, stamp=None, body=None, sums_ok=True):
        """A prebuilt shell for this platform in the source tree, as scripts/shell_dist.py build leaves it."""
        name = shell_dist.asset_name()
        if not name:
            self.skipTest("no prebuilt target for this platform")
        folder = self.src / "prebuilt"
        folder.mkdir(exist_ok=True)
        stamp = shell_dist.source_stamp(self.src) if stamp is None else stamp
        data = body or f'#!/bin/sh\necho "rig-shell src-{stamp}"\n'
        (folder / name).write_text(data)
        (folder / name).chmod(0o755)
        digest = shell_dist.sha256_of(folder / name) if sums_ok else "0" * 64
        (folder / "SHA256SUMS").write_text(f"{digest}  {name}\n")
        (folder / "STAMP").write_text(stamp + "\n")
        return folder

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

    def bare_env(self):
        empty = self.base / "empty"
        empty.mkdir(exist_ok=True)
        env = {**self.env, "PATH": f"{empty}:/usr/bin:/bin"}
        if subprocess.run(["sh", "-c", "command -v go"], env=env, capture_output=True).returncode == 0:
            self.skipTest("a real go is on the system path")
        return env

    def test_a_current_prebuilt_is_installed_without_go_and_without_building(self):
        self.add_prebuilt()
        done = self.run_script("--shell", env=self.bare_env())
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("prebuilt", done.stdout)
        target = self.home / "bin" / "rig-shell"
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o755)
        self.assertIn("rig-shell src-", subprocess.run([str(target), "--version"], capture_output=True, text=True).stdout)
        self.assertEqual([p.name for p in (self.home / "bin").iterdir()], ["rig-shell"], "no temporary files left")
        shutil_go = self.run_script("--shell")  # with the fake go on the path the current prebuilt is still the one used
        self.assertEqual(shutil_go.returncode, 0)
        self.assertFalse(self.log.exists(), "a current prebuilt means go does not run")

    def test_go_builds_a_newer_shell_than_a_stale_prebuilt(self):
        self.add_prebuilt(stamp="000000000000")
        done = self.run_script("--shell")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("built", done.stdout)
        self.assertTrue(self.log.exists(), "the sources changed since the prebuilt: go builds")

    def test_without_go_a_stale_prebuilt_still_beats_nothing(self):
        self.add_prebuilt(stamp="000000000000")
        done = self.run_script("--shell", env=self.bare_env())
        self.assertEqual(done.returncode, 0, done.stdout)
        self.assertIn("prebuilt", done.stdout)
        self.assertTrue((self.home / "bin" / "rig-shell").is_file())

    def test_a_failed_build_falls_back_to_the_prebuilt(self):
        self.add_prebuilt(stamp="000000000000")
        done = self.run_script("--shell", env={**self.env, "FAKE_GO_FAIL": "1"})
        self.assertEqual(done.returncode, 0, done.stdout)
        self.assertIn("prebuilt", done.stdout)

    def test_a_prebuilt_that_does_not_match_its_sums_is_never_installed(self):
        self.add_prebuilt(sums_ok=False)
        done = self.run_script("--shell", env=self.bare_env())
        self.assertEqual(done.returncode, 1)
        self.assertFalse((self.home / "bin" / "rig-shell").exists())

    def test_a_prebuilt_that_does_not_run_is_never_installed(self):
        self.add_prebuilt(body="not a program\n")
        done = self.run_script("--shell", env=self.bare_env())
        self.assertEqual(done.returncode, 1)
        self.assertFalse((self.home / "bin" / "rig-shell").exists())

    def test_download_from_the_repository_when_the_tree_has_none(self):
        name = shell_dist.asset_name()
        if not name:
            self.skipTest("no prebuilt target for this platform")
        mirror = self.base / "mirror" / "main" / "prebuilt"
        mirror.mkdir(parents=True)
        (mirror / name).write_text('#!/bin/sh\necho "rig-shell src-abc"\n')
        (mirror / name).chmod(0o755)
        (mirror / "SHA256SUMS").write_text(f"{shell_dist.sha256_of(mirror / name)}  {name}\n")
        env = {**self.bare_env(), "RIG_SHELL_NO_DOWNLOAD": "", "RIG_SHELL_DIST_URL": (self.base / "mirror").as_uri()}
        done = self.run_script("--shell", env=env)
        self.assertEqual(done.returncode, 0, done.stdout)
        self.assertIn("downloaded", done.stdout)
        self.assertTrue((self.home / "bin" / "rig-shell").is_file())
        (mirror / "SHA256SUMS").write_text(f"{'1' * 64}  {name}\n")
        (self.home / "bin" / "rig-shell").unlink()
        refused = self.run_script("--shell", env=env)
        self.assertEqual(refused.returncode, 1, "a download that does not match the sums is refused")
        self.assertFalse((self.home / "bin" / "rig-shell").exists())

    def test_the_shipped_prebuilt_folder_is_whole_and_matches_its_sums(self):
        folder = ROOT / "prebuilt"
        sums = shell_dist.read_sums((folder / "SHA256SUMS").read_text())
        self.assertEqual(sorted(sums), sorted(f"rig-shell-{o}-{a}" for o, a in shell_dist.TARGETS))
        for name, digest in sums.items():
            self.assertEqual(shell_dist.sha256_of(folder / name), digest, name)
            self.assertTrue(os.access(folder / name, os.X_OK), name)
        self.assertEqual(len(shell_dist.read_stamp(folder)), 12)

    def test_prebuilt_state_says_when_the_sources_moved_on(self):
        (self.src / "shell" / "main.go").write_text("package main\n")
        self.assertEqual(shell_dist.prebuilt_state(self.src)["state"], "none", "nothing prebuilt to compare")
        self.add_prebuilt()
        self.assertEqual(shell_dist.prebuilt_state(self.src)["state"], "current")
        (self.src / "shell" / "main.go").write_text("package main // changed\n")
        info = shell_dist.prebuilt_state(self.src)
        self.assertEqual(info["state"], "stale")
        done = subprocess.run([sys.executable, str(ROOT / "scripts" / "shell_dist.py"), "status", "--src", str(self.src)],
                              capture_output=True, text=True)
        self.assertEqual(done.returncode, 1)
        self.assertIn("shell_dist.py build", done.stdout)

    def test_doctor_flags_stale_prebuilt_shells_in_a_source_tree_only(self):
        current = doctor.prebuilt_check()
        self.assertIsNotNone(current, "this tree ships prebuilt shells")
        self.assertEqual(current["status"], "ready", "the shipped shells match the shipped sources: rebuild them with "
                         "python3 scripts/shell_dist.py build")
        with mock.patch.object(doctor, "HERE", self.src / "scripts"):
            self.assertIsNone(doctor.prebuilt_check(), "an installed runtime has no sources to compare")
            self.add_prebuilt(stamp="000000000000")
            (self.src / "shell" / "main.go").write_text("package main\n")
            stale = doctor.prebuilt_check()
        self.assertEqual((stale["status"], stale["required"]), ("failed", False))
        self.assertIn("shell_dist.py build", stale["detail"])

    def test_the_stamp_follows_the_shell_sources_and_not_their_tests(self):
        (self.src / "shell" / "main.go").write_text("package main\n")
        before = shell_dist.source_stamp(self.src)
        (self.src / "shell" / "main_test.go").write_text("package main\n")
        (self.src / "shell" / "testdata").mkdir()
        (self.src / "shell" / "testdata" / "x.go").write_text("package x\n")
        self.assertEqual(shell_dist.source_stamp(self.src), before)
        (self.src / "shell" / "main.go").write_text("package main // changed\n")
        self.assertNotEqual(shell_dist.source_stamp(self.src), before)


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
            ("echo rig-shell src-0123456789ab", "v1 abc1234\n", "prebuilt", "ready"),
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
        self.assertIn("rig shell", check["detail"])


if __name__ == "__main__":
    unittest.main()
