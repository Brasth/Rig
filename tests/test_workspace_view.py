#!/usr/bin/env python3
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import local_api  # noqa: E402
import workspace  # noqa: E402
from container_test_support import forward_status  # noqa: E402

FAKE_DC = r'''#!/bin/sh
echo "$*" >> "$FAKE_DC_LOG"
out() { if [ "$(eval echo \${$1+set})" = set ]; then eval printf '%s\\n' "\"\$$1\""; else echo "$2"; fi; }
case "$1" in
  --version) echo "dc 0.23.0" ;;
  ls) out FAKE_DC_LS '[]' ;;
  forward) if [ -n "$FAKE_DC_FORWARD_FAIL" ]; then echo "no sidecars" >&2; exit 2; fi; out FAKE_DC_FORWARD 'desired:\nactual:\n  (none)' ;;
  doctor) out FAKE_DC_DOCTOR '{"checks": []}'; exit "${FAKE_DC_DOCTOR_EXIT:-0}" ;;
  *) exit 64 ;;
esac
'''


def make_repo(root: Path) -> Path:
    (root / ".git").mkdir(parents=True, exist_ok=True)
    (root / ".rig").mkdir(exist_ok=True)
    (root / ".rig" / "harness.toml").write_text("[project]\nenabled = true\n")
    return root


