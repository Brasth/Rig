"""Read-only accepted-outcome evidence. Missing history never implies first pass."""
from __future__ import annotations

import json
from pathlib import Path
import re

from runtime_metrics import distribution, duration, parse_stamp

ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,255}")
DEFINITIONS = {
    "attempt_latency": "Reservation created_at to first exact-attempt parent acceptance; excludes pre-admission queue and parent thinking.",
    "workflow_latency": "Recorded workflow created_at to first recorded verified transition; excludes pre-creation work.",
    "first_pass": "Initial accepted attempt with complete bound local history and no prior failed check/criterion, rejection, retry or continuation. Missing history is unknown.",
    "freshness": "Historical first acceptance is separate from current acceptance freshness and is not revalidated by these metrics.",
    "continuations": "Only explicitly linked attempts; missing, cyclic or replaced-attempt evidence is unknown. Later corrections are counted separately.",
    "total_time": "Not measured here: these metrics start at admission and exclude parent preparation. Pilot total parent+worker time is recorded separately by the parent (docs/preparation-pilot.md).",
}


def _read(path):
    """Bound local reads and reject linked artifacts; never return private payloads."""
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_nlink != 1 or path.stat().st_size > 1024 * 1024:
            return None
        value = json.loads(path.read_text())
        return value if isinstance(value, dict) else None
    except (OSError, ValueError):
        return None


def _bound(row, job):
    return (isinstance(row, dict) and bool(job.get("attempt_id")) and bool(job.get("reservation_id"))
            and all(row.get(key) == job.get(key) for key in ("attempt_id", "reservation_id")))


def _folder(repo, job):
    name = job.get("job_id")
    if not isinstance(name, str) or not ID.fullmatch(name) or name in {".", ".."}:
        return None
    folder = repo / ".rig/jobs" / name
    if folder.is_symlink() or any(p.is_symlink() for p in (repo / ".rig", repo / ".rig/jobs")):
        return None
    return folder


def _reservation(repo, job):
    rid = job.get("reservation_id")
    if not isinstance(rid, str) or not ID.fullmatch(rid):
        return None
    base = repo / ".rig/reservations"
    if base.is_symlink():
        return None
    row = _read(base / (rid + ".json"))
    return row if row and _bound(row, job) and row.get("job_id") == job.get("job_id") else None


def attempt(repo, job):
    """Aggregate exact-bound history only; source contents never leave the report."""
    repo = Path(repo).resolve()
    folder = _folder(repo, job)
    reservation = _reservation(repo, job) if folder else None
    result = {"latency_s": None, "accepted_at": None, "first_pass": None,
              "pending": job.get("status") in {"running", "ask"}, "coverage": "unknown",
              "continuation": bool(job.get("continues_job_id")), "admitted_at": None}
    if reservation is None:
        return result
    result["admitted_at"] = reservation.get("created_at")
    result["continuation"] = bool(job.get("continues_job_id") or reservation.get("continues_job_id"))
    stored = _read(folder / "verification.json")
    if not stored:
        return result
    history = stored.get("history")
    rows = (history if isinstance(history, list) else []) + [stored]
    accepted = [row for row in rows if _bound(row, job) and row.get("acceptance") == "accepted"
                and row.get("state") == "verified" and parse_stamp(row.get("accepted_at")) is not None]
    if not accepted:
        if any(_bound(row, job) and row.get("acceptance") == "rejected"
               and duration(result["admitted_at"], row.get("assessed_at")) is not None for row in rows):
            result.update(first_pass=False, coverage="partial")
        return result
    first = min(accepted, key=lambda row: parse_stamp(row["accepted_at"]))
    start, end = parse_stamp(result["admitted_at"]), parse_stamp(first["accepted_at"])
    if start is None or end < start:
        return result
    result.update(latency_s=end - start, accepted_at=first["accepted_at"], pending=False, coverage="partial")
    before = []
    complete = isinstance(history, list) and bool(history)
    for row in rows:
        at = parse_stamp(row.get("assessed_at")) if isinstance(row, dict) else None
        if not _bound(row, job) or at is None:
            complete = False
            continue
        if start <= at <= end:
            before.append(row)
    failed = any(row.get("acceptance") == "rejected" or row.get("state") == "failed" for row in before)
    checks = folder / "checks"
    records = []
    if checks.is_symlink():
        complete = False
    else:
        paths = sorted(checks.glob("*.json"))
        complete &= len(paths) <= 4096
        for path in paths[:4096]:
            row = _read(path)
            if not row or not _bound(row, job) or parse_stamp(row.get("started_at")) is None:
                complete = False
                continue
            if parse_stamp(row["started_at"]) <= end:
                records.append(row)
                failed |= row.get("status") in {"failed", "error"}
        sequences = [row.get("sequence") for row in records]
        if any(type(n) is not int for n in sequences) or sorted(sequences) != list(range(1, len(records) + 1)):
            complete = False
        check_ids = first.get("check_ids")
        if check_ids is not None and (not isinstance(check_ids, list) or not all(isinstance(item, str) for item in check_ids) or
                not set(check_ids) <= {row.get("check_id") for row in records}):
            complete = False
    assertions = folder / "criteria/history"
    if (folder / "criteria").is_symlink() or assertions.is_symlink():
        complete = False
    else:
        paths = sorted(assertions.glob("*.json"))
        complete &= len(paths) <= 4096
        for path in paths[:4096]:
            row = _read(path)
            at = parse_stamp(row.get("recorded_at")) if row else None
            if not row or not _bound(row, job) or at is None:
                complete = False
            elif at <= end:
                failed |= row.get("result") == "fail"
    manifest = _read(folder / "requirements.json")
    if not manifest or manifest.get("job_id") != job.get("job_id"):
        complete = False
    elif not isinstance(manifest.get("requirements"), list):
        complete = False
    elif any(not isinstance(item, dict) or not any(row.get("requirement_id") == item.get("id") for row in records)
             for item in manifest["requirements"]):
        complete = False
    # A linked correction is explicitly non-first-pass even when older history is missing.
    if failed or result["continuation"]:
        result["first_pass"] = False
    elif complete:
        result.update(first_pass=True, coverage="recorded")
    return result


