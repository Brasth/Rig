#!/usr/bin/env python3
"""The conversation with the parent: a headless parent CLI with a Rig-rendered chat (the Session screen's middle column).

Off unless RIG_EXPERIMENT_CHAT=1 (set by `rig shell` for its board) or ``[experiments] chat = true``. Each message runs
the parent once in its headless mode, a detached runner decodes what it says into a transcript under
``.rig/chat/<session>.jsonl``, and the board shows it. Every parent `rig shell` can host has a headless mode here;
claude, codex, grok and opencode resume their own session, the others get the recent conversation with each message.
Rig adds no permission flags: the parent runs with the user's own settings. claude (through Rig's rig-ask server) and
codex (through `codex mcp-server`) ask for permission, and the board answers in the conversation (chat.answer); the
other headless modes cannot ask, so they follow their own settings.

The event formats are the ones Rig already reads for these CLIs. No real parent CLI is available here, so the decoders
and the codex server protocol are tested against recorded-shape fixtures and stand-ins.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import shutil
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

MAX_MESSAGE = 4000
MAX_ENTRY = 6000
MAX_ENTRIES = 5000
SESSION = re.compile(r"c-[0-9a-f]{8}")
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


class ChatError(ValueError):
    pass


# Each parent's headless mode, as Rig's worker launcher already runs it (scripts/worker_commands.py), minus the
# permission flags Rig gives its workers: the parent keeps the user's own settings. ``turn`` carries the repository,
# a file holding the message, and the claude permission server. ``resumes``: the CLI continues its own session.
def _model(turn: dict, flag: str = "--model") -> list[str]:
    """The model chosen for the parent (ui_settings parent_model), as its CLI's own flag; nothing when none is."""
    return [flag, turn["model"]] if turn.get("model") else []


def _effort(turn: dict, parent: str) -> list[str]:
    """The reasoning level chosen for the parent (ui_settings parent_effort), as its CLI's own flag; nothing when none is."""
    level = turn.get("effort")
    if not level:
        return []
    if parent == "claude":
        return [] if "haiku" in str(turn.get("model", "")).lower() else ["--effort", level]  # haiku's print mode hangs on it
    if parent == "codex":
        return ["-c", f'model_reasoning_effort="{level}"']
    if parent == "opencode":
        return ["--variant", level]
    if parent in ("omp", "pi"):
        return ["--thinking", level]
    return ["--effort", level]  # grok, agy


# Plan mode (/plan): the parent looks around and answers with a plan, changing nothing. claude, opencode and codex have a
# flag for it; every other parent is told so in the message.
PLAN_PREFIX = ("[Plan mode] Change nothing: do not edit files, run commands that modify anything, or start helpers. Look around "
               "read-only if you need to, then reply with a short numbered plan for the request below, naming the files you would "
               "change and how you would check it. Wait for me to say go.\n\n")


def _mode(meta: dict) -> str:
    """How much the parent may do without asking: ask (it asks before it changes anything), edits (it edits files without
    asking and still asks about commands) or plan (it changes nothing). Plan wins while it is on."""
    return "plan" if meta.get("plan") else ("edits" if meta.get("edits") else "ask")


MODE_COMMAND = re.compile(r"/mode(?:\s+(\S+))?\s*")
MODE_WORDS = {"ask": "asks before it changes anything", "edits": "edits files without asking and still asks about commands",
              "plan": "plans and changes nothing"}


def _plan(turn: dict, parent: str) -> list[str]:
    if not turn.get("plan"):
        return ["--permission-mode", "acceptEdits"] if turn.get("edits") and parent == "claude" else []
    return {"claude": ["--permission-mode", "plan"], "opencode": ["--agent", "plan"], "codex": ["-s", "read-only"]}.get(parent, [])


def argv_claude(message: str, native: str, turn: dict) -> list[str]:
    asks = ["--mcp-config", turn["ask_mcp"], "--permission-prompt-tool", "mcp__rig-ask__permission_prompt"] if turn.get("ask_mcp") else []
    return ["claude", "-p", "--output-format", "stream-json", "--verbose", "--include-partial-messages", *_model(turn), *_effort(turn, "claude"), *_plan(turn, "claude"), *asks,
            *(["--resume", native] if native else []), "--", message]


def argv_codex(message: str, native: str, turn: dict) -> list[str]:
    return ["codex", "exec", "--json", *_model(turn, "-m"), *_effort(turn, "codex"), *_plan(turn, "codex"), *(["resume", native] if native else []), message]


def argv_grok(message: str, native: str, turn: dict) -> list[str]:
    session = ["-r", native] if native else ["--session-id", turn["new_id"]]
    return ["grok", "--no-auto-update", *_model(turn), *_effort(turn, "grok"), *session, "--prompt-file", turn["message_file"], "--cwd", turn["repo"],
            "--output-format", "streaming-json"]


def argv_opencode(message: str, native: str, turn: dict) -> list[str]:
    return ["opencode", "run", "--format", "json", *_model(turn), *_effort(turn, "opencode"), *_plan(turn, "opencode"), "--dir", turn["repo"], *(["--session", native] if native else []), message]


def argv_omp(message: str, native: str, turn: dict) -> list[str]:
    return ["omp", "-p", "--mode", "json", *_model(turn), *_effort(turn, "omp"), "--cwd", turn["repo"], message]


def argv_pi(message: str, native: str, turn: dict) -> list[str]:
    return ["pi", "-p", "--mode", "json", *_model(turn), *_effort(turn, "pi"), message]


def argv_agy(message: str, native: str, turn: dict) -> list[str]:
    return ["agy", "-p", message, "--output-format", "json", *_effort(turn, "agy")]


HEADLESS = {"claude": argv_claude, "codex": argv_codex, "grok": argv_grok, "opencode": argv_opencode,
            "omp": argv_omp, "pi": argv_pi, "agy": argv_agy}
RESUMES = frozenset({"claude", "codex", "grok", "opencode"})
# A headless mode that can ask the user for permission: claude through Rig's rig-ask server, codex through its own
# MCP server mode (`codex mcp-server`), whose approval requests Rig answers from the board.
ASKS = frozenset({"claude", "codex"})
CODEX_START_SECONDS = 15


def enabled(repo) -> bool:
    if os.environ.get("RIG_EXPERIMENT_CHAT") == "1":
        return True
    import harness
    return bool(harness.parse_harness(harness.harness_path(Path(repo)))["experiments"]["chat"])


def require_enabled(repo) -> None:
    if not enabled(repo):
        raise ChatError("the chat experiment is off; set RIG_EXPERIMENT_CHAT=1 or add [experiments] chat = true to .rig/harness.toml")


def chat_dir(repo) -> Path:
    return Path(repo).resolve() / ".rig" / "chat"


def _session_id(value: str) -> str:
    if not SESSION.fullmatch(str(value or "")):
        raise ChatError("session must look like c-1a2b3c4d")
    return value


def _clean(text, limit=MAX_ENTRY) -> str:
    text = _CONTROL.sub("", str(text))
    return text if len(text) <= limit else text[:limit] + f"… ({len(text) - limit} more characters)"


# -- decoding ---------------------------------------------------------------
# the input that says what a tool is doing, for the tools every parent shares; any other tool shows its first text input
_TOOL_KEYS = {"Bash": "command", "Read": "file_path", "Edit": "file_path", "MultiEdit": "file_path", "Write": "file_path",
              "NotebookEdit": "notebook_path", "Grep": "pattern", "Glob": "pattern", "WebFetch": "url", "WebSearch": "query",
              "Task": "description", "Agent": "description", "Skill": "skill", "LS": "path", "BashOutput": "bash_id"}


def _tool_line(name, data) -> str:
    detail = ""
    if isinstance(data, dict):
        if name == "TodoWrite" and isinstance(data.get("todos"), list):
            detail = f"{len(data['todos'])} items"
        key = _TOOL_KEYS.get(str(name))
        value = data.get(key) if key else None
        if not detail and isinstance(value, str) and value.strip():
            detail = value.strip().splitlines()[0]
        if not detail:
            for value in data.values():
                if isinstance(value, str) and value.strip():
                    detail = value.strip().splitlines()[0]
                    break
    return _clean(f"{name} {detail}".strip(), 200)


def _result_line(content, error: bool = False) -> dict:
    """What a tool answered, as one line: its first line, and how many more there were."""
    if isinstance(content, list):
        content = "\n".join(str(b.get("text") or "") for b in content if isinstance(b, dict) and b.get("type") == "text")
    lines = [l for l in str(content or "").splitlines() if l.strip()]
    if not lines:
        text = "failed" if error else "done"
    else:
        text = lines[0].strip()
        if len(lines) > 1:
            text += f"  (+{len(lines) - 1} lines)"
    return {"role": "result-error" if error else "result", "text": _clean(text, 200)}


def _generic_event(event: dict, out: dict) -> dict:
    """The JSON lines of grok, opencode, omp, pi and agy, read the way Rig reads their job logs (jobs.activity_from_event).
    Streamed text chunks come back as role "chunk" for the runner to join into one answer."""
    import jobs
    props = event.get("properties") if isinstance(event.get("properties"), dict) else {}
    for key in ("sessionID", "sessionId", "session_id"):
        value = event.get(key) or props.get(key)
        if isinstance(value, str) and value.strip():
            out["native"] = value.strip()
            break
    kind = event.get("type")
    part = event.get("part") if isinstance(event.get("part"), dict) else {}
    if kind in ("text", "thought") or (kind == "message" and isinstance(event.get("text"), str)):
        data = event.get("data") or event.get("text") or part.get("text") or ""
        if isinstance(data, str) and data and kind != "thought":
            out["entries"].append({"role": "chunk", "text": data})
    elif kind == "tool_call" or (kind == "assistant" and jobs._assistant_tools(event)):
        line = jobs.activity_from_event(event)
        if line:
            out["entries"].append({"role": "tool", "text": _clean(line, 200)})
    elif kind == "assistant":
        text = jobs._assistant_text(event)
        if text and text.strip():
            out["entries"].append({"role": "assistant", "text": _clean(text)})
    elif kind == "error":
        out["done"], out["error"] = True, True
        out["entries"].append({"role": "error", "text": _clean(event.get("message") or "the parent reported an error")})
    elif kind == "result" or (kind is None and any(isinstance(event.get(k), str) for k in ("result", "response"))):
        out["done"] = True
        out["error"] = bool(event.get("is_error"))
        blob = event.get("result") or event.get("response") or ""
        if isinstance(blob, str) and blob.strip():
            out["entries"].append({"role": "final", "text": _clean(blob)})
    return out


