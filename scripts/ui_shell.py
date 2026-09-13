"""Opt-in interactive shell integration; never installs host executables."""
from __future__ import annotations
import argparse
import os
from pathlib import Path
import shlex
import shutil
from ui_install import lifecycle_lock, Manifest

START = '# >>> Rig shell UI >>>'
END = '# <<< Rig shell UI <<<'

def home(value=None):
    return Path(value or os.environ.get('RIG_HOME', Path.home() / '.rig')).absolute()

def shell_rc_paths(shell, rc_file=None):
    if rc_file:
        return [Path(rc_file).expanduser().absolute()]
    if shell == 'zsh':
        return [Path(os.environ.get('ZDOTDIR') or Path.home()).expanduser() / '.zshrc']
    # Non-login shells read bashrc; login shells read the first existing profile.
    profiles = [Path.home() / name for name in ('.bash_profile', '.bash_login', '.profile')]
    login = next((path for path in profiles if path.exists()), profiles[0])
    return [Path.home() / '.bashrc', login]


def require_tmux():
    from ui_launch import tmux_available
    binary = shutil.which('tmux')
    if not binary or not tmux_available(binary):
        raise ValueError('shell UI requires tmux 3.3 or newer; install or upgrade tmux, then retry')


def enable(rig_home=None, shell=None, rc_file=None):
    root = home(rig_home)
    shell = Path(shell or os.environ.get('SHELL', 'bash')).name
    if shell not in ('bash', 'zsh'):
        raise ValueError('shell UI supports bash and zsh; specify --shell')
    require_tmux()
    paths = shell_rc_paths(shell, rc_file)
    # Journal and edit symlink targets, preserving dotfile manager symlinks.
    targets = list(dict.fromkeys(path.resolve() for path in paths))
    source = root / 'scripts/rig-shell.sh'
    marker = root / 'ui/shell-enabled'
    with lifecycle_lock(root):
        pending = {}
        for rc in targets:
            text = rc.read_text() if rc.exists() else ''
            if START in text or END in text:
                if text.count(START) != 1 or text.count(END) != 1 or text.index(START) > text.index(END):
                    raise ValueError('ambiguous Rig shell block; preserve RC and repair manually')
                begin, tail = text.split(START, 1)
                _, after = tail.split(END, 1)
                text = begin + after.lstrip('\n')
            block = f'{START}\nif [ -r {shlex.quote(str(source))} ]; then\n  RIG_SHELL_HOME={shlex.quote(str(root))}\n  . {shlex.quote(str(source))}\nfi\n{END}\n'
            pending[rc] = text + ('\n' if text and not text.endswith('\n') else '') + block
        manifest = Manifest(root)
        manifest.before([*targets, marker])
        for rc, text in pending.items():
            rc.parent.mkdir(parents=True, exist_ok=True)
            rc.write_text(text)
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text('enabled\n')
        manifest.after([*targets, marker])
    return paths[0]

def disable(rig_home=None):
    root = home(rig_home)
    with lifecycle_lock(root):
        (root / 'ui/shell-enabled').unlink(missing_ok=True)
    return root

def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['enable', 'disable'])
    parser.add_argument('--shell', choices=['bash', 'zsh'])
    parser.add_argument('--rc-file')
    args = parser.parse_args(argv)
    if args.action == 'disable':
        disable()
        print('Shell UI disabled; loaded functions now bypass Rig.')
    else:
        try:
            rc = enable(shell=args.shell, rc_file=args.rc_file)
        except ValueError as exc:
            print(str(exc), file=__import__('sys').stderr)
            return 2
        print(f'Shell UI enabled. Open a new shell or source {shlex.quote(str(rc))}. Existing aliases/functions are preserved.')
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
