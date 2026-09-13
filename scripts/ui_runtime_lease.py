"""Protect an installed runtime while a long-lived MCP or wrapper uses it."""
from contextlib import contextmanager
import os
from pathlib import Path
import uuid


@contextmanager
def lease(kind, repo=None):
    root = Path(os.environ.get("RIG_HOME") or Path(__file__).resolve().parents[1])
    if not (root / "install-manifest.json").is_file():
        yield
        return
    import admission
    from ui_install import lifecycle_lock, register_lease, remove_lease
    name = f"{kind}-{os.getpid()}-{uuid.uuid4().hex}"
    with lifecycle_lock(root):
        register_lease(name, repo or os.getcwd(), os.getpid(),
                       admission.process_identity(os.getpid()).get("start_id"), root)
    try:
        yield
    finally:
        remove_lease(name, root)
