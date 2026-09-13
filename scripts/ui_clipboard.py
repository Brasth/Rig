"""Copy tmux selections or the latest buffer to a local clipboard without a shell."""
from __future__ import annotations
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile

TIMEOUT_SEC = 5
STDERR_DIAG_LIMIT = 4096


def provider(env=None):
    env = os.environ if env is None else env
    if sys.platform == 'darwin':
        binary = shutil.which('pbcopy')
        return [binary] if binary else None
    if env.get('WAYLAND_DISPLAY'):
        binary = shutil.which('wl-copy')
        if binary:
            return [binary]
    if env.get('DISPLAY'):
        for argv in (['xclip', '-selection', 'clipboard'], ['xsel', '--clipboard', '--input']):
            binary = shutil.which(argv[0])
            if binary:
                return [binary, *argv[1:]]
    return None


def copy_command():
    return shlex.join([sys.executable, str(Path(__file__).resolve())])


def _read_stderr_diag(stderr_file):
    try:
        stderr_file.seek(0)
        return stderr_file.read(STDERR_DIAG_LIMIT).decode('utf-8', 'replace').strip()
    except OSError:
        return ''


def copy_bytes(data, command=None):
    command = list(command or provider() or [])
    if not command:
        print('Rig UI: no local clipboard helper (pbcopy, wl-copy, xclip, or xsel)', file=sys.stderr)
        return 1
    # Do not use PIPE for stderr: a forked descendant can keep the pipe open and
    # block subprocess.run past the parent exit. Temp file avoids EOF waits.
    # Do not capture provider stdout: xclip may fork and keep stdout open as clipboard owner.
    with tempfile.TemporaryFile(mode='w+b') as stderr_file:
        try:
            result = subprocess.run(
                command,
                input=data,
                stdout=subprocess.DEVNULL,
                stderr=stderr_file,
                timeout=TIMEOUT_SEC,
            )
        except subprocess.TimeoutExpired:
            print('Rig UI: clipboard helper timed out', file=sys.stderr)
            return 1
        except OSError as exc:
            print(f'Rig UI: clipboard helper failed: {exc}', file=sys.stderr)
            return 1
        if result.returncode:
            err = _read_stderr_diag(stderr_file)
            detail = f': {err}' if err else ''
            print(f'Rig UI: clipboard helper exited {result.returncode}{detail}', file=sys.stderr)
            return result.returncode
    return 0


def copy_tmux_buffer(tmux_argv):
    if not tmux_argv:
        print('Rig UI: missing tmux target for clipboard buffer', file=sys.stderr)
        return 1
    try:
        result = subprocess.run(
            [*tmux_argv, 'save-buffer', '-'],
            capture_output=True,
            timeout=TIMEOUT_SEC,
        )
    except subprocess.TimeoutExpired:
        print('Rig UI: tmux buffer read timed out', file=sys.stderr)
        return 1
    except OSError as exc:
        print(f'Rig UI: tmux buffer read failed: {exc}', file=sys.stderr)
        return 1
    if result.returncode:
        err = result.stderr.decode('utf-8', 'replace').strip() or 'no tmux buffer'
        print(f'Rig UI: {err}', file=sys.stderr)
        return result.returncode
    return copy_bytes(result.stdout)


def main(argv=None):
    argv = sys.argv if argv is None else argv
    args = list(argv[1:])
    if args[:1] == ['--buffer']:
        args = args[1:]
        if args[:1] == ['--']:
            args = args[1:]
        return copy_tmux_buffer(args)
    if args:
        print('Rig UI: clipboard helper does not accept extra arguments', file=sys.stderr)
        return 2
    return copy_bytes(sys.stdin.buffer.read())


if __name__ == '__main__':
    raise SystemExit(main())
