"""Characterized public contracts survive the MCP handler extraction."""
import ast
import copy
import hashlib
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import rig_mcp
from mcp_tools import load_registry


class Modularization(unittest.TestCase):
    def test_existing_schemas_and_order_are_unchanged(self):
        old = copy.deepcopy([tool for tool in rig_mcp.TOOLS if tool["name"] not in {"rig_recovery_guide", "rig_task_prepare"}])
        # Optional preparation inputs are the only additive change to these existing schemas.
        additive = {"rig_pick": "preparation", "rig_session": "preparation", "rig_job_start": "preparation",
                    "rig_job_launch": "preparation", "rig_workflow_extend": "preparations"}
        for tool in old:
            if tool["name"] in additive:
                self.assertIn(additive[tool["name"]], tool["inputSchema"]["properties"])
                tool["inputSchema"]["properties"].pop(additive[tool["name"]])
        fingerprint = lambda value: hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        self.assertEqual(fingerprint(old), "47f45633a9f8aad1a912733f20d90deb1e6ac5aede68b3731a5f60f8a80c59be")
        self.assertEqual(fingerprint(rig_mcp.CHILD_TOOLS), "6c4f23c0e69f6b2363b451c240f60733ac60576538f871afa91478e259fcb368")

    def test_every_declared_tool_has_one_handler(self):
        self.assertEqual(set(load_registry()), {tool["name"] for tool in rig_mcp.TOOLS + rig_mcp.CHILD_TOOLS})

    def test_domain_modules_do_not_import_facade(self):
        for source in (ROOT / "scripts/mcp_tools").glob("*.py"):
            for node in ast.walk(ast.parse(source.read_text())):
                if isinstance(node, ast.Import):
                    self.assertNotIn("rig_mcp", [item.name for item in node.names])
                elif isinstance(node, ast.ImportFrom):
                    self.assertNotEqual(node.module, "rig_mcp")

    def test_live_facade_dependencies_remain_patchable(self):
        with patch.object(rig_mcp, "_ok", return_value={"patched": True}):
            value = rig_mcp.call_tool("rig_status", {"repo": str(ROOT)})
        self.assertEqual(value, {"patched": True})
