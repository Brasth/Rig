"""Run the documented lifecycle and validate referenced parent tool names."""
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import admission
import rig_mcp
import verification


class ProtocolDocumentation(unittest.TestCase):
    @staticmethod
    def protocol_sources():
        sources = {str(path.relative_to(ROOT)): path.read_text() for path in (
            ROOT / "README.md", ROOT / "docs" / "usage.md",
            ROOT / "skills" / "delegate-harness" / "SKILL.md",
            ROOT / "skills" / "rig-jobs" / "SKILL.md",
        )}
        sources["generated parent protocol"] = (ROOT / "bin" / "rig").read_text().split(
            "<!-- rig:start -->", 1)[1].split("<!-- rig:end -->", 1)[0]
        return sources

    def test_cancellation_and_transport_recovery_instructions_are_consistent(self):
        for name, source in self.protocol_sources().items():
            with self.subTest(source=name):
                self.assertIn("rig job wait ID --timeout 0", source)
                self.assertIn("stop-unconfirmed", source)
                self.assertIn("native-cancel-required", source)
                self.assertRegex(source.lower(), r"(?:do not|never) re-wait, re-pick, or drain")
                self.assertNotIn("Host-dropped wait still bash-waits once", source)
                self.assertNotIn("host drops the tool, bash `rig job wait` once (no `--timeout`)", source)

    def test_queue_receipts_and_durable_credentials_are_documented(self):
        sources = self.protocol_sources()
        sources.pop("README.md")
        sources.pop("skills/rig-jobs/SKILL.md")
        sources["skills/rig-queue/SKILL.md"] = (ROOT / "skills" / "rig-queue" / "SKILL.md").read_text()
        for name, source in sources.items():
            with self.subTest(source=name):
                self.assertIn("--idempotency-key", source)
                self.assertIn("--owner-pid", source)
                self.assertIn("--owner-session", source)
                self.assertIn(".rig/queue/credentials/<queue-id>.json", source)
                self.assertIn("receipt", source)
        queue_skill = sources["skills/rig-queue/SKILL.md"]
        self.assertNotIn("then the QUEUE block", queue_skill)
        self.assertIn("The parking command itself stops after its receipt", queue_skill)

    def test_documented_native_cli_example_accepts_real_content_privately(self):
        usage = (ROOT / "docs" / "usage.md").read_text()
        section = usage.split("## Protected writes and parent acceptance", 1)[1]
        example = section.split("```bash\n", 1)[1].split("\n```", 1)[0]
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary) / "repo"
            home = Path(temporary) / "home"
            repo.mkdir(); home.mkdir()
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            (repo / ".rig").mkdir()
            (repo / ".rig" / "harness.toml").write_text('parent = "codex"\n[workers]\ncodex = false\n')
            (repo / ".gitignore").write_text('.rig/\n__pycache__/\n')
            env = {key: value for key, value in os.environ.items() if not key.startswith("RIG_")}
            env.update(HOME=str(home), RIG_HOME=str(ROOT), RIG_INSTALL_TRANSACTION="1", RIG_PARENT="codex", RIG_SKIP_MODEL_CATALOG="1",
                       RIG_SKIP_UPDATE_CHECK="1", PATH=str(ROOT / "bin") + os.pathsep + os.environ["PATH"])
            result = subprocess.run(["bash", "-c", "set -euo pipefail\n" + example], cwd=repo,
                                    env=env, text=True, capture_output=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            response = json.loads((repo / ".rig" / "start-response.json").read_text())
            self.assertNotIn(response["owner_token"], result.stdout + result.stderr)
            self.assertEqual((repo / ".rig" / "start-response.json").stat().st_mode & 0o777, 0o600)
            record = admission.get_reservation(repo, response["reservation_id"])
            self.assertEqual(record["stage"], "released")
            folder = repo / ".rig" / "jobs" / response["job_id"]
            accepted = json.loads((folder / "verification.json").read_text())
            self.assertEqual(accepted["acceptance"], "accepted")
            self.assertEqual(accepted["method"], "mixed")
            self.assertTrue(accepted["check_ids"])
            self.assertEqual((repo / "src" / "example.py").read_text(), "answer = 42\n")
            (repo / "src" / "example.py").write_text("answer = 43\n")
            meta = json.loads((folder / "meta.json").read_text())
            self.assertNotEqual(verification.assessment(repo, meta, refresh=True)["state"], "verified")

    def test_documented_mcp_tools_exist_in_server_schema(self):
        sources = [ROOT / "README.md", ROOT / "docs" / "usage.md"]
        sources.extend(ROOT / "skills" / name / "SKILL.md" for name in ("delegate-harness", "rig-jobs", "rig-queue"))
        known = {tool["name"] for tool in [*rig_mcp.TOOLS, *rig_mcp.CHILD_TOOLS]}
        pattern = re.compile(r"\brig_(?:job_[a-z_]+|queue_[a-z_]+|session|pick|status|jobs|memory(?:_add)?)\b")
        for source in sources:
            with self.subTest(path=source.relative_to(ROOT)):
                mentioned = set(pattern.findall(source.read_text()))
                self.assertFalse(mentioned - known, mentioned - known)
        protocol = (ROOT / "bin" / "rig").read_text().split("<!-- rig:start -->", 1)[1].split("<!-- rig:end -->", 1)[0]
        self.assertFalse(set(pattern.findall(protocol)) - known)


if __name__ == "__main__":
    unittest.main()
