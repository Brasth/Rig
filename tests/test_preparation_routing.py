#!/usr/bin/env python3
"""Opt-in preparation-aware effort: floors, same-model effort, exclusions and launch binding."""
import copy
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import harness  # noqa: E402
import preparation_routing  # noqa: E402
import route  # noqa: E402
import routing_config  # noqa: E402
import routing_policy as policy  # noqa: E402
import task_preparation  # noqa: E402

READY = {"task": "Fix saving", "files": ["app.py"], "checks": [{"id": "t", "argv": ["python3", "-m", "unittest"]}],
         "changes": [{"path": "app.py", "change": "Write the file"}],
         "reading_order": [{"path": "app.py", "symbol": "save", "reason": "entrypoint"}],
         "decisions": [], "unknowns": []}
SUPPORTED = {"supported_efforts": ["low", "medium", "high"]}


class PreparationRouting(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name).resolve()
        (self.repo / ".git").mkdir()
        (self.repo / ".rig").mkdir()
        (self.repo / "app.py").write_text("def save():\n    pass\n")
        self.enterContext(patch.dict(os.environ, {"RIG_JOB_ID": "", "RIG_JOB_DIR": "", "RIG_SKIP_MODEL_CATALOG": "1"}))
        self.harness("true")
        (self.repo / ".rig/routing.json").write_text(json.dumps({"schema_version": 1, "profiles": {
            "claude-sonnet-5-medium": SUPPORTED, "claude-opus-5-high": SUPPORTED, "claude-haiku-4-5-low": SUPPORTED}}))

    def harness(self, value):
        line = "" if value is None else f"preparation_aware_effort = {value}\n"
        (self.repo / ".rig/harness.toml").write_text("[project]\nenabled=true\n[routing]\nmode = \"smart\"\n" + line)

    def prepared(self, **delta):
        return task_preparation.prepare(self.repo, {**READY, **delta})["preparation"]

    def pick(self, role="implement", preparation=None, assessment=None, effective=("claude",)):
        assessment = assessment or {"complexity": "medium", "risk": "medium", "uncertainty": "low"}
        return route.pick("codex", list(effective), role, "Fix saving", repo=self.repo, catalogs={},
                          assessment=assessment, preparation=preparation)

    def test_flag_is_strict_and_default_off_and_fingerprinted(self):
        self.harness(None)
        off = policy.load_config(self.repo)
        self.assertFalse(off.preparation_aware_effort)
        self.harness("false")
        self.assertFalse(policy.load_config(self.repo).preparation_aware_effort)
        self.assertEqual(policy.config_fingerprint(policy.load_config(self.repo)), policy.config_fingerprint(off))
        self.harness("true")
        on = policy.load_config(self.repo)
        self.assertTrue(on.preparation_aware_effort)
        self.assertNotEqual(policy.config_fingerprint(on), policy.config_fingerprint(off))
        for raw in ("yes", "1", "True", "\"true\""):
            self.harness(raw)
            with self.subTest(raw=raw), self.assertRaises(routing_config.ConfigError):
                policy.load_config(self.repo)
        self.assertEqual(routing_config.POLICY_VERSION, 3)
        legacy = routing_config.load_config(self.repo, policy_mode="legacy")
        self.assertFalse(legacy.preparation_aware_effort)

    def test_plain_pick_unchanged_without_preparation_when_off(self):
        self.harness(None)
        choice = self.pick()
        self.assertNotIn("effort", choice["routing"])
        self.assertNotIn("preparation", choice["routing"])
        self.assertEqual(choice["effort"], "medium")

    def test_low_keeps_model_and_capability_floor(self):
        baseline = self.pick()
        low = self.pick(preparation=self.prepared(remaining_work="low"))
        self.assertEqual(low["model"], baseline["model"])
        self.assertEqual(low["routing"]["required_tier"], baseline["routing"]["required_tier"])
        self.assertEqual(low["routing"]["selected_profile"]["id"], baseline["routing"]["selected_profile"]["id"])
        self.assertEqual(low["effort"], "low")
        record = low["routing"]["effort"]
        self.assertEqual((record["baseline"], record["requested"], record["effective"], record["reason"]),
                         ("medium", "low", "low", "adjusted"))
        self.assertEqual(low["routing"]["selected_profile"]["effort"], "low")
        self.assertNotIn("Fix saving", json.dumps(low["routing"]["preparation"]))

    def test_medium_and_high_raise_floor_never_lower(self):
        cheap = {"complexity": "low", "risk": "low", "uncertainty": "low"}
        self.assertEqual(self.pick(assessment=cheap)["routing"]["required_tier"], "fast")
        medium = self.pick(assessment=cheap, preparation=self.prepared(remaining_work="medium"))
        self.assertEqual(medium["routing"]["required_tier"], "standard")
        self.assertEqual(medium["effort"], "medium")
        high = self.pick(assessment=cheap, preparation=self.prepared(remaining_work="high"))
        self.assertEqual(high["routing"]["required_tier"], "strong")
        self.assertEqual(high["routing"]["selected_profile"]["id"], "claude-opus-5-high")
        strong = self.pick(assessment={"complexity": "high", "risk": "medium", "uncertainty": "low"},
                           preparation=self.prepared(remaining_work="low"))
        self.assertEqual(strong["routing"]["required_tier"], "strong")
        self.assertEqual(strong["effort"], "low")

    def test_incomplete_unsupported_excluded_and_opt_out_keep_baseline(self):
        incomplete = self.pick(preparation=self.prepared(remaining_work="low", unknowns=["Which encoding?"]))
        self.assertEqual((incomplete["effort"], incomplete["routing"]["effort"]["reason"]), ("medium", "preparation-incomplete"))
        self.assertIn("unknowns-open", incomplete["routing"]["preparation"]["gap_codes"])
        unknown = self.pick(preparation=self.prepared())
        self.assertEqual(unknown["routing"]["effort"]["reason"], "remaining-work-unknown")
        unsupported = self.pick(preparation=self.prepared(remaining_work="low"), effective=("grok",))
        self.assertEqual(unsupported["routing"]["effort"]["reason"], "effort-unsupported")
        self.assertEqual(unsupported["effort"], unsupported["routing"]["effort"]["baseline"])
        risky = self.pick(preparation=self.prepared(remaining_work="low"),
                          assessment={"complexity": "medium", "risk": "high", "uncertainty": "low"})
        self.assertEqual(risky["routing"]["effort"]["reason"], "risk-high")
        for role in ("hard", "verify", "explore"):
            choice = self.pick(role=role, preparation=self.prepared(remaining_work="low"))
            with self.subTest(role=role):
                self.assertEqual(choice["routing"]["effort"]["reason"], "role-excluded")
                self.assertEqual(choice["effort"], choice["routing"]["effort"]["baseline"])
        self.harness("false")
        off = self.pick(preparation=self.prepared(remaining_work="low"))
        self.assertEqual((off["effort"], off["routing"]["effort"]["reason"]), ("medium", "pilot-off"))

    def test_high_default_to_medium_needs_only_normal_readiness(self):
        strong = {"complexity": "high", "risk": "medium", "uncertainty": "medium"}
        unknowns = self.prepared(remaining_work="medium", unknowns=["Which encoding?"])
        self.assertFalse(unknowns["readiness"]["execution_ready"])
        medium = self.pick(assessment=strong, preparation=unknowns)
        self.assertEqual(medium["routing"]["required_tier"], "strong")
        self.assertEqual(medium["routing"]["selected_profile"]["id"], "claude-opus-5-high")
        record = medium["routing"]["effort"]
        self.assertEqual((record["baseline"], record["effective"], record["reason"]), ("high", "medium", "adjusted"))
        low = self.pick(assessment=strong, preparation=self.prepared(remaining_work="low", unknowns=["Which encoding?"]))
        self.assertEqual((low["effort"], low["routing"]["effort"]["reason"]), ("high", "preparation-incomplete"))
        risky = self.pick(assessment={**strong, "risk": "high"}, preparation=unknowns)
        self.assertEqual((risky["effort"], risky["routing"]["effort"]["reason"]), ("high", "risk-high"))
        hard = self.pick(role="hard", assessment=strong, preparation=unknowns)
        self.assertEqual((hard["effort"], hard["routing"]["effort"]["reason"]), ("high", "role-excluded"))

        class Cfg:
            preparation_aware_effort, mode = True, "smart"

        class Profile:
            effort, supported_efforts = "high", ("low", "medium", "high")
        unready = {"remaining_work": "medium", "ready": False, "execution_ready": False}
        self.assertEqual(preparation_routing.resolve(Cfg, "implement", {"risk": "low"}, unready, Profile, "wrapper")["reason"],
                         "preparation-incomplete")

    def test_parent_and_unknown_baseline_efforts_are_not_adjusted(self):
        class Cfg:
            preparation_aware_effort, mode = True, "smart"
        prep = {"remaining_work": "low", "execution_ready": True}
        assessed = {"risk": "low"}
        parent = preparation_routing.resolve(Cfg, "implement", assessed, prep, None, "parent-fallback")
        self.assertEqual(parent["reason"], "not-delegated")

        class Profile:
            effort, supported_efforts = "max", ("low", "max")
        self.assertEqual(preparation_routing.resolve(Cfg, "implement", assessed, prep, Profile, "wrapper")["reason"],
                         "baseline-effort-not-comparable")
        Profile.effort, Profile.supported_efforts = "", ("", "low")
        self.assertEqual(preparation_routing.resolve(Cfg, "implement", assessed, prep, Profile, "wrapper")["effective"], "")

    def test_launch_tuple_recomputes_effort_and_rejects_tampering(self):
        prep = self.prepared(remaining_work="low")
        choice = self.pick(preparation=prep)
        import preparation_binding
        _draft, summary = preparation_binding.inspect(self.repo, prep)

        def validate(routing, effort="low", preparation=summary):
            return policy.validate_launch_tuple(self.repo, worker="claude", model=choice["model"], effort=effort,
                                                role="implement", routing=routing, case="Fix saving",
                                                access="write", catalogs={}, preparation=preparation)
        accepted = validate(choice["routing"])
        self.assertEqual(accepted["effort"]["effective"], "low")
        for effort in ("medium", "high", "minimal", ""):
            with self.subTest(effort=effort), self.assertRaises(ValueError):
                validate(choice["routing"], effort=effort)
        with self.assertRaisesRegex(ValueError, "same preparation"):
            validate(choice["routing"], preparation=None)
        forged = copy.deepcopy(choice["routing"])
        forged["preparation"]["fingerprint"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "differs"):
            validate(forged)
        no_record = copy.deepcopy(choice["routing"])
        no_record.pop("preparation")
        no_record["effort"]["effective"] = "low"
        with self.assertRaises(ValueError):
            validate(no_record, preparation=None)
        stale_version = copy.deepcopy(choice["routing"])
        stale_version["policy_version"] = 2
        with self.assertRaisesRegex(ValueError, "version"):
            validate(stale_version)
        incomplete = self.prepared(remaining_work="low", unknowns=["x"])
        _draft, weak = preparation_binding.inspect(self.repo, incomplete)
        forged = copy.deepcopy(choice["routing"])
        forged["preparation"]["fingerprint"] = weak["fingerprint"]
        with self.assertRaises(ValueError):
            validate(forged, preparation=weak)
        self.harness("false")
        with self.assertRaises(ValueError):
            validate(choice["routing"])

    def test_stale_or_tampered_preparation_fails_pick(self):
        prep = self.prepared(remaining_work="low")
        tampered = copy.deepcopy(prep)
        tampered["handoff"]["remaining_work"] = "high"
        with self.assertRaisesRegex(ValueError, "fingerprint"):
            self.pick(preparation=tampered)
        (self.repo / "app.py").write_text("changed\n")
        with self.assertRaisesRegex(ValueError, "stale"):
            self.pick(preparation=prep)

    def test_sidecar_reader_rejects_malformed_preparation_evidence(self):
        choice = self.pick(preparation=self.prepared(remaining_work="low"))
        self.assertTrue(preparation_routing.evidence_ok(choice["routing"]))
        for mutate in (lambda r: r["effort"].update(reason="made-up"), lambda r: r["preparation"].update(task="x"),
                       lambda r: r["effort"].update(pilot="yes"), lambda r: r["preparation"].update(gap_codes="x")):
            routing = copy.deepcopy(choice["routing"])
            mutate(routing)
            self.assertFalse(preparation_routing.evidence_ok(routing))
        text = "\n".join(policy.explain_lines(choice))
        self.assertIn("effort pilot=on baseline=medium requested=low effective=low", text)

    def test_cli_pick_parity(self):
        import subprocess
        result = task_preparation.prepare(self.repo, {**READY, "remaining_work": "low"})
        path = self.repo / "prep.json"
        path.write_text(json.dumps(result))
        cli = subprocess.run([sys.executable, str(ROOT / "scripts/route.py"), "pick", "--live", "codex",
                              "--effective", "claude", "--role", "implement", "--case", "Fix saving",
                              "--complexity", "medium", "--risk", "medium", "--uncertainty", "low",
                              "--preparation", str(path), "--repo", str(self.repo), "--json"],
                             capture_output=True, text=True, env={**os.environ, "RIG_SKIP_MODEL_CATALOG": "1"})
        self.assertEqual(cli.returncode, 0, cli.stderr)
        self.assertEqual(json.loads(cli.stdout)["routing"]["effort"]["effective"], "low")


if __name__ == "__main__":
    unittest.main()
