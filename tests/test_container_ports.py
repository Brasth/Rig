#!/usr/bin/env python3
"""Container mode: UI evidence may only come from ports dc-cli forwards."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import browser_skill  # noqa: E402
import computer_use  # noqa: E402
import sandbox_container as sc  # noqa: E402
import sandbox_env  # noqa: E402
import sandbox_provider  # noqa: E402
from container_test_support import install_fake_dc  # noqa: E402


class Base(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.base = Path(self.td.name)
        self.repo = self.base / "repo"
        (self.repo / ".git").mkdir(parents=True)
        (self.repo / ".rig").mkdir()
        self.harness("devcontainer")
        self.dc = install_fake_dc(self.base / "bin")
        self.log = self.base / "dc.log"
        self.env = mock.patch.dict(os.environ, {"RIG_DC_BIN": str(self.dc), "FAKE_DC_LOG": str(self.log),
                                                "FAKE_DC_FORWARD": json.dumps([{"port": 3000, "name": "web"}, {"port": 5173, "name": "vite"}])})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.td.cleanup()

    def harness(self, provider):
        (self.repo / ".rig" / "harness.toml").write_text(f'[project]\nenabled = true\n\n[sandbox]\nprovider = "{provider}"\n')

    def provider(self):
        return sandbox_provider.provider_for(self.repo)

    def calls(self):
        return self.log.read_text().splitlines() if self.log.exists() else []


class CheckUrl(Base):
    def test_forwarded_localhost_ports_are_allowed(self):
        for url in ("http://localhost:3000/", "http://127.0.0.1:5173/app", "http://localhost:3000/a/b?x=1#y"):
            self.provider().check_url(url)

    def test_an_unforwarded_localhost_port_is_refused_and_says_why(self):
        with self.assertRaises(sandbox_provider.SandboxError) as caught:
            self.provider().check_url("http://localhost:9229/")
        message = str(caught.exception)
        self.assertIn("localhost:9229 is not a forwarded port (forwarded: 3000, 5173)", message)
        self.assertIn("reach the host", message)

    def test_default_ports_and_other_loopback_spellings_are_judged_too(self):
        for url, port in (("https://localhost/", 443), ("http://127.0.0.1/", 80), ("http://0.0.0.0:7000/", 7000),
                          ("http://[::1]:8080/", 8080)):
            with self.assertRaisesRegex(sandbox_provider.SandboxError, f":{port} is not a forwarded port"):
                self.provider().check_url(url)
        os.environ["FAKE_DC_FORWARD"] = json.dumps([{"port": 443}])
        self.provider().check_url("https://localhost/")

    def test_non_local_hosts_are_not_dc_cli_business(self):
        self.provider().check_url("https://example.com/app")
        self.provider().check_url("http://192.168.1.5:9999/")
        self.assertEqual([c for c in self.calls() if c.startswith("forward")], [], "dc is not consulted for remote hosts")

    def test_credentials_in_a_url_are_never_echoed(self):
        with self.assertRaises(sandbox_provider.SandboxError) as caught:
            self.provider().check_url("http://user:hunter2@localhost:9229/?token=abc")
        self.assertNotIn("hunter2", str(caught.exception))
        self.assertNotIn("token=abc", str(caught.exception))

    def test_unlistable_ports_fail_closed(self):
        with mock.patch.dict(os.environ, {"RIG_DC_BIN": str(self.base / "missing-dc")}):
            with self.assertRaises(sandbox_provider.SandboxError):
                sandbox_provider.provider_for(self.repo)
        self.dc.write_text("#!/bin/sh\necho 'no sidecars' >&2\nexit 2\n")
        with self.assertRaisesRegex(sandbox_provider.SandboxError, "cannot list forwarded ports"):
            self.provider().check_url("http://localhost:3000/")
        self.dc.write_text("#!/bin/sh\necho not-json\n")
        with self.assertRaisesRegex(sandbox_provider.SandboxError, "cannot list forwarded ports"):
            self.provider().check_url("http://localhost:3000/")

    def test_malformed_urls_are_refused(self):
        with self.assertRaisesRegex(sandbox_provider.SandboxError, "not a valid URL"):
            self.provider().check_url("http://[::1")
        with self.assertRaisesRegex(sandbox_provider.SandboxError, "not a valid URL"):
            self.provider().check_url("http://localhost:99999999/")

    def test_host_provider_allows_everything_without_calling_dc(self):
        self.harness("host")
        sandbox_provider.check_local_url(self.repo, "http://localhost:9229/")
        self.assertEqual(self.calls(), [])


class Lanes(Base):
    def test_browser_navigate_refuses_before_reaching_bsk(self):
        calls = []
        with mock.patch.object(browser_skill, "_require_effective", return_value=None), \
                mock.patch.object(browser_skill, "invoke_bsk", side_effect=lambda *a, **k: calls.append(a)), \
                mock.patch.object(browser_skill, "persist_receipt"):
            refused = browser_skill.bsk_navigate(self.repo, "http://localhost:9229/")
        self.assertFalse(refused["ok"])
        self.assertIn("not a forwarded port", refused["hint"])
        self.assertEqual(calls, [], "bsk must not be asked to open it")

    def test_browser_navigate_proceeds_for_a_forwarded_port(self):
        with mock.patch.object(browser_skill, "_require_effective", return_value=None), \
                mock.patch.object(browser_skill, "invoke_bsk", return_value={"ok": True}) as invoke, \
                mock.patch.object(browser_skill, "_fail_from_data", return_value=None), \
                mock.patch.object(browser_skill, "_active_session", return_value="s1"), \
                mock.patch.object(browser_skill, "persist_receipt"):
            result = browser_skill.bsk_navigate(self.repo, "http://localhost:3000/")
        self.assertTrue(result["ok"])
        invoke.assert_called_once()

    def test_named_chrome_profile_capture_refuses_an_unforwarded_url(self):
        opened = []
        with mock.patch.object(computer_use, "is_effective", return_value=True):
            refused = computer_use.cu_capture(self.repo, profile_key="work", url="http://localhost:9229/",
                                              profile_opener=lambda *a: opened.append(a) or {"ok": True})
        self.assertEqual((refused["ok"], refused["effect"]), (False, "refused"))
        self.assertIn("not a forwarded port", refused["hint"])
        self.assertEqual(opened, [], "the profile must not be opened")

    def test_host_mode_browser_lane_is_unchanged(self):
        self.harness("host")
        with mock.patch.object(browser_skill, "_require_effective", return_value=None), \
                mock.patch.object(browser_skill, "invoke_bsk", return_value={"ok": True}) as invoke, \
                mock.patch.object(browser_skill, "_fail_from_data", return_value=None), \
                mock.patch.object(browser_skill, "_active_session", return_value="s1"), \
                mock.patch.object(browser_skill, "persist_receipt"):
            self.assertTrue(browser_skill.bsk_navigate(self.repo, "http://localhost:9229/")["ok"])
        invoke.assert_called_once()
        self.assertEqual(self.calls(), [])


class EnvPorts(Base):
    def test_ports_listing_text_and_json(self):
        os.environ["FAKE_DC_FORWARD"] = json.dumps([{"port": 3000, "name": "web"}, {"port": 9229, "held_by": "node"}])
        ports = sandbox_env.forwarded_ports(self.repo)
        self.assertEqual([p["port"] for p in ports], ["3000", "9229"])
        text = sandbox_env.format_ports(ports)
        self.assertEqual(text.splitlines(), ["  localhost:3000  web", "! localhost:9229  held by node"])
        self.assertEqual(sandbox_env.format_ports([]), "no forwarded ports")

    def test_cli(self):
        import subprocess
        script = str(ROOT / "scripts" / "sandbox_env.py")
        done = subprocess.run([sys.executable, script, "ports", "--json", "--repo", str(self.repo)],
                              capture_output=True, text=True, env=dict(os.environ), timeout=60)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual([p["port"] for p in json.loads(done.stdout)], ["3000", "5173"])
        self.dc.write_text("#!/bin/sh\necho 'engine down' >&2\nexit 2\n")
        failed = subprocess.run([sys.executable, script, "ports", "--repo", str(self.repo)],
                                capture_output=True, text=True, env=dict(os.environ), timeout=60)
        self.assertEqual(failed.returncode, 1)
        self.assertIn("rig env:", failed.stderr)


if __name__ == "__main__":
    unittest.main()
