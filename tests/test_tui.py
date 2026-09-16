#!/usr/bin/env python3
import json
import sys
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import rig_tui  # noqa: E402
from tui_runtime import Snapshot  # noqa: E402


class FakeScr:
    def __init__(self, h: int, w: int) -> None:
        self.h = h
        self.w = w
        self.calls: list[tuple] = []

    def getmaxyx(self) -> tuple[int, int]:
        return self.h, self.w

    def addnstr(self, y: int, x: int, text: str, n: int, attr: int = 0) -> None:
        written = min(n, len(text))
        if y == self.h - 1 and x + written >= self.w:
            raise rig_tui.curses.error("addnwstr() returned ERR")
        self.calls.append((y, x, text, n, attr))


class LastCellClip(unittest.TestCase):
    def test_room_last_row_leaves_one_cell(self):
        self.assertEqual(rig_tui._room(23, 0, 24, 80), 79)
        self.assertEqual(rig_tui._room(23, 10, 24, 80), 69)
        self.assertEqual(rig_tui._room(23, 79, 24, 80), 0)

    def test_room_other_rows_full_width(self):
        self.assertEqual(rig_tui._room(0, 0, 24, 80), 80)
        self.assertEqual(rig_tui._room(10, 5, 24, 80), 75)

    def test_enqueue_prompt_does_not_write_bottom_right(self):
        scr = FakeScr(24, 80)
        prompt = "enqueue: "
        rig_tui._add(scr, 23, 0, prompt + " " * 80, rig_tui.curses.A_REVERSE)
        self.assertEqual(len(scr.calls), 1)
        y, x, text, n, _attr = scr.calls[0]
        self.assertEqual((y, x), (23, 0))
        self.assertEqual(n, 79)
        self.assertEqual(len(text), 79)
        self.assertLess(x + n, 80)

    def test_footer_as_wide_as_screen_does_not_raise(self):
        scr = FakeScr(24, 80)
        rig_tui._add(scr, 23, 0, "x" * 200, rig_tui.curses.A_REVERSE, width=80)
        self.assertEqual(scr.calls[0][3], 79)

    def test_title_row_can_use_full_width(self):
        scr = FakeScr(24, 80)
        rig_tui._add(scr, 0, 0, "t" * 80, rig_tui.curses.A_REVERSE, width=80)
        self.assertEqual(scr.calls[0][3], 80)


class BoardScr(FakeScr):
    def __init__(self, keys, h=10, w=100):
        super().__init__(h, w)
        self.keys = iter(keys)
        self.frames = []

    def erase(self):
        self.calls = []

    def refresh(self):
        pass

    def get_wch(self):
        self.frames.append(list(self.calls))
        key = next(self.keys, "q")
        return chr(key) if isinstance(key, int) and 0 <= key < 128 else key

    def nodelay(self, _value):
        pass

    timeout = nodelay
    scrollok = nodelay
    keypad = nodelay

    def move(self, *_args):
        pass


class ScriptedRuntime:
    """Inject complete worker results at frame boundaries, without scheduler races."""

    def __init__(self, snapshots):
        self.snapshot = snapshots[0]
        self.remaining = iter(snapshots[1:])
        self.revision = 0
        self.scanning = False
        self.snapshot_error = ""
        self.first = True

    def poll(self):
        if self.first:
            self.first = False
        else:
            newer = next(self.remaining, None)
            if newer is not None:
                self.snapshot = newer
                self.revision += 1
        return []

    def request_snapshot(self, **_kwargs):
        pass

    def refresh(self):
        pass

    def close(self):
        pass


