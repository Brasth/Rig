"""Domain settings are transactional and previews preserve real routing gates."""
import copy
import json
import math
import os
import stat
import sys
import tempfile
import threading
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import catalog
import harness
import route
import routing_config as config
import routing_evidence as evidence
import routing_policy as policy
import routing_settings as settings


class Project(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.repo = Path(self.temp.name)
        (self.repo / ".rig").mkdir()
        (self.repo / ".rig/harness.toml").write_text('[project]\nenabled=true\n[workers]\ngrok=true\nclaude=true\nopencode=true\n')
        self.path = self.repo / ".rig/routing.json"
        self.raw = {"schema_version": 4, "domains": {"frontend": {
            "preferred_profiles": ["claude-sonnet-5-medium", "grok-4.7-high"], "fallback": "none"}}}

    def tearDown(self):
        self.temp.cleanup()

    def write(self, raw=None):
        self.path.write_text(json.dumps(self.raw if raw is None else raw))
        return settings.open_settings(self.repo)

    def files(self):
        return {str(p.relative_to(self.repo)): p.read_bytes() for p in self.repo.rglob("*") if p.is_file()}


class CandidateValidation(Project):
    def test_pure_same_parser_same_fingerprint_no_mutation(self):
        document = self.write()
        before = copy.deepcopy(document.raw)
        expected = config.load_config(self.repo)
        with patch.object(Path, "read_text", side_effect=AssertionError("candidate read filesystem")), \
             patch.object(Path, "write_text", side_effect=AssertionError("candidate write filesystem")):
            got = config.validate_candidate(document.raw, harness=document.harness)
        self.assertEqual(config.config_fingerprint(got), config.config_fingerprint(expected))
        got.routing_json["schema_version"] = 1
        self.assertEqual(document.raw, before)

    def test_old_schema_preserves_unrelated_overrides(self):
        for version in (1, 2, 3, 4):
            raw = {"schema_version": version, "profiles": {"grok-4.5-low": {"capability": {"speed": 81}}},
                   "preferences": {"fast": ["grok-4.5-low"]}}
            if version >= 2:
                raw["execution"] = {"direct_parent_low_risk": True}
            if version >= 3:
                raw["picker"] = {"engine": "local", "local_policy": "ordered-v1", "objective": "cost"}
            self.assertEqual(settings.domain_candidate(raw, {}), raw)
            candidate = settings.domain_candidate(raw, self.raw["domains"])
            self.assertEqual(candidate["schema_version"], 4)
            for key in raw.keys() - {"schema_version"}:
                self.assertEqual(candidate[key], raw[key])
            config.validate_candidate(candidate)

    def test_invalid_candidates_rejected_without_disk_writes(self):
        self.write()
        before = self.files()
        invalid = [[], {"schema_version": 5}, {"schema_version": 4, "mystery": True}]
        for profiles, fallback in [(["unknown"], "scored"), (["grok-4.5-low"] * 2, "scored"), ([], "other")]:
            invalid.append({"schema_version": 4, "domains": {"frontend": {"preferred_profiles": profiles, "fallback": fallback}}})
        invalid += [{"schema_version": 4, "profiles": {"grok-4.5-low": {"selector": "gpt-6-astra"}}},
                    {"schema_version": 4, "domains": {"ui-design": {"fallback": "scored"}}}]
        for raw in invalid:
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                settings.save_settings(self.repo, raw, expected_fingerprint="ignored")
        self.assertEqual(self.files(), before)


class AtomicSave(Project):
    def test_save_reopen_repeat_preserves_mode_and_harness(self):
        document = self.write()
        self.path.chmod(0o640)
        original_harness = (self.repo / ".rig/harness.toml").read_bytes()
        candidate = settings.domain_candidate(document.raw, {"backend": {"preferred_profiles": [], "fallback": "parent"}})
        reply = settings.save_settings(self.repo, candidate, expected_fingerprint=document.expected_fingerprint)
        self.assertEqual(reply["status"], "saved")
        self.assertEqual(json.loads(self.path.read_text()), candidate)
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o640)
        self.assertEqual((self.repo / ".rig/harness.toml").read_bytes(), original_harness)
        self.assertEqual(settings.open_settings(self.repo).expected_fingerprint, reply["fingerprint"])
        self.assertEqual(settings.save_settings(self.repo, candidate, expected_fingerprint=reply["fingerprint"])["status"], "unchanged")

    def test_absent_noop_does_not_create_config_and_save_creates(self):
        document = settings.open_settings(self.repo)
        reply = settings.save_settings(self.repo, None, expected_fingerprint=document.expected_fingerprint)
        self.assertEqual(reply["status"], "unchanged")
        self.assertFalse(self.path.exists())
        settings.save_settings(self.repo, self.raw, expected_fingerprint=document.expected_fingerprint)
        self.assertTrue(self.path.is_file())

    def test_external_change_even_whitespace_rejected(self):
        document = self.write()
        self.path.write_bytes(self.path.read_bytes() + b"\n")
        before = self.path.read_bytes()
        with self.assertRaises(settings.StaleSettings):
            settings.save_settings(self.repo, self.raw, expected_fingerprint=document.expected_fingerprint)
        self.assertEqual(self.path.read_bytes(), before)

    def test_race_serializes_two_editors_one_wins(self):
        document = self.write()
        barrier = threading.Barrier(2)
        results = []
        def save(fallback):
            barrier.wait()
            try:
                candidate = settings.domain_candidate(self.raw, {"backend": {"fallback": fallback}})
                results.append(settings.save_settings(self.repo, candidate, expected_fingerprint=document.expected_fingerprint)["status"])
            except settings.StaleSettings:
                results.append("stale")
        threads = [threading.Thread(target=save, args=(v,)) for v in ("none", "parent")]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(3)
            self.assertFalse(thread.is_alive())
        self.assertCountEqual(results, ["saved", "stale"])

    def test_failure_or_mid_save_change_leaves_original_and_no_temp(self):
        document = self.write()
        candidate = settings.domain_candidate(self.raw, {"backend": {"fallback": "none"}})
        for operation in ("fsync", "replace"):
            before = self.path.read_bytes()
            with patch.object(os, operation, side_effect=OSError("fixture failure")), self.assertRaises(OSError):
                settings.save_settings(self.repo, candidate, expected_fingerprint=document.expected_fingerprint)
            self.assertEqual(self.path.read_bytes(), before)
            self.assertFalse(list(self.path.parent.glob(".routing-*.tmp")))
        real_fsync = os.fsync
        def changed(fd):
            real_fsync(fd)
            self.path.write_text('{"schema_version":1}\n')
        with patch.object(os, "fsync", side_effect=changed), self.assertRaises(settings.StaleSettings):
            settings.save_settings(self.repo, candidate, expected_fingerprint=document.expected_fingerprint)
        self.assertEqual(self.path.read_text(), '{"schema_version":1}\n')
        self.assertFalse(list(self.path.parent.glob(".routing-*.tmp")))

    def test_disabled_uninitialized_and_symlink_fail_closed(self):
        document = self.write()
        target = self.repo / "elsewhere.json"
        target.write_text(self.path.read_text())
        self.path.unlink()
        self.path.symlink_to(target)
        with self.assertRaisesRegex(ValueError, "symlink"):
            settings.save_settings(self.repo, self.raw, expected_fingerprint=document.expected_fingerprint)
        self.path.unlink()
        (self.repo / ".rig/harness.toml").write_text('[project]\nenabled=false\n')
        with self.assertRaisesRegex(ValueError, "disabled"):
            settings.open_settings(self.repo)
        with self.assertRaisesRegex(ValueError, "disabled"):
            settings.preview(self.repo, self.raw)
        (self.repo / ".rig/harness.toml").unlink()
        with self.assertRaisesRegex(ValueError, "uninitialized"):
            settings.open_settings(self.repo)

    def test_malformed_original_cannot_be_silently_replaced(self):
        for text in ('{}', '{"schema_version":4,"schema_version":1}', '[]', 'not json'):
            self.path.write_text(text)
            with self.assertRaises(ValueError):
                settings.open_settings(self.repo)
            self.assertEqual(self.path.read_text(), text)


