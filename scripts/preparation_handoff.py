"""Structured parent handoff: optional findings, decisions, changes and readiness.

Pure data validation and rendering. No repository discovery, execution,
admission, routing, or authority. Callers supply file existence facts.
"""
from __future__ import annotations

import json
import shlex

import context_packages as context
import worker_brief

LEVELS = ("low", "medium", "high")
FINDING_STATUSES = ("verified", "hypothesis")
MAX_ROWS = 32
MAX_CHANGES = 256
MAX_EVIDENCE = 8
_LOCATION = {"type": "object", "additionalProperties": False, "required": ["path"], "properties": {
    "path": {"type": "string", "maxLength": 512},
    "symbol": {"type": "string", "maxLength": 256},
    "line": {"type": "integer", "minimum": 1}}}
SCHEMA = {
    "findings": {"type": "array", "maxItems": MAX_ROWS, "items": {
        "type": "object", "additionalProperties": False, "required": ["statement", "status"],
        "properties": {"statement": {"type": "string", "maxLength": 2048},
                       "status": {"enum": list(FINDING_STATUSES)},
                       "evidence": {"type": "array", "maxItems": MAX_EVIDENCE, "items": _LOCATION}}}},
    "decisions": {"type": "array", "maxItems": MAX_ROWS, "items": {
        "type": "object", "additionalProperties": False, "required": ["choice", "reason"],
        "properties": {"choice": {"type": "string", "maxLength": 2048},
                       "reason": {"type": "string", "maxLength": 2048}}}},
    "changes": {"type": "array", "maxItems": MAX_CHANGES, "items": {
        "type": "object", "additionalProperties": False, "required": ["path", "change"],
        "properties": {"path": {"type": "string", "maxLength": 512},
                       "symbol": {"type": "string", "maxLength": 256},
                       "change": {"type": "string", "maxLength": 2048}}}},
    "reading_order": {"type": "array", "maxItems": MAX_ROWS, "items": {
        "type": "object", "additionalProperties": False, "required": ["path", "reason"],
        "properties": {"path": {"type": "string", "maxLength": 512},
                       "symbol": {"type": "string", "maxLength": 256},
                       "reason": {"type": "string", "maxLength": 512}}}},
    "unknowns": {"type": "array", "maxItems": MAX_ROWS, "items": {"type": "string", "maxLength": 2048}},
    "remaining_work": {"enum": list(LEVELS),
                       "description": "Parent estimate of work left after these findings: low|medium|high."},
}
FIELDS = tuple(SCHEMA)
ESCALATION = (
    "Use the findings above as the starting point. Read the affected code and validate each assumption "
    "before editing; treat hypotheses as unconfirmed. If the code contradicts a finding or needed data is "
    "missing, broaden reading only within the admitted files and references. Report contradictions, scope "
    "or contract problems through rig_job_coordination_request (or rig_job_ask) and stop that part. "
    "Do not re-route yourself, expand scope, retry, or cancel work."
)


def _text(value, label, limit=2048):
    return context._text(value, label, limit).strip()


def _symbol(value):
    symbol = _text(value, "symbol", 256)
    if "\n" in symbol or "\r" in symbol:
        raise ValueError("symbol must be a single line")
    return symbol


def _path(value, label, writers):
    # Writer files are already literal admitted paths; other paths use context screening.
    if isinstance(value, str) and value in writers:
        return value
    path = context._relative(value)
    context._scan(path, label)
    return path


def _rows(value, label, limit=MAX_ROWS):
    if not isinstance(value, list) or len(value) > limit:
        raise ValueError(f"{label} must be a list of at most {limit} entries")
    return value


def normalize(value, writer_files):
    """Validate optional structured fields. Absent fields stay absent; [] stays explicit."""
    out = {}
    writers = set(writer_files)
    if "findings" in value:
        findings = []
        for row in _rows(value["findings"], "findings"):
            context._object(row, {"statement", "status", "evidence"}, "finding", {"statement", "status"})
            status = row["status"]
            if status not in FINDING_STATUSES:
                raise ValueError("finding status must be verified|hypothesis")
            evidence = []
            for item in _rows(row.get("evidence", []), "finding evidence", MAX_EVIDENCE):
                context._object(item, {"path", "symbol", "line"}, "evidence location", {"path"})
                location = {"path": _path(item["path"], "evidence path", writers)}
                if "symbol" in item:
                    location["symbol"] = _symbol(item["symbol"])
                if "line" in item:
                    if type(item["line"]) is not int or item["line"] < 1:
                        raise ValueError("evidence line must be a positive integer")
                    location["line"] = item["line"]
                evidence.append(location)
            if status == "verified" and not evidence:
                raise ValueError("verified findings require evidence locations")
            findings.append({"statement": _text(row["statement"], "finding"), "status": status,
                             "evidence": evidence})
        out["findings"] = findings
    if "decisions" in value:
        decisions = []
        for row in _rows(value["decisions"], "decisions"):
            context._object(row, {"choice", "reason"}, "decision", {"choice", "reason"})
            decisions.append({"choice": _text(row["choice"], "decision"),
                              "reason": _text(row["reason"], "decision reason")})
        out["decisions"] = decisions
    if "changes" in value:
        changes = []
        for row in _rows(value["changes"], "changes", MAX_CHANGES):
            context._object(row, {"path", "symbol", "change"}, "change", {"path", "change"})
            path = row["path"]
            if not isinstance(path, str) or path not in writers:
                raise ValueError("changes must name declared writer files")
            entry = {"path": path}
            if "symbol" in row:
                entry["symbol"] = _symbol(row["symbol"])
            entry["change"] = _text(row["change"], "intended change")
            changes.append(entry)
        out["changes"] = changes
    if "reading_order" in value:
        reading = []
        for row in _rows(value["reading_order"], "reading_order"):
            context._object(row, {"path", "symbol", "reason"}, "reading entry", {"path", "reason"})
            path = _path(row["path"], "reading path", writers)
            entry = {"path": path}
            if "symbol" in row:
                entry["symbol"] = _symbol(row["symbol"])
            entry["reason"] = _text(row["reason"], "reading reason", 512)
            reading.append(entry)
        out["reading_order"] = reading
    if "unknowns" in value:
        out["unknowns"] = [_text(item, "unknown") for item in _rows(value["unknowns"], "unknowns")]
    if "remaining_work" in value:
        if value["remaining_work"] not in LEVELS:
            raise ValueError("remaining_work must be low|medium|high")
        out["remaining_work"] = value["remaining_work"]
    return out


