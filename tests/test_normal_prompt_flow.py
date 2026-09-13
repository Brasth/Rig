"""Normal prompt and local rollout paths through the actual CLI and MCP transport."""
import json
import os
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class NormalPromptFlow(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="rig-prompt-flow-")
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.repo = self.base / "project"
        self.repo.mkdir()
        self.home = self.base / "home"
        self.home.mkdir()
        self.env = {**os.environ, "HOME": str(self.home), "RIG_HOME": str(ROOT),
                    "RIG_PARENT": "codex", "RIG_THREAD": "normal-prompt-parent",
                    "RIG_SKIP_UPDATE_CHECK": "1", "RIG_SKIP_MODEL_CATALOG": "1",
                    "RIG_JOB_ID": "", "RIG_JOB_DIR": "", "RIG_JOB_FILES": "",
                    "RIG_JOB_FILES_JSON": "", "RIG_OWNER_TOKEN": "",
                    "RIG_RESERVATION_ID": "", "RIG_ATTEMPT_ID": ""}
        self.run_process(["git", "init", "-q", str(self.repo)])
        self.cli("init")
        self.subject = self.repo / "guide.md"
        self.subject.write_text("before\n")

    def run_process(self, argv, **kwargs):
        result = subprocess.run(argv, cwd=self.repo, env=self.env, text=True,
                                capture_output=True, timeout=30, **kwargs)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result.stdout

    def cli(self, *argv, lease=None):
        args = list(argv)
        if lease:
            auth = ["--reservation-id", lease["reservation_id"], "--attempt-id", lease["attempt_id"]]
            index = args.index("--") if "--" in args else len(args)
            args[index:index] = auth
        previous = self.env["RIG_OWNER_TOKEN"]
        self.env["RIG_OWNER_TOKEN"] = lease["owner_token"] if lease else ""
        try:
            return self.run_process([str(ROOT / "bin" / "rig"), *args])
        finally:
            self.env["RIG_OWNER_TOKEN"] = previous

    def mcp(self, name, **arguments):
        request = {"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {
            "name": name, "arguments": {"repo": str(self.repo), **arguments}}}
        text = self.run_process([sys.executable, str(ROOT / "scripts" / "rig_mcp.py")],
                                input=json.dumps(request) + "\n")
        reply = next(json.loads(line) for line in text.splitlines() if json.loads(line).get("id") == 7)
        self.assertNotIn("error", reply, reply)
        result = reply["result"]
        self.assertFalse(result.get("isError"), result)
        return result

    def test_parent_write_checks_acceptance_and_content_change_across_transports(self):
        session = self.mcp("rig_session", role="stay", compact=True, terminal_limit=10,
                           case="Explain this design without changing files")
        self.assertEqual(json.loads(session["content"][0]["text"])["pick"]["spawn"], "stay")
        self.assertFalse(list((self.repo / ".rig" / "jobs").glob("*/meta.json")))
        lease = json.loads(self.cli("job", "start", "guide-change", "--role", "parent",
                                    "--files-json", '["guide.md"]', "--json"))
        folder = self.repo / ".rig" / "jobs" / "guide-change"
        self.assertTrue((folder / "change-before.json").is_file())
        self.subject.write_text("after\n")
        self.cli("job", "finish", "guide-change", "--completion-json",
                 '{"kind":"parent_task","completed":true}', lease=lease)
        auth = {key: lease[key] for key in ("reservation_id", "attempt_id", "owner_token")}
        argv = [sys.executable, "-c", "from pathlib import Path; assert Path('guide.md').read_text() == 'after\\n'"]
        self.mcp("rig_job_requirements", id="guide-change", requirements=[{"id": "content", "argv": argv}],
                 manual_criteria=[], **auth)
        check = json.loads(self.cli("job", "check", "guide-change", "--name", "content", "--", *argv, lease=lease))
        accepted = self.mcp("rig_job_accept", id="guide-change", decision="accept",
                            snapshot_id=check["after_snapshot_id"], rationale="Required content assertion passed", **auth)
        self.assertEqual(json.loads(accepted["content"][0]["text"])["state"], "verified")
        self.assertIn("verification  verified", self.cli("job", "show", "guide-change"))
        session = self.mcp("rig_session", role="stay", compact=True, case="Report the result")
        row = json.loads(session["content"][0]["text"])["jobs"][0]
        self.assertEqual(row["verification_summary"]["state"], "verified")
        self.assertNotIn(lease["owner_token"], json.dumps(session))
        self.subject.write_text("changed after acceptance\n")
        self.assertIn("content_changed", self.cli("job", "show", "guide-change"))
        # Completion released ownership; later work needs its own fresh attempt.
        replacement = json.loads(self.cli("job", "start", "next-guide-change", "--role", "parent",
                                          "--files-json", '["guide.md"]', "--json"))
        self.assertNotEqual(lease["attempt_id"], replacement["attempt_id"])
        self.cli("job", "finish", "next-guide-change", "--status", "cancelled", "--completion-json",
                 '{"kind":"parent_task","completed":true}', lease=replacement)
        self.cli("job", "close", "next-guide-change", "--rationale", "Parent stopped without further edits", lease=replacement)

    def test_local_install_setup_and_init_preserve_user_configuration(self):
        kit = self.base / "installed-kit"
        self.env.update(RIG_HOME=str(kit), RIG_SRC=str(ROOT))
        custom = self.home / ".codex" / "agents" / "worker.toml"
        custom.parent.mkdir(parents=True)
        custom_text = 'name = "custom-worker"\nmodel = "custom-model"\nsandbox_mode = "read-only"\n'
        custom.write_text(custom_text)
        agents = self.repo / "AGENTS.md"
        agents.write_text("# Project rules\n\nPreserve my project instructions.\n")
        harness = self.repo / ".rig" / "harness.toml"
        flags = 'parent = "codex"\n[workers]\ncodex = false\ngrok = false\n'
        harness.write_text(flags)
        memory = self.repo / ".rig" / "MEMORY.md"
        memory.write_text("# Memory\n\n- Preserve my project fact.\n")
        self.run_process(["bash", str(ROOT / "install.sh")])
        self.run_process([str(kit / "bin" / "rig"), "setup"])
        for _ in range(2):
            self.run_process([str(kit / "bin" / "rig"), "init"])
        self.assertEqual(custom.read_text(), custom_text)
        settings = tomllib.loads(harness.read_text())
        self.assertEqual(settings["parent"], "codex")
        self.assertFalse(settings["workers"]["codex"])
        self.assertFalse(settings["workers"]["grok"])
        self.assertIn("Preserve my project fact.", memory.read_text())
        self.assertIn("Preserve my project instructions.", agents.read_text())
        self.assertEqual(agents.read_text().count("<!-- rig:start -->"), 1)
        for name in ("admission.py", "change_evidence.py", "verification.py", "jobs.py", "rig_mcp.py"):
            self.assertEqual((kit / "scripts" / name).read_bytes(), (ROOT / "scripts" / name).read_bytes())
        for skill in ("delegate-harness", "rig-jobs", "rig-queue"):
            self.assertEqual((kit / "skills" / skill / "SKILL.md").read_bytes(),
                             (ROOT / "skills" / skill / "SKILL.md").read_bytes())
        session = self.run_process([str(kit / "bin" / "rig"), "session", "--role", "stay",
                                    "--compact", "--json", "--case", "Explain the project"])
        self.assertEqual(json.loads(session)["pick"]["spawn"], "stay")

    def test_wrapper_execution_requires_parent_acceptance_before_verified_hud(self):
        binaries = self.base / "binaries"
        binaries.mkdir()
        worker = binaries / "grok"
        worker.write_text(f"#!{sys.executable}\n" +
                          "import os\nfrom pathlib import Path\n" +
                          "assert (Path(os.environ['RIG_JOB_DIR']) / 'change-before.json').is_file()\n" +
                          "Path('guide.md').write_text('wrapper changed guide\\n')\n" +
                          "print('{\"type\":\"text\",\"text\":\"Updated guide\"}')\n")
        worker.chmod(0o755)
        self.env.update(PATH=str(binaries) + os.pathsep + self.env.get("PATH", ""),
                        RIG_LIVE="1", RIG_ROLE="mini", RIG_MODEL="grok-4.6", RIG_JOB_FILES_JSON='["guide.md"]')
        (self.repo / ".rig" / "harness.toml").write_text('parent = "codex"\n[workers]\ngrok = true\n')
        brief = self.repo / "brief.md"
        brief.write_text("Update the guide. Scope: guide.md\n")
        self.run_process([str(ROOT / "scripts" / "run-worker.sh"), "grok", "wrapper-guide", str(brief)])
        folder = self.repo / ".rig" / "jobs" / "wrapper-guide"
        lease = json.loads((folder / "owner-credentials.json").read_text())
        result = json.loads((folder / "result.json").read_text())
        self.assertEqual(result["files_changed"], ["guide.md"])
        shown = self.cli("job", "show", "wrapper-guide")
        self.assertIn("completed-unverified", shown)
        snapshot = next(line.split()[1] for line in shown.splitlines() if line.startswith("snapshot_id"))
        auth = {key: lease[key] for key in ("reservation_id", "attempt_id", "owner_token")}
        self.mcp("rig_job_requirements", id="wrapper-guide", requirements=[],
                 manual_criteria=["Read the updated guide"], **auth)
        self.mcp("rig_job_accept", id="wrapper-guide", decision="accept", snapshot_id=snapshot,
                 rationale="Read guide.md and confirmed the requested wording", **auth)
        hud = json.loads(self.run_process([sys.executable, str(ROOT / "scripts" / "jobs.py"), "hud", "--json"]))
        self.assertEqual(hud["display_state"], "verified")
        self.assertEqual(hud["selected"]["id"], "wrapper-guide")
        self.assertEqual(hud["independence"], "unknown")


if __name__ == "__main__":
    unittest.main()
