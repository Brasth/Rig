#!/usr/bin/env python3
"""Explicit legacy bootstrap into a fresh, separate, pinned runtime root.

The legacy root is never modified, adopted or given fabricated provenance. A
new empty root is set up only by the pinned checkout's own setup, after a
concrete preview and explicit consent, and its baseline is verified before
success. Backups are private and inventory-verified. An interrupted migration
keeps them and names an executable restore command; nothing claims a rollback.
"""
from __future__ import annotations

from contextlib import ExitStack
import datetime
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import stat
import subprocess
import tempfile
import uuid

import harness
import optional_skills
import runtime_update
import update_gate
from runtime_update import SHA, UpdateError
from ui_install import active_runtime_blocker, install_paths, lifecycle_lock, snapshot

# Written by setup's integration installer but absent from install_paths().
EXTRA_INTEGRATIONS = ('.cursor/mcp.json',)
PROJECT_GUIDANCE = ('AGENTS.md', 'CLAUDE.md', '.gitignore', '.rig/harness.toml')
# The child setup must see only the new root and the pinned checkout.
DROP_ENV = ('RIG_INSTALL_TRANSACTION', 'RIG_LIFECYCLE_FD', 'RIG_LIFECYCLE_OWNER_PID',
            'RIG_SRC', 'RIG_HOME', 'RIG_LIVE', 'RIG_JOB_ID', 'RIG_JOB_DIR')
# Skip optional installers without touching remembered consent (no --no-* flags).
SKIP_ENV = {'RIG_SKIP_CUA_DRIVER': '1', 'RIG_SKIP_BROWSER_SKILL': '1',
            'RIG_SKIP_TMUX_INSTALL': '1', 'RIG_SKIP_UPDATE_CHECK': '1',
            'PYTHONDONTWRITEBYTECODE': '1'}
FINISHED = ('complete', 'restored')
LOCKS = ('.update.lock', '.lifecycle.lock')


class MigrationError(UpdateError):
    pass


def cli_path():
    """The entry point running this code; an old installed CLI lacks the flags."""
    return Path(__file__).resolve().parents[1] / 'bin' / 'rig'


def default_new_root():
    return Path.home() / '.rig-versioned'


def migrations_home():
    return Path.home() / '.rig-migrations'


def restore_command(directory):
    return shlex.join([str(cli_path()), 'update', '--restore-migration', str(directory)])


def write_private(path, data):
    temp = path.with_name('.' + path.name + '.' + uuid.uuid4().hex)
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def write_json(path, value):
    write_private(path, (json.dumps(value, indent=2, sort_keys=True) + '\n').encode())


def read_private_json(path):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise MigrationError('Unsafe migration metadata ownership or permissions: ' + str(path))
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise MigrationError('Invalid migration metadata: ' + str(path))
    return value


def file_hash(path):
    digest = hashlib.sha256()
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b''):
            digest.update(chunk)
    return digest.hexdigest()


def describe(path, digest=True):
    """lstat-only state; a link is recorded as a link and never followed."""
    try:
        info = path.lstat()
    except (FileNotFoundError, NotADirectoryError):
        return {'kind': 'missing'}
    mode = stat.S_IMODE(info.st_mode)
    if stat.S_ISLNK(info.st_mode):
        return {'kind': 'link', 'target': os.readlink(path)}
    if stat.S_ISDIR(info.st_mode):
        return {'kind': 'dir', 'mode': mode}
    if stat.S_ISREG(info.st_mode):
        state = {'kind': 'file', 'mode': mode, 'size': info.st_size}
        if digest:
            state['sha256'] = file_hash(path)
        return state
    return {'kind': 'special'}


def same(current, recorded):
    keys = {'missing': (), 'special': (), 'dir': (), 'link': ('target',), 'file': ('sha256', 'mode')}
    kind = recorded['kind']
    return current['kind'] == kind and all(current.get(k) == recorded.get(k) for k in keys.get(kind, ()))


def walk(path):
    yield path
    if path.is_dir() and not path.is_symlink():
        for child in sorted(path.iterdir()):
            yield from walk(child)


def under(path, root):
    return path == root or path.is_relative_to(root)


