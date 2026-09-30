"""Task doctor must never turn configuration or a fixture into live readiness."""
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
import doctor
import rig_mcp
import mcp_test_support


class TaskDoctor(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.repo = self.root / "repo"
        self.home = self.root / "home"
        self.bins = self.root / "bins"
        self.repo.mkdir()
        self.home.mkdir()
        (self.repo / ".git").mkdir()
        (self.repo / ".rig").mkdir()
        self.harness = self.repo / ".rig" / "harness.toml"
        self.harness.write_text('parent = "codex"\n[project]\nenabled = true\n[workers]\nclaude = true\n')
        for name in ("codex", "claude", "opencode", "pi", "bsk", "cua-driver"):
            path = mcp_test_support.fake_bin(self.bins, name)
            path.write_text('#!/bin/sh\necho CALLED >> "' + str(self.root / 'unexpected-calls') + '"\nexit 97\n')
        mcp_test_support.seed_installed_mcp(self.home)
        self.cache = self.home / "catalog.json"
        env = {"HOME": str(self.home), "PATH": mcp_test_support.stub_path(self.bins),
               "RIG_HOME": str(self.home / ".rig"), "RIG_PARENT": "codex", "RIG_SKIP_MODEL_CATALOG": "1",
               "RIG_SKIP_UPDATE_CHECK": "1", "RIG_MODEL_CATALOG_CACHE": str(self.cache),
               "PYTHONDONTWRITEBYTECODE": "1"}
        patch = mock.patch.dict(os.environ, env, clear=True)
        patch.start()
        self.addCleanup(patch.stop)

    def report(self, **kwargs):
        return doctor.build_report(self.repo, **kwargs)

    def rows(self, report):
        return {c["name"]: c for c in report["checks"]}

    def cache_models(self, worker="opencode", ids=None, age=10, status="ok"):
        self.cache.write_text(json.dumps({worker: {"ids": ids if ids is not None else ["vendor/model"],
                                                "fetched_at": time.time() - age, "status": status}}))

    def cli(self, *args):
        return subprocess.run(["bash", str(ROOT / "bin" / "rig"), "doctor", *args],
                              cwd=self.repo, text=True, capture_output=True, timeout=20)

    def test_healthy_coding_is_configured_not_execution_ready(self):
        before = self.harness.read_bytes()
        report = self.report()
        rows = self.rows(report)
        self.assertEqual(report["status"], "configured-host-unverified")
        self.assertEqual(report["local_prerequisites"], "ready")
        self.assertEqual(rows["mcp"]["status"], "configured-host-unverified")
        self.assertEqual(rows["auth"]["status"], "missing-evidence")
        self.assertEqual(rows["model"]["status"], "missing-evidence")
        for name in ("browser", "computer-use"):
            self.assertEqual(rows[name]["status"], "optional-disabled")
            self.assertFalse(rows[name]["required"])
        self.assertFalse(report["smoke_requested"])
        self.assertEqual(before, self.harness.read_bytes())
        self.assertFalse((self.root / "unexpected-calls").exists())
        self.assertFalse(self.cache.exists())

    def test_selected_parent_is_not_claimed_to_be_live(self):
        report = self.report(parent="opencode")
        self.assertEqual(report["parent"], {"selected": "opencode", "source": "explicit", "detected": "codex", "preferred": "codex"})
        snapshot = doctor.runtime_snapshot(self.repo)
        self.assertEqual(self.rows(self.report(parent="opencode", host_snapshot=snapshot))["mcp"]["status"], "configured-host-unverified")

    def test_cursor_desktop_parent_does_not_require_worker_cli(self):
        os.environ["RIG_PARENT"] = "cursor"
        path = self.home / ".cursor" / "mcp.json"
        path.parent.mkdir()
        path.write_text(json.dumps({"mcpServers": {"rig": {"command": str(self.home / "rig-mcp.sh")}}}))
        report = self.report(parent="cursor", host_snapshot=doctor.runtime_snapshot(self.repo))
        self.assertEqual(self.rows(report)["parent"]["status"], "ready")
        self.assertEqual(self.rows(report)["mcp"]["status"], "ready")
        self.assertEqual(report["status"], "configured-host-unverified")
        offline = self.report(parent="cursor")
        self.assertEqual(offline["local_prerequisites"], "unverified")
        self.assertEqual(self.rows(offline)["parent"]["status"], "configured-host-unverified")

    def test_missing_parent_executable(self):
        (self.bins / "codex").unlink()
        self.assertEqual(self.rows(self.report())["parent"]["status"], "missing")
        self.assertEqual(self.report()["status"], "missing")

    def test_missing_and_disabled_projects_are_readable(self):
        self.harness.unlink()
        report = self.report()
        self.assertEqual(report["status"], "missing")
        self.assertFalse(self.harness.exists())
        result = rig_mcp.call_tool("rig_doctor", {"repo": str(self.repo)})
        self.assertFalse(result.get("isError"), result)
        self.harness.write_text("[project]\nenabled = false\n")
        self.assertEqual(self.report()["status"], "disabled")
        self.assertFalse(rig_mcp.call_tool("rig_doctor", {"repo": str(self.repo)}).get("isError"))

    def test_parent_mcp_config_is_not_child_support(self):
        report = self.report(parent="claude")
        self.assertEqual(self.rows(report)["mcp"]["status"], "missing")
        self.assertEqual(report["workers"][0]["status"], "configured-host-unverified")

    def test_project_parent_mcp_configs_and_precedence(self):
        cursor = self.repo / ".cursor" / "mcp.json"
        cursor.parent.mkdir()
        valid = {"command": str(self.home / "rig-mcp.sh")}
        cursor.write_text(json.dumps({"mcpServers": {"rig": valid}}))
        self.assertEqual(self.rows(self.report(parent="cursor"))["mcp"]["status"], "configured-host-unverified")
        project = self.repo / ".mcp.json"
        project.write_text(json.dumps({"mcpServers": {"rig": valid}}))
        self.assertEqual(self.rows(self.report(parent="claude"))["mcp"]["status"], "configured-host-unverified")
        user = self.home / ".claude.json"
        user.write_text(json.dumps({"mcpServers": {"rig": valid}, "projects": {
            str(self.repo.resolve()): {"mcpServers": {"rig": {"command": "/missing"}}}}}))
        self.assertEqual(self.rows(self.report(parent="claude"))["mcp"]["status"], "missing")
        global_cursor = self.home / ".cursor" / "mcp.json"
        global_cursor.parent.mkdir()
        global_cursor.write_text(json.dumps({"mcpServers": {"rig": valid}}))
        cursor.write_text(json.dumps({"mcpServers": {"rig": {**valid, "enabled": False}}}))
        self.assertEqual(self.rows(self.report(parent="cursor"))["mcp"]["status"], "missing")
        cursor.write_text("{")
        self.assertEqual(self.rows(self.report(parent="cursor"))["mcp"]["status"], "missing")

    def test_project_mcp_change_needs_restart_and_other_repo_is_unknown(self):
        os.environ["RIG_PARENT"] = "cursor"
        cursor = self.repo / ".cursor" / "mcp.json"
        cursor.parent.mkdir()
        cursor.write_text(json.dumps({"mcpServers": {"rig": {"command": str(self.home / "rig-mcp.sh")}}}))
        snapshot = doctor.runtime_snapshot(self.repo)
        cursor.write_text(json.dumps({"mcpServers": {"rig": {"command": "/bin/sh"}}}))
        self.assertEqual(self.report(host_snapshot=snapshot)["status"], "restart-needed")
        snapshot["repo"] = str(self.root / "other")
        self.assertEqual(self.rows(self.report(host_snapshot=snapshot))["mcp"]["status"], "configured-host-unverified")

    def test_invalid_disabled_and_missing_launchers(self):
        path = self.home / ".codex" / "config.toml"
        for text in ('# [mcp_servers.rig]\n', '[mcp_servers.rig]\ncommand="/no/such/launcher"\n',
                     '[mcp_servers.rig]\ncommand="/bin/sh"\nenabled=false\n', '[broken'):
            with self.subTest(text=text):
                path.write_text(text)
                self.assertEqual(self.rows(self.report())["mcp"]["status"], "missing")

    def test_nested_startup_cwd_uses_the_same_repository_root(self):
        nested = self.repo / "src"
        nested.mkdir()
        with mock.patch.object(os, "getcwd", return_value=str(nested)):
            self.assertEqual(doctor.runtime_snapshot()["repo"], str(self.repo))
        with mock.patch.dict(os.environ, {"RIG_REPO": str(nested)}):
            snapshot = doctor.runtime_snapshot()
        self.assertEqual(snapshot["repo"], str(self.repo))
        self.assertEqual(self.rows(self.report(host_snapshot=snapshot))["mcp"]["status"], "ready")
        config = self.home / ".codex" / "config.toml"
        config.write_text(config.read_text().replace("args = []", 'args = ["--changed"]'))
        self.assertEqual(self.report(host_snapshot=snapshot)["status"], "restart-needed")

    def test_grok_home_override_matches_installer(self):
        custom = self.home / "custom-grok"
        custom.mkdir()
        default = self.home / ".grok" / "config.toml"
        (custom / "config.toml").write_bytes(default.read_bytes())
        default.unlink()
        os.environ["GROK_HOME"] = str(custom)
        self.assertEqual(doctor.parent_config_path("grok"), custom / "config.toml")
        self.assertTrue(doctor.child_mcp._stdio_entry_ready(doctor.parent_entry("grok", self.repo)))

    def test_pi_requires_its_adapter(self):
        (self.home / ".pi" / "agent" / "settings.json").unlink()
        self.assertEqual(self.rows(self.report(parent="pi"))["mcp"]["status"], "missing")

    def test_live_mcp_evidence_does_not_prove_auth(self):
        snapshot = doctor.runtime_snapshot(self.repo)
        rows = self.rows(self.report(host_snapshot=snapshot))
        self.assertEqual(rows["mcp"]["status"], "ready")
        self.assertEqual(rows["auth"]["status"], "missing-evidence")
        self.assertEqual(self.report(host_snapshot=snapshot)["status"], "configured-host-unverified")

    def test_stale_mcp_config_requires_restart(self):
        snapshot = doctor.runtime_snapshot(self.repo)
        config = self.home / ".codex" / "config.toml"
        config.write_text(config.read_text().replace("args = []", 'args = ["--new"]'))
        report = self.report(host_snapshot=snapshot)
        self.assertEqual(report["status"], "restart-needed")
        self.assertEqual(self.rows(report)["mcp"]["status"], "restart-needed")

    def test_stale_loaded_source_requires_restart(self):
        snapshot = doctor.runtime_snapshot(self.repo)
        snapshot["sources"]["rig_mcp.py"] = "old"
        self.assertEqual(self.report(host_snapshot=snapshot)["status"], "restart-needed")

    def test_mcp_uses_loaded_snapshot_and_structured_report(self):
        snapshot = doctor.runtime_snapshot(self.repo)
        with mock.patch.object(rig_mcp, "_DOCTOR_HOST_SNAPSHOT", snapshot):
            result = rig_mcp.call_tool("rig_doctor", {"repo": str(self.repo), "task": "coding"})
        self.assertFalse(result.get("isError"), result)
        self.assertEqual(self.rows(result["structuredContent"])["mcp"]["status"], "ready")
        self.assertIn("auth: missing-evidence", result["content"][0]["text"])

    def test_fresh_cached_exact_model_is_evidence_not_auth(self):
        self.cache_models()
        rows = self.rows(self.report(parent="opencode", model="vendor/model"))
        self.assertEqual(rows["model"]["status"], "ready")
        self.assertEqual(rows["model"]["freshness"], "fresh")
        self.assertEqual(rows["auth"]["status"], "missing-evidence")
        self.assertFalse((self.root / "unexpected-calls").exists())

    def test_missing_stale_empty_failed_model_evidence(self):
        for age, status, ids, freshness in [(4000, "ok", ["vendor/model"], "stale"),
                                          (90000, "ok", ["vendor/model"], "expired"),
                                          (10, "empty", [], "fresh"), (10, "failure", [], "unknown"),
                                          (-10, "ok", ["vendor/model"], "unknown")]:
            with self.subTest(age=age, status=status):
                self.cache_models(age=age, status=status, ids=ids)
                row = self.rows(self.report(parent="opencode", model="vendor/model"))["model"]
                self.assertEqual(row["status"], "missing-evidence")
                self.assertEqual(row["freshness"], freshness)

    def test_unavailable_model(self):
        self.cache_models()
        report = self.report(parent="opencode", model="vendor/unavailable")
        self.assertEqual(report["status"], "unavailable")
        self.assertEqual(self.rows(report)["model"]["status"], "unavailable")

    def test_invalid_routing_is_a_blocker(self):
        (self.repo / ".rig" / "routing.json").write_text('{"schema_version": 99}')
        self.assertEqual(self.rows(self.report())["routing"]["status"], "missing")

    def test_toml_dates_and_unreadable_configs_cannot_crash_mcp(self):
        path = self.home / ".grok" / "config.toml"
        path.write_text(path.read_text() + "last_updated = 2026-09-30T00:00:00Z\n")
        snapshot = doctor.runtime_snapshot(self.repo)
        self.assertIn("grok", snapshot["configs"])
        rows = self.rows(self.report(host_snapshot=snapshot))
        self.assertEqual(rows["mcp"]["status"], "ready")
        result = subprocess.run([sys.executable, "-B", "-c", "import rig_mcp; print('started')"],
                                cwd=ROOT / "scripts", text=True, capture_output=True, timeout=8)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "started")
        path.write_bytes(b"\xff invalid UTF-8")
        self.assertIn("grok", doctor.runtime_snapshot(self.repo)["configs"])

    def test_research_delegation_requires_smart_routing(self):
        self.harness.write_text(self.harness.read_text() + '\n[routing]\nmode="legacy"\n')
        (self.repo / "source.md").write_text("source")
        report = self.report(task="research", research_sources=["source.md"])
        self.assertEqual(self.rows(report)["routing"]["status"], "missing")
        self.assertEqual(report["local_prerequisites"], "blocked")
        self.assertIn("smart routing", self.rows(report)["routing"]["detail"])
        self.assertEqual(self.rows(self.report(task="coding"))["routing"]["status"], "ready")

    def test_declined_optional_capabilities_never_run(self):
        self.harness.write_text(self.harness.read_text() + '\n[browser-skill]\nenabled=true\n[computer-use]\nenabled=true\n')
        (self.home / ".rig").mkdir()
        for name in ("browser-skill", "cua-driver"):
            (self.home / ".rig" / (name + ".json")).write_text('{"opt_in": false}')
        for task in ("browser", "computer-use"):
            report = self.report(task=task)
            self.assertEqual(report["status"], "optional-disabled")
            row = self.rows(report)[task]
            self.assertEqual(row["machine"], "declined")
            self.assertTrue(row["required"])
        self.assertEqual(self.report()["status"], "configured-host-unverified")
        self.assertFalse((self.root / "unexpected-calls").exists())

    def test_configured_optional_is_still_unverified(self):
        self.harness.write_text(self.harness.read_text() + '\n[browser-skill]\nenabled=true\n[computer-use]\nenabled=true\n')
        (self.home / ".rig").mkdir()
        for name in ("browser-skill", "cua-driver"):
            (self.home / ".rig" / (name + ".json")).write_text('{"opt_in": true}')
        for task in ("browser", "computer-use"):
            self.assertEqual(self.rows(self.report(task=task))[task]["status"], "configured-host-unverified")
        self.assertFalse((self.root / "unexpected-calls").exists())

    def test_research_sources_are_bounded_and_required_for_delegation(self):
        self.assertEqual(self.report(task="research")["status"], "missing")
        source = self.repo / "source.md"
        source.write_text("fixture source")
        report = self.report(task="research", research_sources=["source.md"])
        self.assertEqual(self.rows(report)["research-sources"]["status"], "ready")
        report = self.report(task="research", research_sources=["missing.md"])
        self.assertEqual(self.rows(report)["research-sources"]["status"], "missing")
        outside = self.root / "outside.md"
        outside.write_text("outside")
        (self.repo / "link.md").symlink_to(outside)
        report = self.report(task="research", research_sources=["link.md"])
        self.assertEqual(self.rows(report)["research-sources"]["status"], "missing")
        self.assertEqual(source.read_text(), "fixture source")

    def test_cli_json_text_and_help(self):
        result = self.cli("--task", "coding", "--parent", "codex", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), self.report(parent="codex"))
        result = self.cli("--capability", "coding")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("configured-host-unverified", result.stdout)
        self.assertIn("temporary child MCP fixture", self.cli("--help").stdout)
        self.assertNotEqual(self.cli("--task", "nonexistent").returncode, 0)
        self.assertNotEqual(self.cli("--task", "research").returncode, 0)

    def test_schema_parent_only_and_invalid_inputs(self):
        tool = next(t for t in rig_mcp.TOOLS if t["name"] == "rig_doctor")
        self.assertTrue(tool["annotations"]["readOnlyHint"])
        self.assertFalse(tool["annotations"]["openWorldHint"])
        self.assertNotIn("rig_doctor", rig_mcp.CHILD_TOOL_NAMES)
        for args in ({"smoke": "false"}, {"task": "bogus"}, {"parent": "bogus"},
                     {"model": False}, {"research_sources": "source.md"},
                     {"task": "research", "research_sources": ["../outside"]},
                     {"research_sources": ["source.md"]}):
            with self.subTest(args=args):
                self.assertTrue(rig_mcp.call_tool("rig_doctor", {"repo": str(self.repo), **args}).get("isError"))

    def test_real_fixture_smoke_preserves_project_and_host(self):
        before = {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        report = self.report(smoke=True)
        row = self.rows(report)["smoke"]
        self.assertEqual(row["status"], "ready", row)
        self.assertEqual(row["scope"], "temporary-child-mcp-fixture")
        self.assertEqual(report["status"], "configured-host-unverified")
        after = {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        self.assertEqual(before, after)
        self.assertFalse((self.root / "unexpected-calls").exists())

    def test_smoke_is_explicit_and_timeout_is_bounded(self):
        with mock.patch.object(doctor, "smoke_check", side_effect=AssertionError("implicit smoke")):
            self.report()
        with mock.patch.object(doctor.subprocess, "run", side_effect=subprocess.TimeoutExpired("fixture", 8)) as run:
            row = doctor.smoke_check()
        self.assertEqual(row["status"], "failed")
        self.assertEqual(run.call_args.kwargs["timeout"], 8)
        self.assertFalse(Path(run.call_args.kwargs["cwd"]).exists())
        env = run.call_args.kwargs["env"]
        self.assertNotIn("OPENAI_API_KEY", env)
        self.assertNotIn("RIG_PARENT", env)
        self.assertEqual(env["PATH"], os.defpath)


if __name__ == "__main__":
    unittest.main()
