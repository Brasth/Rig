"""Conservative parent launch classification and session-local tmux UI."""
from __future__ import annotations
import os
import json
import time
import signal
import re
import shlex
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent


def classify(host, args, cwd=None, env=None, tty=None):
    env = os.environ if env is None else env
    if host not in {'codex', 'grok'} or env.get('RIG_UI') == '0' or env.get('RIG_UI_ACTIVE') or env.get('RIG_WORKER') or env.get('RIG_WORKER_ID') or env.get('RIG_JOB_ID') or env.get('RIG_LIVE') == '1':
        return None
    if not (sys.stdin.isatty() and sys.stdout.isatty() if tty is None else tty):
        return None
    cwd = Path(cwd or os.getcwd())
    launch_cwd = cwd
    # Unknown options/subcommands deliberately retain native host behavior.
    if host == 'grok' and any(arg in {'-p', '--single'} or arg.startswith('--single=') for arg in args): return None
    values = {'-m', '--model', '--reasoning-effort'}
    values |= {'-p', '--profile', '--sandbox', '--ask-for-approval', '-a', '--config', '-c'} if host == 'codex' else set()
    flags = {'--full-auto', '--no-alt-screen', '--dangerously-bypass-approvals-and-sandbox'} if host == 'codex' else {'--fullscreen', '--minimal', '--no-alt-screen', '--continue', '-c'}
    commands = {'exec', 'e', 'review', 'login', 'logout', 'mcp', 'mcp-server', 'app-server', 'completion', 'sandbox', 'debug', 'apply', 'resume', 'fork', 'cloud', 'features', 'help', 'update', 'version'}
    commands |= {'agents', 'plugin', 'remote-control', 'app', 'doctor', 'queue', 'archive', 'delete', 'migrate-rollouts', 'unarchive', 'exec-server', 'a'} if host == 'codex' else {'agent', 'completions', 'dashboard', 'doctor', 'du', 'disk-usage', 'export', 'inspect', 'leader', 'memory', 'models', 'plugin', 'sessions', 'setup', 'trace', 'v', 'worktree', 'wrap'}
    positional = False
    i = 0
    while i < len(args):
        arg = args[i]
        if arg in {'-C', '--cd', '--cwd'}:
            i += 1
            if i >= len(args): return None
            cwd = Path(args[i]).expanduser() if Path(args[i]).is_absolute() else launch_cwd / args[i]
        elif any(arg.startswith(x + '=') for x in ('--cd', '--cwd')):
            value = arg.split('=', 1)[1]
            cwd = Path(value).expanduser() if Path(value).is_absolute() else launch_cwd / value
        elif arg in values:
            i += 1
            if i >= len(args): return None
        elif arg in flags or any(arg.startswith(x + '=') for x in values if x.startswith('--')):
            pass
        elif not arg.startswith('-') and (positional or arg not in commands):
            positional = True
        else:
            return None
        i += 1
    try:
        root = subprocess.run(['git', '-C', str(cwd), 'rev-parse', '--show-toplevel'], capture_output=True, text=True, timeout=3)
        repo = Path(root.stdout.strip()).resolve()
        return repo if root.returncode == 0 and (repo / '.rig/harness.toml').is_file() else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def tmux_available(binary):
    try:
        result = subprocess.run([binary, '-V'], capture_output=True, text=True, timeout=3)
        match = re.search(r'tmux (\d+)\.(\d+)', result.stdout)
        return result.returncode == 0 and match and tuple(map(int, match.groups())) >= (3, 3)
    except (OSError, subprocess.TimeoutExpired):
        return False


def server_args(env=None):
    env = os.environ if env is None else env
    return [] if env.get('TMUX') else ['-L', 'rig-ui', '-f', '/dev/null']


