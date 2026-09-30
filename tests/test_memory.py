#!/usr/bin/env python3
import sys
import hashlib
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
import tempfile
import unittest
from pathlib import Path

from repo_test_support import initialize_project

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import memory  # noqa: E402


class MemoryFile(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.repo = Path(self.td.name)
        initialize_project(self.repo)

    def tearDown(self):
        self.td.cleanup()

    def test_add_show_dedupe(self):
        self.assertIn("no memory yet", memory.show_memory(self.repo))
        self.assertEqual(memory.add_memory(self.repo, "Codex sandbox must write ~/.grok"), "added")
        self.assertEqual(
            memory.add_memory(self.repo, "codex sandbox must write ~/.grok"),
            "exists",
        )
        shown = memory.show_memory(self.repo)
        self.assertIn("Codex sandbox must write ~/.grok", shown)
        self.assertEqual(shown.count("sandbox"), 1)
        self.assertEqual(memory.fact_count(self.repo), 1)

    def test_strips_bullet_and_skips_empty(self):
        self.assertEqual(memory.add_memory(self.repo, "  -  pin full Claude ids  "), "added")
        self.assertEqual(memory.add_memory(self.repo, "-"), "skip empty fact")
        self.assertEqual(memory.add_memory(self.repo, "   "), "skip empty fact")
        self.assertIn("- pin full Claude ids", memory.show_memory(self.repo))

    def test_guarded_replace_remove_and_stale_hash(self):
        memory.add_memory(self.repo, "Old guidance")
        memory.add_memory(self.repo, "Keep this")
        digest = memory.memory_view(self.repo)["sha256"]
        self.assertEqual(memory.edit_memory(self.repo, "old guidance", digest, "New guidance"), "replaced")
        self.assertEqual(memory.memory_view(self.repo)["facts"], ["New guidance", "Keep this"])
        with self.assertRaisesRegex(ValueError, "changed"):
            memory.edit_memory(self.repo, "Keep this", digest)
        digest = memory.memory_view(self.repo)["sha256"]
        with self.assertRaisesRegex(ValueError, "duplicates"):
            memory.edit_memory(self.repo, "Keep this", digest, "New guidance")
        with self.assertRaisesRegex(ValueError, "limit"):
            memory.edit_memory(self.repo, "Keep this", digest, "x" * 241)
        self.assertEqual(memory.edit_memory(self.repo, "Keep this", digest), "removed")

    def test_missing_ambiguous_child_disabled_and_linked_file(self):
        empty = hashlib.sha256(b"").hexdigest()
        with self.assertRaisesRegex(ValueError, "missing"):
            memory.edit_memory(self.repo, "missing", empty)
        path = memory.memory_path(self.repo)
        path.write_text("# MEMORY\n\n- Duplicate\n- duplicate\n")
        digest = memory.memory_view(self.repo)["sha256"]
        with self.assertRaisesRegex(ValueError, "ambiguous"):
            memory.edit_memory(self.repo, "Duplicate", digest)
        with patch.dict(os.environ, {"RIG_JOB_ID": "child"}):
            with self.assertRaisesRegex(ValueError, "parent-only"):
                memory.edit_memory(self.repo, "Duplicate", digest)
        (self.repo / ".rig/harness.toml").write_text("[project]\nenabled=false\n")
        with self.assertRaisesRegex(ValueError, "disabled"):
            memory.add_memory(self.repo, "Blocked")
        (self.repo / ".rig/harness.toml").write_text("[project]\nenabled=true\n")
        path.unlink()
        target = self.repo / "target.md"
        target.write_text("- Keep\n")
        path.symlink_to(target)
        with self.assertRaisesRegex(ValueError, "regular"):
            memory.add_memory(self.repo, "Unsafe")
        self.assertEqual(target.read_text(), "- Keep\n")

    def test_parallel_process_adds_and_compare_and_swap(self):
        command = [sys.executable, str(ROOT / "scripts/memory.py"), "--repo", str(self.repo), "add"]
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda i: subprocess.run(command + [f"Fact {i}"], capture_output=True), range(20)))
        self.assertTrue(all(result.returncode == 0 for result in results))
        self.assertEqual(memory.fact_count(self.repo), 20)
        digest = memory.memory_view(self.repo)["sha256"]
        command = [sys.executable, str(ROOT / "scripts/memory.py"), "--repo", str(self.repo),
                   "replace", "--old", "Fact 0", "--expected-sha256", digest, "--new"]
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda new: subprocess.run(command + [new], capture_output=True), ["First", "Second"]))
        self.assertEqual(sorted(result.returncode for result in results), [0, 1])
        self.assertEqual(memory.fact_count(self.repo), 20)

    def test_cli_and_mcp_memory_edits(self):
        import rig_mcp
        with patch.dict(os.environ, {"RIG_JOB_ID": "", "RIG_JOB_DIR": ""}):
            memory.add_memory(self.repo, "Use recording")
            view = rig_mcp.call_tool("rig_memory", {"repo": str(self.repo)})
            changed = rig_mcp.call_tool("rig_memory_replace", {"repo": str(self.repo), "old": "Use recording",
                                 "new": "Use rig_cu_record", "expected_sha256": view["structuredContent"]["sha256"]})
            self.assertFalse(changed.get("isError"), changed)
            digest = memory.memory_view(self.repo)["sha256"]
            result = subprocess.run([str(ROOT / "bin/rig"), "memory", "remove", "--fact", "Use rig_cu_record",
                                     "--expected-sha256", digest], cwd=self.repo, capture_output=True,
                                    env={**os.environ, "RIG_HOME": str(ROOT), "RIG_INSTALL_TRANSACTION": "1"})
            self.assertEqual(result.returncode, 0, result.stderr)
        with patch.dict(os.environ, {"RIG_JOB_ID": "child"}):
            self.assertTrue(rig_mcp.call_tool("rig_memory_remove", {"repo": str(self.repo), "fact": "anything", "expected_sha256": digest}).get("isError"))

    def test_unicode_hash_matches_exact_bytes(self):
        memory.add_memory(self.repo, "Giữ hướng dẫn 🛠")
        view = memory.memory_view(self.repo)
        self.assertEqual(view["sha256"], hashlib.sha256(memory.memory_path(self.repo).read_bytes()).hexdigest())
        memory.edit_memory(self.repo, "Giữ hướng dẫn 🛠", view["sha256"], "Dùng kiểm tra ✓")
        self.assertEqual(memory.memory_view(self.repo)["facts"], ["Dùng kiểm tra ✓"])

    def test_caps_at_120_lines(self):
        for i in range(200):
            memory.add_memory(self.repo, f"fact number {i:03d} stays short")
        text = memory.memory_path(self.repo).read_text()
        self.assertLessEqual(text.count("\n") + (0 if text.endswith("\n") else 1), memory.MAX_LINES)
        self.assertIn("fact number 199", text)
        self.assertNotIn("fact number 000", text)
        self.assertGreater(memory.fact_count(self.repo), 50)


if __name__ == "__main__":
    unittest.main()
