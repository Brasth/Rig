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
def argv_claude(message: str, native: str, turn: dict) -> list[str]:
    asks = ["--mcp-config", turn["ask_mcp"], "--permission-prompt-tool", "mcp__rig-ask__permission_prompt"] if turn.get("ask_mcp") else []
    return ["claude", "-p", "--output-format", "stream-json", "--verbose", "--include-partial-messages", *asks, *(["--resume", native] if native else []), "--", message]


def argv_codex(message: str, native: str, turn: dict) -> list[str]:
    return ["codex", "exec", "--json", *(["resume", native] if native else []), message]


def argv_grok(message: str, native: str, turn: dict) -> list[str]:
    session = ["-r", native] if native else ["--session-id", turn["new_id"]]
    return ["grok", "--no-auto-update", *session, "--prompt-file", turn["message_file"], "--cwd", turn["repo"],
            "--output-format", "streaming-json"]


def argv_opencode(message: str, native: str, turn: dict) -> list[str]:
    return ["opencode", "run", "--format", "json", "--dir", turn["repo"], *(["--session", native] if native else []), message]


def argv_omp(message: str, native: str, turn: dict) -> list[str]:
    return ["omp", "-p", "--mode", "json", "--cwd", turn["repo"], message]


def argv_pi(message: str, native: str, turn: dict) -> list[str]:
    return ["pi", "-p", "--mode", "json", message]


def argv_agy(message: str, native: str, turn: dict) -> list[str]:
    return ["agy", "-p", message, "--output-format", "json"]


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
def _tool_line(name, data) -> str:
    detail = ""
    if isinstance(data, dict):
        for value in data.values():
            if isinstance(value, str) and value.strip():
                detail = value.strip().splitlines()[0]
                break
    return _clean(f"{name} {detail}".strip(), 200)


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
    out = {"entries": [], "native": "", "done": False, "error": False}
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
        elif kind == "result":
            out["native"] = str(event.get("session_id") or "")
            out["done"], out["error"] = True, bool(event.get("is_error"))
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
        elif kind == "turn.completed":
            out["done"] = True
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


def new_session(repo, parent: str) -> dict:
    folder = chat_dir(repo)
    folder.mkdir(parents=True, exist_ok=True)
    os.chmod(folder, 0o700)
    meta = {"id": "c-" + secrets.token_hex(4), "parent": parent, "native": "", "state": "idle", "turns": 0, "runner_pid": 0,
            "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    _write_json(_meta_path(repo, meta["id"]), meta)
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
    return {"session": meta["id"], "parent": meta["parent"], "state": meta["state"], "turns": meta.get("turns", 0),
            "note": meta.get("note", ""), "entries": rows, "ask": pending_ask(repo, meta["id"]),
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
    result = {"enabled": on, "parents": sorted(HEADLESS), "available": available, "sessions": [], "parent": default_parent(repo),
              "configured": configured}
    if on:
        result["sessions"] = [{"id": s["id"], "parent": s["parent"], "state": s["state"], "turns": s.get("turns", 0)} for s in sessions(repo)[:20]]
    return result


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
    choose_parent(repo, meta["parent"])
    append(repo, meta["id"], "user", _clean(message))
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


def cancel(repo, session: str) -> dict:
    require_enabled(repo)
    meta = read_meta(repo, session)
    if meta["state"] != "running":
        return {"session": meta["id"], "state": meta["state"], "cancelled": False}
    pid = meta.get("runner_pid")
    try:
        os.killpg(pid, signal.SIGTERM)  # the runner leads its own group, with the parent CLI inside it
    except (ProcessLookupError, PermissionError, TypeError):
        pass
    meta.update(state="idle", note="cancelled")
    _write_json(_meta_path(repo, meta["id"]), meta)
    append(repo, meta["id"], "system", "cancelled")
    return {"session": meta["id"], "state": "idle", "cancelled": True}


def turn_dir(repo, session) -> Path:
    """Per-session working files: the message for CLIs that read a prompt file, and the claude permission exchange."""
    folder = chat_dir(repo) / _session_id(session)
    folder.mkdir(mode=0o700, exist_ok=True)
    return folder


def _with_context(repo, session, message: str) -> str:
    """A parent that cannot resume its own session gets the recent conversation with each message, so it can follow."""
    rows = [row for row in transcript(repo, session, tail=40)["entries"] if row["role"] in ("user", "assistant")][:-1][-8:]
    if not rows:
        return message
    lines = [("user: " if row["role"] == "user" else "you: ") + row["text"][:1500] for row in rows]
    return "Earlier in this conversation:\n" + "\n".join(lines) + "\n\nNow: " + message


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
            call = {"name": "codex-reply", "arguments": {"conversationId": native, "threadId": native, "prompt": message}}
        else:
            call = {"name": "codex", "arguments": {"prompt": message, "cwd": str(repo)}}
        send({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": call})
        written, error = 0, False
        streamed = ""
        while True:
            line = lines.get()
            if line is None:
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


def run_turn(repo, session: str) -> int:
    """The detached runner: spawn the parent in its headless mode and write what it says to the transcript."""
    repo = Path(repo).resolve()
    message = sys.stdin.buffer.read().decode(errors="replace")
    meta = read_meta(repo, session)
    folder = turn_dir(repo, session)
    parent = meta["parent"]
    if parent not in RESUMES:
        message = _with_context(repo, session, message)
    (folder / "message.txt").write_text(message)
    turn = {"repo": str(repo), "message_file": str(folder / "message.txt"), "new_id": str(uuid.uuid4())}
    if parent in ASKS:
        _write_json(folder / "meta.json", {"status": "running"})
        turn["ask_mcp"] = _ask_server(folder)
    native = meta.get("native", "")
    if parent == "grok" and not native:
        meta["native"] = turn["new_id"]  # grok takes the id we give it, and later turns resume it
    if parent == "codex" and shutil.which("codex"):
        started, error, _ = run_codex_server(repo, session, message, meta, folder)
        if started:
            _write_json(folder / "meta.json", {"status": "idle"})
            fresh = read_meta(repo, session) if _meta_path(repo, session).exists() else meta
            fresh.update(native=meta.get("native", ""), state="failed" if error else "idle", runner_pid=0,
                         note="see the transcript" if error else "", asks=True)
            if fresh.get("note") == "cancelled":
                fresh["state"] = "idle"
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
    done = error = False
    count = 0
    said = False
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
    flush()
    if not count and raw:
        import jobs  # a stream this decoder did not recognise: read it the way Rig reads that CLI's job log
        lines = jobs.decode_log_text("".join(raw), worker=parent)
        if lines:
            write("assistant", _clean("\n".join(lines)))
    stderr = proc.stderr.read()
    code = proc.wait()
    if parent in ASKS:
        _write_json(folder / "meta.json", {"status": "idle"})  # a late answer to this turn's request is refused
    if code != 0 and not error:
        tail = [row for row in stderr.strip().splitlines() if row.strip()][-3:]
        append(repo, session, "error", _clean(f"the parent exited with status {code}" + (": " + " | ".join(tail) if tail else "")))
        error = True
    fresh = read_meta(repo, session) if _meta_path(repo, session).exists() else meta
    fresh.update(native=meta.get("native", ""), state="failed" if error else "idle", runner_pid=0, note="" if not error else "see the transcript",
                 asks=parent == "claude")  # codex reaches this plain headless mode only when its server mode did not start
    if fresh.get("note") == "cancelled":
        fresh["state"] = "idle"
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
