"""Product contracts for Jev fallback/settings and the MiMo worker gate."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import child_mcp  # noqa: E402
import harness  # noqa: E402
import jev_provider  # noqa: E402
import jev_settings  # noqa: E402
import routing_policy  # noqa: E402


class JevPickerTests(unittest.TestCase):
    def _repo(self) -> Path:
        path = Path(tempfile.mkdtemp())
        (path / ".rig").mkdir()
        (path / ".rig" / "routing.json").write_text(json.dumps({
            "schema_version": 3,
            "picker": {"engine": "jev", "local_policy": "scored-v1", "objective": "balanced"},
        }))
        return path

    def test_jev_can_select_only_from_hard_filtered_candidates(self):
        repo = self._repo()
        with patch("jev_provider.choice", return_value={"id": "claude-sonnet-5-medium"}) as choose:
            picked = routing_policy.smart_pick("codex", ["grok", "claude"], "implement", "fix failing test", repo=repo)
        self.assertEqual(picked["worker"], "claude")
        self.assertEqual(picked["routing"]["picker"]["engine"], "jev")
        self.assertEqual(picked["routing"]["picker"]["selection_source"], "jev")
        sent = choose.call_args.kwargs
        self.assertLessEqual(len(sent["summary"]), 2000)
        self.assertNotIn("case", sent)

    def test_jev_fault_falls_back_to_local_and_sidecar_has_no_task(self):
        repo = self._repo()
        secret_case = "the uniquely sensitive task wording"
        with patch("jev_provider.choice", side_effect=jev_provider.JevError("transport-failed")):
            picked = routing_policy.smart_pick("codex", ["grok", "claude"], "implement", secret_case, repo=repo)
        picker = picked["routing"]["picker"]
        self.assertEqual(picker["engine"], "local")
        self.assertEqual(picker["fallback"], "jev-unavailable")
        self.assertNotIn(secret_case, json.dumps(picker))

    def test_project_settings_only_change_harness_routing(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            jev_settings.update_project(repo, engine="jev", objective="quality")
            parsed = harness.parse_harness(repo / ".rig" / "harness.toml")
            self.assertEqual(parsed["routing"]["engine"], "jev")
            self.assertEqual(parsed["routing"]["objective"], "quality")


class MimoWorkerTests(unittest.TestCase):
    def test_mimo_mcp_readiness_uses_job_scoped_config_support(self):
        self.assertIn("mimo", harness.WORKERS)
        with patch.object(child_mcp, "worker_binary", return_value="/tmp/mimo"):
            ready, reason = child_mcp.worker_mcp_ready("mimo")
        self.assertEqual((ready, reason), (True, ""))

    def test_mimo_cli_is_still_required_for_job_scoped_mcp(self):
        with patch.object(child_mcp, "worker_binary", return_value=""):
            ready, reason = child_mcp.worker_mcp_ready("mimo")
        self.assertFalse(ready)
        self.assertIn("binary 'mimo' not on PATH", reason)
