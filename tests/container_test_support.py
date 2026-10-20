"""Shared fake dc-cli for container-mode tests.

``dc exec --no-start -- CMD...`` runs CMD on the local machine, so the real
supervisor, probe and env wrapper execute for real; only the container boundary
is faked. This proves Rig's behaviour, not dc-cli's or Docker's.
"""
import os
import stat
from pathlib import Path

FAKE_DC = r'''#!/bin/sh
echo "$*" >> "${FAKE_DC_LOG:-/dev/null}"
case "$1" in
  --version) echo "dc 0.23.0" ;;
  exec)
    if [ -n "$FAKE_DC_EXEC_FAIL" ]; then echo "container is not running" >&2; exit 2; fi
    shift
    [ "$1" = "--no-start" ] && shift
    [ "$1" = "--" ] && shift
    exec "$@" ;;
  ls)
    if [ "${FAKE_DC_LS+set}" = set ]; then printf '%s\n' "$FAKE_DC_LS"; else echo '[]'; fi ;;
  forward)
    if [ "${FAKE_DC_FORWARD+set}" = set ]; then printf '%s\n' "$FAKE_DC_FORWARD"; else echo '[]'; fi ;;
  doctor) echo '{"checks": []}' ;;
  *) exit 64 ;;
esac
'''


def install_fake_dc(folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "dc"
    path.write_text(FAKE_DC)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


def fake_agent(folder: Path, name: str, body: str) -> Path:
    """An executable python agent named like a real worker CLI."""
    import sys
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_text(f"#!{sys.executable}\n{body}")
    path.chmod(0o755)
    return path
