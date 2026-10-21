#!/usr/bin/env python3
"""Getting rig-shell onto a machine without making the user build it.

The Go shell is shipped prebuilt, for Linux and macOS on x86-64 and arm64, in ``prebuilt/`` of the source tree (built
by ``python3 scripts/shell_dist.py build``, stamped with a hash of the shell's own sources). Setup and ``rig update``
copy the right one into RIG_HOME/bin; ``rig shell`` fetches it from the repository when it is missing. A Go toolchain
is only a way to get a binary newer than the prebuilt one, never a requirement.
"""
from __future__ import annotations

import hashlib
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

REPO_SLUG = "Brasth/Rig"
DEFAULT_BASE = f"https://raw.githubusercontent.com/{REPO_SLUG}"
TARGETS = (("linux", "amd64"), ("linux", "arm64"), ("darwin", "amd64"), ("darwin", "arm64"))
MAX_BYTES = 40 << 20


def rig_home() -> Path:
    return Path(os.environ.get("RIG_HOME") or Path.home() / ".rig")


def platform_target() -> str:
    """This machine as a prebuilt's name suffix ("linux-amd64"), or "" when none is built for it."""
    system = platform.system().lower()
    machine = platform.machine().lower()
    arch = {"x86_64": "amd64", "amd64": "amd64", "arm64": "arm64", "aarch64": "arm64"}.get(machine, "")
    return f"{system}-{arch}" if (system, arch) in TARGETS else ""


def asset_name(target: str = "") -> str:
    target = target or platform_target()
    return f"rig-shell-{target}" if target else ""


def source_stamp(src: Path) -> str:
    """A hash of what goes into the binary: the Go sources, go.mod and go.sum of shell/, not its tests."""
    module = Path(src) / "shell"
    digest = hashlib.sha256()
    for path in sorted(module.rglob("*")):
        rel = path.relative_to(module)
        if (not path.is_file() or rel.parts[0] in {"bin", "testdata"} or path.name.endswith("_test.go")
                or (path.suffix != ".go" and path.name not in {"go.mod", "go.sum"})):
            continue
        digest.update(rel.as_posix().encode() + b"\0" + path.read_bytes() + b"\0")
    return digest.hexdigest()[:12]


def version_string(stamp: str) -> str:
    return "src-" + stamp


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_sums(text: str) -> dict[str, str]:
    sums = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 2 and len(parts[0]) == 64:
            sums[parts[1].lstrip("*")] = parts[0]
    return sums


def read_stamp(folder: Path) -> str:
    try:
        return (Path(folder) / "STAMP").read_text().strip()
    except OSError:
        return ""


def build_all(src: Path, out: Path | None = None, *, log=print) -> tuple[bool, str]:
    """Cross-compile the shell for every target into prebuilt/ with SHA256SUMS and STAMP (needs Go 1.24+)."""
    src = Path(src)
    go = shutil.which("go")
    module = src / "shell"
    if not go or not (module / "go.mod").is_file():
        return False, "building the prebuilt shells needs Go 1.24+ and a Rig source tree"
    out = Path(out) if out else src / "prebuilt"
    out.mkdir(parents=True, exist_ok=True)
    stamp = source_stamp(src)
    sums = []
    for system, arch in TARGETS:
        name = asset_name(f"{system}-{arch}")
        target = out / name
        log(f"building {name}")
        done = subprocess.run([go, "build", "-trimpath", "-ldflags", f"-s -w -X main.Version={version_string(stamp)}",
                               "-o", str(target), "./cmd/rig-shell"], cwd=str(module), capture_output=True, text=True, timeout=600,
                              env={**os.environ, "GOOS": system, "GOARCH": arch, "CGO_ENABLED": "0", "GOFLAGS": "-mod=readonly"})
        if done.returncode != 0:
            return False, f"{name}: " + " | ".join((done.stderr or done.stdout).strip().splitlines()[-3:])
        os.chmod(target, 0o755)
        sums.append(f"{sha256_of(target)}  {name}")
    (out / "SHA256SUMS").write_text("\n".join(sums) + "\n")
    (out / "STAMP").write_text(stamp + "\n")
    return True, f"built {len(sums)} shells in {out} (sources {stamp})"


