#!/usr/bin/env python3
"""Explicit task domains and capability boundaries, independent of job roles/tiers.

Domain policy chooses among already eligible profiles. It never installs tools,
expands permissions, or grants a child browser/vision access.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

DOMAINS = ("general", "ui-design", "frontend", "ui-verification", "research", "backend", "debugging", "review")
PARENT_DOMAINS = frozenset({"ui-design", "ui-verification"})
FALLBACKS = ("scored", "parent", "none")
DOMAIN_TRAITS = {
    "frontend": ("frontend",), "backend": ("implementation",),
    "debugging": ("debugging",), "research": ("general",),
    "review": ("general",), "ui-design": ("frontend",), "ui-verification": ("tests", "frontend"),
}
# Conservative, bounded fallback. The parent should pass a semantic domain for
# ambiguous/multilingual requests; these rules never change an explicit role.
_RULES = (
    ("ui-verification", r"\b(?:visual (?:verification|qa|test(?:ing)?)|(?:verify|inspect|check) (?:the )?(?:ui|layout|screenshot)|browser test(?:ing)?)\b"),
    ("ui-design", r"\b(?:(?:ui|ux|visual) design|design (?:the )?(?:ui|ux)|figma|wireframe)\b"),
    ("research", r"\b(?:research|literature review|compare sources)\b"),
    ("frontend", r"\b(?:frontend|front-end|ui|css|react|layout|component)\b"),
    ("debugging", r"\b(?:debug(?:ging)?|bug|traceback|exception|broken|failing)\b"),
    ("backend", r"\b(?:backend|back-end|api|database|sql|server)\b"),
)


def normalize_domain(value: str = "") -> str:
    if not isinstance(value, str):
        raise ValueError("task_domain must be a string")
    value = value.strip().lower()
    if value and value not in DOMAINS:
        raise ValueError("task_domain must be " + "|".join(DOMAINS))
    return value


def reject_legacy(task_domain: str = "", research_sources=None) -> None:
    if normalize_domain(task_domain) or research_sources is not None:
        raise ValueError("task domains/research sources require smart routing; use --policy-mode smart")


def classify_domain(task_domain: str, kind: str, case: str) -> dict:
    name = normalize_domain(task_domain)
    if name:
        return {"name": name, "source": "explicit", "rule": "explicit:" + name}
    # A review stage can still be explicitly frontend/backend, but never loses
    # its existing role/provider restrictions through domain inference.
    if kind == "review":
        return {"name": "review", "source": "inferred", "rule": "role:review"}
    for name, pattern in _RULES:
        if re.search(pattern, str(case or "").lower()):
            return {"name": name, "source": "inferred", "rule": "keyword:" + name}
    return {"name": "general", "source": "default", "rule": "default:general"}


def normalize_source_inputs(sources) -> list[str] | None:
    """Normalize spelling/deduplicate without filesystem access (workflow-safe)."""
    if sources is None:
        return None
    if not isinstance(sources, list) or any(not isinstance(s, str) or not s.strip() for s in sources):
        raise ValueError("research_sources must be an array of nonempty repository-relative file paths")
    if len(sources) > 100:
        raise ValueError("research_sources supports at most 100 files")
    for source in sources:
        path = Path(source)
        if path.is_absolute() or ".." in path.parts or re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", source):
            raise ValueError("research source must be repository-relative, not a URL: " + source)
    return list(dict.fromkeys(Path(source).as_posix() for source in sources))


def validate_sources(repo: Path | None, sources) -> list[str]:
    """Require existing, readable, repo-contained regular files, not URLs/tools."""
    sources = normalize_source_inputs(sources) or []
    if not sources:
        return []
    if repo is None:
        raise ValueError("research_sources requires a repository")
    root = Path(repo).resolve()
    result = []
    for source in sources:
        path = Path(source)
        if path.is_absolute() or ".." in path.parts or "://" in source:
            raise ValueError("research source must be repository-relative: " + source)
        try:
            resolved = (root / path).resolve()
        except (OSError, RuntimeError) as exc:
            raise ValueError("research source has an invalid path or symlink: " + source) from exc
        if not resolved.is_relative_to(root) or not resolved.is_file():
            raise ValueError("research source is missing or outside the repository: " + source)
        if not os.access(resolved, os.R_OK):
            raise ValueError("research source is not readable: " + source)
        try:
            # Verify actual access without retaining file contents in evidence.
            with resolved.open("rb") as stream:
                stream.read(1)
        except OSError as exc:
            raise ValueError("research source is not readable: " + source) from exc
        normalized = path.as_posix()
        if normalized not in result:
            result.append(normalized)
    return result


def parse_policies(raw, profiles: dict) -> dict:
    if not isinstance(raw, dict):
        raise ValueError("domains must be an object keyed by task domain")
    result = {}
    for name, value in raw.items():
        if name not in DOMAINS:
            raise ValueError("unknown domain policy: " + str(name))
        if not isinstance(value, dict) or set(value) - {"preferred_profiles", "fallback"}:
            raise ValueError("domains." + name + " accepts preferred_profiles and fallback only")
        preferred = value.get("preferred_profiles", [])
        if not isinstance(preferred, list) or any(not isinstance(p, str) or p not in profiles for p in preferred):
            raise ValueError("domains." + name + ".preferred_profiles must contain known profile IDs")
        if len(preferred) != len(set(preferred)):
            raise ValueError("domains." + name + ".preferred_profiles cannot contain duplicates")
        fallback = value.get("fallback", "scored")
        if fallback not in FALLBACKS:
            raise ValueError("domains." + name + ".fallback must be scored|parent|none")
        if name in PARENT_DOMAINS and (preferred or fallback != "parent"):
            raise ValueError("domains." + name + " is parent-only; use empty preferred_profiles and fallback=parent")
        result[name] = {"preferred_profiles": list(preferred), "fallback": fallback}
    return result


def context(cfg, kind: str, case: str, task_domain: str = "", research_sources=None, repo: Path | None = None) -> dict:
    domain = classify_domain(task_domain, kind, case)
    name = domain["name"]
    sources = validate_sources(repo, research_sources)
    if sources and name != "research":
        raise ValueError("research_sources requires task_domain=research")
    if name == "review" and kind not in {"review", "stay"}:
        raise ValueError("task_domain=review requires role=review (domain never bypasses reviewer restrictions)")
    configured = name in cfg.domains
    policy = cfg.domains.get(name, {})
    preferred = list(policy.get("preferred_profiles", []))
    fallback = policy.get("fallback", "parent" if name in PARENT_DOMAINS else "scored")
    requirements = []
    parent_reason = ""
    if name in PARENT_DOMAINS:
        requirements = ["parent-vision-browser"]
        parent_reason = "UI design/inspection stays with the parent; no child vision, Figma, browser or computer-use access"
    elif name == "research":
        requirements = ["read-only", "local-source-files"]
        if kind != "explore" or not sources:
            parent_reason = "research requires role=explore and verified local source files for a child; source acquisition stays with the parent"
    return {
        **domain, "policy": "domains." + name if configured else "builtin:" + name,
        "preferred_profiles": preferred, "fallback": fallback,
        "requirements": requirements, "research_sources": sources,
        "parent_only": bool(parent_reason), "reason": parent_reason or "eligible profiles retain role, tier, tool and provider gates",
        "selection": "pending",
    }


def evidence_ok(raw) -> bool:
    if not isinstance(raw, dict):
        return False
    string_fields = ("name", "source", "rule", "policy", "fallback", "reason", "selection")
    if any(not isinstance(raw.get(key), str) for key in string_fields):
        return False
    if raw["name"] not in DOMAINS or raw["source"] not in {"explicit", "inferred", "default"} or raw["fallback"] not in FALLBACKS:
        return False
    expected_rules = {
        "explicit": {"explicit:" + raw["name"]},
        "inferred": {"keyword:" + raw["name"]} | ({"role:review"} if raw["name"] == "review" else set()),
        "default": {"default:general"} if raw["name"] == "general" else set(),
    }
    if raw["rule"] not in expected_rules[raw["source"]]:
        return False
    if type(raw.get("parent_only")) is not bool:
        return False
    return all(isinstance(raw.get(key), list) and all(isinstance(v, str) for v in raw[key])
               for key in ("preferred_profiles", "requirements", "research_sources"))


def resolve_inputs(task_domain: str = "", research_sources=None, routing=None) -> tuple[str, list[str] | None]:
    """Inherit a recorded semantic choice without accepting conflicting scalars."""
    name = normalize_domain(task_domain)
    research_sources = normalize_source_inputs(research_sources)
    raw = routing.get("task_domain") if isinstance(routing, dict) else None
    if raw is None:
        return name, research_sources
    if not evidence_ok(raw):
        raise ValueError("routing task_domain evidence is invalid; re-pick")
    if name and name != raw["name"]:
        raise ValueError("task_domain conflicts with routing metadata; re-pick")
    if research_sources is not None and research_sources != raw["research_sources"]:
        raise ValueError("research_sources conflicts with routing metadata; re-pick")
    return raw["name"], list(raw["research_sources"])
