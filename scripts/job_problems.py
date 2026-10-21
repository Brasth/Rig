#!/usr/bin/env python3
"""Problems a job's checks found, for the shell's status bar: each failed check's output read for the lines that
name a file and a line (compilers, linters, test runners), as an IDE's Problems view lists them."""
from __future__ import annotations

import re
from pathlib import Path

import change_evidence as evidence

MAX_PROBLEMS = 200

# file:line[:col]: [severity:] message — gcc, tsc, rustc's notes, eslint's compact, mypy, flake8, go vet
_COLON = re.compile(r"^(?P<file>[^\s:][^:\n]*?\.[A-Za-z0-9]+)(?:\((?P<l2>\d+),(?P<c2>\d+)\))?:(?P<line>\d+)?(?::(?P<col>\d+))?:?\s*(?:-\s*)?"
                    r"(?P<sev>error|warning|warn|note|info|hint)?\s*[:\-]?\s*(?P<msg>.+)$", re.I)
# File "x.py", line 12 — python tracebacks and pytest
_PY = re.compile(r'^\s*File "(?P<file>[^"]+)", line (?P<line>\d+)')
# --> src/main.rs:4:5 — rustc; and " at file:line" — node stacks
_ARROW = re.compile(r"^\s*(?:-->|at)\s+(?P<file>[^\s:()]+\.[A-Za-z0-9]+):(?P<line>\d+)(?::(?P<col>\d+))?")
_SEVERITY_LINE = re.compile(r"^\s*(?P<sev>error|warning)(?:\[[^\]]*\])?:\s*(?P<msg>.+)$", re.I)


def parse(text: str, cwd_hint: str = "") -> list[dict]:
    """Problems in a check's output: {file, line, col, severity, message, check}."""
    out, last_sev_msg = [], None
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        m = _SEVERITY_LINE.match(line)
        if m:
            last_sev_msg = (m.group("sev").lower(), m.group("msg").strip())
            continue
        for rx in (_PY, _ARROW, _COLON):
            m = rx.match(line)
            if not m:
                continue
            file = m.group("file").strip()
            if file.startswith(("http://", "https://")) or "/" not in file and "." not in file:
                break
            ln = m.groupdict().get("line") or m.groupdict().get("l2")
            if not ln:
                break
            col = m.groupdict().get("col") or m.groupdict().get("c2") or 0
            sev = (m.groupdict().get("sev") or "").lower()
            msg = (m.groupdict().get("msg") or "").strip()
            if rx is _PY or rx is _ARROW:
                if last_sev_msg:
                    sev, msg = last_sev_msg
                else:
                    sev, msg = sev or "error", msg or line.strip()
            sev = {"warn": "warning", "note": "info", "hint": "info"}.get(sev, sev or "error")
            out.append({"file": file, "line": int(ln), "col": int(col), "severity": sev, "message": msg[:300]})
            last_sev_msg = None
            break
    # the same place once, the first message kept
    seen, kept = set(), []
    for p in out:
        key = (p["file"], p["line"], p["message"])
        if key not in seen:
            seen.add(key)
            kept.append(p)
    return kept[:MAX_PROBLEMS]


def _relative(repo: Path, file: str) -> str:
    """A path as the repository knows it: absolute ones inside it made relative, ./ dropped."""
    f = file[2:] if file.startswith("./") else file
    try:
        p = Path(f)
        if p.is_absolute():
            return str(p.resolve().relative_to(repo))
    except (ValueError, OSError):
        pass
    return f


def problems(repo, job_id: str) -> dict:
    repo = Path(repo).resolve()
    folder = repo / ".rig" / "jobs" / job_id
    if not job_id or "/" in job_id or job_id.startswith(".") or not folder.is_dir():
        raise ValueError(f"no such job: {job_id}")
    import verification
    rows = []
    try:
        checks = verification._check_rows(folder)
    except Exception:
        checks = []
    for row in checks:
        if row.get("exit_code") in (0, None):
            continue
        name = str(row.get("requirement_id") or row.get("check_id") or "check")
        text = ""
        for key in ("stdout", "stderr", "log"):
            art = row.get(key)
            path = art.get("path") if isinstance(art, dict) else art
            if isinstance(path, str):
                try:
                    text += Path(path if Path(path).is_absolute() else folder / path).read_text(errors="replace") + "\n"
                except OSError:
                    pass
        if not text.strip():
            continue
        for p in parse(text):
            p["file"] = _relative(repo, p["file"])
            p["check"] = name
            rows.append(p)
        if not any(p["check"] == name for p in rows):
            # a failed check with nothing that names a file: one problem for the check itself
            rows.append({"file": "", "line": 0, "col": 0, "severity": "error", "message": f"{name} failed (exit {row.get('exit_code')})",
                         "check": name})
    errors = sum(1 for p in rows if p["severity"] == "error")
    warnings = sum(1 for p in rows if p["severity"] == "warning")
    return {"job_id": job_id, "errors": errors, "warnings": warnings, "problems": rows[:MAX_PROBLEMS]}


def latest(repo) -> dict:
    """The problems of the newest job that has any: what the status bar counts with no job chosen."""
    repo = Path(repo).resolve()
    jobs = repo / ".rig" / "jobs"
    if not jobs.is_dir():
        return {"job_id": "", "errors": 0, "warnings": 0, "problems": []}
    folders = sorted((f for f in jobs.iterdir() if f.is_dir() and (f / "meta.json").is_file()), key=lambda f: f.stat().st_mtime, reverse=True)
    for folder in folders[:10]:
        found = problems(repo, folder.name)
        if found["problems"]:
            return found
    return {"job_id": "", "errors": 0, "warnings": 0, "problems": []}


_ = evidence
