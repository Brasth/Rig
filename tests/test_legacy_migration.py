"""Offline legacy-bootstrap tests: local Git fixture remote, mocked setup runner."""
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
import legacy_migration as migration
import runtime_update as update
import ui_install
import update_assistant

REAL_CHECK_OUTPUT = subprocess.check_output


def quiet_ps(command, *args, **kwargs):
    """No other process references the fixture runtime; Git still runs."""
    return '' if command[0] == 'ps' else REAL_CHECK_OUTPUT(command, *args, **kwargs)


def failing_ps(command, *args, **kwargs):
    if command[0] == 'ps':
        raise subprocess.TimeoutExpired('ps', 5)
    return REAL_CHECK_OUTPUT(command, *args, **kwargs)


REAL_RUN_STEP = migration.run_step


def full_source_repo(path):
    """This checkout's tracked + new files, committed in a fresh local repo."""
    names = subprocess.check_output(['git', 'ls-files', '-z', '--cached', '--others', '--exclude-standard'],
                                    cwd=ROOT).decode().split('\0')
    for name in names:
        if not name or not (ROOT / name).is_file():
            continue
        dest = path / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, dest)
        dest.chmod((ROOT / name).stat().st_mode & 0o777)
    for command in (['git', 'init', '-q', '-b', 'main'], ['git', 'add', '.'],
                    ['git', '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                     'commit', '-q', '-m', 'fixture']):
        subprocess.run(command, cwd=path, check=True, capture_output=True,
                       env={**os.environ, 'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_CONFIG_NOSYSTEM': '1'})
    return subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=path, text=True).strip()


RUNTIME = ('bin/rig', 'scripts/runtime_update.py', 'scripts/update_gate.py', 'scripts/ui_install.py',
           'scripts/admission.py', 'scripts/rig_mcp.py', 'scripts/detect-binaries.sh')


def tree(path):
    """Every path with lstat state and bytes; links are never followed."""
    out = {}
    for item in sorted(path.rglob('*')):
        state = migration.describe(item)
        state.pop('size', None)
        out[str(item.relative_to(path))] = state
    return out


def make_source_repo(path, marker='A'):
    """A minimal official-like source committed in a local Git repository."""
    for name in RUNTIME:
        dest = path / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, dest)
    for name, content in {
        'templates/runtime-compat.json': json.dumps(update.COMPAT),
        'templates/agents-protocol.md': f'<!-- rig:start -->\n{marker} guidance\n<!-- rig:end -->\n',
        'skills/delegate-harness/SKILL.md': f'{marker} skill\n',
        'scripts/fixture.py': f'# {marker} runtime\n',
    }.items():
        dest = path / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content)
    for command in (['git', 'init', '-q', '-b', 'main'], ['git', 'add', '.'],
                    ['git', '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                     'commit', '-q', '-m', 'fixture']):
        subprocess.run(command, cwd=path, check=True, capture_output=True,
                       env={**os.environ, 'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_CONFIG_NOSYSTEM': '1'})
    return subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=path, text=True).strip()


class MigrationFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name).resolve()
        self.home = self.base / 'home'
        self.old = self.home / '.rig'
        self.new = self.home / '.rig-versioned'
        self.project = self.base / 'project'
        self.remote = self.base / 'remote'
        self.remote.mkdir()
        self.revision = make_source_repo(self.remote)
        # Legacy runtime: no runtime-state, mixed modes, a link, consent files.
        for name, content, mode in (('bin/rig', '#!/bin/bash\necho legacy\n', 0o755),
                                    ('scripts/legacy.py', '# legacy\n', 0o644),
                                    ('skills/delegate-harness/SKILL.md', 'legacy skill\n', 0o644),
                                    ('cua-driver.json', '{"opt_in": true}', 0o644),
                                    ('browser-skill.json', '{"opt_in": false}', 0o600),
                                    ('VERSION', 'v0 1234567\n', 0o644)):
            path = self.old / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
            path.chmod(mode)
        (self.old / 'scripts/alias.py').symlink_to('legacy.py')
        (self.old / 'install-manifest.json').write_text(json.dumps(
            {'version': 1, 'runtime_owned': False, 'entries': {}, 'repos': [str(self.project)]}))
        # Integrations pointing at the legacy runtime.
        launcher = self.home / '.local/bin/rig'
        launcher.parent.mkdir(parents=True)
        launcher.symlink_to(self.old / 'bin/rig')
        self.codex = self.home / '.codex/config.toml'
        self.codex.parent.mkdir(parents=True)
        self.codex.write_text('model = "keep"\n[mcp_servers.rig]\ncommand = "%s"\n' % (self.old / 'scripts/rig-mcp.sh'))
        self.codex.chmod(0o640)
        skill_link = self.home / '.agents/skills/delegate-harness'
        skill_link.parent.mkdir(parents=True)
        skill_link.symlink_to(self.old / 'skills/delegate-harness')
        # Existing, enabled project with history.
        for name, content in {
            '.rig/harness.toml': '[project]\nenabled = true\n[workers]\ngrok = true\nclaude = false\n[queue]\nmax_running = 2\n[routing]\nmode = "smart"\n',
            '.rig/jobs/old/meta.json': '{"status": "ok"}',
            '.rig/MEMORY.md': '# MEMORY\n- fact\n',
            '.rig/reservations/old.json': '{"stage": "released", "slot_held": false, "needs_reconciliation": false}',
            'AGENTS.md': 'mine\n<!-- rig:start -->\nlegacy guidance\n<!-- rig:end -->\n',
            '.agents/skills/delegate-harness/SKILL.md': 'legacy skill\n',
            '.gitignore': '.rig/jobs/\n',
        }.items():
            path = self.project / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        self.env = patch.dict(os.environ, {'HOME': str(self.home), 'RIG_HOME': str(self.old),
                                           'RIG_SKIP_UPDATE_CHECK': '1'})
        self.env.start()
        for key in ('RIG_LIVE', 'RIG_JOB_ID', 'RIG_JOB_DIR', 'RIG_SRC'):
            os.environ.pop(key, None)
        self.patches = [patch('ui_install.subprocess.check_output', side_effect=quiet_ps),
                        patch.object(update, 'OFFICIAL_REPO', str(self.remote)),
                        patch.object(migration, 'run_step', side_effect=self.fake_step)]
        for item in self.patches:
            item.start()
        self.steps = []
        self.record_baseline = True
        self.setup_rc = 0
        self.init_change = None
        self.out = []

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.env.stop()
        self.temp.cleanup()

    def fake_step(self, command, cwd, env):
        """Stands in for the pinned checkout's setup/init; no real setup runs."""
        self.steps.append((command, cwd, dict(env)))
        source = Path(env['RIG_SRC'])
        root = Path(env['RIG_HOME'])
        if command[-2:] == ['setup', '--no-mimo']:
            manifest = {'version': 1, 'runtime_owned': True, 'entries': {}}
            for name, state in update.candidate(source).items():
                update.replace(root / name, state)
                manifest['entries'][str(root / name)] = {'before': {'kind': 'missing'}, 'after': state}
            launcher = self.home / '.local/bin/rig'
            prior = ui_install.snapshot(launcher)
            if not launcher.exists() or launcher.is_symlink():  # real setup leaves a regular file
                launcher.unlink(missing_ok=True)
                launcher.symlink_to(root / 'bin/rig')
            # As ui_install.transaction records it: preimage + after (census if unchanged).
            manifest['entries'][str(launcher)] = {'before': prior, 'after': ui_install.snapshot(launcher)}
            self.codex.write_text('model = "keep"\n[mcp_servers.rig]\ncommand = "%s"\n' % (root / 'scripts/rig-mcp.sh'))
            (self.home / '.cursor').mkdir(exist_ok=True)
            (self.home / '.cursor/mcp.json').write_text('{"new": true}')
            update.write_json(root / 'install-manifest.json', manifest)
            if self.record_baseline:
                self.assertTrue(update.record_install(root, source, manifest))
            return self.setup_rc
        if command[-1] == 'init':
            agents = Path(cwd) / 'AGENTS.md'
            agents.write_text(agents.read_text().replace('legacy guidance', 'A guidance'))
            harness = Path(cwd) / '.rig/harness.toml'
            harness.write_text(self.init_change or harness.read_text() + '[browser-skill]\nenabled = false\n')
            return 0
        raise AssertionError(command)

    def migrate(self, **kwargs):
        options = {'old_root': self.old, 'project': self.project, 'out': self.out.append}
        options.update(kwargs)
        return migration.migrate(self.revision, **options)

    def legacy_tree(self):
        """Legacy root contents; the disclosed empty lock files are checked apart."""
        for name in migration.LOCKS:
            lock = self.old / name
            if lock.exists():
                self.assertEqual((lock.stat().st_size, lock.stat().st_mode & 0o777), (0, 0o600))
        return {k: v for k, v in tree(self.old).items() if k not in migration.LOCKS}

    def migration_dir(self):
        found = list((self.home / '.rig-migrations').glob('*'))
        self.assertEqual(len(found), 1)
        return found[0]


