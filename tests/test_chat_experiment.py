#!/usr/bin/env python3
"""EXPERIMENT: headless parent with a Rig-rendered chat. Decoders, sessions, the runner and the API, with stand-in parents."""
import json
import os
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import local_api  # noqa: E402
import rig_chat  # noqa: E402

CLAUDE_STREAM = [
    {"type": "system", "subtype": "init", "session_id": "native-claude-1"},
    {"type": "assistant", "message": {"content": [{"type": "text", "text": "Looking at it."}, {"type": "tool_use", "name": "Read", "input": {"file_path": "/repo/a.txt"}}]}},
    {"type": "user", "message": {"content": [{"type": "tool_result", "content": "one"}]}},
    {"type": "assistant", "message": {"content": [{"type": "text", "text": "It says one."}]}},
    {"type": "result", "subtype": "success", "result": "It says one.", "session_id": "native-claude-1", "is_error": False},
]
CODEX_STREAM = [
    {"type": "thread.started", "thread_id": "native-codex-1"},
    {"type": "item.started", "item": {"type": "command_execution", "command": "cat a.txt"}},
    {"type": "item.completed", "item": {"type": "command_execution", "command": "cat a.txt"}},
    {"type": "item.completed", "item": {"type": "agent_message", "text": "It says one."}},
    {"type": "turn.completed", "usage": {}},
]


def make_repo(root: Path, extra: str = "") -> Path:
    (root / ".git").mkdir(parents=True, exist_ok=True)
    (root / ".rig").mkdir(exist_ok=True)
    (root / ".rig" / "harness.toml").write_text(f'parent = "claude"\n\n[project]\nenabled = true\n\n[workers]\ngrok = true\n{extra}')
    return root


def stand_in(folder: Path, name: str, stream, *, exit_code=0, stderr="", sleep=0, hang_child=False) -> Path:
    """A parent CLI that records how it was called and prints a stream-json event stream."""
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_text(f"""#!{sys.executable}
import json, os, subprocess, sys, time
record = os.environ["STANDIN_RECORD"]
with open(record, "a") as stream:
    stream.write(json.dumps({{"argv": sys.argv[1:], "cwd": os.getcwd(), "parent": os.environ.get("RIG_PARENT"), "owner": os.environ.get("RIG_OWNER_SESSION")}}) + "\\n")
for event in json.loads({json.dumps(json.dumps(stream))}):
    print(json.dumps(event), flush=True)
if {hang_child!r}:
    child = subprocess.Popen(["sleep", "300"])
    open(record + ".pids", "w").write(str(os.getpid()) + " " + str(child.pid))
time.sleep({sleep})
sys.stderr.write({stderr!r})
sys.exit({exit_code})
""")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


