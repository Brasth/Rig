"""Task drafts validate real downstream contracts without executing work."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import acceptance_contract
import context_packages
import rig_mcp
import task_preparation as prep
import workflow_recipes


class TaskPreparation(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name).resolve()
        self.enterContext(patch.dict(os.environ, {"RIG_JOB_ID": "", "RIG_JOB_DIR": ""}))
        (self.repo / ".rig").mkdir()
        (self.repo / ".rig/harness.toml").write_text("[project]\nenabled=true\n")
        (self.repo / "app.py").write_text("pass\n")
        (self.repo / "design.md").write_text("Keep public signatures.\n")
        self.selection = {"task": "Fix saving", "files": ["app.py"],
                          "checks": [{"id": "tests", "argv": ["python3", "-m", "unittest"]}],
                          "references": [{"path": "design.md", "reason": "Design constraints"}]}

    def inventory(self):
        return {str(p.relative_to(self.repo)): (p.read_bytes(), p.stat().st_mtime_ns)
                for p in self.repo.rglob("*") if p.is_file()}

    def test_deterministic_read_only_cli_mcp_and_downstream_contract(self):
        before = self.inventory()
        with patch("subprocess.Popen", side_effect=AssertionError("no execution")), \
             patch("admission.transaction", side_effect=AssertionError("no admission")), \
             patch("context_packages.build", side_effect=AssertionError("no context writes")):
            expected = prep.prepare(self.repo, self.selection)
            self.assertEqual(prep.prepare(self.repo, self.selection), expected)
            actual = rig_mcp.call_tool("rig_task_prepare", {"repo": str(self.repo), "selection": self.selection})
            self.assertEqual(actual["structuredContent"], expected)
        self.assertTrue(expected["ready"])
        self.assertEqual(acceptance_contract.normalize(self.repo, expected["acceptance_contract"], expected["files"]),
                         expected["acceptance_contract"])
        self.assertEqual(context_packages.preview(self.repo, expected["context_selection"]), expected["context_preview"])
        self.assertEqual(self.inventory(), before)
        input_file = self.repo / "selection.json"
        input_file.write_text(json.dumps(self.selection))
        cli = subprocess.run([sys.executable, str(ROOT / "scripts/task_preparation.py"), "prepare",
                              "--repo", str(self.repo), "--file", str(input_file), "--json"], capture_output=True, text=True)
        self.assertEqual(cli.returncode, 0, cli.stderr)
        self.assertEqual(json.loads(cli.stdout), expected)

    def test_missing_information_and_manual_acceptance_new_files(self):
        incomplete = prep.prepare(self.repo, {"task": "Fix saving"})
        self.assertFalse(incomplete["ready"])
        self.assertEqual(len(incomplete["unresolved"]), 2)
        manual = prep.prepare(self.repo, {"task": "Add parser", "files": ["new.py"],
                                         "manual_criteria": ["Review parser signatures"]})
        self.assertTrue(manual["ready"])
        self.assertFalse((self.repo / "new.py").exists())
        self.assertEqual(manual["acceptance_contract"]["criteria"][0]["evidence_type"], "review_assertion")

    def test_all_recipes_and_disjoint_research_inputs(self):
        for recipe in workflow_recipes.NAMES:
            selected = {**self.selection, "recipe": recipe}
            if recipe == "research-implement":
                selected["research_sources"] = ["design.md"]
            result = prep.prepare(self.repo, selected)
            self.assertTrue(result["ready"])
            self.assertEqual(workflow_recipes.preview(self.repo, recipe, result["recipe_parameters"]), result["recipe_preview"])
        missing = prep.prepare(self.repo, {**self.selection, "recipe": "research-implement"})
        self.assertFalse(missing["ready"])
        with self.assertRaises(ValueError):
            prep.prepare(self.repo, {**self.selection, "recipe": "research-implement", "research_sources": ["app.py"]})

    def test_unsafe_invalid_and_private_inputs_fail(self):
        for delta in ({"files": ["../app.py"]}, {"files": [".env"]}, {"unexpected": True},
                      {"checks": [{"id": "t", "argv": []}]}, {"references": [{"path": "missing.md", "reason": "test"}]},
                      {"references": [{"path": "design.md", "reason": "password=never-copy-this"}]},
                      {"task": "password=never-copy-this"}, {"recipe": "unknown"}, {"constraints": "bad"}):
            with self.subTest(delta=delta), self.assertRaises(ValueError):
                prep.prepare(self.repo, {**self.selection, **delta})
        (self.repo / "linked.py").symlink_to(self.repo / "app.py")
        with self.assertRaises(ValueError):
            prep.prepare(self.repo, {**self.selection, "files": ["linked.py"]})

    def test_disabled_uninitialized_and_child_do_not_activate(self):
        for config in ("[project]\nenabled=false\n", None):
            path = self.repo / ".rig/harness.toml"
            if config is None:
                path.unlink()
            else:
                path.write_text(config)
            before = self.inventory()
            result = prep.prepare(self.repo, self.selection)
            self.assertFalse(result["ready"])
            self.assertIsNone(result["context_preview"])
            self.assertEqual(self.inventory(), before)
        with patch.dict(os.environ, {"RIG_JOB_ID": "child"}):
            with self.assertRaisesRegex(ValueError, "parent-only"):
                prep.prepare(self.repo, self.selection)
            self.assertTrue(rig_mcp.call_tool("rig_task_prepare", {"selection": self.selection})["isError"])
            self.assertNotIn("rig_task_prepare", {t["name"] for t in rig_mcp.listed_tools()})
