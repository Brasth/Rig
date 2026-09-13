"""Private atomic UI storage; these records never establish execution ownership."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path


def private_directory(path):
    path = Path(path)
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.is_symlink() or path.stat().st_uid != os.getuid():
        raise ValueError("UI storage must be a private directory owned by this user")
    path.chmod(0o700)
    return path


def read(path, default=None):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return default


def write(path, value):
    path = Path(path)
    private_directory(path.parent)
    fd, name = tempfile.mkstemp(prefix=".ui-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, ensure_ascii=False, default=str)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def token(value):
    return hashlib.sha256(str(value).encode()).hexdigest()[:32]


def runtime_directory(repo):
    # Unix socket paths have a small platform limit, independent of repo depth.
    # macOS TMPDIR alone can consume most of sockaddr_un.sun_path (104 bytes).
    # /tmp is deliberately used instead of TMPDIR for these private sockets.
    root = private_directory(Path("/tmp") / f"rig-ui-{os.getuid()}")
    return private_directory(root / token(Path(repo).resolve()))


def clean_text(value, limit=160):
    """Render user/worker text literally; never allow terminal control sequences."""
    import re
    text = str(value or "")
    text = re.sub(r"\x1b\][^\x07]*(?:\x07|\x1b\\)", "", text)
    text = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", text)
    text = " ".join("".join(c for c in text if c.isprintable() or c.isspace()).split())
    return text if len(text) <= limit else text[:max(0, limit - 1)] + "…"