class Base(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.base = Path(self.td.name)
        self.repo = make_repo(self.base / "repo").resolve()
        self.bin = self.base / "bin"
        self.record = self.base / "record.jsonl"
        self.env = mock.patch.dict(os.environ, {"PATH": f"{self.bin}{os.pathsep}/usr/bin{os.pathsep}/bin", "STANDIN_RECORD": str(self.record),
                                                "RIG_EXPERIMENT_CHAT": "1"})
        self.env.start()
        for key in ("RIG_JOB_ID", "RIG_JOB_DIR"):
            os.environ.pop(key, None)

    def tearDown(self):
        for row in rig_chat.sessions(self.repo) if rig_chat.chat_dir(self.repo).is_dir() else []:
            if row["state"] == "running":
                rig_chat.cancel(self.repo, row["id"])
        self.env.stop()
        self.td.cleanup()

    def calls(self, *, server_attempts=False):
        """The recorded parent calls; a codex stand-in that only knows exec is first tried as `codex mcp-server`."""
        rows = [json.loads(line) for line in self.record.read_text().splitlines()] if self.record.exists() else []
        return rows if server_attempts else [row for row in rows if row["argv"] != ["mcp-server"]]

    def wait_idle(self, session, timeout=20):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            view = rig_chat.transcript(self.repo, session)
            if view["state"] != "running":
                return view
            time.sleep(0.05)
        self.fail("the turn did not finish")


class Flag(Base):
    def test_off_by_default_and_exact_true_only(self):
        with mock.patch.dict(os.environ):
            os.environ.pop("RIG_EXPERIMENT_CHAT")
            self.assertFalse(rig_chat.enabled(self.repo))
            for value in ("1", "yes", "True", "false", "0"):
                os.environ["RIG_EXPERIMENT_CHAT"] = value
                self.assertEqual(rig_chat.enabled(self.repo), value == "1", value)
            os.environ.pop("RIG_EXPERIMENT_CHAT")
            (self.repo / ".rig" / "harness.toml").write_text('[project]\nenabled = true\n\n[experiments]\nchat = true\n')
            self.assertTrue(rig_chat.enabled(self.repo))
            (self.repo / ".rig" / "harness.toml").write_text('[project]\nenabled = true\n\n[experiments]\nchat = "true"\n')
            self.assertTrue(rig_chat.enabled(self.repo), "quoted true is accepted everywhere in harness.toml")
            for typo in ("yes", "TRUE", "1", "on"):
                (self.repo / ".rig" / "harness.toml").write_text(f'[project]\nenabled = true\n\n[experiments]\nchat = {typo}\n')
                self.assertFalse(rig_chat.enabled(self.repo), typo)

    def test_every_entry_point_refuses_while_off_and_says_how_to_enable(self):
        os.environ["RIG_EXPERIMENT_CHAT"] = "0"
        stand_in(self.bin, "claude", CLAUDE_STREAM)
        for call in (lambda: rig_chat.send(self.repo, "hi"), lambda: rig_chat.transcript(self.repo, "c-00000000"),
                     lambda: rig_chat.cancel(self.repo, "c-00000000")):
            with self.assertRaisesRegex(rig_chat.ChatError, "experiment is off; set RIG_EXPERIMENT_CHAT=1"):
                call()
        self.assertEqual(self.calls(), [], "no parent was started")
        self.assertFalse(rig_chat.status(self.repo)["enabled"])
        self.assertEqual(rig_chat.status(self.repo)["sessions"], [])


class Decoding(unittest.TestCase):
    def run_stream(self, parent, stream):
        entries, native, done, error = [], "", False, False
        for event in stream:
            d = rig_chat.decode_event(parent, event)
            entries += d["entries"]
            native = d["native"] or native
            done, error = done or d["done"], error or d["error"]
        return entries, native, done, error

    def test_claude_stream(self):
        entries, native, done, error = self.run_stream("claude", CLAUDE_STREAM)
        self.assertEqual([(e["role"], e["text"]) for e in entries],
                         [("assistant", "Looking at it."), ("tool", "Read /repo/a.txt"), ("result", "one"), ("assistant", "It says one.")])
        self.assertEqual((native, done, error), ("native-claude-1", True, False))

    def test_codex_stream(self):
        entries, native, done, error = self.run_stream("codex", CODEX_STREAM)
        self.assertEqual([(e["role"], e["text"]) for e in entries], [("tool", "ran cat a.txt"), ("assistant", "It says one.")])
        self.assertEqual((native, done, error), ("native-codex-1", True, False))

    def test_error_results(self):
        _, _, done, error = self.run_stream("claude", [{"type": "result", "subtype": "error_during_execution", "is_error": True, "result": "boom"}])
        self.assertEqual((done, error), (True, True))
        entries, _, done, error = self.run_stream("codex", [{"type": "turn.failed", "error": {"message": "rate limited"}}])
        self.assertEqual((entries[0]["text"], done, error), ("rate limited", True, True))
        entries, *_ = self.run_stream("codex", [{"type": "error", "message": "bad"}])
        self.assertEqual(entries[0]["text"], "bad")

    def test_junk_events_are_ignored_not_fatal(self):
        for parent in ("claude", "codex"):
            for junk in (None, 5, "x", [], {}, {"type": 3}, {"type": "assistant", "message": None},
                         {"type": "assistant", "message": {"content": "text"}}, {"type": "item.completed", "item": None}):
                self.assertEqual(rig_chat.decode_event(parent, junk)["entries"], [], (parent, junk))

    def test_text_is_cleaned_and_bounded(self):
        out = rig_chat.decode_event("claude", {"type": "assistant", "message": {"content": [{"type": "text", "text": "a\x1b[2Jb\x00c" + "x" * 9000}]}})
        text = out["entries"][0]["text"]
        self.assertNotIn("\x1b", text)
        self.assertNotIn("\x00", text)
        self.assertLess(len(text), rig_chat.MAX_ENTRY + 60)
        self.assertIn("more characters", text)

    def test_every_parent_rig_shell_can_run_has_a_headless_mode(self):
        import rig_shell
        self.assertEqual(sorted(rig_chat.HEADLESS), sorted(rig_shell.PANE_PARENTS))
        self.assertEqual(rig_chat.ASKS, {"claude", "codex"})

    def test_the_other_parents_streams_read_like_their_job_logs(self):
        grok = [{"type": "text", "data": "Two writers, "}, {"type": "text", "data": "then a verify node."},
                {"type": "tool_call", "toolName": "Bash", "rawInput": {"command": "npm test"}, "status": "completed"},
                {"type": "result", "result": "Two writers, then a verify node."}]
        entries, native, done, error = self.run_stream("grok", grok)
        self.assertEqual([(e["role"], e["text"]) for e in entries],
                         [("chunk", "Two writers, "), ("chunk", "then a verify node."), ("tool", "Bash npm test"),
                          ("final", "Two writers, then a verify node.")])
        self.assertEqual((done, error), (True, False))
        entries, native, *_ = self.run_stream("opencode", [{"type": "step_start", "sessionID": "ses_9"}, {"type": "text", "part": {"text": "done"}}])
        self.assertEqual((native, entries[-1]["text"]), ("ses_9", "done"))
        entries, *_ = self.run_stream("pi", [{"type": "error", "message": "no key"}])
        self.assertEqual((entries[0]["role"], entries[0]["text"]), ("error", "no key"))
        for junk in (None, 5, "x", [], {}, {"type": 3}, {"type": "assistant", "message": None}):
            self.assertEqual(rig_chat.decode_event("omp", junk)["entries"], [], junk)


class Turns(Base):
    def test_a_turn_fills_the_transcript_and_a_second_resumes_the_parents_session(self):
        stand_in(self.bin, "claude", CLAUDE_STREAM)
        started = rig_chat.send(self.repo, "what is in a.txt?")
        self.assertEqual((started["state"], started["parent"], started["turn"]), ("running", "claude", 1))
        view = self.wait_idle(started["session"])
        self.assertEqual((view["state"], view["turns"]), ("idle", 1))
        self.assertEqual([(e["role"], e["text"]) for e in view["entries"]],
                         [("user", "what is in a.txt?"), ("assistant", "Looking at it."), ("tool", "Read /repo/a.txt"), ("result", "one"), ("assistant", "It says one.")])
        rig_chat.send(self.repo, "and b.txt?", started["session"])
        self.wait_idle(started["session"])
        first, second = self.calls()
        asks = ["--mcp-config", str(rig_chat.chat_dir(self.repo) / started["session"] / "ask-mcp.json"),
                "--permission-prompt-tool", "mcp__rig-ask__permission_prompt"]
        stream = ["-p", "--output-format", "stream-json", "--verbose", "--include-partial-messages"]
        self.assertEqual(first["argv"], [*stream, *asks, "--", "what is in a.txt?"])
        self.assertEqual(second["argv"], [*stream, *asks, "--resume", "native-claude-1", "--", "and b.txt?"])
        self.assertEqual((first["cwd"], first["parent"], first["owner"]), (str(self.repo), "claude", started["session"]))

    def test_the_answer_streams_in_before_it_joins_the_transcript(self):
        deltas = [{"type": "stream_event", "event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": t}}}
                  for t in ("It says ", "one.")]
        self.assertEqual(rig_chat.decode_event("claude", deltas[0])["entries"], [{"role": "chunk", "text": "It says "}])
        stand_in(self.bin, "claude", [CLAUDE_STREAM[0], *deltas], sleep=3)
        started = rig_chat.send(self.repo, "what is in a.txt?")
        deadline = time.monotonic() + 10
        view = rig_chat.transcript(self.repo, started["session"])
        while view["partial"] != "It says one." and time.monotonic() < deadline:
            time.sleep(0.05)
            view = rig_chat.transcript(self.repo, started["session"])
        self.assertEqual((view["state"], view["partial"]), ("running", "It says one."))
        done = self.wait_idle(started["session"])
        self.assertEqual(([(e["role"], e["text"]) for e in done["entries"]], done["partial"]),
                         ([("user", "what is in a.txt?"), ("assistant", "It says one.")], ""))
        self.assertFalse((rig_chat.chat_dir(self.repo) / started["session"] / "partial.txt").exists())

    def test_a_streamed_answer_is_kept_once_when_the_whole_message_follows(self):
        deltas = [{"type": "stream_event", "event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": t}}}
                  for t in ("It says ", "one.")]
        stand_in(self.bin, "claude", [CLAUDE_STREAM[0], *deltas, CLAUDE_STREAM[3], CLAUDE_STREAM[4]])
        started = rig_chat.send(self.repo, "what is in a.txt?")
        view = self.wait_idle(started["session"])
        self.assertEqual([(e["role"], e["text"]) for e in view["entries"]], [("user", "what is in a.txt?"), ("assistant", "It says one.")])

    def test_codex_as_the_parent(self):
        stand_in(self.bin, "codex", CODEX_STREAM)
        started = rig_chat.send(self.repo, "go", parent="codex")
        self.wait_idle(started["session"])
        rig_chat.send(self.repo, "again", started["session"])
        self.wait_idle(started["session"])
        first, second = self.calls()
        self.assertEqual(first["argv"], ["exec", "--json", "go"])
        self.assertEqual(second["argv"], ["exec", "--json", "resume", "native-codex-1", "again"])

    def test_the_chosen_model_goes_to_the_parents_own_flag(self):
        import ui_settings
        with mock.patch.dict(os.environ, {"RIG_HOME": str(self.base / "rig-home")}):
            ui_settings.save(self.repo, "parent_model.codex", "gpt-5-codex", "all")
            stand_in(self.bin, "codex", CODEX_STREAM)
            started = rig_chat.send(self.repo, "go", parent="codex")
            self.wait_idle(started["session"])
            self.assertEqual(self.calls()[0]["argv"], ["exec", "--json", "-m", "gpt-5-codex", "go"])
            ui_settings.save(self.repo, "parent_model.claude", "sonnet", "all")
            self.assertEqual((rig_chat.status(self.repo)["model"], rig_chat.status(self.repo)["model_chosen"]), ("sonnet", True),
                             "the project's parent is claude here")
        self.assertEqual(rig_chat.argv_claude("hi", "", {"model": "sonnet"})[6:8], ["--model", "sonnet"])
        self.assertNotIn("--model", rig_chat.argv_claude("hi", "", {}))

    def test_the_chosen_reasoning_level_goes_to_each_parents_own_flag(self):
        import ui_settings
        turn = {"effort": "high", "model": "opus", "repo": "/r", "message_file": "/m", "new_id": "x"}
        flags = {"claude": ["--effort", "high"], "codex": ["-c", 'model_reasoning_effort="high"'], "grok": ["--effort", "high"],
                 "opencode": ["--variant", "high"], "omp": ["--thinking", "high"], "pi": ["--thinking", "high"], "agy": ["--effort", "high"]}
        for parent, want in flags.items():
            argv = rig_chat.HEADLESS[parent]("hi", "", turn)
            at = argv.index(want[0])
            self.assertEqual(argv[at:at + 2], want, parent)
            self.assertNotIn(want[0], rig_chat.HEADLESS[parent]("hi", "", {**turn, "effort": ""}), parent + " with none chosen")
        self.assertNotIn("--effort", rig_chat.argv_claude("hi", "", {**turn, "model": "claude-haiku-4-5"}), "haiku's print mode hangs on it")
        with mock.patch.dict(os.environ, {"RIG_HOME": str(self.base / "rig-home")}):
            ui_settings.save(self.repo, "parent_effort.codex", "medium", "all")
            stand_in(self.bin, "codex", CODEX_STREAM)
            started = rig_chat.send(self.repo, "go", parent="codex")
            self.wait_idle(started["session"])
            self.assertEqual(self.calls()[0]["argv"], ["exec", "--json", "-c", 'model_reasoning_effort="medium"', "go"])
            ui_settings.save(self.repo, "parent_effort.claude", "max", "all")
            self.assertEqual(rig_chat.status(self.repo)["effort"], "max", "the project's parent is claude here")

    def test_plan_mode_is_a_flag_where_the_parent_has_one_and_a_note_where_it_does_not(self):
        turn = {"plan": True, "repo": "/r", "message_file": "/m", "new_id": "x"}
        self.assertEqual(rig_chat.argv_claude("hi", "", turn)[rig_chat.argv_claude("hi", "", turn).index("--permission-mode"):][:2], ["--permission-mode", "plan"])
        self.assertIn("--agent", rig_chat.argv_opencode("hi", "", turn))
        self.assertEqual(rig_chat.argv_codex("hi", "native-1", turn)[:5], ["codex", "exec", "--json", "-s", "read-only"])
        self.assertIn("resume", rig_chat.argv_codex("hi", "native-1", turn), "the sandbox goes before the resume subcommand")
        self.assertLess(rig_chat.argv_codex("hi", "n", turn).index("-s"), rig_chat.argv_codex("hi", "n", turn).index("resume"))
        for parent in ("grok", "omp", "pi", "agy"):
            self.assertNotIn("plan", " ".join(rig_chat.HEADLESS[parent]("hi", "", turn)), parent + " has no plan flag; it is told in the message")
        for parent, fn in rig_chat.HEADLESS.items():
            self.assertNotIn("--permission-mode", fn("hi", "", {**turn, "plan": False}))

    def test_slash_plan_switches_plan_mode_for_the_conversation(self):
        stand_in(self.bin, "claude", CLAUDE_STREAM)
        first = rig_chat.send(self.repo, "/plan")
        session = first["session"]
        self.assertEqual((first["state"], first["plan"]), ("idle", True), "no turn runs for a bare /plan")
        view = rig_chat.transcript(self.repo, session)
        self.assertTrue(view["plan"])
        self.assertEqual(view["entries"][-1]["role"], "system")
        self.assertIn("plan mode on", view["entries"][-1]["text"])
        rig_chat.send(self.repo, "/plan add a login throttle", session)
        self.wait_idle(session)
        argv = self.calls()[0]["argv"]
        self.assertIn("plan", argv[argv.index("--permission-mode") + 1], "claude runs with --permission-mode plan")
        self.assertEqual(argv[-1], "add a login throttle", "what is asked is the text after /plan")
        users = [e for e in rig_chat.transcript(self.repo, session)["entries"] if e["role"] == "user"]
        self.assertEqual(users[-1]["text"], "/plan add a login throttle", "the conversation shows what was typed")
        rig_chat.send(self.repo, "and rate limit it too", session)
        self.wait_idle(session)
        self.assertIn("--permission-mode", self.calls()[1]["argv"], "plan mode stays on until it is turned off")
        rig_chat.send(self.repo, "/plan go", session)
        self.wait_idle(session)
        self.assertNotIn("--permission-mode", self.calls()[2]["argv"])
        self.assertEqual(self.calls()[2]["argv"][-1], "Go ahead with the plan.")
        self.assertFalse(rig_chat.transcript(self.repo, session)["plan"])
        off = rig_chat.send(self.repo, "/plan", session)
        self.assertTrue(off["plan"], "a bare /plan toggles")
        self.assertFalse(rig_chat.send(self.repo, "/plan off", session)["plan"])

    def test_cost_context_and_mcp_answer_from_what_the_turns_reported(self):
        done = rig_chat.decode_event("claude", {"type": "result", "subtype": "success", "result": "ok", "session_id": "n1", "total_cost_usd": 0.0421,
                                                "usage": {"input_tokens": 100, "cache_creation_input_tokens": 900, "cache_read_input_tokens": 37000,
                                                          "output_tokens": 2100}})
        self.assertEqual(done["usage"], {"cost": 0.0421, "input": 38000, "output": 2100, "context": 38000})
        codex = rig_chat.decode_event("codex", {"type": "turn.completed", "usage": {"input_tokens": 5000, "cached_input_tokens": 100, "output_tokens": 70}})
        self.assertEqual(codex["usage"]["input"], 5000)
        meta = rig_chat.new_session(self.repo, "claude")
        first = rig_chat.send(self.repo, "/cost", meta["id"])
        self.assertEqual(first["state"], "idle", "no turn runs for /cost")
        self.assertIn("nothing counted yet", rig_chat.transcript(self.repo, meta["id"])["entries"][-1]["text"])
        meta["usage"] = {"cost": 0.0421, "input": 38000, "output": 2100, "context": 38000, "turns": 3}
        rig_chat._write_json(rig_chat._meta_path(self.repo, meta["id"]), meta)
        rig_chat.send(self.repo, "/cost", meta["id"])
        rig_chat.send(self.repo, "/context", meta["id"])
        entries = [e["text"] for e in rig_chat.transcript(self.repo, meta["id"])["entries"] if e["role"] == "system"]
        self.assertEqual(entries[-2], "cost so far: $0.04 · 38.0k tokens in, 2.1k out over 3 turns")
        self.assertEqual(entries[-1], "context: about 38.0k tokens in the last turn of 200.0k (19%)")
        home = self.base / "home"
        home.mkdir(exist_ok=True)
        (home / ".claude.json").write_text(json.dumps({"mcpServers": {"rig": {}, "github": {}}}))
        with mock.patch.dict(os.environ, {"HOME": str(home)}), mock.patch.object(Path, "home", return_value=home):
            rig_chat.send(self.repo, "/mcp", meta["id"])
        self.assertEqual([e["text"] for e in rig_chat.transcript(self.repo, meta["id"])["entries"]][-1], "MCP servers for claude: github, rig")

    def test_init_review_and_security_review_ask_the_parent_in_words_it_can_run_headless(self):
        stand_in(self.bin, "claude", CLAUDE_STREAM)
        started = rig_chat.send(self.repo, "/init")
        self.wait_idle(started["session"])
        sent = self.calls()[0]["argv"][-1]
        self.assertIn("write a CLAUDE.md file", sent)
        rig_chat.send(self.repo, "/review the login change", started["session"])
        self.wait_idle(started["session"])
        sent = self.calls()[1]["argv"][-1]
        self.assertTrue(sent.startswith("Review the current changes") and sent.endswith("Focus: the login change") and "Do not edit" in sent, sent)
        rig_chat.send(self.repo, "/security-review", started["session"])
        self.wait_idle(started["session"])
        self.assertIn("security problems", self.calls()[2]["argv"][-1])
        users = [e["text"] for e in rig_chat.transcript(self.repo, started["session"])["entries"] if e["role"] == "user"]
        self.assertEqual(users, ["/init", "/review the login change", "/security-review"], "the conversation shows what was typed")
        self.assertIn("AGENTS.md", rig_chat._builtin_prompt("codex", "init", ""))

    def test_the_files_a_turn_changed_are_shown_with_what_changed(self):
        if shutil.which("git") is None:
            self.skipTest("no git here")
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        (self.repo / "a.py").write_text("one\ntwo\nthree\n")
        (self.repo / ".gitignore").write_text("ignored.txt\n.rig/\n")
        stand_in(self.bin, "claude", CLAUDE_STREAM)
        script = self.bin / "claude"
        text = script.read_text().replace("import json, os, subprocess, sys, time\n", "import json, os, subprocess, sys, time\n"
            "open('a.py', 'w').write('one\\nTWO\\nthree\\nfour\\n')\nopen('new.txt', 'w').write('hello\\n')\nopen('ignored.txt', 'w').write('x')\n", 1)
        script.write_text(text)
        started = rig_chat.send(self.repo, "change things")
        self.wait_idle(started["session"])
        entries = rig_chat.transcript(self.repo, started["session"])["entries"]
        diffs = [e["text"] for e in entries if e["role"] == "diff"]
        self.assertEqual([d.splitlines()[0] for d in diffs], ["a.py +2 −1", "new.txt +1 −0"], str([(e["role"], e["text"][:40]) for e in entries]))
        self.assertIn("-two", diffs[0])
        self.assertIn("+TWO", diffs[0])
        self.assertIn("+four", diffs[0])
        self.assertNotIn("ignored", "".join(diffs), "ignored files and .rig are not part of it")
        self.assertEqual(entries[-1]["role"], "diff", "after the answer")
        # nothing changed: nothing shown
        stand_in(self.bin, "claude", CLAUDE_STREAM)
        rig_chat.send(self.repo, "just look", started["session"])
        self.wait_idle(started["session"])
        later = rig_chat.transcript(self.repo, started["session"])["entries"]
        self.assertEqual([e["role"] for e in later if e["role"] == "diff"], ["diff", "diff"], "no new ones")
        # the index of the repository is left as it was
        self.assertEqual(subprocess.run(["git", "-C", str(self.repo), "diff", "--cached", "--name-only"], capture_output=True, text=True).stdout, "")

    def _editing_parent(self, body: str):
        stand_in(self.bin, "claude", CLAUDE_STREAM)
        script = self.bin / "claude"
        script.write_text(script.read_text().replace("import json, os, subprocess, sys, time\n", "import json, os, subprocess, sys, time\n" + body, 1))

    def test_rewind_puts_a_turn_back_and_undo_takes_it_back(self):
        if shutil.which("git") is None:
            self.skipTest("no git here")
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        (self.repo / "a.py").write_text("one\n")
        (self.repo / ".gitignore").write_text(".rig/\n")
        self._editing_parent("open('a.py', 'w').write('two\\n')\nopen('new.txt', 'w').write('x\\n')\n")
        first = rig_chat.send(self.repo, "first change")
        session = first["session"]
        self.wait_idle(session)
        self._editing_parent("open('a.py', 'w').write('three\\n')\nopen('b.txt', 'w').write('b\\n')\n")
        rig_chat.send(self.repo, "second change", session)
        view = self.wait_idle(session)
        self.assertEqual([(m["turn"], [f["path"] for f in m["files"]], m["title"]) for m in view["marks"]],
                         [(1, ["a.py", "new.txt"], "first change"), (2, ["a.py", "b.txt"], "second change")])
        dry = rig_chat.rewind(self.repo, session, 2, dry=True)
        self.assertEqual((dry["paths"], dry["edited"]), (["a.py", "b.txt"], []))
        self.assertEqual((self.repo / "a.py").read_text(), "three\n", "a dry run changes nothing")
        # one file, put back as before turn 2
        rig_chat.rewind(self.repo, session, 2, path="a.py")
        self.assertEqual((self.repo / "a.py").read_text(), "two\n")
        self.assertTrue((self.repo / "b.txt").exists())
        self.assertEqual(len(rig_chat.transcript(self.repo, session)["marks"]), 2, "a single file keeps the turns")
        rig_chat.rewind(self.repo, session, 0, undo=True)
        self.assertEqual((self.repo / "a.py").read_text(), "three\n")
        # the whole of turn 1 and after: files it made are removed, the others are as they were
        (self.repo / "a.py").write_text("mine\n")  # edited since
        dry = rig_chat.rewind(self.repo, session, 1, dry=True)
        self.assertEqual(dry["edited"], ["a.py"], "what was edited since is named")
        rig_chat.rewind(self.repo, session, 1)
        self.assertEqual((self.repo / "a.py").read_text(), "one\n")
        self.assertFalse((self.repo / "new.txt").exists() or (self.repo / "b.txt").exists())
        view = rig_chat.transcript(self.repo, session)
        self.assertEqual((view["marks"], view["can_undo"]), ([], True))
        self.assertIn("rewound to before turn 1: 3 files put back", view["entries"][-1]["text"])
        rig_chat.rewind(self.repo, session, 0, undo=True)
        self.assertEqual(((self.repo / "a.py").read_text(), (self.repo / "b.txt").read_text()), ("mine\n", "b\n"), "undo brings back even the later edit")
        self.assertEqual(len(rig_chat.transcript(self.repo, session)["marks"]), 2)
        with self.assertRaisesRegex(rig_chat.ChatError, "nothing to undo"):
            rig_chat.rewind(self.repo, session, 0, undo=True)
        with self.assertRaisesRegex(rig_chat.ChatError, "no turn 9"):
            rig_chat.rewind(self.repo, session, 9)

    def test_rewind_tells_the_parent_once_and_works_as_a_command(self):
        if shutil.which("git") is None:
            self.skipTest("no git here")
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        (self.repo / ".gitignore").write_text(".rig/\n")
        self._editing_parent("open('a.py', 'w').write('two\\n')\n")
        session = rig_chat.send(self.repo, "edit")["session"]
        self.wait_idle(session)
        rig_chat.send(self.repo, "/rewind", session)
        listing = rig_chat.transcript(self.repo, session)["entries"][-1]["text"]
        self.assertTrue(listing.startswith("turns that changed files") and "turn 1: a.py · edit" in listing, listing)
        rig_chat.send(self.repo, "/rewind 1", session)
        self.assertFalse((self.repo / "a.py").exists())
        self.assertEqual(len(self.calls()), 1, "the commands start no turn")
        rig_chat.send(self.repo, "go on", session)
        self.wait_idle(session)
        rig_chat.send(self.repo, "and more", session)
        self.wait_idle(session)
        first, second = self.calls()[1]["argv"][-1], self.calls()[2]["argv"][-1]
        self.assertIn("the user rewound the working tree: a.py", first)
        self.assertNotIn("rewound", second, "told once")
        # a running turn is refused, and a project that is not a repository says so
        with self.assertRaisesRegex(rig_chat.ChatError, "git repository"):
            with mock.patch.object(rig_chat, "_tree", return_value=""):
                rig_chat.rewind(self.repo, session, 1)

    def test_files_named_with_at_go_with_the_message_whole_or_by_lines(self):
        (self.repo / "pkg").mkdir()
        (self.repo / "pkg" / "auth.py").write_text("\n".join(f"line {i}" for i in range(1, 11)) + "\n")
        (self.repo / "bin.dat").write_bytes(b"\0\1\2")
        (self.repo / ".rig" / "secret.txt").write_text("no")
        text = "look at @pkg/auth.py:3-4 and @pkg/auth.py, not @bin.dat, @.rig/secret.txt, @../x, mail me@pkg/auth.py or @nobody."
        out = rig_chat._mentions(self.repo, "codex", text)
        self.assertTrue(out.startswith(text))
        self.assertIn("[File pkg/auth.py, lines 3-4]\n```\nline 3\nline 4\n```", out)
        self.assertEqual(out.count("[File "), 1, "a name is added once, and only files of the project")
        self.assertNotIn("secret", out.split(text, 1)[1])
        whole = rig_chat._mentions(self.repo, "grok", "see @pkg/auth.py.")
        self.assertIn("[File pkg/auth.py]", whole)
        self.assertIn("line 10", whole)
        # claude reads a plain @path itself; a range it cannot, so that one is added
        self.assertEqual(rig_chat._mentions(self.repo, "claude", "see @pkg/auth.py"), "see @pkg/auth.py")
        self.assertIn("lines 9-10", rig_chat._mentions(self.repo, "claude", "see @pkg/auth.py:9-99"))
        self.assertEqual(rig_chat._mentions(self.repo, "codex", "no mentions here"), "no mentions here")

    def test_the_parent_gets_the_lines_asked_for_and_the_transcript_what_was_typed(self):
        (self.repo / "a.py").write_text("one\ntwo\nthree\n")
        stand_in(self.bin, "grok", CLAUDE_STREAM)
        started = rig_chat.send(self.repo, "explain @a.py:2", parent="grok")
        view = self.wait_idle(started["session"])
        sent = (rig_chat.turn_dir(self.repo, started["session"]) / "message.txt").read_text()  # grok takes its message from a file
        self.assertIn("[File a.py, lines 2-2]\n```\ntwo\n```", sent)
        self.assertNotIn("three", sent)
        self.assertEqual([e["text"] for e in view["entries"] if e["role"] == "user"], ["explain @a.py:2"])

    def test_a_fork_copies_the_conversation_up_to_a_turn_and_tells_its_parent_once(self):
        stand_in(self.bin, "claude", CLAUDE_STREAM)
        first = rig_chat.send(self.repo, "first question")["session"]
        self.wait_idle(first)
        rig_chat.send(self.repo, "second question", first)
        self.wait_idle(first)
        with self.assertRaisesRegex(rig_chat.ChatError, "no turn 5"):
            rig_chat.fork(self.repo, first, 5)
        forked = rig_chat.fork(self.repo, first, 1)
        view = rig_chat.transcript(self.repo, forked["session"])
        users = [e["text"] for e in view["entries"] if e["role"] == "user"]
        self.assertEqual((users, view["turns"], forked["forked_from"]), (["first question"], 1, first))
        self.assertIn(f"forked from {first} at turn 1", view["entries"][-1]["text"])
        self.assertEqual(len(rig_chat.transcript(self.repo, first)["entries"]) > len(view["entries"]), True, "the original is untouched")
        rig_chat.send(self.repo, "a different way", forked["session"])
        self.wait_idle(forked["session"])
        rig_chat.send(self.repo, "and on", forked["session"])
        self.wait_idle(forked["session"])
        told, later = self.calls()[2]["argv"][-1], self.calls()[3]["argv"][-1]
        self.assertIn("a fork of an earlier one, at turn 1", told)
        self.assertIn("user: first question", told)
        self.assertNotIn("second question", told, "only what came before the cut")
        self.assertTrue(told.endswith("a different way"))
        self.assertNotIn("fork of an earlier", later, "told once")
        # as a command: the whole conversation, and the caller moves to the new one
        copy = rig_chat.send(self.repo, "/fork", first)
        self.assertNotEqual(copy["session"], first)
        self.assertEqual(copy["turn"], 2)
        self.assertEqual(len(self.calls()), 4, "forking starts no turn")
        with self.assertRaisesRegex(rig_chat.ChatError, "nothing to fork"):
            rig_chat.fork(self.repo, rig_chat.send(self.repo, "/cost")["session"])

    def test_mode_sets_how_much_the_parent_may_do_without_asking(self):
        stand_in(self.bin, "claude", CLAUDE_STREAM)
        started = rig_chat.send(self.repo, "/mode")
        session = started["session"]
        self.assertEqual(started["mode"], "ask")
        self.assertEqual(rig_chat.transcript(self.repo, session)["mode"], "ask")
        seen = [rig_chat.send(self.repo, "/mode next", session)["mode"] for _ in range(4)]
        self.assertEqual(seen, ["edits", "plan", "ask", "edits"], "ask, edits, plan and round again")
        self.assertEqual(rig_chat.transcript(self.repo, session)["entries"][-1]["text"],
                         "mode edits: claude edits files without asking and still asks about commands")
        rig_chat.send(self.repo, "change it", session)
        self.wait_idle(session)
        argv = self.calls()[0]["argv"]
        self.assertEqual(argv[argv.index("--permission-mode") + 1], "acceptEdits")
        rig_chat.send(self.repo, "/plan on", session)
        self.assertEqual(rig_chat.transcript(self.repo, session)["mode"], "plan", "plan wins while it is on")
        rig_chat.send(self.repo, "/plan off", session)
        self.assertEqual(rig_chat.transcript(self.repo, session)["mode"], "edits", "and edits comes back after it")
        with self.assertRaisesRegex(rig_chat.ChatError, "ask, edits or plan"):
            rig_chat.send(self.repo, "/mode yolo", session)
        rig_chat.send(self.repo, "/mode ask", session)
        rig_chat.send(self.repo, "once more", session)
        self.wait_idle(session)
        self.assertNotIn("--permission-mode", self.calls()[1]["argv"])
        self.assertEqual(len(self.calls()), 2, "the commands start no turn")
        stand_in(self.bin, "grok", CLAUDE_STREAM)
        other = rig_chat.send(self.repo, "/mode edits", parent="grok")
        self.assertIn("grok's headless mode does not ask for permission", rig_chat.transcript(self.repo, other["session"])["entries"][-1]["text"])

    def test_search_finds_conversations_by_what_was_said_in_them(self):
        stand_in(self.bin, "claude", CLAUDE_STREAM)
        first = rig_chat.send(self.repo, "please fix the flaky payment test")["session"]
        self.wait_idle(first)
        second = rig_chat.send(self.repo, "rename the config loader")["session"]
        self.wait_idle(second)
        rows = rig_chat.search(self.repo, "PAYMENT flaky")
        self.assertEqual([(r["session"], r["where"]) for r in rows], [(first, "you")])
        self.assertIn("flaky payment test", rows[0]["snippet"])
        self.assertEqual(rows[0]["title"], "please fix the flaky payment test")
        answered = rig_chat.search(self.repo, "it says one")  # what the stand-in parent answers
        self.assertEqual({r["where"] for r in answered}, {"claude"}, [r for r in answered])
        self.assertEqual(rig_chat.search(self.repo, "x"), [], "one letter is not a search")
        self.assertEqual(rig_chat.search(self.repo, "nothing like this"), [])

    def test_export_writes_the_conversation_as_markdown_inside_the_project(self):
        if shutil.which("git") is None:
            self.skipTest("no git here")
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        (self.repo / ".gitignore").write_text(".rig/\n")
        self._editing_parent("open('a.py', 'w').write('two\\n')\n")
        session = rig_chat.send(self.repo, "change a.py")["session"]
        self.wait_idle(session)
        result = rig_chat.send(self.repo, "/export", session)
        self.assertEqual(result["exported"], f".rig/exports/{session}.md")
        text = (self.repo / result["exported"]).read_text()
        self.assertTrue(text.startswith("# change a.py\n"), text[:60])
        self.assertIn("## You\n\nchange a.py", text)
        self.assertIn("**changed a.py +1 −0**\n\n```diff", text)
        self.assertEqual(len(self.calls()), 1, "exporting starts no turn")
        rig_chat.send(self.repo, "/export notes/chat.md", session)
        self.assertTrue((self.repo / "notes" / "chat.md").is_file())
        for bad in ("../out.md", "/tmp/out.md", ".git/x"):
            with self.assertRaisesRegex(rig_chat.ChatError, "inside the project"):
                rig_chat.export(self.repo, session, bad)

    def test_notes_go_with_the_parents_first_message_and_remember_adds_one(self):
        stand_in(self.bin, "claude", CLAUDE_STREAM)
        first = rig_chat.send(self.repo, "/remember never touch vendor/")
        session = first["session"]
        self.assertEqual(first["notes"], 1)
        rig_chat.send(self.repo, "/remember   tests run with   make test ", session)
        self.assertEqual((self.repo / ".rig" / "notes.md").read_text(), "- never touch vendor/\n- tests run with make test\n")
        rig_chat.send(self.repo, "/notes", session)
        self.assertIn("- tests run with make test", rig_chat.transcript(self.repo, session)["entries"][-1]["text"])
        self.assertEqual(len(self.calls()), 0, "the commands start no turn")
        rig_chat.send(self.repo, "first real question", session)
        self.wait_idle(session)
        rig_chat.send(self.repo, "second", session)
        self.wait_idle(session)
        first_sent, second_sent = self.calls()[0]["argv"][-1], self.calls()[1]["argv"][-1]
        self.assertTrue(first_sent.startswith("[Project notes the user keeps in .rig/notes.md]\n- never touch vendor/"), first_sent)
        self.assertTrue(first_sent.endswith("first real question"))
        self.assertNotIn("Project notes", second_sent, "claude resumes its own session: told once")
        # a parent that cannot resume gets them each time; a fork or a compacted successor starts fresh and gets them again
        stand_in(self.bin, "pi", CLAUDE_STREAM)
        other = rig_chat.send(self.repo, "hi", parent="pi")["session"]
        self.wait_idle(other)
        rig_chat.send(self.repo, "again", other)
        self.wait_idle(other)
        for call in self.calls()[2:4]:
            self.assertIn("Project notes", call["argv"][-1])
        with self.assertRaisesRegex(rig_chat.ChatError, "/remember NOTE"):
            rig_chat.send(self.repo, "/remember", session)

    def test_a_long_patch_and_many_files_are_cut_short(self):
        files = rig_chat.turn_changes(self.repo, "", "x")
        self.assertEqual(files, [], "no trees, no changes")
        text = "\n".join(f"+line {i}" for i in range(200))
        import types
        fake = types.SimpleNamespace(stdout="", returncode=0)
        with mock.patch.object(rig_chat.subprocess, "run", side_effect=[
                types.SimpleNamespace(stdout="200\t0\tbig.py\n", returncode=0),
                types.SimpleNamespace(stdout="diff --git a/big.py b/big.py\nnew file mode 100644\nindex 0..1\n--- /dev/null\n+++ b/big.py\n@@ -0,0 +1,200 @@\n" + text + "\n", returncode=0)]):
            files = rig_chat.turn_changes(self.repo, "a" * 40, "b" * 40)
        self.assertEqual(len(files), 1)
        shown = files[0]["text"].splitlines()
        self.assertEqual(len(shown), rig_chat.DIFF_LINES + 1)
        self.assertTrue(shown[-1].startswith("… ") and shown[-1].endswith("more lines"))
        self.assertEqual((files[0]["added"], files[0]["deleted"]), (200, 0))

    def test_status_lists_conversations_with_what_they_were_about_and_the_one_used_last_first(self):
        stand_in(self.bin, "claude", CLAUDE_STREAM)
        first = rig_chat.send(self.repo, "Add rate limiting\nto POST /login")
        self.wait_idle(first["session"])
        second = rig_chat.send(self.repo, "/init")
        self.wait_idle(second["session"])
        rows = rig_chat.status(self.repo)["sessions"]
        self.assertEqual([r["id"] for r in rows], [second["session"], first["session"]])
        self.assertEqual(rows[1]["title"], "Add rate limiting to POST /login", "the first message, on one line")
        self.assertEqual(rows[0]["title"], "/init")
        self.assertGreater(rows[0]["updated"], 0)
        again = rig_chat.send(self.repo, "and a test", first["session"])
        self.wait_idle(first["session"])
        rows = rig_chat.status(self.repo)["sessions"]
        self.assertEqual(rows[0]["id"], first["session"], "using an old conversation again puts it first")
        self.assertEqual(rows[0]["turns"], 2)
        self.assertEqual(again["session"], first["session"])

    def test_compact_starts_a_fresh_conversation_from_the_parents_own_summary(self):
        stand_in(self.bin, "claude", CLAUDE_STREAM)
        first = rig_chat.send(self.repo, "what is in a.txt?")
        old = first["session"]
        self.wait_idle(old)
        compacting = rig_chat.send(self.repo, "/compact the a.txt part", old)
        self.assertEqual(compacting["state"], "running", "a summary turn runs in the conversation that has the context")
        self.wait_idle(old)
        sent = self.calls()[1]["argv"][-1]
        self.assertIn("Write a summary of this conversation", sent)
        self.assertTrue(sent.endswith("Focus on: the a.txt part"))
        view = rig_chat.transcript(self.repo, old)
        successor = view["successor"]
        self.assertRegex(successor, r"c-[0-9a-f]{8}")
        self.assertIn("compacted: continuing in " + successor, view["entries"][-1]["text"])
        users = [e["text"] for e in view["entries"] if e["role"] == "user"]
        self.assertEqual(users[-1], "/compact the a.txt part", "the conversation shows what was typed")
        new = rig_chat.transcript(self.repo, successor)
        self.assertEqual([e["role"] for e in new["entries"]], ["system", "summary"])
        self.assertIn("It says one.", new["entries"][1]["text"])
        self.assertEqual((new["parent"], new["state"], new["turns"]), ("claude", "idle", 0))
        rig_chat.send(self.repo, "and a.txt's neighbour?", successor)
        self.wait_idle(successor)
        carried = self.calls()[2]["argv"][-1]
        self.assertTrue(carried.startswith("[Summary of our earlier conversation, which was compacted]\n"), carried)
        self.assertIn("It says one.", carried)
        self.assertTrue(carried.endswith("[Now]\nand a.txt's neighbour?"))
        rig_chat.send(self.repo, "once more", successor)
        self.wait_idle(successor)
        self.assertEqual(self.calls()[3]["argv"][-1], "once more", "the summary goes with the first message only")
        empty = rig_chat.new_session(self.repo, "claude")
        rig_chat.send(self.repo, "/compact", empty["id"])
        self.assertIn("nothing to compact", rig_chat.transcript(self.repo, empty["id"])["entries"][-1]["text"])

    def test_a_compact_that_fails_changes_nothing(self):
        stand_in(self.bin, "claude", CLAUDE_STREAM)
        first = rig_chat.send(self.repo, "hello")
        self.wait_idle(first["session"])
        stand_in(self.bin, "claude", CLAUDE_STREAM, exit_code=3)
        rig_chat.send(self.repo, "/compact", first["session"])
        view = self.wait_idle(first["session"])
        self.assertEqual(view["successor"], "")
        self.assertIn("compact failed", view["entries"][-1]["text"])

    def test_the_context_is_shown_as_a_percentage_and_warned_about_once(self):
        busy = [dict(e) for e in CLAUDE_STREAM]
        busy[-1] = {**busy[-1], "usage": {"input_tokens": 100, "cache_read_input_tokens": 170000, "output_tokens": 500}}
        stand_in(self.bin, "claude", busy)
        started = rig_chat.send(self.repo, "go")
        view = self.wait_idle(started["session"])
        self.assertEqual(view["context_pct"], 85)
        warnings = [e["text"] for e in view["entries"] if e["role"] == "system" and "context is 85% full" in e["text"]]
        self.assertEqual(len(warnings), 1)
        self.assertIn("/compact", warnings[0])
        rig_chat.send(self.repo, "again", started["session"])
        view = self.wait_idle(started["session"])
        self.assertEqual(len([e for e in view["entries"] if "% full" in e["text"]]), 1, "once, not every turn")
        quiet = rig_chat.new_session(self.repo, "claude")
        self.assertEqual(rig_chat.transcript(self.repo, quiet["id"])["context_pct"], -1, "unknown before a turn")
        self.assertEqual(rig_chat.context_percent({"parent": "codex", "usage": {"context": 9000}}), -1, "only where the window is known")
        self.assertEqual(rig_chat.context_percent({"parent": "claude", "model": "sonnet[1m]", "usage": {"context": 100000}}), 10)

    def test_tools_and_what_they_answered_are_shown(self):
        claude = lambda event: rig_chat.decode_event("claude", event)["entries"]
        use = {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "Bash", "input": {"description": "list", "command": "ls -la\nsecond"}},
            {"type": "tool_use", "name": "Read", "input": {"limit": 5, "file_path": "src/a.ts"}},
            {"type": "tool_use", "name": "TodoWrite", "input": {"todos": [{}, {}, {}]}}]}}
        self.assertEqual([e["text"] for e in claude(use)], ["Bash ls -la", "Read src/a.ts", "TodoWrite 3 items"])
        answer = {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": "total 8\nfile-a\nfile-b"},
            {"type": "tool_result", "tool_use_id": "t2", "is_error": True, "content": [{"type": "text", "text": "ENOENT: no such file"}]},
            {"type": "tool_result", "tool_use_id": "t3", "content": ""}]}}
        self.assertEqual(claude(answer), [{"role": "result", "text": "total 8  (+2 lines)"},
                                          {"role": "result-error", "text": "ENOENT: no such file"},
                                          {"role": "result", "text": "done"}])
        codex = lambda event: rig_chat.decode_event("codex", event)["entries"]
        self.assertEqual(codex({"type": "item.started", "item": {"type": "command_execution", "command": "pytest -q"}}),
                         [{"role": "tool", "text": "ran pytest -q"}])
        self.assertEqual(codex({"type": "item.completed", "item": {"type": "command_execution", "command": "pytest", "exit_code": 1,
                                                                   "aggregated_output": "FAILED a\n1 failed"}}),
                         [{"role": "result-error", "text": "FAILED a  (+1 lines)"}])
        self.assertEqual(codex({"type": "item.completed", "item": {"type": "file_change", "changes": [{"path": "a.py", "kind": "update"},
                                                                                                      {"path": "b.py", "kind": "add"}]}}),
                         [{"role": "tool", "text": "edited a.py, b.py"}])
        self.assertEqual(codex({"type": "item.started", "item": {"type": "mcp_tool_call", "server": "rig", "tool": "rig_session"}}),
                         [{"role": "tool", "text": "rig.rig_session"}])
        self.assertEqual(codex({"type": "item.started", "item": {"type": "web_search", "query": "go embed"}}),
                         [{"role": "tool", "text": "search go embed"}])

    def test_a_parents_own_command_is_expanded_for_its_headless_mode(self):
        home = self.base / "home"
        (home / ".codex/prompts").mkdir(parents=True)
        (home / ".codex/prompts/fix.md").write_text("---\ndescription: fix one test\n---\nFix the test $1.\n")
        stand_in(self.bin, "codex", CODEX_STREAM)
        with mock.patch.dict(os.environ, {"HOME": str(home)}):
            started = rig_chat.send(self.repo, "/prompts:fix login", parent="codex")
            view = self.wait_idle(started["session"])
        self.assertEqual(self.calls()[0]["argv"], ["exec", "--json", "Fix the test login.\n"])
        self.assertEqual(view["entries"][0]["text"], "/prompts:fix login", "the conversation shows what was typed")

    def test_messages_are_data_never_shell_text_or_flags(self):
        stand_in(self.bin, "claude", CLAUDE_STREAM)
        nasty = "--dangerously-skip-permissions; $(touch /tmp/pwned-by-chat) `id` 'q' \"d\"\nsecond line"
        started = rig_chat.send(self.repo, nasty)
        self.wait_idle(started["session"])
        argv = self.calls()[0]["argv"]
        self.assertEqual(argv[-2:], ["--", nasty], "after --, as one element")
        self.assertFalse(Path("/tmp/pwned-by-chat").exists())

    def test_the_message_is_not_in_the_runners_command_line(self):
        stand_in(self.bin, "claude", CLAUDE_STREAM, sleep=30)
        started = rig_chat.send(self.repo, "a secret question")
        pid = rig_chat.read_meta(self.repo, started["session"])["runner_pid"]
        time.sleep(0.5)
        self.assertNotIn(b"a secret question", Path(f"/proc/{pid}/cmdline").read_bytes())

    def test_one_turn_at_a_time_and_cancel_stops_the_whole_tree(self):
        stand_in(self.bin, "claude", CLAUDE_STREAM, hang_child=True, sleep=300)
        started = rig_chat.send(self.repo, "long job")
        deadline = time.monotonic() + 20
        pids = Path(str(self.record) + ".pids")
        while time.monotonic() < deadline and not pids.exists():
            time.sleep(0.05)
        self.assertTrue(pids.exists())
        with self.assertRaisesRegex(rig_chat.ChatError, "still answering"):
            rig_chat.send(self.repo, "another", started["session"])
        agent, child = (int(x) for x in pids.read_text().split())
        result = rig_chat.cancel(self.repo, started["session"])
        self.assertEqual((result["cancelled"], result["state"]), (True, "idle"))
        time.sleep(0.5)
        for pid in (agent, child):
            self.assertFalse(rig_chat._alive(pid), f"{pid} survived the cancel")
        self.assertEqual(rig_chat.transcript(self.repo, started["session"])["entries"][-1]["text"], "cancelled")
        self.assertFalse(rig_chat.cancel(self.repo, started["session"])["cancelled"])

    def test_steer_stops_the_turn_notes_what_it_changed_and_goes_on_from_the_message(self):
        if shutil.which("git") is None:
            self.skipTest("no git here")
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        (self.repo / ".gitignore").write_text(".rig/\n")
        self._editing_parent("open('half.py', 'w').write('half\\n')\n")
        script = self.bin / "claude"
        script.write_text(script.read_text().replace("sys.exit(0)", "sys.exit(0)").replace("time.sleep(0)", "time.sleep(300)"))
        started = rig_chat.send(self.repo, "long job")
        session = started["session"]
        time.sleep(0.6)
        self.assertEqual(rig_chat.transcript(self.repo, session)["state"], "running")
        stand_in(self.bin, "claude", CLAUDE_STREAM)  # the next turn's parent answers at once
        result = rig_chat.steer(self.repo, session, "stop, do it the other way")
        self.assertEqual((result["session"], result["state"], result["turn"]), (session, "running", 2))
        view = self.wait_idle(session)
        texts = [(e["role"], e["text"].split("\n", 1)[0]) for e in view["entries"]]
        self.assertIn(("system", "interrupted"), texts)
        self.assertIn(("diff", "half.py +1 −0"), texts, "what the stopped turn changed is noted: " + str(texts))
        self.assertNotIn("error", [r for r, _ in texts], "a stopped turn is not a failure")
        self.assertEqual([m["turn"] for m in view["marks"]], [1], "and can be rewound")
        sent = self.calls()[-1]["argv"][-1]
        self.assertTrue(sent.startswith("[Note from Rig: the user interrupted") and sent.endswith("stop, do it the other way"), sent)
        self.assertEqual(view["state"], "idle")
        # on an idle session it is a plain send
        rig_chat.steer(self.repo, session, "and now?")
        self.wait_idle(session)
        self.assertNotIn("interrupted", self.calls()[-1]["argv"][-1])

    def test_a_dead_runner_is_reported_failed_not_running_forever(self):
        stand_in(self.bin, "claude", CLAUDE_STREAM, sleep=300)
        started = rig_chat.send(self.repo, "x")
        pid = rig_chat.read_meta(self.repo, started["session"])["runner_pid"]
        time.sleep(0.3)
        os.killpg(pid, signal.SIGKILL)
        time.sleep(0.3)
        view = rig_chat.transcript(self.repo, started["session"])
        self.assertEqual(view["state"], "failed")
        self.assertIn("runner stopped", view["note"])
        rig_chat.send(self.repo, "retry", started["session"])  # a failed session can be used again

    def test_a_failing_parent_is_shown_as_an_error_with_its_stderr(self):
        stand_in(self.bin, "claude", [], exit_code=3, stderr="not logged in\n")
        started = rig_chat.send(self.repo, "hi")
        view = self.wait_idle(started["session"])
        self.assertEqual(view["state"], "failed")
        self.assertIn("status 3: not logged in", view["entries"][-1]["text"])

    def test_refusals_before_anything_runs(self):
        with self.assertRaisesRegex(rig_chat.ChatError, "claude is not on PATH"):
            rig_chat.send(self.repo, "hi")
        stand_in(self.bin, "claude", CLAUDE_STREAM)
        for bad in ("", "   ", "x" * 4001, "a\0b"):
            with self.assertRaisesRegex(rig_chat.ChatError, "1 to 4000"):
                rig_chat.send(self.repo, bad)
        with self.assertRaisesRegex(rig_chat.ChatError, "supports agy, claude, codex, grok, omp, opencode, pi"):
            rig_chat.send(self.repo, "hi", parent="cursor")
        with self.assertRaisesRegex(rig_chat.ChatError, "grok is not on PATH"):
            rig_chat.send(self.repo, "hi", parent="grok")
        with self.assertRaisesRegex(rig_chat.ChatError, "no such chat session"):
            rig_chat.send(self.repo, "hi", "c-deadbeef")
        for bad in ("../x", "c-xyz", "", "c-1234567"):
            with self.assertRaises(rig_chat.ChatError):
                rig_chat.transcript(self.repo, bad)
        self.assertEqual(self.calls(), [])

    def test_the_session_directory_is_private_and_sessions_are_listed_newest_first(self):
        stand_in(self.bin, "claude", CLAUDE_STREAM)
        a = rig_chat.send(self.repo, "one")["session"]
        self.wait_idle(a)
        time.sleep(1.1)
        b = rig_chat.send(self.repo, "two")["session"]
        self.wait_idle(b)
        self.assertEqual(stat.S_IMODE(os.stat(rig_chat.chat_dir(self.repo)).st_mode), 0o700)
        self.assertEqual([row["id"] for row in rig_chat.sessions(self.repo)], [b, a])


