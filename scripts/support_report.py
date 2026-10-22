#!/usr/bin/env python3
"""One file to send when something does not work: what Rig, the parent and the machine look like, and what failed.

`rig support` (or /logs in rig shell) writes .rig/support.txt. It holds versions, the parent's own sign-in and MCP
listing, the parent command Rig runs (without your message), the last tool errors of the newest conversation, what the
parent printed on stderr, Rig's error log and the doctor's findings. Secrets are left out: environment variables are
named, never shown, and anything shaped like a token is replaced.
"""
from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPORT = "support.txt"
ERROR_WORDS = re.compile(r"(?i)not authori[sz]ed|unauthori[sz]ed|forbidden|denied|error|failed|expired|reauthori[sz]|sign.?in|login")
SECRET = re.compile(r"(?i)(sk-[A-Za-z0-9_-]{8,}|eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_.-]+|bearer\s+[A-Za-z0-9._~+/=-]{12,}|"
                    r"(?:token|secret|password|api[_-]?key|authorization)[\"']?\s*[:=]\s*[\"']?(?:bearer\s+|basic\s+)?[^\s\"',]{6,})")
ENV_NAMES = ("CODEX_HOME", "OPENAI_API_KEY", "CODEX_API_KEY", "ANTHROPIC_API_KEY", "HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY", "SHELL", "TERM")
TOOL_ERRORS = 12
TAIL = 4000


def redact(text: str) -> str:
    return SECRET.sub("«hidden»", text)


def run(argv: list[str], timeout: int = 15) -> str:
    binary = shutil.which(argv[0])
    if not binary:
        return f"({argv[0]} is not on PATH)"
    try:
        out = subprocess.run([binary, *argv[1:]], capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError) as error:
        return f"({' '.join(argv)} did not run: {error})"
    text = (out.stdout + (("\n[stderr]\n" + out.stderr) if out.stderr.strip() else "")).strip()
    text = "\n".join(re.sub(r" {3,}", " | ", line)[:300] for line in text.splitlines())  # a table's padding is not information
    return text + ("" if out.returncode == 0 else f"\n[exit status {out.returncode}]")


def rig_version() -> str:
    """The commit of a Rig clone, or where the installed copy lives when it is not a git checkout (~/.rig)."""
    out = run(["git", "-C", str(HERE.parent), "rev-parse", "--short", "HEAD"])
    return out if re.fullmatch(r"[0-9a-f]{7,40}", out) else f"installed copy at {HERE.parent} (not a git checkout)"


def tail(path: Path, limit: int = TAIL) -> str:
    try:
        return path.read_text(errors="replace")[-limit:].strip() or "(empty)"
    except OSError:
        return "(none)"


def newest_chat(repo: Path) -> Path | None:
    rows = sorted((repo / ".rig" / "chat").glob("c-*.jsonl"), key=lambda p: p.stat().st_mtime) if (repo / ".rig" / "chat").is_dir() else []
    return rows[-1] if rows else None


def tool_errors(path: Path) -> list[str]:
    """The tool lines of a conversation with the result each got, when the result looks like a failure."""
    rows = []
    for line in path.read_text(errors="replace").splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    out = []
    for index, row in enumerate(rows):
        if row.get("role") != "result" or not ERROR_WORDS.search(str(row.get("text"))):
            continue
        tool = next((r for r in reversed(rows[:index]) if r.get("role") == "tool"), {})
        out.append(f"{row.get('at', '')}  tool: {str(tool.get('text', '?'))[:200]}\n    result: {str(row.get('text'))[:500]}")
    return out[-TOOL_ERRORS:]


def section(title: str, body: str) -> str:
    return f"== {title} ==\n{body.strip() or '(nothing)'}\n"


def build(repo) -> str:
    repo = Path(repo).resolve()
    sys.path.insert(0, str(HERE))
    import harness
    import rig_chat
    parts = [f"rig support report · {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}\n"]
    try:
        parent = harness.parse_harness(harness.harness_path(repo))["parent"]
    except (OSError, ValueError, KeyError):
        parent = ""
    parts.append(section("machine", f"{platform.platform()}\npython {platform.python_version()}\nproject: {repo.name}\nparent: {parent or '(none)'}\n"
                         f"rig: {rig_version()}"))
    parts.append(section("environment (names only)", "\n".join(f"{n}: {'set' if os.environ.get(n) else 'not set'}" for n in ENV_NAMES)))
    if parent:
        parts.append(section(f"{parent} version", run([parent, "--version"])))
    if parent == "codex":
        parts.append(section("codex login status", run(["codex", "login", "status"])))
        parts.append(section("codex mcp list", run(["codex", "mcp", "list"])))
        argv = rig_chat.argv_codex("<your message>", "", {})
        parts.append(section("how Rig runs codex (headless)", " ".join(argv) + "\n(Rig also tries `codex mcp-server` first; its stderr goes to the parent log below)"))
    elif parent == "claude":
        parts.append(section("claude auth status", run(["claude", "auth", "status"])))
        parts.append(section("how Rig runs claude (headless)", " ".join(rig_chat.argv_claude("<your message>", "", {}))))
    chat = newest_chat(repo)
    if chat:
        errors = tool_errors(chat)
        parts.append(section(f"tool errors in {chat.name} (newest {TOOL_ERRORS})", "\n".join(errors) or "(none looked like a failure)"))
        parts.append(section("what the parent printed on stderr", tail(chat.with_name(chat.stem + ".parent.log"))))
    else:
        parts.append(section("conversation", "(no conversation in this project yet)"))
    parts.append(section("Rig's error log (.rig/mcp-errors.log)", tail(repo / ".rig" / "mcp-errors.log")))
    try:
        import shell_doctor
        rows = shell_doctor.findings(repo)["findings"]
        parts.append(section("/doctor", "\n".join(f"{r['level']}: {r['title']} — {r['detail']}" for r in rows) or "nothing wrong found"))
    except Exception as error:  # noqa: BLE001 - a broken check must not stop the rest of the report
        parts.append(section("/doctor", f"could not run: {error}"))
    parts.append(section("last selftest", tail(repo / ".rig" / "selftest.txt")))
    return redact("\n".join(parts))


def write(repo) -> Path:
    path = Path(repo).resolve() / ".rig" / REPORT
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(build(repo))
    return path


def main(argv=None) -> int:
    import argparse
    parser = argparse.ArgumentParser(prog="rig support", description="Write .rig/support.txt: what to send when something does not work")
    parser.add_argument("--repo", default="")
    parser.add_argument("--print", action="store_true", help="print the report instead of only its path")
    args = parser.parse_args(argv)
    sys.path.insert(0, str(HERE))
    import jobs
    repo = jobs.repo_root(args.repo or None)
    path = write(repo)
    print(path.read_text() if args.print else "")
    print(f"written: {path}\nSecrets are left out; look it over, then send it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
