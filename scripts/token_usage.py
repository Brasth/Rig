#!/usr/bin/env python3
"""Normalize optional worker token usage. Never estimate or invent totals."""
from __future__ import annotations

import json
from pathlib import Path

FIELDS = ("input", "output", "reasoning", "cached_input", "total")
SOURCES = frozenset({"wrapper", "parent", "native_child"})
ALIASES = {
    "input": ("input", "input_tokens", "prompt_tokens"),
    "output": ("output", "output_tokens", "completion_tokens"),
    "reasoning": ("reasoning", "reasoning_tokens", "thinking_tokens"),
    "cached_input": (
        "cached_input",
        "cached_input_tokens",
        "cache_read_input_tokens",
        "cache_read_tokens",
    ),
    "total": ("total", "total_tokens"),
}
FINAL_TYPES = frozenset({"result", "end"})


class UsageError(ValueError):
    """Malformed token usage. Callers must omit usage rather than guess."""


def _nonneg_int(value, name: str) -> int:
    if type(value) is not int or isinstance(value, bool) or value < 0:
        raise UsageError(f"{name} must be a non-negative integer")
    return value


def normalize_token_usage(value) -> dict | None:
    """Return reported canonical fields, None if absent, or raise UsageError if malformed.

    Any subset of input/output/reasoning/cached_input/total is valid. Missing
    fields stay omitted; total is never derived.
    """
    if value in (None, ""):
        return None
    if not isinstance(value, dict) or isinstance(value, bool):
        raise UsageError("token_usage must be an object")
    out: dict[str, int] = {}
    for field in FIELDS:
        names = ALIASES[field]
        present = [name for name in names if name in value]
        if not present:
            continue
        parsed = [_nonneg_int(value[name], f"token_usage.{field}") for name in present]
        if any(item != parsed[0] for item in parsed[1:]):
            raise UsageError(f"token_usage.{field} is ambiguous")
        out[field] = parsed[0]
    if not out:
        raise UsageError("token_usage has no recognized fields")
    return out


def load_token_usage(value) -> dict | None:
    try:
        return normalize_token_usage(value)
    except UsageError:
        return None


def usage_source(value, *, default: str = "") -> str:
    """Return validated provenance. Empty means unknown, never inferred."""
    raw = default
    if isinstance(value, dict) and "source" in value:
        raw = value.get("source")
    if raw in (None, ""):
        return ""
    if raw not in SOURCES:
        raise UsageError("token_usage.source must be wrapper|parent|native_child")
    if default and default not in (None, "") and default != raw:
        raise UsageError("token_usage.source conflicts with executor")
    return raw


def _payload(event: dict):
    has_usage = "usage" in event
    has_token = "token_usage" in event
    if not has_usage and not has_token:
        return None, False
    payloads = []
    for key in ("token_usage", "usage"):
        if key not in event:
            continue
        raw = event.get(key)
        if raw in (None, ""):
            continue
        payloads.append(raw)
    if not payloads:
        return None, False
    normalized = []
    for raw in payloads:
        try:
            item = normalize_token_usage(raw)
        except UsageError:
            return None, True
        if item is None:
            return None, True
        normalized.append(item)
    first = normalized[0]
    if any(item != first for item in normalized[1:]):
        return None, True
    return first, False


def iter_events(raw: str):
    text = (raw or "").strip()
    if not text:
        return
    if text.startswith("["):
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            data = None
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    yield item
            return
    lines = text.splitlines()
    if len(lines) == 1 and text.startswith("{"):
        try:
            obj = json.loads(text)
        except json.JSONDecodeError:
            obj = None
        if isinstance(obj, dict):
            yield obj
            return
        if isinstance(obj, list):
            for item in obj:
                if isinstance(item, dict):
                    yield item
            return
    for line in lines:
        blob = line.strip()
        if not blob.startswith("{"):
            continue
        try:
            obj = json.loads(blob)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            yield obj


def _is_final_event(event: dict) -> bool:
    kind = event.get("type")
    if kind not in FINAL_TYPES:
        return False
    if kind == "end":
        reason = event.get("stopReason")
        return isinstance(reason, str) and bool(reason.strip())
    return True


def usage_from_events(events) -> dict | None:
    """Persist usage only from one unambiguous final structured event."""
    found: list[dict] = []
    invalid = False
    for event in events or []:
        if not isinstance(event, dict):
            continue
        if not _is_final_event(event):
            continue
        payload, bad = _payload(event)
        if bad:
            invalid = True
            continue
        if payload:
            found.append(payload)
    if invalid:
        return None
    if not found:
        return None
    first = found[0]
    if any(item != first for item in found[1:]):
        return None
    return first


def usage_from_text(raw: str) -> dict | None:
    return usage_from_events(list(iter_events(raw)))


def usage_from_job_dir(job_dir: Path) -> dict | None:
    path = Path(job_dir) / "stdout.log"
    if not path.is_file():
        return None
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    return usage_from_text(raw)
