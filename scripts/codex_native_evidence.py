#!/usr/bin/env python3
"""Read-only Codex host evidence for a native child generation.

Public callers pass recorded parent/child identity only. They cannot supply an
evidence file path. Reports include identity and a digest, never source contents.

A closed spawn edge is not termination: Codex persists Closed before awaiting
shutdown, and the close_agent collab event is emitted even when the tool fails.
Verified termination requires that closed edge plus a successful parent-rollout
function_call_output whose body is the typed CloseAgentResult previous_status.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

UNAVAILABLE = "terminal_evidence_unavailable"
REGISTRY_NAME = "state_5.sqlite"
REQUIRED_THREAD_COLUMNS = ("id", "rollout_path", "cwd", "created_at", "updated_at", "agent_path")
REQUIRED_EDGE_COLUMNS = ("parent_thread_id", "child_thread_id", "status")
EDGE_STATUSES = frozenset({"open", "closed"})
CLOSE_TOOLS = frozenset({"close_agent"})
RESUME_TOOLS = frozenset({"resume_agent", "followup_task", "spawn_agent", "start_agent", "start"})
HOST_NAMESPACES = frozenset({"multi_agent_v1"})
AGENT_STATUS_ATOMS = frozenset({"pending_init", "running", "interrupted", "shutdown", "not_found"})
AGENT_STATUS_OBJECTS = frozenset({"completed", "errored"})


class EvidenceError(ValueError):
    pass


def configured_codex_home():
    sqlite_home = (os.environ.get("CODEX_SQLITE_HOME") or "").strip()
    if sqlite_home:
        return Path(sqlite_home).expanduser()
    home = (os.environ.get("CODEX_HOME") or "").strip()
    if home:
        return Path(home).expanduser()
    return Path.home() / ".codex"


def registry_path(home=None):
    return Path(home or configured_codex_home()) / REGISTRY_NAME


def _unavailable(reason, **fields):
    report = {
        "status": "unavailable",
        "reason": UNAVAILABLE,
        "detail": reason,
        "identity": {},
        "edge_status": "",
        "digest": "",
        "reference": {"registry": REGISTRY_NAME, "tables": ["threads", "thread_spawn_edges"]},
    }
    report.update(fields)
    return report


def _digest(payload):
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return "sha256:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _unix(value):
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        if number > 10_000_000_000:
            number /= 1000.0
        return number
    text = str(value).strip()
    if not text:
        return None
    try:
        stamp = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        return stamp.timestamp()
    except ValueError:
        return None


def _columns(conn, table):
    try:
        return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    except sqlite3.Error:
        return set()


def _tables(conn):
    try:
        return {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    except sqlite3.Error:
        return set()


def _open_readonly(path):
    uri = Path(path).resolve().as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=1.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


def _connect(path):
    try:
        if not Path(path).is_file():
            return None, _unavailable("codex registry is missing")
        return _open_readonly(path), None
    except (OSError, sqlite3.Error) as error:
        return None, _unavailable(f"codex registry is unreadable: {error}")


def _schema_ok(conn):
    names = _tables(conn)
    if "threads" not in names or "thread_spawn_edges" not in names:
        return False
    threads = _columns(conn, "threads")
    edges = _columns(conn, "thread_spawn_edges")
    return set(REQUIRED_THREAD_COLUMNS) <= threads and set(REQUIRED_EDGE_COLUMNS) <= edges


def _thread_time(row, prefix="child"):
    for key in (f"{prefix}_created_at_ms", f"{prefix}_created_at"):
        stamp = _unix(row.get(key))
        if stamp is not None:
            return stamp
    return None


def _same_cwd(left, right):
    if not left or not right:
        return False
    try:
        return Path(left).expanduser().resolve() == Path(right).expanduser().resolve()
    except (OSError, RuntimeError, ValueError):
        return os.path.normpath(str(left)) == os.path.normpath(str(right))


def _select_bound(conn, parent_thread_id, agent_id, agent_path):
    thread_cols = _columns(conn, "threads")
    created_ms = ", child.created_at_ms AS child_created_at_ms" if "created_at_ms" in thread_cols else ""
    sql = f"""
        SELECT parent.id AS parent_id,
               parent.rollout_path AS parent_rollout_path,
               parent.cwd AS parent_cwd,
               child.id AS child_id,
               child.rollout_path AS child_rollout_path,
               child.cwd AS child_cwd,
               child.created_at AS child_created_at,
               child.updated_at AS child_updated_at,
               child.agent_path AS child_agent_path,
               edge.status AS edge_status
               {created_ms}
        FROM threads AS parent
        JOIN thread_spawn_edges AS edge ON edge.parent_thread_id = parent.id
        JOIN threads AS child ON child.id = edge.child_thread_id
        WHERE parent.id = ?
    """
    args = [parent_thread_id]
    agent_id = str(agent_id or "").strip()
    agent_path = str(agent_path or "").strip()
    if agent_id and agent_path:
        sql += " AND child.id = ? AND child.agent_path = ?"
        args.extend([agent_id, agent_path])
    elif agent_id:
        if agent_id.startswith("/"):
            sql += " AND child.agent_path = ?"
        else:
            sql += " AND child.id = ?"
        args.append(agent_id)
    elif agent_path:
        sql += " AND child.agent_path = ?"
        args.append(agent_path)
    try:
        return [dict(row) for row in conn.execute(sql, args)]
    except sqlite3.Error:
        return None


def _later_generation(conn, parent_thread_id, child_id, agent_path, created):
    thread_cols = _columns(conn, "threads")
    created_ms = ", child.created_at_ms AS child_created_at_ms" if "created_at_ms" in thread_cols else ""
    sql = f"""
        SELECT child.id AS child_id,
               child.created_at AS child_created_at
               {created_ms}
        FROM threads AS parent
        JOIN thread_spawn_edges AS edge ON edge.parent_thread_id = parent.id
        JOIN threads AS child ON child.id = edge.child_thread_id
        WHERE parent.id = ? AND child.id != ?
    """
    args = [parent_thread_id, child_id]
    if agent_path:
        sql += " AND child.agent_path = ?"
        args.append(agent_path)
    try:
        rows = conn.execute(sql, args)
    except sqlite3.Error:
        return None
    for row in rows:
        other = _thread_time(dict(row), "child")
        if created is not None and other is not None and other > created:
            return True
        if created is None and other is not None:
            return True
    return False


def _resolve_rollout(raw):
    text = str(raw or "").strip()
    if not text:
        return None
    path = Path(text).expanduser()
    if not path.is_absolute():
        path = configured_codex_home() / path
    try:
        return path.resolve()
    except (OSError, RuntimeError):
        return path


def _tool_name(payload):
    name = str(payload.get("name") or "").strip()
    namespace = str(payload.get("namespace") or "").strip()
    bare = name.split(".")[-1]
    if "__" in bare:
        bare = bare.split("__")[-1]
    return bare, namespace


def _parse_args(raw):
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        value = json.loads(raw)
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def _target(payload):
    args = _parse_args(payload.get("arguments"))
    if not args:
        return ""
    return str(args.get("target") or args.get("id") or "").strip()


def _status_variant(value):
    if isinstance(value, str) and value in AGENT_STATUS_ATOMS:
        return value
    if isinstance(value, dict) and len(value) == 1:
        key = next(iter(value))
        if key in AGENT_STATUS_OBJECTS:
            return key
    return ""


def _output_text(output):
    if isinstance(output, str):
        return output
    if isinstance(output, list):
        parts = []
        for item in output:
            if not isinstance(item, dict):
                return None
            kind = str(item.get("type") or "")
            if kind and kind not in {"input_text", "output_text", "text"}:
                return None
            text = item.get("text")
            if not isinstance(text, str):
                return None
            parts.append(text)
        return "\n".join(parts)
    return None


def _explicit_failure(obj):
    if not isinstance(obj, dict):
        return False
    if obj.get("error") is True or obj.get("is_error") is True or obj.get("isError") is True:
        return True
    return obj.get("success") is False


def _successful_close(output):
    text = _output_text(output)
    if text is None:
        return None, "unsupported"
    try:
        parsed = json.loads(text)
    except ValueError:
        return None, "error"
    if not isinstance(parsed, dict):
        return None, "unsupported"
    if _explicit_failure(parsed):
        return None, "error"
    if "previous_status" not in parsed:
        return None, "unsupported"
    variant = _status_variant(parsed.get("previous_status"))
    if not variant:
        return None, "unsupported"
    return variant, "ok"


def _line_kind(row):
    kind = str(row.get("type") or "")
    payload = row.get("payload")
    payload_type = str(payload.get("type") or "") if isinstance(payload, dict) else ""
    return kind, payload_type


def _code_mode(kind, payload_type, payload):
    blob = " ".join((kind, payload_type))
    if "code_mode" in blob or "code-mode" in blob:
        return True
    if isinstance(payload, dict) and payload.get("code_mode_runtime_tool_id"):
        return True
    return False


def _targets_child(target, child_id, agent_path):
    return bool(target) and target in {child_id, agent_path}


def _scan_parent_rollout(path, *, child_id, agent_path, launched_at, created_at=None):
    try:
        stream = Path(path).open("r", encoding="utf-8")
    except OSError:
        return None, "parent rollout is unreadable"
    calls = {}
    closes = []
    resumes = []
    unknowns = []
    code_mode_at = []
    saw_code_mode = False
    try:
        with stream:
            for raw in stream:
                line = raw.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    return None, "parent rollout format is unsupported"
                if not isinstance(row, dict):
                    return None, "parent rollout format is unsupported"
                kind, payload_type = _line_kind(row)
                payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
                stamp = _unix(row.get("timestamp"))
                if _code_mode(kind, payload_type, payload):
                    saw_code_mode = True
                    code_mode_at.append(stamp)
                if kind != "response_item":
                    continue
                if payload_type == "function_call":
                    name, namespace = _tool_name(payload)
                    call_id = str(payload.get("call_id") or "").strip()
                    if not call_id:
                        continue
                    target = _target(payload)
                    calls[call_id] = {
                        "name": name,
                        "namespace": namespace,
                        "target": target,
                        "at": stamp,
                    }
                    if not _targets_child(target, child_id, agent_path):
                        continue
                    if name in CLOSE_TOOLS or name in RESUME_TOOLS:
                        if namespace not in HOST_NAMESPACES:
                            return None, "host tool namespace is unsupported"
                        if name in RESUME_TOOLS:
                            resumes.append(stamp)
                    elif name:
                        unknowns.append(stamp)
                    continue
                if payload_type != "function_call_output":
                    continue
                call_id = str(payload.get("call_id") or "").strip()
                call = calls.get(call_id)
                if not call:
                    continue
                target = call["target"]
                matched = _targets_child(target, child_id, agent_path)
                if call["name"] in CLOSE_TOOLS:
                    if not matched:
                        continue
                    if call.get("namespace") not in HOST_NAMESPACES:
                        return None, "host tool namespace is unsupported"
                    call_at = call.get("at")
                    if (
                        call_at is None
                        or launched_at is None
                        or created_at is None
                        or call_at < launched_at
                        or call_at < created_at
                    ):
                        continue
                    if stamp is None or stamp < call_at:
                        continue
                    if _explicit_failure(payload):
                        return None, "close_agent output is an error"
                    variant, kind_of = _successful_close(payload.get("output"))
                    if kind_of == "unsupported":
                        return None, "close_agent output format is unsupported"
                    if kind_of == "error":
                        return None, "close_agent output is an error"
                    if kind_of == "ok":
                        closes.append({
                            "call_id": call_id,
                            "at": stamp,
                            "call_at": call_at,
                            "previous_status": variant,
                        })
                    continue
                if call["name"] in RESUME_TOOLS and matched:
                    if call.get("namespace") not in HOST_NAMESPACES:
                        return None, "host tool namespace is unsupported"
                    resumes.append(call.get("at") if call.get("at") is not None else stamp)
    except OSError:
        return None, "parent rollout is unreadable"
    if not closes:
        if saw_code_mode:
            return None, "code-mode close evidence is unsupported"
        return None, "successful close_agent output is missing"
    close = closes[-1]
    later_at = close["call_at"] if close.get("call_at") is not None else close["at"]
    if any(stamp is None or (later_at is not None and stamp >= later_at) for stamp in resumes):
        return None, "a later resume, followup, or start exists for this agent"
    if any(stamp is None or (later_at is not None and stamp >= later_at) for stamp in code_mode_at):
        return None, "code-mode close evidence is unsupported"
    if any(stamp is None or (later_at is not None and stamp >= later_at) for stamp in unknowns):
        return None, "later unknown agent lifecycle exists for this agent"
    return close, ""


def inspect_native_child(*, parent_thread_id, agent_id="", agent_path="", launched_at=None, cwd="", **extra):
    """Resolve one native child generation from the configured Codex registry."""
    if extra:
        raise EvidenceError("public evidence API does not accept extra paths or payloads")
    parent_thread_id = str(parent_thread_id or "").strip()
    agent_id = str(agent_id or "").strip()
    agent_path = str(agent_path or "").strip()
    cwd = str(cwd or "").strip()
    if not parent_thread_id or not (agent_id or agent_path):
        return _unavailable("parent thread and native agent identity are required")
    conn, error = _connect(registry_path())
    if error:
        return error
    try:
        if not _schema_ok(conn):
            return _unavailable("codex registry schema is missing or unsupported")
        rows = _select_bound(conn, parent_thread_id, agent_id, agent_path)
        if rows is None:
            return _unavailable("codex registry query failed")
        if not rows:
            return _unavailable("native child is absent from the codex registry")
        bound = []
        for row in rows:
            status = str(row.get("edge_status") or "")
            if status not in EDGE_STATUSES:
                return _unavailable("thread spawn edge status is unsupported")
            bound.append(row)
        if len(bound) != 1:
            return _unavailable("native child identity is ambiguous")
        row = bound[0]
        created = _thread_time(row)
        launch = _unix(launched_at)
        if launch is None or created is None:
            return _unavailable("generation binding after attempt launch is unknown")
        if int(created) < int(launch):
            return _unavailable("child generation predates the admitted attempt")
        child_cwd = str(row.get("child_cwd") or "")
        if cwd and not _same_cwd(child_cwd, cwd):
            return _unavailable("native child cwd does not match the recovered repository")
        identity = {
            "parent_thread_id": parent_thread_id,
            "child_thread_id": str(row.get("child_id") or ""),
            "agent_id": agent_id or str(row.get("child_id") or ""),
            "agent_path": agent_path or str(row.get("child_agent_path") or ""),
        }
        later = _later_generation(conn, parent_thread_id, identity["child_thread_id"],
                                  identity["agent_path"], created)
        if later is None:
            return _unavailable("codex registry query failed", identity=identity, edge_status=row["edge_status"])
        if later:
            return _unavailable("a later resume, followup, or start exists for this agent",
                                identity=identity, edge_status=row["edge_status"])
        if row["edge_status"] == "open":
            return {
                "status": "live",
                "reason": "native child spawn edge is open",
                "detail": "native child spawn edge is open",
                "identity": identity,
                "edge_status": "open",
                "digest": "",
                "reference": {"registry": REGISTRY_NAME, "tables": ["threads", "thread_spawn_edges"]},
            }
        rollout = _resolve_rollout(row.get("parent_rollout_path"))
        if rollout is None or not rollout.is_file():
            return _unavailable("parent rollout is missing", identity=identity, edge_status="closed")
        close, detail = _scan_parent_rollout(
            rollout, child_id=identity["child_thread_id"], agent_path=identity["agent_path"],
            launched_at=launch, created_at=created,
        )
        if not close:
            return _unavailable(detail or "successful close_agent output is missing",
                                identity=identity, edge_status="closed")
        payload = {
            "parent_thread_id": identity["parent_thread_id"],
            "child_thread_id": identity["child_thread_id"],
            "agent_id": identity["agent_id"],
            "agent_path": identity["agent_path"],
            "edge_status": "closed",
            "created_at": created,
            "launched_at": launch,
            "close_call_id": close["call_id"],
            "previous_status": close["previous_status"],
        }
        return {
            "status": "verified",
            "reason": "",
            "detail": "closed spawn edge with successful close_agent and no later resume, followup, or start",
            "identity": identity,
            "edge_status": "closed",
            "digest": _digest(payload),
            "reference": {"registry": REGISTRY_NAME, "tables": ["threads", "thread_spawn_edges"]},
        }
    except sqlite3.Error as error:
        return _unavailable(f"codex registry query failed: {error}")
    finally:
        conn.close()
