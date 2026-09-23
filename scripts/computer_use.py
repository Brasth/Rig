#!/usr/bin/env python3
"""Parent-only Cua Driver CLI, doctor, and MCP wiring.

Children never receive computer-use tools. Every parent uses the Rig MCP
proxy; raw cua-driver MCP configuration is not required.
"""
from __future__ import annotations

import argparse
import errno
import importlib.util
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import harness  # noqa: E402
import cua_transport  # noqa: E402


def _load_hyphen_module(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, HERE / filename)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


cua_install = _load_hyphen_module("install_cua_driver", "install-cua-driver.py")
install_ui = _load_hyphen_module("install_ui", "install-ui.py")

PARENT_CLIS = frozenset({"grok", "codex", "opencode", "omp", "pi", "agy"})
# Isolation that actually hides user MCP from wrapper children.
ISOLATING = {
    "codex": "ignore-user-config",
    "omp": "OMP_MCP",
    "agy": "AGY_MCP",
}
SERVER = "cua-driver"


def _home() -> Path:
    raw = (os.environ.get("HOME") or "").strip()
    return Path(raw).expanduser() if raw else Path.home()


def binary_path() -> str:
    return shutil.which("cua-driver") or cua_install.cua_driver_bin() or ""


def binary_version(path: str = "") -> str:
    exe = path or binary_path()
    if not exe:
        return ""
    try:
        result = subprocess.run(
            [exe, "--version"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if result.returncode != 0:
        return ""
    line = (result.stdout or result.stderr or "").strip().splitlines()
    return line[0] if line else ""


def machine_state() -> str:
    pref = cua_install.read_preference()
    if not isinstance(pref, dict) or "opt_in" not in pref:
        return "unset"
    if pref.get("opt_in") is True:
        return "on"
    if pref.get("opt_in") is False:
        return "declined"
    return "unset"


def project_state(repo: Path) -> str:
    path = harness.harness_path(repo)
    if not path.is_file():
        return "omitted"
    section = ""
    seen = False
    enabled = False
    try:
        text = path.read_text(errors="replace")
    except OSError:
        return "omitted"
    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("[") and stripped.endswith("]") and not stripped.startswith("[["):
            section = stripped[1:-1]
            continue
        if section != "computer-use" or "=" not in stripped:
            continue
        key, _, val = stripped.partition("=")
        if key.strip() != "enabled":
            continue
        seen = True
        enabled = harness._toml_exact_true(val.split("#", 1)[0].strip())
    if not seen:
        return "omitted"
    return "true" if enabled else "false"


def parent_cli(repo: Path) -> str:
    live = (harness.live_parent() or "").strip()
    if live in PARENT_CLIS:
        return live
    pref = (harness.preferred_parent(repo) or "").strip()
    if pref in PARENT_CLIS:
        return pref
    return live or pref or ""


def isolation_supported(cli: str) -> bool:
    return cli in ISOLATING


def mcp_config_path(cli: str) -> Path:
    home = _home()
    if cli == "codex":
        return home / ".codex" / "config.toml"
    if cli == "grok":
        grok = os.environ.get("GROK_HOME") or str(home / ".grok")
        return Path(grok) / "config.toml"
    if cli == "opencode":
        return Path(os.environ.get("OPENCODE_CONFIG") or (home / ".config" / "opencode" / "opencode.json"))
    if cli == "omp":
        return Path(os.environ.get("OMP_MCP") or (home / ".omp" / "mcp.json"))
    if cli == "agy":
        return Path(os.environ.get("AGY_MCP") or (home / ".gemini" / "config" / "mcp_config.json"))
    if cli == "pi":
        return home / ".pi" / "agent" / "mcp.json"
    return home / f".{cli}" / "mcp.json"


def mcp_wired(cli: str) -> bool:
    path = mcp_config_path(cli)
    if not path.is_file():
        return False
    try:
        text = path.read_text(errors="replace")
    except OSError:
        return False
    if cli in {"codex", "grok"}:
        return bool(install_ui.section_value(text, f"mcp_servers.{SERVER}", "command"))
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return SERVER in text
    if not isinstance(data, dict):
        return False
    servers = data.get("mcpServers")
    if isinstance(servers, dict) and SERVER in servers:
        return True
    mcp = data.get("mcp")
    if isinstance(mcp, dict):
        nested = mcp.get("servers")
        if isinstance(nested, dict) and SERVER in nested:
            return True
        if SERVER in mcp:
            return True
    return False


def _json_entry(binary: str) -> dict:
    return {"command": binary, "args": ["mcp"]}


def wire_parent_mcp(cli: str, binary: str) -> str:
    """Compatibility entry point; parent access now goes through Rig MCP."""
    return "Use Rig MCP rig_cu_status; raw cua-driver MCP wiring is not required."


def install_skill_pack(binary: str) -> str:
    if not binary:
        return "skip cua-driver skills install (binary missing)"
    try:
        result = subprocess.run(
            [binary, "skills", "install"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return f"cua-driver skills install failed ({error}); continuing"
    text = ((result.stdout or "") + (result.stderr or "")).strip()
    if result.returncode != 0:
        detail = text.splitlines()[0] if text else f"exit {result.returncode}"
        return f"cua-driver skills install failed ({detail}); continuing"
    return text or "cua-driver skills install ok"


def session_line(binary: str) -> str:
    if not binary:
        return "(skipped)"
    try:
        result = subprocess.run(
            [binary, "doctor"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "(skipped)"
    blob = ((result.stdout or "") + (result.stderr or "")).strip()
    lowered = blob.lower()
    if "at-spi" in lowered or "display" in lowered or "wayland" in lowered:
        line = blob.splitlines()[0] if blob else "DISPLAY/AT-SPI warning"
        return line[:120]
    if result.returncode != 0:
        return "(skipped)"
    return "doctor ok"


def effective_state(machine: str, has_binary: bool, project: str) -> str:
    if project != "true":
        return "off (project)"
    if not has_binary:
        return "off (no binary)"
    if machine != "on":
        return "off (machine)"
    return "on"


def is_effective(repo: Path) -> bool:
    return effective_state(machine_state(), bool(binary_path()), project_state(repo)) == "on"


def tools_listed(repo: Path, *, child: bool) -> bool:
    """Parent-only Rig CU tools. Hidden for children and when not effective."""
    return (not child) and is_effective(repo)


def cu_status(repo: Path, *, status_runner=None) -> dict:
    """Read-only diagnostics, available even when action tools are hidden.

    `effective` stays the configured gate (machine opt-in, binary, repo flag).
    A bounded `cua-driver status` probe reports daemon readiness separately and
    never starts the daemon, grants permissions, or changes opt-in.
    """
    machine, binary, project = machine_state(), binary_path(), project_state(repo)
    blockers = []
    if machine != "on":
        blockers.append("machine opt-in is " + machine + "; run rig computer-use setup")
    if not binary:
        blockers.append("cua-driver is missing; run rig computer-use setup")
    if project != "true":
        blockers.append("project is disabled; run rig computer-use on in this repository")
    if binary:
        probe = _probe_daemon(binary, runner=status_runner)
    else:
        probe = {"state": "skipped", "ready": False, "detail": "", "recovery": "", "probed": False}
    next_action = (
        "Resolve blockers, then restart parent/MCP tool discovery" if blockers else
        "Use rig_cu_capture; if absent, restart parent/MCP tool discovery"
    )
    recovery = str(probe.get("recovery") or "")
    if probe.get("probed") and not probe.get("ready") and recovery:
        if blockers:
            recovery = recovery + " Configured effective stays off until blockers are resolved."
        else:
            next_action = recovery
    return {
        "machine": machine, "binary": binary, "project": project,
        "effective": not blockers, "blockers": blockers,
        "daemon": probe.get("state") or "unknown",
        "daemon_ready": bool(probe.get("ready")),
        "daemon_detail": str(probe.get("detail") or ""),
        "recovery": recovery,
        "transport": "Rig MCP rig_cu_capture/rig_cu_act/rig_cu_confirm/rig_cu_record",
        "next_action": next_action,
        "existing_profile": EXISTING_PROFILE_HINT,
        "note": "No install, opt-in, daemon or permission changes were made. Do not bypass Rig MCP.",
    }


_SNAPSHOTS: dict[str, dict] = {}
_RECORDING: dict[str, str] = {}
_SECRET_MARKERS = ("password", "passwd", "2fa", "totp", "otp", "cvv", "ssn", "secret")
_ESCALATE = {"escalate_px", "escalate_foreground"}
SNAPSHOT_TTL_SEC = 30.0
PHASE_FRESH = "fresh"
PHASE_CONFIRM = "confirm_required"
PHASE_DONE = "done"
CHROME_BUNDLE = "com.google.Chrome"
# Driver sessions are connection-scoped.  A unique parent session prevents a
# stale public session from another Rig MCP process being reused accidentally.
BROWSER_SESSION = f"rig-cu-{os.getpid()}-{uuid.uuid4().hex[:8]}"
EXISTING_PROFILE_HINT = "start CuaDriver with: cua-driver serve --grant existing-profile"
CHROME_PROFILE_SETUP_HINT = "chrome-profile setup"
EVIDENCE_REL = Path(".rig") / "cu-evidence"
DRIVER_TOOLS = {
    "get_window_state": "get_window_state",
    "click": "click",
    "type": "type_text",
    "type_text": "type_text",
    "key": "press_key",
    "press_key": "press_key",
    "launch_app": "launch_app",
    "list_windows": "list_windows",
    "get_browser_state": "get_browser_state",
    "browser_prepare": "browser_prepare",
    "browser_click": "browser_click",
    "browser_type": "browser_type",
    "browser_navigate": "browser_navigate",
    "start_session": "start_session",
    "start_recording": "start_recording",
    "stop_recording": "stop_recording",
}


def reset_snapshots() -> None:
    _SNAPSHOTS.clear()
    _RECORDING.clear()


def _now() -> float:
    return time.monotonic()


def empty_evidence(**overrides) -> dict:
    payload = {
        "ok": False,
        "effective": False,
        "action": "",
        "snapshot_id": "",
        "pid": 0,
        "window_id": 0,
        "addressed": {
            "kind": "",
            "element_token": "",
            "index": None,
            "label": "",
            "ref": "",
            "x": None,
            "y": None,
        },
        "effect": "",
        "before_png": "",
        "after_png": "",
        "elements": [],
        "outline": "",
        "coord_space": "",
        "recording_path": "",
        "output_dir": "",
        "brief_block": "",
        "hint": "",
    }
    payload.update(overrides)
    if not payload.get("brief_block"):
        payload["brief_block"] = brief_block(payload)
    return payload


def brief_block(ev: dict) -> str:
    addressed = ev.get("addressed") or {}
    kind = addressed.get("kind") or ""
    xy = ""
    if addressed.get("x") is not None and addressed.get("y") is not None:
        xy = f" x={addressed.get('x')} y={addressed.get('y')}"
    lines = [
        "## Computer-use evidence",
        f"- action: {ev.get('action') or ''}",
        f"- effect: {ev.get('effect') or ''}",
        f"- snapshot: {ev.get('snapshot_id') or ''}",
        f"- window: pid={ev.get('pid') or 0} window_id={ev.get('window_id') or 0}",
        f"- coord_space: {ev.get('coord_space') or ''}",
        (
            f"- addressed: kind={kind} {addressed.get('label') or ''} "
            f"token={addressed.get('element_token') or ''} "
            f"ref={addressed.get('ref') or ''} "
            f"index={addressed.get('index')}{xy}"
        ),
        f"- before: {ev.get('before_png') or ''}",
        f"- after: {ev.get('after_png') or ''}",
        "Child must not click.",
    ]
    if ev.get("recording_path") or ev.get("output_dir"):
        lines.insert(
            -1,
            f"- recording: {ev.get('recording_path') or ''} dir={ev.get('output_dir') or ''}",
        )
    outline = str(ev.get("outline") or "").strip()
    if outline:
        clipped = "\n".join(outline.splitlines()[:12])
        lines.insert(-1, f"- outline:\n{clipped}")
    return "\n".join(lines) + "\n"


def _looks_secret(text: str) -> bool:
    lowered = (text or "").strip().lower()
    if not lowered:
        return False
    return any(marker in lowered for marker in _SECRET_MARKERS)


def driver_tool(name: str) -> str:
    key = (name or "").strip().lower()
    return DRIVER_TOOLS.get(key, (name or "").strip())


def _extract_json(blob: str) -> dict | None:
    text = (blob or "").strip()
    if not text:
        return None
    candidates: list[str] = []
    if text.startswith("{") or text.startswith("["):
        candidates.append(text)
    lines = text.splitlines()
    if lines:
        last = lines[-1].strip()
        if last and last not in candidates:
            candidates.append(last)
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        chunk = text[start:end + 1]
        if chunk not in candidates:
            candidates.append(chunk)
    for item in candidates:
        try:
            data = json.loads(item)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            return data
    return None


def _unwrap_driver(data: dict) -> dict:
    if not isinstance(data, dict):
        return {}
    nested = data.get("structuredContent")
    if not isinstance(nested, dict):
        return data
    merged = dict(data)
    merged.update(nested)
    return merged


DAEMON_HINT = "cua-driver daemon is not running; start CuaDriver.app / cua-driver serve"
DAEMON_RECOVERY = (
    "Cua Driver daemon is not running. Start CuaDriver.app or run `cua-driver serve` "
    "in the graphical session, then call rig_cu_status again. Rig does not start the "
    "daemon, grant permissions, or change opt-in."
)
STATUS_PROBE_TIMEOUT = 5


def _daemon_stopped_text(blob: str) -> bool:
    lowered = (blob or "").lower()
    return (
        "daemon is not running" in lowered
        or "no cua driver daemon listening" in lowered
        or "daemon unavailable" in lowered
    )


def _looks_like_driver_help(blob: str) -> bool:
    lowered = (blob or "").lstrip().lower()
    if not lowered or lowered.startswith("{") or lowered.startswith("["):
        return False
    return (
        "usage: cua-driver" in lowered
        or lowered.startswith("usage:")
        or "subcommands:" in lowered
        or "you probably meant one of:" in lowered
    )


def _mcp_text(data: dict) -> str:
    content = data.get("content")
    parts = []
    if isinstance(content, list):
        for item in content:
            if isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
            elif isinstance(item, str):
                parts.append(item)
    elif isinstance(content, str):
        parts.append(content)
    return "\n".join(parts).strip()


def _failure_text(data: dict) -> str:
    if not isinstance(data, dict):
        return ""
    return "\n".join(
        str(data.get(key) or "")
        for key in ("hint", "error", "message", "raw")
        if data.get(key)
    )


def _invoke_error_hint(error: BaseException) -> str:
    if isinstance(error, (subprocess.TimeoutExpired, TimeoutError)):
        return f"cua-driver timed out ({error})"
    text = str(error or "")
    if _daemon_stopped_text(text):
        return DAEMON_HINT
    missing = isinstance(error, FileNotFoundError) or (
        isinstance(error, OSError) and getattr(error, "errno", None) == errno.ENOENT
    )
    if missing:
        return f"cua-driver not installed ({error})"
    return f"cua-driver failed ({error})"


def _public_driver_hint(data: dict, fallback: str) -> str:
    if not isinstance(data, dict):
        return fallback
    hint = str(data.get("hint") or "")
    error = str(data.get("error") or "")
    message = str(data.get("message") or "")
    raw = str(data.get("raw") or "")
    blob = "\n".join(part for part in (hint, error, message, raw) if part).strip()
    lowered = blob.lower()
    if data.get("timeout") or "timed out" in lowered:
        if hint and "timed out" in hint.lower():
            return hint
        if error and "timed out" in error.lower():
            return error
        return "cua-driver timed out" + (f" ({blob})" if blob else "")
    if _daemon_stopped_text(blob):
        return DAEMON_HINT
    if data.get("missing_binary"):
        return hint or f"cua-driver not installed ({blob or fallback})"
    if hint:
        return hint
    if error:
        return error
    if message:
        return message
    if raw:
        return raw
    return fallback


def _non_json_failure(exit_code: int, blob: str) -> dict:
    hint = blob or f"cua-driver exited {exit_code} with no JSON"
    if _daemon_stopped_text(blob):
        hint = DAEMON_HINT
    elif _looks_like_driver_help(blob):
        hint = (
            "cua-driver returned help text, not a tool result; "
            "`cua-driver call <tool> <json>` requires a running daemon"
        )
    return {
        "ok": False,
        "non_json": True,
        "exit_code": exit_code,
        "raw": blob,
        "hint": hint,
        "error": hint,
    }


def _normalize_driver_output(exit_code: int, stdout: str, stderr: str) -> dict:
    """MCP isError / structuredContent / text JSON. Exit 0 and help text are not success."""
    out = stdout or ""
    err = stderr or ""
    blob = (out + ("\n" + err if err else "")).strip()
    if _looks_like_driver_help(out) or (not out.strip() and _looks_like_driver_help(blob)):
        return _non_json_failure(exit_code, blob)
    data = None
    stripped = out.strip()
    if stripped.startswith("{") or stripped.startswith("["):
        data = _extract_json(stripped)
    if not isinstance(data, dict):
        if _looks_like_driver_help(blob):
            return _non_json_failure(exit_code, blob)
        data = _extract_json(blob)
    if not isinstance(data, dict):
        return _non_json_failure(exit_code, blob)
    text = _mcp_text(data)
    text_stripped = text.strip()
    text_json = None
    if text_stripped.startswith("{") or text_stripped.startswith("["):
        text_json = _extract_json(text_stripped)
    text_error = False
    if isinstance(text_json, dict):
        text_error = (
            text_json.get("isError") is True
            or text_json.get("is_error") is True
            or text_json.get("ok") is False
        )
        for key, value in text_json.items():
            if key in {"isError", "is_error", "content"}:
                continue
            data.setdefault(key, value)
    # Outer envelope failure wins over nested structuredContent ok:true.
    outer_failed = (
        data.get("isError") is True
        or data.get("is_error") is True
        or data.get("ok") is False
        or text_error
    )
    is_error = data.get("isError") is True or data.get("is_error") is True or text_error
    data = _unwrap_driver(data)
    data.pop("structuredContent", None)
    is_error = is_error or data.get("isError") is True or data.get("is_error") is True
    daemon = _daemon_stopped_text(blob) or _daemon_stopped_text(text)
    failed = outer_failed or is_error or daemon or exit_code != 0 or data.get("ok") is False
    if failed:
        data["ok"] = False
        # Preserve MCP isError only when the envelope/tool reported it.
        # Plain ok:false (stale/refused/hidden) must stay semantic, not isError.
        if is_error:
            data["isError"] = True
        if daemon:
            data["error"] = DAEMON_HINT
            data["hint"] = DAEMON_HINT
        elif is_error:
            message = text or str(data.get("error") or data.get("message") or data.get("hint") or "")
            message = message or "cua-driver tool returned isError"
            data.setdefault("error", message)
            data.setdefault("hint", message)
        elif outer_failed:
            message = text or str(data.get("error") or data.get("message") or data.get("hint") or "")
            message = message or "cua-driver returned ok:false"
            data.setdefault("error", message)
            data.setdefault("hint", message)
        else:
            data.setdefault("hint", str(data.get("error") or data.get("message") or blob or f"cua-driver exited {exit_code}"))
    else:
        data["ok"] = True
    data["exit_code"] = exit_code
    data.setdefault("raw", blob)
    return data


def _exception_driver_result(error: BaseException) -> dict:
    hint = _invoke_error_hint(error)
    payload = {"ok": False, "raw": str(error), "hint": hint, "error": hint}
    if isinstance(error, (subprocess.TimeoutExpired, TimeoutError)):
        payload["timeout"] = True
        payload["exit_code"] = 124
        return payload
    if isinstance(error, FileNotFoundError) or (
        isinstance(error, OSError) and getattr(error, "errno", None) == errno.ENOENT
    ):
        payload["missing_binary"] = True
        payload["exit_code"] = 127
        return payload
    payload["exit_code"] = 1
    return payload


def _default_runner(binary: str, args: list[str], timeout: int) -> dict:
    """Call one Driver tool over this parent's persistent MCP connection."""
    if not args:
        return {"ok": False, "non_json": True, "hint": "missing cua-driver tool"}
    tool = str(args[0])
    try:
        body = json.loads(args[1]) if len(args) > 1 else {}
        if not isinstance(body, dict):
            raise ValueError("cua-driver tool arguments must be an object")
        result = cua_transport.call(binary, BROWSER_SESSION, tool, body, timeout)
    except (OSError, subprocess.TimeoutExpired, TimeoutError, ValueError, cua_transport.TransportError) as error:
        return _exception_driver_result(error)
    return _normalize_driver_output(0, json.dumps(result), "")


def _snapshot_failed(data: dict) -> bool:
    """ok:false and isError win even when residual elements, refs, or snapshot text remain."""
    if not isinstance(data, dict):
        return True
    if data.get("timeout") or data.get("missing_binary") or data.get("non_json"):
        return True
    if data.get("isError") is True or data.get("is_error") is True:
        return True
    if data.get("ok") is False:
        return True
    return False


def _transport_failure(data: dict) -> bool:
    """Subprocess or MCP envelope failure. Semantic stale/refused effects still flow."""
    if not isinstance(data, dict):
        return True
    if data.get("timeout") or data.get("missing_binary") or data.get("non_json"):
        return True
    if data.get("isError") is True or data.get("is_error") is True:
        return True
    if _daemon_stopped_text(_failure_text(data)):
        return True
    if data.get("ok") is False:
        effect = str(data.get("effect") or "").strip().lower()
        if effect in {"stale", "refused", "hidden"} | set(_ESCALATE):
            return False
        return True
    code = data.get("exit_code")
    if isinstance(code, int) and code != 0 and data.get("ok") is not True:
        return True
    return False


def _refuse_transport(action: str, snapshot_id: str, snap: dict | None, data: dict, addressed: dict | None = None) -> dict:
    snap = snap or {}
    # A connection failure may have happened after Driver received the action.
    # Do not allow that snapshot to issue another action on a replacement
    # connection: force the parent to capture and inspect the live state first.
    if snapshot_id and snap is _SNAPSHOTS.get(snapshot_id):
        snap["phase"] = PHASE_DONE
    return empty_evidence(
        ok=False,
        effective=True,
        action=action,
        snapshot_id=snapshot_id,
        pid=snap.get("pid") or 0,
        window_id=snap.get("window_id") or 0,
        addressed=addressed if isinstance(addressed, dict) else _addressed(),
        before_png=snap.get("png") or "",
        effect="hidden",
        coord_space=snap.get("coord_space") or "",
        hint="transport failed; capture again before another action",
    )


def invoke_driver(args: list[str], *, runner=None, timeout: int = 30, binary: str = "") -> dict:
    exe = binary or binary_path()
    if not exe:
        raise FileNotFoundError("cua-driver not installed")
    mapped = list(args)
    if mapped:
        mapped[0] = driver_tool(str(mapped[0]))
    fn = runner or _default_runner
    try:
        result = fn(exe, mapped, timeout)
    except (OSError, subprocess.TimeoutExpired) as error:
        return _exception_driver_result(error)
    if not isinstance(result, dict):
        return {"ok": False, "non_json": True, "hint": "malformed driver output", "error": "malformed driver output"}
    return _unwrap_driver(result)


def _executable(path: str) -> bool:
    return bool(path) and Path(path).is_file() and os.access(path, os.X_OK)


def _interpret_daemon_status(
    exit_code: int,
    stdout: str,
    stderr: str,
    *,
    timeout: bool = False,
    missing_binary: bool = False,
) -> dict:
    blob = ((stdout or "") + ("\n" + stderr if stderr else "")).strip()
    if timeout:
        return {
            "state": "unknown",
            "ready": False,
            "probed": True,
            "detail": blob or "status probe timed out",
            "recovery": (
                "cua-driver status timed out. This is not a missing binary. "
                "Retry rig_cu_status, or run `cua-driver status` yourself. "
                "Rig does not start the daemon."
            ),
        }
    if missing_binary:
        return {
            "state": "unknown",
            "ready": False,
            "probed": True,
            "detail": blob or "status probe could not execute cua-driver",
            "recovery": "cua-driver is missing; run rig computer-use setup",
        }
    # Nonzero exit is never ready, even if stdout mentions a running daemon.
    if exit_code != 0:
        if _daemon_stopped_text(blob):
            return {
                "state": "stopped",
                "ready": False,
                "probed": True,
                "detail": blob,
                "recovery": DAEMON_RECOVERY,
            }
        return {
            "state": "unknown",
            "ready": False,
            "probed": True,
            "detail": blob or f"exit {exit_code}",
            "recovery": (
                "cua-driver status exited nonzero. Run `cua-driver status`. "
                "If the daemon is stopped, start CuaDriver.app or `cua-driver serve`. "
                "Rig will not start it."
            ),
        }
    lowered = blob.lower()
    if _daemon_stopped_text(blob):
        return {
            "state": "stopped",
            "ready": False,
            "probed": True,
            "detail": blob,
            "recovery": DAEMON_RECOVERY,
        }
    if "daemon is running" in lowered or "daemon listening" in lowered or "registered (running)" in lowered:
        return {"state": "ready", "ready": True, "probed": True, "detail": blob, "recovery": ""}
    data = _extract_json(stdout or "") or _extract_json(blob)
    if isinstance(data, dict) and not _looks_like_driver_help(blob):
        daemon = str(data.get("daemon") or data.get("state") or data.get("status") or "").strip().lower()
        running = data.get("running")
        if running is True or daemon in {"running", "ready", "listening"}:
            return {"state": "ready", "ready": True, "probed": True, "detail": blob, "recovery": ""}
        if running is False or daemon in {"stopped", "not_running", "not running"}:
            return {"state": "stopped", "ready": False, "probed": True, "detail": blob, "recovery": DAEMON_RECOVERY}
    if _looks_like_driver_help(blob) or not blob:
        detail = blob or "status probe returned no daemon status"
        return {
            "state": "unknown",
            "ready": False,
            "probed": True,
            "detail": detail,
            "recovery": (
                "cua-driver status did not report a running daemon. Help text, exit 0, "
                "or non-JSON output is not success. Run `cua-driver status`. If the daemon "
                "is stopped, start CuaDriver.app or `cua-driver serve`."
            ),
        }
    return {
        "state": "unknown",
        "ready": False,
        "probed": True,
        "detail": blob or f"exit {exit_code}",
        "recovery": (
            "Could not tell whether the daemon is running. Run `cua-driver status`. "
            "If it is stopped, start CuaDriver.app or `cua-driver serve`. "
            "Rig will not start it."
        ),
    }


def _default_status_runner(binary: str, args: list[str], timeout: int) -> dict:
    if list(args) != ["status"]:
        return {"returncode": 2, "stdout": "", "stderr": "status probe refused unexpected arguments"}
    try:
        result = subprocess.run(
            [binary, *args],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as error:
        return {"returncode": 124, "stdout": "", "stderr": str(error), "timeout": True}
    except FileNotFoundError as error:
        return {"returncode": 127, "stdout": "", "stderr": str(error), "missing_binary": True}
    except OSError as error:
        return {"returncode": 1, "stdout": "", "stderr": str(error)}
    return {"returncode": result.returncode, "stdout": result.stdout or "", "stderr": result.stderr or ""}


def _probe_daemon(binary: str, *, runner=None, timeout: int = STATUS_PROBE_TIMEOUT) -> dict:
    if runner is None and not _executable(binary):
        return {
            "state": "unknown",
            "ready": False,
            "probed": False,
            "detail": "status probe skipped; cua-driver path is not executable",
            "recovery": "run rig computer-use setup",
        }
    fn = runner or _default_status_runner
    try:
        result = fn(binary, ["status"], timeout)
    except subprocess.TimeoutExpired as error:
        return _interpret_daemon_status(124, "", str(error), timeout=True)
    except FileNotFoundError as error:
        return _interpret_daemon_status(127, "", str(error), missing_binary=True)
    except OSError as error:
        text = str(error)
        return _interpret_daemon_status(
            127 if getattr(error, "errno", None) == errno.ENOENT else 1,
            "",
            text,
            missing_binary=getattr(error, "errno", None) == errno.ENOENT,
        )
    if not isinstance(result, dict):
        return _interpret_daemon_status(1, "", "status probe returned a non-object")
    if "state" in result and "ready" in result:
        result.setdefault("probed", True)
        result.setdefault("detail", "")
        result.setdefault("recovery", "" if result.get("ready") else DAEMON_RECOVERY)
        return result
    return _interpret_daemon_status(
        int(result.get("returncode", result.get("exit_code", 1)) or 0),
        str(result.get("stdout") or ""),
        str(result.get("stderr") or ""),
        timeout=bool(result.get("timeout")),
        missing_binary=bool(result.get("missing_binary")),
    )


def _elements_from_driver(data: dict) -> list[dict]:
    rows = []
    payload = _unwrap_driver(data)
    raw = payload.get("elements") if isinstance(payload.get("elements"), list) else []
    for item in raw:
        if not isinstance(item, dict):
            continue
        index = item.get("index")
        if index is None:
            index = item.get("element_index")
        token = str(item.get("element_token") or item.get("token") or "").strip()
        if index is None and not token:
            continue
        try:
            index_n = int(index) if index is not None else None
        except (TypeError, ValueError):
            index_n = None
        if not token and index_n is not None:
            token = f"el-{index_n}"
        rows.append({
            "index": index_n,
            "role": str(item.get("role") or ""),
            "label": str(item.get("label") or item.get("name") or ""),
            "element_token": token,
        })
    return rows


def _store_snapshot(
    snapshot_id: str,
    pid: int,
    window_id: int,
    elements: list[dict],
    png: str,
    **meta,
) -> dict:
    tokens = {}
    for row in elements:
        token = str(row.get("element_token") or "").strip()
        if token:
            tokens[token] = row
    refs = {}
    for row in meta.get("refs") or []:
        if not isinstance(row, dict):
            continue
        ref = str(row.get("ref") or "").strip()
        if ref:
            refs[ref] = row
    snap = {
        "pid": pid,
        "window_id": window_id,
        "tokens": tokens,
        "refs": refs,
        "png": png,
        "escalate": str(meta.get("escalate") or ""),
        "px_allowed": bool(meta.get("px_allowed")),
        "png_width": int(meta.get("png_width") or 0),
        "png_height": int(meta.get("png_height") or 0),
        "coord_space": str(meta.get("coord_space") or "window_local"),
        "degraded": bool(meta.get("degraded")),
        "pixel_to_css_scale_x": float(meta.get("pixel_to_css_scale_x") or 1),
        "pixel_to_css_scale_y": float(meta.get("pixel_to_css_scale_y") or 1),
        "target_id": str(meta.get("target_id") or ""),
        "tab_id": str(meta.get("tab_id") or ""),
        "session": str(meta.get("session") or ""),
        "outline": str(meta.get("outline") or ""),
        "bind_selector": str(meta.get("bind_selector") or ""),
        "profile_key": str(meta.get("profile_key") or ""),
        "phase": PHASE_FRESH,
        "created_at": _now(),
    }
    _SNAPSHOTS[snapshot_id] = snap
    return snap


def _reject_snapshot(action: str, snapshot_id: str, snap: dict | None, freshness: str, hint: str) -> dict:
    snap = snap or {}
    return empty_evidence(
        ok=False,
        effective=True,
        action=action,
        snapshot_id=snapshot_id,
        pid=snap.get("pid") or 0,
        window_id=snap.get("window_id") or 0,
        before_png=snap.get("png") or "",
        coord_space=snap.get("coord_space") or "",
        effect="stale",
        hint=hint,
        snapshot_freshness=freshness,
    )


def _require_snapshot(snapshot_id: str, *, action: str, need_phase: str) -> tuple[dict | None, dict | None]:
    """Return (snap, None) or (snap_or_none, reject_evidence). Never calls Driver."""
    sid = str(snapshot_id or "").strip()
    snap = _SNAPSHOTS.get(sid)
    if not snap:
        return None, _reject_snapshot(
            action, snapshot_id, None, "unknown",
            "capture_required: unknown snapshot",
        )
    created = float(snap.get("created_at") or 0)
    if _now() - created > SNAPSHOT_TTL_SEC:
        return snap, _reject_snapshot(
            action, sid, snap, "expired",
            "capture_required: snapshot expired",
        )
    phase = str(snap.get("phase") or PHASE_FRESH)
    if phase != need_phase:
        freshness = "consumed" if phase in {PHASE_CONFIRM, PHASE_DONE} else phase
        return snap, _reject_snapshot(
            action, sid, snap, freshness,
            "capture_required: snapshot not usable for this step",
        )
    return snap, None


def _mark_confirm_required(snap: dict) -> None:
    snap["phase"] = PHASE_CONFIRM


def _apply_invoked_effect(snap: dict, effect: str) -> str:
    """After Driver ran. Confirm only real effects; consume stale/refused."""
    if effect in _ESCALATE:
        snap["escalate"] = effect
        if effect == "escalate_px":
            snap["px_allowed"] = True
    if effect in {"stale", "refused"}:
        snap["phase"] = PHASE_DONE
        return "consumed"
    _mark_confirm_required(snap)
    return PHASE_CONFIRM


def _act_effect(data: dict, default: str) -> str:
    effect = _map_effect(data, default)
    if effect == "confirmed":
        return "unverifiable"
    return effect


def _png(data: dict, *keys: str, fallback: str = "") -> str:
    payload = _unwrap_driver(data)
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    path = str(fallback or "").strip()
    if path and Path(path).is_file():
        return path
    return ""


def _driver_blob(data: dict) -> str:
    payload = _unwrap_driver(data)
    parts = [
        payload.get("raw"), payload.get("error"), payload.get("code"),
        payload.get("hint"), payload.get("effect"), payload.get("status"),
        payload.get("name"),
    ]
    return " ".join(str(item) for item in parts if item).lower()


def _map_effect(data: dict, default: str) -> str:
    payload = _unwrap_driver(data)
    raw = str(payload.get("effect") or payload.get("status") or "").strip().lower()
    err = str(payload.get("error") or payload.get("code") or payload.get("name") or "").strip().lower()
    blob = _driver_blob(payload)
    if "permissions_pending" in blob or "permission is still pending" in blob:
        return "refused"
    if "stale" in err or "stale_element_token" in raw or "snapshot_id_required" in err:
        return "stale"
    esc = payload.get("escalation")
    if isinstance(esc, dict):
        target = str(esc.get("target") or esc.get("recommended") or "").strip().lower()
        if target in {"pixel", "px"}:
            return "escalate_px"
        if target in {"foreground"}:
            return "escalate_foreground"
    if raw in {"stale", "confirmed", "unverifiable", "refused", "captured", "hidden"} | _ESCALATE:
        return raw
    if raw in {"escalate-px", "px", "pixel"}:
        return "escalate_px"
    if raw in {"escalate-foreground", "foreground"}:
        return "escalate_foreground"
    if payload.get("ok") is False:
        return "unverifiable"
    return default


def _payload_windows(data: dict) -> list[dict]:
    payload = _unwrap_driver(data if isinstance(data, dict) else {})
    windows = payload.get("windows")
    rows = [item for item in windows if isinstance(item, dict)] if isinstance(windows, list) else []
    if rows:
        return rows
    raw = payload.get("raw")
    if not isinstance(raw, str) or not raw.strip():
        return []
    nested = _extract_json(raw)
    if not isinstance(nested, dict):
        return []
    nested = _unwrap_driver(nested)
    windows = nested.get("windows")
    if not isinstance(windows, list):
        return []
    return [item for item in windows if isinstance(item, dict)]


def _first_window_id(data: dict) -> int:
    fallback = 0
    for item in _payload_windows(data):
        try:
            value = int(item.get("window_id") or 0)
        except (TypeError, ValueError):
            continue
        if value <= 0:
            continue
        if item.get("is_on_screen") or item.get("on_current_space"):
            return value
        if not fallback:
            fallback = value
    return fallback


def _list_window_id(pid: int, *, runner=None) -> int:
    if int(pid or 0) <= 0:
        return 0
    try:
        listed = invoke_driver(
            ["list_windows", json.dumps({"pid": int(pid), "session": BROWSER_SESSION})],
            runner=runner,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return 0
    return _first_window_id(listed)


def _resolve_window_id(pid: int, window_id: int, data: dict | None = None, *, runner=None) -> int:
    win_n = int(window_id or 0)
    if win_n > 0:
        return win_n
    if data:
        win_n = _first_window_id(data)
        if win_n > 0:
            return win_n
    return _list_window_id(pid, runner=runner)


def _as_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _png_ihdr(path: str) -> tuple[int, int]:
    try:
        with open(path, "rb") as handle:
            header = handle.read(24)
    except OSError:
        return 0, 0
    if len(header) < 24 or header[:8] != b"\x89PNG\r\n\x1a\n":
        return 0, 0
    width, height = struct.unpack(">II", header[16:24])
    return int(width), int(height)


def _screenshot_meta(data: dict, png_path: str = "") -> dict:
    payload = _unwrap_driver(data)
    shot = payload.get("screenshot") if isinstance(payload.get("screenshot"), dict) else {}
    space = str(shot.get("coordinate_space") or shot.get("coord_space") or "").strip()
    width = _as_int(payload.get("screenshot_width") or shot.get("width") or shot.get("viewport_css_width"))
    height = _as_int(payload.get("screenshot_height") or shot.get("height") or shot.get("viewport_css_height"))
    if (not width or not height) and png_path:
        file_w, file_h = _png_ihdr(png_path)
        width = width or file_w
        height = height or file_h
    scale_x = shot.get("pixel_to_css_scale_x")
    scale_y = shot.get("pixel_to_css_scale_y")
    if space:
        coord_space = space
    elif scale_x is not None or scale_y is not None:
        coord_space = "viewport_css_px"
    else:
        coord_space = "window_local"
    return {
        "png_width": width,
        "png_height": height,
        "coord_space": coord_space,
        "pixel_to_css_scale_x": _as_float(scale_x, 1.0) if scale_x is not None else 1.0,
        "pixel_to_css_scale_y": _as_float(scale_y, 1.0) if scale_y is not None else 1.0,
    }


def _refs_from_driver(data: dict) -> list[dict]:
    payload = _unwrap_driver(data)
    raw = payload.get("refs")
    if not isinstance(raw, list):
        snap = payload.get("snapshot")
        raw = snap.get("refs") if isinstance(snap, dict) else []
    if not isinstance(raw, list):
        raw = []
    rows = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        ref = str(item.get("ref") or item.get("id") or "").strip()
        if not ref:
            continue
        actions = item.get("actions") if isinstance(item.get("actions"), list) else []
        rows.append({
            "ref": ref,
            "role": str(item.get("role") or ""),
            "label": str(item.get("name") or item.get("label") or item.get("text") or ""),
            "actions": [str(action) for action in actions],
        })
    return rows


def _outline_from_driver(data: dict, limit: int = 40) -> str:
    payload = _unwrap_driver(data)
    outline = payload.get("outline")
    if outline is None:
        snap = payload.get("snapshot")
        if isinstance(snap, dict):
            outline = snap.get("outline")
    if isinstance(outline, list):
        text = "\n".join(str(item) for item in outline)
    else:
        text = str(outline or "").strip()
    lines = [line for line in text.splitlines() if line.strip()][:limit]
    return "\n".join(lines)


def _clickable_refs(refs: list[dict]) -> bool:
    for row in refs:
        actions = [str(item).lower() for item in (row.get("actions") or [])]
        if "click" in actions or "pointer" in actions:
            return True
    return False


def _grant_missing(data: dict) -> bool:
    blob = _driver_blob(data)
    return (
        "existing-profile" in blob
        or "existing_profile" in blob
        or "existing profile" in blob
        or "grant existing" in blob
    )


def _setup_needed(data: dict) -> bool:
    payload = _unwrap_driver(data)
    blob = _driver_blob(payload)
    name = str(payload.get("name") or payload.get("code") or payload.get("status") or "").lower()
    return (
        "browser_requires_setup" in blob
        or "browser_consent_required" in blob
        or name in {"browser_requires_setup", "browser_consent_required"}
    )


def _bind_ok(data: dict) -> bool:
    payload = _unwrap_driver(data)
    if payload.get("ok") is False:
        return False
    status = str(payload.get("status") or "").strip().lower()
    quality = str(payload.get("binding_quality") or "").strip().lower()
    if status and status != "ok":
        return False
    if quality and quality != "exact":
        return False
    if payload.get("mutation_allowed") is False:
        return False
    return True


def _tabs_from_bind(data: dict) -> list[dict]:
    payload = _unwrap_driver(data)
    for key in ("tabs", "targets", "pages"):
        rows = payload.get(key)
        if isinstance(rows, list):
            return [item for item in rows if isinstance(item, dict)]
    if payload.get("target_id") or payload.get("tab_id"):
        return [payload]
    return []


def _pick_tab(tabs: list[dict], bind_selector: str) -> dict | None:
    selector = str(bind_selector or "").strip()
    if not selector:
        return None
    for tab in tabs:
        hay = str(tab.get("url") or tab.get("target_url") or "")
        if selector in hay:
            return tab
    return None


def _chrome_windows(*, runner=None) -> tuple[int, list[int]]:
    try:
        launched = invoke_driver(
            ["launch_app", json.dumps({"bundle_id": CHROME_BUNDLE})],
            runner=runner,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return 0, []
    launched = _unwrap_driver(launched)
    pid = _as_int(launched.get("pid"))
    windows = _payload_windows(launched)
    if pid > 0 and not windows:
        try:
            listed = invoke_driver(["list_windows", json.dumps({"pid": pid})], runner=runner)
        except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
            listed = {}
        windows = _payload_windows(listed)
    preferred: list[int] = []
    fallback: list[int] = []
    for item in windows:
        wid = _as_int(item.get("window_id"))
        if wid <= 0:
            continue
        if item.get("is_on_screen") or item.get("on_current_space"):
            preferred.append(wid)
        else:
            fallback.append(wid)
    return pid, preferred + fallback


def open_named_profile(profile_key: str, url: str, *, opener=None) -> dict:
    key = str(profile_key or "").strip()
    target = str(url or "").strip()
    if not key:
        return {"ok": False, "hint": CHROME_PROFILE_SETUP_HINT}
    if not target:
        return {"ok": False, "hint": "url required with profile_key"}
    lowered = target.lower()
    if not (lowered.startswith("https://") or lowered.startswith("http://")):
        return {"ok": False, "hint": "url must be http(s)"}
    fn = opener or _default_chrome_opener
    data = fn(key, target)
    if not isinstance(data, dict):
        return {"ok": False, "hint": CHROME_PROFILE_SETUP_HINT}
    if data.get("ok") is False:
        data.setdefault("hint", CHROME_PROFILE_SETUP_HINT)
        return data
    data.setdefault("ok", True)
    return data


def _default_chrome_opener(profile_key: str, url: str) -> dict:
    exe = shutil.which("chrome-profile") or ""
    if not exe:
        return {"ok": False, "hint": CHROME_PROFILE_SETUP_HINT}
    try:
        result = subprocess.run(
            [exe, "open", "--json", "--no-activate", "--force", profile_key, url],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired) as error:
        return {"ok": False, "hint": f"chrome-profile failed ({error})"}
    blob = ((result.stdout or "") + ("\n" + (result.stderr or "") if result.stderr else "")).strip()
    data = _extract_json(result.stdout or "") or _extract_json(blob) or {}
    if not isinstance(data, dict):
        data = {}
    else:
        data = dict(data)
    diagnostic = "\n".join(
        part
        for part in (
            str(data.get("hint") or "").strip(),
            str(data.get("error") or "").strip(),
            str(data.get("message") or "").strip(),
            (result.stderr or "").strip(),
            (result.stdout or "").strip() if data.get("ok") is False else "",
        )
        if part
    ).strip()
    if result.returncode != 0:
        data["ok"] = False
        # Keep permission-denied / CLI stderr; only fall back to setup hint.
        if diagnostic:
            data["hint"] = diagnostic
        else:
            data.setdefault("hint", CHROME_PROFILE_SETUP_HINT)
        data["raw"] = blob
        return data
    # Exit 0 must not overwrite JSON ok:false.
    if data.get("ok") is False:
        if diagnostic:
            data.setdefault("hint", diagnostic)
        else:
            data.setdefault("hint", CHROME_PROFILE_SETUP_HINT)
        data["raw"] = blob
        return data
    data["ok"] = True
    return data


def _parse_xy(x, y):
    if x is None and y is None:
        return None
    if x is None or y is None:
        return "incomplete"
    try:
        return (float(x), float(y))
    except (TypeError, ValueError):
        return "invalid"


def _in_bounds(x: float, y: float, width: int, height: int) -> bool:
    if x < 0 or y < 0:
        return False
    if width and x >= width:
        return False
    if height and y >= height:
        return False
    return True


def _addressed(*, kind="", token="", row=None, ref="", x=None, y=None) -> dict:
    row = row or {}
    return {
        "kind": kind,
        "element_token": token,
        "index": row.get("index"),
        "label": row.get("label") or "",
        "ref": ref,
        "x": x,
        "y": y,
    }


def _fresh_png_path(pid: int, window_id: int) -> str:
    return str(
        Path(tempfile.gettempdir())
        / f"rig-cu-{os.getpid()}-{pid}-{window_id}-{len(_SNAPSHOTS) + 1}.png"
    )


def _native_snapshot_result(
    repo: Path,
    *,
    pid: int,
    window_id: int,
    runner=None,
    px_carry: bool = False,
    extra: dict | None = None,
) -> dict:
    action = "capture"
    png_path = _fresh_png_path(pid, window_id)
    payload = {
        "pid": pid,
        "window_id": window_id,
        "screenshot_out_file": png_path,
        "session": BROWSER_SESSION,
    }
    try:
        data = invoke_driver(["get_window_state", json.dumps(payload)], runner=runner)
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired) as error:
        return empty_evidence(
            ok=False, action=action, effective=True, effect="hidden",
            hint=_invoke_error_hint(error),
        )
    data = _unwrap_driver(data)
    if _snapshot_failed(data):
        return empty_evidence(
            ok=False, effective=True, action=action, pid=pid, window_id=window_id,
            effect="hidden", hint=_public_driver_hint(data, "get_window_state failed"),
        )
    elements = _elements_from_driver(data)
    snapshot_id = str(data.get("snapshot_id") or "").strip() or f"snap-{len(_SNAPSHOTS) + 1}"
    pid_n = _as_int(data.get("pid"), pid)
    returned_win = _as_int(data.get("window_id"))
    win_n = returned_win if returned_win > 0 else window_id
    png = _png(data, "screenshot_file_path", "screenshot_path", "after_png", "png", fallback=png_path)
    meta = _screenshot_meta(data, png)
    degraded = bool(data.get("degraded")) or (not elements and bool(png))
    px_allowed = degraded or px_carry
    extra = extra or {}
    _store_snapshot(
        snapshot_id, pid_n, win_n, elements, png,
        px_allowed=px_allowed, degraded=degraded,
        png_width=meta["png_width"], png_height=meta["png_height"],
        coord_space=meta["coord_space"],
        pixel_to_css_scale_x=meta["pixel_to_css_scale_x"],
        pixel_to_css_scale_y=meta["pixel_to_css_scale_y"],
        **extra,
        session=BROWSER_SESSION,
    )
    return empty_evidence(
        ok=True, effective=True, action=action, snapshot_id=snapshot_id,
        pid=pid_n, window_id=win_n, effect="captured", after_png=png,
        elements=elements, outline=str(extra.get("outline") or ""),
        coord_space=meta["coord_space"],
        snapshot_freshness=PHASE_FRESH,
        hint="act only with a token from this snapshot"
        if not px_allowed else "px allowed on this snapshot (degraded or escalate_px)",
    )


def _browser_tab_snapshot(
    *,
    pid: int,
    window_id: int,
    target_id: str,
    tab_id: str,
    session: str,
    runner=None,
    px_carry: bool = False,
    extra: dict | None = None,
) -> dict:
    action = "capture"
    png_path = _fresh_png_path(pid, window_id)
    body = {
        "target_id": target_id,
        "tab_id": tab_id,
        "session": session,
        "snapshot_format": "semantic_v2",
        "include_screenshot": True,
        "screenshot_out_file": png_path,
    }
    try:
        data = invoke_driver(["get_browser_state", json.dumps(body)], runner=runner)
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired) as error:
        return empty_evidence(
            ok=False, action=action, effective=True, effect="hidden",
            hint=_invoke_error_hint(error),
        )
    data = _unwrap_driver(data)
    if _snapshot_failed(data):
        return empty_evidence(
            ok=False, effective=True, action=action, pid=pid, window_id=window_id,
            effect="hidden", hint=_public_driver_hint(data, "get_browser_state snapshot failed"),
        )
    refs = _refs_from_driver(data)
    elements = _elements_from_driver(data)
    outline = _outline_from_driver(data)
    snapshot_id = str(data.get("snapshot_id") or "").strip() or f"snap-{len(_SNAPSHOTS) + 1}"
    png = _png(
        data, "screenshot_file_path", "screenshot_path", "after_png", "png",
        fallback=png_path,
    )
    meta = _screenshot_meta(data, png)
    if meta["coord_space"] == "window_local":
        meta["coord_space"] = "viewport_css_px"
    degraded = bool(data.get("degraded")) or not _clickable_refs(refs)
    px_allowed = degraded or px_carry
    extra = extra or {}
    _store_snapshot(
        snapshot_id, pid, window_id, elements, png,
        refs=refs, px_allowed=px_allowed, degraded=degraded,
        png_width=meta["png_width"], png_height=meta["png_height"],
        coord_space=meta["coord_space"],
        pixel_to_css_scale_x=meta["pixel_to_css_scale_x"],
        pixel_to_css_scale_y=meta["pixel_to_css_scale_y"],
        target_id=target_id, tab_id=tab_id, session=session, outline=outline,
        **extra,
    )
    return empty_evidence(
        ok=True, effective=True, action=action, snapshot_id=snapshot_id,
        pid=pid, window_id=window_id, effect="captured", after_png=png,
        elements=elements, outline=outline, coord_space=meta["coord_space"],
        snapshot_freshness=PHASE_FRESH,
        hint="act with a fresh browser ref; px only if this snapshot allows it",
    )


def _prepare_existing_profile(pid: int, window_id: int, session: str, *, runner=None) -> dict:
    body = {
        "pid": pid,
        "window_id": window_id,
        "session": session,
        "strategy": {"kind": "existing_profile"},
    }
    return _unwrap_driver(invoke_driver(["browser_prepare", json.dumps(body)], runner=runner))


def _bind_named_profile(
    *,
    profile_key: str,
    url: str,
    runner=None,
    opener=None,
    px_carry: bool = False,
) -> dict:
    action = "capture"
    opened = open_named_profile(profile_key, url, opener=opener)
    if not opened.get("ok"):
        return empty_evidence(
            ok=False, effective=True, action=action, effect="refused",
            hint=str(opened.get("hint") or CHROME_PROFILE_SETUP_HINT),
        )
    selector = str(opened.get("bind_selector") or opened.get("open_marker") or "").strip()
    if not selector:
        return empty_evidence(
            ok=False, effective=True, action=action, effect="unverifiable",
            hint="chrome-profile open returned no bind_selector",
        )
    session = BROWSER_SESSION
    pid, window_ids = _chrome_windows(runner=runner)
    if pid <= 0 or not window_ids:
        return empty_evidence(
            ok=False, effective=True, action=action, effect="unverifiable",
            hint="Chrome window not found after chrome-profile open",
        )
    last_hint = "exact Chrome tab bind_selector not found"
    for window_id in window_ids:
        bind_body = {"pid": pid, "window_id": window_id, "session": session}
        try:
            bind = invoke_driver(["get_browser_state", json.dumps(bind_body)], runner=runner)
        except (FileNotFoundError, OSError, subprocess.TimeoutExpired) as error:
            return empty_evidence(
                action=action, effective=True, effect="hidden",
                hint=_invoke_error_hint(error),
            )
        bind = _unwrap_driver(bind)
        if _grant_missing(bind):
            return empty_evidence(
                ok=False, effective=True, action=action, pid=pid, window_id=window_id,
                effect="refused", hint=EXISTING_PROFILE_HINT,
            )
        if _setup_needed(bind):
            try:
                prepared = _prepare_existing_profile(pid, window_id, session, runner=runner)
            except (FileNotFoundError, OSError, subprocess.TimeoutExpired) as error:
                return empty_evidence(
                    action=action, effective=True, effect="hidden",
                    hint=_invoke_error_hint(error),
                )
            if _grant_missing(prepared) or prepared.get("ok") is False:
                return empty_evidence(
                    ok=False, effective=True, action=action, pid=pid, window_id=window_id,
                    effect="refused", hint=EXISTING_PROFILE_HINT,
                )
            try:
                bind = invoke_driver(["get_browser_state", json.dumps(bind_body)], runner=runner)
            except (FileNotFoundError, OSError, subprocess.TimeoutExpired) as error:
                return empty_evidence(
                    action=action, effective=True, effect="hidden",
                    hint=_invoke_error_hint(error),
                )
            bind = _unwrap_driver(bind)
            if _grant_missing(bind) or _setup_needed(bind):
                return empty_evidence(
                    ok=False, effective=True, action=action, pid=pid, window_id=window_id,
                    effect="refused", hint=EXISTING_PROFILE_HINT,
                )
        if not _bind_ok(bind):
            last_hint = str(bind.get("error") or bind.get("status") or "browser bind was not exact")
            continue
        tab = _pick_tab(_tabs_from_bind(bind), selector)
        if not tab:
            last_hint = "exact Chrome tab bind_selector not found"
            continue
        target_id = str(tab.get("target_id") or bind.get("target_id") or "").strip()
        tab_id = str(tab.get("tab_id") or bind.get("tab_id") or "").strip()
        if not target_id or not tab_id:
            last_hint = "browser bind missing target_id/tab_id"
            continue
        return _browser_tab_snapshot(
            pid=pid, window_id=window_id, target_id=target_id, tab_id=tab_id,
            session=session, runner=runner, px_carry=px_carry,
            extra={"bind_selector": selector, "profile_key": profile_key},
        )
    return empty_evidence(
        ok=False, effective=True, action=action, pid=pid,
        effect="unverifiable", hint=last_hint,
    )


def cu_capture(
    repo: Path,
    *,
    pid: int = 0,
    window_id: int = 0,
    bundle_id: str = "",
    app_name: str = "",
    profile_key: str = "",
    url: str = "",
    runner=None,
    profile_opener=None,
    px_carry: bool = False,
) -> dict:
    action = "capture"
    if not is_effective(repo):
        return empty_evidence(
            action=action, effect="hidden",
            hint="computer-use not effective; use chrome-devtools",
        )
    key = str(profile_key or "").strip()
    target = str(url or "").strip()
    if key or target:
        if not key:
            return empty_evidence(
                ok=False, effective=True, action=action, effect="refused",
                hint="pass profile_key for named Chrome; isolated Driver profile is not the Figma path",
            )
        if not target:
            return empty_evidence(
                ok=False, effective=True, action=action, effect="refused",
                hint="url required with profile_key",
            )
        return _bind_named_profile(
            profile_key=key, url=target, runner=runner, opener=profile_opener,
            px_carry=px_carry,
        )
    pid_n = int(pid or 0)
    win_n = int(window_id or 0)
    bundle = str(bundle_id or "").strip()
    name = str(app_name or "").strip()
    if pid_n <= 0 and (bundle or name):
        launch = {"bundle_id": bundle} if bundle else {"name": name}
        launch["session"] = BROWSER_SESSION
        try:
            launched = invoke_driver(["launch_app", json.dumps(launch)], runner=runner)
        except (FileNotFoundError, OSError, subprocess.TimeoutExpired) as error:
            return empty_evidence(
                ok=False, action=action, effective=True, effect="hidden",
                hint=_invoke_error_hint(error),
            )
        launched = _unwrap_driver(launched)
        if _transport_failure(launched):
            hint = _public_driver_hint(launched, "launch_app failed")
            pending = "permissions_pending" in _failure_text(launched).lower()
            if pending:
                hint = "macOS Accessibility/Screen Recording pending; cua-driver permissions grant"
            return empty_evidence(
                ok=False, effective=True, action=action,
                effect="refused" if pending else "hidden", hint=hint,
            )
        pid_n = _as_int(launched.get("pid"))
        win_n = _resolve_window_id(pid_n, win_n, launched, runner=runner)
        if pid_n <= 0:
            hint = _public_driver_hint(launched, "launch_app returned no pid")
            effect = "refused" if "permissions_pending" in hint.lower() else "unverifiable"
            if effect == "refused":
                hint = "macOS Accessibility/Screen Recording pending; cua-driver permissions grant"
            return empty_evidence(
                ok=False, effective=True, action=action, effect=effect, hint=hint,
            )
    if pid_n > 0 and win_n <= 0:
        win_n = _resolve_window_id(pid_n, win_n, runner=runner)
    if pid_n > 0 and win_n <= 0:
        return empty_evidence(
            ok=False, effective=True, action=action, pid=pid_n, window_id=0,
            effect="unverifiable",
            hint="launch_app/list_windows returned no window_id; window_id 0 yields an empty tree",
        )
    return _native_snapshot_result(
        repo, pid=pid_n, window_id=win_n, runner=runner, px_carry=px_carry,
    )


def cu_act(
    repo: Path,
    *,
    snapshot_id: str,
    element_token: str = "",
    ref: str = "",
    action: str = "click",
    text: str = "",
    key: str = "",
    x=None,
    y=None,
    runner=None,
) -> dict:
    kind = (action or "click").strip().lower()
    if kind not in {"click", "type", "key"}:
        kind = "click"
    if not is_effective(repo):
        return empty_evidence(
            action=kind, effect="hidden",
            hint="computer-use not effective; use chrome-devtools",
        )
    snap, rejected = _require_snapshot(snapshot_id, action=kind, need_phase=PHASE_FRESH)
    if rejected:
        return rejected
    token = str(element_token or "").strip()
    ref_id = str(ref or "").strip()
    coords = _parse_xy(x, y)
    modes = [bool(token), bool(ref_id), coords not in {None, "incomplete", "invalid"}]
    if sum(1 for flag in modes if flag) > 1:
        return empty_evidence(
            ok=False, effective=True, action=kind, snapshot_id=snapshot_id,
            pid=snap.get("pid") or 0, window_id=snap.get("window_id") or 0,
            before_png=snap.get("png") or "", effect="refused",
            hint="token, ref, and x,y are mutually exclusive",
        )
    if coords in {"incomplete", "invalid"}:
        return empty_evidence(
            ok=False, effective=True, action=kind, snapshot_id=snapshot_id,
            pid=snap.get("pid") or 0, window_id=snap.get("window_id") or 0,
            before_png=snap.get("png") or "", effect="refused",
            hint="x and y must both be numbers",
        )
    row = (snap.get("tokens") or {}).get(token) if token else None
    ref_row = (snap.get("refs") or {}).get(ref_id) if ref_id else None
    browser = bool(snap.get("target_id") and snap.get("tab_id"))

    if coords is not None:
        px_x, px_y = coords
        browser_px = browser and str(snap.get("coord_space") or "") == "viewport_css_px"
        if not snap.get("px_allowed") and not browser_px:
            return empty_evidence(
                ok=False, effective=True, action=kind, snapshot_id=snapshot_id,
                pid=snap.get("pid") or 0, window_id=snap.get("window_id") or 0,
                addressed=_addressed(kind="px", x=px_x, y=px_y),
                before_png=snap.get("png") or "", effect="stale",
                coord_space=snap.get("coord_space") or "",
                hint="need escalate_px or degraded snapshot",
            )
        if not _in_bounds(px_x, px_y, snap.get("png_width") or 0, snap.get("png_height") or 0):
            return empty_evidence(
                ok=False, effective=True, action=kind, snapshot_id=snapshot_id,
                pid=snap.get("pid") or 0, window_id=snap.get("window_id") or 0,
                addressed=_addressed(kind="px", x=px_x, y=px_y),
                before_png=snap.get("png") or "", effect="refused",
                coord_space=snap.get("coord_space") or "",
                hint="x,y outside PNG bounds",
            )
        if kind == "type" and _looks_secret(text):
            return empty_evidence(
                ok=False, effective=True, action=kind, snapshot_id=snapshot_id,
                pid=snap.get("pid") or 0, window_id=snap.get("window_id") or 0,
                addressed=_addressed(kind="px", x=px_x, y=px_y),
                effect="refused", hint="refused: password/2FA/payment",
            )
        if browser:
            if kind != "click":
                return empty_evidence(
                    ok=False, effective=True, action=kind, snapshot_id=snapshot_id,
                    pid=snap.get("pid") or 0, window_id=snap.get("window_id") or 0,
                    addressed=_addressed(kind="px", x=px_x, y=px_y),
                    before_png=snap.get("png") or "", effect="stale",
                    coord_space=snap.get("coord_space") or "",
                    hint="browser type/key needs a ref; px is click-only on canvas",
                )
            css_x = px_x * float(snap.get("pixel_to_css_scale_x") or 1)
            css_y = px_y * float(snap.get("pixel_to_css_scale_y") or 1)
            body = {
                "target_id": snap.get("target_id"),
                "tab_id": snap.get("tab_id"),
                "session": snap.get("session") or BROWSER_SESSION,
                "x": css_x,
                "y": css_y,
                "input_route": "trusted",
            }
            tool = "browser_click"
        else:
            body = {
                "pid": snap.get("pid") or 0,
                "window_id": snap.get("window_id") or 0,
                "x": px_x,
                "y": px_y,
                "session": snap.get("session") or BROWSER_SESSION,
            }
            if kind == "type":
                body["text"] = text
            if kind == "key":
                body["key"] = key
            tool = driver_tool(kind)
        try:
            data = invoke_driver([tool, json.dumps(body)], runner=runner)
        except (FileNotFoundError, OSError, subprocess.TimeoutExpired) as error:
            return empty_evidence(
                ok=False, action=kind, effective=True, snapshot_id=snapshot_id,
                effect="hidden", hint=_invoke_error_hint(error),
            )
        data = _unwrap_driver(data)
        if _transport_failure(data):
            return _refuse_transport(
                kind, snapshot_id, snap, data, _addressed(kind="px", x=px_x, y=px_y),
            )
        effect = _act_effect(data, "unverifiable")
        blob = _driver_blob(data)
        if "browser_input_trust_unavailable" in blob:
            effect = "escalate_foreground"
        freshness = _apply_invoked_effect(snap, effect)
        return empty_evidence(
            ok=effect not in {"stale", "refused"}, effective=True, action=kind,
            snapshot_id=snapshot_id, pid=snap.get("pid") or 0,
            window_id=snap.get("window_id") or 0,
            addressed=_addressed(kind="px", x=px_x, y=px_y),
            effect=effect, before_png=snap.get("png") or "",
            after_png=_png(data, "screenshot_path", "after_png", "screenshot_file_path"),
            coord_space=snap.get("coord_space") or "",
            outline=snap.get("outline") or "",
            snapshot_freshness=freshness,
            hint="call rig_cu_confirm before reporting success"
            if effect not in {"stale", "refused"} else "capture again",
        )

    if ref_id:
        if not ref_row:
            return empty_evidence(
                ok=False, effective=True, action=kind, snapshot_id=snapshot_id,
                pid=snap.get("pid") or 0, window_id=snap.get("window_id") or 0,
                before_png=snap.get("png") or "", effect="stale", hint="capture again",
            )
        if kind == "type" and _looks_secret(text):
            return empty_evidence(
                ok=False, effective=True, action=kind, snapshot_id=snapshot_id,
                pid=snap.get("pid") or 0, window_id=snap.get("window_id") or 0,
                addressed=_addressed(kind="ref", row=ref_row, ref=ref_id),
                effect="refused", hint="refused: password/2FA/payment",
            )
        body = {
            "target_id": snap.get("target_id"),
            "tab_id": snap.get("tab_id"),
            "ref": ref_id,
            "session": snap.get("session") or BROWSER_SESSION,
            "input_route": "dom_event",
        }
        if kind == "type":
            body["text"] = text
            tool = "browser_type"
        elif kind == "key":
            return empty_evidence(
                ok=False, effective=True, action=kind, snapshot_id=snapshot_id,
                pid=snap.get("pid") or 0, window_id=snap.get("window_id") or 0,
                addressed=_addressed(kind="ref", row=ref_row, ref=ref_id),
                effect="stale", hint="browser key needs press_key on native chrome, not a page ref",
            )
        else:
            tool = "browser_click"
        try:
            data = invoke_driver([tool, json.dumps(body)], runner=runner)
        except (FileNotFoundError, OSError, subprocess.TimeoutExpired) as error:
            return empty_evidence(
                ok=False, action=kind, effective=True, snapshot_id=snapshot_id,
                effect="hidden", hint=_invoke_error_hint(error),
            )
        data = _unwrap_driver(data)
        if _transport_failure(data):
            return _refuse_transport(
                kind, snapshot_id, snap, data,
                _addressed(kind="ref", row=ref_row, ref=ref_id),
            )
        effect = _act_effect(data, "unverifiable")
        freshness = _apply_invoked_effect(snap, effect)
        return empty_evidence(
            ok=effect not in {"stale", "refused"}, effective=True, action=kind,
            snapshot_id=snapshot_id, pid=snap.get("pid") or 0,
            window_id=snap.get("window_id") or 0,
            addressed=_addressed(kind="ref", row=ref_row, ref=ref_id),
            effect=effect, before_png=snap.get("png") or "",
            after_png=_png(data, "screenshot_path", "after_png", "screenshot_file_path"),
            coord_space=snap.get("coord_space") or "",
            outline=snap.get("outline") or "",
            snapshot_freshness=freshness,
            hint="call rig_cu_confirm before reporting success"
            if effect not in {"stale", "refused"} else "capture again",
        )

    if kind in {"click", "type"} and not row:
        return empty_evidence(
            ok=False, effective=True, action=kind, snapshot_id=snapshot_id,
            pid=snap.get("pid") or 0, window_id=snap.get("window_id") or 0,
            before_png=snap.get("png") or "", effect="stale", hint="capture again",
        )
    if kind == "type" and _looks_secret(text):
        return empty_evidence(
            ok=False, effective=True, action=kind, snapshot_id=snapshot_id,
            pid=snap.get("pid") or 0, window_id=snap.get("window_id") or 0,
            addressed=_addressed(kind="ax", token=token, row=row),
            effect="refused", hint="refused: password/2FA/payment",
        )
    body = {
        "pid": snap.get("pid") or 0,
        "window_id": snap.get("window_id") or 0,
        "snapshot_id": snapshot_id,
        "element_token": token,
        "session": snap.get("session") or BROWSER_SESSION,
    }
    if row and row.get("index") is not None:
        body["element_index"] = row["index"]
    if kind == "type":
        body["text"] = text
    if kind == "key":
        body["key"] = key
    try:
        data = invoke_driver([driver_tool(kind), json.dumps(body)], runner=runner)
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired) as error:
        return empty_evidence(
            ok=False, action=kind, effective=True, snapshot_id=snapshot_id,
            effect="hidden", hint=_invoke_error_hint(error),
        )
    data = _unwrap_driver(data)
    if _transport_failure(data):
        return _refuse_transport(
            kind, snapshot_id, snap, data,
            _addressed(kind="ax", token=token, row=row),
        )
    effect = _act_effect(data, "unverifiable")
    freshness = _apply_invoked_effect(snap, effect)
    return empty_evidence(
        ok=effect not in {"stale", "refused"}, effective=True, action=kind,
        snapshot_id=snapshot_id, pid=snap.get("pid") or 0,
        window_id=snap.get("window_id") or 0,
        addressed=_addressed(kind="ax", token=token, row=row),
        effect=effect, before_png=snap.get("png") or "",
        after_png=_png(data, "screenshot_path", "after_png"),
        coord_space=snap.get("coord_space") or "",
        snapshot_freshness=freshness,
        hint="call rig_cu_confirm before reporting success" if effect not in {"stale", "refused"} else "capture again",
    )


def cu_confirm(repo: Path, *, snapshot_id: str, runner=None, profile_opener=None) -> dict:
    action = "confirm"
    if not is_effective(repo):
        return empty_evidence(
            action=action, effect="hidden",
            hint="computer-use not effective; use chrome-devtools",
        )
    snap, rejected = _require_snapshot(snapshot_id, action=action, need_phase=PHASE_CONFIRM)
    if rejected:
        return rejected
    px_carry = bool(snap.get("px_allowed") or snap.get("escalate") == "escalate_px")
    if snap.get("target_id") and snap.get("tab_id"):
        captured = _browser_tab_snapshot(
            pid=snap.get("pid") or 0,
            window_id=snap.get("window_id") or 0,
            target_id=snap.get("target_id"),
            tab_id=snap.get("tab_id"),
            session=snap.get("session") or BROWSER_SESSION,
            runner=runner,
            px_carry=px_carry,
            extra={
                "bind_selector": snap.get("bind_selector") or "",
                "profile_key": snap.get("profile_key") or "",
            },
        )
    else:
        captured = cu_capture(
            repo, pid=snap.get("pid") or 0, window_id=snap.get("window_id") or 0,
            runner=runner, profile_opener=profile_opener, px_carry=px_carry,
        )
    if captured.get("effect") == "hidden":
        return captured
    snap["phase"] = PHASE_DONE
    new_id = str(captured.get("snapshot_id") or "").strip()
    new_snap = _SNAPSHOTS.get(new_id)
    if new_snap is not None and px_carry:
        new_snap["px_allowed"] = True
    effect = str(snap.get("escalate") or "").strip() or "confirmed"
    if captured.get("effect") in {"hidden", "stale"}:
        effect = captured.get("effect") or "unverifiable"
    captured.update(
        action=action,
        effect=effect,
        before_png=snap.get("png") or "",
        ok=effect == "confirmed",
        snapshot_freshness=PHASE_FRESH,
        hint="" if effect == "confirmed" else f"Driver says {effect}",
    )
    captured["brief_block"] = brief_block(captured)
    return captured


def evidence_root(repo: Path) -> Path:
    return (Path(repo) / EVIDENCE_REL).resolve()


def resolve_record_dir(repo: Path, output_dir: str = "") -> tuple[Path | None, str]:
    """Keep recording under repo .rig/cu-evidence. Refuse /tmp and ~/cua-trajectories."""
    root = evidence_root(repo)
    raw = str(output_dir or "").strip()
    if not raw:
        stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        return root / f"gui-test-{stamp}", ""
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = Path(repo) / path
    try:
        resolved = path.resolve()
        resolved.relative_to(root)
    except (OSError, ValueError):
        return None, "output_dir must be under .rig/cu-evidence (not /tmp or ~/cua-trajectories)"
    return resolved, ""


def cu_record(
    repo: Path,
    *,
    action: str = "start",
    output_dir: str = "",
    record_video: bool | None = None,
    runner=None,
) -> dict:
    """Parent MCP recording. Agents must not shell cua-driver recording/start_recording."""
    kind = (action or "start").strip().lower()
    if kind not in {"start", "stop"}:
        return empty_evidence(
            ok=False, effective=True, action="record", effect="refused",
            hint="action must be start or stop",
        )
    if not is_effective(repo):
        return empty_evidence(
            action=f"record_{kind}", effect="hidden",
            hint="computer-use not effective; use chrome-devtools",
        )
    if kind == "start":
        dest, err = resolve_record_dir(repo, output_dir)
        if dest is None:
            return empty_evidence(
                ok=False, effective=True, action="record_start", effect="refused",
                hint=err,
            )
        dest.mkdir(parents=True, exist_ok=True)
        video = True if record_video is None else bool(record_video)
        body = {"output_dir": str(dest), "record_video": video}
        try:
            data = invoke_driver(["start_recording", json.dumps(body)], runner=runner)
        except (FileNotFoundError, OSError, subprocess.TimeoutExpired) as error:
            return empty_evidence(
                action="record_start", effective=True, effect="hidden",
                hint=_invoke_error_hint(error),
            )
        data = _unwrap_driver(data)
        if _transport_failure(data):
            return empty_evidence(
                ok=False, effective=True, action="record_start", effect="hidden",
                hint=_public_driver_hint(data, "start_recording failed"),
            )
        effect = _map_effect(data, "recorded")
        blob = _driver_blob(data)
        if "daemon is not running" in blob:
            effect = "hidden"
            hint = "cua-driver daemon is not running; start CuaDriver.app / cua-driver serve"
        else:
            hint = str(data.get("last_error") or data.get("hint") or "")
            if effect in {"stale", "refused", "hidden"}:
                hint = hint or "capture again"
            elif not hint:
                hint = "click with rig_cu_act; stop with rig_cu_record action=stop"
        if effect not in {"stale", "refused", "hidden", "unverifiable"}:
            _RECORDING["output_dir"] = str(dest)
            _RECORDING["video"] = "1" if video else "0"
        rec = str(dest / "recording.mp4") if video else ""
        return empty_evidence(
            ok=effect not in {"stale", "refused", "hidden"},
            effective=True, action="record_start", effect=effect,
            output_dir=str(dest), recording_path=rec, hint=hint,
        )
    try:
        data = invoke_driver(["stop_recording", "{}"], runner=runner, timeout=60)
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired) as error:
        return empty_evidence(
            action="record_stop", effective=True, effect="hidden",
            hint=_invoke_error_hint(error),
        )
    data = _unwrap_driver(data)
    if _transport_failure(data):
        return empty_evidence(
            ok=False, effective=True, action="record_stop", effect="hidden",
            hint=_public_driver_hint(data, "stop_recording failed"),
        )
    effect = _map_effect(data, "stopped")
    last = str(data.get("last_video_path") or "").strip()
    remembered = _RECORDING.get("output_dir") or ""
    if not last and remembered:
        candidate = Path(remembered) / "recording.mp4"
        if candidate.is_file():
            last = str(candidate)
    if "daemon is not running" in _driver_blob(data):
        effect = "hidden"
    hint = str(data.get("last_error") or data.get("hint") or "")
    _RECORDING.clear()
    if not last:
        hint = hint or (
            "no recording.mp4; stitch confirm PNGs (source=png-stitch) "
            "or check Screen Recording TCC / ffmpeg"
        )
    elif not hint:
        hint = "recording.mp4 finalized"
    return empty_evidence(
        ok=effect not in {"stale", "refused", "hidden"},
        effective=True, action="record_stop", effect=effect,
        output_dir=remembered, recording_path=last, hint=hint,
    )


def parent_mcp_line(cli: str) -> str:
    return "Rig proxy (raw cua-driver MCP is not required)"


def status_lines(repo: Path) -> list[str]:
    machine = machine_state()
    binary = binary_path()
    version = binary_version(binary)
    project = project_state(repo)
    cli = parent_cli(repo)
    if version:
        binary_line = f"{version} (ok)"
    elif binary:
        binary_line = "cua-driver (ok)"
    else:
        binary_line = "(missing — rig computer-use setup)"
    if project == "omitted":
        project_line = "(omitted = off)"
    else:
        project_line = f"enabled={project}"
    return [
        "Computer-use",
        f"  machine:    {machine}",
        f"  binary:     {binary_line}",
        f"  project:    {project_line}",
        f"  effective:  {effective_state(machine, bool(binary), project)}",
        f"  session:    {session_line(binary)}",
        "  parent MCP: Rig proxy (raw cua-driver MCP is not required)",
        f"  existing-profile: human grant ({EXISTING_PROFILE_HINT})",
        "  fallback:   chrome-devtools",
    ]


def set_project_enabled(repo: Path, enabled: bool) -> None:
    path = harness.harness_path(repo)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.is_file():
        path.write_text("")
    install_ui.set_key(path, "computer-use", "enabled", "true" if enabled else "false")


def cmd_status(repo: Path) -> int:
    print("\n".join(status_lines(repo)))
    return 0


def cmd_doctor(repo: Path) -> int:
    print("\n".join(status_lines(repo)))
    return 0


def cmd_on(repo: Path) -> int:
    set_project_enabled(repo, True)
    print("computer-use enabled = true")
    if not binary_path():
        print("project on, binary missing — rig computer-use setup")
    return 0


def cmd_off(repo: Path) -> int:
    set_project_enabled(repo, False)
    print("computer-use enabled = false")
    return 0


def cmd_setup(repo: Path) -> int:
    cua_install.main(["--cua-driver"])
    binary = binary_path()
    print("Use Rig MCP rig_cu_status, then rig_cu_capture/rig_cu_act/rig_cu_confirm.")
    print("Raw cua-driver MCP and vendor skill installation are not required.")
    print("setup does not flip this repo [computer-use] enabled. Per project: rig computer-use on")
    if binary:
        print("next: fully quit the parent once, then rig computer-use on in this repo")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="rig computer-use")
    parser.add_argument(
        "command",
        nargs="?",
        default="status",
        choices=("status", "setup", "on", "off", "doctor"),
    )
    parser.add_argument("--repo", default=".")
    args = parser.parse_args(argv)
    repo = Path(args.repo).resolve()
    if args.command == "setup":
        return cmd_setup(repo)
    if args.command == "on":
        return cmd_on(repo)
    if args.command == "off":
        return cmd_off(repo)
    if args.command == "doctor":
        return cmd_doctor(repo)
    return cmd_status(repo)


if __name__ == "__main__":
    raise SystemExit(main())
