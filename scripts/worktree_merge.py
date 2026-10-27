#!/usr/bin/env python3
"""The merge gate for per-job worktrees (design: docs/journals/261020-worktree-merge-gate-design.md).

Admission no longer keeps worktree writers off the same files, so the overlap is decided here, when the
parent accepts the work: the job's branch must change only its declared files, merge onto the current base
without conflict, and not collide with uncommitted work in the base checkout. Nothing in this module runs
before acceptance, and a refused gate leaves the job, its worktree and its reservation untouched.
"""
from __future__ import annotations

import fcntl
import json
import os
import subprocess
import time
from pathlib import Path

import worktrees
from worktrees import WorktreeError

MERGE_IDENTITY = worktrees.COMMIT_IDENTITY


class GateRefused(WorktreeError):
    """The branch cannot be merged now. ``reason`` is a stable code; ``paths`` names the files involved."""

    def __init__(self, reason, message, paths=()):
        super().__init__(f"{reason}: {message}")
        self.reason, self.paths = reason, list(paths)


def _lines(raw):
    return [line for line in os.fsdecode(raw).splitlines() if line]


def _declared_scope(repo, folder):
    """The union of the declared scopes of every job on the branch: each one's edits were admitted under its own."""
    import change_evidence as evidence
    root = worktrees.resolve(repo)["root"]
    scope = []
    for member, _ in worktrees.chain(repo, folder) or [(Path(folder), None)]:
        meta = evidence.read_json(member / "meta.json") or {}
        files = evidence.normalize_files(root, meta.get("files", []), allow_empty=True)
        if not files:
            return []  # one job with no declared scope: nothing to check against
        scope.extend(f for f in files if f not in scope)
    return scope


def gate(repo, job_dir):
    """Check, without changing anything, whether the job's branch can be merged now."""
    root, folder = Path(repo).resolve(), Path(job_dir).resolve()
    record = worktrees.job_record(folder)
    if not record:
        return {"applies": False, "ok": True}
    if record.get("merged"):
        return {"applies": True, "ok": True, "merged": True, "merge_commit": record.get("merge_commit", "")}
    if record.get("removed") and not record.get("merged"):
        raise GateRefused("worktree_removed", "the job's worktree was removed before it was merged")
    if record.get("held"):
        raise GateRefused("held", "the owner is holding this job's changes; it merges after the hold is released (rig shell, h)")
    busy = worktrees._busy(record["path"]) if Path(record["path"]).is_dir() else []
    if busy:
        raise GateRefused("worktree_busy", "a process is still running in the worktree (pid " + ", ".join(map(str, busy)) + ")")
    record = worktrees.commit_job(root, folder)  # capture anything left after the worker stopped
    base, head, branch = record["base_branch"], record["base_head"], record["branch"]
    current = os.fsdecode(worktrees._git(root, "symbolic-ref", "--quiet", "--short", "HEAD", allow_failure=True)).strip()
    if current != base:
        raise GateRefused("base_moved", f"the base checkout is on {current or 'a detached HEAD'}, the job was based on {base}")
    changed = _lines(worktrees._git(root, "diff", "--name-only", "-z", "--no-renames", head, record["branch_head"]).replace(b"\0", b"\n"))
    result = {"applies": True, "ok": True, "merged": False, "branch": branch, "base_branch": base,
              "changed": changed, "scope_checked": False, "nothing_to_merge": not changed,
              "chain": [member.name for member, _ in worktrees.chain(root, folder)]}
    if not changed:
        return result
    scope = _declared_scope(root, folder)
    if scope:
        result["scope_checked"] = True
        outside = sorted(set(changed) - set(scope))
        if outside:
            raise GateRefused("out_of_scope", "the job branch changes files outside its declared scope: " + ", ".join(outside), outside)
    if worktrees._git(root, "diff", "--cached", "--name-only", allow_failure=True).strip():
        raise GateRefused("base_dirty", "the base checkout has staged changes; commit or unstage them first")
    dirty = set(_lines(worktrees._git(root, "diff", "--name-only", "HEAD", allow_failure=True)))
    dirty |= set(_lines(worktrees._git(root, "ls-files", "--others", "--exclude-standard", allow_failure=True)))
    collide = sorted(dirty & set(changed))
    if collide:
        raise GateRefused("base_dirty", "uncommitted changes in the base checkout would be overwritten: " + ", ".join(collide), collide)
    probe = subprocess.run(["git", "-C", str(root), "merge-tree", "--write-tree", "--name-only", base, branch],
                           capture_output=True, text=True,
                           env={k: v for k, v in os.environ.items() if not k.startswith("GIT_")})
    if probe.returncode == 1:
        names = []
        for line in probe.stdout.splitlines()[1:]:
            if not line.strip():
                break
            names.append(line.strip())
        raise GateRefused("conflict", "the job branch does not merge cleanly onto " + base + ": " + ", ".join(names), names)
    if probe.returncode != 0:
        raise GateRefused("git_too_old", "the merge check needs git 2.38 or newer (git merge-tree --write-tree): "
                          + (probe.stderr.strip().splitlines() or ["failed"])[-1])
    return result


