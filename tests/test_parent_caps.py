#!/usr/bin/env python3
"""The native-parity table: complete, honest, and the generated doc is current."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import parent_caps  # noqa: E402
import rig_chat  # noqa: E402


class ParentCaps(unittest.TestCase):
    def test_every_parent_rig_chat_can_run_has_a_complete_row_set(self):
        self.assertEqual(set(parent_caps.CAPS), set(rig_chat.HEADLESS))
        keys = [k for k, _ in parent_caps.FEATURES]
        for parent, rows in parent_caps.CAPS.items():
            self.assertEqual(list(rows), keys, parent)
            for key, (level, note) in rows.items():
                self.assertIn(level, ("yes", "partial", "no"), (parent, key))
                self.assertTrue(note.strip() and not note.endswith("."), (parent, key, "a note says how, without a full stop"))

    def test_the_table_agrees_with_what_the_code_does(self):
        for parent in rig_chat.HEADLESS:
            asks = parent_caps.CAPS[parent]["approve"][0] == "yes"
            self.assertEqual(asks, parent in rig_chat.ASKS, f"{parent}: approvals in the table and in ASKS must agree")
            resumes = parent_caps.CAPS[parent]["resume"][0] == "yes"
            self.assertEqual(resumes, parent in rig_chat.RESUMES, f"{parent}: resume in the table and in RESUMES must agree")

    def test_the_generated_doc_is_current(self):
        self.assertEqual((ROOT / "docs" / "native-parity.md").read_text(), parent_caps.doc(),
                         "run: python3 scripts/parent_caps.py doc > docs/native-parity.md")

    def test_limits_lead_with_what_cannot_be_done_and_status_carries_them(self):
        grok = parent_caps.limits("grok")
        self.assertTrue(grok[0].startswith("Asks before running a command or editing"), grok)
        self.assertEqual(parent_caps.limits("nobody"), [])
        self.assertLessEqual(len(parent_caps.limits("agy", 3)), 3)


if __name__ == "__main__":
    unittest.main()
