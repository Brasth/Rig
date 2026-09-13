#!/usr/bin/env python3
from __future__ import annotations

import io
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import diagram_preview  # noqa: E402
import diagram_terminal  # noqa: E402

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


class DiagramTerminal(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.dir = Path(self.td.name)

    def tearDown(self):
        self.td.cleanup()

    def write(self, name: str, text: str) -> Path:
        path = self.dir / name
        path.write_text(text, encoding="utf-8")
        return path

    def test_sanitize_strips_controls_keeps_newline_tab(self):
        raw = "ok\tline\n\x1b[31mred\x1b[0m\x07bell\x00nul\rCR\x9bC1\x85NEL"
        out = diagram_terminal.sanitize(raw)
        self.assertEqual(out, "ok\tline\nredbellnulCRC1NEL")
        self.assertNotIn("\x1b", out)
        self.assertNotIn("\x07", out)
        self.assertNotIn("\x00", out)
        self.assertNotIn("\r", out)
        self.assertNotIn("\x9b", out)
        self.assertNotIn("\x85", out)
        self.assertIn("\n", out)
        self.assertIn("\t", out)

    def test_node_flowchart_and_sequence_unicode(self):
        source = self.write(
            "doc.md",
            "```mermaid\nflowchart LR\n  A --> B\n```\n\n"
            "```mermaid\nsequenceDiagram\n  A->>B: hi\n```\n",
        )
        proc = run_rig("diagram", str(source), cwd=self.dir)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("Diagram 1", proc.stdout)
        self.assertIn("Diagram 2", proc.stdout)
        self.assertIn("┌", proc.stdout)
        self.assertIn("A", proc.stdout)
        self.assertIn("B", proc.stdout)
        self.assertIn("hi", proc.stdout)
        self.assertNotIn("\x1b[", proc.stdout)

    def test_ascii_flag_and_output_file(self):
        source = self.write("flow.mmd", "flowchart LR\n  A --> B\n")
        dest = self.dir / "out.txt"
        proc = run_rig("diagram", str(source), "--ascii", "--output", str(dest), cwd=self.dir)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(Path(proc.stdout.strip()), dest.resolve())
        text = dest.read_text(encoding="utf-8")
        self.assertIn("+---+", text)
        self.assertNotIn("┌", text)
        self.assertIn("A", text)
        self.assertNotIn("flowchart LR", proc.stdout)

    def test_unsupported_continues_and_nonzero(self):
        source = self.write(
            "mixed.md",
            "```mermaid\nflowchart LR\n  A --> B\n```\n\n"
            "```mermaid\npie title Share\n  \"A\" : 60\n  \"B\" : 40\n```\n\n"
            "```mermaid\nsequenceDiagram\n  X->>Y: z\n```\n",
        )
        proc = run_rig("diagram", str(source), cwd=self.dir)
        self.assertEqual(proc.returncode, 1, proc.stderr)
        self.assertIn("Diagram 1", proc.stdout)
        self.assertIn("Diagram 2", proc.stdout)
        self.assertIn("Diagram 3", proc.stdout)
        self.assertIn("unsupported diagram type", proc.stdout)
        self.assertIn("pie title Share", proc.stdout)
        self.assertIn("flowchart, state, sequence, class, ER, and XYChart", proc.stdout)
        self.assertIn("┌", proc.stdout)
        self.assertIn("X", proc.stdout)
        self.assertIn("z", proc.stdout)

    def test_control_injection_sanitized_in_source_and_output(self):
        hostile = "flowchart LR\n  A[\"hi\x1b[31mESC\x07\"] --> B\n"
        source = self.write("hostile.mmd", hostile)
        formatted, _failed = diagram_terminal.render_diagrams(
            diagram_preview.load_diagrams(source)
        )
        self.assertNotIn("\x1b", formatted)
        self.assertNotIn("\x07", formatted)
        self.assertIn("hiESC", formatted)
        self.assertIn("B", formatted)
        pie = self.write(
            "bad.md",
            "```mermaid\nflowchart LR\n  A --> B\n```\n\n"
            "```mermaid\npie title X\x1b[31m\x07\n  \"A\" : 1\n```\n",
        )
        mixed, failed = diagram_terminal.render_diagrams(diagram_preview.load_diagrams(pie))
        self.assertGreaterEqual(failed, 1)
        self.assertNotIn("\x1b", mixed)
        self.assertNotIn("\x07", mixed)
        self.assertIn("source:", mixed)

    def test_missing_node_is_actionable(self):
        empty = self.dir / "empty-path"
        empty.mkdir()
        source = self.write("flow.mmd", "flowchart LR\n  A --> B\n")
        env = os.environ.copy()
        env["PATH"] = str(empty)
        with self.assertRaises(diagram_preview.DiagramError) as ctx:
            diagram_terminal.find_node(env)
        self.assertIn("Node.js", str(ctx.exception))
        self.assertIn("PATH", str(ctx.exception))
        proc = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "diagram_preview.py"), str(source)],
            cwd=self.dir,
            env={"PATH": str(empty), "HOME": str(self.dir)},
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 1, proc.stderr)
        self.assertIn("Node.js", proc.stderr)
        self.assertIn("https://nodejs.org", proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)
        self.assertNotIn("Traceback", proc.stdout)

    def test_cli_script_popup_outside_tmux_no_traceback(self):
        source = self.write("flow.mmd", "flowchart LR\n  A --> B\n")
        env = os.environ.copy()
        env.pop("TMUX", None)
        env.pop("TMUX_PANE", None)
        env["HOME"] = str(self.dir)
        proc = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "diagram_preview.py"),
                str(source),
                "--popup",
            ],
            cwd=self.dir,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 1, proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)
        self.assertNotIn("Traceback", proc.stdout)
        self.assertIn("tmux", proc.stderr.lower())
        self.assertIn("rig diagram:", proc.stderr)

    def test_node_uses_max_old_space_size(self):
        recorded: list[tuple] = []

        def fake_run(argv, **kwargs):
            recorded.append((list(argv), kwargs))
            return subprocess.CompletedProcess(argv, 0, b'{"ok":true,"text":"ok"}', b"")

        with patch("diagram_terminal.subprocess.run", side_effect=fake_run):
            result = diagram_terminal.render_one(
                "flowchart LR\n  A --> B",
                False,
                node="/usr/bin/node",
                renderer=Path("/tmp/renderer.mjs"),
            )
        self.assertTrue(result.ok, result.error)
        self.assertEqual(len(recorded), 1)
        argv, kwargs = recorded[0]
        self.assertEqual(argv[0], "/usr/bin/node")
        self.assertEqual(argv[1], "--max-old-space-size=256")
        self.assertEqual(argv[2], "/tmp/renderer.mjs")
        self.assertNotIn("preexec_fn", kwargs)
        self.assertFalse(hasattr(diagram_terminal, "_limit_child_memory"))
        self.assertFalse(hasattr(diagram_terminal, "CHILD_MEMORY_BYTES"))

    def test_main_sanitizes_errors_and_paths(self):
        hostile_error = diagram_preview.DiagramError(
            "bad\x1b[31mESC\r\x07\x9b",
            output_path=Path("/tmp/out\x1b[31m.txt"),
        )
        with patch.object(diagram_preview, "preview_file", side_effect=hostile_error):
            stdout = io.StringIO()
            stderr = io.StringIO()
            with patch("sys.stdout", stdout), patch("sys.stderr", stderr):
                code = diagram_preview.main(["x.md"])
        self.assertEqual(code, 1)
        out = stdout.getvalue()
        err = stderr.getvalue()
        self.assertNotIn("\x1b", out)
        self.assertNotIn("\x1b", err)
        self.assertNotIn("\r", out)
        self.assertNotIn("\r", err)
        self.assertNotIn("\x07", err)
        self.assertNotIn("\x9b", err)
        self.assertIn("badESC", err)
        self.assertIn("/tmp/out.txt", out)

        with patch.object(
            diagram_preview,
            "preview_file",
            return_value=("ok\n", Path("/tmp/dest\x1b[31m\r.txt"), 0),
        ):
            stdout = io.StringIO()
            stderr = io.StringIO()
            with patch("sys.stdout", stdout), patch("sys.stderr", stderr):
                code = diagram_preview.main(["x.md", "--output", "out.txt"])
        self.assertEqual(code, 0)
        printed = stdout.getvalue()
        self.assertNotIn("\x1b", printed)
        self.assertNotIn("\r", printed)
        self.assertIn("/tmp/dest.txt", printed)

    def test_timeout_then_continue(self):
        hang = self.dir / "hang.mjs"
        hang.write_text("await new Promise((resolve) => setTimeout(resolve, 60000))\n", encoding="utf-8")
        diagrams = [
            diagram_preview.Diagram(index=1, source="flowchart LR\n  A --> B"),
            diagram_preview.Diagram(index=2, source="flowchart LR\n  C --> D"),
        ]
        text, failed = diagram_terminal.render_diagrams(
            diagrams,
            renderer=hang,
            timeout=0.2,
        )
        self.assertEqual(failed, 2)
        self.assertIn("timed out", text)
        self.assertIn("Diagram 1", text)
        self.assertIn("Diagram 2", text)

    def test_popup_requires_tmux_and_less(self):
        with self.assertRaises(diagram_preview.DiagramError) as ctx:
            diagram_terminal.show_popup("hello\n", env={"PATH": os.environ.get("PATH", "")})
        self.assertIn("tmux session", str(ctx.exception))
        empty = self.dir / "bin"
        empty.mkdir()
        env = {
            "TMUX": "/tmp/sock,1,0",
            "TMUX_PANE": "%1",
            "PATH": str(empty),
        }
        with self.assertRaises(diagram_preview.DiagramError) as ctx:
            diagram_terminal.show_popup("hello\n", env=env)
        self.assertIn("tmux", str(ctx.exception).lower())
        tmux = empty / "tmux"
        tmux.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        tmux.chmod(0o755)
        with self.assertRaises(diagram_preview.DiagramError) as ctx:
            diagram_terminal.show_popup("hello\n", env=env)
        self.assertIn("less", str(ctx.exception).lower())

    def test_popup_socket_path_quoting_and_cleanup(self):
        recorded: list[list[str]] = []
        leftover: list[Path] = []

        def fake_run(argv, **kwargs):
            recorded.append(list(argv))
            command = argv[-1]
            for token in command.replace("'", " ").split():
                candidate = Path(token)
                if candidate.suffix == ".txt" and candidate.name.startswith("rig-diagram-"):
                    leftover.append(candidate)
                    self.assertTrue(candidate.is_file())
            return subprocess.CompletedProcess(argv, 0, b"", b"")

        env = {
            "TMUX": "/tmp/sock with space,1234,0",
            "TMUX_PANE": "%12",
            "PATH": os.environ.get("PATH", ""),
        }
        with patch("diagram_terminal.shutil.which") as which:
            which.side_effect = lambda name, path=None: {
                "tmux": "/usr/bin/tmux",
                "less": "/usr/bin/less",
            }.get(name)
            diagram_terminal.show_popup("line\n", env=env, runner=fake_run)
        self.assertEqual(len(recorded), 1)
        argv = recorded[0]
        self.assertEqual(argv[0], "/usr/bin/tmux")
        self.assertEqual(argv[1:3], ["-S", "/tmp/sock with space"])
        self.assertIn("-t", argv)
        self.assertIn("%12", argv)
        self.assertIn("display-popup", argv)
        self.assertNotIn("-g", argv)
        self.assertNotIn("bind-key", argv)
        command = argv[-1]
        self.assertIn("-S", command)
        self.assertTrue(command.startswith("'") or command.startswith("/"))
        self.assertIn("less", command)
        self.assertTrue(leftover)
        for path in leftover:
            self.assertFalse(path.exists())

    def test_popup_quotes_spaces_in_temp_path(self):
        argv = diagram_terminal.popup_argv(
            "/usr/bin/tmux",
            "/tmp/sock,not,this",
            "%1",
            "/usr/bin/less",
            Path("/tmp/file with space.txt"),
        )
        self.assertEqual(argv[1:3], ["-S", "/tmp/sock,not,this"])
        self.assertEqual(argv[-1], "/usr/bin/less -S '/tmp/file with space.txt'")

    def test_cli_readme_example(self):
        proc = run_rig("diagram", str(ROOT / "README.md"), cwd=ROOT)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertGreaterEqual(proc.stdout.count("Diagram "), 2)
        self.assertIn("┌", proc.stdout)
        self.assertNotIn("--no-open", proc.stderr)


