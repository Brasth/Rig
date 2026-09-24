#!/usr/bin/env python3
"""Smart routing policy: assessment, pick, launch tuple checks. Re-exports config/evidence."""
from __future__ import annotations

from pathlib import Path

import catalog as rig_catalog
import routing_profiles as rig_profiles
from routing_config import (  # noqa: F401
    POLICY_VERSION,
    ROUTING_JSON,
    ConfigError,
    RoutingConfig,
    config_fingerprint,
    doctor_lines,
    harness_mode,
    load_config,
    resolve_mode,
    routing_json_path,
    validate_profiles,
)
from routing_evidence import (  # noqa: F401
    SIDECAR_NAME,
    CatalogSession,
    empty_routing,
    explain_lines,
    legacy_routing,
    manual_routing,
    match_profile_model,
    read_sidecar,
    selected_profile_dict,
    sidecar_path,
    validate_launch_tuple,
    write_sidecar,
)

LEVELS = ("low", "medium", "high")
TIER_ORDER = ("fast", "standard", "strong")
ASSESSMENT_FIELDS = frozenset({"complexity", "risk", "uncertainty", "reason"})
ASSESSMENT_META = frozenset({"defaulted", "supplied"})
DIRECT_PARENT_ROLES = frozenset({"mini", "implement"})
EXECUTION_STRATEGIES = ("direct-parent", "wrapper", "parent-fallback", "stay", "none")
ROLE_DEFAULTS = {
    "explore": ("low", "low", "low"),
    "mini": ("low", "low", "low"),
    "bulk": ("low", "low", "low"),
    "implement": ("medium", "medium", "medium"),
    "hard": ("high", "medium", "high"),
    "review": ("high", "medium", "medium"),
    "verify": ("medium", "medium", "medium"),
}
CODES = (
    "selected",
    "not-evaluated",
    "worker-not-effective",
    "worker-disabled",
    "worker-cli-missing",
    "worker-mcp-unavailable",
    "worker-excluded",
    "worker-live-parent",
    "cursor-excluded",
    "banned-model",
    "role-incompatible",
    "tier-insufficient",
    "catalog-unconfirmed",
    "catalog-stale-refresh-failed",
    "catalog-empty",
    "catalog-miss",
    "catalog-unavailable",
    "review-same-provider",
    "review-unknown-provider",
    "review-unavailable",
    "outscored",
    "collapsed-transport",
)
_ROLE_TRAITS = {
    "implement": ("implementation",),
    "hard": ("architecture", "implementation"),
    "review": ("general",),
    "explore": ("general",),
    "mini": ("implementation",),
    "bulk": ("implementation",),
    "verify": ("tests",),
    "stay": ("general",),
}
_TRAIT_TERMS = (
    ("debugging", ("bug", "debug", "traceback", "exception", "broken", "failing")),
    ("tests", ("test", "pytest", "unittest", "coverage")),
    ("architecture", ("architect", "refactor", "boundary")),
    ("security", ("security", "auth", "vulnerab", "owasp", "secret", "permission", "credential")),
    ("performance", ("performance", "latency", "slow", "optimi", "benchmark")),
    ("migration", ("migrat", "upgrade")),
    ("documentation", ("docs", "readme", "document", "comment")),
    ("frontend", ("frontend", "css", "react", "layout", "component")),
    ("data", ("sql", "schema", "database", "query")),
    ("implementation", ("implement", "feature")),
)
_TIER_INDEX = {"fast": 0, "standard": 1, "strong": 2}
_REASONING_TARGET = {"fast": 40, "standard": 70, "strong": 95}
_SCORE_WEIGHTS = {
    "balanced": {"capability": 34, "tier": 30, "axis": 20, "preference": 10, "health": 6},
    "quality": {"capability": 25, "tier": 15, "axis": 45, "preference": 10, "health": 5},
    "speed": {"capability": 20, "tier": 30, "axis": 30, "preference": 10, "health": 10},
    "cost": {"capability": 20, "tier": 25, "axis": 35, "preference": 10, "health": 10},
}
_DIRECT_WORKER = {
    "xai": "grok",
    "anthropic": "claude",
    "openai": "codex",
    "google": "agy",
    "cognition": "devin",
    "cursor": "cursor",
}


def _level(value: str, name: str) -> str:
    raw = (value or "").strip().lower()
    if raw not in LEVELS:
        raise ValueError(f"{name} must be low|medium|high")
    return raw


def _require_assessment_str(value, name: str) -> str:
    if value is None:
        return ""
    if isinstance(value, bool) or not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    return value


def _is_normalized(data: dict) -> bool:
    if set(data) - (ASSESSMENT_FIELDS | ASSESSMENT_META):
        return False
    if not isinstance(data.get("defaulted"), list) or not isinstance(data.get("supplied"), bool):
        return False
    for name in ("complexity", "risk", "uncertainty"):
        val = data.get(name, "")
        if val not in LEVELS and val != "":
            return False
    reason = data.get("reason", "")
    if reason is None:
        reason = ""
    if not isinstance(reason, str):
        return False
    return True


