"""Opt-in preparation-aware effort for smart routing.

Pure policy over an already validated preparation summary. It may raise the
capability floor and choose an effort among the *selected* profile's declared
supported efforts. It never lowers the assessment floor, changes worker/model,
or interprets efforts outside exact low|medium|high.
"""
from __future__ import annotations

LEVELS = ("low", "medium", "high")
TIERS = ("fast", "standard", "strong")
ELIGIBLE_ROLES = frozenset({"mini", "bulk", "implement"})
FLOOR = {"medium": "standard", "high": "strong"}
REASONS = (
    "adjusted", "unchanged", "pilot-off", "not-smart", "preparation-absent", "role-excluded",
    "risk-high", "not-delegated", "remaining-work-unknown", "preparation-incomplete",
    "effort-unsupported", "baseline-effort-not-comparable",
)
EFFORT_FIELDS = ("pilot", "baseline", "requested", "effective", "reason", "floor")
PREPARATION_FIELDS = ("status", "version", "fingerprint", "ready", "execution_ready", "remaining_work", "gap_codes")


def _eligibility(cfg, kind, assessed, prep):
    if not getattr(cfg, "preparation_aware_effort", False):
        return "pilot-off"
    if getattr(cfg, "mode", "") != "smart":
        return "not-smart"
    if prep is None:
        return "preparation-absent"
    if kind not in ELIGIBLE_ROLES:
        return "role-excluded"
    if (assessed or {}).get("risk") == "high":
        return "risk-high"
    return ""


def floor_tier(cfg, kind, assessed, prep):
    """Extra capability floor from remaining work: medium -> standard, high -> strong."""
    if _eligibility(cfg, kind, assessed, prep):
        return ""
    return FLOOR.get(prep.get("remaining_work") or "", "")


def raise_floor(need, floor):
    """Never lowers the assessment tier; returns the stronger of the two."""
    if floor not in TIERS or need not in TIERS:
        return need
    return TIERS[max(TIERS.index(need), TIERS.index(floor))]


def resolve(cfg, kind, assessed, prep, profile, strategy):
    """Return the effort record for one routing decision. Baseline unless every gate passes."""
    baseline = str(getattr(profile, "effort", "") or "") if profile is not None else ""
    record = {"pilot": bool(getattr(cfg, "preparation_aware_effort", False)), "baseline": baseline,
              "requested": "", "effective": baseline, "reason": "", "floor": floor_tier(cfg, kind, assessed, prep)}
    code = _eligibility(cfg, kind, assessed, prep)
    if not code and (strategy != "wrapper" or profile is None):
        code = "not-delegated"
    level = (prep or {}).get("remaining_work") or ""
    if not code and level not in LEVELS:
        code = "remaining-work-unknown"
    if not code:
        record["requested"] = level
        lowering = baseline in LEVELS and LEVELS.index(level) < LEVELS.index(baseline)
        if level not in tuple(profile.supported_efforts):
            code = "effort-unsupported"
        elif baseline not in LEVELS and level != baseline:
            code = "baseline-effort-not-comparable"
        elif level == "low" and not prep.get("execution_ready"):
            # Low effort requires a complete handoff (changes, entrypoints, decisions, unknowns=[]).
            code = "preparation-incomplete"
        elif lowering and not prep.get("ready"):
            # Other lowering (high -> medium) needs a normally ready draft; listed unknowns are bounded.
            code = "preparation-incomplete"
        else:
            record["effective"] = level
            code = "adjusted" if level != baseline else "unchanged"
    record["reason"] = code
    return record


def stamp(routing, cfg, kind, assessed, prep, profile, strategy):
    """Attach bounded evidence. Plain picks with the pilot off and no preparation stay unchanged."""
    if prep is None and not getattr(cfg, "preparation_aware_effort", False):
        return None
    record = resolve(cfg, kind, assessed, prep, profile, strategy)
    routing["effort"] = record
    if prep is not None:
        routing["preparation"] = {key: prep[key] for key in PREPARATION_FIELDS}
    if isinstance(routing.get("selected_profile"), dict):
        routing["selected_profile"]["effort"] = record["effective"]
    return record


def evidence_ok(routing):
    """Sidecar readers treat these optional fields as untrusted historical input."""
    effort = routing.get("effort")
    if effort is not None:
        if not isinstance(effort, dict) or set(effort) != set(EFFORT_FIELDS):
            return False
        if type(effort["pilot"]) is not bool or effort["reason"] not in REASONS:
            return False
        if any(not isinstance(effort[key], str) or len(effort[key]) > 32 for key in EFFORT_FIELDS if key != "pilot"):
            return False
    prep = routing.get("preparation")
    if prep is not None:
        if not isinstance(prep, dict) or set(prep) != set(PREPARATION_FIELDS):
            return False
        if prep["status"] != "valid" or type(prep["version"]) is not int:
            return False
        if not isinstance(prep["fingerprint"], str) or len(prep["fingerprint"]) != 64:
            return False
        if type(prep["ready"]) is not bool or type(prep["execution_ready"]) is not bool:
            return False
        if prep["remaining_work"] not in ("", *LEVELS):
            return False
        codes = prep["gap_codes"]
        if not isinstance(codes, list) or len(codes) > 16 or any(not isinstance(c, str) or len(c) > 48 for c in codes):
            return False
    return True


def explain_lines(routing):
    lines = []
    prep = routing.get("preparation")
    if isinstance(prep, dict):
        lines.append(f"preparation fingerprint={prep.get('fingerprint') or '-'} ready={prep.get('ready')} "
                     f"execution_ready={prep.get('execution_ready')} remaining_work={prep.get('remaining_work') or '-'} "
                     f"gaps={','.join(prep.get('gap_codes') or []) or 'none'}")
    effort = routing.get("effort")
    if isinstance(effort, dict):
        lines.append(f"effort pilot={'on' if effort.get('pilot') else 'off'} baseline={effort.get('baseline') or '-'} "
                     f"requested={effort.get('requested') or '-'} effective={effort.get('effective') or '-'} "
                     f"floor={effort.get('floor') or '-'} reason={effort.get('reason') or '-'}")
    return lines
