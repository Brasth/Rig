#!/usr/bin/env python3
import inspect
import os
import socket
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
import rig_tui  # noqa: E402
import tui_runtime  # noqa: E402
from tui_runtime import ApiBoard, ApiLoader, BoardRuntime, DirectBoard, Snapshot  # noqa: E402


def make_repo(root: Path) -> Path:
    (root / ".git").mkdir()
    (root / ".rig").mkdir()
    (root / ".rig" / "harness.toml").write_text("[project]\nenabled = true\n\n[workers]\ngrok = true\n")
    return root


class Served(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.repo = make_repo(Path(self.td.name))
        self.env = {k: v for k, v in os.environ.items() if k not in {"RIG_JOB_ID", "RIG_JOB_DIR"}}
        self.procs = []

    def tearDown(self):
        for proc in self.procs:
            if proc.poll() is None:
                proc.terminate()
                proc.wait(timeout=10)
        self.td.cleanup()

    def serve(self, *extra):
        proc = subprocess.Popen([sys.executable, str(ROOT / "scripts" / "local_api.py"), "serve",
                                 "--repo", str(self.repo), *extra], env=self.env,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.procs.append(proc)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                local_api.call(self.repo, "api.ping", timeout=0.5)
                return proc
            except (OSError, ValueError):
                time.sleep(0.05)
        self.fail("API did not start")


class BoardSnapshotMethod(Served):
    def test_empty_repo_snapshot_has_the_board_shape(self):
        self.serve()
        reply = local_api.call(self.repo, "board.snapshot", {"start": 0, "rows": 10})
        data = reply["result"]
        self.assertEqual((data["jobs"], data["pending"], data["workflows"]), ([], [], []))
        self.assertEqual((data["slots"], data["cap"]), (0, 3))
        self.assertTrue(any(row["id"] == "engine" for row in data["settings"]))
        self.assertIn("captured_wall", data)

    def test_view_parameters_are_validated(self):
        self.serve()
        for bad in ({"start": -1}, {"rows": 501}, {"rows": "5"}, {"selected_id": 7}, {"start": True}):
            reply = local_api.call(self.repo, "board.snapshot", bad)
            self.assertEqual(reply["error"]["code"], local_api.INVALID_PARAMS, bad)

    def test_api_snapshot_matches_direct_read(self):
        self.serve()
        via_api = tui_runtime.api_snapshot(self.repo, start=0, rows=10)
        direct = tui_runtime.load_snapshot(self.repo, start=0, rows=10)
        self.assertEqual(via_api.source, "api")
        self.assertEqual((via_api.jobs, via_api.pending, via_api.slots, via_api.cap),
                         (direct.jobs, direct.pending, direct.slots, direct.cap))
        self.assertEqual(via_api.settings, direct.settings)
        self.assertEqual(via_api.workflows, [])

    def test_idle_server_exits_and_cleans_up(self):
        proc = self.serve("--idle", "1")
        endpoint, token = local_api.runtime_paths(self.repo)
        proc.wait(timeout=15)
        self.assertFalse(endpoint.exists())
        self.assertFalse(token.exists())

    def test_ensure_starts_one_server_and_is_idempotent(self):
        self.assertTrue(local_api.ensure(self.repo, idle=2))
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                local_api.call(self.repo, "api.ping", timeout=0.5)
                break
            except (OSError, ValueError):
                time.sleep(0.05)
        else:
            self.fail("ensure did not start a server")
        self.assertFalse(local_api.ensure(self.repo, idle=2))
        endpoint, _ = local_api.runtime_paths(self.repo)
        deadline = time.monotonic() + 15
        while endpoint.exists() and time.monotonic() < deadline:
            time.sleep(0.1)
        self.assertFalse(endpoint.exists(), "idle ensure-started server should exit by itself")


class LoaderBehaviour(unittest.TestCase):
    def snap(self, **kw):
        return Snapshot(jobs=[], pending=[], slots=0, cap=3, captured_at=1.0, **kw)

    def test_api_result_is_returned_untouched_and_nothing_spawned(self):
        api = self.snap(source="api")
        ensure = mock.Mock()
        loader = ApiLoader(fetch=lambda repo, **v: api, fallback=mock.Mock(side_effect=AssertionError), ensure=ensure)
        self.assertIs(loader(Path("/x"), start=0, rows=5), api)
        ensure.assert_not_called()

    def test_unreachable_api_falls_back_marked_direct_and_spawns_once_per_window(self):
        now = [100.0]
        calls = []
        ensure = lambda repo: calls.append(repo)  # noqa: E731

        def down(repo, **v):
            raise ConnectionError("down")

        loader = ApiLoader(fetch=down, fallback=lambda repo, **v: self.snap(), ensure=ensure,
                           respawn=15, clock=lambda: now[0])
        first = loader(Path("/x"))
        second = loader(Path("/x"))
        self.assertEqual((first.source, second.source), ("direct", "direct"))
        self.assertEqual(len(calls), 1)
        now[0] += 16
        loader(Path("/x"))
        self.assertEqual(len(calls), 2)

    def test_spawn_can_be_disabled_and_spawn_errors_never_blank_the_board(self):
        def down(repo, **v):
            raise OSError("down")

        ensure = mock.Mock()
        ApiLoader(fetch=down, fallback=lambda repo, **v: self.snap(), ensure=ensure, spawn=False)(Path("/x"))
        ensure.assert_not_called()
        boom = mock.Mock(side_effect=OSError("cannot spawn"))
        result = ApiLoader(fetch=down, fallback=lambda repo, **v: self.snap(), ensure=boom)(Path("/x"))
        self.assertEqual(result.source, "direct")

    def test_api_error_response_also_falls_back(self):
        def failing(repo, **v):
            raise RuntimeError("board snapshot failed")

        result = ApiLoader(fetch=failing, fallback=lambda repo, **v: self.snap(), ensure=mock.Mock())(Path("/x"))
        self.assertEqual(result.source, "direct")

    def test_status_marker_only_for_direct_fallback(self):
        for source, expected in (("direct", True), ("api", False), ("local", False)):
            runtime = BoardRuntime(Path("/x"), loader=lambda *a, **k: None)
            runtime.snapshot = self.snap(source=source)
            self.assertEqual("API unavailable" in rig_tui._snapshot_status(runtime), expected, source)

    def test_snapshot_defaults_keep_existing_callers_working(self):
        snap = Snapshot()
        self.assertEqual((snap.workflows, snap.source), (None, "local"))


class BoardActionsThroughApi(Served):
    """Phase 0 exit check: with the API up, the board needs no direct state access."""

    def setUp(self):
        super().setUp()
        self.serve()
        banned = AssertionError("direct state access while the API is available")
        self.patches = [mock.patch.object(DirectBoard, name, side_effect=banned)
                        for name in ("snapshot", "enqueue", "cancel_job", "cancel_queue", "answer", "recovery")]
        self.patches.append(mock.patch.object(tui_runtime, "load_snapshot", side_effect=banned))
        for patch in self.patches:
            patch.start()
        self.board = ApiBoard(spawn=False)

    def tearDown(self):
        for patch in self.patches:
            patch.stop()
        super().tearDown()

    def test_enqueue_snapshot_cancel_roundtrip(self):
        item = self.board.enqueue(self.repo, "Fix login bug", "key-1")
        self.assertEqual(item["text"], "Fix login bug")
        again = self.board.enqueue(self.repo, "Fix login bug", "key-1")
        self.assertEqual(again["id"], item["id"], "idempotency key must dedupe")
        snap = self.board.snapshot(self.repo, start=0, rows=10)
        self.assertEqual((snap.source, [row["id"] for row in snap.pending]), ("api", [item["id"]]))
        cancelled = self.board.cancel_queue(self.repo, item["id"])
        self.assertEqual(cancelled["status"], "cancelled")
        self.assertEqual(self.board.snapshot(self.repo, start=0, rows=10).pending, [])

    def test_api_errors_are_raised_not_bypassed_by_direct_calls(self):
        for call in (lambda: self.board.cancel_queue(self.repo, "no-such-item"),
                     lambda: self.board.cancel_job(self.repo, "no-such-job"),
                     lambda: self.board.answer(self.repo, {"job_id": "no-such-job"}, "allow"),
                     lambda: self.board.answer(self.repo, {"job_id": "no-such-job"}, "deny")):
            with self.assertRaises(RuntimeError):
                call()

    def test_recovery_guidance_arrives_as_the_structured_dict(self):
        guide = self.board.recovery(self.repo, job_id="no-such-job")
        self.assertIsInstance(guide, dict)
        self.assertEqual(guide["scope"], {"job_id": "no-such-job"})
        self.assertEqual(guide["state"], "unknown")

    def test_paint_has_no_direct_state_access(self):
        source = inspect.getsource(rig_tui._paint)
        for forbidden in ("rig_queue.", "rig_jobs.", "recovery_guide."):
            self.assertNotIn(forbidden, source)


class ActionFallbackRules(unittest.TestCase):
    def board(self, call, **kw):
        ensure = mock.Mock()
        return ApiBoard(call=call, ensure=ensure, **kw), ensure

    def test_unreachable_server_runs_the_direct_action_once_and_spawns_throttled(self):
        def down(*a, **k):
            raise ConnectionError("not running")

        board, ensure = self.board(down)
        with mock.patch.object(DirectBoard, "enqueue", return_value={"id": "q1"}) as direct:
            self.assertEqual(board.enqueue(Path("/x"), "t", "k"), {"id": "q1"})
            board.enqueue(Path("/x"), "t", "k")
        self.assertEqual(direct.call_count, 2)
        ensure.assert_called_once()

    def test_failure_after_sending_never_reruns_directly(self):
        def slow(*a, **k):
            raise socket.timeout("timed out")

        board, _ = self.board(slow)
        with mock.patch.object(DirectBoard, "cancel_job") as direct:
            with self.assertRaises(socket.timeout):
                board.cancel_job(Path("/x"), "j1")
        direct.assert_not_called()

    def test_error_response_is_raised_with_its_message(self):
        board, _ = self.board(lambda *a, **k: {"error": {"code": -32000, "message": "Rig is disabled"}})
        with mock.patch.object(DirectBoard, "enqueue") as direct:
            with self.assertRaisesRegex(RuntimeError, "Rig is disabled"):
                board.enqueue(Path("/x"), "t", "k")
        direct.assert_not_called()

    def test_action_parameters_and_result_shapes(self):
        seen = []

        def fake(repo, method, params, timeout=0):
            seen.append((method, params))
            return {"result": {"text": "ok text", "structured": {"id": "q9", "status": "cancelled"}}}

        board, _ = self.board(fake)
        self.assertEqual(board.cancel_job(Path("/x"), "j1"), "ok text")
        self.assertEqual(board.cancel_queue(Path("/x"), "q9")["status"], "cancelled")
        board.answer(Path("/x"), {"job_id": "j1"}, "allow")
        board.answer(Path("/x"), {"job_id": "j1"}, "deny")
        board.recovery(Path("/x"), job_id="j1", workflow_id=None)
        self.assertEqual(seen, [("job.cancel", {"id": "j1", "reason": "tui"}), ("queue.cancel", {"id": "q9"}),
                                ("job.allow", {"id": "j1"}), ("job.deny", {"id": "j1"}),
                                ("recovery.guide", {"job_id": "j1"})])

    def test_direct_board_is_the_default_so_existing_behaviour_is_kept(self):
        self.assertIn("backend or DirectBoard()", inspect.getsource(rig_tui._paint))


if __name__ == "__main__":
    unittest.main()
