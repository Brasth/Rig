#!/usr/bin/env python3
"""Bounded, read-only projection of recorded task evidence, never lifecycle authority."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import heapq
import json
import os
from pathlib import Path
import re
import stat

MAX_FILE_BYTES = 256 * 1024
MAX_TOTAL_BYTES = 2 * 1024 * 1024
MAX_FILES = 1024
MAX_DIRECTORY_ENTRIES = 512
MAX_ATTEMPTS = 64
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,199}\Z")
_HASH = re.compile(r"[a-f0-9]{64}\Z")
_SENSITIVE = re.compile(r"(?i)(?:password|passwd|authorization|api[_-]?key|access[_-]?token|secret|bearer|cookie|\b(?:sk-|ghp_|github_pat_|xox[baprs]-)[A-Za-z0-9_-]{12,}|\bAKIA[A-Z0-9]{16}\b)")
EVENT_KINDS = frozenset({"created", "extended", "advanced", "launched", "approved", "resolved",
                         "cancel-requested", "coordination", "coordination_reply", "runtime-state", "runtime-ask"})
STATUSES = frozenset({"planned", "running", "attention", "blocked", "completed-unverified", "verified", "failed",
                     "cancel-requested", "cancelled", "pending", "ready", "launching", "ask", "unconfirmed",
                     "accepted", "skipped", "ok", "fail", "timeout", "passed", "error"})


class TimelineError(ValueError):
    pass


def _id(value):
    return isinstance(value, str) and bool(_ID.fullmatch(value)) and not _SENSITIVE.search(value)


def _hash(value):
    return value if isinstance(value, str) and _HASH.fullmatch(value) else ""


def _stamp(value):
    if not isinstance(value, str) or len(value) > 64:
        return None
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            return None
        return stamp.astimezone(timezone.utc).isoformat()
    except (ValueError, OverflowError):
        return None


class Reader:
    def __init__(self, root):
        self.root = Path(root).resolve(strict=True)
        self.issues = Counter()
        self.bytes = 0
        self.files = 0

    def _open(self, relative, *, directory=False):
        parts = relative.split("/")
        if any(part in {"", ".", ".."} for part in parts):
            raise TimelineError("unsafe timeline source path")
        handle = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            for index, part in enumerate(parts):
                flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
                if directory or index < len(parts) - 1:
                    flags |= os.O_DIRECTORY
                child = os.open(part, flags, dir_fd=handle)
                os.close(handle)
                handle = child
            result, handle = handle, None
            return result
        finally:
            if handle is not None:
                os.close(handle)

    def read(self, relative, *, required=False):
        if self.files >= MAX_FILES or self.bytes >= MAX_TOTAL_BYTES:
            self.issues["input_budget_exceeded"] += 1
            return None, None
        try:
            descriptor = self._open(relative)
            with os.fdopen(descriptor, "rb") as stream:
                before = os.fstat(stream.fileno())
                if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
                    self.issues["unsafe_source"] += 1
                    return None, None
                maximum = min(MAX_FILE_BYTES, MAX_TOTAL_BYTES - self.bytes)
                if before.st_size > maximum:
                    self.issues["input_budget_exceeded"] += 1
                    return None, None
                self.files += 1
                data = stream.read(maximum + 1)
                self.bytes += len(data)
                after = os.fstat(stream.fileno())
            if len(data) > maximum:
                self.issues["input_budget_exceeded"] += 1
                return None, None
            if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                self.issues["source_changed_during_read"] += 1
                return None, None
            value = json.loads(data)
            if not isinstance(value, dict):
                raise ValueError()
            return value, {"path": relative, "sha256": hashlib.sha256(data).hexdigest()}
        except FileNotFoundError:
            if required:
                self.issues["missing_source"] += 1
        except (OSError, ValueError, UnicodeError, RecursionError):
            self.issues["invalid_or_unsafe_source"] += 1
        return None, None

    def names(self, relative):
        try:
            descriptor = self._open(relative, directory=True)
            try:
                with os.scandir(descriptor) as entries:
                    names = []
                    for entry in entries:
                        names.append(entry.name)
                        if len(names) > MAX_DIRECTORY_ENTRIES:
                            self.issues["directory_limit_exceeded"] += 1
                            return []  # Deterministic refusal, not an arbitrary filesystem prefix.
                return sorted(names)
            finally:
                os.close(descriptor)
        except FileNotFoundError:
            return []
        except OSError:
            self.issues["invalid_or_unsafe_source"] += 1
            return []


def _row(lane, order, kind, at, source, **fields):
    return {"lane": lane, "source_order": order, "kind": kind, "at": _stamp(at),
            "source": source, **fields}


def _attempt_binding(reader, item):
    if not isinstance(item, dict):
        reader.issues["incomplete_or_redacted_attempt_binding"] += 1
        return None
    job, attempt = item.get("job_id"), item.get("attempt_id")
    if job in (None, "") and attempt in (None, ""):
        return None  # A legitimate never-launched row carries neither identity.
    if not _id(job) or not _id(attempt):
        reader.issues["incomplete_or_redacted_attempt_binding"] += 1
        return None
    return job, attempt


def _event(reader, path, expected_workflow):
    value, source = reader.read(path, required=True)
    if not value:
        return None, []
    seq, kind = value.get("seq"), value.get("kind")
    if (value.get("version") != 1 or value.get("workflow_id") != expected_workflow or type(seq) is not int or seq < 1
            or not isinstance(kind, str) or kind not in EVENT_KINDS
            or Path(path).name != f"{seq:06d}-{kind}.json"):
        reader.issues["invalid_event"] += 1
        return None, []
    details = {}
    for key in ("node_id", "job_id", "attempt_id", "request_id", "ask_id", "replaces_ask_id"):
        item = value.get(key)
        if item not in (None, ""):
            if not _id(item):
                reader.issues["redacted_identifier"] += 1
                return None, []
            details[key] = item
    for key, allowed in (("status", STATUSES), ("phase", {"open", "close"}), ("action", {"retry", "skip", "fail"}),
                         ("decision", {"reply", "stop"})):
        if isinstance(value.get(key), str) and value[key] in allowed:
            details[key] = value[key]
    if type(value.get("runtime_revision")) is int and value["runtime_revision"] >= 0:
        details["runtime_revision"] = value["runtime_revision"]
    identities = []
    if details.get("job_id") or details.get("attempt_id"):
        binding = _attempt_binding(reader, value)
        if binding:
            identities.append(binding)
        else:
            details["attempt_binding"] = "unavailable_legacy"
            reader.issues["unscoped_event"] += 1
    attempts = value.get("attempts", [])
    if isinstance(attempts, list):
        if len(attempts) > MAX_ATTEMPTS:
            reader.issues["attempt_limit_exceeded"] += 1
        else:
            for item in attempts:
                binding = _attempt_binding(reader, item)
                if binding:
                    identities.append(binding)
    else:
        reader.issues["invalid_attempt_bindings"] += 1
    result = _row("workflow-events", seq, kind, value.get("observed_at") or value.get("at"), source,
                  workflow_id=expected_workflow, details=details)
    return result, identities


def _bound(value, meta, job_id, attempt_id):
    return (value.get("job_id") == job_id and value.get("attempt_id") == attempt_id
            and value.get("reservation_id") == meta.get("reservation_id")
            and (not meta.get("contract_fingerprint") or value.get("contract_fingerprint") == meta["contract_fingerprint"]))


def _refs(reader, values):
    if not isinstance(values, list) or len(values) > 16:
        reader.issues["invalid_artifact_references"] += 1
        return []
    result = []
    for ref in values:
        path = ref.get("path") if isinstance(ref, dict) else None
        if (not isinstance(path, str) or len(path) > 1024 or not path.startswith("evidence/")
                or any(part in {"", ".", ".."} for part in path.split("/")) or "\\" in path
                or _SENSITIVE.search(path) or any(ord(c) < 32 for c in path)
                or not _id(ref.get("kind")) or not _hash(ref.get("sha256"))):
            reader.issues["invalid_or_redacted_artifact_reference"] += 1
            continue
        result.append({"path": path, "sha256": ref["sha256"], "kind": ref["kind"], "validation": "not_revalidated"})
    return result


def _job(reader, job_id, attempt_id, *, workflow_id=""):
    folder = f".rig/jobs/{job_id}"
    meta, _ = reader.read(folder + "/meta.json", required=True)
    if not meta or meta.get("job_id", job_id) != job_id or not _id(meta.get("attempt_id")):
        reader.issues["attempt_unavailable"] += 1
        return [], attempt_id or ""
    selected = attempt_id or meta["attempt_id"]
    if selected != meta["attempt_id"] or (workflow_id and meta.get("workflow_id") != workflow_id):
        reader.issues["attempt_unavailable"] += 1
        return [], selected
    if not _id(meta.get("reservation_id")):
        reader.issues["attempt_unavailable"] += 1
        return [], selected
    rows = []
    lane = f"job:{job_id}:{selected}"
    identity = {"job_id": job_id, "attempt_id": selected}
    for filename in reader.names(folder + "/checks"):
        if not filename.endswith(".json"):
            continue
        if not _id(filename[:-5]):
            reader.issues["redacted_identifier"] += 1
            continue
        check, source = reader.read(folder + "/checks/" + filename, required=True)
        if not check:
            continue
        if not _bound(check, meta, job_id, selected):
            reader.issues["unbound_record"] += 1
            continue
        sequence = check.get("sequence")
        if (check.get("version") != 1 or check.get("check_id") != filename[:-5] or not _id(check.get("requirement_id"))
                or type(sequence) is not int or sequence < 1):
            reader.issues["invalid_check"] += 1
            continue
        details = {"check_id": filename[:-5], "requirement_id": check["requirement_id"],
                   "started_at": _stamp(check.get("started_at")), "ended_at": _stamp(check.get("ended_at"))}
        if isinstance(check.get("status"), str) and check["status"] in STATUSES:
            details["recorded_status"] = check["status"]
        if type(check.get("exit_code")) is int:
            details["exit_code"] = check["exit_code"]
        for key in ("before_snapshot_id", "after_snapshot_id", "contract_fingerprint"):
            if _hash(check.get(key)):
                details[key] = check[key]
        rows.append(_row(lane + ":checks", sequence, "check_record", check.get("ended_at") or check.get("started_at"),
                         source, **identity, details=details))
    for filename in reader.names(folder + "/criteria/history"):
        if not re.fullmatch(r"[a-f0-9]{32}\.json", filename):
            reader.issues["invalid_assertion_filename"] += 1
            continue
        assertion, source = reader.read(folder + "/criteria/history/" + filename, required=True)
        if not assertion:
            continue
        if not _bound(assertion, meta, job_id, selected):
            reader.issues["unbound_record"] += 1
            continue
        if (assertion.get("schema_version") != 1 or assertion.get("assertion_id") != filename[:-5] or not _id(assertion.get("criterion_id"))
                or assertion.get("result") not in ("pass", "fail")
                or not isinstance(assertion.get("provenance"), dict)
                or assertion["provenance"].get("kind") != "parent_assertion"):
            reader.issues["invalid_assertion"] += 1
            continue
        details = {"assertion_id": filename[:-5], "criterion_id": assertion["criterion_id"],
                   "recorded_result": assertion["result"], "provenance": "parent_assertion",
                   "current_validity": "not_revalidated", "evidence_refs": _refs(reader, assertion.get("evidence_refs"))}
        for key in ("snapshot_id", "contract_fingerprint"):
            if _hash(assertion.get(key)):
                details[key] = assertion[key]
        rows.append(_row(lane + ":assertions", filename[:-5], "criterion_assertion_record", assertion.get("recorded_at"),
                         source, **identity, details=details))
    assessment, source = reader.read(folder + "/verification.json")
    if assessment:
        history = assessment.get("history", [])
        if not isinstance(history, list) or len(history) > MAX_DIRECTORY_ENTRIES:
            reader.issues["invalid_or_oversized_history"] += 1
            history = []
        for index, record in enumerate([*history, {key: val for key, val in assessment.items() if key != "history"}]):
            if not isinstance(record, dict) or record.get("acceptance") not in ("accepted", "rejected"):
                continue
            if not _bound(record, meta, job_id, selected):
                reader.issues["unbound_record"] += 1
                continue
            details = {"recorded_decision": record["acceptance"], "current_validity": "not_revalidated"}
            for key in ("snapshot_id", "contract_fingerprint"):
                if _hash(record.get(key)):
                    details[key] = record[key]
            rows.append(_row(lane + ":acceptance", index, "acceptance_record", record.get("assessed_at"),
                             {**source, "record_index": index}, **identity, details=details))
    marker, source = reader.read(folder + f"/cancellation/{selected}.json")
    if marker:
        if (marker.get("job_id"), marker.get("attempt_id"), marker.get("reservation_id")) == (job_id, selected, meta["reservation_id"]):
            rows.append(_row(lane + ":cancellation", 0, "recorded_stop_intent", marker.get("at"), source,
                             **identity, details={"termination": "not_established_by_intent", "authorization": "not_revalidated"}))
        else:
            reader.issues["unbound_record"] += 1
    return rows, selected


def _ordered(rows, reader):
    lanes = {}
    for row in rows:
        lanes.setdefault(row["lane"], []).append(row)
    summaries, streams, untimed = [], {}, []
    for lane, items in sorted(lanes.items()):
        temporal = lane.endswith(":assertions")
        items.sort(key=lambda row: ((row["at"] or "", row["source_order"]) if temporal else row["source_order"]))
        timed = [row for row in items if row["at"] is not None]
        reverse = any(left["at"] > right["at"] for left, right in zip(timed, timed[1:]))
        if reverse:
            reader.issues["clock_reversal"] += 1
        seen = set()
        for row in items:
            if row["source_order"] in seen:
                reader.issues["duplicate_source_sequence"] += 1
            seen.add(row["source_order"])
        untimed.extend(row for row in items if row["at"] is None)
        summaries.append({"id": lane, "order": "recorded_timestamp_then_id" if temporal else "recorded_source_sequence",
                          "clock_reversal": None if temporal else reverse, "records": len(items)})
        if timed:
            streams[lane] = timed
    # K-way merge preserves each source's sequence even when its clock reverses.
    heap = [(items[0]["at"], lane, 0) for lane, items in streams.items()]
    heapq.heapify(heap)
    merged = []
    while heap:
        _, lane, index = heapq.heappop(heap)
        merged.append(streams[lane][index])
        if index + 1 < len(streams[lane]):
            heapq.heappush(heap, (streams[lane][index + 1]["at"], lane, index + 1))
    if untimed:
        reader.issues["unknown_timestamp"] += len(untimed)
    return merged, untimed, summaries


def build(repo, *, workflow_id="", job_id="", attempt_id="", limit=100):
    if os.environ.get("RIG_JOB_ID") or os.environ.get("RIG_JOB_DIR"):
        raise TimelineError("task timelines are parent-only")
    if bool(workflow_id) == bool(job_id) or not _id(workflow_id or job_id):
        raise TimelineError("provide exactly one valid workflow_id or job_id")
    if attempt_id and (workflow_id or not _id(attempt_id)):
        raise TimelineError("attempt_id requires an exact job scope")
    if type(limit) is not int or not 1 <= limit <= 200:
        raise TimelineError("timeline limit must be an integer from 1 to 200")
    reader, rows, attempts = Reader(repo), [], set()
    scope = {"workflow_id": workflow_id} if workflow_id else {"job_id": job_id, "attempt_id": attempt_id}
    if workflow_id:
        folder = f".rig/workflows/{workflow_id}"
        spec, _ = reader.read(folder + "/spec.json", required=True)
        state, _ = reader.read(folder + "/state.json", required=True)
        if state and state.get("workflow_id") != workflow_id:
            reader.issues["workflow_identity_mismatch"] += 1
            state = None
        if spec and spec.get("workflow_id") != workflow_id:
            reader.issues["workflow_identity_mismatch"] += 1
            spec = None
        nodes = state.get("nodes", {}) if state else {}
        if isinstance(nodes, dict) and len(nodes) <= MAX_ATTEMPTS:
            for node in nodes.values():
                binding = _attempt_binding(reader, node)
                if binding:
                    attempts.add(binding)
        else:
            reader.issues["invalid_or_oversized_nodes"] += 1
        for filename in reader.names(folder + "/events"):
            if not re.fullmatch(r"[0-9]+-[a-z_-]+\.json", filename):
                reader.issues["invalid_event_filename"] += 1
                continue
            row, bindings = _event(reader, folder + "/events/" + filename, workflow_id)
            if row:
                rows.append(row)
            attempts.update(bindings)
        sequences = sorted(row["source_order"] for row in rows)
        if sequences and (sequences[0] != 1 or any(right > left + 1 for left, right in zip(sequences, sequences[1:]))):
            reader.issues["missing_event_sequence"] += 1
        revisions = sorted(row["details"]["runtime_revision"] for row in rows if "runtime_revision" in row["details"])
        if revisions and (revisions[0] != 1 or any(right > left + 1 for left, right in zip(revisions, revisions[1:]))):
            reader.issues["runtime_revision_gap"] += 1
        gap, _ = reader.read(folder + "/runtime-gap.json")
        if gap is not None:
            reader.issues["recorded_persistence_gap"] += 1
        marker, source = reader.read(folder + "/cancel.json")
        if (marker and spec and _stamp(spec.get("created_at")) and marker.get("workflow_id") == workflow_id
                and marker.get("created_at") == spec["created_at"]):
            rows.append(_row("workflow-cancellation", 0, "recorded_stop_intent", marker.get("at"), source,
                workflow_id=workflow_id, details={"termination": "not_established_by_intent", "authorization": "not_revalidated"}))
        if len(attempts) > MAX_ATTEMPTS:
            reader.issues["attempt_limit_exceeded"] += 1
            attempts = set(sorted(attempts)[:MAX_ATTEMPTS])
        for jid, aid in sorted(attempts):
            records, _ = _job(reader, jid, aid, workflow_id=workflow_id)
            rows.extend(records)
    else:
        rows, selected = _job(reader, job_id, attempt_id)
        scope["attempt_id"] = selected
        scope["attempt_selection"] = "requested" if attempt_id else "persisted_current"
    timed, untimed, lanes = _ordered(rows, reader)
    all_rows = timed + untimed
    omitted = max(0, len(all_rows) - limit)
    selected = all_rows[omitted:]
    issues = dict(sorted(reader.issues.items()))
    return {"schema_version": 1, "scope": scope, "entries": [row for row in selected if row["at"] is not None],
            "untimed_entries": [row for row in selected if row["at"] is None], "lanes": lanes,
            "coverage": {"state": "partial" if issues or omitted else "recorded" if rows else "unknown",
                         "issues": issues, "observed_records": len(rows), "returned_records": len(selected),
                         "omitted_records": omitted, "input_bytes": reader.bytes, "input_files": reader.files},
            "ordering": "Deterministic display merge preserves source-local sequence; timestamps do not establish causality. Untimed records are separate. Oldest display records are omitted when limited.",
            "limitations": ["Recorded supported evidence only; complete task history is not guaranteed.",
                            "No lifecycle reconciliation or current acceptance validation was performed.",
                            "Latest-only ASK/inbox state and logs are not reconstructed as history."]}


def format_timeline(result):
    coverage = result["coverage"]
    lines = [f"Timeline ({coverage['state']}): {coverage['returned_records']} recorded entries; {coverage['omitted_records']} omitted"]
    for row in result["entries"] + result["untimed_entries"]:
        identity = row.get("job_id") or row.get("workflow_id") or ""
        lines.append(f"{row['at'] or 'time unknown'}  {row['kind']}  {identity}  [{row['source']['path']}]")
    if coverage["issues"]:
        lines.append("Coverage issues: " + ", ".join(coverage["issues"]))
    lines.append("Historical records only; current acceptance and termination are not revalidated.")
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--workflow", default="")
    group.add_argument("--job", default="")
    parser.add_argument("--attempt", default="")
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = build(args.repo, workflow_id=args.workflow, job_id=args.job, attempt_id=args.attempt, limit=args.limit)
    except (OSError, ValueError) as error:
        parser.exit(2, f"rig timeline: {error}\n")
    print(json.dumps(result, ensure_ascii=True) if args.json else format_timeline(result))


if __name__ == "__main__":
    main()
