#!/usr/bin/env python3
"""/selftest in rig shell: does each installed parent really work through Rig, flag by flag.

It runs a few tiny real turns per installed parent CLI (a word back, a reasoning level, a listed model, plan mode,
auto-edit, stop), in a throwaway project in the temp folder, never in the user's repository. Each check ends pass,
warn, fail or skip with a reason, and the report is written as it goes to <repo>/.rig/selftest.json (and, at the end,
selftest.txt) so the shell can show it live and the owner can paste it in a bug report. Turns cost what a short prompt
costs on the parent's own plan; nothing starts without the owner asking for it.
"""
from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

TURN_SECONDS = float(os.environ.get("RIG_SELFTEST_TURN_SECONDS") or 120)
CHECKS = (("basic", "answers a message"), ("effort", "accepts a reasoning level"), ("model", "accepts a listed model"),
          ("plan", "plan mode changes nothing"), ("edits", "auto-edit edits without asking"), ("stop", "stop ends the turn"))
ASKING = ("claude", "codex")  # the parents whose headless mode can ask for permission, so auto-edit means something


def report_path(repo) -> Path:
    return Path(repo) / ".rig" / "selftest.json"


def read_report(repo) -> dict:
    """The report as far as it has got; `running` is whether its process is still alive."""
    try:
        data = json.loads(report_path(repo).read_text())
    except (OSError, ValueError):
        return {"state": "none", "running": False, "parents": []}
    pid = data.get("pid")
    alive = False
    if isinstance(pid, int) and pid > 0 and data.get("state") == "running":
        try:
            os.kill(pid, 0)
            alive = True
        except (ProcessLookupError, PermissionError):
            alive = False
    if data.get("state") == "running" and not alive:
        data["state"] = "stopped"  # its process is gone without finishing
    data["running"] = alive
    return data


def start(repo, parents=None) -> dict:
    """Start a self-test in the background, once: a second request while one runs says so."""
    repo = Path(repo).resolve()
    current = read_report(repo)
    if current.get("running"):
        return {"started": False, "reason": "a self-test is already running", "report": current}
    argv = [sys.executable, str(HERE / "selftest.py"), "run", "--repo", str(repo)]
    for name in parents or []:
        argv += ["--parent", name]
    process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               start_new_session=True)
    threading.Thread(target=process.wait, daemon=True).start()
    # say it is running before the new process has written anything, or the next status read shows the last report
    path = report_path(repo)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"state": "running", "pid": process.pid, "parents": [],
                                "started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}))
    return {"started": True, "pid": process.pid}


