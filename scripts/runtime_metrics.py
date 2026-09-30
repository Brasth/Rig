#!/usr/bin/env python3
"""Pure local evidence aggregation. Metrics are never scheduling authority."""
from __future__ import annotations

import math
import statistics
from datetime import datetime, timezone

import token_usage

TERMINAL_EXECUTION = frozenset({"ok", "fail", "timeout", "cancelled"})
BLOCKED_CAUSES = ("ask", "coordination", "execution-unconfirmed")
DEFINITIONS = {
    "execution_latency": "Terminal ok/fail/timeout execution wall seconds, excluding not_started and cancelled; not provider response time or acceptance latency.",
    "percentiles": "p50 is the median; p90/p95 use nearest rank. Missing, nonfinite and negative durations are excluded, never zero-filled.",
    "outcomes": "Execution, cancellation and parent acceptance are separate. Small samples do not rank models or establish quality.",
    "tokens": "Only reported components are aggregated; component coverage is separate and missing total is never inferred. No prices or savings are estimated.",
    "retry": "Count of explicit resolved/action=retry workflow events; automatic never-started repicks and continuation jobs are separate concepts.",
    "continuation": "Jobs with an explicit continues_job_id, counted once per recorded attempt; not inferred from workflow retry number.",
    "blocked": "Recorded ASK, coordination and execution-unconfirmed intervals only. Workflow wall time is their interval union; per-cause unions overlap and must not be summed. Other blocking causes are unmeasured.",
    "observations": "Coordination/unconfirmed transitions are observed on existing state writes. ASK transitions are recorded at ask/reply. Reports do not refresh or write state. Legacy latest ASK cannot reconstruct earlier prompts.",
}


def parse_stamp(raw):
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        value = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        result = value.timestamp()
        return result if math.isfinite(result) else None
    except (ValueError, OverflowError, OSError):
        return None


def nonnegative(raw):
    if isinstance(raw, bool) or raw in (None, ""):
        return None
    try:
        result = float(raw)
        return result if math.isfinite(result) and result >= 0 else None
    except (TypeError, ValueError, OverflowError):
        return None


def duration(start, end):
    first, last = parse_stamp(start), parse_stamp(end)
    return last - first if first is not None and last is not None and last >= first else None


def execution_latency(job):
    if job.get("status") not in {"ok", "fail", "timeout"} or job.get("execution_mode") == "not_started":
        return None
    # Explicit malformed/reversed boundaries must not be masked by a cached duration.
    for field in ("started_at", "ended_at"):
        raw = job.get(field)
        if raw is not None and raw != "" and parse_stamp(raw) is None:
            return None
    if job.get("started_at") and job.get("ended_at"):
        return duration(job["started_at"], job["ended_at"])
    return nonnegative(job.get("elapsed_s"))


def distribution(values, *, eligible=None):
    samples = sorted(value for raw in values if (value := nonnegative(raw)) is not None)
    count = len(samples)
    population = len(values) if eligible is None else eligible
    return {
        "sample_count": count, "eligible_count": population,
        "missing_count": max(0, population - count),
        "coverage": count / population if population else None,
        "min_s": samples[0] if count else None,
        "p50_s": float(statistics.median(samples)) if count else None,
        "p90_s": samples[max(0, math.ceil(.90 * count) - 1)] if count else None,
        "p95_s": samples[max(0, math.ceil(.95 * count) - 1)] if count else None,
        "max_s": samples[-1] if count else None,
    }


def union_seconds(intervals):
    ordered = sorted((start, end) for start, end in intervals if end >= start)
    total, end = 0.0, None
    for start, stop in ordered:
        if end is None or start > end:
            total += stop - start
        elif stop > end:
            total += stop - end
        end = stop if end is None else max(end, stop)
    return total


