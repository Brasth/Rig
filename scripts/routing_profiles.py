#!/usr/bin/env python3
"""Declared routing profiles. Selectors come from route.MODELS at runtime."""
from __future__ import annotations

from dataclasses import dataclass, fields

ROLES = ("explore", "mini", "bulk", "implement", "hard", "review", "verify")
TIERS = ("fast", "standard", "strong")
PREF_KEYS = ("fast", "standard", "strong", "review")
TRAITS = (
    "implementation",
    "debugging",
    "tests",
    "architecture",
    "security",
    "performance",
    "migration",
    "documentation",
    "frontend",
    "data",
    "general",
)
_MODEL_PREFIXES = ("xai-oauth/", "openai/", "anthropic/", "xai/", "google/", "cursor/", "xiaomi/")
WRITER_ROLES = ("mini", "bulk", "implement", "hard")
FAST_STANDARD_WORKERS = ("grok", "claude", "codex", "devin", "mimo", "opencode", "omp", "pi", "agy")
STRONG_REVIEW_WORKERS = ("devin", "claude", "codex", "grok", "opencode", "omp", "pi", "agy")
CATALOG_WORKERS = frozenset({"opencode", "omp", "pi", "agy", "devin", "mimo"})
KNOWN_PROVIDERS = frozenset({"openai", "anthropic", "xai", "google", "cursor", "cognition", "xiaomi"})
DEVIN_FAST_ROLES = ("explore", "mini", "bulk")
DEVIN_IMPLEMENT_ROLES = ("implement", "verify")
DEVIN_STRONG_ROLES = ("hard", "review")
CHEAP_ROLES = ("explore", "mini", "bulk", "implement")
WRITE_ROLES = ("mini", "bulk", "implement", "hard", "review")
STANDARD_ROLES = ("explore", "mini", "bulk", "implement", "verify")
STRONG_ROLES = ("explore", "mini", "bulk", "implement", "hard", "review", "verify")
STRONG_NO_REVIEW = ("explore", "mini", "bulk", "implement", "hard", "verify")


@dataclass(frozen=True)
class Capability:
    """Task-fit metadata. Omitted custom profiles keep these unspecialized defaults."""

    implementation: int = 50
    debugging: int = 50
    tests: int = 50
    architecture: int = 50
    security: int = 50
    performance: int = 50
    migration: int = 50
    documentation: int = 50
    frontend: int = 50
    data: int = 50
    general: int = 50
    quality: int = 50
    speed: int = 50
    cost: int = 50
    reasoning: int = 50

    def fit(self, traits: tuple[str, ...] | list[str]) -> int:
        names = [name for name in traits if name in TRAITS]
        if not names:
            return self.general
        return sum(getattr(self, name) for name in names) // len(names)


CAPABILITY_FIELDS = tuple(item.name for item in fields(Capability))
SAFE_CAPABILITY = Capability()


def capability_dict(cap: Capability) -> dict:
    return {name: int(getattr(cap, name)) for name in CAPABILITY_FIELDS}


def canonical_model(selector: str) -> str:
    text = str(selector or "").strip().lower()
    for prefix in _MODEL_PREFIXES:
        if text.startswith(prefix):
            return text[len(prefix):]
    return text


@dataclass(frozen=True)
class Profile:
    id: str
    worker: str
    selector: str
    aliases: tuple[str, ...]
    roles: tuple[str, ...]
    tiers: tuple[str, ...]
    effort: str
    supported_efforts: tuple[str, ...]
    provider: str
    catalog_required: bool
    capability: Capability = SAFE_CAPABILITY

    def allows_role(self, role: str) -> bool:
        return role in self.roles

    def allows_tier(self, tier: str) -> bool:
        return tier in self.tiers

    def model_keys(self) -> tuple[str, ...]:
        keys = (self.selector, *self.aliases)
        return tuple(k for k in keys if k)


def _pin(worker: str, kind: str) -> tuple[str, str]:
    import route as rig_route

    return rig_route.model_for(worker, kind)


