#!/usr/bin/env python3
import hashlib
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import dc_client  # noqa: E402
import sandbox_env  # noqa: E402

FAKE_DC = r'''#!/bin/sh
# Fake dc-cli for tests. Behaviour comes from FAKE_DC_* env vars; argv is logged.
echo "$*" >> "$FAKE_DC_LOG"
case "$1" in
  --version) echo "dc ${FAKE_DC_VERSION:-0.23.0}" ;;
  try)  if [ -n "$FAKE_DC_FAIL" ]; then echo "boom" >&2; exit 1; fi
        if [ "${FAKE_DC_TRY+set}" = set ]; then printf '%s\n' "$FAKE_DC_TRY"; else printf '%s\n' '{"image": "node:22-bookworm"}'; fi ;;
  up|down) if [ -n "$FAKE_DC_FAIL" ]; then echo "engine not running" >&2; exit 2; fi; echo "dc $1 ok" ;;
  ls) if [ -n "$FAKE_DC_FAIL" ]; then echo "engine not running" >&2; exit 2; fi
      if [ "${FAKE_DC_LS+set}" = set ]; then printf '%s\n' "$FAKE_DC_LS"; else echo '[]'; fi ;;
  *) exit 64 ;;
esac
'''


def make_repo(root: Path, enabled: bool = True) -> Path:
    (root / ".git").mkdir(exist_ok=True)
    (root / ".rig").mkdir(exist_ok=True)
    (root / ".rig" / "harness.toml").write_text(f"[project]\nenabled = {'true' if enabled else 'false'}\n")
    return root


