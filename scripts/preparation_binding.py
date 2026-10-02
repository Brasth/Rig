"""Versioned task preparation objects bound to brief, scope, acceptance and source bytes.

A preparation object is inert data, never authority: it grants no ownership,
admission, acceptance, tools or scope. Every consumer recomputes it from the
repository and rejects stale or inconsistent copies with an actionable error.
"""
from __future__ import annotations

import hashlib
import json
import os
import stat

import acceptance_contract as contracts
import change_evidence
import harness
import preparation_handoff as handoff
import preparation_inputs as inputs

SCHEMA_VERSION = 1
KIND = "rig-task-preparation"
MAX_PREPARATION_BYTES = 256 * 1024
MAX_SOURCE_BYTES = 16 * 1024 * 1024
FIELDS = ("schema_version", "kind", "handoff", "brief_sha256", "scope", "acceptance", "sources",
          "readiness", "fingerprint")
REPREPARE = "re-run rig_task_prepare, then re-pick and launch with the new preparation"
SCHEMA = {"type": "object", "description": (
    "Optional preparation object returned by rig_task_prepare. Inert data bound to the exact brief, "
    "writer files, acceptance contract and source bytes; validated before admission. Never authority.")}


class PreparationError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode()


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def draft_gaps(repo, draft):
    """Unresolved draft inputs (the existing `ready` semantics)."""
    gaps = []
    if not harness.project_state(repo)["enabled"]:
        gaps.append("Project must be initialized and enabled before context preview or admission.")
    if not draft["files"]:
        gaps.append("Parent must select concrete writer files.")
    if not draft["checks"] and not draft["manual_criteria"]:
        gaps.append("Parent must supply exact checks or explicit manual acceptance criteria.")
    if draft["recipe"] == "research-implement" and not draft["research_sources"]:
        gaps.append("Parent must supply existing, disjoint research_sources for this recipe.")
    return gaps


def contract_for(repo, draft):
    rows = []
    for check in draft["checks"]:
        rows.append({"id": check["id"], "description": check["description"],
                     "scope": draft["files"], "evidence_type": "check", "verifier_role": "parent",
                     "check": {key: check[key] for key in ("id", "argv", "cwd")}})
    for index, description in enumerate(draft["manual_criteria"], 1):
        identifier = f"manual-{index}"
        while identifier in {item["id"] for item in rows}:
            identifier = "m-" + identifier
        rows.append({"id": identifier, "description": description, "scope": draft["files"],
                     "evidence_type": "review_assertion", "verifier_role": "parent",
                     "artifact_kind": "parent-review"})
    if not rows or not draft["files"]:
        return None
    return contracts.normalize(repo, {"schema_version": 1, "contract_id": "prepared-task",
                                     "revision": 1, "criteria": rows}, draft["files"])


def _digest(root, relative):
    """Hash one literal repository file without following symlinks. None means absent."""
    parts = relative.split("/")
    flags = os.O_RDONLY | os.O_NOFOLLOW
    try:
        directory = os.open(root, flags | os.O_DIRECTORY)
    except OSError:
        raise PreparationError("preparation-invalid", "repository root is unreadable") from None
    try:
        for part in parts[:-1]:
            try:
                child = os.open(part, flags | os.O_DIRECTORY, dir_fd=directory)
            except FileNotFoundError:
                return None
            except OSError:
                raise PreparationError("preparation-invalid", f"source {relative} is unreadable or a symlink") from None
            os.close(directory)
            directory = child
        try:
            leaf = os.open(parts[-1], flags | os.O_NONBLOCK, dir_fd=directory)
        except FileNotFoundError:
            return None
        except OSError:
            raise PreparationError("preparation-invalid", f"source {relative} is unreadable or a symlink") from None
        try:
            before = os.fstat(leaf)
            if not stat.S_ISREG(before.st_mode):
                raise PreparationError("preparation-invalid", f"source {relative} is not a regular file")
            if before.st_size > MAX_SOURCE_BYTES:
                raise PreparationError("preparation-invalid", f"source {relative} exceeds the binding size limit")
            digest, size = hashlib.sha256(), 0
            while chunk := os.read(leaf, 65536):
                size += len(chunk)
                if size > MAX_SOURCE_BYTES:
                    raise PreparationError("preparation-invalid", f"source {relative} exceeds the binding size limit")
                digest.update(chunk)
            after = os.fstat(leaf)
            if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                raise PreparationError("preparation-stale", f"source {relative} changed while hashing; retry")
            return {"bytes": size, "sha256": digest.hexdigest()}
        finally:
            os.close(leaf)
    finally:
        os.close(directory)