ASKING_CLAUDE = r'''#!PYTHON
import json, os, subprocess, sys
args = sys.argv[1:]
config = json.load(open(args[args.index("--mcp-config") + 1]))["mcpServers"]["rig-ask"]
assert args[args.index("--permission-prompt-tool") + 1] == "mcp__rig-ask__permission_prompt"
server = subprocess.Popen([config["command"], *config["args"]], env={**os.environ, **config["env"]},
                          stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
def call(msg):
    server.stdin.write(json.dumps(msg) + "\n"); server.stdin.flush()
    return json.loads(server.stdout.readline())
call({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
print(json.dumps({"type": "system", "subtype": "init", "session_id": "native-ask"}), flush=True)
reply = call({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "permission_prompt",
             "arguments": {"tool_name": "Bash", "input": {"command": "npm i -D supertest"}, "tool_use_id": "t1"}}})
decision = json.loads(reply["result"]["content"][0]["text"])["behavior"]
print(json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "the user said " + decision}]}}), flush=True)
print(json.dumps({"type": "result", "result": "ok", "session_id": "native-ask", "is_error": False}), flush=True)
server.stdin.close()
'''


class Asking(Base):
    def standin(self):
        self.bin.mkdir(parents=True, exist_ok=True)
        path = self.bin / "claude"
        path.write_text(ASKING_CLAUDE.replace("#!PYTHON", "#!" + sys.executable))
        path.chmod(0o755)

    def wait_ask(self, session, timeout=20):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            view = rig_chat.transcript(self.repo, session)
            if view["ask"]:
                return view
            time.sleep(0.05)
        self.fail("the parent never asked")

    def test_claudes_permission_request_waits_in_the_chat_until_the_owner_answers(self):
        self.standin()
        started = rig_chat.send(self.repo, "add supertest")
        view = self.wait_ask(started["session"])
        self.assertTrue(view["asks"])
        self.assertEqual((view["state"], view["ask"]["tool"], view["ask"]["preview"]), ("running", "Bash", "Bash npm i -D supertest"))
        with self.assertRaisesRegex(rig_chat.ChatError, "no longer waiting"):
            rig_chat.answer(self.repo, started["session"], "not-this-one", "allow")
        rig_chat.answer(self.repo, started["session"], view["ask"]["id"], "allow")
        done = self.wait_idle(started["session"])
        texts = [(e["role"], e["text"]) for e in done["entries"]]
        self.assertIn(("system", "allowed: Bash npm i -D supertest"), texts)
        self.assertIn(("assistant", "the user said allow"), texts)
        self.assertIsNone(done["ask"])

    def test_a_denial_reaches_the_parent_and_a_late_answer_is_refused(self):
        self.standin()
        started = rig_chat.send(self.repo, "add supertest")
        ask = self.wait_ask(started["session"])["ask"]
        rig_chat.answer(self.repo, started["session"], ask["id"], "deny")
        done = self.wait_idle(started["session"])
        self.assertIn(("assistant", "the user said deny"), [(e["role"], e["text"]) for e in done["entries"]])
        with self.assertRaises(rig_chat.ChatError):
            rig_chat.answer(self.repo, started["session"], ask["id"], "allow")

    def test_parents_that_cannot_ask_say_so(self):
        stand_in(self.bin, "grok", [{"type": "result", "result": "ok"}])
        started = rig_chat.send(self.repo, "go", parent="grok")
        view = self.wait_idle(started["session"])
        self.assertFalse(view["asks"])
        self.assertIsNone(view["ask"])

    def test_a_codex_without_the_server_mode_falls_back_to_exec_and_says_it_cannot_ask(self):
        stand_in(self.bin, "codex", CODEX_STREAM)
        started = rig_chat.send(self.repo, "go", parent="codex")
        view = self.wait_idle(started["session"])
        self.assertEqual([c["argv"] for c in self.calls(server_attempts=True)], [["mcp-server"], ["exec", "--json", "go"]])
        self.assertIn(("assistant", "It says one."), [(e["role"], e["text"]) for e in view["entries"]])
        self.assertFalse(view["asks"])


