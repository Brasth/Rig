#!/usr/bin/env python3
"""Opt-in Cua Driver bootstrap for first installation and rig update.

Never fails Rig install. Never writes parent MCP. Never prompts unless a TTY
is available. Piped curl|bash must read the prompt from /dev/tty.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

UPSTREAM = "https://cua.ai/driver/install.sh"
PREFERENCE_NAME = "cua-driver.json"
SCHEMA = 1
INSTALL_TIMEOUT = 120


def rig_home() -> Path:
    raw = (os.environ.get("RIG_HOME") or "").strip()
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".rig"


def preference_path() -> Path:
    return rig_home() / PREFERENCE_NAME


def iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_preference() -> dict | None:
    path = preference_path()
    try:
        data = json.loads(path.read_text())
    except FileNotFoundError:
        return None
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    return data


def write_preference(opt_in: bool, source: str) -> None:
    path = preference_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": SCHEMA,
        "opt_in": bool(opt_in),
        "source": str(source or "prompt"),
        "updated_at": iso_now(),
    }
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n")
    os.chmod(tmp, 0o600)
    tmp.replace(path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def has_tty() -> bool:
    if sys.stdin.isatty():
        return True
    try:
        with open("/dev/tty", "r"):
            return True
    except OSError:
        return False


def parse_args(argv: list[str]) -> dict:
    """Honor --cua-driver / --no-cua-driver. Ignore unknown rig setup flags."""
    force_yes = False
    force_no = False
    for item in argv:
        if item == "--cua-driver":
            force_yes = True
        elif item == "--no-cua-driver":
            force_no = True
    return {"force_yes": force_yes, "force_no": force_no}


def cua_driver_bin() -> str:
    return shutil.which("cua-driver") or ""


def run_upstream() -> bool:
    curl = shutil.which("curl")
    bash = shutil.which("bash")
    if not curl or not bash:
        print("cua-driver setup: curl or bash missing; continuing Rig installation.")
        return False
    command = [bash, "-c", f'{curl} -fsSL {UPSTREAM} | bash']
    print("cua-driver setup: installing from " + UPSTREAM, flush=True)
    try:
        result = subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            timeout=INSTALL_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        print(f"cua-driver setup: {error}")
        return False
    return result.returncode == 0 and bool(cua_driver_bin())


def print_success() -> None:
    binary = cua_driver_bin()
    version = ""
    if binary:
        try:
            result = subprocess.run(
                [binary, "--version"],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=5,
            )
            version = (result.stdout or result.stderr or "").strip().splitlines()[0] if result.returncode == 0 else ""
        except (OSError, subprocess.TimeoutExpired, IndexError):
            version = ""
    if version:
        print("cua-driver setup: " + version)
    else:
        print("cua-driver setup: cua-driver installed")
    print("Linux: install at-spi2-core and libxi6; prefer an Xorg session; run cua-driver serve in the graphical session; cua-driver doctor.")
    print("Do not start systemd and do not grant unrestricted permissions from Rig.")
    print("next: rig computer-use setup  (wires parent MCP after child isolation)")


def prompt_tty() -> bool:
    print("Install Cua Driver for parent computer-use?")
    print("Desktop click/type for this parent only. Not a Rig worker. Default No.")
    print("[y/N]", flush=True)
    try:
        stream = open("/dev/tty", "r")
    except OSError:
        if sys.stdin.isatty():
            stream = sys.stdin
        else:
            return False
    try:
        line = stream.readline()
    finally:
        if stream is not sys.stdin:
            stream.close()
    return line.strip().lower() in {"y", "yes"}


def decide(argv: list[str]) -> tuple[str, bool | None, str]:
    """Return (action, opt_in_to_write, source). action is skip|install|upgrade."""
    flags = parse_args(argv)
    if os.environ.get("RIG_SKIP_CUA_DRIVER") == "1":
        return "skip", None, "env"
    if os.environ.get("RIG_INSTALL_CUA_DRIVER") == "1" or flags["force_yes"]:
        return "install", True, "env" if os.environ.get("RIG_INSTALL_CUA_DRIVER") == "1" else "flag"
    if flags["force_no"]:
        return "skip", False, "flag"
    pref = read_preference()
    if pref is not None and pref.get("opt_in") is True:
        return "upgrade", True, "preference"
    if pref is not None and pref.get("opt_in") is False:
        return "skip", False, "preference"
    if has_tty():
        if prompt_tty():
            return "install", True, "prompt"
        return "skip", False, "prompt"
    return "skip", None, "no-tty"


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    action, opt_in, source = decide(argv)
    if action == "skip":
        if source == "env" and os.environ.get("RIG_SKIP_CUA_DRIVER") == "1":
            print("cua-driver setup: skipped (RIG_SKIP_CUA_DRIVER=1)")
        elif opt_in is False and source == "preference":
            print("Cua Driver skipped (declined). Enable: rig computer-use setup")
        elif opt_in is False:
            print("cua-driver setup: skipped")
            write_preference(False, source)
        else:
            print("cua-driver setup: skipped (no TTY). Enable: RIG_INSTALL_CUA_DRIVER=1 or rig computer-use setup")
        return 0
    if opt_in is True:
        write_preference(True, source)
    if run_upstream():
        print_success()
        return 0
    print("cua-driver setup: installer failed; continuing Rig installation.")
    print("Retry: rig computer-use setup")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
