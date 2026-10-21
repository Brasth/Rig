#!/usr/bin/env python3
"""The parent's own skills and slash commands: listed for the shell's / menu, and expanded for a headless parent."""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import local_api  # noqa: E402
import parent_commands  # noqa: E402


class ParentCommands(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        base = Path(self.td.name)
        self.home, self.repo = base / "home", base / "repo"
        (self.repo / ".git").mkdir(parents=True)
        (self.repo / ".rig").mkdir()
        (self.repo / ".rig" / "harness.toml").write_text('parent = "codex"\n\n[project]\nenabled = true\n')
        self.env = mock.patch.dict(os.environ, {"HOME": str(self.home), "RIG_HOME": str(self.home / ".rig")})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.td.cleanup()

    def write(self, path, text):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def test_skills_and_commands_from_the_user_and_the_repository(self):
        self.write(self.home / ".codex/skills/review/SKILL.md", "---\nname: review\ndescription: Review the open diff\n---\n# Review\n")
        self.write(self.repo / ".agents/skills/release/SKILL.md", "---\ndescription: Cut a release\n---\n")
        self.write(self.home / ".codex/prompts/fix.md", "Fix the failing test $1, then run $ARGUMENTS\n")
        self.write(self.home / ".claude/commands/git/commit.md", "---\ndescription: 'Commit with a message'\n---\nCommit: $ARGUMENTS\n")
        rows = {r["name"]: r for r in parent_commands.commands(self.repo, "codex")}
        self.assertEqual(set(rows), {"review", "release", "prompts:fix"})
        self.assertEqual((rows["review"]["kind"], rows["review"]["help"]), ("skill", "Review the open diff"))
        self.assertEqual(rows["prompts:fix"]["help"], "Fix the failing test $1, then run $ARGUMENTS", "no front matter: the first line")
        claude = {r["name"]: r for r in parent_commands.commands(self.repo, "claude")}
        self.assertEqual(claude["git:commit"]["help"], "Commit with a message", "a sub-folder is the namespace")

    def test_a_repository_command_wins_over_a_personal_one(self):
        self.write(self.home / ".config/opencode/command/plan.md", "personal")
        self.write(self.repo / ".opencode/command/plan.md", "---\ndescription: the repo's plan\n---\nPlan $ARGUMENTS")
        rows = parent_commands.commands(self.repo, "opencode")
        self.assertEqual([(r["name"], r["help"]) for r in rows], [("plan", "the repo's plan")])

    def test_expanding_for_a_headless_parent(self):
        self.write(self.home / ".codex/prompts/fix.md", "---\ndescription: fix\n---\nFix $1 and say $ARGUMENTS\n")
        self.write(self.home / ".codex/skills/review/SKILL.md", "---\ndescription: Review\n---\n")
        self.write(self.home / ".pi/agent/prompts/note.md", "Write a note.")
        self.assertEqual(parent_commands.expand(self.repo, "codex", "/prompts:fix login.ts now"), "Fix login.ts and say login.ts now\n")
        skill = parent_commands.expand(self.repo, "codex", "/review the auth change")
        self.assertIn("Use your review skill", skill)
        self.assertIn(str(self.home / ".codex/skills/review/SKILL.md"), skill)
        self.assertTrue(skill.endswith("\n\nthe auth change"))
        self.assertEqual(parent_commands.expand(self.repo, "pi", "/note about tests"), "Write a note.\n\nabout tests", "no placeholder: after it")
        self.assertEqual(parent_commands.expand(self.repo, "codex", "/nope x"), "/nope x", "a name it does not have goes as typed")
        self.assertEqual(parent_commands.expand(self.repo, "claude", "/review x"), "/review x", "claude runs its own")
        self.assertEqual(parent_commands.expand(self.repo, "codex", "hello"), "hello")

    def test_dollar_mentions_and_bundled_skills(self):
        self.write(self.home / ".codex/skills/.system/skill-creator/SKILL.md", "---\ndescription: Make a skill\n---\n")
        self.write(self.home / ".agents/skills/ak/SKILL.md", "---\ndescription: The ak helper\n---\n")
        names = [r["name"] for r in parent_commands.commands(self.repo, "codex")]
        self.assertEqual(sorted(names), ["ak", "skill-creator"], "codex's .system skills are listed")
        sent = parent_commands.expand(self.repo, "codex", "use $ak on login, and $ak again, not $nope or a$ak")
        self.assertTrue(sent.startswith("use $ak on login, and $ak again, not $nope or a$ak\n\n"))
        self.assertEqual(sent.count("Use your ak skill"), 1)
        self.assertIn(str(self.home / ".agents/skills/ak/SKILL.md"), sent)
        self.write(self.repo / ".claude/skills/deploy/SKILL.md", "---\ndescription: Deploy\n---\n")
        self.assertEqual(parent_commands.expand(self.repo, "claude", "$deploy please"), "$deploy please",
                         "claude's box has no $ mentions: the text goes as typed")
        self.write(self.home / ".pi/agent/skills/check/SKILL.md", "---\ndescription: Check\n---\n")
        self.assertIn("Use your check skill", parent_commands.expand(self.repo, "pi", "/skill:check now"), "pi's /skill:name")
        self.assertEqual(parent_commands.expand(self.repo, "pi", "/check now"), "/check now")
        self.assertEqual(parent_commands.expand(self.repo, "codex", "costs $5"), "costs $5")

    def test_over_the_api(self):
        self.write(self.home / ".codex/skills/review/SKILL.md", "---\ndescription: Review\n---\n")
        api = local_api.Api(self.repo, "t" * 64)
        call = lambda method, params=None: api.dispatch({"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {},
                                                          "token": "t" * 64})
        result = call("chat.commands")["result"]
        self.assertEqual((result["parent"], [r["name"] for r in result["commands"]]), ("codex", ["review"]))
        self.assertEqual(call("chat.commands", {"parent": "claude"})["result"]["commands"], [])
        self.assertEqual(call("chat.commands", {"parent": 3})["error"]["code"], local_api.INVALID_PARAMS)
        rows = {r["method"]: r["mutates"] for r in call("api.methods")["result"]["methods"]}
        self.assertFalse(rows["chat.commands"])


if __name__ == "__main__":
    unittest.main()
