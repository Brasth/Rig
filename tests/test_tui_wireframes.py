"""Wireframe acceptance: cell budgets, complete selection, truthful request details, fallbacks."""
import curses
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import tui_chrome
import tui_rows
import tui_style
import tui_view
from tui_detail_state import DetailState
from tui_editor import Draft
from tui_runtime import Snapshot
from tui_text import _text_width
from test_tui_redesign import StrictScr, job, snap, render


class Wireframes(unittest.TestCase):
    def test_layout_matches_reference_cell_budgets(self):
        for h, w, left, body in ((32, 120, 62, 26), (24, 80, 80, 18),
                                (12, 40, 40, 6), (10, 60, 60, 5), (32, 160, 72, 26)):
            with self.subTest(size=(w, h)):
                lay = tui_chrome.layout(h, w)
                self.assertEqual((lay.left_w, lay.list_rows), (left, body))
                self.assertEqual(lay.message_y, h - 2)

    def test_grouped_scrolling_never_splits_or_loses_selected_entry(self):
        rows = ([job(i, effective="ask", display_state="needs-input") for i in range(3)] +
                [job(i) for i in range(3, 16)] +
                [job(i, effective="ok", display_state="completed-unverified") for i in range(16, 30)])
        for budget in (3, 6, 18, 26):
            for grouped in (False, True):
                offset = 0
                for selected in list(range(len(rows))) + list(reversed(range(len(rows)))):
                    offset, stop, items = tui_view.board_window(rows, selected, offset, budget, 2, grouped=grouped)
                    entries = [value for kind, value in items if kind == "entry"]
                    self.assertIn(selected, entries)
                    self.assertEqual(entries, list(range(offset, stop)))
                    used = sum(2 if kind == "entry" else 1 for kind, _ in items)
                    self.assertLessEqual(used, budget)
                    for kind, value in items:
                        if kind == "above":
                            self.assertEqual(value, offset)
                        if kind == "below":
                            self.assertEqual(value, len(rows) - stop)

    def test_request_precedes_metadata_and_read_only_guidance_is_explicit(self):
        row = job(1, effective="ask", display_state="needs-input", model="guessed-model",
                  model_source="inferred", ask={"tool_name": "Bash", "input": {
                      "command": "pnpm test --filter auth -- --update-snapshots", "cwd": "packages/auth"},
                      "asked_at": "2026-10-04T14:02:07Z"})
        board = "\n".join(tui_view.job_detail_lines(row))
        detail = "\n".join(tui_view.job_detail_lines(row, read_only=True))
        self.assertLess(board.index("command"), board.index("model"))
        self.assertIn("asked    2026-10-04T14:02:07Z", board)
        self.assertIn("answer   y allow", board)
        self.assertIn("Esc to the board, then y allow or n deny", detail)
        self.assertNotIn("guessed-model", detail)
        self.assertIn("not recorded", detail)
        for w in (40, 80, 120):
            screen = StrictScr([], h=24, w=w)
            render(screen, snap([row]), detail=DetailState("Jobs", row["job_id"]))
            self.assertIn("read-only", screen.row(1))
            self.assertIn("rows", screen.row(2))
            self.assertNotIn("x stop", screen.row(23))

    def test_narrow_permission_command_is_visible_without_scrolling(self):
        row = job(1, effective="ask", display_state="needs-input", ask={"tool_name": "Bash", "input": {
            "cwd": "packages/auth", "command": "pnpm test --filter auth -- --update-snapshots"}})
        screen = StrictScr([], h=12, w=40)
        render(screen, snap([row]), detail=DetailState("Jobs", row["job_id"]))
        body = "\n".join(screen.row(y) for y in range(3, 8))
        self.assertIn("run  pnpm test", body)
        self.assertIn("--update-snapshots", body)
        self.assertIn("cwd  packages/auth", body)

    def test_old_verified_assessment_never_claims_current_acceptance(self):
        row = job(1, effective="ok", display_state="verified", verification_summary={
            "state": "verified", "acceptance": "accepted", "freshness": "stale"})
        lines = tui_view.job_detail_lines(row)
        self.assertIn("Unverified", lines[0])
        self.assertIn("verify   not accepted for current snapshot", lines)

    def test_loading_never_claims_zero_health_or_empty_project(self):
        screen = StrictScr([], h=24, w=80)
        render(screen, Snapshot())
        self.assertIn("loading", screen.row(0))
        self.assertNotIn("0 running", screen.row(0))
        self.assertNotIn("No jobs", screen.text())
        self.assertIn("Loading snapshot", screen.text())

    def test_stale_snapshot_retains_data_and_retry_hint_even_with_long_error(self):
        screen = StrictScr([], h=24, w=120)
        render(screen, snap([job(1, task="retained task")]),
               snapshot_status="refresh failed: " + "cause " * 100)
        self.assertIn("stale", screen.row(0))
        self.assertIn("Stale", screen.text())
        self.assertIn("retained task", screen.text())
        self.assertIn("r retry", screen.row(22))

    def test_queue_rows_show_waiting_reason_and_recorded_priority(self):
        row = {"id": "q1", "text": "Implement audit trail", "priority": 7,
               "worker": "claude", "waiting_reason": "capacity full (4/4)"}
        lines = tui_rows.entry_lines("Queue", row, 62, two_line=True)
        self.assertIn("p7", lines[0])
        self.assertIn("capacity full", lines[1])
        self.assertIn("claude", lines[1])

    def test_workflow_acceptance_count_survives_long_title(self):
        row = {"workflow_id": "wf-1", "status": "running", "title": "very long workflow " * 20,
               "accepted": 2, "required": 4}
        for width in (40, 62, 80):
            line = tui_rows.entry_lines("Workflows", row, width)[0]
            self.assertIn("accepted 2/4", line)
            self.assertLessEqual(_text_width(line), width)

    def test_editor_cursor_and_whole_draft_survive_wrapping_and_resize(self):
        text = "hello 世界 é " * 100
        draft = Draft(text=text, cursor=len(text), active=True)
        for h, w in ((8, 40), (12, 40), (24, 80), (32, 120)):
            screen = StrictScr([], h=h, w=w)
            screen.move = mock.Mock()
            render(screen, snap([job(1)]), draft=draft)
            y, x = screen.move.call_args.args
            self.assertTrue(0 <= y < h - 2)
            self.assertTrue(0 <= x < w - 1)
            self.assertEqual(draft.text, text)
            self.assertIn("Enqueue task", screen.text())
            self.assertIn("Esc discard", screen.row(h - 1))
            self.assertNotIn("q quit", screen.row(h - 1))
        lines = tui_view._editor_lines(text, 37)
        self.assertEqual("".join(line for _, line in lines), text)
        self.assertTrue(all(_text_width(line) <= 37 for _, line in lines))


