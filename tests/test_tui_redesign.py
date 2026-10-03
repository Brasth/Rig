#!/usr/bin/env python3
"""Board refresh: notifications, titles, state labels, details, tab strip, and layout."""
import copy
import curses
import sys
import time
import unittest
from contextlib import ExitStack, contextmanager
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import rig_tui  # noqa: E402
import tui_chrome  # noqa: E402
import tui_rows  # noqa: E402
import tui_view  # noqa: E402
from tui_detail_state import DetailState, HelpState, Notice, Notifications  # noqa: E402
from tui_editor import Draft  # noqa: E402
from tui_runtime import ActionResult, Snapshot  # noqa: E402
from tui_text import _cell_width, _text_width, abbrev_id, first_line, wrap_cells  # noqa: E402
import test_tui as fixtures  # noqa: E402

BoardScr = fixtures.BoardScr
ScriptedRuntime = fixtures.ScriptedRuntime
LABELS = ("Needs input", "Running", "Reserved", "Checking", "Unverified", "Verified", "Failed",
          "Cancelled", "Stopping", "Stop unclear", "Host stop")


@contextmanager
def terminal():
    with ExitStack() as stack:
        for name in ("curs_set", "use_default_colors", "init_pair", "noecho", "set_escdelay"):
            stack.enter_context(mock.patch.object(curses, name))
        stack.enter_context(mock.patch.object(curses, "color_pair", return_value=0))
        stack.enter_context(mock.patch.object(sys.stdout, "isatty", return_value=False))
        yield


class StrictScr(BoardScr):
    """Fails on any write past the right edge or into the bottom-right cell."""

    def addnstr(self, y, x, text, n, attr=0):
        text = text[:n]
        assert 0 <= y < self.h and 0 <= x < self.w, (y, x, text)
        end = x + _text_width(text)
        assert end <= self.w, (y, x, text, self.w)
        assert not (y == self.h - 1 and end >= self.w), ("bottom-right", y, x, text)
        self.calls.append((y, x, text, n, attr))

    def text(self, frame=-1):
        return "\n".join(call[2] for call in self.frames[frame])

    def row(self, y, frame=-1):
        """Row as painted: later writes overwrite earlier cells, like curses."""
        cells = [" "] * self.w
        for row_y, x, text, _n, _attr in self.frames[frame]:
            if row_y != y:
                continue
            for char in text:
                width = _cell_width(char)
                if width == 0:
                    cells[max(0, x - 1)] += char
                    continue
                cells[x] = char
                if width == 2:
                    cells[x + 1] = ""
                x += width
        return "".join(cells).rstrip()


def job(index, **fields):
    row = {"job_id": f"job-{index:03}", "worker": "codex", "role": "implement", "effective": "running",
           "display_state": "working", "task": f"task {index}", "activities": [],
           "verification_summary": {"state": "pending"}, "display_reason": "", "display_action": ""}
    row.update(fields)
    return row


def snap(jobs=(), pending=(), workflows=None, settings=()):
    value = Snapshot(jobs=list(jobs), pending=list(pending), slots=1, cap=3, captured_at=time.monotonic(),
                     settings=list(settings))
    if workflows is not None:
        object.__setattr__(value, "workflows", workflows)
    return value


class Clocked(ScriptedRuntime):
    """Scripted snapshots plus queued action results and a recorded snapshot budget."""

    def __init__(self, snapshots, results=None):
        super().__init__(snapshots)
        self.results = list(results or [])
        self.submitted = []
        self.budgets = []

    def poll(self):
        out = super().poll()
        if self.results:
            item = self.results.pop(0)
            if item is not None:
                out.append(item)
        return out

    def submit(self, key, work, **_kwargs):
        self.submitted.append(key)
        return True

    def request_snapshot(self, **kwargs):
        self.budgets.append(kwargs)


def run(screen, runtime, clock=None, step=0.0):
    now = [0.0]

    def tick():
        now[0] += step
        return now[0]

    with terminal(), mock.patch.object(rig_tui, "collect_workflows", return_value=[]):
        rig_tui._paint(screen, Path("/fixture/project"), runtime=runtime, clock=clock or tick)
    return screen


