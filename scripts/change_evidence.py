#!/usr/bin/env python3
"""Observe scoped local content and Git changes without changing user files."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import subprocess
from datetime import datetime, timezone
from pathlib import Path


class EvidenceError(ValueError):
    pass


def now():
    return datetime.now(timezone.utc).isoformat()


def read_json(path):
    try:
        value = Path(path).read_text(encoding="utf-8")
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else None
    except (OSError, ValueError, UnicodeError):
        return None


def write_json(path, value):
    """Atomic metadata replacement; user source files are never written here."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
    try:
        with temporary.open("w", encoding="utf-8") as output:
            json.dump(value, output, indent=2, ensure_ascii=True)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def repository(repo):
    try:
        root = Path(repo).resolve(strict=True)
    except OSError as error:
        raise EvidenceError(f"repository is unavailable: {error}") from error
    if not root.is_dir():
        raise EvidenceError("repository must be a directory")
    return root


def normalize_files(repo, files, *, allow_empty=False):
    root = repository(repo)
    if not isinstance(files, (list, tuple)) or any(not isinstance(value, str) for value in files):
        raise EvidenceError("files must be an array of concrete local paths")
    normalized = set()
    for value in files:
        if not value or "\0" in value or "://" in value:
            raise EvidenceError("file scope must contain concrete local paths, without glob patterns")
        path = Path(value)
        path = path if path.is_absolute() else root / path
        # Resolve ancestors, but preserve leaf symlinks as subjects themselves.
        try:
            path = path.parent.resolve() / path.name
        except (OSError, RuntimeError) as error:
            raise EvidenceError("file scope contains an invalid parent path") from error
        try:
            relative = path.relative_to(root)
        except ValueError as error:
            raise EvidenceError("file scope must remain inside the repository") from error
        if not relative.parts or relative.parts[0] == ".git":
            raise EvidenceError("Git internals are not a file scope")
        # JSON scopes are literal paths: brackets commonly name application
        # routes. Unresolved '*'/'?' patterns are refused, without expanding
        # them; an existing literal file bearing those characters is allowed.
        if any(char in value for char in "*?") and not (path.is_file() or path.is_symlink()):
            raise EvidenceError("unresolved '*' or '?' wildcard scope is not a concrete file")
        if path.is_dir() and not path.is_symlink():
            raise EvidenceError("file scope must name files, not directories")
        normalized.add(relative.as_posix())
    if not normalized and not allow_empty:
        raise EvidenceError("a nonempty concrete local file scope is required")
    return sorted(normalized)


def normalize_cwd(repo, cwd=None):
    root = repository(repo)
    if cwd is not None and not isinstance(cwd, (str, Path)):
        raise EvidenceError("check cwd must be a local path")
    value = Path(cwd) if cwd else root
    value = value if value.is_absolute() else root / value
    try:
        value = value.resolve(strict=True)
        relative = value.relative_to(root)
    except (OSError, ValueError, RuntimeError) as error:
        raise EvidenceError("check cwd must be a directory inside the repository") from error
    if not value.is_dir() or (relative.parts and relative.parts[0] == ".git"):
        raise EvidenceError("check cwd must be a directory inside the repository")
    return relative.as_posix()


def job_directory(repo, job_dir):
    root = repository(repo)
    folder = Path(job_dir).resolve()
    try:
        relative = folder.relative_to(root / ".rig" / "jobs")
    except ValueError as error:
        raise EvidenceError("job directory must be inside this repository's .rig/jobs") from error
    if len(relative.parts) != 1:
        raise EvidenceError("job directory must name one job")
    return folder