def working(path: Path) -> str:
    """The version a binary reports, or "" when it does not run."""
    try:
        done = subprocess.run([str(path), "--version"], capture_output=True, text=True, timeout=10, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    parts = done.stdout.split()
    return parts[1] if done.returncode == 0 and len(parts) > 1 and parts[0] == "rig-shell" else ""


def install_file(data: bytes, home: Path) -> tuple[bool, str]:
    """Put a binary at RIG_HOME/bin/rig-shell if it runs; the previous one stays when it does not."""
    target_dir = Path(home) / "bin"
    target_dir.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".rig-shell-", dir=target_dir)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.chmod(temporary, 0o755)
        version = working(Path(temporary))
        if not version:
            return False, "the prebuilt rig-shell does not run on this machine"
        os.replace(temporary, target_dir / "rig-shell")
        return True, version
    except OSError as error:
        return False, f"could not install rig-shell: {error}"
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def from_tree(src: Path, home: Path) -> tuple[bool, str]:
    """Install this platform's prebuilt from a source tree's prebuilt/ folder, checked against its SHA256SUMS."""
    name = asset_name()
    folder = Path(src) / "prebuilt"
    if not name:
        return False, f"no prebuilt rig-shell for {platform.system()} {platform.machine()}"
    binary = folder / name
    if not binary.is_file():
        return False, f"no prebuilt rig-shell in {folder}"
    try:
        sums = read_sums((folder / "SHA256SUMS").read_text())
        data = binary.read_bytes()
    except OSError as error:
        return False, f"could not read the prebuilt rig-shell: {error}"
    if sums.get(name) != hashlib.sha256(data).hexdigest():
        return False, f"{name} does not match SHA256SUMS; not installed"
    ok, note = install_file(data, home)
    return ok, (f"installed the prebuilt {Path(home) / 'bin' / 'rig-shell'} ({note})" if ok else note)


def fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "rig-shell-setup"})
    with urllib.request.urlopen(request, timeout=30) as response:
        data = response.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError("download too large")
    return data


def downloads_allowed() -> bool:
    return os.environ.get("RIG_SHELL_NO_DOWNLOAD") != "1"


def core_ref(home: Path) -> str:
    """The commit the installed runtime came from (RIG_HOME/VERSION is "v1 <commit>"), else main."""
    try:
        parts = (Path(home) / "VERSION").read_text().split()
        if len(parts) > 1 and len(parts[1]) >= 7:
            return parts[1]
    except OSError:
        pass
    return "main"


def download(home: Path, ref: str = "") -> tuple[bool, str]:
    """Fetch this platform's prebuilt from the repository at the installed commit, checked against its SHA256SUMS."""
    name = asset_name()
    if not name:
        return False, f"no prebuilt rig-shell for {platform.system()} {platform.machine()}"
    if not downloads_allowed():
        return False, "downloads are off (RIG_SHELL_NO_DOWNLOAD=1)"
    base = os.environ.get("RIG_SHELL_DIST_URL", DEFAULT_BASE).rstrip("/")
    refs = [ref or core_ref(home)]
    if refs[0] != "main":
        refs.append("main")  # a commit that predates the prebuilt folder: the newest one
    last = ""
    for each in refs:
        try:
            sums = read_sums(fetch(f"{base}/{each}/prebuilt/SHA256SUMS").decode("utf-8", "replace"))
            data = fetch(f"{base}/{each}/prebuilt/{name}")
        except (OSError, ValueError) as error:
            last = f"could not download the prebuilt rig-shell ({error})"
            continue
        if sums.get(name) != hashlib.sha256(data).hexdigest():
            last = f"the downloaded {name} does not match SHA256SUMS; not installed"
            continue
        ok, note = install_file(data, home)
        return ok, (f"downloaded the prebuilt {Path(home) / 'bin' / 'rig-shell'} ({note})" if ok else note)
    return False, last


def main(argv=None) -> int:
    import argparse
    parser = argparse.ArgumentParser(description="Build the prebuilt rig-shell binaries (needs Go) or install one")
    parser.add_argument("action", choices=["build", "install", "download"])
    parser.add_argument("--src", default=str(Path(__file__).resolve().parent.parent))
    parser.add_argument("--out", default="")
    args = parser.parse_args(argv)
    if args.action == "build":
        ok, message = build_all(Path(args.src), Path(args.out) if args.out else None)
    elif args.action == "install":
        ok, message = from_tree(Path(args.src), rig_home())
    else:
        ok, message = download(rig_home())
    print(message)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