def render(screen, snapshot, **kwargs):
    defaults = dict(tab="Jobs", selected=0, offset=0, follow=True, log_off=0, footer="",
                    snapshot_status="snapshot 1.0s old", requested=set(), draft=Draft(), workflows=[])
    defaults.update(kwargs)
    with terminal():
        offset = tui_view.render(screen, Path("/fixture/project"), snapshot, **defaults)
    screen.frames.append(list(screen.calls))
    return offset


class NotificationState(unittest.TestCase):
    def test_info_and_success_expire_after_four_seconds_errors_persist(self):
        now = [10.0]
        notes = Notifications(lambda: now[0])
        notes.info("saved")
        now[0] = 13.9
        self.assertEqual(notes.current().text, "saved")
        now[0] = 14.0
        self.assertIsNone(notes.current())
        notes.success("queued")
        now[0] = 18.0
        self.assertIsNone(notes.current())
        notes.error("lock busy")
        now[0] = 10_000
        self.assertEqual(notes.current().kind, "error")
        notes.info("another action result")
        self.assertEqual(notes.current().text, "another action result")
        notes.error("again")
        self.assertTrue(notes.dismiss())
        self.assertIsNone(notes.current())

    def test_board_info_expires_and_hints_stay_visible(self):
        runtime = Clocked([snap([job(1)])])
        screen = run(StrictScr(["o"] + [-1] * 6 + ["q"], h=12, w=80), runtime, step=1.0)
        shown = [any("no session for job-001" in call[2] for call in frame) for frame in screen.frames]
        self.assertTrue(any(shown))
        self.assertFalse(shown[-1])
        for index in range(len(screen.frames)):
            self.assertIn("q quit", screen.row(11, index))

    def test_error_persists_until_escape_or_next_result(self):
        runtime = Clocked([snap([job(1)])], results=[None, ActionResult("cancel:Jobs:job-001", error="lock busy")])
        screen = run(StrictScr([-1] * 8 + ["\x1b", -1, "q"], h=12, w=80), runtime, step=5.0)
        visible = [any("Action failed: lock busy" in call[2] for call in frame) for frame in screen.frames]
        self.assertTrue(all(visible[1:9]), visible)
        self.assertFalse(visible[-1])
        runtime = Clocked([snap([job(1)])], results=[
            None, ActionResult("a", error="first failure"), ActionResult("b", value="done later")])
        screen = run(StrictScr([-1, -1, -1, "q"], h=12, w=80), runtime, step=0.0)
        self.assertIn("first failure", screen.text(1))
        self.assertNotIn("first failure", screen.text(2))
        self.assertIn("done later", screen.text(2))

    def test_long_error_wraps_on_tall_screens_and_clips_on_short(self):
        long = "Action failed: " + "界 lock contention " * 12
        screen = StrictScr([], h=14, w=60)
        render(screen, snap([job(1)]), notice=Notice("error", long, 0))
        self.assertIn("Action failed", screen.row(11))
        self.assertTrue(screen.row(12).strip())
        self.assertIn("q quit", screen.row(13))
        short = StrictScr([], h=8, w=40)
        render(short, snap([job(1)]), notice=Notice("error", long, 0))
        self.assertTrue(short.row(6).endswith("…"))
        self.assertIn("q quit", short.row(7))

    def test_snapshot_status_shown_beside_notice_not_in_header(self):
        screen = StrictScr([], h=12, w=100)
        render(screen, snap([job(1)]), notice=Notice("info", "follow on", 0), snapshot_status="snapshot 2.0s old")
        self.assertIn("follow on", screen.row(10))
        self.assertIn("snapshot 2.0s old", screen.row(10))
        self.assertNotIn("snapshot", screen.row(0))
        self.assertIn("project", screen.row(0))