def tree_hash(folder: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(folder.rglob("*")):
        if path.is_file():
            digest.update(str(path.relative_to(folder)).encode() + path.read_bytes())
    return digest.hexdigest()


class Base(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.base = Path(self.td.name)
        self.repo = make_repo(self.base / "repo") if (self.base / "repo").mkdir() is None else None
        self.log = self.base / "dc.log"
        self.fake = self.base / "dc"
        self.fake.write_text(FAKE_DC)
        self.fake.chmod(0o755)
        self.env = mock.patch.dict(os.environ, {"RIG_DC_BIN": str(self.fake), "FAKE_DC_LOG": str(self.log)})
        self.env.start()
        self.empty = self.base / "empty-path"
        self.empty.mkdir()
        for key in ("FAKE_DC_FAIL", "FAKE_DC_TRY", "FAKE_DC_LS", "FAKE_DC_VERSION"):
            os.environ.pop(key, None)

    def tearDown(self):
        self.env.stop()
        self.td.cleanup()

    def calls(self):
        return self.log.read_text().splitlines() if self.log.exists() else []


class Detection(Base):
    def test_kinds(self):
        self.assertEqual(sandbox_env.detect_kind(self.repo), "none")
        (self.repo / "docker-compose.yml").write_text("services: {}\n")
        self.assertEqual(sandbox_env.detect_kind(self.repo), "compose")
        (self.repo / ".devcontainer").mkdir()
        (self.repo / ".devcontainer" / "devcontainer.json").write_text("{}")
        self.assertEqual(sandbox_env.detect_kind(self.repo), "devcontainer")

    def test_other_devcontainer_layouts(self):
        (self.repo / ".devcontainer.json").write_text("{}")
        self.assertEqual(sandbox_env.detect_kind(self.repo), "devcontainer")
        (self.repo / ".devcontainer.json").unlink()
        nested = self.repo / ".devcontainer" / "python"
        nested.mkdir(parents=True)
        (nested / "devcontainer.json").write_text("{}")
        self.assertEqual(sandbox_env.detect_kind(self.repo), "devcontainer")


class InitSandbox(Base):
    def test_folder_without_config_gets_a_generated_override(self):
        report = sandbox_env.init_sandbox(self.repo)
        path = self.repo / ".rig" / "sandbox" / "override.json"
        self.assertEqual((report["kind"], report["wrote"]), ("none", True))
        self.assertEqual(json.loads(path.read_text()), {"image": "node:22-bookworm"})
        self.assertEqual(self.calls(), ["try --print"])

    def test_existing_override_is_kept_unless_forced(self):
        sandbox_env.init_sandbox(self.repo)
        path = self.repo / ".rig" / "sandbox" / "override.json"
        path.write_text('{"image": "mine"}\n')
        report = sandbox_env.init_sandbox(self.repo)
        self.assertFalse(report["wrote"])
        self.assertEqual(json.loads(path.read_text()), {"image": "mine"})
        os.environ["FAKE_DC_TRY"] = '{"image": "fresh"}'
        self.assertTrue(sandbox_env.init_sandbox(self.repo, force=True)["wrote"])
        self.assertEqual(json.loads(path.read_text()), {"image": "fresh"})

    def test_projects_own_devcontainer_is_never_touched_and_dc_is_not_called(self):
        folder = self.repo / ".devcontainer"
        folder.mkdir()
        (folder / "devcontainer.json").write_text('{"name": "mine"}\n')
        before = tree_hash(self.repo / ".devcontainer")
        report = sandbox_env.init_sandbox(self.repo)
        self.assertEqual((report["kind"], report["wrote"]), ("devcontainer", False))
        self.assertEqual(tree_hash(self.repo / ".devcontainer"), before)
        self.assertFalse((self.repo / ".rig" / "sandbox").exists())
        self.assertEqual(self.calls(), [])

    def test_failures_leave_no_partial_override(self):
        os.environ["FAKE_DC_FAIL"] = "1"
        with self.assertRaisesRegex(dc_client.DcError, "failed"):
            sandbox_env.init_sandbox(self.repo)
        for bad in ("not json", "[]", "{}", ""):
            os.environ.pop("FAKE_DC_FAIL", None)
            os.environ["FAKE_DC_TRY"] = bad
            with self.assertRaises(dc_client.DcError):
                sandbox_env.init_sandbox(self.repo)
        self.assertFalse((self.repo / ".rig" / "sandbox" / "override.json").exists())
        self.assertEqual(list((self.repo / ".rig").glob("sandbox/*.tmp")), [])

    def test_missing_dc_gives_an_install_hint_and_disabled_projects_are_refused(self):
        with mock.patch.dict(os.environ, {"RIG_DC_BIN": "", "PATH": str(self.empty)}):
            with self.assertRaisesRegex(dc_client.DcError, "not installed"):
                sandbox_env.init_sandbox(self.repo)
        (self.repo / ".rig" / "harness.toml").write_text("[project]\nenabled = false\n")
        with self.assertRaises(ValueError):
            sandbox_env.init_sandbox(self.repo)


class Lifecycle(Base):
    def test_up_passes_the_override_only_for_folders_without_config(self):
        sandbox_env.init_sandbox(self.repo)
        self.log.unlink()
        self.assertEqual(sandbox_env.env_up(self.repo), 0)
        self.assertEqual(self.calls(), [f"up --override-config {self.repo.resolve()}/.rig/sandbox/effective.json"])
        (self.repo / "compose.yaml").write_text("services: {}\n")
        self.log.unlink()
        sandbox_env.env_up(self.repo)
        self.assertEqual(self.calls(), ["up"])

    def test_down_and_exit_status_pass_through(self):
        self.assertEqual(sandbox_env.env_down(self.repo), 0)
        os.environ["FAKE_DC_FAIL"] = "1"
        self.assertEqual(sandbox_env.env_up(self.repo), 2)
        self.assertEqual(self.calls()[0], "down")

    def test_commands_run_in_the_repository_folder(self):
        marker = self.base / "cwd.txt"
        self.fake.write_text("#!/bin/sh\npwd > " + str(marker) + "\n")
        sandbox_env.env_down(self.repo)
        self.assertEqual(marker.read_text().strip(), str(self.repo.resolve()))

    def test_disabled_project_is_refused(self):
        (self.repo / ".rig" / "harness.toml").write_text("[project]\nenabled = false\n")
        with self.assertRaises(ValueError):
            sandbox_env.env_up(self.repo)


class Effective(Base):
    def setUp(self):
        super().setUp()
        self.home = self.base / "home"
        (self.home / ".claude").mkdir(parents=True)
        (self.home / ".claude.json").write_text("{}")
        self.runtime = self.base / "runtime"
        self.runtime.mkdir()

    def config(self, credentials, generated=None):
        return sandbox_env.effective_config(self.repo, generated or {"image": "node:22"}, credentials, self.runtime, self.home)

    def test_repo_is_mounted_at_its_own_path_and_runtime_is_read_only(self):
        cfg = self.config({})
        repo = str(self.repo.resolve())
        self.assertEqual(cfg["workspaceMount"], f"source={repo},target={repo},type=bind")
        self.assertEqual(cfg["workspaceFolder"], repo)
        self.assertEqual(cfg["image"], "node:22")
        self.assertEqual(cfg["mounts"], [f"source={self.runtime},target={self.runtime},type=bind,readonly"])

    def test_only_opted_in_credentials_that_exist_are_mounted_read_only(self):
        mounts = self.config({"claude": "mount-ro", "codex": "mount-ro"})["mounts"]
        self.assertIn(f"source={self.home}/.claude,target={self.home}/.claude,type=bind,readonly", mounts)
        self.assertIn(f"source={self.home}/.claude.json,target={self.home}/.claude.json,type=bind,readonly", mounts)
        self.assertFalse([m for m in mounts if ".codex" in m], "a path that does not exist is never mounted")
        self.assertFalse([m for m in self.config({"claude": "none"})["mounts"] if ".claude" in m])
        self.assertTrue(all(m.endswith("readonly") for m in mounts))

    def test_existing_mounts_are_kept_and_not_duplicated(self):
        ours = f"source={self.runtime},target={self.runtime},type=bind,readonly"
        mounts = self.config({}, {"mounts": ["source=a,target=/a,type=bind", {"type": "volume"}, ours, 7]})["mounts"]
        self.assertEqual(mounts, ["source=a,target=/a,type=bind", {"type": "volume"}, ours])

    def test_up_uses_a_regenerated_effective_config_and_never_the_projects_files(self):
        harness_path = self.repo / ".rig" / "harness.toml"
        harness_path.write_text('[project]\nenabled = true\n\n[sandbox.workers]\nclaude = "mount-ro"\n')
        sandbox_env.init_sandbox(self.repo)
        with mock.patch.dict(os.environ, {"HOME": str(self.home)}):
            sandbox_env.env_up(self.repo)
            first = json.loads(sandbox_env.effective_path(self.repo).read_text())
            self.assertTrue(any(".claude" in m for m in first["mounts"]))
            harness_path.write_text('[project]\nenabled = true\n\n[sandbox.workers]\nclaude = "none"\n')
            sandbox_env.env_up(self.repo)
        second = json.loads(sandbox_env.effective_path(self.repo).read_text())
        self.assertFalse(any(".claude" in m for m in second["mounts"]), "stale mounts must not survive a config change")
        self.assertEqual(json.loads(sandbox_env.override_path(self.repo).read_text()), {"image": "node:22-bookworm"})
        self.assertFalse((self.repo / ".devcontainer").exists())

    def test_a_projects_own_config_is_used_as_is_with_a_note_in_container_mode(self):
        import contextlib
        import io
        (self.repo / ".devcontainer").mkdir()
        (self.repo / ".devcontainer" / "devcontainer.json").write_text('{"name": "mine"}\n')
        before = tree_hash(self.repo / ".devcontainer")
        (self.repo / ".rig" / "harness.toml").write_text('[project]\nenabled = true\n\n[sandbox]\nprovider = "devcontainer"\n')
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            sandbox_env.env_up(self.repo)
        self.assertIn("same paths", err.getvalue())
        self.assertEqual(self.calls(), ["up"])
        self.assertEqual(tree_hash(self.repo / ".devcontainer"), before)
        self.assertFalse(sandbox_env.effective_path(self.repo).exists())
        (self.repo / ".rig" / "harness.toml").write_text("[project]\nenabled = true\n")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            sandbox_env.env_up(self.repo)
        self.assertEqual(err.getvalue(), "", "no note for the host provider")

    def test_a_corrupt_override_is_reported_not_passed_to_dc(self):
        path = sandbox_env.override_path(self.repo)
        path.parent.mkdir(parents=True)
        path.write_text("[1]")
        with self.assertRaisesRegex(dc_client.DcError, "not a JSON object"):
            sandbox_env.env_up(self.repo)
        self.assertEqual(self.calls(), [])


class Status(Base):
    def test_status_matches_containers_by_repo_path_and_reports_pin_state(self):
        os.environ["FAKE_DC_LS"] = json.dumps([
            {"name": "mine", "state": "running", "workspace": str(self.repo.resolve())},
            {"name": "other", "state": "running", "workspace": "/somewhere/else"}])
        status = sandbox_env.env_status(self.repo)
        self.assertEqual([c["name"] for c in status["containers"]], ["mine"])
        self.assertEqual((status["dc"]["version"], status["dc"]["pin_state"]), ("0.23.0", "unpinned"))
        self.assertIn("container   mine  running", sandbox_env.format_status(status))

    def test_accepts_an_object_wrapping_the_list(self):
        os.environ["FAKE_DC_LS"] = json.dumps({"containers": [{"name": "w", "path": str(self.repo.resolve())}]})
        self.assertEqual(len(sandbox_env.env_status(self.repo)["containers"]), 1)

    def test_dc_failure_and_missing_dc_are_reported_not_raised(self):
        os.environ["FAKE_DC_FAIL"] = "1"
        status = sandbox_env.env_status(self.repo)
        self.assertIn("engine not running", status["error"])
        self.assertEqual(status["containers"], [])
        with mock.patch.dict(os.environ, {"RIG_DC_BIN": "", "PATH": str(self.empty)}):
            gone = sandbox_env.env_status(self.repo)
        self.assertEqual((gone["dc"]["found"], gone["dc"]["pin_state"]), (False, "missing"))
        self.assertIn("not installed", gone["error"])


class DcClient(Base):
    def test_pin_states(self):
        self.assertEqual(dc_client.pin_state("", "1.0.0"), "missing")
        self.assertEqual(dc_client.pin_state("1.0.0", ""), "unpinned")
        self.assertEqual(dc_client.pin_state("v1.0.0", "1.0.0"), "match")
        self.assertEqual(dc_client.pin_state("1.0.1", "1.0.0"), "mismatch")

    def test_version_parsing(self):
        for text, want in (("dc 0.23.0", "0.23.0"), ("dc version v1.2.3-rc.1 (abc)", "1.2.3-rc.1"), ("garbage", "")):
            self.fake.write_text(f"#!/bin/sh\necho '{text}'\n")
            self.assertEqual(dc_client.version(), want)

    def test_pick_survives_any_shape(self):
        self.assertEqual(dc_client.pick({"a": {"b": 1}}, "x", "a.b"), 1)
        self.assertIsNone(dc_client.pick({"a": 5}, "a.b"))
        self.assertEqual(dc_client.pick([], "a", default="d"), "d")

    def test_shipped_pin_file_is_valid_json_with_a_string_version(self):
        data = json.loads((ROOT / "templates" / "dc-cli.json").read_text())
        self.assertIsInstance(data["version"], str)

    def test_a_pin_in_the_template_is_honoured(self):
        pin = self.base / "templates"
        pin.mkdir()
        (pin / "dc-cli.json").write_text('{"version": "9.9.9"}')
        with mock.patch.dict(os.environ, {"RIG_HOME": str(self.base)}):
            self.assertEqual(dc_client.pinned_version(), "9.9.9")
            (pin / "dc-cli.json").write_text("not json")
            self.assertEqual(dc_client.pinned_version(), "")


class Cli(Base):
    def run_cli(self, *args):
        env = dict(os.environ)
        return subprocess.run([sys.executable, str(ROOT / "scripts" / "sandbox_env.py"), *args, "--repo", str(self.repo)],
                              env=env, capture_output=True, text=True, timeout=60)

    def test_init_status_json_and_errors(self):
        done = self.run_cli("init")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("sandbox: none", done.stdout)
        status = self.run_cli("status", "--json")
        self.assertEqual(status.returncode, 0, status.stderr)
        self.assertTrue(json.loads(status.stdout)["override"]["exists"])
        os.environ["FAKE_DC_FAIL"] = "1"
        failed = self.run_cli("init", "--force")
        self.assertEqual(failed.returncode, 1)
        self.assertIn("rig env:", failed.stderr)
        self.assertEqual(self.run_cli("up").returncode, 2)


if __name__ == "__main__":
    unittest.main()
