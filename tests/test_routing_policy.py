#!/usr/bin/env python3
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import catalog  # noqa: E402
import route  # noqa: E402
import routing_config  # noqa: E402
import routing_policy as policy  # noqa: E402
import routing_profiles as profiles  # noqa: E402


def smart_pick(live, effective, role, case, **kwargs):
    kwargs.setdefault("policy_mode", "smart")
    kwargs.setdefault("catalogs", {})
    return route.pick(live, effective, role, case, **kwargs)


class Assessment(unittest.TestCase):
    def test_role_defaults(self):
        cheap = policy.normalize_assessment("explore")
        self.assertEqual((cheap["complexity"], cheap["risk"], cheap["uncertainty"]), ("low", "low", "low"))
        self.assertEqual(cheap["defaulted"], ["complexity", "risk", "uncertainty"])
        self.assertFalse(cheap["supplied"])
        implement = policy.normalize_assessment("implement")
        self.assertEqual((implement["complexity"], implement["risk"], implement["uncertainty"]), ("medium", "medium", "medium"))
        hard = policy.normalize_assessment("hard")
        self.assertEqual((hard["complexity"], hard["risk"], hard["uncertainty"]), ("high", "medium", "high"))
        review = policy.normalize_assessment("review")
        self.assertEqual((review["complexity"], review["risk"], review["uncertainty"]), ("high", "medium", "medium"))

    def test_partial_metadata_merges_defaults(self):
        got = policy.normalize_assessment("implement", {"complexity": "high", "reason": "multi-file"})
        self.assertEqual(got["complexity"], "high")
        self.assertEqual(got["risk"], "medium")
        self.assertEqual(got["uncertainty"], "medium")
        self.assertEqual(got["defaulted"], ["risk", "uncertainty"])
        self.assertTrue(got["supplied"])
        self.assertEqual(got["reason"], "multi-file")

    def test_raw_invalid_rejected(self):
        with self.assertRaises(ValueError):
            policy.normalize_assessment("implement", ["high"])
        with self.assertRaises(ValueError):
            policy.normalize_assessment("implement", {"complexity": "high", "extra": "nope"})
        with self.assertRaises(ValueError):
            policy.normalize_assessment("implement", {"complexity": True})
        with self.assertRaises(ValueError):
            policy.normalize_assessment("implement", {"complexity": 1})
        with self.assertRaises(ValueError):
            policy.normalize_assessment("implement", {"complexity": "extreme"})
        with self.assertRaises(ValueError):
            policy.normalize_assessment("implement", {"reason": 3})

    def test_normalize_once_keeps_defaulted(self):
        first = policy.normalize_assessment("implement")
        second = policy.normalize_assessment("implement", first)
        self.assertEqual(second["defaulted"], ["complexity", "risk", "uncertainty"])
        self.assertFalse(second["supplied"])
        choice = smart_pick("codex", ["grok"], "implement", "add a header")
        self.assertEqual(choice["routing"]["assessment"]["defaulted"], ["complexity", "risk", "uncertainty"])

    def test_normalized_metadata_cannot_bypass_dimension_validation(self):
        for extra in ({"complexity": ""}, {"defaulted": ["unknown"]}, {"defaulted": [False]}):
            assessment = dict(policy.normalize_assessment("implement"), **extra)
            with self.assertRaises(ValueError):
                policy.normalize_assessment("implement", assessment)

    def test_stay_validates_but_does_not_use_dims(self):
        got = policy.normalize_assessment("stay", {"complexity": "high", "reason": "ask"})
        self.assertEqual(got["complexity"], "")
        self.assertEqual(got["reason"], "ask")
        self.assertTrue(got["supplied"])
        with self.assertRaises(ValueError):
            policy.normalize_assessment("stay", {"complexity": "nope"})
        choice = smart_pick("grok", ["claude"], "stay", "advise", catalogs={"opencode": ["openai/gpt-5.6-luna"]})
        self.assertEqual(choice["spawn"], "stay")
        self.assertEqual(choice["routing"]["catalog"]["source"], "none")

    def test_required_tier_rules(self):
        low = policy.normalize_assessment("implement", {"complexity": "low", "risk": "low", "uncertainty": "low"})
        self.assertEqual(policy.required_tier("implement", low), "fast")
        mid = policy.normalize_assessment("mini", {"complexity": "medium"})
        self.assertEqual(policy.required_tier("mini", mid), "standard")
        high = policy.normalize_assessment("explore", {"uncertainty": "high"})
        self.assertEqual(policy.required_tier("explore", high), "strong")
        self.assertEqual(policy.required_tier("hard", low), "strong")
        self.assertEqual(policy.required_tier("review", low), "strong")


