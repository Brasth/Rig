#!/usr/bin/env python3
"""Routing sidecar, explain lines, and catalog session for one pick."""
from __future__ import annotations

import json
import time
from pathlib import Path

import catalog as rig_catalog
import routing_profiles as rig_profiles
from routing_config import POLICY_VERSION

SIDECAR_NAME = "routing.json"
PICKER_VERSION = 1
FORBIDDEN_EVIDENCE_KEYS = frozenset({"case", "task", "prompt", "text", "brief", "query", "reason"})
PICKER_STRINGS = (
    "engine",
    "requested_engine",
    "local_policy",
    "objective",
    "selection_source",
    "fallback",
    "selected_profile_id",
)


def sidecar_path(job_dir: Path) -> Path:
    return Path(job_dir) / SIDECAR_NAME


def write_sidecar(job_dir: Path, attempt_id: str, routing: dict) -> dict:
    attempt = str(attempt_id or "").strip()
    if not attempt:
        raise ValueError("sidecar attempt_id must be nonempty")
    if not isinstance(routing, dict):
        raise ValueError("sidecar routing must be an object")
    Path(job_dir).mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "attempt_id": attempt,
        "written_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "routing": routing,
    }
    path = sidecar_path(job_dir)
    tmp = path.with_name(path.name + ".tmp")
    blob = json.dumps(payload, indent=2, ensure_ascii=True) + "\n"
    tmp.write_text(blob)
    tmp.replace(path)
    return payload


def read_sidecar(job_dir: Path, expected_attempt_id: str | None = None) -> dict | None:
    path = sidecar_path(job_dir)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        return None
    if not isinstance(data, dict) or type(data.get("schema_version")) is not int or data["schema_version"] != 1:
        return None
    attempt = str(data.get("attempt_id") or "").strip()
    if not attempt:
        return None
    if not isinstance(data.get("routing"), dict):
        return None
    # Sidecars are untrusted historical input to both job show and reports.
    # Reject malformed nested fields rather than crashing their readers.
    routing = data["routing"]
    for key in ("policy_mode", "required_tier", "review_recommendation", "config_fingerprint", "execution_strategy"):
        if key in routing and not isinstance(routing[key], str):
            return None
    if "policy_version" in routing and type(routing["policy_version"]) is not int:
        return None
    profile = routing.get("selected_profile")
    if profile is not None:
        if not isinstance(profile, dict):
            return None
        if any(key in profile and not isinstance(profile[key], str)
               for key in ("id", "worker", "model", "effort", "tier", "provider", "selector")):
            return None
    if "picker" in routing and not picker_evidence_ok(routing.get("picker")):
        return None
    if expected_attempt_id is not None:
        want = str(expected_attempt_id).strip()
        if not want or want != attempt:
            return None
    return data


def _derived_review_recommendation(assessment: dict | None) -> str:
    return "independent" if (assessment or {}).get("risk") == "high" else "none"


def _require_smart_access(kind: str, access: str, profile: rig_profiles.Profile | None = None) -> None:
    value = str(access or "").strip().lower()
    if not value:
        return
    if value not in {"read", "write"}:
        raise ValueError("access must be read|write; re-pick")
    if value == "write" and profile is not None:
        if profile.worker == "codex" and not (set(profile.roles) & set(rig_profiles.WRITE_ROLES)):
            raise ValueError("codex explorer cannot access write; re-pick")
    if kind in {"explore", "review"} and value != "read":
        raise ValueError("smart explore/review requires access=read; re-pick")


def empty_routing(*, mode: str, fingerprint: str, assessment: dict, required: str = "") -> dict:
    return {
        "policy_mode": mode,
        "policy_version": POLICY_VERSION,
        "config_fingerprint": fingerprint,
        "assessment": assessment,
        "required_tier": required,
        "selected_profile": None,
        "candidate_decisions": [],
        "catalog": {"source": "none", "freshness": "n/a"},
        "review_recommendation": _derived_review_recommendation(assessment),
        "parent_fit_limitations": "",
        "execution_strategy": "",
    }