class TitlesAndActivity(unittest.TestCase):
    def test_title_is_first_nonempty_line_and_details_keep_full_task(self):
        row = job(1, task="\n\n   Ship the board  \nsecond line of context")
        self.assertEqual(tui_rows.row_title("Jobs", row), "Ship the board")
        self.assertEqual(first_line("  \n x \n"), "x")
        detail = "\n".join(tui_view.detail_document("Jobs", row))
        self.assertIn("second line of context", detail)

    def test_two_line_active_rows_show_doing_with_recorded_fallback(self):
        rows = [job(1, effective="ask", display_state="needs-input", task="decide", doing="",
                    display_reason="Bash: npm test"),
                job(2, task="build", doing="running unit tests"),
                job(3, effective="ok", display_state="failed", task="old", display_reason="timeout")]
        screen = StrictScr([], h=16, w=80)
        render(screen, snap(rows))
        self.assertIn("decide", screen.row(3))
        self.assertIn("↳ Bash: npm test", screen.row(4))
        self.assertIn("build", screen.row(5))
        self.assertIn("↳ running unit tests", screen.row(6))
        self.assertIn("old", screen.row(7))
        self.assertIn("codex · timeout", screen.row(8))

    def test_short_screen_is_activity_first_with_title_fallback(self):
        rows = [job(1, task="build", doing="running unit tests"), job(2, task="idle task", doing=""),
                job(3, effective="ok", display_state="completed-unverified", task="history", doing="stale doing")]
        screen = StrictScr([], h=10, w=80)
        render(screen, snap(rows))
        self.assertIn("running unit tests", screen.row(3))
        self.assertNotIn("build", screen.row(3))
        self.assertIn("idle task", screen.row(4))
        self.assertIn("history", screen.row(5))

    def test_renderer_performs_no_filesystem_io(self):
        rows = [job(1, dir="/nonexistent/job", activities=["one"]), job(2, effective="ok", display_state="verified")]
        guard = AssertionError("renderer touched the filesystem")
        with mock.patch("builtins.open", side_effect=guard), \
                mock.patch.object(Path, "exists", side_effect=guard), \
                mock.patch.object(Path, "stat", side_effect=guard), \
                mock.patch("os.stat", side_effect=guard):
            for h, w in ((10, 60), (24, 140)):
                render(StrictScr([], h=h, w=w), snap(rows))
                render(StrictScr([], h=h, w=w), snap(rows), detail=DetailState("Jobs", "job-001"))
                render(StrictScr([], h=h, w=w), snap(rows), help_state=HelpState())


