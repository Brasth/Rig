#!/usr/bin/env python3
"""Git-backed worktree discovery and explicit identity registration.

This registry grants no execution authority and owns no job reservations.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import uuid

import harness


class WorktreeError(ValueError):
    pass


def _git(repo, *args, allow_failure=False):
    # Caller Git environment must not redirect discovery into another checkout.
    env = {key: value for key, value in os.environ.items() if not key.startswith('GIT_')}
    env['GIT_OPTIONAL_LOCKS'] = '0'
    try:
        result = subprocess.run(['git', '-C', str(repo), *args], env=env,
                                capture_output=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise WorktreeError(f'Git discovery failed: {error}') from error
    if result.returncode and allow_failure:
        return b''
    if result.returncode:
        raise WorktreeError(os.fsdecode(result.stderr).strip() or 'Git discovery failed')
    return result.stdout


def resolve(repo):
    """Resolve a real checkout, including nested paths; bare repositories fail."""
    root = Path(os.fsdecode(_git(repo, 'rev-parse', '--show-toplevel')).rstrip('\n')).resolve()
    git_dir = Path(os.fsdecode(_git(root, 'rev-parse', '--absolute-git-dir')).rstrip('\n')).resolve()
    common = Path(os.fsdecode(_git(root, 'rev-parse', '--path-format=absolute', '--git-common-dir')).rstrip('\n')).resolve()
    head = os.fsdecode(_git(root, 'rev-parse', '--verify', 'HEAD', allow_failure=True)).strip()
    return {'root': str(root), 'git_dir': str(git_dir), 'common_dir': str(common), 'head': head}


def _registry(common):
    path = Path(common) / 'rig' / 'worktrees.json'
    if not path.exists():
        return {'version': 1, 'repository_id': None, 'worktrees': {}}
    try:
        value = json.loads(path.read_text())
        if (value.get('version') != 1 or not isinstance(value.get('repository_id'), str)
                or not isinstance(value.get('worktrees'), dict)):
            raise ValueError('unsupported registry schema')
        uuid.UUID(value['repository_id'])
        for identity, row in value['worktrees'].items():
            uuid.UUID(identity)
            if not isinstance(row, dict) or any(not isinstance(row.get(key), str)
                                                for key in ('root', 'git_dir')):
                raise ValueError('invalid worktree record')
        return value
    except (OSError, ValueError, TypeError, AttributeError) as error:
        raise WorktreeError(f'Invalid worktree registry: {error}') from error


def _identity(git_dir):
    path = Path(git_dir) / 'rig-worktree-id'
    try:
        identity = path.read_text().strip()
        uuid.UUID(identity)
        return identity
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as error:
        raise WorktreeError(f'Invalid worktree identity: {error}') from error


def discover(repo):
    context = resolve(repo)
    registry = _registry(context['common_dir'])
    rows = []
    seen = set()
    raw = _git(context['root'], 'worktree', 'list', '--porcelain', '-z')
    for block in raw.split(b'\0\0'):
        if not block:
            continue
        fields = {}
        for entry in block.split(b'\0'):
            if entry:
                key, _, value = entry.partition(b' ')
                fields[os.fsdecode(key)] = os.fsdecode(value)
        if 'worktree' not in fields:
            raise WorktreeError('Malformed Git worktree listing')
        root = Path(fields['worktree']).resolve()
        row = {'root': str(root), 'head': fields.get('HEAD', ''),
               'branch': fields.get('branch', ''), 'detached': 'detached' in fields,
               'locked': 'locked' in fields, 'lock_reason': fields.get('locked', ''),
               'prunable': 'prunable' in fields, 'prune_reason': fields.get('prunable', ''),
               'available': False, 'registered': False, 'stale': False,
               'worktree_id': None, 'git_dir': None, 'error': ''}
        try:
            actual = resolve(root)
            if actual['common_dir'] != context['common_dir']:
                raise WorktreeError('Worktree repository identity changed')
            row['git_dir'] = actual['git_dir']
            identity = _identity(actual['git_dir'])
            record = registry['worktrees'].get(identity)
            row.update(available=True, worktree_id=identity, registered=record is not None)
            if record:
                seen.add(identity)
                row['stale'] = record['root'] != str(root) or record['git_dir'] != actual['git_dir']
        except WorktreeError as error:
            row['error'] = str(error)
        project = harness.parse_harness(root / '.rig' / 'harness.toml')['project']
        row['project_state'] = project['state']
        row['project_error'] = project['error']
        row['eligible'] = bool(row['available'] and row['registered'] and not row['stale']
                               and not row['prunable'] and project['enabled'])
        rows.append(row)
    # Retain unavailable registrations instead of silently forgetting identities.
    stale = [{'worktree_id': identity, **record} for identity, record in registry['worktrees'].items()
             if identity not in seen]
    return {'version': 1, 'repository_id': registry['repository_id'],
            'common_dir': context['common_dir'], 'worktrees': rows, 'unresolved_registrations': stale}


def _atomic_json(path, value):
    fd, name = tempfile.mkstemp(prefix='.worktrees-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def register(repo):
    context = resolve(repo)
    folder = Path(context['common_dir']) / 'rig'
    folder.mkdir(exist_ok=True)
    with (folder / 'worktrees.lock').open('a+') as lock:
        deadline = time.monotonic() + 10
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise WorktreeError('Worktree registry busy; retry shortly')
                time.sleep(0.02)
        # Re-resolve under lock so changed checkout metadata is not registered.
        actual = resolve(repo)
        if actual != context:
            raise WorktreeError('Checkout changed during registration; retry')
        registry = _registry(context['common_dir'])
        identity = _identity(context['git_dir'])
        if identity is None:
            identity = str(uuid.uuid4())
            path = Path(context['git_dir']) / 'rig-worktree-id'
            with path.open('x') as stream:
                stream.write(identity + '\n')
                stream.flush()
                os.fsync(stream.fileno())
        old = registry['worktrees'].get(identity)
        if old and old['git_dir'] != context['git_dir']:
            raise WorktreeError('Worktree identity is already bound to another Git directory')
        registry['repository_id'] = registry['repository_id'] or str(uuid.uuid4())
        registry['worktrees'][identity] = {key: context[key] for key in ('root', 'git_dir')}
        _atomic_json(folder / 'worktrees.json', registry)
        return {'repository_id': registry['repository_id'], 'worktree_id': identity, **context}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('list', 'register'))
    parser.add_argument('--repo', default=os.getcwd())
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args()
    try:
        result = register(args.repo) if args.command == 'register' else discover(args.repo)
    except WorktreeError as error:
        parser.exit(1, f'rig worktrees: {error}\n')
    if args.json:
        print(json.dumps(result, indent=2))
    elif args.command == 'register':
        print(f"Registered {result['worktree_id']}  {result['root']}")
    else:
        for row in result['worktrees']:
            state = 'eligible' if row['eligible'] else ('stale' if row['stale'] else row['project_state'])
            print(f"{row['worktree_id'] or 'unregistered'}  {state}  {row['branch'] or '(detached)'}  {row['root']}")
            if row['error']:
                print(f"  {row['error']}")
        for row in result['unresolved_registrations']:
            print(f"{row['worktree_id']}  unresolved registration  {row['root']}")


if __name__ == '__main__':
    main()
