#!/usr/bin/env python3
"""Explicit pinned, compatible runtime updates; never execute an installer.

Per-file replacement is atomic. A private write-ahead journal makes a multi-file
update recoverable, not globally atomic. Pending transactions block admissions.
"""
from __future__ import annotations

import argparse
import base64
from contextlib import contextmanager
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tarfile
import tempfile
import uuid

import update_gate
from ui_install import Manifest, active_runtime_blocker, lifecycle_lock, snapshot

OFFICIAL_REPO = 'https://github.com/Brasth/Rig.git'
FOLDERS = ('bin', 'scripts', 'skills', 'adapters', 'templates')
COMPAT = {'schema_version': 1, 'update_protocol': 1, 'admission_protocol': 1,
          'child_mcp_protocol': 1, 'job_data_protocol': 1,
          'workflow_data_protocol': 1, 'managed_guidance_protocol': 1}
SHA = re.compile(r'[0-9a-f]{40}')
TXID = re.compile(r'[0-9a-f]{32}')
MAX_BYTES = 64 * 1024 * 1024


class UpdateError(ValueError):
    pass


def digest(state):
    return hashlib.sha256(json.dumps(state, sort_keys=True).encode()).hexdigest()


def file_state(data, mode=0o600):
    return {'kind': 'file', 'data': base64.b64encode(data).decode(), 'mode': mode}


def json_state(value):
    return file_state((json.dumps(value, indent=2, sort_keys=True) + '\n').encode())


def private(path, directory=False):
    update_gate.safe_parents(path)
    info = path.lstat()
    valid = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
    if not valid or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise UpdateError('Unsafe update metadata ownership or permissions: ' + str(path))


def private_directory(path):
    """Create our private namespace, or validate it without adopting/chmodding."""
    update_gate.safe_parents(path)
    if path.exists() or path.is_symlink():
        private(path, True)
        return
    path.mkdir(mode=0o700)
    sync_dir(path.parent)


def read_json(path):
    private(path)
    if path.stat().st_size > MAX_BYTES:
        raise UpdateError('Oversized update metadata')
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise UpdateError('Invalid update metadata')
    return value


def sync_dir(path):
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def replace(path, state):
    """Never truncate a live file and never follow a destination symlink."""
    update_gate.safe_parents(path)
    if path.exists() and not path.is_file() and not path.is_symlink():
        raise UpdateError('Non-file update destination: ' + str(path))
    if state['kind'] == 'missing':
        path.unlink(missing_ok=True)
        sync_dir(path.parent)
        return
    if state['kind'] not in {'file', 'link'}:
        raise UpdateError('Unsupported update snapshot')
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name('.' + path.name + '.update-' + uuid.uuid4().hex)
    try:
        if state['kind'] == 'link':
            temp.symlink_to(state['target'])
        else:
            fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, 'wb') as stream:
                stream.write(base64.b64decode(state['data'], validate=True))
                os.fchmod(stream.fileno(), state['mode'])
                stream.flush()
                os.fsync(stream.fileno())
        os.replace(temp, path)
        sync_dir(path.parent)
    finally:
        temp.unlink(missing_ok=True)


def write_json(path, value):
    replace(path, json_state(value))


def git_env():
    env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
    env.update(GIT_TERMINAL_PROMPT='0', GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull)
    return env


def git(*args, cwd=None):
    return subprocess.check_output(['git', *args], cwd=cwd, env=git_env(), stderr=subprocess.PIPE, timeout=120)


