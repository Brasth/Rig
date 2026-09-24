#!/usr/bin/env python3
"""Opt-in MiMo Code setup for the current Rig repository only."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

INSTALL_URL = "https://mimo.xiaomi.com/install"
INSTALL_TIMEOUT = 180


def _choice(argv: list[str]) -> str:
    choices = {item for item in argv if item in {"--mimo", "--no-mimo"}}
    if len(choices) > 1:
        raise ValueError("use only one of --mimo or --no-mimo")
    return "yes" if "--mimo" in choices else "no" if "--no-mimo" in choices else "ask"


def _confirm() -> bool | None:
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        return None
    print("Set up MiMo Code as an opt-in Rig worker? [y/N] ", end="", flush=True)
    return sys.stdin.readline().strip().lower() in {"y", "yes"}


def _install() -> bool:
    curl, bash = shutil.which("curl"), shutil.which("bash")
    if not curl or not bash:
        print("MiMo setup: curl or bash missing; install from https://github.com/XiaomiMiMo/MiMo-Code")
        return False
    print(f"MiMo setup: installing with the official installer ({INSTALL_URL})", flush=True)
    try:
        result = subprocess.run(
            [bash, "-c", f'{curl} -fsSL "{INSTALL_URL}" | bash'],
            stdin=subprocess.DEVNULL,
            check=False,
            timeout=INSTALL_TIMEOUT,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        print(f"MiMo setup: installer failed: {error}")
        return False
    if result.returncode:
        print(f"MiMo setup: official installer exited {result.returncode}")
        return False
    return True


def _enable_repo(repo: Path, rig_bin: Path) -> None:
    harness = repo / ".rig" / "harness.toml"
    if not harness.is_file():
        print("MiMo installed. Initialize a repo, then enable it with: rig workers mimo=on")
        return
    if not rig_bin.is_file():
        print("MiMo installed, but Rig CLI was not found; enable this repo with: rig workers mimo=on")
        return
    result = subprocess.run([str(rig_bin), "workers", "mimo=on"], cwd=repo, check=False)
    if result.returncode:
        print("MiMo installed, but this repo could not be enabled; retry: rig workers mimo=on")


def main(argv: list[str]) -> int:
    try:
        choice = _choice(argv)
    except ValueError as error:
        print(f"MiMo setup: {error}", file=sys.stderr)
        return 2
    if choice == "no":
        print("MiMo setup: skipped")
        return 0
    if choice == "ask":
        accepted = _confirm()
        if accepted is None:
            print("MiMo setup: skipped (no TTY). Retry: rig setup --mimo")
            return 0
        if not accepted:
            print("MiMo setup: skipped")
            return 0

    if not shutil.which("mimo") and not _install():
        print("Retry after installing MiMo Code: rig setup --mimo")
        return 0
    binary = shutil.which("mimo")
    if not binary:
        print("MiMo setup: installer finished but 'mimo' is not on PATH; retry: rig setup --mimo")
        return 0
    print(f"MiMo setup: CLI ready ({binary})")
    repo = Path(_arg_value(argv, "--repo") or os.getcwd()).expanduser().resolve()
    rig_bin = Path(_arg_value(argv, "--rig-bin") or "").expanduser()
    _enable_repo(repo, rig_bin)
    return 0


def _arg_value(argv: list[str], name: str) -> str:
    try:
        index = argv.index(name)
        return argv[index + 1]
    except (ValueError, IndexError):
        return ""


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