class SmartSelection(unittest.TestCase):
    def test_low_implement_uses_fast_grok(self):
        choice = smart_pick(
            "codex", ["grok", "claude"], "implement", "add a header",
            complexity="low", risk="low", uncertainty="low",
        )
        self.assertEqual(choice["worker"], "grok")
        self.assertEqual(choice["model"], "grok-4.5")
        self.assertEqual(choice["effort"], "low")
        self.assertEqual(choice["routing"]["required_tier"], "fast")
        self.assertEqual(choice["routing"]["selected_profile"]["id"], "grok-4.5-low")

    def test_high_mini_and_high_explore_use_strong_read_capable(self):
        mini = smart_pick(
            "codex", ["grok", "claude"], "mini", "tiny comment",
            complexity="high", risk="low", uncertainty="low",
        )
        self.assertEqual(mini["worker"], "claude")
        self.assertEqual(mini["model"], "claude-opus-5")
        explore = smart_pick(
            "codex", ["grok", "claude"], "explore", "trace remaining gates",
            complexity="high", risk="low", uncertainty="low",
        )
        self.assertEqual(explore["worker"], "claude")
        self.assertEqual(explore["model"], "claude-opus-5")
        self.assertEqual(explore["spawn"], "run-worker")
        self.assertIn("explore", profiles.profiles_by_id()["claude-opus-5-high"].roles)

    def test_smart_stronger_cross_worker_beats_legacy_grok_first(self):
        smart = smart_pick(
            "codex", ["grok", "claude"], "implement", "add a header",
            complexity="high", risk="high", uncertainty="high",
        )
        self.assertEqual(smart["worker"], "claude")
        self.assertEqual(smart["model"], "claude-opus-5")
        legacy = route.pick(
            "codex", ["grok", "claude"], "implement", "add a header",
            complexity="high", risk="high", uncertainty="high", policy_mode="legacy",
        )
        self.assertEqual(legacy["worker"], "grok")
        self.assertEqual(legacy["model"], "grok-4.6")

    def test_filters_live_exclude_cursor(self):
        live = smart_pick("grok", ["grok", "claude"], "implement", "add a header")
        self.assertEqual(live["worker"], "claude")
        excluded = smart_pick("codex", ["grok", "claude"], "implement", "add a header", exclude="grok")
        self.assertEqual(excluded["worker"], "claude")
        cursor = smart_pick("codex", ["cursor", "claude"], "implement", "add a header")
        self.assertEqual(cursor["worker"], "claude")
        codes = {row["code"] for row in cursor["routing"]["candidate_decisions"] if row["id"].startswith("cursor-")}
        self.assertIn("cursor-excluded", codes)
        one = {row["id"]: row["code"] for row in cursor["routing"]["candidate_decisions"]}
        self.assertEqual(len(one), len(cursor["routing"]["candidate_decisions"]))

    def test_preference_override_appends_unmentioned(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            (repo / ".rig").mkdir()
            (repo / ".rig" / "routing.json").write_text(json.dumps({
                "schema_version": 1,
                "preferences": {"fast": ["claude-haiku-4-5-low"]},
            }))
            choice = smart_pick(
                "codex", ["grok", "claude"], "implement", "add a header",
                repo=repo, complexity="low", risk="low", uncertainty="low",
            )
            self.assertEqual(choice["worker"], "claude")
            self.assertEqual(choice["model"], "claude-haiku-4-5-20251001")
            cfg = policy.load_config(repo, policy_mode="smart")
            self.assertEqual(cfg.preferences["fast"][0], "claude-haiku-4-5-low")
            self.assertIn("grok-4.5-low", cfg.preferences["fast"])
            self.assertIn("codex-explorer-low", cfg.preferences["fast"])

    def test_parent_fallback_after_all_wrappers(self):
        choice = smart_pick("codex", [], "implement", "add a header")
        self.assertEqual(choice["spawn"], "native")
        self.assertEqual(choice["worker"], "codex")
        self.assertEqual(choice["model"], "")
        self.assertEqual(choice["model_source"], "unknown")
        observed = smart_pick("codex", [], "implement", "add a header", parent_model="gpt-5.6-luna", parent_effort="low")
        self.assertEqual(observed["model"], "gpt-5.6-luna")
        self.assertEqual(observed["effort"], "low")

    def test_catalog_exact_alias_not_substring(self):
        catalogs = {"opencode": ["openai/gpt-5.6-luna", "openai/gpt-5.6-luna-preview"]}
        choice = smart_pick(
            "", ["opencode"], "implement", "add a header",
            catalogs=catalogs, complexity="medium", risk="medium", uncertainty="medium",
        )
        self.assertEqual(choice["worker"], "opencode")
        self.assertEqual(choice["model"], "openai/gpt-5.6-luna")
        miss = smart_pick(
            "", ["opencode"], "implement", "add a header",
            catalogs={"opencode": ["openai/gpt-5.6-luna-preview"]},
            complexity="medium", risk="medium", uncertainty="medium",
        )
        self.assertEqual(miss["spawn"], "none")
        codes = {row["code"] for row in miss["routing"]["candidate_decisions"] if row["id"] == "opencode-gpt-5.6-luna-high"}
        self.assertIn("catalog-miss", codes)

    def test_review_uses_actual_provider_and_invalid_mode_errors(self):
        choice = smart_pick(
            "codex", ["claude", "grok"], "review", "review the writer diff",
            writer_cli="cursor", writer_model="claude-sonnet-5",
        )
        self.assertEqual(choice["worker"], "grok")
        self.assertEqual(choice["independence"], "confirmed")
        with self.assertRaises(ValueError):
            smart_pick("codex", ["grok"], "implement", "add a header", review_mode="weird")
        with self.assertRaises(ValueError):
            route.pick("codex", ["grok"], "implement", "add a header", policy_mode="legacy", review_mode="weird")

    def test_permutation_stability(self):
        first = smart_pick("codex", ["agy", "claude", "grok", "codex"], "implement", "add a header")
        second = smart_pick("codex", ["codex", "grok", "claude", "agy"], "implement", "add a header")
        self.assertEqual(first["worker"], second["worker"])
        self.assertEqual(first["model"], second["model"])
        self.assertEqual(first["routing"]["selected_profile"]["id"], second["routing"]["selected_profile"]["id"])


class ConfigValidation(unittest.TestCase):
    def test_duplicate_json_keys_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            (repo / ".rig").mkdir()
            (repo / ".rig" / "routing.json").write_text(
                '{"schema_version": 1, "schema_version": 2, "profiles": {}}\n'
            )
            with self.assertRaises(policy.ConfigError) as ctx:
                policy.load_config(repo, policy_mode="smart")
            self.assertIn("duplicate", str(ctx.exception).lower())
            legacy = policy.load_config(repo, policy_mode="legacy")
            self.assertEqual(legacy.source, "builtin")

    def test_invalid_profile_and_strict_bool(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            (repo / ".rig").mkdir()
            (repo / ".rig" / "routing.json").write_text(json.dumps({
                "schema_version": 1,
                "profiles": {"grok-4.6-high": {"catalog_required": "false"}},
            }))
            with self.assertRaises(policy.ConfigError):
                policy.load_config(repo, policy_mode="smart")
            (repo / ".rig" / "routing.json").write_text(json.dumps({
                "schema_version": 1,
                "profiles": {"omp-grok-4.6-high": {"catalog_required": False}},
            }))
            with self.assertRaises(policy.ConfigError) as ctx:
                policy.load_config(repo, policy_mode="smart")
            self.assertIn("catalog-required", str(ctx.exception))

    def test_schema_version_and_alias_cannot_bypass_validation(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            (repo / ".rig").mkdir()
            for config in (
                {"schema_version": True}, {"schema_version": 1.0},
                {"schema_version": 1, "profiles": {"codex-luna-low": {
                    "aliases": ["GPT-5.3-CODEX-MINI"]}}},
                {"schema_version": 1, "profiles": {
                    "grok-4.6-high": {}, " grok-4.6-high ": {}}},
            ):
                (repo / ".rig/routing.json").write_text(json.dumps(config))
                with self.assertRaises(policy.ConfigError):
                    policy.load_config(repo, policy_mode="smart")

    def test_alias_provider_and_banned_selector(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            (repo / ".rig").mkdir()
            (repo / ".rig" / "routing.json").write_text(json.dumps({
                "schema_version": 1,
                "profiles": {"grok-4.6-high": {"aliases": ["anthropic/claude-opus-5"]}},
            }))
            with self.assertRaises(policy.ConfigError):
                policy.load_config(repo, policy_mode="smart")
            (repo / ".rig" / "routing.json").write_text(json.dumps({
                "schema_version": 1,
                "profiles": {"grok-4.6-high": {"selector": "gpt-5.6-sol"}},
            }))
            with self.assertRaises(policy.ConfigError):
                policy.load_config(repo, policy_mode="smart")
            (repo / ".rig" / "routing.json").write_text(json.dumps({
                "schema_version": 1,
                "profiles": {"codex-explorer-low": {"roles": ["explore", "implement"]}},
            }))
            with self.assertRaises(policy.ConfigError) as ctx:
                policy.load_config(repo, policy_mode="smart")
            self.assertIn("cannot write", str(ctx.exception))

    def test_unknown_field_and_effort_supported(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            (repo / ".rig").mkdir()
            (repo / ".rig" / "routing.json").write_text(json.dumps({
                "schema_version": 1,
                "profiles": {"grok-4.6-high": {"enabled": True}},
            }))
            with self.assertRaises(policy.ConfigError):
                policy.load_config(repo, policy_mode="smart")
            (repo / ".rig" / "routing.json").write_text(json.dumps({
                "schema_version": 1,
                "profiles": {"grok-4.6-high": {"effort": "max"}},
            }))
            with self.assertRaises(policy.ConfigError):
                policy.load_config(repo, policy_mode="smart")


class CatalogStale(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.cache = Path(self.td.name) / "model-catalogs.json"
        self._old = {k: os.environ.get(k) for k in ("RIG_SKIP_MODEL_CATALOG", "RIG_REFRESH_MODELS", "RIG_MODEL_CATALOG_CACHE")}
        os.environ.pop("RIG_SKIP_MODEL_CATALOG", None)
        os.environ.pop("RIG_REFRESH_MODELS", None)
        os.environ["RIG_MODEL_CATALOG_CACHE"] = str(self.cache)

    def tearDown(self):
        for key, val in self._old.items():
            if val is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = val
        self.td.cleanup()

    def _put(self, worker, ids, age, status="ok"):
        catalog.cache_put(worker, ids, status=status)
        data = json.loads(self.cache.read_text())
        data[worker]["fetched_at"] = time.time() - age
        self.cache.write_text(json.dumps(data))

    def test_stale_within_24h_ok_even_if_refresh_failed(self):
        self._put("opencode", ["openai/gpt-5.6-luna"], catalog.TTL_SECONDS + 10)
        with patch.object(catalog, "probe_catalog", return_value=("unavailable", None)):
            info = catalog.load_catalog_info("opencode", require_fresh=True)
        self.assertEqual(info["state"], "stale")
        self.assertTrue(info["refresh_failed"])
        self.assertEqual(info["ids"], ["openai/gpt-5.6-luna"])
        choice = smart_pick(
            "", ["opencode"], "implement", "add a header",
            catalogs={"opencode": ["openai/gpt-5.6-luna"]},
        )
        self.assertEqual(choice["model"], "openai/gpt-5.6-luna")

    def test_invalid_cache_timestamp_or_status_requires_confirmation(self):
        for stamp, status in ((float("nan"), "ok"), (float("inf"), "ok"),
                              (time.time() + 10000, "ok"), (time.time() - 10, "failure")):
            self.cache.write_text(json.dumps({"opencode": {
                "ids": ["openai/gpt-5.6-luna"], "fetched_at": stamp, "status": status}}))
            with patch.object(catalog, "probe_catalog", return_value=("unavailable", None)):
                self.assertEqual(catalog.load_catalog_info("opencode")["state"], "unavailable")

    def test_older_than_24h_must_probe_else_reject(self):
        self._put("opencode", ["openai/gpt-5.6-luna"], catalog.STALE_MAX_SECONDS + 10)
        with patch.object(catalog, "probe_catalog", return_value=("unavailable", None)):
            info = catalog.load_catalog_info("opencode")
        self.assertEqual(info["state"], "unavailable")
        self.assertTrue(info["refresh_failed"])

    def test_empty_is_not_unavailable(self):
        with patch.object(catalog, "probe_catalog", return_value=("empty", [])):
            info = catalog.load_catalog_info("omp")
        self.assertEqual(info["state"], "empty")
        self.assertTrue(info["confirmed_empty"])
        self.assertEqual(info["ids"], [])
        empty = smart_pick("", ["opencode"], "implement", "add a header", catalogs={"opencode": []})
        codes = {row["code"] for row in empty["routing"]["candidate_decisions"] if row["id"].startswith("opencode-")}
        self.assertIn("catalog-empty", codes)
        missing = smart_pick("", ["opencode"], "implement", "add a header", catalogs={"opencode": None})
        codes = {row["code"] for row in missing["routing"]["candidate_decisions"] if row["id"].startswith("opencode-")}
        self.assertIn("catalog-unavailable", codes)

    def test_smart_refresh_uses_probe_catalog(self):
        self._put("agy", ["gemini-3.8-flash-high"], catalog.TTL_SECONDS + 5)
        called = []

        def fake_catalog(worker, timeout=8.0):
            called.append(worker)
            return "empty", []

        with patch.object(catalog, "probe_catalog", side_effect=fake_catalog), patch.object(catalog, "probe_worker", side_effect=AssertionError("legacy probe")):
            info = catalog.load_catalog_info("agy")
            self.assertEqual(info["state"], "stale")
            deadline = time.time() + 2
            while not called and time.time() < deadline:
                time.sleep(0.01)
        self.assertEqual(called, ["agy"])
        deadline = time.time() + 2
        while True:
            entry = catalog.cache_entry("agy")
            if entry and entry.get("status") == "empty":
                break
            if time.time() > deadline:
                self.fail("smart refresh did not persist empty catalog")
            time.sleep(0.01)
        self.assertEqual(entry["ids"], [])


def _opt_in(repo: Path, enabled=True, version=2, extra=None):
    (repo / ".rig").mkdir(parents=True, exist_ok=True)
    payload = {"schema_version": version}
    if extra:
        payload.update(extra)
    if version >= 2:
        payload["execution"] = {"direct_parent_low_risk": enabled}
    (repo / ".rig" / "routing.json").write_text(json.dumps(payload))
    return repo


class SchemaAndDirectParent(unittest.TestCase):
    def test_schema_v1_and_v2_false_share_fingerprint(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            empty = policy.config_fingerprint(policy.load_config(repo, policy_mode="smart"))
            _opt_in(repo, version=1, extra={"profiles": {}, "preferences": {}})
            v1 = policy.load_config(repo, policy_mode="smart")
            self.assertFalse(v1.direct_parent_low_risk)
            _opt_in(repo, enabled=False)
            v2 = policy.load_config(repo, policy_mode="smart")
            self.assertFalse(v2.direct_parent_low_risk)
            self.assertEqual(empty, policy.config_fingerprint(v1))
            self.assertEqual(policy.config_fingerprint(v1), policy.config_fingerprint(v2))

    def test_schema_v2_opt_in_changes_fingerprint(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            _opt_in(repo, enabled=False)
            off = policy.config_fingerprint(policy.load_config(repo, policy_mode="smart"))
            _opt_in(repo, enabled=True)
            on = policy.load_config(repo, policy_mode="smart")
            self.assertTrue(on.direct_parent_low_risk)
            self.assertNotEqual(off, policy.config_fingerprint(on))

    def test_malformed_execution_rejected_in_smart_legacy_falls_back(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            (repo / ".rig").mkdir()
            for bad in (
                {"schema_version": 2, "execution": True},
                {"schema_version": 2, "execution": {"direct_parent_low_risk": "true"}},
                {"schema_version": 2, "execution": {"direct_parent_low_risk": 1}},
                {"schema_version": 2, "execution": {"direct_parent_low_risk": True, "extra": False}},
                {"schema_version": 2, "execution": []},
                {"schema_version": 1, "execution": {"direct_parent_low_risk": True}},
                {"schema_version": 3},
            ):
                (repo / ".rig" / "routing.json").write_text(json.dumps(bad))
                with self.assertRaises(policy.ConfigError):
                    policy.load_config(repo, policy_mode="smart")
                legacy = policy.load_config(repo, policy_mode="legacy")
                self.assertEqual(legacy.source, "builtin")
                self.assertFalse(legacy.direct_parent_low_risk)

    def test_direct_parent_before_catalog_for_low_mini_implement(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = _opt_in(Path(temp), enabled=True)
            with patch.object(catalog, "load_catalog_info", side_effect=AssertionError("catalog lookup")):
                for role in ("mini", "implement"):
                    choice = smart_pick(
                        "codex", ["grok", "opencode"], role, "tiny label",
                        repo=repo, complexity="low", risk="low", uncertainty="low",
                    )
                    self.assertEqual(choice["spawn"], "native")
                    self.assertTrue(choice["parent_writes"])
                    self.assertEqual(choice["executor_kind"], "parent")
                    self.assertEqual(choice["execution_strategy"], "direct-parent")
                    self.assertEqual(choice["routing"]["execution_strategy"], "direct-parent")
                    self.assertEqual(choice["routing"]["catalog"]["source"], "none")
                    self.assertIsNone(choice["routing"]["selected_profile"])
                    self.assertEqual(choice["worker"], "codex")

    def test_direct_parent_not_used_without_opt_in_or_other_roles(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            low = dict(complexity="low", risk="low", uncertainty="low", repo=repo)
            choice = smart_pick("codex", ["grok"], "implement", "add a header", **low)
            self.assertEqual(choice["spawn"], "run-worker")
            self.assertEqual(choice["routing"]["execution_strategy"], "wrapper")
            _opt_in(repo, enabled=True)
            bulk = smart_pick("codex", ["grok"], "bulk", "many files", **low)
            self.assertEqual(bulk["spawn"], "run-worker")
            self.assertEqual(bulk["routing"]["execution_strategy"], "wrapper")
            hard = smart_pick(
                "codex", ["grok", "claude"], "hard", "risky",
                repo=repo, complexity="low", risk="low", uncertainty="low",
            )
            self.assertEqual(hard["spawn"], "run-worker")
            medium = smart_pick(
                "codex", ["grok"], "implement", "add a header",
                repo=repo, complexity="low", risk="medium", uncertainty="low",
            )
            self.assertEqual(medium["spawn"], "run-worker")
            blocked = smart_pick(
                "codex", ["grok"], "implement", "tiny",
                repo=repo, complexity="low", risk="low", uncertainty="low", exclude="codex",
            )
            self.assertEqual(blocked["spawn"], "run-worker")
            legacy = route.pick(
                "codex", ["grok"], "implement", "tiny",
                repo=repo, complexity="low", risk="low", uncertainty="low", policy_mode="legacy",
            )
            self.assertEqual(legacy["spawn"], "run-worker")
            self.assertEqual(legacy["routing"]["execution_strategy"], "wrapper")

    def test_parent_fallback_strategy_distinct_from_direct(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = _opt_in(Path(temp), enabled=True)
            fallback = smart_pick("codex", [], "implement", "add a header", repo=repo)
            self.assertEqual(fallback["routing"]["execution_strategy"], "parent-fallback")
            self.assertTrue(fallback["parent_writes"])
            none = smart_pick("", [], "implement", "add a header", repo=repo)
            self.assertEqual(none["spawn"], "none")
            self.assertEqual(none["routing"]["execution_strategy"], "none")


if __name__ == "__main__":
    unittest.main()