def normalize_assessment(
    kind: str,
    assessment: dict | None = None,
    *,
    complexity: str = "",
    risk: str = "",
    uncertainty: str = "",
    reason: str = "",
) -> dict:
    if assessment is not None and not isinstance(assessment, dict):
        raise ValueError("assessment must be an object")
    incoming = dict(assessment) if assessment else {}
    extra_kwargs = any(str(item or "").strip() for item in (complexity, risk, uncertainty, reason))
    if incoming and _is_normalized(incoming) and not extra_kwargs:
        dimensions = {"complexity", "risk", "uncertainty"}
        if any(not isinstance(key, str) or key not in dimensions for key in incoming["defaulted"]):
            raise ValueError("assessment.defaulted must contain only assessment dimensions")
        if kind != "stay" and any(incoming.get(key) not in LEVELS for key in dimensions):
            raise ValueError("normalized assessment dimensions must be low|medium|high")
        if kind == "stay":
            return {
                "complexity": "",
                "risk": "",
                "uncertainty": "",
                "reason": str(incoming.get("reason") or "").strip(),
                "defaulted": list(incoming.get("defaulted") or []),
                "supplied": bool(incoming.get("supplied")),
            }
        return {
            "complexity": incoming.get("complexity") or "",
            "risk": incoming.get("risk") or "",
            "uncertainty": incoming.get("uncertainty") or "",
            "reason": str(incoming.get("reason") or "").strip(),
            "defaulted": list(incoming.get("defaulted") or []),
            "supplied": bool(incoming.get("supplied")),
        }
    extra = set(incoming) - ASSESSMENT_FIELDS
    if extra:
        raise ValueError(f"unknown assessment key {sorted(extra)[0]}")
    for key in ("complexity", "risk", "uncertainty", "reason"):
        if key in incoming:
            incoming[key] = _require_assessment_str(incoming.get(key), f"assessment.{key}")
    complexity = _require_assessment_str(complexity, "complexity")
    risk = _require_assessment_str(risk, "risk")
    uncertainty = _require_assessment_str(uncertainty, "uncertainty")
    reason = _require_assessment_str(reason, "reason")
    for key, supplied in (
        ("complexity", complexity),
        ("risk", risk),
        ("uncertainty", uncertainty),
        ("reason", reason),
    ):
        if supplied and incoming.get(key) and str(incoming.get(key)).strip() != str(supplied).strip():
            raise ValueError(f"assessment.{key} conflicts with {key}")
        if supplied and not str(incoming.get(key) or "").strip():
            incoming[key] = supplied
    defaults = ROLE_DEFAULTS.get(kind, ROLE_DEFAULTS["implement"])
    defaulted: list[str] = []
    values = {}
    for index, name in enumerate(("complexity", "risk", "uncertainty")):
        raw = str(incoming.get(name) or "").strip()
        if not raw:
            values[name] = "" if kind == "stay" else defaults[index]
            if kind != "stay":
                defaulted.append(name)
        else:
            values[name] = _level(raw, name)
            if kind == "stay":
                values[name] = ""
    reason_text = str(incoming.get("reason") or reason or "").strip()
    user_supplied = bool(
        reason_text
        or any(str(incoming.get(name) or "").strip() for name in ("complexity", "risk", "uncertainty"))
    )
    if kind == "stay":
        return {
            "complexity": "",
            "risk": "",
            "uncertainty": "",
            "reason": reason_text,
            "defaulted": [],
            "supplied": user_supplied,
        }
    return {
        "complexity": values["complexity"],
        "risk": values["risk"],
        "uncertainty": values["uncertainty"],
        "reason": reason_text,
        "defaulted": defaulted,
        "supplied": user_supplied or not defaulted,
    }


def required_tier(kind: str, assessment: dict) -> str:
    if kind in {"hard", "review"}:
        return "strong"
    dims = (assessment.get("complexity"), assessment.get("risk"), assessment.get("uncertainty"))
    if any(item == "high" for item in dims):
        return "strong"
    if kind == "verify":
        return "standard"
    if any(item == "medium" for item in dims):
        return "standard"
    return "fast"


def sufficient_tiers(need: str) -> tuple[str, ...]:
    if need not in TIER_ORDER:
        return ()
    start = TIER_ORDER.index(need)
    return TIER_ORDER[start:]


def _decision(pid: str, code: str, detail: str = "") -> dict:
    return {"id": pid, "code": code, "detail": detail}


def _finish_choice(choice: dict, routing: dict) -> dict:
    strategy = str(routing.get("execution_strategy") or "").strip()
    if strategy:
        choice["execution_strategy"] = strategy
    choice["routing"] = routing
    return choice


def assessment_all_low(assessment: dict | None) -> bool:
    data = assessment or {}
    return all(data.get(name) == "low" for name in ("complexity", "risk", "uncertainty"))