def _fingerprint(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _entry(path, cache=None, repo=None):
    try:
        before = path.lstat()
    except FileNotFoundError:
        return {"kind": "absent"}
    except OSError as error:
        raise EvidenceError(f"cannot inspect subject {path.name}: {error}") from error
    key = (str(path), _fingerprint(before))
    if not stat.S_ISLNK(before.st_mode) and cache is not None and key in cache:
        return dict(cache[key])
    value = {"mode": stat.S_IMODE(before.st_mode)}
    try:
        if stat.S_ISLNK(before.st_mode):
            target = os.readlink(path)
            value.update(kind="symlink", target=target, sha256=hashlib.sha256(os.fsencode(target)).hexdigest())
            if repo is not None:
                try:
                    resolved = path.resolve()
                    relative_target = resolved.relative_to(repo)
                except (OSError, ValueError, RuntimeError) as error:
                    raise EvidenceError("symlink subject target must stay inside the repository") from error
                if resolved.is_dir():
                    raise EvidenceError("symlink subject target must be a file")
                if relative_target.parts and relative_target.parts[0] == ".git":
                    raise EvidenceError("Git internals are not a symlink subject")
                value["resolved_target"] = relative_target.as_posix()
                value["target_entry"] = _entry(resolved, cache, repo)
        elif stat.S_ISREG(before.st_mode):
            digest = hashlib.sha256()
            descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            with os.fdopen(descriptor, "rb") as stream:
                if _fingerprint(os.fstat(stream.fileno())) != _fingerprint(before):
                    raise EvidenceError(f"subject changed before hashing: {path.name}")
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
            value.update(kind="file", sha256=digest.hexdigest())
        else:
            raise EvidenceError(f"unsupported subject file type: {path.name}")
        if _fingerprint(path.lstat()) != _fingerprint(before):
            raise EvidenceError(f"subject changed while hashing: {path.name}")
    except OSError as error:
        raise EvidenceError(f"cannot read subject {path.name}: {error}") from error
    if cache is not None and not stat.S_ISLNK(before.st_mode):
        cache[key] = dict(value)
    return value


def snapshot(repo, files, cache=None):
    root = repository(repo)
    names = normalize_files(root, files)
    entries = {name: _entry(root / name, cache, root) for name in names}
    canonical = json.dumps(entries, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    return {"version": 1, "snapshot_id": hashlib.sha256(canonical).hexdigest(),
            "files": names, "entries": entries, "created_at": now()}


def _git(repo, *arguments):
    result = subprocess.run(["git", "-c", "core.fsmonitor=false", *arguments], cwd=repo,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
                            env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"})
    return result.stdout if result.returncode == 0 else None


def parse_status(payload):
    """Porcelain -z emits destination first, then source for rename/copy pairs."""
    rows, fields, index = [], payload.split(b"\0"), 0
    while index < len(fields):
        field = fields[index]
        index += 1
        if not field:
            continue
        if len(field) < 4 or field[2:3] != b" ":
            raise EvidenceError("malformed NUL-delimited Git status")
        code = field[:2].decode("ascii")
        row = {"status": code, "path": os.fsdecode(field[3:])}
        if "R" in code or "C" in code:
            if index >= len(fields) or not fields[index]:
                raise EvidenceError("missing Git rename source")
            row["source"] = os.fsdecode(fields[index])
            index += 1
        rows.append(row)
    return rows


def _inventory(repo, declared):
    tracked_raw = _git(repo, "ls-files", "--cached", "-z")
    if tracked_raw is None:
        return {"git_available": False, "head": "", "index_id": "", "index_entries": {},
                "inventory": [], "dirty_paths": [], "status": []}
    tracked = {os.fsdecode(value) for value in tracked_raw.split(b"\0") if value}
    def visible(name):
        return not name.startswith(".rig/") or name in tracked or name in declared
    others = _git(repo, "ls-files", "--others", "--exclude-standard", "-z") or b""
    inventory = tracked | {os.fsdecode(value) for value in others.split(b"\0") if value}
    status_rows = parse_status(_git(repo, "status", "--porcelain=v1", "-z", "--untracked-files=all") or b"")
    status_rows = [row for row in status_rows if visible(row["path"]) or visible(row.get("source", row["path"]))]
    dirty = {row[key] for row in status_rows for key in ("path", "source") if key in row and visible(row[key])}
    stages = _git(repo, "ls-files", "--stage", "-z") or b""
    index_entries = {}
    for entry in stages.split(b"\0"):
        if entry:
            metadata, name = entry.split(b"\t", 1)
            index_entries.setdefault(os.fsdecode(name), []).append(metadata.decode("ascii"))
    return {"git_available": True, "head": (_git(repo, "rev-parse", "HEAD") or b"").decode().strip(),
            "index_id": hashlib.sha256(stages).hexdigest(), "index_entries": index_entries,
            "inventory": sorted(name for name in inventory if visible(name)),
            "dirty_paths": sorted(dirty), "status": status_rows}


def _observed_entries(repo, names):
    result = {}
    for name in names:
        try:
            normalized = normalize_files(repo, [name])[0]
            result[name] = _entry(repo / normalized, repo=repo)
        except EvidenceError as error:
            path = repo / name
            result[name] = {"kind": "unreadable", "reason": str(error)}
            if path.is_symlink():
                result[name].update(kind="symlink", target=os.readlink(path))
    return result


def _capture(repo, files, initial_dirty=None):
    context = _inventory(repo, files)
    dirty = context["dirty_paths"] if initial_dirty is None else initial_dirty
    subject, snapshot_error = None, ""
    try:
        subject = snapshot(repo, files) if files else None
    except EvidenceError as error:
        snapshot_error = str(error)
    return {"version": 1, "created_at": now(), "files": files, "subject": subject,
            "snapshot_error": snapshot_error, "scoped_entries": _observed_entries(repo, files),
            **context, "initial_dirty_entries": _observed_entries(repo, dirty)}


def begin(repo, job_dir, files):
    root, folder = repository(repo), job_directory(repo, job_dir)
    names = normalize_files(root, files, allow_empty=True)
    previous = read_json(folder / "change-before.json")
    if (folder / "change-before.json").exists() and (previous is None or previous.get("version") != 1):
        raise EvidenceError("existing before evidence is malformed; it will not be replaced")
    if previous is not None:
        if previous.get("files") != names:
            raise EvidenceError("existing before-evidence scope cannot be changed")
        return previous
    value = {**_capture(root, names), "job_id": folder.name}
    write_json(folder / "change-before.json", value)
    return value


def finish(repo, job_dir):
    root, folder = repository(repo), job_directory(repo, job_dir)
    before = read_json(folder / "change-before.json")
    if not before or before.get("version") != 1:
        raise EvidenceError("before evidence is missing or malformed")
    files = normalize_files(root, before.get("files"), allow_empty=True)
    after = {**_capture(root, files, before.get("dirty_paths", [])), "job_id": folder.name}
    scoped = {name for name in files if before.get("scoped_entries", {}).get(name)
              != after.get("scoped_entries", {}).get(name)}
    changed = set(scoped) | (set(before["inventory"]) ^ set(after["inventory"]))
    for field in ("initial_dirty_entries", "index_entries"):
        left, right = before.get(field, {}), after.get(field, {})
        changed.update(name for name in left.keys() | right.keys() if left.get(name) != right.get(name))
    changed.update(set(after["dirty_paths"]) - set(before["dirty_paths"]))
    outside = sorted(changed - set(files))
    meta = read_json(folder / "meta.json") or {}
    established = meta.get("ownership_established") is True
    value = {"version": 1, "job_id": folder.name, "before": before, "after": after,
             "files": files, "snapshot_id": (after.get("subject") or {}).get("snapshot_id", ""),
             "initial_dirty_paths": before["dirty_paths"], "observed_changed_paths": sorted(changed),
             "files_changed": sorted(changed), "scoped_changed_paths": sorted(scoped),
             "out_of_scope_observations": [{"path": name, "attribution": "uncertain"} for name in outside],
             "attribution": "scoped" if established and files and not outside else "uncertain",
             "limitations": ["Changes restored between snapshots are not detected.",
                             "Out-of-scope concurrent changes are observations, not worker attribution."]}
    write_json(folder / "change-after.json", after)
    write_json(folder / "change-evidence.json", value)
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("snapshot", "begin", "finish"))
    parser.add_argument("--repo", required=True)
    parser.add_argument("--job-dir")
    parser.add_argument("--files-json", default="[]")
    args = parser.parse_args()
    try:
        files = json.loads(args.files_json)
        if args.command == "snapshot":
            result = snapshot(args.repo, files)
        elif not args.job_dir:
            parser.error("--job-dir is required for begin/finish")
        elif args.command == "begin":
            result = begin(args.repo, args.job_dir, files)
        else:
            result = finish(args.repo, args.job_dir)
    except (OSError, ValueError) as error:
        parser.exit(2, f"rig evidence: {error}\n")
    print(json.dumps(result, ensure_ascii=True))


if __name__ == "__main__":
    main()
