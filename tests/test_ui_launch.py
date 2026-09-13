import os
import subprocess
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
                self.assertEqual(call.call_args.kwargs['cwd'], '/repo')
                self.assertEqual(call.call_args.args[0], ['/bin/host', 'a b'])
                remove.assert_called_once_with('rig-123456abcdef', directory)
                self.assertEqual(run.call_args.args[0][-4:], ['unbind-key', '-a', '-T', 'rig-rig-123456abcdef'])
            self.assertEqual(path.with_suffix('.exit').read_text(), '7')
            self.assertFalse(path.exists())

    def test_supervisor_recovers_deleted_cwd_and_preserves_relative_arguments(self):
        import subprocess
        import tempfile
        import textwrap
        with tempfile.TemporaryDirectory() as directory:
            script = textwrap.dedent('''
                import contextlib, json, os, sys
                from pathlib import Path
                from unittest.mock import patch
                import ui_launch
                root = Path(sys.argv[1])
                invocation = root / 'invocation'
                invocation.mkdir()
                (invocation / 'subdir').mkdir()
                deleted = root / 'deleted'
                deleted.mkdir()
                os.chdir(deleted)
                deleted.rmdir()
                path = root / 'launch.json'
                host = "import os,sys; os.chdir(sys.argv[1]); print(os.getcwd())"
                path.write_text(json.dumps({'env': dict(os.environ), 'session': 'test',
                    'repo': str(root), 'cwd': str(invocation), 'home': str(root),
                    'endpoint': 'unused', 'tmux': ['tmux'], 'executable': sys.executable,
                    'args': ['-c', host, 'subdir']}))
                path.with_suffix('.ready').touch()
                with patch('ui_launch.subprocess.run'), patch('ui_service.request'), \
                     patch('ui_install.lifecycle_lock', return_value=contextlib.nullcontext()), \
                     patch('ui_install.remove_lease'):
                    assert ui_launch.run_child(path) == 0
                assert path.with_suffix('.exit').read_text() == '0'
            ''')
            env = dict(os.environ, PYTHONPATH=str(Path(ui_launch.__file__).parent))
            result = subprocess.run([sys.executable, '-c', script, directory], env=env,
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(Path(result.stdout.strip()), Path(directory).resolve() / 'invocation/subdir')

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

    def test_session_mouse_and_wheel_are_table_local(self):
        with patch.dict(os.environ, {}, clear=True):
            commands = ui_launch.session_commands('rig-123456abcdef', Path('/repo'), 'codex', '/bin/host', [])
        table = 'rig-rig-123456abcdef'
        self.assertIn(['set-option', '-t', 'rig-123456abcdef', 'mouse', 'on'], commands)
        self.assertIn(
            ['bind-key', '-T', table, 'WheelUpPane', 'select-pane -t = ; copy-mode -e ; send-keys -X -N 5 scroll-up'],
            commands,
        )
        self.assertIn(['bind-key', '-T', table, 'WheelDownPane', 'select-pane -t ='], commands)
        self.assertTrue(all('-g' not in command for command in commands))
        self.assertTrue(all('-T' not in command or 'root' not in command for command in commands))
        self.assertTrue(all(command[:2] != ['bind-key', '-n'] for command in commands))
        self.assertEqual(commands[-2][3], 'F8')
        self.assertEqual(commands[-1][3], 'F9')


class IsolatedTmuxServer(unittest.TestCase):
    def setUp(self):
        import shutil
        import uuid
        self.binary = shutil.which('tmux')
        if not self.binary or not ui_launch.tmux_available(self.binary):
            self.skipTest('tmux 3.3+ required')
        self.socket = 'rig-test-' + uuid.uuid4().hex[:12]
        self.base = [self.binary, '-L', self.socket, '-f', '/dev/null']
        self.session = 'rig-123456abcdef'

    def tearDown(self):
        if not getattr(self, 'base', None):
            return
        subprocess.run(self.base + ['kill-server'], capture_output=True)

    def _run(self, args):
        return subprocess.run(self.base + args, capture_output=True, text=True)

    def test_isolated_server_keeps_global_and_root_unchanged(self):
        created = self._run(['new-session', '-d', '-s', self.session, '-c', os.getcwd(), 'sleep', '30'])
        self.assertEqual(created.returncode, 0, created.stderr)
        before_global = self._run(['show-options', '-g', 'mouse'])
        before_root = self._run(['list-keys', '-T', 'root'])
        before_copy = self._run(['list-keys', '-T', 'copy-mode'])
        before_copy_vi = self._run(['list-keys', '-T', 'copy-mode-vi'])
        self.assertEqual(before_global.returncode, 0, before_global.stderr)
        self.assertEqual(before_root.returncode, 0, before_root.stderr)
        commands = ui_launch.session_commands(self.session, Path('/repo'), 'codex', '/bin/host', [])
        for command in commands[1:]:
            applied = self._run(command)
            self.assertEqual(applied.returncode, 0, applied.stderr + ' ' + str(command))
        mouse = self._run(['show-options', '-t', self.session, 'mouse'])
        self.assertEqual(mouse.returncode, 0, mouse.stderr)
        self.assertRegex(mouse.stdout.strip(), r'mouse\s+on')
        table = 'rig-' + self.session
        keys = self._run(['list-keys', '-T', table])
        self.assertEqual(keys.returncode, 0, keys.stderr)
        self.assertIn('WheelUpPane', keys.stdout)
        self.assertIn('copy-mode -e', keys.stdout)
        self.assertIn('scroll-up', keys.stdout)
        self.assertIn('WheelDownPane', keys.stdout)
        self.assertIn('F8', keys.stdout)
        self.assertIn('F9', keys.stdout)
        after_global = self._run(['show-options', '-g', 'mouse'])
        after_root = self._run(['list-keys', '-T', 'root'])
        after_copy = self._run(['list-keys', '-T', 'copy-mode'])
        after_copy_vi = self._run(['list-keys', '-T', 'copy-mode-vi'])
        self.assertEqual(after_global.stdout, before_global.stdout)
        self.assertEqual(after_root.stdout, before_root.stdout)
        self.assertEqual(after_copy.stdout, before_copy.stdout)
        self.assertEqual(after_copy_vi.stdout, before_copy_vi.stdout)
        self.assertNotIn(table, after_root.stdout)
        # History + copy-mode -e: scroll five up, then enough down proves autoexit.
        self._run(['set-option', '-t', self.session, 'history-limit', '1000'])
        for _ in range(40):
            self._run(['send-keys', '-t', self.session, 'line-' + str(_) , 'Enter'])
        entered = self._run(['copy-mode', '-e', '-t', self.session])
        self.assertEqual(entered.returncode, 0, entered.stderr)
        mode = self._run(['display-message', '-p', '-t', self.session, '#{pane_in_mode}'])
        self.assertEqual(mode.stdout.strip(), '1', mode.stdout + mode.stderr)
        self._run(['send-keys', '-X', '-t', self.session, '-N', '5', 'scroll-up'])
        self._run(['send-keys', '-X', '-t', self.session, '-N', '50', 'scroll-down'])
        exited = self._run(['display-message', '-p', '-t', self.session, '#{pane_in_mode}'])
        self.assertEqual(exited.stdout.strip(), '0', exited.stdout + exited.stderr)
        # Custom table cleanup unbind (no root/global edits).
        cleaned = self._run(['unbind-key', '-a', '-T', table])
        self.assertEqual(cleaned.returncode, 0, cleaned.stderr)
        after_unbind = self._run(['list-keys', '-T', table])
        self.assertNotIn('WheelUpPane', after_unbind.stdout)
        self.assertEqual(self._run(['list-keys', '-T', 'root']).stdout, before_root.stdout)
        self.assertEqual(self._run(['show-options', '-g', 'mouse']).stdout, before_global.stdout)