def direct_parent_eligible(
    kind: str,
    assessment: dict | None,
    cfg,
    live: str,
    blocked=None,
) -> bool:
    import route as rig_route

    live_parent = str(live or "").strip()
    blocked_set = set(blocked or [])
    return (
        getattr(cfg, "mode", "") == "smart"
        and bool(getattr(cfg, "direct_parent_low_risk", False))
        and kind in DIRECT_PARENT_ROLES
        and assessment_all_low(assessment)
        and live_parent in rig_route.NATIVE_PARENTS
        and live_parent not in blocked_set
        and "native" not in blocked_set
    )


def _worker_unavailable(profile: rig_profiles.Profile, repo: Path | None) -> tuple[str, str]:
    """Explain the first concrete gate that keeps a worker out of the pool."""
    if repo is None:
        return "worker-not-effective", "worker is not in the effective set supplied to this picker"
    import harness as rig_harness

    name = profile.worker
    enabled = rig_harness.parse_harness(rig_harness.harness_path(Path(repo)))["workers"].get(name) == "true"
    if not enabled:
        return "worker-disabled", f"enable with: rig workers {name}=on"
    if not rig_harness.find_worker_bin(name):
        if name == "mimo":
            return "worker-cli-missing", "install with: rig setup --mimo"
        return "worker-cli-missing", f"install the {name} CLI, then rerun rig pick"
    import child_mcp

    ready, reason = child_mcp.worker_mcp_ready(name)
    if not ready:
        return "worker-mcp-unavailable", reason or f"{name} job-scoped MCP is unavailable"
    return "worker-not-effective", "worker is not in this run's effective worker set"


def _hard_filter(profile: rig_profiles.Profile, *, live: str, blocked: set[str], wrapper_names: list[str], kind: str, repo: Path | None = None) -> tuple[str, str] | None:
    import route as rig_route

    if profile.worker == "cursor":
        return "cursor-excluded", "Cursor CLI has no isolated job-scoped MCP"
    if profile.worker == live:
        return "worker-live-parent", f"{live} is the live parent; choose a different child CLI"
    if profile.worker in blocked:
        return "worker-excluded", "excluded by --exclude; remove it to consider this worker"
    if profile.worker not in wrapper_names:
        return _worker_unavailable(profile, repo)
    banned = rig_route.assert_child_model(profile.selector)
    if not banned:
        for role in profile.roles:
            banned = rig_route.assert_devin_model(profile.worker, profile.selector, role)
            if banned:
                break
    if banned:
        return "banned-model", banned
    for alias in profile.aliases:
        banned = rig_route.assert_child_model(alias)
        if not banned:
            for role in profile.roles:
                banned = rig_route.assert_devin_model(profile.worker, alias, role)
                if banned:
                    break
        if banned:
            return "banned-model", banned
    if not profile.allows_role(kind):
        supported = ", ".join(profile.roles) or "none"
        return "role-incompatible", f"supports roles: {supported}"
    return None


def task_traits(kind: str, case: str) -> list[str]:
    """Explainable task labels. The raw case is not retained."""
    import re

    text = str(case or "").lower()
    found = set(_ROLE_TRAITS.get(kind, ("general",)))
    for trait, terms in _TRAIT_TERMS:
        for term in terms:
            if re.search(rf"(?<![a-z0-9]){re.escape(term)}", text):
                found.add(trait)
                break
    if found - {"general"}:
        found.discard("general")
    if not found:
        found.add("general")
    return [name for name in rig_profiles.TRAITS if name in found]


def _preference_list(cfg, kind: str, tier: str) -> list[str]:
    review = kind == "review"
    default_ids = rig_profiles.default_preference_ids(
        "strong" if review else tier,
        cfg.profiles,
        role="review" if review else "",
    )
    key = "review" if review else tier
    return rig_profiles.preference_order(cfg.preferences.get(key) or [], default_ids)


def _transport_health(profile: rig_profiles.Profile) -> int:
    if profile.worker == _DIRECT_WORKER.get(profile.provider):
        return 100
    if profile.worker in {"omp", "pi", "opencode", "cursor"}:
        return 40
    return 55


def _tier_fit(profile: rig_profiles.Profile, need: str, offered: str) -> int:
    distance = _TIER_INDEX[offered] - _TIER_INDEX[need]
    distance_score = max(0, 100 - distance * 45)
    alignment = 100 - abs(profile.capability.reasoning - _REASONING_TARGET[need])
    return (distance_score * 70 + alignment * 30) // 100


def _pref_score(rank: int) -> int:
    return max(0, 100 - rank * 8)


def _score_profile(profile: rig_profiles.Profile, *, traits, need: str, offered: str, rank: int, objective: str):
    weights = _SCORE_WEIGHTS[objective]
    capability = profile.capability.fit(traits)
    tier = _tier_fit(profile, need, offered)
    if objective == "speed":
        axis_name, axis = "speed", profile.capability.speed
    elif objective == "cost":
        axis_name, axis = "cost", profile.capability.cost
    else:
        axis_name, axis = "quality", profile.capability.quality
    preference = _pref_score(rank)
    health = _transport_health(profile)
    total = (
        capability * weights["capability"]
        + tier * weights["tier"]
        + axis * weights["axis"]
        + preference * weights["preference"]
        + health * weights["health"]
    )
    explanation = [
        f"capability={capability}",
        f"tier={tier}",
        f"{axis_name}={axis}",
        f"preference={preference}",
        f"health={health}",
    ]
    return total, explanation


