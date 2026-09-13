"""Exercise parent verification through real CLI and MCP dispatch boundaries."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import jobs
import rig_mcp


class VerificationFlow(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        self.leases = {}
        (self.repo / ".rig").mkdir()
        (self.repo / ".rig" / "harness.toml").write_text('parent = "codex"\n[workers]\ncodex = false\n')
        (self.repo / ".gitignore").write_text(".rig/\n")
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True, capture_output=True)
        self.enterContext(patch.dict(os.environ, {
            "RIG_PARENT": "codex", "RIG_HOME": str(ROOT), "RIG_SKIP_UPDATE_CHECK": "1",
            "RIG_SKIP_MODEL_CATALOG": "1", "RIG_JOB_ID": "", "RIG_JOB_DIR": "",
            "RIG_JOB_FILES": "", "RIG_JOB_FILES_JSON": "",
            "RIG_THREAD": "verification-flow-parent",
        }))
        self.file = self.repo / "document with spaces.md"
        self.file.write_text("before\n")

    def tool(self, name, **arguments):
        return rig_mcp.call_tool(name, {"repo": str(self.repo), **arguments})

    def value(self, result):
        self.assertFalse(result.get("isError"), result)
        return json.loads(result["content"][0]["text"])

    def auth(self, name):
        return {key: self.leases[name][key] for key in ("reservation_id", "attempt_id", "owner_token")}

    def cli(self, *arguments, lease=None):
        argv, env = list(arguments), os.environ.copy()
        if lease:
            extra = ["--reservation-id", lease["reservation_id"], "--attempt-id", lease["attempt_id"]]
            index = argv.index("--") if "--" in argv else len(argv)
            argv[index:index] = extra
            env["RIG_OWNER_TOKEN"] = lease["owner_token"]
        result = subprocess.run([str(ROOT / "bin" / "rig"), *argv], cwd=self.repo,
                                env=env, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result.stdout.strip()

    def start_and_finish(self, name="native-change"):
        result = self.tool("rig_job_start", id=name, worker="codex", role="parent",
                           files=[self.file.name], executor_kind="parent", model="gpt-6-astra")
        self.assertFalse(result.get("isError"), result)
        self.leases[name] = result["structuredContent"]
        self.file.write_text("after\n")
        result = self.tool("rig_job_finish", id=name, status="ok", summary="Updated the document",
                           completion={"kind": "parent_task", "completed": True}, **self.auth(name))
        self.assertFalse(result.get("isError"), result)
        return name

    def test_cli_checks_accept_current_content_and_later_edits_invalidate(self):
        name = "cli-change"
        lease = json.loads(self.cli("job", "start", name, "--role", "parent", "--files-json", json.dumps([self.file.name]), "--json"))
        self.file.write_text("after\n")
        self.cli("job", "finish", name, "--summary", "Updated document", "--completion-json",
                 '{"kind":"parent_task","completed":true}', lease=lease)
        argv = [sys.executable, "-c", "from pathlib import Path; assert Path('document with spaces.md').read_text() == 'after\\n'"]
        manifest = self.repo / "manifest.json"
        manifest.write_text(json.dumps({"requirements": [{"id": "content", "argv": argv}], "manual_criteria": []}))
        self.cli("job", "requirements", name, "--file", str(manifest), lease=lease)
        check = json.loads(self.cli("job", "check", name, "--name", "content", "--", *argv, lease=lease))
        self.assertEqual(check["exit_code"], 0)
        accepted = json.loads(self.cli("job", "accept", name, "--decision", "accept",
                                       "--snapshot-id", check["after_snapshot_id"], "--checks", check["check_id"],
                                       "--rationale", "Required document content checked", lease=lease))
        self.assertEqual(accepted["acceptance"], "accepted")
        self.assertIn("verification  verified", self.cli("job", "show", name))
        self.file.write_text("edited after acceptance\n")
        shown = self.cli("job", "show", name)
        self.assertNotIn("verification  verified", shown)
        self.assertIn("content_changed", shown)

    def test_parent_manual_acceptance_exposes_snapshot_and_evidence(self):
        name = self.start_and_finish()
        shown = jobs.format_show(jobs.resolve_job(self.repo, name))
        snapshot_id = next(line.split()[1] for line in shown.splitlines() if line.startswith("snapshot_id"))
        self.assertIn("change-evidence.json", shown)
        self.value(self.tool("rig_job_requirements", id=name, requirements=[], manual_criteria=["Reviewed document wording"], **self.auth(name)))
        accepted = self.value(self.tool("rig_job_accept", id=name, decision="accept", snapshot_id=snapshot_id,
                                        rationale="Read the scoped document and verified its wording", **self.auth(name)))
        self.assertEqual(accepted["method"], "manual")
        self.assertEqual(accepted["acceptance"], "accepted")

    def test_check_progress_uses_check_request_token(self):
        name = self.start_and_finish()
        argv = [sys.executable, "-c", "print('checked')"]
        self.value(self.tool("rig_job_requirements", id=name, requirements=[{"id": "check", "argv": argv}], manual_criteria=[], **self.auth(name)))
        sent = []
        with patch.object(rig_mcp, "write_message", sent.append):
            reply = rig_mcp.handle({"jsonrpc": "2.0", "id": 91, "method": "tools/call", "params": {
                "name": "rig_job_check", "arguments": {"repo": str(self.repo), "id": name, "name": "check", "argv": argv, **self.auth(name)},
                "_meta": {"progressToken": "check-request-token"},
            }})
            rig_mcp._wait_threads[-1].join(timeout=10)
        self.assertIsNone(reply)
        reply = next(event for event in sent if event.get("id") == 91)
        self.assertFalse(reply["result"].get("isError"), reply)
        self.assertTrue(sent)
        for event in (event for event in sent if event.get("method") == "notifications/progress"):
            self.assertEqual(event["params"]["progressToken"], "check-request-token")
            self.assertNotIn("total", event["params"])

    def test_mcp_reader_services_ping_while_actual_check_runs(self):
        name = self.start_and_finish()
        argv = [sys.executable, "-c", "import time; time.sleep(0.5)"]
        self.value(self.tool("rig_job_requirements", id=name, requirements=[{"id": "slow", "argv": argv}], manual_criteria=[], **self.auth(name)))
        messages = [
            {"jsonrpc": "2.0", "id": 101, "method": "tools/call", "params": {
                "name": "rig_job_check", "arguments": {"repo": str(self.repo), "id": name, "name": "slow", "argv": argv, **self.auth(name)}}},
            {"jsonrpc": "2.0", "id": 102, "method": "ping"},
        ]
        process = subprocess.run([sys.executable, str(ROOT / "scripts" / "rig_mcp.py")],
                                 input="".join(json.dumps(item) + "\n" for item in messages),
                                 cwd=self.repo, text=True, capture_output=True, timeout=10)
        self.assertEqual(process.returncode, 0, process.stderr)
        replies = [json.loads(line) for line in process.stdout.splitlines()]
        self.assertEqual([reply["id"] for reply in replies], [102, 101])
        self.assertFalse(replies[1]["result"].get("isError"), replies)

    def test_finishing_legacy_execution_does_not_create_trusted_provenance(self):
        folder = self.repo / ".rig" / "jobs" / "legacy"
        folder.mkdir(parents=True)
        (folder / "meta.json").write_text(json.dumps({"job_id": "legacy", "worker": "codex", "role": "worker",
                                                     "status": "ok", "files": [self.file.name]}))
        jobs.finish_job(self.repo, "legacy", status="ok")
        self.assertEqual(jobs.load_job(folder)["execution_mode"], "unknown")

    def test_json_scope_preserves_literal_brackets_and_filename_whitespace(self):
        self.file = self.repo / "app" / "[id]" / " page.tsx "
        self.file.parent.mkdir(parents=True)
        self.file.write_text("before\n")
        subject = str(self.file.relative_to(self.repo))
        started = self.tool("rig_job_start", id="literal-path", role="parent", files=[subject])
        self.assertFalse(started.get("isError"), started)
        self.leases["literal-path"] = started["structuredContent"]
        self.file.write_text("after\n")
        finished = self.tool("rig_job_finish", id="literal-path", completion={"kind": "parent_task", "completed": True}, **self.auth("literal-path"))
        self.assertFalse(finished.get("isError"), finished)
        folder = self.repo / ".rig" / "jobs" / "literal-path"
        self.assertEqual(jobs.load_job(folder)["files"], [subject])
        self.assertEqual(json.loads((folder / "result.json").read_text())["files_changed"], [subject])

    def test_children_cannot_change_requirements_run_checks_or_accept(self):
        name = self.start_and_finish()
        with patch.dict(os.environ, {"RIG_JOB_ID": name, "RIG_JOB_DIR": str(self.repo / ".rig" / "jobs" / name)}):
            names = {tool["name"] for tool in rig_mcp.listed_tools()}
            for tool in ("rig_job_requirements", "rig_job_check", "rig_job_accept"):
                self.assertNotIn(tool, names)
                result = self.tool(tool, id=name)
                self.assertTrue(result.get("isError"))
                self.assertIn("not a child tool", result["content"][0]["text"])


if __name__ == "__main__":
    unittest.main()