class CatalogSnapshot(unittest.TestCase):
    def capture(self, entry, **env):
        with patch.object(catalog, "_read_cache_file", return_value={"opencode": entry}), \
             patch.object(catalog.time, "time", return_value=1000000), \
             patch.dict(os.environ, {"RIG_SKIP_MODEL_CATALOG": "0", **env}):
            return catalog.readonly_catalog_snapshot()

    def test_preserve_fresh_bounded_stale_expired_empty_missing_invalid(self):
        for age, state in [(0, "fresh"), (3600, "fresh"), (3601, "stale"), (86400, "stale"), (86401, "stale")]:
            snap = self.capture({"ids": ["openai/gpt-6-luna"], "fetched_at": 1000000-age, "status": "ok"})
            row = snap.info("opencode")
            self.assertEqual(row["state"], state)
            self.assertEqual(row["age_s"], age)
            self.assertEqual(row["source"], "cache")
            row["ids"].clear()
            self.assertTrue(snap.info("opencode")["ids"])
        for value in (None, {}, {"ids": [], "fetched_at": math.nan}, {"ids": [], "fetched_at": 1000001},
                      {"ids": ["x"], "fetched_at": 999999, "status": "failure"}):
            self.assertEqual(self.capture(value).info("opencode")["state"], "unavailable")
        empty = self.capture({"ids": [], "status": "empty", "fetched_at": 999999}).info("opencode")
        self.assertEqual(empty["state"], "empty")
        self.assertTrue(empty["confirmed_empty"])
        self.assertEqual(self.capture(None).info("mimo")["state"], "unavailable")
        self.assertEqual(self.capture(None, RIG_SKIP_MODEL_CATALOG="1").info("opencode")["state"], "skipped")

    def test_session_fresh_request_never_refreshes_or_upgrades(self):
        snapshot = self.capture({"ids": ["openai/gpt-6-luna"], "fetched_at": 900000})
        with patch.object(catalog, "load_catalog_info", side_effect=AssertionError("probe")), \
             patch.object(catalog, "_schedule_refresh", side_effect=AssertionError("background probe")):
            session = evidence.CatalogSession(snapshot=snapshot)
            self.assertEqual(session.info("opencode"), session.info("opencode", require_fresh=True))
            self.assertEqual(session.info("opencode")["age_s"], 100000)
        with self.assertRaises(ValueError):
            evidence.CatalogSession({}, snapshot=snapshot)


