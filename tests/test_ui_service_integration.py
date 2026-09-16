"""Real local observer processes; fixtures never install into the user's home."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
from ui_service import request
from ui_store import runtime_directory, read, write


class ObserverIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name) / 'repo'
        (self.repo / '.rig').mkdir(parents=True)
        (self.repo / '.rig/harness.toml').write_text('parent = "codex"\n')
        self.env = dict(os.environ, HOME=self.temp.name, RIG_HOME=str(Path(self.temp.name) / 'runtime'))
        self.folder = runtime_directory(self.repo)
        self.endpoint = self.folder / 'control.sock'
        self.errors = tempfile.TemporaryFile()
        self.addCleanup(self.errors.close)
        self.process = subprocess.Popen([sys.executable, str(SCRIPTS / 'rig_ui.py'), 'serve', '--repo', str(self.repo)],
                                        env=self.env, stdout=subprocess.DEVNULL, stderr=self.errors)
        self.addCleanup(self.stop)
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                self.errors.seek(0)
                error = self.errors.read().decode()
                if 'Operation not permitted' in error or 'Permission denied' in error:
                    self.skipTest('Unix socket process requires sandbox permission')
                self.fail(error)
            try:
                if request(self.endpoint, {'op': 'ping'}, timeout=.2).get('ok'):
                    return
            except (OSError, ValueError):
                time.sleep(.02)
        self.fail('observer startup exceeded deadline')

    def stop(self):
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=3)
        # Only this fixture's hashed runtime files, never any shared server.
        for path in self.folder.iterdir():
            if path.is_file() or path.is_symlink() or path.is_socket():
                path.unlink()
        self.folder.rmdir()

    def test_enqueue_receipt_and_duplicate_start_preserve_owner(self):
        owner = read(self.folder / 'owner.json')
        receipt = request(self.endpoint, {'op': 'enqueue', 'text': 'integration queue', 'submission_id': 'submission', 'action_id': 'receipt'})
        self.assertIn(receipt['status'], ('pending', 'done'))
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            receipt = request(self.endpoint, {'op': 'action', 'action_id': 'receipt'})
            if receipt['status'] != 'pending':
                break
            time.sleep(.02)
        self.assertEqual(receipt['status'], 'done', receipt)
        self.assertEqual(receipt['result']['text'], 'integration queue')
        pending = self.repo / '.rig/ui/actions/uncommitted.json'
        write(pending, {'status': 'pending', 'request': '{}'})
        duplicate = subprocess.run([sys.executable, str(SCRIPTS / 'rig_ui.py'), 'serve', '--repo', str(self.repo)],
                                   env=self.env, capture_output=True, timeout=3)
        self.assertEqual(duplicate.returncode, 2, duplicate.stderr)
        self.assertEqual(read(self.folder / 'owner.json'), owner)
        self.assertEqual(read(pending)['status'], 'pending')
        self.assertIn('status_line', request(self.endpoint, {'op': 'snapshot'}))
        self.assertTrue(request(self.endpoint, {'op': 'ping'})['ok'])

    def test_dead_session_pruned_during_continuous_snapshot_traffic(self):
        child = subprocess.Popen([sys.executable, '-c', 'pass'])
        child.wait(timeout=3)
        request(self.endpoint, {'op': 'register', 'session': 'dead', 'pid': child.pid})
        deadline = time.monotonic() + 7
        while time.monotonic() < deadline:
            request(self.endpoint, {'op': 'snapshot'})
            if 'dead' not in read(self.repo / '.rig/ui/sessions.json', {}):
                break
            time.sleep(.1)
        self.assertNotIn('dead', read(self.repo / '.rig/ui/sessions.json', {}))

    def test_observer_snapshot_includes_fixture_workflows_without_tokens(self):
        secret = 'aa11bb22cc33dd44ee55ff6677889900'
        folder = self.repo / '.rig' / 'workflows' / 'wf-live'
        folder.mkdir(parents=True)
        nodes = [
            {'id': 'a1', 'role': 'implement', 'files': ['a1.py'], 'required': True, 'depends_on': []},
            {'id': 'r2', 'role': 'mini', 'files': ['r2.py'], 'required': True, 'depends_on': []},
            {'id': 'p3', 'role': 'verify', 'files': ['p3.py'], 'required': True, 'depends_on': []},
        ]
        state = {
            'a1': {'status': 'accepted', 'accepted': True},
            'r2': {'status': 'running', 'accepted': False},
            'p3': {'status': 'pending', 'accepted': False},
        }
        (folder / 'spec.json').write_text(json.dumps({
            'version': 1, 'workflow_id': 'wf-live', 'title': 'wf-live', 'nodes': nodes,
            'spec_hash': 'hash-wf-live',
        }))
        (folder / 'state.json').write_text(json.dumps({
            'version': 1, 'workflow_id': 'wf-live', 'status': 'running', 'nodes': state,
            'updated_at': '2026-01-02T00:00:00+00:00',
            'parent_action': {'kind': 'advance', 'owner_token': secret},
            'owner_token': secret,
        }))
        (folder / 'owner-credentials.json').write_text(json.dumps({'owner_token': secret}))
        deadline = time.monotonic() + 3
        snap = {}
        while time.monotonic() < deadline:
            snap = request(self.endpoint, {'op': 'snapshot'})
            if snap.get('workflows'):
                break
            time.sleep(0.05)
        self.assertTrue(snap.get('workflows'), snap)
        row = snap['workflows'][0]
        self.assertEqual(row['workflow_id'], 'wf-live')
        self.assertEqual((row['accepted'], row['required'], row['running'], row['ask']), (1, 3, 1, 0))
        self.assertEqual(row['next_parent_action']['kind'], 'advance')
        self.assertIn('jobs', snap)
        self.assertIn('pending', snap)
        self.assertIn('status_line', snap)
        blob = json.dumps(snap)
        self.assertNotIn(secret, blob)
        self.assertNotIn('owner_token', blob)
