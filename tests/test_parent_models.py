#!/usr/bin/env python3
"""Every model a parent can be told to use, for the shell's /model."""
import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import local_api  # noqa: E402
import parent_models  # noqa: E402

# a stand-in for `codex app-server`: answers initialize and model/list, one JSON line each
FAKE_CODEX = r'''#!/usr/bin/env python3
import json, sys
assert sys.argv[1:] == ["app-server"]
for line in sys.stdin:
    msg = json.loads(line)
    if msg.get("id") == 0:
        print(json.dumps({"id": 0, "result": {"userAgent": "codex"}}), flush=True)
    elif msg.get("method") == "model/list":
        print(json.dumps({"id": 1, "result": {"data": [
            {"id": "gpt-6.1-sol", "model": "gpt-6.1-sol", "displayName": "gpt-6.1-sol", "description": "Frontier model\nmore", "isDefault": True},
            {"id": "gpt-6-luna", "model": "gpt-6-luna", "displayName": "gpt-6-luna", "description": "Fast"}], "nextCursor": None}}), flush=True)
'''


class ParentModels(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        base = Path(self.td.name)
        self.home, self.bin = base / "home", base / "bin"
        self.bin.mkdir()
        self.env = mock.patch.dict(os.environ, {"HOME": str(self.home), "RIG_HOME": str(self.home / ".rig"),
                                                "PATH": str(self.bin), "CODEX_HOME": str(self.home / ".codex"),
                                                "RIG_MODEL_CATALOG_CACHE": str(base / "catalogs.json")})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.td.cleanup()

    def names(self, parent):
        return [r["name"] for r in parent_models.models(parent)]

    def test_codex_lists_its_own_models_and_they_are_kept_an_hour(self):
        codex = self.bin / "codex"
        codex.write_text(f"#!{sys.executable}\n" + FAKE_CODEX.split("\n", 1)[1])
        codex.chmod(codex.stat().st_mode | stat.S_IEXEC)
        rows = parent_models.models("codex")
        self.assertEqual(rows[:2], [{"name": "gpt-6.1-sol", "detail": "Frontier model"}, {"name": "gpt-6-luna", "detail": "Fast"}])
        self.assertIn("gpt-5.6-terra", [r["name"] for r in rows], "the models Rig routes codex to")
        self.assertEqual(len({r["name"] for r in rows}), len(rows), "each once")
        codex.unlink()
        self.assertEqual(parent_models.models("codex")[0]["name"], "gpt-6.1-sol", "from the cache, without asking again")

    def test_codex_without_its_cli_reads_its_files(self):
        (self.home / ".codex").mkdir(parents=True)
        (self.home / ".codex" / "models_cache.json").write_text(json.dumps({"models": [
            {"slug": "gpt-6.1-sol", "display_name": "gpt-6.1-sol", "description": "Frontier", "visibility": "list"},
            {"slug": "old-model", "visibility": "hide"}]}))
        (self.home / ".codex" / "config.toml").write_text('model = "my-profile-model"\n')
        names = self.names("codex")
        self.assertEqual(names[:2], ["gpt-6.1-sol", "my-profile-model"])
        self.assertNotIn("old-model", names, "a hidden model is not offered")

    def test_claude_offers_its_aliases_and_full_names(self):
        names = self.names("claude")
        self.assertEqual(names[:5], ["opus", "sonnet", "haiku", "opusplan", "sonnet[1m]"])
        self.assertIn("claude-sonnet-5-5", names)

    def test_over_the_api(self):
        repo = Path(self.td.name) / "repo"
        (repo / ".git").mkdir(parents=True)
        (repo / ".rig").mkdir()
        (repo / ".rig" / "harness.toml").write_text('parent = "claude"\n\n[project]\nenabled = true\n')
        api = local_api.Api(repo, "t" * 64)
        call = lambda params: api.dispatch({"jsonrpc": "2.0", "id": 1, "method": "chat.models", "params": params, "token": "t" * 64})
        result = call({})["result"]
        self.assertEqual((result["parent"], result["models"][0]["name"]), ("claude", "opus"))
        self.assertEqual(call({"parent": "nope"})["error"]["code"], local_api.INVALID_PARAMS)


if __name__ == "__main__":
    unittest.main()