CODEX_SERVER = r'''#!PYTHON
import json, os, sys
record = os.environ["STANDIN_RECORD"]
with open(record, "a") as stream:
    stream.write(json.dumps({"argv": sys.argv[1:], "cwd": os.getcwd()}) + "\n")
if sys.argv[1:] != ["mcp-server"]:
    sys.exit(2)
def send(obj):
    print(json.dumps(obj), flush=True)
def read():
    line = sys.stdin.readline()
    return json.loads(line) if line else None
while True:
    msg = read()
    if msg is None:
        break
    if msg.get("method") == "initialize":
        send({"jsonrpc": "2.0", "id": msg["id"], "result": {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}}}})
    elif msg.get("method") == "tools/call":
        name, arguments = msg["params"]["name"], msg["params"]["arguments"]
        with open(record + ".calls", "a") as stream:
            stream.write(json.dumps({"name": name, "arguments": arguments}) + "\n")
        send({"jsonrpc": "2.0", "method": "codex/event", "params": {"msg": {"type": "session_configured", "session_id": "conv-1"}}})
        send({"jsonrpc": "2.0", "method": "codex/event", "params": {"msg": {"type": "exec_command_begin", "command": ["cat", "a.txt"]}}})
        decision = "none"
        if "needs approval" in arguments["prompt"]:
            send({"jsonrpc": "2.0", "id": "e1", "method": "elicitation/create", "params": {
                "message": "Allow codex to run npm i -D supertest?", "codex_elicitation": "exec-approval",
                "codex_command": ["npm", "i", "-D", "supertest"], "codex_call_id": "call-1", "requestedSchema": {}}})
            answer = read()
            decision = answer["result"]["decision"]
        send({"jsonrpc": "2.0", "method": "codex/event", "params": {"msg": {"type": "agent_message_delta", "delta": "decision was "}}})
        send({"jsonrpc": "2.0", "method": "codex/event", "params": {"msg": {"type": "agent_message", "message": "decision was " + decision}}})
        send({"jsonrpc": "2.0", "method": "codex/event", "params": {"msg": {"type": "task_complete"}}})
        send({"jsonrpc": "2.0", "id": msg["id"], "result": {"content": [{"type": "text", "text": "decision was " + decision}],
                                                            "structuredContent": {"threadId": "conv-1"}}})
'''