def _offered_tier(profile: rig_profiles.Profile, need: str) -> str:
    for tier in sufficient_tiers(need):
        if profile.allows_tier(tier):
            return tier
    return ""


def _review_failure(model: str, review_ctx: dict | None):
    import route as rig_route

    _independence, rejection = rig_route._review_model(model, review_ctx or {})
    if not rejection:
        return None
    if "matches writer provider" in rejection:
        code = "review-same-provider"
    elif "known reviewer" in rejection:
        code = "review-unknown-provider"
    else:
        code = "review-unavailable"
    return code, rejection


def _stamp_picker(routing: dict, cfg, *, source: str, traits, fallback: str, selected_id: str = "", canonical=None) -> dict:
    from routing_evidence import PICKER_VERSION

    routing["picker"] = {
        "version": PICKER_VERSION,
        "engine": "jev" if source == "jev" else "local",
        "requested_engine": cfg.engine,
        "local_policy": cfg.local_policy,
        "objective": cfg.objective,
        "selection_source": source,
        "traits": list(traits),
        "fallback": fallback,
        "selected_profile_id": selected_id or "",
        "canonical": list(canonical or []),
    }
    return routing


def _select_ordered(cfg, kind: str, need: str, decisions: dict, session: CatalogSession, review_ctx, worker: str = ""):
    selected = None
    selected_tier = ""
    selected_model = ""
    catalog_meta = {"source": "none", "freshness": "n/a"}
    for tier in sufficient_tiers(need):
        order = _preference_list(cfg, kind, tier)
        for index, pid in enumerate(order):
            profile = cfg.profiles.get(pid)
            if profile is None or pid in decisions:
                continue
            if worker and profile.worker != worker:
                continue
            if not profile.allows_tier(tier):
                continue
            if profile.catalog_required:
                model, catalog_meta, failure = _catalog_model(profile, session)
                if failure:
                    decisions[profile.id] = _decision(profile.id, failure[0], failure[1])
                    continue
            else:
                catalog_meta = {"source": "pin", "freshness": "unverified"}
                model = profile.selector
            if kind == "review":
                failure = _review_failure(model, review_ctx)
                if failure:
                    decisions[profile.id] = _decision(profile.id, failure[0], failure[1])
                    continue
            selected = profile
            selected_tier = tier
            selected_model = model or profile.selector
            decisions[profile.id] = _decision(profile.id, "selected", selected_tier)
            canonical = [{
                "provider": profile.provider,
                "model": rig_profiles.canonical_model(selected_model),
                "effort": profile.effort,
                "profile_id": profile.id,
                "preference_rank": index,
                "explanation": ["ordered-v1 preference"],
                "transports": [profile.id],
            }]
            return selected, selected_tier, selected_model, catalog_meta, canonical
    return selected, selected_tier, selected_model, catalog_meta, []


def _select_scored(cfg, kind: str, need: str, decisions: dict, session: CatalogSession, review_ctx, traits, worker: str = ""):
    lists = {tier: _preference_list(cfg, kind, tier) for tier in sufficient_tiers(need)}
    pool = set()
    for order in lists.values():
        pool.update(order)
    required_order = lists.get(need, [])
    rank_of = {pid: index for index, pid in enumerate(required_order)}
    missing_rank = len(required_order)
    eligible = []
    for profile in sorted(cfg.profiles.values(), key=lambda item: item.id):
        if worker and profile.worker != worker:
            continue
        if profile.id in decisions or profile.id not in pool:
            continue
        offered = _offered_tier(profile, need)
        if not offered:
            decisions[profile.id] = _decision(profile.id, "tier-insufficient", need)
            continue
        if profile.catalog_required:
            model, meta, failure = _catalog_model(profile, session)
            if failure:
                decisions[profile.id] = _decision(profile.id, failure[0], failure[1])
                continue
        else:
            meta = {"source": "pin", "freshness": "unverified"}
            model = profile.selector
        if kind == "review":
            failure = _review_failure(model, review_ctx)
            if failure:
                decisions[profile.id] = _decision(profile.id, failure[0], failure[1])
                continue
        eligible.append({
            "profile": profile,
            "model": model or profile.selector,
            "tier": offered,
            "catalog": meta,
            "rank": rank_of.get(profile.id, missing_rank),
        })
    grouped: dict[tuple, list] = {}
    for row in eligible:
        profile = row["profile"]
        key = (profile.provider, rig_profiles.canonical_model(row["model"]), profile.effort)
        grouped.setdefault(key, []).append(row)
    ranked = []
    for key in sorted(grouped):
        rows = grouped[key]
        rows.sort(key=lambda item: (-_transport_health(item["profile"]), item["rank"], item["profile"].id))
        best = rows[0]
        score, explanation = _score_profile(
            best["profile"], traits=traits, need=need, offered=best["tier"],
            rank=best["rank"], objective=cfg.objective,
        )
        transports = sorted(
            (item["profile"].id for item in rows),
            key=lambda pid: (pid != best["profile"].id, pid),
        )
        ranked.append({
            "provider": key[0],
            "model": key[1],
            "effort": key[2],
            "profile_id": best["profile"].id,
            "score": score,
            "preference_rank": best["rank"],
            "explanation": explanation,
            "transports": transports,
            "best": best,
            "rows": rows,
        })
    ranked.sort(key=lambda row: (-row["score"], row["preference_rank"], row["profile_id"]))
    selected = None
    selected_tier = ""
    selected_model = ""
    catalog_meta = {"source": "none", "freshness": "n/a"}
    public = []
    for row in ranked:
        best = row.pop("best")
        members = row.pop("rows")
        public.append(row)
        if selected is None:
            selected = best["profile"]
            selected_tier = best["tier"]
            selected_model = best["model"]
            catalog_meta = best["catalog"]
            decisions[best["profile"].id] = _decision(best["profile"].id, "selected", selected_tier)
            for other in members:
                if other["profile"].id != best["profile"].id:
                    decisions[other["profile"].id] = _decision(
                        other["profile"].id, "collapsed-transport", best["profile"].id,
                    )
        else:
            for other in members:
                decisions[other["profile"].id] = _decision(other["profile"].id, "outscored", f"score={row['score']}")
    return selected, selected_tier, selected_model, catalog_meta, public