def _summary(rows):
    measured = [row["latency_s"] for row in rows if row["latency_s"] is not None]
    known = [row["first_pass"] for row in rows if row["first_pass"] is not None]
    pending = sum(row["pending"] for row in rows)
    return {"latency": distribution(measured, eligible=len(rows)), "eligible": len(rows),
            "measured": len(measured), "pending": pending,
            "missing": sum(row["latency_s"] is None and not row["pending"] for row in rows),
            "first_pass": {"accepted": sum(value is True for value in known), "assessed": len(known),
                           "unknown": len(rows) - len(known),
                           "rate": sum(value is True for value in known) / len(known) if known else None}}


def report(repo, jobs):
    repo = Path(repo).resolve()
    results = [attempt(repo, job) for job in jobs]
    # A chain can cross the report window; follow explicit ancestors only, with a hard bound.
    index = {job.get("job_id"): job for job in jobs}
    chains = {}
    for job in jobs:
        chain, seen, current = [], set(), job
        valid = True
        for _ in range(128):
            name = current.get("job_id")
            if not name or name in seen:
                valid = False
                break
            seen.add(name)
            chain.append(current)
            reservation = _reservation(repo, current)
            parent = current.get("continues_job_id") or (reservation or {}).get("continues_job_id")
            if not parent:
                break
            previous = index.get(parent)
            if previous is None:
                folder = _folder(repo, {"job_id": parent})
                previous = _read(folder / "meta.json") if folder else None
            if (not previous or previous.get("job_id") != parent or
                    (reservation and reservation.get("continues_reservation_id") and
                     reservation["continues_reservation_id"] != previous.get("reservation_id"))):
                valid = False
                break
            current = previous
        else:
            valid = False
        if len(chain) == 1 and not attempt(repo, chain[0])["continuation"]:
            continue
        key = chain[-1].get("job_id") if valid else job.get("job_id")
        samples = [attempt(repo, item) for item in chain]
        firsts = [row["accepted_at"] for row in samples if row["accepted_at"]]
        latency = duration(samples[-1]["admitted_at"], min(firsts, key=parse_stamp)) if valid and firsts else None
        previous_latency = chains.get(key, {}).get("latency_s")
        if previous_latency is not None:
            latency = min(latency, previous_latency) if latency is not None else previous_latency
        chains[key] = {"latency_s": latency, "first_pass": None,
                       "pending": any(row["pending"] for row in samples)}
    return {"definitions": dict(DEFINITIONS), "attempts": _summary(results),
            "continuation_chains": _summary(list(chains.values())),
            "continuation_count": sum(row["continuation"] for row in results),
            "later_corrections": sum(row["continuation"] for row in results)}


def workflow_report(repo, spec, state, events, jobs):
    result = report(repo, jobs)
    endpoints = [event.get("observed_at") or event.get("at") for event in events
                 if event.get("kind") == "runtime-state" and event.get("status") == "verified"]
    endpoints = [stamp for stamp in endpoints if parse_stamp(stamp) is not None]
    first = min(endpoints, key=parse_stamp) if endpoints else None
    retries = sum(event.get("kind") == "resolved" and event.get("action") == "retry" for event in events)
    latency = duration(spec.get("created_at") or state.get("created_at"), first)
    pending = state.get("status") not in {"verified", "failed", "cancelled"}
    # Complete forward transition coverage is needed to describe a clean first pass.
    revisions = [event.get("runtime_revision") for event in events if event.get("kind") == "runtime-state"]
    complete = (state.get("metrics", {}).get("runtime_from_creation") is True
                and revisions == list(range(1, len(revisions) + 1)) and bool(revisions))
    assessed = result["attempts"]["first_pass"]
    first_pass = False if retries or result["continuation_count"] or assessed["assessed"] > assessed["accepted"] else (
        True if first and complete and assessed["unknown"] == 0 else None)
    result.update(workflow={"latency_s": latency, "first_verified_at": first,
                            "pending": pending, "first_pass": first_pass}, retry_count=retries)
    return result
