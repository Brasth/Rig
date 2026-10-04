"""Real-Git worktree identity, lifecycle, and root detection regressions."""
import concurrent.futures
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import jobs
import worktrees


class Worktrees(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.repo = self.base / 'main repo'
        self.git('init', self.repo)
        self.git('-C', self.repo, '-c', 'user.name=Test', '-c', 'user.email=test@example.com',
                 'commit', '--allow-empty', '-m', 'initial')
        self.linked = self.base / 'linked checkout'
        self.git('-C', self.repo, 'worktree', 'add', '-b', 'feature', self.linked)

    def git(self, *args):
        return subprocess.run(['git', *map(str, args)], check=True, capture_output=True).stdout

    def enabled(self, repo, enabled=True):
        path = repo / '.rig' / 'harness.toml'
        path.parent.mkdir(exist_ok=True)
        path.write_text('[project]\nenabled = ' + str(enabled).lower() + '\n')

    def row(self, root):
        return next(row for row in worktrees.discover(self.repo)['worktrees'] if row['root'] == str(root))

    def test_shell_python_roots_and_local_state(self):
        for root in (self.repo, self.linked):
            nested = root / 'src' / 'nested'
            nested.mkdir(parents=True)
            for cwd in (root, nested):
                shell = subprocess.run(['bash', '-c',
                    f'source {shlex.quote(str(ROOT / "scripts/detect-binaries.sh"))}; repo_root'],
                    cwd=cwd, capture_output=True, text=True, check=True)
                self.assertEqual(shell.stdout.strip(), str(root))
                self.assertEqual(jobs.repo_root(str(cwd)), root)
            self.assertEqual(jobs.jobs_dir(root), root / '.rig' / 'jobs')
        self.assertNotEqual(jobs.jobs_dir(self.repo), jobs.jobs_dir(self.linked))

    def test_discovery_is_read_only_and_ignores_git_redirects(self):
        before = set(self.repo.rglob('*')) | set(self.linked.rglob('*'))
        from unittest import mock
        with mock.patch.dict(os.environ, {'GIT_DIR': '/nonexistent', 'GIT_WORK_TREE': '/nonexistent'}):
            result = worktrees.discover(self.linked)
        self.assertEqual(len(result['worktrees']), 2)
        self.assertIsNone(result['repository_id'])
        self.assertTrue(all(not row['eligible'] for row in result['worktrees']))
        self.assertEqual(before, set(self.repo.rglob('*')) | set(self.linked.rglob('*')))

    def test_register_shared_repo_distinct_ids_and_opt_in(self):
        first = worktrees.register(self.repo)
        second = worktrees.register(self.linked)
        self.assertEqual(first['repository_id'], second['repository_id'])
        self.assertNotEqual(first['worktree_id'], second['worktree_id'])
        self.assertEqual(second, worktrees.register(self.linked))
        self.assertFalse((self.linked / '.rig').exists())
        self.assertFalse(self.row(self.linked)['eligible'])
        self.enabled(self.linked)
        self.assertTrue(self.row(self.linked)['eligible'])
        self.enabled(self.linked, False)
        self.assertFalse(self.row(self.linked)['eligible'])

    def test_move_preserves_identity_but_requires_explicit_refresh(self):
        first = worktrees.register(self.linked)
        moved = self.base / 'moved checkout'
        self.git('-C', self.repo, 'worktree', 'move', self.linked, moved)
        self.enabled(moved)
        row = self.row(moved)
        self.assertTrue(row['stale'])
        self.assertFalse(row['eligible'])
        self.assertEqual(row['worktree_id'], first['worktree_id'])
        self.assertEqual(worktrees.register(moved)['worktree_id'], first['worktree_id'])
        self.assertTrue(self.row(moved)['eligible'])

    def test_removed_registration_retained_and_recreation_gets_new_id(self):
        first = worktrees.register(self.linked)
        self.git('-C', self.repo, 'worktree', 'remove', self.linked)
        result = worktrees.discover(self.repo)
        self.assertEqual(result['unresolved_registrations'][0]['worktree_id'], first['worktree_id'])
        self.git('-C', self.repo, 'worktree', 'add', self.linked, 'feature')
        second = worktrees.register(self.linked)
        self.assertNotEqual(first['worktree_id'], second['worktree_id'])

    def test_detached_locked_and_missing_worktrees(self):
        self.git('-C', self.linked, 'checkout', '--detach')
        self.git('-C', self.repo, 'worktree', 'lock', '--reason', 'keep fixture', self.linked)
        row = self.row(self.linked)
        self.assertTrue(row['detached'])
        self.assertTrue(row['locked'])
        self.assertEqual(row['lock_reason'], 'keep fixture')
        hidden = self.base / 'hidden'
        self.linked.rename(hidden)
        row = self.row(self.linked)
        self.assertFalse(row['available'])
        self.assertFalse(row['eligible'])
        self.assertTrue(row['error'])

    def test_parallel_registration_is_idempotent(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(worktrees.register, [self.linked] * 4))
        self.assertEqual(len({row['worktree_id'] for row in results}), 1)
        self.assertEqual(len(worktrees.discover(self.repo)['worktrees']), 2)

    def test_invalid_registry_fails_without_overwriting(self):
        result = worktrees.register(self.repo)
        path = Path(result['common_dir']) / 'rig' / 'worktrees.json'
        path.write_text('{"version":999}')
        for action in (worktrees.discover, worktrees.register):
            with self.assertRaises(worktrees.WorktreeError):
                action(self.repo)
        self.assertEqual(path.read_text(), '{"version":999}')

    def test_cli_from_uninitialized_nested_worktree(self):
        nested = self.linked / 'src'
        nested.mkdir()
        result = subprocess.run(['bash', str(ROOT / 'bin' / 'rig'), 'worktrees', 'list', '--json'],
                                cwd=nested, capture_output=True, text=True, check=True)
        self.assertEqual(len(json.loads(result.stdout)['worktrees']), 2)
        self.assertFalse((self.linked / '.rig').exists())

    def test_unborn_and_non_git(self):
        empty = self.base / 'empty'
        self.git('init', empty)
        self.assertEqual(worktrees.resolve(empty)['head'], '')
        self.assertEqual(len(worktrees.discover(empty)['worktrees']), 1)
        with self.assertRaises(worktrees.WorktreeError):
            worktrees.resolve(self.base)

    def test_bare_repository_refused(self):
        bare = self.base / 'bare.git'
        self.git('init', '--bare', bare)
        with self.assertRaises(worktrees.WorktreeError):
            worktrees.register(bare)
        self.assertFalse((bare / 'rig').exists())

    def test_corrupt_identity_refused(self):
        context = worktrees.resolve(self.linked)
        path = Path(context['git_dir']) / 'rig-worktree-id'
        path.write_text('broken')
        with self.assertRaises(worktrees.WorktreeError):
            worktrees.register(self.linked)
        self.assertFalse(self.row(self.linked)['eligible'])
        self.assertEqual(path.read_text(), 'broken')