def decode_event(parent: str, event) -> dict:
    """{'entries': [{role, text}], 'native': session id or '', 'done': bool, 'error': bool} for one stream event."""
    out = {"entries": [], "native": "", "done": False, "error": False, "usage": None}
    if not isinstance(event, dict):
        return out
    if parent not in ("claude", "codex"):
        return _generic_event(event, out)
    if parent == "claude":
        kind = event.get("type")
        if kind == "system" and event.get("subtype") == "init":
            out["native"] = str(event.get("session_id") or "")
        elif kind == "stream_event":  # --include-partial-messages: the answer's text as it is written
            inner = event.get("event") if isinstance(event.get("event"), dict) else {}
            delta = inner.get("delta") if isinstance(inner.get("delta"), dict) else {}
            if inner.get("type") == "content_block_delta" and delta.get("type") == "text_delta" and isinstance(delta.get("text"), str):
                out["entries"].append({"role": "chunk", "text": delta["text"]})
        elif kind == "assistant":
            content = (event.get("message") or {}).get("content")
            for block in content if isinstance(content, list) else []:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "text" and str(block.get("text") or "").strip():
                    out["entries"].append({"role": "assistant", "text": _clean(block["text"])})
                elif block.get("type") == "tool_use":
                    out["entries"].append({"role": "tool", "text": _tool_line(block.get("name") or "tool", block.get("input"))})
        elif kind == "user":  # what the tools answered
            content = (event.get("message") or {}).get("content")
            for block in content if isinstance(content, list) else []:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    out["entries"].append(_result_line(block.get("content"), bool(block.get("is_error"))))
        elif kind == "result":
            out["native"] = str(event.get("session_id") or "")
            out["done"], out["error"] = True, bool(event.get("is_error"))
            used = event.get("usage") if isinstance(event.get("usage"), dict) else {}
            seen = sum(int(used.get(k) or 0) for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))
            out["usage"] = {"cost": float(event.get("total_cost_usd") or 0), "input": seen, "output": int(used.get("output_tokens") or 0),
                            "context": seen}
            if out["error"]:
                out["entries"].append({"role": "error", "text": _clean(event.get("result") or event.get("subtype") or "the parent reported an error")})
    elif parent == "codex":
        kind = event.get("type")
        if kind == "thread.started":
            out["native"] = str(event.get("thread_id") or "")
        elif kind in ("item.completed", "item.started"):
            item = event.get("item") if isinstance(event.get("item"), dict) else {}
            if item.get("type") == "agent_message" and kind == "item.completed" and str(item.get("text") or "").strip():
                out["entries"].append({"role": "assistant", "text": _clean(item["text"])})
            elif item.get("type") == "command_execution" and kind == "item.started":
                out["entries"].append({"role": "tool", "text": _clean("ran " + str(item.get("command") or ""), 200)})
            elif item.get("type") == "command_execution":
                code = item.get("exit_code")
                failed = isinstance(code, int) and code != 0
                if failed or str(item.get("aggregated_output") or "").strip():  # a command that said nothing needs no line
                    out["entries"].append(_result_line(item.get("aggregated_output"), failed))
            elif item.get("type") == "file_change" and kind == "item.completed":
                paths = [str(c.get("path")) for c in item.get("changes", []) if isinstance(c, dict) and c.get("path")]
                out["entries"].append({"role": "tool", "text": _clean("edited " + (", ".join(paths) if paths else "files"), 200)})
            elif item.get("type") == "mcp_tool_call" and kind == "item.started":
                out["entries"].append({"role": "tool", "text": _clean(f"{item.get('server', 'mcp')}.{item.get('tool', 'tool')}", 200)})
            elif item.get("type") == "web_search" and kind == "item.started":
                out["entries"].append({"role": "tool", "text": _clean("search " + str(item.get("query") or ""), 200)})
            elif item.get("type") == "todo_list" and kind == "item.completed":
                out["entries"].append({"role": "tool", "text": f"plan {len(item.get('items') or [])} steps"})
        elif kind == "turn.completed":
            out["done"] = True
            used = event.get("usage") if isinstance(event.get("usage"), dict) else {}
            seen = int(used.get("input_tokens") or 0)
            out["usage"] = {"cost": 0.0, "input": seen, "output": int(used.get("output_tokens") or 0), "context": seen}
        elif kind in ("turn.failed", "error"):
            out["done"], out["error"] = True, True
            err = event.get("error")
            message = event.get("message") or (err.get("message") if isinstance(err, dict) else None)
            out["entries"].append({"role": "error", "text": _clean(message or "the parent reported an error")})
    return out


# -- sessions ---------------------------------------------------------------
def _meta_path(repo, session):
    return chat_dir(repo) / f"{session}.json"


def _write_json(path: Path, value) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=1) + "\n")
    os.replace(temporary, path)


def _alive(pid) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    try:  # a zombie is not running
        return Path(f"/proc/{pid}/stat").read_text().split()[2] != "Z"
    except (OSError, IndexError):
        return True


def read_meta(repo, session) -> dict:
    path = _meta_path(repo, _session_id(session))
    try:
        meta = json.loads(path.read_text())
    except (OSError, ValueError) as error:
        raise ChatError(f"no such chat session: {session}") from error
    if meta.get("state") == "running" and not _alive(meta.get("runner_pid")):
        meta.update(state="failed", note="the runner stopped before the turn finished")
        _write_json(path, meta)
    return meta


def sessions(repo) -> list[dict]:
    folder = chat_dir(repo)
    rows = []
    for path in sorted(folder.glob("c-*.json")) if folder.is_dir() else []:
        try:
            rows.append(read_meta(repo, path.stem))
        except ChatError:
            continue
    return sorted(rows, key=lambda row: row.get("created", ""), reverse=True)


def _title(repo, session: str) -> str:
    """What a conversation was about: its first message, on one line."""
    path = chat_dir(repo) / f"{session}.jsonl"
    try:
        with path.open(errors="replace") as stream:
            for line in stream:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict) and row.get("role") == "user" and isinstance(row.get("text"), str):
                    return " ".join(row["text"].split())[:100]
    except OSError:
        pass
    return ""


def _updated(repo, session: str) -> float:
    """When the conversation was last used: its transcript or its state file, whichever was written last."""
    stamps = []
    for path in (chat_dir(repo) / f"{session}.jsonl", _meta_path(repo, session)):
        try:
            stamps.append(path.stat().st_mtime)
        except OSError:
            pass
    return max(stamps) if stamps else 0.0


SEARCH_ROWS = 20
SEARCH_ROLES = frozenset({"user", "assistant", "summary"})


def search(repo, query: str) -> list[dict]:
    """The conversations whose messages (yours and the parent's) contain the words of the query, the one used last first:
    {session, parent, title, updated, where: who said it, snippet: the line around the first hit}."""
    require_enabled(repo)
    words = [w for w in str(query or "").lower().split() if w][:6]
    if not words or len("".join(words)) < 2:
        return []
    found = []
    for meta in sessions(repo):
        path = chat_dir(repo) / f"{meta['id']}.jsonl"
        try:
            lines = path.read_text(errors="replace").splitlines()
        except OSError:
            continue
        for line in lines:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            text = row.get("text") if isinstance(row, dict) else None
            if not isinstance(text, str) or row.get("role") not in SEARCH_ROLES:
                continue
            low = text.lower()
            if all(w in low for w in words):
                at = max(0, low.find(words[0]) - 30)
                snippet = " ".join(text[at:at + 110].split())
                found.append({"session": meta["id"], "parent": meta["parent"], "title": _title(repo, meta["id"]),
                              "updated": _updated(repo, meta["id"]), "where": "you" if row["role"] == "user" else meta["parent"],
                              "snippet": snippet})
                break
    return sorted(found, key=lambda row: row["updated"], reverse=True)[:SEARCH_ROWS]


EXPORT_COMMAND = re.compile(r"/export(?:\s+(\S.*))?\s*")
EXPORT_TOOLS = {"tool": "▸", "result": "  ↳", "result-error": "  ↳ error:"}


def export(repo, session: str, path: str = "") -> dict:
    """Write a conversation as markdown: the messages, the tools the parent used and the files each turn changed. The file is
    .rig/exports/ID.md unless a path inside the project is given."""
    repo = Path(repo).resolve()
    require_enabled(repo)
    meta = read_meta(repo, session)
    try:
        lines = (chat_dir(repo) / f"{meta['id']}.jsonl").read_text(errors="replace").splitlines()
    except OSError:
        lines = []
    out = [f"# {_title(repo, meta['id']) or 'Conversation'}", "",
           f"*{meta['id']} · {meta['parent']} · {meta.get('turns', 0)} turn{'s' if meta.get('turns', 0) != 1 else ''}"
           f" · exported {time.strftime('%Y-%m-%d %H:%M', time.gmtime())} UTC*", ""]
    who = meta.get("first_parent") or meta["parent"]
    count = 0
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if not isinstance(row, dict) or not isinstance(row.get("text"), str):
            continue
        role, text = str(row.get("role", "")), row["text"]
        if role == "switch" and " → " in text:
            who = text.split(" → ", 1)[1]
        count += 1
        if role == "user":
            out += ["## You", "", text, ""]
        elif role == "assistant":
            out += [f"## {who}", "", text, ""]
        elif role in EXPORT_TOOLS:
            out += [f"{EXPORT_TOOLS[role]} `{' '.join(text.split())[:200]}`", ""]
        elif role == "diff":
            head, _, body = text.partition("\n")
            out += [f"**changed {head}**", "", "```diff", body, "```", ""]
        elif role == "summary":
            out += ["> **Summary carried over**", ">", *["> " + part for part in text.splitlines()], ""]
        elif role in ("system", "switch", "error"):
            out += [f"*{role}: {' '.join(text.split())}*", ""]
    target = Path(path) if path else Path(".rig") / "exports" / f"{meta['id']}.md"
    target = (repo / target).resolve()
    if repo not in target.parents or target.parts[len(repo.parts)] == ".git":
        raise ChatError("an export goes to a file inside the project")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(out))
    return {"session": meta["id"], "path": str(target.relative_to(repo)), "entries": count}


