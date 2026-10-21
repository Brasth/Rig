#!/usr/bin/env python3
"""What helpers applied today, and the owner's say over it, as the design's "done today", undo, hold and ship.

A helper's work is applied when the parent accepts it: worktree_merge merges the job's branch into the base checkout
as one merge commit ("rig: merge job <id>"). Here the owner can undo that (a revert of the merge, so history is kept
and nothing is rewritten), redo it (a revert of the revert), hold a job so its branch is not merged until released,
and ship what is ahead of the upstream branch: push it, or open a pull request with gh. Agents never push; shipping
is always the owner's, and the board asks before each of these.

Every change runs under the same merge lock as worktree_merge and refuses rather than guesses: the base checkout must
be on the job's base branch with no staged or unstaged changes to tracked files."""
from __future__ import annotations

import fcntl
import json
import os
import re
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path

import worktrees
from worktree_merge import MERGE_IDENTITY

MAX_ROWS = 50


class ApplyError(ValueError):
    pass


def _env() -> dict:
    return {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}


def _git(repo: Path, *args, check=True, timeout=60) -> subprocess.CompletedProcess:
    done = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, errors="replace",
                          timeout=timeout, env=_env())
    if check and done.returncode != 0:
        tail = (done.stderr or done.stdout).strip().splitlines()
        raise ApplyError(tail[-1] if tail else f"git {args[0]} failed")
    return done


def _folder(repo: Path, job_id: str) -> Path:
    if not job_id or "/" in job_id or job_id.startswith("."):
        raise ApplyError(f"no such job: {job_id}")
    folder = repo / ".rig" / "jobs" / job_id
    if not folder.is_dir():
        raise ApplyError(f"no such job: {job_id}")
    return folder


@contextmanager
def _locked(repo: Path):
    common = Path(worktrees.resolve(repo)["common_dir"]) / "rig"
    common.mkdir(exist_ok=True)
    with (common / "merge.lock").open("a+") as lock:
        deadline = time.monotonic() + 30
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise ApplyError("another merge is in progress; retry shortly")
                time.sleep(0.05)
        yield


def _clean_on(repo: Path, branch: str) -> None:
    current = _git(repo, "symbolic-ref", "--quiet", "--short", "HEAD", check=False).stdout.strip()
    if current != branch:
        raise ApplyError(f"the checkout is on {current or 'a detached HEAD'}; switch to {branch} first")
    if _git(repo, "status", "--porcelain", "--untracked-files=no").stdout.strip():
        raise ApplyError("there are uncommitted changes in the checkout; commit or stash them first")


def _title(folder: Path) -> tuple[str, str]:
    try:
        meta = json.loads((folder / "meta.json").read_text())
    except (OSError, ValueError):
        meta = {}
    task = ""
    for key in ("title", "task", "brief", "prompt"):
        value = meta.get(key)
        if isinstance(value, str) and value.strip():
            task = value.strip().splitlines()[0]
            break
    if not task:
        try:
            task = (folder / "prompt.txt").read_text(errors="replace").strip().splitlines()[0]
        except (OSError, IndexError):
            task = folder.name
    return task[:120], str(meta.get("worker") or "")


def _counts(repo: Path, commit: str) -> dict:
    files = added = deleted = 0
    done = _git(repo, "diff", "--numstat", f"{commit}^1", commit, check=False)
    for line in done.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) == 3:
            files += 1
            added += int(parts[0]) if parts[0].isdigit() else 0
            deleted += int(parts[1]) if parts[1].isdigit() else 0
    return {"files": files, "added": added, "deleted": deleted}


ALLOWED = "allowed.jsonl"


def record_allowed(repo, worker: str, command: str, by: str) -> None:
    """Note a command someone allowed (the parent for a helper, or you for the parent) for done today. Never raises:
    an answer must not fail because this note could not be written."""
    try:
        folder = Path(repo) / ".rig" / "applies"
        folder.mkdir(parents=True, exist_ok=True)
        line = {"at": time.time(), "worker": str(worker or ""), "command": " ".join(str(command or "").split())[:200],
                "by": str(by or "")}
        with (folder / ALLOWED).open("a") as stream:
            stream.write(json.dumps(line) + "\n")
    except OSError:
        pass


def _allowed(repo: Path, since: float) -> list[dict]:
    rows = []
    try:
        lines = (repo / ".rig" / "applies" / ALLOWED).read_text(errors="replace").splitlines()[-500:]
    except OSError:
        return rows
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if not isinstance(row, dict) or not isinstance(row.get("at"), (int, float)) or row["at"] < since:
            continue
        rows.append({"job_id": "", "title": "allowed " + str(row.get("command", "")), "worker": str(row.get("worker", "")),
                     "by": str(row.get("by", "")), "branch": "", "state": "command", "at": row["at"], "commit": ""})
    return rows


