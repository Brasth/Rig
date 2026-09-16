#!/usr/bin/env python3
"""Exercise the input loop while real background work is deliberately stalled."""
import curses
import sys
import threading
import time
import unittest
from contextlib import ExitStack, contextmanager
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import rig_tui
import tui_runtime
from tui_editor import Draft, InputDecoder, TEXT_CAP
from tui_runtime import ActionResult, BoardRuntime, Snapshot
from tui_view import _add, _text_width, render
import test_tui as fixtures

BoardScr = fixtures.BoardScr
ScriptedRuntime = fixtures.ScriptedRuntime


@contextmanager
def terminal():
    with ExitStack() as stack:
        for name in ("curs_set", "use_default_colors", "init_pair", "noecho", "set_escdelay"):
            stack.enter_context(mock.patch.object(curses, name))
        stack.enter_context(mock.patch.object(curses, "color_pair", return_value=0))
        stack.enter_context(mock.patch.object(sys.stdout, "isatty", return_value=False))
        yield


def snapshot():
    return Snapshot(jobs=[fixtures.BoardProjection.project(fixtures.BoardProjection.job(index)) for index in range(3)],
                    captured_at=time.monotonic())


def drain(runtime, predicate, timeout=1):
    deadline = time.monotonic() + timeout
    results = []
    while time.monotonic() < deadline:
        results.extend(runtime.poll())
        if predicate():
            return results
        time.sleep(0.001)
    raise AssertionError("background worker did not complete")


class MeasuredScreen(BoardScr):
    def __init__(self, keys):
        super().__init__(keys)
        self.input_times = []
        self.owner = threading.get_ident()

    def get_wch(self):
        self.input_times.append(time.monotonic())
        return super().get_wch()

    def addnstr(self, *args):
        if threading.get_ident() != self.owner:
            raise AssertionError("worker called curses")
        super().addnstr(*args)

    def assert_responsive(self, case):
        gaps = [right - left for left, right in zip(self.input_times, self.input_times[1:])]
        case.assertTrue(gaps)
        case.assertLess(max(gaps), 0.1)


