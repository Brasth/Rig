#!/usr/bin/env python3
"""Smart routing configuration load and profile validation."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

import routing_profiles as rig_profiles

POLICY_VERSION = 1
ROUTING_JSON = ".rig/routing.json"
MODES = ("smart", "legacy")
ENGINES = ("local", "jev")
LOCAL_POLICIES = ("scored-v1", "ordered-v1")
OBJECTIVES = ("quality", "balanced", "speed", "cost")
DEFAULT_ENGINE = "local"
DEFAULT_LOCAL_POLICY = "scored-v1"
DEFAULT_OBJECTIVE = "balanced"
SCHEMA_VERSIONS = (1, 2, 3)
PROFILE_FIELDS = frozenset(
    {
        "worker",
        "selector",
        "aliases",
        "roles",
        "tiers",
        "effort",
        "supported_efforts",
        "provider",
        "catalog_required",
        "capability",
    }
)
V1_FIELDS = frozenset({"schema_version", "profiles", "preferences"})
V2_FIELDS = V1_FIELDS | {"execution"}
EXECUTION_FIELDS = frozenset({"direct_parent_low_risk"})
PICKER_FIELDS = frozenset({"engine", "local_policy", "objective"})
V3_FIELDS = V2_FIELDS | {"picker"}
CONFIG_FIELDS = V3_FIELDS


class ConfigError(ValueError):
    """Invalid smart routing configuration. Never fall back to legacy."""


@dataclass
class RoutingConfig:
    mode: str
    profiles: dict[str, rig_profiles.Profile]
    preferences: dict[str, list[str]]
    routing_json: dict | None
    source: str
    direct_parent_low_risk: bool = False
    engine: str = DEFAULT_ENGINE
    local_policy: str = DEFAULT_LOCAL_POLICY
    objective: str = DEFAULT_OBJECTIVE


def routing_json_path(repo: Path | None) -> Path | None:
    if repo is None:
        return None
    return Path(repo) / ".rig" / "routing.json"


def harness_mode(harness: dict | None) -> str:
    raw = ""
    if isinstance(harness, dict):
        section = harness.get("routing")
        if isinstance(section, dict):
            raw = str(section.get("mode") or "").strip().lower()
    if not raw:
        return "smart"
    if raw not in MODES:
        raise ConfigError(f"harness [routing].mode must be smart|legacy, got {raw!r}")
    return raw


def resolve_mode(repo: Path | None, policy_mode: str | None, harness: dict | None = None) -> str:
    supplied = (policy_mode or "").strip().lower()
    if supplied:
        if supplied not in MODES:
            raise ConfigError(f"policy_mode must be smart|legacy, got {supplied!r}")
        return supplied
    if harness is None and repo is not None:
        import harness as rig_harness

        harness = rig_harness.parse_harness(rig_harness.harness_path(Path(repo)))
    return harness_mode(harness)


def _canonical(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def config_fingerprint(cfg: RoutingConfig) -> str:
    payload = {
        "policy_version": POLICY_VERSION,
        "mode": cfg.mode,
        "profiles": [rig_profiles.as_dict(p) for p in sorted(cfg.profiles.values(), key=lambda item: item.id)],
        "preferences": {key: cfg.preferences.get(key, []) for key in rig_profiles.PREF_KEYS},
    }
    if cfg.direct_parent_low_risk:
        payload["execution"] = {"direct_parent_low_risk": True}
    if (cfg.engine, cfg.local_policy, cfg.objective) != (
        DEFAULT_ENGINE,
        DEFAULT_LOCAL_POLICY,
        DEFAULT_OBJECTIVE,
    ):
        payload["picker"] = {
            "engine": cfg.engine,
            "local_policy": cfg.local_policy,
            "objective": cfg.objective,
        }
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()


def _reject_duplicate_keys(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise ConfigError(f"duplicate JSON key {key!r}")
        out[key] = value
    return out


def _read_json(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_reject_duplicate_keys)
    except ConfigError:
        raise
    except (OSError, json.JSONDecodeError, UnicodeError) as exc:
        raise ConfigError(f"invalid {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"{path} must be a JSON object")
    return data


def _require_str(value, name: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or isinstance(value, bool):
        raise ConfigError(f"{name} must be a string")
    text = value.strip()
    if not allow_empty and not text:
        raise ConfigError(f"{name} must be a nonempty string")
    return text


def _string_list(value, name: str) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) or isinstance(item, bool) or not item.strip() for item in value):
        raise ConfigError(f"{name} must be a list of nonempty strings")
    return [item.strip() for item in value]


def _strict_bool(value, name: str) -> bool:
    if not isinstance(value, bool):
        raise ConfigError(f"{name} must be a boolean")
    return value


def _unknown_fields(raw: dict, allowed: frozenset[str], name: str) -> None:
    extra = set(raw) - allowed
    if extra:
        raise ConfigError(f"{name} unknown field {sorted(extra)[0]}")


def _apply_override(base: rig_profiles.Profile, raw: dict, pid: str) -> rig_profiles.Profile:
    if not isinstance(raw, dict):
        raise ConfigError(f"profiles.{pid} must be an object")
    _unknown_fields(raw, PROFILE_FIELDS, f"profiles.{pid}")
    worker = _require_str(raw["worker"], f"profiles.{pid}.worker") if "worker" in raw else base.worker
    selector = _require_str(raw["selector"], f"profiles.{pid}.selector") if "selector" in raw else base.selector
    aliases = tuple(_string_list(raw["aliases"], f"profiles.{pid}.aliases")) if "aliases" in raw else base.aliases
    roles = tuple(_string_list(raw["roles"], f"profiles.{pid}.roles")) if "roles" in raw else base.roles
    tiers = tuple(_string_list(raw["tiers"], f"profiles.{pid}.tiers")) if "tiers" in raw else base.tiers
    if "effort" in raw:
        effort = _require_str(raw["effort"], f"profiles.{pid}.effort", allow_empty=True)
    else:
        effort = base.effort
    supported = (
        tuple(_string_list(raw["supported_efforts"], f"profiles.{pid}.supported_efforts"))
        if "supported_efforts" in raw
        else base.supported_efforts
    )
    provider = (
        _require_str(raw["provider"], f"profiles.{pid}.provider").lower()
        if "provider" in raw
        else base.provider
    )
    catalog = _strict_bool(raw["catalog_required"], f"profiles.{pid}.catalog_required") if "catalog_required" in raw else base.catalog_required
    capability = (
        _capability_from_raw(raw["capability"], base.capability, f"profiles.{pid}.capability")
        if "capability" in raw
        else base.capability
    )
    return rig_profiles.Profile(
        id=pid,
        worker=worker,
        selector=selector,
        aliases=aliases,
        roles=roles,
        tiers=tiers,
        effort=effort,
        supported_efforts=supported,
        provider=provider,
        catalog_required=catalog,
        capability=capability,
    )


def _new_profile(pid: str, raw: dict) -> rig_profiles.Profile:
    if not isinstance(raw, dict):
        raise ConfigError(f"profiles.{pid} must be an object")
    required = ("worker", "selector", "roles", "tiers", "effort", "provider")
    missing = [key for key in required if key not in raw]
    if missing:
        raise ConfigError(f"profiles.{pid} missing {', '.join(missing)}")
    if "supported_efforts" not in raw:
        effort = raw.get("effort")
        raw = dict(raw)
        if isinstance(effort, str):
            raw["supported_efforts"] = [effort] if effort else [""]
        else:
            raw["supported_efforts"] = []
    if "catalog_required" not in raw:
        worker = raw.get("worker")
        raw = dict(raw)
        raw["catalog_required"] = isinstance(worker, str) and worker.strip() in rig_profiles.CATALOG_WORKERS
    empty = rig_profiles.Profile(
        id=pid,
        worker="",
        selector="",
        aliases=(),
        roles=(),
        tiers=(),
        effort="",
        supported_efforts=(),
        provider="",
        catalog_required=False,
        capability=rig_profiles.SAFE_CAPABILITY,
    )
    return _apply_override(empty, raw, pid)


def _capability_from_raw(raw, base: rig_profiles.Capability, name: str) -> rig_profiles.Capability:
    if not isinstance(raw, dict) or isinstance(raw, bool):
        raise ConfigError(f"{name} must be an object")
    _unknown_fields(raw, frozenset(rig_profiles.CAPABILITY_FIELDS), name)
    data = rig_profiles.capability_dict(base)
    for key, value in raw.items():
        if type(value) is not int or not 0 <= value <= 100:
            raise ConfigError(f"{name}.{key} must be an integer 0..100")
        data[key] = value
    return rig_profiles.Capability(**data)


def harness_picker(harness: dict | None) -> tuple[str, str, str]:
    section = {}
    if isinstance(harness, dict):
        raw = harness.get("routing")
        if isinstance(raw, dict):
            section = raw
    engine = str(section.get("engine") or "").strip().lower() or DEFAULT_ENGINE
    policy = str(section.get("local_policy") or "").strip().lower() or DEFAULT_LOCAL_POLICY
    objective = str(section.get("objective") or "").strip().lower() or DEFAULT_OBJECTIVE
    return engine, policy, objective


def _validate_picker(engine: str, policy: str, objective: str) -> None:
    if engine not in ENGINES:
        raise ConfigError(f"picker.engine must be local|jev, got {engine!r}")
    if policy not in LOCAL_POLICIES:
        raise ConfigError(f"picker.local_policy must be scored-v1|ordered-v1, got {policy!r}")
    if objective not in OBJECTIVES:
        raise ConfigError(f"picker.objective must be quality|balanced|speed|cost, got {objective!r}")


def validate_profiles(rows: dict[str, rig_profiles.Profile]) -> None:
    import route as rig_route

    seen_ids: set[str] = set()
    for pid, profile in rows.items():
        if pid != profile.id:
            raise ConfigError(f"profile id {profile.id!r} does not match key {pid!r}")
        if pid in seen_ids:
            raise ConfigError(f"duplicate profile id {pid}")
        seen_ids.add(pid)
        if profile.worker not in rig_route.WORKER_NAMES - {"native"}:
            raise ConfigError(f"profile {pid}: unknown worker {profile.worker}")
        if not str(profile.selector).strip():
            raise ConfigError(f"profile {pid}: selector is required")
        banned = rig_route.assert_child_model(profile.selector)
        if banned:
            raise ConfigError(f"profile {pid}: {banned}")
        for alias in profile.aliases:
            banned = rig_route.assert_child_model(alias)
            if banned:
                raise ConfigError(f"profile {pid}: banned alias {alias}")
        for role in profile.roles:
            if role not in rig_profiles.ROLES:
                raise ConfigError(f"profile {pid}: unknown role {role}")
        if not profile.roles:
            raise ConfigError(f"profile {pid}: roles cannot be empty")
        for tier in profile.tiers:
            if tier not in rig_profiles.TIERS:
                raise ConfigError(f"profile {pid}: unknown tier {tier}")
        if not profile.tiers:
            raise ConfigError(f"profile {pid}: tiers cannot be empty")
        if profile.effort not in profile.supported_efforts:
            raise ConfigError(f"profile {pid}: effort {profile.effort!r} not in supported_efforts")
        if profile.provider not in rig_profiles.KNOWN_PROVIDERS:
            raise ConfigError(f"profile {pid}: unknown provider {profile.provider}")
        family = rig_route.provider_for(profile.selector)
        if family and family != profile.provider:
            raise ConfigError(
                f"profile {pid}: provider {profile.provider} conflicts with selector family {family}"
            )
        for alias in profile.aliases:
            alias_family = rig_route.provider_for(alias)
            if alias_family and alias_family != profile.provider:
                raise ConfigError(
                    f"profile {pid}: provider {profile.provider} conflicts with alias family {alias_family}"
                )
        spark_selector = "gpt-5.3-codex-spark"
        is_explorer = pid == "codex-explorer-low"
        is_spark = spark_selector in {key.lower() for key in profile.model_keys()}
        if is_explorer or (profile.worker == "codex" and is_spark):
            writes = set(profile.roles) & set(rig_profiles.WRITE_ROLES)
            if writes:
                raise ConfigError(f"profile {pid}: codex explorer/Spark cannot write")
        if profile.worker in rig_profiles.CATALOG_WORKERS and not profile.catalog_required:
            raise ConfigError(f"profile {pid}: catalog-required worker cannot disable confirmation")
        if profile.worker == "devin":
            for role in profile.roles:
                err = rig_route.assert_devin_model("devin", profile.selector, role)
                if err:
                    raise ConfigError(f"profile {pid}: {err}")
            for alias in profile.aliases:
                for role in profile.roles:
                    err = rig_route.assert_devin_model("devin", alias, role)
                    if err:
                        raise ConfigError(f"profile {pid}: {err}")


def _default_preferences(profiles: dict[str, rig_profiles.Profile]) -> dict[str, list[str]]:
    return {
        "fast": rig_profiles.default_preference_ids("fast", profiles),
        "standard": rig_profiles.default_preference_ids("standard", profiles),
        "strong": rig_profiles.default_preference_ids("strong", profiles),
        "review": rig_profiles.default_preference_ids("strong", profiles, role="review"),
    }


def _parse_execution(raw: dict, path: Path | None, version: int) -> bool:
    if version == 1:
        return False
    if "execution" not in raw:
        return False
    spec = raw.get("execution")
    if not isinstance(spec, dict) or isinstance(spec, bool):
        raise ConfigError(f"{path} execution must be an object")
    _unknown_fields(spec, EXECUTION_FIELDS, "execution")
    if "direct_parent_low_risk" not in spec:
        return False
    return _strict_bool(spec["direct_parent_low_risk"], "execution.direct_parent_low_risk")


def _parse_picker(raw: dict, path: Path | None, base: tuple[str, str, str]) -> tuple[str, str, str]:
    if "picker" not in raw:
        return base
    spec = raw.get("picker")
    if not isinstance(spec, dict) or isinstance(spec, bool):
        raise ConfigError(f"{path} picker must be an object")
    _unknown_fields(spec, PICKER_FIELDS, "picker")
    engine, policy, objective = base
    if "engine" in spec:
        engine = _require_str(spec["engine"], "picker.engine").lower()
    if "local_policy" in spec:
        policy = _require_str(spec["local_policy"], "picker.local_policy").lower()
    if "objective" in spec:
        objective = _require_str(spec["objective"], "picker.objective").lower()
    return engine, policy, objective


def _parse_routing_json(path: Path | None, raw: dict | None, profiles: dict[str, rig_profiles.Profile], preferences: dict[str, list[str]], picker: tuple[str, str, str]) -> tuple[dict[str, rig_profiles.Profile], dict[str, list[str]], dict | None, str, bool, tuple[str, str, str]]:
    if raw is None:
        return profiles, preferences, None, "builtin", False, picker
    version = raw.get("schema_version")
    if type(version) is not int or version not in SCHEMA_VERSIONS:
        raise ConfigError(f"{path} schema_version must be 1, 2 or 3")
    allowed = {1: V1_FIELDS, 2: V2_FIELDS, 3: V3_FIELDS}[version]
    extra_keys = set(raw) - allowed
    if extra_keys:
        raise ConfigError(f"{path} unknown keys: {sorted(extra_keys)[0]}")
    direct_parent = _parse_execution(raw, path, version)
    if version >= 3:
        picker = _parse_picker(raw, path, picker)
    overrides = raw.get("profiles") or {}
    if overrides and not isinstance(overrides, dict):
        raise ConfigError(f"{path} profiles must be an object keyed by stable id")
    if "profiles" in raw and raw["profiles"] is not None and not isinstance(raw["profiles"], dict):
        raise ConfigError(f"{path} profiles must be an object keyed by stable id")
    seen = set()
    for pid, spec in (overrides or {}).items():
        if not isinstance(pid, str) or not pid.strip():
            raise ConfigError("profile id cannot be empty")
        ident = pid.strip()
        if ident in seen:
            raise ConfigError(f"duplicate profile id {ident}")
        seen.add(ident)
        if ident in profiles:
            profiles[ident] = _apply_override(profiles[ident], spec, ident)
        else:
            profiles[ident] = _new_profile(ident, spec)
    pref_raw = raw.get("preferences") or {}
    if pref_raw and not isinstance(pref_raw, dict):
        raise ConfigError(f"{path} preferences must be an object")
    if "preferences" in raw and raw["preferences"] is not None and not isinstance(raw["preferences"], dict):
        raise ConfigError(f"{path} preferences must be an object")
    defaults = _default_preferences(profiles)
    for key, value in (pref_raw or {}).items():
        if key not in rig_profiles.PREF_KEYS:
            raise ConfigError(f"preferences key must be fast|standard|strong|review, got {key}")
        ids = _string_list(value, f"preferences.{key}")
        unknown = [item for item in ids if item not in profiles]
        if unknown:
            raise ConfigError(f"preferences.{key} unknown profile id {unknown[0]}")
        preferences[key] = rig_profiles.preference_order(ids, defaults[key])
    for key, default in defaults.items():
        if key not in (pref_raw or {}):
            preferences[key] = default
    return profiles, preferences, raw, str(path), direct_parent, picker


def load_config(repo: Path | None, *, policy_mode: str | None = None, harness: dict | None = None) -> RoutingConfig:
    if harness is None and repo is not None:
        import harness as rig_harness

        harness = rig_harness.parse_harness(rig_harness.harness_path(Path(repo)))
    mode = resolve_mode(repo, policy_mode, harness)
    picker = harness_picker(harness)
    builtin = rig_profiles.profiles_by_id()
    path = routing_json_path(repo)

    def builtin_cfg(source: str = "builtin", selected: tuple[str, str, str] | None = None) -> RoutingConfig:
        profiles = dict(builtin)
        engine, policy, objective = selected or (DEFAULT_ENGINE, DEFAULT_LOCAL_POLICY, DEFAULT_OBJECTIVE)
        if mode == "smart":
            _validate_picker(engine, policy, objective)
        else:
            try:
                _validate_picker(engine, policy, objective)
            except ConfigError:
                engine, policy, objective = DEFAULT_ENGINE, DEFAULT_LOCAL_POLICY, DEFAULT_OBJECTIVE
        cfg = RoutingConfig(
            mode=mode,
            profiles=profiles,
            preferences=_default_preferences(profiles),
            routing_json=None,
            source=source,
            engine=engine,
            local_policy=policy,
            objective=objective,
        )
        if mode == "smart":
            validate_profiles(cfg.profiles)
        return cfg

    try:
        raw = _read_json(path) if path is not None else None
    except ConfigError:
        if mode != "smart":
            return builtin_cfg(source="builtin")
        raise
    if raw is None:
        return builtin_cfg(selected=picker)
    try:
        profiles, preferences, parsed, source, direct_parent, selected = _parse_routing_json(
            path, raw, dict(builtin), _default_preferences(dict(builtin)), picker,
        )
        if mode == "smart":
            _validate_picker(*selected)
        else:
            try:
                _validate_picker(*selected)
            except ConfigError:
                return builtin_cfg(source="builtin")
        engine, policy, objective = selected
        cfg = RoutingConfig(
            mode=mode,
            profiles=profiles,
            preferences=preferences,
            routing_json=parsed,
            source=source,
            direct_parent_low_risk=direct_parent,
            engine=engine,
            local_policy=policy,
            objective=objective,
        )
        validate_profiles(cfg.profiles)
    except ConfigError:
        if mode != "smart":
            return builtin_cfg(source="builtin")
        raise
    return cfg


def doctor_lines(repo: Path | None) -> list[str]:
    rows = ["Routing"]
    try:
        cfg = load_config(repo)
    except ConfigError as exc:
        rows.append(f"  error: {exc}")
        return rows
    rows.append(f"  mode: {cfg.mode} (policy v{POLICY_VERSION})")
    rows.append(f"  engine: {cfg.engine}")
    rows.append(f"  local_policy: {cfg.local_policy}")
    rows.append(f"  objective: {cfg.objective}")
    rows.append(f"  fingerprint: {config_fingerprint(cfg)}")
    rows.append(f"  profiles: {len(cfg.profiles)}")
    rows.append(f"  direct_parent_low_risk: {str(cfg.direct_parent_low_risk).lower()}")
    path = routing_json_path(repo)
    if path is not None and path.is_file():
        rows.append(f"  config: {path}")
    else:
        rows.append("  config: builtin (optional .rig/routing.json absent)")
    return rows