def _catalog_refresh_command(worker: str) -> str:
    if worker == "mimo":
        return "mimo models xiaomi --refresh"
    return f"{worker} models"


def _catalog_model(profile: rig_profiles.Profile, session: CatalogSession) -> tuple[str | None, dict, tuple[str, str] | None]:
    info = session.info(profile.worker)
    age = float(info.get("age_s") or 0)
    if info["state"] == "stale" and age > rig_catalog.STALE_MAX_SECONDS:
        info = session.info(profile.worker, require_fresh=True)
        age = float(info.get("age_s") or 0)
    catalog_meta = {"source": info.get("source") or "catalog", "freshness": info.get("freshness") or info.get("state") or "n/a"}
    refresh_command = _catalog_refresh_command(profile.worker)
    if info["state"] == "unavailable":
        return None, catalog_meta, ("catalog-unavailable", f"run `{refresh_command}` and confirm the CLI catalog is available")
    if info["state"] in {"unverified", "skipped"}:
        return None, catalog_meta, ("catalog-unconfirmed", f"catalog for {profile.worker} has not been confirmed; run `{refresh_command}`")
    if info.get("confirmed_empty") or info["state"] == "empty":
        return None, catalog_meta, ("catalog-empty", f"`{refresh_command}` returned no models")
    if info.get("ids") is None:
        return None, catalog_meta, ("catalog-unconfirmed", f"catalog for {profile.worker} has not been confirmed; run `{refresh_command}`")
    matched = match_profile_model(profile, info["ids"])
    if not matched:
        selectors = ", ".join(profile.model_keys())
        return None, catalog_meta, ("catalog-miss", f"catalog did not contain pinned selector {selectors}; check `rig doctor` and the worker model IDs")
    if info["state"] == "stale" and age > rig_catalog.STALE_MAX_SECONDS:
        return None, catalog_meta, ("catalog-stale-refresh-failed", f"catalog refresh failed; rerun `{refresh_command}` and pick again")
    return matched, catalog_meta, None


def _jev_choice(case: str, kind: str, assessment: dict, traits: list[str], canonical: list[dict]) -> tuple[str, str]:
    """Return a verified canonical profile id or a non-sensitive fallback code.

    The provider receives only the bounded task summary and canonical candidates.
    The result is never trusted until it maps back to this already hard-filtered pool.
    """
    try:
        import jev_provider

        candidates = [
            {
                "id": row["profile_id"],
                "provider": row["provider"],
                "effort": row["effort"],
                "capability": row.get("explanation", []),
            }
            for row in canonical
        ]
        reply = jev_provider.choice(
            summary=case, role=kind, assessment=assessment, traits=traits, choices=candidates,
        )
        selected_id = str(reply.get("id") or "").strip()
        if selected_id not in {row["profile_id"] for row in canonical}:
            return "", "jev-invalid-response"
        return selected_id, ""
    except Exception as exc:  # Provider faults always leave routing available.
        code = str(exc)
        if "credential-unavailable" in code:
            return "", "jev-credential-unavailable"
        if "candidate-limit" in code:
            return "", "jev-candidate-limit"
        return "", "jev-unavailable"