class Fallbacks(unittest.TestCase):
    def test_ascii_and_cjk_keep_cell_budgets_and_status_words(self):
        for variable in ("RIG_TUI_ASCII", "RIG_TUI_CJK"):
            with mock.patch.dict(os.environ, {variable: "1"}):
                screen = StrictScr([], h=24, w=120)
                render(screen, snap([job(1)]))
                self.assertNotIn("━", screen.text())
                self.assertNotIn("│", screen.text())
                self.assertIn("Running", screen.text())
                self.assertIn("|", screen.text())

    def test_no_color_does_not_initialize_colors(self):
        with mock.patch.dict(os.environ, {"NO_COLOR": ""}), mock.patch.object(curses, "start_color") as start:
            tui_style.initialize()
            self.assertEqual(tui_style.pair(4), 0)
            self.assertTrue(tui_style.selection() & curses.A_REVERSE)
            start.assert_not_called()

    def test_color_depth_and_unsupported_terminal(self):
        for depth, expected in ((8, curses.COLOR_CYAN), (256, 111)):
            with mock.patch.dict(os.environ, {}, clear=True), \
                    mock.patch.object(curses, "has_colors", return_value=True), \
                    mock.patch.object(curses, "COLORS", depth, create=True), \
                    mock.patch.object(curses, "start_color"), \
                    mock.patch.object(curses, "use_default_colors"), \
                    mock.patch.object(curses, "init_pair") as init:
                tui_style.initialize()
                self.assertIn(mock.call(4, expected, -1), init.call_args_list)
                self.assertEqual(init.call_count, 7)
        with mock.patch.object(curses, "has_colors", side_effect=curses.error):
            tui_style.initialize()
            self.assertEqual(tui_style.pair(4), 0)


if __name__ == "__main__":
    unittest.main()
