#!/usr/bin/env python3
"""EXPERIMENT: a headless parent CLI with a Rig-rendered chat (design: docs/journals/261020-phase-4-design.md).

Off unless RIG_EXPERIMENT_CHAT=1 or ``[experiments] chat = true``. Each message runs the parent CLI once in its
headless mode (``claude -p --output-format stream-json`` or ``codex exec --json``), a detached runner decodes the
event stream into a transcript under ``.rig/chat/<session>.jsonl``, and the board shows that transcript. Later
messages resume the parent's own session. Rig adds no permission flags: the parent runs with the user's own
configuration, and in these modes a tool the user has not allowed is refused rather than asked about.

The event formats are the ones run-worker.sh already relies on for these CLIs. They have not been checked against a
real parent CLI here (none is available), so the decoder is tested against recorded-shape fixtures and a stand-in.
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


def argv_claude(message: str, native: str) -> list[str]:
    return ["claude", "-p", "--output-format", "stream-json", "--verbose", *(["--resume", native] if native else []), "--", message]


def argv_codex(message: str, native: str) -> list[str]:
    return ["codex", "exec", "--json", *(["resume", native] if native else []), message]


HEADLESS = {"claude": argv_claude, "codex": argv_codex}


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


def decode_event(parent: str, event) -> dict:
    """{'entries': [{role, text}], 'native': session id or '', 'done': bool, 'error': bool} for one stream event."""
    out = {"entries": [], "native": "", "done": False, "error": False}
    if not isinstance(event, dict):
        return out
    if parent == "claude":
        kind = event.get("type")
        if kind == "system" and event.get("subtype") == "init":
            out["native"] = str(event.get("session_id") or "")
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
            "note": meta.get("note", ""), "entries": rows}


def status(repo) -> dict:
    on = enabled(repo)
    available = [name for name in HEADLESS if shutil.which(name)]
    result = {"enabled": on, "parents": sorted(HEADLESS), "available": available, "sessions": []}
    if on:
        result["sessions"] = [{"id": s["id"], "parent": s["parent"], "state": s["state"], "turns": s.get("turns", 0)} for s in sessions(repo)[:20]]
    return result


def choose_parent(repo, parent: str = "") -> str:
    if not parent:
        import harness
        parent = harness.parse_harness(harness.harness_path(Path(repo)))["parent"]
    if parent not in HEADLESS:
        raise ChatError(f"the chat experiment supports {', '.join(sorted(HEADLESS))} as the parent, not {parent or 'none'}; pass --parent")
    if not shutil.which(parent):
        raise ChatError(f"{parent} is not on PATH")
    return parent


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


def run_turn(repo, session: str) -> int:
    """The detached runner: spawn the parent in its headless mode and write what it says to the transcript."""
    repo = Path(repo).resolve()
    message = sys.stdin.buffer.read().decode(errors="replace")
    meta = read_meta(repo, session)
    argv = HEADLESS[meta["parent"]](message, meta.get("native", ""))
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
    for line in proc.stdout:
        try:
            decoded = decode_event(meta["parent"], json.loads(line))
        except ValueError:
            continue
        if decoded["native"]:
            meta["native"] = decoded["native"]
        for entry in decoded["entries"]:
            if count < MAX_ENTRIES:
                append(repo, session, entry["role"], entry["text"])
                count += 1
        done, error = done or decoded["done"], error or decoded["error"]
    stderr = proc.stderr.read()
    code = proc.wait()
    if code != 0 and not error:
        tail = [row for row in stderr.strip().splitlines() if row.strip()][-3:]
        append(repo, session, "error", _clean(f"the parent exited with status {code}" + (": " + " | ".join(tail) if tail else "")))
        error = True
    fresh = read_meta(repo, session) if _meta_path(repo, session).exists() else meta
    fresh.update(native=meta.get("native", ""), state="failed" if error else "idle", runner_pid=0, note="" if not error else "see the transcript")
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