def legacy_repos(old_root):
    path = old_root / 'install-manifest.json'
    if not path.exists() and not path.is_symlink():
        return set()
    try:
        data = json.loads(path.read_text())
        entries = data.get('entries', {})
        repos = set(data.get('repos', []))
        repos.update(e['repo'] for e in entries.values() if isinstance(e, dict) and e.get('repo'))
    except (OSError, ValueError, AttributeError, TypeError) as error:
        raise MigrationError('Legacy installation manifest is unreadable; ownership uncertain: ' + str(error))
    if any(not isinstance(p, str) or not Path(p).is_absolute() for p in repos):
        raise MigrationError('Legacy installation manifest has an invalid registered repository')
    return repos


def check_project(project):
    if not (project / '.rig/harness.toml').is_file():
        raise MigrationError(f'--project must be an existing initialized Rig project: {project}')
    state = harness.parse_harness(project / '.rig/harness.toml')['project']
    if not state['enabled']:
        raise MigrationError(f'Project is disabled; it is not initialized by migration: {project}')


def unfinished(new_root):
    for path in sorted(migrations_home().glob('*/migration.json'), reverse=True):
        try:
            journal = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if isinstance(journal, dict) and journal.get('new_root') == str(new_root) and journal.get('phase') not in FINISHED:
            return path.parent
    return None


def preflight(old_root, new_root, project):
    """Read-only refusal checks; runs again under the old root's locks."""
    if not old_root.is_dir() or old_root.is_symlink():
        raise MigrationError(f'No legacy Rig runtime directory at {old_root}')
    if update_gate.pending(old_root):
        raise MigrationError('Pending runtime update: run rig update --status, then rig update --recover')
    if (old_root / 'runtime-state.json').exists():
        raise MigrationError(f'{old_root} already has a safe-update baseline; use rig update --latest')
    if new_root.exists() or new_root.is_symlink():
        previous = unfinished(new_root)
        hint = (f'; interrupted migration found, restore with: {restore_command(previous)}' if previous
                else '; choose another --new-root or inspect it (it is never overwritten)')
        raise MigrationError(f'Destination conflict: {new_root} already exists{hint}')
    if not new_root.parent.is_dir():
        raise MigrationError(f'Parent of new root does not exist: {new_root.parent}')
    update_gate.safe_parents(new_root)
    if under(new_root, old_root) or under(old_root, new_root):
        raise MigrationError('New root must be separate from the legacy root')
    repos = legacy_repos(old_root)
    if project:
        check_project(project)
        repos.add(str(project))
    repos = sorted(repos)
    runtime_update.quiet(old_root, repos)
    return repos


def pinned_checkout(revision, dest):
    """Clean detached Git checkout of one official full commit; nothing executed."""
    if not SHA.fullmatch(revision or ''):
        raise MigrationError('Migration requires a full 40-character commit SHA')
    git = runtime_update.git
    git('init', '-q', str(dest))
    git('-C', str(dest), 'fetch', '-q', '--depth=1', '--no-tags', runtime_update.OFFICIAL_REPO, revision)
    if git('-C', str(dest), 'rev-parse', 'FETCH_HEAD^{commit}').decode().strip() != revision:
        raise MigrationError('Fetched revision did not resolve to the requested full commit')
    git('-C', str(dest), '-c', 'core.hooksPath=' + os.devnull, '-c', 'advice.detachedHead=false',
        'checkout', '-q', '--detach', revision)
    verify_checkout(dest, revision)
    runtime_update.candidate(dest)
    return dest


def verify_checkout(source, revision):
    git = runtime_update.git
    if git('-C', str(source), 'rev-parse', 'HEAD').decode().strip() != revision:
        raise MigrationError('Pinned checkout HEAD does not match the requested commit')
    if git('-C', str(source), 'status', '--porcelain', '--untracked-files=normal'):
        raise MigrationError('Pinned checkout is not clean')


def backup_targets(old_root, new_root, source, project):
    """path -> role. 'restore' paths are reinstated by --restore-migration;
    'archive' paths (legacy root, project data) are kept for manual recovery."""
    targets = {old_root: 'archive'}
    paths = {p for p in install_paths(new_root, source) if not under(p, new_root)}
    paths.update(Path.home() / name for name in EXTRA_INTEGRATIONS)
    if project:
        targets[project / '.rig'] = 'archive'
        paths.update(install_paths(new_root, source, project))
        paths.update(project / name for name in PROJECT_GUIDANCE)
        paths.add(project / '.agents/skills')
    for path in paths:
        targets[path.absolute()] = 'restore'
    return targets


