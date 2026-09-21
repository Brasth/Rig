#!/usr/bin/env python3
import json
import sys
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RIG = ROOT / "bin" / "rig"


def run_rig(repo: Path, *args: str, env: dict | None = None, stdin: str | None = None) -> subprocess.CompletedProcess:
    merged = os.environ.copy()
    merged["RIG_HOME"] = str(ROOT)
    # CLI behavior fixtures use the checkout as a read-only runtime.
    merged["RIG_INSTALL_TRANSACTION"] = "1"
    merged["PATH"] = f"{ROOT / 'bin'}:{merged.get('PATH', '')}"
    merged["RIG_SKIP_UPDATE_CHECK"] = "1"
    merged["RIG_SKIP_MODEL_CATALOG"] = "1"
    if env:
        merged.update(env)
    return subprocess.run(
        [str(RIG), *args],
        cwd=repo,
        env=merged,
        text=True,
        capture_output=True,
        check=False,
        input=stdin,
    )


class CliMemoryAndThread(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.repo = Path(self.td.name)
        (self.repo / ".git").mkdir()
        proc = run_rig(self.repo, "init")
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def tearDown(self):
        self.td.cleanup()

    def test_memory_add_and_show(self):
        add = run_rig(self.repo, "memory", "add", "Pin full Claude model ids")
        self.assertEqual(add.returncode, 0, add.stderr)
        self.assertEqual(add.stdout.strip(), "added")
        again = run_rig(self.repo, "memory", "add", "pin full Claude model ids")
        self.assertEqual(again.stdout.strip(), "exists")
        shown = run_rig(self.repo, "memory")
        self.assertIn("Pin full Claude model ids", shown.stdout)

    def test_job_start_stamps_parent_thread(self):
        proc = run_rig(
            self.repo,
            "job",
            "start",
            "--worker",
            "grok",
            "--role",
            "implement",
            env={"RIG_THREAD": "parent-thread-cli", "RIG_PARENT": "grok"},
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        job_id = proc.stdout.strip().splitlines()[-1]
        meta = json.loads((self.repo / ".rig" / "jobs" / job_id / "meta.json").read_text())
        self.assertEqual(meta.get("thread"), "parent-thread-cli")
        listed = run_rig(self.repo, "jobs")
        self.assertIn("parent-thread-cli", listed.stdout)
        self.assertIn(job_id, listed.stdout)
        mailed = run_rig(self.repo, "job", "message", job_id, "--text", "use listed files")
        self.assertEqual(mailed.returncode, 0, mailed.stderr + mailed.stdout)
        self.assertIn("inbox pending", mailed.stdout)
        shown = run_rig(self.repo, "job", "show", job_id)
        self.assertIn("use listed files", shown.stdout)

    def test_native_job_metadata_is_explicit_and_survives_finish(self):
        for command in ("start", "record"):
            for explicit in (False, True):
                with self.subTest(command=command, explicit=explicit):
                    job_id = f"parent-{command}-{explicit}"
                    args = ["job", command, job_id, "--worker", "grok", "--role", "parent"]
                    if command == "start":
                        args.extend(["--json", "--owner-session", "cli-metadata-test"])
                    if explicit:
                        args.extend(["--model", "actual-parent-model", "--effort", "high", "--executor-kind", "parent"])
                    proc = run_rig(self.repo, *args, env={"RIG_PARENT": "grok"})
                    self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
                    meta_path = self.repo / ".rig" / "jobs" / job_id / "meta.json"
                    meta = json.loads(meta_path.read_text())
                    self.assertEqual(meta["executor_kind"], "parent")
                    self.assertEqual(meta["model"], "actual-parent-model" if explicit else "")
                    self.assertEqual(meta["effort"], "high" if explicit else "")
                    self.assertEqual(meta["model_source"], "observed" if explicit else "unknown")
                    if command == "start":
                        lease = json.loads(proc.stdout)
                        credentials = ["--reservation-id", lease["reservation_id"], "--attempt-id", lease["attempt_id"],
                                       "--owner-session", "cli-metadata-test"]
                        owner_env = {"RIG_PARENT": "grok", "RIG_OWNER_TOKEN": lease["owner_token"]}
                        finished = run_rig(self.repo, "job", "finish", job_id, *credentials,
                                           "--completion-json", '{"kind":"parent_task","completed":true}', env=owner_env)
                        self.assertEqual(finished.returncode, 0, finished.stderr)
                        after = json.loads(meta_path.read_text())
                        for key in ("model", "effort", "executor_kind", "model_source"):
                            self.assertEqual(after[key], meta[key])
                        closed = run_rig(self.repo, "job", "close", job_id, *credentials,
                                         "--rationale", "Metadata fixture complete", env=owner_env)
                        self.assertEqual(closed.returncode, 0, closed.stderr)

    def test_job_start_forwards_native_child_selection(self):
        proc = run_rig(
            self.repo, "job", "start", "selected-child", "--worker", "codex", "--role", "mini",
            "--model", "gpt-5.6-luna", "--effort", "low", "--executor-kind", "native_child",
            env={"RIG_PARENT": "codex"},
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        meta = json.loads((self.repo / ".rig" / "jobs" / "selected-child" / "meta.json").read_text())
        self.assertEqual(meta["model"], "gpt-5.6-luna")
        self.assertEqual(meta["effort"], "low")
        self.assertEqual(meta["executor_kind"], "native_child")
        self.assertEqual(meta["model_source"], "selected")

    def test_job_metadata_flags_validate_before_writing(self):
        for args in (
            ["--executor-kind", "wrapper"], ["--executor-kind", "invalid"],
            ["--executor-kind"], ["--model"], ["--effort"],
        ):
            with self.subTest(args=args):
                proc = run_rig(self.repo, "job", "start", "invalid-job", *args)
                self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
                self.assertFalse((self.repo / ".rig" / "jobs" / "invalid-job").exists())

    def test_session_compact_cli_is_opt_in_and_validates_limit(self):
        common = ["session", "--role", "stay", "--case", "advise"]
        env = {"PATH": _stub_path(), "RIG_PARENT": "grok"}
        default = run_rig(self.repo, *common, "--json", env=env)
        self.assertEqual(default.returncode, 0, default.stderr)
        full = json.loads(default.stdout)
        self.assertEqual(set(full), {"memory", "jobs", "workflows", "status", "pick"})
        self.assertIsInstance(full["status"], str)
        for limit in (0, 10, 100):
            compact = run_rig(self.repo, *common, "--compact", "--terminal-limit", str(limit), "--json", env=env)
            self.assertEqual(compact.returncode, 0, compact.stderr)
            payload = json.loads(compact.stdout)
            self.assertEqual(payload["schema_version"], 2)
            self.assertEqual(payload["mode"], "compact")
            self.assertIsInstance(payload["status"], dict)
            self.assertEqual(payload["history"]["total"], 0)
        text = run_rig(self.repo, *common, "--compact", env=env)
        self.assertEqual(text.returncode, 0, text.stderr)
        self.assertEqual(text.stdout.count("# jobs"), 1)
        self.assertEqual(text.stdout.count("no jobs"), 1)
        self.assertEqual(text.stdout.count("# status"), 1)
        for value in ("-1", "101", "1.5", "true"):
            invalid = run_rig(self.repo, *common, "--compact", "--terminal-limit", value, env=env)
            self.assertEqual(invalid.returncode, 2, invalid.stdout + invalid.stderr)
            self.assertIn("terminal-limit", invalid.stderr)
        missing = run_rig(self.repo, *common, "--terminal-limit", env=env)
        self.assertEqual(missing.returncode, 2)

    def test_pick_and_session_forward_observed_parent_metadata(self):
        env = {"PATH": _stub_path(), "RIG_PARENT": "grok"}
        for command in ("pick", "session"):
            with self.subTest(command=command):
                args = [command, "implement", "--case", "fix header", "--json"]
                proc = run_rig(
                    self.repo, *args, "--parent-model", "actual-parent-model", "--parent-effort", "high", env=env,
                )
                self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
                choice = json.loads(proc.stdout)
                if command == "session":
                    choice = choice["pick"]
                self.assertEqual(choice["model"], "actual-parent-model")
                self.assertEqual(choice["effort"], "high")
                self.assertEqual(choice["model_source"], "observed")

    def test_queue_add_list_cancel_and_jobs_footer(self):
        added = run_rig(self.repo, "queue", "add", "fix pagination")
        self.assertEqual(added.returncode, 0, added.stderr + added.stdout)
        self.assertIn("queued ", added.stdout)
        self.assertIn("fix pagination", added.stdout)
        qdir = self.repo / ".rig" / "queue"
        files = list(qdir.glob("*.json"))
        self.assertEqual(len(files), 1)
        obj = json.loads(files[0].read_text())
        self.assertEqual(obj["status"], "pending")
        listed = run_rig(self.repo, "queue", "list")
        self.assertIn("1 pending", listed.stdout)
        jobs = run_rig(self.repo, "jobs")
        self.assertIn("QUEUE", jobs.stdout)
        self.assertIn("fix pagination", jobs.stdout)
        cancelled = run_rig(self.repo, "queue", "cancel", obj["id"])
        self.assertEqual(cancelled.returncode, 0, cancelled.stderr)
        self.assertIn("cancelled", cancelled.stdout)
        again = run_rig(self.repo, "queue", "list")
        self.assertIn("0 pending", again.stdout)

    def _template_ignore_entries(self):
        return [line for line in (ROOT / "templates" / "gitignore-fragment").read_text().splitlines() if line]

    def test_init_system_bash_completes_and_preserves_protocol(self):
        env = dict(os.environ, RIG_HOME=str(ROOT), RIG_INSTALL_TRANSACTION="1",
                   RIG_SKIP_UPDATE_CHECK="1", RIG_SKIP_MODEL_CATALOG="1")
        proc = subprocess.run(
            ["/bin/bash", str(RIG), "init"], cwd=self.repo, env=env,
            capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        def protocol(path):
            return path.read_text().split("<!-- rig:start -->", 1)[1].split("<!-- rig:end -->", 1)[0]
        self.assertEqual(protocol(self.repo / "AGENTS.md"), protocol(ROOT / "AGENTS.md"))

    def test_init_applies_template_gitignore_and_keeps_max_running(self):
        gi = self.repo / ".gitignore"
        gi.write_text(".rig/\nkeep-unrelated/\n")
        harness = self.repo / ".rig" / "harness.toml"
        original = harness.read_text()
        self.assertIn("max_running", original)
        proc = run_rig(self.repo, "init")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        text = gi.read_text()
        gi_lines = text.splitlines()
        self.assertIn(".rig/", gi_lines)
        self.assertEqual(gi_lines.count("keep-unrelated/"), 1)
        for line in self._template_ignore_entries():
            self.assertEqual(gi_lines.count(line), 1)
        self.assertIn(".rig/queue/", gi_lines)
        self.assertIn("max_running = 3", harness.read_text())
        harness.write_text('parent = "codex"\n\n[workers]\ngrok = true\n\n[queue]\nmax_running = 1\n')
        again = run_rig(self.repo, "init")
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertIn("max_running = 1", harness.read_text())
        self.assertNotIn("max_running = 3", harness.read_text())
        again_lines = gi.read_text().splitlines()
        self.assertIn(".rig/", again_lines)
        self.assertEqual(again_lines.count("keep-unrelated/"), 1)
        for line in self._template_ignore_entries():
            self.assertEqual(again_lines.count(line), 1)
        skill = self.repo / ".agents" / "skills" / "rig-queue" / "SKILL.md"
        self.assertTrue(skill.is_file())
        self.assertIn("Does not spawn", skill.read_text())

    def test_init_creates_gitignore_from_template_when_missing(self):
        gi = self.repo / ".gitignore"
        if gi.exists():
            gi.unlink()
        proc = run_rig(self.repo, "init")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue(gi.is_file())
        gi_lines = gi.read_text().splitlines()
        for line in self._template_ignore_entries():
            self.assertEqual(gi_lines.count(line), 1)

    def test_prune_persists_activity_then_drops_ok_log(self):
        job_dir = self.repo / ".rig" / "jobs" / "prune-ok"
        job_dir.mkdir(parents=True)
        (job_dir / "meta.json").write_text(
            json.dumps(
                {
                    "job_id": "prune-ok",
                    "worker": "grok",
                    "role": "implement",
                    "status": "ok",
                }
            )
        )
        (job_dir / "stdout.log").write_text(
            '{"type":"tool_call","toolName":"read_file","rawInput":{"path":"README.md"}}\n'
        )
        proc = run_rig(self.repo, "prune")
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        self.assertFalse((job_dir / "stdout.log").is_file())
        activity = json.loads((job_dir / "activity.json").read_text())
        self.assertTrue(any("read_file" in str(line) for line in activity.get("lines") or []))


def _stub_path(extra: Path | None = None) -> str:
    import sys

    parts = [
        "/usr/bin",
        "/bin",
        "/usr/sbin",
        "/sbin",
        str(Path(sys.executable).resolve().parent),
    ]
    if extra:
        parts.insert(0, str(extra))
    return ":".join(parts)


def _core_bins(folder: Path) -> str:
    import shutil

    names = (
        "bash",
        "mkdir",
        "cp",
        "mv",
        "mktemp",
        "cat",
        "grep",
        "chmod",
        "ln",
        "awk",
        "sed",
        "rm",
        "dirname",
        "head",
        "tr",
        "uname",
    )
    folder.mkdir(parents=True, exist_ok=True)
    for name in names:
        src = shutil.which(name)
        if not src:
            continue
        dest = folder / name
        if not dest.exists():
            dest.symlink_to(src)
    return str(folder)


def _fake_bin(folder: Path, name: str) -> None:
    path = folder / name
    path.write_text("#!/bin/sh\nexit 0\n")
    path.chmod(0o755)


class InitPresence(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.repo = Path(self.td.name)
        (self.repo / ".git").mkdir()
        self.bins = self.repo / "bins"
        self.bins.mkdir()

    def tearDown(self):
        self.td.cleanup()

    def test_new_init_cursor_on_when_cli_present(self):
        _fake_bin(self.bins, "cursor-agent")
        proc = run_rig(self.repo, "init", env={"PATH": _stub_path(self.bins)})
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        text = (self.repo / ".rig" / "harness.toml").read_text()
        self.assertRegex(text, r"cursor\s*=\s*true")
        self.assertRegex(text, r"grok\s*=\s*false")
        self.assertRegex(text, r"claude\s*=\s*false")

    def test_new_init_cursor_off_when_cli_missing(self):
        proc = run_rig(self.repo, "init", env={"PATH": _stub_path()})
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        text = (self.repo / ".rig" / "harness.toml").read_text()
        self.assertRegex(text, r"cursor\s*=\s*false")

    def test_existing_harness_flags_not_flipped(self):
        rig_dir = self.repo / ".rig"
        rig_dir.mkdir()
        (rig_dir / "harness.toml").write_text(
            'parent = "codex"\n\n[workers]\ncodex = false\ngrok = true\nclaude = false\n'
        )
        _fake_bin(self.bins, "opencode")
        _fake_bin(self.bins, "omp")
        _fake_bin(self.bins, "pi")
        _fake_bin(self.bins, "agy")
        proc = run_rig(self.repo, "init", env={"PATH": _stub_path(self.bins)})
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        text = (rig_dir / "harness.toml").read_text()
        self.assertRegex(text, r"claude\s*=\s*false")
        self.assertRegex(text, r"cursor\s*=\s*false")
        self.assertNotRegex(text, r"cursor\s*=\s*true")
        self.assertRegex(text, r"opencode\s*=\s*false")
        self.assertRegex(text, r"omp\s*=\s*false")
        self.assertRegex(text, r"pi\s*=\s*false")
        self.assertRegex(text, r"agy\s*=\s*false")
        self.assertNotRegex(text, r"opencode\s*=\s*true")
        self.assertNotRegex(text, r"omp\s*=\s*true")
        self.assertNotRegex(text, r"pi\s*=\s*true")
        self.assertNotRegex(text, r"agy\s*=\s*true")

    def test_workers_cursor_on(self):
        proc = run_rig(self.repo, "init", env={"PATH": _stub_path()})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        on = run_rig(self.repo, "workers", "cursor=on", env={"PATH": _stub_path()})
        self.assertEqual(on.returncode, 0, on.stderr)
        self.assertIn("cursor = true", on.stdout)
        text = (self.repo / ".rig" / "harness.toml").read_text()
        self.assertRegex(text, r"cursor\s*=\s*true")

    def test_doctor_lists_cursor_and_apps(self):
        proc = run_rig(self.repo, "init", env={"PATH": _stub_path()})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        doc = run_rig(self.repo, "doctor", env={"PATH": _stub_path(), "RIG_PARENT": "codex"})
        self.assertEqual(doc.returncode, 0, doc.stderr)
        self.assertIn("cursor", doc.stdout)
        self.assertIn("Apps (not spawnable)", doc.stdout)
        self.assertIn("grok-bot", doc.stdout)
        self.assertIn("curl https://cursor.com/install", doc.stdout)
        self.assertIn("MCP", doc.stdout)
        self.assertRegex(doc.stdout, r"cursor: excluded")
        self.assertRegex(doc.stdout, r"grok: unavailable \(binary 'grok' not on PATH\)")
        self.assertRegex(doc.stdout, r"codex: unavailable \(binary 'codex' not on PATH\)")
        self.assertRegex(doc.stdout, r"opencode: unavailable \(binary 'opencode' not on PATH\)")
        self.assertRegex(doc.stdout, r"omp: unavailable \(binary 'omp' not on PATH\)")
        self.assertRegex(doc.stdout, r"pi: unavailable \(binary 'pi' not on PATH\)")
        self.assertRegex(doc.stdout, r"agy: unavailable \(binary 'agy' not on PATH\)")

    def test_new_init_opencode_omp_pi_agy_on_when_cli_present(self):
        _fake_bin(self.bins, "opencode")
        _fake_bin(self.bins, "omp")
        _fake_bin(self.bins, "pi")
        _fake_bin(self.bins, "agy")
        proc = run_rig(self.repo, "init", env={"PATH": _stub_path(self.bins)})
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        text = (self.repo / ".rig" / "harness.toml").read_text()
        self.assertRegex(text, r"opencode\s*=\s*true")
        self.assertRegex(text, r"omp\s*=\s*true")
        self.assertRegex(text, r"pi\s*=\s*true")
        self.assertRegex(text, r"agy\s*=\s*true")
        self.assertRegex(text, r"cursor\s*=\s*false")

    def test_workers_opencode_on(self):
        proc = run_rig(self.repo, "init", env={"PATH": _stub_path()})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        on = run_rig(
            self.repo,
            "workers",
            "opencode=on",
            "omp=on",
            "pi=on",
            "agy=on",
            env={"PATH": _stub_path()},
        )
        self.assertEqual(on.returncode, 0, on.stderr)
        self.assertIn("opencode = true", on.stdout)
        self.assertIn("omp = true", on.stdout)
        self.assertIn("pi = true", on.stdout)
        self.assertIn("agy = true", on.stdout)
        text = (self.repo / ".rig" / "harness.toml").read_text()
        self.assertRegex(text, r"opencode\s*=\s*true")
        self.assertRegex(text, r"omp\s*=\s*true")
        self.assertRegex(text, r"pi\s*=\s*true")
        self.assertRegex(text, r"agy\s*=\s*true")

    def test_doctor_lists_opencode_omp_pi_agy_and_install_hints(self):
        proc = run_rig(self.repo, "init", env={"PATH": _stub_path()})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        doc = run_rig(self.repo, "doctor", env={"PATH": _stub_path(), "RIG_PARENT": "codex"})
        self.assertEqual(doc.returncode, 0, doc.stderr)
        self.assertIn("opencode", doc.stdout)
        self.assertIn("omp", doc.stdout)
        self.assertRegex(doc.stdout, r"\bpi\b")
        self.assertRegex(doc.stdout, r"\bagy\b")
        self.assertIn("curl -fsSL https://opencode.ai/install", doc.stdout)
        self.assertIn("curl -fsSL https://omp.sh/install", doc.stdout)
        self.assertIn("@earendil-works/pi-coding-agent", doc.stdout)
        self.assertIn("curl -fsSL https://antigravity.google/cli/install.sh", doc.stdout)

    def test_job_start_accepts_cursor(self):
        # Explicit native model registration remains manual compatibility, not
        # permission to auto-select or launch a Cursor wrapper.
        run_rig(self.repo, "init", env={"PATH": _stub_path()})
        run_rig(self.repo, "workers", "cursor=on", env={"PATH": _stub_path()})
        proc = run_rig(
            self.repo,
            "job",
            "start",
            "--worker",
            "cursor",
            "--role",
            "implement",
            "--model", "composer-2.5",
            env={"PATH": _stub_path()},
        )
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)

    def test_job_start_accepts_opencode_omp_pi_agy(self):
        run_rig(self.repo, "init", env={"PATH": _stub_path()})
        run_rig(
            self.repo,
            "workers",
            "opencode=on",
            "omp=on",
            "pi=on",
            "agy=on",
            env={"PATH": _stub_path()},
        )
        for name in ("opencode", "omp", "pi", "agy"):
            models = {"opencode": "openai/gpt-5.6-luna", "omp": "grok-4.6",
                      "pi": "grok-4.6", "agy": "gemini-3.8-flash-high"}
            proc = run_rig(
                self.repo,
                "job",
                "start",
                "--worker",
                name,
                "--role",
                "implement",
                "--model", models[name],
                "--json", "--owner-session", "worker-availability-test",
                env={"PATH": _stub_path()},
            )
            self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
            lease = json.loads(proc.stdout)
            credentials = ["--reservation-id", lease["reservation_id"], "--attempt-id", lease["attempt_id"],
                           "--owner-session", "worker-availability-test"]
            owner_env = {"PATH": _stub_path(), "RIG_OWNER_TOKEN": lease["owner_token"]}
            completion = json.dumps({"kind": "native_child", "agent_id": name + "-fixture", "terminal": True, "outcome": "ok"})
            finished = run_rig(self.repo, "job", "finish", lease["job_id"], *credentials,
                               "--completion-json", completion, env=owner_env)
            self.assertEqual(finished.returncode, 0, finished.stderr)
            closed = run_rig(self.repo, "job", "close", lease["job_id"], *credentials,
                             "--rationale", "Availability fixture complete", env=owner_env)
            self.assertEqual(closed.returncode, 0, closed.stderr)

    def test_job_start_refuses_disabled_grok_unless_live_parent(self):
        run_rig(self.repo, "init", env={"PATH": _stub_path()})
        refused = run_rig(
            self.repo,
            "job",
            "start",
            "--worker",
            "grok",
            env={"PATH": _stub_path(), "RIG_PARENT": "pi"},
        )
        self.assertNotEqual(refused.returncode, 0, refused.stdout + refused.stderr)
        self.assertIn("off in harness", refused.stderr)
        allowed = run_rig(
            self.repo,
            "job",
            "start",
            "--worker",
            "grok",
            env={"PATH": _stub_path(), "RIG_PARENT": "grok"},
        )
        self.assertEqual(allowed.returncode, 0, allowed.stderr + allowed.stdout)

    def test_init_writes_agents_without_python3(self):
        path = _core_bins(self.bins / "core")
        proc = run_rig(self.repo, "init", env={"PATH": path})
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        agents = self.repo / "AGENTS.md"
        self.assertTrue(agents.is_file(), proc.stdout + proc.stderr)
        text = agents.read_text(encoding="utf-8")
        self.assertIn("<!-- rig:start -->", text)
        self.assertIn("MUST use Rig", text)
        self.assertIn("wrote ", proc.stdout)
        self.assertIn("AGENTS.md", proc.stdout)
        self.assertNotIn("python3 is required", proc.stderr)

    def test_init_writes_agents_when_locale_is_c(self):
        proc = run_rig(
            self.repo,
            "init",
            env={
                "PATH": _stub_path(),
                "LANG": "C",
                "LC_ALL": "C",
                "PYTHONUTF8": "0",
            },
        )
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        path = self.repo / "AGENTS.md"
        self.assertTrue(path.is_file(), proc.stdout + proc.stderr)
        text = path.read_text(encoding="utf-8")
        self.assertIn("<!-- rig:start -->", text)
        self.assertIn("MUST use Rig", text)
        self.assertIn("\u2192", text)

    def test_init_agents_tells_parent_to_answer_ask(self):
        proc = run_rig(self.repo, "init", env={"PATH": _stub_path()})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        text = (self.repo / "AGENTS.md").read_text()
        self.assertIn("rig job wait", text)
        self.assertIn("rig job allow", text)
        self.assertIn("Orchestrate with MCP", text)
        self.assertIn("rig_job_allow", text)
        self.assertIn("rig_job_message", text)
        self.assertIn("Never kill", text)
        self.assertIn("Never spawn another worker", text)
        self.assertIn("parent_writes", text)
        self.assertIn("rig_session", text)
        self.assertIn("--exclude", text)
        self.assertIn("background", text)
        self.assertNotIn("Loop `rig job wait`", text)
        self.assertIn("rig_job_wait", text)
        self.assertIn("rig_pick", text)
        self.assertIn("rig_status", text)
        self.assertIn("rig_job_start", text)
        self.assertIn("Do not spawn Cursor/Codex/OpenCode/OMP/Pi/agy just because their CLI is on PATH", text)
        self.assertIn("rig use grok|codex|opencode|omp|pi|agy", text)
        self.assertIn("Claude Code and Cursor CLI are never the parent", text)
        self.assertIn("OpenCode, OMP, Pi, and agy can be the parent", text)
        self.assertIn("When live parent is agy, do not use nested agy /agent dispatch", text)
        self.assertIn("openai/gpt-5.6-luna", text)
        self.assertIn("gemini-3.8-flash-high", text)
        self.assertIn("~/.rig/cache/model-catalogs.json", text)
        self.assertIn("profiles require exact selectors or declared aliases", text)
        self.assertIn("never spawn a worker whose harness flag is false", text)
        self.assertIn("Timeout/fail does not unlock a disabled worker", text)
        self.assertIn("Parent checks first", text)
        self.assertIn("Brief lists files, the change, and absolute skill file paths", text)
        self.assertIn("Child does not hunt extra updates", text)
        self.assertIn("write of the listed files", text)
        skill = (self.repo / ".agents" / "skills" / "delegate-harness" / "SKILL.md").read_text()
        self.assertIn("parent_writes", skill)
        self.assertIn("Stage-gated parallel", skill)
        self.assertIn("MCP first", skill)
        self.assertIn("Do not shell", skill)
        self.assertIn("Fail classes", skill)
        self.assertIn("skill file paths", skill)
        self.assertNotIn("spawn a worker that can", skill)
        self.assertIn('Not "find the bug"', text)
        self.assertIn("codebase gather", text)
        self.assertIn("explore/mini", text)
        self.assertIn("Ask / plan / advise", text)
        self.assertIn("Docs/skills-only", text)
        self.assertIn("cannot name the files after a short check", text)
        self.assertIn("do not also spawn explore", text)
        self.assertIn("After implement+verify ok", text)
        self.assertIn("disjoint", text)
        self.assertIn("Claim needs id", text)
        self.assertIn("occupied files", text)
        self.assertIn("skip overlap", text)
        self.assertIn("ids", text)
        self.assertIn("more than one item is pending", skill)
        self.assertIn("Parent chooses kind", text)
        self.assertIn("Do not encode local slash command names", text)
        self.assertIn("rig pick implement --case", text)
        self.assertIn("slash-command catalog", text)
        self.assertNotIn("omit --model unless RIG_MODEL is set", text)
        start = text.split("<!-- rig:start -->", 1)[1].split("<!-- rig:end -->", 1)[0]
        # Quoted protocol text must remain literal and valid on macOS Bash 3.2.
        syntax = subprocess.run(["/bin/bash", "-n", str(RIG)], capture_output=True, text=True)
        self.assertEqual(syntax.returncode, 0, syntax.stderr)
        self.assertIn("the parent's reply includes", start)
        self.assertIn("stay|explore|mini|bulk|implement|hard|review|verify", start)
        self.assertIn("rig_workflow_wait", start)
        self.assertIn("rig_workflow_advance", start)
        self.assertIn("rig_job_coordination_reply", start)
        self.assertIn("rig_job_coordination_request", start)
        self.assertIn("file AND resource", start)
        self.assertIn("[orchestration]", start)
        source_skill = (ROOT / "skills" / "delegate-harness" / "SKILL.md").read_bytes()
        copied = (self.repo / ".agents" / "skills" / "delegate-harness" / "SKILL.md").read_bytes()
        self.assertEqual(source_skill, copied)

    def test_session_and_pick_exclude(self):
        sess = run_rig(
            self.repo,
            "session",
            "--role",
            "stay",
            "--case",
            "advise on the tradeoff",
            "--json",
            env={"PATH": _stub_path(), "RIG_PARENT": "grok"},
        )
        self.assertEqual(sess.returncode, 0, sess.stderr + sess.stdout)
        payload = json.loads(sess.stdout)
        self.assertIn("memory", payload)
        self.assertIn("jobs", payload)
        self.assertIn("status", payload)
        self.assertEqual(payload["pick"]["spawn"], "stay")
        picked = run_rig(
            self.repo,
            "pick",
            "implement",
            "--case",
            "add a header",
            "--exclude",
            "grok",
            "--json",
            env={"PATH": _stub_path(), "RIG_PARENT": "grok"},
        )
        self.assertEqual(picked.returncode, 0, picked.stderr + picked.stdout)
        choice = json.loads(picked.stdout)
        self.assertIn(choice["spawn"], ("none", "run-worker", "native"))
        self.assertFalse(choice.get("parent_writes"))

    def test_use_opencode_omp_pi_writes_parent(self):
        proc = run_rig(self.repo, "init", env={"PATH": _stub_path()})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        for name in ("opencode", "omp", "pi", "agy", "grok", "codex"):
            used = run_rig(self.repo, "use", name, env={"PATH": _stub_path()})
            self.assertEqual(used.returncode, 0, used.stderr + used.stdout)
            self.assertIn(f"preferred parent = {name}", used.stdout)
            text = (self.repo / ".rig" / "harness.toml").read_text()
            self.assertRegex(text, rf'parent\s*=\s*"{name}"')

    def test_use_claude_and_cursor_rejected(self):
        proc = run_rig(self.repo, "init", env={"PATH": _stub_path()})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        for name in ("claude", "cursor"):
            used = run_rig(self.repo, "use", name, env={"PATH": _stub_path()})
            self.assertEqual(used.returncode, 2, used.stdout + used.stderr)
            self.assertIn("usage: rig use", used.stderr)

    def test_init_keeps_existing_codex_parent(self):
        rig_dir = self.repo / ".rig"
        rig_dir.mkdir()
        (rig_dir / "harness.toml").write_text(
            'parent = "codex"\n\n[workers]\ncodex = false\ngrok = true\nclaude = false\n'
        )
        proc = run_rig(self.repo, "init", env={"PATH": _stub_path()})
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        text = (rig_dir / "harness.toml").read_text()
        self.assertRegex(text, r'parent\s*=\s*"codex"')
        self.assertNotRegex(text, r'parent\s*=\s*"opencode"')

    def test_doctor_lists_opencode_omp_pi_mcp_missing(self):
        proc = run_rig(self.repo, "init", env={"PATH": _stub_path()})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        home = self.repo / "empty-home"
        home.mkdir()
        bins = self.repo / "mcp-bins-missing"
        bins.mkdir()
        for name in ("opencode", "omp", "pi", "agy"):
            _fake_bin(bins, name)
        doc = run_rig(
            self.repo,
            "doctor",
            env={"PATH": _stub_path(bins), "RIG_PARENT": "codex", "HOME": str(home)},
        )
        self.assertEqual(doc.returncode, 0, doc.stderr)
        self.assertRegex(doc.stdout, r"opencode: unavailable \(.*MCP missing")
        self.assertRegex(doc.stdout, r"omp: unavailable \(.*MCP missing")
        self.assertRegex(doc.stdout, r"pi: unavailable \(.*MCP missing")
        self.assertRegex(doc.stdout, r"agy: unavailable \(.*MCP missing")
        self.assertRegex(doc.stdout, r"cursor: excluded")
        self.assertIn("/rig /queue in Grok, Codex, OpenCode, OMP, Pi, or agy", doc.stdout)
        self.assertIn(".config/opencode/skill/delegate-harness", doc.stdout)
        self.assertIn(".omp/agent/skills/delegate-harness", doc.stdout)
        self.assertIn(".pi/agent/skills/delegate-harness", doc.stdout)
        self.assertIn(".gemini/antigravity-cli/skills/delegate-harness", doc.stdout)

    def test_doctor_reports_json_mcp_present(self):
        sys.path.insert(0, str(ROOT / "tests"))
        from mcp_test_support import seed_installed_mcp

        proc = run_rig(self.repo, "init", env={"PATH": _stub_path()})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        home = self.repo / "mcp-home"
        home.mkdir()
        bins = self.repo / "mcp-bins-ready"
        bins.mkdir()
        for name in ("opencode", "omp", "pi", "agy"):
            _fake_bin(bins, name)
        launcher = home / "rig-mcp.sh"
        seed_installed_mcp(home, launcher=launcher)
        # Invalid/disabled config must not report ready.
        bad = home / ".config" / "opencode" / "opencode-disabled.json"
        self.assertFalse(bad.exists())
        doc = run_rig(
            self.repo,
            "doctor",
            env={"PATH": _stub_path(bins), "RIG_PARENT": "codex", "HOME": str(home)},
        )
        self.assertEqual(doc.returncode, 0, doc.stderr)
        self.assertRegex(doc.stdout, r"opencode: ready \(mcp\.rig")
        self.assertRegex(doc.stdout, r"omp: ready \(mcpServers\.rig")
        self.assertRegex(doc.stdout, r"pi: ready \(mcpServers\.rig")
        self.assertRegex(doc.stdout, r"agy: ready \(mcpServers\.rig")
        self.assertNotIn("pi MCP adapter missing", doc.stdout)
        self.assertRegex(doc.stdout, r"cursor: excluded")

    def test_doctor_pi_adapter_present_skips_hint(self):
        proc = run_rig(self.repo, "init", env={"PATH": _stub_path()})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        home = self.repo / "pi-home"
        settings = home / ".pi" / "agent" / "settings.json"
        settings.parent.mkdir(parents=True)
        settings.write_text(json.dumps({"packages": ["pi-mcp-adapter"]}))
        mcp = home / ".pi" / "agent" / "mcp.json"
        mcp.write_text(
            json.dumps({"mcpServers": {"rig": {"command": "/tmp/rig-mcp.sh"}}})
        )
        doc = run_rig(
            self.repo,
            "doctor",
            env={"PATH": _stub_path(), "RIG_PARENT": "codex", "HOME": str(home)},
        )
        self.assertEqual(doc.returncode, 0, doc.stderr)
        self.assertNotIn("pi MCP adapter missing", doc.stdout)

    def test_setup_writes_user_mcp_not_project(self):
        run_rig(self.repo, "init", env={"PATH": _stub_path()})
        home = self.repo / "setup-home"
        home.mkdir()
        proc = run_rig(
            self.repo,
            "setup",
            env={
                "PATH": _stub_path(),
                "HOME": str(home),
                "RIG_HOME": str(home / ".rig"),
                "RIG_SRC": str(ROOT),
            },
        )
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        oc = home / ".config" / "opencode" / "opencode.json"
        omp = home / ".omp" / "mcp.json"
        pi = home / ".pi" / "agent" / "mcp.json"
        agy = home / ".gemini" / "config" / "mcp_config.json"
        self.assertTrue(oc.is_file(), proc.stdout)
        self.assertTrue(omp.is_file(), proc.stdout)
        self.assertTrue(pi.is_file(), proc.stdout)
        self.assertTrue(agy.is_file(), proc.stdout)
        self.assertIn("rig-mcp", oc.read_text())
        self.assertIn("rig-mcp", omp.read_text())
        self.assertIn("rig-mcp", pi.read_text())
        self.assertIn("rig-mcp", agy.read_text())
        self.assertFalse((self.repo / "mcp.json").exists())
        self.assertFalse((self.repo / "opencode.json").exists())
        self.assertFalse((self.repo / ".mcp.json").exists())
        skill = home / ".config" / "opencode" / "skill" / "delegate-harness"
        self.assertTrue(skill.is_symlink() or skill.is_dir(), proc.stdout)
        self.assertTrue((home / ".omp" / "agent" / "skills" / "rig-jobs").exists())
        self.assertTrue((home / ".pi" / "agent" / "skills" / "delegate-harness").exists())
        self.assertTrue((home / ".gemini" / "antigravity-cli" / "skills" / "delegate-harness").exists())
        self.assertTrue((home / ".grok" / "skills" / "rig-queue").exists())
        self.assertTrue((home / ".grok" / "skills" / "computer-use").exists())
        self.assertTrue((home / ".grok" / "skills" / "style-guide").exists())
        self.assertTrue((home / ".grok" / "skills" / "computer-test").exists())
        self.assertTrue((home / ".codex" / "skills" / "rig-queue").exists())
        self.assertTrue((home / ".codex" / "skills" / "computer-use").exists())
        self.assertTrue((home / ".codex" / "skills" / "style-guide").exists())
        self.assertTrue((home / ".codex" / "skills" / "computer-test").exists())
        self.assertTrue((home / ".config" / "opencode" / "skill" / "rig-queue").exists())
        self.assertTrue((home / ".codex" / "prompts" / "queue.md").is_file())
        self.assertTrue((home / ".config" / "opencode" / "commands" / "queue.md").is_file())
        hook = home / ".grok" / "hooks" / "rig-queue-submit.json"
        self.assertTrue(hook.is_file(), proc.stdout)
        self.assertIn("queue_submit_hook", hook.read_text())
        self.assertIn("UserPromptSubmit", hook.read_text())
        prompt = (home / ".codex" / "prompts" / "queue.md").read_text()
        self.assertIn("rig queue", prompt)
        self.assertIn("Do not spawn", prompt)
        codex_hook = home / ".codex" / "hooks.json"
        self.assertTrue(codex_hook.is_file(), proc.stdout)
        self.assertIn("queue_submit_hook", codex_hook.read_text())
        self.assertIn("UserPromptSubmit", codex_hook.read_text())
        codex_cfg = (home / ".codex" / "config.toml").read_text()
        self.assertIn("hooks = true", codex_cfg)
        self.assertNotIn("codex_hooks", codex_cfg)
        oc_plugin = home / ".config" / "opencode" / "plugins" / "rig-queue.js"
        self.assertTrue(oc_plugin.is_file(), proc.stdout)
        self.assertIn("queue_submit_hook", oc_plugin.read_text())
        omp_ext = home / ".omp" / "agent" / "extensions" / "rig-queue.js"
        self.assertTrue(omp_ext.is_file(), proc.stdout)
        self.assertIn("registerCommand", omp_ext.read_text())
        pi_ext = home / ".pi" / "agent" / "extensions" / "rig-queue.js"
        self.assertTrue(pi_ext.is_file(), proc.stdout)
        self.assertIn("registerCommand", pi_ext.read_text())
        self.assertIn("setWidget", omp_ext.read_text())
        tui_hud = home / ".config" / "opencode" / "tui-plugins" / "rig-hud.tsx"
        self.assertTrue(tui_hud.is_file(), proc.stdout)
        self.assertIn("rig-hud", (home / ".config" / "opencode" / "tui.json").read_text())
        self.assertTrue((home / ".agents" / "plugins" / "marketplace.json").is_file(), proc.stdout)
        self.assertIn("rig-queue", (home / ".agents" / "plugins" / "marketplace.json").read_text())
        self.assertTrue(
            (home / ".agents" / "plugins" / "rig-queue" / ".codex-plugin" / "plugin.json").is_file(),
            proc.stdout,
        )
        agy_settings = home / ".gemini" / "antigravity-cli" / "settings.json"
        self.assertTrue(agy_settings.is_file(), proc.stdout)
        self.assertIn("statusLine", agy_settings.read_text())

    def test_new_init_harness_has_no_parent_profile(self):
        proc = run_rig(self.repo, "init", env={"PATH": _stub_path()})
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        text = (self.repo / ".rig" / "harness.toml").read_text()
        self.assertNotIn("[parent]", text)
        self.assertNotRegex(text, r"profile\s*=")

    def test_init_leaves_existing_parent_profile_section(self):
        rig_dir = self.repo / ".rig"
        rig_dir.mkdir()
        original = (
            'parent = "codex"\n'
            "\n"
            "[parent]\n"
            'profile = "sol"\n'
            "\n"
            "[workers]\n"
            "codex = false\n"
            "grok = true\n"
            "claude = false\n"
            "cursor = false\n"
            "opencode = false\n"
            "omp = false\n"
            "pi = false\n"
            "agy = false\n"
            "\n"
            "[queue]\n"
            "max_running = 3\n"
            "\n"
            "[computer-use]\n"
            "enabled = false\n"
            "\n"
            "[browser-skill]\n"
            "enabled = false\n"
        )
        path = rig_dir / "harness.toml"
        path.write_text(original)
        proc = run_rig(self.repo, "init", env={"PATH": _stub_path()})
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        self.assertEqual(path.read_text(), original)

    def test_parent_sol_astra_exit_2_and_do_not_write(self):
        proc = run_rig(self.repo, "init", env={"PATH": _stub_path()})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        path = self.repo / ".rig" / "harness.toml"
        before = path.read_text()
        for who in ("sol", "astra"):
            gone = run_rig(self.repo, "parent", who, env={"PATH": _stub_path()})
            self.assertEqual(gone.returncode, 2, gone.stdout + gone.stderr)
            combined = gone.stdout + gone.stderr
            self.assertIn("gone", combined)
            self.assertIn("CLI", combined)
            self.assertIn("rig pick", combined)
            self.assertEqual(path.read_text(), before)
            self.assertNotRegex(path.read_text(), r"profile\s*=")

    def test_status_and_doctor_omit_profile(self):
        rig_dir = self.repo / ".rig"
        rig_dir.mkdir()
        (rig_dir / "harness.toml").write_text(
            'parent = "codex"\n\n[parent]\nprofile = "sol"\n\n[workers]\n'
            "codex = false\ngrok = true\nclaude = false\ncursor = false\n"
            "opencode = false\nomp = false\npi = false\nagy = false\n"
        )
        status = run_rig(
            self.repo, "status", env={"PATH": _stub_path(), "RIG_PARENT": "codex"}
        )
        self.assertEqual(status.returncode, 0, status.stderr)
        self.assertNotIn("profile=", status.stdout)
        self.assertRegex(status.stdout, r"parent live=\S+ preferred=codex")
        doc = run_rig(
            self.repo, "doctor", env={"PATH": _stub_path(), "RIG_PARENT": "codex"}
        )
        self.assertEqual(doc.returncode, 0, doc.stderr)
        self.assertNotIn("profile=", doc.stdout)
        self.assertNotRegex(doc.stdout, r"(?m)^\s*profile:")

    def test_init_syncs_all_kit_skills(self):
        proc = run_rig(self.repo, "init", env={"PATH": _stub_path()})
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        skills = self.repo / ".agents" / "skills"
        for name in (
            "delegate-harness",
            "rig-jobs",
            "rig-queue",
            "computer-use",
            "computer-test",
            "style-guide",
        ):
            self.assertTrue((skills / name / "SKILL.md").is_file(), name)
        self.assertTrue(
            (skills / "computer-use" / "references" / "desktop-drive.md").is_file()
        )

    def test_init_backfills_computer_use_on_existing_harness(self):
        rig_dir = self.repo / ".rig"
        rig_dir.mkdir()
        (rig_dir / "harness.toml").write_text(
            'parent = "codex"\n\n[workers]\ncodex = true\ngrok = false\n'
        )
        proc = run_rig(self.repo, "init", env={"PATH": _stub_path()})
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        text = (rig_dir / "harness.toml").read_text()
        self.assertRegex(text, r"\[computer-use\]")
        self.assertRegex(text, r"enabled\s*=\s*false")
        self.assertRegex(text, r"\[browser-skill\]")
        self.assertIn('parent = "codex"', text)

    def test_init_preserves_existing_computer_use_flag(self):
        rig_dir = self.repo / ".rig"
        rig_dir.mkdir()
        (rig_dir / "harness.toml").write_text(
            'parent = "codex"\n\n[workers]\ncodex = true\ngrok = false\n\n'
            "[computer-use]\nenabled = true\n"
        )
        proc = run_rig(self.repo, "init", env={"PATH": _stub_path()})
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        text = (rig_dir / "harness.toml").read_text()
        self.assertRegex(text, r"enabled\s*=\s*true")
        section = text.split("[computer-use]", 1)[1]
        next_section = section.find("\n[")
        if next_section != -1:
            section = section[:next_section]
        self.assertNotRegex(section, r"enabled\s*=\s*false")

    def test_init_keeps_project_own_skills(self):
        own = self.repo / ".agents" / "skills" / "my-own"
        own.mkdir(parents=True)
        sentinel = "project-owned skill sentinel\n"
        (own / "SKILL.md").write_text(sentinel)
        proc = run_rig(self.repo, "init", env={"PATH": _stub_path()})
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        self.assertEqual((own / "SKILL.md").read_text(), sentinel)


class CliWorkflow(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.repo = Path(self.td.name)
        (self.repo / ".git").mkdir()
        proc = run_rig(self.repo, "init")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        (self.repo / "a.py").write_text("a\n")
        (self.repo / "b.py").write_text("b\n")

    def tearDown(self):
        self.td.cleanup()

    def _spec(self, nodes=None):
        return {
            "title": "cli-wf",
            "case": "implement a.py",
            "nodes": nodes or [{"id": "w1", "role": "implement", "files": ["a.py"]}],
        }

    def test_help_lists_workflow_commands(self):
        help_out = run_rig(self.repo, "-h")
        self.assertEqual(help_out.returncode, 2)
        self.assertIn("workflows", help_out.stdout)
        self.assertIn("workflow create", help_out.stdout)
        self.assertIn("workflow show|advance|wait|extend|resolve|approve|cancel|report", help_out.stdout)
        self.assertIn("explore|mini|bulk|implement|hard|review|verify|stay", help_out.stdout)

    def test_pick_help_lists_verify_role(self):
        pick_help = run_rig(self.repo, "pick", "--help")
        self.assertEqual(pick_help.returncode, 2)
        self.assertIn("explore|mini|bulk|implement|hard|review|verify|stay", pick_help.stderr)

    def test_workflows_create_file_stdin_show_report_cancel_omit_token(self):
        spec_path = self.repo / "wf.json"
        spec_path.write_text(json.dumps(self._spec()))
        created = run_rig(self.repo, "workflow", "create", "--file", str(spec_path), "--json")
        self.assertEqual(created.returncode, 0, created.stderr + created.stdout)
        payload = json.loads(created.stdout)
        creds = json.loads(Path(payload["credentials_path"]).read_text())
        token = creds["owner_token"]
        self.assertNotIn("owner_token", payload)
        self.assertNotIn(token, created.stdout)
        self.assertNotIn(token, created.stderr)
        listed = run_rig(self.repo, "workflows")
        self.assertEqual(listed.returncode, 0, listed.stderr)
        self.assertIn(payload["workflow_id"], listed.stdout)
        self.assertNotIn(token, listed.stdout)
        shown = run_rig(self.repo, "workflow", "show", payload["workflow_id"], "--json")
        self.assertEqual(shown.returncode, 0, shown.stderr)
        self.assertNotIn(token, shown.stdout)
        waited = run_rig(self.repo, "workflow", "wait", payload["workflow_id"], "--timeout", "0")
        self.assertIn(waited.returncode, {0, 1, 2, 124, 130}, waited.stderr + waited.stdout)
        reported = run_rig(self.repo, "workflow", "report", payload["workflow_id"])
        self.assertEqual(reported.returncode, 0, reported.stderr)
        self.assertIn("wall_time_s", reported.stdout)
        cancelled = run_rig(
            self.repo, "workflow", "cancel", payload["workflow_id"], "--json",
            env={"RIG_OWNER_TOKEN": token, "RIG_OWNER_SESSION": creds.get("session_id") or ""},
        )
        self.assertEqual(cancelled.returncode, 0, cancelled.stderr + cancelled.stdout)
        self.assertNotIn(token, cancelled.stdout)
        stdin_create = run_rig(
            self.repo, "workflow", "create", "--json",
            stdin=json.dumps(self._spec([{"id": "w2", "role": "mini", "files": ["b.py"]}])),
        )
        self.assertEqual(stdin_create.returncode, 0, stdin_create.stderr + stdin_create.stdout)
        stdin_payload = json.loads(stdin_create.stdout)
        self.assertIn("credentials_path", stdin_payload)
        self.assertNotIn("owner_token", stdin_payload)
        json_list = run_rig(self.repo, "workflows", "--json")
        self.assertEqual(json_list.returncode, 0, json_list.stderr)
        rows = json.loads(json_list.stdout)
        self.assertTrue(any(row["workflow_id"] == stdin_payload["workflow_id"] for row in rows))

    def test_workflow_extend_and_missing_id(self):
        spec_path = self.repo / "wf.json"
        spec_path.write_text(json.dumps(self._spec()))
        created = run_rig(self.repo, "workflow", "create", "--file", str(spec_path), "--json")
        payload = json.loads(created.stdout)
        creds = json.loads(Path(payload["credentials_path"]).read_text())
        nodes = json.dumps([{"id": "extra", "role": "review", "files": ["b.py"], "depends_on": ["w1"]}])
        extended = run_rig(
            self.repo, "workflow", "extend", payload["workflow_id"], "--nodes-json", nodes, "--json",
            env={"RIG_OWNER_TOKEN": creds["owner_token"]},
        )
        self.assertEqual(extended.returncode, 0, extended.stderr + extended.stdout)
        self.assertIn("extra", extended.stdout)
        self.assertNotIn(creds["owner_token"], extended.stdout)
        missing = run_rig(self.repo, "workflow", "show")
        self.assertEqual(missing.returncode, 1)
        self.assertIn("workflow id", missing.stderr)

    def test_help_lists_computer_use(self):
        help_out = run_rig(self.repo, "-h")
        self.assertEqual(help_out.returncode, 2)
        self.assertIn("computer-use", help_out.stdout)
        self.assertIn("--cua-driver", help_out.stdout)
        self.assertIn("browser-skill", help_out.stdout)
        self.assertIn("--browser-skill", help_out.stdout)


class ComputerUseCli(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.repo = Path(self.td.name) / "repo"
        self.home = Path(self.td.name) / "home"
        self.bins = Path(self.td.name) / "bins"
        self.repo.mkdir()
        self.home.mkdir()
        self.bins.mkdir()
        (self.repo / ".git").mkdir()
        proc = run_rig(self.repo, "init", env={"PATH": _stub_path()})
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def tearDown(self):
        self.td.cleanup()

    def _env(self, **extra):
        env = {
            "HOME": str(self.home),
            "RIG_HOME": str(self.home / ".rig"),
            "RIG_SRC": str(ROOT),
            "PATH": _stub_path(self.bins),
            "RIG_SKIP_CUA_DRIVER": "1",
        }
        env.update(extra)
        return env

    def _fake_driver(self):
        path = self.bins / "cua-driver"
        path.write_text(
            "#!/bin/sh\n"
            'if [ "$1" = "--version" ]; then echo "cua-driver 0.28.0"; exit 0; fi\n'
            'if [ "$1" = "doctor" ]; then echo "ok"; exit 0; fi\n'
            'if [ "$1" = "skills" ]; then echo "skills installed"; exit 0; fi\n'
            "exit 0\n"
        )
        path.chmod(0o755)
        return path

    def test_init_defaults_computer_use_off(self):
        text = (self.repo / ".rig" / "harness.toml").read_text()
        self.assertRegex(text, r"\[computer-use\]")
        self.assertRegex(text, r"enabled\s*=\s*false")
        shown = run_rig(self.repo, "computer-use", env=self._env())
        self.assertEqual(shown.returncode, 0, shown.stderr + shown.stdout)
        self.assertIn("enabled=false", shown.stdout)
        self.assertIn("off (project)", shown.stdout)
        self.assertIn("chrome-devtools", shown.stdout)

    def test_on_off_round_trip_does_not_need_binary(self):
        on = run_rig(self.repo, "computer-use", "on", env=self._env())
        self.assertEqual(on.returncode, 0, on.stderr + on.stdout)
        self.assertIn("enabled = true", on.stdout)
        self.assertIn("binary missing", on.stdout)
        text = (self.repo / ".rig" / "harness.toml").read_text()
        self.assertRegex(text, r"enabled\s*=\s*true")
        off = run_rig(self.repo, "computer-use", "off", env=self._env())
        self.assertEqual(off.returncode, 0, off.stderr)
        self.assertRegex((self.repo / ".rig" / "harness.toml").read_text(), r"enabled\s*=\s*false")

    def test_doctor_includes_computer_use_block(self):
        doc = run_rig(self.repo, "doctor", env={**self._env(), "RIG_PARENT": "codex"})
        self.assertEqual(doc.returncode, 0, doc.stderr + doc.stdout)
        self.assertIn("Computer-use", doc.stdout)
        self.assertIn("fallback:", doc.stdout)
        self.assertIn("chrome-devtools", doc.stdout)

    def test_setup_uses_rig_proxy_without_raw_parent_mcp(self):
        self._fake_driver()
        (self.home / ".rig").mkdir()
        (self.home / ".rig" / "cua-driver.json").write_text(
            json.dumps({"schema": 1, "opt_in": True, "source": "test", "updated_at": "2026-09-19T00:00:00Z"})
        )
        proc = run_rig(
            self.repo,
            "computer-use",
            "setup",
            env=self._env(RIG_PARENT="codex"),
        )
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        self.assertFalse((self.home / ".codex" / "config.toml").exists())
        self.assertIn("rig_cu_status", proc.stdout)
        self.assertIn("rig_cu_capture", proc.stdout)
        grok_cfg = self.home / ".grok" / "config.toml"
        if grok_cfg.is_file():
            self.assertNotIn("cua-driver", grok_cfg.read_text())
        self.assertFalse((self.home / ".omp" / "mcp.json").exists())

    def test_setup_uses_rig_proxy_for_grok_parent(self):
        self._fake_driver()
        (self.home / ".rig").mkdir()
        (self.home / ".rig" / "cua-driver.json").write_text(
            json.dumps({"schema": 1, "opt_in": True, "source": "test", "updated_at": "2026-09-19T00:00:00Z"})
        )
        proc = run_rig(
            self.repo,
            "computer-use",
            "setup",
            env=self._env(RIG_PARENT="grok", GROK_HOME=str(self.home / ".grok")),
        )
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        self.assertIn("rig_cu_status", proc.stdout)
        self.assertIn("rig_cu_capture", proc.stdout)
        self.assertNotIn("use cua-driver call", proc.stdout)
        grok_cfg = self.home / ".grok" / "config.toml"
        if grok_cfg.is_file():
            self.assertNotIn("cua-driver", grok_cfg.read_text())
        self.assertFalse((self.home / ".codex" / "config.toml").exists())

    def test_setup_flags_are_accepted_unknown_still_errors(self):
        home = self.home
        skip = self._env()
        skip["RIG_HOME"] = str(home / ".rig")
        ok = run_rig(self.repo, "setup", "--no-cua-driver", env=skip)
        self.assertEqual(ok.returncode, 0, ok.stderr + ok.stdout)
        ok_bsk = run_rig(self.repo, "setup", "--no-browser-skill", env=skip)
        self.assertEqual(ok_bsk.returncode, 0, ok_bsk.stderr + ok_bsk.stdout)
        bad = run_rig(self.repo, "setup", "--bogus", env=skip)
        self.assertEqual(bad.returncode, 2, bad.stderr)
        self.assertIn("unknown flag", bad.stderr)


class BrowserSkillCli(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.repo = Path(self.td.name) / "repo"
        self.home = Path(self.td.name) / "home"
        self.bins = Path(self.td.name) / "bins"
        self.repo.mkdir()
        self.home.mkdir()
        self.bins.mkdir()
        (self.repo / ".git").mkdir()
        proc = run_rig(self.repo, "init", env={"PATH": _stub_path()})
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def tearDown(self):
        self.td.cleanup()

    def _env(self, **extra):
        env = {
            "HOME": str(self.home),
            "RIG_HOME": str(self.home / ".rig"),
            "RIG_SRC": str(ROOT),
            "PATH": _stub_path(self.bins),
            "RIG_SKIP_CUA_DRIVER": "1",
            "RIG_SKIP_BROWSER_SKILL": "1",
        }
        env.update(extra)
        return env

    def _fake_bsk(self):
        path = self.bins / "bsk"
        path.write_text(
            "#!/bin/sh\n"
            'if [ "$1" = "--version" ]; then echo "bsk 0.1.0"; exit 0; fi\n'
            'if [ "$1" = "status" ]; then echo "{\"ok\":true,\"browsers\":[{\"id\":\"chrome\"}]}"; exit 0; fi\n'
            'if [ "$1" = "doctor" ]; then echo "ok"; exit 0; fi\n'
            "exit 0\n"
        )
        path.chmod(0o755)
        return path

    def test_init_defaults_browser_skill_off(self):
        text = (self.repo / ".rig" / "harness.toml").read_text()
        self.assertRegex(text, r"\[browser-skill\]")
        self.assertRegex(text, r"enabled\s*=\s*false")
        shown = run_rig(self.repo, "browser-skill", env=self._env())
        self.assertEqual(shown.returncode, 0, shown.stderr + shown.stdout)
        self.assertIn("enabled=false", shown.stdout)
        self.assertIn("off (project)", shown.stdout)
        self.assertIn("chrome-devtools", shown.stdout)
        self.assertIn("bsk install-skill", shown.stdout)

    def test_on_off_round_trip_does_not_need_binary(self):
        on = run_rig(self.repo, "browser-skill", "on", env=self._env())
        self.assertEqual(on.returncode, 0, on.stderr + on.stdout)
        self.assertIn("enabled = true", on.stdout)
        self.assertIn("binary missing", on.stdout)
        text = (self.repo / ".rig" / "harness.toml").read_text()
        self.assertRegex(text.split("[browser-skill]", 1)[1], r"enabled\s*=\s*true")
        off = run_rig(self.repo, "browser-skill", "off", env=self._env())
        self.assertEqual(off.returncode, 0, off.stderr)
        section = (self.repo / ".rig" / "harness.toml").read_text().split("[browser-skill]", 1)[1]
        self.assertRegex(section, r"enabled\s*=\s*false")

    def test_doctor_includes_browser_skill_block(self):
        doc = run_rig(self.repo, "doctor", env={**self._env(), "RIG_PARENT": "codex"})
        self.assertEqual(doc.returncode, 0, doc.stderr + doc.stdout)
        self.assertIn("Browser-skill", doc.stdout)
        self.assertIn("fallback:", doc.stdout)
        self.assertIn("chrome-devtools", doc.stdout)
        self.assertIn("bsk install-skill", doc.stdout)

    def test_setup_reprints_urls_without_enabling_repo_flag(self):
        self._fake_bsk()
        (self.home / ".rig").mkdir()
        (self.home / ".rig" / "browser-skill.json").write_text(
            json.dumps({"schema": 1, "opt_in": True, "source": "test", "updated_at": "2026-09-19T00:00:00Z"})
        )
        before = (self.repo / ".rig" / "harness.toml").read_text()
        proc = run_rig(
            self.repo,
            "browser-skill",
            "setup",
            env=self._env(RIG_PARENT="codex"),
        )
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        self.assertIn("rig_bsk_status", proc.stdout)
        self.assertIn("chromewebstore.google.com", proc.stdout)
        self.assertIn("Never run bsk install-skill", proc.stdout)
        after = (self.repo / ".rig" / "harness.toml").read_text()
        self.assertEqual(before, after)

    def test_setup_flags_are_accepted(self):
        skip = self._env()
        skip["RIG_HOME"] = str(self.home / ".rig")
        ok = run_rig(self.repo, "setup", "--browser-skill", env={**skip, "RIG_SKIP_BROWSER_SKILL": "1"})
        self.assertEqual(ok.returncode, 0, ok.stderr + ok.stdout)

    def test_missing_installer_diagnostic_is_not_python3(self):
        src = (ROOT / "bin" / "rig").read_text()
        self.assertIn("browser-skill setup: installer missing", src)
        self.assertNotIn(
            "browser-skill setup: Python 3 unavailable; skip. Enable later: rig browser-skill setup",
            src,
        )


if __name__ == "__main__":
    unittest.main()
