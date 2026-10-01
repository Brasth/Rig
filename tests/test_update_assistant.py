"""Offline guided `rig update` tests: local Git remote, no setup, no network."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'tests'))
import runtime_update as update
import update_assistant as assistant
from test_legacy_migration import make_source_repo, quiet_ps, tree

A = 'a' * 40


class LatestFlow(unittest.TestCase):
    """A versioned runtime at A; official main (local fixture) is a newer commit."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name).resolve()
        self.home = self.base / 'home'
        self.root = self.home / '.rig'
        self.remote = self.base / 'remote'
        self.remote.mkdir()
        self.main = make_source_repo(self.remote, marker='B')
        self.home.mkdir()
        self.env = patch.dict(os.environ, {'HOME': str(self.home), 'RIG_HOME': str(self.root)})
        self.env.start()
        for key in ('RIG_LIVE', 'RIG_JOB_ID', 'RIG_JOB_DIR'):
            os.environ.pop(key, None)
        self.patches = [patch('ui_install.subprocess.check_output', side_effect=quiet_ps),
                        patch.object(update, 'OFFICIAL_REPO', str(self.remote))]
        for item in self.patches:
            item.start()
        installed = self.base / 'installed-source'
        shutil.copytree(self.remote, installed, ignore=shutil.ignore_patterns('.git'))
        (installed / 'scripts/fixture.py').write_text('# A runtime\n')
        manifest = {'version': 1, 'runtime_owned': True, 'entries': {}, 'repos': []}
        for name, state in update.candidate(installed).items():
            update.replace(self.root / name, state)
            manifest['entries'][str(self.root / name)] = {'before': {'kind': 'missing'}, 'after': state}
        update.write_json(self.root / 'install-manifest.json', manifest)
        update.write_json(self.root / 'runtime-state.json', {
            'schema_version': 1, 'commit': A, 'compatibility': update.COMPAT, 'last_transaction': None,
            'owned_hashes': {p: update.digest(e['after']) for p, e in update.owned_entries(manifest, self.root).items()}})
        (self.root / 'updates').mkdir(mode=0o700)
        (self.root / '.update.lock').touch(mode=0o600)
        (self.root / '.lifecycle.lock').touch(mode=0o600)
        self.out = []

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.env.stop()
        self.temp.cleanup()

    def run_main(self, *argv, interactive=False, ask=None):
        return assistant.main(list(argv), interactive=interactive, ask=ask or (lambda q: self.fail('unexpected prompt: ' + q)),
                              out=self.out.append)

    @contextmanager
    def offline(self):
        with patch.object(assistant, 'resolve_latest', side_effect=AssertionError('network used')), \
                patch.object(update, 'fetched_source', side_effect=AssertionError('fetch used')):
            yield

    def test_resolve_latest_from_official_main(self):
        self.assertEqual(assistant.resolve_latest(), self.main)

    def test_resolve_latest_refuses_malformed_or_failed_resolution(self):
        cases = {b'': 'exactly one full', b'abc123\trefs/heads/main\n': 'exactly one full',
                 (A + '\trefs/heads/main\n' + A + '\trefs/heads/main\n').encode(): 'exactly one full',
                 (A + '\trefs/heads/other\n').encode(): 'exactly one full'}
        for output, message in cases.items():
            with self.subTest(output), patch.object(update, 'git', return_value=output):
                with self.assertRaisesRegex(update.UpdateError, message):
                    assistant.resolve_latest()
        with patch.object(update, 'git', side_effect=subprocess.CalledProcessError(128, 'git')):
            with self.assertRaisesRegex(update.UpdateError, 'Could not resolve official main'):
                assistant.resolve_latest()
        with patch.object(update, 'git', return_value=(A.upper() + '\trefs/heads/main\n').encode()):
            with self.assertRaises(update.UpdateError):
                assistant.resolve_latest()

    def test_bare_noninteractive_prints_exact_commands_without_network_or_writes(self):
        before = tree(self.base)
        with self.offline():
            self.assertEqual(self.run_main(), 2)
        self.assertEqual(tree(self.base), before)
        text = '\n'.join(self.out)
        for expected in (A, 'rig update --latest --dry-run', 'rig update --latest --yes', 'rig update --revision FULL_COMMIT_SHA'):
            self.assertIn(expected, text)

    def test_latest_noninteractive_needs_dry_run_or_yes(self):
        before = tree(self.base)
        with self.offline():
            self.assertEqual(self.run_main('--latest'), 2)
        self.assertEqual(tree(self.base), before)
        self.assertIn('--latest --yes', '\n'.join(self.out))

    def test_latest_dry_run_pins_one_sha_and_changes_nothing(self):
        before = tree(self.base)
        with patch.object(assistant, 'resolve_latest', wraps=assistant.resolve_latest) as resolve:
            self.assertEqual(self.run_main('--latest', '--dry-run'), 0)
        self.assertEqual(resolve.call_count, 1)
        self.assertEqual(tree(self.base), before)
        text = '\n'.join(self.out)
        self.assertIn(f'Compatible update: {A} → {self.main}', text)
        self.assertIn(f'rig update --revision {self.main}', text)

    def test_latest_yes_applies_through_pinned_controller(self):
        self.assertEqual(self.run_main('--latest', '--yes'), 0)
        self.assertEqual(update.read_json(self.root / 'runtime-state.json')['commit'], self.main)
        self.assertEqual((self.root / 'scripts/fixture.py').read_text(), '# B runtime\n')
        self.assertIn('Update complete', '\n'.join(self.out))

    def test_guided_tty_previews_then_applies_only_after_consent(self):
        before = tree(self.base)
        answers = iter([True, False])
        self.assertEqual(self.run_main(interactive=True, ask=lambda q: next(answers)), 1)
        self.assertEqual(tree(self.base), before)
        self.assertIn('Cancelled; no files changed', self.out)
        self.out.clear()
        self.assertEqual(self.run_main(interactive=True, ask=lambda q: True), 0)
        self.assertEqual(update.read_json(self.root / 'runtime-state.json')['commit'], self.main)

    def test_already_at_main_does_not_fetch(self):
        state = update.read_json(self.root / 'runtime-state.json')
        state['commit'] = self.main
        update.write_json(self.root / 'runtime-state.json', state)
        with patch.object(update, 'fetched_source', side_effect=AssertionError('fetch used')):
            self.assertEqual(self.run_main('--latest', '--yes'), 0)
        self.assertIn('Already at official main; no files changed', self.out)

    def test_latest_on_legacy_gives_runnable_migration_command(self):
        (self.root / 'runtime-state.json').unlink()
        with self.offline():
            self.assertEqual(self.run_main('--latest'), 2)
        text = '\n'.join(self.out)
        self.assertIn(str(ROOT / 'bin/rig') + ' update --migrate --latest --dry-run', text)

    def test_noninteractive_migrate_prints_preview_and_apply_commands(self):
        (self.root / 'runtime-state.json').unlink()
        before = tree(self.base)
        with self.offline():
            self.assertEqual(self.run_main('--migrate', '--latest'), 2)
        self.assertEqual(tree(self.base), before)
        text = '\n'.join(self.out)
        self.assertIn('update --migrate --latest --dry-run', text)
        self.assertIn('update --migrate --latest --yes', text)

    def test_argument_errors(self):
        for argv in (['--migrate'], ['--migrate', '--latest', '--revision', A], ['--project', '/x'],
                     ['--revision', A, '--yes'], ['--latest', '--rollback'], ['--dry-run'],
                     ['--migrate', '--latest', '--restore-migration', '/x']):
            with self.subTest(argv), self.assertRaises(SystemExit) as raised, \
                    patch('sys.stderr'):
                assistant.parse(argv)
            self.assertEqual(raised.exception.code, 2)

    def test_worker_refused(self):
        with patch.dict(os.environ, {'RIG_JOB_ID': 'job'}), self.offline(), patch('sys.stderr'):
            self.assertEqual(self.run_main('--latest', '--yes'), 1)


