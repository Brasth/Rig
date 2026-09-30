import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import runtime_update as update
import update_gate
import ui_install

A = 'a' * 40
B = 'b' * 40


def tree(path):
    return {str(p.relative_to(path)): ui_install.snapshot(p) for p in path.rglob('*')
            if p.is_file() or p.is_symlink()}


class SafeUpdate(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.home = self.base / 'home'
        self.root = self.home / '.rig'
        self.repo = self.base / 'repo'
        self.source = self.base / 'source'
        self.home.mkdir()
        self.repo.mkdir()
        self.root.mkdir()
        self.env = patch.dict(os.environ, {'HOME': str(self.home), 'RIG_HOME': str(self.root),
                                         'RIG_SKIP_UPDATE_CHECK': '1'})
        self.env.start()
        self.ps = patch('ui_install.subprocess.check_output', return_value='')
        self.ps.start()
        for name in ('bin/rig', 'scripts/runtime_update.py', 'scripts/update_gate.py',
                     'scripts/ui_install.py', 'scripts/admission.py', 'scripts/rig_mcp.py',
                     'scripts/detect-binaries.sh'):
            dest = self.source / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, dest)
        for name, content in {
            'templates/runtime-compat.json': json.dumps(update.COMPAT),
            'templates/agents-protocol.md': '<!-- rig:start -->\nA guidance\n<!-- rig:end -->\n',
            'skills/delegate-harness/SKILL.md': 'A skill\n',
            'skills/computer-use/SKILL.md': 'A optional skill\n',
            'skills/computer-test/SKILL.md': 'A optional skill\n',
            'scripts/fixture.py': '# A runtime\n',
        }.items():
            dest = self.source / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(content)
        files = update.candidate(self.source)
        manifest = {'version': 1, 'runtime_owned': True, 'entries': {}, 'repos': [str(self.repo)]}
        for name, state in files.items():
            path = self.root / name
            update.replace(path, state)
            manifest['entries'][str(path)] = {'before': {'kind': 'missing'}, 'after': state}
        self.agents = self.repo / 'AGENTS.md'
        before = update.file_state(b'User prefix\r\n\r\nUser suffix\x00\n', 0o644)
        self.agents.write_bytes(b'User prefix\r\n<!-- rig:start -->\nA guidance\n<!-- rig:end -->\r\nUser suffix\x00\n')
        manifest['entries'][str(self.agents)] = {'before': before, 'after': ui_install.snapshot(self.agents), 'repo': str(self.repo)}
        self.skill = self.repo / '.agents/skills/delegate-harness/SKILL.md'
        self.skill.parent.mkdir(parents=True)
        self.skill.write_text('A skill\n')
        manifest['entries'][str(self.skill)] = {'before': {'kind': 'missing'}, 'after': ui_install.snapshot(self.skill), 'repo': str(self.repo)}
        self.manifest = manifest
        update.write_json(self.root / 'install-manifest.json', manifest)
        update.write_json(self.root / 'runtime-state.json', {'schema_version': 1, 'commit': A,
                          'compatibility': update.COMPAT, 'owned_hashes': {p: update.digest(e['after']) for p, e in update.owned_entries(manifest, self.root).items()},
                          'last_transaction': None})
        (self.root / 'updates').mkdir(mode=0o700)
        (self.root / '.update.lock').touch(mode=0o600)
        (self.root / '.lifecycle.lock').touch(mode=0o600)
        (self.root / 'VERSION').write_text('v1 ' + A + '\n')
        (self.source / 'scripts/fixture.py').write_text('# B runtime\n')
        (self.source / 'skills/delegate-harness/SKILL.md').write_text('B skill\n')
        for name in ('computer-use', 'computer-test'):
            (self.source / f'skills/{name}/SKILL.md').write_text('B optional skill\n')
        (self.source / 'templates/agents-protocol.md').write_text('<!-- rig:start -->\nB guidance\n<!-- rig:end -->\n')
        self.data_paths = ['.rig/harness.toml', '.rig/routing.json', '.rig/MEMORY.md',
                           '.rig/billing/receipt.json', '.rig/jobs/old/meta.json',
                           '.rig/reservations/old.json']
        values = ['[project]\nenabled=false\n[workers]\ngrok=false\n', '{}', 'memory\x00',
                  '{"secret":"receipt"}', '{"status":"ok"}',
                  '{"stage":"released","slot_held":false,"needs_reconciliation":false}']
        for name, value in zip(self.data_paths, values):
            path = self.repo / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(value)
        for name in ('browser-skill.json', 'cua-driver.json'):
            (self.root / name).write_text('{"opt_in":false}')
        (self.root / 'scripts/custom.txt').write_text('manual runtime file')
        self.data_before = {p: (self.repo / p).read_bytes() for p in self.data_paths}

    def tearDown(self):
        self.ps.stop()
        self.env.stop()
        self.temp.cleanup()

    def apply(self, **kwargs):
        return update.execute(self.root, source=self.source, revision=B, **kwargs)

    def test_a_b_a_preserves_data_and_original_preimages(self):
        original_agents = self.agents.read_bytes()
        original_manifest = (self.root / 'install-manifest.json').read_bytes()
        result = self.apply()
        self.assertIn('Update complete', result)
        self.assertIn(b'B guidance', self.agents.read_bytes())
        self.assertTrue(self.agents.read_bytes().endswith(b'\r\nUser suffix\x00\n'))
        manifest = update.read_json(self.root / 'install-manifest.json')
        self.assertEqual(manifest['entries'][str(self.agents)]['before'], self.manifest['entries'][str(self.agents)]['before'])
        self.assertIn('Rollback complete', update.execute(self.root, rollback=True))
        self.assertEqual(self.agents.read_bytes(), original_agents)
        self.assertEqual((self.root / 'install-manifest.json').read_bytes(), original_manifest)
        self.assertEqual(update.read_json(self.root / 'runtime-state.json')['commit'], A)
        self.assertEqual(self.data_before, {p: (self.repo / p).read_bytes() for p in self.data_paths})
        self.assertEqual((self.root / 'scripts/custom.txt').read_text(), 'manual runtime file')
        self.assertEqual((self.root / 'browser-skill.json').read_text(), '{"opt_in":false}')
        before = tree(self.root)
        self.assertIn('Already rolled back', update.execute(self.root, rollback=True))
        self.assertEqual(tree(self.root), before)

    def test_dry_run_changes_no_installed_or_project_bytes(self):
        before = tree(self.base)
        self.assertIn('no files changed', self.apply(dry_run=True))
        self.assertEqual(tree(self.base), before)

    def test_unsupported_compatibility_no_writes(self):
        path = self.source / 'templates/runtime-compat.json'
        path.write_text('{"schema_version":99}')
        before = tree(self.base)
        with self.assertRaisesRegex(update.UpdateError, 'Unsupported runtime compatibility'):
            self.apply()
        self.assertEqual(tree(self.base), before)

    def test_modified_owned_file_conflict_no_writes(self):
        (self.root / 'scripts/fixture.py').write_text('# user edits\n')
        before = tree(self.base)
        with self.assertRaisesRegex(update.UpdateError, 'Modified owned file conflict'):
            self.apply()
        self.assertEqual(tree(self.base), before)

    def test_interrupted_copy_blocks_admission_and_restores_preimages(self):
        before_root = tree(self.root)
        before_repo = tree(self.repo)
        real = update.replace
        def crash(path, state):
            if path == self.root / 'scripts/fixture.py':
                raise RuntimeError('simulated interruption')
            return real(path, state)
        with patch.object(update, 'replace', side_effect=crash):
            with self.assertRaisesRegex(RuntimeError, 'simulated interruption'):
                self.apply()
        self.assertTrue(update_gate.pending(self.root))
        with self.assertRaisesRegex(update_gate.UpdateBlocked, 'interrupted'):
            with update_gate.lock(self.root):
                self.fail('admitted')
        self.assertIn('Recovered', update.execute(self.root, recovery=True))
        self.assertFalse(update_gate.pending(self.root))
        after = tree(self.root)
        self.assertEqual({k: after[k] for k in before_root}, before_root)
        self.assertEqual(tree(self.repo), before_repo)
        self.assertIn('nothing to recover', update.execute(self.root, recovery=True))

    def test_recovery_conflict_writes_nothing(self):
        real = update.replace
        def crash(path, state):
            if path == self.root / 'scripts/fixture.py':
                raise RuntimeError('crash')
            return real(path, state)
        with patch.object(update, 'replace', side_effect=crash):
            with self.assertRaises(RuntimeError): self.apply()
        (self.root / 'scripts/fixture.py').write_text('# external edit')
        before = tree(self.base)
        with self.assertRaisesRegex(update.UpdateError, 'Modified owned'):
            update.execute(self.root, recovery=True)
        self.assertEqual(tree(self.base), before)

    def test_journal_permissions_and_hashes(self):
        self.apply()
        for path in (self.root / 'updates').rglob('*'):
            self.assertEqual(path.stat().st_mode & 0o077, 0, path)
        state = update.read_json(self.root / 'runtime-state.json')
        self.assertEqual(state['commit'], B)
        self.assertEqual(state['compatibility'], update.COMPAT)
        self.assertIn(str(self.root / 'scripts/fixture.py'), state['owned_hashes'])
        self.assertEqual((self.root / 'install-manifest.json').stat().st_mode & 0o777, 0o600)

    def test_new_reference_is_owned_and_removed_by_rollback(self):
        path = self.source / 'skills/delegate-harness/references/new.md'
        path.parent.mkdir()
        path.write_text('new reference')
        self.apply()
        dest = self.skill.parent / 'references/new.md'
        self.assertEqual(dest.read_text(), 'new reference')
        manifest = update.read_json(self.root / 'install-manifest.json')
        self.assertEqual(manifest['entries'][str(dest)]['repo'], str(self.repo))
        update.execute(self.root, rollback=True)
        self.assertFalse(dest.exists())

    def test_manual_symlink_and_custom_assets_stay_manual(self):
        link = self.home / '.agents/skills/delegate-harness'
        link.parent.mkdir(parents=True)
        link.symlink_to(self.root / 'skills/delegate-harness', target_is_directory=True)
        state = ui_install.snapshot(link)
        manifest = update.read_json(self.root / 'install-manifest.json')
        manifest['entries'][str(link)] = {'before': state, 'after': state}
        update.write_json(self.root / 'install-manifest.json', manifest)
        self.apply()
        self.assertEqual(ui_install.snapshot(link), state)
        self.assertEqual((link / 'SKILL.md').read_text(), 'B skill\n')
        update.execute(self.root, rollback=True)
        self.assertEqual(ui_install.snapshot(link), state)

    def test_symlink_parent_is_never_followed(self):
        external = self.base / 'external'
        shutil.move(self.root / 'scripts', external)
        (self.root / 'scripts').symlink_to(external, target_is_directory=True)
        before = tree(self.base)
        with self.assertRaisesRegex(update_gate.UpdateBlocked, 'Symlink parent'):
            self.apply()
        self.assertEqual(tree(self.base), before)

    def test_uninitialized_and_disabled_projects_do_not_become_enabled(self):
        harness = self.repo / '.rig/harness.toml'
        disabled = harness.read_bytes()
        self.apply()
        self.assertEqual(harness.read_bytes(), disabled)
        update.execute(self.root, rollback=True)
        harness.unlink()
        self.apply()
        self.assertFalse(harness.exists())

    def test_active_or_unknown_reservation_no_writes(self):
        path = self.repo / '.rig/reservations/old.json'
        for value in ('{"stage":"running","slot_held":true}', '{bad', '{"stage":"released","needs_reconciliation":true}'):
            path.write_text(value)
            before = tree(self.base)
            with self.assertRaises(update.UpdateError): self.apply()
            self.assertEqual(tree(self.base), before)

    def test_active_unconfirmed_missing_job_meta_no_writes(self):
        path = self.repo / '.rig/jobs/old/meta.json'
        for value in ('{"status":"running"}', '{"status":"unknown"}', None):
            if value is None: path.unlink()
            else: path.write_text(value)
            before = tree(self.base)
            with self.assertRaises(update.UpdateError): self.apply()
            self.assertEqual(tree(self.base), before)

    def test_missing_registered_repo_refuses(self):
        shutil.rmtree(self.repo)
        with self.assertRaisesRegex(update.UpdateError, 'repository unavailable'): self.apply()

    def test_active_lease_refuses(self):
        ui_install.register_lease('fixture', self.repo, os.getpid(), rig_home=self.root)
        with self.assertRaisesRegex(update.UpdateError, 'active runtime lease'): self.apply()

    def test_uncertain_lease_and_ps_failure_refuse(self):
        directory = self.root / 'ui/leases'
        directory.mkdir(parents=True)
        (directory / 'bad.json').write_text('[]')
        with self.assertRaisesRegex(update.UpdateError, 'uncertain runtime lease'): self.apply()
        (directory / 'bad.json').unlink()
        with patch('ui_install.subprocess.check_output', side_effect=subprocess.TimeoutExpired('ps', 5)):
            with self.assertRaisesRegex(update.UpdateError, 'process inspection unavailable'): self.apply()

    def test_live_old_process_refuses(self):
        with patch('ui_install.subprocess.check_output', return_value=f'999999 1 python {self.root}/scripts/rig_mcp.py\n'):
            with self.assertRaisesRegex(update.UpdateError, 'active process'): self.apply()

    def test_candidate_is_compiled_never_executed(self):
        (self.source / 'scripts/fixture.py').write_text('raise RuntimeError("candidate must never execute")\n')
        self.apply()

    def test_bad_candidate_syntax_refuses_without_writes(self):
        (self.source / 'scripts/fixture.py').write_text('syntax error @\n')
        before = tree(self.base)
        with self.assertRaises(SyntaxError): self.apply()
        self.assertEqual(tree(self.base), before)

    def test_legacy_admission_allowed_but_update_requires_baseline(self):
        (self.root / 'runtime-state.json').unlink()
        (self.root / '.update.lock').unlink()
        with update_gate.lock(self.root): pass
        with self.assertRaisesRegex(update_gate.UpdateBlocked, 'Legacy/unversioned'):
            self.apply()

    def test_no_update_snapshot_is_not_rollback(self):
        with self.assertRaisesRegex(update.UpdateError, 'No controller update'):
            update.execute(self.root, rollback=True)

    def test_revision_must_be_explicit_full_sha(self):
        for revision in ('main', 'v1', 'abc1234', '', '-x'):
            with self.assertRaises(update.UpdateError):
                update.execute(self.root, source=self.source, revision=revision)

    def test_shared_gate_blocks_update_without_timeout_unlock(self):
        errors = []
        with update_gate.lock(self.root):
            def contender():
                try:
                    with update_gate.lock(self.root, exclusive=True, timeout=.05): errors.append('admitted')
                except update_gate.UpdateBlocked: pass
            thread = threading.Thread(target=contender)
            thread.start(); thread.join(1)
        self.assertFalse(errors)
        self.assertFalse(thread.is_alive())
        with update_gate.lock(self.root, exclusive=True, timeout=.05): pass

    def test_exclusive_gate_blocks_new_admission(self):
        with update_gate.lock(self.root, exclusive=True):
            with self.assertRaisesRegex(update_gate.UpdateBlocked, 'busy'):
                with update_gate.lock(self.root, timeout=.05): pass

    def test_symlink_lock_is_rejected(self):
        lock = self.root / '.update.lock'
        lock.unlink()
        lock.symlink_to(self.root / 'install-manifest.json')
        with self.assertRaises(OSError):
            with update_gate.lock(self.root): pass

    def test_stale_loaded_process_requires_restart_after_rollback(self):
        with patch.object(update_gate, '_LOADED_ROOT', self.root), patch.object(update_gate, '_LOADED_EPOCH', b''), patch.object(update_gate, '_LOADED_PENDING', False):
            self.apply()
            with self.assertRaisesRegex(update_gate.UpdateBlocked, 'restart'):
                with update_gate.lock(self.root): pass
            update.execute(self.root, rollback=True)
            with self.assertRaisesRegex(update_gate.UpdateBlocked, 'restart'):
                ui_install.register_lease('stale', self.repo, os.getpid(), rig_home=self.root)

    def test_same_commit_is_idempotent(self):
        self.apply()
        before = tree(self.base)
        self.assertIn('already installed', self.apply())
        self.assertEqual(tree(self.base), before)

    def test_private_manifest_required(self):
        (self.root / 'install-manifest.json').chmod(0o644)
        with self.assertRaisesRegex(update.UpdateError, 'permissions'): self.apply()


    def test_cli_recovery_uses_saved_controller_before_runtime_sources(self):
        real = update.replace
        def crash(path, state):
            if path == self.root / 'scripts/fixture.py':
                raise RuntimeError('crash')
            return real(path, state)
        with patch.object(update, 'replace', side_effect=crash):
            with self.assertRaises(RuntimeError): self.apply()
        # A normal runtime source can now be either preimage or candidate. Break
        # it to prove the pending CLI does not source it before reaching status.
        detect = self.root / 'scripts/detect-binaries.sh'
        saved = detect.read_bytes()
        detect.write_text('echo UNSAFE_SOURCE_EXECUTED >&2; exit 99\n')
        env = os.environ.copy()
        for key in ('RIG_LIVE', 'RIG_JOB_ID', 'RIG_JOB_DIR'):
            env.pop(key, None)
        cmd = ['bash', str(self.root / 'bin/rig')]
        status = subprocess.run(cmd + ['update', '--status'], env=env, capture_output=True, text=True)
        self.assertEqual(status.returncode, 0, status.stderr)
        self.assertIn('Pending update', status.stdout)
        self.assertNotIn('UNSAFE_SOURCE', status.stderr)
        blocked = subprocess.run(cmd + ['job', 'start', 'new'], env=env, capture_output=True, text=True)
        self.assertNotEqual(blocked.returncode, 0)
        self.assertNotIn('UNSAFE_SOURCE', blocked.stderr)
        detect.write_bytes(saved)
        result = subprocess.run(cmd + ['update', '--recover'], env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Recovered', result.stdout)
        self.assertFalse(update_gate.pending(self.root))

    def test_interrupted_rollback_recovers_b_then_can_retry(self):
        self.apply()
        b_root = tree(self.root)
        b_repo = tree(self.repo)
        real = update.replace
        def crash(path, state):
            if path == self.root / 'scripts/fixture.py':
                raise RuntimeError('rollback crash')
            return real(path, state)
        with patch.object(update, 'replace', side_effect=crash):
            with self.assertRaises(RuntimeError): update.execute(self.root, rollback=True)
        update.execute(self.root, recovery=True)
        after = tree(self.root)
        for name, state in b_root.items():
            if not name.startswith('updates/'):
                self.assertEqual(after[name], state, name)
        self.assertEqual(tree(self.repo), b_repo)
        update.execute(self.root, rollback=True)
        self.assertEqual(update.read_json(self.root / 'runtime-state.json')['commit'], A)

    def test_interrupted_recovery_remains_recoverable(self):
        real = update.replace
        def crash(path, state):
            if path == self.root / 'scripts/fixture.py': raise RuntimeError('crash')
            return real(path, state)
        with patch.object(update, 'replace', side_effect=crash):
            with self.assertRaises(RuntimeError): self.apply()
            with self.assertRaises(RuntimeError): update.execute(self.root, recovery=True)
        self.assertTrue(update_gate.pending(self.root))
        update.execute(self.root, recovery=True)
        self.assertEqual(update.read_json(self.root / 'runtime-state.json')['commit'], A)

    def test_pending_journal_corruption_is_not_accepted(self):
        real = update.replace
        def crash(path, state):
            if path == self.root / 'scripts/fixture.py': raise RuntimeError('crash')
            return real(path, state)
        with patch.object(update, 'replace', side_effect=crash):
            with self.assertRaises(RuntimeError): self.apply()
        marker = update.read_json(self.root / 'updates/pending.json')
        path = update.journal_path(self.root, marker['transaction'])
        journal = update.read_json(path)
        journal['repos'] = []
        update.write_json(path, journal)
        before = tree(self.base)
        with self.assertRaisesRegex(update.UpdateError, 'integrity'):
            update.execute(self.root, recovery=True)
        self.assertEqual(tree(self.base), before)

    def test_commit_cleanup_interruption_keeps_b(self):
        real = update.replace
        def crash(path, state):
            if path == self.root / 'updates/epoch': raise RuntimeError('cleanup crash')
            return real(path, state)
        with patch.object(update, 'replace', side_effect=crash):
            with self.assertRaises(RuntimeError): self.apply()
        self.assertTrue(update_gate.pending(self.root))
        self.assertIn('committed transaction cleanup', update.execute(self.root, recovery=True))
        self.assertEqual(update.read_json(self.root / 'runtime-state.json')['commit'], B)
        update.execute(self.root, rollback=True)

    def test_config_paths_are_preserved_even_when_owned(self):
        paths = [self.home / '.codex/agents/worker.toml', self.home / '.codex/config.toml',
                 self.home / '.agents/plugins/marketplace.json']
        manifest = update.read_json(self.root / 'install-manifest.json')
        for path in paths:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('custom setting must remain')
            manifest['entries'][str(path)] = {'before': {'kind': 'missing'}, 'after': ui_install.snapshot(path)}
        update.write_json(self.root / 'install-manifest.json', manifest)
        self.apply()
        update.execute(self.root, rollback=True)
        for path in paths:
            self.assertEqual(path.read_text(), 'custom setting must remain')

    def test_bootstrap_records_only_clean_matching_full_commit(self):
        (self.source / 'scripts/fixture.py').write_text('# A runtime\n')
        for name in ('computer-use', 'computer-test'):
            (self.source / f'skills/{name}/SKILL.md').write_text('A optional skill\n')
        (self.source / 'skills/delegate-harness/SKILL.md').write_text('A skill\n')
        (self.source / 'templates/agents-protocol.md').write_text('<!-- rig:start -->\nA guidance\n<!-- rig:end -->\n')
        listing = b''
        for name, state in update.candidate(self.source).items():
            data = update.base64.b64decode(state['data'])
            blob = update.hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()
            listing += ('100644 blob ' + blob + '\t' + name).encode() + b'\0'
        with patch.object(update, 'git', side_effect=[A.encode(), b'', listing]):
            self.assertTrue(update.record_install(self.root, self.source, self.manifest))
        self.assertEqual(update.read_json(self.root / 'runtime-state.json')['commit'], A)
        with patch.object(update, 'git', side_effect=[A.encode(), b' M scripts/fixture.py']):
            self.assertFalse(update.record_install(self.root, self.source, self.manifest))
        self.assertFalse((self.root / 'runtime-state.json').exists())


    def test_real_fresh_setup_records_baseline_and_preserves_installed_modes(self):
        # Materialize only tracked source plus current edits in a fresh local Git
        # fixture. No network, provider or actual user installation is involved.
        source = self.base / 'full-source'
        source.mkdir()
        self.ps.stop()
        try:
            names = subprocess.check_output(['git', 'ls-files', '-z'], cwd=ROOT).decode().split('\0')
            for name in names:
                if not name: continue
                path = source / name
                path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(ROOT / name, path)
                path.chmod((ROOT / name).stat().st_mode & 0o777)
            for command in (['git', 'init'], ['git', 'add', '.'],
                            ['git', '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-m', 'fixture']):
                subprocess.run(command, cwd=source, check=True, capture_output=True)
            commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=source, text=True).strip()
            home = self.base / 'fresh-home'; home.mkdir()
            kit = home / '.rig'
            env = {**os.environ, 'HOME': str(home), 'RIG_HOME': str(kit), 'RIG_SRC': str(source),
                   'RIG_SKIP_UPDATE_CHECK': '1', 'RIG_SKIP_MODEL_CATALOG': '1', 'PYTHONDONTWRITEBYTECODE': '1'}
            for key in ('RIG_INSTALL_TRANSACTION', 'RIG_LIFECYCLE_FD', 'RIG_LIVE', 'RIG_JOB_ID', 'RIG_JOB_DIR'):
                env.pop(key, None)
            proc = subprocess.run(['bash', str(source / 'bin/rig'), 'setup', '--no-cua-driver', '--no-browser-skill', '--no-mimo'],
                                  cwd=self.repo, env=env, capture_output=True, text=True, timeout=30)
            self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
            self.assertNotIn('safe updates unavailable', proc.stderr)
            state = update.read_json(kit / 'runtime-state.json')
            self.assertEqual(state['commit'], commit)
            self.assertEqual((kit / 'bin/rig').stat().st_mode & 0o777, 0o711)
            self.assertEqual((kit / 'templates/harness.toml').stat().st_mode & 0o777, 0o600)
            modes = {p: entry['after']['mode'] for p, entry in update.read_json(kit / 'install-manifest.json')['entries'].items()
                     if update.runtime_asset(Path(p), kit) and entry.get('after', {}).get('kind') == 'file'}
            (source / 'scripts/runtime_update.py').write_text((source / 'scripts/runtime_update.py').read_text() + '\n# next revision\n')
            with patch.dict(os.environ, {'HOME': str(home), 'RIG_HOME': str(kit)}), patch('ui_install.subprocess.check_output', return_value=''):
                update.execute(kit, source=source, revision=B)
                for name, mode in modes.items(): self.assertEqual(Path(name).stat().st_mode & 0o777, mode, name)
                update.execute(kit, rollback=True)
                for name, mode in modes.items(): self.assertEqual(Path(name).stat().st_mode & 0o777, mode, name)
        finally:
            self.ps.start()


    def test_metadata_directory_symlink_refuses_without_external_mutation(self):
        outside = self.base / 'manual-external'
        outside.mkdir(mode=0o755)
        (self.root / 'updates/transactions').symlink_to(outside, target_is_directory=True)
        before = tree(self.base)
        mode = outside.stat().st_mode
        with self.assertRaises((update.UpdateError, update_gate.UpdateBlocked)):
            self.apply()
        self.assertEqual(tree(self.base), before)
        self.assertEqual(outside.stat().st_mode, mode)
        self.assertEqual(list(outside.iterdir()), [])

    def test_existing_nonprivate_metadata_directory_is_not_adopted(self):
        folder = self.root / 'updates/transactions'
        folder.mkdir(mode=0o755)
        with self.assertRaisesRegex(update.UpdateError, 'permissions'):
            self.apply()
        self.assertEqual(folder.stat().st_mode & 0o777, 0o755)
        self.assertFalse(list(folder.iterdir()))

    def test_lifecycle_environment_does_not_bypass_another_thread(self):
        entered, release, contender = threading.Event(), threading.Event(), threading.Event()
        errors = []
        def holder():
            try:
                with ui_install.lifecycle_lock(self.root):
                    entered.set(); release.wait(2)
            except BaseException as exc: errors.append(exc)
        def other():
            try:
                with ui_install.lifecycle_lock(self.root): contender.set()
            except BaseException as exc: errors.append(exc)
        first = threading.Thread(target=holder); first.start()
        self.assertTrue(entered.wait(1))
        second = threading.Thread(target=other); second.start()
        self.assertFalse(contender.wait(.1))
        release.set(); first.join(2); second.join(2)
        self.assertTrue(contender.is_set())
        self.assertFalse(errors)

    def test_lease_paused_after_readiness_prevents_update_then_is_detected(self):
        checked, release, finished = threading.Event(), threading.Event(), threading.Event()
        lease_errors, update_errors = [], []
        real = update_gate.assert_ready
        def pause(*args, **kwargs):
            real(*args, **kwargs)
            if threading.current_thread().name == 'lease-fixture' and str(self.root) in getattr(ui_install._lifecycle_held, 'roots', set()):
                checked.set(); release.wait(3)
        def lease():
            try: ui_install.register_lease('race', self.repo, os.getpid(), rig_home=self.root)
            except BaseException as exc: lease_errors.append(exc)
        def updater():
            try: self.apply()
            except BaseException as exc: update_errors.append(exc)
            finally: finished.set()
        before = (self.root / 'scripts/fixture.py').read_bytes()
        with patch.object(update_gate, 'assert_ready', side_effect=pause):
            first = threading.Thread(target=lease, name='lease-fixture'); first.start()
            self.assertTrue(checked.wait(1))
            second = threading.Thread(target=updater); second.start()
            self.assertFalse(finished.wait(.15))
            release.set(); first.join(3); second.join(3)
        self.assertFalse(lease_errors)
        self.assertTrue(update_errors)
        self.assertIn('active runtime lease', str(update_errors[0]))
        self.assertEqual((self.root / 'scripts/fixture.py').read_bytes(), before)

    def test_pending_shell_ui_launch_has_no_side_effect_or_fallback(self):
        import ui_launch
        (self.root / 'ui').mkdir()
        (self.root / 'ui/shell-enabled').write_text('1')
        update.write_json(self.root / 'updates/pending.json', {'transaction': '0' * 32})
        before = tree(self.base)
        with patch.object(ui_launch, 'classify', return_value=self.repo) as classify, \
             patch.object(ui_launch.subprocess, 'run') as run, \
             patch.object(ui_launch.os, 'execvpe') as fallback:
            self.assertEqual(ui_launch.main(['codex', '/fake/codex']), 1)
            classify.assert_not_called(); run.assert_not_called(); fallback.assert_not_called()
        self.assertEqual(tree(self.base), before)


    def test_declined_optional_consent_pinned_update_and_rollback(self):
        consent = {name: (self.root / name).read_bytes() for name in ('cua-driver.json', 'browser-skill.json')}
        self.apply()
        for name in ('computer-use', 'computer-test'):
            self.assertEqual((self.root / f'skills/{name}/SKILL.md').read_text(), 'B optional skill\n')
            self.assertFalse((self.home / f'.agents/skills/{name}').exists())
            self.assertFalse((self.repo / f'.agents/skills/{name}').exists())
        update.execute(self.root, rollback=True)
        for name in ('computer-use', 'computer-test'):
            self.assertEqual((self.root / f'skills/{name}/SKILL.md').read_text(), 'A optional skill\n')
            self.assertFalse((self.home / f'.agents/skills/{name}').exists())
            self.assertFalse((self.repo / f'.agents/skills/{name}').exists())
        self.assertEqual(consent, {name: (self.root / name).read_bytes() for name in consent})

    def test_accepted_optional_consent_pinned_update_and_rollback(self):
        (self.root / 'browser-skill.json').write_text('{"opt_in":true,"saved":"keep exact bytes"}\n')
        consent = {name: (self.root / name).read_bytes() for name in ('cua-driver.json', 'browser-skill.json')}
        manifest = update.read_json(self.root / 'install-manifest.json')
        links = {}
        for name in ('computer-use', 'computer-test'):
            link = self.home / f'.agents/skills/{name}'
            link.parent.mkdir(parents=True, exist_ok=True)
            link.symlink_to(self.root / 'skills' / name, target_is_directory=True)
            links[str(link)] = ui_install.snapshot(link)
            manifest['entries'][str(link)] = {'before': {'kind': 'missing'}, 'after': links[str(link)]}
            path = self.repo / f'.agents/skills/{name}/SKILL.md'
            path.parent.mkdir(parents=True)
            path.write_text('A optional skill\n')
            manifest['entries'][str(path)] = {'before': {'kind': 'missing'}, 'after': ui_install.snapshot(path), 'repo': str(self.repo)}
        update.write_json(self.root / 'install-manifest.json', manifest)
        self.apply()
        for name in ('computer-use', 'computer-test'):
            self.assertEqual((self.home / f'.agents/skills/{name}/SKILL.md').read_text(), 'B optional skill\n')
            self.assertEqual((self.repo / f'.agents/skills/{name}/SKILL.md').read_text(), 'B optional skill\n')
        self.assertEqual(consent, {name: (self.root / name).read_bytes() for name in consent})
        update.execute(self.root, rollback=True)
        for name in ('computer-use', 'computer-test'):
            self.assertEqual((self.home / f'.agents/skills/{name}/SKILL.md').read_text(), 'A optional skill\n')
            self.assertEqual((self.repo / f'.agents/skills/{name}/SKILL.md').read_text(), 'A optional skill\n')
        self.assertEqual(consent, {name: (self.root / name).read_bytes() for name in consent})
        self.assertEqual(links, {path: ui_install.snapshot(Path(path)) for path in links})


    def test_legacy_symlink_without_lock_allows_shared_but_refuses_update(self):
        (self.root / '.update.lock').unlink()
        alias = self.base / 'legacy-runtime-alias'
        alias.symlink_to(self.root, target_is_directory=True)
        before = tree(self.base)
        with update_gate.lock(alias):
            pass
        with self.assertRaisesRegex(update_gate.UpdateBlocked, 'Legacy/unversioned'):
            with update_gate.lock(alias, exclusive=True):
                self.fail('legacy update admitted')
        self.assertEqual(tree(self.base), before)
        self.assertFalse((self.root / '.update.lock').exists())
        self.assertTrue(alias.is_symlink())

    def test_legacy_symlink_pending_without_lock_refuses_admission_and_recovery(self):
        (self.root / '.update.lock').unlink()
        alias = self.base / 'legacy-runtime-alias'
        alias.symlink_to(self.root, target_is_directory=True)
        update.write_json(self.root / 'updates/pending.json', {'transaction': '0' * 32})
        before = tree(self.base)
        with self.assertRaisesRegex(update_gate.UpdateBlocked, 'interrupted'):
            with update_gate.lock(alias):
                self.fail('pending legacy runtime admitted')
        with self.assertRaisesRegex(update_gate.UpdateBlocked, 'Legacy/unversioned'):
            with update_gate.lock(alias, exclusive=True):
                self.fail('legacy update admitted')
        for path in (alias, self.root):
            with self.assertRaises((update_gate.UpdateBlocked, update.UpdateError)):
                update.execute(path, recovery=True)
        self.assertEqual(tree(self.base), before)
        self.assertFalse((self.root / '.update.lock').exists())


if __name__ == '__main__':
    unittest.main()
