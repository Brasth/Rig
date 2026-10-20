#!/usr/bin/env python3
"""The only place Rig talks to dc-cli. Rig never calls docker itself.

CONTRACT STATUS: checked against dc-cli 0.23.0 (outputs in tests/fixtures/dc/).
What Rig relies on:
  * ``dc --version`` prints a semantic version.
  * ``dc up`` / ``dc down`` signal success with exit status 0; ``dc up`` refuses a folder with no
    devcontainer or compose file.
  * ``dc exec --no-start WORKSPACE -- CMD...`` runs CMD in the existing container and passes the exit code through.
  * ``dc try --print`` prints ``override:  PATH`` naming the JSON config it generated.
  * ``dc ls --json`` and ``dc doctor --json`` print JSON; ``dc forward --status`` prints text only.
Every field read from dc JSON goes through ``pick`` so a renamed key degrades to
"unknown" instead of a crash. When a dc-cli release adds schemaVersion, check it
here and refuse unknown majors.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent


class DcError(RuntimeError):
    """dc-cli is missing, failed, or printed something Rig cannot use."""


def dc_binary() -> str:
    """RIG_DC_BIN when it names a real executable, else dc on PATH. A bad override is "not found"
    rather than a silent fall back to some other dc."""
    override = os.environ.get("RIG_DC_BIN")
    if override:
        return override if os.path.isfile(override) and os.access(override, os.X_OK) else ""
    return shutil.which("dc") or ""


def require() -> str:
    binary = dc_binary()
    if not binary:
        raise DcError("dc-cli is not installed (no `dc` on PATH); install it, then retry")
    return binary


def _template_path() -> Path:
    installed = Path(os.environ.get("RIG_HOME") or Path.home() / ".rig") / "templates" / "dc-cli.json"
    return installed if installed.is_file() else ROOT / "templates" / "dc-cli.json"


def pinned_version() -> str:
    try:
        value = json.loads(_template_path().read_text()).get("version", "")
    except (OSError, ValueError):
        return ""
    return value.strip() if isinstance(value, str) else ""


_VERSION = re.compile(r"\bv?(\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.\-]+)?)\b")


def version(binary: str | None = None) -> str:
    binary = binary or dc_binary()
    if not binary:
        return ""
    try:
        done = subprocess.run([binary, "--version"], capture_output=True, text=True, timeout=10,
                              stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    match = _VERSION.search(done.stdout + done.stderr)
    return match.group(1) if match else ""


def pin_state(found: str, pin: str) -> str:
    """unpinned | match | mismatch | missing (dc not installed or unreadable version)."""
    if not found:
        return "missing"
    if not pin:
        return "unpinned"
    return "match" if found.lstrip("v") == pin.lstrip("v") else "mismatch"


def run(args: list[str], cwd: Path, *, timeout: float = 120, stream: bool = False) -> subprocess.CompletedProcess:
    binary = require()
    try:
        if stream:  # the user watches pulls and builds; nothing is captured
            return subprocess.run([binary, *args], cwd=str(cwd), timeout=timeout, stdin=subprocess.DEVNULL)
        return subprocess.run([binary, *args], cwd=str(cwd), capture_output=True, text=True,
                              timeout=timeout, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired as error:
        raise DcError(f"dc {' '.join(args)} timed out after {timeout:.0f}s") from error
    except OSError as error:
        raise DcError(f"could not run dc: {error}") from error


def run_text(args: list[str], cwd: Path, *, timeout: float = 30) -> str:
    done = run(args, cwd, timeout=timeout)
    if done.returncode != 0:
        detail = (done.stderr or done.stdout).strip().splitlines()
        raise DcError(f"dc {' '.join(args)} failed ({done.returncode}): {detail[-1] if detail else 'no output'}")
    return done.stdout


def run_json(args: list[str], cwd: Path, *, timeout: float = 30):
    done = run(args, cwd, timeout=timeout)
    if done.returncode != 0:
        detail = (done.stderr or done.stdout).strip().splitlines()
        raise DcError(f"dc {' '.join(args)} failed ({done.returncode}): {detail[-1] if detail else 'no output'}")
    try:
        return json.loads(done.stdout)
    except ValueError as error:
        raise DcError(f"dc {' '.join(args)} did not print JSON") from error


def pick(data, *paths, default=None):
    """First present value among dotted paths in nested dicts; tolerant of any shape."""
    for path in paths:
        node = data
        for key in path.split("."):
            if not isinstance(node, dict) or key not in node:
                break
            node = node[key]
        else:
            return node
    return default
