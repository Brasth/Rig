#!/usr/bin/env python3
"""SandboxProvider: where a worker process runs.

Only the ``host`` provider exists today and it changes nothing. The interface is
the single seam a container backend plugs into later; callers never branch on
the provider name. An unsupported or malformed provider is an explicit error,
never a silent fall back to the host.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import harness  # noqa: E402


class SandboxError(ValueError):
    """The configured provider cannot be used."""


class SandboxProvider:
    name = ""

    def status(self, repo: Path) -> dict:
        raise NotImplementedError

    def wrap_worker_cmd(self, cmd: list[str], env: dict, *, worker: str = "",
                        job_dir=None) -> tuple[list[str], dict]:
        """Return the argv and environment the worker is actually started with."""
        raise NotImplementedError

    def run_check(self, argv: list[str], cwd: Path, env: dict | None = None) -> subprocess.CompletedProcess:
        raise NotImplementedError

    def evidence(self) -> dict:
        """Non-secret facts recorded with acceptance evidence."""
        raise NotImplementedError


class HostProvider(SandboxProvider):
    name = "host"

    def status(self, repo: Path) -> dict:
        return {"provider": "host", "boundary": "none", "ready": True}

    def wrap_worker_cmd(self, cmd, env, *, worker="", job_dir=None):
        return list(cmd), dict(env)

    def run_check(self, argv, cwd, env=None):
        return subprocess.run(argv, cwd=str(cwd), env=env, capture_output=True, text=True)

    def evidence(self):
        return {"provider": "host"}


def provider_for(repo) -> SandboxProvider:
    config = harness.parse_harness(harness.harness_path(Path(repo)))["sandbox"]
    if config["error"]:
        raise SandboxError(config["error"])
    if config["provider"] == "host":
        return HostProvider()
    if config["provider"] == "devcontainer":
        import dc_client
        if not dc_client.dc_binary():
            raise SandboxError("[sandbox] provider = \"devcontainer\" needs dc-cli, which is not installed (no `dc` on PATH); "
                               "install it or set provider = \"host\"")
        from sandbox_container import DevcontainerProvider
        return DevcontainerProvider(repo, config["credentials"])
    raise SandboxError(f"[sandbox] provider {config['provider']!r} is not available in this Rig runtime")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Rig sandbox provider helper for run-worker.sh and doctor")
    parser.add_argument("command", choices=["check", "status", "name", "prepare", "wrap"])
    parser.add_argument("--repo", default=".")
    parser.add_argument("--worker", default="")
    parser.add_argument("--job-dir", default="")
    parser.add_argument("--job-id", default="")
    parser.add_argument("--role", default="worker")
    parser.add_argument("argv", nargs="*", help="wrap: the worker command, after --")
    args = parser.parse_args(argv)
    try:
        if args.command == "name":
            config = harness.parse_harness(harness.harness_path(Path(args.repo)))["sandbox"]
            if config["error"]:
                raise SandboxError(config["error"])
            print(config["provider"])
            return 0
        provider = provider_for(args.repo)
        if args.command == "status":
            print(json.dumps(provider.status(Path(args.repo)), sort_keys=True))
        elif args.command == "prepare":
            if not hasattr(provider, "prepare"):
                return 0  # the host provider has nothing to prepare
            provider.prepare(args.worker, args.job_dir, args.job_id)
        elif args.command == "wrap":
            if not args.argv:
                raise SandboxError("wrap needs the worker command after --")
            env = {"RIG_JOB_ID": args.job_id, "RIG_JOB_DIR": args.job_dir,
                   "RIG_REPO": str(Path(args.repo).resolve()), "RIG_ROLE": args.role}
            wrapped, _ = provider.wrap_worker_cmd(args.argv, env, worker=args.worker, job_dir=args.job_dir or None)
            sys.stdout.buffer.write(b"".join(item.encode() + b"\0" for item in wrapped))
    except SandboxError as error:
        print(f"sandbox: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