def _sources(root, draft):
    rows = []
    for path in draft["files"]:
        found = _digest(root, path)
        rows.append({"path": path, "role": "writer", **({"state": "present", **found} if found else {"state": "absent"})})
    for path in handoff.evidence_paths(draft):
        found = _digest(root, path)
        if found is None:
            raise PreparationError("preparation-invalid", f"evidence or reading path {path} does not exist")
        rows.append({"path": path, "role": "evidence", "state": "present", **found})
    return sorted(rows, key=lambda row: (row["path"], row["role"]))


def _assemble(root, draft):
    unresolved = draft_gaps(root, draft)
    contract = contract_for(root, draft)
    brief = handoff.render(draft)
    sources = _sources(root, draft)
    existing = {row["path"] for row in sources if row["role"] == "writer" and row["state"] == "present"}
    execution_ready, gaps = handoff.readiness(draft, ready=not unresolved, existing=existing)
    body = {
        "schema_version": SCHEMA_VERSION, "kind": KIND, "handoff": draft,
        "brief_sha256": _sha(brief.encode("utf-8")),
        "scope": {"files": list(draft["files"]), "access": "write"},
        "acceptance": {"contract_fingerprint": contracts.fingerprint(contract) if contract else None},
        "sources": sources,
        "readiness": {"ready": not unresolved, "execution_ready": execution_ready,
                      "gaps": sorted({row["code"] for row in gaps})},
    }
    body["fingerprint"] = _sha(_canonical(body))
    return body, {"brief": brief, "contract": contract, "unresolved": unresolved, "gaps": gaps}


def build(repo, draft):
    """Return (preparation, details) for a normalized draft. Read-only."""
    import context_packages

    if len(json.dumps(draft, allow_nan=False).encode()) > context_packages.MAX_SPEC_BYTES:
        # Launch re-normalizes the stored handoff; refuse instead of truncating.
        raise PreparationError("preparation-invalid", "normalized task preparation exceeds 32 KiB; reduce the inputs")
    return _assemble(change_evidence.repository(repo), draft)


def summary(preparation):
    """Bounded routing evidence: status, level, hashes and reason codes only."""
    readiness = preparation["readiness"]
    return {"status": "valid", "version": SCHEMA_VERSION, "fingerprint": preparation["fingerprint"],
            "ready": readiness["ready"], "execution_ready": readiness["execution_ready"],
            "remaining_work": preparation["handoff"].get("remaining_work", ""),
            "gap_codes": list(readiness["gaps"])}


def _structure(value):
    if not isinstance(value, dict) or set(value) != set(FIELDS):
        raise PreparationError("preparation-invalid", "preparation must be the unmodified object from rig_task_prepare; " + REPREPARE)
    try:
        size = len(_canonical(value))
    except (TypeError, ValueError, RecursionError):
        raise PreparationError("preparation-invalid", "preparation must be bounded JSON data") from None
    if size > MAX_PREPARATION_BYTES:
        raise PreparationError("preparation-invalid", "preparation exceeds its byte limit")
    if type(value["schema_version"]) is not int or value["schema_version"] != SCHEMA_VERSION or value["kind"] != KIND:
        raise PreparationError("preparation-invalid", "unsupported preparation version; " + REPREPARE)
    if not isinstance(value["handoff"], dict):
        raise PreparationError("preparation-invalid", "preparation handoff must be an object")


