#!/usr/bin/env python3
"""Replacement-parent recovery of a cancelled native child after the original owner died.

Normal admission _auth stays same-actor. This path matches exact credentials, then
requires a dead original parent CLI (pid+start), cancelled intent, and host evidence.
Callers cannot attest terminal=true.
"""
from __future__ import annotations

import copy
import os
from pathlib import Path

import admission
import cancellation
import change_evidence as evidence
import codex_native_evidence
import jobs

RECOVERY_OPERATION = "recover_cancelled"
PHASES = ("started", "execution_recorded", "audited", "released", "complete")
TERMINAL_OK = frozenset({"ok", "fail", "timeout"})


class NativeRecoveryError(admission.AdmissionError):
    pass


def _parent_only():
    if os.environ.get("RIG_JOB_ID") or os.environ.get("RIG_JOB_DIR"):
        raise NativeRecoveryError("cancelled native recovery is parent-only")


def _public_recovery(record):
    recovery = copy.deepcopy(record.get("recovery") or {})
    recovery.pop("owner_token", None)
    return recovery


def _job_dir(root, job_id):
    return root / ".rig" / "jobs" / admission._id(job_id, "job id")


def _audit_path(root, record):
    return _job_dir(root, record["job_id"]) / "recovery-audit.json"


def _active_check(root, record):
    if record.get("operation"):
        return True
    folder = _job_dir(root, record["job_id"])
    return (folder / "check-running.json").is_file()


def _cancel_intent(root, record):
    return cancellation.requested(_job_dir(root, record["job_id"]),
                                  attempt_id=record.get("attempt_id", ""),
                                  reservation_id=record.get("reservation_id", ""))


def _native_child(record, meta):
    owner = record.get("owner") or {}
    return owner.get("kind") == "native_child" and str(meta.get("executor_kind") or "") == "native_child"


def _known_agent(record, meta):
    agent = str((record.get("owner") or {}).get("native_agent_id") or meta.get("native_agent_id") or "").strip()
    return agent


def _parent_thread(record, meta):
    owner = record.get("owner") or {}
    return str(owner.get("session_id") or meta.get("thread") or "").strip()


def _host_report(root, record, meta, agent):
    parent_thread = _parent_thread(record, meta)
    binding = record.get("host_binding") or meta.get("host_binding") or {}
    parent_thread = parent_thread or str(binding.get("parent_thread_id") or "").strip()
    path = str(binding.get("agent_path") or meta.get("agent_path") or "").strip()
    return codex_native_evidence.inspect_native_child(
        parent_thread_id=parent_thread,
        agent_id=agent,
        agent_path=path,
        launched_at=record.get("created_at") or meta.get("started_at"),
        cwd=str(root),
    )


def _original_parent_state(owner):
    owner = owner or {}
    pid = owner.get("parent_pid")
    start = str(owner.get("parent_start_id") or "").strip()
    if not isinstance(pid, int) or pid <= 0 or not start:
        return "unknown"
    return admission._process_state({"pid": pid, "start_id": start})


def _audit(record, replacement, evidence_report, rationale):
    owner = record.get("owner") or {}
    identity = evidence_report.get("identity") or {}
    return {
        "original": {
            "kind": owner.get("kind"),
            "session_id": owner.get("session_id") or "",
            "parent_pid": owner.get("parent_pid"),
            "parent_start_id": owner.get("parent_start_id") or "",
            "initiating_identity": owner.get("initiating_identity") or "",
        },
        "replacement": {
            "kind": replacement.get("kind"),
            "session_id": replacement.get("session_id") or "",
            "parent_pid": replacement.get("parent_pid"),
            "parent_start_id": replacement.get("parent_start_id") or "",
            "initiating_identity": replacement.get("initiating_identity") or "",
        },
        "attempt_id": record.get("attempt_id"),
        "reservation_id": record.get("reservation_id"),
        "job_id": record.get("job_id"),
        "evidence": {
            "digest": evidence_report.get("digest") or "",
            "reference": evidence_report.get("reference") or {},
            "parent_thread_id": identity.get("parent_thread_id") or "",
            "child_thread_id": identity.get("child_thread_id") or "",
            "agent_id": identity.get("agent_id") or "",
            "agent_path": identity.get("agent_path") or "",
            "edge_status": evidence_report.get("edge_status") or "",
        },
        "rationale": rationale.strip(),
        "at": admission._now(),
        "outcome": "cancelled",
    }


def _complete_audit(root, record):
    path = _audit_path(root, record)
    value = admission._read(path)
    if not value:
        return None
    if value.get("attempt_id") != record.get("attempt_id"):
        return None
    if value.get("outcome") != "cancelled":
        return None
    return value


def _preserve_audit(record, audit):
    recovery = record.get("recovery") or {}
    existing = {}
    if recovery.get("at"):
        existing["at"] = recovery["at"]
    if recovery.get("original"):
        existing["original"] = recovery["original"]
    if recovery.get("replacement"):
        existing["replacement"] = recovery["replacement"]
    if recovery.get("rationale"):
        existing["rationale"] = recovery["rationale"]
    audit.update({key: value for key, value in existing.items() if value not in (None, "", {}, [])})
    return audit


