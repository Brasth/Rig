#!/usr/bin/env python3
"""Preparation pilot evaluation: quality-first comparison of recorded cohorts.

Cohorts: A = existing briefs, current routing; B = structured preparation, current
routing (pilot off); C = structured preparation with preparation-aware effort (pilot on).
Reads only explicitly listed local jobs. Never runs work, changes settings, or
invents measurements; missing evidence stays unknown. No brief text is reported.
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import jobs as rig_jobs  # noqa: E402
import outcome_metrics  # noqa: E402
import routing_evidence  # noqa: E402
import runtime_metrics  # noqa: E402
import token_usage  # noqa: E402

COHORTS = {
    "A": {"preparation": "absent", "pilot": None, "label": "existing briefs, current routing"},
    "B": {"preparation": "present", "pilot": False, "label": "structured preparation, current routing"},
    "C": {"preparation": "present", "pilot": True, "label": "structured preparation, preparation-aware effort"},
}
COMPARISONS = (("B", "A"), ("C", "B"), ("C", "A"))
MAX_RUNS = 300
MAX_INPUT_BYTES = 256 * 1024
DEFAULT_MIN_PAIRS = 10
ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")
NOTE = ("Quality first: a cohort is only compared on speed or tokens after paired acceptance holds. "
        "Offline or synthetic evidence is not real-provider quality proof. Total time spans the "
        "parent-recorded start to first parent acceptance; admission metrics alone exclude preparation.")


def _object(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise ValueError("duplicate pilot field: " + key)
        out[key] = value
    return out


def normalize(value):
    if not isinstance(value, dict) or set(value) - {"schema_version", "pilot_id", "synthetic", "min_pairs", "runs"}:
        raise ValueError("pilot input has missing or unsupported fields")
    if type(value.get("schema_version")) is not int or value["schema_version"] != 1:
        raise ValueError("pilot schema_version must be 1")
    pilot_id = value.get("pilot_id")
    if not isinstance(pilot_id, str) or not ID.fullmatch(pilot_id):
        raise ValueError("pilot_id must be a stable identifier")
    synthetic = value.get("synthetic", False)
    min_pairs = value.get("min_pairs", DEFAULT_MIN_PAIRS)
    if type(synthetic) is not bool or type(min_pairs) is not int or not 1 <= min_pairs <= 1000:
        raise ValueError("synthetic must be boolean and min_pairs an integer from 1 to 1000")
    runs = value.get("runs")
    if not isinstance(runs, list) or not 1 <= len(runs) <= MAX_RUNS:
        raise ValueError(f"runs must list 1 to {MAX_RUNS} recorded jobs")
    out, seen = [], set()
    for row in runs:
        if not isinstance(row, dict) or set(row) - {"task_id", "cohort", "job_id", "parent_started_at", "parent_tokens"} \
                or not {"task_id", "cohort", "job_id"} <= set(row):
            raise ValueError("each run needs task_id, cohort and job_id only (plus optional parent evidence)")
        if not all(isinstance(row[key], str) and ID.fullmatch(row[key]) for key in ("task_id", "job_id")):
            raise ValueError("task_id and job_id must be stable identifiers")
        if row["cohort"] not in COHORTS:
            raise ValueError("cohort must be A, B or C")
        if (row["task_id"], row["cohort"]) in seen:
            raise ValueError("one run per task and cohort; record retries as the same job's continuation")
        seen.add((row["task_id"], row["cohort"]))
        started = row.get("parent_started_at")
        if started is not None and runtime_metrics.parse_stamp(started) is None:
            raise ValueError("parent_started_at must be an observed ISO timestamp")
        tokens = row.get("parent_tokens")
        if tokens is not None and token_usage.normalize_token_usage(tokens) is None:
            raise ValueError("parent_tokens must be observed canonical token usage")
        out.append({key: row[key] for key in ("task_id", "cohort", "job_id")}
                   | ({"parent_started_at": started} if started is not None else {})
                   | ({"parent_tokens": token_usage.normalize_token_usage(tokens)} if tokens is not None else {}))
    return {"schema_version": 1, "pilot_id": pilot_id, "synthetic": synthetic, "min_pairs": min_pairs, "runs": out}


def _cohort_evidence(routing):
    if not isinstance(routing, dict) or type(routing.get("policy_version")) is not int or routing["policy_version"] < 3:
        return "unknown", None
    preparation = "present" if isinstance(routing.get("preparation"), dict) else "absent"
    effort = routing.get("effort")
    return preparation, (effort.get("pilot") is True) if isinstance(effort, dict) else False


def _run(repo, row):
    folder = rig_jobs.jobs_dir(repo) / row["job_id"]
    job = rig_jobs.load_job(folder, include_activity=False) if folder.is_dir() and not folder.is_symlink() else None
    out = {"task_id": row["task_id"], "cohort": row["cohort"], "job_id": row["job_id"], "status": "valid"}
    if job is None:
        return {**out, "status": "missing-job"}
    sidecar = routing_evidence.read_sidecar(folder, expected_attempt_id=str(job.get("attempt_id") or ""))
    routing = (sidecar or {}).get("routing")
    preparation, pilot = _cohort_evidence(routing)
    expected = COHORTS[row["cohort"]]
    if preparation == "unknown":
        out["status"] = "cohort-unverifiable"
    elif preparation != expected["preparation"] or (expected["pilot"] is not None and pilot is not expected["pilot"]):
        out["status"] = "cohort-mismatch"
    outcome = outcome_metrics.attempt(repo, job)
    effort = (routing or {}).get("effort") if isinstance(routing, dict) else None
    worker_tokens = token_usage.load_token_usage(job.get("token_usage"))
    started = row.get("parent_started_at")
    timing = "not-recorded" if not started else "ok"
    pre_admission = total = None
    if started:
        start, admitted = runtime_metrics.parse_stamp(started), runtime_metrics.parse_stamp(outcome["admitted_at"])
        if admitted is None:
            timing = "admission-unknown"
        elif start > admitted:
            # A parent start after admission cannot bound preparation; never report a partial total.
            timing = "parent-start-after-admission"
        else:
            pre_admission = admitted - start
            total = runtime_metrics.duration(started, outcome["accepted_at"]) if outcome["accepted_at"] else None
    out.update(
        outcome=_outcome(job, outcome), accepted=outcome["latency_s"] is not None,
        first_pass=outcome["first_pass"], pending=outcome["pending"], timing=timing,
        acceptance_latency_s=outcome["latency_s"],
        worker_execution_s=runtime_metrics.execution_latency(job),
        parent_pre_admission_s=pre_admission, total_s=total,
        worker_tokens_total=(worker_tokens or {}).get("total"),
        parent_tokens_total=(row.get("parent_tokens") or {}).get("total"),
        model=str(job.get("model") or ""), effort=str(job.get("effort") or ""),
        effort_baseline=str((effort or {}).get("baseline") or ""), effort_reason=str((effort or {}).get("reason") or ""),
    )
    return out


def _outcome(job, outcome):
    """accepted | failed (adjudicated) | pending | unknown. Unknown and pending are never failures."""
    if outcome["latency_s"] is not None:
        return "accepted"
    if outcome["pending"]:
        return "pending"
    if outcome["first_pass"] is False:
        return "failed"  # recorded parent rejection or failed check before any acceptance
    if job.get("status") in {"fail", "timeout"} and job.get("execution_mode") != "not_started":
        return "failed"
    return "unknown"  # cancelled, never started, or no recorded parent decision


def _median(values):
    values = [value for value in values if value is not None]
    return {"median": float(statistics.median(values)) if values else None, "known": len(values)}


def _cohort_summary(rows):
    valid = [row for row in rows if row["status"] == "valid"]
    known_first = [row["first_pass"] for row in valid if row["first_pass"] is not None]
    return {
        "runs": len(rows), "valid": len(valid),
        "invalid": {status: sum(row["status"] == status for row in rows)
                    for status in ("missing-job", "cohort-mismatch", "cohort-unverifiable")},
        "outcomes": {name: sum(row["outcome"] == name for row in valid)
                     for name in ("accepted", "failed", "pending", "unknown")},
        "accepted": sum(row["accepted"] for row in valid), "pending": sum(row["pending"] for row in valid),
        "first_pass": {"true": sum(known_first), "known": len(known_first), "unknown": len(valid) - len(known_first)},
        "timing_invalid": sum(row["timing"] in {"parent-start-after-admission", "admission-unknown"} for row in valid),
        "total_s": _median([row["total_s"] for row in valid]),
        "parent_pre_admission_s": _median([row["parent_pre_admission_s"] for row in valid]),
        "worker_execution_s": _median([row["worker_execution_s"] for row in valid]),
        "acceptance_latency_s": _median([row["acceptance_latency_s"] for row in valid]),
        "worker_tokens_total": _median([row["worker_tokens_total"] for row in valid]),
        "parent_tokens_total": _median([row["parent_tokens_total"] for row in valid]),
    }


def _compare(rows, candidate, baseline, min_pairs):
    left = {row["task_id"]: row for row in rows if row["cohort"] == candidate and row["status"] == "valid"}
    right = {row["task_id"]: row for row in rows if row["cohort"] == baseline and row["status"] == "valid"}
    pairs = [(left[task], right[task]) for task in sorted(set(left) & set(right))]
    adjudicated = {"accepted", "failed"}
    decided = [(cand, base) for cand, base in pairs if cand["outcome"] in adjudicated and base["outcome"] in adjudicated]
    # Known regressions stay visible at any sample size; pending/unknown never count as failure.
    regressions = sorted(base["task_id"] for cand, base in decided
                         if base["outcome"] == "accepted" and cand["outcome"] == "failed")
    gains = sorted(base["task_id"] for cand, base in decided
                   if cand["outcome"] == "accepted" and base["outcome"] == "failed")
    first_regressions = sorted(base["task_id"] for cand, base in pairs
                               if base["first_pass"] is True and cand["first_pass"] is False)
    comparable = [(cand, base) for cand, base in pairs
                  if cand["outcome"] == base["outcome"] == "accepted"
                  and cand["first_pass"] is not None and base["first_pass"] is not None]

    def delta(field):
        values = [cand[field] - base[field] for cand, base in comparable
                  if cand[field] is not None and base[field] is not None]
        return {"median_delta": float(statistics.median(values)) if values else None, "pairs": len(values)}

    result = {"candidate": candidate, "baseline": baseline, "pairs": len(pairs), "adjudicated_pairs": len(decided),
              "comparable_pairs": len(comparable), "min_pairs": min_pairs,
              "unresolved_pairs": len(pairs) - len(decided),
              "acceptance_regressions": regressions, "acceptance_gains": gains,
              "first_pass_regressions": first_regressions}
    if regressions or first_regressions:
        result["verdict"] = "quality-regression"
    elif len(comparable) < min_pairs:
        # Requires completed pairs accepted in both cohorts with known first-pass evidence.
        result["verdict"] = "insufficient-evidence"
    else:
        result["verdict"] = "quality-held"
        # Deltas are observations on comparable accepted pairs only; negative means the candidate was lower.
        result["deltas"] = {field: delta(field) for field in (
            "total_s", "parent_pre_admission_s", "worker_execution_s", "worker_tokens_total")}
    return result


def evaluate(repo, value):
    record = normalize(value)
    repo = rig_jobs.repo_root(str(repo))
    rows = [_run(repo, row) for row in record["runs"]]
    return {
        "schema_version": 1, "pilot_id": record["pilot_id"],
        "evidence": "synthetic-fixture" if record["synthetic"] else "recorded-local-jobs",
        "cohorts": {name: {"label": spec["label"], **_cohort_summary([r for r in rows if r["cohort"] == name])}
                    for name, spec in COHORTS.items()},
        "comparisons": [_compare(rows, cand, base, record["min_pairs"]) for cand, base in COMPARISONS],
        "runs": rows, "note": NOTE,
    }


def format_text(result):
    lines = [f"preparation pilot {result['pilot_id']} evidence={result['evidence']}"]
    for name, row in result["cohorts"].items():
        lines.append(f"  {name} ({row['label']}): valid={row['valid']}/{row['runs']} accepted={row['accepted']} "
                     f"first_pass={row['first_pass']['true']}/{row['first_pass']['known']} "
                     f"total_s_median={row['total_s']['median']} (n={row['total_s']['known']})")
    for row in result["comparisons"]:
        lines.append(f"  {row['candidate']} vs {row['baseline']}: pairs={row['pairs']}/{row['min_pairs']} "
                     f"comparable={row['comparable_pairs']} verdict={row['verdict']} "
                     f"regressions={len(row['acceptance_regressions'])} first_pass_regressions={len(row['first_pass_regressions'])}")
    lines.append(result["note"])
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="rig pilot", description=__doc__)
    parser.add_argument("command", choices=["evaluate"])
    parser.add_argument("--file", required=True, help="Bounded pilot JSON listing recorded runs")
    parser.add_argument("--repo", default=".")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        with open(args.file, "rb") as stream:
            raw = stream.read(MAX_INPUT_BYTES + 1)
        if len(raw) > MAX_INPUT_BYTES:
            raise ValueError("pilot input exceeds 256 KiB")
        result = evaluate(args.repo, json.loads(raw, object_pairs_hook=_object))
    except (ValueError, OSError) as error:
        print(f"rig pilot: {error}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2) if args.json else format_text(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
