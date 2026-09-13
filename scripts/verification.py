#!/usr/bin/env python3
"""Parent acceptance tied to immutable check requirements and scoped content.

The parent tool surface is the orchestration boundary. Local artifacts are not
cryptographic protection from other processes running as the same user.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import shutil
import subprocess
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

import change_evidence as evidence
from admission import mutation_guard


class VerificationError(ValueError):
    pass


@contextmanager
def _lock(folder):
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / "verification.lock").open("a") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def _parent_only():
    if os.environ.get("RIG_JOB_ID") or os.environ.get("RIG_JOB_DIR"):
        raise VerificationError("verification mutations are parent-only; child claims cannot authorize checks")


def _meta(folder):
    result = evidence.read_json(folder / "meta.json")
    if not result:
        raise VerificationError("job execution metadata is missing or malformed")
    return result


def _scope(repo, folder):
    return evidence.normalize_files(repo, _meta(folder).get("files", []))


def _argv(argv):
    if not isinstance(argv, (list, tuple)) or not argv or any(not isinstance(x, str) or "\0" in x for x in argv) or not argv[0]:
        raise VerificationError("check argv must be a nonempty array of strings")
    return list(argv)


def _name(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,100}", value):
        raise VerificationError("check IDs must be simple nonempty names")
    return value


def _requirements(repo, requirements):
    if not isinstance(requirements, list):
        raise VerificationError("requirements must be an array")
    result, used = [], set()
    for item in requirements:
        if not isinstance(item, dict):
            raise VerificationError("each requirement must contain id, argv, and optional cwd")
        name = _name(item.get("id", item.get("name")))
        if name in used:
            raise VerificationError("required-check IDs must be unique")
        used.add(name)
        result.append({"id": name, "argv": _argv(item.get("argv")),
                       "cwd": evidence.normalize_cwd(repo, item.get("cwd"))})
    return result


def _manifest(repo, folder):
    value = evidence.read_json(folder / "requirements.json")
    if not value or value.get("version") != 1:
        raise VerificationError("requirements manifest is missing or malformed")
    value["requirements"] = _requirements(repo, value.get("requirements"))
    criteria = value.get("manual_criteria")
    if not isinstance(criteria, list) or any(not isinstance(x, str) or not x.strip() for x in criteria):
        raise VerificationError("manual criteria are malformed")
    if not value["requirements"] and not criteria:
        raise VerificationError("declare at least one required check or manual criterion")
    return value


def _check_rows(folder):
    rows = []
    for path in (folder / "checks").glob("*.json"):
        row = evidence.read_json(path)
        if not row or row.get("version") != 1 or row.get("check_id") != path.stem:
            raise VerificationError("check evidence is malformed")
        _name(row.get("requirement_id"))
        if not isinstance(row.get("sequence"), int) or isinstance(row["sequence"], bool) or row["sequence"] < 1:
            raise VerificationError("check sequence is malformed")
        rows.append(row)
    return sorted(rows, key=lambda row: (int(row.get("sequence", 0)), row.get("started_at", ""), row["check_id"]))


def _idle(folder):
    if (folder / "check-running.json").exists():
        raise VerificationError("an active or interrupted check must finish before this mutation")


def _required_check_summary(folder, requirements):
    latest = {row["requirement_id"]: row for row in _check_rows(folder)}
    failed = [item["id"] for item in requirements
              if item["id"] in latest and latest[item["id"]].get("status") in {"failed", "error"}]
    return {"state": "failed" if failed else "pending", "acceptance": "pending",
            "reason": "required_check_failed: " + ", ".join(failed) if failed else "parent_acceptance_required"}


def _save_assessment(folder, value):
    previous = evidence.read_json(folder / "verification.json") or {}
    history = list(previous.get("history", []))
    if previous:
        history.append({key: item for key, item in previous.items() if key != "history"})
    result = {"version": 1, "job_id": folder.name, "assessed_at": evidence.now(), **value, "history": history}
    evidence.write_json(folder / "verification.json", result)
    return result


def record_requirements(repo, job_dir, requirements, manual_criteria, *,
                        reservation_id="", attempt_id="", owner_token="", owner_session=""):
    _parent_only()
    root, folder = evidence.repository(repo), evidence.job_directory(repo, job_dir)
    required = _requirements(root, requirements)
    criteria = [] if manual_criteria is None else manual_criteria
    if not isinstance(criteria, list) or any(not isinstance(x, str) or not x.strip() for x in criteria):
        raise VerificationError("manual_criteria must be an array of nonempty strings")
    criteria = list(dict.fromkeys(x.strip() for x in criteria))
    if not required and not criteria:
        raise VerificationError("declare at least one required check or manual criterion")
    with mutation_guard(root, folder, "requirements", reservation_id=reservation_id,
                        attempt_id=attempt_id, owner_token=owner_token, owner_session=owner_session), _lock(folder):
        _meta(folder)
        _scope(root, folder)
        _idle(folder)
        previous = evidence.read_json(folder / "requirements.json")
        if (folder / "requirements.json").exists() and previous is None:
            raise VerificationError("requirements manifest is malformed")
        if previous and previous.get("requirements") == required and previous.get("manual_criteria") == criteria:
            return previous
        if _check_rows(folder):
            old = _manifest(root, folder)
            current = {row["id"]: row for row in required}
            if any(current.get(row["id"]) != row for row in old["requirements"]) or not set(old["manual_criteria"]) <= set(criteria):
                raise VerificationError("requirements cannot be removed or redefined after checks begin; only append")
        value = {"version": 1, "job_id": folder.name, "requirements": required, "manual_criteria": criteria,
                 "created_at": (previous or {}).get("created_at", evidence.now()), "updated_at": evidence.now()}
        evidence.write_json(folder / "requirements.json", value)
        previous_assessment = evidence.read_json(folder / "verification.json") or {}
        _save_assessment(folder, {**_required_check_summary(folder, required),
                                  "method": "mixed" if required and criteria else "checks" if required else "manual",
                                  "snapshot_id": previous_assessment.get("snapshot_id", "")})
        return value


def _artifact(path, folder):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return {"path": path.relative_to(folder).as_posix(), "sha256": digest.hexdigest(), "bytes": path.stat().st_size}


def run_check(repo, job_dir, name, argv, cwd=None, on_tick=None, *,
              reservation_id="", attempt_id="", owner_token="", owner_session=""):
    _parent_only()
    root, folder = evidence.repository(repo), evidence.job_directory(repo, job_dir)
    command, directory, name = _argv(argv), evidence.normalize_cwd(root, cwd), _name(name)
    with mutation_guard(root, folder, "check", reservation_id=reservation_id,
                        attempt_id=attempt_id, owner_token=owner_token, owner_session=owner_session):
        with _lock(folder):
            _idle(folder)
            manifest = _manifest(root, folder)
            wanted = next((row for row in manifest["requirements"] if row["id"] == name), None)
            if wanted != {"id": name, "argv": command, "cwd": directory}:
                raise VerificationError("check name, argv, and cwd must match its declared requirement exactly")
            subject = evidence.snapshot(root, _scope(root, folder))
            check_id = name + "-" + uuid.uuid4().hex
            checks = folder / "checks"
            checks.mkdir(exist_ok=True)
            rows = _check_rows(folder)
            record = {"version": 1, "job_id": folder.name, "check_id": check_id, "requirement_id": name,
                      "argv": command, "cwd": directory, "started_at": evidence.now(), "status": "running",
                      "sequence": max((row.get("sequence", 0) for row in rows), default=0) + 1,
                      "before_snapshot_id": subject["snapshot_id"], "after_snapshot_id": "", "exit_code": None}
            evidence.write_json(checks / (check_id + ".json"), record)
            active = {"version": 1, "job_id": folder.name, "check_id": check_id, "name": name,
                      "pid": os.getpid(), "started_at": record["started_at"]}
            evidence.write_json(folder / "check-running.json", active)
            try:
                _save_assessment(folder, {"state": "verifying", "acceptance": "pending", "reason": "check_running",
                                          "snapshot_id": subject["snapshot_id"], "active_check": check_id})
            except BaseException:
                (folder / "check-running.json").unlink(missing_ok=True)
                raise
        process, failure = None, None
        stdout_path, stderr_path = checks / (check_id + ".stdout.log"), checks / (check_id + ".stderr.log")
        try:
            with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
                process = subprocess.Popen(command, cwd=root / directory, shell=False, stdout=stdout, stderr=stderr)
                active["process_pid"] = process.pid
                evidence.write_json(folder / "check-running.json", active)
                if on_tick:
                    on_tick({**_meta(folder), "job_id": folder.name, "effective": "verifying", "doing": name,
                             "check_id": check_id, "dir": str(folder)})
                record["exit_code"] = process.wait()
        except BaseException as error:
            failure = error
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            if process is not None:
                record["exit_code"] = process.returncode
            record["error"] = f"{type(error).__name__}: {error}"
        finally:
            try:
                record["ended_at"] = evidence.now()
                try:
                    record["after_snapshot_id"] = evidence.snapshot(root, _scope(root, folder))["snapshot_id"]
                except (OSError, ValueError) as error:
                    record["snapshot_error"] = str(error)
                record["status"] = "error" if failure else "passed" if record["exit_code"] == 0 else "failed"
                for path in (stdout_path, stderr_path):
                    if not path.exists():
                        path.touch()
                log = checks / (check_id + ".log")
                with log.open("wb") as output:
                    for label, path in ((b"stdout\n", stdout_path), (b"\nstderr\n", stderr_path)):
                        output.write(label)
                        with path.open("rb") as source:
                            shutil.copyfileobj(source, output)
                record.update(stdout=_artifact(stdout_path, folder), stderr=_artifact(stderr_path, folder), log=_artifact(log, folder))
                with _lock(folder):
                    evidence.write_json(checks / (check_id + ".json"), record)
                    _save_assessment(folder, {**_required_check_summary(folder, _manifest(root, folder)["requirements"]),
                                              "snapshot_id": record["after_snapshot_id"], "last_check_id": check_id})
            finally:
                (folder / "check-running.json").unlink(missing_ok=True)
        if failure is not None:
            raise failure
        return record


def _execution_problem(meta):
    mode = meta.get("execution_mode")
    if mode == "dry_run" or meta.get("dry_run") is True:
        return "dry_run"
    if mode == "retrospective":
        return "retrospective_execution"
    if not isinstance(mode, str) or mode not in {"live", "native", "parent"}:
        return "legacy_execution"
    if meta.get("status") != "ok":
        return "execution_not_ok"
    if (meta.get("ownership_established") is not True
            or any(not isinstance(meta.get(key), str) or not meta[key]
                   for key in ("reservation_id", "attempt_id"))):
        return "unreserved_execution"
    return ""


def _validate_artifacts(folder, row):
    for key in ("stdout", "stderr", "log"):
        value = row.get(key)
        if not isinstance(value, dict) or not isinstance(value.get("path"), str):
            raise VerificationError("check output artifacts are missing")
        path = (folder / value["path"]).resolve()
        if not path.is_relative_to((folder / "checks").resolve()) or not path.is_file():
            raise VerificationError("check output artifact is unavailable")
        if _artifact(path, folder)["sha256"] != value.get("sha256"):
            raise VerificationError("check output artifact changed")


def _required_passes(repo, folder, manifest, snapshot_id):
    rows, latest = _check_rows(folder), {}
    for row in rows:
        latest[row.get("requirement_id")] = row
    selected = []
    for requirement in manifest["requirements"]:
        row = latest.get(requirement["id"])
        if not row:
            raise VerificationError("missing required check: " + requirement["id"])
        if row.get("argv") != requirement["argv"] or row.get("cwd") != requirement["cwd"]:
            raise VerificationError("required check identity changed: " + requirement["id"])
        if row.get("status") != "passed" or row.get("exit_code") != 0:
            raise VerificationError("required check did not pass: " + requirement["id"])
        if row.get("before_snapshot_id") != snapshot_id or row.get("after_snapshot_id") != snapshot_id:
            raise VerificationError("required check did not observe unchanged current content: " + requirement["id"])
        _validate_artifacts(folder, row)
        selected.append(row["check_id"])
    return selected


def accept(repo, job_dir, decision, snapshot_id, check_ids=None, rationale="", next="complete", *,
           reservation_id="", attempt_id="", owner_token="", owner_session=""):
    _parent_only()
    root, folder = evidence.repository(repo), evidence.job_directory(repo, job_dir)
    if decision not in {"accept", "reject"} or next not in {"complete", "review"}:
        raise VerificationError("decision must be accept/reject and next must be complete/review")
    if not isinstance(rationale, str) or not rationale.strip():
        raise VerificationError("parent acceptance requires a nonempty rationale addressing the criteria")
    if check_ids is not None and (not isinstance(check_ids, list) or any(not isinstance(x, str) for x in check_ids)):
        raise VerificationError("check_ids must be an array of evidence IDs")
    with mutation_guard(root, folder, "accept", reservation_id=reservation_id,
                        attempt_id=attempt_id, owner_token=owner_token, owner_session=owner_session) as reservation, _lock(folder):
        _idle(folder)
        meta, manifest = _meta(folder), _manifest(root, folder)
        current = evidence.snapshot(root, _scope(root, folder))
        if not snapshot_id or current["snapshot_id"] != snapshot_id:
            raise VerificationError("content_changed: acceptance requires the current subject snapshot")
        selected = set(check_ids or [])
        known = {row["check_id"] for row in _check_rows(folder)}
        if selected - known:
            raise VerificationError("unknown check evidence reference")
        if decision == "accept":
            problem = _execution_problem(meta)
            if problem:
                raise VerificationError(problem + ": execution cannot be verified")
            selected.update(_required_passes(root, folder, manifest, snapshot_id))
        method = "mixed" if manifest["requirements"] and manifest["manual_criteria"] else "checks" if manifest["requirements"] else "manual"
        values = {"state": "verified" if decision == "accept" else "failed",
                  "acceptance": "accepted" if decision == "accept" else "rejected",
                  "reason": "parent_accepted" if decision == "accept" else "parent_rejected",
                  "method": method, "snapshot_id": snapshot_id, "check_ids": sorted(selected),
                  "rationale": rationale.strip(), "manual_criteria": manifest["manual_criteria"], "next": next,
                  "reservation_id": reservation_id, "attempt_id": attempt_id}
        previous = evidence.read_json(folder / "verification.json") or {}
        if all(previous.get(key) == value for key, value in values.items()):
            return previous
        if reservation.get("stage") == "released":
            raise VerificationError("released attempt permits only an identical accepted completion; start a fresh attempt")
        values.update(accepted_at=evidence.now() if decision == "accept" else "",
                      parent_identity={"pid": os.getpid(), "cli": os.environ.get("RIG_PARENT", ""),
                                       "thread": owner_session or reservation.get("owner", {}).get("session_id", "")})
        return _save_assessment(folder, values)


def worker_claims(job_dir):
    value = evidence.read_json(Path(job_dir) / "worker-evidence.json")
    if not value or value.get("version") != 1:
        return {"available": False, "trusted": False, "reason": "missing_or_malformed_child_claims"}
    return {"available": True, "trusted": False, "claims": {key: value.get(key) for key in
            ("summary", "claimed_files", "claimed_checks", "limitations") if key in value}}


def _alive(pid):
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True
    except OSError:
        return False


def assessment(repo, job, refresh=False, cache=None):
    root = Path(repo)
    folder = Path(job.get("dir") or root / ".rig" / "jobs" / str(job.get("job_id", ""))) if isinstance(job, dict) else Path(job)
    stored = evidence.read_json(folder / "verification.json")
    result = {"state": "unknown", "acceptance": "pending", "reason": "missing_verification",
              "freshness": "not_checked", "snapshot_id": "", "method": "", "accepted_at": ""}
    meta = job if isinstance(job, dict) and not refresh else evidence.read_json(folder / "meta.json") or {}
    if stored is None:
        if (folder / "verification.json").exists():
            result["reason"] = "malformed_verification"
        return result
    if stored.get("version") != 1 or not isinstance(stored.get("state"), str) or stored["state"] not in {"unknown", "pending", "verifying", "verified", "failed"}:
        result["reason"] = "malformed_verification"
        return result
    if not isinstance(stored.get("acceptance"), str) or stored["acceptance"] not in {"pending", "accepted", "rejected"}:
        result["reason"] = "malformed_verification"
        return result
    if stored.get("state") == "verified" and (
        stored.get("acceptance") != "accepted"
        or not isinstance(stored.get("method"), str) or stored["method"] not in {"checks", "manual", "mixed"}
        or not isinstance(stored.get("snapshot_id"), str)
        or not re.fullmatch(r"[a-f0-9]{64}", stored["snapshot_id"])
        or not isinstance(stored.get("rationale"), str) or not stored["rationale"].strip()
        or not isinstance(stored.get("accepted_at"), str) or not stored["accepted_at"]
        or not isinstance(stored.get("check_ids"), list)
        or any(not isinstance(value, str) for value in stored["check_ids"])
    ):
        result["reason"] = "malformed_verification"
        return result
    result.update({key: stored.get(key, result[key]) for key in result if key != "freshness"})
    if result["state"] == "verified" and result["acceptance"] != "accepted":
        result.update(state="pending", reason="parent_acceptance_required")
    problem = _execution_problem(meta)
    if problem:
        result.update(state="pending", reason=problem)
        return result
    if stored.get("state") == "verified" and any(
        stored.get(key) != meta.get(key) for key in ("reservation_id", "attempt_id")
    ):
        result.update(state="pending", reason="acceptance_attempt_changed")
        return result
    active = evidence.read_json(folder / "check-running.json")
    if active:
        alive = _alive(active.get("pid")) or _alive(active.get("process_pid"))
        result.update(state="verifying" if alive else "pending",
                      reason="check_running" if alive else "interrupted_check", active_check=active)
        return result
    if (folder / "check-running.json").exists():
        result.update(state="pending", reason="malformed_check_activity")
        return result
    if not refresh:
        return result
    try:
        current = evidence.snapshot(root, _scope(root, folder), cache=cache)
        result["current_snapshot_id"] = current["snapshot_id"]
        result["freshness"] = "current"
        if stored.get("snapshot_id") != current["snapshot_id"]:
            if result["state"] == "failed":
                result["reason"] = "content_changed; " + result["reason"]
            else:
                result.update(state="pending", reason="content_changed")
        elif stored.get("state") == "verified" and stored.get("acceptance") == "accepted":
            manifest = _manifest(root, folder)
            required_ids = _required_passes(root, folder, manifest, current["snapshot_id"])
            if not set(required_ids) <= set(stored["check_ids"]):
                raise VerificationError("required_checks_changed")
            method = "mixed" if manifest["requirements"] and manifest["manual_criteria"] else "checks" if manifest["requirements"] else "manual"
            if stored.get("method") != method or stored.get("manual_criteria") != manifest["manual_criteria"]:
                raise VerificationError("acceptance requirements changed")
    except (OSError, ValueError) as error:
        result.update(state="pending", reason=str(error), freshness="unavailable")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    required = commands.add_parser("requirements")
    required.add_argument("id")
    required.add_argument("--file", required=True)
    check = commands.add_parser("check")
    check.add_argument("id")
    check.add_argument("--name", required=True)
    check.add_argument("--cwd")
    accepted = commands.add_parser("accept")
    accepted.add_argument("id")
    accepted.add_argument("--decision", choices=("accept", "reject"), required=True)
    accepted.add_argument("--snapshot-id", required=True)
    accepted.add_argument("--checks", default="")
    accepted.add_argument("--rationale", required=True)
    accepted.add_argument("--next", choices=("complete", "review"), default="complete")
    for command in (required, check, accepted):
        command.add_argument("--reservation-id", default="")
        command.add_argument("--attempt-id", default="")
        command.add_argument("--owner-session", default="")
    import sys
    arguments = sys.argv[1:]
    command_argv = []
    if "--" in arguments:
        delimiter = arguments.index("--")
        arguments, command_argv = arguments[:delimiter], arguments[delimiter + 1:]
    args = parser.parse_args(arguments)
    try:
        _parent_only()
        # Job IDs retain the shared safe-ID and unique-partial lookup contract;
        # required-check names have their own narrower filename convention.
        import jobs as rig_jobs
        folder = Path(rig_jobs.resolve_job(evidence.repository(args.repo), args.id)["dir"])
        ownership = {"reservation_id": args.reservation_id, "attempt_id": args.attempt_id,
                     "owner_token": os.environ.get("RIG_OWNER_TOKEN", ""), "owner_session": args.owner_session}
        if args.command == "requirements":
            manifest = evidence.read_json(args.file)
            if manifest is None:
                raise VerificationError("manifest file is malformed")
            result = record_requirements(args.repo, folder, manifest.get("requirements", []), manifest.get("manual_criteria", []), **ownership)
        elif args.command == "check":
            result = run_check(args.repo, folder, args.name, command_argv, cwd=args.cwd, **ownership)
        else:
            result = accept(args.repo, folder, args.decision, args.snapshot_id,
                            [value for value in args.checks.split(",") if value], args.rationale, args.next, **ownership)
    except (OSError, ValueError) as error:
        parser.exit(2, f"rig verification: {error}\n")
    print(json.dumps(result, ensure_ascii=True))


if __name__ == "__main__":
    main()