class StateLabels(unittest.TestCase):
    CASES = [
        ("Needs input", dict(effective="ask", display_state="needs-input")),
        ("Needs input", dict(effective="unconfirmed", display_state="needs-input")),
        ("Needs input", dict(effective="ok", display_state="needs-input",
                             reservation={"needs_reconciliation": True, "stage": "running"})),
        ("Running", dict(effective="running", display_state="working")),
        ("Reserved", dict(effective="reserved", display_state="reserved")),
        ("Checking", dict(effective="running", display_state="verifying")),
        ("Unverified", dict(effective="ok", display_state="completed-unverified")),
        ("Verified", dict(effective="ok", display_state="verified", verification_summary={
            "state": "verified", "acceptance": "accepted", "freshness": "current"})),
        ("Unverified", dict(effective="ok", display_state="verified", verification_summary={
            "state": "verified", "acceptance": "accepted", "freshness": "stale"})),
        ("Unverified", dict(effective="ok", display_state="verified", verification_summary={})),
        ("Failed", dict(effective="fail", display_state="failed")),
        ("Cancelled", dict(effective="cancelled", display_state="cancelled",
                           reservation={"stage": "released"})),
        ("Cancelled", dict(effective="cancel_requested", display_state="needs-input",
                           cancellation_state="stopped", reservation={"stopped": True})),
        ("Stopping", dict(effective="cancelled", display_state="cancelled",
                          reservation={"stage": "running", "stopped": False})),
        ("Stop unclear", dict(effective="cancel_requested", display_state="needs-input",
                              cancellation_state="stop-unconfirmed")),
        ("Host stop", dict(effective="cancel_requested", display_state="needs-input",
                           cancellation_state="native-cancel-required")),
    ]

    def test_every_label_case_is_presentation_only(self):
        seen = set()
        for expected, fields in self.CASES:
            with self.subTest(expected=expected, fields=fields):
                row = job(1, **fields)
                before = copy.deepcopy(row)
                label, tone, explanation = tui_rows.job_state(row)
                self.assertEqual(label, expected)
                self.assertTrue(explanation)
                self.assertEqual(row, before)
                seen.add(label)
        local = job(1)
        self.assertEqual(tui_rows.job_state(local, {"cancel:Jobs:job-001"})[0], "Stopping")
        finished = job(1, effective="ok", display_state="completed-unverified")
        self.assertEqual(tui_rows.job_state(finished, {"cancel:Jobs:job-001"})[0], "Unverified")
        seen.add("Stopping")
        self.assertEqual(seen, set(LABELS))

    def test_unprojected_history_row_is_never_verified(self):
        raw = {"job_id": "old", "status": "ok", "effective": "ok", "task": "history"}
        self.assertEqual(tui_rows.job_state(raw)[0], "Unverified")

    def test_stop_states_explain_their_requirement(self):
        explanations = {label: tui_rows.job_state(job(1, **fields))[2] for label, fields in self.CASES}
        self.assertIn("unconfirmed", explanations["Stop unclear"])
        self.assertIn("owning host", explanations["Host stop"])
        self.assertIn("release", explanations["Stopping"])
        self.assertIn("no longer running", explanations["Cancelled"])
        self.assertIn("requested from this board",
                      tui_rows.job_state(job(1), {"cancel:Jobs:job-001"})[2])

    def test_tones_follow_colour_contract(self):
        tone = {label: tui_rows.job_state(job(1, **fields))[1] for label, fields in self.CASES}
        for label in ("Needs input", "Stopping", "Stop unclear", "Host stop"):
            self.assertEqual(tone[label], tui_rows.ATTENTION)
        for label in ("Running", "Reserved", "Checking"):
            self.assertEqual(tone[label], tui_rows.ACTIVE)
        self.assertEqual(tone["Verified"], tui_rows.VERIFIED)
        self.assertEqual(tone["Failed"], tui_rows.FAILED)
        self.assertEqual(tone["Unverified"], tui_rows.HISTORY)
        self.assertEqual(tone["Cancelled"], tui_rows.HISTORY)

    def test_selected_row_has_marker_and_text_not_only_colour(self):
        screen = StrictScr([], h=10, w=80)
        render(screen, snap([job(1), job(2, effective="fail", display_state="failed")]), selected=1)
        self.assertTrue(screen.row(4).startswith("▸ Failed"))
        self.assertTrue(screen.row(3).startswith("  Running"))

    def test_help_legend_lists_every_label(self):
        legend = "\n".join(tui_view.help_lines())
        for label in LABELS:
            self.assertIn(label, legend)


