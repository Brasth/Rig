#!/usr/bin/env python3
"""Optional, immutable job acceptance contracts on the existing admission boundary.

Fingerprints detect drift and bind evidence; they are not signatures or a sandbox
against other processes running as the same user. No capability is granted here.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from pathlib import Path

import change_evidence as evidence

MAX_CRITERIA = 64
MAX_CONTRACT_BYTES = 262144
MAX_ARTIFACT_BYTES = 16 * 1024 * 1024
BINDING_KEYS = ("job_id", "reservation_id", "attempt_id", "contract_fingerprint")


class ContractError(ValueError):
    pass


def name(value, label="ID"):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,100}", value):
        raise ContractError(f"{label} must be a simple nonempty name (at most 101 characters)")
    return value


def text(value, label, limit=4096):
    if not isinstance(value, str) or not value.strip() or len(value) > limit or "\0" in value:
        raise ContractError(f"{label} must be nonempty text (at most {limit} characters)")
    return value.strip()


def _keys(value, allowed, required, label):
    if not isinstance(value, dict) or set(value) - set(allowed) or set(required) - set(value):
        raise ContractError(f"{label} has missing or unsupported fields")


def fingerprint(contract):
    return hashlib.sha256(json.dumps(contract, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=True).encode()).hexdigest()


def normalize(repo, value, files, *, independent_review=False):
    _keys(value, {"schema_version", "contract_id", "revision", "criteria"},
          {"schema_version", "contract_id", "revision", "criteria"}, "acceptance contract")
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise ContractError("unsupported acceptance contract schema_version; expected 1")
    revision = value["revision"]
    if type(revision) is not int or not 1 <= revision <= 1000000:
        raise ContractError("contract revision must be an integer from 1 to 1000000")
    rows = value["criteria"]
    if not isinstance(rows, list) or not 1 <= len(rows) <= MAX_CRITERIA:
        raise ContractError(f"contract requires 1 to {MAX_CRITERIA} criteria")
    allowed_scope = set(evidence.normalize_files(repo, files))
    criteria, ids, checks = [], set(), {}
    for row in rows:
        common = {"id", "description", "scope", "evidence_type", "verifier_role"}
        _keys(row, common | {"check", "artifact_kind"}, common, "criterion")
        ident = name(row["id"], "criterion ID")
        if ident in ids:
            raise ContractError("criterion IDs must be unique")
        ids.add(ident)
        raw_scope = row["scope"]
        if (not isinstance(raw_scope, list) or not 1 <= len(raw_scope) <= 256
                or any(not isinstance(p, str) or Path(p).is_absolute() or ".." in Path(p).parts
                       or Path(p).parts[:1] in {('.rig',), ('.git',)} for p in raw_scope)):
            raise ContractError("criterion scope requires bounded repo-relative source paths")
        scope = evidence.normalize_files(repo, raw_scope)
        root = evidence.repository(repo)
        for subject in scope:
            if Path(subject).parts[0].casefold() in {".rig", ".git"}:
                raise ContractError("criterion scope cannot include repository control files")
            try:
                resolved = (root / subject).resolve()
                target = resolved.relative_to(root)
            except (OSError, RuntimeError, ValueError) as error:
                raise ContractError("criterion scope target must stay inside the repository") from error
            if not target.parts or target.parts[0].casefold() in {".rig", ".git"} or resolved.is_dir():
                raise ContractError("criterion scope target cannot be a control file or directory")
        if not set(scope) <= allowed_scope:
            raise ContractError("criterion scope exceeds admitted files")
        verifier = row["verifier_role"]
        if not isinstance(verifier, str) or verifier not in {"parent", "independent-review"}:
            raise ContractError("verifier_role must be parent or independent-review")
        if verifier == "independent-review" and not independent_review:
            raise ContractError("independent-review criteria require a separately admitted independent reviewer stage")
        item = {"id": ident, "description": text(row["description"], "criterion description", 2048),
                "scope": scope, "evidence_type": row["evidence_type"], "verifier_role": verifier}
        if item["evidence_type"] == "check":
            if "artifact_kind" in row:
                raise ContractError("check criteria cannot use review artifacts")
            check = row.get("check")
            _keys(check, {"id", "argv", "cwd"}, {"id", "argv"}, "criterion check")
            argv = check["argv"]
            if (not isinstance(argv, list) or not 1 <= len(argv) <= 128
                    or any(not isinstance(arg, str) or len(arg) > 8192 or "\0" in arg for arg in argv)
                    or not argv[0]):
                raise ContractError("criterion check argv must be a bounded nonempty string array")
            check = {"id": name(check["id"], "check ID"), "argv": list(argv),
                     "cwd": evidence.normalize_cwd(repo, check.get("cwd"))}
            if check["id"] in checks and checks[check["id"]] != check:
                raise ContractError("shared check IDs must have identical argv and cwd")
            checks[check["id"]] = check
            item["check"] = check
        elif item["evidence_type"] == "review_assertion":
            if "check" in row:
                raise ContractError("review assertions cannot masquerade as objective checks")
            item["artifact_kind"] = name(row.get("artifact_kind"), "artifact kind")
        else:
            raise ContractError("evidence_type must be check or review_assertion")
        criteria.append(item)
    result = {"schema_version": 1, "contract_id": name(value["contract_id"], "contract ID"),
              "revision": revision, "criteria": sorted(criteria, key=lambda row: row["id"])}
    if len(json.dumps(result).encode()) > MAX_CONTRACT_BYTES:
        raise ContractError("acceptance contract exceeds 256 KiB")
    return result


def requirements(contract):
    checks = {row["check"]["id"]: row["check"] for row in contract["criteria"] if row["evidence_type"] == "check"}
    return {"requirements": [checks[key] for key in sorted(checks)],
            "manual_criteria": list(dict.fromkeys(row["description"] for row in contract["criteria"]
                                               if row["evidence_type"] == "review_assertion"))}


def initialize(folder, reservation):
    """Called by launch/start before activation, never on a historical job."""
    contract = reservation.get("acceptance_contract")
    if contract is None:
        return
    binding = {key: reservation[key] for key in BINDING_KEYS}
    value = {"schema_version": 1, **binding, "frozen_at": reservation["created_at"], "contract": contract}
    path = Path(folder) / "acceptance-contract.json"
    if path.exists():
        if evidence.read_json(path) != value:
            raise ContractError("frozen acceptance contract changed")
        return
    evidence.write_json(path, value)
    evidence.write_json(Path(folder) / "requirements.json", {
        "version": 1, **binding, **requirements(contract),
        "created_at": value["frozen_at"], "updated_at": value["frozen_at"],
    })


def _reservation_binding(repo, meta):
    import admission
    record = admission.get_reservation(repo, meta.get("reservation_id", "")) or {}
    # Independent review rotates an attempt inside the same reservation and keeps
    # the writer in history. Its contract must remain inspectable after handoff.
    candidates = [record, *record.get("history", [])]
    return next((row for row in candidates if isinstance(row, dict)
                 and all(row.get(key) == meta.get(key) for key in ("job_id", "attempt_id"))), {})


def load(repo, folder, meta=None):
    folder = Path(folder)
    meta = meta if meta is not None else evidence.read_json(folder / "meta.json") or {}
    path = folder / "acceptance-contract.json"
    expected = meta.get("contract_fingerprint")
    if not expected and not path.exists():
        if meta.get("reservation_id") and _reservation_binding(repo, meta).get("acceptance_contract") is not None:
            raise ContractError("admitted acceptance contract is missing")
        return None
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_CONTRACT_BYTES + 4096:
        raise ContractError("acceptance contract artifact is missing, unsafe or oversized")
    saved = evidence.read_json(path)
    if not saved or type(saved.get("schema_version")) is not int or saved["schema_version"] != 1 or not expected:
        raise ContractError("acceptance contract artifact is malformed or not admitted")
    record = _reservation_binding(repo, meta)
    contract = normalize(repo, saved.get("contract"), meta.get("files", []),
                         independent_review=bool(record.get("writer_job_id") and record.get("writer_snapshot_id")))
    if (fingerprint(contract) != expected or saved.get("contract") != contract
            or record.get("acceptance_contract") != contract
            or record.get("contract_fingerprint") != expected
            or any(saved.get(key) != meta.get(key) for key in BINDING_KEYS)):
        raise ContractError("acceptance contract changed or belongs to another attempt")
    return saved


def validate_manifest(frozen, manifest):
    if frozen is None:
        return
    wanted = requirements(frozen["contract"])
    if (any(manifest.get(key) != value for key, value in wanted.items())
            or any(manifest.get(key) != frozen[key] for key in BINDING_KEYS)):
        raise ContractError("requirements differ from frozen acceptance contract; use a fresh attempt for revisions")


def artifact_refs(folder, refs, kind):
    """Hash only explicit existing regular files within this job's evidence/.

    Refuse symlink traversal, hard links, control paths, FIFOs and large files.
    No file contents are returned and no network or capability is used.
    """
    if not isinstance(refs, list) or not 1 <= len(refs) <= 16:
        raise ContractError("review assertions require 1 to 16 evidence references")
    result, used = [], set()
    folder = Path(folder)
    for ref in refs:
        _keys(ref, {"path", "sha256", "kind"}, {"path", "sha256", "kind"}, "evidence reference")
        raw = ref["path"]
        if not isinstance(raw, str) or len(raw) > 1024 or "\0" in raw:
            raise ContractError("invalid evidence reference path")
        path = Path(raw)
        if (path.is_absolute() or len(path.parts) < 2 or path.parts[0] != "evidence"
                or ".." in path.parts or path.as_posix() != raw or raw in used):
            raise ContractError("evidence references must be unique paths under this job's evidence/")
        used.add(raw)
        if ref["kind"] != kind or not isinstance(ref["sha256"], str) or not re.fullmatch(r"[a-f0-9]{64}", ref["sha256"]):
            raise ContractError("evidence reference kind or SHA-256 does not match criterion")
        current = folder
        try:
            for part in path.parts:
                current = current / part
                if current.is_symlink():
                    raise ContractError("evidence reference cannot traverse symlinks")
            before = current.lstat()
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > MAX_ARTIFACT_BYTES:
                raise ContractError("evidence reference must be a regular unlinked file at most 16 MiB")
            descriptor = os.open(current, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            with os.fdopen(descriptor, "rb") as stream:
                if evidence._fingerprint(os.fstat(stream.fileno())) != evidence._fingerprint(before):
                    raise ContractError("evidence reference changed before hashing")
                digest, total = hashlib.sha256(), 0
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    total += len(chunk)
                    if total > MAX_ARTIFACT_BYTES:
                        raise ContractError("evidence reference grew beyond 16 MiB")
                    digest.update(chunk)
            if (evidence._fingerprint(current.lstat()) != evidence._fingerprint(before)
                    or digest.hexdigest() != ref["sha256"]):
                raise ContractError("evidence reference changed")
        except OSError as error:
            raise ContractError("evidence reference unavailable") from error
        result.append(dict(ref))
    return sorted(result, key=lambda row: row["path"])


def outcomes(repo, folder, frozen, snapshot_id, checks):
    """Computed check outcomes and explicit assertions remain different kinds."""
    if frozen is None:
        return []
    latest = {row["requirement_id"]: row for row in checks}
    result = []
    for criterion in frozen["contract"]["criteria"]:
        row = {"criterion_id": criterion["id"], "evidence_type": criterion["evidence_type"],
               "verifier_role": criterion["verifier_role"]}
        if criterion["evidence_type"] == "check":
            checked = latest.get(criterion["check"]["id"])
            if not checked or checked.get("contract_fingerprint") != frozen["contract_fingerprint"]:
                raise ContractError("missing current contract check for criterion: " + criterion["id"])
            # verification._required_passes checks argv, exit, snapshots and logs.
            row.update(result="pass", provenance="computed_check", check_id=checked["check_id"])
        else:
            assertion = evidence.read_json(Path(folder) / "criteria" / (criterion["id"] + ".json"))
            if (not assertion or type(assertion.get("schema_version")) is not int or assertion["schema_version"] != 1
                    or assertion.get("criterion_id") != criterion["id"]
                    or any(assertion.get(key) != frozen[key] for key in BINDING_KEYS)
                    or assertion.get("snapshot_id") != snapshot_id
                    or not isinstance(assertion.get("provenance"), dict)
                    or assertion["provenance"].get("kind") != "parent_assertion"
                    or assertion["provenance"].get("verifier_role") != criterion["verifier_role"]
                    or not isinstance(assertion.get("assertion_id"), str)
                    or not re.fullmatch(r"[a-f0-9]{32}", assertion["assertion_id"])
                    or not isinstance(assertion.get("rationale"), str) or not assertion["rationale"].strip()):
                raise ContractError("missing or stale review assertion for criterion: " + criterion["id"])
            if assertion.get("result") != "pass":
                raise ContractError("review assertion did not pass: " + criterion["id"])
            history = Path(folder) / "criteria" / "history" / (assertion["assertion_id"] + ".json")
            if (history.is_symlink() or history.parent.is_symlink() or history.parent.parent.is_symlink()
                    or evidence.read_json(history) != assertion):
                raise ContractError("review assertion differs from its immutable receipt: " + criterion["id"])
            refs = artifact_refs(folder, assertion.get("evidence_refs"), criterion["artifact_kind"])
            row.update(result="pass", provenance="parent_assertion", assertion_id=assertion["assertion_id"],
                       assertion_fingerprint=fingerprint(assertion), evidence_refs=refs)
        result.append(row)
    return result
