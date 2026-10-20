#!/usr/bin/env python3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import harness  # noqa: E402
import sandbox_provider  # noqa: E402


def write_harness(repo: Path, body: str) -> None:
    path = repo / ".rig" / "harness.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)


class SandboxConfig(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.repo = Path(self.td.name)

    def tearDown(self):
        self.td.cleanup()

    def parsed(self, body: str) -> dict:
        write_harness(self.repo, body)
        return harness.parse_harness(harness.harness_path(self.repo))["sandbox"]

    def test_default_is_host_without_section_or_file(self):
        self.assertEqual(harness.parse_harness(harness.harness_path(self.repo))["sandbox"],
                         {"provider": "host", "error": "", "credentials": {}})
        self.assertEqual(self.parsed("[workers]\ngrok = true\n"), {"provider": "host", "error": "", "credentials": {}})

    def test_known_providers_parse_quoted_and_cased(self):
        self.assertEqual(self.parsed('[sandbox]\nprovider = "host"\n')["provider"], "host")
        self.assertEqual(self.parsed("[sandbox]\nprovider = 'DevContainer'  # later\n")["provider"], "devcontainer")

    def test_unknown_provider_is_an_error_not_a_fallback(self):
        state = self.parsed('[sandbox]\nprovider = "docker"\n')
        self.assertIn("unknown [sandbox] provider", state["error"])

    def test_provider_key_outside_sandbox_section_is_ignored(self):
        self.assertEqual(self.parsed('[routing]\nprovider = "devcontainer"\n'),
                         {"provider": "host", "error": "", "credentials": {}})

    def test_worker_credential_choices_parse_and_bad_ones_are_errors(self):
        state = self.parsed('[sandbox]\nprovider = "devcontainer"\n\n[sandbox.workers]\nclaude = "mount-ro"\ncodex = \'None\'  # no login needed\n')
        self.assertEqual((state["provider"], state["credentials"], state["error"]),
                         ("devcontainer", {"claude": "mount-ro", "codex": "none"}, ""))
        self.assertIn("unknown credential choice", self.parsed('[sandbox.workers]\nclaude = "mount-rw"\n')["error"])
        self.assertIn("unknown worker", self.parsed('[sandbox.workers]\nbogus = "none"\n')["error"])
        bad = self.parsed('[sandbox.workers]\nclaude = "mount-rw"\ncodex = "none"\n')
        self.assertEqual(bad["credentials"], {"codex": "none"}, "a bad value is never half-applied")

    def test_workers_section_does_not_change_the_provider(self):
        self.assertEqual(self.parsed('[sandbox.workers]\nclaude = "mount-ro"\n')["provider"], "host")


class HostProviderBehaviour(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.repo = Path(self.td.name)
        write_harness(self.repo, '[sandbox]\nprovider = "host"\n')

    def tearDown(self):
        self.td.cleanup()

    def test_wrap_is_identity_and_returns_copies(self):
        provider = sandbox_provider.provider_for(self.repo)
        cmd, env = ["claude", "-p", "a b"], {"RIG_JOB_ID": "j1"}
        out_cmd, out_env = provider.wrap_worker_cmd(cmd, env)
        self.assertEqual((out_cmd, out_env), (cmd, env))
        self.assertIsNot(out_cmd, cmd)
        self.assertIsNot(out_env, env)

    def test_status_evidence_and_check_run(self):
        provider = sandbox_provider.provider_for(self.repo)
        self.assertEqual(provider.status(self.repo)["boundary"], "none")
        self.assertEqual(provider.evidence(), {"provider": "host"})
        done = provider.run_check([sys.executable, "-c", "print('ok')"], self.repo)
        self.assertEqual((done.returncode, done.stdout.strip()), (0, "ok"))

    def test_devcontainer_and_unknown_are_refused(self):
        for value in ("devcontainer", "docker"):
            write_harness(self.repo, f'[sandbox]\nprovider = "{value}"\n')
            with self.assertRaises(sandbox_provider.SandboxError):
                sandbox_provider.provider_for(self.repo)

    def test_cli_check_exit_codes_and_message(self):
        script = str(ROOT / "scripts" / "sandbox_provider.py")
        ok = subprocess.run([sys.executable, script, "check", "--repo", str(self.repo)],
                            capture_output=True, text=True)
        self.assertEqual(ok.returncode, 0, ok.stderr)
        write_harness(self.repo, '[sandbox]\nprovider = "devcontainer"\n')
        bad = subprocess.run([sys.executable, script, "check", "--repo", str(self.repo)],
                             capture_output=True, text=True)
        self.assertEqual(bad.returncode, 1)
        self.assertIn("needs dc-cli", bad.stderr)


if __name__ == "__main__":
    unittest.main()
