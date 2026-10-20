#!/usr/bin/env python3
"""Git-backed worktree discovery and explicit identity registration.

This registry grants no execution authority and owns no job reservations.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import uuid

import harness


class WorktreeError(ValueError):
    pass


def _git(repo, *args, allow_failure=False, timeout=15):
    # Caller Git environment must not redirect discovery into another checkout.
    env = {key: value for key, value in os.environ.items() if not key.startswith('GIT_')}
    env['GIT_OPTIONAL_LOCKS'] = '0'
    try:
        result = subprocess.run(['git', '-C', str(repo), *args], env=env,
                                capture_output=True, timeout=timeout)
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


# -- per-job worktrees (Phase 3) ------------------------------------------------------
# The state root (the main checkout's .rig) never moves. A worktree job also has a work root, the
# checkout its worker edits; <job dir>/worktree.json is the only record and the proof Rig made it.
JOB_RECORD = 'worktree.json'
READ_ROLES = {'review', 'reviewer', 'explore', 'explorer', 'verify'}
COMMIT_IDENTITY = ('-c', 'user.name=rig', '-c', 'user.email=rig@localhost', '-c', 'commit.gpgsign=false')


def settings(repo):
    return harness.parse_harness(harness.harness_path(Path(repo)))['worktrees']


def job_branch(job_id):
    return f'rig/{job_id}'


def job_path(repo, job_id):
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]*', str(job_id)):
        raise WorktreeError(f'job id {job_id!r} cannot name a worktree')
    return Path(repo).resolve() / '.rig' / 'worktrees' / job_id


def plan(repo, job_id, role='worker', access='', writer_job='', continues=''):
    """Where a job's worker edits. Isolated only for write jobs when [worktrees] mode is per-job."""
    root = Path(repo).resolve()
    config = settings(root)
    access = access or ('read' if role.lower() in READ_ROLES else 'write')
    isolated = config['mode'] == 'per-job' and access == 'write' and not config['error']
    if isolated and continues:
        # A successor would start from the base without the predecessor's unmerged edits; refuse rather than lose them.
        raise WorktreeError('continuations are not supported in [worktrees] mode = "per-job"; accept or discard the earlier job first')
    path = job_path(root, job_id) if isolated else root
    if not isolated and access == 'read' and writer_job and not config['error']:
        # A reviewer reads the writer's work, which lives in the writer's worktree until it is merged.
        try:
            record = job_record(root / '.rig' / 'jobs' / writer_job)
        except WorktreeError:
            record = None
        if record and not (record.get('merged') or record.get('removed')) and Path(record['path']).is_dir():
            path = Path(record['path'])
    return {'isolated': isolated, 'error': config['error'], 'container': config['container'],
            'path': str(path), 'branch': job_branch(job_id) if isolated else ''}


def job_record(job_dir):
    path = Path(job_dir) / JOB_RECORD
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text())
    except (OSError, ValueError) as error:
        raise WorktreeError(f'worktree record is unreadable: {error}') from error
    if not isinstance(value, dict) or value.get('version') != 1 or not isinstance(value.get('path'), str):
        raise WorktreeError('worktree record is malformed')
    return value


def work_root(repo, job_dir):
    """The checkout whose files a job's evidence, scope and checks refer to."""
    record = job_record(job_dir)
    # Once merged (or nothing to merge) the job's files live in the base checkout again.
    if record and not (record.get('merged') or record.get('nothing_to_merge')):
        return Path(record['path'])
    return Path(repo).resolve()


def _write_record(job_dir, record):
    _atomic_json(Path(job_dir) / JOB_RECORD, record)


def _exclude(common_dir, pattern):
    path = Path(common_dir) / 'info' / 'exclude'
    path.parent.mkdir(exist_ok=True)
    current = path.read_text() if path.exists() else ''
    if pattern not in current.splitlines():
        path.write_text(current + ('' if current.endswith('\n') or not current else '\n') + pattern + '\n')