class Details(unittest.TestCase):
    def board(self, keys, snapshots, *, h=16, w=80, workflows=None):
        runtime = Clocked(snapshots)
        if workflows is not None:
            for value in snapshots:
                object.__setattr__(value, "workflows", workflows)
        return run(StrictScr(keys, h=h, w=w), runtime), runtime

    def test_details_bind_to_id_through_reorder_and_report_missing_items(self):
        first = job(1, task="first job")
        second = job(2, task="second job")
        reordered = snap([job(9, effective="ask", display_state="needs-input"), second, first])
        screen, _ = self.board(["j", "\n", -1, -1, "q"], [snap([first, second]), snap([first, second]),
                                                           snap([first, second]), reordered,
                                                           snap([first])])
        self.assertIn("Details · Jobs · job-002", screen.text(2))
        self.assertIn("second job", screen.text(3))
        self.assertIn("Item no longer available", screen.text(4))

    def test_details_open_for_each_list_tab_wide_and_narrow(self):
        workflow = {"workflow_id": "wf-1", "status": "blocked", "title": "ship it", "accepted": 0,
                    "required": 2, "running": 0, "ask": 0, "blocker": "node failed",
                    "next_parent_action": {"kind": "resolve", "node_id": "n1"}, "owner_token": "secret"}
        pending = [{"id": "queue-1", "text": "queued work\nfull second line", "waiting_reason": "Awaiting parent claim"}]
        for w in (60, 140):
            with self.subTest(w=w):
                state = snap([job(1, task="job task", token_usage={"input": 1200})], pending)
                screen, _ = self.board(["\n", "\t", "\n", "\t", "\n", "q"], [state], w=w, workflows=[workflow])
                self.assertIn("Tokens  1.2k in", screen.text(1))
                self.assertIn("full second line", screen.text(3))
                self.assertIn("Blocked: node failed", screen.text(5))
                self.assertNotIn("secret", screen.text(5))

    def test_scroll_close_switch_and_quit(self):
        long_task = "\n".join(f"line {index}" for index in range(60))
        state = snap([job(1, task=long_task)], [{"id": "q-1", "text": "queued"}])
        screen, _ = self.board(["\n", "j", "j", curses.KEY_NPAGE, curses.KEY_PPAGE, "k", "\x1b", -1,
                                "\n", "\t", "\n", curses.KEY_BTAB, "\n", "\r", "\n", "q", "x"], [state])
        tops = [screen.row(2, index) for index in range(1, 7)]
        self.assertNotEqual(tops[0], tops[1])
        self.assertNotEqual(tops[2], tops[3])
        self.assertEqual(tops[1], tops[5])
        self.assertIn("Jobs 1-1/1", screen.text(8))
        self.assertIn("Queue 1-1/1", screen.text(10))
        self.assertIn("Details · Queue", screen.text(11))
        self.assertIn("Jobs 1-1/1", screen.text(12))
        self.assertIn("Details · Jobs", screen.text(13))
        self.assertIn("Jobs 1-1/1", screen.text(14))
        self.assertEqual(len(screen.frames), 16)

    def test_mutation_keys_are_disabled_inside_details_and_help(self):
        ask = job(1, effective="ask", display_state="needs-input")
        settings = [{"id": "jev", "state": "missing", "text": "Jev"}]
        for opener in ("\n", "?"):
            with self.subTest(opener=opener):
                keys = [opener] + list("xyneocdtg") + ["\x1b", -1, "q"]
                with mock.patch.object(rig_tui.rig_jobs, "cancel_job") as cancel, \
                        mock.patch.object(rig_tui.rig_jobs, "answer_pending") as answer, \
                        mock.patch.object(rig_tui.rig_queue, "add_item") as enqueue:
                    screen, runtime = self.board(keys, [snap([ask], settings=settings)])
                self.assertEqual(runtime.submitted, [])
                cancel.assert_not_called()
                answer.assert_not_called()
                enqueue.assert_not_called()
                text = "\n".join(screen.text(index) for index in range(len(screen.frames)))
                self.assertNotIn("y confirm · any other key aborts", text)
                self.assertNotIn("enqueue 0/2000", text)
                self.assertIn("Jobs 1-1/1", screen.text(-1))
        screen, _ = self.board(["\n", "x", "q"], [snap([ask])])
        self.assertIn("Details are read-only", screen.text(2))

    def test_help_scrolls_closes_with_escape_or_question_and_q_quits(self):
        # Esc is decoded when the next key (or idle tick) arrives, hence the -1 no-op.
        screen, _ = self.board(["?", "j", "j", "\x1b", -1, "?", curses.KEY_NPAGE, "?", "?", "x", "q", "x"],
                               [snap([job(1)])], h=12)
        self.assertIn("Help", screen.text(1))
        self.assertNotEqual(screen.row(2, 1), screen.row(2, 3))
        self.assertIn("Jobs 1-1/1", screen.text(5))
        self.assertNotEqual(screen.row(2, 6), screen.row(2, 7))
        self.assertIn("Jobs 1-1/1", screen.text(8))
        self.assertIn("Help", screen.text(9))
        self.assertNotIn("y confirm · any other key aborts", screen.text(10))
        self.assertEqual(len(screen.frames), 11)

    def test_activity_toggle_follow_and_manual_scroll_survive_new_activity(self):
        acts = [f"activity {index}" for index in range(40)]
        more = acts + [f"activity {index}" for index in range(40, 45)]
        snapshots = [snap([job(1, activities=acts)])] * 4 + [snap([job(1, activities=more)])] * 6
        screen, _ = self.board(["\n", "l", "k", "k", -1, "f", "l", "l", "q"], snapshots, h=12)
        self.assertIn("Details · Jobs", screen.text(1))
        self.assertIn("activity 39", screen.text(2))
        self.assertIn("follow on", screen.text(2))
        held = screen.text(4)
        self.assertNotIn("activity 39", held)
        self.assertIn("follow off", held)
        self.assertEqual(screen.row(2, 4), screen.row(2, 5))
        self.assertIn("activity 44", screen.text(6))
        self.assertIn("Details · Jobs", screen.text(7))
        self.assertIn("Activity · Jobs", screen.text(8))

    def test_narrow_l_opens_activity_and_wide_l_toggles_pane(self):
        state = snap([job(1, activities=["edited file"])])
        narrow, _ = self.board(["l", "q"], [state], w=80)
        self.assertIn("Activity · Jobs", narrow.text(1))
        wide, _ = self.board(["l", "q"], [state], w=120)
        self.assertIn("Activity · follow on", wide.text(1))
        self.assertIn("edited file", wide.text(1))


