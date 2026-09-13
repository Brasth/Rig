import io
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import ui_clipboard


def _sink_command(path):
    script = 'import sys; open(sys.argv[1], "wb").write(sys.stdin.buffer.read())'
    return [sys.executable, '-c', script, str(path)]


def _assert_stderr_file(test, kwargs):
    stderr = kwargs.get('stderr')
    test.assertIsNotNone(stderr)
    test.assertNotEqual(stderr, subprocess.PIPE)
    test.assertTrue(hasattr(stderr, 'seek') and hasattr(stderr, 'read'))
    test.assertIs(kwargs['stdout'], subprocess.DEVNULL)


def _run_writing_stderr(stderr_bytes, returncode=0):
    def run(argv, **kwargs):
        stderr = kwargs.get('stderr')
        if stderr is not None and stderr is not subprocess.PIPE and hasattr(stderr, 'write'):
            stderr.write(stderr_bytes)
            stderr.flush()
        return subprocess.CompletedProcess(args=argv, returncode=returncode)
    return run


class ClipboardHelperTests(unittest.TestCase):
    def test_copy_command_is_helper_argv_without_user_text(self):
        command = ui_clipboard.copy_command()
        self.assertIn('ui_clipboard.py', command)
        self.assertNotIn(';', command)
        self.assertFalse(command.startswith('sh '))

    def test_unicode_goes_through_stdin_to_sink(self):
        data = 'café 你好 🎯'.encode()
        with tempfile.TemporaryDirectory() as directory:
            sink = Path(directory) / 'clip'
            self.assertEqual(ui_clipboard.copy_bytes(data, command=_sink_command(sink)), 0)
            self.assertEqual(sink.read_bytes(), data)

    def test_missing_provider_is_honest_and_skips_subprocess(self):
        with patch('ui_clipboard.provider', return_value=None), patch('ui_clipboard.subprocess.run') as run:
            self.assertEqual(ui_clipboard.copy_bytes(b'secret'), 1)
            run.assert_not_called()

    def test_provider_errors_do_not_use_a_shell(self):
        with patch('ui_clipboard.subprocess.run', side_effect=OSError('missing')) as run:
            self.assertEqual(ui_clipboard.copy_bytes(b'hi', command=['pbcopy']), 1)
            self.assertFalse(run.call_args.kwargs.get('shell'))
        with patch('ui_clipboard.subprocess.run', side_effect=_run_writing_stderr(b'denied', returncode=3)) as run:
            with patch('ui_clipboard.sys.stderr', new_callable=io.StringIO) as err:
                self.assertEqual(ui_clipboard.copy_bytes(b'hi', command=['pbcopy']), 3)
                self.assertIn('denied', err.getvalue())
            self.assertEqual(run.call_args.args[0], ['pbcopy'])
            self.assertEqual(run.call_args.kwargs['input'], b'hi')
            _assert_stderr_file(self, run.call_args.kwargs)
            self.assertEqual(run.call_args.kwargs['timeout'], ui_clipboard.TIMEOUT_SEC)
            self.assertFalse(run.call_args.kwargs.get('shell'))

    def test_provider_timeout_returns_one_without_raising(self):
        with patch('ui_clipboard.subprocess.run', side_effect=subprocess.TimeoutExpired(cmd=['pbcopy'], timeout=5)) as run:
            self.assertEqual(ui_clipboard.copy_bytes(b'hi', command=['pbcopy']), 1)
            self.assertEqual(run.call_args.kwargs['timeout'], ui_clipboard.TIMEOUT_SEC)
            _assert_stderr_file(self, run.call_args.kwargs)

    def test_xclip_provider_does_not_capture_stdout(self):
        with patch('ui_clipboard.subprocess.run', side_effect=_run_writing_stderr(b'', returncode=0)) as run:
            self.assertEqual(ui_clipboard.copy_bytes(b'sel', command=['xclip', '-selection', 'clipboard']), 0)
            _assert_stderr_file(self, run.call_args.kwargs)
            self.assertEqual(run.call_args.kwargs['timeout'], ui_clipboard.TIMEOUT_SEC)
            self.assertNotIn('capture_output', run.call_args.kwargs)

    def test_providers(self):
        with patch('ui_clipboard.sys.platform', 'darwin'), patch('ui_clipboard.shutil.which', return_value='/usr/bin/pbcopy'):
            self.assertEqual(ui_clipboard.provider({}), ['/usr/bin/pbcopy'])
        with patch('ui_clipboard.sys.platform', 'darwin'), patch('ui_clipboard.shutil.which', return_value=None):
            self.assertIsNone(ui_clipboard.provider({'DISPLAY': ':0'}))
        def wayland(name):
            return '/usr/bin/wl-copy' if name == 'wl-copy' else None
        with patch('ui_clipboard.sys.platform', 'linux'), patch('ui_clipboard.shutil.which', side_effect=wayland):
            self.assertEqual(ui_clipboard.provider({'WAYLAND_DISPLAY': 'wayland-0', 'DISPLAY': ':0'}), ['/usr/bin/wl-copy'])
        def xclip(name):
            return '/usr/bin/xclip' if name == 'xclip' else None
        with patch('ui_clipboard.sys.platform', 'linux'), patch('ui_clipboard.shutil.which', side_effect=xclip):
            self.assertEqual(ui_clipboard.provider({'DISPLAY': ':0'}), ['/usr/bin/xclip', '-selection', 'clipboard'])
        def xsel(name):
            return '/usr/bin/xsel' if name == 'xsel' else None
        with patch('ui_clipboard.sys.platform', 'linux'), patch('ui_clipboard.shutil.which', side_effect=xsel):
            self.assertEqual(ui_clipboard.provider({'DISPLAY': ':0'}), ['/usr/bin/xsel', '--clipboard', '--input'])
        with patch('ui_clipboard.sys.platform', 'linux'), patch('ui_clipboard.shutil.which', return_value=None):
            self.assertIsNone(ui_clipboard.provider({'DISPLAY': ':0', 'WAYLAND_DISPLAY': 'wayland-0'}))

    def test_buffer_copy_uses_exact_tmux_argv_and_stdin(self):
        payload = 'café 你好'.encode()
        calls = []
        def run(argv, **kwargs):
            calls.append((list(argv), kwargs))
            if argv[-2:] == ['save-buffer', '-']:
                return subprocess.CompletedProcess(args=argv, returncode=0, stdout=payload, stderr=b'')
            stderr = kwargs.get('stderr')
            if stderr is not None and stderr is not subprocess.PIPE and hasattr(stderr, 'write'):
                stderr.write(b'')
                stderr.flush()
            return subprocess.CompletedProcess(args=argv, returncode=0)
        with patch('ui_clipboard.subprocess.run', side_effect=run), patch('ui_clipboard.provider', return_value=['pbcopy']):
            self.assertEqual(ui_clipboard.copy_tmux_buffer(['tmux', '-L', 'rig-ui']), 0)
        self.assertEqual(calls[0][0], ['tmux', '-L', 'rig-ui', 'save-buffer', '-'])
        self.assertTrue(calls[0][1].get('capture_output'))
        self.assertEqual(calls[0][1].get('timeout'), ui_clipboard.TIMEOUT_SEC)
        self.assertFalse(calls[0][1].get('shell'))
        self.assertEqual(calls[1][0], ['pbcopy'])
        self.assertEqual(calls[1][1]['input'], payload)
        _assert_stderr_file(self, calls[1][1])
        self.assertEqual(calls[1][1]['timeout'], ui_clipboard.TIMEOUT_SEC)
        self.assertFalse(calls[1][1].get('shell'))
        self.assertTrue(all('delete-buffer' not in ' '.join(argv) for argv, _ in calls))

    def test_buffer_errors_retain_tmux_buffer(self):
        failed = subprocess.CompletedProcess(args=[], returncode=1, stdout=b'', stderr=b'no buffers')
        with patch('ui_clipboard.subprocess.run', return_value=failed) as run, patch('ui_clipboard.provider', return_value=['pbcopy']):
            self.assertEqual(ui_clipboard.copy_tmux_buffer(['tmux', '-L', 'rig-ui']), 1)
            self.assertEqual(run.call_count, 1)
            self.assertEqual(run.call_args.args[0], ['tmux', '-L', 'rig-ui', 'save-buffer', '-'])
            self.assertEqual(run.call_args.kwargs['timeout'], ui_clipboard.TIMEOUT_SEC)
        with patch('ui_clipboard.subprocess.run') as run:
            self.assertEqual(ui_clipboard.copy_tmux_buffer([]), 1)
            run.assert_not_called()

    def test_buffer_timeout_retains_tmux_buffer(self):
        with patch('ui_clipboard.subprocess.run', side_effect=subprocess.TimeoutExpired(cmd=['tmux'], timeout=5)) as run, patch('ui_clipboard.provider', return_value=['pbcopy']):
            self.assertEqual(ui_clipboard.copy_tmux_buffer(['tmux', '-L', 'rig-ui']), 1)
            self.assertEqual(run.call_count, 1)
            self.assertEqual(run.call_args.args[0], ['tmux', '-L', 'rig-ui', 'save-buffer', '-'])
            self.assertTrue(run.call_args.kwargs.get('capture_output'))
            self.assertEqual(run.call_args.kwargs['timeout'], ui_clipboard.TIMEOUT_SEC)

    def test_main_stdin_and_buffer_modes(self):
        data = 'hello 你好'.encode()
        with tempfile.TemporaryDirectory() as directory:
            sink = Path(directory) / 'clip'
            command = _sink_command(sink)
            with patch('ui_clipboard.provider', return_value=command), patch('ui_clipboard.sys.stdin') as stdin:
                stdin.buffer.read.return_value = data
                self.assertEqual(ui_clipboard.main(['ui_clipboard.py']), 0)
            self.assertEqual(sink.read_bytes(), data)
        with patch('ui_clipboard.copy_tmux_buffer', return_value=0) as copy_buffer:
            self.assertEqual(ui_clipboard.main(['ui_clipboard.py', '--buffer', '--', 'tmux', '-L', 'rig-ui']), 0)
            copy_buffer.assert_called_once_with(['tmux', '-L', 'rig-ui'])
        self.assertEqual(ui_clipboard.main(['ui_clipboard.py', 'pbcopy']), 2)

    def test_forked_descendant_holding_stderr_returns_promptly(self):
        # Parent exits immediately; child sleeps 1s while inheriting fds.
        # Prior PIPE stderr waited on EOF and hit the short timeout.
        # Temp-file stderr returns as soon as the provider parent exits.
        command = [
            sys.executable,
            '-c',
            "import os, time; child = os.fork(); os._exit(0) if child else None; time.sleep(1); os._exit(0)",
        ]
        with patch('ui_clipboard.TIMEOUT_SEC', 0.2):
            started = time.monotonic()
            code = ui_clipboard.copy_bytes(b'fixture', command=command)
            elapsed = time.monotonic() - started
        self.assertEqual(code, 0)
        self.assertLess(elapsed, 0.5)


if __name__ == '__main__':
    unittest.main()