def selected_profile_dict(profile: rig_profiles.Profile, *, model: str, tier: str) -> dict:
    return {
        "id": profile.id,
        "worker": profile.worker,
        "selector": profile.selector,
        "tier": tier,
        "effort": profile.effort,
        "model": model,
        "provider": profile.provider,
        "catalog_required": profile.catalog_required,
    }


def match_profile_model(profile: rig_profiles.Profile, ids: list[str]) -> str | None:
    index: dict[str, str] = {}
    for item in sorted(ids):
        key = str(item).strip()
        if not key:
            continue
        index.setdefault(key.lower(), key)
    for key in profile.model_keys():
        hit = index.get(key.lower())
        if hit:
            return hit
    return None


class CatalogSession:
    def __init__(self, catalogs: dict | None = None):
        self.injected = catalogs
        self.seen: dict[str, dict] = {}

    def info(self, worker: str, *, require_fresh: bool = False) -> dict:
        if worker in self.seen and not require_fresh:
            return self.seen[worker]
        if worker not in rig_catalog.CATALOG_WORKERS:
            info = {
                "worker": worker,
                "ids": None,
                "state": "unverified",
                "source": "pin",
                "freshness": "unverified",
                "confirmed_empty": False,
            }
            self.seen[worker] = info
            return info
        if self.injected is not None:
            if worker not in self.injected:
                info = {
                    "worker": worker,
                    "ids": None,
                    "state": "unverified",
                    "source": "pin",
                    "freshness": "unverified",
                    "confirmed_empty": False,
                }
            else:
                ids = self.injected.get(worker)
                if ids is None:
                    info = {
                        "worker": worker,
                        "ids": None,
                        "state": "unavailable",
                        "source": "injected",
                        "freshness": "unavailable",
                        "confirmed_empty": False,
                    }
                elif not ids:
                    info = {
                        "worker": worker,
                        "ids": [],
                        "state": "empty",
                        "source": "injected",
                        "freshness": "empty",
                        "confirmed_empty": True,
                    }
                else:
                    info = {
                        "worker": worker,
                        "ids": list(ids),
                        "state": "fresh",
                        "source": "injected",
                        "freshness": "fresh",
                        "confirmed_empty": False,
                    }
            self.seen[worker] = info
            return info
        info = rig_catalog.load_catalog_info(worker, require_fresh=require_fresh)
        self.seen[worker] = info
        return info


def _string_list(value) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def picker_evidence_ok(picker) -> bool:
    if not isinstance(picker, dict):
        return False
    if FORBIDDEN_EVIDENCE_KEYS & set(picker):
        return False
    if type(picker.get("version")) is not int or picker.get("version") != PICKER_VERSION:
        return False
    if any(not isinstance(picker.get(key), str) for key in PICKER_STRINGS):
        return False
    if not _string_list(picker.get("traits")):
        return False
    canonical = picker.get("canonical")
    if not isinstance(canonical, list):
        return False
    for row in canonical:
        if not isinstance(row, dict) or (FORBIDDEN_EVIDENCE_KEYS & set(row)):
            return False
        if any(not isinstance(row.get(key), str) for key in ("provider", "model", "effort", "profile_id")):
            return False
        if "score" in row and type(row.get("score")) is not int:
            return False
        if "preference_rank" in row and type(row.get("preference_rank")) is not int:
            return False
        if not _string_list(row.get("explanation")) or not _string_list(row.get("transports")):
            return False
    return True


def picker_contains_task_text(picker, case: str) -> bool:
    banned = str(case or "").strip()
    if not banned:
        return False

    def walk(value) -> bool:
        if isinstance(value, str):
            return banned in value
        if isinstance(value, dict):
            return any(walk(key) or walk(item) for key, item in value.items())
        if isinstance(value, list):
            return any(walk(item) for item in value)
        return False

    return walk(picker)