def inventory(targets, digest=False):
    entries = {}
    for top in sorted(targets, key=str):
        for path in walk(top):
            key = str(path)
            restore = targets.get(path) == 'restore' or targets[top] == 'restore' or entries.get(key, {}).get('role') == 'restore'
            entries[key] = {**describe(path, digest), 'role': 'restore' if restore else 'archive'}
            if restore:
                # Restore refuses if a parent is later redirected elsewhere.
                entries[key]['parent'] = os.path.realpath(path.parent)
    return entries


def after_images(entries):
    return {k: describe(Path(k)) for k, e in entries.items()
            if e['role'] == 'restore' and e['kind'] not in ('special', 'via-link')}


def tree_path(directory, key):
    return directory / 'tree' / Path(key).relative_to('/')


def linked_parent(dest, stop):
    return any(parent.is_symlink() for parent in dest.parents if under(parent, stop) and parent != stop)


def private_dirs(path):
    missing = []
    while not path.exists() and not path.is_symlink():
        missing.append(path)
        path = path.parent
    for item in reversed(missing):
        item.mkdir(mode=0o700)
        item.chmod(0o700)


def copy_private(source, dest):
    digest = hashlib.sha256()
    out_fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    in_fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(in_fd, 'rb') as src, os.fdopen(out_fd, 'wb') as out:
        for chunk in iter(lambda: src.read(1 << 20), b''):
            digest.update(chunk)
            out.write(chunk)
        out.flush()
        os.fsync(out.fileno())
    return digest.hexdigest()


def backup(entries, directory):
    """Private copy (dirs 0700, files 0600, links as links); modes in inventory."""
    tree = directory / 'tree'
    private_dirs(tree)
    for key in sorted(entries):
        entry = entries[key]
        dest = tree_path(directory, key)
        if entry['kind'] in ('missing', 'special'):
            continue
        if linked_parent(dest, tree):
            # Reached through a recorded link; restoring that link covers it.
            entry['kind'] = 'via-link'
            continue
        private_dirs(dest.parent)
        if entry['kind'] == 'dir':
            private_dirs(dest)
        elif entry['kind'] == 'link':
            os.symlink(entry['target'], dest)
        else:
            entry['sha256'] = copy_private(Path(key), dest)
    write_json(directory / 'inventory.json', {'schema_version': 1, 'entries': entries})
    verify_backup(entries, directory)


def verify_backup(entries, directory):
    for key, entry in entries.items():
        dest = tree_path(directory, key)
        actual = describe(dest)
        if entry['kind'] == 'file' and (actual['kind'] != 'file' or actual['sha256'] != entry['sha256']):
            raise MigrationError('Backup verification failed: ' + key)
        if entry['kind'] == 'link' and actual != {'kind': 'link', 'target': entry['target']}:
            raise MigrationError('Backup verification failed: ' + key)
        if entry['kind'] == 'dir' and actual['kind'] != 'dir':
            raise MigrationError('Backup verification failed: ' + key)
    for path in walk(directory):
        info = path.lstat()
        if not stat.S_ISLNK(info.st_mode) and (info.st_uid != os.getuid() or info.st_mode & 0o077):
            raise MigrationError('Backup is not private: ' + str(path))


def verify_unchanged(entries, root):
    recorded = {k: e for k, e in entries.items() if under(Path(k), root)}
    current = {str(p) for p in walk(root)}
    if current != set(recorded) or any(not same(describe(Path(k)), e) for k, e in recorded.items()
                                       if e['kind'] != 'via-link'):
        raise MigrationError(f'Legacy runtime changed during migration: {root}')


def project_snapshot(project):
    path = project / '.rig/harness.toml'
    data = {str(p): describe(p) for p in walk(project / '.rig')
            if p != path and describe(p, False)['kind'] != 'dir'}
    return path.read_text().splitlines(), harness.parse_harness(path), data


def verify_project(project, before):
    lines, parsed, data = before
    after_lines, after_parsed, after_data = project_snapshot(project)
    remaining = iter(after_lines)
    if not all(line in remaining for line in lines):
        raise MigrationError('Project init changed existing harness configuration lines')
    if after_parsed != parsed:
        raise MigrationError('Project init changed effective worker/cap/routing settings')
    # Init may add missing templates (e.g. STATE.md); existing entries must not change.
    changed = sorted(k for k, v in data.items() if after_data.get(k) != v)
    if changed:
        raise MigrationError('Project init changed project history/data: ' + ', '.join(changed[:5]))


