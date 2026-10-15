#!/usr/bin/env python3
"""Rig-wide OpenCode cheap-model policy: allowlist, roles, catalog, config migration."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import catalog  # noqa: E402
import opencode_policy as policy  # noqa: E402
import route  # noqa: E402
import routing_config  # noqa: E402
import routing_profiles as profiles  # noqa: E402

GLM = "opencode-go/glm-5.3-flash"
DEEPSEEK = "opencode-go/deepseek-v4.1-flash"
MUSE = "opencode-go/muse-spark-1.3-contributor"


class Allowlist(unittest.TestCase):
    def test_exact_selectors_in_preference_order(self):
        self.assertEqual(policy.APPROVED, (GLM, DEEPSEEK, MUSE))
        self.assertEqual(policy.DEFAULT_MODEL, GLM)

    def test_approved_selectors_pass_for_every_supported_role(self):
        for model in policy.APPROVED:
            for role in policy.ROLES:
                with self.subTest(model=model, role=role):
                    self.assertIsNone(route.assert_opencode_model("opencode", model, role, ""))

    def test_other_workers_are_untouched(self):
        for worker in ("grok", "claude", "codex", "omp", "pi", "agy", "devin", "mimo", "cursor", ""):
            self.assertIsNone(route.assert_opencode_model(worker, "gpt-6-luna", "hard", "high"))
            self.assertIsNone(route.assert_opencode_model(worker, "", "review"))

    def test_no_cheaper_named_or_suffix_aliases(self):
        for model in (
            "glm-5.3-flash", "zai/glm-5.3-flash", "opencode/glm-5.3-flash", "opencode-go/glm-5.3",
            "opencode-go/glm-5.3-flash-lite", "opencode-go/glm-5.3-flash-preview", "OPENCODE-GO/GLM-5.3-FLASH",
            " opencode-go/glm-5.3-flashx", "opencode-go/deepseek-v4.1", "deepseek-v4.1-flash",
            "opencode-go/muse-spark-1.3", "opencode-go/glm-5.3-flash/extra", "opencode-go/flash",
            "anthropic/claude-haiku-4-5-20251001", "openai/gpt-5.4-mini",
        ):
            with self.subTest(model=model):
                problem = route.assert_opencode_model("opencode", model, "implement")
                self.assertTrue(problem)
                self.assertIn(policy.approved_text(), problem)

    def test_empty_model_is_rejected_with_approved_list(self):
        for model in ("", "   "):
            problem = route.assert_opencode_model("opencode", model, "implement")
            self.assertIn("explicit approved model", problem)
            self.assertIn(GLM, problem)

    def test_removed_selectors_get_migration_message(self):
        for model in ("openai/gpt-5.4-mini", "openai/gpt-6-luna", "openai/gpt-5.6-terra", "gpt-6-luna"):
            with self.subTest(model=model):
                problem = route.assert_opencode_model("opencode", model, "implement")
                self.assertIn("removed", problem)
                self.assertIn(GLM, problem)

    def test_hard_and_review_roles_fail_eligibility(self):
        for role in ("hard", "review", "implement-hard", "reviewer"):
            with self.subTest(role=role):
                self.assertIn("does not support", route.assert_opencode_model("opencode", GLM, role))

    def test_effort_is_rejected(self):
        self.assertIn("no --variant", route.assert_opencode_model("opencode", GLM, "implement", "high"))
        self.assertIsNone(route.assert_opencode_model("opencode", GLM, "implement", ""))

    def test_allow_command_rejects_before_launch(self):
        import subprocess

        script = str(Path(route.__file__))
        base = [sys.executable, script, "allow", "--worker", "opencode", "--role", "implement"]
        for model in ("openai/gpt-6-luna", ""):
            proc = subprocess.run([*base, "--model", model], capture_output=True, text=True)
            self.assertEqual(proc.returncode, 1, model)
            self.assertIn(GLM, proc.stderr)
        proc = subprocess.run([*base[:-1], "hard", "--model", GLM], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 1)


class CatalogConfirmation(unittest.TestCase):
    def info(self, state, ids, **extra):
        return {"state": state, "ids": ids, **extra}

    def test_exact_catalog_match_is_required(self):
        self.assertIsNone(policy.catalog_problem(GLM, self.info("fresh", [GLM, "other/model"])))
        self.assertIsNone(policy.catalog_problem(GLM, self.info("stale", [GLM.upper()])))
        for ids in (["opencode-go/glm-5.3-flash-preview"], ["glm-5.3-flash"], ["x/opencode-go/glm-5.3-flash"], [DEEPSEEK]):
            with self.subTest(ids=ids):
                self.assertIn("exact selector", policy.catalog_problem(GLM, self.info("fresh", ids)))

    def test_unavailable_unconfirmed_and_empty_catalogs_mark_opencode_unavailable(self):
        for state, ids, extra in (
            ("unavailable", None, {}), ("skipped", None, {}), ("unverified", None, {}),
            ("empty", [], {"confirmed_empty": True}),
        ):
            with self.subTest(state=state):
                self.assertIn("OpenCode unavailable", policy.catalog_problem(GLM, self.info(state, ids, **extra)))

    def test_live_lookup_uses_catalog_info(self):
        with patch.object(catalog, "load_catalog_info", return_value=self.info("fresh", [MUSE])) as load:
            self.assertIsNone(policy.catalog_problem(MUSE))
            self.assertIn("exact selector", policy.catalog_problem(GLM))
        self.assertEqual(load.call_count, 2)

    def test_over_stale_catalog_is_refreshed_once(self):
        old = self.info("stale", [GLM], age_s=catalog.STALE_MAX_SECONDS + 5)
        fresh = self.info("fresh", [GLM], age_s=0)
        with patch.object(catalog, "load_catalog_info", side_effect=[old, fresh]) as load:
            self.assertIsNone(policy.catalog_problem(GLM))
        self.assertEqual(load.call_args_list[1].kwargs, {"require_fresh": True})

    def test_catalog_resolution_never_substitutes_opencode_models(self):
        ids = ["opencode-go/glm-5.3-flash-lite", "opencode-go/other-flash"]
        self.assertEqual(catalog.resolve_model("opencode", "explore", GLM, catalogs={"opencode": ids}), GLM)
        self.assertEqual(catalog.resolve_model("opencode", "implement", GLM, catalog=ids), GLM)


class BuiltinProfiles(unittest.TestCase):
    def setUp(self):
        self.rows = {pid: p for pid, p in profiles.profiles_by_id().items() if p.worker == "opencode"}

    def test_only_new_profiles_exist_with_exact_selectors(self):
        self.assertEqual(
            {pid: p.selector for pid, p in self.rows.items()},
            {"opencode-glm-5.3-flash": GLM, "opencode-deepseek-v4.1-flash": DEEPSEEK,
             "opencode-muse-spark-1.3-contributor": MUSE},
        )
        for removed in policy.REMOVED_PROFILE_IDS:
            self.assertNotIn(removed, profiles.profiles_by_id())

    def test_profile_shape_roles_effort_providers_aliases(self):
        self.assertEqual(
            {p.selector: p.provider for p in self.rows.values()},
            {GLM: "zai", DEEPSEEK: "deepseek", MUSE: "meta"},
        )
        for profile in self.rows.values():
            self.assertEqual(profile.effort, "")
            self.assertEqual(profile.supported_efforts, ("",))
            self.assertEqual(profile.aliases, ())
            self.assertEqual(set(profile.roles), {"explore", "mini", "bulk", "implement", "verify"})
            self.assertNotIn("strong", profile.tiers)
            self.assertTrue(profile.catalog_required)
            self.assertFalse(profile.allows_role("hard") or profile.allows_role("review"))

    def test_default_preference_order_is_glm_deepseek_muse(self):
        rows = profiles.profiles_by_id()
        for tier in ("fast", "standard"):
            order = [pid for pid in profiles.default_preference_ids(tier, rows) if pid in self.rows]
            self.assertEqual(order, list(policy.PROFILE_IDS))
        for pid in profiles.default_preference_ids("strong", rows, role="review"):
            self.assertNotIn(pid, self.rows)
        for pid in profiles.default_preference_ids("strong", rows):
            self.assertNotIn(pid, self.rows)

    def test_canonical_model_strips_only_known_transport_and_provider_prefixes(self):
        for raw, expected in (
            ("opencode-go/glm-5.3-flash", "glm-5.3-flash"), ("zai/glm-5.3-flash", "glm-5.3-flash"),
            ("deepseek/deepseek-v4.1-flash", "deepseek-v4.1-flash"), ("meta/muse-spark-1.3-contributor", "muse-spark-1.3-contributor"),
            ("metadata/foo", "metadata/foo"), ("opencode/glm-5.3-flash", "opencode/glm-5.3-flash"),
            ("opencode-go/zai/glm-5.3-flash", "zai/glm-5.3-flash"), ("openai/gpt-6-luna", "gpt-6-luna"),
        ):
            with self.subTest(raw=raw):
                self.assertEqual(profiles.canonical_model(raw), expected)

    def test_provider_enums_include_new_canonical_providers(self):
        for provider in ("zai", "deepseek", "meta"):
            self.assertIn(provider, profiles.KNOWN_PROVIDERS)
            self.assertIn(provider, route.PROVIDERS)
        self.assertNotIn("opencode-go", route.PROVIDERS)
        self.assertNotIn("opencode-go", profiles.KNOWN_PROVIDERS)


class ConfigMigration(unittest.TestCase):
    def load(self, raw):
        return routing_config.validate_candidate(raw)

    def test_removed_profile_ids_fail_with_migration_guidance(self):
        for pid in policy.REMOVED_PROFILE_IDS:
            for raw in (
                {"schema_version": 4, "preferences": {"standard": [pid]}},
                {"schema_version": 4, "profiles": {pid: {"roles": ["implement"]}}},
                {"schema_version": 4, "domains": {"backend": {"preferred_profiles": [pid], "fallback": "none"}}},
            ):
                with self.subTest(pid=pid, keys=sorted(raw)):
                    with self.assertRaises(routing_config.ConfigError) as caught:
                        self.load(raw)
                    self.assertIn("removed", str(caught.exception))
                    self.assertIn("opencode-glm-5.3-flash", str(caught.exception))

    def test_disallowed_override_values_fail_clearly(self):
        pid = "opencode-glm-5.3-flash"
        for override, text in (
            ({"selector": "openai/gpt-6-luna"}, "removed"),
            ({"selector": "opencode-go/glm-5.3"}, "exact OpenCode Go selectors"),
            ({"aliases": ["glm-5.3-flash"]}, "exact selectors only"),
            ({"effort": "high", "supported_efforts": ["high"]}, "no --variant"),
            ({"roles": ["hard"]}, "supports roles"),
            ({"roles": ["review"]}, "supports roles"),
            ({"tiers": ["strong"]}, "strong tier"),
        ):
            with self.subTest(override=override):
                with self.assertRaises(routing_config.ConfigError) as caught:
                    self.load({"schema_version": 4, "profiles": {pid: override}})
                self.assertIn(text, str(caught.exception))

    def test_custom_opencode_profile_must_use_approved_selector(self):
        base = {"worker": "opencode", "roles": ["implement"], "tiers": ["standard"], "effort": "", "provider": "openai",
                "supported_efforts": ["x"]}
        with self.assertRaises(routing_config.ConfigError) as caught:
            self.load({"schema_version": 4, "profiles": {"mine": {**base, "selector": "openai/gpt-6-luna"}}})
        self.assertIn("removed", str(caught.exception))

    def test_valid_override_of_approved_profile_still_loads(self):
        cfg = self.load({"schema_version": 4, "preferences": {"fast": ["opencode-deepseek-v4.1-flash"]}})
        self.assertEqual(cfg.preferences["fast"][0], "opencode-deepseek-v4.1-flash")

    def test_legacy_mode_tolerates_old_config_on_disk_without_rewriting_it(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / ".rig" / "routing.json"
            path.parent.mkdir()
            text = json.dumps({"schema_version": 4, "preferences": {"fast": ["opencode-gpt-5.4-mini-minimal"]}})
            path.write_text(text)
            cfg = routing_config.load_config(Path(td), policy_mode="legacy")
            self.assertEqual(cfg.source, "builtin")
            with self.assertRaises(routing_config.ConfigError):
                routing_config.load_config(Path(td), policy_mode="smart")
            self.assertEqual(path.read_text(), text)


def smart(effective, role="implement", catalogs=None, **kwargs):
    kwargs.setdefault("complexity", "medium")
    kwargs.setdefault("risk", "medium")
    kwargs.setdefault("uncertainty", "medium")
    return route.pick("", effective, role, "add a header", policy_mode="smart",
                      catalogs=catalogs if catalogs is not None else {}, **kwargs)


class SmartRouting(unittest.TestCase):
    def test_default_is_glm_then_deepseek_then_muse(self):
        both = smart(["opencode"], catalogs={"opencode": [MUSE, DEEPSEEK, GLM]})
        self.assertEqual((both["worker"], both["model"], both["effort"]), ("opencode", GLM, ""))
        second = smart(["opencode"], catalogs={"opencode": [MUSE, DEEPSEEK]})
        self.assertEqual(second["model"], DEEPSEEK)
        third = smart(["opencode"], catalogs={"opencode": [MUSE]})
        self.assertEqual(third["model"], MUSE)
        self.assertEqual(third["provider"], "meta")

    def test_all_approved_unavailable_marks_opencode_unavailable(self):
        for catalogs in (
            {"opencode": None}, {"opencode": []},
            {"opencode": ["opencode-go/glm-5.3-flash-preview", "openai/gpt-6-luna", "opencode-go/kimi-k2"]},
        ):
            with self.subTest(catalogs=catalogs):
                choice = smart(["opencode"], catalogs=catalogs)
                self.assertEqual(choice["spawn"], "none")
                codes = {row["code"] for row in choice["routing"]["candidate_decisions"]
                         if row["id"] in policy.PROFILE_IDS}
                self.assertTrue(codes <= {"catalog-miss", "catalog-empty", "catalog-unavailable", "catalog-unconfirmed"}, codes)

    def test_other_workers_remain_when_opencode_is_unavailable(self):
        choice = smart(["opencode", "omp"], catalogs={"opencode": [], "omp": ["xai-oauth/grok-4.6"]})
        self.assertEqual(choice["worker"], "omp")

    def test_hard_review_and_strong_tier_never_select_opencode(self):
        catalogs = {"opencode": list(policy.APPROVED)}
        for role in ("hard", "review"):
            with self.subTest(role=role):
                choice = smart(["opencode"], role=role, catalogs=catalogs)
                self.assertNotEqual(choice["worker"], "opencode")
        high = smart(["opencode"], catalogs=catalogs, complexity="high", risk="high", uncertainty="high")
        self.assertNotEqual(high["worker"], "opencode")

    def test_cheap_roles_use_opencode_without_effort(self):
        catalogs = {"opencode": list(policy.APPROVED)}
        for role, case_complexity in (("explore", "low"), ("mini", "low"), ("bulk", "low"), ("verify", "medium")):
            with self.subTest(role=role):
                choice = smart(["opencode"], role=role, catalogs=catalogs, complexity=case_complexity,
                               risk="low" if case_complexity == "low" else "medium",
                               uncertainty="low" if case_complexity == "low" else "medium")
                self.assertEqual((choice["worker"], choice["effort"]), ("opencode", ""))
                self.assertIn(choice["model"], policy.APPROVED)

    def test_explicit_worker_choice_never_substitutes_models(self):
        import routing_policy

        choice = routing_policy.resolve_explicit_worker_choice(
            "", "opencode", "implement", "add a header", catalogs={"opencode": [DEEPSEEK]},
            assessment={"complexity": "medium", "risk": "medium", "uncertainty": "medium"},
        )
        self.assertEqual(choice["model"], DEEPSEEK)
        with self.assertRaises(ValueError):
            routing_policy.resolve_explicit_worker_choice(
                "", "opencode", "implement", "add a header", catalogs={"opencode": ["openai/gpt-6-luna"]},
                assessment={"complexity": "medium", "risk": "medium", "uncertainty": "medium"},
            )

    def test_legacy_routes_use_the_same_policy(self):
        legacy = route.pick("", ["opencode"], "implement", "add a header", policy_mode="legacy",
                            catalogs={"opencode": [GLM]})
        self.assertEqual((legacy["model"], legacy["effort"]), (GLM, ""))
        self.assertEqual(legacy["provider"], "zai")


class ReviewIndependence(unittest.TestCase):
    def test_reviewer_with_same_family_as_opencode_writer_is_rejected(self):
        ctx = {"review_mode": "independent", "writer_providers": ["zai"], "writer_provider": "zai"}
        self.assertEqual(route._review_model(DEEPSEEK, ctx)[0], "confirmed")
        self.assertEqual(route._review_model("zai/glm-5.3-flash", ctx)[0], "unavailable")

    def test_unknown_opencode_family_cannot_be_independent(self):
        ctx = {"review_mode": "independent", "writer_providers": ["meta"], "writer_provider": "meta"}
        independence, reason = route._review_model("opencode-go/unlisted-model", ctx)
        self.assertEqual(independence, "unavailable")
        self.assertIn("known reviewer model provider", reason)

    def test_writer_context_records_family_for_opencode_writer(self):
        context, reason = route._writer_context(writer_cli="opencode", writer_model=MUSE)
        self.assertEqual(context["writer_provider"], "meta")
        self.assertEqual(context["writer_provider_source"], "model")
        self.assertEqual(reason, "")
        with self.assertRaises(ValueError):
            route._writer_context(writer_cli="opencode", writer_model=MUSE, writer_provider="zai")


if __name__ == "__main__":
    unittest.main()