def explain_lines(choice: dict) -> list[str]:
    routing = choice.get("routing") or {}
    lines = [
        f"policy_mode={routing.get('policy_mode') or '-'} version={routing.get('policy_version') or '-'}",
        f"fingerprint={routing.get('config_fingerprint') or '-'}",
        f"required_tier={routing.get('required_tier') or '-'}",
        f"execution_strategy={routing.get('execution_strategy') or '-'}",
    ]
    assessment = routing.get("assessment") or {}
    if assessment:
        defaulted = ",".join(assessment.get("defaulted") or []) or "none"
        lines.append(
            "assessment "
            f"complexity={assessment.get('complexity') or '-'} "
            f"risk={assessment.get('risk') or '-'} "
            f"uncertainty={assessment.get('uncertainty') or '-'} "
            f"defaulted={defaulted}"
        )
        if assessment.get("reason"):
            lines.append(f"assessment_reason={assessment['reason']}")
    profile = routing.get("selected_profile") or {}
    if profile:
        lines.append(
            f"profile={profile.get('id') or '-'} worker={profile.get('worker') or '-'} "
            f"selector={profile.get('selector') or '-'} tier={profile.get('tier') or '-'}"
        )
    catalog = routing.get("catalog") or {}
    lines.append(f"catalog source={catalog.get('source') or '-'} freshness={catalog.get('freshness') or '-'}")
    rec = routing.get("review_recommendation") or "none"
    lines.append(f"review_recommendation={rec} (not a completion gate)")
    if routing.get("parent_fit_limitations"):
        lines.append(f"parent_fit={routing['parent_fit_limitations']}")
    picker = routing.get("picker") or {}
    if picker:
        lines.append(
            f"engine={picker.get('engine') or '-'} requested={picker.get('requested_engine') or '-'} "
            f"local_policy={picker.get('local_policy') or '-'} objective={picker.get('objective') or '-'}"
        )
        traits = ",".join(picker.get("traits") or []) or "-"
        lines.append(
            f"traits={traits} source={picker.get('selection_source') or '-'} "
            f"fallback={picker.get('fallback') or '-'}"
        )
        for row in picker.get("canonical") or []:
            score = row.get("score")
            shown = "-" if score is None else score
            detail = " ".join(row.get("explanation") or [])
            lines.append(f"canonical {row.get('profile_id') or '-'} score={shown} {detail}".rstrip())
    for row in routing.get("candidate_decisions") or []:
        extra = f" {row['detail']}" if row.get("detail") else ""
        lines.append(f"candidate {row.get('id') or '-'} {row.get('code')}{extra}")
    return lines


def legacy_routing(
    choice: dict,
    *,
    assessment: dict,
    fingerprint: str,
    required: str,
    role: str = "",
    case: str = "",
) -> dict:
    routing = empty_routing(mode="legacy", fingerprint=fingerprint, assessment=assessment, required=required)
    routing["catalog"] = {"source": "legacy", "freshness": "n/a"}
    routing["selected_profile"] = {
        "id": "",
        "worker": choice.get("worker") or "",
        "selector": choice.get("model") or "",
        "tier": required,
        "effort": choice.get("effort") or "",
        "model": choice.get("model") or "",
        "provider": choice.get("provider") or "",
        "catalog_required": False,
    } if choice.get("worker") or choice.get("model") else None
    if choice.get("executor_kind") == "parent" and choice.get("model_source") == "unknown":
        routing["parent_fit_limitations"] = "parent model/effort unverified"
    elif choice.get("spawn") == "stay":
        routing["parent_fit_limitations"] = "stay uses the live parent; no catalog discovery"
    routing["review_recommendation"] = _derived_review_recommendation(assessment)
    routing["candidate_decisions"] = [{"id": "", "code": "not-evaluated", "detail": "legacy worker ladder"}]
    spawn = str(choice.get("spawn") or "")
    if spawn == "stay":
        routing["execution_strategy"] = "stay"
    elif spawn == "none" or not (choice.get("worker") or choice.get("model")):
        routing["execution_strategy"] = "none"
    elif choice.get("parent_writes") or choice.get("executor_kind") == "parent":
        routing["execution_strategy"] = "parent-fallback"
    elif spawn == "run-worker":
        routing["execution_strategy"] = "wrapper"
    traits = ["general"]
    if role or case:
        import routing_policy as policy

        traits = policy.task_traits(role or "implement", case)
    routing["picker"] = {
        "version": PICKER_VERSION,
        "engine": "local",
        "requested_engine": "local",
        "local_policy": "ordered-v1",
        "objective": "balanced",
        "selection_source": "legacy",
        "traits": traits,
        "fallback": "",
        "selected_profile_id": "",
        "canonical": [],
    }
    return routing