class CodexServer(Base):
    def standin(self):
        self.bin.mkdir(parents=True, exist_ok=True)
        path = self.bin / "codex"
        path.write_text(CODEX_SERVER.replace("#!PYTHON", "#!" + sys.executable))
        path.chmod(0o755)

    def tool_calls(self):
        path = Path(str(self.record) + ".calls")
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def test_codex_runs_as_its_mcp_server_and_a_second_turn_continues_the_session(self):
        self.standin()
        started = rig_chat.send(self.repo, "what is in a.txt?", parent="codex")
        view = self.wait_idle(started["session"])
        self.assertEqual([(e["role"], e["text"]) for e in view["entries"]],
                         [("user", "what is in a.txt?"), ("tool", "ran cat a.txt"), ("assistant", "decision was none")])
        self.assertTrue(view["asks"])
        rig_chat.send(self.repo, "and again", started["session"])
        self.wait_idle(started["session"])
        first, second = self.tool_calls()
        self.assertEqual((first["name"], first["arguments"]), ("codex", {"prompt": "what is in a.txt?", "cwd": str(self.repo)}))
        self.assertEqual((second["name"], second["arguments"]["conversationId"], second["arguments"]["prompt"]),
                         ("codex-reply", "conv-1", "and again"))

    def test_codexs_approval_request_waits_in_the_chat_and_the_answer_reaches_codex(self):
        self.standin()
        started = rig_chat.send(self.repo, "this needs approval", parent="codex")
        deadline = time.monotonic() + 20
        view = rig_chat.transcript(self.repo, started["session"])
        while not view["ask"] and time.monotonic() < deadline:
            time.sleep(0.05)
            view = rig_chat.transcript(self.repo, started["session"])
        self.assertEqual((view["ask"]["tool"], view["ask"]["preview"]), ("Exec", "npm i -D supertest"))
        rig_chat.answer(self.repo, started["session"], view["ask"]["id"], "deny")
        done = self.wait_idle(started["session"])
        texts = [(e["role"], e["text"]) for e in done["entries"]]
        self.assertIn(("system", "denied: npm i -D supertest"), texts)
        self.assertIn(("assistant", "decision was denied"), texts)


