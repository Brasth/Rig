#!/usr/bin/env python3
"""Read-only routing evidence report. Never influences pick."""
from __future__ import annotations

import json
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import jobs as rig_jobs
import routing_policy
import verification


def _parse_stamp(raw: str) -> float | None:
    text = (raw or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S.%fZ"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc).timestamp()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _job_stamp(job: dict, sidecar: dict | None) -> float | None:
    for raw in (
        (sidecar or {}).get("written_at"),
        job.get("started_at"),
        job.get("ended_at"),
    ):
        stamp = _parse_stamp(str(raw or ""))
        if stamp is not None:
            return stamp
    mtime = job.get("mtime")
    try:
        return float(mtime) if mtime else None
    except (TypeError, ValueError):
        return None


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    return float(statistics.median(values))


def _actual_model(job: dict) -> tuple[str, str]:
    if job.get("model_inferred"):
        return "", ""
    if job.get("model_source") not in {"selected", "observed"}:
        return "", ""
    return str(job.get("model") or ""), str(job.get("effort") or "")


def _group_key(routing: dict | None, job: dict) -> tuple:
    routing = routing or {}
    profile = routing.get("selected_profile") or {}
    model, effort = _actual_model(job)
    return (
        routing.get("policy_version") if routing.get("policy_mode") == "smart" else "legacy",
        routing.get("required_tier") or "",
        (profile.get("id") if isinstance(profile, dict) else "") or "",
        model,
        effort,
    )


def _empty_bucket() -> dict:
    return {
        "attempts": 0,
        "missing_provenance": 0,
        "completed": 0,
        "launch_failures": 0,
        "execution_failures": 0,
        "timeouts": 0,
        "cancels": 0,
        "unverified": 0,
        "accepted": 0,
        "rejected": 0,
        "pending": 0,
        "stale": 0,
        "exit0": 0,
        "durations": [],
    }


def _job_folder(root: Path, job: dict) -> Path | None:
    raw = job.get("dir")
    if raw:
        return Path(raw)
    job_id = str(job.get("job_id") or "").strip()
    if not job_id:
        return None
    return root / ".rig" / "jobs" / job_id


def _provenance(job: dict, sidecar: dict | None) -> str:
    if not sidecar:
        return "missing"
    routing = sidecar.get("routing") if isinstance(sidecar.get("routing"), dict) else None
    if not routing:
        return "missing"
    mode = str(routing.get("policy_mode") or "").strip().lower()
    if mode == "smart":
        return "smart"
    if mode == "legacy":
        return "legacy"
    if mode == "manual":
        return "manual"
    return "missing"


def _is_stale(assessed: dict) -> bool:
    reason = str(assessed.get("reason") or "")
    freshness = assessed.get("freshness")
    state = assessed.get("state")
    return (
        "content_changed" in reason
        or freshness == "unavailable"
        or (state == "verified" and freshness != "current")
    )


def build_report(repo: Path, *, days: int = 30, now: float | None = None) -> dict:
    if type(days) is not int or days < 1:
        raise ValueError("days must be a positive integer")
    root = Path(repo)
    cutoff = (now if now is not None else time.time()) - days * 86400
    listing = rig_jobs.list_jobs(root)
    groups: dict[tuple, dict] = {}
    legacy = _empty_bucket()
    manual = _empty_bucket()
    missing = _empty_bucket()
    totals = {
        "attempts": 0,
        "smart_attempts": 0,
        "legacy_attempts": 0,
        "manual_attempts": 0,
        "missing_provenance": 0,
        "unknown_timestamps": 0,
        "accepted": 0,
        "assessed": 0,
        "exit0": 0,
        "cancels": 0,
    }

    def ensure(key: tuple) -> dict:
        if key not in groups:
            groups[key] = {
                "policy_version": key[0],
                "required_tier": key[1],
                "profile": key[2],
                "model": key[3],
                "effort": key[4],
                **_empty_bucket(),
            }
        return groups[key]

    cache = {}
    for job in listing:
        folder = _job_folder(root, job)
        job_attempt = str(job.get("attempt_id") or "").strip()
        sidecar = routing_policy.read_sidecar(folder, expected_attempt_id=job_attempt) if folder else None
        stamp = _job_stamp(job, sidecar)
        if stamp is None:
            totals["unknown_timestamps"] += 1
            continue
        if stamp < cutoff:
            continue
        totals["attempts"] += 1
        routing = (sidecar or {}).get("routing") if sidecar else None
        origin = _provenance(job, sidecar)
        if origin == "smart":
            bucket = ensure(_group_key(routing, job))
            totals["smart_attempts"] += 1
        elif origin == "legacy":
            bucket = legacy
            totals["legacy_attempts"] += 1
        elif origin == "manual":
            bucket = manual
            totals["manual_attempts"] += 1
        else:
            bucket = missing
            totals["missing_provenance"] += 1
            missing["missing_provenance"] += 1
        bucket["attempts"] += 1
        status = str(job.get("status") or "")
        exit_code = job.get("exit_code")
        execution_mode = str(job.get("execution_mode") or "")
        if status == "ok" and exit_code == 0:
            bucket["exit0"] += 1
            totals["exit0"] += 1
        if status == "timeout":
            bucket["timeouts"] += 1
        if status == "cancelled":
            bucket["cancels"] += 1
            totals["cancels"] += 1
        if execution_mode == "not_started":
            bucket["launch_failures"] += 1
        elif status == "fail":
            bucket["execution_failures"] += 1
        if status in {"ok", "fail", "timeout"} and execution_mode != "not_started":
            bucket["completed"] += 1
            elapsed = job.get("elapsed_s")
            if elapsed is not None:
                try:
                    bucket["durations"].append(float(elapsed))
                except (TypeError, ValueError):
                    pass
        try:
            assessed = verification.assessment(root, job, refresh=True, cache=cache)
        except Exception:
            assessed = {"state": "unknown", "acceptance": "pending", "freshness": "not_checked", "reason": ""}
        acceptance = assessed.get("acceptance")
        state = assessed.get("state")
        freshness = assessed.get("freshness")
        stale = _is_stale(assessed)
        if acceptance in {"accepted", "rejected"}:
            totals["assessed"] += 1
            if acceptance == "accepted" and state == "verified" and freshness == "current":
                bucket["accepted"] += 1
                totals["accepted"] += 1
            elif acceptance == "rejected":
                bucket["rejected"] += 1
                bucket["unverified"] += 1
            elif stale:
                bucket["stale"] += 1
                bucket["unverified"] += 1
            else:
                bucket["pending"] += 1
                bucket["unverified"] += 1
        elif stale:
            bucket["stale"] += 1
            bucket["unverified"] += 1
        else:
            bucket["pending"] += 1
            bucket["unverified"] += 1

    def finish(bucket: dict) -> dict:
        durations = bucket.pop("durations", [])
        out = dict(bucket)
        out["median_duration_s"] = _median(durations)
        return out

    grouped = [finish(item) for item in groups.values()]
    grouped.sort(key=lambda row: (
        str(row["policy_version"]), row["required_tier"], row["profile"], row["model"], row["effort"],
    ))
    return {
        "days": days,
        "policy_version": routing_policy.POLICY_VERSION,
        "totals": {
            **{k: v for k, v in totals.items()},
            "accepted_numerator": totals["accepted"],
            "accepted_denominator": totals["assessed"],
            "note": (
                "exit0 is not acceptance; cancellation is not a model-quality failure; "
                "smart, legacy, and manual provenance are counted separately; "
                "missing/malformed sidecar evidence is never accepted"
            ),
        },
        "groups": grouped,
        "legacy": finish(legacy),
        "manual": finish(manual),
        "missing": finish(missing),
    }


def format_report(report: dict) -> str:
    totals = report["totals"]
    lines = [
        f"routing report days={report['days']} policy_v={report['policy_version']}",
        (
            f"attempts={totals['attempts']} smart={totals['smart_attempts']} "
            f"legacy={totals['legacy_attempts']} manual={totals.get('manual_attempts', 0)} "
            f"missing_provenance={totals['missing_provenance']} "
            f"unknown_timestamps={totals.get('unknown_timestamps', 0)}"
        ),
        (
            f"accepted={totals['accepted_numerator']}/{totals['accepted_denominator']} "
            f"exit0={totals['exit0']} cancels={totals['cancels']}"
        ),
        totals["note"],
    ]
    for row in report["groups"]:
        median = row["median_duration_s"]
        median_s = "-" if median is None else f"{median:.1f}s"
        lines.append(
            f"  v={row['policy_version']} tier={row['required_tier'] or '-'} "
            f"profile={row['profile'] or '-'} model={row['model'] or '-'} effort={row['effort'] or '-'} "
            f"n={row['attempts']} ok_accept={row['accepted']} fail_exec={row['execution_failures']} "
            f"launch_fail={row['launch_failures']} timeout={row['timeouts']} cancel={row['cancels']} "
            f"unverified={row['unverified']} pending={row['pending']} stale={row['stale']} median={median_s}"
        )
    legacy = report["legacy"]
    lines.append(
        f"  legacy n={legacy['attempts']} missing={legacy.get('missing_provenance', 0)} "
        f"accepted={legacy['accepted']} exit0={legacy['exit0']}"
    )
    manual = report.get("manual") or {}
    if manual:
        lines.append(
            f"  manual n={manual.get('attempts', 0)} accepted={manual.get('accepted', 0)} "
            f"exit0={manual.get('exit0', 0)}"
        )
    missing = report.get("missing") or {}
    if missing:
        lines.append(
            f"  missing n={missing.get('attempts', 0)} accepted={missing.get('accepted', 0)}"
        )
    return "\n".join(lines)


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="routing_report.py")
    parser.add_argument("--repo", default="")
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    repo = rig_jobs.repo_root(args.repo or None)
    try:
        report = build_report(repo, days=args.days)
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(format_report(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
