#!/usr/bin/env python3
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import harness  # noqa: E402
import jobs  # noqa: E402
import route  # noqa: E402


def _stub_path(extra: Path | None = None) -> str:
    parts = [
        "/usr/bin",
        "/bin",
        "/usr/sbin",
        "/sbin",
        str(Path(sys.executable).resolve().parent),
    ]
    if extra:
        parts.insert(0, str(extra))
    return ":".join(parts)


def _fake_bin(folder: Path, name: str) -> None:
    path = folder / name
    path.write_text("#!/bin/sh\nexit 0\n")
    path.chmod(0o755)


def _write_harness(repo: Path, body: str) -> None:
    path = repo / ".rig" / "harness.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)


class RepoRoot(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.home = Path(self.td.name) / "home"
        self.kit = self.home / ".rig"
        (self.kit / "scripts").mkdir(parents=True)
        self.proj = self.home / "BTMHv2"
        (self.proj / ".rig").mkdir(parents=True)
        (self.proj / "src").mkdir()
        (self.proj / ".rig" / "harness.toml").write_text(
            'parent = "pi"\n\n[workers]\ngrok = false\npi = true\n'
        )
        self.downloads = self.home / "Downloads"
        self.downloads.mkdir()
        self.bare = self.home / "bare-dot-rig"
        (self.bare / ".rig").mkdir(parents=True)
        (self.bare / "nested").mkdir()
        self._env = {k: os.environ.get(k) for k in ("HOME", "RIG_HOME")}
        os.environ["HOME"] = str(self.home)
        os.environ["RIG_HOME"] = str(self.kit)

    def tearDown(self):
        for key, val in self._env.items():
            if val is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = val
        self.td.cleanup()

    def test_home_kit_is_not_a_project(self):
        self.assertNotEqual(jobs.repo_root(str(self.kit / "scripts")), self.home)
        self.assertNotEqual(jobs.repo_root(str(self.kit / "scripts")), self.kit)
        self.assertNotEqual(jobs.repo_root(str(self.downloads)), self.home)
        self.assertNotEqual(jobs.repo_root(str(self.bare / "nested")), self.bare)

    def test_project_with_harness_toml_wins(self):
        want = self.proj.resolve()
        self.assertEqual(jobs.repo_root(str(self.proj)), want)
        self.assertEqual(jobs.repo_root(str(self.proj / "src")), want)

    def test_bash_repo_root_matches(self):
        env = os.environ.copy()
        env["HOME"] = str(self.home)
        env["RIG_HOME"] = str(self.kit)
        script = f"source '{ROOT / 'scripts' / 'detect-binaries.sh'}' && repo_root"
        from_kit = subprocess.run(
            ["bash", "-c", script],
            cwd=self.kit / "scripts",
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(from_kit.returncode, 0, from_kit.stderr)
        self.assertNotEqual(from_kit.stdout.strip(), str(self.home))
        self.assertNotEqual(from_kit.stdout.strip(), str(self.kit))
        from_proj = subprocess.run(
            ["bash", "-c", script],
            cwd=self.proj / "src",
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(from_proj.returncode, 0, from_proj.stderr)
        self.assertEqual(from_proj.stdout.strip(), str(self.proj.resolve()))


class ParseAndPick(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.repo = Path(self.td.name)
        (self.repo / ".git").mkdir()
        self.bins = self.repo / "bins"
        self.bins.mkdir()
        _fake_bin(self.bins, "grok")
        _fake_bin(self.bins, "pi")
        self._env = {
            k: os.environ.get(k)
            for k in ("PATH", "RIG_PARENT", "CLAUDECODE", "CLAUDE_CODE", "RIG_SKIP_MODEL_CATALOG")
        }
        os.environ["PATH"] = _stub_path(self.bins)
        os.environ["RIG_SKIP_MODEL_CATALOG"] = "1"
        os.environ.pop("CLAUDECODE", None)
        os.environ.pop("CLAUDE_CODE", None)

    def tearDown(self):
        for key, val in self._env.items():
            if val is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = val
        self.td.cleanup()

    def test_missing_harness_all_workers_false(self):
        flags = harness.parse_harness(harness.harness_path(self.repo))["workers"]
        self.assertTrue(flags)
        self.assertTrue(all(v == "false" for v in flags.values()))

    def test_existing_file_missing_key_is_false(self):
        _write_harness(self.repo, 'parent = "pi"\n\n[workers]\npi = true\n')
        flags = harness.parse_harness(harness.harness_path(self.repo))["workers"]
        self.assertEqual(flags["grok"], "false")
        self.assertEqual(flags["claude"], "false")
        self.assertEqual(flags["pi"], "true")

    def test_missing_harness_live_pi_pick_is_native_not_grok(self):
        os.environ["RIG_PARENT"] = "pi"
        eff = harness.effective_workers(self.repo, "pi")
        self.assertNotIn("grok", eff)
        choice = route.pick("pi", eff, "implement", "add a header")
        self.assertEqual(choice["worker"], "pi")
        self.assertEqual(choice["spawn"], "native")

    def test_grok_false_pi_true_live_pi_is_native(self):
        _write_harness(
            self.repo,
            'parent = "pi"\n\n[workers]\n'
            "codex = false\ngrok = false\nclaude = false\ncursor = false\n"
            "opencode = false\nomp = false\npi = true\nagy = false\n",
        )
        os.environ["RIG_PARENT"] = "pi"
        eff = harness.effective_workers(self.repo, "pi")
        self.assertNotIn("grok", eff)
        self.assertNotIn("pi", eff)
        choice = route.pick("pi", eff, "implement", "add a header")
        self.assertEqual(choice["worker"], "pi")
        self.assertEqual(choice["spawn"], "native")

    def test_grok_false_live_grok_is_native(self):
        _write_harness(
            self.repo,
            'parent = "codex"\n\n[workers]\n'
            "codex = false\ngrok = false\nclaude = false\ncursor = false\n"
            "opencode = false\nomp = false\npi = false\nagy = false\n",
        )
        os.environ["RIG_PARENT"] = "grok"
        eff = harness.effective_workers(self.repo, "grok")
        self.assertNotIn("grok", eff)
        choice = route.pick("grok", eff, "implement", "add a header")
        self.assertEqual(choice["worker"], "grok")
        self.assertEqual(choice["spawn"], "native")


class StartJob(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.repo = Path(self.td.name)
        (self.repo / ".git").mkdir()
        _write_harness(
            self.repo,
            'parent = "pi"\n\n[workers]\n'
            "codex = false\ngrok = false\nclaude = false\ncursor = false\n"
            "opencode = false\nomp = false\npi = true\nagy = false\n",
        )
        (self.repo / ".rig" / "jobs").mkdir(parents=True, exist_ok=True)
        self._env = {k: os.environ.get(k) for k in ("RIG_PARENT", "CLAUDECODE", "CLAUDE_CODE")}
        os.environ.pop("CLAUDECODE", None)
        os.environ.pop("CLAUDE_CODE", None)

    def tearDown(self):
        for key, val in self._env.items():
            if val is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = val
        self.td.cleanup()

    def test_start_grok_false_live_pi_errors(self):
        with self.assertRaises(SystemExit) as ctx:
            jobs.start_job(self.repo, worker="grok", live="pi")
        self.assertIn("off in harness", str(ctx.exception))

    def test_start_grok_false_live_grok_ok(self):
        job_id = jobs.start_job(self.repo, worker="grok", live="grok")
        meta = json.loads((self.repo / ".rig" / "jobs" / job_id / "meta.json").read_text())
        self.assertEqual(meta["worker"], "grok")
        self.assertEqual(meta["status"], "running")

    def test_record_grok_false_live_pi_errors(self):
        with self.assertRaises(SystemExit) as ctx:
            jobs.record_job(self.repo, worker="grok", live="pi")
        self.assertIn("off in harness", str(ctx.exception))

    def test_record_grok_false_live_grok_ok(self):
        text = jobs.record_job(self.repo, worker="grok", live="grok", status="ok", summary="native")
        self.assertIn("status=ok", text)
        self.assertIn("worker=grok", text)


class LiveParent(unittest.TestCase):
    def test_pi_prefix_is_pi(self):
        self.assertEqual(harness._comm_parent("pi", 1), "pi")
        self.assertEqual(harness._comm_parent("pi-foo", 1), "pi")
        self.assertEqual(harness._comm_parent("pi-coding-agent", 1), "pi")
        self.assertEqual(harness._comm_parent("node", 1), "")
        self.assertEqual(harness._comm_parent("pip", 1), "")


if __name__ == "__main__":
    unittest.main()
