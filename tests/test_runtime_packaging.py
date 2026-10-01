"""Exercise modular MCP and advisory tools from a real isolated installation."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class RuntimePackaging(unittest.TestCase):
    def test_install_preserves_modules_and_parent_child_tool_boundaries(self):
        with tempfile.TemporaryDirectory() as temporary:
            home, repo = Path(temporary) / 'home', Path(temporary) / 'project'
            home.mkdir()
            repo.mkdir()
            kit = home / '.rig'
            env = {**os.environ, 'HOME': str(home), 'RIG_HOME': str(kit), 'RIG_SRC': str(ROOT),
                   'RIG_SKIP_UPDATE_CHECK': '1', 'RIG_SKIP_MODEL_CATALOG': '1',
                   'RIG_SKIP_TMUX_INSTALL': '1', 'PYTHONDONTWRITEBYTECODE': '1'}
            for key in ('RIG_INSTALL_TRANSACTION', 'RIG_LIFECYCLE_FD', 'RIG_LIVE', 'RIG_JOB_ID', 'RIG_JOB_DIR'):
                env.pop(key, None)
            installed = subprocess.run(['bash', str(ROOT / 'bin/rig'), 'setup', '--no-cua-driver',
                                        '--no-browser-skill', '--no-mimo'], cwd=repo, env=env,
                                       capture_output=True, text=True, timeout=60)
            self.assertEqual(installed.returncode, 0, installed.stderr)
            entries = json.loads((kit / 'install-manifest.json').read_text())['entries']
            sources = list((ROOT / 'scripts/mcp_tools').glob('*.py')) + [
                ROOT / 'scripts' / name for name in ('preparation_inputs.py', 'task_preparation.py',
                                                      'recovery_guide.py', 'recovery_view.py')]
            for source in sources:
                target = kit / source.relative_to(ROOT)
                self.assertEqual(target.read_bytes(), source.read_bytes())
                self.assertIn(str(target), entries)
            env.pop('RIG_SRC')
            request = {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'}
            def rpc(message, environment=env):
                completed = subprocess.run([sys.executable, str(kit / 'scripts/rig_mcp.py')],
                    input=json.dumps(message) + '\n', cwd=repo, env=environment,
                    capture_output=True, text=True, timeout=15)
                self.assertEqual(completed.returncode, 0, completed.stderr)
                return json.loads(completed.stdout.splitlines()[-1])['result']
            parent = {row['name'] for row in rpc(request)['tools']}
            self.assertTrue({'rig_task_prepare', 'rig_recovery_guide'} <= parent)
            child = {row['name'] for row in rpc(request, {**env, 'RIG_JOB_ID': 'child'})['tools']}
            self.assertNotIn('rig_task_prepare', child)
            self.assertNotIn('rig_recovery_guide', child)
            selection = {'task': 'Prepare a fix', 'files': ['new.py'],
                         'manual_criteria': ['Parent inspects the result']}
            draft_path = home / 'draft.json'
            draft_path.write_text(json.dumps(selection))
            cli = subprocess.run(['bash', str(kit / 'bin/rig'), 'task', 'prepare', '--file', str(draft_path),
                                  '--json'], cwd=repo, env=env, capture_output=True, text=True, timeout=15)
            self.assertEqual(cli.returncode, 0, cli.stderr)
            prepared = rpc({'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call',
                           'params': {'name': 'rig_task_prepare', 'arguments': {'repo': str(repo),
                                                                             'selection': selection}}})
            self.assertEqual(json.loads(cli.stdout), prepared['structuredContent'])
            self.assertFalse(json.loads(cli.stdout)['ready'])
            recovery = subprocess.run(['bash', str(kit / 'bin/rig'), 'recovery', 'guide', '--job', 'absent',
                                       '--json'], cwd=repo, env=env, capture_output=True, text=True, timeout=15)
            self.assertEqual(recovery.returncode, 0, recovery.stderr)
            self.assertEqual(json.loads(recovery.stdout)['coverage'], 'unknown')
            self.assertFalse((repo / '.rig').exists())
