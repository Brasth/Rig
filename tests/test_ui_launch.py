import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import ui_launch


class LaunchTests(unittest.TestCase):
    def test_headless_and_worker_passthrough(self):
        for args in (['exec', 'hello'], ['--help'], ['--worktree'], ['--unknown']):
            self.assertIsNone(ui_launch.classify('codex', args, env={}, tty=True))
        self.assertIsNone(ui_launch.classify('codex', [], env={}, tty=False))
        self.assertIsNone(ui_launch.classify('codex', [], env={'RIG_WORKER': '1'}, tty=True))

    def test_status_and_keys_are_session_local(self):
        with patch.dict(os.environ, {}, clear=True):
            commands = ui_launch.session_commands('rig-123456abcdef', Path('/repo with space'), 'codex', '/host bin/codex', ['-C', 'subdir'])
        self.assertTrue(all('-g' not in command for command in commands))
        self.assertTrue(all('root' not in command for command in commands))
        self.assertIn("'/host bin/codex' -C subdir", commands[0][-1])
        self.assertEqual(commands[0][-2], os.getcwd())
        self.assertEqual(commands[-1][-5:-1], ['-w', '85%', '-h', '80%'])

    def test_tmux_versions(self):
        for version, expected in [('tmux 3.2a', False), ('tmux 3.3a', True), ('tmux 3.6', True)]:
            with patch('ui_launch.subprocess.run') as run:
                run.return_value.stdout = version
                run.return_value.returncode = 0
                self.assertEqual(bool(ui_launch.tmux_available('tmux')), expected)

    def test_private_server_outside_tmux(self):
        self.assertEqual(ui_launch.server_args({'TMUX': '/user/socket'}), [])
        self.assertEqual(ui_launch.server_args({}), ['-L', 'rig-ui', '-f', '/dev/null'])

    def test_fallback_forwards_exact_arguments(self):
        with patch('ui_launch.classify', return_value=None), patch('ui_launch.os.execvpe', side_effect=RuntimeError('exec')) as execute:
            with self.assertRaises(RuntimeError):
                ui_launch.main(['codex', '/bin/codex', '--', 'exec', 'a b', '--json'])
        self.assertEqual(execute.call_args.args[:2], ('/bin/codex', ['/bin/codex', 'exec', 'a b', '--json']))

    def test_host_specific_headless_and_interactive_flags(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory).resolve()
            (repo / '.rig').mkdir()
            (repo / '.rig/harness.toml').touch()
            with patch('ui_launch.subprocess.run') as run:
                run.return_value.stdout = str(repo)
                run.return_value.returncode = 0
                self.assertEqual(ui_launch.classify('grok', ['--minimal', '--reasoning-effort', 'high', 'fix the bug'], env={}, tty=True), repo)
                self.assertEqual(ui_launch.classify('codex', ['-p', 'default', 'fix the bug'], env={}, tty=True), repo)
                for args in [['-p', 'hello'], ['--single'], ['--single=true']]:
                    self.assertIsNone(ui_launch.classify('grok', args, env={}, tty=True))
                self.assertIsNone(ui_launch.classify('codex', [], env={'RIG_JOB_ID': 'job'}, tty=True))

    def test_supervisor_uses_private_payload_not_command_environment(self):
        commands = ui_launch.session_commands('rig-123456abcdef', Path('/repo'), 'grok', '/bin/grok', ['secret prompt'], Path('/private/launch.json'))
        self.assertNotIn('secret prompt', commands[0][-1])
        self.assertIn('--child /private/launch.json', commands[0][-1])
        self.assertIn(['set-option', '-t', 'rig-123456abcdef', 'status-interval', '1'], commands)

    def test_sessions_returns_only_rig_names(self):
        with patch('ui_launch.shutil.which', return_value='tmux'), patch('ui_launch.subprocess.run') as run:
            run.return_value.stdout = 'user-session\nrig-123456abcdef\n'
            self.assertEqual(ui_launch.sessions(), ['rig-123456abcdef'])

    def test_supervisor_preserves_fresh_environment_and_exit_status(self):
        import contextlib
        import json
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'launch.json'
            path.write_text(json.dumps({'env': {'FRESH': 'current'}, 'session': 'rig-123456abcdef', 'repo': '/repo', 'home': directory, 'endpoint': 'socket', 'tmux': ['tmux', '-L', 'test'], 'executable': '/bin/host', 'args': ['a b']}))
            path.with_suffix('.ready').touch()
            with patch('ui_launch.signal.signal'), patch('ui_launch.subprocess.call', return_value=7) as call, patch('ui_launch.subprocess.run') as run, patch('ui_service.request'), patch('ui_install.lifecycle_lock', return_value=contextlib.nullcontext()), patch('ui_install.remove_lease') as remove:
                self.assertEqual(ui_launch.run_child(path), 7)
                self.assertEqual(call.call_args.kwargs['env']['FRESH'], 'current')
                self.assertEqual(call.call_args.args[0], ['/bin/host', 'a b'])
                remove.assert_called_once_with('rig-123456abcdef', directory)
                self.assertEqual(run.call_args.args[0][-4:], ['unbind-key', '-a', '-T', 'rig-rig-123456abcdef'])
            self.assertEqual(path.with_suffix('.exit').read_text(), '7')
            self.assertFalse(path.exists())

    def test_admin_commands_and_distinct_hotkeys(self):
        for host, commands in [('codex', ['queue', 'plugin', 'doctor', 'agents']), ('grok', ['agent', 'export', 'trace', 'inspect', 'leader', 'dashboard'])]:
            for command in commands:
                self.assertIsNone(ui_launch.classify(host, [command], env={}, tty=True))
        with patch.dict(os.environ, {'RIG_UI_MANAGER_KEY': 'F9', 'RIG_UI_ADD_KEY': 'F9'}):
            commands = ui_launch.session_commands('rig-123456abcdef', Path('/repo'), 'codex', '/bin/host', [])
        self.assertEqual(commands[-2][3], 'F8')
        self.assertEqual(commands[-1][3], 'F9')


    def test_unsupported_function_keys_use_defaults(self):
        with patch.dict(os.environ, {'RIG_UI_MANAGER_KEY': 'F99', 'RIG_UI_ADD_KEY': 'F0'}):
            commands = ui_launch.session_commands('rig-123456abcdef', Path('/repo'), 'codex', '/bin/host', [])
        self.assertEqual(commands[-2][3], 'F8')
        self.assertEqual(commands[-1][3], 'F9')
