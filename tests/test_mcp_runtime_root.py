"""rig-mcp.sh binds RIG_HOME to a versioned runtime parent when unset.

Uses a fake adjacent rig_mcp.py that prints the env root. No MCP/network.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LAUNCHER_SRC = ROOT / 'scripts' / 'rig-mcp.sh'


class McpRuntimeRoot(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name).resolve()
        self.home = self.base / 'home'
        self.home.mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def install_launcher(self, runtime: Path, *, versioned: bool) -> Path:
        scripts = runtime / 'scripts'
        scripts.mkdir(parents=True)
        launcher = scripts / 'rig-mcp.sh'
        shutil.copyfile(LAUNCHER_SRC, launcher)
        launcher.chmod(0o755)
        # Fake MCP: print effective RIG_HOME (empty string when unset).
        (scripts / 'rig_mcp.py').write_text(
            'import os\nprint(os.environ.get("RIG_HOME", ""), end="")\n'
        )
        if versioned:
            (runtime / 'runtime-state.json').write_text('{"schema_version": 1}\n')
        return launcher

    def run_launcher(self, launcher: Path, *, rig_home=None, unset=False) -> str:
        env = {**os.environ, 'HOME': str(self.home), 'PYTHONDONTWRITEBYTECODE': '1'}
        env.pop('RIG_HOME', None)
        if unset:
            pass
        elif rig_home is not None:
            env['RIG_HOME'] = str(rig_home)
        result = subprocess.run(
            ['bash', str(launcher)],
            capture_output=True, text=True, env=env, timeout=10, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def test_versioned_install_binds_own_runtime_when_unset(self):
        runtime = self.base / 'relocated' / '.rig-versioned'
        launcher = self.install_launcher(runtime, versioned=True)
        self.assertEqual(self.run_launcher(launcher, unset=True), str(runtime.resolve()))

    def test_explicit_rig_home_override_wins(self):
        runtime = self.base / 'install' / '.rig-versioned'
        other = self.base / 'other-root'
        other.mkdir()
        launcher = self.install_launcher(runtime, versioned=True)
        self.assertEqual(self.run_launcher(launcher, rig_home=other), str(other))

    def test_unversioned_and_source_leave_unset(self):
        unversioned = self.base / 'legacy' / '.rig'
        launcher = self.install_launcher(unversioned, versioned=False)
        self.assertEqual(self.run_launcher(launcher, unset=True), '')

        # Real checkout launcher: no runtime-state.json beside scripts/ → unset.
        self.assertFalse((ROOT / 'runtime-state.json').exists())
        env = {**os.environ, 'HOME': str(self.home), 'PYTHONDONTWRITEBYTECODE': '1'}
        env.pop('RIG_HOME', None)
        # Replace adjacent MCP only in a copy so the real tree is untouched.
        copy_root = self.base / 'source-copy'
        scripts = copy_root / 'scripts'
        scripts.mkdir(parents=True)
        shutil.copyfile(LAUNCHER_SRC, scripts / 'rig-mcp.sh')
        (scripts / 'rig-mcp.sh').chmod(0o755)
        (scripts / 'rig_mcp.py').write_text(
            'import os\nprint(os.environ.get("RIG_HOME", ""), end="")\n'
        )
        result = subprocess.run(
            ['bash', str(scripts / 'rig-mcp.sh')],
            capture_output=True, text=True, env=env, timeout=10, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, '')

    def test_source_with_versioned_evidence_binds(self):
        """Checkout-shaped tree that already carries runtime-state.json binds."""
        source = self.base / 'Rig'
        launcher = self.install_launcher(source, versioned=True)
        self.assertEqual(self.run_launcher(launcher, unset=True), str(source.resolve()))

    def test_repo_launcher_integration_against_fake_adjacent(self):
        """Integration: current scripts/rig-mcp.sh bytes + versioned parent → bind."""
        runtime = self.base / 'integration-root'
        launcher = self.install_launcher(runtime, versioned=True)
        self.assertEqual(launcher.read_text(), LAUNCHER_SRC.read_text())
        self.assertEqual(self.run_launcher(launcher, unset=True), str(runtime.resolve()))
        # Empty string override is treated as unset and still binds.
        self.assertEqual(self.run_launcher(launcher, rig_home=''), str(runtime.resolve()))


if __name__ == '__main__':
    unittest.main()
