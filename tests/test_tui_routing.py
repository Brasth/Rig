"""Settings form keyboard, cancellation, stale saves, and board integration."""
import copy
import curses
import sys
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import rig_tui
import routing_settings as settings
import tui_routing
from tui_runtime import ActionResult, Snapshot
from test_routing_settings import Project
from test_tui import BoardScr, ScriptedRuntime


class Form(Project):
    def panel(self):
        return tui_routing.RoutingPanel(self.write())

    def field(self, panel, name):
        panel.selected = next(i for i, field in enumerate(tui_routing.FIELDS) if field[0] == name)
        panel.cursor = len(panel.values[name])

    def test_cancel_no_changes_and_known_ids_validation(self):
        panel = self.panel()
        before = self.files()
        self.field(panel, "profiles")
        for char in "unknown-profile":
            panel.key(char)
        self.assertEqual(panel.key("\x1b"), "cancel")
        self.assertEqual(self.files(), before)
        with self.assertRaisesRegex(ValueError, "known profile"):
            settings.config.validate_candidate(panel.candidate())

    def test_multiple_domains_preserve_order_reset_and_parent_boundary(self):
        panel = self.panel()
        self.field(panel, "profiles")
        for char in "grok-4.7-high, claude-sonnet-5-medium":
            panel.key(char)
        self.assertEqual(panel.candidate()["domains"]["general"]["preferred_profiles"], ["grok-4.7-high", "claude-sonnet-5-medium"])
        self.field(panel, "domain")
        panel.key(" ")  # ui-design
        self.assertEqual(panel.values["domain"], "ui-design")
        self.field(panel, "fallback")
        panel.key(" ")
        self.assertEqual(panel.values["fallback"], "parent")
        self.assertIn("parent-only", panel.message)
        self.field(panel, "domain")
        panel.key(" ")  # frontend
        self.assertEqual(panel.values["profiles"], "claude-sonnet-5-medium, grok-4.7-high")
        panel.key("\x12")
        self.assertNotIn("frontend", panel.candidate()["domains"])
        self.assertIn("general", panel.candidate()["domains"])

    def test_ordered_profile_input_stays_visible_and_capped(self):
        panel = self.panel()
        self.field(panel, "profiles")
        panel.key("x" * 1999, pasted=True)
        panel.key("XYZ", pasted=True)
        self.assertEqual(len(panel.values["profiles"]), 2000)
        self.assertEqual(panel.cursor, 2000)
        screen = BoardScr([], 24, 80)
        tui_routing.render_panel(screen, panel)
        selected = [call[2] for call in screen.calls if "> Preferred" in call[2]]
        self.assertTrue(selected)
        self.assertIn("X│", selected[0])

    def test_task_role_assessment_exclude_and_sources_do_not_persist(self):
        panel = self.panel()
        for name in ("case", "exclude", "sources", "writer_job_id"):
            self.field(panel, name)
            for char in "fixture":
                panel.key(char)
        for name in ("role", "risk", "complexity", "uncertainty", "review_mode"):
            self.field(panel, name)
            panel.key(" ")
        self.assertEqual(panel.candidate(), self.raw)
        args = panel.preview_args()
        self.assertEqual(args["research_sources"], ["fixture"])
        self.assertEqual(args["role"], "explore")
        self.assertEqual(args["risk"], "low")
        self.assertEqual(args["review_mode"], "independent")

    def test_edit_navigation_unicode_and_paste_never_submits(self):
        panel = self.panel()
        self.field(panel, "case")
        for key in "界éabc":
            panel.key(key, pasted=True)
        for key in ("\x13", "\x10", "\x1b"):
            self.assertIsNone(panel.key(key, pasted=True))
        panel.key(curses.KEY_HOME)
        panel.key("✓")
        panel.key(curses.KEY_END)
        panel.key(curses.KEY_BACKSPACE)
        panel.key(curses.KEY_LEFT)
        panel.key(curses.KEY_DC)
        self.assertEqual(panel.values["case"], "✓界éa")
        panel.key("\t")
        self.assertEqual(tui_routing.FIELDS[panel.selected][0], "role")
        panel.key(curses.KEY_BTAB)
        self.assertEqual(tui_routing.FIELDS[panel.selected][0], "case")
        self.assertEqual(panel.key(curses.KEY_F2), "save")
        self.assertEqual(panel.key(curses.KEY_F5), "preview")

    def test_pending_save_freezes_mutation_and_repeated_submit(self):
        panel = self.panel()
        panel.pending = "save"
        original = copy.deepcopy(panel.values)
        for key in ("a", "\x1b", "\x13", "\t"):
            self.assertIsNone(panel.key(key))
        self.assertEqual(panel.values, original)
        panel.accept("save", ActionResult("test", error="changed"))
        self.assertEqual(panel.pending, "")
        self.assertIn("draft retained", panel.message)
        panel.pending = "preview"
        self.assertEqual(panel.key("\x1b"), "cancel")

    def test_save_updates_fingerprint_repeat_then_new_draft(self):
        panel = self.panel()
        self.field(panel, "fallback")
        panel.key(" ")
        value = settings.save_settings(self.repo, panel.candidate(), expected_fingerprint=panel.document.expected_fingerprint)
        panel.accept("save", ActionResult("test", value=value))
        self.assertEqual(panel.edits, {})
        self.assertEqual(panel.document.expected_fingerprint, settings.open_settings(self.repo).expected_fingerprint)
        self.assertEqual(panel.candidate(), settings.open_settings(self.repo).raw)
        before = self.path.read_bytes()
        panel.key(" ")
        panel.key("\x1b")
        self.assertEqual(self.path.read_bytes(), before)

    def test_invalid_or_stale_save_retains_original_and_editable_form(self):
        panel = self.panel()
        self.field(panel, "profiles")
        panel.key("x")
        before = self.path.read_bytes()
        try:
            settings.save_settings(self.repo, panel.candidate(), expected_fingerprint=panel.document.expected_fingerprint)
        except ValueError as exc:
            panel.accept("save", ActionResult("test", error=str(exc)))
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(panel.values["profiles"], "x")
        self.assertIn("failed", panel.message)
        panel.key(curses.KEY_BACKSPACE)
        self.path.write_bytes(before + b"\n")
        with self.assertRaises(settings.StaleSettings):
            settings.save_settings(self.repo, panel.candidate(), expected_fingerprint=panel.document.expected_fingerprint)

    def test_render_tiny_normal_and_scrolling_never_bottom_right(self):
        panel = self.panel()
        with patch.object(curses, "color_pair", return_value=0):
            for h, w in ((5, 25), (10, 40), (24, 80), (30, 120)):
                for field in (0, len(tui_routing.FIELDS)-1):
                    panel.selected = field
                    screen = BoardScr([], h, w)
                    tui_routing.render_panel(screen, panel)
                    self.assertTrue(screen.calls)
                    self.assertTrue(all(y < h and x < w for y, x, *_ in screen.calls))
                    panel.key(curses.KEY_NPAGE)
                    tui_routing.render_panel(screen, panel)
                    panel.key(curses.KEY_PPAGE)