def verify_baseline(new_root, revision):
    state, _ = runtime_update.baseline(new_root)
    if state['commit'] != revision:
        raise MigrationError('New runtime baseline commit does not match the pinned revision')
    return state


def child_env(new_root, source):
    env = {k: v for k, v in os.environ.items() if k not in DROP_ENV}
    env.update(SKIP_ENV, RIG_HOME=str(new_root), RIG_SRC=str(source))
    return env


def run_step(command, cwd, env):
    """Reviewed setup/init from the pinned checkout; never reads a TTY."""
    return subprocess.run(command, cwd=cwd, env=env, stdin=subprocess.DEVNULL, check=False, timeout=900).returncode


def create_new_root(new_root, old_root):
    new_root.mkdir(mode=0o700)  # FileExistsError: destination conflict, refuse.
    new_root.chmod(0o700)
    copied = []
    for name in optional_skills.PREFERENCES:
        source = old_root / name
        if describe(source, False)['kind'] == 'file':
            write_private(new_root / name, source.read_bytes())
            copied.append(name)
    return copied


def missing_locks(old_root):
    return [n for n in LOCKS if not (old_root / n).exists() and not (old_root / n).is_symlink()]


def old_root_locks(stack, old_root):
    """Hold the legacy update gate (exclusive) then lifecycle lock.

    Absent lock files are created empty (0600): the only change made to the
    legacy root. Gate-aware legacy admissions/leases then wait and refuse;
    gate-unaware code is caught by the quiescence re-check after setup.
    """
    created = missing_locks(old_root)
    for name in created:
        os.close(os.open(old_root / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600))
    stack.enter_context(update_gate.lock(old_root, exclusive=True))
    stack.enter_context(lifecycle_lock(old_root))
    return created


def launcher_path():
    return Path.home() / '.local/bin/rig'


def write_launcher(new_root, directory):
    """Bind ~/.local/bin/rig to the new root, whatever setup did with it.

    The new installation manifest keeps setup's preimage and records this
    launcher as its after-image, so uninstall restores rather than preserves it.
    """
    path = launcher_path()
    if describe(path, False)['kind'] not in ('missing', 'file', 'link'):
        raise MigrationError(f'Launcher conflict: {path} is not a file or link; left unchanged')
    prior = snapshot(path)
    body = ('#!/usr/bin/env bash\n'
            '# Generated by rig update --migrate: runs the migrated runtime with its RIG_HOME.\n'
            f'# Undo: {restore_command(directory)}\n'
            f'export RIG_HOME={shlex.quote(str(new_root))}\n'
            f'exec {shlex.quote(str(new_root / "bin/rig"))} "$@"\n')
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name('.' + path.name + '.migrate-' + uuid.uuid4().hex)
    try:
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o700)
        with os.fdopen(fd, 'w') as stream:
            stream.write(body)
            os.fchmod(stream.fileno(), 0o700)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)
    with lifecycle_lock(new_root):
        manifest_path = new_root / 'install-manifest.json'
        manifest = runtime_update.read_json(manifest_path)
        entry = manifest['entries'].setdefault(str(path), {'before': prior})
        entry['after'] = snapshot(path)
        runtime_update.write_json(manifest_path, manifest)
    return path


def consented(question, *, assume_yes, interactive, ask):
    if assume_yes:
        return True
    if not interactive:
        raise MigrationError('Non-interactive run needs --yes after reviewing the --dry-run preview; nothing changed')
    return bool(ask(question))


