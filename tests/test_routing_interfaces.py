"""Public smart routing contracts, using fake CLIs and isolated configuration."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import rig_mcp
import mcp_test_support


class RoutingInterfaces(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.repo = self.root / "repo"
        self.home = self.root / "home"
        self.repo.mkdir()
        self.home.mkdir()
        (self.repo / ".git").mkdir()
        (self.repo / ".rig").mkdir()
        (self.repo / ".rig" / "harness.toml").write_text(
            'parent = "codex"\n[workers]\ngrok = true\nclaude = false\n'
            'codex = false\ncursor = false\nopencode = false\nomp = false\npi = false\nagy = false\n'
        )
        bins = self.root / "bins"
        mcp_test_support.fake_bin(bins, "grok")
        mcp_test_support.seed_installed_mcp(self.home)
        env = {k: v for k, v in os.environ.items() if not k.startswith("RIG_")}
        env.update(HOME=str(self.home), PATH=mcp_test_support.stub_path(bins),
                   RIG_HOME=str(ROOT), RIG_PARENT="codex", RIG_SKIP_MODEL_CATALOG="1",
                   RIG_SKIP_UPDATE_CHECK="1", RIG_INSTALL_TRANSACTION="1")
        patch = mock.patch.dict(os.environ, env, clear=True)
        patch.start()
        self.addCleanup(patch.stop)

    def cli(self, *args):
        return subprocess.run(["bash", str(ROOT / "bin" / "rig"), *args],
                              cwd=self.repo, text=True, capture_output=True, timeout=20)

    def mcp(self, tool, **args):
        return rig_mcp.call_tool(tool, {"repo": str(self.repo), "case": "scoped task", **args})

    def test_pick_session_cli_mcp_parity(self):
        for role, level, tier in (("implement", "low", "fast"), ("mini", "high", "strong")):
            with self.subTest(role=role):
                args = ["--case", "scoped task", "--complexity", level, "--risk", level,
                        "--uncertainty", level, "--assessment-reason", "bounded assessment", "--json"]
                pick = self.cli("pick", role, *args)
                self.assertEqual(pick.returncode, 0, pick.stderr)
                selected = json.loads(pick.stdout)
                self.assertEqual(selected["routing"]["required_tier"], tier)
                for tool in ("rig_pick", "rig_session"):
                    result = self.mcp(tool, role=role, complexity=level, risk=level,
                                      uncertainty=level, assessment_reason="bounded assessment", compact=True)
                    self.assertFalse(result.get("isError"), result)
                    body = json.loads(result["content"][0]["text"])
                    choice = body["pick"] if tool == "rig_session" else body
                    self.assertEqual(choice, selected)
                session = self.cli("session", "--role", role, "--compact", *args)
                self.assertEqual(session.returncode, 0, session.stderr)
                self.assertEqual(json.loads(session.stdout)["pick"], selected)

    def test_invalid_assessment_and_explain_are_errors(self):
        for tool in ("rig_pick", "rig_session"):
            for bad in ({"assessment": {"unknown": "low"}}, {"assessment": {"risk": True}},
                        {"complexity": "extreme"}, {"explain": "false"}):
                with self.subTest(tool=tool, bad=bad):
                    self.assertTrue(self.mcp(tool, role="implement", **bad).get("isError"))
        for command in ("pick", "session"):
            result = self.cli(command, "implement", "--case", "scoped task", "--risk", "extreme")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("low|medium|high", result.stderr)

    def test_verify_pick_is_read_only_standard(self):
        pick = self.cli("pick", "verify", "--case", "check results", "--json")
        self.assertEqual(pick.returncode, 0, pick.stderr)
        body = json.loads(pick.stdout)
        self.assertEqual(body["kind"], "verify")
        self.assertEqual(body["routing"]["required_tier"], "standard")
        self.assertEqual(body["spawn"], "run-worker")
        self.assertFalse(body.get("parent_writes"))
        high = self.cli(
            "pick", "verify", "--case", "check results", "--risk", "high",
            "--complexity", "low", "--uncertainty", "low", "--json",
        )
        self.assertEqual(high.returncode, 0, high.stderr)
        high_body = json.loads(high.stdout)
        self.assertEqual(high_body["kind"], "verify")
        self.assertEqual(high_body["routing"]["required_tier"], "strong")
        self.assertFalse(high_body.get("parent_writes"))
        review = self.cli("pick", "review", "--case", "review the writer diff", "--json")
        self.assertEqual(review.returncode, 0, review.stderr)
        review_body = json.loads(review.stdout)
        self.assertEqual(review_body["kind"], "review")
        self.assertEqual(review_body["routing"]["required_tier"], "strong")

    def test_explain_legacy_and_report(self):
        for command in ("pick", "session"):
            result = self.cli(command, "implement", "--case", "scoped task", "--explain")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("fingerprint=", result.stdout)
            self.assertIn("candidate ", result.stdout)
        legacy = self.cli("pick", "implement", "--policy-mode", "legacy", "--json")
        self.assertEqual(legacy.returncode, 0, legacy.stderr)
        self.assertEqual(json.loads(legacy.stdout)["routing"]["policy_mode"], "legacy")
        report = self.cli("routing", "report", "--json")
        self.assertEqual(report.returncode, 0, report.stderr)
        mcp = rig_mcp.call_tool("rig_routing_report", {"repo": str(self.repo)})
        self.assertEqual(json.loads(report.stdout), mcp["structuredContent"])

    def test_doctor_rejects_invalid_policy_without_changing_workers(self):
        path = self.repo / ".rig" / "harness.toml"
        before = path.read_bytes()
        (self.repo / ".rig" / "routing.json").write_text('{"schema_version": 99}')
        result = self.cli("doctor")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("schema_version", result.stdout + result.stderr)
        self.assertEqual(path.read_bytes(), before)

    def test_schema_v2_opt_in_pick_and_doctor(self):
        (self.repo / ".rig" / "routing.json").write_text(json.dumps({
            "schema_version": 2,
            "execution": {"direct_parent_low_risk": True},
        }))
        doctor = self.cli("doctor")
        self.assertEqual(doctor.returncode, 0, doctor.stderr)
        self.assertIn("direct_parent_low_risk: true", doctor.stdout)
        pick = self.cli(
            "pick", "implement", "--case", "tiny label",
            "--complexity", "low", "--risk", "low", "--uncertainty", "low", "--json",
        )
        self.assertEqual(pick.returncode, 0, pick.stderr)
        body = json.loads(pick.stdout)
        self.assertEqual(body["spawn"], "native")
        self.assertTrue(body["parent_writes"])
        self.assertEqual(body["routing"]["execution_strategy"], "direct-parent")
        mcp = self.mcp("rig_pick", role="implement", complexity="low", risk="low", uncertainty="low")
        self.assertFalse(mcp.get("isError"), mcp)
        choice = json.loads(mcp["content"][0]["text"])
        self.assertEqual(choice["routing"]["execution_strategy"], "direct-parent")


if __name__ == "__main__":
    unittest.main()
