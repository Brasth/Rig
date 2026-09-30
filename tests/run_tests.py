#!/usr/bin/env python3
"""Run syntax checks and the offline unittest suite in a disposable environment.

These are accident-prevention guards, not an OS sandbox for untrusted code.
Fixtures may replace PATH/environment deliberately to exercise subprocesses.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
SYSTEM_PATH = os.pathsep.join(("/usr/bin", "/bin", "/usr/sbin", "/sbin"))
PROVIDERS = ("codex", "claude", "grok", "cursor-agent", "agent", "opencode",
             "omp", "pi", "agy", "devin", "mimo", "bsk", "cua-driver")

# Python subprocesses inherit this via PYTHONPATH. Keep localhost/Unix sockets
# working for MCP, cancellation, and UI service tests, but refuse remote DNS/TCP.
PYTHON_GUARD = '''import ipaddress, socket, sys

def _local(host):
    if host in (None, "localhost", b"localhost"):
        return True
    try:
        return ipaddress.ip_address(host.decode() if isinstance(host, bytes) else host).is_loopback
    except ValueError:
        return False

def _offline(event, args):
    if event == "socket.getaddrinfo" and not _local(args[0]):
        raise OSError("Rig tests prohibit external DNS/network access")
    if event in ("socket.connect", "socket.sendto"):
        sock, address = args[0], args[-1]
        if sock.family != socket.AF_UNIX and not _local(address[0]):
            raise OSError("Rig tests prohibit external network access")

sys.addaudithook(_offline)
'''

NODE_GUARD = r'''const net = require('node:net');
const dns = require('node:dns');
const dgram = require('node:dgram');
const local = host => host == null || host === 'localhost' || host === '::1' ||
  (net.isIP(host) === 4 && host.startsWith('127.'));
const denied = () => { throw new Error('Rig tests prohibit external network access'); };
const connect = net.Socket.prototype.connect;
net.Socket.prototype.connect = function (...args) {
  const options = Array.isArray(args[0]) ? args[0][0] :
    (typeof args[0] === 'object' ? args[0] :
      (typeof args[0] === 'string' && !/^\d+$/.test(args[0]) ? {path: args[0]} :
        {host: typeof args[1] === 'string' ? args[1] : undefined}));
  if (!options.path && !local(options.host)) denied();
  return connect.apply(this, args);
};
const lookup = dns.lookup;
dns.lookup = function (host, ...args) {
  if (!local(host)) denied();
  return lookup.call(this, host, ...args);
};
dgram.Socket.prototype.send = denied;
'''


def isolated_environment(base: Path) -> dict[str, str]:
    """Build from scratch: never forward agent context, tokens or shell hooks."""
    home, kit, tools, guards, scratch = [base / name for name in
                                      ("home", "rig", "bin", "guards", "tmp")]
    for folder in (home, kit, tools, guards, scratch):
        folder.mkdir()
    for name in PROVIDERS:
        if shutil.which(name, path=SYSTEM_PATH):
            raise RuntimeError(f"{name} is installed on the system PATH; use a clean test machine")
    executables = {"python": sys.executable, "python3": sys.executable}
    for name in ("node", "git"):
        binary = shutil.which(name)
        if not binary:
            raise RuntimeError(f"{name} is required (CI uses Python 3.12 and Node.js 22)")
        executables[name] = binary
    # Optional real tmux integration tests remain enabled when it is installed.
    if binary := shutil.which("tmux"):
        executables["tmux"] = binary
    for name, binary in executables.items():
        (tools / name).symlink_to(Path(binary).resolve())
    # Test-specific fake commands can precede these on PATH. Ordinary installer,
    # download and remote-shell commands must not escape into the host system.
    for name in ("wget", "npm", "npx", "pnpm", "yarn", "brew", "ssh", "scp"):
        blocked = tools / name
        blocked.write_text('#!/bin/sh\necho "Rig tests blocked external command: $0" >&2\nexit 97\n')
        blocked.chmod(0o755)
    # Rig update's fixture transport is exactly: curl -fsSL file:///... -o PATH.
    # Copy bytes ourselves rather than forwarding arbitrary curl options/config.
    curl = tools / "curl"
    curl.write_text("#!/usr/bin/env python3\n" + r'''import pathlib, sys, urllib.parse
args = sys.argv[1:]
if len(args) != 4 or args[0] != '-fsSL' or args[2] != '-o':
    sys.exit('Rig tests blocked unsupported curl invocation')
url = urllib.parse.urlsplit(args[1])
if url.scheme != 'file' or url.netloc not in ('', 'localhost'):
    sys.exit('Rig tests blocked external curl URL')
try:
    data = pathlib.Path(urllib.parse.unquote(url.path)).read_bytes()
    pathlib.Path(args[3]).write_bytes(data)
except OSError as error:
    sys.exit(str(error))
''')
    curl.chmod(0o755)
    (guards / "sitecustomize.py").write_text(PYTHON_GUARD)
    (guards / "offline.cjs").write_text(NODE_GUARD)
    return {
        "HOME": str(home), "RIG_HOME": str(kit), "RIG_SRC": str(ROOT),
        "PATH": str(tools) + os.pathsep + SYSTEM_PATH,
        "TMPDIR": str(scratch), "TMP": str(scratch), "TEMP": str(scratch),
        "XDG_CONFIG_HOME": str(home / ".config"),
        "XDG_CACHE_HOME": str(home / ".cache"),
        "XDG_DATA_HOME": str(home / ".local/share"),
        "LANG": "C", "LC_ALL": "C", "TZ": "UTC", "TERM": "xterm-256color",
        "PYTHONUTF8": "1", "PYTHONUNBUFFERED": "1", "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1", "PYTHONPATH": str(guards),
        "NODE_OPTIONS": '--require="' + str(guards / "offline.cjs") + '"',
        "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_TERMINAL_PROMPT": "0", "GIT_ALLOW_PROTOCOL": "file",
        "RIG_SKIP_UPDATE_CHECK": "1", "RIG_SKIP_MODEL_CATALOG": "1",
        "RIG_SKIP_TMUX_INSTALL": "1", "RIG_SKIP_BROWSER_SKILL": "1",
        "RIG_SKIP_CUA_DRIVER": "1",
    }


def checks(pattern: str, syntax_only: bool) -> int:
    print(f"Python: {sys.version.split()[0]}")
    for command in (["node", "--version"], ["git", "--version"], ["bash", "--version"]):
        subprocess.run(command, check=True)
    files = subprocess.check_output(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=ROOT,
    ).decode().split("\0")
    python_count = shell_count = 0
    for name in sorted(set(files)):
        path = ROOT / name
        if not path.is_file():
            continue
        if path.suffix == ".py":
            compile(path.read_bytes(), str(path), "exec")
            python_count += 1
        elif path.suffix == ".sh" or name == "bin/rig":
            subprocess.run(["bash", "-n", str(path)], check=True)
            shell_count += 1
    print(f"Syntax passed: {python_count} Python files, {shell_count} shell files", flush=True)
    if syntax_only:
        return 0
    # Standard discovery includes unit, stub integration, and bundled Node tests.
    suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"), pattern=pattern)
    if not suite.countTestCases():
        raise RuntimeError(f"No tests matched {pattern!r}")
    return 0 if unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful() else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pattern", default="test_*.py", help="unittest discovery filename pattern")
    parser.add_argument("--syntax-only", action="store_true")
    parser.add_argument("--timeout", type=float, default=900, help="overall timeout in seconds (default: 900)")
    parser.add_argument("--log-dir", type=Path, help="save test.log here (default: a new temporary directory)")
    parser.add_argument("--_child", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args._child:
        return checks(args.pattern, args.syntax_only)
    if sys.version_info < (3, 11) or os.name != "posix":
        parser.error("Python 3.11+ on Linux or macOS is required; CI uses Python 3.12")
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    logs = (args.log_dir or Path(tempfile.mkdtemp(prefix="rig-test-logs-"))).resolve()
    logs.mkdir(parents=True, exist_ok=True)
    print(f"Test log: {logs / 'test.log'}", flush=True)
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="rig-tests-", dir="/tmp") as directory, \
            (logs / "test.log").open("w", encoding="utf-8") as log:
        try:
            env = isolated_environment(Path(directory))
        except RuntimeError as error:
            print(error, file=log)
            print(error, file=sys.stderr)
            return 2
        command = [sys.executable, str(Path(__file__).resolve()), "--_child", "--pattern", args.pattern]
        if args.syntax_only:
            command.append("--syntax-only")
        process = subprocess.Popen(command, cwd=ROOT, env=env, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                                   errors="replace", start_new_session=True)
        def relay():
            for line in process.stdout:
                log.write(line)
                log.flush()
                print(line, end="", flush=True)
        reader = threading.Thread(target=relay, daemon=True)
        reader.start()
        try:
            result = process.wait(timeout=args.timeout)
        except (subprocess.TimeoutExpired, KeyboardInterrupt) as error:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
            result = 124 if isinstance(error, subprocess.TimeoutExpired) else 130
        reader.join(timeout=5)
        summary = f"\nRunner exit {result}; elapsed {time.monotonic() - started:.1f}s\n"
        log.write(summary)
        print(summary, end="")
        return result


if __name__ == "__main__":
    raise SystemExit(main())
