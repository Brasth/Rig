#!/usr/bin/env python3
"""Best-effort tmux bootstrap for first installation and rig update."""
import os
import platform
import re
import shutil
import signal
import subprocess


def compatible():
    binary = shutil.which("tmux")
    if not binary:
        return False
    try:
        result = subprocess.run([binary, "-V"], stdin=subprocess.DEVNULL,
                                capture_output=True, text=True, timeout=5)
        version = re.search(r"tmux (\d+)\.(\d+)", result.stdout)
        return bool(result.returncode == 0 and version and
                    tuple(map(int, version.groups())) >= (3, 3))
    except (OSError, subprocess.TimeoutExpired):
        return False


def run(command, env):
    print("tmux setup: " + " ".join(command), flush=True)
    with subprocess.Popen(command, stdin=subprocess.DEVNULL, env=env,
                          start_new_session=True) as process:
        try:
            return process.wait(timeout=300) == 0
        except subprocess.TimeoutExpired:
            print("tmux setup: package operation timed out", flush=True)
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
            # A package-manager child can outlive its direct parent.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
            return False


def commands():
    system = platform.system()
    if system == "Darwin" and shutil.which("brew"):
        action = "upgrade" if shutil.which("tmux") else "install"
        return [[shutil.which("brew"), action, "tmux"]]
    if system != "Linux":
        return []
    prefix = []
    if os.geteuid() != 0:
        sudo = shutil.which("sudo")
        if not sudo:
            return []
        result = subprocess.run([sudo, "-n", "true"], stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
        if result.returncode:
            return []
        prefix = [sudo, "-n"]
    if shutil.which("apt-get"):
        apt = shutil.which("apt-get")
        if prefix:
            prefix += ["env", "DEBIAN_FRONTEND=noninteractive"]
        return [prefix + [apt, "update"],
                prefix + [apt, "install", "-y", "tmux"]]
    if shutil.which("dnf"):
        action = "upgrade" if shutil.which("tmux") else "install"
        return [prefix + [shutil.which("dnf"), action, "-y", "tmux"]]
    return []


def main():
    if os.environ.get("RIG_SKIP_TMUX_INSTALL") == "1":
        print("tmux setup: skipped (RIG_SKIP_TMUX_INSTALL=1)")
        return 0
    if compatible():
        print("tmux setup: compatible tmux already available")
        return 0
    env = dict(os.environ, NONINTERACTIVE="1", HOMEBREW_NO_AUTO_UPDATE="1",
               DEBIAN_FRONTEND="noninteractive")
    try:
        operations = commands()
        if operations and all(run(command, env) for command in operations) and compatible():
            print("tmux setup: tmux 3.3+ ready; enable the companion with rig ui enable")
            return 0
    except (OSError, subprocess.SubprocessError) as error:
        print(f"tmux setup: {error}")
    print("tmux setup: tmux 3.3+ unavailable; continuing Rig installation.")
    print("Install manually: macOS: brew install tmux; Debian/Ubuntu: sudo apt-get install tmux; Fedora: sudo dnf install tmux.")
    print("Check tmux -V; your repository may require a newer package source. Then run rig ui enable.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
