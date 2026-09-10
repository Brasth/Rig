#!/usr/bin/env python3
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import route  # noqa: E402


class Classify(unittest.TestCase):
    def test_review_keyword(self):
        self.assertEqual(route.classify("worker", "please review the diff"), "review")
        self.assertEqual(route.classify("", "please review the diff"), "review")

    def test_explore_keyword(self):
        self.assertEqual(route.classify("", "locate the auth middleware"), "explore")
        self.assertEqual(route.classify("worker", "locate the auth middleware"), "explore")

    def test_mini_keyword(self):
        self.assertEqual(route.classify("", "fix a typo in README"), "mini")

    def test_hard_keyword(self):
        self.assertEqual(route.classify("", "security architecture across modules"), "hard")
        self.assertEqual(route.classify("worker", "security architecture across modules"), "hard")

    def test_default_implement(self):
        self.assertEqual(route.classify("worker", "add session header"), "implement")
        self.assertEqual(route.classify("", "add session header"), "implement")
        self.assertEqual(route.classify("auto", "add session header"), "implement")

    def test_stay_computer_use_and_chrome(self):
        self.assertEqual(route.classify("", "open chrome profile and check admin"), "stay")
        self.assertEqual(route.classify("", "use computer-use to click the dialog"), "stay")
        self.assertEqual(route.classify("stay", "look at the live desktop"), "stay")

    def test_stay_ask_and_advise(self):
        self.assertEqual(route.classify("", "advise on the tradeoff"), "stay")
        self.assertEqual(route.classify("", "do you think this is faster"), "stay")
        self.assertEqual(route.classify("", "technical question about routing"), "stay")
        self.assertEqual(route.classify("", "architectural guidance for the parent"), "stay")
        self.assertEqual(route.classify("auto", "advise on the tradeoff"), "stay")

    def test_device_slash_names_are_not_keywords(self):
        self.assertEqual(route.classify("", "/ak-ask can we make this faster"), "implement")
        self.assertEqual(route.classify("", "ak-ask about the tradeoff"), "implement")

    def test_explicit_kind_wins(self):
        self.assertEqual(route.classify("stay", "add a header"), "stay")
        self.assertEqual(route.classify("implement", "advise on the tradeoff"), "implement")
        self.assertEqual(route.classify("implement", "locate the auth middleware"), "implement")
        self.assertEqual(route.classify("review", "add a header"), "review")
        self.assertEqual(route.classify("reviewer", ""), "review")
        self.assertEqual(route.classify("reviewer", "add a header"), "review")
        self.assertEqual(route.classify("implement", "use computer-use to click the dialog"), "implement")
        self.assertEqual(route.classify("implement", "update the skill"), "implement")

    def test_mini_docs_only(self):
        self.assertEqual(route.classify("", "docs only: update the skill"), "mini")
        self.assertEqual(route.classify("", "update the skill"), "mini")
        self.assertEqual(route.classify("", "readme only"), "mini")
        self.assertEqual(route.classify("", "documentation only"), "mini")
        self.assertEqual(route.classify("", "skill only"), "mini")
        self.assertEqual(route.classify("", "update usage.md"), "mini")

    def test_ssh_and_fix_go_to_workers(self):
        self.assertEqual(route.classify("worker", "ssh to staging and pull nginx logs"), "implement")
        self.assertEqual(route.classify("", "fix the auth bug in login.ts"), "implement")
        self.assertEqual(route.classify("", "fix the auth bug and update docs"), "implement")
        self.assertEqual(route.classify("worker", "add session header"), "implement")


