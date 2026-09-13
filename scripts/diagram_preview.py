#!/usr/bin/env python3
"""Extract Mermaid diagrams and render them as terminal text."""
from __future__ import annotations

import argparse
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

MAX_BYTES = 1024 * 1024
MAX_DIAGRAMS = 20
RAW_SUFFIXES = {".mmd", ".mermaid"}
_FENCE = re.compile(r"^([ \t]{0,3})(`{3,}|~{3,})(.*)$")


class DiagramError(Exception):
    def __init__(self, message: str, output_path: Path | None = None) -> None:
        super().__init__(message)
        self.output_path = output_path


@dataclass(frozen=True)
class Diagram:
    index: int
    source: str
    start_line: int | None = None

    @property
    def label(self) -> str:
        if self.start_line:
            return f"Diagram {self.index} (line {self.start_line})"
        return f"Diagram {self.index}"


def read_input(path: Path) -> str:
    if not path.exists():
        raise DiagramError(f"file not found: {path}")
    if not path.is_file():
        raise DiagramError(f"not a file: {path}")
    size = path.stat().st_size
    if size > MAX_BYTES:
        raise DiagramError(f"file too large: {size} bytes (limit {MAX_BYTES})")
    data = path.read_bytes()
    if len(data) > MAX_BYTES:
        raise DiagramError(f"file too large: {len(data)} bytes (limit {MAX_BYTES})")
    if not data.strip():
        raise DiagramError(f"file is empty: {path}")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise DiagramError(f"file is not valid UTF-8: {path}") from exc


def _lang_from_info(info: str) -> str:
    token = info.strip()
    if not token:
        return ""
    first = token.split()[0]
    return first.strip("{}").lstrip(".").lower()


def parse_markdown_fences(text: str) -> list[Diagram]:
    diagrams: list[Diagram] = []
    lines = text.splitlines()
    i = 0
    n = len(lines)
    while i < n:
        match = _FENCE.match(lines[i])
        if not match:
            i += 1
            continue
        marker = match.group(2)
        fence_char = marker[0]
        fence_len = len(marker)
        start_line = i + 1
        is_mermaid = _lang_from_info(match.group(3)) == "mermaid"
        i += 1
        body: list[str] = []
        closed = False
        while i < n:
            close = _FENCE.match(lines[i])
            if (
                close
                and close.group(2)[0] == fence_char
                and len(close.group(2)) >= fence_len
                and close.group(3).strip() == ""
            ):
                closed = True
                break
            body.append(lines[i])
            i += 1
        if not closed:
            if is_mermaid:
                raise DiagramError(
                    f"unclosed mermaid fence starting at line {start_line}; "
                    f"close it with {fence_char * fence_len}"
                )
            break
        if is_mermaid:
            source = "\n".join(body)
            if not source.strip():
                raise DiagramError(f"empty mermaid fence at line {start_line}")
            diagrams.append(
                Diagram(index=len(diagrams) + 1, source=source, start_line=start_line)
            )
        i += 1
    return diagrams


def parse_diagrams(path: Path, text: str) -> list[Diagram]:
    if path.suffix.lower() in RAW_SUFFIXES:
        body = text.strip("\n")
        if not body.strip():
            raise DiagramError(f"diagram file is empty: {path}")
        diagrams = [Diagram(index=1, source=body)]
    else:
        diagrams = parse_markdown_fences(text)
        if not diagrams:
            raise DiagramError(
                f"no mermaid diagrams found in {path}; "
                "add a ```mermaid fenced block, or pass a .mmd/.mermaid file"
            )
    if len(diagrams) > MAX_DIAGRAMS:
        raise DiagramError(f"too many diagrams: {len(diagrams)} (limit {MAX_DIAGRAMS})")
    return diagrams


def resolve_output(source: Path, output: Path) -> Path:
    source = source.resolve()
    dest = output.expanduser()
    if not dest.is_absolute():
        dest = Path.cwd() / dest
    dest = dest.resolve()
    if dest == source:
        raise DiagramError("refusing to overwrite the source file")
    if dest.exists():
        raise DiagramError(f"output already exists: {dest}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    return dest


def write_output(dest: Path, text: str) -> None:
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    try:
        fd = os.open(str(dest), flags, 0o600)
    except FileExistsError as exc:
        raise DiagramError(f"output already exists: {dest}") from exc
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            if text and not text.endswith("\n"):
                handle.write("\n")
    except Exception:
        try:
            dest.unlink()
        except OSError:
            pass
        raise


def load_diagrams(source: Path) -> list[Diagram]:
    source = source.expanduser()
    if not source.is_absolute():
        source = Path.cwd() / source
    text = read_input(source)
    return parse_diagrams(source, text)


def preview_file(
    source: Path,
    *,
    use_ascii: bool = False,
    output: Path | None = None,
    popup: bool = False,
    env: dict[str, str] | None = None,
) -> tuple[str, Path | None, int]:
    import diagram_terminal

    source = source.expanduser()
    if not source.is_absolute():
        source = Path.cwd() / source
    diagrams = load_diagrams(source)
    dest: Path | None = None
    if output is not None:
        dest = resolve_output(source, output)
    formatted, failed = diagram_terminal.render_diagrams(
        diagrams, use_ascii=use_ascii, env=env
    )
    if dest is not None:
        write_output(dest, formatted)
    if popup:
        try:
            diagram_terminal.show_popup(formatted, env=env)
        except DiagramError as exc:
            if dest is not None:
                raise DiagramError(str(exc), output_path=dest) from exc
            raise
    return formatted, dest, (1 if failed else 0)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rig diagram",
        description=(
            "Render Mermaid diagrams from a local Markdown or .mmd/.mermaid file "
            "as terminal text. Default output is Unicode box drawing. "
            "Requires Node.js. Supports flowchart, state, sequence, class, ER, "
            "and XYChart. Does not render diagrams inline in Codex or other CLIs."
        ),
        epilog=(
            "examples:\n"
            "  ./bin/rig diagram README.md\n"
            "  ./bin/rig diagram README.md --ascii\n"
            "  ./bin/rig diagram chart.mmd --popup\n"
            "  ./bin/rig diagram chart.mmd --output /tmp/chart.txt"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("path", help="Markdown with ```mermaid fences, or raw .mmd/.mermaid")
    parser.add_argument("--ascii", action="store_true", help="plain ASCII instead of Unicode")
    parser.add_argument(
        "--popup",
        action="store_true",
        help="show a scrollable tmux display-popup with less -S",
    )
    parser.add_argument("--output", help="write text to PATH; refused if the path already exists")
    return parser


def main(argv: list[str] | None = None) -> int:
    from diagram_terminal import sanitize

    parser = build_parser()
    args = parser.parse_args(argv)
    output = Path(args.output) if args.output else None
    try:
        formatted, dest, failed = preview_file(
            Path(args.path),
            use_ascii=args.ascii,
            output=output,
            popup=args.popup,
        )
    except DiagramError as exc:
        if exc.output_path is not None:
            print(sanitize(str(exc.output_path)))
        print(f"rig diagram: {sanitize(str(exc))}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"rig diagram: {sanitize(str(exc))}", file=sys.stderr)
        return 1
    if dest is not None:
        print(sanitize(str(dest)))
    elif not args.popup:
        sys.stdout.write(formatted)
        if formatted and not formatted.endswith("\n"):
            sys.stdout.write("\n")
    return failed


if __name__ == "__main__":
    # Script execution would otherwise load a second copy via `import diagram_preview`.
    sys.modules["diagram_preview"] = sys.modules[__name__]
    raise SystemExit(main())
