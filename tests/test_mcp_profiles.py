#!/usr/bin/env python3
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import doctor  # noqa: E402
import harness  # noqa: E402
import rig_mcp  # noqa: E402
from mcp_tools import schemas  # noqa: E402


def repo_with(root: Path, body: str) -> Path:
    (root / ".git").mkdir(exist_ok=True)
    (root / ".rig").mkdir(exist_ok=True)
    (root / ".rig" / "harness.toml").write_text(body)
    return root


class ProfileDefinition(unittest.TestCase):
    def test_core_is_a_strict_subset_of_full_and_excludes_only_real_tools(self):
        full = schemas.profile_tool_names("full")
        core = schemas.profile_tool_names("core")
        self.assertEqual(full, schemas.PARENT_TOOL_NAMES)
        self.assertTrue(core < full)
        self.assertTrue(schemas.CORE_EXCLUDED_TOOLS <= full)
        self.assertEqual(full - core, schemas.CORE_EXCLUDED_TOOLS)

    def test_unknown_profile_is_full(self):
        self.assertEqual(schemas.profile_tool_names("bogus"), schemas.PARENT_TOOL_NAMES)

    def test_core_keeps_every_step_of_the_launch_wait_accept_path(self):
        core = schemas.profile_tool_names("core")
        for name in ("rig_session", "rig_pick", "rig_job_launch", "rig_job_wait", "rig_job_check",
                     "rig_job_accept", "rig_job_close", "rig_queue_claim", "rig_workflow_create",
                     "rig_workflow_advance", "rig_workflow_wait", "rig_recovery_guide", "rig_doctor",
                     "rig_job_break_glass_close", "rig_task_prepare", "rig_memory"):
            self.assertIn(name, core)


class ProfileSelection(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.root = Path(self.td.name)
        env = {k: v for k, v in os.environ.items()
               if k not in {"RIG_JOB_ID", "RIG_JOB_DIR", "RIG_MCP_PROFILE", "RIG_REPO"}}
        self.env = mock.patch.dict(os.environ, env, clear=True)
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.td.cleanup()

    def names(self):
        return {tool["name"] for tool in rig_mcp.listed_tools()}

    def test_parse_defaults_values_and_errors(self):
        parse = lambda body: harness.parse_harness(harness.harness_path(repo_with(self.root, body)))["mcp"]  # noqa: E731
        self.assertEqual(parse("[workers]\ngrok = true\n"), {"profile": "full", "error": ""})
        self.assertEqual(parse('[mcp]\nprofile = "core"\n'), {"profile": "core", "error": ""})
        self.assertEqual(parse("[mcp]\nprofile = 'CORE'  # note\n")["profile"], "core")
        bad = parse('[mcp]\nprofile = "tiny"\n')
        self.assertEqual(bad["profile"], "full")
        self.assertIn("unknown [mcp] profile", bad["error"])

    def test_default_lists_billing_and_core_config_hides_it(self):
        os.environ["RIG_REPO"] = str(repo_with(self.root, "[project]\nenabled = true\n"))
        self.assertIn("rig_billing_report", self.names())
        repo_with(self.root, '[project]\nenabled = true\n\n[mcp]\nprofile = "core"\n')
        names = self.names()
        self.assertNotIn("rig_billing_report", names)
        self.assertNotIn("rig_benchmark_create", names)
        self.assertIn("rig_job_launch", names)

    def test_env_overrides_config_and_junk_env_is_ignored(self):
        os.environ["RIG_REPO"] = str(repo_with(self.root, '[mcp]\nprofile = "core"\n'))
        os.environ["RIG_MCP_PROFILE"] = "full"
        self.assertIn("rig_billing_report", self.names())
        os.environ["RIG_MCP_PROFILE"] = "nonsense"
        self.assertNotIn("rig_billing_report", self.names())

    def test_child_surface_is_always_the_same_eight_tools(self):
        for profile in ("core", "full"):
            with mock.patch.dict(os.environ, {"RIG_JOB_ID": "j1", "RIG_MCP_PROFILE": profile}):
                self.assertEqual([t["name"] for t in rig_mcp.listed_tools()], list(schemas.CHILD_TOOL_ORDER))
        self.assertEqual(len(schemas.CHILD_TOOL_ORDER), 8)

    def test_hidden_tools_stay_callable_because_profiles_only_filter_the_list(self):
        os.environ["RIG_MCP_PROFILE"] = "core"
        repo = repo_with(self.root, "[project]\nenabled = true\n")
        outcome = rig_mcp.call_tool("rig_billing_report", {"repo": str(repo)})
        self.assertNotIn("is not a parent tool", outcome["content"][0]["text"])

    def test_doctor_reports_profile_as_optional(self):
        repo = repo_with(self.root, '[project]\nenabled = true\n\n[mcp]\nprofile = "core"\n')
        report = doctor.build_report(repo)
        row = next(c for c in report["checks"] if c["name"] == "mcp-profile")
        self.assertEqual((row["status"], row["required"]), ("ready", False))
        self.assertIn("core profile", row["detail"])
        repo_with(self.root, '[mcp]\nprofile = "tiny"\n')
        row = next(c for c in doctor.build_report(repo)["checks"] if c["name"] == "mcp-profile")
        self.assertEqual(row["status"], "missing")


if __name__ == "__main__":
    unittest.main()