def inspect(repo, *, job_id, reservation_id, attempt_id, owner_token, rationale="", owner=None, owner_session=""):
    root = admission._root(repo)
    if not isinstance(rationale, str):
        raise NativeRecoveryError("recovery rationale required")
    record = admission.match_credentials(root, reservation_id, attempt_id, owner_token, job_id)
    meta = jobs._read_meta_dict(_job_dir(root, record["job_id"]))
    recovery = record.get("recovery") or {}
    result = {
        "eligible": False,
        "job_id": record.get("job_id"),
        "attempt_id": record.get("attempt_id"),
        "reservation_id": record.get("reservation_id"),
        "stage": record.get("stage"),
        "execution_status": record.get("execution_status"),
        "recovery": _public_recovery(record),
        "evidence": {},
        "reason": "",
    }
    persisted = _complete_audit(root, record)
    if recovery.get("phase") == "complete" and recovery.get("outcome") == "cancelled" and persisted:
        result.update(eligible=True, reason="recovery already complete", already_complete=True)
        return result
    if record.get("stopped") and record.get("execution_status") in TERMINAL_OK:
        result["reason"] = "confirmed execution status is immutable"
        return result
    if not _native_child(record, meta):
        result["reason"] = "recovery requires a native child attempt"
        return result
    if not _cancel_intent(root, record):
        result["reason"] = "exact cancelled intent is required"
        return result
    agent = _known_agent(record, meta)
    if not agent:
        result["reason"] = "native agent identity is unknown"
        return result
    parent_state = _original_parent_state(record.get("owner") or {})
    if parent_state != "dead":
        result["reason"] = "original parent is live or unknown"
        return result
    if _active_check(root, record):
        result["reason"] = "an active or interrupted verification operation still holds this reservation"
        return result
    pending = record.get("pending_operation")
    if pending and pending != RECOVERY_OPERATION:
        result["reason"] = "another durable operation is in progress"
        return result
    if recovery and recovery.get("attempt_id") not in {None, "", record.get("attempt_id")}:
        result["reason"] = "recorded recovery belongs to another attempt"
        return result
    report = _host_report(root, record, meta, agent)
    result["evidence"] = {key: report.get(key) for key in ("status", "reason", "detail", "identity", "edge_status", "digest", "reference")}
    if report.get("status") == "live":
        result["reason"] = "native child is live"
        return result
    if report.get("status") != "verified" or not report.get("digest"):
        result["reason"] = report.get("reason") or codex_native_evidence.UNAVAILABLE
        return result
    if recovery.get("digest") and recovery.get("digest") != report.get("digest") and recovery.get("phase") in PHASES:
        result["reason"] = "host evidence changed during recorded recovery"
        return result
    result.update(eligible=True, reason="")
    return result


def _write_execution(root, record, replacement):
    folder = _job_dir(root, record["job_id"])
    meta = jobs._read_meta_dict(folder)
    now = jobs.iso_now()
    started = jobs._started_at(folder, meta, now)
    jobs.write_job_files(
        folder, record["job_id"], str(meta.get("worker") or record.get("worker") or "codex"),
        str(meta.get("role") or record.get("role") or "worker"), "cancelled", 130, started, now,
        "Cancelled native child recovered after original parent died", str(meta.get("kind") or "native"),
        thread=str(meta.get("thread") or ""), model=str(meta.get("model") or record.get("model") or ""),
        effort=str(meta.get("effort") or ""), executor_kind="native_child", execution_mode="native",
        capture_evidence=True,
    )
    jobs.write_state(root, record["job_id"], str(meta.get("worker") or record.get("worker") or "codex"),
                     "cancelled", "Cancelled native child recovered after original parent died")
    binding = ((record.get("recovery") or {}).get("evidence") or {})
    if binding.get("child_thread_id") and not (record.get("host_binding") or {}).get("child_thread_id"):
        record["host_binding"] = {
            "parent_thread_id": binding.get("parent_thread_id") or "",
            "child_thread_id": binding.get("child_thread_id") or "",
            "agent_path": binding.get("agent_path") or "",
            "digest": binding.get("digest") or "",
        }


def _phase(record, phase, audit):
    recovery = dict(record.get("recovery") or {})
    if recovery.get("outcome") not in {None, "", "cancelled"}:
        raise NativeRecoveryError("recorded recovery outcome is immutable")
    recovery.update(phase=phase, outcome="cancelled", attempt_id=record.get("attempt_id"),
                    reservation_id=record.get("reservation_id"), job_id=record.get("job_id"),
                    digest=(audit.get("evidence") or {}).get("digest") or recovery.get("digest") or "",
                    evidence=audit.get("evidence") or recovery.get("evidence") or {},
                    original=recovery.get("original") or audit.get("original") or {},
                    replacement=recovery.get("replacement") or audit.get("replacement") or {},
                    rationale=recovery.get("rationale") or audit.get("rationale") or "",
                    at=recovery.get("at") or audit.get("at") or admission._now())
    record["recovery"] = recovery
    record["pending_operation"] = RECOVERY_OPERATION