def preview(out, *, old_root, new_root, revision, project, directory, entries, targets):
    files = [e for e in entries.values() if e['kind'] == 'file']
    existing = sorted(str(p) for p, role in targets.items() if role == 'restore'
                      and entries.get(str(p), {}).get('kind') not in (None, 'missing'))
    consent = [n for n in optional_skills.PREFERENCES if describe(old_root / n, False)['kind'] == 'file']
    locks = missing_locks(old_root)
    lines = [
        'Legacy migration plan (nothing has been changed):',
        f'  legacy runtime:  {old_root} (kept; not adopted; contents not modified)',
        ('  legacy locks:    creates empty ' + ', '.join(locks) + ' (0600) there and holds them to block admissions')
        if locks else '  legacy locks:    holds its existing update/lifecycle locks during migration',
        f'  new root:        {new_root} (absent now; created empty, 0700)',
        f'  pinned commit:   {revision} from {runtime_update.OFFICIAL_REPO}',
        '                   clean Git checkout, compatibility/syntax checked, not executed yet',
        f'  consent copied:  {", ".join(consent) or "none"} (no other runtime files)',
        '  setup:           checkout bin/rig setup --no-mimo with RIG_SKIP_CUA_DRIVER=1 RIG_SKIP_BROWSER_SKILL=1',
        '                   RIG_SKIP_TMUX_INSTALL=1; no optional installer, login, grant or project enabling',
        ('  project init:    ' + str(project) + ' (existing enabled project; workers/caps/routing/history kept)')
        if project else '  project init:    none (initialize chosen projects later with RIG_HOME set)',
        f'  backup:          {directory}/backup (private 0700/0600; {len(files)} files, '
        f'{sum(e["size"] for e in files)} bytes; legacy root, project data, integrations)',
        f'  launcher:        {launcher_path()} replaced by a generated script binding RIG_HOME to the new root',
        f'  restore:         {restore_command(directory)}',
        '  existing integration/guidance destinations setup or init may change:',
    ]
    lines += ['    ' + path for path in existing] or ['    none']
    lines.append('  success requires a verified baseline; fully restart parent/MCP sessions')
    lines.append('  agent MCP:       scripts/rig-mcp.sh binds RIG_HOME to its versioned runtime '
                 'parent when unset (no shell-profile export required)')
    current = (os.environ.get('RIG_HOME') or '').strip()
    if current and Path(current).expanduser().absolute() != Path(new_root).absolute():
        lines.append('  warning:         RIG_HOME=' + shlex.quote(current)
                     + ' differs from the new root; unset it or point it at '
                     + shlex.quote(str(new_root)) + ' so agents use the migrated runtime')
    out('\n'.join(lines))


def failure(out, directory, journal, error, entries):
    journal.update(phase='failed', error=str(error))
    try:
        write_json(directory / 'after.json', {'schema_version': 1, 'entries': after_images(entries)})
        write_json(directory / 'migration.json', journal)
    except OSError:
        pass
    locks = ', '.join(journal.get('created_locks') or [])
    out(f'Migration incomplete: {error}\n'
        f'  legacy runtime {journal["old_root"]} contents were not modified'
        + (f' (empty lock files added: {locks})' if locks else '') + f'; backup kept: {directory}/backup\n'
        f'  new root {journal["new_root"]} is left for inspection (not deleted; this is not a rollback)\n'
        f'  preview restore: {restore_command(directory)} --dry-run\n'
        f'  restore integrations/project guidance: {restore_command(directory)}\n'
        '  then fully restart parent/MCP sessions')


