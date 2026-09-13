"""Ownership journal and conservative, reversible installation lifecycle."""
from __future__ import annotations
import argparse
import base64
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import time


def runtime_home(value=None):
    return Path(value or os.environ.get('RIG_HOME', Path.home() / '.rig')).absolute()

@contextmanager
def lifecycle_lock(rig_home=None):
    root = runtime_home(rig_home)
    root.mkdir(parents=True, exist_ok=True)
    # A child installer inherits this descriptor while the controller holds it.
    inherited = os.environ.get('RIG_LIFECYCLE_FD')
    if inherited:
        try:
            if os.fstat(int(inherited)).st_ino == (root / '.lifecycle.lock').stat().st_ino:
                yield
                return
        except (OSError, ValueError):
            pass
    with (root / '.lifecycle.lock').open('a+') as handle:
        deadline = time.monotonic() + 5
        while True:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise TimeoutError('Rig lifecycle busy; retry after installation or UI startup finishes')
                time.sleep(0.025)
        old = os.environ.get('RIG_LIFECYCLE_FD')
        os.environ['RIG_LIFECYCLE_FD'] = str(handle.fileno())
        try:
            yield
        finally:
            if old is None:
                os.environ.pop('RIG_LIFECYCLE_FD', None)
            else:
                os.environ['RIG_LIFECYCLE_FD'] = old
            fcntl.flock(handle, fcntl.LOCK_UN)


def register_lease(session, repo, pid, start_id=None, rig_home=None):
    """Caller may hold lifecycle_lock; registration itself is atomic."""
    import hashlib
    root = runtime_home(rig_home)
    directory = root / 'ui/leases'
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (hashlib.sha256(str(session).encode()).hexdigest() + '.json')
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps({'session': str(session), 'repo': str(repo), 'pid': pid, 'start_id': start_id}))
    tmp.replace(path)
    return path

def remove_lease(session, rig_home=None):
    import hashlib
    path = runtime_home(rig_home) / 'ui/leases' / (hashlib.sha256(str(session).encode()).hexdigest() + '.json')
    path.unlink(missing_ok=True)


def snapshot(path):
    if path.is_symlink():
        return {'kind': 'link', 'target': os.readlink(path)}
    if path.is_file():
        return {'kind': 'file', 'data': base64.b64encode(path.read_bytes()).decode(), 'mode': path.stat().st_mode & 0o777}
    return {'kind': 'directory' if path.exists() else 'missing'}

class Manifest:
    def __init__(self, root):
        self.path = runtime_home(root) / 'install-manifest.json'
        self.data = json.loads(self.path.read_text()) if self.path.exists() else {'version': 1, 'runtime_owned': not (runtime_home(root) / 'scripts').exists(), 'entries': {}}
        if self.data.get('version') != 1:
            raise ValueError('unsupported installation manifest')
    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix('.tmp')
        tmp.write_text(json.dumps(self.data, indent=2) + '\n')
        tmp.chmod(0o600)
        tmp.replace(self.path)
    def before(self, paths):
        for path in paths:
            key = str(path.absolute())
            self.data['entries'].setdefault(key, {'before': snapshot(path)})
        self.save()
    def after(self, paths):
        for path in paths:
            entry = self.data['entries'][str(path.absolute())]
            entry['after'] = snapshot(path)
        self.save()