def _write_audit(root, record, audit):
    path = _audit_path(root, record)
    existing = admission._read(path)
    if existing and existing.get("attempt_id") == record.get("attempt_id") and existing.get("outcome") == "cancelled":
        audit["at"] = existing.get("at") or audit.get("at")
        audit["original"] = existing.get("original") or audit.get("original")
        audit["replacement"] = existing.get("replacement") or audit.get("replacement")
        audit["rationale"] = existing.get("rationale") or audit.get("rationale")
    evidence.write_json(path, audit)


def apply_recovery(repo, *, job_id, reservation_id, attempt_id, owner_token, rationale, owner=None, owner_session=""):
    root = admission._root(repo)
    record = admission.match_credentials(root, reservation_id, attempt_id, owner_token, job_id)
    replacement = admission._owner(owner, owner_session, "parent")
    snapshot = inspect(root, job_id=job_id, reservation_id=reservation_id, attempt_id=attempt_id,
                       owner_token=owner_token, rationale=rationale, owner=replacement, owner_session=owner_session)
    if snapshot.get("already_complete"):
        return {"applied": 0, "items": [admission._public(record)], "recovery": snapshot["recovery"]}
    if not snapshot.get("eligible"):
        raise NativeRecoveryError(snapshot.get("reason") or "recovery is not eligible")
    if record.get("stopped") and record.get("execution_status") in TERMINAL_OK:
        raise NativeRecoveryError("confirmed execution status is immutable")
    audit = _preserve_audit(record, _audit(record, replacement, snapshot.get("evidence") or {}, rationale))
    recovery = record.get("recovery") or {}
    phase = recovery.get("phase") or ""
    if phase not in PHASES:
        _phase(record, "started", audit)
        admission._save(root, record)
        phase = "started"
    if phase == "started":
        record.update(stopped=True, slot_held=False, needs_reconciliation=False, reconciliation_reason="",
                      execution_status="cancelled", stage="verifying",
                      completion={"kind": "host_registry", "host": "codex", "outcome": "cancelled",
                                  "evidence_digest": audit["evidence"].get("digest") or "",
                                  "agent_id": audit["evidence"].get("agent_id") or ""})
        _write_execution(root, record, replacement)
        _phase(record, "execution_recorded", audit)
        admission._save(root, record)
        phase = "execution_recorded"
    if phase == "execution_recorded":
        _write_audit(root, record, audit)
        _phase(record, "audited", audit)
        admission._save(root, record)
        phase = "audited"
    if phase == "audited":
        record.update(stage="released", slot_held=False, stopped=True, release_reason=rationale.strip(),
                      needs_reconciliation=False, reconciliation_reason="")
        _phase(record, "released", audit)
        admission._save(root, record)
        phase = "released"
    if phase in {"released", "complete"}:
        if _complete_audit(root, record) is None:
            _write_audit(root, record, audit)
        if phase == "released":
            admission._queue_update(root, record, "cancelled")
            _phase(record, "complete", audit)
            record.pop("pending_operation", None)
            admission._save(root, record)
        elif record.get("pending_operation") == RECOVERY_OPERATION:
            record.pop("pending_operation", None)
            admission._save(root, record)
    return {"applied": 1, "items": [admission._public(record)], "recovery": _public_recovery(record)}


def recover_cancelled(repo, *, job_id, reservation_id, attempt_id, owner_token, rationale,
                      owner=None, owner_session="", apply=False, **extra):
    _parent_only()
    if extra:
        raise NativeRecoveryError("public recovery API does not accept evidence file paths")
    if not isinstance(rationale, str) or not rationale.strip():
        raise NativeRecoveryError("recovery rationale required")
    if not isinstance(apply, bool):
        raise NativeRecoveryError("apply must be a boolean")
    repo = Path(repo)
    with admission.transaction(repo) as root:
        snapshot = inspect(root, job_id=job_id, reservation_id=reservation_id, attempt_id=attempt_id,
                           owner_token=owner_token, rationale=rationale, owner=owner, owner_session=owner_session)
        if snapshot.get("already_complete"):
            record = admission.match_credentials(root, reservation_id, attempt_id, owner_token, job_id)
            return {"applied": 0, "dry_run": not apply, "items": [admission._public(record)], **snapshot}
        if not snapshot.get("eligible"):
            raise NativeRecoveryError(snapshot.get("reason") or "recovery is not eligible")
        if not apply:
            return {"applied": 0, "dry_run": True, **snapshot}
        result = apply_recovery(root, job_id=job_id, reservation_id=reservation_id, attempt_id=attempt_id,
                                owner_token=owner_token, rationale=rationale, owner=owner, owner_session=owner_session)
        result.update(dry_run=False, eligible=True, evidence=snapshot.get("evidence") or {}, job_id=job_id)
        return result