@contextmanager
def fetched_source(revision):
    """Resolve a full commit only from the official repository; no hooks/code."""
    if not SHA.fullmatch(revision):
        raise UpdateError('--revision requires a full 40-character commit SHA (no moving main/tag default)')
    with tempfile.TemporaryDirectory(prefix='rig-update-') as raw:
        temp = Path(raw)
        objects, source = temp / 'objects', temp / 'source'
        source.mkdir()
        git('init', '--bare', str(objects))
        git('-C', str(objects), 'fetch', '--depth=1', '--no-tags', OFFICIAL_REPO, revision)
        actual = git('-C', str(objects), 'rev-parse', 'FETCH_HEAD^{commit}').decode().strip()
        if actual != revision:
            raise UpdateError('Fetched revision did not resolve to the requested full commit')
        archive = git('-C', str(objects), 'archive', '--format=tar', revision)
        if len(archive) > MAX_BYTES:
            raise UpdateError('Candidate source archive is too large')
        total = 0
        with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
            members = tar.getmembers()
            if len(members) > 10000:
                raise UpdateError('Candidate contains too many paths')
            for item in members:
                path = Path(item.name)
                if path.is_absolute() or '..' in path.parts:
                    raise UpdateError('Unsafe candidate archive path')
                if item.isdir():
                    continue
                if not item.isfile():
                    raise UpdateError('Candidate symlink/special file is unsupported')
                total += item.size
                if total > MAX_BYTES:
                    raise UpdateError('Candidate source is too large')
                dest = source / path
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(tar.extractfile(item).read())
                dest.chmod(item.mode & 0o777)
        yield source


def candidate(source):
    compat = json.loads((source / 'templates/runtime-compat.json').read_text())
    if compat != COMPAT:
        raise UpdateError('Unsupported runtime compatibility; no migration or files changed')
    required = ('bin/rig', 'scripts/runtime_update.py', 'scripts/update_gate.py',
                'scripts/ui_install.py', 'scripts/admission.py', 'scripts/rig_mcp.py',
                'templates/agents-protocol.md', 'skills/delegate-harness/SKILL.md')
    for name in required:
        if not (source / name).is_file() or (source / name).is_symlink():
            raise UpdateError('Candidate missing required runtime asset: ' + name)
    files = {}
    total = 0
    for folder in FOLDERS:
        for path in sorted((source / folder).rglob('*')):
            if '__pycache__' in path.parts or path.suffix == '.pyc':
                continue
            if path.is_symlink():
                raise UpdateError('Candidate runtime symlink is unsupported')
            if path.is_dir():
                continue
            if not path.is_file():
                raise UpdateError('Candidate special file is unsupported')
            name = path.relative_to(source).as_posix()
            data = path.read_bytes()
            total += len(data)
            if len(files) >= 10000 or total > MAX_BYTES:
                raise UpdateError('Candidate runtime exceeds update bounds')
            if path.suffix == '.py':
                compile(data, name, 'exec')
            if path.suffix == '.sh' or name == 'bin/rig':
                subprocess.run(['bash', '-n', str(path)], check=True, capture_output=True, timeout=10)
            # Newly introduced runtime files are owner-only. Existing owned
            # destination modes are preserved by plan(), never broadened.
            # Setup marks only bin/rig and top-level scripts executable; nested
            # packages (scripts/mcp_tools) are imported and stay 0600.
            mode = 0o600
            if name == 'bin/rig' or (name.count('/') == 1 and name.startswith('scripts/') and path.suffix in {'.py', '.sh'}):
                mode = 0o700
            files[name] = file_state(data, mode)
    return files


def runtime_asset(path, root):
    try:
        rel = path.relative_to(root)
        return rel.as_posix() if rel.parts[0] in FOLDERS else None
    except (ValueError, IndexError):
        return None