def migrate(revision, *, old_root, new_root=None, project=None, dry_run=False, assume_yes=False,
            interactive=False, ask=None, out=print):
    if not SHA.fullmatch(revision or ''):
        raise MigrationError('Migration requires a full 40-character commit SHA')
    old_root = Path(old_root).absolute()
    new_root = Path(new_root or default_new_root()).expanduser().absolute()
    project = Path(project).expanduser().resolve() if project else None
    if not dry_run and not assume_yes and not interactive:
        raise MigrationError('Non-interactive migration needs --dry-run to preview or --yes to apply; nothing changed')
    preflight(old_root, new_root, project)
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    directory = migrations_home() / f'{stamp}-{revision[:12]}'
    with tempfile.TemporaryDirectory(prefix='rig-migrate-') as temp:
        staged = pinned_checkout(revision, Path(temp) / 'source')
        targets = backup_targets(old_root, new_root, staged, project)
        preview(out, old_root=old_root, new_root=new_root, revision=revision, project=project,
                directory=directory, entries=inventory(targets), targets=targets)
        if dry_run:
            out('Dry run: no installed, project or integration files changed')
            return 0
        if not consented(f'Migrate to {new_root} at {revision}?', assume_yes=assume_yes,
                         interactive=interactive, ask=ask):
            out('Cancelled; no files changed')
            return 1
        private_dirs(migrations_home())
        directory.mkdir(mode=0o700)
        shutil.copytree(staged, directory / 'source', symlinks=True)
    source = directory / 'source'
    verify_checkout(source, revision)
    journal = {'schema_version': 1, 'phase': 'backing-up', 'old_root': str(old_root), 'new_root': str(new_root),
               'revision': revision, 'project': str(project) if project else None, 'source': str(source)}
    write_json(directory / 'migration.json', journal)
    with ExitStack() as stack:
        journal['created_locks'] = missing_locks(old_root)
        write_json(directory / 'migration.json', journal)
        try:
            old_root_locks(stack, old_root)
            repos = preflight(old_root, new_root, project)
            entries = inventory(backup_targets(old_root, new_root, source, project))
            backup(entries, directory / 'backup')
        except (Exception, KeyboardInterrupt) as error:
            journal.update(phase='backup-failed', error=str(error))
            write_json(directory / 'migration.json', journal)
            locks = ', '.join(journal['created_locks'])
            raise MigrationError(f'Not migrated ({error}); nothing outside {directory} changed'
                                 + (f' except empty legacy lock files ({locks})' if locks else '')
                                 + f'; {old_root} remains in use') from error
        journal['phase'] = 'backed-up'
        write_json(directory / 'migration.json', journal)
        try:
            copied = create_new_root(new_root, old_root)
            journal['phase'] = 'setup'
            write_json(directory / 'migration.json', journal)
            env = child_env(new_root, source)
            if run_step(['bash', str(source / 'bin/rig'), 'setup', '--no-mimo'], str(Path.home()), env):
                raise MigrationError('pinned setup failed')
            verify_baseline(new_root, revision)
            if project:
                journal['phase'] = 'init'
                write_json(directory / 'migration.json', journal)
                before = project_snapshot(project)
                if run_step(['bash', str(source / 'bin/rig'), 'init'], str(project), env):
                    raise MigrationError('project init failed')
                verify_project(project, before)
                verify_baseline(new_root, revision)
            journal['phase'] = 'launcher'
            write_json(directory / 'migration.json', journal)
            launcher = write_launcher(new_root, directory)
            verify_baseline(new_root, revision)
            # Gate-unaware legacy code cannot be locked out; detect it instead.
            runtime_update.quiet(old_root, repos)
            verify_unchanged(entries, old_root)
        except (Exception, KeyboardInterrupt) as error:
            failure(out, directory, journal, error, entries)
            raise MigrationError('migration incomplete; backup preserved') from error
        write_json(directory / 'after.json', {'schema_version': 1, 'entries': after_images(entries)})
    journal.update(phase='complete', consent_copied=copied)
    write_json(directory / 'migration.json', journal)
    current = (os.environ.get('RIG_HOME') or '').strip()
    warn = ''
    if current and Path(current).expanduser().absolute() != Path(new_root).absolute():
        warn = (f'  warning: RIG_HOME={shlex.quote(current)} differs from the new root; '
                f'unset it or point it at {shlex.quote(str(new_root))} so agents use the migrated runtime\n')
    out(f'Migration complete: {new_root} at {revision} (baseline verified; legacy {old_root} kept)\n'
        f'  launcher: {launcher} now runs the new runtime with RIG_HOME bound\n'
        '  agent MCP: scripts/rig-mcp.sh binds RIG_HOME to the versioned runtime when unset '
        '(no shell-profile export required)\n'
        f'{warn}'
        '  fully restart every parent/MCP session; later: rig update --latest\n'
        f'  backup: {directory}/backup   undo integration switch: {restore_command(directory)}')
    return 0


def restore_plan(entries, after, force=False):
    """Only undo what migration did. A path changed since the recorded
    migration result (or unrecorded, after a hard crash) needs --force;
    structural conflicts never proceed."""
    actions, conflicts, forced = [], [], []
    for key in sorted(entries):
        entry = entries[key]
        if entry['role'] != 'restore' or entry['kind'] in ('special', 'via-link'):
            continue
        path = Path(key)
        current = describe(path)
        if same(current, entry) or (entry['kind'] == 'missing' and current['kind'] == 'dir'):
            continue  # unchanged, or a directory setup created (left in place)
        if entry.get('parent') and os.path.realpath(path.parent) != entry['parent']:
            conflicts.append((key, 'parent directory now resolves elsewhere'))
            continue
        if (current['kind'] != 'missing') if entry['kind'] == 'dir' else current['kind'] in ('dir', 'special'):
            conflicts.append((key, 'is now a ' + current['kind']))
            continue
        reason = None
        if after is None:
            reason = 'no recorded migration result (interrupted)'
        elif key not in after or not same(current, after[key]):
            reason = 'changed after migration'
        if reason:
            forced.append((key, reason))
            if not force:
                continue
        actions.append(('remove' if entry['kind'] == 'missing' else 'mkdir' if entry['kind'] == 'dir' else 'restore',
                        key, entry))
    return actions, conflicts, forced


