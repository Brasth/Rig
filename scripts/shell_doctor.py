#!/usr/bin/env python3
"""What is wrong with this project, in plain words, and the fix for what Rig can fix safely.

`findings(repo)` is what /doctor in rig shell lists: each row is {id, level, title, detail, fix}. A fix names an action
Rig can take on its own because it can verify it is safe (remove a blank file, remove a job folder that never started
and holds no reservation, clear the error log). Everything else is told in words for the owner to do: Rig never
initializes, enables, installs or repairs a project by itself. `apply(repo, fix_id)` runs one fix.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
LEVELS = ("problem", "warning", "note")
STALE_SECONDS = 120  # a job folder younger than this may still be being made
ERRORS_SHOWN = 3


def finding(fid: str, level: str, title: str, detail: str, fix: str = "", fix_label: str = "") -> dict:
    return {"id": fid, "level": level, "title": title, "detail": detail,
            "fix": fix, "fix_label": fix_label}


def _rig(repo: Path) -> Path:
    return Path(repo) / ".rig"


def blank_thread(repo: Path) -> dict | None:
    path = _rig(repo) / "thread"
    try:
        if path.is_file() and not path.read_text(errors="replace").strip():
            return finding("blank-thread", "problem", "The thread file is empty",
                           ".rig/thread has no session id. Older Rig versions failed every launch with "
                           "'list index out of range' because of it.", "remove-thread", "remove it")
    except OSError:
        pass
    return None


def never_started(repo: Path) -> list[dict]:
    """Job folders with a brief and no meta.json: a launch that stopped before it began. Safe to remove when no
    reservation is held for them; a held one needs the recovery tools, which this only names."""
    jobs_dir = _rig(repo) / "jobs"
    if not jobs_dir.is_dir():
        return []
    import admission
    try:
        held = {row.get("job_id") for row in admission.list_reservations(repo) if row.get("slot_held")}
    except Exception:  # noqa: BLE001 - a broken reservation file is its own finding, not a reason to hide the rest
        held = set()
    out = []
    now = time.time()
    for folder in sorted(jobs_dir.iterdir()):
        if not folder.is_dir() or (folder / "meta.json").exists():
            continue
        try:
            age = now - max(p.stat().st_mtime for p in [folder, *folder.iterdir()])
        except (OSError, ValueError):
            continue
        if age < STALE_SECONDS:
            continue
        if folder.name in held:
            out.append(finding("held-" + folder.name, "problem", f"Job {folder.name} never started but holds its slot",
                               "A reservation is still held for it. Ask the parent to run rig_job_recover_cancelled or "
                               "rig_job_reconcile for this job; Rig will not release another owner's reservation."))
        else:
            out.append(finding("stale-" + folder.name, "warning", f"Job {folder.name} never started",
                               "Its folder has a brief and no meta.json, so Rig shows 'never started' for it.",
                               "remove-job:" + folder.name, "remove the folder"))
    return out


def recent_errors(repo: Path) -> dict | None:
    """Unexpected errors the MCP tools hit (.rig/mcp-errors.log): the newest few, with where each came from."""
    path = _rig(repo) / "mcp-errors.log"
    try:
        text = path.read_text(errors="replace")
    except OSError:
        return None
    entries = [e for e in text.split("--- ")[1:] if e.strip()]
    if not entries:
        return None
    lines = []
    for entry in entries[-ERRORS_SHOWN:]:
        head, _, rest = entry.partition("\n")
        last = [l for l in rest.strip().splitlines() if l.strip()]
        lines.append(f"{head.strip()}: {last[-1].strip() if last else ''}")
    return finding("mcp-errors", "warning", f"{len(entries)} unexpected error{'s' if len(entries) != 1 else ''} in Rig's tools",
                   " · ".join(lines) + " (full tracebacks: .rig/mcp-errors.log)", "clear-errors", "clear the log")


def from_doctor(repo: Path) -> list[dict]:
    """What `rig doctor` already checks, as findings: every required check that is not ready, in its own words."""
    import doctor
    try:
        report = doctor.build_report(Path(repo))
    except (ValueError, OSError, SystemExit) as error:
        return [finding("doctor", "problem", "rig doctor could not run", str(error) or "unknown error")]
    out = []
    for row in report.get("checks", []):
        status = str(row.get("status") or "")
        if status in ("ready", "configured-host-unverified") or "evidence" in status or "optional" in status:
            continue  # fine, not tested, or something the user chose not to use: not a problem to show
        if not row.get("required") and status != "failed":
            continue  # an optional thing that is missing is a choice, not a fault
        level = "problem" if row.get("required") else "warning"
        out.append(finding("check-" + str(row.get("name")).replace(" ", "-"), level, f"{row.get('name')}: {status}",
                           str(row.get("detail") or "")))
    return out


FIXES = {
    "remove-thread": "remove the empty .rig/thread",
    "clear-errors": "empty .rig/mcp-errors.log",
}


def findings(repo) -> dict:
    repo = Path(repo).resolve()
    rows = []
    for one in (blank_thread(repo),):
        if one:
            rows.append(one)
    rows.extend(never_started(repo))
    one = recent_errors(repo)
    if one:
        rows.append(one)
    rows.extend(from_doctor(repo))
    rows.sort(key=lambda r: LEVELS.index(r["level"]))
    return {"findings": rows, "problems": sum(1 for r in rows if r["level"] == "problem"),
            "warnings": sum(1 for r in rows if r["level"] == "warning"), "checked": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}


def apply(repo, fix: str) -> dict:
    """Run one fix by the id a finding named, after checking again that it is still safe."""
    repo = Path(repo).resolve()
    if fix == "remove-thread":
        if blank_thread(repo) is None:
            raise ValueError("the thread file is not empty any more; nothing was removed")
        (_rig(repo) / "thread").unlink(missing_ok=True)
        return {"fix": fix, "done": "removed the empty .rig/thread"}
    if fix == "clear-errors":
        (_rig(repo) / "mcp-errors.log").unlink(missing_ok=True)
        return {"fix": fix, "done": "cleared .rig/mcp-errors.log"}
    if fix.startswith("remove-job:"):
        name = fix.split(":", 1)[1]
        if not name or "/" in name or name in (".", ".."):
            raise ValueError("not a job name")
        still = [f for f in never_started(repo) if f["id"] == "stale-" + name]
        if not still:
            raise ValueError(f"job {name} is not a stale job that never started (it has a meta.json, a held slot, or is too new); nothing was removed")
        import shutil
        shutil.rmtree(_rig(repo) / "jobs" / name)
        return {"fix": fix, "done": f"removed .rig/jobs/{name}"}
    raise ValueError(f"unknown fix {fix!r}")


def main(argv=None) -> int:
    import argparse
    parser = argparse.ArgumentParser(prog="rig shell doctor", description="What is wrong with this project, and the safe fixes")
    parser.add_argument("--repo", default="")
    parser.add_argument("--fix", default="", help="run one fix by the id a finding names")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    import jobs
    repo = jobs.repo_root(args.repo or None)
    if args.fix:
        print(json.dumps(apply(repo, args.fix)))
        return 0
    result = findings(repo)
    if args.json:
        print(json.dumps(result, indent=1))
        return 0
    if not result["findings"]:
        print("nothing wrong found")
    for row in result["findings"]:
        print(f"{row['level']:8} {row['title']}\n         {row['detail']}" + (f"\n         fix: {row['fix']} ({row['fix_label']})" if row["fix"] else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