def inspect(repo, value, *, fresh=True):
    """Recompute the object from current repository bytes. Returns (draft, summary).

    fresh=False checks only internal consistency (fingerprint, normalized handoff, brief,
    scope and acceptance) for stored workflow specs; launches always use fresh=True.
    """
    root = change_evidence.repository(repo)
    _structure(value)
    body = {key: item for key, item in value.items() if key != "fingerprint"}
    if value["fingerprint"] != _sha(_canonical(body)):
        raise PreparationError("preparation-tampered", "preparation fingerprint mismatch; " + REPREPARE)
    try:
        draft = inputs.normalize(root, value["handoff"])
    except ValueError as error:
        raise PreparationError("preparation-invalid", f"preparation handoff is invalid ({error}); " + REPREPARE) from None
    if _canonical(draft) != _canonical(value["handoff"]):
        raise PreparationError("preparation-tampered", "preparation handoff is not normalized; " + REPREPARE)
    if not fresh:
        contract = contract_for(root, draft)
        expected = {"brief_sha256": _sha(handoff.render(draft).encode("utf-8")),
                    "scope": {"files": list(draft["files"]), "access": "write"},
                    "acceptance": {"contract_fingerprint": contracts.fingerprint(contract) if contract else None}}
        if any(value[key] != item for key, item in expected.items()):
            raise PreparationError("preparation-tampered", "preparation brief, scope or acceptance is inconsistent; " + REPREPARE)
        if not isinstance(value["readiness"], dict) or set(value["readiness"]) != {"ready", "execution_ready", "gaps"}:
            raise PreparationError("preparation-invalid", "preparation readiness is malformed; " + REPREPARE)
        return draft, summary(value)
    current, _details = _assemble(root, draft)
    if current["sources"] != value["sources"]:
        recorded = {(row.get("path"), row.get("role")): row for row in value["sources"] if isinstance(row, dict)}
        changed = sorted({row["path"] for row in current["sources"]
                          if recorded.get((row["path"], row["role"])) != row})
        raise PreparationError("preparation-stale", "preparation is stale: source changed (" + ", ".join(changed[:8])
                               + ("..." if len(changed) > 8 else "") + "); " + REPREPARE)
    if current != value:
        raise PreparationError("preparation-tampered", "preparation does not match its recomputed brief, scope, acceptance or readiness; " + REPREPARE)
    return draft, summary(current)


def validate_launch(repo, value, *, brief, files, acceptance_contract, access):
    """Bind the raw (pre-suffix) brief, writer scope and actual acceptance contract. Read-only."""
    root = change_evidence.repository(repo)
    draft, result = inspect(root, value)
    if not result["ready"]:
        raise PreparationError("preparation-not-ready", "preparation is not ready; resolve unresolved items and " + REPREPARE)
    if str(access or "write") != "write":
        raise PreparationError("preparation-scope-mismatch", "preparation binds a writer scope; launch with access=write")
    if not isinstance(brief, str) or brief != handoff.render(draft):
        raise PreparationError("preparation-brief-mismatch",
                               "brief differs from the prepared brief; pass the exact prepared brief "
                               "(Rig appends context, continuation and workflow data itself) or " + REPREPARE)
    try:
        declared = inputs.paths(root, list(files or []), "files")
    except ValueError as error:
        raise PreparationError("preparation-scope-mismatch", f"launch file scope is invalid ({error})") from None
    if sorted(declared) != sorted(draft["files"]):
        raise PreparationError("preparation-scope-mismatch", "launch files differ from the prepared writer scope; " + REPREPARE)
    if acceptance_contract in (None, ""):
        raise PreparationError("preparation-acceptance-mismatch",
                               "prepared acceptance contract is required at launch; pass the returned acceptance_contract")
    try:
        actual = contracts.normalize(root, acceptance_contract, draft["files"])
    except ValueError as error:
        raise PreparationError("preparation-acceptance-mismatch", f"acceptance contract is invalid ({error})") from None
    if contracts.fingerprint(actual) != value["acceptance"]["contract_fingerprint"]:
        raise PreparationError("preparation-acceptance-mismatch",
                               "acceptance contract differs from the prepared checks/manual criteria; " + REPREPARE)
    return result


def recheck(repo, value):
    """Cheap freshness recheck at the ownership boundary (under the admission lock)."""
    _draft, result = inspect(repo, value)
    return result