def _select_jev_candidate(
    selected_id: str, canonical: list[dict], cfg, session: CatalogSession, need: str,
) -> tuple[rig_profiles.Profile | None, str, str, dict]:
    row = next((item for item in canonical if item.get("profile_id") == selected_id), None)
    if row is None:
        return None, "", "", {"source": "none", "freshness": "n/a"}
    profile = cfg.profiles.get(selected_id)
    if profile is None:
        return None, "", "", {"source": "none", "freshness": "n/a"}
    if profile.catalog_required:
        model, meta, failure = _catalog_model(profile, session)
        if failure:
            return None, "", "", meta
    else:
        model, meta = profile.selector, {"source": "pin", "freshness": "unverified"}
    return profile, str(model or profile.selector), _offered_tier(profile, need), meta


def smart_pick(
    live: str,
    effective: list[str],
    role: str,
    case: str,
    *,
    catalogs: dict | None = None,
    exclude=None,
    parent_model: str = "",
    parent_effort: str = "",
    writer_job_id: str = "",
    writer_cli: str = "",
    writer_model: str = "",
    writer_provider: str = "",
    writer_job_ids=None,
    writer_snapshot_ids=None,
    writer_providers=None,
    review_mode: str = "standalone",
    repo: Path | None = None,
    jobs_snapshot: list[dict] | None = None,
    hash_cache: dict | None = None,
    assessment: dict | None = None,
    complexity: str = "",
    risk: str = "",
    uncertainty: str = "",
    assessment_reason: str = "",
    policy_mode: str | None = None,
    harness: dict | None = None,
) -> dict:
    import route as rig_route

    if review_mode not in {"standalone", "independent"}:
        raise ValueError("review_mode must be standalone or independent")
    cfg = load_config(repo, policy_mode=policy_mode or "smart", harness=harness)
    fingerprint = config_fingerprint(cfg)
    classification = rig_route.classify_details(role, case)
    kind = classification["kind"]
    assessed = normalize_assessment(
        kind, assessment, complexity=complexity, risk=risk, uncertainty=uncertainty, reason=assessment_reason,
    )
    blocked = rig_route.parse_exclude(exclude)
    actual_model = (parent_model or "").strip()
    actual_effort = (parent_effort or "").strip() if actual_model else ""
    review_ctx = None
    review_reason = ""
    if kind == "review":
        review_ctx, review_reason = rig_route._writer_context(
            writer_job_id=writer_job_id, writer_cli=writer_cli, writer_model=writer_model,
            writer_provider=writer_provider, review_mode=review_mode, repo=repo,
            jobs_snapshot=jobs_snapshot, hash_cache=hash_cache,
            writer_job_ids=writer_job_ids, writer_snapshot_ids=writer_snapshot_ids,
            writer_providers=writer_providers,
        )

    def base(**kwargs):
        return rig_route._base_choice(kind, kwargs.pop("worker", ""), kwargs.pop("spawn", ""), classification=classification, **kwargs)

    traits = task_traits(kind, case)
    # jev is a declared engine with no local runtime; selection stays on the local policy.
    fallback = "jev-unavailable" if cfg.engine == "jev" else ""
    routing = empty_routing(mode="smart", fingerprint=fingerprint, assessment=assessed)

    def finish(choice, source: str, selected_id: str = "", canonical=None):
        _stamp_picker(
            routing, cfg, source=source, traits=traits, fallback=fallback,
            selected_id=selected_id, canonical=canonical,
        )
        return _finish_choice(choice, routing)

    if kind == "stay":
        routing["execution_strategy"] = "stay"
        routing["parent_fit_limitations"] = (
            "stay uses the live parent; no catalog discovery; model is observed or unknown"
        )
        if not actual_model:
            routing["parent_fit_limitations"] += "; parent model/effort unverified"
        return finish(
            base(
                worker=live,
                spawn="stay",
                model=actual_model,
                effort=actual_effort,
                executor_kind="parent",
                model_source="observed" if actual_model else "unknown",
                reason=(
                    "parent keeps ask / plan / advise / vision / computer-use / chrome-profile. "
                    "Figma, computer-use, and chrome-profile stay with the parent."
                ),
            ),
            "stay",
        )

    need = required_tier(kind, assessed)
    routing["required_tier"] = need
    routing["review_recommendation"] = "independent" if assessed.get("risk") == "high" else "none"
    if direct_parent_eligible(kind, assessed, cfg, live, blocked):
        routing["execution_strategy"] = "direct-parent"
        routing["catalog"] = {"source": "none", "freshness": "n/a"}
        routing["parent_fit_limitations"] = (
            "opt-in direct parent writes for low-risk mini/implement; no catalog discovery"
        )
        if not actual_model:
            routing["parent_fit_limitations"] += "; parent model/effort unverified"
        return finish(
            base(
                worker=live,
                spawn="native",
                model=actual_model,
                effort=actual_effort,
                parent_writes=True,
                executor_kind="parent",
                model_source="observed" if actual_model else "unknown",
                reason=(
                    f"{kind}: this parent writes (direct-parent, low-risk opt-in). "
                    "MCP rig_job_start BEFORE editing: concrete files, access=write, executor_kind=parent; "
                    "retain ownership and finish authenticated. do not spawn a second same-CLI session."
                ),
            ),
            "direct-parent",
        )
    wrapper_names = [w for w in effective if w not in blocked and w != "cursor"]
    decisions: dict[str, dict] = {}
    session = CatalogSession(catalogs)

    for profile in sorted(cfg.profiles.values(), key=lambda item: item.id):
        filtered = _hard_filter(
            profile, live=live, blocked=blocked, wrapper_names=wrapper_names, kind=kind, repo=repo,
        )
        if filtered:
            decisions[profile.id] = _decision(profile.id, filtered[0], filtered[1])

    if review_reason and kind == "review" and review_mode == "independent":
        for profile in cfg.profiles.values():
            if profile.id not in decisions:
                decisions[profile.id] = _decision(profile.id, "review-unavailable", review_reason)
        routing["candidate_decisions"] = [decisions[pid] for pid in sorted(decisions)]
        routing["execution_strategy"] = "none"
        return finish(
            base(worker="", spawn="none", reason=review_reason, review={**(review_ctx or {}), "independence": "unavailable"}),
            "none",
        )

    if cfg.local_policy == "ordered-v1":
        selected, selected_tier, selected_model, catalog_meta, canonical = _select_ordered(
            cfg, kind, need, decisions, session, review_ctx,
        )
        selection_source = "ordered"
    else:
        selected, selected_tier, selected_model, catalog_meta, canonical = _select_scored(
            cfg, kind, need, decisions, session, review_ctx, traits,
        )
        selection_source = "scored"

    if selected and cfg.engine == "jev" and canonical:
        jev_id, jev_fallback = _jev_choice(case, kind, assessed, traits, canonical)
        if jev_id:
            jev_profile, jev_model, jev_tier, jev_catalog = _select_jev_candidate(
                jev_id, canonical, cfg, session, need,
            )
            if jev_profile and jev_tier:
                if jev_profile.id != selected.id:
                    decisions[selected.id] = _decision(selected.id, "outscored", "jev-selected")
                    decisions[jev_profile.id] = _decision(jev_profile.id, "selected", jev_tier)
                selected, selected_model, selected_tier, catalog_meta = (
                    jev_profile, jev_model, jev_tier, jev_catalog,
                )
                selection_source = "jev"
                fallback = ""
            else:
                fallback = "jev-catalog-invalid"
        else:
            fallback = jev_fallback

    for profile in cfg.profiles.values():
        if profile.id in decisions:
            continue
        if any(profile.allows_tier(tier) for tier in sufficient_tiers(need)):
            decisions[profile.id] = _decision(profile.id, "not-evaluated")
        else:
            decisions[profile.id] = _decision(profile.id, "tier-insufficient", need)

    routing["candidate_decisions"] = [decisions[pid] for pid in sorted(decisions)]
    routing["catalog"] = catalog_meta
    if selected:
        routing["execution_strategy"] = "wrapper"
        routing["selected_profile"] = selected_profile_dict(selected, model=selected_model, tier=selected_tier)
        effort = selected.effort
        reason = f"{kind}: {selected.worker} child {selected_model}" + (f" effort={effort}" if effort else "")
        if kind == "review":
            independence, _rejection = rig_route._review_model(selected_model, review_ctx or {})
            reason = f"review: {selected.worker} child {selected_model}" + (f" effort={effort}" if effort else "")
            return finish(
                base(
                    worker=selected.worker, spawn="run-worker", model=selected_model, effort=effort,
                    reason=reason, executor_kind="wrapper", model_source="selected",
                    review={**(review_ctx or {}), "independence": independence},
                ),
                selection_source,
                selected.id,
                canonical,
            )
        return finish(
            base(
                worker=selected.worker, spawn="run-worker", model=selected_model, effort=effort,
                native_agent="", reason=reason, parent_writes=False,
                executor_kind="wrapper", model_source="selected",
            ),
            selection_source,
            selected.id,
            canonical,
        )

    if kind in {"explore", "verify"}:
        routing["execution_strategy"] = "stay"
        routing["parent_fit_limitations"] = (
            f"no eligible {kind} child; parent stays read-only; no native child"
        )
        if not actual_model:
            routing["parent_fit_limitations"] += "; parent model/effort unverified"
        return finish(
            base(
                worker=live, spawn="stay", model=actual_model, effort=actual_effort,
                executor_kind="parent", model_source="observed" if actual_model else "unknown",
                reason=f"{kind}: no MCP-capable child; parent stays read-only. no native child.",
            ),
            "stay",
        )
    if kind == "review":
        reason = review_reason or "review needs a different vendor; no eligible reviewer"
        if "cursor" in {str(x).strip().lower() for x in effective} and "Cursor" not in reason:
            reason = "review needs a different vendor; Cursor has no isolated job-scoped MCP"
        routing["execution_strategy"] = "none"
        routing["parent_fit_limitations"] = "independent review unavailable without a different known provider"
        return finish(
            base(
                worker="", spawn="none", reason=reason,
                review={**(review_ctx or {}), "independence": "unavailable"},
            ),
            "none",
        )
    skip_native = bool(live) and (live in blocked or "native" in blocked)
    if kind in {"implement", "hard", "mini", "bulk"} and live in rig_route.NATIVE_PARENTS and not skip_native:
        routing["execution_strategy"] = "parent-fallback"
        routing["parent_fit_limitations"] = "no eligible wrapper; parent writes"
        if not actual_model:
            routing["parent_fit_limitations"] += "; parent model/effort unverified"
        return finish(
            base(
                worker=live, spawn="native", model=actual_model, effort=actual_effort,
                parent_writes=True, executor_kind="parent",
                model_source="observed" if actual_model else "unknown",
                reason=(
                    f"{kind}: this parent writes. "
                    "MCP rig_job_start BEFORE editing: concrete files, access=write, executor_kind=parent; "
                    "retain ownership and finish authenticated. do not spawn a second same-CLI session."
                ),
            ),
            "parent-fallback",
        )
    names = {str(x).strip().lower() for x in effective}
    if names and names <= {"cursor"}:
        reason = "Cursor CLI has no isolated job-scoped MCP; excluded until a safe --mcp-config exists"
    elif blocked:
        reason = "no effective worker after exclude; do not unlock a disabled worker."
    else:
        reason = "no effective worker; use cheaper same-CLI workers. That is success."
    routing["execution_strategy"] = "none"
    routing["parent_fit_limitations"] = reason
    return finish(base(worker="", spawn="none", reason=reason), "none")