class CliRouting(unittest.TestCase):
    """bin/rig from this checkout bootstraps a legacy RIG_HOME without its flags."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name).resolve()
        self.home = self.base / 'home'
        self.root = self.home / '.rig'
        (self.root / 'scripts').mkdir(parents=True)
        # An old installed controller without the guided flags.
        (self.root / 'scripts/runtime_update.py').write_text('import sys\nprint("old controller", sys.argv[1:])\n')

    def tearDown(self):
        self.temp.cleanup()

    def rig(self, *args):
        env = {k: v for k, v in os.environ.items()
               if k not in ('RIG_INSTALL_TRANSACTION', 'RIG_LIFECYCLE_FD', 'RIG_LIVE', 'RIG_JOB_ID', 'RIG_JOB_DIR', 'RIG_SRC')}
        env.update(HOME=str(self.home), RIG_HOME=str(self.root), RIG_SKIP_UPDATE_CHECK='1', PYTHONDONTWRITEBYTECODE='1')
        return subprocess.run(['bash', str(ROOT / 'bin/rig'), 'update', *args], env=env, cwd=self.base,
                              stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60)

    def test_bare_update_without_tty_is_actionable_and_changes_nothing(self):
        before = tree(self.base)
        proc = self.rig()
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertIn(str(ROOT / 'bin/rig') + ' update --migrate --latest --dry-run', proc.stdout)
        self.assertEqual(tree(self.base), before)

    def test_migrate_flags_reach_checkout_helper_not_old_controller(self):
        proc = self.rig('--migrate', '--latest')
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertNotIn('old controller', proc.stdout)
        self.assertIn('--migrate --latest --yes', proc.stdout)
        proc = self.rig('--latest')
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertIn('--migrate --latest --dry-run', proc.stdout)

    def test_classic_flags_keep_installed_controller(self):
        proc = self.rig('--revision', A, '--dry-run')
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn('old controller', proc.stdout)

    def test_pending_bare_update_points_to_recovery(self):
        (self.root / 'updates').mkdir()
        (self.root / 'updates/pending.json').write_text('{}')
        proc = self.rig()
        self.assertEqual(proc.returncode, 1)
        self.assertIn('rig update --recover', proc.stdout)


if __name__ == '__main__':
    unittest.main()
