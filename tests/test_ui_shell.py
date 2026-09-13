from pathlib import Path
import os
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import ui_shell

class ShellTests(unittest.TestCase):
    @patch("ui_shell.require_tmux")
    def test_enable_idempotent_disable_and_custom_rc(self, _check):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / 'runtime'; rc = Path(d) / 'custom rc'
            rc.write_text('# user\n')
            ui_shell.enable(root, 'bash', rc); ui_shell.enable(root, 'bash', rc)
            self.assertEqual(rc.read_text().count(ui_shell.START), 1)
            self.assertTrue(rc.read_text().startswith('# user\n'))
            ui_shell.disable(root)
            self.assertFalse((root / 'ui/shell-enabled').exists())

    def test_preserves_aliases_functions_and_bypass_arguments(self):
        script = Path(__file__).resolve().parents[1] / 'scripts/rig-shell.sh'
        with tempfile.TemporaryDirectory() as d:
            binary = Path(d) / 'grok'
            binary.write_text('#!/bin/bash\nprintf "<%s>\\n" "$@"\n'); binary.chmod(0o755)
            env = dict(os.environ, PATH=d + ':' + os.environ['PATH'], RIG_SHELL_HOME=d)
            command = 'codex() { echo custom; }; source "$1"; codex; grok "a b" ""; bash -c "type codex"'
            result = subprocess.run(['bash', '--noprofile', '--norc', '-ic', command, 'bash', str(script)], env=env, capture_output=True, text=True)
            self.assertIn('custom\n<a b>\n<>\n', result.stdout)
            self.assertNotIn('codex is a function', result.stdout)

    def test_dependency_failure_leaves_rc_and_marker_untouched(self):
        with tempfile.TemporaryDirectory() as d:
            rc = Path(d) / 'rc'; rc.write_text('user')
            root = Path(d) / 'runtime'
            with patch('ui_shell.require_tmux', side_effect=ValueError('missing')):
                with self.assertRaises(ValueError):
                    ui_shell.enable(root, 'bash', rc)
            self.assertEqual(rc.read_text(), 'user')
            self.assertFalse(root.exists())

    @patch('ui_shell.require_tmux')
    def test_zdotdir_and_symlink_target_preserved(self, _check):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / 'runtime'; target = Path(d) / 'dotfile'; target.write_text('user\n')
            rc = Path(d) / '.zshrc'; rc.symlink_to(target)
            with patch.dict(os.environ, ZDOTDIR=d):
                ui_shell.enable(root, 'zsh')
            self.assertTrue(rc.is_symlink())
            self.assertIn(ui_shell.START, target.read_text())
            import ui_install
            ui_install.uninstall(root)
            self.assertTrue(rc.is_symlink())
            self.assertEqual(target.read_text(), 'user\n')

    def test_bash_login_profile_selection(self):
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ, HOME=d):
            profile = Path(d) / '.profile'; profile.touch()
            self.assertEqual(ui_shell.shell_rc_paths('bash'), [Path(d) / '.bashrc', profile])
            login = Path(d) / '.bash_profile'; login.touch()
            self.assertEqual(ui_shell.shell_rc_paths('bash')[-1], login)
