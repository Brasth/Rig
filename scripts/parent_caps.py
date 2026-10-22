#!/usr/bin/env python3
"""What each parent can do through rig chat, said plainly: the native-parity list.

Every row is what a person does in the parent's own TUI. "yes" means rig chat does it the way the native TUI does, "partial"
means it does it differently or in part (the note says how), "no" means it cannot, and the note says why. The table is the
single source: docs/native-parity.md is generated from it (`python3 scripts/parent_caps.py doc > docs/native-parity.md`, a
test keeps it current), and `limits(parent)` feeds the shell's help so a gap is told, not discovered.
"""
from __future__ import annotations

import sys

FEATURES = [
    ("stream", "The answer streams in as it is written"),
    ("tools", "Tool calls and what they answered"),
    ("edits", "File changes shown as a diff"),
    ("approve", "Asks before running a command or editing"),
    ("interrupt", "Stop mid-turn, cleanly"),
    ("steer", "Say something while it works"),
    ("resume", "Continue an earlier conversation"),
    ("compact", "Compact a long conversation"),
    ("context", "How full the context is"),
    ("model", "Choose the model and how hard it thinks"),
    ("plan", "Plan first, change nothing"),
    ("mcp", "Use its MCP servers"),
    ("skills", "Its skills and slash commands"),
    ("status", "Say when it is reconnecting, retrying or compacting"),
]

Y, P, N = "yes", "partial", "no"

CAPS: dict[str, dict[str, tuple[str, str]]] = {
    "codex": {
        "stream": (Y, "through codex app-server: item and text deltas"),
        "tools": (Y, "commands, MCP calls, searches, plans and helpers, each with its result"),
        "edits": (Y, "from the files the turn changed (git), shown as a diff"),
        "approve": (Y, "codex's own approval requests, answered in rig; the edits mode lets file changes through"),
        "interrupt": (Y, "turn/interrupt: the turn ends as interrupted and the thread stays valid"),
        "steer": (P, "interrupt, then continue with the new message (codex's turn/steer is not used yet)"),
        "resume": (Y, "thread/resume of the same thread id"),
        "compact": (P, "rig starts a fresh conversation from codex's own summary; codex's thread/compact/start is not used yet"),
        "context": (Y, "from codex's token usage (best effort: the payload is not documented)"),
        "model": (Y, "per turn: model and reasoning effort"),
        "plan": (P, "read-only sandbox and a plan instruction; codex's own plan mode is not used"),
        "mcp": (Y, "the servers in codex's config, the project's own first; sign in with /mcp login NAME"),
        "skills": (P, "listed from codex's files and sent as text; codex's skills/list is not used yet"),
        "status": (Y, "reconnecting 3/5, compacting and thinking show on the working line"),
    },
    "claude": {
        "stream": (Y, "stream-json with partial messages"),
        "tools": (Y, "every tool call and result"),
        "edits": (Y, "from the files the turn changed (git), shown as a diff"),
        "approve": (Y, "through the rig-ask permission prompt tool"),
        "interrupt": (P, "the process is stopped; claude's own interrupt message is not used yet"),
        "steer": (P, "stop, then continue with the new message"),
        "resume": (Y, "--resume of the same session"),
        "compact": (P, "rig starts a fresh conversation from a summary; claude's /compact is not run"),
        "context": (Y, "the last call's input against the model's window"),
        "model": (Y, "--model and --effort"),
        "plan": (Y, "--permission-mode plan"),
        "mcp": (Y, ".mcp.json and ~/.claude.json, the project's own first"),
        "skills": (Y, "the skills and slash commands in claude's files"),
        "status": (P, "retries, compaction and thinking show when this claude version reports them"),
    },
    "grok": {
        "stream": (P, "streaming-json when grok sends it, else whole answers"),
        "tools": (P, "what grok's stream names; less detail than the TUI"),
        "edits": (Y, "from the files the turn changed (git), shown as a diff"),
        "approve": (N, "grok's headless mode cannot ask: it runs with its own permissions"),
        "interrupt": (P, "the process is stopped"),
        "steer": (P, "stop, then continue with the new message"),
        "resume": (Y, "-r of the same session"),
        "compact": (P, "rig starts a fresh conversation from a summary"),
        "context": (N, "grok does not report it in headless mode"),
        "model": (Y, "--model and effort when grok takes them"),
        "plan": (P, "a plan instruction only: grok cannot be made read-only"),
        "mcp": (P, "its own settings; rig does not list them"),
        "skills": (P, "sent as text"),
        "status": (N, "no reconnect or compaction events"),
    },
    "opencode": {
        "stream": (P, "json events per step"),
        "tools": (P, "what opencode's json names"),
        "edits": (Y, "from the files the turn changed (git), shown as a diff"),
        "approve": (N, "opencode run cannot ask: it runs with its own permissions"),
        "interrupt": (P, "the process is stopped"),
        "steer": (P, "stop, then continue with the new message"),
        "resume": (Y, "--session of the same session"),
        "compact": (P, "rig starts a fresh conversation from a summary"),
        "context": (N, "not reported in headless mode"),
        "model": (Y, "--model and effort when opencode takes them"),
        "plan": (Y, "--agent plan"),
        "mcp": (P, "its own settings; rig does not list them"),
        "skills": (P, "sent as text"),
        "status": (N, "no reconnect or compaction events"),
    },
    "pi": {
        "stream": (P, "json mode: whole messages"),
        "tools": (P, "what pi's json names"),
        "edits": (Y, "from the files the turn changed (git), shown as a diff"),
        "approve": (N, "pi -p cannot ask"),
        "interrupt": (P, "the process is stopped"),
        "steer": (P, "stop, then continue with the new message"),
        "resume": (N, "every message starts a new pi session; rig sends the conversation along instead"),
        "compact": (P, "rig starts a fresh conversation from a summary"),
        "context": (N, "not reported"),
        "model": (Y, "--model and effort when pi takes them"),
        "plan": (P, "a plan instruction only"),
        "mcp": (P, "its own settings; rig does not list them"),
        "skills": (P, "sent as text"),
        "status": (N, "no reconnect or compaction events"),
    },
    "omp": {
        "stream": (P, "json mode: whole messages"),
        "tools": (P, "what omp's json names"),
        "edits": (Y, "from the files the turn changed (git), shown as a diff"),
        "approve": (N, "omp -p cannot ask"),
        "interrupt": (P, "the process is stopped"),
        "steer": (P, "stop, then continue with the new message"),
        "resume": (N, "every message starts a new omp session; rig sends the conversation along instead"),
        "compact": (P, "rig starts a fresh conversation from a summary"),
        "context": (N, "not reported"),
        "model": (Y, "--model and effort when omp takes them"),
        "plan": (P, "a plan instruction only"),
        "mcp": (P, "its own settings; rig does not list them"),
        "skills": (P, "sent as text"),
        "status": (N, "no reconnect or compaction events"),
    },
    "agy": {
        "stream": (N, "the answer arrives whole, when the turn ends"),
        "tools": (N, "no tool calls in agy's json output"),
        "edits": (Y, "from the files the turn changed (git), shown as a diff"),
        "approve": (N, "agy -p cannot ask"),
        "interrupt": (P, "the process is stopped"),
        "steer": (P, "stop, then continue with the new message"),
        "resume": (N, "every message starts a new agy session; rig sends the conversation along instead"),
        "compact": (P, "rig starts a fresh conversation from a summary"),
        "context": (N, "not reported"),
        "model": (P, "effort only"),
        "plan": (P, "a plan instruction only"),
        "mcp": (P, "its own settings; rig does not list them"),
        "skills": (P, "sent as text"),
        "status": (N, "no reconnect or compaction events"),
    },
}