def integration_source(path, entry):
    """Explicit copied-asset map. Configuration files are deliberately absent."""
    home = Path.home()
    fixed = {
        home / '.codex/prompts/queue.md': 'adapters/codex/prompts/queue.md',
        home / '.config/opencode/commands/queue.md': 'adapters/opencode/commands/queue.md',
        home / '.config/opencode/plugins/rig-queue.js': 'adapters/opencode/plugin/rig-queue.js',
        home / '.config/opencode/plugin/rig-queue.js': 'adapters/opencode/plugin/rig-queue.js',
        home / '.omp/agent/extensions/rig-queue.js': 'adapters/omp/extensions/rig-queue.js',
        home / '.pi/agent/extensions/rig-queue.js': 'adapters/pi/extensions/rig-queue.js',
        Path(os.environ.get('GROK_HOME', home / '.grok')) / 'rig-statusline.sh': 'scripts/rig-statusline.sh',
    }
    # Agent TOML, MCP/hook settings, marketplace configuration and consent
    # records are configuration, not copied executable/prose assets. Preserve.
    for ext in ('tsx', 'js'):
        fixed[home / '.config/opencode/tui-plugins' / ('rig-hud.' + ext)] = 'adapters/opencode/tui/rig-hud.' + ext
    if path in fixed:
        return fixed[path]
    plugin = home / '.agents/plugins/rig-queue'
    if path.is_relative_to(plugin):
        return 'adapters/codex/plugin/rig-queue/' + path.relative_to(plugin).as_posix()
    if entry.get('repo'):
        repo = Path(entry['repo'])
        skill = repo / '.agents/skills'
        if path.is_relative_to(skill):
            return 'skills/' + path.relative_to(skill).as_posix()
        if path == repo / 'AGENTS.md':
            return '@agents-block'
        # CLAUDE is intentionally a stable pointer to AGENTS; no changes needed.
    return None


def owned_entries(manifest, root):
    return {name: entry for name, entry in manifest['entries'].items()
            if entry.get('after') is not None and entry['before'] != entry['after']
            and (runtime_asset(Path(name), root) or integration_source(Path(name), entry))}


def record_install(root, source, manifest):
    """Called only after successful explicit setup, never during update.

    Dirty/unversioned sources and legacy uncertain ownership are not adopted.
    """
    state_path = root / 'runtime-state.json'
    if state_path.exists():
        # Explicit setup can alter sources/config. Invalidate old update evidence
        # unless the complete new baseline is established below.
        state_path.unlink()
    try:
        revision = git('-C', str(source), 'rev-parse', 'HEAD').decode().strip()
        if not SHA.fullmatch(revision) or git('-C', str(source), 'status', '--porcelain', '--untracked-files=normal'):
            raise UpdateError('source is dirty or unversioned')
        files = candidate(source)
        tracked = {}
        for row in git('-C', str(source), 'ls-tree', '-r', '-z', revision).split(b'\0'):
            if not row:
                continue
            metadata, name = row.split(b'\t', 1)
            mode, kind, sha = metadata.split()
            if kind == b'blob' and mode in {b'100644', b'100755'}:
                tracked[name.decode()] = sha.decode()
        for name, value in files.items():
            data = base64.b64decode(value['data'])
            blob = hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()
            if tracked.get(name) != blob:
                raise UpdateError('source runtime bytes do not match the full commit')
        if not manifest.get('runtime_owned'):
            raise UpdateError('legacy runtime ownership is uncertain')
        owned = owned_entries(manifest, root)
        for name, entry in owned.items():
            rel = runtime_asset(Path(name), root)
            if rel:
                expected, actual = files.get(rel), entry['after']
                if expected is None or actual.get('kind') != 'file' or actual.get('data') != expected['data']:
                    raise UpdateError('installed runtime does not match source')
                mode = actual.get('mode')
                # install_file uses mktemp 0600; copy_rig_tree then adds +x
                # under the caller's umask. Do not bless any other mode.
                valid_mode = (isinstance(mode, int) and mode & ~0o011 == 0o700) if expected['mode'] == 0o700 else mode == 0o600
                if not valid_mode:
                    raise UpdateError('installed runtime mode does not match fresh setup')
        if any(str(root / name) not in owned for name in files):
            raise UpdateError('source runtime asset is not installation-owned')
        directory = root / 'updates'
        directory.mkdir(mode=0o700, exist_ok=True)
        private(directory, True)
        lock = root / '.update.lock'
        if not lock.exists():
            fd = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            os.close(fd)
        private(lock)
        write_json(state_path, {'schema_version': 1, 'commit': revision, 'compatibility': COMPAT,
                               'owned_hashes': {p: digest(e['after']) for p, e in owned.items()}, 'last_transaction': None})
        replace(directory / 'epoch', file_state(uuid.uuid4().hex.encode()))
        return True
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print('safe updates unavailable for this setup: ' + str(error), file=sys.stderr)
        return False