def _p(
    pid: str,
    worker: str,
    selector: str,
    *,
    aliases: tuple[str, ...] = (),
    roles: tuple[str, ...] = CHEAP_ROLES,
    tiers: tuple[str, ...] = ("fast",),
    effort: str,
    supported: tuple[str, ...] | None = None,
    provider: str,
    catalog: bool | None = None,
) -> Profile:
    if catalog is None:
        catalog = worker in CATALOG_WORKERS
    efforts = supported if supported is not None else ((effort,) if effort else ("",))
    return Profile(
        id=pid,
        worker=worker,
        selector=selector,
        aliases=aliases,
        roles=roles,
        tiers=tiers,
        effort=effort,
        supported_efforts=efforts,
        provider=provider,
        catalog_required=catalog,
        capability=_builtin_capability(pid),
    )


def builtin_profiles() -> tuple[Profile, ...]:
    grok_fast_m, grok_fast_e = _pin("grok", "explore")
    grok_strong_m, grok_strong_e = _pin("grok", "implement")
    haiku_m, haiku_e = _pin("claude", "explore")
    sonnet_m, sonnet_e = _pin("claude", "implement")
    opus_m, opus_e = _pin("claude", "hard")
    explorer_m, explorer_e = _pin("codex", "explore")
    luna_m, luna_e = _pin("codex", "mini")
    terra_hard_m, terra_hard_e = _pin("codex", "hard")
    terra_review_m, terra_review_e = _pin("codex", "review")
    oc_fast_m, oc_fast_e = _pin("opencode", "explore")
    oc_std_m, oc_std_e = _pin("opencode", "implement")
    oc_strong_m, oc_strong_e = _pin("opencode", "hard")
    omp_fast_m, omp_fast_e = _pin("omp", "explore")
    omp_std_m, omp_std_e = _pin("omp", "implement")
    omp_review_m, omp_review_e = _pin("omp", "review")
    pi_fast_m, pi_fast_e = _pin("pi", "explore")
    pi_std_m, pi_std_e = _pin("pi", "implement")
    pi_review_m, pi_review_e = _pin("pi", "review")
    agy_fast_m, agy_fast_e = _pin("agy", "explore")
    agy_std_m, agy_std_e = _pin("agy", "implement")
    agy_strong_m, agy_strong_e = _pin("agy", "hard")
    devin_fast_m, devin_fast_e = _pin("devin", "explore")
    devin_std_m, devin_std_e = _pin("devin", "implement")
    devin_strong_m, devin_strong_e = _pin("devin", "hard")
    mimo_fast_m, mimo_fast_e = _pin("mimo", "explore")
    mimo_std_m, mimo_std_e = _pin("mimo", "implement")
    cur_fast_m, cur_fast_e = _pin("cursor", "explore")
    cur_std_m, cur_std_e = _pin("cursor", "implement")
    cur_hard_m, cur_hard_e = _pin("cursor", "hard")
    cur_review_m, cur_review_e = _pin("cursor", "review")
    omp_grok46 = ("xai-oauth/grok-4.6", "xai/grok-4.6")
    omp_grok45 = ("xai-oauth/grok-4.5", "xai/grok-4.5")
    pi_grok46 = ("xai/grok-4.6",)
    pi_grok45 = ("xai/grok-4.5",)
    opus_alias = ("anthropic/claude-opus-5",)
    return (
        _p("grok-4.5-low", "grok", grok_fast_m, effort=grok_fast_e, provider="xai"),
        _p(
            "claude-haiku-4-5-low",
            "claude",
            haiku_m,
            effort=haiku_e,
            provider="anthropic",
        ),
        _p(
            "codex-explorer-low",
            "codex",
            explorer_m,
            roles=("explore",),
            effort=explorer_e,
            provider="openai",
        ),
        _p(
            "codex-luna-low",
            "codex",
            luna_m,
            roles=("mini", "bulk", "implement"),
            tiers=("fast", "standard"),
            effort=luna_e,
            provider="openai",
        ),
        _p(
            "opencode-gpt-5.4-mini-minimal",
            "opencode",
            oc_fast_m,
            aliases=("gpt-5.4-mini",),
            effort=oc_fast_e,
            provider="openai",
        ),
        _p(
            "omp-grok-4.5-low",
            "omp",
            omp_fast_m,
            aliases=omp_grok45,
            effort=omp_fast_e,
            provider="xai",
        ),
        _p(
            "pi-grok-4.5-low",
            "pi",
            pi_fast_m,
            aliases=pi_grok45,
            effort=pi_fast_e,
            provider="xai",
        ),
        _p(
            "agy-gemini-3.8-flash-low",
            "agy",
            agy_fast_m,
            effort=agy_fast_e,
            provider="google",
        ),
        _p(
            "devin-swe-2-medium",
            "devin",
            devin_fast_m,
            roles=DEVIN_FAST_ROLES,
            effort=devin_fast_e,
            provider="cognition",
        ),
        _p("mimo-v2-flash-low", "mimo", mimo_fast_m, effort=mimo_fast_e, provider="xiaomi"),
        _p(
            "cursor-composer-2.5-fast",
            "cursor",
            cur_fast_m,
            effort=cur_fast_e,
            supported=(cur_fast_e,),
            provider="cursor",
        ),
        _p(
            "grok-4.7-high",
            "grok",
            grok_strong_m,
            roles=STRONG_ROLES,
            tiers=("standard", "strong"),
            effort=grok_strong_e,
            provider="xai",
        ),
        _p(
            "claude-sonnet-5-medium",
            "claude",
            sonnet_m,
            roles=STANDARD_ROLES,
            tiers=("standard",),
            effort=sonnet_e,
            provider="anthropic",
        ),
        _p(
            "opencode-gpt-5.6-luna-high",
            "opencode",
            oc_std_m,
            aliases=("gpt-5.6-luna",),
            roles=STANDARD_ROLES,
            tiers=("standard",),
            effort=oc_std_e,
            provider="openai",
        ),
        _p(
            "omp-grok-4.6-high",
            "omp",
            omp_std_m,
            aliases=omp_grok46,
            roles=STRONG_NO_REVIEW,
            tiers=("standard", "strong"),
            effort=omp_std_e,
            provider="xai",
        ),
        _p(
            "pi-grok-4.6-high",
            "pi",
            pi_std_m,
            aliases=pi_grok46,
            roles=STRONG_NO_REVIEW,
            tiers=("standard", "strong"),
            effort=pi_std_e,
            provider="xai",
        ),
        _p(
            "agy-gemini-3.8-flash-high",
            "agy",
            agy_std_m,
            roles=STANDARD_ROLES,
            tiers=("standard",),
            effort=agy_std_e,
            provider="google",
        ),
        _p(
            "devin-swe-2-high",
            "devin",
            devin_std_m,
            roles=DEVIN_IMPLEMENT_ROLES,
            tiers=("standard",),
            effort=devin_std_e,
            provider="cognition",
        ),
        _p(
            "mimo-v2-pro-high", "mimo", mimo_std_m,
            roles=STANDARD_ROLES, tiers=("standard",), effort=mimo_std_e, provider="xiaomi",
        ),
        _p(
            "cursor-composer-2.5",
            "cursor",
            cur_std_m,
            roles=STANDARD_ROLES,
            tiers=("standard",),
            effort=cur_std_e,
            supported=(cur_std_e,),
            provider="cursor",
        ),
        _p(
            "claude-opus-5-high",
            "claude",
            opus_m,
            roles=STRONG_ROLES,
            tiers=("strong",),
            effort=opus_e,
            provider="anthropic",
        ),
        _p(
            "opencode-gpt-5.6-terra-max",
            "opencode",
            oc_strong_m,
            aliases=("gpt-5.6-terra",),
            roles=STRONG_ROLES,
            tiers=("strong",),
            effort=oc_strong_e,
            provider="openai",
        ),
        _p(
            "omp-claude-opus-5-high",
            "omp",
            omp_review_m,
            aliases=opus_alias,
            roles=STRONG_ROLES,
            tiers=("strong",),
            effort=omp_review_e,
            provider="anthropic",
        ),
        _p(
            "pi-claude-opus-5-high",
            "pi",
            pi_review_m,
            aliases=opus_alias,
            roles=STRONG_ROLES,
            tiers=("strong",),
            effort=pi_review_e,
            provider="anthropic",
        ),
        _p(
            "agy-gemini-3.1-pro-high",
            "agy",
            agy_strong_m,
            roles=STRONG_ROLES,
            tiers=("strong",),
            effort=agy_strong_e,
            provider="google",
        ),
        _p(
            "devin-swe-2-max",
            "devin",
            devin_strong_m,
            roles=DEVIN_STRONG_ROLES,
            tiers=("strong",),
            effort=devin_strong_e,
            provider="cognition",
        ),
        _p(
            "codex-terra-medium",
            "codex",
            terra_hard_m,
            roles=("hard", "implement", "mini", "bulk"),
            tiers=("strong",),
            effort=terra_hard_e,
            provider="openai",
        ),
        _p(
            "codex-terra-high",
            "codex",
            terra_review_m,
            roles=("review",),
            tiers=("strong",),
            effort=terra_review_e,
            provider="openai",
        ),
        _p(
            "cursor-grok-4.6-high",
            "cursor",
            cur_hard_m,
            roles=("hard",),
            tiers=("strong",),
            effort=cur_hard_e,
            supported=(cur_hard_e,),
            provider="xai",
        ),
        _p(
            "cursor-opus-thinking-high",
            "cursor",
            cur_review_m,
            roles=("review",),
            tiers=("strong",),
            effort=cur_review_e,
            supported=(cur_review_e,),
            provider="anthropic",
        ),
    )


