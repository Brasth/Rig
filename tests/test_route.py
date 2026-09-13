#!/usr/bin/env python3
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

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

    def test_questions_and_plans_stay_before_incidental_keywords(self):
        for case in (
            "How does pick choose a worker?", "Why review the diff?",
            "What caused this security bug?", "Explain the tiny patch",
            "Plan a security refactor", "Please explain the queue",
            "Please, plan a queue refactor", "Can you explain the security rules?",
            "Could you explain the queue?", "Please could you explain the queue?",
        ):
            with self.subTest(case=case):
                self.assertEqual(route.classify("", case), "stay")

    def test_modal_actions_remain_implementation(self):
        for case in (
            "Can you fix the queue parser?", "Could you add a retry button?",
            "Please fix the parser", "Implement a plan selector",
        ):
            with self.subTest(case=case):
                self.assertEqual(route.classify("", case), "implement")

    def test_keyword_boundaries_avoid_substring_collisions(self):
        for case in (
            "Fix preview rendering", "Fix the reviewer badge color",
            "Add auditability labels", "Fix typography", "Fix a bulkhead",
            "Fix the planet selector", "Fix the traceable flag",
        ):
            with self.subTest(case=case):
                self.assertEqual(route.classify("", case), "implement")

    def test_explicit_explore_inflections(self):
        for word in ("explore", "explored", "exploring", "exploration", "exploratory"):
            with self.subTest(word=word):
                self.assertEqual(route.classify("", f"{word} the queue"), "explore")

    def test_risk_precedes_size_but_retains_review_explore_bulk_order(self):
        for case, kind in (
            ("Fix a security bug with a tiny patch", "hard"),
            ("Make a tiny architecture change", "hard"),
            ("Tiny cross-module fix", "hard"),
            ("Review a tiny security patch", "review"),
            ("Trace security middleware", "explore"),
            ("Bulk security configuration update", "bulk"),
        ):
            with self.subTest(case=case):
                self.assertEqual(route.classify("", case), kind)

    def test_docs_shortcuts_match_the_whole_request(self):
        for case in ("Please update the skill.", "Please, update usage.md!", "UPDATE  THE SKILL?"):
            with self.subTest(case=case):
                self.assertEqual(route.classify("", case), "mini")
        for case in (
            "Fix the bug and update the skill", "Fix the bug and update usage.md",
            "Update the skill and fix the parser", "Please update usage.md and fix the bug",
        ):
            with self.subTest(case=case):
                self.assertEqual(route.classify("", case), "implement")

    def test_classification_provenance(self):
        for role, case, expected in (
            (" MINI ", "security fix", {"kind": "mini", "source": "explicit", "rule": "explicit:mini"}),
            ("explorer", "review architecture", {"kind": "explore", "source": "explicit", "rule": "explicit:explorer"}),
            ("", "How does this work?", {"kind": "stay", "source": "fallback", "rule": "stay:opening"}),
            ("worker", "tiny security fix", {"kind": "hard", "source": "fallback", "rule": "hard:security"}),
            ("auto", "update the skill", {"kind": "mini", "source": "fallback", "rule": "mini:whole-request-docs"}),
            ("", "fix preview", {"kind": "implement", "source": "fallback", "rule": "implement:default"}),
        ):
            with self.subTest(role=role, case=case):
                self.assertEqual(route.classify_details(role, case), expected)
                self.assertEqual(route.classify(role, case), expected["kind"])


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
        self.assertEqual(c["model"], "")

    def test_codex_explore_is_native_mini(self):
        c = route.pick("codex", ["grok"], "explore", "trace remaining gates")
        self.assertEqual(c["spawn"], "native")
        self.assertEqual(c["worker"], "codex")
        self.assertEqual(c["native_agent"], "explorer")
        self.assertEqual(c["model"], "gpt-5.3-codex-mini")
        self.assertEqual(c["effort"], "low")

    def test_codex_mini_matches_shipped_write_capable_worker(self):
        agents = Path(__file__).resolve().parents[1] / "adapters" / "codex" / "agents"
        for kind, agent, sandbox in (
            ("mini", "worker", "workspace-write"),
            ("explore", "explorer", "read-only"),
        ):
            with self.subTest(kind=kind):
                template = (agents / f"{agent}.toml").read_text()
                choice = route.pick("codex", ["grok"], kind, "update the skill")
                self.assertEqual(choice["native_agent"], agent)
                self.assertIn(f'model = "{choice["model"]}"', template.splitlines())
                self.assertIn(f'model_reasoning_effort = "{choice["effort"]}"', template.splitlines())
                self.assertIn(f'sandbox_mode = "{sandbox}"', template.splitlines())
                self.assertFalse(choice["parent_writes"])
                self.assertEqual(choice["executor_kind"], "native_child")
                self.assertEqual(choice["model_source"], "selected")

    def test_other_native_mini_mappings_are_preserved(self):
        for live in route.NATIVE_PARENTS - {"codex"}:
            with self.subTest(live=live):
                choice = route.pick(live, [], "mini", "update the skill")
                self.assertEqual(choice["native_agent"], "explore")
                self.assertEqual((choice["model"], choice["effort"]), route.model_for(live, "mini"))

    def test_codex_bulk_luna_low(self):
        c = route.pick("codex", ["grok"], "bulk", "rename the helper")
        self.assertEqual(c["native_agent"], "bulk")
        self.assertEqual(c["model"], "gpt-5.6-luna")
        self.assertEqual(c["effort"], "low")

    def test_hard_codex_parent_has_no_invented_model(self):
        c = route.pick("codex", ["cursor"], "hard", "multi-file architecture")
        self.assertEqual(c["spawn"], "native")
        self.assertEqual(c["worker"], "codex")
        self.assertEqual(c["model"], "")
        self.assertEqual(c["effort"], "")
        self.assertEqual(route.model_for("codex", "hard"), ("gpt-5.6-terra", "medium"))

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
        self.assertEqual(c["model"], "")
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
        self.assertEqual(c["model"], "")

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
        self.assertEqual(c["model"], "")
        self.assertEqual(c["effort"], "")
        self.assertEqual(c["native_agent"], "worker")
        c = route.pick("opencode", ["cursor", "codex"], "implement", "add a header")
        self.assertEqual(c["spawn"], "native")
        self.assertEqual(c["worker"], "opencode")
        self.assertEqual(c["model"], "")

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
        self.assertEqual(c["model"], "")
        self.assertEqual(c["effort"], "")
        c = route.pick("omp", ["grok", "pi"], "implement", "add a header")
        self.assertEqual(c["worker"], "grok")
        self.assertEqual(c["spawn"], "run-worker")

    def test_pi_live_plus_omp_is_native_pi(self):
        c = route.pick("pi", ["omp"], "implement", "add a header")
        self.assertEqual(c["spawn"], "native")
        self.assertEqual(c["worker"], "pi")
        self.assertEqual(c["model"], "")
        self.assertEqual(c["effort"], "")

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
        self.assertEqual(c["model"], "")
        self.assertEqual(c["effort"], "")

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

    def test_opencode_beats_cursor_as_last_resort(self):
        c = route.pick("", ["cursor", "opencode"], "implement", "add a header")
        self.assertEqual(c["worker"], "opencode")
        self.assertEqual(c["spawn"], "run-worker")
        self.assertFalse(c["parent_writes"])

    def test_exclude_skips_native_grok_to_omp(self):
        c = route.pick(
            "grok",
            ["cursor", "omp", "pi"],
            "implement",
            "add a header",
            exclude="grok",
        )
        self.assertEqual(c["worker"], "omp")
        self.assertEqual(c["spawn"], "run-worker")
        self.assertFalse(c["parent_writes"])

    def test_exclude_grok_omp_falls_to_pi(self):
        c = route.pick(
            "grok",
            ["cursor", "omp", "pi"],
            "implement",
            "add a header",
            exclude="grok,omp",
        )
        self.assertEqual(c["worker"], "pi")
        self.assertEqual(c["spawn"], "run-worker")

    def test_exclude_leaving_only_cursor_is_none(self):
        c = route.pick(
            "grok",
            ["cursor"],
            "implement",
            "add a header",
            exclude="grok",
        )
        self.assertEqual(c["spawn"], "none")
        self.assertEqual(c["worker"], "")
        self.assertFalse(c["parent_writes"])
        self.assertIn("Cursor", c["reason"])

    def test_first_pick_cursor_only_still_cursor(self):
        c = route.pick("", ["cursor"], "implement", "add a header")
        self.assertEqual(c["worker"], "cursor")
        self.assertEqual(c["spawn"], "run-worker")

    def test_native_implement_parent_writes(self):
        c = route.pick("grok", ["cursor", "codex"], "implement", "add a header")
        self.assertEqual(c["spawn"], "native")
        self.assertTrue(c["parent_writes"])
        self.assertEqual(c["model"], "")
        self.assertIn("this parent writes", c["reason"])
        c = route.pick("grok", ["claude"], "implement", "add a header")
        self.assertEqual(c["spawn"], "run-worker")
        self.assertEqual(c["worker"], "claude")
        self.assertFalse(c["parent_writes"])

    def test_native_explore_is_not_parent_writes(self):
        c = route.pick("grok", ["claude"], "explore", "trace remaining gates")
        self.assertEqual(c["spawn"], "native")
        self.assertFalse(c["parent_writes"])
        self.assertEqual(c["model"], "grok-4.5")

    def test_native_hard_parent_writes_on_each_parent(self):
        for live in route.NATIVE_PARENTS:
            c = route.pick(live, [], "hard", "multi-file architecture")
            self.assertEqual(c["spawn"], "native", live)
            self.assertTrue(c["parent_writes"], live)
            self.assertEqual(c["worker"], live)
            self.assertEqual(c["model"], "", live)
            self.assertEqual(c["effort"], "", live)
            self.assertEqual(c["executor_kind"], "parent", live)
            self.assertEqual(c["model_source"], "unknown", live)

    def test_review_does_not_self_review(self):
        c = route.pick("grok", [], "review", "review the writer diff")
        self.assertEqual(c["spawn"], "none")
        self.assertFalse(c["parent_writes"])
        self.assertIn("different vendor", c["reason"])

    def test_pick_exposes_classification_for_every_outcome(self):
        for live, effective, role, case in (
            ("codex", [], "", "How does pick work?"),
            ("codex", [], "mini", "security fix"),
            ("grok", [], "hard", "tiny typo"),
            ("codex", ["grok"], "", "fix preview"),
            ("grok", [], "review", "review the diff"),
        ):
            with self.subTest(live=live, role=role, case=case):
                details = route.classify_details(role, case)
                choice = route.pick(live, effective, role, case)
                self.assertEqual(choice["classification_source"], details["source"])
                self.assertEqual(choice["classification_rule"], details["rule"])

    def test_observed_parent_model_is_explicit_and_trimmed(self):
        for role in ("stay", "implement", "hard"):
            with self.subTest(role=role):
                choice = route.pick(
                    "codex", [], role, "fix the parser",
                    parent_model=" gpt-6-astra ", parent_effort=" high ",
                )
                self.assertEqual(choice["model"], "gpt-6-astra")
                self.assertEqual(choice["effort"], "high")
                self.assertEqual(choice["executor_kind"], "parent")
                self.assertEqual(choice["model_source"], "observed")

    def test_unknown_parent_model_discards_effort_and_skips_catalogs(self):
        with patch.object(route.rig_catalog, "resolve_model", side_effect=AssertionError("unexpected discovery")):
            for live in route.NATIVE_PARENTS:
                for role in ("stay", "implement", "hard"):
                    with self.subTest(live=live, role=role):
                        choice = route.pick(live, [], role, "fix parser", parent_model=" ", parent_effort="high")
                        self.assertEqual(choice["model"], "")
                        self.assertEqual(choice["effort"], "")
                        self.assertEqual(choice["executor_kind"], "parent")
                        self.assertEqual(choice["model_source"], "unknown")
                        self.assertNotIn(route.model_for(live, role)[0], choice["reason"])

    def test_child_choices_ignore_parent_metadata(self):
        for live, effective, role, executor in (
            ("codex", [], "mini", "native_child"),
            ("codex", ["grok"], "implement", "wrapper"),
        ):
            with self.subTest(executor=executor):
                choice = route.pick(
                    live, effective, role, "fix the parser",
                    parent_model="gpt-6-astra", parent_effort="max",
                )
                self.assertNotEqual(choice["model"], "gpt-6-astra")
                self.assertEqual(choice["executor_kind"], executor)
                self.assertEqual(choice["model_source"], "selected")

    def test_only_selected_child_catalog_is_loaded(self):
        for live, effective, role, expected in (
            ("", ["opencode", "omp", "pi", "agy"], "implement", "opencode"),
            ("pi", ["opencode", "omp", "agy"], "explore", "pi"),
        ):
            with self.subTest(expected=expected):
                with patch.object(route.rig_catalog, "load_catalog", return_value=[]) as load:
                    with patch.object(route.rig_catalog, "load_catalogs", side_effect=AssertionError("all-worker discovery")):
                        route.pick(live, effective, role, "task")
                load.assert_called_once_with(expected)

    def test_no_worker_has_no_executor_or_catalog(self):
        with patch.object(route.rig_catalog, "resolve_model", side_effect=AssertionError("unexpected discovery")):
            choice = route.pick("grok", [], "review", "review diff")
        self.assertEqual(choice["executor_kind"], "")
        self.assertEqual(choice["model_source"], "unknown")

    def test_pick_keeps_existing_optional_positional_arguments(self):
        choice = route.pick(
            "grok", ["opencode"], "implement", "task",
            {"opencode": ["anthropic/claude-sonnet-5"]}, "grok",
        )
        self.assertEqual(choice["worker"], "opencode")
        self.assertEqual(choice["model"], "anthropic/claude-sonnet-5")