class TabsAndLayout(unittest.TestCase):
    def test_tab_strip_counts_highlight_and_compact_widths(self):
        rows = [job(1, effective="ask", display_state="needs-input"),
                job(2, effective="cancel_requested", display_state="needs-input", cancellation_state="stop-unconfirmed"),
                job(3), job(4, effective="ok", display_state="completed-unverified")]
        workflows = [{"workflow_id": "w", "status": "attention"}, {"workflow_id": "v", "status": "running"}]
        pending = [{"id": "q1", "text": "a"}, {"id": "q2", "text": "b"}, {"id": "q3", "text": "c"}]
        state = snap(rows, pending, workflows=workflows)
        self.assertEqual(tui_rows.tab_counts(state), {"Jobs": 2, "Queue": 3, "Workflows": 1, "Settings": 0})
        for w in (40, 60, 80, 140):
            with self.subTest(w=w):
                screen = StrictScr([], h=10, w=w)
                render(screen, state, tab="Queue", workflows=workflows)
                tabs = [call for call in screen.calls if call[0] == 1]
                self.assertEqual(len(tabs), 4)
                strip = "".join(call[2] for call in tabs)
                self.assertIn("2!", strip)
                self.assertIn("3", strip)
                self.assertIn("1!", strip)
                active = [call for call in tabs if call[4] & curses.A_REVERSE]
                self.assertEqual(len(active), 1)
                self.assertIn("Q", active[0][2])
        segments = tui_chrome.tab_segments("Jobs", {"Jobs": 12, "Queue": 300, "Workflows": 4}, 40)
        self.assertEqual(len(segments), 4)
        self.assertLessEqual(sum(_text_width(text) for text, _ in segments), 40)

    def test_header_is_project_and_health_and_empty_states_help(self):
        screen = StrictScr([], h=10, w=80)
        render(screen, snap([]), tab="Queue")
        self.assertIn("project", screen.row(0))
        self.assertIn("need input", screen.row(0))
        self.assertIn("Press e to enqueue", screen.text())
        screen = StrictScr([], h=10, w=80)
        render(screen, snap([]))
        self.assertIn("No jobs", screen.text())
        self.assertIn("Press e to enqueue", screen.text())

    def test_row_order_marker_state_task_then_abbreviated_id(self):
        long_id = "20261003T044507Z-31263-e4f0a4c7b1544f4295d32d1be1185f8b"
        row = job(1, job_id=long_id, task="short", token_usage={"input": 900, "output": 20})
        text = tui_rows.entry_lines("Jobs", row, 60, selected=True)[0]
        self.assertTrue(text.startswith("▸ Running"))
        self.assertLess(text.index("Running"), text.index("short"))
        self.assertIn(abbrev_id(long_id), text)
        self.assertNotIn(long_id, text)
        self.assertNotIn("900", text)
        crowded = tui_rows.entry_lines("Jobs", {**row, "task": "x" * 80}, 60)[0]
        self.assertNotIn("1185f8b", crowded)
        detail = "\n".join(tui_view.detail_document("Jobs", row))
        self.assertIn(long_id, detail)
        self.assertIn("Tokens  900 in / 20 out", detail)
        self.assertIn("Tokens  unknown", "\n".join(tui_view.detail_document("Jobs", job(2))))

    def test_layout_thresholds(self):
        self.assertTrue(tui_chrome.layout(8, 39).tiny)
        self.assertTrue(tui_chrome.layout(7, 40).tiny)
        self.assertFalse(tui_chrome.layout(8, 40).tiny)
        self.assertFalse(tui_chrome.layout(30, 99).split)
        self.assertTrue(tui_chrome.layout(30, 100).split)
        self.assertEqual(tui_chrome.layout(11, 80).entry_lines, 1)
        self.assertEqual(tui_chrome.layout(12, 80).entry_lines, 2)
        self.assertEqual(tui_chrome.layout(12, 80, tab="Queue").entry_lines, 1)
        self.assertEqual(tui_chrome.layout(24, 80).capacity, 9)
        self.assertEqual(tui_chrome.layout(10, 80).capacity, 5)

    def test_all_sizes_clip_unicode_safely(self):
        nasty = "修复 é́ combining 🚀 wide " * 8 + "\x1b[31m\tcontrol"
        rows = [job(index, task=nasty, doing=nasty, job_id=f"{'界' * 20}-{index}",
                    activities=[nasty] * 5) for index in range(12)]
        pending = [{"id": "q" * 60, "text": nasty, "waiting_reason": nasty}]
        workflows = [{"workflow_id": "w" * 70, "status": "running", "title": nasty}]
        state = snap(rows, pending, workflows=workflows)
        for w in (40, 60, 80, 100, 140):
            for h in (8, 11, 12, 30):
                for overlay in ({}, {"detail": DetailState("Jobs", rows[3]["job_id"])},
                                {"detail": DetailState("Queue", "q" * 60)}, {"help_state": HelpState()},
                                {"notice": Notice("error", nasty, 0)},
                                {"confirm": {"tab": "Jobs", "id": rows[0]["job_id"], "task": nasty}}):
                    for tab in ("Jobs", "Queue", "Workflows"):
                        with self.subTest(w=w, h=h, overlay=list(overlay), tab=tab):
                            screen = StrictScr([], h=h, w=w)
                            render(screen, state, tab=tab, selected=5, workflows=workflows, **overlay)
                            self.assertTrue(screen.calls)
                            self.assertFalse(any("\x1b" in call[2] or "\t" in call[2] for call in screen.calls))
                            if (overlay == {} and tab == "Jobs"):
                                self.assertEqual("│" in screen.text(), w >= 100)
        for h, w in ((7, 40), (8, 39), (3, 20)):
            screen = StrictScr([], h=h, w=w)
            render(screen, state)
            self.assertIn("Terminal", screen.text())

    def test_wrap_cells_respects_cells_and_keeps_clusters(self):
        for text, width in (("界" * 25, 9), ("é" * 30, 7), ("Title   " + "word " * 20, 30)):
            lines = wrap_cells(text, width)
            self.assertTrue(all(_text_width(line) <= width for line in lines), lines)
            self.assertFalse(any(line and line[0] == "́" for line in lines), lines)

    def test_tiny_terminal_only_quits_and_blocks_actions(self):
        runtime = Clocked([snap([job(1)])])
        screen = run(StrictScr(["x", "y", "e", "\n", "q", "x"], h=7, w=60), runtime)
        self.assertEqual(runtime.submitted, [])
        self.assertIn("Terminal too small", screen.text(0))
        self.assertEqual(len(screen.frames), 5)
        self.assertEqual(runtime.budgets[0]["rows"], 0)

    def test_snapshot_budget_counts_two_line_entries(self):
        for h, expected in ((24, 9), (12, 3), (10, 5)):
            runtime = Clocked([snap([job(index) for index in range(40)])])
            run(StrictScr(["q"], h=h, w=80), runtime)
            self.assertEqual(runtime.budgets[0]["rows"], expected)

    def test_resize_keeps_selection_by_id_and_clamps_scroll(self):
        class Resizing(StrictScr):
            def get_wch(self):
                key = super().get_wch()
                if key == curses.KEY_RESIZE:
                    self.h, self.w = 9, 44
                return key

        listing = [job(index, task=f"wide 界 task {index}") for index in range(30)]
        screen = Resizing(["j"] * 20 + [curses.KEY_RESIZE, -1, "q"], h=30, w=140)
        run(screen, Clocked([snap(listing)]))
        after = screen.frames[-1]
        selected = [call[2] for call in after if call[2].startswith("▸")]
        self.assertEqual(len(selected), 1)
        self.assertIn("task 20", selected[0])
        self.assertTrue(all(call[1] + _text_width(call[2]) <= 44 for call in after))

    def test_selection_survives_reorder_and_clamps_when_target_disappears(self):
        rows = [job(index) for index in range(5)]
        reordered = rows[1:] + rows[:1]
        screen = run(StrictScr(["j", "j", -1, -1, "q"], h=10, w=80),
                     Clocked([snap(rows), snap(rows), snap(rows), snap(reordered), snap(rows[:1])]))
        self.assertIn("task 2", [call[2] for call in screen.frames[3] if call[2].startswith("▸")][0])
        self.assertIn("task 0", [call[2] for call in screen.frames[4] if call[2].startswith("▸")][0])


