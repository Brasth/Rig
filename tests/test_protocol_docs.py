"""Run the documented lifecycle and validate referenced parent tool names."""
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import admission
import rig_mcp
import verification


class ProtocolDocumentation(unittest.TestCase):
    def test_smart_policy_is_documented_across_managed_protocols(self):
        sources = self.protocol_sources()
        sources.pop("skills/rig-jobs/SKILL.md")
        sources.pop("README.md")
        sources["AGENTS.md"] = (ROOT / "AGENTS.md").read_text()
        sources["managed skill"] = (ROOT / ".agents/skills/delegate-harness/SKILL.md").read_text()
        for name, source in sources.items():
            with self.subTest(source=name):
                self.assertIn("complexity", source)
                self.assertIn("uncertainty", source)
                self.assertIn("routing.json", source)
                self.assertIn("legacy", source)
                self.assertIn("rig_routing_report", source)
                self.assertNotIn("Implement: Grok child if effective.", source)

    @staticmethod
    def protocol_sources():
        sources = {str(path.relative_to(ROOT)): path.read_text() for path in (
            ROOT / "README.md", ROOT / "docs" / "usage.md",
            ROOT / "skills" / "delegate-harness" / "SKILL.md",
            ROOT / "skills" / "rig-jobs" / "SKILL.md",
        )}
        sources["generated parent protocol"] = (ROOT / "bin" / "rig").read_text().split(
            "<!-- rig:start -->", 1)[1].split("<!-- rig:end -->", 1)[0]
        return sources

    def test_cancellation_and_transport_recovery_instructions_are_consistent(self):
        for name, source in self.protocol_sources().items():
            with self.subTest(source=name):
                self.assertIn("rig job wait ID --timeout 0", source)
                self.assertIn("stop-unconfirmed", source)
                self.assertIn("native-cancel-required", source)
                self.assertRegex(source.lower(), r"(?:do not|never) re-wait, re-pick, or drain")
                self.assertNotIn("Host-dropped wait still bash-waits once", source)
                self.assertNotIn("host drops the tool, bash `rig job wait` once (no `--timeout`)", source)

    def test_orphan_wrapper_stop_and_unconfirmed_wait_are_documented(self):
        sources = {
            "docs/usage.md": (ROOT / "docs" / "usage.md").read_text(),
            "docs/rig-flow.md": (ROOT / "docs" / "rig-flow.md").read_text(),
            "AGENTS.md": (ROOT / "AGENTS.md").read_text(),
            "skills/delegate-harness/SKILL.md": (ROOT / "skills" / "delegate-harness" / "SKILL.md").read_text(),
            "managed skill": (ROOT / ".agents" / "skills" / "delegate-harness" / "SKILL.md").read_text(),
            "skills/rig-jobs/SKILL.md": (ROOT / "skills" / "rig-jobs" / "SKILL.md").read_text(),
        }
        for name, source in sources.items():
            with self.subTest(source=name):
                self.assertIn("orphans", source)
                self.assertIn("in-tree", source)
                self.assertIn("unconfirmed", source)
                self.assertIn("stop-unconfirmed", source)

    def test_queue_receipts_and_durable_credentials_are_documented(self):
        sources = self.protocol_sources()
        sources.pop("README.md")
        sources.pop("skills/rig-jobs/SKILL.md")
        sources["skills/rig-queue/SKILL.md"] = (ROOT / "skills" / "rig-queue" / "SKILL.md").read_text()
        for name, source in sources.items():
            with self.subTest(source=name):
                self.assertIn("--idempotency-key", source)
                self.assertIn("--owner-pid", source)
                self.assertIn("--owner-session", source)
                self.assertIn(".rig/queue/credentials/<queue-id>.json", source)
                self.assertIn("receipt", source)
        queue_skill = sources["skills/rig-queue/SKILL.md"]
        self.assertNotIn("then the QUEUE block", queue_skill)
        self.assertIn("The parking command itself stops after its receipt", queue_skill)

    def test_documented_native_cli_example_accepts_real_content_privately(self):
        usage = (ROOT / "docs" / "usage.md").read_text()
        section = usage.split("## Protected writes and parent acceptance", 1)[1]
        example = section.split("```bash\n", 1)[1].split("\n```", 1)[0]
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary) / "repo"
            home = Path(temporary) / "home"
            repo.mkdir(); home.mkdir()
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            (repo / ".rig").mkdir()
            (repo / ".rig" / "harness.toml").write_text('parent = "codex"\n[workers]\ncodex = false\n')
            (repo / ".gitignore").write_text('.rig/\n__pycache__/\n')
            env = {key: value for key, value in os.environ.items() if not key.startswith("RIG_")}
            env.update(HOME=str(home), RIG_HOME=str(ROOT), RIG_INSTALL_TRANSACTION="1", RIG_PARENT="codex", RIG_SKIP_MODEL_CATALOG="1",
                       RIG_SKIP_UPDATE_CHECK="1", PATH=str(ROOT / "bin") + os.pathsep + os.environ["PATH"])
            result = subprocess.run(["bash", "-c", "set -euo pipefail\n" + example], cwd=repo,
                                    env=env, text=True, capture_output=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            response = json.loads((repo / ".rig" / "start-response.json").read_text())
            self.assertNotIn(response["owner_token"], result.stdout + result.stderr)
            self.assertEqual((repo / ".rig" / "start-response.json").stat().st_mode & 0o777, 0o600)
            record = admission.get_reservation(repo, response["reservation_id"])
            self.assertEqual(record["stage"], "released")
            folder = repo / ".rig" / "jobs" / response["job_id"]
            accepted = json.loads((folder / "verification.json").read_text())
            self.assertEqual(accepted["acceptance"], "accepted")
            self.assertEqual(accepted["method"], "mixed")
            self.assertTrue(accepted["check_ids"])
            self.assertEqual((repo / "src" / "example.py").read_text(), "answer = 42\n")
            (repo / "src" / "example.py").write_text("answer = 43\n")
            meta = json.loads((folder / "meta.json").read_text())
            self.assertNotEqual(verification.assessment(repo, meta, refresh=True)["state"], "verified")


    def test_mcp_launch_and_handshake_policy(self):
        sources = self.protocol_sources()
        sources["skills/rig-queue/SKILL.md"] = (ROOT / "skills" / "rig-queue" / "SKILL.md").read_text()
        sources["docs/rig-flow.md"] = (ROOT / "docs" / "rig-flow.md").read_text()
        for name, source in sources.items():
            with self.subTest(source=name):
                self.assertNotIn("Launching a child is still bash", source)
                self.assertNotIn("There is no spawn-from-MCP tool", source)
                self.assertIn("rig_job_launch", source)
        handshake_sources = {
            "docs/usage.md": sources["docs/usage.md"],
            "skills/delegate-harness/SKILL.md": sources["skills/delegate-harness/SKILL.md"],
            "skills/rig-jobs/SKILL.md": sources["skills/rig-jobs/SKILL.md"],
            "generated parent protocol": sources["generated parent protocol"],
        }
        for name, source in handshake_sources.items():
            with self.subTest(handshake=name):
                self.assertIn("rig_job_inbox", source)
                self.assertIn("child MCP handshake missing", source)
                self.assertRegex(
                    source.lower(),
                    r"(human|internal|fallback).{0,100}run-worker|run-worker.{0,100}(human|internal|fallback)",
                )

    def test_mcp_agent_steps_do_not_precreate_brief_before_launch(self):
        sources = {
            "generated parent protocol": (ROOT / "bin" / "rig").read_text().split(
                "<!-- rig:start -->", 1)[1].split("<!-- rig:end -->", 1)[0],
            "AGENTS.md": (ROOT / "AGENTS.md").read_text(),
            "skills/delegate-harness/SKILL.md": (ROOT / "skills" / "delegate-harness" / "SKILL.md").read_text(),
            "skills/rig-jobs/SKILL.md": (ROOT / "skills" / "rig-jobs" / "SKILL.md").read_text(),
            "skills/rig-queue/SKILL.md": (ROOT / "skills" / "rig-queue" / "SKILL.md").read_text(),
        }
        bad = re.compile(
            r"(?:write|writes)\s+`?\.rig/jobs/<id>/brief\.md`?\s+then\s+MCP\s+`?rig_job_launch",
            re.IGNORECASE,
        )
        bad_step = re.compile(r"(?m)^\d+\.\s+Write\s+`\.rig/jobs/<id>/brief\.md`")
        for name, source in sources.items():
            with self.subTest(source=name):
                self.assertIsNone(bad.search(source), f"{name} still instructs precreate-then-MCP-launch")
                self.assertIsNone(bad_step.search(source), f"{name} still has Write .rig/jobs brief step")
                self.assertRegex(
                    source.lower(),
                    r"prepare brief text|pass(?:es)? brief text|brief text \(do not",
                )
                self.assertRegex(
                    source.lower(),
                    r"(tool|launch)\s+creates|do not (?:mkdir|precreate)|do not mkdir or write",
                )
        delegate = sources["skills/delegate-harness/SKILL.md"]
        self.assertRegex(
            delegate.lower(),
            r"human/internal fallback.{0,120}write `?\.rig/jobs/<id>/brief\.md`?",
        )

    def test_documented_mcp_tools_exist_in_server_schema(self):
        sources = [ROOT / "README.md", ROOT / "docs" / "usage.md"]
        sources.extend(ROOT / "skills" / name / "SKILL.md" for name in ("delegate-harness", "rig-jobs", "rig-queue"))
        known = {tool["name"] for tool in [*rig_mcp.TOOLS, *rig_mcp.CHILD_TOOLS]}
        pattern = re.compile(
            r"\brig_(?:job_[a-z_]+|queue_[a-z_]+|workflow_[a-z_]+|workflows|session|pick|status|jobs|memory(?:_add)?)\b"
        )
        for source in sources:
            with self.subTest(path=source.relative_to(ROOT)):
                mentioned = set(pattern.findall(source.read_text()))
                self.assertFalse(mentioned - known, mentioned - known)
        protocol = (ROOT / "bin" / "rig").read_text().split("<!-- rig:start -->", 1)[1].split("<!-- rig:end -->", 1)[0]
        self.assertFalse(set(pattern.findall(protocol)) - known)

    def test_delegate_harness_copies_are_byte_consistent(self):
        left = (ROOT / "skills" / "delegate-harness" / "SKILL.md").read_bytes()
        right = (ROOT / ".agents" / "skills" / "delegate-harness" / "SKILL.md").read_bytes()
        self.assertEqual(left, right)

    def test_generated_parent_protocol_matches_agents_workflow_contract(self):
        agents = (ROOT / "AGENTS.md").read_text().split(
            "<!-- rig:start -->", 1)[1].split("<!-- rig:end -->", 1)[0]
        generated = (ROOT / "bin" / "rig").read_text().split(
            "<!-- rig:start -->", 1)[1].split("<!-- rig:end -->", 1)[0]
        self.assertEqual(agents, generated)
        for phrase in (
            "stay|explore|mini|bulk|implement|hard|review|verify",
            "rig_workflow_create",
            "rig_workflow_advance",
            "rig_workflow_wait",
            "rig_job_coordination_reply",
            "rig_job_coordination_request",
            "file AND resource",
            "[orchestration]",
            "final-verify",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, generated)

    def test_mcp_pick_and_session_describe_verify_role(self):
        tools = {tool["name"]: tool for tool in rig_mcp.TOOLS}
        for name in ("rig_pick", "rig_session"):
            role = tools[name]["inputSchema"]["properties"]["role"]
            with self.subTest(tool=name):
                self.assertIn("verify", role["enum"])
                self.assertIn("verify", role["description"])

    def test_workflow_credential_gitignore_entries(self):
        needed = (
            ".rig/queue/",
            ".rig/workflows/",
            ".rig/workflows/*/owner-credentials.json",
        )
        for path in (ROOT / ".gitignore", ROOT / "templates" / "gitignore-fragment"):
            text = path.read_text()
            for line in needed:
                with self.subTest(path=str(path.relative_to(ROOT)), line=line):
                    self.assertIn(line, text)

    def test_adaptive_workflow_contracts_are_documented(self):
        managed = {
            "README.md": (ROOT / "README.md").read_text(),
            "docs/usage.md": (ROOT / "docs" / "usage.md").read_text(),
            "docs/rig-flow.md": (ROOT / "docs" / "rig-flow.md").read_text(),
            "docs/smart-routing.md": (ROOT / "docs" / "smart-routing.md").read_text(),
            "docs/release-notes.md": (ROOT / "docs" / "release-notes.md").read_text(),
            "AGENTS.md": (ROOT / "AGENTS.md").read_text(),
            "skills/delegate-harness/SKILL.md": (ROOT / "skills" / "delegate-harness" / "SKILL.md").read_text(),
        }
        managed["generated parent protocol"] = (ROOT / "bin" / "rig").read_text().split(
            "<!-- rig:start -->", 1)[1].split("<!-- rig:end -->", 1)[0]
        core_names = (
            "README.md", "docs/usage.md", "AGENTS.md",
            "skills/delegate-harness/SKILL.md", "generated parent protocol",
        )
        core_phrases = (
            ".rig/workflows/",
            "owner-credentials.json",
            "0600",
            "[orchestration]",
            "adaptive",
            "single",
            "max_nodes",
            "Children never spawn",
            "file AND resource",
            "independent review unavailable",
            "rig_workflow_advance",
            "rig_workflow_wait",
        )
        for name in core_names:
            source = managed[name]
            lowered = source.lower()
            for phrase in core_phrases:
                with self.subTest(source=name, phrase=phrase):
                    self.assertIn(phrase.lower(), lowered)
            with self.subTest(source=name, check="no estimates"):
                self.assertRegex(source.lower(), r"no estimated progress, savings, or eta|no eta")
                self.assertNotRegex(source.lower(), r"estimated (eta|time remaining|savings of)")
                self.assertNotIn("F8 Workflows", source)

        usage = managed["docs/usage.md"]
        for phrase in (
            "rig_workflow_create",
            "rig_workflows",
            "rig_workflow_show",
            "rig_workflow_extend",
            "rig_workflow_resolve",
            "rig_workflow_approve",
            "rig_workflow_cancel",
            "rig_workflow_report",
            "rig_job_coordination_reply",
            "rig_job_coordination_request",
            "accepted/required",
            "next parent action",
            "planned",
            "cancel-requested",
            "final-verify",
            "never deletes data",
            "Tab Jobs/Queue/Workflows",
            "Queue and worker caps remain authoritative",
        ):
            with self.subTest(usage_phrase=phrase):
                self.assertIn(phrase.lower(), usage.lower())

        for name in ("docs/rig-flow.md", "docs/smart-routing.md", "docs/release-notes.md"):
            source = managed[name]
            with self.subTest(source=name, check="orchestration"):
                self.assertIn("[orchestration]", source)
                self.assertIn("single", source)
                self.assertNotIn("F8 Workflows", source)
                self.assertNotRegex(source.lower(), r"estimated (eta|time remaining|savings of)")

        release = managed["docs/release-notes.md"]
        self.assertIn("never deletes data", release)
        self.assertIn("confirm stopped", release)

        journal = (ROOT / "docs" / "journals" / "260913-wait-cancel-responsiveness.md").read_text()
        self.assertRegex(journal.lower(), r"combined.{0,80}rollout|rollout.{0,80}combined")
        journal_l = journal.lower()
        self.assertRegex(
            journal_l,
            r"(pending.{0,120}(installed smoke|final smoke|combined rollout))"
            r"|((installed smoke|final smoke|combined rollout).{0,120}pending)",
        )
        self.assertRegex(
            journal_l,
            r"(does not claim|still outstanding|still pending).{0,100}"
            r"(final installed-smoke|combined-rollout|installed-rollout|installed smoke|combined rollout)",
        )
        # No affirmative completion claims (space-separated forms).
        self.assertNotRegex(journal_l, r"installed smoke (passed|complete|done|verified)")
        self.assertNotRegex(journal_l, r"combined rollout (passed|complete|done|verified)")

    def test_computer_use_parent_protocol_and_fallback(self):
        sources = {
            "AGENTS.md": (ROOT / "AGENTS.md").read_text(),
            "generated parent protocol": (ROOT / "bin" / "rig").read_text().split(
                "<!-- rig:start -->", 1)[1].split("<!-- rig:end -->", 1)[0],
            "skills/delegate-harness/SKILL.md": (ROOT / "skills" / "delegate-harness" / "SKILL.md").read_text(),
            "docs/usage.md": (ROOT / "docs" / "usage.md").read_text(),
            "docs/rig-flow.md": (ROOT / "docs" / "rig-flow.md").read_text(),
            "README.md": (ROOT / "README.md").read_text(),
        }
        phrases = (
            "[computer-use] enabled",
            "cua-driver",
            "chrome-devtools",
            "Never Figma MCP or Playwright",
            "Children never receive cua-driver",
            "rig_cu_capture",
            "rig_cu_record",
            "escalate_px",
            "existing-profile",
        )
        for name, source in sources.items():
            for phrase in phrases:
                with self.subTest(source=name, phrase=phrase):
                    self.assertIn(phrase, source)
            with self.subTest(source=name, hermes="forbidden fallback"):
                self.assertIn("Hermes", source)
                self.assertIn("computer_use", source)

    def test_browser_skill_parent_protocol(self):
        sources = {
            "AGENTS.md": (ROOT / "AGENTS.md").read_text(),
            "generated parent protocol": (ROOT / "bin" / "rig").read_text().split(
                "<!-- rig:start -->", 1)[1].split("<!-- rig:end -->", 1)[0],
            "skills/delegate-harness/SKILL.md": (ROOT / "skills" / "delegate-harness" / "SKILL.md").read_text(),
            "docs/usage.md": (ROOT / "docs" / "usage.md").read_text(),
            "docs/rig-flow.md": (ROOT / "docs" / "rig-flow.md").read_text(),
            "README.md": (ROOT / "README.md").read_text(),
        }
        phrases = (
            "[browser-skill] enabled",
            "rig_bsk_status",
            "rig_bsk_observe",
            "Never run `bsk install-skill`",
            "Children never receive `bsk` or `rig_bsk_*`",
        )
        for name, source in sources.items():
            for phrase in phrases:
                with self.subTest(source=name, phrase=phrase):
                    self.assertIn(phrase, source)

    def test_browser_skill_real_cli_surface(self):
        sources = {
            "AGENTS.md": (ROOT / "AGENTS.md").read_text(),
            "generated parent protocol": (ROOT / "bin" / "rig").read_text().split(
                "<!-- rig:start -->", 1)[1].split("<!-- rig:end -->", 1)[0],
            "docs/usage.md": (ROOT / "docs" / "usage.md").read_text(),
            "docs/rig-flow.md": (ROOT / "docs" / "rig-flow.md").read_text(),
            "README.md": (ROOT / "README.md").read_text(),
            "skills/computer-use/SKILL.md": (ROOT / "skills" / "computer-use" / "SKILL.md").read_text(),
            "logged-in-browser.md": (
                ROOT / "skills" / "computer-use" / "references" / "logged-in-browser.md"
            ).read_text(),
        }
        phrases = (
            "session start --json",
            "status.browsers",
            "session_id",
        )
        for name, source in sources.items():
            for phrase in phrases:
                with self.subTest(source=name, phrase=phrase):
                    self.assertIn(phrase, source)
        self.assertIn("installer missing", (ROOT / "bin" / "rig").read_text())
        self.assertNotIn(
            "browser-skill setup: Python 3 unavailable; skip. Enable later: rig browser-skill setup",
            (ROOT / "bin" / "rig").read_text(),
        )

    def test_computer_use_figma_to_code_fidelity_loop(self):
        skill = (ROOT / "skills" / "computer-use" / "SKILL.md").read_text()
        howto = (ROOT / "skills" / "computer-use" / "references" / "figma-to-code.md").read_text()
        usage = (ROOT / "docs" / "usage.md").read_text()
        for name, source in (("skill", skill), ("howto", howto), ("usage", usage)):
            with self.subTest(source=name, phrase="download every image"):
                self.assertIn("download every image", source)
            with self.subTest(source=name, phrase="stand-in"):
                self.assertIn("stand-in", source)
        self.assertIn("references/figma-to-code.md", skill)
        self.assertIn("inspect tokens", skill)
        for name, source in (("skill", skill), ("howto", howto), ("usage", usage)):
            with self.subTest(source=name, phrase="every section"):
                self.assertIn("every section", source)
            with self.subTest(source=name, phrase="spacing and gap"):
                self.assertIn("spacing and gap", source)
        self.assertIn("Inventory (all content)", howto)
        self.assertIn("Spacing and gap (every section, every item)", howto)
        self.assertIn("space to next sibling", howto)
        self.assertIn("Colours (every fill, text, stroke, effect)", howto)
        self.assertIn("Typography (every text layer)", howto)
        self.assertIn("skills/style-guide/SKILL.md", howto)
        self.assertIn("Compare until it matches", howto)
        self.assertIn("figma-frame.png", howto)
        self.assertIn("html-frame.png", howto)
        self.assertIn("Children never click", howto)
        self.assertIn("skills/computer-use/references/figma-to-code.md", usage)
        self.assertIn("skills/style-guide/SKILL.md", usage)
        guide = (ROOT / "skills" / "style-guide" / "SKILL.md").read_text()
        homes = (ROOT / "skills" / "style-guide" / "references" / "token-homes.md").read_text()
        self.assertIn("Write colour, spacing, and typography tokens", guide)
        self.assertIn("token file", guide)
        self.assertIn("--color-*", guide)
        self.assertIn("--space-*", guide)
        self.assertIn("--font-*", guide)
        self.assertIn("Sass", guide)
        self.assertIn("Tailwind", guide)
        self.assertIn("theme.extend", guide)
        self.assertIn("@theme", guide)
        self.assertIn("Children never click", guide)
        self.assertIn("references/token-homes.md", guide)
        self.assertIn("Follow the codebase skill or rule first", guide)
        self.assertIn("repo’s own style-guide skill or rules", guide)
        self.assertIn("style-guide skill or rule", skill)
        self.assertIn("style-guide skill or rule", howto)
        self.assertIn("codebase’s own style-guide skill or rule", usage)
        for name, source in (("skill", skill), ("howto", howto), ("usage", usage), ("guide", guide)):
            with self.subTest(source=name, phrase="reuse existing"):
                self.assertTrue(
                    "reuse it" in source or "Reuse existing config" in source
                    or "reuse it and do not create a new or custom file" in source,
                    "missing reuse-existing-config rule",
                )
        self.assertIn("do not create a new or custom file", guide)
        self.assertIn("Reuse existing config", guide)
        self.assertIn("codebase assets folder", skill)
        self.assertIn("codebase assets folder", howto)
        self.assertIn("codebase assets folder", usage)
        self.assertIn("repo-relative", howto)
        self.assertNotIn("Absolute paths to downloaded images", howto)
        self.assertIn("$color-heading", homes)
        self.assertIn("tailwind.config", homes)
        self.assertIn("@theme", homes)
        self.assertIn("theme.extend", homes)
        self.assertIn("references/figma-to-mobile.md", skill)
        self.assertIn("references/screenshot-to-ui.md", skill)
        mobile = (ROOT / "skills" / "computer-use" / "references" / "figma-to-mobile.md").read_text()
        shot = (ROOT / "skills" / "computer-use" / "references" / "screenshot-to-ui.md").read_text()
        self.assertIn("download every image", mobile)
        self.assertIn("codebase assets folder", mobile)
        self.assertIn("React Native", mobile)
        self.assertIn("SwiftUI", mobile)
        self.assertIn("source=visual", shot)
        self.assertIn("download every image", shot)
        self.assertIn("figma-to-mobile.md", usage)
        self.assertIn("screenshot-to-ui.md", usage)
        self.assertIn("Native (reuse the existing theme file)", homes)
        self.assertIn("React Native", homes)
        self.assertIn("skills/computer-test/SKILL.md", skill)
        self.assertIn("references/desktop-drive.md", skill)
        self.assertIn("references/logged-in-browser.md", skill)
        self.assertIn("skills/computer-test/SKILL.md", usage)
        test_skill = (ROOT / "skills" / "computer-test" / "SKILL.md").read_text()
        gui = (ROOT / "skills" / "computer-test" / "references" / "gui-test.md").read_text()
        desk = (ROOT / "skills" / "computer-use" / "references" / "desktop-drive.md").read_text()
        browser = (ROOT / "skills" / "computer-use" / "references" / "logged-in-browser.md").read_text()
        self.assertIn("Real GUI testing", test_skill)
        self.assertIn("Children never click", test_skill)
        self.assertIn("pass/fail", gui)
        self.assertIn(".rig/cu-evidence", gui)
        self.assertIn("window_id", desk)
        self.assertIn("launch_app", desk)
        self.assertIn("profile_key", browser)
        self.assertIn("existing-profile", browser)
        self.assertIn("recording.mp4", test_skill)
        self.assertIn("Click the UI", test_skill)
        self.assertIn("recording.mp4", gui)
        rec = (ROOT / "skills" / "computer-test" / "references" / "record-video.md").read_text()
        self.assertIn("recording.mp4", rec)
        self.assertIn("rig_cu_record", rec)
        self.assertNotIn("cua-driver recording start", rec)
        self.assertIn("Do not shell cua-driver", rec)
        self.assertIn(".rig/cu-evidence", rec)
        self.assertIn("record-video.md", usage)
        self.assertIn("rig_cu_record", usage)
        self.assertIn("Do not shell cua-driver", skill)


if __name__ == "__main__":
    unittest.main()