def baseline(root):
    if not (root / 'runtime-state.json').is_file():
        raise UpdateError('Legacy/unversioned installation (no runtime-state.json): no automatic adoption or legacy rollback. '
                          'Preview the guided migration: rig update --migrate --latest --dry-run '
                          '(an older installed rig lacks --migrate; run it as /path/to/Rig-checkout/bin/rig update --migrate --latest --dry-run)')
    state = read_json(root / 'runtime-state.json')
    manifest = read_json(root / 'install-manifest.json')
    if state.get('schema_version') != 1 or not SHA.fullmatch(str(state.get('commit', ''))) or state.get('compatibility') != COMPAT:
        raise UpdateError('Unsupported installed runtime compatibility/provenance')
    if manifest.get('version') != 1 or not manifest.get('runtime_owned') or not isinstance(manifest.get('entries'), dict):
        raise UpdateError('Unsupported or uncertain installation ownership')
    for repo in repos_for(manifest):
        if not Path(repo).is_dir():
            raise UpdateError('registered repository unavailable')
    for name, value in state.get('owned_hashes', {}).items():
        entry = manifest['entries'].get(name)
        if not entry or digest(entry.get('after')) != value:
            raise UpdateError('Installation ownership evidence changed; safe update refused')
        if snapshot(Path(name)) != entry['after']:
            raise UpdateError('Modified owned file conflict: ' + name)
    if not state.get('owned_hashes'):
        raise UpdateError('Installed runtime has no owned-file hashes')
    return state, manifest


def repos_for(manifest):
    repos = set(manifest.get('repos', []))
    repos.update(e['repo'] for e in manifest['entries'].values() if e.get('repo'))
    if any(not isinstance(p, str) or not Path(p).is_absolute() for p in repos):
        raise UpdateError('Invalid registered repository')
    return sorted(repos)


def quiet(root, repos):
    blocker = active_runtime_blocker(root, repos)
    if blocker:
        raise UpdateError(blocker)
    # Historical legacy jobs may predate reservation tracking. Unknown outcomes
    # are never treated as stopped merely because they are old.
    for repo in repos:
        rig = Path(repo) / '.rig'
        for folder in (rig / 'jobs').glob('*'):
            if not folder.is_dir():
                continue
            path = folder / 'meta.json'
            if not path.is_file():
                raise UpdateError('Registered repository has a job without terminal evidence')
            data = json.loads(path.read_text())
            if not isinstance(data, dict) or data.get('status') not in {'ok', 'fail', 'timeout', 'cancelled', 'stopped'}:
                raise UpdateError('Registered repository has active or unconfirmed jobs')
            if data.get('cancel_status') in {'stop-requested', 'stop-unconfirmed', 'native-cancel-required'} or data.get('stop_state') in {'stop-requested', 'stop-unconfirmed', 'native-cancel-required'}:
                raise UpdateError('Registered repository has unconfirmed job termination')
        for path in (rig / 'workflows').glob('*/state.json'):
            data = json.loads(path.read_text())
            if not isinstance(data, dict) or data.get('status') not in {'verified', 'failed', 'cancelled', 'completed-unverified'}:
                raise UpdateError('Registered repository has active or uncertain workflows')


def validate_paths(changes, *, recovery=False):
    for name, change in changes.items():
        path = Path(name)
        if not path.is_absolute():
            raise UpdateError('Invalid journal destination')
        update_gate.safe_parents(path)
        current = snapshot(path)
        allowed = [change['before'], change['after']] if recovery else [change['before']]
        if current not in allowed:
            raise UpdateError('Modified owned file conflict: ' + name)


