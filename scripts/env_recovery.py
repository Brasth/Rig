"""Environment recovery through dc-cli: show the one next step, apply it only when it is safe and confirmed.

`dc recover --json` names a plan (id, summary, command, apply, applyAllowed). Rig shows every plan, but runs
only the apply actions in SAFE_APPLY, only for the plan id the caller confirmed, and writes one audit line
per attempt. Everything else (sudo, opening guides, switching Docker contexts, pruning, starting a sandbox
for a folder without config) is shown as text for the owner to run; Rig never prunes volumes.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import dc_client

SAFE_APPLY = frozenset({"colima_start", "create_nets", "take_ports"})
AUDIT = Path(".rig") / "recovery" / "environment.jsonl"


def _parse(done) -> dict:
    try:
        data = json.loads(done.stdout)
    except ValueError as error:
        detail = (done.stderr or done.stdout).strip().splitlines()
        raise dc_client.DcError(f"dc recover did not print JSON (exit {done.returncode}): {detail[-1] if detail else 'no output'}") from error
    nxt, host = data.get("next") or {}, data.get("host") or {}
    apply_id = str(dc_client.pick(nxt, "apply", default="none") or "none")
    allowed = dc_client.pick(nxt, "applyAllowed", default=False) is True
    return {"ready": nxt.get("id") == "ready", "id": str(nxt.get("id") or ""), "summary": str(nxt.get("summary") or ""),
            "command": str(nxt.get("command") or ""), "apply": apply_id, "apply_allowed": allowed,
            "safe": allowed and apply_id in SAFE_APPLY, "host_status": str(host.get("status") or ""),
            "host_code": str(host.get("code") or "")}


def plan(repo) -> dict:
    """The plan dc-cli would follow now. The exit status is 1 whenever something is wrong, so it is not an error here."""
    repo = Path(repo).resolve()
    return _parse(dc_client.run(["recover", "--json", str(repo)], repo, timeout=60))


def _audit(repo: Path, entry: dict) -> None:
    path = repo / AUDIT
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as stream:
        stream.write(json.dumps(entry, sort_keys=True) + "\n")


def apply(repo, confirm_id: str, actor: str = "board") -> dict:
    """Run dc's next step once. Refuses unless the live plan is the confirmed one and its action is in SAFE_APPLY."""
    repo = Path(repo).resolve()
    if not confirm_id:
        raise ValueError("confirm_id is required: the id of the plan the owner confirmed")
    before = plan(repo)
    entry = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "actor": actor, "confirmed": confirm_id,
             "plan": before["id"], "action": before["apply"]}
    if before["id"] != confirm_id:
        _audit(repo, {**entry, "outcome": "refused", "reason": "plan_changed"})
        raise ValueError(f"the plan changed from {confirm_id!r} to {before['id']!r}; review it and confirm again")
    if not before["safe"]:
        _audit(repo, {**entry, "outcome": "refused", "reason": "not_safe"})
        raise ValueError(f"{before['apply']!r} is not an action Rig applies; run it yourself: {before['command']}")
    done = dc_client.run(["recover", "--yes", "--json", str(repo)], repo, timeout=300)
    try:
        after = _parse(done)
    except dc_client.DcError:
        after = {"id": "", "ready": False}
    _audit(repo, {**entry, "outcome": "applied" if done.returncode == 0 else "failed", "exit": done.returncode,
                  "after": after["id"]})
    return {"applied": before["apply"], "exit": done.returncode, "before": before, "after": after}