class Report:
    """The report file, rewritten whole after every change, from several threads."""

    def __init__(self, repo: Path, names: list[str]):
        self.repo, self.lock = repo, threading.Lock()
        self.data = {"state": "running", "pid": os.getpid(), "started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                     "parents": [{"name": n, "checks": [{"id": cid, "label": label, "status": "waiting", "detail": "", "seconds": 0}
                                                         for cid, label in CHECKS]} for n in names]}
        self.save()

    def save(self) -> None:
        path = report_path(self.repo)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=1))
        os.replace(tmp, path)

    def set(self, parent: str, check: str, status: str, detail: str = "", seconds: float = 0.0) -> None:
        with self.lock:
            for row in self.data["parents"]:
                if row["name"] == parent:
                    for one in row["checks"]:
                        if one["id"] == check:
                            one.update(status=status, detail=detail[:400], seconds=round(seconds, 1))
            self.save()

    def finish(self) -> None:
        with self.lock:
            self.data["state"] = "done"
            self.data["finished"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            self.save()
            self.repo.joinpath(".rig", "selftest.txt").write_text(format_text(self.data))


MARK = {"pass": "✓", "warn": "!", "fail": "✕", "skip": "–", "waiting": "·", "running": "…"}


def format_text(data: dict) -> str:
    lines = [f"rig selftest · started {data.get('started', '')} · {data.get('state', '')}"]
    for row in data.get("parents", []):
        lines.append(f"\n{row['name']}")
        for one in row["checks"]:
            tail = f" — {one['detail']}" if one.get("detail") else ""
            lines.append(f"  {MARK.get(one['status'], '?')} {one['label']}: {one['status']}{tail}")
    return "\n".join(lines) + "\n"


class Context:
    """One parent in its own throwaway project: send a message, wait for the turn, look at what it left."""

    def __init__(self, parent: str, root: Path):
        self.parent, self.repo = parent, root
        (root / ".git").mkdir(parents=True, exist_ok=True)
        (root / ".rig").mkdir(exist_ok=True)
        (root / ".rig" / "harness.toml").write_text(
            f'parent = "{parent}"\n\n[project]\nenabled = true\n\n[experiments]\nchat = true\n')
        (root / "a.txt").write_text("one\n")
        subprocess.run(["git", "init", "-q", str(root)], capture_output=True)
        import rig_chat
        self.chat = rig_chat
        self.session = ""

    def say(self, message: str, seconds: float = TURN_SECONDS) -> dict:
        """Send one message and wait for the turn to end (or the time to run out); the transcript as it stands."""
        started = self.chat.send(self.repo, message, self.session, self.parent)
        self.session = started["session"]
        return self.wait(seconds)

    def wait(self, seconds: float) -> dict:
        deadline = time.monotonic() + seconds
        view = self.chat.transcript(self.repo, self.session)
        while view["state"] == "running" and time.monotonic() < deadline:
            time.sleep(0.5)
            view = self.chat.transcript(self.repo, self.session)
        return view

    def command(self, text: str) -> None:
        self.chat.send(self.repo, text, self.session, self.parent)

    @staticmethod
    def errors(view: dict) -> str:
        found = [e["text"] for e in view["entries"] if e["role"] == "error"]
        return found[-1] if found else ""

    @staticmethod
    def answered(view: dict, word: str) -> bool:
        return any(e["role"] == "assistant" and word.lower() in e["text"].lower() for e in view["entries"])


def _turn_result(ctx: Context, view: dict, word: str = "") -> tuple[str, str]:
    error = ctx.errors(view)
    if view["state"] == "running":
        return "fail", f"no answer in {int(TURN_SECONDS)}s"
    if error or view["state"] == "failed":
        return "fail", error or view.get("note") or "the turn failed"
    if word and not ctx.answered(view, word):
        return "fail", "it ended without the answer"
    return "pass", ""


def check_basic(ctx: Context) -> tuple[str, str]:
    return _turn_result(ctx, ctx.say("Reply with exactly the word PONG and nothing else."), "PONG")


def check_effort(ctx: Context) -> tuple[str, str]:
    if not ctx.chat._effort({"effort": "low"}, ctx.parent):
        return "skip", f"{ctx.parent} has no reasoning flag Rig can set"
    import ui_settings
    ui_settings.save(ctx.repo, "parent_effort." + ctx.parent, "low", "repo")
    try:
        return _turn_result(ctx, ctx.say("Reply with exactly the word OK and nothing else."), "OK")
    finally:
        ui_settings.save(ctx.repo, "parent_effort." + ctx.parent, "default", "repo")


def check_model(ctx: Context) -> tuple[str, str]:
    import ui_settings
    names = ui_settings.model_suggestions(ctx.parent)
    if not names or not ctx.chat._model({"model": names[0]}):
        return "skip", f"no listed model to try for {ctx.parent}"
    ui_settings.save(ctx.repo, "parent_model." + ctx.parent, names[0], "repo")
    try:
        status, detail = _turn_result(ctx, ctx.say("Reply with exactly the word OK and nothing else."), "OK")
        return status, (detail or f"model {names[0]}")
    finally:
        ui_settings.save(ctx.repo, "parent_model." + ctx.parent, "default", "repo")


def check_plan(ctx: Context) -> tuple[str, str]:
    target = ctx.repo / "plan-check.txt"
    ctx.command("/plan on")
    try:
        view = ctx.say("Create a file named plan-check.txt containing the word hi. Do it now.")
        status, detail = _turn_result(ctx, view)
        if status != "pass":
            return status, detail
        if target.exists():
            flagged = bool(ctx.chat._plan({"plan": True}, ctx.parent))
            if flagged:
                return "fail", "it wrote a file although plan mode was on (its own plan flag did not hold)"
            return "warn", "plan mode is only asked for in the message for this parent, and it wrote the file anyway"
        return "pass", ""
    finally:
        ctx.command("/plan off")
        target.unlink(missing_ok=True)


def check_edits(ctx: Context) -> tuple[str, str]:
    if ctx.parent not in ASKING:
        return "skip", f"{ctx.parent}'s headless mode never asks for permission, so there is nothing to skip"
    target = ctx.repo / "edit-check.txt"
    ctx.command("/mode edits")
    try:
        view = ctx.say("Create a file named edit-check.txt containing the word hi.", seconds=min(TURN_SECONDS, 90))
        if view["state"] == "running":
            ctx.chat.cancel(ctx.repo, ctx.session)
            return "fail", "it stopped to ask for permission although auto-edit was on"
        status, detail = _turn_result(ctx, view)
        if status == "pass" and not target.exists():
            return "fail", "it answered but did not make the file"
        return status, detail
    finally:
        ctx.command("/mode ask")
        target.unlink(missing_ok=True)


def check_stop(ctx: Context) -> tuple[str, str]:
    ctx.chat.send(ctx.repo, "Run the shell command: sleep 120. Then say done.", ctx.session, ctx.parent)
    pid = ctx.chat.read_meta(ctx.repo, ctx.session).get("runner_pid")
    time.sleep(10)
    if ctx.chat.transcript(ctx.repo, ctx.session)["state"] != "running":
        return "warn", "the turn ended before it could be stopped (the parent did not run the command)"
    ctx.chat.cancel(ctx.repo, ctx.session)
    deadline = time.monotonic() + ctx.chat.KILL_AFTER + 10
    while time.monotonic() < deadline and ctx.chat._alive(pid):
        time.sleep(0.3)
    if ctx.chat._alive(pid):
        return "fail", "the turn's process was still running after the stop"
    return "pass", ""


FUNCTIONS = {"basic": check_basic, "effort": check_effort, "model": check_model, "plan": check_plan,
             "edits": check_edits, "stop": check_stop}


def run_parent(report: Report, parent: str, functions=None) -> None:
    functions = functions or FUNCTIONS
    root = Path(tempfile.mkdtemp(prefix=f"rig-selftest-{parent}-"))
    ctx = None
    try:
        ctx = Context(parent, root)
        for cid, _ in CHECKS:
            report.set(parent, cid, "running")
            started = time.monotonic()
            try:
                status, detail = functions[cid](ctx)
            except Exception as error:  # noqa: BLE001 - a check that crashes is a result, not the end of the test
                import crash_log
                status, detail = "fail", "the check itself crashed: " + crash_log.describe(error)
            report.set(parent, cid, status, detail, time.monotonic() - started)
            if cid == "basic" and status == "fail":
                for rest, _ in CHECKS[1:]:  # no answer at all: the rest would only repeat it
                    report.set(parent, rest, "skip", "skipped: it did not answer")
                break
    finally:
        try:
            view = ctx.chat.transcript(root, ctx.session) if ctx is not None and ctx.session else {}
            if view.get("state") == "running":
                ctx.chat.cancel(root, ctx.session)
        except Exception:  # noqa: BLE001
            pass
        shutil.rmtree(root, ignore_errors=True)


def run(repo, parents=None, functions=None) -> dict:
    import rig_chat
    repo = Path(repo).resolve()
    names = [p for p in (parents or list(rig_chat.HEADLESS)) if shutil.which(p) and p in rig_chat.HEADLESS]
    report = Report(repo, names)
    if not names:
        report.finish()
        return report.data
    threads = [threading.Thread(target=run_parent, args=(report, name, functions), daemon=True) for name in names]
    for one in threads:
        one.start()
    for one in threads:
        one.join()
    report.finish()
    return report.data


def main(argv=None) -> int:
    import argparse
    parser = argparse.ArgumentParser(prog="rig selftest", description="Does each installed parent work through Rig? (runs tiny real turns)")
    parser.add_argument("command", nargs="?", default="run", choices=("run", "show"))
    parser.add_argument("--repo", default="")
    parser.add_argument("--parent", action="append", default=[])
    args = parser.parse_args(argv)
    import jobs
    repo = jobs.repo_root(args.repo or None)
    if args.command == "show":
        print(format_text(read_report(repo)))
        return 0
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(1))
    data = run(repo, args.parent or None)
    print(format_text(data))
    return 1 if any(c["status"] == "fail" for p in data["parents"] for c in p["checks"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
