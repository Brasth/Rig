"""Evidence drawer for one job: where it ran, what changed, the contract, the checks and the ports.

Everything here is read from records Rig already wrote or from git and dc-cli as observed now; nothing is
estimated. Sections are ready to render ({title, lines: [{text, tone}]}) so the Go shell never parses it.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import change_evidence as evidence
import worktrees

MAX_FILES = 30


def _line(text: str, tone: str = "") -> dict:
    return {"text": text, "tone": tone}


def _short(sha) -> str:
    return str(sha or "")[:10]


def _numstat(path, *args) -> list[tuple[str, str, str]]:
    try:
        done = subprocess.run(["git", "-C", str(path), "diff", "--numstat", "--no-renames", *args],
                              capture_output=True, text=True, timeout=20,
                              env={k: v for k, v in os.environ.items() if not k.startswith("GIT_")})
    except (OSError, subprocess.TimeoutExpired):
        return []
    rows = []
    for raw in done.stdout.splitlines():
        parts = raw.split("\t", 2)
        if len(parts) == 3:
            rows.append((parts[0], parts[1], parts[2]))
    return rows


def _ran(folder: Path, record) -> list[dict]:
    lines = []
    sandbox = evidence.read_json(folder / "sandbox.json") or {}
    if sandbox.get("provider") == "devcontainer":
        box = sandbox.get("container") or {}
        if box.get("found"):
            lines.append(_line(f"container  {box.get('name') or '?'} {_short(box.get('id'))}  {box.get('state') or ''}".rstrip()))
            if box.get("image"):
                lines.append(_line(f"image      {box['image']}"))
        else:
            lines.append(_line("container  not found when the job started", "attention"))
    else:
        lines.append(_line("on the host"))
    if record:
        state = ("merged " + _short(record.get("merge_commit"))) if record.get("merged") else (
            "nothing to merge" if record.get("nothing_to_merge") else "not merged")
        lines.append(_line(f"worktree   {record.get('branch')}  base {record.get('base_branch')} {_short(record.get('base_head'))}"))
        lines.append(_line(f"           {state}" + ("  (worktree removed)" if record.get("removed") else ""),
                           "ok" if record.get("merged") else ""))
        if not record.get("merged") and not record.get("nothing_to_merge") and Path(record["path"]).is_dir() \
                and (evidence.read_json(folder / "meta.json") or {}).get("status") in {"ok", "fail", "failed", "timeout", "cancelled"}:
            lines.append(_line(f"           kept; remove with: rig worktrees remove --job {folder.name}"))
        if record.get("cleanup_error"):
            lines.append(_line("           cleanup: " + record["cleanup_error"], "attention"))
    return lines


def _change(repo: Path, folder: Path, record) -> list[dict]:
    rows = []
    source = "git"
    if record and record.get("branch_head"):
        rows = _numstat(repo, f"{record['base_head']}..{record['branch_head']}")
    elif record and Path(record["path"]).is_dir():
        rows = _numstat(record["path"], record["base_head"])
    else:
        report = evidence.read_json(folder / "change-evidence.json") or {}
        source = "recorded"
        rows = [("", "", name) for name in report.get("scoped_changed_paths", [])]
    lines = []
    for added, deleted, name in rows[:MAX_FILES]:
        counts = f"+{added} -{deleted}" if added else "changed"
        lines.append(_line(f"{counts:<12}{name}"))
    if len(rows) > MAX_FILES:
        lines.append(_line(f"… {len(rows) - MAX_FILES} more files"))
    if not rows:
        lines.append(_line("no changes recorded"))
    report = evidence.read_json(folder / "change-evidence.json") or {}
    for item in (report.get("out_of_scope_observations") or [])[:10]:
        lines.append(_line(f"outside scope (uncertain)  {item.get('path')}", "attention"))
    lines.append(_line(f"source: {source}", ""))
    return lines


def _contract(repo: Path, folder: Path) -> list[dict]:
    import acceptance_contract as contracts
    import verification
    meta = evidence.read_json(folder / "meta.json") or {}
    lines = []
    try:
        assessed = verification.assessment(repo, meta, refresh=False) if meta else {}
        lines.append(_line(f"acceptance {assessed.get('state', 'unknown')} · {assessed.get('reason') or assessed.get('acceptance', '')}".strip(" ·"),
                           "ok" if assessed.get("state") == "verified" else ""))
        frozen = contracts.load(repo, folder, meta)
    except Exception as error:  # a damaged contract is evidence worth showing, not a crash
        return [_line(f"unavailable: {error}", "attention")]
    if frozen is None:
        return lines + [_line("no acceptance contract")]
    outcomes = {row.get("criterion_id"): row for row in (assessed.get("criterion_outcomes") or [])}
    stored = evidence.read_json(folder / "verification.json") or {}
    outcomes.update({row.get("criterion_id"): row for row in (stored.get("criterion_outcomes") or [])})
    for criterion in frozen["contract"]["criteria"]:
        outcome = outcomes.get(criterion["id"])
        mark = "✓" if outcome and outcome.get("result") == "pass" else "○"
        how = "check" if criterion["evidence_type"] == "check" else "review"
        lines.append(_line(f"{mark} {criterion['id']}  {criterion['description']}  [{how}]", "ok" if mark == "✓" else ""))
    return lines


def _checks(folder: Path) -> list[dict]:
    import verification
    try:
        rows = verification._check_rows(folder)
    except Exception as error:
        return [_line(f"unavailable: {error}", "attention")]
    lines = []
    for row in rows[-12:]:
        code = row.get("exit_code")
        where = (row.get("sandbox") or {}).get("provider", "host")
        lines.append(_line(f"{'●' if code == 0 else '!'} {row.get('requirement_id')}  exit {code if code is not None else 'running'}  {where}",
                           "ok" if code == 0 else "attention" if code is not None else ""))
    return lines or [_line("no checks run")]


def _ports(repo: Path, folder: Path, record) -> list[dict]:
    sandbox = evidence.read_json(folder / "sandbox.json") or {}
    if sandbox.get("provider") != "devcontainer":
        return [_line("no container")]
    import dc_client
    import workspace
    where = Path(record["path"]) if record and Path(record["path"]).is_dir() else repo
    try:
        rows = workspace.parse_forward_status(dc_client.run_text(["forward", "--status"], where, timeout=10))
    except dc_client.DcError as error:
        return [_line(f"unavailable: {error}", "attention")]
    return [_line(("! " if row["problem"] else "  ") + f"localhost:{row['port']}  {row['state']}", "attention" if row["problem"] else "")
            for row in rows] or [_line("no forwarded ports")]


def build(repo, job_id: str) -> dict:
    repo = Path(repo).resolve()
    folder = repo / ".rig" / "jobs" / job_id
    if not job_id or "/" in job_id or not folder.is_dir():
        raise ValueError(f"no such job: {job_id}")
    try:
        record = worktrees.job_record(folder)
    except worktrees.WorktreeError:
        record = None
    sections = []
    for title, make in (("Ran", lambda: _ran(folder, record)), ("Change", lambda: _change(repo, folder, record)),
                        ("Contract", lambda: _contract(repo, folder)), ("Checks", lambda: _checks(folder)),
                        ("Ports", lambda: _ports(repo, folder, record))):
        try:
            sections.append({"title": title, "lines": make()})
        except Exception as error:
            sections.append({"title": title, "lines": [_line(f"unavailable: {error}", "attention")]})
    return {"job_id": job_id, "sections": sections}
