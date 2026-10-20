#!/usr/bin/env python3
"""The devcontainer sandbox provider: run workers and checks inside the workspace container.

Invariant: every absolute path Rig puts in a command (repository, job directory,
the Rig runtime, the home directory) exists at the SAME path inside the container.
Rig does not translate paths; it probes that the invariant holds before each launch
and refuses with a specific reason when it does not.

Only ``dc exec --no-start`` is used to run anything, so a job can never start a
container; ``rig env up`` is the only way one starts. Rig never calls docker.

Stopping is the hard part: killing the host-side ``dc exec`` client does not
stop a process inside the container. A small supervisor script runs the command
inside the container and kills its whole process group when a stop file appears
or when the host heartbeat stops changing for a lease period, so an agent cannot
outlive the host process that owns its files for longer than the lease.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path

import container_modes
import dc_client
from sandbox_provider import SandboxError, SandboxProvider

HERE = Path(__file__).resolve().parent
RUNTIME = HERE.parent  # RIG_HOME when installed, the checkout when run from one
LEASE_SECONDS = 15
STOP_FILE = "container-stop"
HEARTBEAT_FILE = "container-heartbeat"
ENV_KEYS = ("RIG_JOB_ID", "RIG_JOB_DIR", "RIG_REPO", "RIG_ROLE")

# argv: cwd stop-file heartbeat-file lease command...
# The heartbeat is compared by content, not by time, so clock drift between a
# VM-backed engine and the host cannot expire a live job.
SUPERVISOR = r'''cwd=$1; stopf=$2; hbf=$3; lease=$4; shift 4
cd "$cwd" || exit 125
setsid "$@" &
pid=$!
last=""; stale=0
stop_group() {
  kill -TERM "-$pid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null
  n=0
  while kill -0 "$pid" 2>/dev/null && [ "$n" -lt 3 ]; do sleep 1; n=$((n+1)); done
  kill -KILL "-$pid" 2>/dev/null || kill -KILL "$pid" 2>/dev/null
}
trap 'stop_group' TERM INT HUP
while kill -0 "$pid" 2>/dev/null; do
  if [ -e "$stopf" ]; then stop_group; break; fi
  cur=$(cat "$hbf" 2>/dev/null)
  if [ "$cur" = "$last" ]; then stale=$((stale+1)); else stale=0; last=$cur; fi
  if [ "$stale" -ge "$lease" ]; then stop_group; break; fi
  sleep 1
done
wait "$pid"
exit $?
'''

# argv: repo runtime python binary [credential paths...]; exit codes name the failure.
PROBE = r'''repo=$1; runtime=$2; py=$3; bin=$4; shift 4
test -d "$repo/.rig" || exit 11
test -r "$runtime/scripts/rig-mcp.sh" || exit 12
command -v "$py" >/dev/null 2>&1 || exit 13
command -v setsid >/dev/null 2>&1 || exit 14
command -v "$bin" >/dev/null 2>&1 || exit 15
if [ "$#" -gt 0 ]; then
  found=""
  for p in "$@"; do [ -e "$p" ] && found=1; done
  [ -n "$found" ] || exit 16
fi
exit 0
'''

_PROBE_MESSAGES = {
    11: "the repository is not visible at the same path inside the container; set workspaceMount and workspaceFolder "
        "to ${{localWorkspaceFolder}} (rig env up does this for a generated config)",
    12: "the Rig runtime is not readable at the same path inside the container; mount {runtime} read-only",
    13: "python3 is not installed in the container (the child MCP server needs it)",
    14: "setsid is not installed in the container (needed to stop a worker's whole process tree)",
    15: "the {worker} CLI is not installed in the container",
    16: "no credentials for {worker} are mounted in the container; mount one of {paths} read-only, or set "
        "{worker} = \"none\" under [sandbox.workers] if it does not need them",
}


def supervised(binary: str, cwd, stop_file, heartbeat_file, command: list[str], env_pairs: list[str],
               lease: int = LEASE_SECONDS) -> list[str]:
    """The argv that runs ``command`` supervised inside the container."""
    return [binary, "exec", "--no-start", "--", "sh", "-c", SUPERVISOR, "rig-supervisor",
            str(cwd), str(stop_file), str(heartbeat_file), str(lease), "env", *env_pairs, *command]


def image_digest(row: dict) -> str:
    return str(dc_client.pick(row, "image_digest", "digest", "image_id", "imageID", "image.digest", default="") or "")


class Lease:
    """Host side of the supervisor protocol, for work run from Python (acceptance checks)."""

    def __init__(self, heartbeat_file, stop_file, interval: float = 1.0):
        self.heartbeat, self.stop, self.interval = Path(heartbeat_file), Path(stop_file), interval
        self._done = threading.Event()
        self._thread = None
        self._count = 0

    def _beat(self):
        self._count += 1
        self.heartbeat.write_text(str(self._count))

    def __enter__(self):
        self.stop.unlink(missing_ok=True)
        self._beat()

        def loop():
            while not self._done.wait(self.interval):
                try:
                    self._beat()
                except OSError:
                    pass

        self._thread = threading.Thread(target=loop, daemon=True, name="rig-container-lease")
        self._thread.start()
        return self

    def request_stop(self):
        """Ask the supervisor to stop the command and its children, and stop beating."""
        self._done.set()
        self.stop.write_text("stop\n")

    def __exit__(self, *_):
        self._done.set()
        if self._thread:
            self._thread.join(timeout=2)
        for path in (self.heartbeat, self.stop):
            path.unlink(missing_ok=True)


class DevcontainerProvider(SandboxProvider):
    name = "devcontainer"

    def __init__(self, repo, credentials: dict | None = None):
        self.repo = Path(repo).resolve()
        self.credentials = dict(credentials or {})
        self.home = Path(os.path.expanduser("~"))

    def dc(self) -> str:
        try:
            return dc_client.require()
        except dc_client.DcError as error:
            raise SandboxError(str(error)) from error

    def status(self, repo) -> dict:
        return {"provider": "devcontainer", "boundary": "container", "ready": bool(dc_client.dc_binary()),
                "runtime": str(RUNTIME), "supported_workers": container_modes.supported_workers()}

    # -- workers ---------------------------------------------------------
    def check_worker(self, worker: str) -> None:
        problem = container_modes.credential_problem(worker, self.credentials)
        if problem:
            raise SandboxError(problem)

    def wrap_worker_cmd(self, cmd, env, *, worker="", job_dir=None):
        self.check_worker(worker)
        if not cmd:
            raise SandboxError("empty worker command")
        raw = str(job_dir or env.get("RIG_JOB_DIR") or "")
        if not raw:
            raise SandboxError("container mode needs the job directory")
        job = Path(raw)
        command = container_modes.apply_permissions(worker, [os.path.basename(cmd[0]), *cmd[1:]])
        pairs = [f"HOME={self.home}"] + [f"{key}={env[key]}" for key in ENV_KEYS if env.get(key)]
        return supervised(self.dc(), self.repo, job / STOP_FILE, job / HEARTBEAT_FILE, command, pairs), dict(env)

    def _probe_run(self, args: list[str], timeout: float = 60):
        import subprocess
        try:
            return subprocess.run([self.dc(), "exec", "--no-start", "--", "sh", "-c", args[0], "rig-probe", *args[1:]],
                                  capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
        except subprocess.TimeoutExpired as error:
            raise SandboxError(f"the container did not answer within {timeout:.0f}s") from error
        except OSError as error:
            raise SandboxError(f"could not run dc: {error}") from error

    def probe(self, worker: str) -> None:
        info = container_modes.mode(worker)
        paths = []
        if self.credentials.get(worker) == "mount-ro":
            paths = [str(self.home / p) for p in info.get("credential_paths", [])]
        done = self._probe_run([PROBE, str(self.repo), str(RUNTIME), "python3", info.get("binary", worker), *paths])
        if done.returncode == 0:
            return
        template = _PROBE_MESSAGES.get(done.returncode)
        if template:
            raise SandboxError("container not ready: " + template.format(
                runtime=RUNTIME, worker=worker, paths=", ".join(f"~/{p}" for p in info.get("credential_paths", []))))
        tail = (done.stderr or done.stdout).strip().splitlines()
        raise SandboxError(f"could not reach the workspace container (dc exit {done.returncode}"
                           f"{': ' + tail[-1] if tail else ''}); start it with: rig env up")

    def container_info(self) -> dict:
        """Facts about the container for evidence. Unknown fields stay empty, never invented."""
        import sandbox_env
        import workspace
        status = sandbox_env.env_status(self.repo)
        rows = status["containers"]
        if not rows:
            return {"found": False, "note": status["error"] or "dc ls reported no container for this repository"}
        row = next((r for r in rows if str(dc_client.pick(r, "state", "status", default="")).lower().startswith(("up", "run"))), rows[0])
        info = workspace.containers_view([row])[0]
        return {"found": True, "name": info["name"], "id": info["id"], "image": info["image"],
                "image_digest": image_digest(row), "state": info["state"], "matched": len(rows)}

    def prepare(self, worker: str, job_dir, job_id: str = "") -> dict:
        """Probe the container and record where this job runs. Called once before a live launch."""
        self.check_worker(worker)
        self.probe(worker)
        import datetime
        evidence = {"version": 1, "provider": "devcontainer", "worker": worker, "job_id": job_id,
                    "container": self.container_info(),
                    "probe": "ok", "lease_seconds": LEASE_SECONDS,
                    "recorded_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")}
        path = Path(job_dir) / "sandbox.json"
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n")
        os.replace(temporary, path)
        return evidence

    # -- checks ----------------------------------------------------------
    def check_command(self, argv: list[str], cwd, stop_file, heartbeat_file) -> list[str]:
        """Probe that the check's directory is visible, then return the supervised argv."""
        done = self._probe_run(['test -d "$1"', str(cwd)], timeout=30)
        if done.returncode != 0:
            raise SandboxError(f"the check directory {cwd} is not visible inside the workspace container; "
                               "is it running (rig env up) with the repository mounted at the same path?")
        return supervised(self.dc(), cwd, stop_file, heartbeat_file, list(argv), [])

    LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "0.0.0.0"})

    def forwarded_ports(self) -> list[int]:
        """Host ports dc-cli currently forwards to the container. Fails closed when it cannot say."""
        import workspace
        try:
            data = dc_client.run_json(["forward", "--status", "--json"], self.repo, timeout=10)
        except dc_client.DcError as error:
            raise SandboxError(f"cannot list forwarded ports, so localhost URLs are refused: {error}") from error
        return sorted({int(p["port"]) for p in workspace.ports_view(data) if str(p["port"]).isdigit()})

    def check_url(self, url: str) -> None:
        """A localhost URL on a port dc-cli does not forward points at the HOST, not the app in the
        container, so evidence captured from it would describe the wrong program."""
        from urllib.parse import urlsplit
        try:
            parts = urlsplit(str(url))
            host, port = (parts.hostname or "").lower(), parts.port
        except ValueError as error:
            raise SandboxError("not a valid URL") from error
        if host not in self.LOCAL_HOSTS:
            return
        port = port or (443 if parts.scheme == "https" else 80)
        forwarded = self.forwarded_ports()
        if port not in forwarded:
            listed = ", ".join(map(str, forwarded)) or "none"
            raise SandboxError(f"{host}:{port} is not a forwarded port (forwarded: {listed}). In container mode that address "
                               "would reach the host, not the app in the container; forward the port with dc-cli first.")

    def evidence(self) -> dict:
        info = self.container_info()
        return {"provider": "devcontainer", "container": info}