class ImmediateRuntime(ScriptedRuntime):
    def __init__(self, repo, *, delay_preview=False):
        super().__init__([Snapshot(settings=settings.settings_rows(repo))])
        self.results = []
        self.submitted = []
        self.delay_preview = delay_preview

    def poll(self):
        results, self.results = self.results, []
        return results

    def submit(self, key, action, **kwargs):
        self.submitted.append(key)
        if self.delay_preview and key.endswith(":preview"):
            return True
        try:
            result = ActionResult(key, value=action())
        except (Exception, SystemExit) as exc:
            result = ActionResult(key, error=str(exc))
        self.results.append(result)
        return True


class BoardForm(Project):
    def run_board(self, keys, runtime=None):
        runtime = runtime or ImmediateRuntime(self.repo)
        screen = BoardScr(keys, h=24, w=100)
        with ExitStack() as stack:
            for name in ("curs_set", "use_default_colors", "init_pair", "noecho", "set_escdelay"):
                stack.enter_context(patch.object(curses, name))
            stack.enter_context(patch.object(curses, "color_pair", return_value=0))
            stack.enter_context(patch.object(sys.stdout, "isatty", return_value=False))
            stack.enter_context(patch.object(rig_tui, "collect_workflows", return_value=[]))
            rig_tui._paint(screen, self.repo, runtime=runtime)
        return runtime, screen

    def test_board_open_edit_cancel_reopen_save_repeated_safe(self):
        self.write()
        keys = ["\t", "\t", "\t", "e", "\t", "x", "\x1b", "e", "\t", "\t", " ",
                curses.KEY_F2, curses.KEY_F2, "\x1b", "q"]
        runtime, screen = self.run_board(keys)
        document = settings.open_settings(self.repo)
        self.assertEqual(document.raw["domains"]["general"], {"preferred_profiles": [], "fallback": "parent"})
        self.assertEqual(document.raw["domains"]["frontend"], self.raw["domains"]["frontend"])
        self.assertEqual(sum(key.endswith(":save") for key in runtime.submitted), 2)
        self.assertTrue(any("Routing Settings" in call[2] for frame in screen.frames for call in frame))

    def test_delayed_open_cannot_replace_newer_navigation_and_queue_editor(self):
        self.write()
        before = self.files()
        runtime = ImmediateRuntime(self.repo)
        original_submit = runtime.submit
        original_poll = runtime.poll
        delayed, ticks = [], [0]
        def submit(key, action, **kwargs):
            if key.startswith("routing-open:"):
                runtime.submitted.append(key)
                delayed.append(ActionResult(key, value=action()))
                return True
            return original_submit(key, action, **kwargs)
        def poll():
            ticks[0] += 1
            result = original_poll()
            if ticks[0] == 7:
                result.extend(delayed)
                delayed.clear()
            return result
        runtime.submit, runtime.poll = submit, poll
        # The old request completes after Tab to Jobs and opening its queue editor.
        _runtime, screen = self.run_board(["\t", "\t", "\t", "e", "\t", "e", "x", "\x1b", "q"], runtime)
        self.assertEqual(self.files(), before)
        self.assertTrue(any("enqueue" in call[2] for frame in screen.frames[6:] for call in frame))
        self.assertFalse(any("Routing Settings / task preview" in call[2] for frame in screen.frames for call in frame))

    def test_late_preview_from_cancelled_form_is_ignored(self):
        self.write()
        before = self.files()
        runtime = ImmediateRuntime(self.repo, delay_preview=True)
        original_submit = runtime.submit
        def submit(key, action, **kwargs):
            if key.startswith("routing-open:2"):
                old_key = next(k for k in runtime.submitted if k.endswith(":preview"))
                # The stale result lands after the new form opens. It must neither
                # overwrite its output nor surface as a generic board error.
                accepted = original_submit(key, action, **kwargs)
                runtime.results.append(ActionResult(old_key, error="STALE PREVIEW MUST NOT APPEAR"))
                return accepted
            return original_submit(key, action, **kwargs)
        runtime.submit = submit
        _runtime, screen = self.run_board(["\t", "\t", "\t", "e", curses.KEY_F5, "\x1b", "e", "\x1b", "q"], runtime)
        self.assertEqual(self.files(), before)
        self.assertFalse(any("STALE PREVIEW" in call[2] for frame in screen.frames for call in frame))

    def test_preview_cancel_and_reopen_does_not_write_or_launch(self):
        self.write()
        before = self.files()
        runtime = ImmediateRuntime(self.repo, delay_preview=True)
        self.run_board(["\t", "\t", "\t", "e", curses.KEY_F5, "\x1b", "e", "\x1b", "q"], runtime)
        self.assertEqual(self.files(), before)
        self.assertEqual(sum(key.endswith(":preview") for key in runtime.submitted), 1)
        self.assertEqual(sum(key.startswith("routing-open:") for key in runtime.submitted), 2)


if __name__ == "__main__":
    unittest.main()
