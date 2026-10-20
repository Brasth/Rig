#!/usr/bin/env python3
"""Conformance checks for an adapter package: ``rig adapters check DIR``.

Runs the adapter through the real launcher (run-worker.sh) in a throwaway repository, with a stand-in CLI installed
under the adapter's binary name (adapter_standin.py), so what is checked is what a real worker CLI would meet:
the manifest, the dry-run argv, the job-scoped MCP payload, a live job with a real MCP handshake and evidence, a
cancel that stops the whole process tree, and container-mode refusal without a credential choice.
Nothing runs against a real worker CLI, and no network or credentials are used.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

import adapters  # noqa: E402

CLEAR = ("RIG_JOB_ID", "RIG_JOB_DIR", "RIG_OWNER_TOKEN", "RIG_LIVE", "RIG_REPO", "RIG_ROLE", "RIG_CHILD_PYTHON", "RIG_PARENT",
         "RIG_OWNER_SESSION", "RIG_TIMEOUT", "RIG_ISOLATION", "RIG_WORKDIR", "RIG_ACCESS", "RIG_MODEL", "RIG_EFFORT",
         "RIG_RUNTIME_READONLY", "RIG_STANDIN_OUTPUT", "RIG_STANDIN_EDIT")


class Result:
    def __init__(self, name, ok, detail=""):
        self.name, self.ok, self.detail = name, ok, detail

    def line(self):
        return f"{'ok  ' if self.ok else 'FAIL'} {self.name}" + (f": {self.detail}" if self.detail else "")


def _git(repo, *args):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *args], check=True, capture_output=True, env=env)


class Sandbox:
    """A throwaway repository, home and bin directory with the stand-in installed as the adapter's binary."""

    def __init__(self, manifest: dict, directory: Path, extra_harness: str = ""):
        self.manifest, self.directory = manifest, Path(directory).resolve()
        self.base = Path(tempfile.mkdtemp(prefix="rig-adapter-check-"))
        self.repo = self.base / "repo"
        self.repo.mkdir()
        _git(self.repo, "init", "-q", "-b", "main")
        (self.repo / ".rig").mkdir()
        (self.repo / ".rig" / "harness.toml").write_text(
            f'parent = "grok"\n\n[project]\nenabled = true\n\n[workers]\n{manifest["name"]} = true\n{extra_harness}')
        (self.repo / "a.txt").write_text("one\n")
        _git(self.repo, "add", "-A")
        _git(self.repo, "commit", "-q", "-m", "init")
        self.home = self.base / "home"
        self.home.mkdir()
        self.bin = self.base / "bin"
        self.bin.mkdir()
        target = self.bin / manifest["binary"]
        target.write_text(f"#!{sys.executable}\n" + (HERE / "adapter_standin.py").read_text())
        target.chmod(0o755)
        self.n = 0

    def close(self):
        shutil.rmtree(self.base, ignore_errors=True)

    def env(self, **extra):
        env = {k: v for k, v in os.environ.items() if k not in CLEAR}
        path = [str(self.bin), str(Path(sys.executable).resolve().parent), "/usr/bin", "/bin", "/usr/sbin", "/sbin"]
        env.update({"PATH": os.pathsep.join(path), "HOME": str(self.home), "RIG_HOME": str(ROOT), "RIG_PARENT": "grok",
                    "RIG_SKIP_MODEL_CATALOG": "1", "RIG_SKIP_UPDATE_CHECK": "1", "RIG_ROLE": "worker",
                    "RIG_OWNER_SESSION": "adapter-check", "RIG_ADAPTER_DIRS": str(self.directory.parent)})
        output = self.manifest["output"]
        if output["decoder"] == "jsonl":
            value = "adapter standin done"
            for key in reversed(output["result_field"].split(".")):
                value = {key: value}
            env["RIG_STANDIN_OUTPUT"] = json.dumps(value)
        env.update(extra)
        return env

    def job(self, job_id=None):
        self.n += 1
        job_id = job_id or f"check{self.n}"
        folder = self.repo / ".rig" / "jobs" / job_id
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "brief.md").write_text("Edit a.txt.\n")
        return job_id, folder

    def run(self, job_id, folder, *, live=False, timeout=120, **extra):
        # Each job claims its own file: a finished job keeps its scope until the parent accepts it.
        env = self.env(**({"RIG_LIVE": "1", "RIG_TIMEOUT": "60"} if live else {}), RIG_JOB_FILES_JSON=json.dumps([f"{job_id}.txt"]), **extra)
        return subprocess.run([str(ROOT / "scripts" / "run-worker.sh"), self.manifest["name"], job_id, str(folder / "brief.md")],
                              cwd=self.repo, env=env, capture_output=True, text=True, timeout=timeout)


def _would_run(out: str) -> list[str]:
    import shlex
    text = out.split("would run:", 1)[1].splitlines()[0].strip()
    return shlex.split(text)


def check(directory, *, live=True, container=True) -> list[Result]:
    results: list[Result] = []
    try:
        manifest = adapters.load_dir(directory)
    except adapters.AdapterError as error:
        return [Result("manifest", False, str(error))]
    results.append(Result("manifest", True, f"{manifest['name']} runs {manifest['binary']}"))
    box = Sandbox(manifest, Path(directory))
    try:
        results += _dry_run(box, manifest)
        results += _mcp_payload(box, manifest)
        if live:
            results += _live(box, manifest)
            results += _cancel(box, manifest)
        if container and manifest["container"]["supported"]:
            results += _container(manifest, directory)
    finally:
        box.close()
    return results