class ReviewProvenance(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {"RIG_SKIP_MODEL_CATALOG": "1"})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        self.repo = Path(self.td.name)
        self.job_dir = self.repo / ".rig" / "jobs" / "writer"
        self.job_dir.mkdir(parents=True)
        self.writer = {
            "job_id": "writer", "worker": "cursor", "status": "ok", "role": "implement",
            "model": "claude-sonnet-5", "model_source": "selected", "model_inferred": False,
        }
        (self.job_dir / "meta.json").write_text(json.dumps(self.writer))
        self.assessment = Mock(return_value={
            "state": "verified", "acceptance": "accepted", "snapshot_id": "accepted-snapshot",
        })
        self.verification = patch("verification.assessment", self.assessment)
        self.verification.start()
        self.addCleanup(self.verification.stop)

    def pick(self, effective, **kwargs):
        return route.pick("codex", effective, "review", "review diff", repo=self.repo, **kwargs)

    def test_provider_models_across_cli_wrappers(self):
        for model, provider in (
            ("gpt-5.6-luna", "openai"), ("openai/gpt-6-astra", "openai"),
            ("o3-mini", "openai"), ("codex-mini-latest", "openai"),
            ("claude-opus-5-thinking-high", "anthropic"),
            ("openrouter/anthropic/claude-sonnet-5", "anthropic"),
            ("xai-oauth/grok-4.6", "xai"), ("cursor-grok-4.6-high", "xai"),
            ("google/gemini-3.1-pro-high", "google"), ("composer-2.5-fast", "cursor"),
            ("openai/custom-v2", "openai"),
            ("sonnet", ""), ("opus", ""), ("grok", ""), ("custom/corporate-best", ""),
            ("gpt-best", ""), ("", ""),
        ):
            with self.subTest(model=model):
                self.assertEqual(route.provider_for(model), provider)

    def test_standalone_unknown_writer_remains_compatible(self):
        choice = self.pick(["claude", "grok"])
        self.assertEqual(choice["worker"], "claude")
        self.assertEqual(choice["independence"], "unknown")
        self.assertEqual(choice["provider"], "anthropic")
        self.assertEqual(choice["provider_source"], "model")
        self.assessment.assert_not_called()

    def test_known_writer_skips_same_provider_across_wrappers(self):
        choice = self.pick(["claude", "grok", "cursor"], writer_cli="cursor", writer_model="claude-sonnet-5")
        self.assertEqual(choice["worker"], "grok")
        self.assertEqual(choice["independence"], "confirmed")
        self.assertEqual(choice["writer_provider"], "anthropic")
        choice = self.pick(["cursor"], writer_model="claude-sonnet-5")
        self.assertEqual(choice["spawn"], "none")
        self.assertIn("matches writer provider anthropic", choice["reason"])

    def test_independent_requires_writer_id_and_current_acceptance(self):
        choice = self.pick(["grok"], review_mode="independent", writer_model="claude-sonnet-5")
        self.assertEqual(choice["spawn"], "none")
        self.assertIn("writer_job_id", choice["reason"])
        self.assessment.return_value = {"state": "pending", "acceptance": "accepted", "reason": "content_changed"}
        choice = self.pick(["grok"], review_mode="independent", writer_job_id="writer")
        self.assertEqual(choice["spawn"], "none")
        self.assertEqual(choice["independence"], "unavailable")
        self.assertIn("content_changed", choice["reason"])

    def test_independent_carries_current_accepted_writer_identity(self):
        cache = {}
        choice = self.pick(["claude", "grok"], review_mode="independent", writer_job_id="writer", hash_cache=cache)
        self.assertEqual(choice["worker"], "grok")
        self.assertEqual(choice["writer_job_id"], "writer")
        self.assertEqual(choice["writer_snapshot_id"], "accepted-snapshot")
        self.assertEqual(choice["writer_provider"], "anthropic")
        self.assertEqual(choice["independence"], "confirmed")
        self.assertTrue(self.assessment.call_args.kwargs["refresh"])
        self.assertIs(self.assessment.call_args.kwargs["cache"], cache)
        self.assertIn("RIG_WRITER_JOB_ID=writer", route.format_text(choice))
        self.assertIn("RIG_REVIEW_MODE=independent", route.format_text(choice))

    def test_inferred_legacy_model_cannot_establish_independence(self):
        self.writer.update(model_source="unknown", model_inferred=True)
        choice = self.pick(["grok"], writer_job_id="writer", review_mode="independent", jobs_snapshot=[self.writer])
        self.assertEqual(choice["spawn"], "none")
        self.assertIn("known actual writer provider", choice["reason"])
        choice = self.pick(
            ["grok"], writer_job_id="writer", review_mode="independent",
            jobs_snapshot=[self.writer], writer_model="claude-sonnet-5",
        )
        self.assertEqual(choice["independence"], "confirmed")

    def test_conflicting_recorded_provenance_errors(self):
        for supplied in (
            {"writer_cli": "grok"}, {"writer_model": "gpt-5.6-luna"}, {"writer_provider": "xai"},
        ):
            with self.subTest(supplied=supplied), self.assertRaisesRegex(ValueError, "conflicts"):
                self.pick(["grok"], writer_job_id="writer", **supplied)
        with self.assertRaisesRegex(ValueError, "conflicts"):
            self.pick(["grok"], writer_model="claude-sonnet-5", writer_provider="xai")

    def test_explicit_provider_fills_an_unknown_alias(self):
        self.writer.update(model="corporate-alias", model_source="selected")
        choice = self.pick(
            ["grok"], writer_job_id="writer", review_mode="independent",
            writer_provider="anthropic", jobs_snapshot=[self.writer],
        )
        self.assertEqual(choice["writer_provider_source"], "explicit")
        self.assertEqual(choice["independence"], "confirmed")

    def test_actual_resolved_model_provider_is_validated(self):
        choice = self.pick(
            ["opencode", "omp"], writer_job_id="writer", review_mode="independent",
            catalogs={"opencode": ["anthropic/claude-opus-5"], "omp": ["google/gemini-3.1-pro-high"]},
        )
        self.assertEqual(choice["worker"], "omp")
        self.assertEqual(choice["provider"], "google")
        self.assertEqual(choice["independence"], "confirmed")

    def test_unknown_reviewer_provider_cannot_be_independent(self):
        choice = self.pick(
            ["opencode"], writer_job_id="writer", review_mode="independent",
            catalogs={"opencode": ["custom/corporate-best"]},
        )
        self.assertEqual(choice["spawn"], "none")
        self.assertIn("known reviewer model provider", choice["reason"])

    def test_catalogs_are_resolved_in_candidate_order_until_eligible(self):
        with patch.object(route, "resolved_model_for", wraps=route.resolved_model_for) as resolve:
            choice = self.pick(["claude", "grok", "opencode", "omp"], writer_model="claude-sonnet-5")
        self.assertEqual(choice["worker"], "grok")
        self.assertEqual([call.args[0] for call in resolve.call_args_list], ["claude", "grok"])

    def test_banned_selected_reviewer_model_never_launches(self):
        with patch.object(route, "resolved_model_for", return_value=("gpt-6-astra", "high")):
            choice = self.pick(["claude"], writer_provider="xai")
        self.assertEqual(choice["spawn"], "none")
        self.assertIn("parent-only", choice["reason"])

    def test_snapshot_is_authoritative_and_avoids_job_reads(self):
        import jobs
        with patch.object(jobs, "resolve_job", side_effect=AssertionError("unexpected history read")):
            choice = self.pick(["grok"], writer_job_id="write", jobs_snapshot=[self.writer])
            self.assertEqual(choice["writer_job_id"], "writer")
            with self.assertRaisesRegex(ValueError, "no such"):
                self.pick(["grok"], writer_job_id="writer", jobs_snapshot=[])
            with self.assertRaisesRegex(ValueError, "ambiguous"):
                self.pick(["grok"], writer_job_id="write", jobs_snapshot=[self.writer, {**self.writer, "job_id": "writer-two"}])
            with self.assertRaisesRegex(ValueError, "invalid"):
                self.pick(["grok"], writer_job_id="../writer", jobs_snapshot=[self.writer])

    def test_known_writer_still_respects_effective_flags_and_excludes(self):
        choice = self.pick(["claude", "grok"], writer_provider="anthropic", exclude="grok")
        self.assertEqual(choice["spawn"], "none")
        choice = self.pick([], writer_provider="anthropic")
        self.assertEqual(choice["spawn"], "none")
        choice = self.pick(["claude", "cursor"], writer_provider="xai", exclude="claude")
        self.assertEqual(choice["spawn"], "none")
        self.assertIn("Cursor", choice["reason"])

    def test_legacy_parent_placeholder_does_not_conflict_with_actual_cli(self):
        self.writer.update(worker="parent", model="", model_source="unknown")
        choice = self.pick(
            ["claude"], writer_job_id="writer", jobs_snapshot=[self.writer],
            writer_cli="codex", writer_model="gpt-6-astra",
        )
        self.assertEqual(choice["writer_cli"], "codex")
        self.assertEqual(choice["independence"], "confirmed")


