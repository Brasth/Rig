import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from ui_popup import draft_path, load_draft, save_draft
from tui_editor import Draft


class PopupDraftTests(unittest.TestCase):
    def test_close_reopen_preserves_unicode_and_idempotency(self):
        with tempfile.TemporaryDirectory() as directory:
            path = draft_path(directory, 'session')
            draft = Draft(text='Việt Nam 😀', cursor=11)
            save_draft(path, draft)
            restored = load_draft(path)
            self.assertEqual(restored.text, draft.text)
            self.assertEqual(restored.submission_id, draft.submission_id)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_session_path_cannot_escape(self):
        self.assertEqual(draft_path('/repo', '../../outside').parent, Path('/repo/.rig/ui/drafts'))

    def test_pending_action_survives_close_for_recovery(self):
        with tempfile.TemporaryDirectory() as directory:
            path = draft_path(directory, 'session')
            draft = Draft(text='pending')
            draft.action_id = draft.submission_id
            save_draft(path, draft)
            self.assertEqual(load_draft(path).action_id, draft.submission_id)

    def test_readable_details_include_approval_without_identity(self):
        from ui_popup_view import detail_lines
        lines = '\n'.join(detail_lines({'task': 'Fix issue', 'worker': 'grok', 'effective': 'ask', 'identity': {'directory_identity': [4, 9]}, 'ask': {'tool_name': 'Bash', 'preview': 'pytest tests'}, 'files': ['app.py'], 'reservation': {'stage': 'running'}}))
        self.assertIn('pytest tests', lines)
        self.assertIn('Held files: app.py', lines)
        self.assertNotIn('directory_identity', lines)

    def test_client_setup_and_snapshot_do_not_block_stop_lane(self):
        import threading
        import time
        from unittest.mock import patch
        from ui_popup_client import Client
        release = threading.Event()
        def connect(repo):
            release.wait(1)
            return 'socket'
        with patch('ui_service.ensure_service', side_effect=connect), patch('ui_service.request', return_value={}):
            start = time.monotonic()
            client = Client('/repo')
            self.assertLess(time.monotonic() - start, .1)
            self.assertTrue(client.send({'op': 'snapshot'}))
            self.assertTrue(client.send({'op': 'enqueue'}))
            self.assertTrue(client.send({'op': 'stop'}))
            release.set()
            for _ in range(3): client.results.get(timeout=2)