def profiles_by_id(profiles: tuple[Profile, ...] | None = None) -> dict[str, Profile]:
    rows = profiles if profiles is not None else builtin_profiles()
    return {p.id: p for p in rows}


def default_preference_ids(
    tier: str,
    profiles: dict[str, Profile],
    *,
    role: str = "",
) -> list[str]:
    review = role == "review" or tier == "review"
    workers = STRONG_REVIEW_WORKERS if review or tier in {"strong", "review"} else FAST_STANDARD_WORKERS
    want_tier = "strong" if tier == "review" else tier
    ordered: list[str] = []
    seen: set[str] = set()
    by_worker: dict[str, list[Profile]] = {}
    for profile in profiles.values():
        if review and not profile.allows_role("review"):
            continue
        if want_tier in TIERS and not profile.allows_tier(want_tier):
            continue
        by_worker.setdefault(profile.worker, []).append(profile)
    for worker in workers:
        for profile in sorted(by_worker.get(worker, []), key=lambda item: item.id):
            if profile.id not in seen:
                seen.add(profile.id)
                ordered.append(profile.id)
    for profile in sorted(profiles.values(), key=lambda item: item.id):
        if profile.id in seen:
            continue
        if review and not profile.allows_role("review"):
            continue
        if want_tier in TIERS and not profile.allows_tier(want_tier):
            continue
        ordered.append(profile.id)
    return ordered


