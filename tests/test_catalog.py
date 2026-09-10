#!/usr/bin/env python3
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import catalog  # noqa: E402
import route  # noqa: E402

OPENCODE_LUNA = "openai/gpt-5.6-luna"
OMP_GROK = "grok-4.6"
AGY_FLASH = "gemini-3.8-flash-high"


class CatalogEnv(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.cache = Path(self.td.name) / "model-catalogs.json"
        self.bins = Path(self.td.name) / "bins"
        self.bins.mkdir()
        self._old = {
            k: os.environ.get(k)
            for k in (
                "RIG_SKIP_MODEL_CATALOG",
                "RIG_REFRESH_MODELS",
                "RIG_MODEL_CATALOG_CACHE",
                "PATH",
            )
        }
        os.environ.pop("RIG_SKIP_MODEL_CATALOG", None)
        os.environ.pop("RIG_REFRESH_MODELS", None)
        os.environ["RIG_MODEL_CATALOG_CACHE"] = str(self.cache)
        os.environ["PATH"] = f"{self.bins}:/usr/bin:/bin"

    def tearDown(self):
        for key, val in self._old.items():
            if val is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = val
        self.td.cleanup()

    def _bin(self, name: str, script: str) -> None:
        path = self.bins / name
        path.write_text("#!/bin/sh\n" + script)
        path.chmod(0o755)


class ParseCatalog(unittest.TestCase):
    def test_opencode_skips_json_blobs(self):
        text = (
            "openai/gpt-5.6-luna\n"
            '{"type":"model","id":"nope"}\n'
            "anthropic/claude-sonnet-5\n"
        )
        self.assertEqual(
            catalog.parse_opencode(text),
            ["openai/gpt-5.6-luna", "anthropic/claude-sonnet-5"],
        )

    def test_omp_json_prefers_selector(self):
        payload = {
            "models": [
                {"provider": "xai", "id": "grok-4.6", "selector": "xai-oauth/grok-4.6"},
                {"provider": "anthropic", "id": "claude-4-sonnet", "selector": ""},
            ]
        }
        self.assertEqual(
            catalog.parse_omp_json(json.dumps(payload)),
            ["xai-oauth/grok-4.6", "claude-4-sonnet"],
        )

    def test_omp_table_last_resort(self):
        text = (
            "provider id selector\n"
            "xai grok-4.6 xai-oauth/grok-4.6\n"
            "anthropic claude-4-sonnet cursor/claude-4-sonnet\n"
        )
        self.assertEqual(
            catalog.parse_omp_table(text),
            ["xai-oauth/grok-4.6", "cursor/claude-4-sonnet"],
        )

    def test_pi_stores_provider_and_bare(self):
        text = "provider model context\nopenai gpt-5.4-mini 128000\nxai grok-4.6 256000\n"
        self.assertEqual(
            catalog.parse_pi(text),
            ["openai/gpt-5.4-mini", "gpt-5.4-mini", "xai/grok-4.6", "grok-4.6"],
        )

    def test_agy_skips_fetching_line(self):
        text = (
            "Fetching available models...\n"
            "gemini-3.8-flash-high\tGemini 3.8 Flash High\n"
            "gemini-3.8-flash-low  Gemini 3.8 Flash Low\n"
        )
        self.assertEqual(
            catalog.parse_agy(text),
            ["gemini-3.8-flash-high", "gemini-3.8-flash-low"],
        )


class PinMatch(unittest.TestCase):
    def test_suffix_and_slash(self):
        self.assertTrue(catalog.pin_matches("grok-4.6", "xai-oauth/grok-4.6"))
        self.assertTrue(catalog.pin_matches("grok-4.6", "grok-cli/grok-4.6"))
        self.assertTrue(catalog.pin_matches(OPENCODE_LUNA, OPENCODE_LUNA))
        self.assertFalse(catalog.pin_matches("grok-4", "xai-oauth/grok-4.6"))
        self.assertTrue(catalog.pin_matches("openai/gpt-5.6-luna", "gpt-5.6-luna"))


class ResolveAgainstCatalog(unittest.TestCase):
    def test_preferred_pin_in_catalog(self):
        self.assertEqual(
            catalog.resolve_model(
                "opencode",
                "implement",
                OPENCODE_LUNA,
                [OPENCODE_LUNA, "openai/gpt-5.4-mini"],
            ),
            OPENCODE_LUNA,
        )
        self.assertEqual(
            catalog.resolve_model(
                "omp",
                "implement",
                OMP_GROK,
                ["xai-oauth/grok-4.6", "cursor/claude-4-sonnet"],
            ),
            OMP_GROK,
        )
        self.assertEqual(
            catalog.resolve_model(
                "agy",
                "implement",
                AGY_FLASH,
                ["gemini-3.8-flash-low", AGY_FLASH],
            ),
            AGY_FLASH,
        )

    def test_preferred_missing_picks_sonnet(self):
        got = catalog.resolve_model(
            "opencode",
            "implement",
            OPENCODE_LUNA,
            ["anthropic/claude-sonnet-5", "openai/gpt-5.4-mini"],
        )
        self.assertEqual(got, "anthropic/claude-sonnet-5")
        self.assertTrue(got)

    def test_banned_only_falls_back_to_pin(self):
        got = catalog.resolve_model(
            "opencode",
            "implement",
            OPENCODE_LUNA,
            ["openai/gpt-5.6-sol", "claude-fable-5"],
        )
        self.assertEqual(got, OPENCODE_LUNA)
        self.assertNotIn("sol", got.lower())
        self.assertNotIn("fable", got.lower())
        self.assertNotIn("astra", got.lower())

    def test_empty_catalog_is_static_pin(self):
        self.assertEqual(
            catalog.resolve_model("opencode", "implement", OPENCODE_LUNA, []),
            OPENCODE_LUNA,
        )

    def test_skip_env_is_static_pin(self):
        old = os.environ.get("RIG_SKIP_MODEL_CATALOG")
        os.environ["RIG_SKIP_MODEL_CATALOG"] = "1"
        try:
            self.assertEqual(
                catalog.resolve_model("opencode", "implement", OPENCODE_LUNA),
                OPENCODE_LUNA,
            )
        finally:
            if old is None:
                os.environ.pop("RIG_SKIP_MODEL_CATALOG", None)
            else:
                os.environ["RIG_SKIP_MODEL_CATALOG"] = old

    def test_codex_grok_claude_cursor_ignore_catalog(self):
        for worker, kind, pin in (
            ("codex", "implement", "gpt-5.6-luna"),
            ("grok", "implement", "grok-4.6"),
            ("claude", "implement", "claude-sonnet-5"),
            ("cursor", "implement", "composer-2.5"),
        ):
            preferred, _effort = route.model_for(worker, kind)
            self.assertEqual(preferred, pin)
            self.assertEqual(
                catalog.resolve_model(
                    worker, kind, preferred, catalogs={worker: ["other-model"]}
                ),
                pin,
            )

    def test_first_remaining_when_no_keyword(self):
        got = catalog.resolve_model(
            "opencode",
            "implement",
            OPENCODE_LUNA,
            ["custom/weird-id", "other/also-weird"],
        )
        self.assertEqual(got, "custom/weird-id")

    def test_implement_skips_mini_if_stronger_exists(self):
        got = catalog.resolve_model(
            "opencode",
            "implement",
            OPENCODE_LUNA,
            ["openai/gpt-5.6-mini", "openai/gpt-5.6-codex"],
        )
        self.assertEqual(got, "openai/gpt-5.6-codex")


class PickAndEnvCatalog(unittest.TestCase):
    def test_pick_uses_injected_catalog(self):
        c = route.pick(
            "",
            ["opencode"],
            "implement",
            "add a header",
            catalogs={"opencode": ["anthropic/claude-sonnet-5"]},
        )
        self.assertEqual(c["worker"], "opencode")
        self.assertEqual(c["model"], "anthropic/claude-sonnet-5")
        self.assertEqual(c["effort"], "high")

    def test_pick_effort_stays_on_pin_table(self):
        c = route.pick(
            "",
            ["agy"],
            "hard",
            "multi-file architecture",
            catalogs={"agy": ["gemini-3.1-pro-high", AGY_FLASH]},
        )
        self.assertEqual(c["effort"], "high")
        self.assertEqual(c["model"], "gemini-3.1-pro-high")

    def test_resolved_env_matches_pick(self):
        catalogs = {"omp": ["xai-oauth/grok-4.6"]}
        model, effort = route.resolved_model_for("omp", "implement", catalogs)
        self.assertEqual(model, OMP_GROK)
        self.assertEqual(effort, "high")

    def test_model_for_stays_static(self):
        self.assertEqual(route.model_for("opencode", "implement"), (OPENCODE_LUNA, "high"))
        self.assertEqual(route.model_for("omp", "implement"), (OMP_GROK, "high"))
        self.assertEqual(route.model_for("agy", "implement"), (AGY_FLASH, "high"))


class ProbeAndCache(CatalogEnv):
    def test_probe_failure_and_missing_binary_are_unknown(self):
        self.assertIsNone(catalog.load_catalog("opencode"))
        self._bin("omp", "echo nope\nexit 1\n")
        self.assertIsNone(catalog.load_catalog("omp"))
        self.assertEqual(
            catalog.resolve_model("omp", "implement", OMP_GROK),
            OMP_GROK,
        )

    def test_probe_opencode_and_cache_hit(self):
        self._bin("opencode", 'echo "openai/gpt-5.6-luna"\necho "anthropic/claude-sonnet-5"\n')
        ids = catalog.load_catalog("opencode")
        self.assertEqual(ids, [OPENCODE_LUNA, "anthropic/claude-sonnet-5"])
        self.assertTrue(self.cache.is_file())
        self.bins.joinpath("opencode").write_text("#!/bin/sh\nexit 1\n")
        again = catalog.load_catalog("opencode")
        self.assertEqual(again, [OPENCODE_LUNA, "anthropic/claude-sonnet-5"])

    def test_refresh_bypasses_ttl(self):
        catalog.cache_put("agy", [AGY_FLASH])
        self._bin("agy", 'echo "gemini-3.8-flash-low"\n')
        os.environ["RIG_REFRESH_MODELS"] = "1"
        ids = catalog.load_catalog("agy")
        self.assertEqual(ids, ["gemini-3.8-flash-low"])

    def test_skip_does_not_probe(self):
        self._bin("pi", 'echo "xai grok-4.6 1"\n')
        os.environ["RIG_SKIP_MODEL_CATALOG"] = "1"
        self.assertIsNone(catalog.load_catalog("pi"))

    def test_doctor_only_on_fresh_cache(self):
        self.assertEqual(catalog.doctor_lines(), [])
        catalog.cache_put("opencode", [OPENCODE_LUNA, "openai/gpt-5.4-mini"])
        lines = catalog.doctor_lines()
        self.assertEqual(lines[0], "Model catalogs (cached)")
        self.assertIn("opencode: 2 models", "\n".join(lines))
        data = json.loads(self.cache.read_text())
        data["opencode"]["fetched_at"] = time.time() - catalog.TTL_SECONDS - 10
        self.cache.write_text(json.dumps(data))
        self.assertEqual(catalog.doctor_lines(), [])

    def test_timeout_is_unknown(self):
        self._bin("opencode", "sleep 2\n")
        self.assertIsNone(catalog.probe_worker("opencode", timeout=0.2))

    def test_omp_json_probe(self):
        payload = json.dumps(
            {"models": [{"provider": "xai", "id": "grok-4.6", "selector": "xai-oauth/grok-4.6"}]}
        )
        self._bin("omp", f"echo '{payload}'\n")
        self.assertEqual(catalog.probe_worker("omp"), ["xai-oauth/grok-4.6"])

    def test_stale_ttl_returns_without_waiting(self):
        catalog.cache_put("omp", [OMP_GROK])
        data = json.loads(self.cache.read_text())
        data["omp"]["fetched_at"] = time.time() - catalog.TTL_SECONDS - 10
        self.cache.write_text(json.dumps(data))
        self._bin("omp", "sleep 2\necho grok-4.5\n")
        t0 = time.time()
        ids = catalog.load_catalog("omp")
        elapsed = time.time() - t0
        self.assertEqual(ids, [OMP_GROK])
        self.assertLess(elapsed, 0.5)

    def test_refresh_still_probes(self):
        catalog.cache_put("agy", [AGY_FLASH])
        data = json.loads(self.cache.read_text())
        data["agy"]["fetched_at"] = time.time() - catalog.TTL_SECONDS - 10
        self.cache.write_text(json.dumps(data))
        self._bin("agy", 'echo "gemini-3.8-flash-low"\n')
        os.environ["RIG_REFRESH_MODELS"] = "1"
        ids = catalog.load_catalog("agy")
        self.assertEqual(ids, ["gemini-3.8-flash-low"])

    def test_load_catalogs_probes_missing_in_parallel(self):
        def slow_probe(worker, timeout=8.0):
            time.sleep(0.35)
            return {
                "opencode": [OPENCODE_LUNA],
                "agy": [AGY_FLASH],
            }.get(worker)

        old = catalog.probe_worker
        catalog.probe_worker = slow_probe
        try:
            t0 = time.time()
            got = catalog.load_catalogs(["opencode", "agy"])
            elapsed = time.time() - t0
        finally:
            catalog.probe_worker = old
        self.assertEqual(got["opencode"], [OPENCODE_LUNA])
        self.assertEqual(got["agy"], [AGY_FLASH])
        self.assertLess(elapsed, 0.55)
        self.assertGreater(elapsed, 0.2)

    def test_load_catalogs_skip_and_fresh_hit(self):
        os.environ["RIG_SKIP_MODEL_CATALOG"] = "1"
        self._bin("pi", 'echo "xai grok-4.6 1"\n')
        self.assertEqual(catalog.load_catalogs(["pi"]), {"pi": None})
        os.environ.pop("RIG_SKIP_MODEL_CATALOG", None)
        catalog.cache_put("pi", ["grok-4.6"])
        self.bins.joinpath("pi").write_text("#!/bin/sh\nexit 1\n")
        self.bins.joinpath("pi").chmod(0o755)
        got = catalog.load_catalogs(["pi"])
        self.assertEqual(got["pi"], ["grok-4.6"])


if __name__ == "__main__":
    unittest.main()