class InputResponsiveness(unittest.TestCase):
    def test_unchanged_enqueue_retry_preserves_submission_identity(self):
        draft = Draft(active=True)
        draft.insert("queue this")
        submission = draft.submission_id
        self.assertEqual(draft.key("\n"), "submit")
        draft.key(curses.KEY_LEFT)
        self.assertEqual(draft.key("\n"), "submit")
        self.assertEqual(draft.submission_id, submission)
        draft.key("!")
        self.assertNotEqual(draft.submission_id, submission)

    def test_cancel_result_displays_held_reservation_reason(self):
        runtime = ScriptedRuntime([snapshot()])
        runtime.poll = mock.Mock(side_effect=[
            [ActionResult("cancel:Queue:queue-one", {
                "id": "queue-one", "status": "cancelled",
                "held_reason": "Reservation held until stopped"})], []])
        screen = BoardScr(["q"])
        with terminal():
            rig_tui._paint(screen, Path("/fixture"), runtime=runtime)
        self.assertTrue(any("Reservation held until stopped" in cell[2]
                            for frame in screen.frames for cell in frame))

    def test_slow_snapshot_keeps_navigation_editor_and_quit_responsive(self):
        entered, release = threading.Event(), threading.Event()

        def slow_scan(*_args, **_kwargs):
            entered.set()
            release.wait(2)
            return snapshot()

        runtime = BoardRuntime(Path("/fixture"), loader=slow_scan)
        runtime.snapshot = snapshot()
        runtime.request_snapshot()
        self.assertTrue(entered.wait(1))
        screen = MeasuredScreen(["j", "k", "\t", "e", "你", "é", curses.KEY_RESIZE, "\x1b", "q"])
        try:
            with terminal(), mock.patch.object(rig_tui.rig_jobs, "cancel_job") as cancel, \
                    mock.patch.object(rig_tui.rig_queue, "add_item") as enqueue:
                rig_tui._paint(screen, Path("/fixture"), runtime=runtime)
            screen.assert_responsive(self)
            self.assertTrue(runtime.closed)
            cancel.assert_not_called()
            enqueue.assert_not_called()
            self.assertTrue(any("你é" in cell[2] for frame in screen.frames for cell in frame))
        finally:
            release.set()

    def test_slow_cancel_is_immediate_idempotent_and_does_not_block_exit(self):
        entered, release = threading.Event(), threading.Event()

        def slow_cancel(*_args, **_kwargs):
            entered.set()
            release.wait(2)
            return "cancellation requested job-000"

        runtime = BoardRuntime(Path("/fixture"), loader=lambda *_args, **_kwargs: snapshot())
        runtime.snapshot = snapshot()
        screen = MeasuredScreen(["x", "y", "x", "j", "k", "q"])
        try:
            with terminal(), mock.patch.object(rig_tui.rig_jobs, "cancel_job", side_effect=slow_cancel) as cancel:
                rig_tui._paint(screen, Path("/fixture"), runtime=runtime)
                self.assertTrue(entered.wait(1))
                cancel.assert_called_once_with(Path("/fixture"), "job-000", "tui")
            screen.assert_responsive(self)
            confirm = "\n".join(cell[2] for cell in screen.frames[1])
            self.assertIn("Stop job job-000", confirm)
            self.assertIn("y confirm", confirm)
            self.assertTrue(any("stop requested job-000" in cell[2] for cell in screen.frames[2]))
            self.assertTrue(any("stop already requested job-000" in cell[2] for cell in screen.frames[3]))
        finally:
            release.set()

    def test_paste_outside_editor_never_executes_navigation_actions(self):
        keys = list("\x1b[200~xynq\x1b[201~") + ["q"]
        screen = MeasuredScreen(keys)
        runtime = ScriptedRuntime([snapshot()])
        runtime.submit = mock.Mock()
        with terminal():
            rig_tui._paint(screen, Path("/fixture"), runtime=runtime)
        runtime.submit.assert_not_called()
        self.assertEqual(len(screen.frames), len(keys))

    def test_cancel_confirmation_does_not_mutate_before_confirm_or_on_abort(self):
        runtime = ScriptedRuntime([snapshot()])
        runtime.submit = mock.Mock(return_value=True)
        screen = BoardScr(["x", "n", "q"])
        with terminal():
            rig_tui._paint(screen, Path("/fixture"), runtime=runtime)
        runtime.submit.assert_not_called()
        first = "\n".join(cell[2] for cell in screen.frames[1])
        self.assertIn("Stop job job-000", first)
        self.assertIn("scoped task", first)
        self.assertIn("y confirm · any other key aborts", first)
        aborted = "\n".join(cell[2] for cell in screen.frames[2])
        self.assertIn("Cancellation aborted job-000", aborted)

    def test_queued_rows_can_be_selected_without_guessing_scope(self):
        state = Snapshot(pending=[{"id": "queue-one", "text": "one", "status": "pending", "waiting_reason": "Execution capacity full (3/3)"},
                                  {"id": "queue-two", "text": "two", "status": "pending", "waiting_reason": "Awaiting parent scope and claim"}],
                         captured_at=time.monotonic())
        runtime = ScriptedRuntime([state])
        runtime.submit = mock.Mock(return_value=True)
        screen = BoardScr(["\t", "j", "x", "y", "q"])
        with terminal():
            rig_tui._paint(screen, Path("/fixture"), runtime=runtime)
        confirm = "\n".join(cell[2] for cell in screen.frames[3])
        self.assertIn("Cancel queue item queue-two", confirm)
        self.assertIn("y confirm", confirm)
        self.assertEqual(runtime.submit.call_count, 1)
        self.assertEqual(runtime.submit.call_args.args[0], "cancel:Queue:queue-two")
        action = runtime.submit.call_args.args[1]
        with mock.patch.object(rig_tui.rig_queue, "cancel_item") as cancel:
            action()
        cancel.assert_called_once_with(Path("/fixture"), "queue-two")
        self.assertTrue(any("Awaiting parent scope" in cell[2] for cell in screen.frames[2]))

    def test_failed_enqueue_retains_unicode_draft_through_resize_and_retry(self):
        class FailedOnce(ScriptedRuntime):
            def __init__(self):
                super().__init__([snapshot()])
                self.results = []
                self.texts = []

            def submit(self, key, action, **_kwargs):
                self.results.append(ActionResult(key, error="lock busy"))
                return True

            def poll(self):
                results, self.results = self.results, []
                return results

        runtime = FailedOnce()
        screen = BoardScr(["e", "你", "é", "\n", curses.KEY_RESIZE, curses.KEY_LEFT, "好", "\x1b", "q"])
        with terminal():
            rig_tui._paint(screen, Path("/fixture"), runtime=runtime)
        cells = [cell[2] for frame in screen.frames for cell in frame]
        self.assertTrue(any("Enqueue failed: lock busy; draft retained" in text for text in cells))
        self.assertTrue(any("你é" in cell[2] for cell in screen.frames[5]))
        self.assertTrue(any("你好é" in text for text in cells))

    def test_failed_action_is_visible_and_can_be_retried(self):
        class Refused(ScriptedRuntime):
            def __init__(self):
                super().__init__([snapshot()])
                self.results, self.submitted = [], []

            def submit(self, key, _action, **_kwargs):
                self.submitted.append(key)
                self.results.append(ActionResult(key, error="job disappeared"))
                return True

            def poll(self):
                results, self.results = self.results, []
                return results

        runtime = Refused()
        screen = BoardScr(["x", "y", "x", "y", "q"])
        with terminal():
            rig_tui._paint(screen, Path("/fixture"), runtime=runtime)
        self.assertEqual(runtime.submitted, ["cancel:Jobs:job-000"] * 2)
        self.assertTrue(any("Action failed: job disappeared" in cell[2]
                            for frame in screen.frames for cell in frame))


