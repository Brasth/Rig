#!/usr/bin/env python3
"""EXPERIMENT: headless parent with a Rig-rendered chat. Decoders, sessions, the runner and the API, with stand-in parents."""
import json
import os
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
                         [("assistant", "Looking at it."), ("tool", "Read /repo/a.txt"), ("assistant", "It says one.")])
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
                         [("user", "what is in a.txt?"), ("assistant", "Looking at it."), ("tool", "Read /repo/a.txt"), ("assistant", "It says one.")])
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