_LABEL = dict(FEATURES)
_MARK = {Y: "yes", P: "partly", N: "no"}


def limits(parent: str, most: int = 6) -> list[str]:
    """The parent's gaps in one line each, the "no" rows first: "Asks before running a command or editing: grok's headless mode cannot ask"."""
    rows = CAPS.get(parent, {})
    order = {N: 0, P: 1}
    gaps = sorted(((order[level], key) for key, (level, _) in rows.items() if level in order), key=lambda r: (r[0], [k for k, _ in FEATURES].index(r[1])))
    return [f"{_LABEL[key]}: {rows[key][1]}" for _, key in gaps[:most]]


def doc() -> str:
    names = list(CAPS)
    out = ["# What each assistant can do in rig shell", "",
           "Generated from `scripts/parent_caps.py` (`python3 scripts/parent_caps.py doc > docs/native-parity.md`); a test keeps it current.",
           "Each row is something you do in the assistant's own TUI. **yes** is the same in rig shell, **partly** is done differently or in part,",
           "**no** cannot be done, with the reason below. Codex and Claude have a protocol that carries everything; the others are driven through",
           "their headless mode, which says less, and the table says so. The rows for grok, opencode, pi, omp and agy are read from how rig runs",
           "them and are not yet checked against the live CLIs (`/selftest` checks part of it).", "",
           "| | " + " | ".join(names) + " |", "|---|" + "---|" * len(names)]
    for key, label in FEATURES:
        out.append(f"| {label} | " + " | ".join(_MARK[CAPS[n][key][0]] for n in names) + " |")
    for name in names:
        out += ["", f"## {name}", ""]
        for key, label in FEATURES:
            level, note = CAPS[name][key]
            out.append(f"- **{label}**: {_MARK[level]}. {note}.")
    return "\n".join(out) + "\n"


if __name__ == "__main__":
    if sys.argv[1:] == ["doc"]:
        sys.stdout.write(doc())
    else:
        for parent in CAPS:
            print(parent, *limits(parent, 99), sep="\n  ")