class RuntimeIsolation(unittest.TestCase):
    def test_only_one_snapshot_can_be_in_flight_and_failure_retains_last(self):
        entered, release = threading.Event(), threading.Event()

        def failing_scan(*_args, **_kwargs):
            entered.set()
            release.wait(2)
            raise OSError("disk busy")

        runtime = BoardRuntime(Path("/fixture"), loader=failing_scan)
        last = runtime.snapshot = snapshot()
        try:
            self.assertTrue(runtime.request_snapshot())
            self.assertTrue(entered.wait(1))
            self.assertFalse(runtime.request_snapshot())
            release.set()
            drain(runtime, lambda: not runtime.scanning)
            self.assertIs(runtime.snapshot, last)
            self.assertEqual(runtime.snapshot_error, "disk busy")
            self.assertIn("snapshot", rig_tui._snapshot_status(runtime))
            self.assertIn("disk busy", rig_tui._snapshot_status(runtime))
        finally:
            release.set()
            runtime.close()

    def test_bounded_action_lanes_reserve_cancellation_and_catch_system_exit(self):
        release = threading.Event()
        runtime = BoardRuntime(Path("/fixture"))
        slow = lambda: release.wait(2)
        try:
            self.assertTrue(runtime.submit("one", slow))
            self.assertTrue(runtime.submit("two", slow))
            self.assertFalse(runtime.submit("three", slow))
            self.assertFalse(runtime.submit("one", slow))
            self.assertTrue(runtime.submit("cancel-one", slow, cancellation=True))
            self.assertTrue(runtime.submit("cancel-two", slow, cancellation=True))
            self.assertFalse(runtime.submit("cancel-three", slow, cancellation=True))
            release.set()
            drain(runtime, lambda: not runtime._actions)

            def refused():
                raise SystemExit("permission missing")

            self.assertTrue(runtime.submit("failure", refused))
            results = drain(runtime, lambda: not runtime._actions)
            self.assertEqual(results[0].error, "permission missing")
        finally:
            release.set()
            runtime.close()

    def test_snapshot_reports_known_capacity_but_does_not_infer_scope_from_text(self):
        job = fixtures.BoardProjection.job(1)
        pending = [{"id": "queued", "text": "edit src/shared.py", "status": "pending"}]
        held = [{"job_id": "owner", "files": ["src/shared.py"], "access": "write", "slot_held": True}]
        with mock.patch.object(tui_runtime.rig_jobs, "list_jobs", return_value=[job]), \
                mock.patch.object(tui_runtime.rig_queue, "_held_rows", return_value=held), \
                mock.patch.object(tui_runtime.rig_queue, "list_items", return_value=pending), \
                mock.patch.object(tui_runtime.rig_queue, "max_running", return_value=1):
            full = tui_runtime.load_snapshot(Path("/fixture"), rows=0)
            self.assertIn("capacity full (1/1)", full.pending[0]["waiting_reason"])
            held[0]["slot_held"] = False
            free = tui_runtime.load_snapshot(Path("/fixture"), rows=0)
            self.assertEqual(free.slots, 0)
            self.assertIn("file overlap is not yet known", free.pending[0]["waiting_reason"])


