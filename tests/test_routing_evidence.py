#!/usr/bin/env python3
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import routing_evidence as evidence  # noqa: E402
import routing_policy as policy  # noqa: E402
import route  # noqa: E402


def _harness(repo: Path, mode: str = "smart") -> None:
    (repo / ".rig").mkdir(parents=True, exist_ok=True)
    (repo / ".git").mkdir(exist_ok=True)
    (repo / ".rig" / "harness.toml").write_text(
        'parent = "codex"\n[workers]\ngrok = true\nclaude = true\ncodex = true\n'
        "cursor = false\nopencode = true\nomp = true\npi = true\nagy = true\n"
        f"[routing]\nmode = \"{mode}\"\n"
    )


class SidecarRoundtrip(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.job = Path(self.td.name) / "job"

    def tearDown(self):
        self.td.cleanup()

    def test_write_requires_nonempty_attempt_and_schema_1(self):
        routing = evidence.manual_routing(worker="grok", model="grok-4.6", effort="high")
        with self.assertRaises(ValueError):
            evidence.write_sidecar(self.job, "", routing)
        payload = evidence.write_sidecar(self.job, "att-1", routing)
        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(payload["attempt_id"], "att-1")
        got = evidence.read_sidecar(self.job, expected_attempt_id="att-1")
        self.assertEqual(got["attempt_id"], "att-1")
        self.assertEqual(got["routing"]["policy_mode"], "manual")

    def test_read_rejects_malformed_wrong_and_missing_attempt(self):
        routing = evidence.manual_routing(worker="grok", model="grok-4.6", effort="high")
        evidence.write_sidecar(self.job, "att-1", routing)
        self.assertIsNone(evidence.read_sidecar(self.job, expected_attempt_id="att-2"))
        self.assertIsNone(evidence.read_sidecar(self.job, expected_attempt_id=""))
        path = evidence.sidecar_path(self.job)
        path.write_text("{not json")
        self.assertIsNone(evidence.read_sidecar(self.job))

        path.write_text(json.dumps({"schema_version": 2, "attempt_id": "att-1", "routing": {}}))
        self.assertIsNone(evidence.read_sidecar(self.job))
        path.write_text(json.dumps({"schema_version": 1, "attempt_id": "", "routing": {}}))
        self.assertIsNone(evidence.read_sidecar(self.job))
        path.write_text(json.dumps({"schema_version": 1, "attempt_id": "att-1", "routing": "nope"}))
        self.assertIsNone(evidence.read_sidecar(self.job))

    def test_nested_malformed_evidence_is_ignored(self):
        for bad in ({"selected_profile": []}, {"selected_profile": {"id": []}},
                    {"policy_version": {}}, {"required_tier": []}, {"policy_mode": True}):
            evidence.write_sidecar(self.job, "att-1", bad)
            self.assertIsNone(evidence.read_sidecar(self.job, expected_attempt_id="att-1"))


class LaunchTupleValidation(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.repo = Path(self.td.name)
        _harness(self.repo)
        self.cfg = policy.load_config(self.repo)
        self.fp = policy.config_fingerprint(self.cfg)
        os.environ.pop("RIG_JOB_ID", None)
        os.environ.pop("RIG_JOB_DIR", None)
        os.environ.pop("RIG_LIVE", None)

    def tearDown(self):
        self.td.cleanup()

    def _smart(self, **kwargs):
        kwargs.setdefault("catalogs", {})
        kwargs.setdefault("policy_mode", "smart")
        return route.pick("codex", ["grok", "claude"], "implement", "add a header", repo=self.repo, **kwargs)

    def test_empty_and_manual_are_sanitized(self):
        got = policy.validate_launch_tuple(
            self.repo, worker="grok", model="grok-4.6", effort="high", role="implement", routing=None,
        )
        self.assertEqual(got["policy_mode"], "manual")
        self.assertFalse(got["assessment"]["supplied"])
        dirty = {
            "policy_mode": "manual",
            "selected_profile": {"id": "grok-4.6-high", "provider": "invented"},
            "assessment": {"complexity": "high", "extra": "nope"},
        }
        clean = policy.validate_launch_tuple(
            self.repo, worker="grok", model="grok-4.6", effort="high", role="implement", routing=dirty,
        )
        self.assertEqual(clean["policy_mode"], "manual")
        self.assertEqual(clean["assessment"]["complexity"], "")
        self.assertNotEqual((clean.get("selected_profile") or {}).get("id"), "grok-4.6-high")

    def test_legacy_caller_claims_are_sanitized(self):
        dirty = {
            "policy_mode": "legacy",
            "selected_profile": {"id": "grok-4.6-high", "provider": "xai"},
            "assessment": {"complexity": "high", "risk": "high", "uncertainty": "high"},
        }
        got = policy.validate_launch_tuple(
            self.repo, worker="grok", model="grok-4.6", effort="high", role="implement", routing=dirty,
        )
        self.assertEqual(got["policy_mode"], "legacy")
        self.assertEqual(got["assessment"]["complexity"], "")
        self.assertEqual((got.get("selected_profile") or {}).get("id"), "")

    def test_smart_rebuilds_profile_and_rejects_tampering(self):
        choice = self._smart(complexity="low", risk="low", uncertainty="low")
        routing = choice["routing"]
        ok = policy.validate_launch_tuple(
            self.repo, worker=choice["worker"], model=choice["model"], effort=choice["effort"],
            role="implement", routing=routing, executor_kind="wrapper", catalogs={},
        )
        self.assertEqual(ok["policy_mode"], "smart")
        self.assertEqual(ok["selected_profile"]["id"], "grok-4.5-low")
        self.assertEqual(ok["selected_profile"]["provider"], "xai")
        tampered = dict(routing)
        tampered["selected_profile"] = {**routing["selected_profile"], "id": "claude-opus-5-high", "provider": "forged"}
        with self.assertRaisesRegex(ValueError, "worker does not match|profile is not"):
            policy.validate_launch_tuple(
                self.repo, worker="grok", model="grok-4.5", effort="low", role="implement",
                routing=tampered, executor_kind="wrapper", catalogs={},
            )
        bad_model = dict(routing)
        with self.assertRaisesRegex(ValueError, "model does not match"):
            policy.validate_launch_tuple(
                self.repo, worker="grok", model="grok-4.6", effort="low", role="implement",
                routing=routing, executor_kind="wrapper", catalogs={},
            )
        del bad_model

    def test_assessment_conflict_and_insufficient_tier(self):
        choice = self._smart(complexity="low", risk="low", uncertainty="low")
        routing = choice["routing"]
        with self.assertRaisesRegex(ValueError, "assessment conflicts"):
            policy.validate_launch_tuple(
                self.repo, worker=choice["worker"], model=choice["model"], effort=choice["effort"],
                role="implement", routing=routing, assessment={"complexity": "high", "risk": "high", "uncertainty": "high"},
                executor_kind="wrapper", catalogs={},
            )
        strong = self._smart(complexity="high", risk="high", uncertainty="high")
        weak = dict(strong["routing"])
        weak["selected_profile"] = dict(choice["routing"]["selected_profile"])
        with self.assertRaisesRegex(ValueError, "insufficient|inconsistent"):
            policy.validate_launch_tuple(
                self.repo, worker="grok", model="grok-4.5", effort="low", role="implement",
                routing=weak, executor_kind="wrapper", catalogs={},
            )

    def test_stale_fingerprint_and_mode_change(self):
        choice = self._smart()
        routing = dict(choice["routing"])
        routing["config_fingerprint"] = "deadbeef"
        with self.assertRaisesRegex(ValueError, "fingerprint"):
            policy.validate_launch_tuple(
                self.repo, worker=choice["worker"], model=choice["model"], effort=choice["effort"],
                role="implement", routing=routing, executor_kind="wrapper", catalogs={},
            )
        _harness(self.repo, mode="legacy")
        with self.assertRaisesRegex(ValueError, "policy changed|fingerprint"):
            policy.validate_launch_tuple(
                self.repo, worker=choice["worker"], model=choice["model"], effort=choice["effort"],
                role="implement", routing=choice["routing"], executor_kind="wrapper", catalogs={},
            )

    def test_parent_unknown_fallback_and_native_child_rejected(self):
        choice = route.pick(
            "codex", [], "implement", "add a header", repo=self.repo, policy_mode="smart", catalogs={},
        )
        self.assertIsNone((choice["routing"] or {}).get("selected_profile"))
        ok = policy.validate_launch_tuple(
            self.repo, worker="codex", model="", effort="", role="implement",
            routing=choice["routing"], executor_kind="parent", catalogs={},
        )
        self.assertIsNone(ok["selected_profile"])
        self.assertEqual(ok["policy_mode"], "smart")
        with self.assertRaisesRegex(ValueError, "executor_kind=parent"):
            policy.validate_launch_tuple(
                self.repo, worker="codex", model="", effort="", role="implement",
                routing=choice["routing"], executor_kind="native_child", catalogs={},
            )
        with self.assertRaisesRegex(ValueError, "executor_kind=parent"):
            policy.validate_launch_tuple(
                self.repo, worker="codex", model="", effort="", role="implement",
                routing=choice["routing"], executor_kind="wrapper", catalogs={},
            )

    def test_catalog_miss_does_not_substitute(self):
        choice = route.pick(
            "codex", ["opencode"], "implement", "add a header", repo=self.repo,
            policy_mode="smart", catalogs={"opencode": ["openai/gpt-5.6-luna"]},
        )
        self.assertEqual(choice["worker"], "opencode")
        with self.assertRaisesRegex(ValueError, "catalog-miss"):
            policy.validate_launch_tuple(
                self.repo, worker=choice["worker"], model=choice["model"], effort=choice["effort"],
                role="implement", routing=choice["routing"], executor_kind="wrapper",
                catalogs={"opencode": ["openai/gpt-5.6-luna-preview"]},
            )
        ok = policy.validate_launch_tuple(
            self.repo, worker=choice["worker"], model=choice["model"], effort=choice["effort"],
            role="implement", routing=choice["routing"], executor_kind="wrapper",
            catalogs={"opencode": ["openai/gpt-5.6-luna"]},
        )
        self.assertEqual(ok["selected_profile"]["model"], "openai/gpt-5.6-luna")

    def test_smart_policy_version_must_be_current_integer(self):
        choice = self._smart(complexity="low", risk="low", uncertainty="low")
        routing = dict(choice["routing"])
        self.assertEqual(routing["policy_version"], policy.POLICY_VERSION)
        for bad in (None, "", "1", True, False, 1.0, 0):
            with self.subTest(version=bad):
                tampered = dict(routing)
                if bad is None:
                    tampered.pop("policy_version", None)
                else:
                    tampered["policy_version"] = bad
                with self.assertRaisesRegex(ValueError, "policy version"):
                    policy.validate_launch_tuple(
                        self.repo, worker=choice["worker"], model=choice["model"], effort=choice["effort"],
                        role="implement", routing=tampered, executor_kind="wrapper", catalogs={},
                    )

    def test_high_risk_review_recommendation_cannot_be_caller_none(self):
        choice = self._smart(complexity="high", risk="high", uncertainty="high")
        routing = dict(choice["routing"])
        routing["review_recommendation"] = "none"
        ok = policy.validate_launch_tuple(
            self.repo, worker=choice["worker"], model=choice["model"], effort=choice["effort"],
            role="implement", routing=routing, executor_kind="wrapper", catalogs={},
        )
        self.assertEqual(ok["review_recommendation"], "independent")
        parent = route.pick(
            "codex", [], "implement", "add a header", repo=self.repo, policy_mode="smart",
            catalogs={}, complexity="high", risk="high", uncertainty="low",
        )
        parent_routing = dict(parent["routing"])
        parent_routing["review_recommendation"] = "none"
        parent_ok = policy.validate_launch_tuple(
            self.repo, worker="codex", model="", effort="", role="implement",
            routing=parent_routing, executor_kind="parent", catalogs={},
        )
        self.assertIsNone(parent_ok["selected_profile"])
        self.assertEqual(parent_ok["review_recommendation"], "independent")

    def test_smart_explore_review_require_read_access(self):
        explore = route.pick(
            "codex", ["grok", "claude"], "explore", "trace remaining gates",
            repo=self.repo, policy_mode="smart", catalogs={}, complexity="low", risk="low", uncertainty="low",
        )
        with self.assertRaisesRegex(ValueError, "access=read"):
            policy.validate_launch_tuple(
                self.repo, worker=explore["worker"], model=explore["model"], effort=explore["effort"],
                role="explore", routing=explore["routing"], executor_kind="wrapper", catalogs={},
                access="write",
            )
        ok = policy.validate_launch_tuple(
            self.repo, worker=explore["worker"], model=explore["model"], effort=explore["effort"],
            role="explore", routing=explore["routing"], executor_kind="wrapper", catalogs={},
            access="read",
        )
        self.assertEqual(ok["policy_mode"], "smart")
        explorer = route.pick(
            "grok", ["codex"], "explore", "trace remaining gates",
            repo=self.repo, policy_mode="smart", catalogs={}, complexity="low", risk="low", uncertainty="low",
        )
        self.assertEqual(explorer["routing"]["selected_profile"]["id"], "codex-explorer-low")
        with self.assertRaisesRegex(ValueError, "codex explorer cannot access write"):
            policy.validate_launch_tuple(
                self.repo, worker=explorer["worker"], model=explorer["model"], effort=explorer["effort"],
                role="explore", routing=explorer["routing"], executor_kind="wrapper", catalogs={},
                access="write",
            )
        review = route.pick(
            "codex", ["grok", "claude"], "review", "review the writer diff",
            repo=self.repo, policy_mode="smart", catalogs={},
        )
        with self.assertRaisesRegex(ValueError, "access=read"):
            policy.validate_launch_tuple(
                self.repo, worker=review["worker"], model=review["model"], effort=review["effort"],
                role="review", routing=review["routing"], executor_kind="wrapper", catalogs={},
                access="write",
            )

    def test_manual_legacy_write_access_unaffected(self):
        dirty = {
            "policy_mode": "manual",
            "policy_version": "1",
            "review_recommendation": "none",
            "selected_profile": {"id": "codex-explorer-low", "provider": "forged"},
        }
        manual = policy.validate_launch_tuple(
            self.repo, worker="codex", model="gpt-5.3-codex-mini", effort="low", role="explore",
            routing=dirty, access="write",
        )
        self.assertEqual(manual["policy_mode"], "manual")
        legacy = policy.validate_launch_tuple(
            self.repo, worker="grok", model="grok-4.6", effort="high", role="review",
            routing={"policy_mode": "legacy"}, access="write",
        )
        self.assertEqual(legacy["policy_mode"], "legacy")

    def test_selected_model_provider_rebuilt(self):
        choice = self._smart(complexity="low", risk="low", uncertainty="low")
        routing = dict(choice["routing"])
        forged = dict(routing["selected_profile"])
        forged["provider"] = "forged"
        forged["model"] = "invented-model"
        routing["selected_profile"] = forged
        ok = policy.validate_launch_tuple(
            self.repo, worker=choice["worker"], model=choice["model"], effort=choice["effort"],
            role="implement", routing=routing, executor_kind="wrapper", catalogs={},
        )
        self.assertEqual(ok["selected_profile"]["provider"], "xai")
        self.assertEqual(ok["selected_profile"]["model"], choice["model"])
        self.assertEqual(ok["selected_profile"]["id"], "grok-4.5-low")

    def test_direct_parent_tuple_revalidates_without_catalog(self):
        (self.repo / ".rig" / "routing.json").write_text(json.dumps({
            "schema_version": 2,
            "execution": {"direct_parent_low_risk": True},
        }))
        with patch.object(route, "resolved_model_for", side_effect=AssertionError("catalog")):
            choice = route.pick(
                "codex", ["grok", "opencode"], "implement", "tiny label",
                repo=self.repo, policy_mode="smart", catalogs={},
                complexity="low", risk="low", uncertainty="low",
            )
        self.assertEqual(choice["execution_strategy"], "direct-parent")
        ok = policy.validate_launch_tuple(
            self.repo, worker="codex", model="", effort="", role="implement",
            routing=choice["routing"], executor_kind="parent", catalogs={}, live="codex",
        )
        self.assertEqual(ok["execution_strategy"], "direct-parent")
        self.assertIsNone(ok["selected_profile"])
        self.assertEqual(ok["catalog"]["source"], "none")
        job_dir = self.repo / ".rig" / "jobs" / "direct"
        payload = evidence.write_sidecar(job_dir, "att-direct", ok)
        self.assertEqual(payload["routing"]["execution_strategy"], "direct-parent")
        with self.assertRaisesRegex(ValueError, "executor_kind=parent"):
            policy.validate_launch_tuple(
                self.repo, worker="codex", model="", effort="", role="implement",
                routing=choice["routing"], executor_kind="wrapper", catalogs={}, live="codex",
            )

    def test_direct_parent_rejects_live_parent_mismatch(self):
        (self.repo / ".rig" / "routing.json").write_text(json.dumps({
            "schema_version": 2,
            "execution": {"direct_parent_low_risk": True},
        }))
        with patch.object(route, "resolved_model_for", side_effect=AssertionError("catalog")):
            choice = route.pick(
                "codex", ["grok", "opencode"], "implement", "tiny label",
                repo=self.repo, policy_mode="smart", catalogs={},
                complexity="low", risk="low", uncertainty="low",
            )
        self.assertEqual(choice["execution_strategy"], "direct-parent")
        self.assertEqual(choice["worker"], "codex")
        with self.assertRaisesRegex(ValueError, "live parent mismatch"):
            policy.validate_launch_tuple(
                self.repo, worker="codex", model="", effort="", role="implement",
                routing=choice["routing"], executor_kind="parent", catalogs={}, live="grok",
            )
        ok = policy.validate_launch_tuple(
            self.repo, worker="parent", model="", effort="", role="implement",
            routing=choice["routing"], executor_kind="parent", catalogs={}, live="codex",
        )
        self.assertEqual(ok["execution_strategy"], "direct-parent")

    def test_sidecar_accepts_execution_strategy_string(self):
        job_dir = self.repo / ".rig" / "jobs" / "strategy"
        routing = evidence.empty_routing(
            mode="smart", fingerprint="x",
            assessment=policy.normalize_assessment("implement", {"complexity": "low", "risk": "low", "uncertainty": "low"}),
        )
        routing["execution_strategy"] = "wrapper"
        evidence.write_sidecar(job_dir, "att-1", routing)
        got = evidence.read_sidecar(job_dir, expected_attempt_id="att-1")
        self.assertEqual(got["routing"]["execution_strategy"], "wrapper")
        routing["execution_strategy"] = True
        evidence.write_sidecar(job_dir, "att-1", routing)
        self.assertIsNone(evidence.read_sidecar(job_dir, expected_attempt_id="att-1"))


if __name__ == "__main__":
    unittest.main()
