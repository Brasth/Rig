#!/usr/bin/env python3
"""Pause and resume a running helper, as the design's `p pause` does: SIGSTOP to the worker's process group, SIGCONT to
let it go on. A `paused` file in the job's folder says it is paused; the board shows it. Nothing else changes: the
job stays running, its files stay held, and a stop still works on a paused job."""
from __future__ import annotations

import json
import os
import signal
import time
from pathlib import Path

import jobs as rig_jobs

PAUSED_NAME = "paused"


def paused_path(job_dir) -> Path:
    return Path(job_dir) / PAUSED_NAME


def is_paused(job_dir) -> bool:
    return paused_path(job_dir).is_file()


def _group(job: dict) -> int:
    pid = job.get("pid")
    if not pid or not rig_jobs.pid_alive(pid):
        raise ValueError(f"{job['job_id']} is not running")
    try:
        return os.getpgid(int(pid))
    except (OSError, ValueError) as error:
        raise ValueError(f"{job['job_id']}: cannot find its process group") from error


def pause(repo, job_id: str) -> dict:
    job = rig_jobs.resolve_job(Path(repo).resolve(), job_id)
    if job.get("kind") == "native":
        raise ValueError(f"{job['job_id']} runs inside the parent (native): the parent must pause it")
    if job.get("effective") not in ("running", "ask"):
        raise ValueError(f"{job['job_id']} is not running")
    if is_paused(job["dir"]):
        return {"job_id": job["job_id"], "paused": True, "already": True}
    group = _group(job)
    try:
        os.killpg(group, signal.SIGSTOP)
    except OSError as error:
        raise ValueError(f"{job['job_id']}: could not pause: {error}") from error
    paused_path(job["dir"]).write_text(json.dumps({"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "pgid": group}) + "\n")
    return {"job_id": job["job_id"], "paused": True}


def resume(repo, job_id: str) -> dict:
    job = rig_jobs.resolve_job(Path(repo).resolve(), job_id)
    path = paused_path(job["dir"])
    if not path.is_file():
        return {"job_id": job["job_id"], "paused": False, "already": True}
    try:
        group = _group(job)
        os.killpg(group, signal.SIGCONT)
    except (ValueError, OSError):
        pass  # the worker is gone: nothing to wake, but the mark goes
    path.unlink(missing_ok=True)
    return {"job_id": job["job_id"], "paused": False}


def resume_before_stop(job_dir) -> None:
    """A stop must reach a paused worker: SIGCONT first, so SIGTERM is handled."""
    path = paused_path(job_dir)
    if not path.is_file():
        return
    try:
        pgid = int(json.loads(path.read_text()).get("pgid"))
        os.killpg(pgid, signal.SIGCONT)
    except (OSError, ValueError, TypeError):
        pass
    path.unlink(missing_ok=True)
