#!/usr/bin/env python3
"""Private, parent-assembled UI evidence. Never capture, act, or accept work.

Receipts are local caller-provided records, not authenticated backend proof.
Privacy review and visual observations are parent assertions, not certification.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
import zlib
from pathlib import Path

import admission
import change_evidence as evidence
import verification

VERSION = "rig.ui-pack.v1"
MAX_STEPS = 32
MAX_IMAGE_BYTES = 4 * 1024 * 1024
MAX_TOTAL_BYTES = 16 * 1024 * 1024
MAX_MANIFEST_BYTES = 128 * 1024
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,100}\Z")
_HASH = re.compile(r"[a-f0-9]{64}\Z")
_SECRET = re.compile(r"(?i)\b(?:password|passwd|authorization|api[ _-]?key|access[ _-]?token|secret|bearer|cookie|otp|ssn|cvv)\b")
_CREDENTIAL = re.compile(r"(?:\b(?:sk-|ghp_|github_pat_|xox[baprs]-)[A-Za-z0-9_-]{12,}|\bAKIA[A-Z0-9]{16}\b|-----BEGIN [A-Z ]*PRIVATE KEY-----)")
_QUERY = re.compile(r"(?i)https?://[^\s]*[?#]")


class UIEvidenceError(ValueError):
    pass


def _object(value, name):
    if not isinstance(value, dict):
        raise UIEvidenceError(f"{name} must be an object")
    return value


def _keys(value, allowed, name):
    _object(value, name)
    if set(value) - set(allowed):
        raise UIEvidenceError(f"{name} contains unsupported fields")


def _identifier(value, name):
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise UIEvidenceError(f"{name} must be a bounded simple identifier")
    return _text(value, name, 101)


def _hash(value, name):
    if not isinstance(value, str) or not _HASH.fullmatch(value):
        raise UIEvidenceError(f"{name} must be a SHA-256 digest")
    return value


def _text(value, name, limit=1000):
    if not isinstance(value, str) or len(value) > limit or any(ord(c) < 32 and c != "\n" for c in value):
        raise UIEvidenceError(f"{name} must be bounded plain text")
    if _SECRET.search(value) or _CREDENTIAL.search(value) or _QUERY.search(value):
        raise UIEvidenceError(f"{name} contains potentially sensitive metadata; redact before recording")
    return value


def _json_bytes(value):
    try:
        data = (json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":"), allow_nan=False) + "\n").encode()
    except (TypeError, ValueError, RecursionError) as error:
        raise UIEvidenceError("pack must contain finite JSON data") from error
    if len(data) > MAX_MANIFEST_BYTES:
        raise UIEvidenceError("pack metadata exceeds the byte limit")
    return data


def _digest(data):
    return hashlib.sha256(data).hexdigest()


def _parts(relative):
    if not isinstance(relative, str) or len(relative) > 1024 or "\\" in relative or "\0" in relative:
        raise UIEvidenceError("evidence path must be a bounded repository-relative path")
    parts = relative.split("/")
    if any(part in {"", ".", ".."} for part in parts) or Path(relative).is_absolute():
        raise UIEvidenceError("evidence path must be a normalized relative path")
    return parts


def _safe_path(root, relative):
    parts = _parts(relative)
    current = Path(root)
    for part in parts:
        current = current / part
        try:
            info = current.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode):
            raise UIEvidenceError("symlink evidence paths are not supported")
    return current


def _read(root, relative, limit, *, missing=False):
    """Open every path component without following symlinks; bound actual reads."""
    parts = _parts(relative)
    directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = child
        handle = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        try:
            info = os.fstat(handle)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise UIEvidenceError("evidence must be a regular, non-hardlinked file")
            if info.st_size > limit:
                raise UIEvidenceError("evidence exceeds the byte limit")
            with os.fdopen(handle, "rb", closefd=False) as stream:
                data = stream.read(limit + 1)
            if len(data) > limit:
                raise UIEvidenceError("evidence exceeds the byte limit")
            return data
        finally:
            os.close(handle)
    except FileNotFoundError:
        if missing:
            return None
        raise UIEvidenceError("required evidence file is missing") from None
    except OSError as error:
        raise UIEvidenceError("evidence path is unreadable or unsafe") from error
    finally:
        os.close(directory)


def _source(root, folder, spec, limit, *, missing=False):
    _object(spec, "source")
    path = spec.get("path")
    parts = _parts(path)
    allowed = [".rig/cu-evidence/", ".rig/bsk-evidence/", folder.relative_to(root).as_posix() + "/evidence/inputs/"]
    if not any(path.startswith(prefix) for prefix in allowed):
        raise UIEvidenceError("source must be in a capture evidence root or this job's evidence/inputs")
    expected = _hash(spec.get("sha256"), "source hash")
    data = _read(root, "/".join(parts), limit, missing=missing)
    if data is not None and _digest(data) != expected:
        raise UIEvidenceError("source content does not match its declared hash")
    return data


def _receipt(root, folder, step):
    if ("receipt" in step) == ("receipt_file" in step):
        raise UIEvidenceError("each step needs exactly one receipt or receipt_file")
    if "receipt_file" in step:
        source = step["receipt_file"]
        _keys(source, {"path", "sha256"}, "receipt_file")
        data = _source(root, folder, source, MAX_MANIFEST_BYTES)
        try:
            raw = json.loads(data)
        except (ValueError, UnicodeError) as error:
            raise UIEvidenceError("recorded receipt must be JSON") from error
        provenance = {"type": "recorded_receipt_file", "source_sha256": _digest(data), "backend_authenticated": False}
    else:
        raw = step["receipt"]
        _json_bytes(raw)
        provenance = {"type": "supplied_unverified", "backend_authenticated": False}
    raw = _object(raw, "receipt")
    raw = raw.get("receipt", raw)
    raw = _object(raw, "receipt")
    version = raw.get("version")
    if not isinstance(version, str) or version not in {"rig.cu.v1", "rig.bsk.v1"}:
        raise UIEvidenceError("receipt must be an existing rig.cu.v1 or rig.bsk.v1 record")
    snapshot = _object(raw.get("snapshot"), "receipt snapshot")
    image = _object(raw.get("image", {}), "receipt image")
    image_hash = image.get("sha256") or ""
    if image_hash:
        _hash(image_hash, "receipt image hash")
    projection = {
        "version": version,
        "operation": _text(_identifier(raw.get("operation"), "receipt operation"), "receipt operation"),
        "status": _text(_identifier(raw.get("status"), "receipt status"), "receipt status"),
        "effect": _text(_identifier(raw.get("effect"), "receipt effect"), "receipt effect"),
        "observation_snapshot_id": _text(snapshot.get("id", ""), "observation snapshot", 200),
        "freshness": _text(snapshot.get("freshness", "unknown"), "observation freshness", 40),
        "image_sha256": image_hash,
    }
    return {"provenance": provenance, "record": projection}, image_hash


def _viewport(raw):
    if raw is None:
        return {"width": None, "height": None, "coordinate_space": "unknown"}
    _keys(raw, {"width", "height", "coordinate_space"}, "viewport")
    result = {}
    for key in ("width", "height"):
        value = raw.get(key)
        if value is not None and (type(value) is not int or not 1 <= value <= 100000):
            raise UIEvidenceError("viewport dimensions must be positive bounded integers or null")
        result[key] = value
    result["coordinate_space"] = _text(raw.get("coordinate_space", "unknown"), "coordinate space", 80)
    return result


def _png_dimensions(data):
    if data[:8] != PNG_MAGIC:
        raise UIEvidenceError("image must be a PNG")
    offset, dimensions, has_data = 8, None, False
    while offset + 12 <= len(data):
        size = int.from_bytes(data[offset:offset + 4], "big")
        kind = data[offset + 4:offset + 8]
        end = offset + size + 12
        if end > len(data):
            break
        chunk = data[offset + 8:offset + 8 + size]
        checksum = int.from_bytes(data[end - 4:end], "big")
        if zlib.crc32(kind + chunk) & 0xffffffff != checksum:
            raise UIEvidenceError("PNG chunk checksum is invalid")
        if dimensions is None:
            if kind != b"IHDR" or size != 13:
                raise UIEvidenceError("PNG must begin with an image header")
            dimensions = (int.from_bytes(chunk[:4], "big"), int.from_bytes(chunk[4:8], "big"))
            if any(not 1 <= value <= 100000 for value in dimensions):
                raise UIEvidenceError("PNG dimensions are invalid")
        elif kind == b"IHDR":
            raise UIEvidenceError("PNG has duplicate image headers")
        if kind in {b"tEXt", b"zTXt", b"iTXt", b"eXIf"}:
            raise UIEvidenceError("PNG contains private metadata; provide a reviewed metadata-free copy")
        has_data = has_data or kind == b"IDAT"
        if kind == b"IEND":
            if size or end != len(data) or not has_data:
                raise UIEvidenceError("PNG image structure is invalid")
            return dimensions
        offset = end
    raise UIEvidenceError("PNG image is incomplete")


def _binding(root, folder, pack):
    import acceptance_contract
    meta = evidence.read_json(folder / "meta.json")
    if not meta:
        raise UIEvidenceError("job metadata is missing")
    artifact = acceptance_contract.load(root, folder, meta=meta)
    if not artifact:
        raise UIEvidenceError("UI packs require a frozen acceptance contract")
    if pack.get("contract_fingerprint") != artifact["contract_fingerprint"]:
        raise UIEvidenceError("UI pack contract does not match this attempt")
    criteria = pack.get("criterion_ids")
    if (not isinstance(criteria, list) or not criteria or len(criteria) > 32
            or any(not isinstance(item, str) for item in criteria) or len(set(criteria)) != len(criteria)):
        raise UIEvidenceError("criterion_ids must contain unique bounded criterion IDs")
    eligible = {row["id"] for row in artifact["contract"]["criteria"]
                if row.get("evidence_type") == "review_assertion" and row.get("artifact_kind") == "ui-pack"}
    if any(_identifier(value, "criterion ID") not in eligible for value in criteria):
        raise UIEvidenceError("UI pack must reference ui-pack review assertion criteria")
    current = evidence.snapshot(root, meta.get("files", []))["snapshot_id"]
    if pack.get("content_snapshot_id") != current:
        raise UIEvidenceError("UI pack content snapshot is not current")
    identity = {key: _text(artifact[key], "attempt identity", 200)
                for key in ("job_id", "reservation_id", "attempt_id", "contract_fingerprint")}
    return identity | {
        "content_snapshot_id": current, "criterion_ids": sorted(criteria),
    }


def _prepare(root, folder, pack):
    _keys(pack, {"privacy_reviewed", "contract_fingerprint", "content_snapshot_id", "criterion_ids", "steps"}, "pack")
    _json_bytes(pack)
    if pack.get("privacy_reviewed") is not True:
        raise UIEvidenceError("parent privacy review is required before recording")
    binding = _binding(root, folder, pack)
    steps = pack.get("steps")
    if not isinstance(steps, list) or not 1 <= len(steps) <= MAX_STEPS:
        raise UIEvidenceError("pack needs between 1 and 32 steps")
    rows, blobs, used, total, unavailable = [], {}, set(), 0, 0
    for step in steps:
        _keys(step, {"id", "receipt", "receipt_file", "image", "viewport", "parent_observation"}, "step")
        sid = _identifier(step.get("id"), "step ID")
        if sid in used:
            raise UIEvidenceError("step IDs must be unique")
        used.add(sid)
        receipt, original_hash = _receipt(root, folder, step)
        observation = step.get("parent_observation", {})
        _keys(observation, {"expected", "observed"}, "parent observation")
        row = {"id": sid, "receipt": receipt, "viewport": _viewport(step.get("viewport")),
               "parent_observation": {"provenance": "parent_assertion",
                   "expected": _text(observation.get("expected", ""), "expected observation"),
                   "observed": _text(observation.get("observed", ""), "observed observation")}}
        image = step.get("image")
        if image is None:
            row["image"] = {"available": False, "reason": "not_supplied"}
            unavailable += 1
        else:
            _keys(image, {"path", "sha256", "privacy", "original_sha256"}, "image")
            privacy = image.get("privacy")
            if not isinstance(privacy, str) or privacy not in {"reviewed", "redacted"}:
                raise UIEvidenceError("image requires explicit reviewed or redacted privacy status")
            expected = _hash(image.get("sha256"), "image hash")
            declared_original = image.get("original_sha256")
            if privacy == "redacted":
                _hash(declared_original, "original image hash")
                if expected == declared_original or (original_hash and original_hash != declared_original):
                    raise UIEvidenceError("redaction must retain a distinct, matching original image hash")
            elif declared_original is not None or (original_hash and original_hash != expected):
                raise UIEvidenceError("image does not match the receipt's selected image")
            data = _source(root, folder, image, MAX_IMAGE_BYTES, missing=True)
            info = {"privacy": privacy, "privacy_provenance": "parent_assertion", "sha256": expected}
            if privacy == "redacted":
                info["original_sha256"] = declared_original
            if data is None:
                info.update(available=False, reason="missing_at_recording")
                unavailable += 1
            else:
                width, height = _png_dimensions(data)
                total += len(data)
                if total > MAX_TOTAL_BYTES:
                    raise UIEvidenceError("pack images exceed the total byte limit")
                path = f"images/{sid}.png"
                blobs[path] = data
                info.update(available=True, path=path, bytes=len(data), pixel_width=width, pixel_height=height)
            row["image"] = info
        rows.append(row)
    manifest = {"version": VERSION, **binding,
                "privacy_review": {"reviewed": True, "provenance": "parent_assertion", "automatic_certification": False},
                "coverage": {"steps": len(rows), "available_images": len(blobs), "unavailable_images": unavailable},
                "steps": rows}
    return manifest, blobs


def _private_write(path, data):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def record_pack(repo, job_dir, pack, *, reservation_id="", attempt_id="", owner_token="", owner_session=""):
    verification._parent_only()
    root = evidence.repository(repo)
    folder = evidence.job_directory(root, job_dir)
    _safe_path(root, folder.relative_to(root).as_posix())
    with admission.mutation_guard(root, folder, "evidence", reservation_id=reservation_id,
                                  attempt_id=attempt_id, owner_token=owner_token, owner_session=owner_session), verification._lock(folder):
        verification._idle(folder)
        manifest, blobs = _prepare(root, folder, pack)
        data = _json_bytes(manifest)
        digest = _digest(data)
        relative = f"evidence/ui-packs/{digest}/manifest.json"
        destination = _safe_path(folder, relative).parent
        reference = {"path": relative, "sha256": digest, "kind": "ui-pack"}
        if destination.exists():
            validate_pack_reference(folder, reference)
            return {"evidence_ref": reference, "coverage": manifest["coverage"], "acceptance": "unchanged"}
        base = _safe_path(folder, "evidence/ui-packs")
        for directory in (folder / "evidence", base):
            directory.mkdir(mode=0o700, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=".pack-", dir=base))
        try:
            (staging / "images").mkdir(mode=0o700)
            for name, value in blobs.items():
                _private_write(staging / name, value)
            _private_write(staging / "manifest.json", data)
            # Recheck content before publishing after reading/copying all inputs.
            _binding(root, folder, pack)
            staging.rename(destination)
        finally:
            if staging.exists():
                shutil.rmtree(staging)
        return {"evidence_ref": reference, "coverage": manifest["coverage"], "acceptance": "unchanged"}


def validate_pack_reference(job_dir, reference, *, criterion_id=""):
    """Revalidate transitive artifact bytes and current contract/content binding.

    This does not certify backend provenance, privacy, or visual correctness.
    Explicitly unavailable images stay unavailable; formerly available images
    that are deleted or changed fail validation.
    """
    folder = Path(job_dir)
    _keys(reference, {"path", "sha256", "kind"}, "UI pack reference")
    digest = _hash(reference.get("sha256"), "UI pack hash")
    if reference.get("kind") != "ui-pack" or reference.get("path") != f"evidence/ui-packs/{digest}/manifest.json":
        raise UIEvidenceError("invalid UI pack reference")
    data = _read(folder, reference["path"], MAX_MANIFEST_BYTES)
    if _digest(data) != digest:
        raise UIEvidenceError("UI pack manifest has changed")
    try:
        manifest = json.loads(data)
    except (ValueError, UnicodeError) as error:
        raise UIEvidenceError("UI pack manifest is malformed") from error
    if not isinstance(manifest, dict) or manifest.get("version") != VERSION:
        raise UIEvidenceError("UI pack manifest version is unsupported")
    if len(folder.parents) < 3 or folder.parent.name != "jobs" or folder.parent.parent.name != ".rig":
        raise UIEvidenceError("UI pack requires a canonical job directory")
    root = folder.parents[2]
    _safe_path(root, folder.relative_to(root).as_posix())
    expected = _binding(root, folder, manifest)
    if criterion_id and criterion_id not in expected["criterion_ids"]:
        raise UIEvidenceError("UI pack does not name the asserted criterion")
    if any(manifest.get(key) != value for key, value in expected.items()):
        raise UIEvidenceError("UI pack is bound to a different attempt")
    steps = manifest.get("steps")
    if not isinstance(steps, list) or not 1 <= len(steps) <= MAX_STEPS:
        raise UIEvidenceError("UI pack step count is invalid")
    total, seen, available = 0, set(), 0
    for row in steps:
        _object(row, "step")
        sid = _identifier(row.get("id"), "step ID")
        if sid in seen:
            raise UIEvidenceError("UI pack step IDs are duplicated")
        seen.add(sid)
        image = _object(row.get("image"), "image")
        if image.get("available") is not True:
            continue
        path = f"images/{sid}.png"
        if image.get("path") != path:
            raise UIEvidenceError("UI pack image path is invalid")
        image_data = _read(folder, str(Path(reference["path"]).parent / path), MAX_IMAGE_BYTES)
        total += len(image_data)
        if total > MAX_TOTAL_BYTES or len(image_data) != image.get("bytes") or _digest(image_data) != image.get("sha256"):
            raise UIEvidenceError("UI pack image bytes have changed")
        available += 1
    if manifest.get("coverage") != {"steps": len(steps), "available_images": available, "unavailable_images": len(steps) - available}:
        raise UIEvidenceError("UI pack coverage is inconsistent")
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("id")
    parser.add_argument("--repo", required=True)
    parser.add_argument("--file", required=True)
    parser.add_argument("--reservation-id", default="")
    parser.add_argument("--attempt-id", default="")
    parser.add_argument("--owner-session", default="")
    parser.add_argument("--credentials-path", default="")
    args = parser.parse_args(argv)
    try:
        verification._parent_only()
        import jobs
        folder = Path(jobs.resolve_job(Path(args.repo), args.id)["dir"])
        with Path(args.file).open("rb") as stream:
            raw = stream.read(MAX_MANIFEST_BYTES + 1)
        if len(raw) > MAX_MANIFEST_BYTES:
            raise UIEvidenceError("pack metadata exceeds the byte limit")
        pack = json.loads(raw)
        ownership = admission.resolve_ownership(args.repo, job_id=folder.name,
            reservation_id=args.reservation_id, attempt_id=args.attempt_id,
            owner_token=os.environ.get("RIG_OWNER_TOKEN", ""), owner_session=args.owner_session,
            credentials_path=args.credentials_path)
        print(json.dumps(record_pack(args.repo, folder, pack, **ownership)))
    except (OSError, ValueError) as error:
        parser.exit(2, f"rig UI evidence: {error}\n")


if __name__ == "__main__":
    main()
