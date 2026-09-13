#!/usr/bin/env python3
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import diagram_preview  # noqa: E402

RIG = ROOT / "bin" / "rig"


def run_rig(*args: str, cwd: Path | None = None, env: dict | None = None) -> subprocess.CompletedProcess:
    merged = os.environ.copy()
    merged["RIG_HOME"] = str(ROOT)
    merged["PATH"] = f"{ROOT / 'bin'}:{merged.get('PATH', '')}"
    merged["RIG_SKIP_UPDATE_CHECK"] = "1"
    merged["RIG_SKIP_MODEL_CATALOG"] = "1"
    if env:
        merged.update(env)
    return subprocess.run(
        [str(RIG), *args],
        cwd=cwd or ROOT,
        env=merged,
        text=True,
        capture_output=True,
        check=False,
    )


class DiagramPreviewParse(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.dir = Path(self.td.name)

    def tearDown(self):
        self.td.cleanup()

    def write(self, name: str, text: str) -> Path:
        path = self.dir / name
        path.write_text(text, encoding="utf-8")
        return path

    def parse(self, source: Path) -> list[diagram_preview.Diagram]:
        return diagram_preview.load_diagrams(source)

    def test_raw_mmd(self):
        source = self.write("flow.mmd", "flowchart TD\n  A --> B\n")
        diagrams = self.parse(source)
        self.assertEqual(len(diagrams), 1)
        self.assertEqual(diagrams[0].source, "flowchart TD\n  A --> B")
        self.assertEqual(diagrams[0].index, 1)
        self.assertIsNone(diagrams[0].start_line)

    def test_raw_mermaid_suffix(self):
        source = self.write("chart.mermaid", "sequenceDiagram\n  A->>B: hi\n")
        diagrams = self.parse(source)
        self.assertEqual(diagrams[0].source, "sequenceDiagram\n  A->>B: hi")

    def test_markdown_fences_and_tilde(self):
        source = self.write(
            "doc.md",
            "# Title\n\n"
            "```mermaid\nflowchart LR\n  A --> B\n```\n\n"
            "~~~mermaid\nsequenceDiagram\n  X->>Y: z\n~~~\n",
        )
        diagrams = self.parse(source)
        self.assertEqual(len(diagrams), 2)
        self.assertEqual(diagrams[0].source, "flowchart LR\n  A --> B")
        self.assertEqual(diagrams[1].source, "sequenceDiagram\n  X->>Y: z")
        self.assertEqual(diagrams[0].start_line, 3)
        self.assertEqual(diagrams[0].label, "Diagram 1 (line 3)")

    def test_multiple_diagrams(self):
        blocks = "\n\n".join(f"```mermaid\nflowchart TD\n  N{i} --> N{i}x\n```" for i in range(3))
        source = self.write("many.md", blocks)
        diagrams = self.parse(source)
        self.assertEqual([d.index for d in diagrams], [1, 2, 3])

    def test_skips_nested_mermaid_inside_non_mermaid_fence(self):
        source = self.write(
            "nested.md",
            "Before\n\n"
            "````text\n"
            "Example:\n"
            "```mermaid\n"
            "flowchart TD\n"
            "  Fake --> Diagram\n"
            "```\n"
            "````\n\n"
            "```mermaid\n"
            "flowchart LR\n"
            "  Real --> One\n"
            "```\n",
        )
        diagrams = self.parse(source)
        self.assertEqual(len(diagrams), 1)
        self.assertEqual(diagrams[0].source, "flowchart LR\n  Real --> One")
        self.assertNotIn("Fake", diagrams[0].source)

    def test_longer_delimiters_and_multiple_diagrams(self):
        source = self.write(
            "long-fence.md",
            "````mermaid\n"
            "flowchart TD\n"
            "  A --> B\n"
            "````\n\n"
            "~~~~~mermaid\n"
            "sequenceDiagram\n"
            "  X->>Y: hi\n"
            "~~~~~\n\n"
            "```mermaid\n"
            "pie title Share\n"
            "  \"A\" : 60\n"
            "  \"B\" : 40\n"
            "```\n",
        )
        diagrams = self.parse(source)
        self.assertEqual(len(diagrams), 3)
        self.assertEqual(diagrams[0].source, "flowchart TD\n  A --> B")
        self.assertEqual(diagrams[0].start_line, 1)
        self.assertEqual(diagrams[1].source, "sequenceDiagram\n  X->>Y: hi")
        self.assertEqual(diagrams[2].index, 3)
        self.assertIn("pie title Share", diagrams[2].source)

    def test_empty_mermaid_fence_rejected(self):
        source = self.write(
            "empty-fence.md",
            "# Doc\n\n"
            "```mermaid\n"
            "```\n",
        )
        with self.assertRaises(diagram_preview.DiagramError) as ctx:
            self.parse(source)
        self.assertIn("empty mermaid fence", str(ctx.exception))
        self.assertIn("line 3", str(ctx.exception))

    def test_whitespace_only_mermaid_fence_rejected(self):
        source = self.write(
            "ws-fence.md",
            "intro\n"
            "~~~mermaid\n"
            "  \n"
            "\t\n"
            "~~~\n",
        )
        with self.assertRaises(diagram_preview.DiagramError) as ctx:
            self.parse(source)
        self.assertIn("empty mermaid fence", str(ctx.exception))
        self.assertIn("line 2", str(ctx.exception))

    def test_close_requires_same_or_longer_marker_without_info(self):
        source = self.write(
            "close-rules.md",
            "````mermaid\n"
            "flowchart TD\n"
            "  A --> B\n"
            "```\n"
            "info still in fence\n"
            "````\n",
        )
        diagrams = self.parse(source)
        self.assertEqual(len(diagrams), 1)
        self.assertIn("```", diagrams[0].source)
        self.assertIn("info still in fence", diagrams[0].source)

    def test_missing_file(self):
        missing = self.dir / "nope.md"
        with self.assertRaises(diagram_preview.DiagramError) as ctx:
            self.parse(missing)
        self.assertIn("file not found", str(ctx.exception))

    def test_empty_file(self):
        source = self.write("empty.mmd", "")
        with self.assertRaises(diagram_preview.DiagramError) as ctx:
            self.parse(source)
        self.assertIn("empty", str(ctx.exception))

    def test_markdown_without_fences(self):
        source = self.write("plain.md", "# no diagrams here\n")
        with self.assertRaises(diagram_preview.DiagramError) as ctx:
            self.parse(source)
        self.assertIn("no mermaid diagrams", str(ctx.exception))

    def test_unclosed_fence(self):
        source = self.write("open.md", "```mermaid\nflowchart TD\n  A --> B\n")
        with self.assertRaises(diagram_preview.DiagramError) as ctx:
            self.parse(source)
        self.assertIn("unclosed mermaid fence", str(ctx.exception))
        self.assertIn("line 1", str(ctx.exception))

    def test_oversize_file(self):
        source = self.dir / "big.mmd"
        source.write_bytes(b"A" * (diagram_preview.MAX_BYTES + 1))
        with self.assertRaises(diagram_preview.DiagramError) as ctx:
            self.parse(source)
        self.assertIn("too large", str(ctx.exception))

    def test_too_many_diagrams(self):
        blocks = "\n".join("```mermaid\nflowchart TD\n  A --> B\n```" for _ in range(21))
        source = self.write("too-many.md", blocks)
        with self.assertRaises(diagram_preview.DiagramError) as ctx:
            self.parse(source)
        self.assertIn("too many diagrams", str(ctx.exception))

    def test_output_collision_and_source_overwrite(self):
        source = self.write("flow.mmd", "flowchart TD\n  A --> B\n")
        existing = self.dir / "out.txt"
        existing.write_text("keep", encoding="utf-8")
        with self.assertRaises(diagram_preview.DiagramError) as ctx:
            diagram_preview.resolve_output(source, existing)
        self.assertIn("already exists", str(ctx.exception))
        self.assertEqual(existing.read_text(encoding="utf-8"), "keep")
        with self.assertRaises(diagram_preview.DiagramError) as ctx:
            diagram_preview.resolve_output(source, source)
        self.assertIn("overwrite", str(ctx.exception))

    def test_cli_help_lists_diagram(self):
        help_proc = run_rig("-h")
        self.assertEqual(help_proc.returncode, 2)
        self.assertIn("diagram PATH [--ascii] [--popup] [--output PATH]", help_proc.stdout)
        self.assertNotIn("--no-open", help_proc.stdout)

    def test_cli_missing_and_output_collision(self):
        missing = run_rig("diagram", str(self.dir / "missing.md"), cwd=self.dir)
        self.assertEqual(missing.returncode, 1)
        self.assertIn("file not found", missing.stderr)
        source = self.write("flow.mmd", "flowchart TD\n  A --> B\n")
        taken = self.dir / "taken.txt"
        taken.write_text("nope", encoding="utf-8")
        collide = run_rig("diagram", str(source), "--output", str(taken), cwd=self.dir)
        self.assertEqual(collide.returncode, 1)
        self.assertIn("already exists", collide.stderr)
        self.assertEqual(taken.read_text(encoding="utf-8"), "nope")


if __name__ == "__main__":
    unittest.main()
