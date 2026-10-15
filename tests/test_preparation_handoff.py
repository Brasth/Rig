#!/usr/bin/env python3
"""Structured handoff inputs: optional fields, missing vs [], readiness gaps and readable briefs."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import preparation_handoff as handoff  # noqa: E402
import preparation_inputs as inputs  # noqa: E402
import task_preparation as prep  # noqa: E402
import worker_brief  # noqa: E402

BASE = {"task": "Fix saving", "files": ["app.py", "new_helper.py"],
        "checks": [{"id": "tests", "argv": ["python3", "-m", "unittest", "it's quoted"], "cwd": "."}]}
FULL = {**BASE,
        "findings": [{"statement": "save() drops the payload", "status": "verified",
                      "evidence": [{"path": "app.py", "symbol": "save", "line": 2}]},
                     {"statement": "Callers may ignore errors", "status": "hypothesis"}],
        "decisions": [{"choice": "Write atomically", "reason": "Avoid torn files"}],
        "changes": [{"path": "app.py", "symbol": "save", "change": "Write via a temp file"},
                    {"path": "new_helper.py", "change": "Add atomic_write helper following util.py"}],
        "reading_order": [{"path": "app.py", "symbol": "save", "reason": "Entrypoint"},
                          {"path": "util.py", "reason": "Pattern for the new helper"}],
        "unknowns": [], "remaining_work": "low"}


class Handoff(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name).resolve()
        self.enterContext(patch.dict(os.environ, {"RIG_JOB_ID": "", "RIG_JOB_DIR": ""}))
        (self.repo / ".rig").mkdir()
        (self.repo / ".rig/harness.toml").write_text("[project]\nenabled=true\n")
        (self.repo / "app.py").write_text("def save(data):\n    pass\n")
        (self.repo / "util.py").write_text("def helper():\n    pass\n")

    def test_old_inputs_keep_semantics_and_omit_new_fields(self):
        draft = inputs.normalize(self.repo, BASE)
        for field in handoff.FIELDS:
            self.assertNotIn(field, draft)
        result = prep.prepare(self.repo, BASE)
        self.assertTrue(result["ready"])
        self.assertFalse(result["execution_ready"])
        self.assertEqual({row["code"] for row in result["readiness_gaps"]},
                         {"change-missing", "entrypoint-missing", "decisions-missing", "unknowns-missing"})
        self.assertNotIn("## Findings", result["brief"])
        self.assertNotIn("## Decisions", result["brief"])

    def test_execution_ready_requires_every_condition(self):
        self.assertTrue(prep.prepare(self.repo, FULL)["execution_ready"])
        cases = {
            "change-missing": {"changes": FULL["changes"][:1]},
            "entrypoint-missing": {"reading_order": FULL["reading_order"][1:]},
            "decisions-missing": {"decisions": None},
            "unknowns-missing": {"unknowns": None},
            "unknowns-open": {"unknowns": ["Which encoding?"]},
            "acceptance-missing": {"checks": []},
        }
        for code, delta in cases.items():
            selection = {**FULL, **{k: v for k, v in delta.items() if v is not None}}
            for key in [k for k, v in delta.items() if v is None]:
                selection.pop(key)
            with self.subTest(code=code):
                result = prep.prepare(self.repo, selection)
                self.assertFalse(result["execution_ready"])
                self.assertIn(code, {row["code"] for row in result["readiness_gaps"]})
        # A new writer file needs a change entry but no entrypoint; it may cite a pattern file.
        result = prep.prepare(self.repo, FULL)
        self.assertFalse((self.repo / "new_helper.py").exists())
        self.assertNotIn("entrypoint-missing", {row["code"] for row in result["readiness_gaps"]})

    def test_missing_differs_from_explicit_empty(self):
        missing = inputs.normalize(self.repo, {**BASE})
        explicit = inputs.normalize(self.repo, {**BASE, "unknowns": [], "decisions": []})
        self.assertNotIn("unknowns", missing)
        self.assertEqual(explicit["unknowns"], [])
        brief = handoff.render(explicit)
        self.assertIn("- Unknowns: none declared.", brief)
        self.assertIn("- No design decision is required.", brief)
        self.assertNotIn("Unknowns", handoff.render(missing))

    def test_readable_brief_sections_and_exact_argv(self):
        brief = prep.prepare(self.repo, FULL)["brief"]
        self.assertTrue(brief.startswith(worker_brief.PREAMBLE))
        self.assertEqual(brief.count(worker_brief.MARKER), 1)
        order = ["## Goal", "## Findings", "## Decisions", "## Changes", "## Reading order",
                 "## Boundaries", "## Acceptance", "## Escalation"]
        self.assertEqual([brief.index(name) for name in order], sorted(brief.index(name) for name in order))
        self.assertIn("[verified] save() drops the payload", brief)
        self.assertIn("Evidence: app.py:2 (save)", brief)
        self.assertIn("[hypothesis - validate before relying on it]", brief)
        self.assertIn('argv=["python3", "-m", "unittest", "it\'s quoted"] cwd="."', brief)
        self.assertIn("rig_job_coordination_request", brief)
        self.assertEqual(brief, brief.strip())

    def test_invalid_unsafe_or_malformed_inputs_fail(self):
        bad = (
            {"findings": [{"statement": "x", "status": "verified"}]},
            {"findings": [{"statement": "x", "status": "certain", "evidence": [{"path": "app.py"}]}]},
            {"findings": [{"statement": "x", "status": "hypothesis", "evidence": [{"path": "../etc/passwd"}]}]},
            {"findings": [{"statement": "x", "status": "hypothesis", "evidence": [{"path": ".env"}]}]},
            {"findings": [{"statement": "x", "status": "hypothesis", "evidence": [{"path": "app.py", "line": 0}]}]},
            {"decisions": [{"choice": "x"}]},
            {"changes": [{"path": "other.py", "change": "outside writer scope"}]},
            {"changes": [{"path": "app.py", "change": "x", "extra": 1}]},
            {"reading_order": [{"path": "*.py", "reason": "glob"}]},
            {"reading_order": [{"path": "app.py", "symbol": "a\nb", "reason": "x"}]},
            {"unknowns": "none"},
            {"unknowns": ["token=abcdefghijklmnop"]},
            {"remaining_work": "minimal"},
            {"remaining_work": ""},
            {"decisions": [{"choice": "x", "reason": "y"}] * 33},
        )
        for delta in bad:
            with self.subTest(delta=delta), self.assertRaises(ValueError):
                prep.prepare(self.repo, {**BASE, **delta})
        with self.assertRaises(ValueError):
            prep.prepare(self.repo, {**FULL, "reading_order": [{"path": "missing.py", "reason": "absent"}]})

    def test_size_bound_is_not_silently_truncated(self):
        huge = {**BASE, "unknowns": ["u" * 2000] * 17}
        with self.assertRaisesRegex(ValueError, "32 KiB"):
            prep.prepare(self.repo, huge)

    def test_wrapper_marker_matches_canonical_preamble(self):
        detect = (ROOT / "scripts/detect-binaries.sh").read_text()
        self.assertIn(worker_brief.MARKER, detect)
        self.assertIn("worker_brief.py", (ROOT / "scripts/run-worker.sh").read_text())
        self.assertIn(worker_brief.MARKER, (ROOT / "scripts/run-worker.sh").read_text())
        self.assertEqual(worker_brief.with_preamble("legacy text"), worker_brief.PREAMBLE + "\n\nlegacy text")
        prepared = prep.prepare(self.repo, FULL)["brief"]
        self.assertEqual(worker_brief.with_preamble(prepared), prepared)
        self.assertIn(json.dumps(worker_brief.PREAMBLE)[1:40], json.dumps(prepared))


if __name__ == "__main__":
    unittest.main()
