#!/usr/bin/env python3
"""Opt-in BrowserSkill bootstrap for first installation and rig update.

Never fails Rig install. Never writes parent MCP. Never prompts unless a TTY
is available. Piped curl|bash must read the prompt from /dev/tty.
Never runs `bsk install-skill`. Never flips the repo [browser-skill] flag.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

UPSTREAM = "https://raw.githubusercontent.com/Tencent/BrowserSkill/main/install.sh"
CHROME_STORE = "https://chromewebstore.google.com/detail/hhcmgoofomhgciiibhipgmgkgnoenaoi"
EDGE_STORE = "https://microsoftedge.microsoft.com/addons/detail/browserskill/emacgiaaaiojkkpkddmmdfhmokgmnikg"
PREFERENCE_NAME = "browser-skill.json"
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
    """Honor --browser-skill / --no-browser-skill. Ignore unknown rig setup flags."""
    force_yes = False
    force_no = False
    for item in argv:
        if item == "--browser-skill":
            force_yes = True
        elif item == "--no-browser-skill":
            force_no = True
    return {"force_yes": force_yes, "force_no": force_no}


def bsk_bin() -> str:
    return shutil.which("bsk") or ""


def run_upstream() -> bool:
    curl = shutil.which("curl")
    bash = shutil.which("bash")
    if not curl or not bash:
        print("browser-skill setup: curl or bash missing; continuing Rig installation.")
        return False
    command = [bash, "-c", f"{curl} -fsSL {UPSTREAM} | bash"]
    print("browser-skill setup: installing from " + UPSTREAM, flush=True)
    try:
        result = subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            timeout=INSTALL_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        print(f"browser-skill setup: {error}")
        return False
    return result.returncode == 0 and bool(bsk_bin())


def print_extension_urls() -> None:
    print("Install the Chrome extension: " + CHROME_STORE)
    print("Or Edge: " + EDGE_STORE)
    print("Leave Confirm before borrowing tabs ON.")
    print("Never run bsk install-skill. Parent only. Not a Rig worker.")


def print_success() -> None:
    binary = bsk_bin()
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
        print("browser-skill setup: " + version)
    else:
        print("browser-skill setup: bsk installed")
    print_extension_urls()
    print("next: rig browser-skill setup  (does not enable the repo flag)")


def prompt_tty() -> bool:
    print("Install BrowserSkill for parent logged-in browser?")
    print("Installs the bsk CLI. You must install the Chrome/Edge extension yourself.")
    print("Parent only. Not a Rig worker. Default No.")
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
    if os.environ.get("RIG_SKIP_BROWSER_SKILL") == "1":
        return "skip", None, "env"
    if os.environ.get("RIG_INSTALL_BROWSER_SKILL") == "1" or flags["force_yes"]:
        return "install", True, "env" if os.environ.get("RIG_INSTALL_BROWSER_SKILL") == "1" else "flag"
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
        if source == "env" and os.environ.get("RIG_SKIP_BROWSER_SKILL") == "1":
            print("browser-skill setup: skipped (RIG_SKIP_BROWSER_SKILL=1)")
        elif opt_in is False and source == "preference":
            print("BrowserSkill skipped (declined). Enable: rig browser-skill setup")
        elif opt_in is False:
            print("browser-skill setup: skipped")
            write_preference(False, source)
        else:
            print("browser-skill setup: skipped (no TTY). Enable: RIG_INSTALL_BROWSER_SKILL=1 or rig browser-skill setup")
        return 0
    if opt_in is True:
        write_preference(True, source)
    if run_upstream():
        print_success()
        return 0
    print("browser-skill setup: installer failed; continuing Rig installation.")
    print("Retry: rig browser-skill setup")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