class Preview(Project):
    def setUp(self):
        super().setUp()
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(harness, "live_parent", return_value="codex"))
        self.stack.enter_context(patch.object(harness, "find_worker_bin", side_effect=lambda name: "/fake/" + name))
        self.stack.enter_context(patch.object(harness, "worker_mcp_reason", return_value=""))
        for target in ((catalog, "load_catalog_info"), (catalog, "probe_catalog"), (catalog, "_schedule_refresh"),
                       (catalog, "cache_put"), (policy, "_jev_choice")):
            self.stack.enter_context(patch.object(*target, side_effect=AssertionError("preview invoked external work")))
        self.snapshot = catalog.ReadOnlyCatalogSnapshot({}, 123)

    def show(self, raw=None, **kw):
        return settings.preview(self.repo, self.raw if raw is None else raw,
                                catalog_snapshot=kw.pop("catalog_snapshot", self.snapshot), **kw)

    def test_actual_pick_parity_for_identical_candidate_and_snapshot(self):
        self.write()
        before = self.files()
        cfg = config.validate_candidate(self.raw, harness=harness.parse_harness(harness.harness_path(self.repo)))
        kwargs = dict(role="implement", case="frontend update", task_domain="frontend", risk="high")
        got = self.show(**kwargs)
        actual = route.pick("codex", harness.effective_workers(self.repo, "codex"), repo=self.repo,
                            preview_config=cfg, catalog_snapshot=self.snapshot, **kwargs)
        self.assertEqual(got, actual)
        self.assertEqual(self.files(), before)
        self.assertTrue(got["routing"]["preview_only"])
        self.assertEqual(got["model"], "grok-4.7")
        text = "\n".join(settings.preview_lines(got))
        for field in ("worker=", "model=", "effort=", "tier=", "fingerprint=", "candidate "):
            self.assertIn(field, text)

    def test_live_picker_parity_under_same_catalog_observation_and_gates(self):
        raw = {"schema_version": 4, "domains": {
            "backend": {"preferred_profiles": ["opencode-gpt-5.6-luna-high"], "fallback": "none"},
            "frontend": {"preferred_profiles": ["claude-sonnet-5-medium"], "fallback": "scored"},
        }}
        self.write(raw)
        (self.repo / "source.md").write_text("read-only local source")
        for age, ids in ((0, ["openai/gpt-6-luna"]), (7200, ["openai/gpt-6-luna"]),
                         (90000, ["openai/gpt-6-luna"]), (0, [])):
            row = {"worker": "opencode", "ids": ids, "age_s": age,
                   "state": "stale" if age > 3600 else "fresh", "source": "cache",
                   "freshness": "stale" if age > 3600 else "fresh", "confirmed_empty": not ids}
            snap = catalog.ReadOnlyCatalogSnapshot({"opencode": row}, 123)
            cases = [dict(task_domain="backend"), dict(task_domain="frontend", exclude="claude"),
                     dict(task_domain="frontend", risk="high"), dict(task_domain="ui-verification"),
                     dict(task_domain="research", role="explore"),
                     dict(task_domain="research", role="explore", research_sources=["source.md"]),
                     dict(task_domain="review", role="review", review_mode="independent")]
            for kwargs in cases:
                with self.subTest(age=age, ids=ids, kwargs=kwargs):
                    preview = self.show(raw, catalog_snapshot=snap, **kwargs)
                    preview["routing"].pop("preview_only")
                    actual_args = {"role": "implement", "case": "", **kwargs}
                    with patch.object(catalog, "load_catalog_info", side_effect=lambda worker, **_kw: snap.info(worker)):
                        actual = route.pick("codex", harness.effective_workers(self.repo, "codex"),
                                            repo=self.repo, **actual_args)
                    self.assertEqual(preview, actual)

    def test_preview_evidence_cannot_launch_even_when_config_matches(self):
        self.write()
        c = self.show(task_domain="frontend")
        with self.assertRaisesRegex(ValueError, "preview is not launch evidence"):
            evidence.validate_launch_tuple(self.repo, worker=c["worker"], model=c["model"], effort=c["effort"],
                                           role=c["kind"], routing=c["routing"])

    def test_stripping_preview_marker_still_requires_real_launch_catalog(self):
        raw = {"schema_version": 4, "domains": {"backend": {
            "preferred_profiles": ["opencode-gpt-5.6-luna-high"], "fallback": "none"}}}
        self.write(raw)
        snap = catalog.ReadOnlyCatalogSnapshot({"opencode": {
            "ids": ["openai/gpt-6-luna"], "state": "fresh", "freshness": "fresh", "source": "cache",
            "age_s": 0, "confirmed_empty": False}}, 123)
        c = self.show(raw, task_domain="backend", catalog_snapshot=snap)
        c["routing"].pop("preview_only")
        with patch.object(catalog, "load_catalog_info", return_value={
            "ids": None, "state": "unavailable", "source": "none", "freshness": "unavailable",
            "confirmed_empty": False}) as real_catalog, self.assertRaises(ValueError):
            evidence.validate_launch_tuple(self.repo, worker=c["worker"], model=c["model"], effort=c["effort"],
                                           role=c["kind"], routing=c["routing"], access="write",
                                           executor_kind="wrapper", live="codex", task_domain="backend")
        real_catalog.assert_called()

    def test_parent_boundaries_research_sources_and_fallbacks(self):
        for domain in ("ui-design", "ui-verification", "research"):
            c = self.show(task_domain=domain)
            self.assertEqual(c["spawn"], "stay")
            self.assertIn("parent only", settings.preview_lines(c)[1])
            self.assertEqual(c["model"], "")
        (self.repo / "source.md").write_text("local source")
        c = self.show(task_domain="research", role="explore", research_sources=["source.md"])
        self.assertEqual(c["spawn"], "run-worker")
        for source in ("missing.md", "../escape", "https://example.com"):
            with self.assertRaises(ValueError):
                self.show(task_domain="research", role="explore", research_sources=[source])
        for fallback, result in (("none", "none"), ("parent", "native"), ("scored", "run-worker")):
            raw = {"schema_version": 4, "domains": {"backend": {"fallback": fallback}}}
            self.assertEqual(self.show(raw, task_domain="backend")["spawn"], result)

    def test_worker_disable_binary_mcp_live_parent_and_exclusions(self):
        raw = {"schema_version": 4, "domains": {"frontend": {"preferred_profiles": ["codex-luna-low", "claude-sonnet-5-medium"], "fallback": "none"}}}
        for gate in ("disabled", "binary", "mcp", "exclude"):
            with ExitStack() as stack:
                if gate == "disabled":
                    path = self.repo / ".rig/harness.toml"
                    original = path.read_text()
                    path.write_text(original.replace("claude=true", "claude=false"))
                    stack.callback(path.write_text, original)
                if gate == "binary":
                    stack.enter_context(patch.object(harness, "find_worker_bin", side_effect=lambda name: "" if name == "claude" else "/fake"))
                if gate == "mcp":
                    stack.enter_context(patch.object(harness, "worker_mcp_reason", return_value="missing"))
                    import child_mcp
                    stack.enter_context(patch.object(child_mcp, "worker_mcp_ready", return_value=(False, "missing")))
                c = self.show(raw, task_domain="frontend", exclude="claude" if gate == "exclude" else "")
                self.assertEqual(c["spawn"], "none")
                decisions = {row["id"]: row["code"] for row in c["routing"]["candidate_decisions"]}
                self.assertEqual(decisions["codex-luna-low"], "worker-live-parent")
                self.assertEqual(decisions["claude-sonnet-5-medium"], {
                    "disabled": "worker-disabled", "binary": "worker-cli-missing",
                    "mcp": "worker-mcp-unavailable", "exclude": "worker-excluded"}[gate])

    def test_catalog_exactness_and_freshness_gate(self):
        raw = {"schema_version": 4, "domains": {"backend": {"preferred_profiles": ["opencode-gpt-5.6-luna-high"], "fallback": "none"}}}
        profile = config.validate_candidate(raw).profiles["opencode-gpt-5.6-luna-high"]
        for age, ids, expected in ((0, [profile.selector], "run-worker"), (7200, [profile.selector], "run-worker"),
                                    (90000, [profile.selector], "none"), (0, ["other"], "none"), (0, [], "none")):
            row = {"worker": "opencode", "ids": ids, "age_s": age, "state": "stale" if age > 3600 else "fresh",
                   "source": "cache", "freshness": "stale" if age > 3600 else "fresh", "confirmed_empty": not ids}
            snap = catalog.ReadOnlyCatalogSnapshot({"opencode": row}, 123)
            c = self.show(raw, task_domain="backend", catalog_snapshot=snap)
            self.assertEqual(c["spawn"], expected)
            if age > 86400:
                self.assertIn("catalog-stale-refresh-failed", str(c["routing"]["candidate_decisions"]))
        self.assertEqual(self.show(raw, task_domain="backend")["spawn"], "none")

    def test_review_independence_requires_accepted_writer_and_different_provider(self):
        c = self.show(role="review", task_domain="review", review_mode="independent")
        self.assertEqual(c["spawn"], "none")
        self.assertIn("writer_job_id", c["reason"])
        context = {"writer_job_id": "writer", "writer_snapshot_id": "a" * 64, "writer_provider": "anthropic",
                   "writer_providers": ["anthropic"], "review_mode": "independent"}
        with patch.object(route, "_writer_context", return_value=(context, "")):
            c = self.show(role="review", task_domain="review", review_mode="independent", writer_job_id="writer")
        self.assertNotEqual(c["provider"], "anthropic")
        self.assertIn("review-same-provider", str(c["routing"]["candidate_decisions"]))

    def test_jev_never_called_local_fallback_explicit(self):
        c = self.show({"schema_version": 3, "picker": {"engine": "jev"}}, task_domain="general")
        self.assertEqual(c["routing"]["picker"]["fallback"], "preview-provider-not-invoked")
        self.assertIn("live Jev pick may differ", "\n".join(settings.preview_lines(c)))

    def test_no_effort_selector_is_not_reported_as_unknown_observation(self):
        c = self.show(task_domain="frontend")
        c["effort"] = ""
        self.assertIn("effort=not specified", "\n".join(settings.preview_lines(c)))
        parent = self.show(task_domain="ui-design")
        self.assertIn("effort=unknown", "\n".join(settings.preview_lines(parent)))

    def test_legacy_not_silently_switched(self):
        with (self.repo / ".rig/harness.toml").open("a") as stream:
            stream.write('[routing]\nmode="legacy"\n')
        with self.assertRaisesRegex(ValueError, "legacy"):
            self.show()
        with self.assertRaisesRegex(ValueError, "requires smart"):
            route.pick("codex", [], "implement", "task", repo=self.repo, catalog_snapshot=self.snapshot)


if __name__ == "__main__":
    unittest.main()