def _checks(folder: Path) -> list[dict]:
    """The latest result of each check the job ran: name and whether it passed."""
    import verification
    try:
        rows = verification._check_rows(folder)
    except Exception:
        return []
    latest = {}
    for row in rows:
        latest[row.get("requirement_id", "")] = row
    return [{"name": name, "ok": row.get("status") == "passed" and row.get("exit_code") == 0}
            for name, row in latest.items() if name and row.get("status") in ("passed", "failed", "error")]


def _reviews(root: Path) -> dict:
    """Finished review jobs by the job they reviewed: writer job id to the reviewer's worker."""
    found = {}
    for folder in root.iterdir() if root.is_dir() else []:
        try:
            meta = json.loads((folder / "meta.json").read_text())
        except (OSError, ValueError):
            continue
        writer = meta.get("writer_job_id")
        if writer and meta.get("role") in ("review", "reviewer") and meta.get("status") == "ok":
            found[writer] = str(meta.get("worker") or "")
    return found


def _since_midnight() -> float:
    local = time.localtime()
    return time.mktime((local.tm_year, local.tm_mon, local.tm_mday, 0, 0, 0, 0, 0, -1))


def today(repo, since: float | None = None) -> dict:
    """Done today: applied (merged) jobs, undone ones, held ones and allowed commands, newest first."""
    repo = Path(repo).resolve()
    if since is None:
        since = _since_midnight()
    rows = _allowed(repo, since)
    root = repo / ".rig" / "jobs"
    reviews = _reviews(root)
    for folder in sorted(root.iterdir()) if root.is_dir() else []:
        try:
            record = worktrees.job_record(folder)
        except worktrees.WorktreeError:
            continue
        if not record:
            continue
        title, worker = _title(folder)
        row = {"job_id": folder.name, "title": title, "worker": worker, "branch": record.get("base_branch", "")}
        if record.get("held") and not record.get("merged"):
            rows.append({**row, "state": "held", "at": record.get("held_at", 0), "commit": ""})
            continue
        if not record.get("merged") or not record.get("merge_commit"):
            continue
        try:
            at = time.mktime(time.strptime(record.get("merged_at", ""), "%Y-%m-%dT%H:%M:%SZ")) - time.timezone
        except ValueError:
            at = 0
        if at < since and not record.get("undone_at"):
            continue
        state = "undone" if record.get("undone") else "applied"
        rows.append({**row, "state": state, "at": record.get("undone_at", at) if state == "undone" else at,
                     "commit": record["merge_commit"][:7], **_counts(repo, record["merge_commit"]),
                     "checks": _checks(folder), "review": reviews.get(folder.name, "")})
    rows.sort(key=lambda r: r["at"], reverse=True)
    return {"rows": rows[:MAX_ROWS]}


def undo(repo, job_id: str) -> dict:
    """Revert the job's merge commit on its base branch. History is kept; a conflict is aborted and reported."""
    repo = Path(repo).resolve()
    folder = _folder(repo, job_id)
    record = worktrees.job_record(folder) or {}
    if not record.get("merged") or not record.get("merge_commit"):
        raise ApplyError(f"{job_id} was not applied, so there is nothing to undo")
    if record.get("undone"):
        raise ApplyError(f"{job_id} is already undone")
    with _locked(repo):
        _clean_on(repo, record["base_branch"])
        if _git(repo, "merge-base", "--is-ancestor", record["merge_commit"], "HEAD", check=False).returncode != 0:
            raise ApplyError(f"{record['base_branch']} no longer contains {job_id}'s merge")
        done = _git(repo, *MERGE_IDENTITY, "revert", "--no-edit", "-m", "1", record["merge_commit"], check=False, timeout=120)
        if done.returncode != 0:
            _git(repo, "revert", "--abort", check=False)
            raise ApplyError(f"undoing {job_id} conflicts with later changes; ask the parent to undo it by hand")
        commit = _git(repo, "rev-parse", "HEAD").stdout.strip()
        record.update(undone=True, undo_commit=commit, undone_at=time.time())
        worktrees._write_record(folder, record)
    return {"job_id": job_id, "state": "undone", "commit": commit[:7]}


def redo(repo, job_id: str) -> dict:
    """Apply an undone job again: revert its undo commit."""
    repo = Path(repo).resolve()
    folder = _folder(repo, job_id)
    record = worktrees.job_record(folder) or {}
    if not record.get("undone") or not record.get("undo_commit"):
        raise ApplyError(f"{job_id} is not undone")
    with _locked(repo):
        _clean_on(repo, record["base_branch"])
        done = _git(repo, *MERGE_IDENTITY, "revert", "--no-edit", record["undo_commit"], check=False, timeout=120)
        if done.returncode != 0:
            _git(repo, "revert", "--abort", check=False)
            raise ApplyError(f"redoing {job_id} conflicts with later changes")
        record.update(undone=False, undo_commit="", redo_commit=_git(repo, "rev-parse", "HEAD").stdout.strip())
        record.pop("undone_at", None)
        worktrees._write_record(folder, record)
    return {"job_id": job_id, "state": "applied"}


