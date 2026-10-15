#!/usr/bin/env python3
"""Canonical child worker preamble shared by generated briefs and the wrapper fallback.

The wrapper adds this preamble only when a brief lacks MARKER, so generated
briefs that already start with it are never duplicated and arbitrary legacy
brief text is never rewritten.
"""
from __future__ import annotations

import sys

MARKER = "You are a worker, not the orchestrator"
PREAMBLE = (
    MARKER + ". Do not spawn codex, grok, claude, cursor, opencode, omp, pi, agy, or devin. "
    "Do not use computer-use, chrome-profile, Figma MCP, BrowserSkill, or bsk. "
    "Follow skill file paths listed in the brief. Write code, fix, review, SSH/debug, or gather facts. "
    "Do only the files and changes in the brief. Do not hunt extra updates. "
    "First Rig operation must be rig_job_inbox (strict child MCP handshake). "
    "Each turn, call rig_job_inbox once if listed (empty is fine); pull inbox/messages periodically. "
    "Inbox is not ASK. Doing/note/ask require the handshake. Print a short summary. Stop."
)


def has_preamble(text: str) -> bool:
    return MARKER in str(text or "")


def with_preamble(text: str) -> str:
    """Prefix the canonical preamble once; never edits the supplied body."""
    body = str(text or "")
    return body if has_preamble(body) else PREAMBLE + "\n\n" + body


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args != ["preamble"]:
        print("usage: worker_brief.py preamble", file=sys.stderr)
        return 2
    sys.stdout.write(PREAMBLE)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
