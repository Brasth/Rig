#!/usr/bin/env python3
"""Rig-wide OpenCode cheap-model policy shared by every routing and launch path.

OpenCode children may only run the exact OpenCode Go selectors below, for the
fast/standard roles below, with no `--variant`. This never reads or edits the
user's global OpenCode configuration; it only constrains what Rig will launch.
"""
from __future__ import annotations

WORKER = "opencode"
TRANSPORT_PREFIX = "opencode-go/"

# (profile id, exact selector, canonical provider), in default preference order.
# The third entry is region dependent; the catalog decides whether it is usable.
PROFILES = (
    ("opencode-glm-5.3-flash", "opencode-go/glm-5.3-flash", "zai"),
    ("opencode-deepseek-v4.1-flash", "opencode-go/deepseek-v4.1-flash", "deepseek"),
    ("opencode-muse-spark-1.3-contributor", "opencode-go/muse-spark-1.3-contributor", "meta"),
)
APPROVED = tuple(selector for _pid, selector, _provider in PROFILES)
DEFAULT_MODEL = APPROVED[0]
PROFILE_IDS = tuple(pid for pid, _selector, _provider in PROFILES)
PROFILE_ORDER = {pid: index for index, pid in enumerate(PROFILE_IDS)}

# Fast/standard roles only. Hard and review never run on OpenCode.
ROLES = ("explore", "mini", "bulk", "implement", "verify")
UNSUPPORTED_ROLES = frozenset({"hard", "review"})
EFFORT = ""

REMOVED_PROFILE_IDS = frozenset({
    "opencode-gpt-5.4-mini-minimal",
    "opencode-gpt-5.6-luna-high",
    "opencode-gpt-5.6-terra-max",
})
REMOVED_SELECTORS = frozenset({
    "openai/gpt-5.4-mini", "gpt-5.4-mini",
    "openai/gpt-6-luna", "gpt-6-luna", "openai/gpt-5.6-luna", "gpt-5.6-luna",
    "openai/gpt-5.6-terra", "gpt-5.6-terra",
})


def approved_text() -> str:
    return ", ".join(APPROVED)


def selector_for(profile_id: str) -> str:
    for pid, selector, _provider in PROFILES:
        if pid == profile_id:
            return selector
    return ""


def removed_profile_problem(profile_id: str) -> str | None:
    """Actionable migration error for pre-policy OpenCode profile IDs."""
    if str(profile_id or "").strip() not in REMOVED_PROFILE_IDS:
        return None
    return (
        f"OpenCode profile '{profile_id}' was removed by the OpenCode cheap-model policy; "
        f"replace it with one of: {', '.join(PROFILE_IDS)} (models: {approved_text()})"
    )


def model_problem(model: str) -> str | None:
    """Exact-selector check. Empty or unsupported values never fall back to a default."""
    raw = str(model or "").strip()
    if not raw:
        return (
            "OpenCode requires an explicit approved model (an empty model would use the OpenCode default); "
            f"approved: {approved_text()}"
        )
    if raw in APPROVED:
        return None
    if raw.lower() in REMOVED_SELECTORS:
        return (
            f"OpenCode model '{raw}' was removed by the OpenCode cheap-model policy; "
            f"migrate to one of: {approved_text()}"
        )
    return f"refuse OpenCode model '{raw}'; exact OpenCode Go selectors only: {approved_text()}"


def role_problem(kind: str) -> str | None:
    if str(kind or "").strip() in UNSUPPORTED_ROLES:
        return (
            f"OpenCode does not support {kind} jobs; it runs explore|mini|bulk|implement|verify only. "
            "Choose another worker."
        )
    return None


def effort_problem(effort: str) -> str | None:
    if str(effort or "").strip():
        return "OpenCode takes no reasoning effort (no --variant); leave effort empty"
    return None


def problem(worker: str, model: str, role: str = "", effort: str | None = None) -> str | None:
    """Single gate for manual launches, MCP/native admission, and routing profiles."""
    if str(worker or "").strip() != WORKER:
        return None
    if role:
        import route

        found = role_problem(route.classify(role, ""))
        if found:
            return found
    found = model_problem(model)
    if found:
        return found
    if effort is not None:
        return effort_problem(effort)
    return None


def catalog_problem(model: str, info: dict | None = None) -> str | None:
    """Confirm the exact selector in the OpenCode catalog; missing auth/model means unavailable."""
    import catalog as rig_catalog

    if info is None:
        info = rig_catalog.load_catalog_info(WORKER)
        if info.get("state") == "stale" and float(info.get("age_s") or 0) > rig_catalog.STALE_MAX_SECONDS:
            info = rig_catalog.load_catalog_info(WORKER, require_fresh=True)
    refresh = "`opencode models`"
    state = info.get("state")
    if state == "unavailable":
        return f"OpenCode unavailable: catalog could not be read (CLI missing, not authenticated, or probe failed); check {refresh}"
    if state in {"unverified", "skipped"} or info.get("ids") is None:
        return f"OpenCode unavailable: catalog is unconfirmed; run {refresh}"
    if state == "empty" or info.get("confirmed_empty") or not info["ids"]:
        return f"OpenCode unavailable: {refresh} returned no models (check authentication)"
    wanted = str(model or "").strip().lower()
    if wanted not in {str(item).strip().lower() for item in info["ids"]}:
        return (
            f"OpenCode unavailable: catalog does not list exact selector '{model}' "
            f"(not authenticated or not offered in your region); check {refresh}"
        )
    return None