class BoardProjection(unittest.TestCase):
    @staticmethod
    def job(index):
        return {"job_id": f"job-{index:03}", "worker": "codex", "role": "implement", "model": "",
                "effective": "running", "task": "scoped task", "activities": []}

    @staticmethod
    def project(job, **_kwargs):
        return {**job, "display_state": "working", "display_reason": "", "display_action": "",
                "verification_summary": {"state": "pending"}, "independence": "unknown"}

    def paint(self, screen, snapshots, times=None, workflows=None):
        snaps = []
        for rows in snapshots:
            snap = Snapshot(jobs=[self.project(row) for row in rows], captured_at=1)
            if workflows is not None:
                object.__setattr__(snap, "workflows", workflows)
            snaps.append(snap)
        runtime = ScriptedRuntime(snaps)
        with ExitStack() as stack:
            for name in ("curs_set", "use_default_colors", "init_pair", "noecho", "set_escdelay"):
                stack.enter_context(mock.patch.object(rig_tui.curses, name))
            stack.enter_context(mock.patch.object(rig_tui.curses, "color_pair", return_value=0))
            stack.enter_context(mock.patch.object(rig_tui.rig_jobs, "list_jobs", side_effect=snapshots))
            projected = stack.enter_context(mock.patch.object(rig_tui.rig_jobs, "project_job", side_effect=self.project, create=True))
            stack.enter_context(mock.patch.object(rig_tui.rig_queue, "list_items", return_value=[]))
            stack.enter_context(mock.patch.object(rig_tui.rig_queue, "max_running", return_value=3))
            mutations = [stack.enter_context(mock.patch.object(module, name)) for module, name in (
                (rig_tui.rig_jobs, "cancel_job"), (rig_tui.rig_jobs, "answer_pending"),
                (rig_tui.rig_queue, "add_item"),
            )]
            rig_tui._paint(screen, Path("/fixture"), runtime=runtime)
            for mutation in mutations:
                mutation.assert_not_called()
            return projected.call_args_list

    def test_every_job_can_be_selected_and_remains_visible(self):
        listing = [self.job(index) for index in range(25)]
        screen = BoardScr([ord("j")] * 24 + [ord("q")])
        self.paint(screen, [listing])
        for index, frame in enumerate(screen.frames):
            left = [call[2] for call in frame if call[1] == 0 and 2 <= call[0] < screen.h - 1]
            self.assertTrue(any(f"job-{index:03}" in line for line in left), (index, left))
        self.assertTrue(any("Jobs 19-25/25" in call[2] for call in screen.frames[-1]))

    def test_refresh_preserves_selected_job_when_order_changes(self):
        first, second = self.job(1), self.job(2)
        screen = BoardScr([ord("j"), ord("q")])
        self.paint(screen, [[first, second], [second, first]], times=[10, 11])
        detail_id = next(call[2] for call in screen.frames[-1] if call[0] == 2 and call[1] > 33)
        self.assertEqual(detail_id, second["job_id"])

    def test_visible_rows_share_cache_and_history_is_not_refreshed(self):
        listing = [self.job(index) for index in range(1000)]
        with mock.patch.object(rig_tui.rig_jobs, "project_job", side_effect=self.project, create=True) as project:
            visible = rig_tui._visible_jobs(listing, Path("/fixture"), 50, 57)
        self.assertEqual([row["job_id"] for row in visible], [f"job-{index:03}" for index in range(50, 57)])
        self.assertEqual(project.call_count, 7)
        self.assertEqual(len({id(call.kwargs["cache"]) for call in project.call_args_list}), 1)
        self.assertTrue(all(call.kwargs["refresh"] for call in project.call_args_list))
        self.assertTrue(all("display_state" not in row for row in listing))

    def test_small_terminal_does_not_refresh_undisplayed_subjects(self):
        screen = BoardScr([ord("q")], h=6, w=30)
        self.assertEqual(self.paint(screen, [[self.job(1)]]), [])
        self.assertTrue(any("terminal too small" in call[2] for call in screen.frames[0]))

    def test_narrow_board_keeps_job_identity_visible(self):
        screen = BoardScr([ord("q")], h=10, w=65)
        with mock.patch.object(self, "project", side_effect=lambda job, **kwargs: {
            **job, "display_state": "completed-unverified", "verification_summary": {"state": "pending"},
        }):
            self.paint(screen, [[self.job(1)]])
        left = [call[2] for call in screen.frames[0] if call[1] == 0 and call[0] == 2]
        self.assertIn("job-001", left[0])

    def test_details_keep_action_truthful_model_and_literal_scope(self):
        job = {**self.job(1), "display_state": "needs-input", "display_reason": "owner liveness unknown",
               "display_action": "rig job reconcile job-001", "independence": "unknown", "review_completed": False,
               "reservation": {"stage": "verifying", "slot_held": False, "access": "write", "files": [" [literal].txt "]}}
        detail = rig_tui._detail_lines(job)
        self.assertEqual(detail[2], job["display_action"])
        self.assertIn("model unknown", "\n".join(detail))
        self.assertIn('files held (write): [" [literal].txt "]', detail)
        self.assertIn("execution slot free", detail)
        self.assertIn("independent review unknown; not completed", detail)

    def test_board_uses_shared_projection_for_success_without_acceptance(self):
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary)
            folder = repo / ".rig" / "jobs" / "legacy-ok"
            folder.mkdir(parents=True)
            (folder / "meta.json").write_text(json.dumps({
                "job_id": "legacy-ok", "worker": "codex", "role": "implement", "status": "ok",
                "execution_mode": "live", "model": "gpt-5.6-luna", "model_source": "selected",
                "files": ["file.txt"],
            }))
            job = rig_tui.rig_jobs.load_job(folder)
            projected = rig_tui._visible_jobs([job], repo, 0, 1)[0]
        self.assertEqual(projected["display_state"], "completed-unverified")
        self.assertEqual(projected["effective"], "ok")
        self.assertIn("status completed-unverified", rig_tui._detail_lines(projected))
        self.assertIn("model gpt-5.6-luna", "\n".join(rig_tui._detail_lines(projected)))

    def test_board_does_not_present_inferred_model_as_actual(self):
        job = {**self.job(1), "model": "gpt-5.6-luna", "model_inferred": True,
               "model_source": "unknown", "display_state": "completed-unverified"}
        detail = "\n".join(rig_tui._detail_lines(job))
        self.assertIn("model unknown", detail)
        self.assertNotIn("gpt-5.6-luna", detail)

    def test_permission_actions_have_separate_early_detail_lines(self):
        job = rig_tui.rig_jobs.project_job({**self.job(1), "effective": "ask", "ask": {"preview": "permission"}})
        detail = rig_tui._detail_lines(job)
        self.assertEqual(detail[2:4], ["rig job allow job-001", "rig job deny job-001"])

    def _workflow(self, workflow_id, **fields):
        row = {"workflow_id": workflow_id, "status": "running", "accepted": 1, "required": 3,
               "running": 1, "ask": 0, "blocker": "", "next_parent_action": {"kind": "advance"},
               "title": workflow_id}
        row.update(fields)
        return row

    def test_no_workflow_keeps_jobs_visible_and_empty_detail(self):
        screen = BoardScr(["\t", "\t", "q"])
        self.paint(screen, [[self.job(1)]], workflows=[])
        first = "\n".join(call[2] for call in screen.frames[0])
        title = next(call[2] for call in screen.frames[0] if call[0] == 0)
        self.assertIn("job-001", first)
        self.assertNotIn("wf ", title)
        workflow_tab = "\n".join(call[2] for call in screen.frames[2])
        self.assertIn("No workflows in .rig/workflows", workflow_tab)

    def test_one_active_workflow_summary_and_selected_detail(self):
        workflow = self._workflow("wf-active", title="ship feature")
        screen = BoardScr(["\t", "\t", "q"], h=16, w=120)
        self.paint(screen, [[self.job(1)]], workflows=[workflow])
        header = "\n".join(call[2] for call in screen.frames[0])
        self.assertIn("job-001", header)
        self.assertIn("wf 1 active / 0 attention", header)
        selected = "\n".join(call[2] for call in screen.frames[2])
        self.assertIn("wf-active", selected)
        self.assertIn("accepted/required 1/3", selected)
        self.assertIn("running 1  ask 0", selected)
        self.assertIn("next parent action advance", selected)
        self.assertNotIn("%", selected)
        self.assertNotIn("ETA", selected)
        self.assertNotIn("savings", selected)

    def test_blocked_and_multiple_workflows_select_detail(self):
        workflows = [
            self._workflow("wf-block", status="blocked", accepted=0, required=1, running=0,
                           blocker="unresolved failure on n1",
                           next_parent_action={"kind": "resolve", "node_id": "n1"}),
            self._workflow("wf-ask", status="attention", accepted=1, required=3, running=0, ask=1,
                           blocker="parent acceptance required",
                           next_parent_action={"kind": "allow_or_deny", "node_id": "k1", "job_id": "ask-job"}),
            self._workflow("wf-run"),
        ]
        screen = BoardScr(["\t", "\t", "j", "q"], h=18, w=120)
        self.paint(screen, [[self.job(1)]], workflows=workflows)
        header = "\n".join(call[2] for call in screen.frames[0])
        self.assertIn("wf 3 active / 2 attention", header)
        self.assertIn("job-001", header)
        first = "\n".join(call[2] for call in screen.frames[2])
        self.assertIn("wf-block", first)
        self.assertIn("unresolved failure on n1", first)
        self.assertIn("next parent action resolve node_id n1", first)
        second = "\n".join(call[2] for call in screen.frames[3])
        self.assertIn("wf-ask", second)
        self.assertIn("allow_or_deny", second)
        self.assertIn("ask 1", second)

    def test_workflow_tab_does_not_invent_mutation_actions(self):
        workflow = self._workflow("wf-active")
        screen = BoardScr(["\t", "\t", "y", "n", "x", "q"], h=16, w=120)
        self.paint(screen, [[self.job(1)]], workflows=[workflow])
        body = "\n".join(call[2] for call in screen.frames[2])
        self.assertIn("wf-active", body)
        self.assertNotIn("press y", body.lower())
        self.assertNotIn("owner_token", body)


if __name__ == "__main__":
    unittest.main()