def manual_routing(*, worker: str = "", model: str = "", effort: str = "", mode: str = "manual") -> dict:
    assessment = {
        "complexity": "",
        "risk": "",
        "uncertainty": "",
        "reason": "",
        "defaulted": ["complexity", "risk", "uncertainty"],
        "supplied": False,
    }
    routing = empty_routing(mode=mode, fingerprint="", assessment=assessment)
    routing["selected_profile"] = None
    routing["parent_fit_limitations"] = "manual/legacy launch; assessment unknown; provenance not invented"
    if worker or model:
        routing["selected_profile"] = {
            "id": "",
            "worker": worker,
            "selector": model,
            "tier": "",
            "effort": effort,
            "model": model,
            "provider": "",
            "catalog_required": False,
        }
    return routing


def _require_catalog_model(profile: rig_profiles.Profile, model: str, session: CatalogSession) -> dict:
    age = float(session.info(profile.worker).get("age_s") or 0)
    info = session.info(profile.worker)
    if info["state"] == "stale" and age > rig_catalog.STALE_MAX_SECONDS:
        info = session.info(profile.worker, require_fresh=True)
        age = float(info.get("age_s") or 0)
    catalog_meta = {
        "source": info.get("source") or "catalog",
        "freshness": info.get("freshness") or info.get("state") or "n/a",
    }
    if info["state"] == "unavailable":
        raise ValueError("routing profile catalog-unavailable; re-pick")
    if info["state"] in {"unverified", "skipped"} or info.get("ids") is None:
        raise ValueError("routing profile catalog-unconfirmed; re-pick")
    if info.get("confirmed_empty") or info["state"] == "empty":
        raise ValueError("routing profile catalog-empty; re-pick")
    if info["state"] == "stale" and age > rig_catalog.STALE_MAX_SECONDS:
        raise ValueError("routing profile catalog-stale-refresh-failed; re-pick")
    ids = [str(item).strip() for item in (info.get("ids") or []) if str(item).strip()]
    index = {item.lower(): item for item in ids}
    if (model or "").strip().lower() not in index:
        raise ValueError("routing profile catalog-miss; re-pick")
    return catalog_meta


