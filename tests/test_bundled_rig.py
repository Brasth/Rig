#!/usr/bin/env python3
"""A Rig carried inside an app runs its own scripts, not an older install's."""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class BundledRig(unittest.TestCase):
    def run_rig(self, **extra):
        with tempfile.TemporaryDirectory() as base:
            home = str(Path(base) / "home")
            repo = Path(base) / "repo"
            (repo / ".rig").mkdir(parents=True)
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            (repo / ".rig" / "harness.toml").write_text('parent = "claude"\n\n[project]\nenabled = true\n')
            scripts = Path(home) / "scripts"
            scripts.mkdir(parents=True)
            scripts = scripts
            (scripts / "local_api.py").write_text('print("OLD INSTALL")\n')
            env = {**os.environ, "RIG_HOME": home, **extra}
            env.pop("RIG_BUNDLED", None) if "RIG_BUNDLED" not in extra else None
            return subprocess.run([str(ROOT / "bin" / "rig"), "api", "doc"], capture_output=True, text=True, env=env, cwd=repo, timeout=60)

    def test_an_installs_scripts_win_by_default(self):
        self.assertIn("OLD INSTALL", self.run_rig().stdout)

    def test_a_bundled_rig_uses_the_scripts_beside_it(self):
        out = self.run_rig(RIG_BUNDLED="1")
        self.assertNotIn("OLD INSTALL", out.stdout)
        self.assertIn("chat.parent", out.stdout)


if __name__ == "__main__":
    unittest.main()
