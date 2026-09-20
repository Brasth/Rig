#!/usr/bin/env python3
"""Normalize CUA evidence into a versioned Rig receipt and MCP image content.

Pure presentation. Does not call Cua Driver or persist screenshot bytes.
"""
from __future__ import annotations

import base64
import hashlib
from pathlib import Path

RECEIPT_VERSION = "rig.cu.v1"
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
PNG_MIME = "image/png"
ACT_OPERATIONS = frozenset({"click", "type", "key"})
ESCALATE = frozenset({"escalate_px", "escalate_foreground"})
STATUS_EFFECTS = frozenset({
    "hidden", "refused", "captured", "confirmed", "recorded", "stopped",
    "unverifiable",
}) | ESCALATE


def selected_png(evidence: dict) -> str:
    if not isinstance(evidence, dict):
        return ""
    for key in ("after_png", "before_png"):
        value = evidence.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def read_png(path: str) -> tuple[bytes, str] | None:
    """Return (bytes, sha256 hex) only when path is a readable PNG."""
    raw = str(path or "").strip()
    if not raw:
        return None
    try:
        data = Path(raw).read_bytes()
    except OSError:
        return None
    if len(data) < 8 or data[:8] != PNG_MAGIC:
        return None
    return data, hashlib.sha256(data).hexdigest()


def image_meta(evidence: dict) -> dict:
    loaded = read_png(selected_png(evidence))
    if loaded is None:
        return {"available": False, "mime": "", "sha256": ""}
    _data, digest = loaded
    return {"available": True, "mime": PNG_MIME, "sha256": digest}


def image_content(evidence: dict) -> dict | None:
    """MCP image content entry, or None when the PNG is missing/unreadable."""
    loaded = read_png(selected_png(evidence))
    if loaded is None:
        return None
    data, _digest = loaded
    return {
        "type": "image",
        "mimeType": PNG_MIME,
        "data": base64.b64encode(data).decode("ascii"),
    }


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
    if action == "capture" and effect == "captured":
        return "fresh"
    if action in ACT_OPERATIONS and effect not in {"hidden", "refused", "stale"}:
        return "confirm_required"
    if action == "confirm":
        return "fresh"
    return "unknown"


def _status(evidence: dict) -> str:
    effect = str(evidence.get("effect") or "")
    if effect == "stale":
        return "capture_required"
    if effect in STATUS_EFFECTS:
        return effect
    if evidence.get("ok"):
        return "ok"
    return "error"


def _next_action(evidence: dict, status: str) -> str:
    if status in {"capture_required", "stale"}:
        return "capture"
    if status in {"hidden", "refused", "error"}:
        return ""
    action = str(evidence.get("action") or "")
    if action == "capture" and evidence.get("ok"):
        return "act"
    if action in ACT_OPERATIONS and evidence.get("ok"):
        return "confirm"
    if action == "confirm" and str(evidence.get("effect") or "") == "confirmed":
        return "act"
    if action == "record_start" and evidence.get("ok"):
        return "stop"
    return ""


def _target(evidence: dict) -> dict:
    addressed = evidence.get("addressed")
    if isinstance(addressed, dict):
        return {
            "kind": addressed.get("kind") or "",
            "element_token": addressed.get("element_token") or "",
            "index": addressed.get("index"),
            "label": addressed.get("label") or "",
            "ref": addressed.get("ref") or "",
            "x": addressed.get("x"),
            "y": addressed.get("y"),
        }
    return {
        "kind": "",
        "element_token": "",
        "index": None,
        "label": "",
        "ref": "",
        "x": None,
        "y": None,
    }


def _elements(evidence: dict) -> list:
    rows = evidence.get("elements")
    if not isinstance(rows, list):
        return []
    return [item for item in rows if isinstance(item, dict)]


def normalize(evidence: dict) -> dict:
    """Versioned rig.cu.v1 receipt. Always valid, even when the PNG is missing."""
    ev = evidence if isinstance(evidence, dict) else {}
    status = _status(ev)
    snapshot_id = str(ev.get("snapshot_id") or "")
    return {
        "version": RECEIPT_VERSION,
        "operation": str(ev.get("action") or ""),
        "status": status,
        "snapshot": {
            "id": snapshot_id,
            "freshness": _freshness(ev),
            "coord_space": str(ev.get("coord_space") or ""),
        },
        "observation": {
            "outline": str(ev.get("outline") or ""),
            "elements": _elements(ev),
        },
        "target": _target(ev),
        "effect": str(ev.get("effect") or ""),
        "next_action": _next_action(ev, status),
        "image": image_meta(ev),
        "brief_block": str(ev.get("brief_block") or ""),
    }


def summary(evidence: dict, receipt: dict | None = None) -> str:
    ev = evidence if isinstance(evidence, dict) else {}
    rec = receipt if isinstance(receipt, dict) else normalize(ev)
    snapshot = rec.get("snapshot") if isinstance(rec.get("snapshot"), dict) else {}
    lines = [
        (
            f"{rec.get('operation') or 'cu'} {rec.get('status') or ''} "
            f"snapshot={snapshot.get('id') or ''} "
            f"effect={rec.get('effect') or ''} "
            f"next={rec.get('next_action') or ''}"
        ).strip()
    ]
    brief = str(ev.get("brief_block") or rec.get("brief_block") or "").strip()
    if brief:
        lines.append(brief)
    hint = str(ev.get("hint") or "").strip()
    if hint and hint not in brief:
        lines.append(hint)
    return "\n".join(lines)


def present_mcp(evidence: dict) -> dict:
    """Concise text + structured receipt (with legacy evidence keys) + optional image."""
    ev = dict(evidence) if isinstance(evidence, dict) else {}
    receipt = normalize(ev)
    structured = dict(ev)
    structured["receipt"] = receipt
    content = [{"type": "text", "text": summary(ev, receipt)}]
    image = image_content(ev)
    if image is not None:
        content.append(image)
    return {"content": content, "structuredContent": structured}