def _merge_message(root, folder, branch) -> str:
    """'Merge <the job commit's subject>' with the job as a footer, so history reads as what changed; the old
    'rig: merge job <id>' when the job commit has no subject to borrow."""
    try:
        subject = os.fsdecode(worktrees._git(root, "log", "-1", "--format=%s", branch)).strip()
    except Exception:
        subject = ""
    if not subject or subject.startswith("rig job "):
        return f"rig: merge job {folder.name}"
    return f"Merge {subject}\n\nRig-Job: {folder.name}\n"


def merge(repo, job_dir, snapshot_id=""):
    """Gate, then merge the job's branch into the base checkout and clean up. Idempotent once merged."""
    root, folder = Path(repo).resolve(), Path(job_dir).resolve()
    record = worktrees.job_record(folder)
    if not record:
        return {"applies": False, "ok": True}
    if record.get("merged"):
        return gate(root, folder)
    common = Path(worktrees.resolve(root)["common_dir"]) / "rig"
    common.mkdir(exist_ok=True)
    with (common / "merge.lock").open("a+") as lock:
        deadline = time.monotonic() + 30
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise GateRefused("merge_busy", "another merge is in progress; retry shortly")
                time.sleep(0.05)
        result = gate(root, folder)  # under the lock, so two accepts cannot both pass against the same base
        record = worktrees.job_record(folder)
        if result.get("nothing_to_merge"):
            merge_commit = ""
        else:
            done = subprocess.run(["git", "-C", str(root), *MERGE_IDENTITY, "merge", "--no-ff", "-m",
                                   _merge_message(root, folder, record["branch"]), record["branch"]],
                                  capture_output=True, text=True, timeout=120,
                                  env={k: v for k, v in os.environ.items() if not k.startswith("GIT_")})
            if done.returncode != 0:
                subprocess.run(["git", "-C", str(root), "merge", "--abort"], capture_output=True)
                tail = (done.stderr or done.stdout).strip().splitlines()
                raise GateRefused("merge_failed", tail[-1] if tail else "git merge failed")
            merge_commit = os.fsdecode(worktrees._git(root, "rev-parse", "HEAD")).strip()
        record.update(merged=not result.get("nothing_to_merge"), merge_commit=merge_commit,
                      merged_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), nothing_to_merge=bool(result.get("nothing_to_merge")),
                      snapshot_id=snapshot_id)
        worktrees._write_record(folder, record)
        # One branch: every job that shared the worktree is merged by this commit.
        worktrees._mark_chain(root, folder, merged=record["merged"], merge_commit=merge_commit, merged_at=record["merged_at"],
                              nothing_to_merge=record["nothing_to_merge"])
        try:
            worktrees.remove_job(root, folder)
        except WorktreeError as error:  # a reviewer may still be reading it; the merge itself stands
            record = worktrees.job_record(folder)
            record["cleanup_error"] = str(error)
            worktrees._write_record(folder, record)
    return {**result, "merged": not result.get("nothing_to_merge"), "merge_commit": merge_commit}
