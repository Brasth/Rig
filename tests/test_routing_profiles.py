#!/usr/bin/env python3
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import route  # noqa: E402
import routing_profiles as profiles  # noqa: E402


class BuiltinPins(unittest.TestCase):
    def setUp(self):
        self.rows = profiles.profiles_by_id()

    def test_selectors_come_from_route_models(self):
        mapping = {
            "grok-4.5-low": ("grok", "explore"),
            "grok-4.6-high": ("grok", "implement"),
            "claude-haiku-4-5-low": ("claude", "explore"),
            "claude-sonnet-5-medium": ("claude", "implement"),
            "claude-opus-5-high": ("claude", "hard"),
            "codex-explorer-low": ("codex", "explore"),
            "codex-luna-low": ("codex", "mini"),
            "codex-terra-medium": ("codex", "hard"),
            "codex-terra-high": ("codex", "review"),
            "opencode-gpt-5.4-mini-minimal": ("opencode", "explore"),
            "opencode-gpt-5.6-luna-high": ("opencode", "implement"),
            "opencode-gpt-5.6-terra-max": ("opencode", "hard"),
            "omp-grok-4.5-low": ("omp", "explore"),
            "omp-grok-4.6-high": ("omp", "implement"),
            "omp-claude-opus-5-high": ("omp", "review"),
            "pi-grok-4.5-low": ("pi", "explore"),
            "pi-grok-4.6-high": ("pi", "implement"),
            "pi-claude-opus-5-high": ("pi", "review"),
            "agy-gemini-3.8-flash-low": ("agy", "explore"),
            "agy-gemini-3.8-flash-high": ("agy", "implement"),
            "agy-gemini-3.1-pro-high": ("agy", "hard"),
        }
        for pid, (worker, kind) in mapping.items():
            model, effort = route.model_for(worker, kind)
            self.assertEqual(self.rows[pid].selector, model, pid)
            self.assertEqual(self.rows[pid].effort, effort, pid)

    def test_codex_explorer_is_read_only(self):
        explorer = self.rows["codex-explorer-low"]
        self.assertEqual(explorer.roles, ("explore",))
        self.assertEqual(explorer.tiers, ("fast",))
        self.assertTrue(set(explorer.roles).isdisjoint(profiles.WRITE_ROLES))

    def test_high_explore_uses_standard_and_strong_read_profiles(self):
        for pid in (
            "grok-4.6-high",
            "claude-sonnet-5-medium",
            "claude-opus-5-high",
            "omp-grok-4.6-high",
            "pi-grok-4.6-high",
            "omp-claude-opus-5-high",
            "pi-claude-opus-5-high",
        ):
            self.assertIn("explore", self.rows[pid].roles, pid)

    def test_omp_pi_opus_are_not_review_only(self):
        for pid in ("omp-claude-opus-5-high", "pi-claude-opus-5-high"):
            roles = set(self.rows[pid].roles)
            for role in ("hard", "implement", "mini", "bulk", "review", "explore"):
                self.assertIn(role, roles, f"{pid} {role}")


class PreferenceOrder(unittest.TestCase):
    def test_fast_default_includes_codex_explorer(self):
        rows = profiles.profiles_by_id()
        fast = profiles.default_preference_ids("fast", rows)
        self.assertIn("codex-explorer-low", fast)
        self.assertLess(fast.index("grok-4.5-low"), fast.index("claude-haiku-4-5-low"))

    def test_review_default_puts_claude_before_grok(self):
        rows = profiles.profiles_by_id()
        review = profiles.default_preference_ids("strong", rows, role="review")
        self.assertLess(review.index("claude-opus-5-high"), review.index("grok-4.6-high"))

    def test_override_mentions_then_appends_unmentioned(self):
        rows = profiles.profiles_by_id()
        default = profiles.default_preference_ids("fast", rows)
        mentioned = ["claude-haiku-4-5-low", "codex-explorer-low"]
        ordered = profiles.preference_order(mentioned, default)
        self.assertEqual(ordered[:2], mentioned)
        self.assertIn("grok-4.5-low", ordered)
        self.assertGreater(ordered.index("grok-4.5-low"), ordered.index("codex-explorer-low"))
        self.assertEqual(len(ordered), len(set(ordered)))

    def test_permutation_of_profile_dict_is_stable(self):
        rows = profiles.profiles_by_id()
        baseline = profiles.default_preference_ids("fast", rows)
        shuffled = dict(reversed(list(rows.items())))
        self.assertEqual(profiles.default_preference_ids("fast", shuffled), baseline)
        self.assertEqual(profiles.default_preference_ids("strong", shuffled, role="review"), profiles.default_preference_ids("strong", rows, role="review"))


if __name__ == "__main__":
    unittest.main()
