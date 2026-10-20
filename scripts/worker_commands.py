#!/usr/bin/env python3
"""Worker launch argv for the built-in workers, and the adapter hand-off.

`run-worker.sh` calls this once per launch. The argv for each built-in worker is
fixed here (tests/fixtures/worker-argv/matrix.json pins it against the shell code
it replaced); adapter workers expand their manifest through adapters.py.
"""
from __future__ import annotations

import argparse
import sys
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
ADAPTER_DOCS = HERE / ".." / "adapters"  # same spelling the shell used
BUILTIN = ("grok", "codex", "claude", "cursor", "opencode", "omp", "pi", "agy", "devin", "mimo")


class Refuse(Exception):
    pass


def _doc(name: str) -> Path:
    return ADAPTER_DOCS / name


def _read_doc(name: str) -> str | None:
    path = _doc(name)
    if not path.is_file():
        return None
    # Mirrors $(cat file): trailing newlines are dropped.
    return path.read_text(encoding="utf-8").rstrip("\n")


def build(worker: str, *, work: str, brief_file: str, brief_text: str, model: str = "",
          effort: str = "", role: str = "", job_id: str = "", timeout_secs: str = "",
          resume: bool = False, session: str = "", binary: str = "",
          mcp_argv: list[str] | None = None) -> tuple[list[str], str]:
    """Return (argv, session_id) for a built-in worker."""
    mcp = list(mcp_argv or [])
    sid = ""
    cmd: list[str]
    if worker == "grok":
        if resume:
            sid = session
            cmd = ["grok", "--no-auto-update", "-r", sid, "--prompt-file", brief_file, "--cwd", work,
                   "--output-format", "streaming-json", "--always-approve", "--max-turns", "40"]
        else:
            sid = str(uuid.uuid4())
            cmd = ["grok", "--no-auto-update", "--prompt-file", brief_file, "--cwd", work,
                   "--output-format", "streaming-json", "--session-id", sid, "--always-approve",
                   "--max-turns", "40"]
        if model:
            cmd += ["-m", model]
        if effort:
            cmd += ["--effort", effort]
    elif worker == "codex":
        cmd = ["codex", "exec", "--json", "-s", "workspace-write", "-C", work]
        if model:
            cmd += ["-m", model]
        if effort:
            cmd += ["-c", f'model_reasoning_effort="{effort}"']
        cmd += mcp
        if resume:
            sid = session
            cmd += ["resume", sid, brief_text]
        else:
            cmd += [brief_text]
    elif worker == "claude":
        cmd = ["claude", "-p", "--model", model or "claude-sonnet-5-5", "--output-format", "stream-json",
               "--verbose", "--permission-mode", "acceptEdits", "--allowedTools",
               "Read,Grep,Glob,Bash,Edit,Write", "--tools", "Bash,Edit,Read,Grep,Glob,Write"]
        cmd += mcp
        cmd += ["--permission-prompt-tool", "mcp__rig-ask__permission_prompt", "--disable-slash-commands"]
        if resume:
            sid = session
            cmd += ["--resume", sid]
        if _doc("claude/CLAUDE.worker.md").is_file():
            cmd += ["--append-system-prompt-file", str(_doc("claude/CLAUDE.worker.md"))]
        # Haiku print-mode hangs on --effort. Effort is still stamped in meta.json.
        if effort and "haiku" not in model.lower():
            cmd += ["--effort", effort]
        cmd += ["--", brief_text]
    elif worker == "cursor":
        prompt = brief_text
        doc = _read_doc("cursor/CURSOR.worker.md")
        if doc is not None:
            prompt = doc + "\n\n" + brief_text
        cmd = [binary or "cursor-agent", "-p", "--workspace", work, "--output-format", "stream-json",
               "--stream-partial-output", "--force", "--trust"]
        cmd += mcp
        if model:
            cmd += ["--model", model]
        if role in ("explore", "mini"):
            cmd += ["--mode=ask"]
        if resume:
            sid = session
            cmd += ["--resume", sid]
        cmd += [prompt]
    elif worker == "opencode":
        cmd = ["opencode", "run", "--format", "json", "--dir", work, "--title", f"rig {job_id}", "--auto"]
        if resume:
            sid = session
            cmd += ["--session", sid]
        if model:
            cmd += ["-m", model]
        cmd += [brief_text]
    elif worker in ("omp", "pi"):
        if worker == "omp":
            cmd = ["omp", "-p", "--mode", "json", "--cwd", work, "--no-session", "--approval-mode", "write"]
        else:
            cmd = ["pi", "-p", "--mode", "json", "--no-session", "--approve"]
        doc = _doc(f"{worker}/{worker.upper()}.worker.md")
        if doc.is_file():
            cmd += ["--append-system-prompt", str(doc)]
        if model:
            cmd += ["--model", model]
        if effort:
            cmd += ["--thinking", effort]
        cmd += [brief_text]
    elif worker == "agy":
        prompt = brief_text
        doc = _read_doc("agy/AGY.worker.md")
        if doc is not None:
            prompt = doc + "\n\n" + brief_text
        cmd = ["agy", "-p", prompt, "--output-format", "json", "--mode", "accept-edits",
               "--print-timeout", f"{timeout_secs}s", "--disable-slash-commands"]
        if model:
            cmd += ["--model", model]
        if effort:
            cmd += ["--effort", effort]
    elif worker == "devin":
        if not model:
            raise Refuse("Devin requires an exact SWE-2 model")
        cmd = ["devin", "--print", "--prompt-file", brief_file, "--model", model, "--permission-mode",
               "accept-edits", "--respect-workspace-trust", "true"]
        cmd += mcp
    elif worker == "mimo":
        cmd = ["mimo", "run", "--format", "json", "--dir", work]
        if model:
            cmd += ["--model", model]
        if effort:
            cmd += ["--variant", effort]
        cmd += [brief_text]
    else:
        raise Refuse(f"not a built-in worker: {worker}")
    return cmd, sid


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="worker_commands.py")
    ap.add_argument("--worker", required=True)
    ap.add_argument("--work", required=True)
    ap.add_argument("--brief-file", required=True)
    ap.add_argument("--model", default="")
    ap.add_argument("--effort", default="")
    ap.add_argument("--role", default="")
    ap.add_argument("--job-id", default="")
    ap.add_argument("--timeout-secs", default="")
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--session", default="")
    ap.add_argument("--binary", default="")
    ap.add_argument("--mcp-arg", action="append", default=[])
    args = ap.parse_args(argv)
    # Read here, not from argv: a long brief would exceed the per-argument limit. Mirrors $(cat file).
    brief_text = Path(args.brief_file).read_text(encoding="utf-8").rstrip("\n")
    try:
        cmd, sid = build(args.worker, work=args.work, brief_file=args.brief_file,
                         brief_text=brief_text, model=args.model, effort=args.effort,
                         role=args.role, job_id=args.job_id, timeout_secs=args.timeout_secs,
                         resume=args.resume, session=args.session, binary=args.binary,
                         mcp_argv=args.mcp_arg)
    except Refuse as exc:
        print(f"run-worker: refuse: {exc}", file=sys.stderr)
        return 1
    # NUL-separated: the first element is the session id (possibly empty).
    sys.stdout.buffer.write(b"\0".join(s.encode("utf-8") for s in [sid, *cmd]) + b"\0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
