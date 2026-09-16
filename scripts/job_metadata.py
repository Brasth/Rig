#!/usr/bin/env python3
"""Locked job metadata reads and unique-tmp writes using admission.transaction.

Standard job dirs are repo/.rig/jobs/<id>. Legacy or test dirs never take the
repo flock, so a nonstandard path cannot create a lock at the filesystem root.
"""
from __future__ import annotations

import json
import os
import uuid
from contextlib import contextmanager
from pathlib import Path

HANDSHAKE_KEYS = ("child_mcp_status", "child_mcp_connected_at", "child_mcp_protocol")
DOING_KEYS = ("doing", "doing_updated_at")
WORKFLOW_KEYS = ("workflow_id", "workflow_node_id", "workflow_spec_hash", "workflow_attempt")


class MetadataError(ValueError):
    """Existing metadata is unreadable; writers must fail closed."""


def job_repo(job_dir) -> Path | None:
    """Repo root for standard repo/.rig/jobs/<id>. None for legacy/test dirs."""
    try:
        path = Path(job_dir).expanduser().resolve()
    except OSError:
        return None
    if path.name in {"", ".", ".."}:
        return None
    jobs_root, rig_root, repo = path.parent, path.parent.parent, path.parent.parent.parent
    if jobs_root.name != "jobs" or rig_root.name != ".rig":
        return None
    if repo == path.anchor or str(repo) == os.sep:
        return None
    if not (repo / ".rig" / "harness.toml").is_file():
        return None
    return repo


@contextmanager
def metadata_lock(job_dir):
    """Same admission.transaction as the rest of admission; reentrant, no extra lock."""
    repo = job_repo(job_dir)
    if repo is None:
        yield None
        return
    import admission

    with admission.transaction(repo) as root:
        yield root


def read_json_object(path: Path) -> dict:
    path = Path(path)
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise
    except OSError as error:
        raise MetadataError(f"unreadable job metadata: {path.name}") from error
    try:
        value = json.loads(raw)
    except (OSError, UnicodeError, ValueError) as error:
        raise MetadataError(f"malformed job metadata: {path.name}") from error
    if not isinstance(value, dict):
        raise MetadataError(f"malformed job metadata: {path.name}")
    return value


def read_meta(job_dir, *, missing_ok: bool = True) -> dict:
    path = Path(job_dir) / "meta.json"
    try:
        return read_json_object(path)
    except FileNotFoundError:
        if missing_ok:
            return {}
        raise MetadataError("job metadata is missing") from None


def write_json_atomic(path: Path, value) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    legacy_tmp = path.with_name(path.name + ".tmp")
    if legacy_tmp.is_dir():
        raise OSError(f"atomic write blocked: {legacy_tmp.name} is a directory")
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def merge_record(old, overlay) -> dict:
    merged = dict(old or {})
    for key, value in (overlay or {}).items():
        if value is None:
            continue
        merged[key] = value
    return merged


def preserve_live_fields(latest: dict, merged: dict, *, restore_doing: bool = True,
                         restore_workflow: bool = False, restore_resources: bool = False) -> dict:
    for key in HANDSHAKE_KEYS:
        if key in latest:
            merged[key] = latest[key]
    if restore_doing:
        for key in DOING_KEYS:
            if key in latest:
                merged[key] = latest[key]
    if restore_workflow:
        for key in WORKFLOW_KEYS:
            if latest.get(key) not in (None, ""):
                merged[key] = latest[key]
    if restore_resources and latest.get("resources") not in (None, ""):
        merged["resources"] = latest["resources"]
    return merged


def update_meta(job_dir, mutator, *, create: bool = True) -> dict:
    """Read/modify/write meta.json inside one transaction. missing may create."""
    job_dir = Path(job_dir)

    def mutate(latest):
        return mutator(dict(latest))

    return update_records(job_dir, mutate, names=("meta.json",), create=create)


def update_records(job_dir, mutator, *, names=("meta.json",), create: bool = True) -> dict:
    job_dir = Path(job_dir)
    with metadata_lock(job_dir):
        path = job_dir / "meta.json"
        if not path.is_file():
            if not create:
                return {}
            latest = {}
        else:
            latest = read_json_object(path)
        updated = mutator(dict(latest))
        if not isinstance(updated, dict):
            raise MetadataError("job metadata mutator must return an object")
        job_dir.mkdir(parents=True, exist_ok=True)
        for name in names:
            write_json_atomic(job_dir / name, updated)
        return updated