def hold(repo, job_id: str, on: bool = True) -> dict:
    """Hold a job's branch: the parent's acceptance will not merge it until the hold is released."""
    repo = Path(repo).resolve()
    folder = _folder(repo, job_id)
    record = worktrees.job_record(folder)
    if not record:
        raise ApplyError(f"{job_id} has no worktree, so there is nothing to hold")
    if record.get("merged"):
        raise ApplyError(f"{job_id} is already applied; undo it instead")
    with _locked(repo):
        record = worktrees.job_record(folder)
        record.update(held=bool(on), held_at=time.time() if on else 0)
        worktrees._write_record(folder, record)
    return {"job_id": job_id, "held": bool(on)}


_SLUG = re.compile(r"[^a-z0-9]+")


def ship_status(repo) -> dict:
    """What is ahead of the upstream branch, and the pull request it would make."""
    repo = Path(repo).resolve()
    branch = _git(repo, "symbolic-ref", "--quiet", "--short", "HEAD", check=False).stdout.strip()
    upstream = _git(repo, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}", check=False).stdout.strip()
    rows = []
    if upstream:
        log = _git(repo, "log", "--format=%h%x09%s", f"{upstream}..HEAD", check=False).stdout
        rows = [{"commit": c, "subject": s} for c, _, s in (line.partition("\t") for line in log.splitlines()) if c]
    # A helper's merge is shown as its task, which the parent wrote; its checks count toward the ones shown here.
    applied = {r["commit"]: r for r in today(repo, since=0)["rows"] if r["state"] == "applied" and r["commit"]}
    tasks, checks = [], {}
    for row in rows:
        job = next((applied[c] for c in applied if row["commit"].startswith(c) or c.startswith(row["commit"])), None)
        if job:
            tasks.append(job["title"])
            for check in job.get("checks", []):
                checks[check["name"]] = checks.get(check["name"], True) and check["ok"]
    subjects = [r["subject"] for r in rows if not r["subject"].startswith(("rig: merge job", "Revert "))] or [r["subject"] for r in rows]
    named = tasks or subjects
    title = "; ".join(named[:3])[:72] if named else ""
    slug = _SLUG.sub("-", (named[0] if named else "changes").lower()).strip("-")[:40] or "changes"
    gh = subprocess.run(["sh", "-c", "command -v gh"], capture_output=True).returncode == 0
    return {"branch": branch, "upstream": upstream, "ahead": rows, "title": title, "title_from": "tasks" if tasks else "commits",
            "checks": [{"name": k, "ok": v} for k, v in checks.items()], "pr_branch": f"rig/{slug}", "gh": gh}


def ship(repo, mode: str, title: str = "", body: str = "") -> dict:
    """Push the commits ahead (mode "push"), or push them to a new branch and open a pull request ("pr")."""
    repo = Path(repo).resolve()
    status = ship_status(repo)
    if not status["upstream"]:
        raise ApplyError(f"{status['branch'] or 'this branch'} has no upstream branch to ship to")
    if not status["ahead"]:
        raise ApplyError("nothing to ship: the branch is not ahead of its upstream")
    remote = status["upstream"].split("/", 1)[0]
    if mode == "push":
        _git(repo, "push", remote, f"HEAD:{status['upstream'].split('/', 1)[1]}", timeout=180)
        return {"shipped": "push", "to": status["upstream"]}
    if mode != "pr":
        raise ApplyError("mode is push or pr")
    if not status["gh"]:
        raise ApplyError("opening a pull request needs the gh CLI")
    head = status["pr_branch"]
    _git(repo, "push", remote, f"HEAD:refs/heads/{head}", timeout=180)
    title = (title or status["title"] or "Changes from rig").strip()[:200]
    body = body or "\n".join(f"- {r['subject']}" for r in status["ahead"])
    done = subprocess.run(["gh", "pr", "create", "--head", head, "--base", status["upstream"].split("/", 1)[1], "--title", title,
                           "--body", body], cwd=str(repo), capture_output=True, text=True, timeout=120)
    if done.returncode != 0:
        tail = (done.stderr or done.stdout).strip().splitlines()
        raise ApplyError("gh pr create failed: " + (tail[-1] if tail else "unknown error"))
    return {"shipped": "pr", "url": done.stdout.strip().splitlines()[-1] if done.stdout.strip() else ""}
