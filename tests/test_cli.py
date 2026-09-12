#!/usr/bin/env python3
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RIG = ROOT / "bin" / "rig"


def run_rig(repo: Path, *args: str, env: dict | None = None) -> subprocess.CompletedProcess:
    merged = os.environ.copy()
    merged["RIG_HOME"] = str(ROOT)
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

    def test_init_appends_queue_gitignore_and_keeps_max_running(self):
        gi = self.repo / ".gitignore"
        gi.write_text(".rig/\n")
        harness = self.repo / ".rig" / "harness.toml"
        original = harness.read_text()
        self.assertIn("max_running", original)
        proc = run_rig(self.repo, "init")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn(".rig/queue/", gi.read_text())
        self.assertIn("max_running = 3", harness.read_text())
        harness.write_text('parent = "codex"\n\n[workers]\ngrok = true\n\n[queue]\nmax_running = 1\n')
        again = run_rig(self.repo, "init")
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertIn("max_running = 1", harness.read_text())
        self.assertNotIn("max_running = 3", harness.read_text())
        skill = self.repo / ".agents" / "skills" / "rig-queue" / "SKILL.md"
        self.assertTrue(skill.is_file())
        self.assertIn("Does not spawn", skill.read_text())

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
        self.assertRegex(doc.stdout, r"grok:.*(mcp_servers\.rig|missing)")
        self.assertRegex(doc.stdout, r"codex:.*(mcp_servers\.rig|missing)")
        self.assertRegex(doc.stdout, r"opencode:.*(mcp\.rig|missing)")
        self.assertRegex(doc.stdout, r"omp:.*(mcpServers\.rig|missing)")
        self.assertRegex(doc.stdout, r"pi:.*(mcpServers\.rig|missing)")
        self.assertRegex(doc.stdout, r"agy:.*(mcpServers\.rig|missing)")

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
            proc = run_rig(
                self.repo,
                "job",
                "start",
                "--worker",
                name,
                "--role",
                "implement",
                env={"PATH": _stub_path()},
            )
            self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)

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
        self.assertIn("pins are preferences", text)
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
        self.assertNotIn("'", text.split("<!-- rig:start -->", 1)[1].split("<!-- rig:end -->", 1)[0])

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
        doc = run_rig(
            self.repo,
            "doctor",
            env={"PATH": _stub_path(), "RIG_PARENT": "codex", "HOME": str(home)},
        )
        self.assertEqual(doc.returncode, 0, doc.stderr)
        self.assertRegex(doc.stdout, r"opencode:.*missing")
        self.assertRegex(doc.stdout, r"omp:.*missing")
        self.assertRegex(doc.stdout, r"pi:.*missing")
        self.assertRegex(doc.stdout, r"agy:.*missing")
        self.assertIn("pi install npm:pi-mcp-adapter", doc.stdout)
        self.assertIn("/rig /queue in Grok, Codex, OpenCode, OMP, Pi, or agy", doc.stdout)
        self.assertIn(".config/opencode/skill/delegate-harness", doc.stdout)
        self.assertIn(".omp/agent/skills/delegate-harness", doc.stdout)
        self.assertIn(".pi/agent/skills/delegate-harness", doc.stdout)
        self.assertIn(".gemini/antigravity-cli/skills/delegate-harness", doc.stdout)

    def test_doctor_reports_json_mcp_present(self):
        proc = run_rig(self.repo, "init", env={"PATH": _stub_path()})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        home = self.repo / "mcp-home"
        oc = home / ".config" / "opencode" / "opencode.json"
        oc.parent.mkdir(parents=True)
        launcher = str(home / "rig-mcp.sh")
        oc.write_text(
            json.dumps(
                {
                    "mcp": {
                        "rig": {
                            "type": "local",
                            "command": [launcher],
                            "enabled": True,
                        }
                    }
                }
            )
        )
        omp = home / ".omp" / "mcp.json"
        omp.parent.mkdir(parents=True)
        omp.write_text(json.dumps({"mcpServers": {"rig": {"command": launcher}}}))
        pi = home / ".pi" / "agent" / "mcp.json"
        pi.parent.mkdir(parents=True)
        pi.write_text(json.dumps({"mcpServers": {"rig": {"command": launcher}}}))
        agy = home / ".gemini" / "config" / "mcp_config.json"
        agy.parent.mkdir(parents=True)
        agy.write_text(json.dumps({"mcpServers": {"rig": {"command": launcher}}}))
        doc = run_rig(
            self.repo,
            "doctor",
            env={"PATH": _stub_path(), "RIG_PARENT": "codex", "HOME": str(home)},
        )
        self.assertEqual(doc.returncode, 0, doc.stderr)
        self.assertRegex(doc.stdout, r"opencode:.*mcp\.rig")
        self.assertRegex(doc.stdout, r"omp:.*mcpServers\.rig")
        self.assertRegex(doc.stdout, r"pi:.*mcpServers\.rig")
        self.assertRegex(doc.stdout, r"agy:.*mcpServers\.rig")
        self.assertIn("pi install npm:pi-mcp-adapter", doc.stdout)

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
        self.assertTrue((home / ".codex" / "skills" / "rig-queue").exists())
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
        self.assertIn("codex_hooks = true", (home / ".codex" / "config.toml").read_text())
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


if __name__ == "__main__":
    unittest.main()
