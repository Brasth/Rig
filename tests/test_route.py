#!/usr/bin/env python3
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import route  # noqa: E402


class Classify(unittest.TestCase):
    def test_review_keyword(self):
        self.assertEqual(route.classify("worker", "please review the diff"), "review")

    def test_explore_keyword(self):
        self.assertEqual(route.classify("implement", "locate the auth middleware"), "explore")

    def test_mini_keyword(self):
        self.assertEqual(route.classify("", "fix a typo in README"), "mini")

    def test_hard_keyword(self):
        self.assertEqual(route.classify("implement", "security architecture across modules"), "hard")

    def test_default_implement(self):
        self.assertEqual(route.classify("worker", "add session header"), "implement")

    def test_stay_computer_use_and_chrome(self):
        self.assertEqual(route.classify("", "open chrome profile and check admin"), "stay")
        self.assertEqual(route.classify("implement", "use computer-use to click the dialog"), "stay")
        self.assertEqual(route.classify("stay", "look at the live desktop"), "stay")

    def test_ssh_and_fix_go_to_workers(self):
        self.assertEqual(route.classify("worker", "ssh to staging and pull nginx logs"), "implement")
        self.assertEqual(route.classify("", "fix the auth bug in login.ts"), "implement")


class Pick(unittest.TestCase):
    def test_implement_prefers_grok(self):
        c = route.pick("codex", ["grok", "claude"], "implement", "add a header")
        self.assertEqual(c["worker"], "grok")
        self.assertEqual(c["spawn"], "run-worker")
        self.assertEqual(c["model"], "grok-4.6")
        self.assertEqual(c["effort"], "high")

    def test_grok_parent_uses_claude_then_native(self):
        c = route.pick("grok", ["claude"], "implement", "add a header")
        self.assertEqual(c["worker"], "claude")
        self.assertEqual(c["model"], "claude-sonnet-5")
        self.assertEqual(c["effort"], "medium")
        c = route.pick("grok", ["codex"], "implement", "add a header")
        self.assertEqual(c["spawn"], "native")
        self.assertEqual(c["worker"], "grok")
        self.assertEqual(c["model"], "grok-4.6")

    def test_codex_explore_is_native_mini(self):
        c = route.pick("codex", ["grok"], "explore", "trace remaining gates")
        self.assertEqual(c["spawn"], "native")
        self.assertEqual(c["worker"], "codex")
        self.assertEqual(c["native_agent"], "explorer")
        self.assertEqual(c["model"], "gpt-5.3-codex-mini")
        self.assertEqual(c["effort"], "low")

    def test_codex_bulk_luna_low(self):
        c = route.pick("codex", ["grok"], "bulk", "rename the helper")
        self.assertEqual(c["native_agent"], "bulk")
        self.assertEqual(c["model"], "gpt-5.6-luna")
        self.assertEqual(c["effort"], "low")

    def test_hard_codex_is_terra_medium(self):
        c = route.pick("codex", ["cursor"], "hard", "multi-file architecture")
        self.assertEqual(c["spawn"], "native")
        self.assertEqual(c["worker"], "codex")
        self.assertEqual(c["model"], "gpt-5.6-terra")
        self.assertEqual(c["effort"], "medium")

    def test_review_different_vendor(self):
        c = route.pick("grok", ["claude", "codex"], "review", "review the writer diff")
        self.assertEqual(c["worker"], "claude")
        self.assertEqual(c["model"], "claude-opus-5")
        self.assertEqual(c["effort"], "high")

    def test_claude_code_ladder(self):
        self.assertEqual(route.model_for("claude", "explore"), ("claude-haiku-4-5-20251001", "low"))
        self.assertEqual(route.model_for("claude", "implement"), ("claude-sonnet-5", "medium"))
        hard = route.pick("grok", ["claude"], "hard", "multi-file architecture")
        self.assertEqual((hard["model"], hard["effort"]), ("claude-opus-5", "high"))

    def test_no_effective(self):
        c = route.pick("codex", [], "implement", "add a header")
        self.assertEqual(c["spawn"], "native")
        self.assertEqual(c["worker"], "codex")

    def test_stay_does_not_spawn(self):
        c = route.pick("grok", ["claude", "codex"], "stay", "chrome profile login")
        self.assertEqual(c["spawn"], "stay")
        self.assertEqual(c["worker"], "grok")
        self.assertEqual(c["kind"], "stay")
        self.assertIn("parent keeps", c["reason"])
        c = route.pick("codex", ["grok"], "implement", "open chrome profile and check admin")
        self.assertEqual(c["spawn"], "stay")
        self.assertEqual(c["kind"], "stay")

    def test_refuse_parent_models(self):
        self.assertIsNotNone(route.assert_child_model("gpt-5.6-sol"))
        self.assertIsNotNone(route.assert_child_model("gpt-5.6-sol-high"))
        self.assertIsNotNone(route.assert_child_model("gpt-6-astra"))
        self.assertIsNone(route.assert_child_model("gpt-5.6-luna"))
        self.assertIsNone(route.assert_child_model("gpt-5.3-codex-mini"))
        self.assertIsNone(route.assert_child_model("claude-opus-5"))
        self.assertIsNone(route.assert_child_model("claude-sonnet-5"))
        self.assertIsNone(route.assert_child_model("claude-haiku-4-5-20251001"))
        self.assertIsNone(route.assert_child_model("composer-2.5"))
        self.assertIsNone(route.assert_child_model("cursor-grok-4.6-high"))
        self.assertIsNotNone(route.assert_child_model("claude-fable-5"))

    def test_same_cli_beats_cursor_and_codex(self):
        c = route.pick("grok", ["cursor", "codex"], "implement", "add a header")
        self.assertEqual(c["spawn"], "native")
        self.assertEqual(c["worker"], "grok")
        self.assertEqual(c["model"], "grok-4.6")
        c = route.pick("codex", ["cursor"], "implement", "add a header")
        self.assertEqual(c["spawn"], "native")
        self.assertEqual(c["worker"], "codex")
        self.assertEqual(c["native_agent"], "worker")

    def test_cursor_last_resort_when_parent_cannot_native(self):
        c = route.pick("", ["cursor"], "implement", "add a header")
        self.assertEqual(c["worker"], "cursor")
        self.assertEqual(c["spawn"], "run-worker")
        self.assertEqual(c["model"], "composer-2.5")
        self.assertEqual(c["effort"], "")

    def test_grok_still_beats_cursor(self):
        c = route.pick("codex", ["grok", "cursor"], "implement", "add a header")
        self.assertEqual(c["worker"], "grok")

    def test_cursor_review_model(self):
        c = route.pick("grok", ["cursor"], "review", "review the writer diff")
        self.assertEqual(c["worker"], "cursor")
        self.assertEqual(c["model"], "claude-opus-5-thinking-high")

    def test_cursor_explore_model(self):
        self.assertEqual(route.model_for("cursor", "explore"), ("composer-2.5-fast", ""))
        self.assertEqual(route.model_for("cursor", "hard"), ("cursor-grok-4.6-high", ""))

    def test_same_cli_beats_opencode_omp_pi(self):
        c = route.pick("grok", ["opencode", "omp", "pi", "cursor", "codex"], "implement", "add a header")
        self.assertEqual(c["spawn"], "native")
        self.assertEqual(c["worker"], "grok")
        self.assertEqual(c["model"], "grok-4.6")

    def test_omp_beats_pi_when_both_effective(self):
        c = route.pick("", ["pi", "omp"], "implement", "add a header")
        self.assertEqual(c["worker"], "omp")
        self.assertEqual(c["spawn"], "run-worker")
        self.assertEqual(c["model"], "")

    def test_opencode_last_resort_when_parent_cannot_native(self):
        c = route.pick("", ["opencode"], "implement", "add a header")
        self.assertEqual(c["worker"], "opencode")
        self.assertEqual(c["spawn"], "run-worker")
        self.assertEqual(c["model"], "")

    def test_grok_still_beats_opencode(self):
        c = route.pick("codex", ["grok", "opencode"], "implement", "add a header")
        self.assertEqual(c["worker"], "grok")

    def test_opencode_omp_pi_default_model_empty(self):
        self.assertEqual(route.model_for("opencode", "implement"), ("", ""))
        self.assertEqual(route.model_for("omp", "hard"), ("", ""))
        self.assertEqual(route.model_for("pi", "review"), ("", ""))


if __name__ == "__main__":
    unittest.main()