class NoParentSet(Base):
    def setUp(self):
        super().setUp()
        (self.repo / ".rig" / "harness.toml").write_text("[project]\nenabled = true\n")  # `rig parent` is gone; often unset

    def test_the_first_installed_parent_answers_and_status_names_it(self):
        self.assertEqual(rig_chat.status(self.repo)["parent"], "")
        with self.assertRaisesRegex(rig_chat.ChatError, "no parent CLI is installed here"):
            rig_chat.send(self.repo, "hi")
        stand_in(self.bin, "grok", [{"type": "result", "result": "hi"}])
        stand_in(self.bin, "codex", CODEX_STREAM)
        self.assertEqual(rig_chat.status(self.repo)["parent"], "codex", "the preferred order: claude, then codex, ...")
        started = rig_chat.send(self.repo, "hi")
        self.assertEqual(started["parent"], "codex")
        self.wait_idle(started["session"])

    def test_the_project_parent_still_wins(self):
        (self.repo / ".rig" / "harness.toml").write_text('parent = "grok"\n[project]\nenabled = true\n')
        stand_in(self.bin, "codex", CODEX_STREAM)
        self.assertEqual(rig_chat.status(self.repo)["parent"], "grok")


class ChoosingTheParent(Base):
    def test_the_picker_saves_the_parent_as_a_top_level_key(self):
        harness = self.repo / ".rig" / "harness.toml"
        harness.write_text("# my project\n[project]\nenabled = true\n")
        self.assertEqual(rig_chat.status(self.repo)["configured"], "")
        with self.assertRaisesRegex(rig_chat.ChatError, "not on PATH"):
            rig_chat.set_parent(self.repo, "codex")
        with self.assertRaisesRegex(rig_chat.ChatError, "not cursor"):
            rig_chat.set_parent(self.repo, "cursor")
        stand_in(self.bin, "codex", CODEX_STREAM)
        stand_in(self.bin, "grok", [])
        self.assertEqual(rig_chat.set_parent(self.repo, "codex")["parent"], "codex")
        self.assertEqual(harness.read_text(), '# my project\nparent = "codex"\n[project]\nenabled = true\n')
        rig_chat.set_parent(self.repo, "grok")
        self.assertEqual(harness.read_text(), '# my project\nparent = "grok"\n[project]\nenabled = true\n')
        import harness as harness_module
        self.assertEqual(harness_module.parse_harness(harness)["parent"], "grok")
        self.assertTrue(harness_module.parse_harness(harness)["project"]["enabled"])
        self.assertEqual((rig_chat.status(self.repo)["configured"], rig_chat.status(self.repo)["parent"]), ("grok", "grok"))

    def test_the_title_names_the_model_from_the_parents_own_settings(self):
        home = self.base / "home"
        (home / ".claude").mkdir(parents=True)
        (home / ".codex").mkdir()
        with mock.patch.dict(os.environ, {"HOME": str(home)}):
            self.assertEqual(rig_chat.parent_model("claude"), "")
            (home / ".claude" / "settings.json").write_text('{"model": "claude-opus-4-7"}')
            self.assertEqual(rig_chat.parent_model("claude"), "opus")
            (home / ".claude" / "settings.json").write_text("not json")
            self.assertEqual(rig_chat.parent_model("claude"), "")
            (home / ".codex" / "config.toml").write_text('model = "gpt-5.6-sol"\n')
            self.assertEqual(rig_chat.parent_model("codex"), "gpt-5.6-sol")
            self.assertEqual(rig_chat.parent_model("grok"), "")
            stand_in(self.bin, "claude", CLAUDE_STREAM)
            (home / ".claude" / "settings.json").write_text('{"model": "sonnet"}')
            self.assertEqual(rig_chat.status(self.repo)["model"], "sonnet")

    def test_a_parent_key_inside_a_table_is_left_alone(self):
        harness = self.repo / ".rig" / "harness.toml"
        harness.write_text('[project]\nenabled = true\n[other]\nparent = "x"\n')
        stand_in(self.bin, "codex", CODEX_STREAM)
        rig_chat.set_parent(self.repo, "codex")
        self.assertEqual(harness.read_text(), 'parent = "codex"\n[project]\nenabled = true\n[other]\nparent = "x"\n')
        harness.unlink()
        with self.assertRaisesRegex(rig_chat.ChatError, "rig init"):
            rig_chat.set_parent(self.repo, "codex")
        self.assertFalse(harness.exists(), "choosing a parent never creates the harness")


