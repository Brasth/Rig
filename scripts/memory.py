#!/usr/bin/env python3
"""Read and append standing facts in .rig/MEMORY.md."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import jobs as rig_jobs  # noqa: E402

MAX_LINES = 120
MAX_FACT = 240
HEADER = """# MEMORY

Durable local facts only. Cap about 120 lines.
Do not store transcripts. Do not copy `~/.codex/memories` here.
"""


def memory_path(repo: Path) -> Path:
    return repo / ".rig" / "MEMORY.md"


def normalize_fact(text: str) -> str:
    s = " ".join((text or "").split())
    if s.startswith("- "):
        s = s[2:].strip()
    elif s == "-":
        s = ""
    if len(s) > MAX_FACT:
        s = s[: MAX_FACT - 1].rstrip() + "…"
    return s


def parse_bullets(text: str) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in text.splitlines():
        s = raw.strip()
        if not s.startswith("-"):
            continue
        fact = s[1:].strip()
        if not fact:
            continue
        key = fact.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(fact)
    return out


def render(bullets: list[str]) -> str:
    lines = [HEADER.rstrip(), ""]
    if bullets:
        lines.extend(f"- {b}" for b in bullets)
    else:
        lines.append("-")
    return "\n".join(lines) + "\n"


def _line_count(text: str) -> int:
    if not text:
        return 0
    n = text.count("\n")
    if not text.endswith("\n"):
        n += 1
    return n


def cap_bullets(bullets: list[str]) -> list[str]:
    kept = list(bullets)
    while kept and _line_count(render(kept)) > MAX_LINES:
        kept.pop(0)
    return kept


def read_memory(repo: Path) -> str:
    path = memory_path(repo)
    if not path.is_file():
        return ""
    try:
        return path.read_text(errors="replace")
    except OSError:
        return ""


def write_memory(repo: Path, text: str) -> None:
    path = memory_path(repo)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text)
    tmp.replace(path)


def show_memory(repo: Path) -> str:
    text = read_memory(repo).rstrip()
    if not text:
        return "no memory yet  (rig memory add \"standing fact\")"
    return text


def add_memory(repo: Path, fact: str) -> str:
    fact = normalize_fact(fact)
    if not fact:
        return "skip empty fact"
    bullets = parse_bullets(read_memory(repo))
    if any(b.lower() == fact.lower() for b in bullets):
        return "exists"
    bullets.append(fact)
    before = len(bullets)
    bullets = cap_bullets(bullets)
    write_memory(repo, render(bullets))
    dropped = before - len(bullets)
    if dropped:
        return f"added (capped, dropped {dropped} oldest)"
    return "added"


def fact_count(repo: Path) -> int:
    return len(parse_bullets(read_memory(repo)))


def main() -> int:
    parser = argparse.ArgumentParser(prog="memory.py")
    parser.add_argument("cmd", nargs="?", default="show", choices=["show", "add", "count"])
    parser.add_argument("fact", nargs="*")
    parser.add_argument("--repo")
    args = parser.parse_args()
    repo = rig_jobs.repo_root(args.repo)
    if args.cmd == "show":
        print(show_memory(repo))
        return 0
    if args.cmd == "count":
        print(fact_count(repo))
        return 0
    fact = " ".join(args.fact).strip()
    if not fact and not sys.stdin.isatty():
        fact = sys.stdin.read()
    if not normalize_fact(fact):
        print("usage: rig memory add \"standing fact\"", file=sys.stderr)
        return 2
    print(add_memory(repo, fact))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