def managed_block(old, new):
    start, end = b'<!-- rig:start -->', b'<!-- rig:end -->'
    if old.count(start) != 1 or old.count(end) != 1 or new.count(start) != 1 or new.count(end) != 1:
        raise UpdateError('Uncertain managed guidance boundaries')
    a, b = old.index(start), old.index(end) + len(end)
    c, d = new.index(start), new.index(end) + len(end)
    if a >= b - len(end) or c >= d - len(end):
        raise UpdateError('Uncertain managed guidance ordering')
    return old[:a] + new[c:d] + old[b:]


def plan(root, source, revision, state, manifest):
    files = candidate(source)
    updated = copy.deepcopy(manifest)
    entries = updated['entries']
    desired = {str(root / name): value for name, value in files.items()}
    owned = owned_entries(manifest, root)
    for name, entry in owned.items():
        path = Path(name)
        rel = runtime_asset(path, root)
        if rel:
            desired.setdefault(name, {'kind': 'missing'})
            if desired[name]['kind'] == 'file':
                desired[name]['mode'] = entry['after']['mode']
            continue
        source_name = integration_source(path, entry)
        if source_name == '@agents-block':
            if entry['after']['kind'] != 'file':
                raise UpdateError('Owned guidance is not a regular file')
            old = base64.b64decode(entry['after']['data'])
            new = base64.b64decode(files['templates/agents-protocol.md']['data'])
            desired[name] = file_state(managed_block(old, new), entry['after']['mode'])
        elif source_name:
            value = copy.deepcopy(files.get(source_name, {'kind': 'missing'}))
            if value['kind'] == 'file':
                value['mode'] = entry['after'].get('mode', value['mode'])
            desired[name] = value
    # Add new nested references only inside an already-owned copied project skill
    # or plugin tree. Never turn a manual directory/symlink into owned content.
    prefixes = {}
    for name, entry in owned.items():
        source_name = integration_source(Path(name), entry)
        if source_name and source_name.startswith('skills/') and entry.get('repo'):
            skill = source_name.split('/')[1]
            prefixes[Path(entry['repo']) / '.agents/skills' / skill] = 'skills/' + skill + '/'
        if source_name and source_name.startswith('adapters/codex/plugin/rig-queue/'):
            prefixes[Path.home() / '.agents/plugins/rig-queue'] = 'adapters/codex/plugin/rig-queue/'
    for dest, prefix in prefixes.items():
        for rel, value in files.items():
            if rel.startswith(prefix):
                desired.setdefault(str(dest / rel[len(prefix):]), value)
    changes = {}
    for name, after in desired.items():
        before = snapshot(Path(name))
        old = entries.get(name)
        if name in owned:
            if before != old['after']:
                raise UpdateError('Modified owned file conflict: ' + name)
        elif before['kind'] != 'missing':
            raise UpdateError('Unowned runtime/integration asset conflict: ' + name)
        if before == after:
            continue
        if old is None or old.get('after') == old['before']:
            entries[name] = {'before': before}
            for dest in prefixes:
                if Path(name).is_relative_to(dest):
                    # Recover the repository association from a sibling entry.
                    for sibling, original in owned.items():
                        if original.get('repo') and Path(sibling).is_relative_to(dest):
                            entries[name]['repo'] = original['repo']
                            break
        entries[name]['after'] = after
        changes[name] = {'before': before, 'after': after}
    next_state = {'schema_version': 1, 'commit': revision, 'compatibility': COMPAT,
                  'owned_hashes': {p: digest(e['after']) for p, e in owned_entries(updated, root).items()},
                  'last_transaction': None}
    version_before = snapshot(root / 'VERSION')
    changes[str(root / 'VERSION')] = {'before': version_before, 'after': file_state(('v1 ' + revision + '\n').encode(), version_before.get('mode', 0o600))}
    changes[str(root / 'install-manifest.json')] = {'before': snapshot(root / 'install-manifest.json'), 'after': json_state(updated)}
    changes[str(root / 'runtime-state.json')] = {'before': snapshot(root / 'runtime-state.json'), 'after': json_state(next_state)}
    validate_paths(changes)
    return changes


