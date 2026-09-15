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
}
CODES = (
    "selected",
    "not-evaluated",
    "worker-not-effective",
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
)


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


def _hard_filter(profile: rig_profiles.Profile, *, live: str, blocked: set[str], wrapper_names: list[str], kind: str) -> tuple[str, str] | None:
    import route as rig_route

    if profile.worker == "cursor":
        return "cursor-excluded", "Cursor CLI has no isolated job-scoped MCP"
    if profile.worker == live:
        return "worker-live-parent", ""
    if profile.worker in blocked:
        return "worker-excluded", ""
    if profile.worker not in wrapper_names:
        return "worker-not-effective", ""
    banned = rig_route.assert_child_model(profile.selector)
    if banned:
        return "banned-model", banned
    for alias in profile.aliases:
        banned = rig_route.assert_child_model(alias)
        if banned:
            return "banned-model", banned
    if not profile.allows_role(kind):
        return "role-incompatible", ""
    return None


def _catalog_model(profile: rig_profiles.Profile, session: CatalogSession) -> tuple[str | None, dict, tuple[str, str] | None]:
    info = session.info(profile.worker)
    age = float(info.get("age_s") or 0)
    if info["state"] == "stale" and age > rig_catalog.STALE_MAX_SECONDS:
        info = session.info(profile.worker, require_fresh=True)
        age = float(info.get("age_s") or 0)
    catalog_meta = {"source": info.get("source") or "catalog", "freshness": info.get("freshness") or info.get("state") or "n/a"}
    if info["state"] == "unavailable":
        return None, catalog_meta, ("catalog-unavailable", "")
    if info["state"] in {"unverified", "skipped"}:
        return None, catalog_meta, ("catalog-unconfirmed", "")
    if info.get("confirmed_empty") or info["state"] == "empty":
        return None, catalog_meta, ("catalog-empty", "")
    if info.get("ids") is None:
        return None, catalog_meta, ("catalog-unconfirmed", "")
    matched = match_profile_model(profile, info["ids"])
    if not matched:
        return None, catalog_meta, ("catalog-miss", "")
    if info["state"] == "stale" and age > rig_catalog.STALE_MAX_SECONDS:
        return None, catalog_meta, ("catalog-stale-refresh-failed", "")
    return matched, catalog_meta, None


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
        )

    def base(**kwargs):
        return rig_route._base_choice(kind, kwargs.pop("worker", ""), kwargs.pop("spawn", ""), classification=classification, **kwargs)

    routing = empty_routing(mode="smart", fingerprint=fingerprint, assessment=assessed)
    if kind == "stay":
        routing["execution_strategy"] = "stay"
        routing["parent_fit_limitations"] = (
            "stay uses the live parent; no catalog discovery; model is observed or unknown"
        )
        if not actual_model:
            routing["parent_fit_limitations"] += "; parent model/effort unverified"
        return _finish_choice(
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
            routing,
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
        return _finish_choice(
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
            routing,
        )
    wrapper_names = [w for w in effective if w not in blocked and w != "cursor"]
    decisions: dict[str, dict] = {}
    session = CatalogSession(catalogs)

    for profile in sorted(cfg.profiles.values(), key=lambda item: item.id):
        filtered = _hard_filter(
            profile, live=live, blocked=blocked, wrapper_names=wrapper_names, kind=kind,
        )
        if filtered:
            decisions[profile.id] = _decision(profile.id, filtered[0], filtered[1])

    if review_reason and kind == "review" and review_mode == "independent":
        for profile in cfg.profiles.values():
            if profile.id not in decisions:
                decisions[profile.id] = _decision(profile.id, "review-unavailable", review_reason)
        routing["candidate_decisions"] = [decisions[pid] for pid in sorted(decisions)]
        routing["execution_strategy"] = "none"
        return _finish_choice(
            base(worker="", spawn="none", reason=review_reason, review={**(review_ctx or {}), "independence": "unavailable"}),
            routing,
        )

    selected = None
    selected_tier = ""
    selected_model = ""
    catalog_meta = {"source": "none", "freshness": "n/a"}
    for tier in sufficient_tiers(need):
        pref_key = "review" if kind == "review" else tier
        default_ids = rig_profiles.default_preference_ids(
            "strong" if pref_key == "review" else tier,
            cfg.profiles,
            role="review" if kind == "review" else "",
        )
        mentioned = cfg.preferences.get(pref_key) or []
        order = rig_profiles.preference_order(mentioned, default_ids)
        for pid in order:
            profile = cfg.profiles.get(pid)
            if profile is None or pid in decisions:
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
                independence, rejection = rig_route._review_model(model, review_ctx or {})
                if rejection:
                    if "matches writer provider" in rejection:
                        code = "review-same-provider"
                    elif "known reviewer" in rejection:
                        code = "review-unknown-provider"
                    else:
                        code = "review-unavailable"
                    decisions[profile.id] = _decision(profile.id, code, rejection)
                    continue
            selected = profile
            selected_tier = tier
            selected_model = model or profile.selector
            decisions[profile.id] = _decision(profile.id, "selected", selected_tier)
            break
        if selected:
            break

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
            return _finish_choice(
                base(
                    worker=selected.worker, spawn="run-worker", model=selected_model, effort=effort,
                    reason=reason, executor_kind="wrapper", model_source="selected",
                    review={**(review_ctx or {}), "independence": independence},
                ),
                routing,
            )
        return _finish_choice(
            base(
                worker=selected.worker, spawn="run-worker", model=selected_model, effort=effort,
                native_agent="", reason=reason, parent_writes=False,
                executor_kind="wrapper", model_source="selected",
            ),
            routing,
        )

    if kind == "explore":
        routing["execution_strategy"] = "stay"
        routing["parent_fit_limitations"] = "no eligible explore child; parent stays read-only; no native child"
        if not actual_model:
            routing["parent_fit_limitations"] += "; parent model/effort unverified"
        return _finish_choice(
            base(
                worker=live, spawn="stay", model=actual_model, effort=actual_effort,
                executor_kind="parent", model_source="observed" if actual_model else "unknown",
                reason="explore: no MCP-capable child; parent stays read-only. no native child.",
            ),
            routing,
        )
    if kind == "review":
        reason = review_reason or "review needs a different vendor; no eligible reviewer"
        if "cursor" in {str(x).strip().lower() for x in effective} and "Cursor" not in reason:
            reason = "review needs a different vendor; Cursor has no isolated job-scoped MCP"
        routing["execution_strategy"] = "none"
        routing["parent_fit_limitations"] = "independent review unavailable without a different known provider"
        return _finish_choice(
            base(
                worker="", spawn="none", reason=reason,
                review={**(review_ctx or {}), "independence": "unavailable"},
            ),
            routing,
        )
    skip_native = bool(live) and (live in blocked or "native" in blocked)
    if kind in {"implement", "hard", "mini", "bulk"} and live in rig_route.NATIVE_PARENTS and not skip_native:
        routing["execution_strategy"] = "parent-fallback"
        routing["parent_fit_limitations"] = "no eligible wrapper; parent writes"
        if not actual_model:
            routing["parent_fit_limitations"] += "; parent model/effort unverified"
        return _finish_choice(
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
            routing,
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
    return _finish_choice(base(worker="", spawn="none", reason=reason), routing)


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
        if banned:
            decisions[profile.id] = _decision(profile.id, "banned-model", banned)
            continue
        if kind and kind != "stay" and not profile.allows_role(kind):
            decisions[profile.id] = _decision(profile.id, "role-incompatible")
    selected = None
    selected_tier = ""
    selected_model = ""
    catalog_meta = {"source": "none", "freshness": "n/a"}
    for tier in sufficient_tiers(need):
        pref_key = "review" if kind == "review" else tier
        default_ids = rig_profiles.default_preference_ids(
            "strong" if pref_key == "review" else tier,
            cfg.profiles,
            role="review" if kind == "review" else "",
        )
        mentioned = cfg.preferences.get(pref_key) or []
        order = rig_profiles.preference_order(mentioned, default_ids)
        for pid in order:
            profile = cfg.profiles.get(pid)
            if profile is None or profile.worker != worker or pid in decisions:
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
            selected = profile
            selected_tier = tier
            selected_model = model or profile.selector
            decisions[profile.id] = _decision(profile.id, "selected", selected_tier)
            break
        if selected:
            break
    if selected is None:
        raise ValueError(f"no eligible smart profile for worker '{worker}'; re-pick")
    routing = empty_routing(mode="smart", fingerprint=fingerprint, assessment=assessed, required=need)
    routing["execution_strategy"] = "wrapper"
    routing["selected_profile"] = selected_profile_dict(selected, model=selected_model, tier=selected_tier)
    routing["catalog"] = catalog_meta
    routing["candidate_decisions"] = [decisions[pid] for pid in sorted(decisions)]
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