class Base(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.base = Path(self.td.name)
        self.repo = make_repo(self.base / "repo")
        self.log = self.base / "dc.log"
        self.fake = self.base / "dc"
        self.fake.write_text(FAKE_DC)
        self.fake.chmod(0o755)
        self.env = mock.patch.dict(os.environ, {"RIG_DC_BIN": str(self.fake), "FAKE_DC_LOG": str(self.log),
                                                "RIG_HOME": str(self.base / "home")})
        self.env.start()
        for key in [k for k in os.environ if k.startswith("FAKE_DC_") and k != "FAKE_DC_LOG"]:
            os.environ.pop(key)
        workspace._cache.clear()

    def tearDown(self):
        self.env.stop()
        workspace._cache.clear()
        self.td.cleanup()

    def calls(self):
        return self.log.read_text().splitlines() if self.log.exists() else []


class Parsing(unittest.TestCase):
    def test_containers_tolerate_alternate_keys_and_junk(self):
        rows = [{"container": "c1", "status": "Up 2 hours", "image_name": "img", "running_for": "2h"}, "junk", 5, {"id": "abc"}]
        out = workspace.containers_view(rows)
        self.assertEqual(out[0], {"name": "c1", "id": "", "state": "Up 2 hours", "image": "img", "uptime": "2h"})
        self.assertEqual(out[1]["name"], "abc")
        self.assertEqual(workspace.containers_view(None), [])

    def test_forward_status_text_from_the_real_dc_cli(self):
        real = (ROOT / "tests" / "fixtures" / "dc" / "forward-status.txt").read_text()
        ports = workspace.parse_forward_status(real)
        self.assertEqual([(p["port"], p["state"][:2], p["problem"]) for p in ports], [("3000", "Up", False)])
        down = workspace.parse_forward_status(real.split("actual:")[0] + "actual:\n  (none)\n")
        self.assertEqual([(p["port"], p["problem"]) for p in down], [("3000", True)])
        bound = "desired:\n  8080:80  (explicit)\nactual:\nsidecar  dc-fwd-x-8080\tUp 1 hour\t0.0.0.0:8080->80/tcp\n"
        self.assertEqual(workspace.parse_forward_status(bound)[0]["port"], "8080")
        self.assertEqual(workspace.parse_forward_status(""), [])


class Facts(Base):
    def test_shallow_status_never_asks_dc_for_ports_or_doctor(self):
        status = workspace.status(self.repo)
        self.assertEqual((status["ports"], status["doctor"]), ([], None))
        self.assertEqual(sorted(c.split()[0] for c in self.calls()), ["--version", "ls"])

    def test_deep_status_adds_ports_and_the_dc_diagnosis(self):
        os.environ["FAKE_DC_FORWARD"] = forward_status(3000)
        os.environ["FAKE_DC_DOCTOR"] = '{"checks": [{"name": "engine", "status": "fail", "recover": "engine-down"}]}'
        status = workspace.status(self.repo, deep=True)
        self.assertEqual(status["ports"][0]["port"], "3000")
        self.assertFalse(status["doctor"]["ok"])
        self.assertEqual(status["doctor"]["problems"][0]["recover"], "engine-down")

    def test_results_are_cached_per_depth_for_a_few_seconds(self):
        clock = [100.0]
        now = lambda: clock[0]  # noqa: E731
        workspace.status(self.repo, now=now)
        workspace.status(self.repo, now=now)
        self.assertEqual(len([c for c in self.calls() if c == "ls --json"]), 1)
        workspace.status(self.repo, deep=True, now=now)
        self.assertEqual(len([c for c in self.calls() if c == "ls --json"]), 2)
        clock[0] += workspace.CACHE_SECONDS + 1
        workspace.status(self.repo, now=now)
        self.assertEqual(len([c for c in self.calls() if c == "ls --json"]), 3)

    def test_forward_failure_is_reported_and_does_not_hide_the_rest(self):
        os.environ["FAKE_DC_FORWARD_FAIL"] = "1"
        status = workspace.status(self.repo, deep=True)
        self.assertIn("no sidecars", status["error"])
        self.assertIsNotNone(status["doctor"])


class Rendering(Base):
    def sample(self):
        return json.loads((ROOT / "shell" / "testdata" / "sample-workspace.json").read_text())

    def lines(self, view, title):
        return [l["text"] for s in view["sections"] if s["title"] == title for l in s["lines"]]

    def test_full_view_matches_the_panel_design(self):
        view = workspace.view(self.sample())
        self.assertTrue(view["attention"])
        self.assertEqual(self.lines(view, "Workspace")[:3], ["kind     devcontainer", "override none", "  dc-cli   0.23.0 (pin 0.23.0)"])
        self.assertIn("● running  a3f9c1d2e4b5 · 2h", self.lines(view, "Workspace"))
        self.assertEqual(self.lines(view, "Ports"), ["  3000  web", "  5173  vite", "! 9229  held by node"])
        self.assertEqual(self.lines(view, "Doctor"), ["! engine  (fix: dc recover engine-slow)"])

    def test_no_dc_and_clean_states(self):
        status = self.sample()
        status.update(dc={"found": False, "path": "", "version": "", "pin": "", "pin_state": "missing"},
                      containers=[], ports=[], doctor=None, error="dc-cli is not installed")
        view = workspace.view(status)
        self.assertTrue(view["attention"])
        self.assertIn("! dc-cli is not installed", self.lines(view, "Workspace"))
        clean = self.sample()
        clean.update(ports=[{"port": "3000", "name": "web", "state": "", "held_by": "", "problem": False}],
                     doctor={"ok": True, "detail": "", "problems": []})
        view = workspace.view(clean)
        self.assertFalse(view["attention"])
        self.assertEqual(self.lines(view, "Doctor"), ["● dc doctor reports no problems"])

    def test_pin_mismatch_is_flagged(self):
        status = self.sample()
        status["dc"]["pin_state"], status["dc"]["pin"] = "mismatch", "9.9.9"
        view = workspace.view(status)
        self.assertIn("! dc-cli   0.23.0 (pin 9.9.9)", self.lines(view, "Workspace"))

    def test_go_shell_fixture_matches_the_view_builder(self):
        data = ROOT / "shell" / "testdata"
        fresh = {"view": workspace.view(self.sample())}
        path = data / "workspace.json"
        if os.environ.get("RIG_UPDATE_FIXTURES") == "1":
            path.write_text(json.dumps(fresh, indent=1, ensure_ascii=False) + "\n")
        self.assertEqual(json.loads(path.read_text()), fresh,
                         "run: RIG_UPDATE_FIXTURES=1 python3 tests/test_workspace_view.py Rendering")


class OverTheWire(Base):
    def setUp(self):
        super().setUp()
        (self.repo / ".git").mkdir(exist_ok=True)
        self.env_copy = {k: v for k, v in os.environ.items() if k not in {"RIG_JOB_ID", "RIG_JOB_DIR"}}
        self.proc = subprocess.Popen([sys.executable, str(ROOT / "scripts" / "local_api.py"), "serve", "--repo", str(self.repo)],
                                     env=self.env_copy, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                local_api.call(self.repo, "api.ping", timeout=0.5)
                return
            except OSError:
                time.sleep(0.05)
        self.fail("API did not start")

    def tearDown(self):
        self.proc.terminate()
        self.proc.wait(timeout=10)
        super().tearDown()

    def test_workspace_status_method_and_parameter_validation(self):
        reply = local_api.call(self.repo, "workspace.status", {"deep": False})
        self.assertEqual(reply["result"]["view"]["version"], workspace.VIEW_VERSION)
        self.assertEqual(reply["result"]["view"]["sections"][0]["title"], "Workspace")
        self.assertEqual(reply["result"]["status"]["kind"], "none")
        bad = local_api.call(self.repo, "workspace.status", {"deep": "yes"})
        self.assertEqual(bad["error"]["code"], local_api.INVALID_PARAMS)


if __name__ == "__main__":
    unittest.main()