class OtherParents(Base):
    def test_grok_gets_a_session_id_then_resumes_it(self):
        stand_in(self.bin, "grok", [{"type": "text", "data": "hi"}, {"type": "result", "result": "hi"}])
        started = rig_chat.send(self.repo, "hello", parent="grok")
        view = self.wait_idle(started["session"])
        self.assertEqual([(e["role"], e["text"]) for e in view["entries"]], [("user", "hello"), ("assistant", "hi")])
        rig_chat.send(self.repo, "again", started["session"])
        self.wait_idle(started["session"])
        first, second = self.calls()
        given = first["argv"][first["argv"].index("--session-id") + 1]
        self.assertEqual(second["argv"][second["argv"].index("-r") + 1], given)
        prompt = Path(second["argv"][second["argv"].index("--prompt-file") + 1])
        self.assertEqual(prompt.read_text(), "again", "a resuming parent gets only the new message")

    def test_a_parent_that_cannot_resume_gets_the_conversation_so_far(self):
        stand_in(self.bin, "pi", [{"type": "result", "result": "noted"}])
        started = rig_chat.send(self.repo, "the port is 3000", parent="pi")
        self.wait_idle(started["session"])
        rig_chat.send(self.repo, "which port?", started["session"])
        self.wait_idle(started["session"])
        first, second = self.calls()
        self.assertEqual(first["argv"][-1], "the port is 3000")
        self.assertIn("user: the port is 3000", second["argv"][-1])
        self.assertIn("you: noted", second["argv"][-1])
        self.assertTrue(second["argv"][-1].endswith("Now: which port?"))

    def test_another_parent_takes_the_conversation_over_and_each_keeps_its_own_session(self):
        stand_in(self.bin, "claude", CLAUDE_STREAM)
        stand_in(self.bin, "grok", [{"type": "text", "data": "grok here"}, {"type": "result", "result": "grok here"}])
        started = rig_chat.send(self.repo, "what is in a.txt?")
        session = started["session"]
        self.wait_idle(session)
        prompt = lambda call: Path(call["argv"][call["argv"].index("--prompt-file") + 1]).read_text()
        moved = rig_chat.send(self.repo, "now rename it", session, parent="grok")
        self.assertEqual((moved["session"], moved["parent"]), (session, "grok"), "the same conversation goes on")
        self.wait_idle(session)
        handed = prompt(self.calls()[-1])
        rig_chat.send(self.repo, "thanks", session, parent="grok")
        self.wait_idle(session)
        thanked = prompt(self.calls()[-1])
        back = rig_chat.send(self.repo, "check it", session, parent="claude")
        view = self.wait_idle(session)
        first, taken, again, returned = self.calls()
        self.assertTrue(handed.startswith("You are taking this conversation over from claude."), handed)
        self.assertIn("user: what is in a.txt?", handed)
        self.assertIn("claude: It says one.", handed)
        self.assertTrue(handed.endswith("Now: now rename it"))
        self.assertEqual(thanked, "thanks", "told once: then its own session carries it")
        self.assertIn("-r", again["argv"])
        self.assertEqual(returned["argv"][returned["argv"].index("--resume") + 1], "native-claude-1", "claude resumes its own session")
        told = returned["argv"][-1]
        self.assertIn("user: now rename it", told)
        self.assertIn("grok: grok here", told)
        self.assertNotIn("what is in a.txt?", told, "only what was said since it last took part")
        self.assertEqual(back["parent"], "claude")
        rows = [(e["role"], e.get("parent")) for e in view["entries"] if e["role"] in ("assistant", "switch")]
        self.assertEqual(rows[:3], [("assistant", "claude"), ("assistant", "claude"), ("switch", "grok")])
        self.assertIn(("assistant", "grok"), rows)
        self.assertEqual(rows[-1], ("assistant", "claude"))

    def test_an_unknown_stream_is_read_like_that_clis_job_log(self):
        stand_in(self.bin, "agy", [{"response": "All done."}])
        started = rig_chat.send(self.repo, "go", parent="agy")
        view = self.wait_idle(started["session"])
        self.assertIn(("assistant", "All done."), [(e["role"], e["text"]) for e in view["entries"]])


