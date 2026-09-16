#!/usr/bin/env python3
"""Frozen local benchmark specs, parent-only outcomes, and coverage reports."""
from __future__ import annotations

import json
import os
import re
import sys
from decimal import Decimal
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import billing_ledger as ledger
import job_metadata
import jobs as rig_jobs
import token_usage as rig_tokens
import verification

SAFE_ID = re.compile(r"^[A-Za-z0-9._-]+$")
ARMS = frozenset({"rig", "baseline"})
SAVINGS_PAIRS = 20
EDITOR_ID = "opus-openai-editor"


class BenchmarkError(ValueError):
    """Malformed spec, outcome, or ineligible comparison."""


def _parent_only() -> None:
    if os.environ.get("RIG_JOB_ID") or os.environ.get("RIG_JOB_DIR"):
        raise BenchmarkError("benchmark mutations are parent-only")


def spec_dir(repo: Path, benchmark_id: str) -> Path:
    return Path(repo) / ".rig" / "benchmarks" / benchmark_id


def currently_accepted(assessed: dict) -> bool:
    reason = str((assessed or {}).get("reason") or "")
    return (
        assessed.get("acceptance") == "accepted"
        and assessed.get("state") == "verified"
        and assessed.get("freshness") == "current"
        and "content_changed" not in reason
    )


def _validate_spec(value) -> dict:
    if not isinstance(value, dict) or isinstance(value, bool):
        raise BenchmarkError("benchmark spec must be an object")
    spec_id = str(value.get("id") or "").strip()
    tasks, arms = value.get("tasks"), value.get("arms")
    if not spec_id or not SAFE_ID.match(spec_id):
        raise BenchmarkError("benchmark id must be filesystem-safe")
    if not isinstance(tasks, list) or not tasks or any(not isinstance(item, str) or not item.strip() for item in tasks):
        raise BenchmarkError("benchmark tasks must be a nonempty list of strings")
    if len(set(tasks)) != len(tasks) or not isinstance(arms, dict) or set(arms) != ARMS:
        raise BenchmarkError("benchmark needs unique tasks and rig/baseline arms")
    out_arms = {}
    for name in ("rig", "baseline"):
        spec = arms.get(name)
        if not isinstance(spec, dict):
            raise BenchmarkError(f"arm {name} must be an object")
        kind = str(spec.get("kind") or "").strip()
        if name == "baseline" and spec_id == EDITOR_ID and kind != "prose-only":
            raise BenchmarkError("opus-openai-editor baseline arm must be prose-only")
        cohort = str(spec.get("cohort") or name).strip() or name
        if not SAFE_ID.match(cohort):
            raise BenchmarkError(f"arm {name} cohort must be filesystem-safe")
        out_arms[name] = {
            "label": str(spec.get("label") or name),
            "kind": kind or ("prose-only" if name == "baseline" else "rig"),
            "cohort": cohort,
        }
    return {
        "id": spec_id,
        "title": str(value.get("title") or spec_id),
        "frozen": True,
        "tasks": [item.strip() for item in tasks],
        "arms": out_arms,
        "notes": str(value.get("notes") or ""),
    }


def create_spec(repo: Path, value) -> dict:
    _parent_only()
    spec = _validate_spec(value)
    path = spec_dir(repo, spec["id"]) / "spec.json"
    if path.is_file():
        existing = job_metadata.read_json_object(path)
        if existing != spec:
            raise BenchmarkError(f"frozen spec {spec['id']} already exists")
        return spec
    job_metadata.write_json_atomic(path, spec)
    return spec


def load_spec(repo: Path, benchmark_id: str) -> dict:
    path = spec_dir(repo, benchmark_id) / "spec.json"
    if not path.is_file():
        raise BenchmarkError(f"unknown benchmark {benchmark_id}")
    return _validate_spec(job_metadata.read_json_object(path))


def load_outcomes(repo: Path, benchmark_id: str) -> list[dict]:
    path = spec_dir(repo, benchmark_id) / "outcomes.json"
    if not path.is_file():
        return []
    rows = job_metadata.read_json_object(path)
    return list(rows.get("outcomes") or []) if isinstance(rows, dict) else []