def resolve_explicit_worker_choice(
    live: str,
    worker: str,
    role: str,
    case: str,
    *,
    repo: Path | None = None,
    assessment: dict | None = None,
    catalogs: dict | None = None,
    exclude=None,
    **kwargs,
) -> dict:
    """Pick an eligible smart profile restricted to one caller worker. No silent substitution."""
    import route as rig_route

    del live, kwargs
    worker = str(worker or "").strip()
    classification = rig_route.classify_details(role, case)
    kind = classification["kind"]
    cfg = load_config(repo, policy_mode="smart")
    fingerprint = config_fingerprint(cfg)
    assessed = normalize_assessment(kind, assessment)
    need = "" if kind == "stay" else required_tier(kind, assessed)
    blocked = rig_route.parse_exclude(exclude)
    session = CatalogSession(catalogs)
    decisions: dict[str, dict] = {}
    for profile in cfg.profiles.values():
        if profile.worker != worker:
            continue
        if profile.worker == "cursor":
            decisions[profile.id] = _decision(profile.id, "cursor-excluded", "Cursor CLI has no isolated job-scoped MCP")
            continue
        if profile.worker in blocked:
            decisions[profile.id] = _decision(profile.id, "worker-excluded")
            continue
        banned = rig_route.assert_child_model(profile.selector)
        if not banned:
            for allowed_role in profile.roles:
                banned = rig_route.assert_devin_model(profile.worker, profile.selector, allowed_role)
                if banned:
                    break
        if banned:
            decisions[profile.id] = _decision(profile.id, "banned-model", banned)
            continue
        if kind and kind != "stay" and not profile.allows_role(kind):
            decisions[profile.id] = _decision(profile.id, "role-incompatible")
    traits = task_traits(kind, case)
    fallback = "jev-unavailable" if cfg.engine == "jev" else ""
    if cfg.local_policy == "ordered-v1":
        selected, selected_tier, selected_model, catalog_meta, canonical = _select_ordered(
            cfg, kind, need, decisions, session, None, worker=worker,
        )
        source = "ordered"
    else:
        selected, selected_tier, selected_model, catalog_meta, canonical = _select_scored(
            cfg, kind, need, decisions, session, None, traits, worker=worker,
        )
        source = "scored"
    if selected is None:
        raise ValueError(f"no eligible smart profile for worker '{worker}'; re-pick")
    routing = empty_routing(mode="smart", fingerprint=fingerprint, assessment=assessed, required=need)
    routing["execution_strategy"] = "wrapper"
    routing["selected_profile"] = selected_profile_dict(selected, model=selected_model, tier=selected_tier)
    routing["catalog"] = catalog_meta
    routing["candidate_decisions"] = [decisions[pid] for pid in sorted(decisions)]
    _stamp_picker(
        routing, cfg, source=source, traits=traits, fallback=fallback,
        selected_id=selected.id, canonical=canonical,
    )
    return {
        "worker": selected.worker,
        "model": selected_model,
        "effort": selected.effort,
        "spawn": "run-worker",
        "executor_kind": "wrapper",
        "parent_writes": False,
        "execution_strategy": "wrapper",
        "routing": routing,
    }


def main() -> int:
    import argparse

    import jobs as rig_jobs

    parser = argparse.ArgumentParser(prog="routing_policy.py")
    parser.add_argument("cmd", choices=["doctor"])
    parser.add_argument("--repo", default="")
    args = parser.parse_args()
    repo = rig_jobs.repo_root(args.repo or None)
    if args.cmd == "doctor":
        lines = doctor_lines(repo)
        for line in lines:
            print(line)
        if any("error:" in line for line in lines):
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