class Api(Base):
    def setUp(self):
        super().setUp()
        self.api = local_api.Api(self.repo, "t" * 64)

    def call(self, method, params=None):
        return self.api.dispatch({"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}, "token": "t" * 64})

    def test_the_methods_and_which_ones_mutate(self):
        rows = {r["method"]: r["mutates"] for r in self.call("api.methods")["result"]["methods"]}
        self.assertEqual([rows[m] for m in ("chat.status", "chat.transcript", "chat.send", "chat.cancel", "chat.answer", "chat.parent")],
                         [False, False, True, True, True, True])

    def test_the_board_saves_the_parent_through_the_api(self):
        stand_in(self.bin, "codex", CODEX_STREAM)
        self.assertEqual(self.call("chat.parent", {"name": "codex"})["result"]["parent"], "codex")
        self.assertIn('parent = "codex"', (self.repo / ".rig" / "harness.toml").read_text())
        self.assertEqual(self.call("chat.parent", {"name": 5})["error"]["code"], local_api.INVALID_PARAMS)
        self.assertEqual(self.call("chat.parent", {"name": "nope"})["error"]["code"], local_api.INVALID_PARAMS)

    def test_send_then_read_the_transcript_through_the_api(self):
        stand_in(self.bin, "claude", CLAUDE_STREAM)
        started = self.call("chat.send", {"message": "hello"})["result"]
        self.wait_idle(started["session"])
        view = self.call("chat.transcript", {"session": started["session"], "tail": 10})["result"]
        self.assertEqual(view["entries"][0]["text"], "hello")
        status = self.call("chat.status")["result"]
        self.assertEqual((status["enabled"], status["sessions"][0]["id"]), (True, started["session"]))
        self.assertEqual(self.call("chat.cancel", {"session": started["session"]})["result"]["cancelled"], False)

    def test_bad_parameters_and_a_disabled_experiment(self):
        for method, params in (("chat.send", {}), ("chat.send", {"message": 5}), ("chat.transcript", {"session": 5}),
                               ("chat.transcript", {"session": "c-00000000", "tail": 0}), ("chat.transcript", {"session": "c-00000000", "tail": True})):
            self.assertEqual(self.call(method, params)["error"]["code"], local_api.INVALID_PARAMS, (method, params))
        os.environ["RIG_EXPERIMENT_CHAT"] = "0"
        stand_in(self.bin, "claude", CLAUDE_STREAM)
        refused = self.call("chat.send", {"message": "hi"})
        self.assertEqual(refused["error"]["code"], local_api.INVALID_PARAMS)
        self.assertIn("experiment is off", refused["error"]["message"])
        self.assertFalse(self.call("chat.status")["result"]["enabled"])
        self.assertEqual(self.calls(), [])


class Cli(Base):
    def run_cli(self, *args):
        return subprocess.run([sys.executable, str(ROOT / "scripts" / "rig_chat.py"), *args, "--repo", str(self.repo)],
                              capture_output=True, text=True, env=dict(os.environ))

    def test_send_wait_prints_the_answer_and_show_lists_it(self):
        stand_in(self.bin, "claude", CLAUDE_STREAM)
        done = self.run_cli("send", "hello", "--wait")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("assistant › It says one.", done.stdout)
        shown = self.run_cli("show")
        self.assertIn("     user › hello", shown.stdout)
        self.assertIn("claude  idle  1 turns", self.run_cli("sessions").stdout)

    def test_off_means_a_clear_refusal(self):
        os.environ["RIG_EXPERIMENT_CHAT"] = "0"
        done = self.run_cli("send", "hello")
        self.assertEqual(done.returncode, 1)
        self.assertIn("experiment is off", done.stderr)

    def test_doctor_names_the_experiment_only_while_it_is_on(self):
        import doctor
        stand_in(self.bin, "claude", CLAUDE_STREAM)
        rows = [c for c in doctor.build_report(self.repo)["checks"] if c["name"] == "chat-experiment"]
        self.assertEqual(len(rows), 1)
        self.assertIn("EXPERIMENT", rows[0]["detail"])
        self.assertIn("not verified against a real parent CLI", rows[0]["detail"])
        os.environ["RIG_EXPERIMENT_CHAT"] = "0"
        self.assertEqual([c for c in doctor.build_report(self.repo)["checks"] if c["name"] == "chat-experiment"], [])


if __name__ == "__main__":
    unittest.main()
