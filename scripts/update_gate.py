"""Small, non-migrating admission barrier for journaled runtime updates.

Lock order: update gate (shared for admissions, exclusive for updates), then
repository admission locks OR the lifecycle lock. Never acquire the update
gate while holding either of those locks. Legacy runtimes without a gate keep
working; a pending marker always fails closed.
"""
from contextlib import contextmanager
import fcntl
import os
from pathlib import Path
import stat
import time


class UpdateBlocked(ValueError):
    pass


def root_path(root=None):
    return Path(root or os.environ.get('RIG_HOME') or Path.home() / '.rig').absolute()


def pending(root=None):
    path = root_path(root) / 'updates/pending.json'
    return path.exists() or path.is_symlink()


def epoch(root):
    path = root / 'updates/epoch'
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return b''


_LOADED_ROOT = root_path()
_LOADED_EPOCH = epoch(_LOADED_ROOT)
_LOADED_PENDING = pending(_LOADED_ROOT)


def assert_ready(root=None, *, freshness=True):
    root = root_path(root)
    if pending(root):
        raise UpdateBlocked('Runtime update interrupted or in progress; run rig update --status, then rig update --recover before new work')
    if freshness and root == _LOADED_ROOT and (_LOADED_PENDING or epoch(root) != _LOADED_EPOCH):
        raise UpdateBlocked('Runtime changed since this process started; fully restart the parent/MCP before new work')


def safe_parents(path):
    """Never replace through a user-owned directory symlink, even into Rig."""
    for parent in path.absolute().parents:
        if parent.is_symlink():
            raise UpdateBlocked('Symlink parent is not update-owned: ' + str(parent))


@contextmanager
def lock(root=None, *, exclusive=False, timeout=5):
    root = root_path(root)
    path = root / '.update.lock'
    if not path.exists() and not path.is_symlink():
        if exclusive:
            raise UpdateBlocked('Legacy/unversioned installation: no safe-update baseline; see docs/safe-updates.md')
        assert_ready(root)
        yield
        return
    # A legacy runtime with no gate keeps its existing path semantics. Once
    # a gate exists, its ownership and every parent must be safe.
    safe_parents(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise UpdateBlocked('Unsafe update lock ownership or permissions')
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(fd, (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise UpdateBlocked('Runtime update/admission busy; retry after it finishes')
                time.sleep(.025)
        if not exclusive:
            assert_ready(root)
        yield
    finally:
        os.close(fd)