def session_commands(session, repo, host, executable, args, launch_file=None):
    cli = shlex.join([sys.executable, str(HERE / 'rig_ui.py')])
    context = shlex.join(['--repo', str(repo), '--session', session])
    command = shlex.join(['env', 'RIG_UI_ACTIVE=1', 'RIG_UI_SESSION=' + session, 'RIG_UI_REPO=' + str(repo), executable, *args])
    if launch_file is not None:
        command = shlex.join([sys.executable, str(HERE / 'ui_launch.py'), '--child', str(launch_file)])
    table = 'rig-' + session
    manager = os.environ.get('RIG_UI_MANAGER_KEY', 'F8')
    add = os.environ.get('RIG_UI_ADD_KEY', 'F9')
    if not re.fullmatch(r'(?:[CMS]-)*(?:F(?:[1-9]|1[0-2])|[a-zA-Z0-9])', manager): manager = 'F8'
    if not re.fullmatch(r'(?:[CMS]-)*(?:F(?:[1-9]|1[0-2])|[a-zA-Z0-9])', add): add = 'F9'
    if manager == add: manager, add = 'F8', 'F9'
    return [
        ['new-session', '-d', '-s', session, '-c', os.getcwd(), command],
        ['set-option', '-t', session, 'status', 'on'],
        ['set-option', '-t', session, 'status-interval', '1'],
        ['set-option', '-t', session, 'status-left', ''],
        ['set-option', '-t', session, 'status-right', ''],
        ['set-option', '-t', session, 'status-format[0]', '#(' + cli + ' status ' + context + ' --width #{client_width} --manager-key ' + manager + ' --add-key ' + add + ')'],
        ['set-option', '-t', session, 'key-table', table],
        ['set-option', '-t', session, 'mouse', 'on'],
        ['bind-key', '-T', table, 'WheelUpPane', 'select-pane -t = ; copy-mode -e ; send-keys -X -N 5 scroll-up'],
        ['bind-key', '-T', table, 'WheelDownPane', 'select-pane -t ='],
        ['bind-key', '-T', table, manager, 'display-popup', '-E', '-w', '85%', '-h', '80%', cli + ' popup ' + context],
        ['bind-key', '-T', table, add, 'display-popup', '-E', '-w', '85%', '-h', '80%', cli + ' popup ' + context + ' --add'],
    ]