def _dry_run(box, manifest):
    out = []
    job_id, folder = box.job()
    done = box.run(job_id, folder, RIG_MODEL="m-1")
    text = done.stdout + done.stderr
    if done.returncode != 0 or "would run:" not in text:
        return [Result("dry-run", False, (text.strip().splitlines() or ["no output"])[-1])]
    argv = _would_run(text)
    mcp_path = str(folder / "mcp.json")
    problems = []
    if Path(argv[0]).name != manifest["binary"]:
        problems.append(f"runs {argv[0]!r}, not {manifest['binary']!r}")
    if manifest["mcp"]["mode"] == "flag" and mcp_path not in " ".join(argv):
        problems.append("the argv does not name the job-scoped MCP file")
    for flag in manifest["permissions"]["host"]:
        if flag not in argv:
            problems.append(f"missing permission flag {flag}")
    if manifest["model_args"] and not all(item.replace("{model}", "m-1") in argv for item in manifest["model_args"]):
        problems.append("a model was selected but its flags are not in the argv")
    if str(box.repo) not in " ".join(argv) and any("{work}" in item for item in manifest["command"]):
        problems.append("the working directory is not the repository")
    out.append(Result("dry-run argv", not problems, "; ".join(problems)))
    plain = box.run(*box.job())
    argv2 = _would_run(plain.stdout + plain.stderr) if "would run:" in plain.stdout + plain.stderr else []
    leaked = [a for a in argv2 if manifest["model_args"] and manifest["model_args"][0] in a and manifest["model_args"][0].startswith("-")]
    out.append(Result("no model flags without a model", not leaked, ", ".join(leaked)))
    return out


def _mcp_payload(box, manifest):
    job_id, folder = box.job()
    box.run(job_id, folder)
    try:
        payload = json.loads((folder / "mcp.json").read_text())
        import child_mcp
        child_mcp.assert_job_mcp_payload(payload)
    except Exception as error:
        return [Result("job-scoped MCP payload", False, str(error))]
    names = sorted(payload["mcpServers"])
    return [Result("job-scoped MCP payload", names == ["rig", "rig-ask"], f"servers: {', '.join(names)}")]


def _live(box, manifest):
    job_id, folder = box.job()
    done = box.run(job_id, folder, live=True)
    text = done.stdout + done.stderr
    try:
        result = json.loads((folder / "result.json").read_text())
        record = json.loads((folder / "standin.json").read_text())
    except (OSError, ValueError):
        return [Result("live job", False, (text.strip().splitlines() or ["no output"])[-1])]
    out = [Result("live job", done.returncode == 0 and result.get("status") == "ok", f"status {result.get('status')}")]
    out.append(Result("MCP handshake", bool(record.get("handshake")) and "rig" in record.get("servers", []),
                      "the worker started the MCP server from the config it was given and answered rig_job_inbox"))
    out.append(Result("output decoder", result.get("summary") == "adapter standin done", f"summary {result.get('summary')!r}"))
    secrets = [a for a in record.get("argv", []) if adapters._SECRETISH.search(a)]
    out.append(Result("no secrets in argv", not secrets, ", ".join(secrets)))
    out.append(Result("change evidence", (folder / "change-evidence.json").is_file() or (folder / "change-after.json").is_file(), ""))
    return out


def _cancel(box, manifest):
    sys.path.insert(0, str(HERE))
    import jobs
    job_id, folder = box.job()
    (folder / "standin-mode").write_text("hang")
    proc = subprocess.Popen([str(ROOT / "scripts" / "run-worker.sh"), manifest["name"], job_id, str(folder / "brief.md")],
                            cwd=box.repo, env=box.env(RIG_LIVE="1", RIG_TIMEOUT="60", RIG_JOB_FILES_JSON=json.dumps([f"{job_id}.txt"])), stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True)
    deadline = time.monotonic() + 40
    while time.monotonic() < deadline and not (folder / "standin-pids").exists():
        time.sleep(0.1)
    if not (folder / "standin-pids").exists():
        proc.kill()
        return [Result("cancel", False, "the worker never started")]
    agent, child = (int(x) for x in (folder / "standin-pids").read_text().split())
    jobs.cancel_job(box.repo, job_id)
    try:
        proc.communicate(timeout=60)
    except subprocess.TimeoutExpired:
        proc.kill()
        return [Result("cancel", False, "the launcher did not exit after cancel")]
    time.sleep(0.5)
    alive = []
    for pid in (agent, child):
        stat = Path(f"/proc/{pid}/stat")
        try:
            os.kill(pid, 0)
            if not stat.exists() or stat.read_text().split()[2] != "Z":
                alive.append(pid)
        except ProcessLookupError:
            pass
    return [Result("cancel stops the process tree", not alive and proc.returncode == 130, f"alive: {alive}" if alive else "")]


def _container(manifest, directory):
    out = []
    tmp = Sandbox(manifest, Path(directory), extra_harness='\n[sandbox]\nprovider = "devcontainer"\n')
    try:
        sys.path.insert(0, str(ROOT / "tests"))
        job_id, folder = tmp.job()
        done = tmp.run(job_id, folder, RIG_DC_BIN=shutil.which("true") or "/bin/true")
        text = done.stdout + done.stderr
        out.append(Result("container mode needs a credential choice", done.returncode != 0 and "credential choice" in text,
                          (text.strip().splitlines() or [""])[-1][:120]))
    finally:
        tmp.close()
    return out


def main(argv=None) -> int:
    import argparse
    parser = argparse.ArgumentParser(prog="rig adapters check", description="Run the conformance checks on an adapter directory")
    parser.add_argument("directory")
    parser.add_argument("--no-live", action="store_true", help="skip the live job and cancel checks")
    args = parser.parse_args(argv)
    results = check(args.directory, live=not args.no_live)
    for result in results:
        print(result.line())
    return 0 if all(r.ok for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
