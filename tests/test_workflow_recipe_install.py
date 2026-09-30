"""Bundled recipe data must remain available outside the source checkout."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class WorkflowRecipeInstall(unittest.TestCase):
    def test_fresh_setup_installs_and_tracks_recipe_catalog(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary) / "home"
            repo = Path(temporary) / "project"
            home.mkdir()
            repo.mkdir()
            kit = home / ".rig"
            manual = kit / "templates/workflows/custom.json"
            manual.parent.mkdir(parents=True)
            manual.write_text('{"local": "manual file"}\n')
            manual.chmod(0o640)
            env = {**os.environ, "HOME": str(home), "RIG_HOME": str(kit), "RIG_SRC": str(ROOT),
                   "RIG_SKIP_UPDATE_CHECK": "1", "RIG_SKIP_MODEL_CATALOG": "1", "PYTHONDONTWRITEBYTECODE": "1"}
            for key in ("RIG_INSTALL_TRANSACTION", "RIG_LIFECYCLE_FD", "RIG_LIVE", "RIG_JOB_ID", "RIG_JOB_DIR"):
                env.pop(key, None)
            installed = subprocess.run(["bash", str(ROOT / "bin/rig"), "setup", "--no-cua-driver", "--no-browser-skill", "--no-mimo"],
                                       cwd=repo, env=env, capture_output=True, text=True, timeout=60)
            self.assertEqual(installed.returncode, 0, installed.stderr)
            manifest = json.loads((kit / "install-manifest.json").read_text())
            self.assertEqual(manual.read_text(), '{"local": "manual file"}\n')
            self.assertEqual(manual.stat().st_mode & 0o777, 0o640)
            self.assertNotIn(str(manual), manifest["entries"])
            for source in (ROOT / "templates/workflows").glob("*.json"):
                target = kit / "templates/workflows" / source.name
                self.assertEqual(target.read_bytes(), source.read_bytes())
                self.assertEqual(manifest["entries"][str(target)]["after"]["kind"], "file")
                self.assertEqual(target.stat().st_mode & 0o777, 0o600)
            env.pop("RIG_SRC")
            listed = subprocess.run(["bash", str(kit / "bin/rig"), "workflow", "recipe", "list", "--json"],
                                    cwd=repo, env=env, capture_output=True, text=True, timeout=15)
            self.assertEqual(listed.returncode, 0, listed.stderr)
            self.assertEqual([row["name"] for row in json.loads(listed.stdout)], ["bugfix", "research-implement", "ui-validation"])
            (repo / "app.py").write_text("pass\n")
            preview = subprocess.run(["bash", str(kit / "bin/rig"), "workflow", "recipe", "preview", "ui-validation", "--json"],
                                     cwd=repo, env=env, input=json.dumps({"task": "Inspect save UI", "files": ["app.py"]}),
                                     capture_output=True, text=True, timeout=15)
            self.assertEqual(preview.returncode, 0, preview.stderr)
            self.assertTrue(json.loads(preview.stdout)["preview_only"])
            self.assertTrue(json.loads(preview.stdout)["nodes"][0]["parent_only"])
            self.assertFalse((repo / ".rig").exists())