class LegacyMigration(MigrationFixture):
    def test_dry_run_preserves_every_byte_and_previews_concretely(self):
        before = tree(self.base)
        self.assertEqual(self.migrate(dry_run=True), 0)
        self.assertEqual(tree(self.base), before)
        text = '\n'.join(self.out)
        for expected in (self.revision, str(self.new), str(self.old), str(self.codex),
                         str(self.home / '.local/bin/rig'), '--restore-migration', 'cua-driver.json'):
            self.assertIn(expected, text)
        self.assertEqual(self.steps, [])

    def test_noninteractive_without_yes_refuses_without_changes(self):
        before = tree(self.base)
        with self.assertRaisesRegex(update.UpdateError, 'needs --dry-run to preview or --yes'):
            self.migrate(interactive=False)
        self.assertEqual(tree(self.base), before)

    def test_declined_consent_changes_nothing(self):
        before = tree(self.base)
        self.assertEqual(self.migrate(interactive=True, ask=lambda q: False), 1)
        self.assertEqual(tree(self.base), before)
        self.assertEqual(self.steps, [])

    def test_consented_migration_bootstraps_verified_fresh_root(self):
        old_before = self.legacy_tree()
        data_before = {p: tree(self.project / '.rig')[p] for p in ('jobs/old/meta.json', 'MEMORY.md', 'reservations/old.json')}
        harness_before = (self.project / '.rig/harness.toml').read_text()
        self.assertEqual(self.migrate(interactive=True, ask=lambda q: True), 0)
        state = update.read_json(self.new / 'runtime-state.json')
        self.assertEqual(state['commit'], self.revision)
        self.assertEqual(update.baseline(self.new)[0]['commit'], self.revision)
        self.assertEqual(self.legacy_tree(), old_before)
        self.assertEqual(self.new.stat().st_mode & 0o777, 0o700)
        # Consent retained byte-for-byte, privately; nothing else copied.
        self.assertEqual((self.new / 'cua-driver.json').read_text(), '{"opt_in": true}')
        self.assertEqual((self.new / 'browser-skill.json').read_text(), '{"opt_in": false}')
        self.assertEqual((self.new / 'cua-driver.json').stat().st_mode & 0o777, 0o600)
        self.assertFalse((self.new / 'VERSION').exists())
        # Setup ran from the clean pinned checkout with installers skipped.
        (setup, setup_cwd, env), (init, init_cwd, _) = self.steps
        directory = self.migration_dir()
        self.assertEqual(setup, ['bash', str(directory / 'source/bin/rig'), 'setup', '--no-mimo'])
        self.assertEqual(init[-1], 'init')
        self.assertEqual(init_cwd, str(self.project))
        for key in ('RIG_SKIP_CUA_DRIVER', 'RIG_SKIP_BROWSER_SKILL', 'RIG_SKIP_TMUX_INSTALL'):
            self.assertEqual(env[key], '1')
        self.assertEqual(env['RIG_HOME'], str(self.new))
        self.assertNotIn('--no-cua-driver', setup)
        head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=directory / 'source', text=True).strip()
        self.assertEqual(head, self.revision)
        # Project settings/history unchanged; only managed guidance refreshed.
        self.assertTrue((self.project / '.rig/harness.toml').read_text().startswith(harness_before))
        after = tree(self.project / '.rig')
        self.assertEqual({p: after[p] for p in data_before}, data_before)
        journal = migration.read_private_json(directory / 'migration.json')
        self.assertEqual(journal['phase'], 'complete')
        text = '\n'.join(self.out)
        self.assertIn('rig-mcp.sh binds RIG_HOME', text)
        self.assertIn('no shell-profile export required', text)
        # Fixture RIG_HOME points at the legacy root, so migration warns.
        self.assertIn('warning:', text)
        self.assertIn(str(self.old), text)
        self.assertNotIn('export RIG_HOME=' + str(self.new) + ' in your shell profile', text)

    def test_backup_is_private_complete_and_preserves_links_and_modes(self):
        self.assertEqual(self.migrate(assume_yes=True), 0)
        directory = self.migration_dir()
        backup = directory / 'backup'
        for path in [directory, *directory.rglob('*')]:
            if path.is_symlink() or 'source' in path.relative_to(directory).parts[:1]:
                continue
            self.assertEqual(path.stat().st_mode & 0o077, 0, path)
        entries = migration.read_private_json(backup / 'inventory.json')['entries']
        self.assertEqual(entries[str(self.old / 'bin/rig')]['mode'], 0o755)
        self.assertEqual(entries[str(self.codex)]['mode'], 0o640)
        launcher = dict(entries[str(self.home / '.local/bin/rig')])
        self.assertEqual(launcher.pop('parent'), str(self.home / '.local/bin'))
        self.assertEqual(launcher, {'kind': 'link', 'target': str(self.old / 'bin/rig'), 'role': 'restore'})
        copy = migration.tree_path(backup, str(self.old / 'scripts/alias.py'))
        self.assertTrue(copy.is_symlink())
        self.assertEqual(os.readlink(copy), 'legacy.py')
        for name in ('bin/rig', 'install-manifest.json', 'cua-driver.json', 'VERSION'):
            self.assertEqual(migration.tree_path(backup, str(self.old / name)).read_bytes(), (self.old / name).read_bytes())
        for path in (self.project / '.rig/jobs/old/meta.json', self.project / 'AGENTS.md', self.codex,
                     self.project / '.agents/skills/delegate-harness/SKILL.md'):
            self.assertIn(str(path), entries)
        self.assertEqual(entries[str(self.home / '.cursor/mcp.json')]['kind'], 'missing')

    def test_destination_conflict_refuses(self):
        self.new.mkdir()
        before = tree(self.base)
        with self.assertRaisesRegex(update.UpdateError, 'Destination conflict'):
            self.migrate(assume_yes=True)
        self.assertEqual(tree(self.base), before)

    def test_busy_or_uncertain_runtime_refuses_without_changes(self):
        cases = []
        lease = self.old / 'ui/leases/live.json'
        def live():
            lease.parent.mkdir(parents=True)
            lease.write_text(json.dumps({'pid': os.getpid()}))
        def cleanup_live():
            shutil.rmtree(self.old / 'ui')
        cases.append((live, cleanup_live, 'active runtime lease'))
        reservation = self.project / '.rig/reservations/new.json'
        cases.append((lambda: reservation.write_text('{"stage": "running"}'), reservation.unlink, 'reservations'))
        job = self.project / '.rig/jobs/live/meta.json'
        def running():
            job.parent.mkdir()
            job.write_text('{"status": "running"}')
        cases.append((running, lambda: shutil.rmtree(job.parent), 'active or unconfirmed jobs'))
        for arrange, cleanup, message in cases:
            arrange()
            before = tree(self.base)
            with self.subTest(message), self.assertRaisesRegex(update.UpdateError, message):
                self.migrate(assume_yes=True)
            self.assertEqual(tree(self.base), before)
            cleanup()
        with patch('ui_install.subprocess.check_output', side_effect=failing_ps):
            with self.assertRaisesRegex(update.UpdateError, 'process inspection unavailable'):
                self.migrate(assume_yes=True)
        self.assertFalse(self.new.exists())

    def test_disabled_versioned_or_pending_roots_refuse(self):
        harness = self.project / '.rig/harness.toml'
        original = harness.read_text()
        harness.write_text(original.replace('enabled = true', 'enabled = false'))
        with self.assertRaisesRegex(update.UpdateError, 'disabled'):
            self.migrate(assume_yes=True)
        harness.write_text(original)
        (self.old / 'updates').mkdir()
        (self.old / 'updates/pending.json').write_text('{}')
        with self.assertRaisesRegex(update.UpdateError, 'Pending runtime update'):
            self.migrate(assume_yes=True)
        (self.old / 'updates/pending.json').unlink()
        (self.old / 'runtime-state.json').write_text('{}')
        with self.assertRaisesRegex(update.UpdateError, 'already has a safe-update baseline'):
            self.migrate(assume_yes=True)
        self.assertFalse(self.new.exists())

    def test_unknown_revision_fails_before_any_write(self):
        before = tree(self.base)
        with self.assertRaises(subprocess.CalledProcessError):
            migration.migrate('c' * 40, old_root=self.old, assume_yes=True, out=self.out.append)
        with self.assertRaisesRegex(update.UpdateError, 'full 40-character'):
            migration.migrate('abc', old_root=self.old, assume_yes=True, out=self.out.append)
        self.assertEqual(tree(self.base), before)

    def test_baseline_failure_preserves_backup_and_restore_reverts_integrations(self):
        self.record_baseline = False
        integrations = {p: migration.describe(p) for p in (self.home / '.local/bin/rig', self.codex)}
        guidance = (self.project / 'AGENTS.md').read_bytes()
        old_before = self.legacy_tree()
        with self.assertRaisesRegex(update.UpdateError, 'migration incomplete'):
            self.migrate(assume_yes=True)
        directory = self.migration_dir()
        text = '\n'.join(self.out)
        self.assertIn('Legacy/unversioned', text)  # baseline() refusal reason
        self.assertIn(migration.restore_command(directory), text)
        self.assertEqual(migration.read_private_json(directory / 'migration.json')['phase'], 'failed')
        self.assertTrue((directory / 'backup/inventory.json').is_file())
        self.assertNotEqual(migration.describe(self.codex), integrations[self.codex])
        # A second attempt names the interrupted migration's restore command.
        with self.assertRaisesRegex(update.UpdateError, 'interrupted migration found.*--restore-migration'):
            self.migrate(assume_yes=True)
        before_preview = tree(self.base)
        self.assertEqual(migration.restore(directory, dry_run=True, out=self.out.append), 0)
        self.assertEqual(tree(self.base), before_preview)
        with self.assertRaisesRegex(update.UpdateError, 'needs --dry-run to preview or --yes'):
            migration.restore(directory, out=self.out.append)
        self.assertEqual(migration.restore(directory, assume_yes=True, out=self.out.append), 0)
        for path, state in integrations.items():
            self.assertEqual(migration.describe(path), state)
        self.assertFalse((self.home / '.cursor/mcp.json').exists())
        self.assertEqual((self.project / 'AGENTS.md').read_bytes(), guidance)
        self.assertEqual(self.legacy_tree(), old_before)
        self.assertTrue(self.new.is_dir())  # left for inspection, not deleted
        self.assertIn('Already restored', self.restore_again(directory))

    def restore_again(self, directory):
        lines = []
        migration.restore(directory, assume_yes=True, out=lines.append)
        return '\n'.join(lines)

    def test_setup_failure_and_project_setting_change_are_not_success(self):
        self.setup_rc = 3
        with self.assertRaisesRegex(update.UpdateError, 'migration incomplete'):
            self.migrate(assume_yes=True)
        self.assertIn('pinned setup failed', '\n'.join(self.out))

    def test_init_changing_worker_flags_fails_verification(self):
        self.init_change = (self.project / '.rig/harness.toml').read_text().replace('grok = true', 'grok = false')
        with self.assertRaisesRegex(update.UpdateError, 'migration incomplete'):
            self.migrate(assume_yes=True)
        self.assertIn('changed existing harness configuration', '\n'.join(self.out))
        directory = self.migration_dir()
        self.assertEqual(migration.restore(directory, assume_yes=True, out=self.out.append), 0)
        self.assertIn('grok = true', (self.project / '.rig/harness.toml').read_text())

    def test_interrupted_backup_reports_nothing_changed(self):
        real = migration.copy_private
        calls = []
        def crash(source, dest):
            calls.append(source)
            if len(calls) == 3:
                raise OSError('disk full')
            return real(source, dest)
        def outside():
            return {k: v for k, v in tree(self.base).items()
                    if '.rig-migrations' not in k and Path(k).name not in migration.LOCKS}
        before_home = outside()
        with patch.object(migration, 'copy_private', side_effect=crash):
            with self.assertRaisesRegex(update.UpdateError, 'Not migrated .*disk full.*except empty legacy lock files'):
                self.migrate(assume_yes=True)
        self.assertEqual(outside(), before_home)
        self.legacy_tree()
        self.assertFalse(self.new.exists())
        lines = []
        self.assertEqual(migration.restore(self.migration_dir(), out=lines.append), 0)
        self.assertIn('nothing to restore', '\n'.join(lines))

    def test_regular_file_launcher_is_rebound_to_new_root(self):
        launcher = self.home / '.local/bin/rig'
        launcher.unlink()
        launcher.write_text('#!/bin/sh\nexec %s "$@"\n' % (self.old / 'bin/rig'))
        launcher.chmod(0o755)
        original = migration.describe(launcher)
        self.assertEqual(self.migrate(assume_yes=True), 0)
        text = launcher.read_text()
        self.assertIn('export RIG_HOME=' + str(self.new), text)
        self.assertIn('exec ' + str(self.new / 'bin/rig'), text)
        self.assertEqual(launcher.stat().st_mode & 0o777, 0o700)
        self.assertEqual(update.baseline(self.new)[0]['commit'], self.revision)
        entry = update.read_json(self.new / 'install-manifest.json')['entries'][str(launcher)]
        self.assertEqual(entry['after'], ui_install.snapshot(launcher))
        self.assertEqual(entry['before']['kind'], 'file')
        self.assertIn(str(self.old / 'bin/rig'), __import__('base64').b64decode(entry['before']['data']).decode())
        directory = self.migration_dir()
        entries = migration.read_private_json(directory / 'backup/inventory.json')['entries']
        self.assertEqual(entries[str(launcher)]['sha256'], original['sha256'])
        self.assertEqual(migration.restore(directory, assume_yes=True, out=self.out.append), 0)
        self.assertEqual(migration.describe(launcher), original)

    def test_symlink_launcher_is_also_bound(self):
        launcher = self.home / '.local/bin/rig'
        self.assertEqual(self.migrate(assume_yes=True), 0)
        self.assertIn('export RIG_HOME=', launcher.read_text())
        entry = update.read_json(self.new / 'install-manifest.json')['entries'][str(launcher)]
        self.assertEqual(entry['after'], ui_install.snapshot(launcher))
        self.assertEqual(entry['before'], {'kind': 'link', 'target': str(self.old / 'bin/rig')})
        self.assertEqual(update.baseline(self.new)[0]['commit'], self.revision)

    def test_real_setup_and_init_from_clean_offline_checkout(self):
        """Actual pinned setup/init in a fake HOME; only the Git transport is local."""
        remote = self.base / 'full-remote'
        remote.mkdir()
        revision = full_source_repo(remote)
        (self.project / '.git').mkdir()
        launcher = self.home / '.local/bin/rig'
        harness_before = (self.project / '.rig/harness.toml').read_text()
        old_before = self.legacy_tree()
        with patch.object(update, 'OFFICIAL_REPO', str(remote)), \
                patch.object(migration, 'run_step', side_effect=REAL_RUN_STEP):
            code = migration.migrate(revision, old_root=self.old, project=self.project, assume_yes=True,
                                     out=self.out.append)
        self.assertEqual(code, 0, '\n'.join(self.out))
        state, manifest = update.baseline(self.new)
        self.assertEqual(state['commit'], revision)
        self.assertEqual((self.new / 'bin/rig').stat().st_mode & 0o700, 0o700)
        self.assertEqual(manifest['entries'][str(launcher)]['after'], ui_install.snapshot(launcher))
        self.assertEqual(manifest['entries'][str(launcher)]['before'],
                         {'kind': 'link', 'target': str(self.old / 'bin/rig')})
        self.assertIn(str(self.project), manifest.get('repos', []))
        self.assertTrue((self.project / '.rig/harness.toml').read_text().startswith(harness_before.split('[queue]')[0]))
        self.assertIn('<!-- rig:start -->', (self.project / 'AGENTS.md').read_text())
        self.assertNotIn('legacy guidance', (self.project / 'AGENTS.md').read_text())
        self.assertEqual(self.legacy_tree(), old_before)
        self.assertEqual((self.new / 'cua-driver.json').read_text(), '{"opt_in": true}')
        proc = subprocess.run(['bash', str(launcher), 'update', '--status'], capture_output=True, text=True,
                              env={k: v for k, v in os.environ.items() if k != 'RIG_HOME'}, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn('Installed commit: ' + revision, proc.stdout)

    def test_directory_launcher_conflict_fails_with_restore(self):
        launcher = self.home / '.local/bin/rig'
        launcher.unlink()
        launcher.mkdir()
        with self.assertRaisesRegex(update.UpdateError, 'migration incomplete'):
            self.migrate(assume_yes=True)
        self.assertIn('Launcher conflict', '\n'.join(self.out))
        self.assertTrue(launcher.is_dir())

    def test_lock_creation_is_disclosed_and_busy_gate_refuses(self):
        self.assertEqual(self.migrate(dry_run=True), 0)
        self.assertIn('creates empty .update.lock, .lifecycle.lock', '\n'.join(self.out))
        self.assertFalse((self.old / '.update.lock').exists())
        gate = self.old / '.update.lock'
        gate.touch(mode=0o600)
        import fcntl
        with open(gate) as held:
            fcntl.flock(held, fcntl.LOCK_SH)  # a gate-aware legacy admission in progress
            with self.assertRaisesRegex(update.UpdateError, 'Not migrated .*busy'):
                self.migrate(assume_yes=True)
        self.assertFalse(self.new.exists())
        self.assertEqual(self.steps, [])
        gate.chmod(0o644)
        with self.assertRaisesRegex(update.UpdateError, 'Unsafe update lock'):
            self.migrate(assume_yes=True)

    def test_restore_never_silently_undoes_later_edits(self):
        self.assertEqual(self.migrate(assume_yes=True), 0)
        directory = self.migration_dir()
        self.codex.write_text('model = "user edit after migration"\n')
        before = tree(self.base)
        with self.assertRaisesRegex(update.UpdateError, 'Restore refused; no files changed.*--force'):
            migration.restore(directory, assume_yes=True, out=self.out.append)
        self.assertEqual(tree(self.base), before)
        self.assertIn('needs --force ' + str(self.codex) + ': changed after migration', '\n'.join(self.out))
        self.assertEqual(migration.restore(directory, assume_yes=True, force=True, out=self.out.append), 0)
        self.assertIn('[mcp_servers.rig]', self.codex.read_text())
        self.assertIn(str(self.old), self.codex.read_text())

    def test_restore_without_recorded_result_needs_force(self):
        self.assertEqual(self.migrate(assume_yes=True), 0)
        directory = self.migration_dir()
        (directory / 'after.json').unlink()  # as after a hard crash mid-setup
        with self.assertRaisesRegex(update.UpdateError, 'Restore refused'):
            migration.restore(directory, assume_yes=True, out=self.out.append)
        self.assertIn('no recorded migration result (interrupted)', '\n'.join(self.out))
        self.assertEqual(migration.restore(directory, assume_yes=True, force=True, out=self.out.append), 0)
        self.assertTrue((self.home / '.local/bin/rig').is_symlink())

    def test_restore_refuses_redirected_parent_even_with_force(self):
        self.assertEqual(self.migrate(assume_yes=True), 0)
        directory = self.migration_dir()
        elsewhere = self.base / 'dotfiles-codex'
        (self.home / '.codex').rename(elsewhere)
        (self.home / '.codex').symlink_to(elsewhere)
        before = tree(self.base)
        with self.assertRaisesRegex(update.UpdateError, 'Restore refused'):
            migration.restore(directory, assume_yes=True, force=True, out=self.out.append)
        self.assertIn('parent directory now resolves elsewhere', '\n'.join(self.out))
        self.assertEqual(tree(self.base), before)

    def test_guided_legacy_update_reaches_bootstrap(self):
        """Bare TTY `rig update` on a legacy root resolves main and runs setup."""
        cwd = os.getcwd()
        os.chdir(self.project)
        try:
            questions = []
            def ask(question):
                questions.append(question)
                return True
            code = update_assistant.main([], interactive=True, ask=ask, out=self.out.append)
        finally:
            os.chdir(cwd)
        self.assertEqual(code, 0, '\n'.join(self.out))
        self.assertEqual(update.read_json(self.new / 'runtime-state.json')['commit'], self.revision)
        self.assertEqual([step[0][-1] for step in self.steps], ['--no-mimo', 'init'])
        self.assertTrue(any(str(self.project) in q for q in questions))


if __name__ == '__main__':
    unittest.main()
