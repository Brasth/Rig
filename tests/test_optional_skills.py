"""Setup/init/update consent coverage; all installs use an isolated home and stubs."""
import json
import os
from pathlib import Path
import pty
import select
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import optional_skills

GLOBAL_DIRS = (
    ".agents/skills", ".grok/skills", ".codex/skills", ".config/opencode/skill",
    ".omp/agent/skills", ".pi/agent/skills", ".gemini/antigravity-cli/skills",
)


class OptionalSkills(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.home = self.base / "home"
        self.kit = self.home / ".rig"
        self.repo = self.base / "repo"
        self.stub = self.base / "stub"
        for path in (self.home, self.repo, self.stub):
            path.mkdir()
        (self.repo / ".git").mkdir()
        # No network, installers, package managers, or real user configurations.
        self.env = {
            "HOME": str(self.home), "RIG_HOME": str(self.kit), "RIG_SRC": str(ROOT),
            "PATH": str(self.stub) + os.pathsep + os.environ["PATH"],
            "RIG_SKIP_TMUX_INSTALL": "1", "RIG_SKIP_UPDATE_CHECK": "1",
            "RIG_SKIP_MODEL_CATALOG": "1", "INSTALL_LOG": str(self.base / "installs"),
        }
        self.write_stub("curl", '''if [ "$3" = "-o" ]; then
  cp "${2#file://}" "$4"
else
  printf 'echo install >> "$INSTALL_LOG"\\n'
fi
''')
        for name in ("cua-driver", "bsk"):
            self.write_stub(name, "echo stub-1.0\n")

    def write_stub(self, name, text):
        path = self.stub / name
        path.write_text("#!/bin/bash\n" + text)
        path.chmod(0o755)

    def run_rig(self, *args, installer=False, expected_code=0, **env):
        command = ["bash", str(ROOT / "install.sh")] if installer else [str(ROOT / "bin/rig")]
        result = subprocess.run(command + list(args), cwd=self.repo, env={**self.env, **env},
                                stdin=subprocess.DEVNULL, capture_output=True, text=True,
                                start_new_session=True, timeout=90)
        self.assertEqual(result.returncode, expected_code, result.stdout + result.stderr)
        return result

    def pref(self, name, value):
        self.kit.mkdir(exist_ok=True, parents=True)
        (self.kit / name).write_text(json.dumps({"opt_in": value}))

    def interactive_setup(self, answers):
        master, slave = pty.openpty()
        proc = subprocess.Popen([str(ROOT / "bin/rig"), "setup", "--no-mimo"],
                                cwd=self.repo, env=self.env, stdin=slave, stdout=slave,
                                stderr=slave, start_new_session=True)
        os.close(slave)
        output = b""
        sent = 0
        deadline = time.monotonic() + 90
        try:
            while time.monotonic() < deadline:
                if select.select([master], [], [], 0.2)[0]:
                    try:
                        chunk = os.read(master, 65536)
                    except OSError:
                        break
                    if not chunk:
                        break
                    output += chunk
                    if output.count(b"[y/N]") > sent:
                        self.assertLess(sent, len(answers), output.decode())
                        os.write(master, (answers[sent] + "\n").encode())
                        sent += 1
                elif proc.poll() is not None:
                    break
            self.assertEqual(proc.wait(timeout=5), 0, output.decode())
            self.assertEqual(sent, len(answers), output.decode())
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
            os.close(master)
        return output.decode()

    def assert_global(self, enabled):
        for directory in GLOBAL_DIRS:
            for skill in optional_skills.OPTIONAL_SKILLS:
                self.assertEqual((self.home / directory / skill).is_symlink(), enabled,
                                 f"{directory}/{skill}")
            self.assertTrue((self.home / directory / "delegate-harness" / "SKILL.md").is_file())

    def assert_project(self, enabled):
        for skill in optional_skills.OPTIONAL_SKILLS:
            self.assertEqual((self.repo / ".agents/skills" / skill / "SKILL.md").is_file(), enabled)
        self.assertTrue((self.repo / ".agents/skills/delegate-harness/SKILL.md").is_file())

    def test_clean_noninteractive_setup_and_init_skip_optional(self):
        result = self.run_rig("setup", "--no-mimo")
        self.assertEqual(result.stdout.count("skipped (no TTY)"), 2)
        self.assert_global(False)
        self.run_rig("init")
        self.assert_project(False)
        self.assertFalse((self.base / "installs").exists())

    def test_shell_ui_without_extra_arguments_keeps_optional_backends_skipped(self):
        self.write_stub("tmux", "printf 'tmux 3.3\\n'\n")
        result = self.run_rig("setup", "--shell-ui", "--no-mimo")
        self.assertIn("Shell UI enabled", result.stdout)
        self.assert_global(False)
        self.assertTrue((self.kit / "ui/shell-enabled").is_file())
        self.assertIn("# >>> Rig shell UI >>>", (self.home / ".bashrc").read_text())
        self.assertFalse((self.base / "installs").exists())

    def test_interactive_default_no_remembers_both_choices(self):
        output = self.interactive_setup(["", ""])
        self.assertIn("Rig's desktop/browser skills", output)
        self.assert_global(False)
        for name in optional_skills.PREFERENCES:
            self.assertIs(json.loads((self.kit / name).read_text())["opt_in"], False)
        self.assertFalse((self.base / "installs").exists())

    def test_interactive_browser_yes_installs_bundle(self):
        self.interactive_setup(["n", "y"])
        self.assert_global(True)
        self.assertEqual((self.base / "installs").read_text().splitlines(), ["install"])
        self.run_rig("init")
        self.assert_project(True)

    def test_accept_each_backend_installs_shared_skills_without_enabling_repo(self):
        self.run_rig("setup", "--no-cua-driver", "--no-browser-skill", "--no-mimo")
        for backend in ("cua-driver", "browser-skill"):
            with self.subTest(backend=backend):
                other = "browser-skill" if backend == "cua-driver" else "cua-driver"
                self.run_rig("setup", "--" + backend, "--no-" + other, "--no-mimo")
                self.assert_global(True)
                self.run_rig("init")
                self.assert_project(True)
                text = (self.repo / ".rig/harness.toml").read_text()
                for section in ("computer-use", "browser-skill"):
                    self.assertIn("enabled = false", text.split("[" + section + "]")[1].split("[")[0])
                reference = self.repo / ".agents/skills/computer-use/references/logged-in-browser.md"
                self.assertTrue(reference.is_file())
                manifest = json.loads((self.kit / "install-manifest.json").read_text())["entries"]
                self.assertIn(str(reference), manifest)
                self.assertEqual(manifest[str(reference)]["before"]["kind"], "missing")
                for directory in GLOBAL_DIRS:
                    for skill in optional_skills.OPTIONAL_SKILLS:
                        entry = manifest[str(self.home / directory / skill)]
                        self.assertEqual(entry["before"]["kind"], "missing")
                        self.assertEqual(entry["after"]["kind"], "link")

    def test_decline_survives_refused_implicit_update_and_init(self):
        self.run_rig("--no-cua-driver", "--no-browser-skill", "--no-mimo", installer=True)
        for name in optional_skills.PREFERENCES:
            self.assertIs(json.loads((self.kit / name).read_text())["opt_in"], False)
        # Bare update is now usage-only and must never invoke an installer.
        # Accepted/declined consent through pinned A→B→A is covered by the
        # real controller fixtures in test_runtime_update.py.
        wrapper = self.base / "install-wrapper.sh"
        wrapper.write_text(f'#!/bin/bash\nexec bash "{ROOT / "install.sh"}" --no-mimo\n')
        self.run_rig("init")
        result = self.run_rig("update", expected_code=2, RIG_INSTALL_SH=str(wrapper))
        self.assertIn("--revision", result.stderr)
        self.run_rig("init")
        self.assert_global(False)
        self.assert_project(False)
        self.assertFalse((self.base / "installs").exists())

    def test_install_sh_runs_each_accepted_installer_once(self):
        self.run_rig("--cua-driver", "--browser-skill", "--no-mimo", installer=True)
        self.assertEqual((self.base / "installs").read_text().splitlines(), ["install", "install"])
        self.assert_global(True)

    def test_uninstall_removes_owned_optional_links_and_project_references(self):
        self.run_rig("setup", "--browser-skill", "--no-cua-driver", "--no-mimo")
        self.run_rig("init")
        self.run_rig("uninstall")
        for directory in GLOBAL_DIRS:
            for skill in optional_skills.OPTIONAL_SKILLS:
                self.assertFalse((self.home / directory / skill).is_symlink())
        reference = self.repo / ".agents/skills/computer-use/references/logged-in-browser.md"
        self.assertFalse(reference.exists())
        self.assertTrue((self.repo / ".rig/harness.toml").exists())

    def test_skipped_runs_never_claim_later_user_installed_skills(self):
        self.run_rig("setup", "--no-cua-driver", "--no-browser-skill", "--no-mimo")
        self.run_rig("init")
        target = self.base / "user-computer-use"
        target.mkdir()
        (target / "SKILL.md").write_text("user skill")
        link = self.home / ".codex/skills/computer-use"
        link.symlink_to(target)
        bundled_link = self.home / ".grok/skills/computer-use"
        bundled_link.symlink_to(os.path.relpath(self.kit / "skills/computer-use", bundled_link.parent))
        project = self.repo / ".agents/skills/computer-use/SKILL.md"
        project.parent.mkdir(parents=True)
        project.write_text("user project skill")
        self.run_rig("setup", "--no-mimo")
        self.run_rig("init")
        self.run_rig("uninstall")
        self.assertTrue(link.is_symlink())
        self.assertEqual(link.resolve(), target)
        self.assertTrue((bundled_link / "SKILL.md").is_file(), "manual runtime reference must not dangle")
        self.assertEqual(project.read_text(), "user project skill")

    def test_later_opt_in_restores_intervening_user_skill_on_uninstall(self):
        self.run_rig("setup", "--no-cua-driver", "--no-browser-skill", "--no-mimo")
        target = self.base / "user-computer-use"
        target.mkdir()
        (target / "SKILL.md").write_text("user skill")
        link = self.home / ".codex/skills/computer-use"
        link.symlink_to(target)
        self.run_rig("setup", "--browser-skill", "--no-mimo")
        self.assertEqual(link.resolve(), self.kit / "skills/computer-use")
        self.run_rig("init")
        self.run_rig("uninstall")
        self.assertTrue(link.is_symlink())
        self.assertEqual(link.resolve(), target)

    def test_skipped_project_directory_link_retains_bundled_runtime(self):
        self.run_rig("setup", "--no-cua-driver", "--no-browser-skill", "--no-mimo")
        self.run_rig("init")
        link = self.repo / ".agents/skills/computer-use"
        link.symlink_to(self.kit / "skills/computer-use")
        self.run_rig("init")
        self.run_rig("uninstall")
        self.assertTrue(link.is_symlink())
        self.assertTrue((link / "SKILL.md").is_file(), "manual project directory link must not dangle")

    def test_existing_opt_in_refreshes_without_prompt(self):
        self.pref("browser-skill.json", True)
        self.pref("cua-driver.json", False)
        result = self.run_rig("setup", "--no-mimo")
        self.assertNotIn("[y/N]", result.stdout)
        self.assert_global(True)
        self.run_rig("init")
        self.assert_project(True)

    def test_legacy_skills_or_binary_presence_does_not_imply_consent(self):
        own = self.home / ".agents/skills/computer-use"
        own.mkdir(parents=True)
        (own / "SKILL.md").write_text("user-owned skill")
        legacy = self.repo / ".agents/skills/computer-use"
        legacy.mkdir(parents=True)
        (legacy / "SKILL.md").write_text("legacy skill")
        self.run_rig("setup", "--no-cua-driver", "--no-browser-skill", "--no-mimo")
        self.run_rig("init")
        self.assertEqual((own / "SKILL.md").read_text(), "user-owned skill")
        self.assertEqual((legacy / "SKILL.md").read_text(), "legacy skill")
        self.assertFalse((self.home / ".codex/skills/computer-use").exists())
        self.assertFalse((self.repo / ".agents/skills/computer-test").exists())

    def test_declining_does_not_uninstall_existing_opted_in_skills(self):
        self.run_rig("setup", "--cua-driver", "--no-browser-skill", "--no-mimo")
        self.run_rig("init")
        self.run_rig("setup", "--no-cua-driver", "--no-browser-skill", "--no-mimo")
        self.run_rig("init")
        self.assert_global(True)
        self.assert_project(True)
        # Removing one old copy must not cause a skipped update to recreate it.
        (self.home / ".codex/skills/computer-use").unlink()
        self.run_rig("setup", "--no-mimo")
        self.assertFalse((self.home / ".codex/skills/computer-use").exists())

    def test_malformed_or_truthy_preferences_fail_closed(self):
        self.kit.mkdir(parents=True)
        for value in ("true", 1, {}, None):
            self.pref("cua-driver.json", value)
            self.assertFalse(optional_skills.enabled(self.kit), repr(value))
        (self.kit / "cua-driver.json").write_text("bad json")
        self.assertFalse(optional_skills.enabled(self.kit))
        self.pref("browser-skill.json", True)
        self.assertTrue(optional_skills.enabled(self.kit))

    def test_conflicting_flags_fail_before_installation(self):
        for backend in ("cua-driver", "browser-skill"):
            result = subprocess.run([str(ROOT / "bin/rig"), "setup", "--" + backend, "--no-" + backend],
                                    cwd=self.repo, env=self.env, stdin=subprocess.DEVNULL,
                                    capture_output=True, text=True, start_new_session=True)
            self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertFalse((self.kit / "bin/rig").exists())
        self.assertFalse((self.base / "installs").exists())


if __name__ == "__main__":
    unittest.main()
