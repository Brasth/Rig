#!/usr/bin/env python3
"""Problems a job's checks found: the lines of compilers, linters and test runners that name a file and a line."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import job_problems  # noqa: E402
import local_api  # noqa: E402


class Parse(unittest.TestCase):
    def test_the_shapes_compilers_and_runners_write(self):
        text = """src/a.ts(12,5): error TS2322: Type 'string' is not assignable.
src/b.ts:3:1 - error TS1005: ';' expected.
lib/c.py:7:1: E302 expected 2 blank lines
  File "tests/test_x.py", line 44, in test_it
error[E0308]: mismatched types
 --> src/main.rs:4:5
warning: unused variable `x`
 --> src/main.rs:9:9
main.go:15:2: undefined: foo
https://example.com/doc:1: not a file
src/a.ts(12,5): error TS2322: Type 'string' is not assignable.
"""
        rows = job_problems.parse(text)
        got = [(r["file"], r["line"], r["col"], r["severity"]) for r in rows]
        self.assertEqual(got, [("src/a.ts", 12, 5, "error"), ("src/b.ts", 3, 1, "error"), ("lib/c.py", 7, 1, "error"),
                               ("tests/test_x.py", 44, 0, "error"), ("src/main.rs", 4, 5, "error"), ("src/main.rs", 9, 9, "warning"),
                               ("main.go", 15, 2, "error")])
        self.assertEqual(rows[4]["message"], "mismatched types", "rustc's message is the line before the arrow")
        self.assertEqual(rows[1]["message"], "TS1005: ';' expected.")


class OverAJob(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.repo = Path(self.td.name)
        (self.repo / ".git").mkdir()
        (self.repo / ".rig").mkdir()
        (self.repo / ".rig" / "harness.toml").write_text('parent = "claude"\n\n[project]\nenabled = true\n')
        self.folder = self.repo / ".rig" / "jobs" / "j1"
        (self.folder / "checks").mkdir(parents=True)
        (self.folder / "meta.json").write_text(json.dumps({"job_id": "j1", "worker": "codex", "status": "ok"}))
        (self.folder / "checks" / "c1.stdout.log").write_text("src/a.ts:3:1 - error TS1: bad\nsrc/a.ts:8:2 - warning TS2: meh\n")
        (self.folder / "checks" / "c1.json").write_text(json.dumps({"version": 1, "check_id": "c1", "requirement_id": "tsc", "sequence": 1,
                                                                    "exit_code": 2, "stdout": {"path": "checks/c1.stdout.log"}}))
        (self.folder / "checks" / "c2.json").write_text(json.dumps({"version": 1, "check_id": "c2", "requirement_id": "lint", "sequence": 2,
                                                                    "exit_code": 0}))

    def tearDown(self):
        self.td.cleanup()

    def test_only_failed_checks_count_and_the_api_serves_them(self):
        found = job_problems.problems(self.repo, "j1")
        self.assertEqual((found["errors"], found["warnings"]), (1, 1))
        self.assertEqual([(p["file"], p["line"], p["check"]) for p in found["problems"]], [("src/a.ts", 3, "tsc"), ("src/a.ts", 8, "tsc")])
        self.assertEqual(job_problems.latest(self.repo)["job_id"], "j1")
        api = local_api.Api(self.repo, "t" * 64)
        call = lambda params: api.dispatch({"jsonrpc": "2.0", "id": 1, "method": "job.problems", "params": params, "token": "t" * 64})
        self.assertEqual(call({"id": "j1"})["result"]["errors"], 1)
        self.assertEqual(call({})["result"]["job_id"], "j1")
        self.assertEqual(call({"id": 3})["error"]["code"], local_api.INVALID_PARAMS)


if __name__ == "__main__":
    unittest.main()