def install_paths(root, source, repo=None):
    """Enumerate destinations before the legacy installer can create them."""
    h = Path.home()
    paths = {h / '.local/bin/rig'}
    for folder in ('bin', 'scripts', 'skills', 'adapters', 'templates'):
        for item in (source / folder).rglob('*'):
            if item.is_file():
                paths.add(root / item.relative_to(source))
    skills = ('delegate-harness', 'rig-jobs', 'rig-queue')
    for directory in ('.agents/skills', '.grok/skills', '.codex/skills', '.config/opencode/skill', '.omp/agent/skills', '.pi/agent/skills', '.gemini/antigravity-cli/skills'):
        paths.update(h / directory / skill for skill in skills)
    fixed = ['.codex/config.toml', '.codex/hooks.json', '.codex/prompts/queue.md', '.grok/config.toml', '.grok/rig-statusline.sh', '.grok/hooks/rig-queue-submit.json', '.agents/plugins/marketplace.json', '.config/opencode/commands/queue.md', '.config/opencode/plugins/rig-queue.js', '.config/opencode/plugin/rig-queue.js', '.config/opencode/tui-plugins/rig-hud.tsx', '.config/opencode/tui-plugins/rig-hud.js', '.config/opencode/tui.json', '.omp/agent/extensions/rig-queue.js', '.pi/agent/extensions/rig-queue.js', '.gemini/config/hooks.json', '.gemini/antigravity-cli/settings.json']
    paths.update(h / name for name in fixed)
    paths.update(h / '.codex/agents' / (name + '.toml') for name in ('explorer', 'worker', 'bulk', 'reviewer'))
    for item in (source / 'adapters/codex/plugin/rig-queue').rglob('*'):
        if item.is_file():
            paths.add(h / '.agents/plugins/rig-queue' / item.relative_to(source / 'adapters/codex/plugin/rig-queue'))
    paths.add(Path(os.environ.get('GROK_HOME', h / '.grok')) / 'config.toml')
    paths.add(Path(os.environ.get('GROK_HOME', h / '.grok')) / 'rig-statusline.sh')
    for env, default in [('OPENCODE_CONFIG', h / '.config/opencode/opencode.json'), ('OMP_MCP', h / '.omp/mcp.json'), ('AGY_MCP', h / '.gemini/config/mcp_config.json')]:
        paths.add(Path(os.environ.get(env) or default))
    pi = Path(os.environ.get('PI_CODING_AGENT_DIR') or os.environ.get('PI_AGENT_DIR') or h / '.pi/agent')
    paths.add(pi / 'mcp.json')
    if repo:
        paths = {repo / 'AGENTS.md', repo / 'CLAUDE.md', repo / '.gitignore'}
        paths.update(repo / '.agents/skills' / skill / 'SKILL.md' for skill in skills)
    return paths


def transaction(command, root, source, repo=None):
    with lifecycle_lock(root):
        paths = install_paths(root, source, repo)
        manifest = Manifest(root)
        manifest.before(paths)
        if repo:
            manifest.data['repos'] = sorted(set(manifest.data.get('repos', [])) | {str(repo.absolute())})
            for path in paths:
                manifest.data['entries'][str(path.absolute())]['repo'] = str(repo.absolute())
            manifest.save()
        env = os.environ.copy()
        env['RIG_INSTALL_TRANSACTION'] = '1'
        fd = int(env['RIG_LIFECYCLE_FD'])
        try:
            return subprocess.call(command, env=env, pass_fds=(fd,))
        finally:
            Manifest(root).after(paths)


def restore(path, state):
    if path.is_symlink() or path.is_file():
        path.unlink()
    if state['kind'] == 'missing':
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    if state['kind'] == 'link':
        path.symlink_to(state['target'])
    elif state['kind'] == 'file':
        path.write_bytes(base64.b64decode(state['data']))
        path.chmod(state['mode'])


def runtime_blocker(root, entries, ignored=(), repos=()):
    """Fail closed on process inspection errors and changed external references."""
    for repo in repos:
        try:
            if not Path(repo).is_dir():
                return 'registered repository unavailable'
            for path in (Path(repo) / '.rig/reservations').glob('*.json'):
                record = json.loads(path.read_text())
                if not isinstance(record, dict) or record.get('stage') != 'released' or record.get('slot_held') or record.get('needs_reconciliation'):
                    return 'registered repository has active or unreconciled reservations'
        except (OSError, ValueError):
            return 'registered repository reservation state uncertain'
    for name, entry in entries.items():
        path = Path(name)
        if path.is_relative_to(root) and '/ui/' not in name and entry.get('after') != snapshot(path):
            return 'modified runtime files'
    for lease in (root / 'ui/leases').glob('*.json'):
        try:
            pid = int(json.loads(lease.read_text())['pid'])
            os.kill(pid, 0)
            return 'active runtime lease'
        except ProcessLookupError:
            pass
        except (OSError, ValueError, KeyError):
            return 'uncertain runtime lease'
    try:
        output = subprocess.check_output(['ps', '-axo', 'pid=,ppid=,args='], text=True)
        rows = [line.strip().split(None, 2) for line in output.splitlines()]
        parents = {int(row[0]): int(row[1]) for row in rows if len(row) >= 2}
        own = {os.getpid()}
        current = os.getpid()
        while parents.get(current) and parents[current] not in own:
            current = parents[current]
            own.add(current)
        for row in rows:
            if len(row) == 3 and int(row[0]) not in own and str(root) in row[2]:
                return 'active process references runtime'
    except (OSError, ValueError, subprocess.SubprocessError):
        return 'process inspection unavailable'
    for name, entry in entries.items():
        if name in ignored:
            continue
        path = Path(name)
        if entry.get('repo') and snapshot(path) != entry['before']:
            return 'modified repository integration retained'
        if path.is_relative_to(root):
            continue
        if path.is_symlink() and str(root) in os.readlink(path):
            return 'remaining symlink references runtime'
        if path.is_file():
            try:
                if str(root).encode() in path.read_bytes():
                    return 'remaining user configuration references runtime'
            except OSError:
                return 'configuration references uncertain'
    return None


