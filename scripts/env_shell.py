"""The command for an interactive shell in a job's container, decided on the server so the Go shell never derives it.

Always ``dc exec --no-start <workspace>``: a shell can enter a container that is already running and can never start
one (the Phase 2 rule that only ``rig env up`` and the launcher start containers). The workspace is the job's own
worktree when its container is keyed by that worktree, otherwise the repository.
"""
from __future__ import annotations

from pathlib import Path

import dc_client
import worktrees


def plan(repo, job_id: str = "") -> dict:
    repo = Path(repo).resolve()
    workspace = repo
    if job_id:
        folder = repo / ".rig" / "jobs" / job_id
        if "/" in job_id or not folder.is_dir():
            return {"available": False, "reason": f"no such job: {job_id}", "argv": [], "workspace": ""}
        try:
            record = worktrees.job_record(folder)
        except worktrees.WorktreeError:
            record = None
        if record and record.get("container") == "per-job" and not (record.get("merged") or record.get("removed")) \
                and Path(record["path"]).is_dir():
            workspace = Path(record["path"])
    binary = dc_client.dc_binary()
    if not binary:
        return {"available": False, "reason": "dc-cli is not installed", "argv": [], "workspace": str(workspace)}
    import sandbox_env
    status = sandbox_env.env_status(workspace)
    running = [row for row in status["containers"]
               if str(dc_client.pick(row, "state", "status", default="")).lower().startswith(("up", "run"))]
    if status["error"]:
        return {"available": False, "reason": status["error"], "argv": [], "workspace": str(workspace)}
    if not running:
        return {"available": False, "argv": [], "workspace": str(workspace),
                "reason": f"no running container for {workspace}; start it with: rig env up"}
    import rig_shell
    return {"available": True, "reason": "", "argv": [binary, "exec", "--no-start", str(workspace)], "workspace": str(workspace),
            "dir": str(repo), "env": rig_shell.pane_env_pairs(repo), "label": "container shell"}