@unittest.skipUnless(shutil.which("tmux") and shutil.which("node"), "tmux or node missing")
class DiagramTmuxSmoke(unittest.TestCase):
    def test_isolated_capture_pane(self):
        name = f"rigdiag{os.getpid()}"
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "flow.mmd"
            source.write_text(
                "flowchart LR\n  TMUX_SOURCE --> TMUX_DEST\n", encoding="utf-8"
            )
            inner = [
                sys.executable,
                str(ROOT / "scripts" / "diagram_preview.py"),
                str(source),
            ]
            cmd = f"{shlex.join(inner)}; sleep 12"
            try:
                created = subprocess.run(
                    [
                        "tmux",
                        "-L",
                        name,
                        "-f",
                        "/dev/null",
                        "new-session",
                        "-d",
                        "-s",
                        "t",
                        "-x",
                        "160",
                        "-y",
                        "40",
                        cmd,
                    ],
                    capture_output=True,
                    text=True,
                    timeout=5,
                    env={**os.environ, "TMUX": ""},
                )
                self.assertEqual(created.returncode, 0, created.stderr)
                pane = ""
                for _ in range(40):
                    cap = subprocess.run(
                        ["tmux", "-L", name, "capture-pane", "-pt", "t"],
                        capture_output=True,
                        text=True,
                        timeout=5,
                    )
                    pane = cap.stdout
                    if (
                        "┌" in pane
                        and "TMUX_SOURCE" in pane
                        and "TMUX_DEST" in pane
                        and "error:" not in pane.lower()
                        and "Traceback" not in pane
                    ):
                        break
                    time.sleep(0.2)
                self.assertIn("┌", pane)
                self.assertIn("TMUX_SOURCE", pane)
                self.assertIn("TMUX_DEST", pane)
                self.assertNotIn("error:", pane.lower())
                self.assertNotIn("Traceback", pane)
            finally:
                subprocess.run(
                    ["tmux", "-L", name, "kill-server"],
                    capture_output=True,
                    timeout=5,
                )


if __name__ == "__main__":
    unittest.main()
