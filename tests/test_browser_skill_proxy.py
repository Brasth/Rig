#!/usr/bin/env python3
"""Parent BSK proxy: real bsk argv, gated tools, child hidden, mocked bsk."""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(ROOT / "scripts"))
import browser_skill
import rig_mcp


def _observe_payload(**overrides):
    data = {
        "ok": True,
        "snapshot_id": "obs-1",
        "screenshot_path": "/tmp/bsk-before.png",
        "outline": "@e1 Submit",
        "refs": [
            {"ref": "@e1", "label": "Submit", "index": 1},
            {"ref": "@e2", "label": "Cancel", "index": 2},
        ],
    }
    data.update(overrides)
    return data


class BrowserSkillProxy(unittest.TestCase):
    def setUp(self):
        browser_skill.reset_snapshots()
        self.td = tempfile.TemporaryDirectory()
        self.repo = Path(self.td.name)
        (self.repo / ".git").mkdir()
        (self.repo / ".rig").mkdir()
        (self.repo / ".rig" / "harness.toml").write_text("[browser-skill]\nenabled = true\n")
        self.machine = mock.patch.object(browser_skill, "machine_state", return_value="on")
        self.binary = mock.patch.object(browser_skill, "binary_path", return_value="/tmp/fake-bsk")
        self.extension = mock.patch.object(browser_skill, "extension_connected", return_value=True)
        self.machine.start()
        self.binary.start()
        self.extension.start()
        self.addCleanup(self.td.cleanup)
        self.addCleanup(self.machine.stop)
        self.addCleanup(self.binary.stop)
        self.addCleanup(self.extension.stop)
        self.addCleanup(browser_skill.reset_snapshots)
        self._repo_env = mock.patch.dict(os.environ, {"RIG_REPO": str(self.repo)}, clear=False)
        self._repo_env.start()
        self.addCleanup(self._repo_env.stop)

    def _runner(self, mapping):
        calls = []

        def runner(binary, args, timeout):
            calls.append((binary, list(args), timeout))
            payload = mapping.get(tuple(args[:2]), mapping.get(tuple(args[:1]), mapping.get(args[0] if args else "")))
            if callable(payload):
                return payload(args)
            if payload is None:
                raise AssertionError(f"unexpected bsk args {args}")
            return dict(payload)

        runner.calls = calls
        return runner

    def _live(self, extra=None):
        mapping = {("session", "start"): {"ok": True, "session_id": "sess-1"}}
        if extra:
            mapping.update(extra)
        return self._runner(mapping)

    def _start(self, runner):
        ev = browser_skill.bsk_session(self.repo, "start", runner=runner)
        self.assertEqual(ev["effect"], "started")
        self.assertEqual(ev["session_id"], "sess-1")
        return ev

    def test_status_always_listed_when_gates_off(self):
        (self.repo / ".rig" / "harness.toml").write_text("[browser-skill]\nenabled = false\n")
        self.assertFalse(browser_skill.tools_listed(self.repo, child=False))
        with mock.patch.object(rig_mcp, "is_child", return_value=False):
            names = {tool["name"] for tool in rig_mcp.listed_tools()}
        self.assertIn("rig_bsk_status", names)
        self.assertNotIn("rig_bsk_observe", names)
        self.assertNotIn("rig_bsk_act", names)
        self.assertNotIn("rig_bsk_confirm", names)
        self.assertNotIn("rig_bsk_session", names)
        self.assertNotIn("rig_bsk_navigate", names)
        self.assertNotIn("rig_bsk_tab", names)

    def test_actions_listed_when_all_gates_on(self):
        self.assertTrue(browser_skill.tools_listed(self.repo, child=False))
        with mock.patch.object(rig_mcp, "is_child", return_value=False):
            names = {tool["name"] for tool in rig_mcp.listed_tools()}
        self.assertIn("rig_bsk_status", names)
        self.assertIn("rig_bsk_session", names)
        self.assertIn("rig_bsk_observe", names)
        self.assertIn("rig_bsk_act", names)
        self.assertIn("rig_bsk_confirm", names)
        self.assertIn("rig_bsk_navigate", names)
        self.assertIn("rig_bsk_tab", names)

    def test_actions_hidden_when_machine_off(self):
        self.machine.stop()
        with mock.patch.object(browser_skill, "machine_state", return_value="declined"):
            self.assertFalse(browser_skill.tools_listed(self.repo, child=False))
        self.machine.start()

    def test_actions_hidden_when_no_binary(self):
        self.binary.stop()
        with mock.patch.object(browser_skill, "binary_path", return_value=""):
            self.assertFalse(browser_skill.tools_listed(self.repo, child=False))
        self.binary.start()

    def test_actions_hidden_when_extension_off(self):
        self.extension.stop()
        with mock.patch.object(browser_skill, "extension_connected", return_value=False):
            self.assertFalse(browser_skill.tools_listed(self.repo, child=False))
            self.assertEqual(
                browser_skill.effective_state("on", True, "true", False),
                "off (extension)",
            )
        self.extension.start()

    def test_child_never_lists_bsk_tools(self):
        self.assertFalse(browser_skill.tools_listed(self.repo, child=True))
        with mock.patch.object(rig_mcp, "is_child", return_value=True):
            names = {tool["name"] for tool in rig_mcp.listed_tools()}
        self.assertFalse(any(name.startswith("rig_bsk_") for name in names))
        self.assertNotIn("bsk", names)

    def test_observe_act_confirm_writes_receipt(self):
        runner = self._live({
            "observe": _observe_payload(),
            "click": {"ok": True, "effect": "unverifiable", "screenshot_path": "/tmp/bsk-after.png"},
        })
        self._start(runner)
        obs = browser_skill.bsk_observe(self.repo, runner=runner)
        self.assertTrue(obs["ok"])
        self.assertEqual(obs["effect"], "observed")
        self.assertEqual(obs["snapshot_id"], "obs-1")
        self.assertEqual(obs["refs"][0]["ref"], "@e1")
        self.assertIn("Child must not click", obs["brief_block"])
        self.assertEqual(obs["receipt"]["version"], "rig.bsk.v1")
        act = browser_skill.bsk_act(
            self.repo, snapshot_id=obs["snapshot_id"], ref="@e1", action="click", runner=runner,
        )
        self.assertEqual(act["effect"], "unverifiable")
        self.assertEqual(act["addressed"]["ref"], "@e1")
        confirm = browser_skill.bsk_confirm(self.repo, snapshot_id=obs["snapshot_id"], runner=runner)
        self.assertEqual(confirm["effect"], "confirmed")
        self.assertEqual(confirm["receipt"]["status"], "confirmed")
        self.assertIn("Child must not click", confirm["brief_block"])
        evidence = self.repo / ".rig" / "bsk-evidence"
        self.assertTrue(evidence.is_dir())
        files = list(evidence.glob("*.json"))
        self.assertGreaterEqual(len(files), 3)
        blob = "\n".join(path.read_text() for path in files)
        self.assertIn("rig.bsk.v1", blob)
        self.assertIn("Child must not click", blob)
        click = [c for c in runner.calls if c[1][:1] == ["click"]]
        self.assertEqual(len(click), 1)
        self.assertIn("@e1", click[0][1])
        self.assertIn("--session", click[0][1])
        self.assertIn("sess-1", click[0][1])
        self.assertFalse(any(c[1][:1] == ["act"] for c in runner.calls))
        observe = [c for c in runner.calls if c[1][:1] == ["observe"]]
        self.assertTrue(observe)
        self.assertIn("--session", observe[0][1])

    def test_second_act_is_stale_and_does_not_call_bsk(self):
        runner = self._live({
            "observe": _observe_payload(),
            "click": {"ok": True, "effect": "unverifiable"},
        })
        self._start(runner)
        obs = browser_skill.bsk_observe(self.repo, runner=runner)
        browser_skill.bsk_act(
            self.repo, snapshot_id=obs["snapshot_id"], ref="@e1", action="click", runner=runner,
        )
        again = browser_skill.bsk_act(
            self.repo, snapshot_id=obs["snapshot_id"], ref="@e1", action="click", runner=runner,
        )
        self.assertEqual(again["effect"], "stale")
        self.assertEqual(len([c for c in runner.calls if c[1][:1] == ["click"]]), 1)

    def test_evaluate_is_refused(self):
        runner = self._live({"observe": _observe_payload()})
        self._start(runner)
        obs = browser_skill.bsk_observe(self.repo, runner=runner)
        ev = browser_skill.bsk_act(
            self.repo, snapshot_id=obs["snapshot_id"], ref="@e1", action="evaluate", runner=runner,
        )
        self.assertEqual(ev["effect"], "refused")
        self.assertFalse(any("evaluate" in c[1] for c in runner.calls))

    def test_invoke_never_allows_install_skill_or_unattended(self):
        runner = self._runner({})
        refused = browser_skill.invoke_bsk(["install-skill"], runner=runner)
        self.assertEqual(refused["effect"], "refused")
        refused = browser_skill.invoke_bsk(["session", "start", "--unattended"], runner=runner)
        self.assertEqual(refused["effect"], "refused")
        refused = browser_skill.invoke_bsk(["tab", "borrow", "t1", "--no-confirm"], runner=runner)
        self.assertEqual(refused["effect"], "refused")
        self.assertEqual(runner.calls, [])

    def test_session_start_retains_bsk_id_and_stop_is_positional(self):
        runner = self._live({("session", "stop"): {"ok": True}})
        ev = self._start(runner)
        args = runner.calls[0][1]
        self.assertEqual(args[:2], ["session", "start"])
        self.assertIn("--json", args)
        self.assertIn("--no-focus", args)
        self.assertNotIn("--session", args)
        self.assertFalse(str(ev["session_id"]).startswith("rig-bsk-"))
        stopped = browser_skill.bsk_session(self.repo, "stop", runner=runner)
        self.assertEqual(stopped["effect"], "stopped")
        stop_args = runner.calls[-1][1]
        self.assertEqual(stop_args, ["session", "stop", "sess-1"])

    def test_session_start_no_focus_is_optional(self):
        runner = self._live()
        ev = browser_skill.bsk_session(self.repo, "start", no_focus=False, runner=runner)
        self.assertEqual(ev["effect"], "started")
        args = runner.calls[0][1]
        self.assertEqual(args[:3], ["session", "start", "--json"])
        self.assertNotIn("--no-focus", args)

    def test_effective_off_does_not_call_bsk(self):
        (self.repo / ".rig" / "harness.toml").write_text("[browser-skill]\nenabled = false\n")
        runner = self._runner({})
        ev = browser_skill.bsk_observe(self.repo, runner=runner)
        self.assertEqual(ev["effect"], "hidden")
        self.assertFalse(ev["effective"])
        self.assertEqual(runner.calls, [])
        self.assertFalse(browser_skill.tools_listed(self.repo, child=False))

    def test_status_never_installs(self):
        with mock.patch.object(browser_skill.bsk_install, "main") as install, \
             mock.patch.object(browser_skill, "set_project_enabled") as enable:
            status = browser_skill.bsk_status(self.repo)
        install.assert_not_called()
        enable.assert_not_called()
        self.assertTrue(status["effective"])
        self.assertIn("Never run bsk install-skill", status["note"])

    def test_nonempty_browsers_is_connected(self):
        self.extension.stop()
        runner = self._runner({"status": {"ok": True, "browsers": [{"id": "chrome"}]}})
        self.assertTrue(browser_skill.extension_connected(runner=runner, binary="/tmp/fake-bsk"))
        self.extension.start()

    def test_empty_browsers_is_disconnected(self):
        self.extension.stop()
        runner = self._runner({"status": {"ok": True, "browsers": [], "connected": True}})
        self.assertFalse(browser_skill.extension_connected(runner=runner, binary="/tmp/fake-bsk"))
        self.extension.start()

    def test_session_failures_do_not_store_id(self):
        cases = [
            {"ok": False, "hint": "denied"},
            {"ok": True},
            {"ok": False, "malformed": True, "hint": "malformed bsk output"},
            {"ok": True, "exit_code": 3, "session_id": "should-not-keep"},
        ]
        for payload in cases:
            with self.subTest(payload=payload):
                browser_skill.reset_snapshots()
                runner = self._runner({("session", "start"): payload})
                ev = browser_skill.bsk_session(self.repo, "start", runner=runner)
                self.assertFalse(ev["ok"])
                self.assertNotEqual(ev["effect"], "started")
                self.assertEqual(browser_skill._SESSION["id"], "")

    def test_timeout_and_oserror_are_error_receipts(self):
        def timeout_runner(binary, args, timeout):
            timeout_runner.calls.append((binary, list(args), timeout))
            raise subprocess.TimeoutExpired("bsk", timeout)
        timeout_runner.calls = []
        ev = browser_skill.bsk_session(self.repo, "start", runner=timeout_runner)
        self.assertFalse(ev["ok"])
        self.assertEqual(ev["effect"], "error")
        self.assertIn("timed out", ev["hint"])
        self.assertEqual(browser_skill._SESSION["id"], "")

        def os_runner(binary, args, timeout):
            os_runner.calls.append((binary, list(args), timeout))
            raise OSError("boom")
        os_runner.calls = []
        ev = browser_skill.bsk_session(self.repo, "start", runner=os_runner)
        self.assertFalse(ev["ok"])
        self.assertEqual(ev["effect"], "error")
        self.assertIn("boom", ev["hint"])
        self.assertEqual(browser_skill._SESSION["id"], "")

    def test_observe_failure_does_not_store_snapshot(self):
        runner = self._live({"observe": {"ok": False, "exit_code": 1, "hint": "nope"}})
        self._start(runner)
        ev = browser_skill.bsk_observe(self.repo, runner=runner)
        self.assertFalse(ev["ok"])
        self.assertEqual(browser_skill._SNAPSHOTS, {})

    def test_act_failure_does_not_consume_snapshot(self):
        runner = self._live({
            "observe": _observe_payload(),
            "click": {"ok": False, "hint": "miss"},
        })
        self._start(runner)
        obs = browser_skill.bsk_observe(self.repo, runner=runner)
        failed = browser_skill.bsk_act(
            self.repo, snapshot_id=obs["snapshot_id"], ref="@e1", action="click", runner=runner,
        )
        self.assertFalse(failed["ok"])
        self.assertEqual(failed["effect"], "error")
        self.assertEqual(browser_skill._SNAPSHOTS[obs["snapshot_id"]]["phase"], browser_skill.PHASE_FRESH)
        runner.mapping_override = True
        # second attempt still reaches bsk because snapshot stayed fresh
        again_calls = len([c for c in runner.calls if c[1][:1] == ["click"]])
        browser_skill.bsk_act(
            self.repo, snapshot_id=obs["snapshot_id"], ref="@e1", action="click", runner=runner,
        )
        self.assertEqual(len([c for c in runner.calls if c[1][:1] == ["click"]]), again_calls + 1)

    def test_fill_and_press_argv(self):
        runner = self._live({
            "observe": _observe_payload(),
            "fill": {"ok": True, "effect": "unverifiable"},
            "press": {"ok": True, "effect": "unverifiable"},
        })
        self._start(runner)
        obs = browser_skill.bsk_observe(self.repo, runner=runner)
        browser_skill.bsk_act(
            self.repo, snapshot_id=obs["snapshot_id"], ref="@e1", action="fill",
            text="hello", runner=runner,
        )
        fill = [c[1] for c in runner.calls if c[1][:1] == ["fill"]][0]
        self.assertEqual(fill[:2], ["fill", "@e1"])
        self.assertIn("--value", fill)
        self.assertIn("hello", fill)
        self.assertIn("--session", fill)
        browser_skill.reset_snapshots()
        runner = self._live({
            "observe": _observe_payload(),
            "press": {"ok": True, "effect": "unverifiable"},
        })
        self._start(runner)
        obs = browser_skill.bsk_observe(self.repo, runner=runner)
        browser_skill.bsk_act(
            self.repo, snapshot_id=obs["snapshot_id"], ref="@e1", action="press",
            key="Enter", runner=runner,
        )
        press = [c[1] for c in runner.calls if c[1][:1] == ["press"]][0]
        self.assertEqual(press[:2], ["press", "Enter"])
        self.assertIn("--ref", press)
        self.assertIn("@e1", press)

    def test_navigate_and_tab_boundaries(self):
        runner = self._live({
            "navigate": {"ok": True},
            ("tab", "list"): {"ok": True, "tabs": [{"id": "t1"}]},
            ("tab", "borrow"): {"ok": True},
            ("tab", "return"): {"ok": True},
            "observe": _observe_payload(),
        })
        self._start(runner)
        obs = browser_skill.bsk_observe(self.repo, runner=runner)
        self.assertIn(obs["snapshot_id"], browser_skill._SNAPSHOTS)
        nav = browser_skill.bsk_navigate(self.repo, url="https://example.com", runner=runner)
        self.assertEqual(nav["effect"], "navigated")
        self.assertEqual(browser_skill._SNAPSHOTS, {})
        nav_args = [c[1] for c in runner.calls if c[1][:1] == ["navigate"]][0]
        self.assertEqual(nav_args[:2], ["navigate", "https://example.com"])
        self.assertIn("--session", nav_args)
        listed = browser_skill.bsk_tab(self.repo, action="list", runner=runner)
        self.assertEqual(listed["effect"], "listed")
        borrowed = browser_skill.bsk_tab(self.repo, action="borrow", tab_id="t1", runner=runner)
        self.assertEqual(borrowed["effect"], "borrowed")
        returned = browser_skill.bsk_tab(self.repo, action="return", tab_id="t1", runner=runner)
        self.assertEqual(returned["effect"], "returned")
        list_args = [c[1] for c in runner.calls if c[1][:2] == ["tab", "list"]][0]
        self.assertIn("--scope", list_args)
        self.assertIn("user", list_args)
        self.assertIn("--session", list_args)
        borrow_args = [c[1] for c in runner.calls if c[1][:2] == ["tab", "borrow"]][0]
        self.assertEqual(borrow_args[:3], ["tab", "borrow", "t1"])
        self.assertNotIn("--no-confirm", borrow_args)
        self.assertNotIn("--unattended", borrow_args)
        missing = browser_skill.bsk_navigate(self.repo, url="", runner=runner)
        self.assertFalse(missing["ok"])
        self.assertIn("url", missing["hint"])

    def test_navigate_failure_keeps_snapshots(self):
        runner = self._live({
            "observe": _observe_payload(),
            "navigate": {"ok": False, "hint": "blocked"},
        })
        self._start(runner)
        obs = browser_skill.bsk_observe(self.repo, runner=runner)
        sid = obs["snapshot_id"]
        ev = browser_skill.bsk_navigate(self.repo, url="https://example.com", runner=runner)
        self.assertFalse(ev["ok"])
        self.assertIn(sid, browser_skill._SNAPSHOTS)

    def test_observe_screenshot_fallback_uses_json_and_session(self):
        runner = self._live({
            "observe": {"ok": True, "outline": "@e1 Submit", "refs": [{"ref": "@e1", "label": "Submit", "index": 1}]},
            "screenshot": {"ok": True, "raw": "/tmp/bsk-from-raw.png"},
        })
        self._start(runner)
        obs = browser_skill.bsk_observe(self.repo, runner=runner)
        self.assertTrue(obs["ok"])
        self.assertEqual(obs["after_png"], "/tmp/bsk-from-raw.png")
        shot = [c[1] for c in runner.calls if c[1][:1] == ["screenshot"]][0]
        self.assertIn("--json", shot)
        self.assertIn("--session", shot)
        self.assertIn("sess-1", shot)
        self.assertFalse(any(c[1][:1] == ["act"] for c in runner.calls))

    def test_mcp_navigate_tab_and_child_isolation(self):
        nav_ev = browser_skill.empty_evidence(ok=True, effective=True, action="navigate", effect="navigated")
        tab_ev = browser_skill.empty_evidence(ok=True, effective=True, action="tab", effect="listed")
        sess_ev = browser_skill.empty_evidence(ok=True, effective=True, action="session", effect="started")
        with mock.patch.object(rig_mcp, "is_child", return_value=False), \
             mock.patch.object(browser_skill, "bsk_navigate", return_value=nav_ev) as nav, \
             mock.patch.object(browser_skill, "bsk_tab", return_value=tab_ev) as tab, \
             mock.patch.object(browser_skill, "bsk_session", return_value=sess_ev) as sess:
            out = rig_mcp.call_tool("rig_bsk_navigate", {"repo": str(self.repo), "url": "https://example.com"})
            listed = rig_mcp.call_tool("rig_bsk_tab", {"repo": str(self.repo), "action": "list"})
            started = rig_mcp.call_tool(
                "rig_bsk_session",
                {"repo": str(self.repo), "action": "start", "no_focus": False},
            )
        self.assertEqual(out["structuredContent"]["effect"], "navigated")
        self.assertEqual(listed["structuredContent"]["effect"], "listed")
        self.assertEqual(started["structuredContent"]["effect"], "started")
        nav.assert_called_once()
        tab.assert_called_once()
        self.assertEqual(sess.call_args.kwargs.get("no_focus"), False)
        with mock.patch.object(rig_mcp, "is_child", return_value=True):
            for name in ("rig_bsk_navigate", "rig_bsk_tab", "rig_bsk_observe", "rig_bsk_act"):
                err = rig_mcp.call_tool(name, {"repo": str(self.repo), "url": "https://example.com"})
                self.assertTrue(err.get("isError"))
                self.assertIn("not a child tool", err["content"][0]["text"])


if __name__ == "__main__":
    unittest.main()