def main(argv):
    if len(argv) < 2: return 2
    original_env = os.environ.copy()
    host, executable, *args = argv
    if args[:1] == ['--']: args = args[1:]
    repo = classify(host, args)
    binary = shutil.which('tmux')
    if repo is None or not binary or not tmux_available(binary):
        if repo is None and sys.stdout.isatty() and any('worktree' in arg or 'remote' in arg for arg in args):
            print('Rig UI: dynamic workspace launch uses the native parent without a panel.', file=sys.stderr)
        if repo is not None:
            print('Rig UI unavailable (tmux 3.3+ required); opening parent normally.', file=sys.stderr)
        os.execvpe(executable, [executable, *args], original_env)
    from ui_service import ensure_service, request
    from ui_install import lifecycle_lock, register_lease, runtime_home
    session = 'rig-' + uuid.uuid4().hex[:12]
    base = [binary, *server_args()]
    started = False
    launch_file = None
    try:
        with lifecycle_lock():
            if not (runtime_home() / 'ui/shell-enabled').exists():
                os.execvpe(executable, [executable, *args], original_env)
            socket = ensure_service(repo)
            directory = runtime_home() / 'ui/launch'
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            launch_file = directory / (session + '.json')
            payload = {'env': original_env, 'executable': executable, 'args': args, 'repo': str(repo), 'session': session, 'endpoint': socket, 'tmux': base, 'home': str(runtime_home())}
            with os.fdopen(os.open(launch_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as stream:
                json.dump(payload, stream)
            commands = session_commands(session, repo, host, executable, args, launch_file)
            # After this succeeds, failure never starts a second parent.
            result = subprocess.run(base + commands[0], check=True)
            started = True
            if not os.environ.get('TMUX'):
                subprocess.run(base + ['set-option', '-s', 'escape-time', '25'], check=True)
            pane = subprocess.run(base + ['display-message', '-p', '-t', session, '#{pane_pid}'], capture_output=True, text=True, check=True)
            pid = int(pane.stdout.strip())
            from admission import process_identity
            identity = process_identity(pid)
            register_lease(session, repo, pid, start_id=identity.get('start_id'))
            for command in commands[1:]: subprocess.run(base + command, check=True)
            request(socket, {'op': 'register', 'session': session, 'host': host, 'pid': pid, 'start_id': identity.get('start_id'), 'tmux_session': session, 'socket_name': None if os.environ.get('TMUX') else 'rig-ui', 'server': os.environ.get('TMUX', 'rig-ui')})
    except Exception as exc:
        if not started:
            if launch_file is not None: launch_file.unlink(missing_ok=True)
            print(f'Rig UI unavailable: {exc}; opening parent normally.', file=sys.stderr)
            os.execvpe(executable, [executable, *args], original_env)
        print(f'Rig UI configuration incomplete: {exc}; attach with rig ui attach {session}', file=sys.stderr)
    finally:
        if launch_file is not None:
            if started: launch_file.with_suffix('.ready').touch(mode=0o600)
            else: launch_file.unlink(missing_ok=True)
    if os.environ.get('TMUX'):
        return subprocess.run(base + ['switch-client', '-t', session]).returncode
    result = subprocess.run(base + ['attach-session', '-t', session]).returncode
    exit_file = launch_file.with_suffix('.exit')
    if exit_file.exists():
        try: result = int(exit_file.read_text())
        finally: exit_file.unlink(missing_ok=True)
    return result


def sessions():
    binary = shutil.which('tmux')
    if not binary: return []
    result = subprocess.run([binary, *server_args(), 'list-sessions', '-F', '#{session_name}'], capture_output=True, text=True)
    return [name for name in result.stdout.splitlines() if re.fullmatch(r'rig-[a-f0-9]{12}', name)]


def attach(session):
    if not re.fullmatch(r'rig-[a-f0-9]{12}', session): return 2
    binary = shutil.which('tmux')
    if not binary: return 1
    return subprocess.run([binary, *server_args(), 'switch-client' if os.environ.get('TMUX') else 'attach-session', '-t', session]).returncode


def run_child(path):
    """Per-pane supervisor consumes private launch environment and cleans only its session."""
    from ui_install import lifecycle_lock, remove_lease
    from ui_service import request
    path = Path(path)
    payload = json.loads(path.read_text())
    path.unlink()
    ready = path.with_suffix('.ready')
    deadline = time.monotonic() + 10
    while not ready.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    if not ready.exists():
        try: request(payload['endpoint'], {'op': 'unregister', 'session': payload['session']})
        except Exception: pass
        subprocess.run(payload['tmux'] + ['unbind-key', '-a', '-T', 'rig-' + payload['session']], capture_output=True)
        with lifecycle_lock(payload['home']): remove_lease(payload['session'], payload['home'])
        return 1
    ready.unlink(missing_ok=True)
    env = payload['env']
    env.pop('RIG_LIFECYCLE_FD', None)
    env.update({'RIG_UI_ACTIVE': '1', 'RIG_UI_SESSION': payload['session'], 'RIG_UI_REPO': payload['repo']})
    for key in ('TMUX', 'TMUX_PANE', 'TERM'):
        if key in os.environ: env[key] = os.environ[key]
    def host_signals():
        signal.signal(signal.SIGINT, signal.SIG_DFL)
        signal.signal(signal.SIGQUIT, signal.SIG_DFL)
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    signal.signal(signal.SIGQUIT, signal.SIG_IGN)
    try:
        code = subprocess.call([payload['executable'], *payload['args']], env=env, preexec_fn=host_signals)
        path.with_suffix('.exit').write_text(str(code if code >= 0 else 128 - code))
        return code
    finally:
        try: request(payload['endpoint'], {'op': 'unregister', 'session': payload['session']})
        except Exception: pass
        subprocess.run(payload['tmux'] + ['unbind-key', '-a', '-T', 'rig-' + payload['session']], capture_output=True)
        with lifecycle_lock(payload['home']): remove_lease(payload['session'], payload['home'])


if __name__ == '__main__':
    raise SystemExit(run_child(sys.argv[2]) if sys.argv[1:2] == ['--child'] else main(sys.argv[1:]))
