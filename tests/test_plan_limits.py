#!/usr/bin/env python3
"""Plan limits: meters that are reported or read from codex's files, resets, hand-over decisions, the routing note."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import plan_limits as pl  # noqa: E402

NOW = 1_800_000_000.0


class Limits(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.base = Path(self.td.name)
        self.env = mock.patch.dict(os.environ, {"RIG_HOME": str(self.base / "rig")})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.td.cleanup()

    def test_a_report_becomes_a_meter_until_its_window_resets(self):
        pl.report("claude", 92, NOW + 3600, "5 hours", now=NOW)
        self.assertEqual(pl.meters(NOW + 60, self.base)["claude"]["used_pct"], 92.0)
        after = pl.meters(NOW + 7200, self.base)["claude"]
        self.assertEqual((after["used_pct"], after["windows"][0]["reset"]), (0.0, True))

    def test_the_worst_window_is_the_meter(self):
        pl.report("claude", 30, NOW + 3600, "5 hours", now=NOW)
        pl.report("claude", 71, NOW + 86400, "week", now=NOW)
        self.assertEqual(pl.meters(NOW, self.base)["claude"]["used_pct"], 71.0)

    def test_an_old_report_without_a_reset_time_is_dropped(self):
        pl.report("claude", 50, None, now=NOW)
        self.assertIn("claude", pl.meters(NOW + 60, self.base))
        self.assertNotIn("claude", pl.meters(NOW + pl.MAX_AGE + 1, self.base))

    def test_bad_reports_are_refused(self):
        for args in (("nobody", 5), ("claude", 101), ("claude", "9"), ("claude", True), ("claude", 5, -1)):
            with self.assertRaises(ValueError):
                pl.report(*args)

    def test_codex_files_are_read(self):
        folder = self.base / ".codex" / "sessions" / "2026" / "10" / "23"
        folder.mkdir(parents=True)
        event = {"type": "event_msg", "payload": {"type": "token_count", "rate_limits": {
            "primary": {"used_percent": 41.0, "window_minutes": 300, "resets_at": NOW + 600},
            "secondary": {"used_percent": 12.5, "window_minutes": 10080, "resets_in_seconds": 5000}}}}
        (folder / "rollout-x.jsonl").write_text(json.dumps({"type": "session_meta"}) + "\n" + json.dumps(event) + "\n")
        got = pl.meters(os.stat(folder / "rollout-x.jsonl").st_mtime + 1, self.base)["codex"]
        self.assertEqual((got["used_pct"], [w["label"] for w in got["windows"]]), (41.0, ["5 hours", "week"]))

    def test_nothing_is_invented_when_nothing_was_reported(self):
        self.assertEqual(pl.meters(NOW, self.base), {})
        self.assertEqual(pl.note({}), "")

    def test_handover_when_the_parent_is_out_and_back_when_it_resets(self):
        full = {"claude": {"used_pct": 97.0, "windows": [{"used_pct": 97.0}]}, "codex": {"used_pct": 20.0, "windows": [{"used_pct": 20.0}]}}
        got = pl.decide("claude", "claude", ["claude", "codex"], full)
        self.assertEqual((got["action"], got["to"]), ("handover", "codex"))
        still = pl.decide("claude", "codex", ["claude", "codex"], full)
        self.assertEqual(still["action"], "stay")
        reset = {"claude": {"used_pct": 0.0, "windows": [{"used_pct": 0.0, "reset": True}]}, "codex": full["codex"]}
        self.assertEqual(pl.decide("claude", "codex", ["claude", "codex"], reset)["action"], "back")

    def test_no_handover_when_nobody_has_room(self):
        both = {"claude": {"used_pct": 97.0, "windows": []}, "codex": {"used_pct": 90.0, "windows": []}}
        got = pl.decide("claude", "claude", ["claude", "codex"], both)
        self.assertEqual(got["action"], "stay")
        self.assertIn("no other parent", got["reason"])

    def test_the_routing_note(self):
        cur = {"claude": {"used_pct": 92.0}, "codex": {"used_pct": 10.0}}
        self.assertEqual(pl.note(cur), "tests → codex, claude at 92%")
        self.assertEqual(pl.note({"claude": {"used_pct": 92.0}}), "claude at 92%")
        self.assertEqual(pl.note({"claude": {"used_pct": 50.0}}), "")


class OverTheApi(unittest.TestCase):
    setUp = Limits.setUp
    tearDown = Limits.tearDown

    def test_report_then_get(self):
        import local_api
        repo = self.base / "repo"
        (repo / ".git").mkdir(parents=True)
        (repo / ".rig").mkdir()
        (repo / ".rig" / "harness.toml").write_text('parent = "claude"\n\n[project]\nenabled = true\n')
        api = local_api.Api(repo, "t" * 64)
        call = lambda method, params=None: api.dispatch({"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}, "token": "t" * 64})
        rows = {r["method"]: r["mutates"] for r in call("api.methods")["result"]["methods"]}
        self.assertEqual((rows["limits.get"], rows["limits.report"], rows["limits.handover"]), (False, True, True))
        self.assertEqual(call("limits.get")["result"]["meters"], {})
        out = call("limits.report", {"provider": "claude", "used_pct": 92, "window": "5 hours", "resets_at": time_ahead()})
        self.assertEqual(out["result"]["meters"]["claude"]["used_pct"], 92.0)
        self.assertEqual(out["result"]["note"], "claude at 92%")
        self.assertEqual(call("limits.report", {"provider": "claude", "used_pct": 500})["error"]["code"], local_api.INVALID_PARAMS)
        self.assertEqual(call("limits.handover", {"to": "nobody"})["error"]["code"], local_api.INVALID_PARAMS)


def time_ahead():
    import time
    return time.time() + 3600


if __name__ == "__main__":
    unittest.main()
