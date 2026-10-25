#!/usr/bin/env python3
"""Fleet change: who calls what from manifests and imports, the two-repo limit, linked draft PRs with one description."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import applies  # noqa: E402
import fleet_change as fc  # noqa: E402


def make(base: Path, name: str, files: dict) -> Path:
    repo = base / name
    (repo / ".git").mkdir(parents=True)
    for rel, text in files.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return repo


class Fleet(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        base = Path(self.td.name)
        self.lib = make(base, "billing-lib", {
            "pyproject.toml": '[project]\nname = "billing-lib"\ndependencies = ["requests>=2"]\n',
            "billing_lib/__init__.py": "",
        })
        self.web = make(base, "web", {
            "package.json": json.dumps({"name": "web", "dependencies": {"react": "1"}}),
            "requirements.txt": "billing_lib==1.2\n# note\nflask\n",
            "app/server.py": "from billing_lib import charge\nimport flask\n",
            "src/ui.tsx": "import React from 'react'\nimport x from '@acme/other/deep'\n",
        })
        self.other = make(base, "other", {"go.mod": "module github.com/acme/other\n\nrequire github.com/x/y v1.0.0\n"})

    def tearDown(self):
        self.td.cleanup()

    def test_manifests_say_what_a_repository_provides_and_needs(self):
        m = fc.manifest(self.lib)
        self.assertIn("billing-lib", m["provides"])
        self.assertIn("requests", m["depends"])
        w = fc.manifest(self.web)
        self.assertEqual((w["provides"], "react" in w["depends"], "billing-lib" in w["depends"], "flask" in w["depends"]), (["web"], True, True, True))
        self.assertEqual(fc.manifest(self.other)["provides"], ["github.com/acme/other"])

    def test_who_calls_what_needs_a_dependency_or_an_import_of_a_provided_name(self):
        graph = fc.calls([self.web, self.lib])
        self.assertEqual(len(graph["edges"]), 1)
        edge = graph["edges"][0]
        self.assertEqual((Path(edge["from"]).name, Path(edge["to"]).name), ("web", "billing-lib"))
        self.assertEqual(edge["via"], ["declares billing-lib", "imports billing_lib"])
        self.assertEqual(edge["files"], ["app/server.py"])

    def test_unrelated_repositories_have_no_edge(self):
        self.assertEqual(fc.calls([self.lib, self.other])["edges"], [])

    def test_the_provider_merges_first(self):
        self.assertEqual([Path(p).name for p in fc.order([self.web, self.lib])], ["billing-lib", "web"])

    def test_two_git_repositories_and_no_more(self):
        with self.assertRaises(fc.FleetError):
            fc.check_repos([self.lib])
        with self.assertRaises(fc.FleetError):
            fc.check_repos([self.lib, self.web, self.other])
        with self.assertRaises(fc.FleetError):
            fc.check_repos([self.lib, Path(self.td.name) / "nowhere"])

    def test_ship_opens_drafts_in_order_then_one_description_on_all(self):
        opened, edited = [], []

        def shipper(repo, mode, title, body, draft=False):
            opened.append((Path(repo).name, mode, title, draft))
            return {"shipped": "pr", "url": f"https://github.com/acme/{Path(repo).name}/pull/{len(opened)}"}

        def gh(args, cwd):
            edited.append((args, Path(cwd).name))
            return ""

        done = fc.ship([self.web, self.lib], "Charge in cents", "Moves money as integer cents.", shipper=shipper, gh=gh)
        self.assertEqual(opened, [("billing-lib", "pr", "Charge in cents", True), ("web", "pr", "Charge in cents", True)])
        self.assertEqual([e[1] for e in edited], ["billing-lib", "web"])
        self.assertTrue(all(e[0][:2] == ["pr", "edit"] for e in edited))
        text = edited[0][0][4]
        self.assertEqual(text, edited[1][0][4], "the same description on both")
        self.assertIn("Moves money as integer cents.", text)
        self.assertIn("1. billing-lib: https://github.com/acme/billing-lib/pull/1", text)
        self.assertIn("2. web: https://github.com/acme/web/pull/2", text)
        self.assertIn("web declares billing-lib, imports billing_lib from billing-lib", text)
        self.assertIn("Merge billing-lib first.", text)
        self.assertEqual(len(done["prs"]), 2)

    def test_a_failure_in_the_second_repository_names_what_was_already_opened(self):
        calls = []

        def shipper(repo, mode, title, body, draft=False):
            calls.append(repo)
            if len(calls) == 2:
                raise applies.ApplyError("nothing to ship")
            return {"url": "https://github.com/acme/billing-lib/pull/1"}

        with self.assertRaises(fc.FleetError) as raised:
            fc.ship([self.web, self.lib], "t", shipper=shipper, gh=lambda *a: "")
        self.assertIn("web: nothing to ship", str(raised.exception))
        self.assertIn("pull/1", str(raised.exception))

    def test_over_the_api(self):
        import local_api
        with mock.patch.dict(os.environ, {"RIG_HOME": str(Path(self.td.name) / "home")}):
            (self.web / ".rig").mkdir()
            api = local_api.Api(self.web, "t" * 64)
            call = lambda method, params=None: api.dispatch({"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}, "token": "t" * 64})
            rows = {r["method"]: r["mutates"] for r in call("api.methods")["result"]["methods"]}
            self.assertEqual((rows["fleet.map"], rows["fleet.ship"]), (False, True))
            out = call("fleet.map", {"with": str(self.lib)})["result"]
            self.assertEqual([Path(e["to"]).name for e in out["edges"]], ["billing-lib"])
            self.assertEqual(call("fleet.map")["error"]["code"], local_api.INVALID_PARAMS)
            self.assertEqual(call("fleet.map", {"with": "/nowhere"})["error"]["code"], local_api.INVALID_PARAMS)
            self.assertEqual(call("fleet.ship", {"with": str(self.lib), "title": 5})["error"]["code"], local_api.INVALID_PARAMS)


if __name__ == "__main__":
    unittest.main()