class UnicodeDrafts(unittest.TestCase):
    def test_wide_unicode_is_clipped_by_terminal_cells_and_cursor_matches(self):
        screen = BoardScr([], w=40)
        screen.move = mock.Mock()
        draft = Draft(text="你好é", cursor=3, active=True)
        with terminal():
            render(screen, Path("/fixture"), Snapshot(), tab="Jobs", selected=0, offset=0,
                   follow=True, log_off=0, footer="", snapshot_status="", requested=set(), draft=draft)
        screen.move.assert_called_once_with(9, len("enqueue 3/2000: ") + 5)
        screen.calls.clear()
        _add(screen, 9, 0, "界" * 100)
        self.assertLessEqual(_text_width(screen.calls[0][2]), 39)

    def test_editing_and_paste_keep_unicode_and_enforce_explicit_cap(self):
        draft = Draft(active=True)
        draft.key("你é")
        draft.key(curses.KEY_LEFT)
        draft.key("好")
        self.assertEqual(draft.text, "你好é")
        draft.key(curses.KEY_BACKSPACE)
        self.assertEqual(draft.text, "你é")
        draft.key(curses.KEY_HOME)
        draft.key(curses.KEY_DC)
        self.assertEqual(draft.text, "é")
        draft.key(curses.KEY_END)
        draft.key("中文\nline\ttext", pasted=True)
        self.assertEqual(draft.text, "é中文 line text")
        draft.key("界" * TEXT_CAP, pasted=True)
        self.assertEqual(len(draft.text), TEXT_CAP)
        self.assertIn("Limit 2000", draft.message)

    def test_bracketed_paste_newline_never_submits(self):
        decoder = InputDecoder()
        draft = Draft(active=True)
        actions = []
        for char in "\x1b[200~你好\nxqé\x1b[201~":
            for key, pasted in decoder.feed(char):
                actions.append(draft.key(key, pasted=pasted))
        self.assertEqual(draft.text, "你好 xqé")
        self.assertTrue(draft.active)
        self.assertNotIn("submit", actions)
        self.assertNotIn("cancel", actions)

    def test_escape_is_decoded_without_blocking_and_cancels_only_draft(self):
        now = [0.0]
        decoder = InputDecoder(clock=lambda: now[0])
        draft = Draft(text="unfinished", cursor=10, active=True)
        self.assertEqual(decoder.feed("\x1b"), [])
        now[0] = 0.031
        key, pasted = decoder.flush()[0]
        self.assertEqual(draft.key(key, pasted=pasted), "cancel")
        self.assertFalse(draft.active)
        self.assertEqual(draft.text, "")


if __name__ == "__main__":
    unittest.main()