class Pick(unittest.TestCase):
    def setUp(self):
        self._skip = os.environ.get("RIG_SKIP_MODEL_CATALOG")
        os.environ["RIG_SKIP_MODEL_CATALOG"] = "1"

    def tearDown(self):
        if self._skip is None:
            os.environ.pop("RIG_SKIP_MODEL_CATALOG", None)
        else:
            os.environ["RIG_SKIP_MODEL_CATALOG"] = self._skip

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
        c = route.pick("pi", [], "implement", "add a header")
        self.assertEqual(c["spawn"], "native")
        self.assertEqual(c["worker"], "pi")
        self.assertNotEqual(c["worker"], "grok")

    def test_stay_does_not_spawn(self):
        c = route.pick("grok", ["claude", "codex"], "stay", "chrome profile login")
        self.assertEqual(c["spawn"], "stay")
        self.assertEqual(c["worker"], "grok")
        self.assertEqual(c["kind"], "stay")
        self.assertIn("parent keeps", c["reason"])
        c = route.pick("codex", ["grok"], "", "open chrome profile and check admin")
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
        self.assertIsNone(route.assert_child_model("openai/gpt-5.4-mini"))
        self.assertIsNone(route.assert_child_model("openai/gpt-5.6-luna"))
        self.assertIsNone(route.assert_child_model("openai/gpt-5.6-terra"))
        self.assertIsNone(route.assert_child_model("gemini-3.8-flash-high"))
        self.assertIsNone(route.assert_child_model("gemini-3.1-pro-high"))
        self.assertIsNotNone(route.assert_child_model("claude-fable-5"))
        self.assertIsNotNone(route.assert_child_model("openai/gpt-5.6-sol"))

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

    def test_same_cli_beats_opencode_omp_pi_agy(self):
        c = route.pick("grok", ["opencode", "omp", "pi", "agy", "cursor", "codex"], "implement", "add a header")
        self.assertEqual(c["spawn"], "native")
        self.assertEqual(c["worker"], "grok")
        self.assertEqual(c["model"], "grok-4.6")

    def test_omp_beats_pi_when_both_effective(self):
        c = route.pick("", ["pi", "omp"], "implement", "add a header")
        self.assertEqual(c["worker"], "omp")
        self.assertEqual(c["spawn"], "run-worker")
        self.assertEqual(c["model"], "grok-4.6")
        self.assertEqual(c["effort"], "high")

    def test_opencode_last_resort_when_parent_cannot_native(self):
        c = route.pick("", ["opencode"], "implement", "add a header")
        self.assertEqual(c["worker"], "opencode")
        self.assertEqual(c["spawn"], "run-worker")
        self.assertEqual(c["model"], "openai/gpt-5.6-luna")
        self.assertEqual(c["effort"], "high")

    def test_grok_still_beats_opencode(self):
        c = route.pick("codex", ["grok", "opencode"], "implement", "add a header")
        self.assertEqual(c["worker"], "grok")

    def test_opencode_omp_pi_agy_model_pins(self):
        self.assertEqual(route.model_for("opencode", "explore"), ("openai/gpt-5.4-mini", "minimal"))
        self.assertEqual(route.model_for("opencode", "mini"), ("openai/gpt-5.4-mini", "minimal"))
        self.assertEqual(route.model_for("opencode", "bulk"), ("openai/gpt-5.4-mini", "minimal"))
        self.assertEqual(route.model_for("opencode", "implement"), ("openai/gpt-5.6-luna", "high"))
        self.assertEqual(route.model_for("opencode", "hard"), ("openai/gpt-5.6-terra", "max"))
        self.assertEqual(route.model_for("opencode", "review"), ("openai/gpt-5.6-terra", "max"))
        self.assertEqual(route.model_for("omp", "explore"), ("grok-4.5", "low"))
        self.assertEqual(route.model_for("omp", "mini"), ("grok-4.5", "low"))
        self.assertEqual(route.model_for("omp", "bulk"), ("grok-4.5", "low"))
        self.assertEqual(route.model_for("omp", "implement"), ("grok-4.6", "high"))
        self.assertEqual(route.model_for("omp", "hard"), ("grok-4.6", "high"))
        self.assertEqual(route.model_for("omp", "review"), ("claude-opus-5", "high"))
        self.assertEqual(route.model_for("pi", "explore"), ("grok-4.5", "low"))
        self.assertEqual(route.model_for("pi", "mini"), ("grok-4.5", "low"))
        self.assertEqual(route.model_for("pi", "bulk"), ("grok-4.5", "low"))
        self.assertEqual(route.model_for("pi", "implement"), ("grok-4.6", "high"))
        self.assertEqual(route.model_for("pi", "hard"), ("grok-4.6", "high"))
        self.assertEqual(route.model_for("pi", "review"), ("claude-opus-5", "high"))
        self.assertEqual(route.model_for("agy", "explore"), ("gemini-3.8-flash-low", "low"))
        self.assertEqual(route.model_for("agy", "mini"), ("gemini-3.8-flash-low", "low"))
        self.assertEqual(route.model_for("agy", "bulk"), ("gemini-3.8-flash-low", "low"))
        self.assertEqual(route.model_for("agy", "implement"), ("gemini-3.8-flash-high", "high"))
        self.assertEqual(route.model_for("agy", "hard"), ("gemini-3.1-pro-high", "high"))
        self.assertEqual(route.model_for("agy", "review"), ("gemini-3.1-pro-high", "high"))
        for (_worker, _kind), (model, _effort) in route.MODELS.items():
            self.assertIsNone(route.assert_child_model(model), model)
            raw = model.lower()
            self.assertNotIn("sol", raw)
            self.assertNotIn("astra", raw)
            self.assertNotIn("fable", raw)

    def test_opencode_live_plus_grok_is_grok_child(self):
        c = route.pick("opencode", ["grok"], "implement", "add a header")
        self.assertEqual(c["worker"], "grok")
        self.assertEqual(c["spawn"], "run-worker")
        self.assertEqual(c["model"], "grok-4.6")

    def test_opencode_live_no_other_is_native(self):
        c = route.pick("opencode", [], "implement", "add a header")
        self.assertEqual(c["spawn"], "native")
        self.assertEqual(c["worker"], "opencode")
        self.assertEqual(c["model"], "openai/gpt-5.6-luna")
        self.assertEqual(c["effort"], "high")
        self.assertEqual(c["native_agent"], "worker")
        c = route.pick("opencode", ["cursor", "codex"], "implement", "add a header")
        self.assertEqual(c["spawn"], "native")
        self.assertEqual(c["worker"], "opencode")
        self.assertEqual(c["model"], "openai/gpt-5.6-luna")

    def test_opencode_live_explore_is_native(self):
        c = route.pick("opencode", ["cursor", "codex"], "explore", "trace remaining gates")
        self.assertEqual(c["spawn"], "native")
        self.assertEqual(c["worker"], "opencode")
        self.assertEqual(c["native_agent"], "explore")
        self.assertEqual(c["model"], "openai/gpt-5.4-mini")
        self.assertEqual(c["effort"], "minimal")

    def test_opencode_live_bulk_native_agent(self):
        c = route.pick("opencode", ["grok"], "bulk", "rename the helper")
        self.assertEqual(c["spawn"], "native")
        self.assertEqual(c["worker"], "opencode")
        self.assertEqual(c["native_agent"], "bulk")

    def test_omp_live_plus_pi_is_native_omp(self):
        c = route.pick("omp", ["pi"], "implement", "add a header")
        self.assertEqual(c["spawn"], "native")
        self.assertEqual(c["worker"], "omp")
        self.assertEqual(c["model"], "grok-4.6")
        self.assertEqual(c["effort"], "high")
        c = route.pick("omp", ["grok", "pi"], "implement", "add a header")
        self.assertEqual(c["worker"], "grok")
        self.assertEqual(c["spawn"], "run-worker")

    def test_pi_live_plus_omp_is_native_pi(self):
        c = route.pick("pi", ["omp"], "implement", "add a header")
        self.assertEqual(c["spawn"], "native")
        self.assertEqual(c["worker"], "pi")
        self.assertEqual(c["model"], "grok-4.6")
        self.assertEqual(c["effort"], "high")

    def test_omp_beats_pi_as_workers_of_other_parent(self):
        c = route.pick("", ["pi", "omp"], "implement", "add a header")
        self.assertEqual(c["worker"], "omp")
        self.assertEqual(c["spawn"], "run-worker")

    def test_agy_live_explore_is_native(self):
        c = route.pick("agy", ["cursor", "codex"], "explore", "trace remaining gates")
        self.assertEqual(c["spawn"], "native")
        self.assertEqual(c["worker"], "agy")
        self.assertEqual(c["native_agent"], "explore")
        self.assertEqual(c["model"], "gemini-3.8-flash-low")
        self.assertEqual(c["effort"], "low")

    def test_agy_live_implement_is_native_when_grok_claude_off(self):
        c = route.pick("agy", ["cursor", "codex"], "implement", "add a header")
        self.assertEqual(c["spawn"], "native")
        self.assertEqual(c["worker"], "agy")
        self.assertEqual(c["native_agent"], "worker")
        self.assertEqual(c["model"], "gemini-3.8-flash-high")
        self.assertEqual(c["effort"], "high")

    def test_agy_live_plus_grok_is_grok_child(self):
        c = route.pick("agy", ["grok"], "implement", "add a header")
        self.assertEqual(c["worker"], "grok")
        self.assertEqual(c["spawn"], "run-worker")

    def test_agy_last_resort_when_parent_cannot_native(self):
        c = route.pick("", ["agy"], "implement", "add a header")
        self.assertEqual(c["worker"], "agy")
        self.assertEqual(c["spawn"], "run-worker")
        self.assertEqual(c["model"], "gemini-3.8-flash-high")
        self.assertEqual(c["effort"], "high")

    def test_omp_still_beats_pi_with_agy_present(self):
        c = route.pick("", ["agy", "pi", "omp"], "implement", "add a header")
        self.assertEqual(c["worker"], "omp")
        self.assertEqual(c["spawn"], "run-worker")

    def test_pi_beats_agy_as_last_resort(self):
        c = route.pick("", ["agy", "pi"], "implement", "add a header")
        self.assertEqual(c["worker"], "pi")


if __name__ == "__main__":
    unittest.main()