def record_outcome(repo: Path, benchmark_id: str, *, job_id: str, task: str, arm: str) -> dict:
    _parent_only()
    spec = load_spec(repo, benchmark_id)
    job_id, task, arm = (job_id or "").strip(), (task or "").strip(), (arm or "").strip()
    if task not in spec["tasks"] or arm not in ARMS:
        raise BenchmarkError("outcome task/arm must match the frozen spec")
    job = rig_jobs.load_job(rig_jobs.jobs_dir(repo) / job_id)
    if not job:
        raise BenchmarkError(f"job not found: {job_id}")
    assessed = verification.assessment(repo, job, refresh=True)
    if not currently_accepted(assessed):
        raise BenchmarkError("outcome requires a currently accepted job")
    row = {
        "job_id": job_id,
        "task": task,
        "arm": arm,
        "snapshot_id": str(assessed.get("snapshot_id") or ""),
        "recorded_at": rig_jobs.iso_now(),
    }
    rows = load_outcomes(repo, benchmark_id)
    for existing in rows:
        if existing.get("job_id") == job_id and existing.get("task") == task and existing.get("arm") == arm:
            return existing
        if existing.get("job_id") == job_id or (existing.get("task") == task and existing.get("arm") == arm):
            raise BenchmarkError("outcome conflicts with an existing attribution")
    rows.append(row)
    job_metadata.write_json_atomic(spec_dir(repo, benchmark_id) / "outcomes.json", {"outcomes": rows})
    return row


def _token_fields(job: dict) -> dict | None:
    usage = rig_tokens.load_token_usage(job.get("token_usage"))
    if not usage:
        return None
    source = str(job.get("token_usage_source") or "")
    try:
        source = rig_tokens.usage_source({"source": source} if source else usage, default=source)
    except rig_tokens.UsageError:
        source = ""
    return {"usage": usage, "source": source}


def _cohort_usd(receipts, cohort: str) -> tuple[bool, str]:
    rows = [row for row in receipts if str(row.get("cohort") or "") == cohort]
    if not rows:
        return False, "0"
    return True, ledger.add_usd(*[row["amount_usd"] for row in rows])


def build_report(repo: Path, benchmark_id: str, *, scope: str = "") -> dict:
    spec = load_spec(repo, benchmark_id)
    name = ledger._scope(repo, scope) if (Path(repo) / ".rig" / "billing.json").is_file() or scope else ""
    receipts = []
    if name or (Path(repo) / ".rig" / "billing").is_dir():
        try:
            name = name or ledger._scope(repo, scope)
            receipts = ledger.load_receipts(repo, scope=name)
        except ledger.LedgerError:
            receipts = []
            name = name or ""
    token_known = token_unknown = 0
    by_arm = {
        arm: {"accepted": 0, "tasks": [], "usd": "0", "covered": False, "cohort": spec["arms"][arm]["cohort"]}
        for arm in ("rig", "baseline")
    }
    current = {}
    for row in load_outcomes(repo, benchmark_id):
        job = rig_jobs.load_job(rig_jobs.jobs_dir(repo) / str(row.get("job_id") or ""))
        assessed = verification.assessment(repo, job, refresh=True) if job else {}
        if not (job and currently_accepted(assessed)):
            continue
        arm = row["arm"]
        by_arm[arm]["accepted"] += 1
        by_arm[arm]["tasks"].append(row["task"])
        tokens = _token_fields(job)
        if tokens:
            token_known += 1
        else:
            token_unknown += 1
        current[(row["task"], arm)] = row
    for arm in ("rig", "baseline"):
        covered, amount = _cohort_usd(receipts, by_arm[arm]["cohort"])
        by_arm[arm]["covered"] = covered
        by_arm[arm]["usd"] = amount if covered else "0"
    rig_tasks, base_tasks = set(by_arm["rig"]["tasks"]), set(by_arm["baseline"]["tasks"])
    pairs = sorted(rig_tasks & base_tasks)
    same_matrix = rig_tasks == base_tasks == set(spec["tasks"])
    pair_count = len(pairs)
    dollar_complete = by_arm["rig"]["covered"] and by_arm["baseline"]["covered"]
    missing = [arm for arm in ("rig", "baseline") if not by_arm[arm]["covered"]]
    if pair_count < SAVINGS_PAIRS:
        quality_gate, quality_reason = "incomplete", f"need exactly {SAVINGS_PAIRS} matched completed pairs"
    elif by_arm["rig"]["accepted"] < by_arm["baseline"]["accepted"]:
        quality_gate, quality_reason = "inferior", "rig quality is inferior to baseline"
    else:
        quality_gate, quality_reason = "non_inferior", ""
    quality_ok = quality_gate == "non_inferior"
    comparable = dollar_complete
    if not dollar_complete:
        savings_reason = "incomplete receipt coverage"
    elif not quality_ok:
        savings_reason = quality_reason
    elif pair_count != SAVINGS_PAIRS:
        savings_reason = f"need exactly {SAVINGS_PAIRS} matched completed pairs"
    elif not same_matrix:
        savings_reason = "task matrix mismatch"
    else:
        savings_reason = ""
    eligible = quality_ok and comparable and pair_count == SAVINGS_PAIRS
    rig_usd = Decimal(ledger.normalize_usd(by_arm["rig"]["usd"])) if by_arm["rig"]["covered"] else None
    base_usd = Decimal(ledger.normalize_usd(by_arm["baseline"]["usd"])) if by_arm["baseline"]["covered"] else None
    cheaper = bool(eligible and rig_usd is not None and base_usd is not None and rig_usd < base_usd)
    claimed = eligible and cheaper
    if eligible and not cheaper:
        savings_reason = "quality is non-inferior and costs are covered, but Rig is not cheaper"
    savings = {
        "eligible": eligible,
        "claimed": claimed,
        "pairs": pair_count,
        "required_pairs": SAVINGS_PAIRS,
        "reason": savings_reason,
    }
    if eligible:
        savings.update(
            rig_usd=by_arm["rig"]["usd"],
            baseline_usd=by_arm["baseline"]["usd"],
            delta_usd=ledger.sub_usd(by_arm["baseline"]["usd"], by_arm["rig"]["usd"]) if cheaper else "0",
        )
    return {
        "benchmark_id": spec["id"],
        "title": spec["title"],
        "scope": name,
        "token_coverage": {
            "known": token_known,
            "unknown": token_unknown,
            "note": "unknown usage is not zero and is excluded from token aggregates",
        },
        "dollar_coverage": {
            "complete": dollar_complete,
            "covered_cohorts": [arm for arm in ("rig", "baseline") if by_arm[arm]["covered"]],
            "missing_cohorts": missing,
            "totals_usd": {"rig": by_arm["rig"]["usd"], "baseline": by_arm["baseline"]["usd"]},
            "job_attribution": "unavailable",
            "note": (
                "actual invoice dollars only; no estimates or subscription amortization. "
                + ledger.JOB_ATTRIBUTION_NOTE
            ),
        },
        "quality": {
            "gate": quality_gate,
            "non_inferior": quality_ok,
            "rig_accepted": by_arm["rig"]["accepted"],
            "baseline_accepted": by_arm["baseline"]["accepted"],
            "reason": quality_reason,
        },
        "acceptance": {"rig": by_arm["rig"]["accepted"], "baseline": by_arm["baseline"]["accepted"]},
        "pairs": pair_count,
        "same_task_matrix": same_matrix,
        "savings": savings,
    }