def blocked_snapshot(state):
    """Only evidence fields, never prompts, credentials or decision authority."""
    nodes = state.get("nodes") or {}
    attempts = []
    blocked = []
    for node_id, row in sorted(nodes.items()):
        identity = {"node_id": node_id, "job_id": row.get("job_id") or "",
                    "attempt_id": row.get("attempt_id") or "",
                    "workflow_attempt": row.get("workflow_attempt") or 0}
        attempts.append({**identity, "status": row.get("status") or ""})
        if row.get("status") == "unconfirmed":
            blocked.append({**identity, "cause": "execution-unconfirmed", "key": node_id})
    for item in state.get("coordination") or []:
        if item.get("status") != "pending":
            continue
        node_id = item.get("node_id") or ""
        row = nodes.get(node_id) or {}
        blocked.append({"node_id": node_id, "job_id": item.get("job_id") or row.get("job_id") or "",
                        "attempt_id": item.get("attempt_id") or (row.get("attempt_id") if item.get("job_id") == row.get("job_id") else "") or "",
                        "workflow_attempt": row.get("workflow_attempt") or 0,
                        "cause": "coordination", "key": item.get("id") or node_id})
    # Do not keep extending observations past an actually terminal workflow.
    if state.get("status") in {"verified", "cancelled"}:
        blocked = []
    return {"attempts": attempts, "blocked": blocked, "status": state.get("status") or ""}


def _identity(row):
    return (str(row.get("job_id") or ""), str(row.get("attempt_id") or ""))


def blocked_report(state, events, jobs, *, now):
    """Aggregate forward evidence; tolerate damaged timestamps without negative time."""
    metrics = state.get("metrics") or {}
    by_cause = {cause: [] for cause in BLOCKED_CAUSES}
    active, asks, replies = {}, {}, {}
    invalid = 0
    open_count = 0
    seen_states = 0
    expected_revision = 1
    previous_at = None
    covered = bool(metrics.get("runtime_from_creation"))
    terminal = state.get("status") in {"verified", "cancelled"}
    terminal_at = parse_stamp(metrics.get("ended_at")) or parse_stamp(state.get("updated_at"))
    terminal_events = [parse_stamp(event.get("observed_at") or event.get("at")) for event in events
                       if isinstance(event, dict) and event.get("kind") == "runtime-state"
                       and event.get("status") == state.get("status")]
    terminal_events = [value for value in terminal_events if value is not None and value <= now]
    if terminal and terminal_events:
        terminal_at = min(terminal_events + ([terminal_at] if terminal_at is not None else []))
    horizon = min(now, terminal_at) if terminal and terminal_at is not None else now
    if terminal and terminal_at is None:
        covered = False
    job_index = {_identity(job): job for job in jobs if all(_identity(job))}

    def close(item, end, *, is_open=False):
        nonlocal invalid, open_count
        start, info = item
        stop = min(end, horizon)
        job = job_index.get(_identity(info)) or {}
        if is_open and not terminal and (info.get("cause") != "ask" or job.get("status") not in TERMINAL_EXECUTION):
            open_count += 1
        if info.get("cause") == "ask" and is_open and not job:
            invalid += 1
            return
        if info.get("cause") == "ask" and job.get("status") in TERMINAL_EXECUTION:
            ended = parse_stamp(job.get("ended_at"))
            if ended is None:
                invalid += 1
                return
            stop = min(stop, ended)
        if stop < start:
            invalid += 1
            return
        by_cause[info["cause"]].append((start, stop))

    # State event ordering follows durable sequence, not wall clock. A backwards
    # clock is a coverage gap, never a negative interval or reordered authority.
    for event in events:
        if not isinstance(event, dict) or event.get("kind") not in {"runtime-state", "runtime-ask"}:
            continue
        at = parse_stamp(event.get("observed_at") or event.get("at"))
        if at is None or at > now:
            invalid += 1
            continue
        if event["kind"] == "runtime-state":
            seen_states += 1
            revision = event.get("runtime_revision")
            if type(revision) is not int or revision != expected_revision:
                covered = False
            if type(revision) is int:
                expected_revision = revision + 1
            if previous_at is not None and at < previous_at:
                invalid += 1
                active.clear()
                continue
            previous_at = at
            current = {}
            for row in event.get("blocked") or []:
                if not isinstance(row, dict) or row.get("cause") not in {"coordination", "execution-unconfirmed"}:
                    invalid += 1
                    continue
                key = (row["cause"], str(row.get("key") or ""), *_identity(row))
                current[key] = row
            for key in set(active) - set(current):
                close(active.pop(key), at)
            for key, row in current.items():
                active.setdefault(key, (at, row))
        else:
            if (event.get("schema_version") != 1 or not all(_identity(event)) or not event.get("ask_id")
                    or not isinstance(event.get("replaces_ask_id", ""), str)):
                invalid += 1
                continue
            key = (*_identity(event), str(event["ask_id"]))
            if event.get("phase") == "open":
                if key in asks:
                    invalid += 1
                else:
                    asks[key] = (at, {**event, "cause": "ask"})
            elif event.get("phase") == "close":
                if key in replies:
                    invalid += 1
                else:
                    replies[key] = at
            else:
                invalid += 1
    for item in active.values():
        close(item, horizon, is_open=True)
    # ASK event append order can differ from operation order because recording
    # happens outside the ASK lock. Match identities first, then use explicit
    # replacement edges (including equal timestamps) or later observed opens.
    for key, item in asks.items():
        start, info = item
        replacement_times, ambiguous = [], False
        ancestors = set()
        predecessor = info.get("replaces_ask_id") or ""
        cycle = False
        while predecessor:
            if predecessor in ancestors:
                cycle = True
                break
            ancestors.add(predecessor)
            previous = asks.get((*key[:2], predecessor))
            if previous is None:
                invalid += 1
                break
            predecessor = previous[1].get("replaces_ask_id") or ""
        if cycle:
            invalid += 1
            continue
        for other_key, (other_start, other_info) in asks.items():
            if other_key[:2] != key[:2] or other_key == key or other_key[2] in ancestors:
                continue
            if other_info.get("replaces_ask_id") == key[2] or other_start > start:
                replacement_times.append(other_start)
            elif other_start == start and replies.get(other_key, float("inf")) > start:
                ambiguous = True
        end = replies.get(key)
        if end is not None and end < start:
            invalid += 1
            continue
        if replacement_times:
            replacement = min(replacement_times)
            end = min(end, replacement) if end is not None else replacement
        if end is None and ambiguous:
            # Same-clock opens without a causal edge cannot establish which
            # prompt remained live; do not fabricate a long open interval.
            invalid += 1
            continue
        close(item, end if end is not None else horizon, is_open=end is None)
    invalid += len(set(replies) - set(asks))
    if type(metrics.get("runtime_revision")) is not int or metrics.get("runtime_revision") != expected_revision - 1 or not seen_states:
        covered = False
    covered = covered and not invalid
    has_evidence = seen_states or any(by_cause.values())
    coverage = "recorded" if covered else "partial" if has_evidence else "unknown"
    all_intervals = [interval for values in by_cause.values() for interval in values]
    observed = union_seconds(all_intervals) if has_evidence else None
    return {
        "coverage": coverage,
        "scope": "recorded supported causes only; total blocking across all causes is unknown",
        "observed_wall_s": observed,
        "complete_wall_s": None,
        "known_zero": covered and observed == 0,
        "interval_count": len(all_intervals), "open_interval_count": open_count,
        "invalid_event_count": invalid,
        "state_transition_count": seen_states,
        "by_cause": {cause: {"observed_wall_s": union_seconds(values) if has_evidence else None,
                              "interval_count": len(values)} for cause, values in by_cause.items()},
    }