def create_job(repo, job_dir):
    """Make the job's worktree and branch from the base HEAD. Idempotent for the same job."""
    root = Path(repo).resolve()
    folder = Path(job_dir).resolve()
    job_id = folder.name
    existing = job_record(folder)
    if existing:
        if not Path(existing['path']).is_dir():
            raise WorktreeError(f'worktree {existing["path"]} for job {job_id} is gone; the job cannot continue')
        return existing
    context = resolve(root)
    if not context['head']:
        raise WorktreeError('worktree mode needs at least one commit in the repository')
    base_branch = os.fsdecode(_git(root, 'symbolic-ref', '--quiet', '--short', 'HEAD', allow_failure=True)).strip()
    if not base_branch:
        raise WorktreeError('worktree mode needs the base checkout on a branch, not a detached HEAD')
    path, branch = job_path(root, job_id), job_branch(job_id)
    if path.exists():
        raise WorktreeError(f'{path} already exists and is not this job\'s recorded worktree')
    if _git(root, 'rev-parse', '--verify', '--quiet', f'refs/heads/{branch}', allow_failure=True).strip():
        raise WorktreeError(f'branch {branch} already exists')
    path.parent.mkdir(parents=True, exist_ok=True)
    _exclude(context['common_dir'], '/.rig/worktrees/')
    _git(root, 'worktree', 'add', '-b', branch, str(path), context['head'], timeout=120)
    record = {'version': 1, 'job_id': job_id, 'path': str(path.resolve()), 'branch': branch,
              'base_branch': base_branch, 'base_head': context['head'], 'common_dir': context['common_dir'],
              'created_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
              'container': settings(root)['container'], 'branch_head': '', 'merged': False, 'merge_commit': ''}
    _write_record(folder, record)
    return record


def commit_job(repo, job_dir):
    """Commit what the worker left in its worktree so the merge gate has a commit. Never merges."""
    folder = Path(job_dir).resolve()
    record = job_record(folder)
    if not record:
        raise WorktreeError('this job has no worktree')
    work = record['path']
    _git(work, 'add', '-A', timeout=120)
    if _git(work, 'diff', '--cached', '--name-only').strip():
        _git(work, *COMMIT_IDENTITY, 'commit', '-q', '-m', f'rig job {record["job_id"]}', timeout=120)
    record['branch_head'] = os.fsdecode(_git(work, 'rev-parse', 'HEAD')).strip()
    _write_record(folder, record)
    return record


def _busy(path):
    """PIDs whose working directory is inside path (Linux; elsewhere the caller relies on job state)."""
    proc, found = Path('/proc'), []
    if not proc.is_dir():
        return found
    target = str(Path(path).resolve())
    for entry in proc.iterdir():
        if not entry.name.isdigit() or int(entry.name) == os.getpid():
            continue
        try:
            cwd = os.readlink(entry / 'cwd')
        except OSError:
            continue
        if cwd == target or cwd.startswith(target + os.sep):
            found.append(int(entry.name))
    return found


def remove_job(repo, job_dir, *, discard=False):
    """Remove the worktree Rig made for a job. Never forces, never touches one Rig did not create."""
    root = Path(repo).resolve()
    folder = Path(job_dir).resolve()
    record = job_record(folder)
    if not record:
        raise WorktreeError('this job has no recorded worktree; Rig only removes worktrees it created')
    path = Path(record['path'])
    expected = job_path(root, folder.name)
    if path.resolve() != expected.resolve():
        raise WorktreeError('recorded worktree path is not the job\'s own path; refusing')
    busy = _busy(path) if path.exists() else []
    if busy:
        raise WorktreeError(f'processes are still running in the worktree (pid {", ".join(map(str, busy))})')
    if path.exists():
        if _git(path, 'status', '--porcelain').strip():
            raise WorktreeError('the worktree has uncommitted changes; commit_job first or inspect them')
        _git(root, 'worktree', 'remove', str(path), timeout=60)
    branch = record['branch']
    flag = '-D' if discard else '-d'
    result = subprocess.run(['git', '-C', str(root), 'branch', flag, branch], capture_output=True, text=True,
                            env={k: v for k, v in os.environ.items() if not k.startswith('GIT_')})
    if result.returncode:
        raise WorktreeError(f'worktree removed, but branch {branch} was kept (unmerged work; --discard deletes it)')
    record.update(removed=True)
    _write_record(folder, record)
    return record