def format_report(report: dict) -> str:
    tokens, dollars, savings, quality = (
        report["token_coverage"], report["dollar_coverage"], report["savings"], report["quality"],
    )
    extra = f" reason={savings['reason']}" if savings.get("reason") else ""
    return (
        f"benchmark {report['benchmark_id']} pairs={report['pairs']} "
        f"tokens_known={tokens['known']} tokens_unknown={tokens['unknown']} "
        f"dollars_complete={str(dollars['complete']).lower()} "
        f"rig_usd={dollars['totals_usd']['rig']} baseline_usd={dollars['totals_usd']['baseline']} "
        f"quality={quality['gate']} "
        f"savings_claimed={str(savings['claimed']).lower()} "
        f"job_attribution=unavailable "
        f"actual provider cohort dollars cannot be allocated to individual jobs"
        f"{extra}"
    )


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="benchmark_reporting.py")
    parser.add_argument("cmd", choices=["create", "outcome", "report"])
    parser.add_argument("--repo", default="")
    parser.add_argument("--id", default="")
    parser.add_argument("--file", default="")
    parser.add_argument("--job", default="")
    parser.add_argument("--task", default="")
    parser.add_argument("--arm", default="")
    parser.add_argument("--scope", default="")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    repo = rig_jobs.repo_root(args.repo or None)
    try:
        if args.cmd == "create":
            result = create_spec(repo, json.loads(Path(args.file).read_text() if args.file else sys.stdin.read()))
        elif args.cmd == "outcome":
            result = record_outcome(repo, args.id, job_id=args.job, task=args.task, arm=args.arm)
        else:
            result = build_report(repo, args.id, scope=args.scope)
        print(json.dumps(result, indent=2) if args.json or args.cmd != "report" else format_report(result))
    except (BenchmarkError, OSError, json.JSONDecodeError, ledger.LedgerError) as error:
        print(json.dumps({"error": str(error)}) if args.json else str(error), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
