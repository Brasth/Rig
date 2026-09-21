#!/usr/bin/env python3
"""Parent-only BrowserSkill CLI, doctor, and MCP proxy.

Children never receive bsk or rig_bsk_* tools. Parent invokes bsk only
through this proxy. Never runs `bsk install-skill`.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import harness  # noqa: E402


def _load_hyphen_module(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, HERE / filename)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


bsk_install = _load_hyphen_module("install_browser_skill", "install-browser-skill.py")
install_ui = _load_hyphen_module("install_ui", "install-ui.py")

RECEIPT_VERSION = "rig.bsk.v1"
EVIDENCE_REL = Path(".rig") / "bsk-evidence"
SNAPSHOT_TTL_SEC = 30.0
PHASE_FRESH = "fresh"
PHASE_CONFIRM = "confirm_required"
PHASE_DONE = "done"
FORBIDDEN_BSK = frozenset({
    "evaluate", "upload", "download", "install-skill",
    "--unattended", "pair", "pairing",
})
_SNAPSHOTS: dict[str, dict] = {}
_SESSION: dict[str, str] = {"id": "", "active": ""}


def reset_snapshots() -> None:
    _SNAPSHOTS.clear()
    _SESSION["id"] = ""
    _SESSION["active"] = ""


def _home() -> Path:
    raw = (os.environ.get("HOME") or "").strip()
    return Path(raw).expanduser() if raw else Path.home()


def binary_path() -> str:
    return shutil.which("bsk") or bsk_install.bsk_bin() or ""


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
    pref = bsk_install.read_preference()
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
        if section != "browser-skill" or "=" not in stripped:
            continue
        key, _, val = stripped.partition("=")
        if key.strip() != "enabled":
            continue
        seen = True
        enabled = harness._toml_exact_true(val.split("#", 1)[0].strip())
    if not seen:
        return "omitted"
    return "true" if enabled else "false"


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


def _default_runner(binary: str, args: list[str], timeout: int) -> dict:
    try:
        result = subprocess.run(
            [binary, *args],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "raw": "", "exit_code": 124, "timeout": True, "hint": "bsk timed out"}
    except OSError as error:
        return {"ok": False, "raw": "", "exit_code": 1, "hint": f"bsk failed ({error})"}
    blob = ((result.stdout or "") + (result.stderr or "")).strip()
    want_json = "--json" in [str(item) for item in args]
    data = _extract_json(blob)
    if isinstance(data, dict):
        data.setdefault("exit_code", result.returncode)
        if blob and "raw" not in data:
            data["raw"] = blob
        if result.returncode != 0:
            data["ok"] = False
            data.setdefault("hint", f"bsk exited {result.returncode}")
        elif "ok" not in data:
            data["ok"] = True
        return data
    if want_json or result.returncode != 0:
        hint = "malformed bsk output" if result.returncode == 0 else f"bsk exited {result.returncode}"
        return {
            "ok": False,
            "raw": blob,
            "exit_code": result.returncode,
            "malformed": True,
            "hint": hint,
        }
    return {"ok": True, "raw": blob, "exit_code": result.returncode}


def _forbidden_args(args: list[str]) -> str:
    lowered = [str(item).strip().lower() for item in args]
    for item in lowered:
        key = item.lstrip("-").replace("_", "-")
        if item in FORBIDDEN_BSK or key in FORBIDDEN_BSK:
            return item
        if item.startswith("install-skill") or key == "install-skill":
            return "install-skill"
    joined = " ".join(lowered)
    if "borrow-confirm" in joined and any(token in joined for token in (" off", "=off", "false", "0")):
        return "borrow-confirm-off"
    if "--no-confirm" in lowered:
        return "--no-confirm"
    return ""


def invoke_bsk(args: list[str], *, runner=None, timeout: int = 30, binary: str = "") -> dict:
    forbidden = _forbidden_args(args)
    if forbidden:
        return {
            "ok": False,
            "effect": "refused",
            "hint": "forbidden bsk operation: " + forbidden,
            "exit_code": 2,
        }
    exe = binary or binary_path()
    if not exe:
        return {"ok": False, "hint": "bsk not installed", "exit_code": 127}
    fn = runner or _default_runner
    try:
        data = fn(exe, list(args), timeout)
    except subprocess.TimeoutExpired:
        return {"ok": False, "hint": "bsk timed out", "exit_code": 124, "timeout": True}
    except OSError as error:
        return {"ok": False, "hint": f"bsk failed ({error})", "exit_code": 1}
    if not isinstance(data, dict):
        return {"ok": False, "hint": "malformed bsk output", "malformed": True, "exit_code": 1}
    return data


def _browsers_from_status(data: dict) -> list | None:
    browsers = data.get("browsers")
    if isinstance(browsers, list):
        return browsers
    nested = data.get("status")
    if isinstance(nested, dict) and isinstance(nested.get("browsers"), list):
        return nested["browsers"]
    return None


def extension_connected(*, runner=None, binary: str = "") -> bool:
    exe = binary or binary_path()
    if not exe:
        return False
    data = invoke_bsk(["status", "--json"], runner=runner, timeout=10, binary=exe)
    if not isinstance(data, dict):
        return False
    if data.get("ok") is False or data.get("timeout") or data.get("malformed"):
        return False
    if isinstance(data.get("exit_code"), int) and data["exit_code"] != 0:
        return False
    browsers = _browsers_from_status(data)
    if browsers is None:
        return False
    return len(browsers) > 0


def effective_state(machine: str, has_binary: bool, project: str, extension: bool) -> str:
    if project != "true":
        return "off (project)"
    if not has_binary:
        return "off (no binary)"
    if machine != "on":
        return "off (machine)"
    if not extension:
        return "off (extension)"
    return "on"


def is_effective(repo: Path, *, runner=None) -> bool:
    machine = machine_state()
    binary = binary_path()
    project = project_state(repo)
    if project != "true" or not binary or machine != "on":
        return False
    return extension_connected(runner=runner, binary=binary)


def tools_listed(repo: Path, *, child: bool, runner=None) -> bool:
    """Parent-only BSK action tools. Hidden for children and when not effective."""
    return (not child) and is_effective(repo, runner=runner)


def bsk_status(repo: Path, *, runner=None) -> dict:
    """Read-only diagnostics, available even when action tools are hidden."""
    machine, binary, project = machine_state(), binary_path(), project_state(repo)
    extension = extension_connected(runner=runner, binary=binary)
    blockers = []
    if machine != "on":
        blockers.append("machine opt-in is " + machine + "; run rig browser-skill setup")
    if not binary:
        blockers.append("bsk is missing; run rig browser-skill setup")
    if project != "true":
        blockers.append("project is disabled; run rig browser-skill on in this repository")
    if not extension:
        blockers.append("extension is not connected; install the Chrome/Edge extension and leave Confirm before borrowing tabs ON")
    return {
        "machine": machine, "binary": binary, "project": project,
        "extension": "connected" if extension else "missing",
        "effective": not blockers, "blockers": blockers,
        "transport": "Rig MCP rig_bsk_session/rig_bsk_observe/rig_bsk_act/rig_bsk_confirm/rig_bsk_navigate/rig_bsk_tab",
        "next_action": "Resolve blockers, then restart parent/MCP tool discovery" if blockers else
                       "Use rig_bsk_session then rig_bsk_observe; if absent, restart parent/MCP tool discovery",
        "note": "No install, opt-in, or extension changes were made. Never run bsk install-skill. Do not bypass Rig MCP.",
    }


def _now() -> float:
    return time.monotonic()


def brief_block(ev: dict) -> str:
    addressed = ev.get("addressed") or {}
    lines = [
        "## BrowserSkill evidence",
        f"- action: {ev.get('action') or ''}",
        f"- effect: {ev.get('effect') or ''}",
        f"- snapshot: {ev.get('snapshot_id') or ''}",
        f"- session: {ev.get('session_id') or ''}",
        f"- addressed: ref={addressed.get('ref') or ''} label={addressed.get('label') or ''}",
        f"- before: {ev.get('before_png') or ''}",
        f"- after: {ev.get('after_png') or ''}",
        "Child must not click.",
    ]
    outline = str(ev.get("outline") or "").strip()
    if outline:
        clipped = "\n".join(outline.splitlines()[:12])
        lines.insert(-1, f"- outline:\n{clipped}")
    return "\n".join(lines) + "\n"


def empty_evidence(**overrides) -> dict:
    payload = {
        "ok": False,
        "effective": False,
        "action": "",
        "snapshot_id": "",
        "session_id": _SESSION.get("id") or "",
        "addressed": {"kind": "ref", "ref": "", "label": "", "index": None},
        "effect": "",
        "before_png": "",
        "after_png": "",
        "refs": [],
        "outline": "",
        "brief_block": "",
        "hint": "",
        "receipt": {},
        "receipt_path": "",
    }
    payload.update(overrides)
    if not payload.get("brief_block"):
        payload["brief_block"] = brief_block(payload)
    payload["receipt"] = normalize_receipt(payload)
    return payload


def _freshness(evidence: dict) -> str:
    explicit = str(evidence.get("snapshot_freshness") or "").strip()
    if explicit:
        return explicit
    effect = str(evidence.get("effect") or "")
    action = str(evidence.get("action") or "")
    if effect == "stale":
        return "unknown"
    if not str(evidence.get("snapshot_id") or "").strip():
        return "unknown"
    if action == "observe" and effect == "observed":
        return "fresh"
    if action in {"click", "fill", "press", "type", "key", "act"} and effect not in {"hidden", "refused", "stale", "error"}:
        return "confirm_required"
    if action == "confirm":
        return "fresh"
    return "unknown"


def _status(evidence: dict) -> str:
    effect = str(evidence.get("effect") or "")
    if effect == "stale":
        return "observe_required"
    if effect in {"hidden", "refused", "observed", "confirmed", "started", "stopped", "unverifiable", "navigated", "listed", "borrowed", "returned"}:
        return effect
    if evidence.get("ok"):
        return "ok"
    return "error"


def normalize_receipt(evidence: dict) -> dict:
    ev = evidence if isinstance(evidence, dict) else {}
    return {
        "version": RECEIPT_VERSION,
        "operation": str(ev.get("action") or ""),
        "status": _status(ev),
        "snapshot": {
            "id": str(ev.get("snapshot_id") or ""),
            "freshness": _freshness(ev),
        },
        "observation": {
            "outline": str(ev.get("outline") or ""),
            "refs": list(ev.get("refs") or []),
        },
        "effect": str(ev.get("effect") or ""),
        "hint": str(ev.get("hint") or ""),
        "brief_block": str(ev.get("brief_block") or ""),
    }


def persist_receipt(repo: Path, evidence: dict) -> dict:
    receipt = normalize_receipt(evidence)
    evidence["receipt"] = receipt
    dest = Path(repo) / EVIDENCE_REL
    try:
        dest.mkdir(parents=True, exist_ok=True)
        name = f"{receipt.get('operation') or 'bsk'}-{receipt['snapshot']['id'] or 'none'}.json"
        path = dest / name.replace("/", "_")
        path.write_text(json.dumps(receipt, indent=2) + "\n")
        evidence["receipt_path"] = str(path)
    except OSError:
        pass
    return evidence


def present_mcp(evidence: dict) -> dict:
    ev = dict(evidence) if isinstance(evidence, dict) else {}
    receipt = ev.get("receipt") if isinstance(ev.get("receipt"), dict) else normalize_receipt(ev)
    ev["receipt"] = receipt
    lines = [
        (
            f"{receipt.get('operation') or 'bsk'} {receipt.get('status') or ''} "
            f"snapshot={receipt.get('snapshot', {}).get('id') or ''} "
            f"effect={receipt.get('effect') or ''}"
        ).strip()
    ]
    brief = str(ev.get("brief_block") or "").strip()
    if brief:
        lines.append(brief)
    hint = str(ev.get("hint") or "").strip()
    if hint and hint not in brief:
        lines.append(hint)
    return {"content": [{"type": "text", "text": "\n".join(lines)}], "structuredContent": ev}


def _require_effective(repo: Path, action: str, *, runner=None) -> dict | None:
    if is_effective(repo, runner=runner):
        return None
    ev = empty_evidence(
        action=action, effect="hidden",
        hint="browser-skill is not effective; fallback chrome-devtools",
    )
    persist_receipt(repo, ev)
    return ev

def _fail(repo: Path, action: str, hint: str, *, effect: str = "error", **extra) -> dict:
    ev = empty_evidence(ok=False, effective=True, action=action, effect=effect, hint=hint, **extra)
    persist_receipt(repo, ev)
    return ev


def _command_failed(data, required: tuple[str, ...] = ()) -> str:
    if not isinstance(data, dict):
        return "malformed bsk output"
    if data.get("effect") == "refused":
        return str(data.get("hint") or "refused")
    if data.get("timeout"):
        return str(data.get("hint") or "bsk timed out")
    if data.get("malformed"):
        return str(data.get("hint") or "malformed bsk output")
    exit_code = data.get("exit_code")
    if isinstance(exit_code, int) and exit_code != 0:
        return str(data.get("hint") or f"bsk exited {exit_code}")
    if data.get("ok") is False:
        return str(data.get("hint") or "bsk returned ok:false")
    for field in required:
        if not str(data.get(field) or "").strip():
            return f"missing required field {field}"
    return ""


def _fail_from_data(repo: Path, action: str, data, *, required: tuple[str, ...] = (), **extra) -> dict | None:
    hint = _command_failed(data, required=required)
    if not hint:
        return None
    effect = "refused" if isinstance(data, dict) and data.get("effect") == "refused" else "error"
    return _fail(repo, action, hint, effect=effect, **extra)


def _active_session() -> str:
    return str(_SESSION.get("id") or "").strip()


def _scoped_args(args: list[str]) -> tuple[list[str] | None, str]:
    sid = _active_session()
    if not sid:
        return None, "session_required: start a session first"
    out = list(args)
    if "--session" not in out:
        out.extend(["--session", sid])
    return out, ""


def _session_id_from_data(data: dict) -> str:
    sid = str(data.get("session_id") or data.get("sessionId") or "").strip()
    if sid:
        return sid
    nested = data.get("session")
    if isinstance(nested, dict):
        return str(nested.get("id") or nested.get("session_id") or "").strip()
    if isinstance(nested, str):
        return nested.strip()
    return ""


def _store_snapshot(snapshot_id: str, refs: list[dict], png: str, outline: str) -> dict:
    by_ref = {}
    for row in refs:
        if not isinstance(row, dict):
            continue
        ref = str(row.get("ref") or "").strip()
        if ref:
            by_ref[ref] = row
    snap = {
        "refs": by_ref,
        "png": png,
        "outline": outline,
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
        before_png=snap.get("png") or "",
        effect="stale",
        hint=hint,
        snapshot_freshness=freshness,
        refs=list((snap.get("refs") or {}).values()),
        outline=str(snap.get("outline") or ""),
    )


def _require_snapshot(snapshot_id: str, *, action: str, need_phase: str) -> tuple[dict | None, dict | None]:
    sid = str(snapshot_id or "").strip()
    snap = _SNAPSHOTS.get(sid)
    if not snap:
        return None, _reject_snapshot(
            action, snapshot_id, None, "unknown",
            "observe_required: unknown snapshot",
        )
    created = float(snap.get("created_at") or 0)
    if _now() - created > SNAPSHOT_TTL_SEC:
        return snap, _reject_snapshot(
            action, sid, snap, "expired",
            "observe_required: snapshot expired",
        )
    phase = str(snap.get("phase") or PHASE_FRESH)
    if phase != need_phase:
        freshness = "consumed" if phase in {PHASE_CONFIRM, PHASE_DONE} else phase
        return snap, _reject_snapshot(
            action, sid, snap, freshness,
            "observe_required: snapshot not usable for this step",
        )
    return snap, None


def _refs_from_data(data: dict) -> list[dict]:
    rows = []
    raw = data.get("refs") if isinstance(data.get("refs"), list) else []
    if not raw and isinstance(data.get("elements"), list):
        raw = data["elements"]
    for index, item in enumerate(raw, start=1):
        if not isinstance(item, dict):
            continue
        ref = str(item.get("ref") or item.get("id") or "").strip()
        if not ref:
            ref = f"@e{index}"
        if not ref.startswith("@") and ref[:1].isdigit():
            ref = "@e" + ref
        if not ref.startswith("@"):
            ref = "@" + ref.lstrip("@")
        rows.append({
            "ref": ref,
            "label": str(item.get("label") or item.get("name") or item.get("text") or ""),
            "index": item.get("index") if item.get("index") is not None else index,
        })
    if rows:
        return rows
    outline = str(data.get("outline") or data.get("raw") or "")
    found = []
    token = ""
    for part in outline.replace(",", " ").split():
        if part.startswith("@e") or (part.startswith("@") and len(part) > 1):
            found.append(part.strip(".,;"))
    for index, ref in enumerate(found, start=1):
        rows.append({"ref": ref, "label": "", "index": index})
    return rows


def _png_from_data(data: dict) -> str:
    for key in (
        "screenshot_path", "screenshot_file_path", "png", "image_path",
        "path", "file", "out", "output",
    ):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    nested = data.get("screenshot")
    if isinstance(nested, dict):
        for key in ("path", "file", "out", "screenshot_path", "png"):
            value = nested.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    raw = str(data.get("raw") or "").strip()
    for line in reversed(raw.splitlines() or [raw]):
        token = line.strip().strip("'\"")
        if token.endswith(".png") and token and not any(ch.isspace() for ch in token):
            return token
    return ""


def _snapshot_id_from_data(data: dict, prefix: str) -> str:
    sid = str(data.get("snapshot_id") or data.get("id") or "").strip()
    return sid or f"{prefix}-{uuid.uuid4().hex[:10]}"


ACT_CMDS = {
    "click": "click",
    "fill": "fill",
    "type": "fill",
    "press": "press",
    "key": "press",
}


def bsk_session(repo: Path, action: str = "start", *, no_focus: bool = True, runner=None) -> dict:
    blocked = _require_effective(repo, "session", runner=runner)
    if blocked:
        return blocked
    act = (action or "start").strip().lower()
    if act not in {"start", "stop"}:
        ev = empty_evidence(
            ok=False, effective=True, action="session", effect="refused",
            hint="action must be start|stop",
        )
        persist_receipt(repo, ev)
        return ev
    if act == "start":
        raw_args = ["session", "start", "--json"]
        if no_focus:
            raw_args.append("--no-focus")
        data = invoke_bsk(raw_args, runner=runner)
        failed = _fail_from_data(repo, "session", data)
        if failed:
            return failed
        sid = _session_id_from_data(data)
        if not sid:
            return _fail(repo, "session", "missing required field session_id")
        _SESSION["id"] = sid
        _SESSION["active"] = "yes"
        hint = "session started"
        if no_focus:
            hint += " with --no-focus"
        hint += "; retained bsk session_id"
        ev = empty_evidence(
            ok=True, effective=True, action="session", effect="started",
            session_id=sid, hint=hint,
        )
        persist_receipt(repo, ev)
        return ev
    sid = _active_session()
    if not sid:
        return _fail(repo, "session", "session_required: start a session first")
    data = invoke_bsk(["session", "stop", sid], runner=runner)
    failed = _fail_from_data(repo, "session", data, session_id=sid)
    if failed:
        return failed
    _SESSION["id"] = ""
    _SESSION["active"] = ""
    ev = empty_evidence(
        ok=True, effective=True, action="session", effect="stopped",
        session_id=sid, hint="session stopped",
    )
    persist_receipt(repo, ev)
    return ev


def bsk_observe(repo: Path, *, runner=None) -> dict:
    blocked = _require_effective(repo, "observe", runner=runner)
    if blocked:
        return blocked
    args, hint = _scoped_args(["observe"])
    if hint:
        return _fail(repo, "observe", hint)
    data = invoke_bsk(args, runner=runner)
    failed = _fail_from_data(repo, "observe", data)
    if failed:
        return failed
    png = _png_from_data(data)
    if not png:
        shot_args, shot_hint = _scoped_args(["screenshot", "--json"])
        if not shot_hint:
            shot = invoke_bsk(shot_args, runner=runner)
            if not _command_failed(shot):
                png = _png_from_data(shot) or png
    refs = _refs_from_data(data)
    sid = _snapshot_id_from_data(data, "obs")
    outline = str(data.get("outline") or data.get("raw") or "")
    _store_snapshot(sid, refs, png, outline)
    ev = empty_evidence(
        ok=True, effective=True, action="observe", effect="observed",
        snapshot_id=sid, after_png=png, refs=refs, outline=outline,
        hint="act on a fresh @eN ref, then confirm",
    )
    persist_receipt(repo, ev)
    return ev


def bsk_act(
    repo: Path,
    snapshot_id: str = "",
    ref: str = "",
    action: str = "click",
    text: str = "",
    key: str = "",
    *,
    runner=None,
) -> dict:
    blocked = _require_effective(repo, action or "act", runner=runner)
    if blocked:
        return blocked
    act = (action or "click").strip().lower()
    if act in {"evaluate", "upload", "download"}:
        ev = empty_evidence(
            ok=False, effective=True, action=act, effect="refused",
            hint="evaluate/upload/download are forbidden",
        )
        persist_receipt(repo, ev)
        return ev
    cmd = ACT_CMDS.get(act)
    if not cmd:
        ev = empty_evidence(
            ok=False, effective=True, action=act, effect="refused",
            hint="action must be click|fill|press",
        )
        persist_receipt(repo, ev)
        return ev
    snap, reject = _require_snapshot(snapshot_id, action=act, need_phase=PHASE_FRESH)
    if reject:
        persist_receipt(repo, reject)
        return reject
    target = str(ref or "").strip()
    if not target or target not in (snap or {}).get("refs", {}):
        ev = _reject_snapshot(
            act, snapshot_id, snap, "unknown",
            "observe_required: missing or unknown ref",
        )
        persist_receipt(repo, ev)
        return ev
    row = snap["refs"][target]
    if cmd == "click":
        raw_args = ["click", target]
    elif cmd == "fill":
        payload = str(text or "")
        if not payload:
            return _fail(repo, act, "missing required field value", snapshot_id=snapshot_id)
        raw_args = ["fill", target, "--value", payload]
    else:
        if not str(key or ""):
            return _fail(repo, act, "missing required field key", snapshot_id=snapshot_id)
        raw_args = ["press", key, "--ref", target]
    args, hint = _scoped_args(raw_args)
    if hint:
        return _fail(repo, act, hint, snapshot_id=snapshot_id)
    data = invoke_bsk(args, runner=runner)
    addressed = {"kind": "ref", "ref": target, "label": row.get("label") or "", "index": row.get("index")}
    failed = _fail_from_data(
        repo, act, data, snapshot_id=snapshot_id, addressed=addressed,
        before_png=snap.get("png") or "",
    )
    if failed:
        return failed
    snap["phase"] = PHASE_CONFIRM
    png = _png_from_data(data) or snap.get("png") or ""
    effect = str(data.get("effect") or "unverifiable")
    ev = empty_evidence(
        ok=True, effective=True, action=act, effect=effect,
        snapshot_id=snapshot_id, after_png=png, before_png=snap.get("png") or "",
        addressed=addressed,
        hint="confirm with rig_bsk_confirm; Child must not click.",
        refs=list(snap["refs"].values()),
        outline=str(snap.get("outline") or ""),
    )
    persist_receipt(repo, ev)
    return ev


def bsk_confirm(repo: Path, snapshot_id: str = "", *, runner=None) -> dict:
    blocked = _require_effective(repo, "confirm", runner=runner)
    if blocked:
        return blocked
    snap, reject = _require_snapshot(snapshot_id, action="confirm", need_phase=PHASE_CONFIRM)
    if reject:
        persist_receipt(repo, reject)
        return reject
    args, hint = _scoped_args(["observe"])
    if hint:
        return _fail(repo, "confirm", hint, snapshot_id=snapshot_id)
    data = invoke_bsk(args, runner=runner)
    failed = _fail_from_data(repo, "confirm", data, snapshot_id=snapshot_id)
    if failed:
        return failed
    png = _png_from_data(data)
    if not png:
        shot_args, shot_hint = _scoped_args(["screenshot", "--json"])
        if not shot_hint:
            shot = invoke_bsk(shot_args, runner=runner)
            if not _command_failed(shot):
                png = _png_from_data(shot) or png
    refs = _refs_from_data(data) or list((snap or {}).get("refs", {}).values())
    successor = _snapshot_id_from_data(data, "obs")
    outline = str(data.get("outline") or (snap or {}).get("outline") or "")
    _store_snapshot(successor, refs, png, outline)
    if snap is not None:
        snap["phase"] = PHASE_DONE
    ev = empty_evidence(
        ok=True, effective=True, action="confirm", effect="confirmed",
        snapshot_id=successor, after_png=png, refs=refs, outline=outline,
        hint="confirmed; successor snapshot is fresh",
    )
    persist_receipt(repo, ev)
    return ev


def bsk_navigate(repo: Path, url: str = "", *, runner=None) -> dict:
    blocked = _require_effective(repo, "navigate", runner=runner)
    if blocked:
        return blocked
    target = str(url or "").strip()
    if not target:
        return _fail(repo, "navigate", "missing required field url")
    args, hint = _scoped_args(["navigate", target])
    if hint:
        return _fail(repo, "navigate", hint)
    data = invoke_bsk(args, runner=runner)
    failed = _fail_from_data(repo, "navigate", data)
    if failed:
        return failed
    _SNAPSHOTS.clear()
    ev = empty_evidence(
        ok=True, effective=True, action="navigate", effect="navigated",
        hint="navigate ok; observe for fresh refs",
    )
    persist_receipt(repo, ev)
    return ev


def bsk_tab(repo: Path, action: str = "list", tab_id: str = "", *, runner=None) -> dict:
    blocked = _require_effective(repo, "tab", runner=runner)
    if blocked:
        return blocked
    act = (action or "list").strip().lower()
    if act not in {"list", "borrow", "return"}:
        ev = empty_evidence(
            ok=False, effective=True, action="tab", effect="refused",
            hint="action must be list|borrow|return",
        )
        persist_receipt(repo, ev)
        return ev
    tid = str(tab_id or "").strip()
    if act == "list":
        raw_args = ["tab", "list", "--scope", "user"]
    elif act == "borrow":
        if not tid:
            return _fail(repo, "tab", "missing required field tab_id")
        raw_args = ["tab", "borrow", tid]
    else:
        if not tid:
            return _fail(repo, "tab", "missing required field tab_id")
        raw_args = ["tab", "return", tid]
    args, hint = _scoped_args(raw_args)
    if hint:
        return _fail(repo, "tab", hint)
    data = invoke_bsk(args, runner=runner)
    failed = _fail_from_data(repo, "tab", data)
    if failed:
        return failed
    effect = {"list": "listed", "borrow": "borrowed", "return": "returned"}[act]
    ev = empty_evidence(
        ok=True, effective=True, action="tab", effect=effect,
        hint=f"tab {act} ok",
    )
    persist_receipt(repo, ev)
    return ev


def session_line(binary: str) -> str:
    if not binary:
        return "(skipped)"
    try:
        data = invoke_bsk(["status", "--json"], timeout=10, binary=binary)
    except (OSError, subprocess.TimeoutExpired, FileNotFoundError):
        return "(skipped)"
    if extension_connected(binary=binary):
        return "extension connected"
    raw = str(data.get("raw") or "")
    line = raw.splitlines()[0] if raw else "extension missing"
    return line[:120]


def status_lines(repo: Path) -> list[str]:
    machine = machine_state()
    binary = binary_path()
    version = binary_version(binary)
    project = project_state(repo)
    extension = extension_connected(binary=binary)
    if version:
        binary_line = f"{version} (ok)"
    elif binary:
        binary_line = "bsk (ok)"
    else:
        binary_line = "(missing — rig browser-skill setup)"
    if project == "omitted":
        project_line = "(omitted = off)"
    else:
        project_line = f"enabled={project}"
    return [
        "Browser-skill",
        f"  machine:    {machine}",
        f"  binary:     {binary_line}",
        f"  project:    {project_line}",
        f"  extension:  {'connected' if extension else 'missing'}",
        f"  effective:  {effective_state(machine, bool(binary), project, extension)}",
        f"  session:    {session_line(binary)}",
        "  parent MCP: Rig proxy (raw bsk MCP is not required)",
        "  fallback:   chrome-devtools",
        "  never:      bsk install-skill",
    ]


def set_project_enabled(repo: Path, enabled: bool) -> None:
    path = harness.harness_path(repo)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.is_file():
        path.write_text("")
    install_ui.set_key(path, "browser-skill", "enabled", "true" if enabled else "false")


def cmd_status(repo: Path) -> int:
    print("\n".join(status_lines(repo)))
    return 0


def cmd_doctor(repo: Path) -> int:
    print("\n".join(status_lines(repo)))
    return 0


def cmd_on(repo: Path) -> int:
    set_project_enabled(repo, True)
    print("browser-skill enabled = true")
    if not binary_path():
        print("project on, binary missing — rig browser-skill setup")
    return 0


def cmd_off(repo: Path) -> int:
    set_project_enabled(repo, False)
    print("browser-skill enabled = false")
    return 0


def cmd_setup(repo: Path) -> int:
    bsk_install.main(["--browser-skill"])
    bsk_install.print_extension_urls()
    print("Use Rig MCP rig_bsk_status, then rig_bsk_session/rig_bsk_observe/rig_bsk_act/rig_bsk_confirm/rig_bsk_navigate/rig_bsk_tab.")
    print("Never run bsk install-skill. Raw bsk MCP is not required.")
    print("setup does not flip this repo [browser-skill] enabled. Per project: rig browser-skill on")
    if binary_path():
        print("next: fully quit the parent once, then rig browser-skill on in this repo")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="rig browser-skill")
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