def job_status(repo):
    """Every job worktree Rig made, whether it is still there, and the dc-cli containers keyed to them.
    Read-only: it lists what could be cleaned up and never removes anything."""
    root = Path(repo).resolve()
    rows = []
    for folder in sorted((root / '.rig' / 'jobs').glob('*/')):
        try:
            record = job_record(folder)
        except WorktreeError as error:
            rows.append({'job_id': folder.name, 'state': 'unreadable', 'detail': str(error)})
            continue
        if not record:
            continue
        meta = {}
        try:
            meta = json.loads((folder / 'meta.json').read_text())
        except (OSError, ValueError):
            pass
        present = Path(record['path']).is_dir()
        if record.get('merged') or record.get('nothing_to_merge'):
            state = 'merged' if present is False else 'merged, worktree still present'
        elif record.get('removed'):
            state = 'removed without merge'
        else:
            state = 'unmerged' if present else 'unmerged, worktree missing'
        rows.append({'job_id': folder.name, 'state': state, 'branch': record.get('branch', ''), 'present': present,
                     'job_status': meta.get('status', ''), 'path': record['path'], 'created_at': record.get('created_at', ''),
                     'cleanup': ('' if state == 'merged' else f'rig worktrees remove --job {folder.name}')})
    containers = []
    try:
        import dc_client
        if dc_client.dc_binary():
            prefix = str(root / '.rig' / 'worktrees') + '/'
            for item in dc_client.run_json(['ls', '--all', '--json'], root):
                folder = str(item.get('local_folder') or '') if isinstance(item, dict) else ''
                if folder.startswith(prefix):
                    containers.append({'job_id': Path(folder).name, 'name': item.get('name', ''), 'status': item.get('status', ''),
                                       'id': item.get('id', '')})
    except Exception as error:  # dc being absent or down is not a reason to hide the worktrees
        return {'worktrees': rows, 'containers': containers, 'containers_error': str(error)}
    return {'worktrees': rows, 'containers': containers}


def main():
    if len(sys.argv) > 1 and sys.argv[1] in JOB_COMMANDS:
        return job_main(sys.argv[1:])
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


JOB_COMMANDS = ('plan', 'create', 'commit', 'remove', 'status')


def job_main(argv):
    parser = argparse.ArgumentParser(prog='rig worktrees', description='Per-job worktrees')
    parser.add_argument('command', choices=JOB_COMMANDS)
    parser.add_argument('--repo', default=os.getcwd())
    parser.add_argument('--job', default='')
    parser.add_argument('--role', default='worker')
    parser.add_argument('--access', default='')
    parser.add_argument('--writer-job', default='')
    parser.add_argument('--continues', default='')
    parser.add_argument('--discard', action='store_true')
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args(argv)
    try:
        import jobs
        repo = jobs.repo_root(args.repo)
        if args.command != 'status' and not args.job:
            raise WorktreeError('--job is required')
        folder = Path(repo) / '.rig' / 'jobs' / args.job
        if args.command == 'plan':
            result = plan(repo, args.job, args.role, args.access, args.writer_job, args.continues)
            if result['error']:
                raise WorktreeError(result['error'])
        elif args.command == 'create':
            result = create_job(repo, folder)
        elif args.command == 'status':
            result = job_status(repo)
        elif args.command == 'commit':
            result = commit_job(repo, folder)
        else:
            result = remove_job(repo, folder, discard=args.discard)
    except (WorktreeError, ValueError, SystemExit) as error:
        print(f'rig worktrees: {error}', file=sys.stderr)
        return 1
    if args.command == 'status' and not args.json:
        for row in result['worktrees']:
            print(f"{row['job_id']}  {row['state']}  {row.get('branch', '')}  job {row.get('job_status') or '-'}"
                  + (f"  -> {row['cleanup']}" if row.get('cleanup') else ''))
        for box in result['containers']:
            print(f"container  job {box['job_id']}  {box['name']}  {box['status']}  (rig never removes containers; see dc prune)")
        if result.get('containers_error'):
            print('containers unavailable: ' + result['containers_error'])
        if not result['worktrees'] and not result['containers']:
            print('no job worktrees')
        return 0
    print(json.dumps(result))
    return 0


if __name__ == '__main__':
    sys.exit(main())