class ReviewAcceptedContent(unittest.TestCase):
    def test_edit_after_real_parent_acceptance_invalidates_review(self):
        import admission
        import change_evidence
        import subprocess
        import verification

        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {
            "RIG_SKIP_MODEL_CATALOG": "1", "RIG_PARENT": "claude", "RIG_JOB_ID": "", "RIG_JOB_DIR": "",
        }):
            repo = Path(temp).resolve()
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            folder = repo / ".rig" / "jobs" / "writer"
            folder.mkdir(parents=True)
            (repo / "subject.txt").write_text("accepted content\n")
            owner_session = "accepted-writer-test"
            owner = admission.caller_owner("parent", owner_session=owner_session)
            reserved = admission.reserve(repo, job_id="writer", worker="parent", role="implement",
                                         model="claude-sonnet-5", files=["subject.txt"], access="write", owner=owner)
            credentials = {**admission.credentials(reserved), "owner_session": owner_session}
            admission.activate(repo, job_id="writer", worker="parent", files=["subject.txt"], access="write", **credentials)
            admission.finish(repo, status="ok", completion={"kind": "parent_task", "completed": True}, **credentials)
            (folder / "meta.json").write_text(json.dumps({
                "job_id": "writer", "worker": "parent", "model": "claude-sonnet-5",
                "model_source": "selected", "status": "ok", "execution_mode": "parent",
                "files": ["subject.txt"], "access": "write", "owner": reserved["owner"],
                "ownership_established": True, "reservation_id": reserved["reservation_id"],
                "attempt_id": reserved["attempt_id"],
            }))
            verification.record_requirements(repo, folder, [], ["Check the subject text"], **credentials)
            snapshot_id = change_evidence.snapshot(repo, ["subject.txt"])["snapshot_id"]
            verification.accept(repo, folder, "accept", snapshot_id, rationale="Subject text checked", next="review", **credentials)
            choice = route.pick("codex", ["grok"], "review", "review diff", repo=repo,
                                writer_job_id="writer", review_mode="independent")
            self.assertEqual(choice["independence"], "confirmed")
            self.assertEqual(choice["writer_snapshot_id"], snapshot_id)
            self.assertEqual(choice["writer_provider"], "anthropic")
            (repo / "subject.txt").write_text("changed after acceptance\n")
            choice = route.pick("codex", ["grok"], "review", "review diff", repo=repo,
                                writer_job_id="writer", review_mode="independent")
            self.assertEqual(choice["spawn"], "none")
            self.assertIn("content_changed", choice["reason"])


if __name__ == "__main__":
    unittest.main()