def evidence_paths(draft):
    """Non-writer repository paths whose bytes the handoff relies upon."""
    paths = {row["path"] for row in draft.get("references", [])}
    for finding in draft.get("findings", []):
        paths.update(item["path"] for item in finding["evidence"])
    paths.update(row["path"] for row in draft.get("reading_order", []))
    return sorted(paths - set(draft.get("files", [])))


def readiness(draft, *, ready, existing):
    """Execution readiness gaps. `existing` is the set of present writer files."""
    gaps = []
    if not ready:
        gaps.append({"code": "draft-not-ready", "message": "Resolve the unresolved draft items first."})
    changed = {row["path"] for row in draft.get("changes", [])}
    for path in draft.get("files", []):
        if path not in changed:
            gaps.append({"code": "change-missing", "message": f"Describe the intended change for {path}."})
    read = {row["path"] for row in draft.get("reading_order", [])}
    for path in draft.get("files", []):
        if path in existing and path not in read:
            gaps.append({"code": "entrypoint-missing",
                         "message": f"Add {path} to reading_order with its entrypoint or symbol."})
    if "decisions" not in draft:
        gaps.append({"code": "decisions-missing",
                     "message": "State decisions, or decisions: [] when no decision is needed."})
    if "unknowns" not in draft:
        gaps.append({"code": "unknowns-missing", "message": "State unknowns: [] once nothing remains unknown."})
    elif draft["unknowns"]:
        gaps.append({"code": "unknowns-open", "message": "Resolve the listed unknowns before low-effort execution."})
    if not draft.get("checks") and not draft.get("manual_criteria"):
        gaps.append({"code": "acceptance-missing", "message": "Supply exact checks or manual acceptance criteria."})
    return not gaps, gaps


def _location(row):
    text = row["path"]
    if row.get("line"):
        text += f":{row['line']}"
    if row.get("symbol"):
        text += f" ({row['symbol']})"
    return text


def render(draft):
    """Readable worker brief. Deterministic for identical normalized drafts."""
    out = [worker_brief.PREAMBLE, "", "## Goal", draft["task"]]
    if draft.get("findings"):
        out += ["", "## Findings"]
        for row in draft["findings"]:
            label = "verified" if row["status"] == "verified" else "hypothesis - validate before relying on it"
            out.append(f"- [{label}] {row['statement']}")
            if row["evidence"]:
                out.append("  Evidence: " + "; ".join(_location(item) for item in row["evidence"]))
    if "decisions" in draft:
        out += ["", "## Decisions"]
        out += [f"- {row['choice']} (reason: {row['reason']})" for row in draft["decisions"]] or [
            "- No design decision is required."]
    if draft.get("changes"):
        out += ["", "## Changes"]
        for row in draft["changes"]:
            target = row["path"] + (f" [{row['symbol']}]" if row.get("symbol") else "")
            out.append(f"- {target}: {row['change']}")
    if draft.get("reading_order"):
        out += ["", "## Reading order"]
        for index, row in enumerate(draft["reading_order"], 1):
            target = row["path"] + (f" [{row['symbol']}]" if row.get("symbol") else "")
            out.append(f"{index}. {target} - {row['reason']}")
    out += ["", "## Boundaries", "Files to modify: " + (", ".join(draft["files"]) or "(none selected)")]
    for label, key in (("Constraint", "constraints"), ("Do not change", "exclusions")):
        out += [f"- {label}: {item}" for item in draft.get(key, [])]
    if "unknowns" in draft:
        out += [f"- Unknown: {item}" for item in draft["unknowns"]] or ["- Unknowns: none declared."]
    if draft.get("remaining_work"):
        out.append(f"- Parent estimate of remaining work: {draft['remaining_work']}")
    if draft.get("checks") or draft.get("manual_criteria"):
        out += ["", "## Acceptance"]
        for check in draft.get("checks", []):
            out.append(f"- Required check {check['id']}: {shlex.join(check['argv'])}")
            out.append(f"  argv={json.dumps(check['argv'], ensure_ascii=False)} cwd={json.dumps(check['cwd'])}")
        out += [f"- Manual: {item}" for item in draft.get("manual_criteria", [])]
    out += ["", "## Escalation", ESCALATION]
    return "\n".join(out)
