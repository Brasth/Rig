"""Bare-shell wrappers keep one invocation identity across Python helpers."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import admission
import jobs
import mcp_test_support


class WrapperOwnerSession(unittest.TestCase):
    def run_wrapper(self, session=""):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        repo = Path(temporary.name)
        (repo / ".git").mkdir()
        (repo / ".rig").mkdir()
        (repo / ".rig" / "harness.toml").write_text(
            'parent = "codex"\n[workers]\ngrok = true\ncodex = true\n'
        )
        (repo / "a.py").write_text("original\n")
        brief = repo / "brief.txt"
        brief.write_text("no changes\n")
        bins, home = repo / "bins", repo / "home"
        bins.mkdir()
        uuidgen = bins / "uuidgen"
        uuidgen.write_text(f"#!{sys.executable}\nimport uuid\nprint(uuid.uuid4())\n")
        uuidgen.chmod(0o755)
        mcp_test_support.seed_installed_mcp(home)
        worker = bins / "grok"
        worker.write_text(f"#!{sys.executable}\n" + mcp_test_support.inbox_handshake_prelude(ROOT)
                          + "print('finished authenticated fake work')\n")
        worker.chmod(0o755)
        env = {key: value for key, value in os.environ.items()
               if not key.startswith("RIG_") and key not in
               {"CODEX_THREAD_ID", "CODEX_SESSION_ID", "GROK_SESSION_ID"}}
        env.update(PATH=mcp_test_support.stub_path(bins), HOME=str(home), RIG_HOME=str(ROOT),
                   RIG_PARENT="codex", RIG_LIVE="1", RIG_TIMEOUT="20", RIG_SKIP_MODEL_CATALOG="1",
                   RIG_ROLE="worker", RIG_JOB_FILES_JSON='["a.py"]', RIG_SKIP_UPDATE_CHECK="1")
        if session:
            env["RIG_OWNER_SESSION"] = session
        output = tempfile.TemporaryFile(mode="w+")
        self.addCleanup(output.close)
        process = subprocess.Popen([str(ROOT / "scripts" / "run-worker.sh"), "grok", "bare-shell", str(brief)],
                                   cwd=repo, env=env, stdout=output, stderr=subprocess.STDOUT, text=True)
        def cleanup():
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
        self.addCleanup(cleanup)
        # The real child handshake and normal finish exercise all authenticated
        # wrapper helper paths without network, sleeps, or external providers.
        process.wait(timeout=15)
        output.seek(0)
        transcript = output.read()
        self.assertEqual(process.returncode, 0, transcript)
        job_dir = repo / ".rig" / "jobs" / "bare-shell"
        result = json.loads((job_dir / "result.json").read_text())
        self.assertEqual(result["status"], "ok", transcript)
        saved = json.loads((job_dir / "owner-credentials.json").read_text())
        record = admission.get_reservation(repo, saved["reservation_id"])
        self.assertTrue(record["stopped"])
        owner_session = record["owner"]["session_id"]
        if session:
            self.assertEqual(owner_session, session)
        else:
            self.assertTrue(owner_session.startswith("wrapper:"), owner_session)
        self.assertEqual(record["owner"]["initiating_identity"], "session:" + owner_session)
        with self.assertRaisesRegex(admission.AdmissionError, "owner session mismatch"):
            admission.assert_owned(repo, **admission.credentials(saved), owner_session="unrelated")
        # Explicit validated receipt recovery preserves that invocation's owner.
        restored = admission.resolve_ownership(repo, credentials_path=str(job_dir / "owner-credentials.json"), job_id="bare-shell")
        self.assertEqual(restored["owner_session"], owner_session)
        self.assertNotIn(saved["owner_token"], transcript)
        return owner_session

    def test_no_session_wrapper_helpers_and_receipt_keep_fresh_invocation_identity(self):
        first = self.run_wrapper()
        second = self.run_wrapper()
        self.assertNotEqual(first, second)

    def test_explicit_owner_session_is_preserved(self):
        self.run_wrapper("explicit-wrapper-owner")


if __name__ == "__main__":
    unittest.main()
