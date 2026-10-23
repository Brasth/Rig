#!/usr/bin/env python3
"""Issue to PR: plan once, only your thumbs-up counts, approved work is queued once, shipping opens a draft."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import issue_to_pr as itp  # noqa: E402
import work_queue  # noqa: E402

ISSUE = {"number": 7, "title": "Rate-limit login", "url": "https://github.com/o/r/issues/7",
         "body": "We get hammered.\n\n- [ ] limit by IP\n- [x] limit by account\n* return 429"}


class FakeGh:
    def __init__(self, me="owner"):
        self.me, self.issues, self.reactions, self.calls, self.next_id = me, [ISSUE], [], [], 100

    def __call__(self, args, cwd):
        self.calls.append(args)
        if args[:2] == ["api", "user"]:
            return self.me + "\n"
        if args[:2] == ["issue", "list"]:
            return json.dumps(self.issues)
        if args[0] == "api" and args[1].endswith("/comments"):
            self.next_id += 1
            return json.dumps({"id": self.next_id})
        if args[0] == "api" and args[1].endswith("/reactions"):
            return json.dumps(self.reactions)
        raise AssertionError(args)

    def posted(self):
        return [a for a in self.calls if a[0] == "api" and a[1].endswith("/comments")]


class IssueToPr(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        base = Path(self.td.name)
        self.repo = base / "repo"
        (self.repo / ".git").mkdir(parents=True)
        (self.repo / ".rig").mkdir()
        (self.repo / ".rig" / "harness.toml").write_text('parent = "claude"\n\n[project]\nenabled = true\n\n[issues]\nenabled = true\n')
        self.env = mock.patch.dict(os.environ, {"RIG_HOME": str(base / "home")})
        self.env.start()
        self.gh = FakeGh()

    def tearDown(self):
        self.env.stop()
        self.td.cleanup()

    def poll(self):
        return itp.poll(self.repo, self.gh, parent="claude", enabled=["codex"])

    def test_off_until_the_project_turns_it_on(self):
        (self.repo / ".rig" / "harness.toml").write_text('parent = "claude"\n')
        with self.assertRaises(itp.IssueError):
            self.poll()
        self.assertEqual(self.gh.calls, [])

    def test_a_labelled_issue_gets_one_plan_comment(self):
        self.assertEqual(self.poll()["did"], ["planned #7"])
        self.assertEqual(self.poll()["did"], [])
        self.assertEqual(len(self.gh.posted()), 1)
        body = self.gh.posted()[0][3]
        self.assertIn(itp.MARKER, body)
        self.assertIn("- limit by IP", body)
        self.assertIn("- return 429", body)
        self.assertIn("rig/7-rate-limit-login", body)
        self.assertIn("👍", body)

    def test_only_your_thumbs_up_counts(self):
        self.poll()
        self.gh.reactions = [{"content": "+1", "user": {"login": "stranger"}}]
        self.assertEqual(self.poll()["did"], [])
        self.assertEqual(itp.view(self.repo)["issues"][0]["state"], "planned")
        self.gh.reactions.append({"content": "+1", "user": {"login": "owner"}})
        self.assertEqual(self.poll()["did"], ["approved #7", "queued #7"])
        self.assertEqual(itp.view(self.repo)["issues"][0]["state"], "queued")

    def test_queued_once_with_the_issue_as_the_brief(self):
        self.poll()
        self.gh.reactions = [{"content": "+1", "user": {"login": "owner"}}]
        self.poll()
        self.poll()
        pending = work_queue.list_items(self.repo)
        self.assertEqual(len(pending), 1)
        self.assertIn("issue #7", pending[0]["text"])

    def test_your_thumbs_down_leaves_the_issue_alone(self):
        self.poll()
        self.gh.reactions = [{"content": "-1", "user": {"login": "owner"}}]
        self.assertEqual(self.poll()["did"], ["declined #7"])
        self.assertEqual(work_queue.list_items(self.repo), [])
        self.assertEqual(self.poll()["did"], [])

    def test_ship_opens_a_draft_for_a_queued_issue_only(self):
        self.poll()
        with self.assertRaises(itp.IssueError):
            itp.ship(self.repo, 7, shipper=lambda *a, **k: {})
        self.gh.reactions = [{"content": "+1", "user": {"login": "owner"}}]
        self.poll()
        seen = {}

        def shipper(repo, mode, title, body, draft=False):
            seen.update(mode=mode, title=title, body=body, draft=draft)
            return {"shipped": "pr", "url": "https://github.com/o/r/pull/9"}

        itp.ship(self.repo, 7, shipper=shipper)
        self.assertEqual((seen["mode"], seen["draft"], seen["title"]), ("pr", True, "Rate-limit login"))
        self.assertTrue(seen["body"].startswith("Closes #7"))
        state = itp.view(self.repo)["issues"][0]
        self.assertEqual((state["state"], state["pr"]), ("shipped", "https://github.com/o/r/pull/9"))

    def test_over_the_api(self):
        import local_api
        api = local_api.Api(self.repo, "t" * 64)
        call = lambda method, params=None: api.dispatch({"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}, "token": "t" * 64})
        rows = {r["method"]: r["mutates"] for r in call("api.methods")["result"]["methods"]}
        self.assertEqual((rows["issues.get"], rows["issues.poll"], rows["issues.ship"]), (False, True, True))
        out = call("issues.get")["result"]
        self.assertEqual((out["enabled"], out["label"], out["issues"]), (True, "rig", []))
        self.assertEqual(call("issues.ship", {"number": "x"})["error"]["code"], local_api.INVALID_PARAMS)
        self.assertEqual(call("issues.ship", {"number": 7})["error"]["code"], local_api.INVALID_PARAMS)

    def test_the_setting_round_trips(self):
        import ui_settings
        self.assertTrue(ui_settings.read(self.repo)["issues_enabled"])
        ui_settings.save(self.repo, "issues.enabled", False, "repo")
        self.assertFalse(ui_settings.read(self.repo)["issues_enabled"])


if __name__ == "__main__":
    unittest.main()
