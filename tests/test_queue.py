#!/usr/bin/env python3
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import ask as rig_ask  # noqa: E402
import harness  # noqa: E402
import jobs  # noqa: E402
import work_queue as rig_queue  # noqa: E402
import rig_mcp  # noqa: E402


def _write_harness(repo: Path, body: str) -> None:
    path = repo / ".rig" / "harness.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)


class QueueFiles(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.repo = Path(self.td.name)
        (self.repo / ".git").mkdir()
        _write_harness(
            self.repo,
            'parent = "codex"\n\n[workers]\ngrok = true\n\n[queue]\nmax_running = 3\n',
        )
        self._env = {k: os.environ.get(k) for k in ("RIG_JOB_ID", "RIG_JOB_DIR", "RIG_THREAD")}
        os.environ.pop("RIG_JOB_ID", None)
        os.environ.pop("RIG_JOB_DIR", None)

    def tearDown(self):
        for key, val in self._env.items():
            if val is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = val
        self.td.cleanup()

    def test_add_list_cancel(self):
        obj = rig_queue.add_item(self.repo, "fix pagination")
        self.assertEqual(obj["status"], "pending")
        self.assertEqual(obj["text"], "fix pagination")
        path = self.repo / ".rig" / "queue" / f"{obj['id']}.json"
        self.assertTrue(path.is_file())
        listed = rig_queue.list_items(self.repo)
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]["id"], obj["id"])
        cancelled = rig_queue.cancel_item(self.repo, obj["id"])
        self.assertEqual(cancelled["status"], "cancelled")
        self.assertEqual(rig_queue.list_items(self.repo), [])

    def test_truncates_long_text(self):
        obj = rig_queue.add_item(self.repo, "x" * 3000)
        self.assertEqual(len(obj["text"]), 2000)
        self.assertTrue(obj["text"].endswith("…"))

    def test_empty_text_rejected(self):
        with self.assertRaises(ValueError):
            rig_queue.add_item(self.repo, "   ")

    def test_cancel_missing(self):
        with self.assertRaises(FileNotFoundError):
            rig_queue.cancel_item(self.repo, "nope")

    def test_max_running_default_and_override(self):
        self.assertEqual(rig_queue.max_running(self.repo), 3)
        _write_harness(
            self.repo,
            'parent = "codex"\n\n[workers]\ngrok = true\n\n[queue]\nmax_running = 1\n',
        )
        self.assertEqual(rig_queue.max_running(self.repo), 1)
        _write_harness(self.repo, 'parent = "codex"\n\n[workers]\ngrok = true\n')
        self.assertEqual(rig_queue.max_running(self.repo), 3)
        self.assertEqual(harness.parse_harness(self.repo / ".rig" / "harness.toml")["queue"]["max_running"], 3)

    def test_format_block(self):
        rig_queue.add_item(self.repo, "fix pagination")
        block = rig_queue.format_block(self.repo, live=0)
        self.assertIn("QUEUE", block)
        self.assertIn("1 pending", block)
        self.assertIn("live 0/3", block)
        self.assertIn("fix pagination", block)

    def test_mcp_parent_add_list_cancel(self):
        added = rig_mcp.call_tool(
            "rig_queue_add",
            {"text": "add tests", "repo": str(self.repo)},
        )
        self.assertNotIn("isError", added)
        text = added["content"][0]["text"]
        self.assertIn("queued ", text)
        listed = rig_mcp.call_tool("rig_queue_list", {"repo": str(self.repo)})
        self.assertIn("add tests", listed["content"][0]["text"])
        item_id = [p.stem for p in (self.repo / ".rig" / "queue").glob("*.json")][0]
        cancelled = rig_mcp.call_tool(
            "rig_queue_cancel",
            {"id": item_id, "repo": str(self.repo)},
        )
        self.assertNotIn("isError", cancelled)
        self.assertIn("cancelled", cancelled["content"][0]["text"])

    def test_child_cannot_call_queue_tools(self):
        os.environ["RIG_JOB_ID"] = "child-q"
        os.environ["RIG_JOB_DIR"] = str(self.repo / ".rig" / "jobs" / "child-q")
        Path(os.environ["RIG_JOB_DIR"]).mkdir(parents=True)
        try:
            names = [t["name"] for t in rig_mcp.listed_tools()]
            self.assertNotIn("rig_queue_add", names)
            blocked = rig_mcp.call_tool(
                "rig_queue_add",
                {"text": "nope", "repo": str(self.repo)},
            )
            self.assertTrue(blocked.get("isError"))
            self.assertIn("not a child tool", blocked["content"][0]["text"])
            blocked_claim = rig_mcp.call_tool(
                "rig_queue_claim",
                {"files": ["a.py"], "repo": str(self.repo)},
            )
            self.assertTrue(blocked_claim.get("isError"))
        finally:
            os.environ.pop("RIG_JOB_ID", None)
            os.environ.pop("RIG_JOB_DIR", None)

    def _live_job(self, job_id: str, files=None, role: str = "implement") -> None:
        job_dir = self.repo / ".rig" / "jobs" / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        meta = {
            "job_id": job_id,
            "worker": "grok",
            "role": role,
            "status": "running",
            "pid": os.getpid(),
            "files": list(files or []),
        }
        (job_dir / "meta.json").write_text(json.dumps(meta) + "\n")

    def test_claim_and_unclaim(self):
        item = rig_queue.add_item(self.repo, "fix pagination")
        claimed = rig_queue.claim_next(self.repo, files=["src/jobs.py"])
        self.assertEqual(claimed["id"], item["id"])
        self.assertEqual(claimed["status"], "claimed")
        self.assertEqual(claimed["files"], ["src/jobs.py"])
        back = rig_queue.unclaim(self.repo, item["id"])
        self.assertEqual(back["status"], "pending")

    def test_claim_needs_id_when_several_pending(self):
        first = rig_queue.add_item(self.repo, "fix a")
        second = rig_queue.add_item(self.repo, "fix b")
        with self.assertRaises(rig_queue.QueueError) as ctx:
            rig_queue.claim_next(self.repo, files=["src/a.py"])
        self.assertIn("needs id", str(ctx.exception))
        self.assertEqual(rig_queue.load_item(self.repo, first["id"])["status"], "pending")
        self.assertEqual(rig_queue.load_item(self.repo, second["id"])["status"], "pending")
        claimed = rig_queue.claim_next(
            self.repo, files=["src/b.py"], item_id=second["id"]
        )
        self.assertEqual(claimed["id"], second["id"])
        self.assertEqual(claimed["files"], ["src/b.py"])
        self.assertEqual(rig_queue.load_item(self.repo, first["id"])["status"], "pending")

    def test_selects_disjoint_subset_leaves_overlap_pending(self):
        item_a = rig_queue.add_item(self.repo, "touch foo a")
        item_b = rig_queue.add_item(self.repo, "touch foo b")
        item_c = rig_queue.add_item(self.repo, "touch bar")
        claimed_a = rig_queue.claim_next(
            self.repo, files=["foo.py"], item_id=item_a["id"]
        )
        claimed_c = rig_queue.claim_next(
            self.repo, files=["bar.py"], item_id=item_c["id"]
        )
        self.assertEqual(claimed_a["id"], item_a["id"])
        self.assertEqual(claimed_c["id"], item_c["id"])
        with self.assertRaises(rig_queue.QueueError) as ctx:
            rig_queue.claim_next(self.repo, files=["foo.py"], item_id=item_b["id"])
        self.assertIn("overlap", str(ctx.exception))
        self.assertEqual(rig_queue.load_item(self.repo, item_b["id"])["status"], "pending")

    def test_format_block_shows_occupied_files(self):
        self._live_job("writer-a", files=["src/foo.py", "src/bar.py"])
        block = rig_queue.format_block(self.repo)
        self.assertIn("occupied", block)
        self.assertIn("src/foo.py", block)
        self.assertIn("src/bar.py", block)

    def test_format_block_unknown_occupied(self):
        self._live_job("writer-empty", files=[], role="implement")
        block = rig_queue.format_block(self.repo)
        self.assertIn("occupied", block)
        self.assertIn("unknown", block)
        self.assertIn("writer-empty", block)
        with self.assertRaises(rig_queue.QueueError) as ctx:
            rig_queue.check_start(
                self.repo, "next", files=["src/other.py"], role="implement"
            )
        self.assertIn("writer-empty", str(ctx.exception))
        self.assertIn("no listed files", str(ctx.exception))

    def test_format_block_caps_occupied_paths(self):
        files = [f"src/f{i}.py" for i in range(10)]
        self._live_job("writer-many", files=files)
        block = rig_queue.format_block(self.repo)
        self.assertIn("+2 more", block)
        self.assertIn("src/f0.py", block)
        self.assertNotIn("src/f9.py", block)

    def test_mcp_claim_without_id_two_pending_errors(self):
        rig_queue.add_item(self.repo, "one")
        rig_queue.add_item(self.repo, "two")
        blocked = rig_mcp.call_tool(
            "rig_queue_claim",
            {"files": ["a.py"], "repo": str(self.repo)},
        )
        self.assertTrue(blocked.get("isError"), blocked)
        self.assertIn("needs id", blocked["content"][0]["text"])

    def test_cap_refuses_fourth_start(self):
        for i in range(3):
            self._live_job(f"live-{i}", files=[f"f{i}.py"])
        with self.assertRaises(rig_queue.QueueError) as ctx:
            rig_queue.check_start(self.repo, "live-3", files=["f3.py"], role="implement")
        self.assertIn("3/3", str(ctx.exception))
        with self.assertRaises(SystemExit) as start_ctx:
            jobs.start_job(self.repo, worker="grok", role="implement", job_id="live-3")
        self.assertIn("3/3", str(start_ctx.exception))
        existing = jobs.start_job(self.repo, worker="grok", role="implement", job_id="live-0")
        self.assertEqual(existing, "live-0")

    def test_overlap_and_empty_files_block(self):
        self._live_job("writer-a", files=["src/foo.py"])
        item = rig_queue.add_item(self.repo, "touch foo")
        with self.assertRaises(rig_queue.QueueError) as ctx:
            rig_queue.claim_next(self.repo, files=["src/foo.py"])
        self.assertIn("overlap", str(ctx.exception))
        other = rig_queue.claim_next(self.repo, files=["src/bar.py"])
        self.assertEqual(other["id"], item["id"])
        rig_queue.unclaim(self.repo, item["id"])
        self._live_job("writer-empty", files=[], role="implement")
        with self.assertRaises(rig_queue.QueueError) as empty_ctx:
            rig_queue.claim_next(self.repo, files=["src/other.py"])
        self.assertIn("no listed files", str(empty_ctx.exception))

    def test_mcp_claim_spawned_and_finish_marks_done(self):
        item = rig_queue.add_item(self.repo, "add tests")
        claimed = rig_mcp.call_tool(
            "rig_queue_claim",
            {"id": item["id"], "files": ["tests/test_queue.py"], "repo": str(self.repo)},
        )
        self.assertNotIn("isError", claimed, claimed)
        self.assertIn("claimed", claimed["content"][0]["text"])
        os.environ["RIG_JOB_FILES"] = "tests/test_queue.py"
        try:
            job_id = jobs.start_job(
                self.repo,
                worker="grok",
                role="implement",
                job_id="q-spawn",
                summary="add tests",
            )
            spawned = rig_mcp.call_tool(
                "rig_queue_spawned",
                {
                    "id": item["id"],
                    "job_id": job_id,
                    "files": ["tests/test_queue.py"],
                    "repo": str(self.repo),
                },
            )
        finally:
            os.environ.pop("RIG_JOB_FILES", None)
        self.assertNotIn("isError", spawned, spawned)
        jobs.finish_job(self.repo, job_id, status="ok", summary="done")
        loaded = rig_queue.load_item(self.repo, item["id"])
        self.assertEqual(loaded["status"], "done")

    def test_wait_all_wakes_on_first_ask_with_three_live(self):
        for i in range(3):
            self._live_job(f"panel-{i}", files=[f"p{i}.py"])
        ask_dir = self.repo / ".rig" / "jobs" / "panel-1"
        rig_ask.write_ask(ask_dir, "Bash", {"command": "ls"}, "u1", preview_text="ls")
        code, text = jobs.wait_job(
            self.repo, None, timeout=0, ids=["panel-0", "panel-1", "panel-2"]
        )
        self.assertEqual(code, 2)
        self.assertTrue(text.startswith("ASK") or "ASK" in text.splitlines()[0])
        self.assertIn("panel-1", text)

    def test_priority_fifo_and_slash_parse(self):
        low = rig_queue.add_item(self.repo, "later", priority=0)
        high = rig_queue.add_item(self.repo, "first", priority=2)
        mid = rig_queue.add_item(self.repo, "middle", priority=1)
        order = [i["id"] for i in rig_queue.list_items(self.repo)]
        self.assertEqual(order, [high["id"], mid["id"], low["id"]])
        self.assertEqual(rig_queue.parse_slash("hello"), None)
        self.assertEqual(rig_queue.parse_slash("/queue")["action"], "list")
        parsed = rig_queue.parse_slash("/queue --priority 3 --worker claude fix pagination")
        self.assertEqual(parsed["action"], "add")
        self.assertEqual(parsed["priority"], 3)
        self.assertEqual(parsed["worker"], "claude")
        self.assertEqual(parsed["text"], "fix pagination")
        cancel = rig_queue.parse_slash("/prompts:queue cancel abc")
        self.assertEqual(cancel, {"action": "cancel", "id": "abc"})
        dollar = rig_queue.parse_slash("$queue park fix pagination")
        self.assertEqual(dollar["action"], "add")
        self.assertEqual(dollar["text"], "fix pagination")
        self.assertEqual(
            rig_queue.parse_slash("$rig-queue park --priority 2 later")["text"], "later"
        )
        self.assertIsNone(rig_queue.parse_slash("please $queue this"))
        plugin = ROOT / "adapters" / "opencode" / "plugin" / "rig-queue.js"
        self.assertTrue(plugin.is_file())
        text = plugin.read_text()
        self.assertIn("chat.message", text)
        self.assertIn("command.execute.before", text)
        ext = ROOT / "adapters" / "omp" / "extensions" / "rig-queue.js"
        self.assertIn("registerCommand", ext.read_text())

    def test_per_worker_cap(self):
        _write_harness(
            self.repo,
            'parent = "codex"\n\n[workers]\ngrok = true\nclaude = true\n\n'
            "[queue]\nmax_running = 3\nmax_per_worker = 1\n",
        )
        self._live_job("g1", files=["a.py"], role="implement")
        meta = json.loads((self.repo / ".rig" / "jobs" / "g1" / "meta.json").read_text())
        meta["worker"] = "grok"
        (self.repo / ".rig" / "jobs" / "g1" / "meta.json").write_text(json.dumps(meta) + "\n")
        with self.assertRaises(rig_queue.QueueError) as ctx:
            rig_queue.check_start(
                self.repo, "g2", files=["b.py"], role="implement", worker="grok"
            )
        self.assertIn("grok live 1/1", str(ctx.exception))
        rig_queue.check_start(
            self.repo, "c1", files=["c.py"], role="implement", worker="claude"
        )

    def test_submit_hook_parks_and_blocks(self):
        import queue_submit_hook as hook

        out = hook.handle({"prompt": "/queue fix pagination", "cwd": str(self.repo)})
        self.assertEqual(out["decision"], "block")
        self.assertIn("queued", out["reason"])
        self.assertIn("queued", out["systemMessage"])
        items = rig_queue.list_items(self.repo)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["text"], "fix pagination")
        self.assertIsNone(hook.handle({"prompt": "implement pagination", "cwd": str(self.repo)}))
        self.assertIsNone(hook.handle({"prompt": "/queue", "cwd": str(self.repo)}))
        second = hook.handle({"prompt": "$queue park another item", "cwd": str(self.repo)})
        self.assertEqual(second["decision"], "block")
        self.assertEqual(len(rig_queue.list_items(self.repo)), 2)

    def test_print_list_flag(self):
        import subprocess
        import sys

        rig_queue.add_item(self.repo, "listed item")
        script = ROOT / "scripts" / "queue_submit_hook.py"
        proc = subprocess.run(
            [sys.executable, str(script), "--print-list"],
            cwd=str(self.repo),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("QUEUE", proc.stdout)
        self.assertIn("listed item", proc.stdout)


if __name__ == "__main__":
    unittest.main()
