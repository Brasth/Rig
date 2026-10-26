#!/usr/bin/env python3
"""rig dsh-worker: run one Rig job with DeepSeek Harness (`dsh`) as the worker.

The adapter package ``adapters/deepseek`` names this as its command. It turns what a Rig worker is given (the brief, the
job's MCP config naming the ``rig`` and ``rig-ask`` servers, a working directory) into what ``dsh`` takes:

* the task is the brief, as the positional argument of ``dsh --profile headless TASK`` (a brief too long for one argument
  becomes "read and follow this file");
* the MCP servers go in a per-run patch file, ``--patch JOB/dsh.patch.yml``, that inserts ``@deepseek-ai/dsh-mcp-client``
  with each server (the user's own profiles and credentials are left alone);
* telemetry is switched off and the permission mode set for the job.

Assumptions not yet checked against a real ``dsh`` (docs/journals/261026-deepseek-harness-worker-plan.md, Step 0 and
scripts/dsh-probe.sh): the patch row shape in ``render_patch``, that ``--patch`` is applied on top of the ``headless``
profile, and that headless never waits for an approval under ``DSH_PERMISSION_MODE``. Everything that depends on them is
in ``render_patch`` and ``command_for`` so a fix is one place.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

MCP_CLIENT = "@deepseek-ai/dsh-mcp-client"
MAX_TASK_BYTES = 100_000  # one argument; macOS allows 256 KB for all of them
PERMISSION = {"host": "workspace-write", "container": "workspace-write"}


class DshError(RuntimeError):
    pass


# The adapter package for this worker (docs/adapters.md). `rig dsh-worker install` writes it to the user's adapter
# directory, which updates never touch; examples/adapters/deepseek/adapter.json is the same manifest.
MANIFEST = {
    "schema": 1,
    "name": "deepseek",
    "binary": "rig",
    "provider": "deepseek",
    "command": ["{binary}", "dsh-worker", "--work", "{work}", "--brief-file", "{brief_file}", "--job-dir", "{job_dir}",
                "{model_args}", "{effort_args}", "{mcp}", "{permissions}"],
    "model_args": ["--model", "{model}"],
    "effort_args": ["--effort", "{effort}"],
    "mcp": {"mode": "flag", "argv": ["--mcp-config", "{mcp_path}"]},
    "permissions": {"host": ["--mode", "host"], "container": ["--mode", "container"]},
    "container": {"supported": False, "credential_paths": []},
    "output": {"decoder": "plain"},
}


def adapter_dir() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME") or "~/.config").expanduser() / "rig" / "adapters" / "deepseek"


def install_adapter() -> Path:
    """Write the adapter package into the user's adapter directory; enable it per project with [workers] deepseek = true."""
    folder = adapter_dir()
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "adapter.json").write_text(json.dumps(MANIFEST, indent=2) + "\n")
    return folder


def render_patch(servers: dict) -> str:
    """The per-run patch inserting one MCP client row per Rig server. JSON is YAML, so the file is written as JSON.

    Row shape follows a third-party setup guide (id, name, config with serverName, transport, command, args, cwd): unverified."""
    rows = []
    for name, server in servers.items():
        config = {"serverName": name, "transport": "stdio", "command": server["command"], "args": list(server.get("args") or [])}
        if server.get("env"):
            config["env"] = dict(server["env"])
        if server.get("cwd"):
            config["cwd"] = server["cwd"]
        rows.append({"insert": {"id": f"rig-mcp-{name}", "name": MCP_CLIENT, "config": config}})
    return json.dumps(rows, indent=2) + "\n"


def read_servers(mcp_config) -> dict:
    try:
        data = json.loads(Path(mcp_config).read_text())
    except (OSError, ValueError) as error:
        raise DshError(f"cannot read the MCP config {mcp_config}: {error}") from error
    servers = data.get("mcpServers") if isinstance(data, dict) else None
    if not isinstance(servers, dict) or not servers or any(not isinstance(s, dict) or "command" not in s for s in servers.values()):
        raise DshError(f"{mcp_config} names no MCP servers with a command")
    return servers


def task_text(brief_file) -> str:
    text = Path(brief_file).read_text(errors="replace")
    if len(text.encode()) > MAX_TASK_BYTES:
        return f"Your task is long. Read the whole brief in {brief_file} and carry it out."
    return text


def command_for(binary: str, patch, task: str) -> list[str]:
    return [binary, "--profile", "headless", "--patch", str(patch), task]


def environment(mode: str, base=None) -> dict:
    env = dict(os.environ if base is None else base)
    env["DSH_TELEMETRY_MODE"] = "DISABLED"
    env["DSH_PERMISSION_MODE"] = PERMISSION.get(mode, "workspace-write")
    return env


def prepare(args) -> tuple[list[str], dict, Path]:
    binary = os.environ.get("DSH_BIN") or shutil.which("dsh")
    if not binary:
        raise DshError("dsh is not installed (npm install -g @deepseek-ai/dsh), or set DSH_BIN to its path")
    if not (os.environ.get("DEEPSEEK_API_KEY") or "").strip() and not (Path(os.environ.get("DSH_HOME") or Path.home() / ".dsh") / ".credentials.yaml").is_file():
        raise DshError("no DeepSeek credentials: set DEEPSEEK_API_KEY, or sign in with dsh once")
    work = Path(args.work)
    if not work.is_dir():
        raise DshError(f"the working directory {work} does not exist")
    job_dir = Path(args.job_dir)
    job_dir.mkdir(parents=True, exist_ok=True)
    patch = job_dir / "dsh.patch.yml"
    patch.write_text(render_patch(read_servers(args.mcp_config)))
    return command_for(binary, patch, task_text(args.brief_file)), environment(args.mode), work


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else list(argv)
    if argv[:1] == ["install"]:
        folder = install_adapter()
        print(f"installed the deepseek adapter in {folder}\nenable it in .rig/harness.toml:  [workers]  deepseek = true\nthen launch with worker = \"deepseek\"")
        return 0
    parser = argparse.ArgumentParser(prog="rig dsh-worker")
    parser.add_argument("--work", required=True)
    parser.add_argument("--brief-file", required=True)
    parser.add_argument("--job-dir", required=True)
    parser.add_argument("--mcp-config", required=True)
    parser.add_argument("--mode", choices=("host", "container"), default="host")
    parser.add_argument("--model", default="")
    parser.add_argument("--effort", default="")
    args = parser.parse_args(argv)
    try:
        command, env, work = prepare(args)
    except DshError as error:
        print(f"rig dsh-worker: {error}", file=sys.stderr)
        return 2
    if args.model or args.effort:
        # dsh has no model flag: the model is part of its profile configuration
        print("rig dsh-worker: note: dsh takes its model from its profile; --model/--effort are not passed", file=sys.stderr)
    os.chdir(work)
    os.execvpe(command[0], command, env)  # replaces this process: signals and the process tree stay Rig's to manage
    return 0  # unreachable


if __name__ == "__main__":
    raise SystemExit(main())