def preference_order(mentioned: list[str], default: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for pid in mentioned:
        if pid not in seen:
            seen.add(pid)
            ordered.append(pid)
    for pid in default:
        if pid not in seen:
            seen.add(pid)
            ordered.append(pid)
    return ordered


def as_dict(profile: Profile) -> dict:
    return {
        "id": profile.id,
        "worker": profile.worker,
        "selector": profile.selector,
        "aliases": list(profile.aliases),
        "roles": list(profile.roles),
        "tiers": list(profile.tiers),
        "effort": profile.effort,
        "supported_efforts": list(profile.supported_efforts),
        "provider": profile.provider,
        "catalog_required": profile.catalog_required,
        "capability": capability_dict(profile.capability),
    }


def _cap(quality: int, speed: int, cost: int, reasoning: int, **traits: int) -> Capability:
    data = capability_dict(SAFE_CAPABILITY)
    data.update(quality=quality, speed=speed, cost=cost, reasoning=reasoning)
    data.update(traits)
    return Capability(**data)


def _from(base: dict, **overrides: int) -> Capability:
    merged = dict(base)
    merged.update(overrides)
    return _cap(**merged)


def _builtin_capability(pid: str) -> Capability:
    fast = dict(quality=56, speed=84, cost=86, reasoning=42, implementation=64, debugging=60, tests=58, general=60)
    standard = dict(
        quality=78, speed=58, cost=48, reasoning=74,
        implementation=80, debugging=76, tests=74, architecture=70,
        security=66, performance=64, migration=68, documentation=66,
        frontend=62, data=66, general=74,
    )
    strong = dict(
        quality=90, speed=36, cost=24, reasoning=92,
        implementation=88, debugging=86, tests=82, architecture=90,
        security=86, performance=78, migration=80, documentation=76,
        frontend=70, data=74, general=84,
    )
    table = {
        "grok-4.5-low": _from(fast, debugging=66),
        "claude-haiku-4-5-low": _from(fast, documentation=72, speed=88, cost=84),
        "codex-explorer-low": _from(fast, implementation=22, general=72, quality=60),
        "codex-luna-low": _from(fast, implementation=70, quality=64, reasoning=48),
        "opencode-gpt-5.4-mini-minimal": _from(fast, quality=52, speed=80),
        "omp-grok-4.5-low": _from(fast, debugging=66),
        "pi-grok-4.5-low": _from(fast, debugging=66),
        "agy-gemini-3.8-flash-low": _from(fast, speed=86, cost=88, quality=54),
        "devin-swe-2-medium": _from(fast, implementation=68, quality=60),
        "cursor-composer-2.5-fast": _from(fast, quality=50, speed=82),
        "grok-4.7-high": _from(standard, quality=82, debugging=84, reasoning=78),
        "claude-sonnet-5-medium": _from(standard, quality=80, frontend=84, documentation=80),
        "opencode-gpt-5.6-luna-high": _from(standard, quality=76, implementation=76),
        "omp-grok-4.6-high": _from(standard, quality=74, implementation=76, reasoning=72),
        "pi-grok-4.6-high": _from(standard, quality=74, implementation=76, reasoning=72),
        "agy-gemini-3.8-flash-high": _from(standard, quality=74, speed=70),
        "devin-swe-2-high": _from(standard, quality=72, implementation=74),
        "mimo-v2-flash-low": _from(fast, quality=58, implementation=66, speed=82),
        "mimo-v2-pro-high": _from(standard, quality=77, implementation=79, debugging=78),
        "cursor-composer-2.5": _from(standard, quality=70),
        "claude-opus-5-high": _from(
            strong, quality=96, architecture=96, security=94, reasoning=96, implementation=92, debugging=90,
        ),
        "opencode-gpt-5.6-terra-max": _from(strong, quality=91, architecture=88, reasoning=90),
        "omp-claude-opus-5-high": _from(
            strong, quality=96, architecture=96, security=94, reasoning=96, implementation=92,
        ),
        "pi-claude-opus-5-high": _from(
            strong, quality=96, architecture=96, security=94, reasoning=96, implementation=92,
        ),
        "agy-gemini-3.1-pro-high": _from(strong, quality=88, data=84, reasoning=88),
        "devin-swe-2-max": _from(strong, quality=86, architecture=84),
        "codex-terra-medium": _from(strong, quality=84, reasoning=80, implementation=82),
        "codex-terra-high": _from(strong, quality=86, reasoning=88),
        "cursor-grok-4.6-high": _from(standard, quality=74, reasoning=76),
        "cursor-opus-thinking-high": _from(strong, quality=90, reasoning=90),
    }
    return table.get(pid, SAFE_CAPABILITY)
