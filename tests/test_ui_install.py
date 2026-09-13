import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import ui_install

class OwnershipTests(unittest.TestCase):
    def test_original_preimage_survives_updates(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / 'runtime'
            target = Path(d) / 'setting'
            target.write_text('original')
            m = ui_install.Manifest(root)
            m.before([target]); target.write_text('version1'); m.after([target])
            m = ui_install.Manifest(root)
            m.before([target]); target.write_text('version2'); m.after([target])
            ui_install.uninstall(root)
            self.assertEqual(target.read_text(), 'original')

    def test_modified_settings_and_runtime_preserved(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / 'runtime'; root.mkdir(); (root / 'scripts').mkdir()
            target = Path(d) / 'setting'; runtime = root / 'worker.py'
            m = ui_install.Manifest(root); m.before([target, runtime])
            target.write_text('installed'); runtime.write_text('worker'); m.after([target, runtime])
            target.write_text('user')
            ui_install.uninstall(root)
            self.assertEqual(target.read_text(), 'user')
            self.assertTrue(runtime.exists())

    def test_dry_run_keeps_installed_file(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / 'runtime'; target = Path(d) / 'link'
            m = ui_install.Manifest(root); m.before([target]); target.symlink_to('/old'); m.after([target])
            ui_install.uninstall(root, dry_run=True)
            self.assertTrue(target.is_symlink())
            ui_install.uninstall(root)
            self.assertFalse(target.is_symlink())

    def test_new_runtime_removed_when_idle(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / 'runtime'; runtime = root / 'scripts/entry.py'
            m = ui_install.Manifest(root); m.before([runtime])
            runtime.parent.mkdir(parents=True); runtime.write_text('runtime'); m.after([runtime])
            with patch('ui_install.subprocess.check_output', return_value=''):
                ui_install.uninstall(root)
            self.assertFalse(runtime.exists())

    def test_live_lease_preserves_runtime(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / 'runtime'; runtime = root / 'entry.py'
            m = ui_install.Manifest(root); m.before([runtime])
            runtime.write_text('runtime'); m.after([runtime])
            ui_install.register_lease('session', d, os.getpid(), rig_home=root)
            ui_install.uninstall(root)
            self.assertTrue(runtime.exists())

    def test_registered_repo_removed_without_repo_option(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / 'runtime'; repo = Path(d) / 'repo'; repo.mkdir()
            target = repo / 'AGENTS.md'; target.write_text('user\n')
            m = ui_install.Manifest(root); m.before([target])
            m.data['entries'][str(target)]['repo'] = str(repo)
            target.write_text('user\n<!-- rig:start -->\nmanaged\n<!-- rig:end -->\n'); m.after([target])
            (repo / '.rig/jobs').mkdir(parents=True)
            (repo / '.rig/jobs/job').write_text('data')
            ui_install.uninstall(root)
            self.assertEqual(target.read_text(), 'user\n')
            self.assertEqual((repo / '.rig/jobs/job').read_text(), 'data')

    def test_explicit_legacy_repo_removes_only_marked_instructions(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / 'runtime'; repo = Path(d) / 'repo'; repo.mkdir()
            target = repo / 'AGENTS.md'
            target.write_text('user\n<!-- rig:start -->\nmanaged\n<!-- rig:end -->\nafter\n')
            ui_install.uninstall(root, repos=[repo])
            self.assertEqual(target.read_text(), 'user\nafter\n')

    def test_native_reservation_keeps_runtime_after_integrations_restore(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / 'runtime'; repo = Path(d) / 'repo'; repo.mkdir()
            target = repo / 'AGENTS.md'; runtime = root / 'entry.py'
            m = ui_install.Manifest(root); m.before([target, runtime])
            m.data['entries'][str(target)]['repo'] = str(repo)
            target.write_text('managed'); runtime.write_text('runtime'); m.after([target, runtime])
            directory = repo / '.rig/reservations'; directory.mkdir(parents=True)
            (directory / 'native.json').write_text('{"stage":"running","slot_held":true,"needs_reconciliation":false}')
            ui_install.uninstall(root)
            self.assertFalse(target.exists())
            self.assertTrue(runtime.exists())
            ui_install.uninstall(root)
            self.assertTrue(runtime.exists(), 'repeat uninstall must retain registered reservation knowledge')