def remove_legacy_repo_blocks(repos, entries, dry_run):
    for repo in repos:
        for name in ('AGENTS.md', 'CLAUDE.md'):
            path = repo / name
            if str(path) in entries or not path.is_file():
                continue
            text = path.read_text()
            start, end = '<!-- rig:start -->', '<!-- rig:end -->'
            if text.count(start) != 1 or text.count(end) != 1 or text.index(start) > text.index(end):
                continue
            before, tail = text.split(start, 1)
            _, after = tail.split(end, 1)
            print(f'{"would remove" if dry_run else "remove"} legacy Rig block: {path}')
            if not dry_run:
                path.write_text(before + after.lstrip('\n'))


def uninstall(root=None, dry_run=False, repos=()):
    root = runtime_home(root)
    with lifecycle_lock(root):
        manifest = Manifest(root)
        entries = manifest.data['entries']
        restored = set()
        registered_repos = set(manifest.data.get('repos', [])) | {entry['repo'] for entry in entries.values() if entry.get('repo')}
        manifest.data['repos'] = sorted(registered_repos)
        selected_repos = [Path(p).absolute() for p in repos]
        remove_legacy_repo_blocks(selected_repos, entries, dry_run)
        if not dry_run:
            (root / 'ui/shell-enabled').unlink(missing_ok=True)
        # Old MCP sessions/workers do not register leases. Their absence cannot
        # prove this runtime unused, so preserve runtime files and report it.
        # Runtime cleanup follows restoration of external integrations.
        if not entries:
            print('legacy install: no ownership manifest; preserve settings and runtime')
            return 0
        for name, entry in list(entries.items()):
            path = Path(name)
            if path.is_relative_to(root):
                continue
            if 'after' not in entry or snapshot(path) != entry['after']:
                # A user's subsequent RC edits do not prevent removing our exact block.
                after = entry.get('after', {})
                if after.get('kind') == 'file' and path.is_file():
                    installed = base64.b64decode(after['data']).decode(errors='replace')
                    if entry.get('repo'):
                        start, end = '<!-- rig:start -->', '<!-- rig:end -->'
                    else:
                        start, end = '# >>> Rig shell UI >>>', '# <<< Rig shell UI <<<'
                    if installed.count(start) == 1 and installed.count(end) == 1:
                        block = start + installed.split(start, 1)[1].split(end, 1)[0] + end
                        current = path.read_text()
                        if current.count(block) == 1:
                            print(f'{"would remove" if dry_run else "remove"} Rig block: {path}')
                            if not dry_run:
                                path.write_text(current.replace(block + '\n', '').replace(block, ''))
                                del entries[name]
                            continue
                print(f'preserve modified or uncertain: {path}')
                continue
            if entry['before'] == entry['after']:
                continue
            restored.add(name)
            print(f'{"would restore" if dry_run else "restore"}: {path}')
            if not dry_run:
                restore(path, entry['before'])
                del entries[name]
        blocker = None if manifest.data.get('runtime_owned') else 'legacy runtime ownership uncertain'
        blocker = blocker or runtime_blocker(root, entries, restored if dry_run else (), registered_repos)
        if blocker:
            print(f'preserve runtime: {blocker}')
        else:
            for name, entry in list(entries.items()):
                path = Path(name)
                if not path.is_relative_to(root) or entry.get('after') != snapshot(path):
                    continue
                if entry['before'] == entry['after']:
                    continue
                print(f'{"would restore" if dry_run else "restore"} runtime: {path}')
                if not dry_run:
                    restore(path, entry['before'])
                    del entries[name]
        if not dry_run:
            manifest.save()
    return 0


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == 'transaction':
        _, root, source, repo, *command = argv
        return transaction(command, Path(root), Path(source), Path(repo) if repo != '-' else None)
    parser = argparse.ArgumentParser(description='Remove owned integrations; preserve runtime and project data when usage is uncertain.')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--repo', action='append', default=[])
    args = parser.parse_args(argv)
    return uninstall(dry_run=args.dry_run, repos=args.repo)

if __name__ == '__main__':
    raise SystemExit(main())