def apply_restore(actions, directory):
    for action, key, entry in actions:
        path = Path(key)
        if action == 'remove':
            path.unlink()
            continue
        if action == 'mkdir':
            path.mkdir(parents=True, mode=entry['mode'])
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name('.' + path.name + '.restore-' + uuid.uuid4().hex)
        try:
            if entry['kind'] == 'link':
                temp.symlink_to(entry['target'])
            else:
                data = tree_path(directory, key).read_bytes()
                if hashlib.sha256(data).hexdigest() != entry['sha256']:
                    raise MigrationError('Backup changed; restore stopped at ' + key)
                fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
                with os.fdopen(fd, 'wb') as stream:
                    stream.write(data)
                    os.fchmod(stream.fileno(), entry['mode'])
                    stream.flush()
                    os.fsync(stream.fileno())
            os.replace(temp, path)
        finally:
            temp.unlink(missing_ok=True)


def restore(directory, *, dry_run=False, assume_yes=False, force=False, interactive=False, ask=None, out=print):
    directory = Path(directory).expanduser().absolute()
    journal = read_private_json(directory / 'migration.json')
    if journal.get('phase') == 'restored':
        out('Already restored; no files changed')
        return 0
    if journal.get('phase') in ('backing-up', 'backup-failed') or not (directory / 'backup/inventory.json').exists():
        out('Backup never completed, so no integration or project file was changed; nothing to restore.\n'
            f'  legacy runtime {journal.get("old_root")} remains in use')
        return 0
    if not dry_run and not assume_yes and not interactive:
        raise MigrationError('Non-interactive restore needs --dry-run to preview or --yes to apply; nothing changed')
    entries = read_private_json(directory / 'backup/inventory.json')['entries']
    verify_backup(entries, directory / 'backup')
    after = read_private_json(directory / 'after.json')['entries'] if (directory / 'after.json').exists() else None
    new_root = Path(journal['new_root'])
    with ExitStack() as stack:
        if (new_root / '.lifecycle.lock').is_file():
            stack.enter_context(lifecycle_lock(new_root))
        if new_root.is_dir():
            blocker = active_runtime_blocker(new_root, [journal['project']] if journal.get('project') else [])
            if blocker:
                raise MigrationError(f'New runtime is busy ({blocker}); stop its sessions first. No files changed')
        actions, conflicts, forced = restore_plan(entries, after, force)
        lines = [f'Restore from {directory}/backup:'] + [f'  {a} {k}' for a, k, _ in actions]
        lines += [f'  conflict {k}: {why}' for k, why in conflicts]
        lines += [f'  {"overwrite" if force else "needs --force"} {k}: {why}' for k, why in forced]
        out('\n'.join(lines if len(lines) > 1 else lines + ['  nothing differs from the backup']))
        if conflicts or (forced and not force):
            raise MigrationError(f'Restore refused; no files changed. Resolve the listed paths manually from '
                                 f'{directory}/backup/tree' + ('' if conflicts else ', or rerun with --force after review'))
        if dry_run:
            out('Dry run: no files changed')
            return 0
        if actions and not consented('Restore these integration/project guidance paths?', assume_yes=assume_yes,
                                     interactive=interactive, ask=ask):
            out('Cancelled; no files changed')
            return 1
        apply_restore(actions, directory / 'backup')
        journal['phase'] = 'restored'
        write_json(directory / 'migration.json', journal)
    out(f'Restored {len(actions)} paths. Legacy runtime {journal["old_root"]} contents were never modified; '
        f'new root {new_root} is left for inspection (not deleted). Unset RIG_HOME if you exported it, '
        'then fully restart parent/MCP sessions.')
    return 0