def new_session(repo, parent: str) -> dict:
    folder = chat_dir(repo)
    folder.mkdir(parents=True, exist_ok=True)
    os.chmod(folder, 0o700)
    meta = {"id": "c-" + secrets.token_hex(4), "parent": parent, "native": "", "state": "idle", "turns": 0, "runner_pid": 0,
            "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    _write_json(_meta_path(repo, meta["id"]), meta)
    return meta


def _count(repo, session) -> int:
    path = chat_dir(repo) / f"{session}.jsonl"
    try:
        with path.open("rb") as stream:
            return sum(1 for _ in stream)
    except OSError:
        return 0


def hand_over(repo, meta: dict, parent: str) -> dict:
    """The conversation goes on with another parent: each parent keeps its own native session, and the one taking over
    is told on its next message what was said since it last took part (since the start, for a newcomer)."""
    old = meta["parent"]
    natives, seen = dict(meta.get("natives") or {}), dict(meta.get("seen") or {})
    natives[old], seen[old] = meta.get("native", ""), _count(repo, meta["id"])
    append(repo, meta["id"], "switch", f"{old} → {parent}")
    meta.setdefault("first_parent", old)
    meta.update(parent=parent, native=natives.get(parent, ""), natives=natives, seen=seen,
                context_from=seen.get(parent, 0), handed_by=old, model="")
    meta.pop("asks", None)
    return meta


def append(repo, session, role: str, text: str) -> None:
    path = chat_dir(repo) / f"{session}.jsonl"
    with path.open("a") as stream:
        stream.write(json.dumps({"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "role": role, "text": text}) + "\n")


def transcript(repo, session, tail: int = 200) -> dict:
    require_enabled(repo)
    meta = read_meta(repo, session)
    path = chat_dir(repo) / f"{session}.jsonl"
    rows = []
    if path.is_file():
        for line in path.read_text(errors="replace").splitlines()[-max(1, min(int(tail), MAX_ENTRIES)):]:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict) and isinstance(row.get("text"), str):
                rows.append({"role": str(row.get("role", "")), "text": row["text"], "at": str(row.get("at", ""))})
    # who said each thing: the parent at the time, from the switches in between
    who = meta.get("first_parent") or meta["parent"]
    if path.is_file() and rows:
        skipped = max(0, _count(repo, session) - len(rows))
        if skipped:
            for line in path.read_text(errors="replace").splitlines()[:skipped]:
                if '"switch"' in line:
                    try:
                        who = json.loads(line)["text"].split(" → ", 1)[1]
                    except (ValueError, KeyError, IndexError):
                        pass
    for row in rows:
        if row["role"] == "switch" and " → " in row["text"]:
            who = row["text"].split(" → ", 1)[1]
        row["parent"] = who
    return {"session": meta["id"], "parent": meta["parent"], "state": meta["state"], "turns": meta.get("turns", 0),
            "note": meta.get("note", ""), "plan": bool(meta.get("plan")), "mode": _mode(meta), "native": str(meta.get("native") or ""),
            "successor": str(meta.get("successor") or ""), "context_pct": context_percent(meta),
            "marks": [{"turn": m.get("turn", 0), "at": m.get("at", ""), "title": m.get("title", ""), "files": m.get("files", [])}
                      for m in (meta.get("marks") or []) if isinstance(m, dict)],
            "can_undo": isinstance(meta.get("rewind_backup"), dict),
            "entries": rows, "ask": pending_ask(repo, meta["id"]),
            "partial": _partial(repo, meta) if meta["state"] == "running" else "",
            "asks": bool(meta.get("asks", meta["parent"] in ASKS))}


def _partial_path(repo, session) -> Path:
    return chat_dir(repo) / session / "partial.txt"


def _partial(repo, meta) -> str:
    try:
        return _clean(_partial_path(repo, meta["id"]).read_text(errors="replace"))
    except OSError:
        return ""


def write_partial(repo, session, text: str) -> None:
    """The answer as it streams in, until the runner writes it to the transcript; empty text clears it."""
    path = _partial_path(repo, session)
    if not text:
        path.unlink(missing_ok=True)
        return
    temporary = path.with_suffix(".tmp")
    temporary.write_text(text)
    os.replace(temporary, path)


def status(repo) -> dict:
    on = enabled(repo)
    available = [name for name in HEADLESS if shutil.which(name)]
    import harness
    configured = harness.parse_harness(harness.harness_path(Path(repo)))["parent"]
    parent = default_parent(repo)
    import ui_settings
    chosen, _ = ui_settings.chosen_model(repo, parent)
    effort, _ = ui_settings.chosen_effort(repo, parent)
    result = {"enabled": on, "parents": sorted(HEADLESS), "available": available, "sessions": [], "parent": parent,
              "configured": configured, "model": chosen or parent_model(parent), "model_chosen": bool(chosen), "effort": effort}
    if on:
        rows = [{"id": s["id"], "parent": s["parent"], "state": s["state"], "turns": s.get("turns", 0),
                 "title": _title(repo, s["id"]), "updated": _updated(repo, s["id"])} for s in sessions(repo)[:60]]
        result["sessions"] = sorted(rows, key=lambda row: row["updated"], reverse=True)  # the one used last first, for /resume
    return result


_FAMILY = re.compile(r"(opus|sonnet|haiku|fable)", re.I)


def parent_model(parent: str) -> str:
    """The model the parent CLI is set to use, from its own settings, in the mockup's short form ("opus"); empty when
    its settings name none. Reads the file only; never runs the CLI."""
    try:
        if parent == "claude":
            model = json.loads((Path.home() / ".claude" / "settings.json").read_text()).get("model")
            if isinstance(model, str):
                family = _FAMILY.search(model)
                return family.group(1).lower() if family else model.strip()[:24]
        elif parent == "codex":
            import child_mcp
            model = child_mcp._load_toml(Path.home() / ".codex" / "config.toml").get("model")
            if isinstance(model, str):
                return model.strip()[:24]
    except (OSError, ValueError, AttributeError, UnicodeError):
        pass
    return ""


PREFERRED = ("claude", "codex", "grok", "opencode", "omp", "pi", "agy")  # the order a parent is picked in when none is set


def default_parent(repo) -> str:
    """The project's parent, or else the first parent CLI installed here; empty when there is none at all."""
    import harness
    parent = harness.parse_harness(harness.harness_path(Path(repo)))["parent"]
    if parent:
        return parent
    return next((name for name in PREFERRED if shutil.which(name)), "")


def choose_parent(repo, parent: str = "") -> str:
    if not parent:
        parent = default_parent(repo)
        if not parent:
            raise ChatError("no parent CLI is installed here (" + ", ".join(PREFERRED) + "); install one, or name one with "
                            "rig shell --parent NAME")
    if parent not in HEADLESS:
        raise ChatError(f"the chat supports {', '.join(sorted(HEADLESS))} as the parent, not {parent or 'none'}; pass --parent")
    if not shutil.which(parent):
        raise ChatError(f"{parent} is not on PATH")
    return parent


_TOP_PARENT = re.compile(r"\s*parent\s*=")


def set_parent(repo, name: str) -> dict:
    """Name the project's parent in .rig/harness.toml (top-level ``parent``), as `rig use` does; the board's picker."""
    import harness
    name = str(name or "").strip()
    if name not in HEADLESS:
        raise ChatError(f"the parent is one of {', '.join(PREFERRED)}, not {name or 'none'}")
    if not shutil.which(name):
        raise ChatError(f"{name} is not on PATH")
    path = harness.harness_path(Path(repo).resolve())
    if not path.is_file():  # never create the harness here: a new one would turn Rig on for the project
        raise ChatError("this project has no .rig/harness.toml; run rig init first")
    lines = path.read_text().splitlines()
    out, placed, top = [], False, True
    for line in lines:
        stripped = line.strip()
        if top and stripped.startswith("[") and not placed:
            out.append(f'parent = "{name}"')  # a top-level key must come before the first table
            placed = True
        if stripped.startswith("["):
            top = False
        if top and _TOP_PARENT.match(line) and not stripped.startswith("#"):
            if not placed:
                out.append(f'parent = "{name}"')
                placed = True
            continue
        out.append(line)
    if not placed:
        out.append(f'parent = "{name}"')
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text("\n".join(out) + "\n")
    os.replace(temporary, path)
    return {"parent": name, "path": str(path)}


PLAN_COMMAND = re.compile(r"/plan(?:\s+(.*))?", re.S)
FORK_COMMAND = re.compile(r"/fork(?:\s+(\d+))?\s*")
REWIND_COMMAND = re.compile(r"/rewind(?:\s+(\S+))?\s*")
COMPACT_COMMAND = re.compile(r"/compact(?:\s+(.*))?", re.S)
# /compact asks the parent, which still has the whole conversation, for a summary; the next conversation starts from it
COMPACT_PROMPT = ("[Compact] Write a summary of this conversation so that I can continue it in a fresh one without losing anything "
                  "that matters: what we are doing and why, the decisions made, the files and commands that matter, what is done, "
                  "what is left, and anything I told you to keep in mind. Be specific and concise (under 400 words). Change "
                  "nothing and call no tools; reply with the summary only.")
CONTEXT_WARN = 80  # percent of the window at which a turn's end says so, once


def _window(meta: dict) -> int:
    """How many tokens of context the parent's model holds, when it is known: claude's, 1M for its [1m] models."""
    if meta.get("parent") != "claude":
        return 0
    return 1_000_000 if "[1m]" in str(meta.get("model", "")) else 200_000


def context_percent(meta: dict) -> int:
    """How full the parent's context is after its last turn, in percent; -1 when it is not known."""
    used = meta.get("usage") if isinstance(meta.get("usage"), dict) else {}
    window = _window(meta)
    if not window or not used.get("context"):
        return -1
    return min(100, int(used["context"]) * 100 // window)


def _finish_compaction(repo, fresh: dict, error: bool) -> None:
    """After a /compact turn: the summary the parent wrote starts a new conversation, and this one points to it."""
    fresh["compacting"] = False
    if error:
        append(repo, fresh["id"], "system", "compact failed: the turn did not finish; nothing changed")
        return
    rows = []
    path = chat_dir(repo) / f"{fresh['id']}.jsonl"
    for line in path.read_text(errors="replace").splitlines() if path.is_file() else []:
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    last_user = max((i for i, r in enumerate(rows) if r.get("role") == "user"), default=-1)
    summary = "\n\n".join(r["text"] for r in rows[last_user + 1:] if r.get("role") == "assistant" and isinstance(r.get("text"), str)).strip()
    if not summary:
        append(repo, fresh["id"], "system", "compact failed: the parent wrote no summary; nothing changed")
        return
    new = new_session(repo, fresh["parent"])
    new.update(summary=summary[:12000], plan=bool(fresh.get("plan")), edits=bool(fresh.get("edits")), compacted_from=fresh["id"])
    _write_json(_meta_path(repo, new["id"]), new)
    append(repo, new["id"], "system", f"continuing from {fresh['id']}: the summary below goes with your next message")
    append(repo, new["id"], "summary", _clean(summary, 6000))
    fresh["successor"] = new["id"]
    append(repo, fresh["id"], "system", f"compacted: continuing in {new['id']} (this one stays in /resume)")
INFO_COMMAND = re.compile(r"/(cost|context|mcp)\s*")
PROMPT_COMMAND = re.compile(r"/(init|review|security-review)(?:\s+(.*))?", re.S)


def _builtin_prompt(parent: str, name: str, rest: str) -> str:
    """What /init, /review and /security-review ask the parent: the prompts those commands stand for in the parents' own
    screens, written out, so they work in every parent's headless mode."""
    memory = "CLAUDE.md" if parent == "claude" else "AGENTS.md"
    if name == "init":
        return (f"Look through this codebase and write a {memory} file for it (improve the one that is there if there is one): "
                "how to build, test and lint it, how it is laid out, and the conventions a newcomer must follow. Keep it short and "
                "specific to this project, and do not repeat what the code makes obvious." + (f" {rest}" if rest else ""))
    if name == "review":
        return ("Review the current changes: the staged and unstaged diff and any new files (git status and git diff). Report bugs, risky "
                "changes and missing tests, most serious first, each with its file and line. Do not edit anything." + (f" Focus: {rest}" if rest else ""))
    return ("Review the current changes (git status and git diff) for security problems: injection, unsafe file or network access, secrets, "
            "missing checks on input. Report each with its file and line and how to fix it. Do not edit anything." + (f" Focus: {rest}" if rest else ""))


def _tokens(n: int) -> str:
    return f"{n / 1000:.1f}k" if n >= 1000 else str(n)


def _mcp_servers(repo, parent: str) -> list[str]:
    """The MCP servers the parent has configured, as its own files name them."""
    names: list[str] = []
    home = Path.home()
    try:
        if parent == "claude":
            for path in (Path(repo) / ".mcp.json", home / ".claude.json"):
                try:
                    data = json.loads(path.read_text())
                except (OSError, ValueError):
                    continue
                names += list((data.get("mcpServers") or {}))
                names += list(((data.get("projects") or {}).get(str(Path(repo).resolve())) or {}).get("mcpServers") or {})
        elif parent == "codex":
            import child_mcp
            names += list(child_mcp._load_toml(home / ".codex" / "config.toml").get("mcp_servers") or {})
    except (OSError, ValueError, AttributeError):
        pass
    return sorted(dict.fromkeys(names))


def _info(repo, meta: dict, what: str) -> str:
    """What /cost, /context and /mcp say, from what the turns reported and the parent's own config."""
    parent = meta["parent"]
    used = meta.get("usage") if isinstance(meta.get("usage"), dict) else {}
    if what == "cost":
        if not used:
            return f"nothing counted yet: {parent} reports what a turn used when it ends" + ("" if parent in ("claude", "codex") else "; this parent does not report it")
        text = f"{_tokens(used.get('input', 0))} tokens in, {_tokens(used.get('output', 0))} out over {used.get('turns', 0)} turns"
        return (f"cost so far: ${used['cost']:.2f} · " if used.get("cost") else "usage so far: ") + text
    if what == "context":
        if not used:
            return f"nothing counted yet: {parent} reports its context when a turn ends"
        window = _window(meta)
        share = f" of {_tokens(window)} ({used['context'] * 100 // window}%)" if window else ""
        return f"context: about {_tokens(used['context'])} tokens in the last turn{share}" + (
            "; /compact continues in a fresh conversation from a summary" if window and used["context"] * 100 // window >= 50 else "")
    servers = _mcp_servers(repo, parent)
    return ("MCP servers for " + parent + ": " + ", ".join(servers)) if servers else (
        f"no MCP servers found in {parent}'s own config" if parent in ("claude", "codex") else f"{parent}'s MCP servers are in its own settings")


def send(repo, message: str, session: str = "", parent: str = "") -> dict:
    """Start one turn. Returns at once; the transcript fills as the detached runner decodes the stream."""
    repo = Path(repo).resolve()
    require_enabled(repo)
    message = str(message or "").strip()
    if not message or len(message) > MAX_MESSAGE or "\0" in message:
        raise ChatError(f"a message is 1 to {MAX_MESSAGE} characters")
    meta = read_meta(repo, session) if session else new_session(repo, choose_parent(repo, parent))
    if meta["state"] == "running":
        raise ChatError(f"session {meta['id']} is still answering; wait for it or cancel it")
    if session and parent and parent != meta["parent"]:
        meta = hand_over(repo, meta, choose_parent(repo, parent))
    shown = message
    info = INFO_COMMAND.fullmatch(message)  # these answer without a turn, so the parent CLI need not be installed
    if info:
        append(repo, meta["id"], "system", _info(repo, meta, info.group(1)))
        return {"session": meta["id"], "parent": meta["parent"], "state": meta["state"], "turn": meta.get("turns", 0)}
    moded = MODE_COMMAND.fullmatch(message)
    if moded:
        # /mode shows how much the parent may do without asking; /mode ask|edits|plan sets it; /mode next goes round
        word = (moded.group(1) or "").lower()
        if word == "next":
            word = {"ask": "edits", "edits": "plan", "plan": "ask"}[_mode(meta)]
        if word:
            if word not in MODE_WORDS:
                raise ChatError("a mode is ask, edits or plan (or next)")
            meta["plan"], meta["edits"] = word == "plan", word == "edits"
            _write_json(_meta_path(repo, meta["id"]), meta)
        now = _mode(meta)
        note = f"mode {now}: {meta['parent']} {MODE_WORDS[now]}"
        if now == "edits" and meta["parent"] not in ("claude", "codex"):
            note += " (" + meta["parent"] + "'s headless mode does not ask for permission at all)"
        append(repo, meta["id"], "system", note)
        return {"session": meta["id"], "parent": meta["parent"], "state": meta["state"], "turn": meta.get("turns", 0), "mode": now}
    kept = REMEMBER_COMMAND.fullmatch(message)
    if kept:
        result = remember(repo, kept.group(1) or "")
        append(repo, meta["id"], "system", f"kept in {NOTES_FILE} ({result['lines']} notes): it goes with every conversation's first message")
        return {"session": meta["id"], "parent": meta["parent"], "state": meta["state"], "turn": meta.get("turns", 0), "notes": result["lines"]}
    if NOTES_COMMAND.fullmatch(message):
        text = notes(repo)
        append(repo, meta["id"], "system", (NOTES_FILE + ":\n" + text) if text else f"no notes yet: /remember NOTE keeps one in {NOTES_FILE}")
        return {"session": meta["id"], "parent": meta["parent"], "state": meta["state"], "turn": meta.get("turns", 0)}
    exported = EXPORT_COMMAND.fullmatch(message)
    if exported:
        result = export(repo, meta["id"], (exported.group(1) or "").strip())
        append(repo, meta["id"], "system", f"exported {result['entries']} entries to {result['path']}")
        return {"session": meta["id"], "parent": meta["parent"], "state": meta["state"], "turn": meta.get("turns", 0),
                "exported": result["path"]}
    forked = FORK_COMMAND.fullmatch(message)
    if forked:
        if meta["state"] == "running":
            raise ChatError(f"session {meta['id']} is still answering; wait for it or cancel it")
        return fork(repo, meta["id"], int(forked.group(1) or 0))  # the new conversation: the caller moves to it
    rewound = REWIND_COMMAND.fullmatch(message)
    if rewound:
        # /rewind lists the turns that changed files; /rewind N puts them back as before turn N; /rewind undo takes it back
        arg = (rewound.group(1) or "").strip()
        marks = [m for m in (meta.get("marks") or []) if isinstance(m, dict)]
        if arg == "undo" or arg.isdigit():
            result = rewind(repo, meta["id"], int(arg) if arg.isdigit() else 0, undo=arg == "undo")
            return {"session": meta["id"], "parent": meta["parent"], "state": meta["state"], "turn": meta.get("turns", 0),
                    "rewound": result["paths"]}
        rows = [f"turn {m['turn']}: " + ", ".join(f["path"] for f in m.get("files", [])[:4]) + f" · {m.get('title', '')}" for m in marks[-10:]]
        append(repo, meta["id"], "system", ("turns that changed files (/rewind N puts them back as before turn N):\n" + "\n".join(rows))
               if rows else "no turn has changed files in this conversation yet")
        return {"session": meta["id"], "parent": meta["parent"], "state": meta["state"], "turn": meta.get("turns", 0)}
    planned = PLAN_COMMAND.fullmatch(message)
    if planned:
        # /plan: plan mode on, off or toggled for this conversation; /plan TEXT asks TEXT in plan mode; /plan go leaves it
        arg = (planned.group(1) or "").strip()
        word = arg.lower()
        if word in ("", "on", "off", "exit", "stop"):
            on = (not meta.get("plan")) if word == "" else word == "on"
            meta["plan"] = on
            append(repo, meta["id"], "system", ("plan mode on: " + meta["parent"] + " plans and changes nothing until /plan off or /plan go")
                   if on else "plan mode off")
            _write_json(_meta_path(repo, meta["id"]), meta)
            return {"session": meta["id"], "parent": meta["parent"], "state": meta["state"], "turn": meta.get("turns", 0), "plan": on}
        if word == "go":
            meta["plan"], message = False, "Go ahead with the plan."
            append(repo, meta["id"], "system", "plan mode off")
        else:
            meta["plan"], message = True, arg
    compact = COMPACT_COMMAND.fullmatch(message)
    if compact:
        if not meta.get("turns"):
            append(repo, meta["id"], "system", "nothing to compact yet: this conversation has no turns")
            return {"session": meta["id"], "parent": meta["parent"], "state": meta["state"], "turn": 0}
        rest = (compact.group(1) or "").strip()
        message = COMPACT_PROMPT + (f"\nFocus on: {rest}" if rest else "")
        meta["compacting"] = True
    prompt = PROMPT_COMMAND.fullmatch(message)
    if prompt:
        import parent_commands
        if any(c["name"] == prompt.group(1) for c in parent_commands.commands(repo, meta["parent"])):
            prompt = None  # the parent has a command of that name of its own: that one runs
    if prompt:
        message = _builtin_prompt(meta["parent"], prompt.group(1), (prompt.group(2) or "").strip())
    choose_parent(repo, meta["parent"])
    append(repo, meta["id"], "user", _clean(shown))
    meta.update(state="running", turns=meta.get("turns", 0) + 1, note="")
    runner = subprocess.Popen([sys.executable, str(HERE / "rig_chat.py"), "run-turn", "--repo", str(repo), "--session", meta["id"]],
                              stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
                              env={**os.environ, "RIG_PARENT": meta["parent"], "RIG_OWNER_SESSION": meta["id"]})
    meta["runner_pid"] = runner.pid
    _write_json(_meta_path(repo, meta["id"]), meta)
    runner.stdin.write(message.encode())  # the message travels on stdin, never in the runner's own argv
    runner.stdin.close()
    import threading
    threading.Thread(target=runner.wait, daemon=True).start()  # reap it: a long-lived API server must not collect zombies
    return {"session": meta["id"], "parent": meta["parent"], "state": "running", "turn": meta["turns"]}


def cancel(repo, session: str, why: str = "cancelled") -> dict:
    require_enabled(repo)
    meta = read_meta(repo, session)
    if meta["state"] != "running":
        return {"session": meta["id"], "state": meta["state"], "cancelled": False}
    pid = meta.get("runner_pid")
    meta.update(state="idle", note=why)
    _write_json(_meta_path(repo, meta["id"]), meta)  # before the signal: the runner reads it as it winds up
    try:
        os.killpg(pid, signal.SIGTERM)  # the runner leads its own group, with the parent CLI inside it; the runner itself
    except (ProcessLookupError, PermissionError, TypeError):  # survives the signal to note what the turn changed
        pass
    append(repo, meta["id"], "system", why)
    return {"session": meta["id"], "state": "idle", "cancelled": True}


STEER_NOTE = ("[Note from Rig: the user interrupted your previous turn with the message below. What you had done before the "
              "interruption stands; what you had not finished was not done. Go on from the message.]")
STEER_SECONDS = 10


def steer(repo, session: str, message: str) -> dict:
    """A message for the parent now, in the middle of its turn: the turn is stopped, what it changed so far is noted, and
    the parent goes on from the message, told that it was interrupted. On an idle session it is a plain send."""
    repo = Path(repo).resolve()
    require_enabled(repo)
    meta = read_meta(repo, session)
    if meta["state"] == "running":
        pid = meta.get("runner_pid")
        cancel(repo, meta["id"], "interrupted")
        deadline = time.monotonic() + STEER_SECONDS
        while _alive(pid) and time.monotonic() < deadline:
            time.sleep(0.05)  # the runner notes what the turn changed, then exits
        if _alive(pid):
            try:
                os.killpg(pid, signal.SIGKILL)  # it is stuck: it must not write the next turn's state over ours
            except (ProcessLookupError, PermissionError, TypeError):
                pass
        meta = read_meta(repo, meta["id"])
        meta["notes"] = list(meta.get("notes") or []) + [STEER_NOTE]
        _write_json(_meta_path(repo, meta["id"]), meta)
    return send(repo, message, meta["id"])


def turn_dir(repo, session) -> Path:
    """Per-session working files: the message for CLIs that read a prompt file, and the claude permission exchange."""
    folder = chat_dir(repo) / _session_id(session)
    folder.mkdir(mode=0o700, exist_ok=True)
    return folder


def _said(rows, parent: str) -> list[str]:
    """The conversation's lines, each with who said it: the user, this parent ("you"), or another parent by name."""
    out = []
    for row in rows:
        if row["role"] == "user":
            out.append("user: " + row["text"][:1500])
        elif row["role"] == "assistant":
            name = row.get("parent") or parent
            out.append(("you" if name == parent else name) + ": " + row["text"][:1500])
    return out


def _with_context(repo, session, message: str) -> str:
    """A parent that cannot resume its own session gets the recent conversation with each message, so it can follow."""
    meta = read_meta(repo, session)
    rows = [row for row in transcript(repo, session, tail=40)["entries"] if row["role"] in ("user", "assistant")][:-1][-8:]
    if not rows:
        return message
    return "Earlier in this conversation:\n" + "\n".join(_said(rows, meta["parent"])) + "\n\nNow: " + message


HANDOFF_TURNS = 12


def _handoff(repo, session, meta: dict, message: str) -> str:
    """A parent taking the conversation over: what was said since it last took part, once, before the message."""
    path = chat_dir(repo) / f"{session}.jsonl"
    rows = []
    try:
        lines = path.read_text(errors="replace").splitlines()
    except OSError:
        lines = []
    who = meta.get("first_parent") or meta["parent"]
    for i, line in enumerate(lines):
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if not isinstance(row, dict) or not isinstance(row.get("text"), str):
            continue
        if row.get("role") == "switch" and " → " in row["text"]:
            who = row["text"].split(" → ", 1)[1]
        if i >= int(meta.get("context_from") or 0):
            rows.append({"role": row.get("role", ""), "text": row["text"], "parent": who})
    rows = [row for row in rows if row["role"] in ("user", "assistant")][:-1][-HANDOFF_TURNS:]  # not the message itself
    if not rows:
        return message
    by = meta.get("handed_by") or "another agent"
    return (f"You are taking this conversation over from {by}. What was said since you last took part:\n"
            + "\n".join(_said(rows, meta["parent"]))
            + "\n\nHelpers started earlier keep their work; Rig's job tools list them.\n\nNow: " + message)


FORK_ROWS = 12  # of the conversation, what the fork's parent is told


def fork(repo, session: str, turn: int = 0) -> dict:
    """A new conversation that starts as a copy of this one up to a turn (the whole of it when turn is 0): the same parent, the
    same transcript and the same turns to rewind to. The parent starts fresh and is told once what was said, so the two
    conversations go different ways from the same point and both stay in /resume."""
    repo = Path(repo).resolve()
    require_enabled(repo)
    meta = read_meta(repo, session)
    path = chat_dir(repo) / f"{meta['id']}.jsonl"
    try:
        lines = path.read_text(errors="replace").splitlines()
    except OSError:
        lines = []
    rows, kept, users = [], [], 0
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if not isinstance(row, dict) or not isinstance(row.get("text"), str):
            continue
        if row.get("role") == "user":
            users += 1
            if turn and users > int(turn):
                break
        kept.append(line)
        rows.append(row)
    if not users or (turn and int(turn) > users):
        raise ChatError(f"no turn {turn} to fork from" if turn else "nothing to fork yet: this conversation has no turns")
    cut = users if not turn else int(turn)
    fresh = new_session(repo, meta["parent"])
    new_path = chat_dir(repo) / f"{fresh['id']}.jsonl"
    new_path.write_text("\n".join(kept) + "\n")
    told = [r for r in rows if r.get("role") in ("user", "assistant")][-FORK_ROWS:]
    fresh.update(turns=cut, plan=bool(meta.get("plan")), edits=bool(meta.get("edits")), fork_of=meta["id"],
                 marks=[m for m in (meta.get("marks") or []) if isinstance(m, dict) and int(m.get("turn", 0)) <= cut],
                 notes=["[This conversation is a fork of an earlier one, at turn " + str(cut) + ". What was said:\n"
                        + "\n".join(_said([{**r, "parent": meta["parent"]} for r in told], meta["parent"])) + "]"])
    append(repo, fresh["id"], "system", f"forked from {meta['id']} at turn {cut}: the two go their own ways from here")
    _write_json(_meta_path(repo, fresh["id"]), fresh)
    return {"session": fresh["id"], "parent": fresh["parent"], "state": "idle", "turn": cut, "forked_from": meta["id"]}


def _ask_server(folder: Path) -> str:
    """The MCP config that gives claude Rig's permission prompt, answered from the board (chat.answer)."""
    config = {"mcpServers": {"rig-ask": {"command": sys.executable, "args": [str(HERE / "claude-ask.py")],
                                         "env": {"RIG_JOB_DIR": str(folder)}}}}
    path = folder / "ask-mcp.json"
    _write_json(path, config)
    return str(path)


def pending_ask(repo, session) -> dict | None:
    import ask
    folder = chat_dir(repo) / session
    if not folder.is_dir():
        return None
    request = ask.load_ask(folder)
    if not request:
        return None
    return {"id": request["ask_id"], "tool": request.get("tool_name", ""), "preview": request.get("preview", "")}


def answer(repo, session: str, ask_id: str, decision: str) -> dict:
    """Allow or deny the parent's pending permission request. Only the request the owner saw can be answered."""
    import ask
    require_enabled(repo)
    meta = read_meta(repo, session)
    if decision not in ("allow", "deny"):
        raise ChatError("decision must be allow or deny")
    folder = chat_dir(repo) / meta["id"]
    request = ask.load_ask(folder) if folder.is_dir() else None
    if not request or request["ask_id"] != ask_id:
        raise ChatError("that request is no longer waiting; refresh")
    ask.write_reply(folder, decision, expected=request)
    append(repo, meta["id"], "system", ("allowed: " if decision == "allow" else "denied: ") + _clean(request.get("preview", ""), 200))
    if decision == "allow":
        import applies
        applies.record_allowed(repo, meta.get("parent", ""), request.get("preview", "") or request.get("tool_name", ""), "you")
    return {"session": meta["id"], "answered": decision}


# -- codex through `codex mcp-server` ------------------------------------------------------------------------
# Codex documents this mode as an MCP server: tools/call "codex" starts a session and "codex-reply" continues one,
# progress arrives as "codex/event" notifications, and an exec or patch approval is an "elicitation/create" request
# that waits for {"decision": "approved" | "denied"}. No real codex is available here, so this follows the
# documented shapes, is tested with a stand-in, and falls back to `codex exec` when the mode does not start.
class _Lines:
    """A process's stdout lines on a thread, so the start can time out without blocking the runner."""

    def __init__(self, stream):
        import queue
        import threading
        self.queue = queue.Queue()
        threading.Thread(target=self._pump, args=(stream,), daemon=True).start()

    def _pump(self, stream):
        for line in stream:
            self.queue.put(line)
        self.queue.put(None)

    def get(self, timeout=None):
        import queue
        try:
            return self.queue.get(timeout=timeout)
        except queue.Empty:
            return ""


def _codex_event(msg: dict) -> tuple[list[dict], str, bool]:
    """(entries, native session id, done) for one codex/event message."""
    kind = msg.get("type")
    if kind == "session_configured":
        return [], str(msg.get("session_id") or ""), False
    if kind == "agent_message_delta" and isinstance(msg.get("delta"), str):
        return [{"role": "chunk", "text": msg["delta"]}], "", False
    if kind == "agent_message" and str(msg.get("message") or "").strip():
        return [{"role": "assistant", "text": _clean(msg["message"])}], "", False
    if kind == "exec_command_begin":
        command = msg.get("command")
        text = " ".join(map(str, command)) if isinstance(command, list) else str(command or "")
        return [{"role": "tool", "text": _clean("ran " + text, 200)}], "", False
    if kind == "patch_apply_begin":
        changes = msg.get("changes") if isinstance(msg.get("changes"), dict) else {}
        return [{"role": "tool", "text": _clean("edited " + ", ".join(sorted(changes)) if changes else "edited files", 200)}], "", False
    if kind in ("error", "stream_error"):
        return [{"role": "error", "text": _clean(msg.get("message") or "the parent reported an error")}], "", False
    if kind == "task_complete":
        return [], "", True
    return [], "", False


def _codex_approval_preview(params: dict) -> tuple[str, dict, str]:
    kind = str(params.get("codex_elicitation") or "")
    if kind == "patch-approval":
        changes = params.get("codex_changes") if isinstance(params.get("codex_changes"), dict) else {}
        return "Edit", {"path": ", ".join(sorted(changes))}, "edit " + (", ".join(sorted(changes)) or "files")
    command = params.get("codex_command")
    text = " ".join(map(str, command)) if isinstance(command, list) else str(command or params.get("message") or "")
    return "Exec", {"command": text}, text


def run_codex_server(repo: Path, session: str, message: str, meta: dict, folder: Path) -> tuple[bool, bool, int]:
    """One turn through `codex mcp-server`. Returns (started, error, entries written); not started means use exec."""
    import ask
    binary = shutil.which("codex")
    try:
        proc = subprocess.Popen([binary, "mcp-server"], cwd=str(repo), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, text=True, errors="replace",
                                env={**os.environ, "RIG_PARENT": "codex", "RIG_OWNER_SESSION": session})
    except OSError:
        return False, False, 0
    _CHILD.append(proc)
    lines = _Lines(proc.stdout)

    def send(obj):
        proc.stdin.write(json.dumps(obj) + "\n")
        proc.stdin.flush()

    def stop():
        try:
            proc.stdin.close()
        except OSError:
            pass
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()

    try:
        send({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18",
              "capabilities": {"elicitation": {}}, "clientInfo": {"name": "rig-chat", "version": "1"}}})
        started = False
        deadline = time.monotonic() + CODEX_START_SECONDS
        while time.monotonic() < deadline:
            line = lines.get(timeout=max(0.1, deadline - time.monotonic()))
            if line is None:
                break
            try:
                reply = json.loads(line) if line else {}
            except ValueError:
                continue
            if reply.get("id") == 1:
                started = "result" in reply
                break
        if not started:
            stop()
            return False, False, 0
        send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        native = meta.get("native", "")
        if native:
            call = {"name": "codex-reply", "arguments": {"conversationId": native, "threadId": native,
                                                         "prompt": message}}
        else:
            call = {"name": "codex", "arguments": {"prompt": message, "cwd": str(repo),
                                                   **({"sandbox": "read-only"} if meta.get("plan") else {}),
                                                   **({"model": meta["model"]} if meta.get("model") else {}),
                                                   **({"config": {"model_reasoning_effort": meta["effort"]}} if meta.get("effort") else {})}}
        send({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": call})
        written, error = 0, False
        streamed = ""
        while True:
            line = lines.get()
            if line is None:
                if _was_stopped(repo, session):
                    break
                error = True
                append(repo, session, "error", "codex mcp-server stopped before the turn finished")
                written += 1
                break
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if not isinstance(msg, dict):
                continue
            if msg.get("method") == "codex/event":
                event = (msg.get("params") or {}).get("msg") if isinstance(msg.get("params"), dict) else None
                entries, sid, _ = _codex_event(event if isinstance(event, dict) else {})
                if sid:
                    meta["native"] = sid
                for entry in entries:
                    if entry["role"] == "chunk":
                        streamed += entry["text"]
                        write_partial(repo, session, streamed)
                        continue
                    if entry["role"] == "assistant":
                        streamed = ""
                        write_partial(repo, session, "")
                    if written < MAX_ENTRIES:
                        append(repo, session, entry["role"], entry["text"])
                        written += 1
                    error = error or entry["role"] == "error"
            elif msg.get("method") == "elicitation/create" and "id" in msg:
                params = msg.get("params") if isinstance(msg.get("params"), dict) else {}
                tool, inp, preview_text = _codex_approval_preview(params)
                if tool == "Edit" and meta.get("edits") and not meta.get("plan"):
                    reply = {"behavior": "allow"}  # the edits mode: file changes go through, commands still ask
                else:
                    ask.write_ask(folder, tool, inp, str(params.get("codex_call_id") or ""), preview_text)
                    reply = ask.wait_reply(folder)
                    ask.consume_ask(folder)
                send({"jsonrpc": "2.0", "id": msg["id"],
                      "result": {"decision": "approved" if reply.get("behavior") == "allow" else "denied"}})
            elif "method" in msg and "id" in msg:  # a request this client does not serve: say so, never leave it hanging
                send({"jsonrpc": "2.0", "id": msg["id"], "error": {"code": -32601, "message": "not supported by rig chat"}})
            elif "method" not in msg and msg.get("id") == 2:
                result = msg.get("result") if isinstance(msg.get("result"), dict) else {}
                structured = result.get("structuredContent") if isinstance(result.get("structuredContent"), dict) else {}
                sid = structured.get("threadId") or structured.get("conversationId")
                if isinstance(sid, str) and sid:
                    meta["native"] = sid
                if "error" in msg or result.get("isError"):
                    error = True
                    detail = (msg.get("error") or {}).get("message") if isinstance(msg.get("error"), dict) else ""
                    text = detail or " ".join(str(c.get("text", "")) for c in result.get("content", []) if isinstance(c, dict))
                    append(repo, session, "error", _clean(text or "codex reported an error"))
                    written += 1
                elif not written:
                    text = " ".join(str(c.get("text", "")) for c in result.get("content", []) if isinstance(c, dict)).strip()
                    if text:
                        append(repo, session, "assistant", _clean(text))
                        written += 1
                break
        return True, error, written
    finally:
        write_partial(repo, session, "")
        stop()


# -- what a turn changed -----------------------------------------------------
DIFF_FILES = 8        # files shown per turn; the rest are counted
DIFF_LINES = 60       # lines shown per file
DIFF_SECONDS = 25


def _tree(repo) -> str:
    """A tree naming the working tree as it is now: tracked and new files, not ignored ones, nor .rig. It is made through a
    copy of the index, so the repository's own index and branch are never touched. Empty when this is not a git repository."""
    import tempfile
    repo = str(repo)
    try:
        top = subprocess.run(["git", "-C", repo, "rev-parse", "--git-dir"], capture_output=True, text=True, timeout=10)
        if top.returncode != 0:
            return ""
        git_dir = Path(top.stdout.strip())
        git_dir = git_dir if git_dir.is_absolute() else Path(repo) / git_dir
        fd, index = tempfile.mkstemp(prefix="rig-index-")
        os.close(fd)
        try:
            try:
                shutil.copyfile(git_dir / "index", index)  # keeps git's stat cache, so a big repository is not read again
            except OSError:
                os.unlink(index)  # no index yet: an empty one
            env = {**os.environ, "GIT_INDEX_FILE": index}
            added = subprocess.run(["git", "-C", repo, "add", "-A", "--", "."], capture_output=True, text=True,
                                   timeout=DIFF_SECONDS, env=env)
            if added.returncode != 0:
                return ""
            # Rig's own files change while a turn runs; an exclude pathspec would fail where .rig is ignored, so they are taken out after
            subprocess.run(["git", "-C", repo, "rm", "-r", "--cached", "-q", "--ignore-unmatch", "--", ".rig"], capture_output=True,
                           text=True, timeout=DIFF_SECONDS, env=env)
            out = subprocess.run(["git", "-C", repo, "write-tree"], capture_output=True, text=True, timeout=10, env=env)
            return out.stdout.strip() if out.returncode == 0 else ""
        finally:
            if os.path.exists(index):
                os.unlink(index)
    except (OSError, subprocess.SubprocessError):
        return ""


def turn_changes(repo, before: str, after: str) -> list[dict]:
    """The files that differ between two trees: {path, added, deleted, text} where text is the patch's hunks, cut short."""
    if not before or not after or before == after:
        return []
    try:
        stat = subprocess.run(["git", "-C", str(repo), "diff", "--numstat", "--no-color", "--no-ext-diff", before, after],
                              capture_output=True, text=True, timeout=DIFF_SECONDS)
        patch = subprocess.run(["git", "-C", str(repo), "diff", "--no-color", "--no-ext-diff", "-U2", before, after],
                               capture_output=True, text=True, timeout=DIFF_SECONDS, errors="replace")
    except (OSError, subprocess.SubprocessError):
        return []
    counts = {}
    for row in stat.stdout.splitlines():
        a, _, rest = row.partition("\t")
        d, _, path = rest.partition("\t")
        counts[path] = (int(a) if a.isdigit() else 0, int(d) if d.isdigit() else 0)
    files = []
    for chunk in re.split(r"(?m)^diff --git ", patch.stdout)[1:]:
        head, _, body = chunk.partition("\n")
        path = head.rsplit(" b/", 1)[-1].strip()
        lines = [l for l in body.splitlines() if not l.startswith(("index ", "--- ", "+++ ", "new file", "deleted file", "similarity", "rename "))]
        if any(l.startswith("Binary files") for l in lines):
            lines = ["binary file"]
        if len(lines) > DIFF_LINES:
            lines = lines[:DIFF_LINES] + [f"… {len(lines) - DIFF_LINES} more lines"]
        added, deleted = counts.get(path, (0, 0))
        files.append({"path": path, "added": added, "deleted": deleted, "text": "\n".join(lines)})
    return files


MARKS = 40  # turns kept to rewind to


def _record_changes(repo, session: str, before: str) -> None:
    """After a turn: one entry for each file it changed, with what changed, for any parent."""
    after = _tree(repo)
    files = turn_changes(repo, before, after)
    if files:
        try:  # the two trees stay in git's object store, so /rewind can put the files back as they were
            meta = read_meta(repo, session)
            marks = [m for m in (meta.get("marks") or []) if isinstance(m, dict)]
            marks.append({"turn": meta.get("turns", 0), "before": before, "after": after, "title": _last_user(repo, session),
                          "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                          "files": [{"path": f["path"], "added": f["added"], "deleted": f["deleted"]} for f in files[:200]]})
            meta["marks"] = marks[-MARKS:]
            _write_json(_meta_path(repo, session), meta)
        except ChatError:
            pass
    for entry in files[:DIFF_FILES]:
        append(repo, session, "diff", _clean(f"{entry['path']} +{entry['added']} −{entry['deleted']}\n{entry['text']}", 6000))
    if len(files) > DIFF_FILES:
        rest = files[DIFF_FILES:]
        append(repo, session, "system", f"and {len(rest)} more files changed: " + ", ".join(e["path"] for e in rest[:6]) + (" …" if len(rest) > 6 else ""))


MENTION = re.compile(r"(?<![\w@])@([\w.][\w./-]*)(?::(\d+)(?:-(\d+))?)?")
MENTION_FILES = 8
MENTION_BYTES = 100_000


def _mentions(repo, parent: str, message: str) -> str:
    """The message with the files it names with @ added after it, for every parent: @path is the whole file, @path:10-40 only
    those lines. claude reads a plain @path itself, so for claude only a range is added. A name that is not a file of this
    project (an e-mail address, a person) is left alone."""
    root, seen, blocks = Path(repo).resolve(), set(), []
    for found in MENTION.finditer(message):
        name, first, last = found.group(1).rstrip(".,;:)"), found.group(2), found.group(3)
        if name in seen or len(blocks) >= MENTION_FILES or (parent == "claude" and not first):
            continue
        target = (root / name).resolve()
        if root not in target.parents or name.split("/")[0] in (".git", ".rig") or not target.is_file():
            continue
        try:
            data = target.read_bytes()[:MENTION_BYTES + 1]
        except OSError:
            continue
        if b"\0" in data:
            continue
        seen.add(name)
        text = data[:MENTION_BYTES].decode(errors="replace")
        label = name
        if first:
            lines = text.splitlines()
            a = max(1, int(first))
            b = max(a, int(last or first))
            text, label = "\n".join(lines[a - 1:b]), f"{name}, lines {a}-{min(b, len(lines))}"
        elif len(data) > MENTION_BYTES:
            label += " (the first 100 KB)"
        blocks.append(f"[File {label}]\n```\n{text}\n```")
    return message + ("\n\n" + "\n\n".join(blocks) if blocks else "")


NOTES_FILE = ".rig/notes.md"
NOTES_CHARS = 4000
REMEMBER_COMMAND = re.compile(r"/remember(?:\s+(.*))?", re.S)
NOTES_COMMAND = re.compile(r"/notes\s*")


def notes_path(repo) -> Path:
    return Path(repo).resolve() / NOTES_FILE


def notes(repo) -> str:
    """What the user keeps in .rig/notes.md for every conversation: project facts, conventions, things not to touch."""
    try:
        text = notes_path(repo).read_text(errors="replace").strip()
    except OSError:
        return ""
    return text[:NOTES_CHARS] + ("\n…" if len(text) > NOTES_CHARS else "")


def remember(repo, text: str) -> dict:
    """Add a line to the notes; they go with the first message of every conversation from now on."""
    text = " ".join(str(text or "").split())
    if not text:
        raise ChatError("/remember NOTE keeps NOTE for every conversation; /notes opens them")
    path = notes_path(repo)
    path.parent.mkdir(parents=True, exist_ok=True)
    had = path.read_text(errors="replace") if path.is_file() else ""
    with path.open("a") as stream:
        stream.write(("" if not had or had.endswith("\n") else "\n") + "- " + text + "\n")
    return {"path": NOTES_FILE, "lines": sum(1 for l in path.read_text(errors="replace").splitlines() if l.strip())}


def _with_notes(repo, meta: dict, message: str) -> str:
    """The notes go with a parent's first message: once per native session for the parents that resume their own, with
    every message for the others (which get the conversation with every message too)."""
    text = notes(repo)
    if not text or (meta["parent"] in RESUMES and meta.get("native")):
        return message
    return "[Project notes the user keeps in " + NOTES_FILE + "]\n" + text + "\n\n" + message


def _last_user(repo, session: str) -> str:
    """What the last message said, on one line, to name a turn by."""
    try:
        lines = (chat_dir(repo) / f"{session}.jsonl").read_text(errors="replace").splitlines()
    except OSError:
        return ""
    for line in reversed(lines):
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and row.get("role") == "user":
            return _clean(str(row.get("text", "")).split("\n", 1)[0])[:80]
    return ""


def _git(repo, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, timeout=DIFF_SECONDS)


def _blob(repo, tree: str, path: str) -> str:
    """The id of a file in a tree; empty when the tree has no such file."""
    out = _git(repo, "rev-parse", "--verify", "-q", f"{tree}:{path}")
    return out.stdout.decode().strip() if out.returncode == 0 else ""


def _restore(repo, tree: str, paths: list[str]) -> None:
    """Put these files back as they are in the tree: the ones it has are written, the ones it lacks are deleted."""
    root = Path(repo).resolve()
    for path in paths:
        target = (root / path).resolve()
        if root not in target.parents or path.split("/")[0] in (".git", ".rig"):
            raise ChatError(f"{path} is outside the project")
        listing = _git(repo, "ls-tree", tree, "--", path).stdout.decode().split("\t", 1)[0].split()
        if not listing:
            target.unlink(missing_ok=True)
            continue
        mode = listing[0]
        data = _git(repo, "cat-file", "blob", f"{tree}:{path}").stdout
        target.parent.mkdir(parents=True, exist_ok=True)
        if mode == "120000":
            target.unlink(missing_ok=True)
            os.symlink(data.decode(errors="replace"), target)
            continue
        if target.is_symlink():
            target.unlink()
        target.write_bytes(data)
        target.chmod(0o755 if mode == "100755" else 0o644)


def rewind(repo, session: str, turn: int = 0, path: str = "", dry: bool = False, undo: bool = False) -> dict:
    """Put the working tree back as it was before a turn: every file that turn and the later ones changed, or one file.
    What was there is kept first, so undo=True puts it back. dry=True only says what would change."""
    repo = Path(repo).resolve()
    require_enabled(repo)
    meta = read_meta(repo, session)
    if meta["state"] == "running":
        raise ChatError(f"session {meta['id']} is still answering; wait for it or cancel it")
    now = _tree(repo)
    if not now:
        raise ChatError("rewind needs a git repository")
    backup = meta.get("rewind_backup") if isinstance(meta.get("rewind_backup"), dict) else None
    if undo:
        if not backup:
            raise ChatError("nothing to undo: no rewind has been made in this conversation")
        target, paths, last_after, turn = backup["tree"], list(backup["paths"]), "", int(backup.get("turn", 0))
    else:
        marks = [m for m in (meta.get("marks") or []) if isinstance(m, dict) and int(m.get("turn", 0)) >= int(turn)]
        if not marks:
            raise ChatError(f"no turn {turn} that changed files")
        first, last = marks[0], marks[-1]
        target, last_after = first["before"], last["after"]
        turn = int(first["turn"])
        out = _git(repo, "diff", "--name-only", "-z", "--no-ext-diff", target, last_after)
        paths = [p for p in out.stdout.decode(errors="replace").split("\0") if p]
        if path:
            if path not in paths:
                raise ChatError(f"{path} was not changed by turn {turn} or after")
            paths = [path]
    # files that differ now from how the last of those turns left them were edited since: they are named, not hidden
    edited = [p for p in paths if last_after and _blob(repo, now, p) != _blob(repo, last_after, p)]
    result = {"session": meta["id"], "turn": turn, "paths": paths, "edited": edited, "dry": bool(dry)}
    if dry:
        return result
    _restore(repo, target, paths)
    if undo:
        meta["marks"] = (backup.get("marks") or []) + [m for m in (meta.get("marks") or []) if m not in (backup.get("marks") or [])]
        meta.pop("rewind_backup", None)
        what = f"undid the rewind: {len(paths)} file{'s' if len(paths) != 1 else ''} put back as before it"
        note = "[Note from Rig: the user undid a rewind, so " + ", ".join(paths[:8]) + " are as they were before it.]"
    else:
        dropped = [m for m in (meta.get("marks") or []) if int(m.get("turn", 0)) >= turn] if not path else []
        meta["rewind_backup"] = {"tree": now, "paths": paths, "turn": turn, "marks": dropped}
        if not path:
            meta["marks"] = [m for m in (meta.get("marks") or []) if int(m.get("turn", 0)) < turn]
        what = (f"rewound {path} to before turn {turn}" if path else
                f"rewound to before turn {turn}: {len(paths)} file{'s' if len(paths) != 1 else ''} put back")
        note = ("[Note from Rig: the user rewound the working tree: " + ", ".join(paths[:8]) + (" …" if len(paths) > 8 else "") +
                f" are as they were before turn {turn}. Your later edits to them are gone; do not assume they are still there.]")
    meta["notes"] = list(meta.get("notes") or []) + [note]
    append(repo, meta["id"], "system", what + ("; /rewind undo puts it back" if not undo else ""))
    _write_json(_meta_path(repo, meta["id"]), meta)
    return result


_CHILD: list = []  # the parent CLI the runner is waiting on, for the SIGTERM handler


def _stop_child(*_) -> None:
    """A cancel sends SIGTERM to the whole group; the runner itself only stops its child and goes on to note what the
    turn changed and leave the state file right."""
    for proc in _CHILD:
        try:
            proc.terminate()
        except OSError:
            pass


def _was_stopped(repo, session: str) -> str:
    """The reason a cancel wrote while the turn ran, if one did."""
    try:
        note = json.loads(_meta_path(repo, session).read_text()).get("note")
    except (OSError, ValueError):
        return ""
    return note if note in ("cancelled", "interrupted") else ""


def run_turn(repo, session: str) -> int:
    """The detached runner: spawn the parent in its headless mode and write what it says to the transcript."""
    repo = Path(repo).resolve()
    signal.signal(signal.SIGTERM, _stop_child)
    message = sys.stdin.buffer.read().decode(errors="replace")
    meta = read_meta(repo, session)
    folder = turn_dir(repo, session)
    parent = meta["parent"]
    import parent_commands
    message = parent_commands.expand(repo, parent, message)  # /name: the parent's own skill or command
    message = _mentions(repo, parent, message)  # @file and @file:10-40: what they name goes with the message
    message = _with_notes(repo, meta, message)  # .rig/notes.md, with the parent's first message
    if parent not in RESUMES:
        message = _with_context(repo, session, message)
    elif meta.get("context_from") is not None:
        message = _handoff(repo, session, meta, message)
    if meta.get("context_from") is not None:
        meta["context_from"] = None  # told once; from here its own session carries the conversation
        _write_json(_meta_path(repo, session), meta)
    if meta.get("summary"):
        message = "[Summary of our earlier conversation, which was compacted]\n" + meta["summary"] + "\n\n[Now]\n" + message
        meta["summary"] = ""  # told once; from here its own session carries the conversation
        _write_json(_meta_path(repo, session), meta)
    if meta.get("notes"):
        message = "\n".join(meta["notes"]) + "\n\n" + message
        meta["notes"] = []  # told once
        _write_json(_meta_path(repo, session), meta)
    if meta.get("plan") and parent != "claude":
        message = PLAN_PREFIX + message  # claude has a flag for plan mode; the others are told in the message
    (folder / "message.txt").write_text(message)
    import ui_settings
    plan = bool(meta.get("plan"))
    turn = {"repo": str(repo), "message_file": str(folder / "message.txt"), "new_id": str(uuid.uuid4()), "plan": plan,
            "edits": bool(meta.get("edits")),
            "model": ui_settings.chosen_model(repo, parent)[0], "effort": ui_settings.chosen_effort(repo, parent)[0]}
    meta["model"], meta["effort"] = turn["model"], turn["effort"]
    if parent in ASKS:
        _write_json(folder / "meta.json", {"status": "running"})
        turn["ask_mcp"] = _ask_server(folder)
    native = meta.get("native", "")
    if parent == "grok" and not native:
        meta["native"] = turn["new_id"]  # grok takes the id we give it, and later turns resume it
    before = _tree(repo)  # what the working tree is before the parent starts, to show what its turn changed
    if parent == "codex" and shutil.which("codex"):
        started, error, _ = run_codex_server(repo, session, message, meta, folder)
        if started:
            _record_changes(repo, session, before)
            _write_json(folder / "meta.json", {"status": "idle"})
            if _was_stopped(repo, session):
                fresh = read_meta(repo, session)
                fresh.update(native=meta.get("native", ""), state="idle", runner_pid=0, model=meta.get("model", ""))
                _write_json(_meta_path(repo, session), fresh)
                return 0
            fresh = read_meta(repo, session) if _meta_path(repo, session).exists() else meta
            fresh.update(native=meta.get("native", ""), state="failed" if error else "idle", runner_pid=0,
                         note="see the transcript" if error else "", asks=True, model=meta.get("model", ""))
            if fresh.get("note") == "cancelled":
                fresh["state"] = "idle"
            if fresh.get("compacting"):
                _finish_compaction(repo, fresh, error)
            _write_json(_meta_path(repo, session), fresh)
            return 1 if error else 0
        # this codex has no MCP server mode, or it did not start: the plain headless mode, which cannot ask
    argv = HEADLESS[parent](message, native, turn)
    binary = shutil.which(argv[0])
    if not binary:
        append(repo, session, "error", f"{argv[0]} is not on PATH")
        meta.update(state="failed", note="parent not found")
        _write_json(_meta_path(repo, session), meta)
        return 1
    proc = subprocess.Popen([binary, *argv[1:]], cwd=str(repo), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, errors="replace", env={**os.environ, "RIG_PARENT": meta["parent"], "RIG_OWNER_SESSION": session})
    _CHILD.append(proc)
    done = error = False
    count = 0
    said = False
    used = None
    chunks: list[str] = []
    raw: list[str] = []

    def write(role, text):
        nonlocal count, said
        if count < MAX_ENTRIES:
            append(repo, session, role, text)
            count += 1
        said = said or role == "assistant"

    def flush():
        if "".join(chunks).strip():
            write("assistant", _clean("".join(chunks).strip()))
        chunks.clear()
        write_partial(repo, session, "")

    for line in proc.stdout:
        raw.append(line)
        try:
            decoded = decode_event(parent, json.loads(line))
        except ValueError:
            continue
        if decoded["native"]:
            meta["native"] = decoded["native"]
        for entry in decoded["entries"]:
            if entry["role"] == "chunk":
                chunks.append(entry["text"])
                write_partial(repo, session, "".join(chunks))
                continue
            if entry["role"] == "assistant" and entry["text"].strip().startswith("".join(chunks).strip()):
                chunks.clear()  # the whole message, after its streamed pieces: keep it once
            flush()
            if entry["role"] == "final":
                if not said:  # a closing summary repeats the answer when the text was already streamed
                    write("assistant", entry["text"])
                continue
            write(entry["role"], entry["text"])
        done, error = done or decoded["done"], error or decoded["error"]
        if decoded.get("usage"):
            used = decoded["usage"]
    flush()
    if not count and raw:
        import jobs  # a stream this decoder did not recognise: read it the way Rig reads that CLI's job log
        lines = jobs.decode_log_text("".join(raw), worker=parent)
        if lines:
            write("assistant", _clean("\n".join(lines)))
    stderr = proc.stderr.read()
    code = proc.wait()
    _record_changes(repo, session, before)
    if parent in ASKS:
        _write_json(folder / "meta.json", {"status": "idle"})  # a late answer to this turn's request is refused
    stopped = _was_stopped(repo, session)
    if stopped:
        fresh = read_meta(repo, session)
        fresh.update(native=meta.get("native", ""), state="idle", runner_pid=0, note=stopped, model=meta.get("model", ""))
        _write_json(_meta_path(repo, session), fresh)
        return 0
    if code != 0 and not error:
        tail = [row for row in stderr.strip().splitlines() if row.strip()][-3:]
        append(repo, session, "error", _clean(f"the parent exited with status {code}" + (": " + " | ".join(tail) if tail else "")))
        error = True
    fresh = read_meta(repo, session) if _meta_path(repo, session).exists() else meta
    fresh.update(native=meta.get("native", ""), state="failed" if error else "idle", runner_pid=0, note="" if not error else "see the transcript",
                 asks=parent == "claude", model=meta.get("model", ""))  # codex reaches this plain headless mode only when its server mode did not start
    if fresh.get("note") == "cancelled":
        fresh["state"] = "idle"
    if used:  # what the turn cost and how full the parent's context is, for /cost and /context
        total = fresh.get("usage") if isinstance(fresh.get("usage"), dict) else {}
        fresh["usage"] = {"cost": round(float(total.get("cost", 0)) + used["cost"], 6), "input": int(total.get("input", 0)) + used["input"],
                          "output": int(total.get("output", 0)) + used["output"], "context": used["context"], "turns": int(total.get("turns", 0)) + 1}
    if fresh.get("compacting"):
        _finish_compaction(repo, fresh, error)
    elif used and context_percent(fresh) >= CONTEXT_WARN and not fresh.get("warned"):
        fresh["warned"] = True
        append(repo, session, "system", f"context is {context_percent(fresh)}% full: /compact continues in a fresh conversation from a summary")
    _write_json(_meta_path(repo, session), fresh)
    return 0 if not error else 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="rig chat", description="EXPERIMENT: a headless parent with a Rig-rendered chat")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("send", "show", "sessions", "cancel", "status", "run-turn"):
        command = sub.add_parser(name)
        command.add_argument("--repo", default="")
        if name in ("send", "show", "cancel", "run-turn"):
            command.add_argument("--session", default="")
        if name == "send":
            command.add_argument("message")
            command.add_argument("--parent", default="")
            command.add_argument("--wait", action="store_true", help="print the answer as it arrives")
        if name == "show":
            command.add_argument("--tail", type=int, default=50)
    args = parser.parse_args(argv)
    try:
        import jobs
        repo = jobs.repo_root(args.repo or None)
        if args.command == "run-turn":
            return run_turn(repo, args.session)
        if args.command == "status":
            print(json.dumps(status(repo), indent=1))
            return 0
        require_enabled(repo)
        if args.command == "sessions":
            for row in sessions(repo):
                print(f"{row['id']}  {row['parent']}  {row['state']}  {row.get('turns', 0)} turns")
            return 0
        if args.command == "cancel":
            print(json.dumps(cancel(repo, args.session)))
            return 0
        if args.command == "show":
            for row in transcript(repo, args.session or sessions(repo)[0]["id"], args.tail)["entries"]:
                print(f"{row['role']:>9} › {row['text']}")
            return 0
        started = send(repo, args.message, args.session, args.parent)
        print(json.dumps(started))
        if args.wait:
            seen = 0
            while True:
                view = transcript(repo, started["session"], MAX_ENTRIES)
                for row in view["entries"][seen:]:
                    if row["role"] != "user":
                        print(f"{row['role']:>9} › {row['text']}", flush=True)
                seen = len(view["entries"])
                if view["state"] != "running":
                    return 0 if view["state"] == "idle" else 1
                time.sleep(0.3)
        return 0
    except (ChatError, ValueError, SystemExit) as error:
        print(f"rig chat: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