def journal_path(root, transaction):
    if not isinstance(transaction, str) or not TXID.fullmatch(transaction):
        raise UpdateError('Invalid transaction ID')
    return root / 'updates/transactions' / transaction / 'journal.json'


def load_pending(root):
    marker = read_json(root / 'updates/pending.json')
    journal = read_json(journal_path(root, marker.get('transaction')))
    immutable = {k: v for k, v in journal.items() if k != 'phase'}
    if digest(immutable) != marker.get('journal_hash'):
        raise UpdateError('Pending journal integrity check failed; no files changed')
    if journal.get('schema_version') != 1 or journal.get('transaction') != marker.get('transaction') or journal.get('root') != str(root):
        raise UpdateError('Uncertain pending update journal')
    for name, value in journal.get('recovery_hashes', {}).items():
        helper = root / 'updates/recovery' / name
        private(helper)
        if helper.name != name or digest(snapshot(helper)) != value:
            raise UpdateError('Recovery controller integrity check failed')
    if journal.get('phase') not in {'prepared', 'applying', 'restoring', 'committed', 'recovered'}:
        raise UpdateError('Unsupported update transaction state')
    if not isinstance(journal.get('changes'), dict) or not journal['changes']:
        raise UpdateError('Empty update transaction journal')
    return journal


def finish(root, journal, phase):
    journal['phase'] = phase
    write_json(journal_path(root, journal['transaction']), journal)
    replace(root / 'updates/epoch', file_state(uuid.uuid4().hex.encode()))
    (root / 'updates/pending.json').unlink()
    sync_dir(root / 'updates')


def recover(root):
    journal = load_pending(root)
    quiet(root, journal['repos'])
    changes = journal['changes']
    # Check every preimage before writing even one. Never overwrite uncertain
    # external changes, including changes made after an interrupted rollback.
    validate_paths(changes, recovery=True)
    if journal['phase'] == 'committed':
        if any(snapshot(Path(p)) != e['after'] for p, e in changes.items()):
            raise UpdateError('Committed transaction contents are uncertain')
        finish(root, journal, 'committed')
        return 'Completed committed transaction cleanup'
    journal['phase'] = 'restoring'
    write_json(journal_path(root, journal['transaction']), journal)
    for name, change in reversed(list(changes.items())):
        replace(Path(name), change['before'])
    finish(root, journal, 'recovered')
    return 'Recovered the complete pre-transaction runtime; restart all parent/MCP sessions'


def transact(root, changes, repos, *, kind, from_commit, to_commit, rollback_of=None):
    transaction = uuid.uuid4().hex
    state_change = changes[str(root / 'runtime-state.json')]
    after_state = json.loads(base64.b64decode(state_change['after']['data']))
    after_state['last_transaction'] = transaction
    state_change['after'] = json_state(after_state)
    validate_paths(changes)
    folder = journal_path(root, transaction).parent
    private_directory(folder.parent)
    private_directory(folder)
    recovery = root / 'updates/recovery'
    private_directory(recovery)
    recovery_hashes = {}
    for name in ('runtime_update.py', 'ui_install.py', 'update_gate.py'):
        state = file_state((root / 'scripts' / name).read_bytes())
        replace(recovery / name, state)
        recovery_hashes[name] = digest(state)
    journal = {'schema_version': 1, 'transaction': transaction, 'root': str(root),
               'recovery_hashes': recovery_hashes,
               'kind': kind, 'rollback_of': rollback_of, 'from_commit': from_commit,
               'to_commit': to_commit, 'compatibility': COMPAT, 'repos': repos,
               'phase': 'prepared', 'changes': changes}
    if len(json.dumps(journal).encode()) > MAX_BYTES:
        raise UpdateError('Update journal exceeds recovery bounds; no runtime files changed')
    write_json(folder / 'journal.json', journal)
    sync_dir(folder.parent)
    write_json(root / 'updates/pending.json', {'transaction': transaction, 'journal_hash': digest({k: v for k, v in journal.items() if k != 'phase'})})
    journal['phase'] = 'applying'
    write_json(folder / 'journal.json', journal)
    # Exceptions deliberately leave the marker. Explicit recovery has the same
    # preflight and cannot hide an uncertain partial write as success.
    for name, change in changes.items():
        replace(Path(name), change['after'])
    finish(root, journal, 'committed')
    return transaction