def workflow_metrics(spec, state, events, jobs, *, now):
    events = [event for event in events if isinstance(event, dict)]
    eligible = [job for job in jobs if job.get("status") in {"ok", "fail", "timeout"}
                and job.get("execution_mode") != "not_started"]
    retries = [event for event in events if event.get("kind") == "resolved" and event.get("action") == "retry"]
    usages = [token_usage.load_token_usage(job.get("token_usage")) for job in jobs]
    components = {}
    for field in token_usage.FIELDS:
        values = [usage[field] for usage in usages if usage is not None and field in usage]
        components[field] = {"known": len(values), "unknown": len(jobs) - len(values),
                             "sum": sum(values) if values else None}
    return {
        "definitions": dict(DEFINITIONS),
        "token_coverage": {"known": sum(usage is not None for usage in usages),
                           "unknown": sum(usage is None for usage in usages)},
        "token_components": components,
        "execution_latency": distribution([execution_latency(job) for job in eligible]),
        "attempt_coverage": {"loaded": len(jobs), "note": "Durable current-node and launch/transition job identities; missing historical job files are not reconstructed."},
        "workflow_retry_count": len(retries),
        "retry_coverage": "recorded events only",
        "continuation_count": sum(bool(job.get("continues_job_id")) for job in jobs),
        "cancelled_attempts": sum(job.get("status") == "cancelled" for job in jobs),
        "blocked_time": blocked_report(state, events, jobs, now=now),
    }