def validate_launch_tuple(
    repo: Path | None,
    *,
    worker: str,
    model: str,
    effort: str,
    role: str,
    routing,
    assessment=None,
    executor_kind: str = "",
    access: str = "",
    catalogs: dict | None = None,
    case: str = "",
    live: str = "",
) -> dict:
    """Rebuild trusted launch routing. Fingerprint is not an admission credential."""
    import route as rig_route
    import routing_policy as policy

    worker = str(worker or "").strip()
    model = str(model or "").strip()
    effort = str(effort or "").strip() if model else ""
    role = str(role or "").strip()
    kind = rig_route.classify(role, case)
    exec_kind = str(executor_kind or "").strip()
    access_value = str(access or "").strip().lower()
    live_parent = str(live or "").strip()
    if routing in (None, "", {}):
        return manual_routing(worker=worker, model=model, effort=effort, mode="manual")
    if not isinstance(routing, dict):
        raise ValueError("routing metadata must be an object; re-pick")
    mode = str(routing.get("policy_mode") or "").strip().lower()
    if mode in {"", "manual", "legacy"}:
        sanitized = mode if mode in {"manual", "legacy"} else "manual"
        return manual_routing(worker=worker, model=model, effort=effort, mode=sanitized)
    if mode != "smart":
        raise ValueError("routing.policy_mode must be smart|legacy|manual; re-pick")
    try:
        cfg = policy.load_config(repo)
    except policy.ConfigError as exc:
        raise ValueError(f"current routing policy is invalid; re-pick ({exc})") from exc
    if cfg.mode != "smart":
        raise ValueError("routing policy changed or fingerprint mismatch; re-pick with current rig_pick")
    fingerprint = policy.config_fingerprint(cfg)
    recorded = str(routing.get("config_fingerprint") or "")
    if recorded != fingerprint:
        raise ValueError("routing policy changed or fingerprint mismatch; re-pick with current rig_pick")
    recorded_version = routing.get("policy_version")
    if type(recorded_version) is not int or recorded_version != POLICY_VERSION:
        raise ValueError("routing policy version mismatch; re-pick")
    raw_assessment = routing.get("assessment")
    if raw_assessment not in (None, "") and not isinstance(raw_assessment, dict):
        raise ValueError("routing.assessment must be an object; re-pick")
    assessed = policy.normalize_assessment(kind, raw_assessment if isinstance(raw_assessment, dict) else None)
    if assessment not in (None, "", {}):
        if not isinstance(assessment, dict):
            raise ValueError("assessment must be an object")
        arg_assessed = policy.normalize_assessment(kind, assessment)
        if (
            arg_assessed.get("complexity"),
            arg_assessed.get("risk"),
            arg_assessed.get("uncertainty"),
        ) != (
            assessed.get("complexity"),
            assessed.get("risk"),
            assessed.get("uncertainty"),
        ):
            raise ValueError("assessment conflicts with routing metadata; re-pick")
    need = "" if kind == "stay" else policy.required_tier(kind, assessed)
    selected = routing.get("selected_profile")
    if selected in (None, "", {}):
        if exec_kind != "parent":
            raise ValueError("smart routing without a selected profile requires executor_kind=parent; re-pick")
        out = empty_routing(mode="smart", fingerprint=fingerprint, assessment=assessed, required=need)
        out["policy_version"] = POLICY_VERSION
        out["selected_profile"] = None
        limits = routing.get("parent_fit_limitations")
        out["parent_fit_limitations"] = (
            limits if isinstance(limits, str) and limits else "no eligible wrapper; parent writes"
        )
        out["review_recommendation"] = _derived_review_recommendation(assessed)
        decisions = routing.get("candidate_decisions")
        out["candidate_decisions"] = decisions if isinstance(decisions, list) else []
        claimed_cli = "" if worker in {"", "parent"} else worker
        claimed_direct = str(routing.get("execution_strategy") or "") == "direct-parent"
        live_eligible = policy.direct_parent_eligible(kind, assessed, cfg, live_parent)
        claimed_eligible = bool(claimed_cli) and policy.direct_parent_eligible(
            kind, assessed, cfg, claimed_cli,
        )
        identity_mismatch = bool(live_parent and claimed_cli and claimed_cli != live_parent)
        if kind == "stay":
            out["execution_strategy"] = "stay"
        elif identity_mismatch and (claimed_direct or live_eligible or claimed_eligible):
            raise ValueError("direct-parent live parent mismatch; re-pick")
        elif live_eligible:
            out["execution_strategy"] = "direct-parent"
            out["catalog"] = {"source": "none", "freshness": "n/a"}
            if out["parent_fit_limitations"] in {"", "no eligible wrapper; parent writes"}:
                out["parent_fit_limitations"] = (
                    "opt-in direct parent writes for low-risk mini/implement; no catalog discovery"
                )
        else:
            out["execution_strategy"] = "parent-fallback"
        _require_smart_access(kind, access_value, profile=None)
        return _accept_recorded_picker(out, routing, cfg, case)
    if not isinstance(selected, dict):
        raise ValueError("routing.selected_profile must be an object; re-pick")
    if exec_kind == "parent":
        raise ValueError("parent smart fallback cannot carry a child profile; re-pick")
    profile_id = str(routing.get("profile_id") or selected.get("id") or "").strip()
    profile = cfg.profiles.get(profile_id)
    if profile is None:
        raise ValueError("routing profile is not in the current policy; re-pick")
    if profile.worker != worker:
        raise ValueError("worker does not match the approved routing profile; re-pick")
    if profile.effort != (effort or "") and (profile.effort or effort):
        raise ValueError("effort does not match the approved routing profile; re-pick")
    allowed_models = {key.lower() for key in profile.model_keys()}
    if model.lower() not in allowed_models:
        raise ValueError("model does not match the approved routing profile; re-pick")
    if kind and kind != "stay" and kind in rig_profiles.ROLES and not profile.allows_role(kind):
        raise ValueError("role is not compatible with the approved routing profile; re-pick")
    allowed_tiers = policy.sufficient_tiers(need) if need else profile.tiers
    if need and not any(profile.allows_tier(tier) for tier in allowed_tiers):
        raise ValueError("routing profile is insufficient for the claimed task; re-pick")
    selected_tier = str(selected.get("tier") or "").strip()
    if selected_tier:
        if selected_tier not in profile.tiers:
            raise ValueError("selected tier is not allowed on the approved routing profile; re-pick")
        if need and selected_tier not in allowed_tiers:
            raise ValueError("selected tier is inconsistent with assessment; re-pick")
    else:
        selected_tier = next((tier for tier in allowed_tiers if profile.allows_tier(tier)), "")
        if need and not selected_tier:
            raise ValueError("routing profile is insufficient for the claimed task; re-pick")
    catalog_meta = {"source": "pin", "freshness": "unverified"}
    if profile.catalog_required:
        catalog_meta = _require_catalog_model(profile, model, CatalogSession(catalogs))
    out = empty_routing(mode="smart", fingerprint=fingerprint, assessment=assessed, required=need)
    out["policy_version"] = POLICY_VERSION
    out["selected_profile"] = selected_profile_dict(profile, model=model, tier=selected_tier)
    out["catalog"] = catalog_meta
    decisions = routing.get("candidate_decisions")
    out["candidate_decisions"] = decisions if isinstance(decisions, list) else []
    out["review_recommendation"] = _derived_review_recommendation(assessed)
    out["execution_strategy"] = "wrapper"
    limits = routing.get("parent_fit_limitations")
    if isinstance(limits, str):
        out["parent_fit_limitations"] = limits
    _require_smart_access(kind, access_value, profile=profile)
    return _accept_recorded_picker(out, routing, cfg, case)


def _accept_recorded_picker(out: dict, routing: dict, cfg, case: str) -> dict:
    raw = routing.get("picker") if isinstance(routing, dict) else None
    if raw is None:
        selected = out.get("selected_profile") or {}
        out["picker"] = {
            "version": PICKER_VERSION,
            "engine": "local",
            "requested_engine": cfg.engine,
            "local_policy": cfg.local_policy,
            "objective": cfg.objective,
            "selection_source": str(out.get("execution_strategy") or ""),
            "traits": [],
            "fallback": "jev-unavailable" if cfg.engine == "jev" else "",
            "selected_profile_id": str(selected.get("id") or "") if isinstance(selected, dict) else "",
            "canonical": [],
        }
        return out
    if not picker_evidence_ok(raw) or picker_contains_task_text(raw, case):
        raise ValueError("routing evidence is invalid; re-pick")
    if (
        raw.get("requested_engine") != cfg.engine
        or raw.get("local_policy") != cfg.local_policy
        or raw.get("objective") != cfg.objective
    ):
        raise ValueError("routing evidence does not match the current picker; re-pick")
    out["picker"] = raw
    return out
