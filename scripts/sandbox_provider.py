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

    def wrap_worker_cmd(self, cmd: list[str], env: dict) -> tuple[list[str], dict]:
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

    def wrap_worker_cmd(self, cmd, env):
        return list(cmd), dict(env)

    def run_check(self, argv, cwd, env=None):
        return subprocess.run(argv, cwd=str(cwd), env=env, capture_output=True, text=True)

    def evidence(self):
        return {"provider": "host"}


PROVIDERS = {"host": HostProvider}


def provider_for(repo) -> SandboxProvider:
    config = harness.parse_harness(harness.harness_path(Path(repo)))["sandbox"]
    if config["error"]:
        raise SandboxError(config["error"])
    cls = PROVIDERS.get(config["provider"])
    if cls is None:
        raise SandboxError(f"[sandbox] provider {config['provider']!r} is not available in this Rig runtime")
    return cls()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Check the configured Rig sandbox provider")
    parser.add_argument("command", choices=["check", "status"])
    parser.add_argument("--repo", default=".")
    args = parser.parse_args(argv)
    try:
        provider = provider_for(args.repo)
    except SandboxError as error:
        print(f"sandbox: {error}", file=sys.stderr)
        return 1
    if args.command == "status":
        print(json.dumps(provider.status(Path(args.repo)), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
