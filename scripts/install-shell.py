#!/usr/bin/env python3
"""Build the optional rig-shell binary from a Rig source tree into RIG_HOME/bin.

Best effort, like the other optional components: a missing Go toolchain or a
failed build never fails setup, and a previous working binary is kept when a new
build fails. The binary is stamped with the source commit so rig doctor can tell
when it no longer matches the installed core.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent


def rig_home() -> Path:
    return Path(os.environ.get("RIG_HOME") or Path.home() / ".rig")


def source_commit(src: Path) -> str:
    try:
        out = subprocess.run(["git", "-C", str(src), "rev-parse", "HEAD"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return "dev"
    value = out.stdout.strip()
    return value if out.returncode == 0 and len(value) >= 7 else "dev"


def build(src: Path, home: Path, *, timeout: float = 600) -> tuple[bool, str]:
    go = shutil.which("go")
    module = src / "shell"
    if not (module / "go.mod").is_file():
        return False, f"shell source not found at {module}; set RIG_SRC to a Rig clone"
    if not go:
        return False, "Go toolchain not found (need Go 1.24+); the shell is optional. Install Go, then run: rig shell build"
    target_dir = home / "bin"
    target_dir.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".rig-shell-", dir=target_dir)
    os.close(fd)
    commit = source_commit(src)
    try:
        done = subprocess.run([go, "build", "-trimpath", "-ldflags", f"-s -w -X main.Version={commit}",
                               "-o", temporary, "./cmd/rig-shell"], cwd=str(module), capture_output=True, text=True,
                              timeout=timeout, env={**os.environ, "GOFLAGS": "-mod=readonly"})
        if done.returncode != 0:
            tail = (done.stderr or done.stdout).strip().splitlines()[-3:]
            return False, "build failed; previous rig-shell kept: " + " | ".join(tail)
        os.chmod(temporary, 0o755)
        os.replace(temporary, target_dir / "rig-shell")
        return True, f"built {target_dir / 'rig-shell'} ({commit[:12]})"
    except subprocess.TimeoutExpired:
        return False, f"build timed out after {timeout:.0f}s; previous rig-shell kept"
    except OSError as error:
        return False, f"build could not run: {error}"
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Build rig-shell into RIG_HOME/bin")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--shell", action="store_true", help="build and report failure with a non-zero exit")
    group.add_argument("--no-shell", action="store_true", help="skip")
    parser.add_argument("--src", default=os.environ.get("RIG_SRC") or str(HERE.parent))
    args = parser.parse_args(argv)
    if args.no_shell:
        print("rig-shell setup: skipped. Enable later: rig shell build")
        return 0
    if os.environ.get("RIG_SKIP_SHELL_BUILD") == "1" and not args.shell:
        print("rig-shell setup: skipped (RIG_SKIP_SHELL_BUILD=1)")
        return 0
    ok, message = build(Path(args.src), rig_home())
    print(("rig-shell setup: " if ok else "rig-shell setup: skipped, ") + message)
    # Setup treats this component as optional; an explicit --shell is a request.
    return 0 if ok or not args.shell else 1


if __name__ == "__main__":
    raise SystemExit(main())
