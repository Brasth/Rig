#!/usr/bin/env python3
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def upsert(path: Path, block: str) -> str:
    script = f"""
source "{ROOT / "scripts" / "detect-binaries.sh"}"
rig_upsert_marked_block "{path}" "<!-- rig:start -->" "<!-- rig:end -->" "$(cat <<'EOF'
{block}
EOF
)"
"""
    proc = subprocess.run(
        ["bash", "-lc", script],
        text=True,
        capture_output=True,
        check=False,
        env={**os.environ, "RIG_HOME": str(ROOT)},
    )
    if proc.returncode != 0:
        raise AssertionError(proc.stderr)
    return proc.stdout.strip()


BLOCK = """<!-- rig:start -->
MUST use Rig.
<!-- rig:end -->"""


class UpsertTop(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.path = Path(self.td.name) / "AGENTS.md"

    def tearDown(self):
        self.td.cleanup()

    def test_prepends_when_missing(self):
        self.path.write_text("# later\n\nedit locally\n")
        action = upsert(self.path, BLOCK)
        self.assertEqual(action, "prepended")
        text = self.path.read_text()
        self.assertTrue(text.startswith("<!-- rig:start -->"))
        self.assertIn("edit locally", text)
        self.assertLess(text.find("MUST use Rig"), text.find("edit locally"))

    def test_writes_unicode_when_locale_is_c(self):
        block = (
            "<!-- rig:start -->\n"
            "stay \u2192 parent. Follow pick JSON \u2014 do not ask.\n"
            "<!-- rig:end -->"
        )
        script = f"""
source "{ROOT / "scripts" / "detect-binaries.sh"}"
rig_upsert_marked_block "{self.path}" "<!-- rig:start -->" "<!-- rig:end -->" "$(cat <<'EOF'
{block}
EOF
)"
"""
        proc = subprocess.run(
            ["bash", "-lc", script],
            text=True,
            capture_output=True,
            check=False,
            env={
                **os.environ,
                "RIG_HOME": str(ROOT),
                "LANG": "C",
                "LC_ALL": "C",
                "PYTHONUTF8": "0",
            },
        )
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        self.assertEqual(proc.stdout.strip(), "wrote")
        text = self.path.read_text(encoding="utf-8")
        self.assertIn("\u2192", text)
        self.assertIn("\u2014", text)
        self.assertTrue(self.path.exists())

    def test_moves_existing_block_to_top(self):
        self.path.write_text(
            "# VM\n\nMake code changes locally.\n\n"
            "<!-- rig:start -->\nold\n<!-- rig:end -->\n"
        )
        action = upsert(self.path, BLOCK)
        self.assertEqual(action, "moved")
        text = self.path.read_text()
        self.assertTrue(text.startswith("<!-- rig:start -->"))
        self.assertIn("MUST use Rig", text)
        self.assertNotIn("old", text)
        self.assertLess(text.find("MUST use Rig"), text.find("Make code changes locally"))


if __name__ == "__main__":
    unittest.main()
