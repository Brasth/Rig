"""Validate parent-selected draft inputs using existing Rig contracts."""
from __future__ import annotations

import json

import acceptance_contract as contracts
import admission
import change_evidence
import context_packages as context
import verification

INPUT_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["task"],
    "properties": {
        "task": {"type": "string", "minLength": 1, "maxLength": 16384},
        "files": {"type": "array", "maxItems": 256, "items": {"type": "string"}},
        **{key: {"type": "array", "maxItems": 32, "items": {"type": "string"}}
           for key in ("constraints", "exclusions", "manual_criteria")},
        "references": context.SELECTION_SCHEMA["properties"]["files"],
        "checks": {"type": "array", "maxItems": 32, "items": {"type": "object",
            "additionalProperties": False, "required": ["id", "argv"], "properties": {
                "id": {"type": "string"}, "argv": {"type": "array", "minItems": 1,
                    "maxItems": 128, "items": {"type": "string"}},
                "cwd": {"type": "string"}, "description": {"type": "string"}}}},
        "recipe": {"enum": ["bugfix", "research-implement", "ui-validation"]},
        "research_sources": {"type": "array", "maxItems": 100, "items": {"type": "string"}},
    }}


def text(value, label, limit=2048):
    return context._text(value, label, limit).strip()


def json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate task preparation field")
        result[key] = value
    return result


def statements(value, label):
    if not isinstance(value, list) or len(value) > 32:
        raise ValueError(label + " must contain at most 32 statements")
    return [text(item, label) for item in value]


def paths(repo, value, label, limit=256):
    if not isinstance(value, list) or len(value) > limit:
        raise ValueError(label + " must be a bounded file list")
    # Keep writer paths literal and screen private locations; proposed leaves may be absent.
    selected = [context._relative(item) for item in value]
    for item in selected:
        context._scan(item, label)
    if len(set(selected)) != len(selected):
        raise ValueError("duplicate " + label)
    declared, canonical = admission.canonical_files(repo, selected)
    for item in canonical:
        context._relative(item)
    if set(selected) != set(declared) or set(declared) != set(canonical):
        raise ValueError(label + " aliases would expand scope; select literal target files explicitly")
    return declared


def normalize(repo, value):
    context._object(value, set(INPUT_SCHEMA["properties"]), "task preparation", {"task"})
    if len(json.dumps(value, allow_nan=False).encode()) > context.MAX_SPEC_BYTES:
        raise ValueError("task preparation exceeds 32 KiB")
    root = change_evidence.repository(repo)
    task = text(value["task"], "task", 16384)
    out = {"task": task, "files": paths(root, value.get("files", []), "files"),
           **{key: statements(value.get(key, []), key)
              for key in ("constraints", "exclusions", "manual_criteria")}}
    raw = value.get("checks", [])
    if not isinstance(raw, list) or len(raw) > 32:
        raise ValueError("checks must contain at most 32 exact commands")
    checks, ids = [], set()
    for row in raw:
        context._object(row, {"id", "argv", "cwd", "description"}, "check", {"id", "argv"})
        identifier = contracts.name(row["id"], "check ID")
        context._scan(identifier, "check ID")
        if identifier in ids:
            raise ValueError("duplicate check ID")
        ids.add(identifier)
        argv = verification._argv(row["argv"])
        if len(argv) > 128 or any(len(arg) > 8192 for arg in argv):
            raise ValueError("check command exceeds acceptance contract limits")
        for arg in argv:
            context._text(arg, "check argument", 8192, allow_blank=True)
        checks.append({"id": identifier, "argv": argv,
                       "cwd": change_evidence.normalize_cwd(root, row.get("cwd")),
                       "description": text(row.get("description", "Check " + identifier), "check description")})
    out["checks"] = checks
    refs = value.get("references", [])
    if not isinstance(refs, list) or len(refs) > context.MAX_FILES:
        raise ValueError("references exceed context selection limits")
    selected_refs = []
    for row in refs:
        context._object(row, {"path", "reason", "provenance"}, "reference", {"path", "reason"})
        path = context._relative(row["path"])
        context._scan(path, "reference path")
        selected_refs.append({"path": path, "reason": text(row["reason"], "reference reason", 512),
                              "provenance": text(row.get("provenance", "explicit parent selection"),
                                                 "reference provenance", 512)})
    if len({row["path"] for row in selected_refs}) != len(selected_refs):
        raise ValueError("duplicate context source")
    out["references"] = selected_refs
    recipe = value.get("recipe")
    if recipe is not None and recipe not in INPUT_SCHEMA["properties"]["recipe"]["enum"]:
        raise ValueError("unknown workflow recipe")
    out["recipe"] = recipe
    out["research_sources"] = paths(root, value.get("research_sources", []), "research_sources", 100)
    if out["research_sources"] and recipe != "research-implement":
        raise ValueError("research_sources requires research-implement recipe")
    return out
