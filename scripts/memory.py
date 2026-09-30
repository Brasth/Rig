#!/usr/bin/env python3
"""Read and append standing facts in .rig/MEMORY.md."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import os
import re
import stat
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import jobs as rig_jobs  # noqa: E402
import harness  # noqa: E402
import update_gate  # noqa: E402

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
    with _mutation(repo):
        _write(path, text)


def _write(path, text):
    fd, name = tempfile.mkstemp(prefix=".memory-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


@contextmanager
def _mutation(repo):
    # All read-modify-write operations share one lock, outside the replaced file.
    with update_gate.lock():
        harness.assert_project_enabled(repo)
        path = memory_path(Path(repo).resolve())
        if path.parent.is_symlink():
            raise ValueError("Memory directory must not be a symlink")
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path.parent / ".memory.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                    or info.st_uid != os.getuid() or info.st_mode & 0o077):
                raise ValueError("Unsafe memory lock")
            fcntl.flock(fd, fcntl.LOCK_EX)
            if path.is_symlink() or (path.exists() and (not path.is_file() or path.stat().st_nlink != 1)):
                raise ValueError("Memory must be a regular non-linked file")
            yield path
        finally:
            os.close(fd)


def memory_view(repo):
    path = memory_path(repo)
    raw = path.read_bytes() if path.is_file() else b""
    return {"sha256": hashlib.sha256(raw).hexdigest(),
            "text": raw.decode("utf-8", errors="replace").rstrip() or 'no memory yet  (rig memory add "standing fact")',
            "facts": parse_bullets(raw.decode("utf-8", errors="replace"))}


def edit_memory(repo, fact, expected_sha256, replacement=None):
    if os.environ.get("RIG_JOB_ID") or os.environ.get("RIG_JOB_DIR"):
        raise ValueError("Memory replacement/removal is parent-only")
    if not isinstance(expected_sha256, str) or not re.fullmatch(r"[a-f0-9]{64}", expected_sha256):
        raise ValueError("expected_sha256 must be a SHA256 digest")
    wanted = normalize_fact(fact)
    if not wanted or len(" ".join(fact.split()).removeprefix("- ")) > MAX_FACT:
        raise ValueError("Fact must be nonempty and within the fact limit")
    new = None
    if replacement is not None:
        clean = " ".join(replacement.split()).removeprefix("- ").strip()
        if not clean or len(clean) > MAX_FACT:
            raise ValueError("Replacement must be nonempty and within the fact limit")
        new = normalize_fact(replacement)
    with _mutation(repo) as path:
        raw = path.read_bytes() if path.exists() else b""
        if hashlib.sha256(raw).hexdigest() != expected_sha256:
            raise ValueError("Memory changed; read the current hash before retrying")
        # Preserve duplicate rows until ambiguity has been checked.
        bullets = [line.strip()[1:].strip() for line in raw.decode("utf-8").splitlines()
                   if line.strip().startswith("-") and line.strip()[1:].strip()]
        matches = [i for i, value in enumerate(bullets) if normalize_fact(value).lower() == wanted.lower()]
        if len(matches) != 1:
            raise ValueError("Fact is missing or ambiguous")
        index = matches[0]
        if new is not None and any(i != index and value.lower() == new.lower() for i, value in enumerate(bullets)):
            raise ValueError("Replacement duplicates another fact")
        if new is None:
            del bullets[index]
        else:
            bullets[index] = new
        _write(path, render(bullets))
    return "removed" if new is None else "replaced"


def show_memory(repo: Path) -> str:
    text = read_memory(repo).rstrip()
    if not text:
        return "no memory yet  (rig memory add \"standing fact\")"
    return text


def add_memory(repo: Path, fact: str) -> str:
    fact = normalize_fact(fact)
    if not fact:
        return "skip empty fact"
    with _mutation(repo) as path:
        bullets = parse_bullets(path.read_text() if path.exists() else "")
        if any(b.lower() == fact.lower() for b in bullets):
            return "exists"
        bullets.append(fact)
        before = len(bullets)
        bullets = cap_bullets(bullets)
        _write(path, render(bullets))
    dropped = before - len(bullets)
    if dropped:
        return f"added (capped, dropped {dropped} oldest)"
    return "added"


def fact_count(repo: Path) -> int:
    return len(parse_bullets(read_memory(repo)))


def main() -> int:
    parser = argparse.ArgumentParser(prog="memory.py")
    parser.add_argument("cmd", nargs="?", default="show", choices=["show", "add", "count", "replace", "remove"])
    parser.add_argument("fact", nargs="*")
    parser.add_argument("--repo")
    parser.add_argument("--old")
    parser.add_argument("--new")
    parser.add_argument("--fact", dest="selected_fact")
    parser.add_argument("--expected-sha256")
    args = parser.parse_args()
    repo = rig_jobs.repo_root(args.repo)
    if args.cmd in {"replace", "remove"}:
        try:
            selected = args.old if args.cmd == "replace" else args.selected_fact
            if selected is None or (args.cmd == "replace" and args.new is None):
                raise ValueError("Specify the exact fact and replacement")
            print(edit_memory(repo, selected, args.expected_sha256, args.new if args.cmd == "replace" else None))
            return 0
        except (ValueError, OSError) as error:
            print(str(error), file=sys.stderr)
            return 1
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
    try:
        print(add_memory(repo, fact))
        return 0
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