class Safeguards(unittest.TestCase):
    def test_confirmation_and_editor_own_the_bottom_rows(self):
        runtime = Clocked([snap([job(1, task="stop me")])])
        screen = run(StrictScr(["x", "\x1b", -1, "e", "a", "\x1b", -1, "q"], h=12, w=80), runtime)
        self.assertIn("Stop job job-001 — stop me", screen.row(10, 1))
        self.assertEqual(screen.row(11, 1), "y confirm · any other key aborts")
        self.assertIn("Cancellation aborted job-001", screen.text(3))
        self.assertTrue(screen.row(11, 5).startswith("enqueue 1/2000: a"))
        self.assertIn("Enter queues", screen.row(10, 5))
        self.assertIn("Jobs 1-1/1", screen.text(7))
        self.assertEqual(runtime.submitted, [])

    def test_confirmed_cancel_is_suppressed_while_pending_and_quit_does_not_stop(self):
        runtime = Clocked([snap([job(1)])])
        with mock.patch.object(rig_tui.rig_jobs, "cancel_job") as cancel:
            screen = run(StrictScr(["x", "y", "x", "q"], h=12, w=80), runtime)
        self.assertEqual(runtime.submitted, ["cancel:Jobs:job-001"])
        self.assertIn("Stopping", screen.row(3, 2))
        self.assertIn("stop already requested job-001", screen.text(3))
        cancel.assert_not_called()

    def test_esc_dismisses_board_error_without_other_effects(self):
        runtime = Clocked([snap([job(1)])], results=[ActionResult("x", error="denied")])
        screen = run(StrictScr([-1, "\x1b", -1, "q"], h=12, w=80), runtime)
        self.assertIn("Action failed: denied", screen.text(1))
        self.assertNotIn("Action failed", screen.text(3))
        self.assertEqual(runtime.submitted, [])


if __name__ == "__main__":
    unittest.main()