def execute(root, *, source=None, revision=None, rollback=False, recovery=False, dry_run=False):
    root = update_gate.root_path(root)
    private(root / 'updates', True)
    info = (root / '.lifecycle.lock').lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
        raise UpdateError('Unsafe lifecycle lock')
    # EX update → lifecycle; admissions SH update → repo. No nested admission.
    with update_gate.lock(root, exclusive=True), lifecycle_lock(root):
        if update_gate.pending(root):
            if recovery and not dry_run:
                return recover(root)
            raise UpdateError('Pending update must be recovered first: rig update --recover')
        if recovery:
            return 'No pending update; nothing to recover'
        state, manifest = baseline(root)
        repos = repos_for(manifest)
        quiet(root, repos)
        if rollback:
            transaction = state.get('last_transaction')
            if not transaction:
                raise UpdateError('No controller update to roll back; legacy installation is not a rollback snapshot')
            previous = read_json(journal_path(root, transaction))
            if previous.get('kind') == 'rollback' and previous.get('phase') == 'committed':
                return 'Already rolled back; no files changed'
            if previous.get('kind') != 'update' or previous.get('phase') != 'committed' or previous.get('to_commit') != state['commit'] or previous.get('compatibility') != COMPAT:
                raise UpdateError('Previous successful update snapshot is uncertain or incompatible')
            changes = {p: {'before': e['after'], 'after': e['before']} for p, e in previous['changes'].items()}
            validate_paths(changes)
            destination = previous['from_commit']
            kind = 'rollback'
        else:
            if not revision or not SHA.fullmatch(revision):
                raise UpdateError('--revision requires a full 40-character commit SHA')
            changes = plan(root, source, revision, state, manifest)
            destination, kind, transaction = revision, 'update', None
            if revision == state['commit']:
                return 'Requested commit already installed; no files changed'
        if dry_run:
            return f'Compatible {kind}: {state["commit"]} → {destination}; {len(changes)} owned/metadata files; no files changed'
        transact(root, changes, repos, kind=kind, from_commit=state['commit'], to_commit=destination, rollback_of=transaction)
        return f'{kind.capitalize()} complete: {state["commit"]} → {destination}; fully restart parent/MCP sessions before new work'


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument('--revision', metavar='FULL_COMMIT_SHA')
    modes.add_argument('--rollback', action='store_true')
    modes.add_argument('--recover', action='store_true')
    modes.add_argument('--status', action='store_true')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args(argv)
    root = update_gate.root_path()
    try:
        if any(os.environ.get(k) for k in ('RIG_LIVE', 'RIG_JOB_ID', 'RIG_JOB_DIR')):
            raise UpdateError('refuse inside a worker')
        if args.status:
            if update_gate.pending(root):
                journal = load_pending(root)
                print('Pending ' + journal['kind'] + ': ' + journal['phase'] + '; run rig update --recover')
            else:
                state, _ = baseline(root)
                print('Installed commit: ' + state['commit'] + '; no pending update')
            return 0
        if args.recover and args.dry_run:
            raise UpdateError('--recover does not accept --dry-run; inspect --status first')
        # Refuse unsupported installations before any network request.
        if not args.recover:
            baseline(root)
        if args.revision:
            with fetched_source(args.revision) as source:
                result = execute(root, source=source, revision=args.revision, dry_run=args.dry_run)
        else:
            result = execute(root, rollback=args.rollback, recovery=args.recover, dry_run=args.dry_run)
        print(result)
        return 0
    except (OSError, ValueError, TypeError, KeyError, SyntaxError, subprocess.SubprocessError) as error:
        print('rig update: ' + str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
