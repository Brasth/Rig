#!/usr/bin/env python3
"""Regenerate matrix.json from a saved copy of the old shell build_worker_cmd.

Usage: capture.py FUNC_FILE OUT.json   (FUNC_FILE: the function body text, incl. closing brace)
Kept for provenance; the test only reads matrix.json.
"""
import itertools, json, re, shlex, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "run-worker.sh"
func = Path(sys.argv[1]).read_text().replace('"${BASH_SOURCE[0]}"', f'"{SCRIPT}"')
WORKERS = ["grok", "codex", "claude", "cursor", "opencode", "omp", "pi", "agy", "devin", "mimo"]
BRIEF = 'do "this" $HOME `x`\nsecond line\'s end'
cases = []
i = 0
for w, resume, model, effort, role in itertools.product(
        WORKERS, [0, 1], ["", "gpt-x", "claude-haiku-4-5", "composer-2"], ["", "high"],
        ["worker", "explore", "mini"]):
    i += 1
    mcp = [] if i % 2 else ["--mcp-config", "/j/mcp.json", "--strict-mcp-config"]
    binary = "" if i % 3 else f"/opt/bin/{w}"
    cases.append(dict(worker=w, resume=resume, model=model, effort=effort, role=role, mcp=mcp, binary=binary))
out = []
for c in cases:
    driver = f'''set -euo pipefail
{func}
WORKER={c["worker"]}; SHOULD_RESUME={c["resume"]}; CONTINUES_SESSION=sess-123
MODEL={shlex.quote(c["model"])}; EFFORT={shlex.quote(c["effort"])}; ROLE={c["role"]}; BIN={shlex.quote(c["binary"])}
WORK=/w/work; BRIEF=/j/brief.md; BRIEF_TEXT={shlex.quote(BRIEF)}; JOB_ID=job-1; TIMEOUT_SECS=1200
CHILD_MCP_ARGV=({" ".join(shlex.quote(a) for a in c["mcp"])})
build_worker_cmd
printf '%s\\0' "$SESSION_ID" "${{CMD[@]}}"
'''
    r = subprocess.run(["bash", "-c", driver], capture_output=True)
    if r.returncode:
        out.append({**c, "error": True})
        continue
    parts = r.stdout.decode().split("\0")[:-1]
    sid, cmd = parts[0], parts[1:]
    if re.fullmatch(r"[0-9a-f-]{36}", sid):
        cmd = ["<uuid>" if a == sid else a for a in cmd]
        sid = "<uuid>"
    out.append({**c, "session": sid, "argv": cmd})
Path(sys.argv[2]).write_text(json.dumps(out, indent=1) + "\n")
print(len(out), "cases", sum(1 for o in out if o.get("error")), "errors")
