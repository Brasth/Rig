"""The built-in workers' argv equals what the shell builder produced (matrix.json)."""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import worker_commands  # noqa: E402

MATRIX = json.loads((ROOT / "tests/fixtures/worker-argv/matrix.json").read_text())
BRIEF = 'do "this" $HOME `x`\nsecond line\'s end'


class WorkerArgvMatrix(unittest.TestCase):
    def test_matrix_is_complete(self):
        self.assertEqual(len(MATRIX), 480)
        self.assertEqual({c["worker"] for c in MATRIX}, set(worker_commands.BUILTIN))

    def test_every_case_matches_the_shell_output(self):
        for c in MATRIX:
            kw = dict(work="/w/work", brief_file="/j/brief.md", brief_text=BRIEF, model=c["model"],
                      effort=c["effort"], role=c["role"], job_id="job-1", timeout_secs="1200",
                      resume=bool(c["resume"]), session="sess-123", binary=c["binary"], mcp_argv=c["mcp"])
            with self.subTest(**{k: c[k] for k in ("worker", "resume", "model", "effort", "role")}):
                if c.get("error"):
                    with self.assertRaises(worker_commands.Refuse):
                        worker_commands.build(c["worker"], **kw)
                    continue
                argv, sid = worker_commands.build(c["worker"], **kw)
                if c["session"] == "<uuid>":
                    self.assertRegex(sid, r"^[0-9a-f-]{36}$")
                    argv = ["<uuid>" if a == sid else a for a in argv]
                    sid = "<uuid>"
                self.assertEqual(sid, c["session"])
                self.assertEqual(argv, c["argv"])


if __name__ == "__main__":
    unittest.main()
