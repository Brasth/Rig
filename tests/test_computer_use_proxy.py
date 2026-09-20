#!/usr/bin/env python3
"""Parent CU proxy: evidence JSON, stale tokens, effective-off, mocked Driver."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(ROOT / "scripts"))
import computer_use


def _capture_payload(**overrides):
    data = {
        "ok": True,
        "snapshot_id": "drv-1",
        "pid": 844,
        "window_id": 10725,
        "screenshot_path": "/tmp/before.png",
        "elements": [
            {"index": 1, "role": "AXButton", "label": "1", "element_token": "tok-1"},
            {"index": 2, "role": "AXButton", "label": "2", "element_token": "tok-2"},
        ],
    }
    data.update(overrides)
    return data


class ComputerUseProxy(unittest.TestCase):
    def setUp(self):
        computer_use.reset_snapshots()
        self.td = tempfile.TemporaryDirectory()
        self.repo = Path(self.td.name)
        (self.repo / ".git").mkdir()
        (self.repo / ".rig").mkdir()
        (self.repo / ".rig" / "harness.toml").write_text("[computer-use]\nenabled = true\n")
        self.machine = mock.patch.object(computer_use, "machine_state", return_value="on")
        self.binary = mock.patch.object(computer_use, "binary_path", return_value="/tmp/fake-cua-driver")
        self.machine.start()
        self.binary.start()
        self.addCleanup(self.td.cleanup)
        self.addCleanup(self.machine.stop)
        self.addCleanup(self.binary.stop)
        self.addCleanup(computer_use.reset_snapshots)

    def _runner(self, mapping):
        calls = []

        def runner(binary, args, timeout):
            calls.append((binary, list(args), timeout))
            tool = args[0]
            payload = mapping.get(tool)
            if callable(payload):
                return payload(args)
            if payload is None:
                raise AssertionError(f"unexpected driver tool {tool}")
            return dict(payload)

        runner.calls = calls
        return runner

    def test_status_ready_and_setup_use_rig_proxy_without_raw_wiring(self):
        self.assertTrue(computer_use.cu_status(self.repo)["effective"])
        with mock.patch.object(computer_use.cua_install, "main"), \
             mock.patch.object(computer_use, "wire_parent_mcp") as wire, \
             mock.patch.object(computer_use, "install_skill_pack") as skills, \
             mock.patch("builtins.print") as output:
            computer_use.cmd_setup(self.repo)
        wire.assert_not_called()
        skills.assert_not_called()
        text = "\n".join(str(call.args[0]) for call in output.call_args_list)
        self.assertIn("rig_cu_status", text)
        self.assertNotIn("use cua-driver call", text)

    def test_capture_returns_snapshot_tokens_and_screenshot(self):
        runner = self._runner({"get_window_state": _capture_payload()})
        ev = computer_use.cu_capture(self.repo, pid=844, window_id=10725, runner=runner)
        self.assertTrue(ev["ok"])
        self.assertTrue(ev["effective"])
        self.assertEqual(ev["action"], "capture")
        self.assertEqual(ev["snapshot_id"], "drv-1")
        self.assertEqual(ev["effect"], "captured")
        self.assertEqual(ev["after_png"], "/tmp/before.png")
        self.assertEqual(len(ev["elements"]), 2)
        self.assertEqual(ev["elements"][0]["element_token"], "tok-1")
        self.assertIn("Child must not click", ev["brief_block"])
        self.assertEqual(runner.calls[0][1][0], "get_window_state")

    def test_act_fresh_token_invokes_click(self):
        runner = self._runner({
            "get_window_state": _capture_payload(),
            "click": {"ok": True, "effect": "unverifiable", "screenshot_path": "/tmp/after.png"},
        })
        cap = computer_use.cu_capture(self.repo, runner=runner)
        ev = computer_use.cu_act(
            self.repo, snapshot_id=cap["snapshot_id"], element_token="tok-1",
            action="click", runner=runner,
        )
        self.assertEqual(ev["effect"], "unverifiable")
        self.assertEqual(ev["addressed"]["element_token"], "tok-1")
        self.assertEqual(ev["addressed"]["label"], "1")
        self.assertEqual(ev["addressed"]["index"], 1)
        click = [c for c in runner.calls if c[1][0] == "click"]
        self.assertEqual(len(click), 1)
        body = json.loads(click[0][1][1])
        self.assertEqual(body["element_token"], "tok-1")

    def test_act_without_token_is_stale_and_does_not_click(self):
        runner = self._runner({"get_window_state": _capture_payload()})
        cap = computer_use.cu_capture(self.repo, runner=runner)
        ev = computer_use.cu_act(
            self.repo, snapshot_id=cap["snapshot_id"], element_token="",
            action="click", runner=runner,
        )
        self.assertEqual(ev["effect"], "stale")
        self.assertFalse(any(c[1][0] == "click" for c in runner.calls))

    def test_act_unknown_token_is_stale(self):
        runner = self._runner({"get_window_state": _capture_payload()})
        cap = computer_use.cu_capture(self.repo, runner=runner)
        ev = computer_use.cu_act(
            self.repo, snapshot_id=cap["snapshot_id"], element_token="nope",
            action="click", runner=runner,
        )
        self.assertEqual(ev["effect"], "stale")
        self.assertFalse(any(c[1][0] == "click" for c in runner.calls))

    def test_confirm_maps_driver_escalate(self):
        runner = self._runner({
            "get_window_state": _capture_payload(),
            "click": {"ok": True, "effect": "escalate_foreground"},
        })
        cap = computer_use.cu_capture(self.repo, runner=runner)
        computer_use.cu_act(
            self.repo, snapshot_id=cap["snapshot_id"], element_token="tok-1",
            action="click", runner=runner,
        )
        ev = computer_use.cu_confirm(self.repo, snapshot_id=cap["snapshot_id"], runner=runner)
        self.assertEqual(ev["action"], "confirm")
        self.assertEqual(ev["effect"], "escalate_foreground")
        self.assertIn("escalate_foreground", ev["hint"])

    def test_effective_off_does_not_call_driver(self):
        (self.repo / ".rig" / "harness.toml").write_text("[computer-use]\nenabled = false\n")
        runner = self._runner({})
        ev = computer_use.cu_capture(self.repo, runner=runner)
        self.assertEqual(ev["effect"], "hidden")
        self.assertFalse(ev["effective"])
        self.assertEqual(runner.calls, [])
        self.assertFalse(computer_use.tools_listed(self.repo, child=False))

    def test_tools_hidden_for_child_even_when_effective(self):
        self.assertTrue(computer_use.tools_listed(self.repo, child=False))
        self.assertFalse(computer_use.tools_listed(self.repo, child=True))

    def test_type_secret_is_refused(self):
        runner = self._runner({"get_window_state": _capture_payload()})
        cap = computer_use.cu_capture(self.repo, runner=runner)
        ev = computer_use.cu_act(
            self.repo, snapshot_id=cap["snapshot_id"], element_token="tok-1",
            action="type", text="my password is hunter2", runner=runner,
        )
        self.assertEqual(ev["effect"], "refused")
        self.assertFalse(any(c[1][0] == "type" for c in runner.calls))
        self.assertFalse(any(c[1][0] == "type_text" for c in runner.calls))

    def test_capture_parses_driver_element_index_and_structured_content(self):
        runner = self._runner({
            "get_window_state": {
                "ok": True,
                "structuredContent": {
                    "snapshot_id": "s0000002a",
                    "pid": 844,
                    "window_id": 10725,
                    "screenshot_file_path": "/tmp/before.png",
                    "elements": [{
                        "element_index": 14,
                        "role": "AXButton",
                        "label": "1",
                        "element_token": "s0000002a:14",
                    }],
                },
            },
        })
        ev = computer_use.cu_capture(self.repo, pid=844, window_id=10725, runner=runner)
        self.assertEqual(ev["snapshot_id"], "s0000002a")
        self.assertEqual(ev["elements"][0]["index"], 14)
        self.assertEqual(ev["elements"][0]["element_token"], "s0000002a:14")
        self.assertEqual(ev["after_png"], "/tmp/before.png")

    def test_act_type_invokes_type_text(self):
        runner = self._runner({
            "get_window_state": _capture_payload(),
            "type_text": {"ok": True, "effect": "unverifiable"},
        })
        cap = computer_use.cu_capture(self.repo, runner=runner)
        ev = computer_use.cu_act(
            self.repo, snapshot_id=cap["snapshot_id"], element_token="tok-1",
            action="type", text="hello", runner=runner,
        )
        self.assertEqual(ev["effect"], "unverifiable")
        self.assertTrue(any(c[1][0] == "type_text" for c in runner.calls))
        self.assertFalse(any(c[1][0] == "type" for c in runner.calls))

    def test_act_key_invokes_press_key(self):
        runner = self._runner({
            "get_window_state": _capture_payload(),
            "press_key": {"ok": True, "effect": "unverifiable"},
        })
        cap = computer_use.cu_capture(self.repo, runner=runner)
        ev = computer_use.cu_act(
            self.repo, snapshot_id=cap["snapshot_id"], element_token="tok-1",
            action="key", key="return", runner=runner,
        )
        self.assertEqual(ev["effect"], "unverifiable")
        self.assertTrue(any(c[1][0] == "press_key" for c in runner.calls))

    def test_act_maps_escalation_target_pixel(self):
        runner = self._runner({
            "get_window_state": _capture_payload(),
            "click": {
                "ok": True,
                "effect": "unverifiable",
                "escalation": {"target": "pixel"},
            },
        })
        cap = computer_use.cu_capture(self.repo, runner=runner)
        ev = computer_use.cu_act(
            self.repo, snapshot_id=cap["snapshot_id"], element_token="tok-1",
            action="click", runner=runner,
        )
        self.assertEqual(ev["effect"], "escalate_px")

    def test_capture_launches_when_pid_missing(self):
        runner = self._runner({
            "launch_app": {
                "ok": True,
                "pid": 844,
                "windows": [{"window_id": 10725}],
            },
            "get_window_state": _capture_payload(),
        })
        ev = computer_use.cu_capture(
            self.repo, bundle_id="com.apple.calculator", runner=runner,
        )
        self.assertTrue(ev["ok"])
        self.assertEqual(ev["pid"], 844)
        self.assertEqual(ev["window_id"], 10725)
        self.assertTrue(any(c[1][0] == "launch_app" for c in runner.calls))
        body = json.loads([c for c in runner.calls if c[1][0] == "launch_app"][0][1][1])
        self.assertEqual(body["bundle_id"], "com.apple.calculator")
        gws = json.loads([c for c in runner.calls if c[1][0] == "get_window_state"][0][1][1])
        self.assertEqual(gws["window_id"], 10725)
        self.assertEqual(gws["pid"], 844)

    def test_capture_lists_windows_when_launch_omits_window_id(self):
        runner = self._runner({
            "launch_app": {"ok": True, "pid": 844, "windows": []},
            "list_windows": {
                "ok": True,
                "windows": [{"window_id": 1325, "is_on_screen": True, "on_current_space": True}],
            },
            "get_window_state": _capture_payload(window_id=1325),
        })
        ev = computer_use.cu_capture(
            self.repo, bundle_id="com.apple.calculator", runner=runner,
        )
        self.assertTrue(ev["ok"])
        self.assertEqual(ev["window_id"], 1325)
        self.assertTrue(any(c[1][0] == "list_windows" for c in runner.calls))
        gws = json.loads([c for c in runner.calls if c[1][0] == "get_window_state"][0][1][1])
        self.assertEqual(gws["window_id"], 1325)

    def test_capture_reads_window_id_from_launch_raw_json(self):
        runner = self._runner({
            "launch_app": {
                "ok": True,
                "pid": 844,
                "raw": json.dumps({"pid": 844, "windows": [{"window_id": 1325, "is_on_screen": True}]}),
            },
            "get_window_state": _capture_payload(window_id=1325),
        })
        ev = computer_use.cu_capture(
            self.repo, bundle_id="com.apple.calculator", runner=runner,
        )
        self.assertEqual(ev["window_id"], 1325)
        self.assertFalse(any(c[1][0] == "list_windows" for c in runner.calls))
        gws = json.loads([c for c in runner.calls if c[1][0] == "get_window_state"][0][1][1])
        self.assertEqual(gws["window_id"], 1325)

    def test_capture_refuses_window_id_zero(self):
        runner = self._runner({
            "launch_app": {"ok": True, "pid": 844, "windows": []},
            "list_windows": {"ok": True, "windows": []},
        })
        ev = computer_use.cu_capture(
            self.repo, bundle_id="com.apple.calculator", runner=runner,
        )
        self.assertFalse(ev["ok"])
        self.assertEqual(ev["effect"], "unverifiable")
        self.assertEqual(ev["window_id"], 0)
        self.assertFalse(any(c[1][0] == "get_window_state" for c in runner.calls))
        self.assertIn("window_id", ev["hint"])

    def test_permissions_pending_is_refused(self):
        runner = self._runner({
            "launch_app": {
                "ok": False,
                "raw": "permissions_pending: macOS Accessibility or Screen Recording permission is still pending",
            },
        })
        ev = computer_use.cu_capture(
            self.repo, bundle_id="com.apple.calculator", runner=runner,
        )
        self.assertEqual(ev["effect"], "refused")
        self.assertFalse(ev["ok"])
        self.assertIn("permissions grant", ev["hint"])

    def test_act_token_does_not_send_xy(self):
        runner = self._runner({
            "get_window_state": _capture_payload(screenshot_width=200, screenshot_height=100),
            "click": {"ok": True, "effect": "unverifiable"},
        })
        cap = computer_use.cu_capture(self.repo, pid=844, window_id=10725, runner=runner)
        ev = computer_use.cu_act(
            self.repo, snapshot_id=cap["snapshot_id"], element_token="tok-1",
            action="click", runner=runner,
        )
        self.assertEqual(ev["addressed"]["kind"], "ax")
        body = json.loads([c for c in runner.calls if c[1][0] == "click"][0][1][1])
        self.assertEqual(body["element_token"], "tok-1")
        self.assertNotIn("x", body)
        self.assertNotIn("y", body)

    def test_xy_on_healthy_snapshot_does_not_click(self):
        runner = self._runner({
            "get_window_state": _capture_payload(screenshot_width=200, screenshot_height=100),
        })
        cap = computer_use.cu_capture(self.repo, pid=844, window_id=10725, runner=runner)
        ev = computer_use.cu_act(
            self.repo, snapshot_id=cap["snapshot_id"], action="click", x=10, y=20, runner=runner,
        )
        self.assertEqual(ev["effect"], "stale")
        self.assertIn("escalate_px", ev["hint"])
        self.assertFalse(any(c[1][0] == "click" for c in runner.calls))

    def test_degraded_snapshot_allows_xy_click(self):
        runner = self._runner({
            "get_window_state": _capture_payload(
                degraded=True, elements=[], screenshot_width=200, screenshot_height=100,
            ),
            "click": {"ok": True, "effect": "unverifiable"},
        })
        cap = computer_use.cu_capture(self.repo, pid=844, window_id=10725, runner=runner)
        ev = computer_use.cu_act(
            self.repo, snapshot_id=cap["snapshot_id"], action="click", x=10, y=20, runner=runner,
        )
        self.assertEqual(ev["addressed"]["kind"], "px")
        self.assertEqual(ev["addressed"]["x"], 10)
        self.assertEqual(ev["addressed"]["y"], 20)
        self.assertIn("kind=px", ev["brief_block"])
        body = json.loads([c for c in runner.calls if c[1][0] == "click"][0][1][1])
        self.assertEqual(body["x"], 10)
        self.assertEqual(body["y"], 20)
        self.assertNotIn("element_token", body)

    def test_token_and_xy_are_refused(self):
        runner = self._runner({
            "get_window_state": _capture_payload(degraded=True, screenshot_width=200, screenshot_height=100),
        })
        cap = computer_use.cu_capture(self.repo, pid=844, window_id=10725, runner=runner)
        ev = computer_use.cu_act(
            self.repo, snapshot_id=cap["snapshot_id"], element_token="tok-1",
            action="click", x=10, y=20, runner=runner,
        )
        self.assertEqual(ev["effect"], "refused")
        self.assertFalse(any(c[1][0] == "click" for c in runner.calls))

    def test_xy_out_of_bounds_is_refused(self):
        runner = self._runner({
            "get_window_state": _capture_payload(
                degraded=True, elements=[], screenshot_width=200, screenshot_height=100,
            ),
        })
        cap = computer_use.cu_capture(self.repo, pid=844, window_id=10725, runner=runner)
        ev = computer_use.cu_act(
            self.repo, snapshot_id=cap["snapshot_id"], action="click", x=500, y=20, runner=runner,
        )
        self.assertEqual(ev["effect"], "refused")
        self.assertIn("bounds", ev["hint"])
        self.assertFalse(any(c[1][0] == "click" for c in runner.calls))

    def test_escalate_px_confirm_allows_xy_on_new_snapshot(self):
        runner = self._runner({
            "get_window_state": _capture_payload(screenshot_width=200, screenshot_height=100),
            "click": {"ok": True, "escalation": {"target": "pixel"}},
        })
        cap = computer_use.cu_capture(self.repo, pid=844, window_id=10725, runner=runner)
        computer_use.cu_act(
            self.repo, snapshot_id=cap["snapshot_id"], element_token="tok-1",
            action="click", runner=runner,
        )
        confirmed = computer_use.cu_confirm(self.repo, snapshot_id=cap["snapshot_id"], runner=runner)
        ev = computer_use.cu_act(
            self.repo, snapshot_id=confirmed["snapshot_id"], action="click",
            x=12, y=18, runner=runner,
        )
        self.assertEqual(ev["addressed"]["kind"], "px")
        clicks = [c for c in runner.calls if c[1][0] == "click"]
        self.assertGreaterEqual(len(clicks), 2)
        body = json.loads(clicks[-1][1][1])
        self.assertEqual(body["x"], 12)
        self.assertNotIn("element_token", body)

    def _profile_opener(self, key="work", selector="cdp-open=abc"):
        def opener(profile_key, url):
            return {
                "ok": True,
                "profile_key": profile_key or key,
                "bind_selector": selector,
                "opened_url": f"{url}#{selector}",
            }
        return opener

    def test_profile_key_without_grant_is_refused(self):
        def gbs(args):
            return {
                "ok": False,
                "status": "browser_requires_setup",
                "name": "browser_requires_setup",
            }
        runner = self._runner({
            "launch_app": {
                "ok": True, "pid": 4242,
                "windows": [{"window_id": 991, "is_on_screen": True}],
            },
            "get_browser_state": gbs,
            "browser_prepare": {"ok": False, "error": "existing-profile grant missing"},
        })
        ev = computer_use.cu_capture(
            self.repo, profile_key="work", url="https://www.figma.com/design/abc",
            runner=runner, profile_opener=self._profile_opener(),
        )
        self.assertEqual(ev["effect"], "refused")
        self.assertIn("cua-driver serve --grant existing-profile", ev["hint"])
        self.assertTrue(any(c[1][0] == "browser_prepare" for c in runner.calls))
        prepare = json.loads([c for c in runner.calls if c[1][0] == "browser_prepare"][0][1][1])
        self.assertEqual(prepare["strategy"]["kind"], "existing_profile")
        self.assertFalse(any(
            c[1][0] == "get_browser_state" and "semantic_v2" in c[1][1]
            for c in runner.calls
        ))

    def test_named_profile_capture_returns_viewport_outline(self):
        def gbs(args):
            payload = json.loads(args[1])
            if payload.get("snapshot_format") == "semantic_v2":
                return {
                    "ok": True,
                    "snapshot_id": "b-1",
                    "outline": "Figma\nFile name",
                    "refs": [{"ref": "p1:2", "name": "Layers", "actions": ["click"]}],
                    "screenshot_path": "/tmp/figma.png",
                    "screenshot": {
                        "coordinate_space": "viewport_css_px",
                        "pixel_to_css_scale_x": 0.5,
                        "pixel_to_css_scale_y": 0.5,
                        "width": 800,
                        "height": 600,
                    },
                    "screenshot_width": 800,
                    "screenshot_height": 600,
                }
            return {
                "ok": True,
                "status": "ok",
                "binding_quality": "exact",
                "mutation_allowed": True,
                "tabs": [{
                    "target_id": "tgt-1",
                    "tab_id": "tab-1",
                    "url": "https://www.figma.com/design/abc#cdp-open=abc",
                }],
            }
        runner = self._runner({
            "launch_app": {
                "ok": True, "pid": 4242,
                "windows": [{"window_id": 991, "is_on_screen": True}],
            },
            "get_browser_state": gbs,
        })
        ev = computer_use.cu_capture(
            self.repo, profile_key="work", url="https://www.figma.com/design/abc",
            runner=runner, profile_opener=self._profile_opener(),
        )
        self.assertTrue(ev["ok"])
        self.assertEqual(ev["effect"], "captured")
        self.assertEqual(ev["snapshot_id"], "b-1")
        self.assertEqual(ev["coord_space"], "viewport_css_px")
        self.assertIn("Figma", ev["outline"])
        self.assertEqual(ev["after_png"], "/tmp/figma.png")
        self.assertEqual(ev["pid"], 4242)
        self.assertEqual(ev["window_id"], 991)

    def test_figma_canvas_px_uses_css_space_and_confirm_keeps_bind(self):
        def gbs(args):
            payload = json.loads(args[1])
            if payload.get("snapshot_format") == "semantic_v2":
                return {
                    "ok": True,
                    "snapshot_id": "fig-1",
                    "degraded": True,
                    "outline": "canvas",
                    "refs": [],
                    "screenshot_path": "/tmp/canvas.png",
                    "screenshot": {
                        "coordinate_space": "viewport_css_px",
                        "pixel_to_css_scale_x": 0.5,
                        "pixel_to_css_scale_y": 0.5,
                        "width": 800,
                        "height": 600,
                    },
                    "screenshot_width": 800,
                    "screenshot_height": 600,
                }
            return {
                "ok": True,
                "status": "ok",
                "binding_quality": "exact",
                "mutation_allowed": True,
                "tabs": [{
                    "target_id": "tgt-1",
                    "tab_id": "tab-1",
                    "url": "https://www.figma.com/design/abc#cdp-open=abc",
                }],
            }
        runner = self._runner({
            "launch_app": {
                "ok": True, "pid": 4242,
                "windows": [{"window_id": 991, "is_on_screen": True}],
            },
            "get_browser_state": gbs,
            "browser_click": {"ok": True, "effect": "unverifiable"},
        })
        cap = computer_use.cu_capture(
            self.repo, profile_key="work", url="https://www.figma.com/design/abc",
            runner=runner, profile_opener=self._profile_opener(),
        )
        ev = computer_use.cu_act(
            self.repo, snapshot_id=cap["snapshot_id"], action="click",
            x=10, y=20, runner=runner,
        )
        self.assertEqual(ev["addressed"]["kind"], "px")
        click = json.loads([c for c in runner.calls if c[1][0] == "browser_click"][0][1][1])
        self.assertEqual(click["x"], 5.0)
        self.assertEqual(click["y"], 10.0)
        self.assertEqual(click["target_id"], "tgt-1")
        self.assertNotIn("element_token", click)
        confirmed = computer_use.cu_confirm(self.repo, snapshot_id=cap["snapshot_id"], runner=runner)
        self.assertFalse(any(c[1][0] == "get_window_state" for c in runner.calls))
        snapshots = [
            json.loads(c[1][1])
            for c in runner.calls
            if c[1][0] == "get_browser_state" and "semantic_v2" in c[1][1]
        ]
        self.assertGreaterEqual(len(snapshots), 2)
        self.assertEqual(snapshots[-1]["target_id"], "tgt-1")
        self.assertEqual(snapshots[-1]["tab_id"], "tab-1")
        self.assertEqual(confirmed["coord_space"], "viewport_css_px")

    def test_url_without_profile_key_is_refused(self):
        runner = self._runner({})
        ev = computer_use.cu_capture(
            self.repo, url="https://www.figma.com/design/abc", runner=runner,
        )
        self.assertEqual(ev["effect"], "refused")
        self.assertIn("profile_key", ev["hint"])
        self.assertEqual(runner.calls, [])

    def test_record_start_uses_driver_tool_under_evidence(self):
        dest = self.repo / ".rig" / "cu-evidence" / "gui-test-run"
        runner = self._runner({
            "start_recording": {"ok": True, "output_dir": str(dest)},
        })
        ev = computer_use.cu_record(
            self.repo, action="start", output_dir=str(dest), runner=runner,
        )
        self.assertTrue(ev["ok"])
        self.assertEqual(ev["action"], "record_start")
        self.assertEqual(ev["effect"], "recorded")
        self.assertEqual(ev["output_dir"], str(dest.resolve()))
        self.assertTrue(ev["recording_path"].endswith("recording.mp4"))
        self.assertTrue(dest.is_dir())
        self.assertEqual(runner.calls[0][1][0], "start_recording")
        body = json.loads(runner.calls[0][1][1])
        self.assertEqual(body["output_dir"], str(dest.resolve()))
        self.assertTrue(body["record_video"])
        self.assertIn("rig_cu_record", ev["hint"])

    def test_record_refuses_path_outside_evidence(self):
        runner = self._runner({})
        ev = computer_use.cu_record(
            self.repo, action="start", output_dir="/tmp/cua-trajectories", runner=runner,
        )
        self.assertEqual(ev["effect"], "refused")
        self.assertIn(".rig/cu-evidence", ev["hint"])
        self.assertEqual(runner.calls, [])

    def test_record_stop_calls_driver_and_returns_mp4(self):
        dest = self.repo / ".rig" / "cu-evidence" / "gui-test-run"
        dest.mkdir(parents=True)
        mp4 = dest / "recording.mp4"
        runner = self._runner({
            "start_recording": {"ok": True},
            "stop_recording": {"ok": True, "last_video_path": str(mp4)},
        })
        computer_use.cu_record(
            self.repo, action="start", output_dir=str(dest), runner=runner,
        )
        ev = computer_use.cu_record(self.repo, action="stop", runner=runner)
        self.assertEqual(ev["action"], "record_stop")
        self.assertEqual(ev["effect"], "stopped")
        self.assertEqual(ev["recording_path"], str(mp4))
        self.assertTrue(any(c[1][0] == "stop_recording" for c in runner.calls))

    def test_record_hidden_when_not_effective(self):
        (self.repo / ".rig" / "harness.toml").write_text("[computer-use]\nenabled = false\n")
        runner = self._runner({})
        ev = computer_use.cu_record(self.repo, action="start", runner=runner)
        self.assertEqual(ev["effect"], "hidden")
        self.assertEqual(runner.calls, [])

    def test_capture_act_confirm_succeeds(self):
        runner = self._runner({
            "get_window_state": _capture_payload(),
            "click": {"ok": True, "effect": "unverifiable", "screenshot_path": "/tmp/after.png"},
        })
        cap = computer_use.cu_capture(self.repo, pid=844, window_id=10725, runner=runner)
        act = computer_use.cu_act(
            self.repo, snapshot_id=cap["snapshot_id"], element_token="tok-1",
            action="click", runner=runner,
        )
        confirmed = computer_use.cu_confirm(
            self.repo, snapshot_id=cap["snapshot_id"], runner=runner,
        )
        self.assertTrue(act["ok"])
        self.assertEqual(act["effect"], "unverifiable")
        self.assertEqual(confirmed["action"], "confirm")
        self.assertEqual(confirmed["effect"], "confirmed")
        self.assertTrue(confirmed["ok"])
        self.assertTrue(any(c[1][0] == "click" for c in runner.calls))
        self.assertGreaterEqual(
            len([c for c in runner.calls if c[1][0] == "get_window_state"]), 2,
        )

    def test_second_action_refuses_without_driver(self):
        runner = self._runner({
            "get_window_state": _capture_payload(),
            "click": {"ok": True, "effect": "unverifiable"},
        })
        cap = computer_use.cu_capture(self.repo, runner=runner)
        computer_use.cu_act(
            self.repo, snapshot_id=cap["snapshot_id"], element_token="tok-1",
            action="click", runner=runner,
        )
        clicks = [c for c in runner.calls if c[1][0] == "click"]
        self.assertEqual(len(clicks), 1)
        ev = computer_use.cu_act(
            self.repo, snapshot_id=cap["snapshot_id"], element_token="tok-2",
            action="click", runner=runner,
        )
        self.assertEqual(ev["effect"], "stale")
        self.assertIn("capture_required", ev["hint"])
        self.assertEqual(len([c for c in runner.calls if c[1][0] == "click"]), 1)

    def test_confirm_before_action_refuses_without_driver(self):
        runner = self._runner({"get_window_state": _capture_payload()})
        cap = computer_use.cu_capture(self.repo, pid=844, window_id=10725, runner=runner)
        calls = len(runner.calls)
        ev = computer_use.cu_confirm(
            self.repo, snapshot_id=cap["snapshot_id"], runner=runner,
        )
        self.assertEqual(ev["effect"], "stale")
        self.assertIn("capture_required", ev["hint"])
        self.assertEqual(len(runner.calls), calls)
        self.assertFalse(ev["ok"])

    def test_expired_capture_refuses_without_driver(self):
        clock = {"t": 0.0}

        def now():
            return clock["t"]

        runner = self._runner({"get_window_state": _capture_payload()})
        with mock.patch.object(computer_use, "_now", side_effect=now):
            cap = computer_use.cu_capture(
                self.repo, pid=844, window_id=10725, runner=runner,
            )
            clock["t"] = 31.0
            ev = computer_use.cu_act(
                self.repo, snapshot_id=cap["snapshot_id"], element_token="tok-1",
                action="click", runner=runner,
            )
        self.assertEqual(ev["effect"], "stale")
        self.assertEqual(ev.get("snapshot_freshness"), "expired")
        self.assertIn("expired", ev["hint"])
        self.assertFalse(any(c[1][0] == "click" for c in runner.calls))

    def _assert_invoked_failure_not_confirmable(self, driver_effect: str) -> None:
        runner = self._runner({
            "get_window_state": _capture_payload(),
            "click": {"ok": False, "effect": driver_effect},
        })
        cap = computer_use.cu_capture(self.repo, pid=844, window_id=10725, runner=runner)
        act = computer_use.cu_act(
            self.repo, snapshot_id=cap["snapshot_id"], element_token="tok-1",
            action="click", runner=runner,
        )
        self.assertEqual(act["effect"], driver_effect)
        self.assertFalse(act["ok"])
        self.assertNotEqual(act.get("snapshot_freshness"), "confirm_required")
        self.assertEqual(len([c for c in runner.calls if c[1][0] == "click"]), 1)
        calls = len(runner.calls)
        confirmed = computer_use.cu_confirm(
            self.repo, snapshot_id=cap["snapshot_id"], runner=runner,
        )
        self.assertEqual(confirmed["effect"], "stale")
        self.assertNotEqual(confirmed["effect"], "confirmed")
        self.assertFalse(confirmed["ok"])
        self.assertIn("capture_required", confirmed["hint"])
        self.assertEqual(len(runner.calls), calls)
        ev = computer_use.cu_act(
            self.repo, snapshot_id=cap["snapshot_id"], element_token="tok-2",
            action="click", runner=runner,
        )
        self.assertEqual(ev["effect"], "stale")
        self.assertIn("capture_required", ev["hint"])
        self.assertEqual(len([c for c in runner.calls if c[1][0] == "click"]), 1)

    def test_invoked_stale_cannot_be_confirmed_or_reused(self):
        self._assert_invoked_failure_not_confirmable("stale")

    def test_invoked_refused_cannot_be_confirmed_or_reused(self):
        self._assert_invoked_failure_not_confirmable("refused")


if __name__ == "__main__":
    unittest.main()
