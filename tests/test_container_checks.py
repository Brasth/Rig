#!/usr/bin/env python3
"""Acceptance checks run where the job ran. Fake dc exec runs locally; see container_test_support."""
import json
import os
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import verification  # noqa: E402
from container_test_support import install_fake_dc  # noqa: E402
from test_verification import ParentVerification  # noqa: E402


class ContainerChecks(ParentVerification):
    # Inheriting ParentVerification re-runs every existing verification behaviour under the
    # container provider; test_missing_executable_leaves_error_artifact_... differs by design
    # and is replaced below.
    test_missing_executable_leaves_error_artifact_and_clears_active_check = None

    def setUp(self):
        super().setUp()
        harness = self.repo / ".rig" / "harness.toml"
        harness.write_text(harness.read_text().rstrip("\n") + '\n\n[sandbox]\nprovider = "devcontainer"\n')
        self.bin = self.repo.parent / (self.repo.name + "-bin")
        self.dc = install_fake_dc(self.bin)
        self.log = self.bin / "dc.log"
        self.enterContext(patch.dict(os.environ, {"RIG_DC_BIN": str(self.dc), "FAKE_DC_LOG": str(self.log)}))
        os.environ.pop("FAKE_DC_EXEC_FAIL", None)
        os.environ.pop("FAKE_DC_LS", None)
        self.addCleanup(lambda: __import__("shutil").rmtree(self.bin, ignore_errors=True))

    def calls(self):
        return self.log.read_text().splitlines() if self.log.exists() else []

    def test_check_runs_in_the_container_and_records_where(self):
        self.requirements()
        checked = self.check("tests", self.command())
        self.assertEqual((checked["status"], checked["exit_code"]), ("passed", 0))
        log = "\n".join(self.calls())  # the supervisor script spans several lines
        self.assertIn("exec --no-start -- sh -c", log)
        self.assertIn("rig-supervisor", log)
        self.assertEqual(checked["sandbox"]["provider"], "devcontainer")
        self.assertFalse(checked["sandbox"]["container"]["found"], "no container facts are invented")
        saved = json.loads((self.job / "checks" / (checked["check_id"] + ".json")).read_text())
        self.assertEqual(saved["sandbox"], checked["sandbox"])
        self.assertIn("real stdout", (self.job / checked["stdout"]["path"]).read_text())
        self.assertIn("real stderr", (self.job / checked["stderr"]["path"]).read_text())
        self.assertEqual(list((self.job / "checks").glob(".*")), [], "lease files are removed")

    def test_container_checks_still_verify_and_accept(self):
        self.requirements()
        self.check("tests", self.command())
        self.assertEqual(self.accept(next="review")["state"], "verified")

    def test_exit_code_and_failure_status_pass_through(self):
        self.requirements([{"id": "tests", "argv": self.command("import sys; sys.exit(3)")}])
        checked = self.check("tests", self.command("import sys; sys.exit(3)"))
        self.assertEqual((checked["status"], checked["exit_code"]), ("failed", 3))

    def test_the_exact_argv_reaches_the_command_without_shell_interpretation(self):
        script = "import json,sys; print(json.dumps(sys.argv[1:]))"
        tricky = ["a b", "c\nd", "$(touch pwned)", "'q'", "--flag=\"x\"", ""]
        argv = [sys.executable, "-c", script, *tricky]
        self.requirements([{"id": "tests", "argv": argv}])
        checked = self.check("tests", argv)
        out = (self.job / checked["stdout"]["path"]).read_text()
        self.assertEqual(json.loads(out.strip().splitlines()[-1]), tricky)
        self.assertFalse((self.repo / "pwned").exists())

    def test_unreachable_container_refuses_before_any_check_state_exists(self):
        self.requirements()
        os.environ["FAKE_DC_EXEC_FAIL"] = "1"
        with self.assertRaisesRegex(verification.VerificationError, "cannot run this check in the sandbox"):
            self.check("tests", self.command())
        self.assertEqual(list((self.job / "checks").glob("*.json")) if (self.job / "checks").exists() else [], [])
        self.assertFalse((self.job / "check-running.json").exists())

    def test_missing_dc_never_falls_back_to_the_host(self):
        self.requirements()
        marker = self.repo / "ran-on-host"
        with patch.dict(os.environ, {"RIG_DC_BIN": "", "PATH": str(self.bin.parent / "nowhere")}):
            with self.assertRaisesRegex(verification.VerificationError, "needs dc-cli"):
                self.check("tests", self.command(f"open({str(marker)!r}, 'w').write('x')"))
        self.assertFalse(marker.exists())

    def test_aborting_a_check_stops_its_whole_tree_in_the_container(self):
        pids = self.repo.parent / (self.repo.name + "-pids")
        script = ("import os,subprocess,time\nc = subprocess.Popen(['sleep','300'])\n"
                  f"open({str(pids)!r},'w').write(f'{{os.getpid()}} {{c.pid}}')\ntime.sleep(300)\n")
        argv = self.command(script)
        self.requirements([{"id": "tests", "argv": argv}])

        def abort(_job):
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline and not pids.exists():
                time.sleep(0.05)
            raise RuntimeError("parent aborted the check")

        started = time.monotonic()
        with self.assertRaisesRegex(RuntimeError, "parent aborted"):
            self.check("tests", argv, on_tick=abort)
        self.assertLess(time.monotonic() - started, 25)
        agent, child = (int(x) for x in pids.read_text().split())
        time.sleep(0.5)
        for pid in (agent, child):
            try:
                os.kill(pid, 0)
                stat = Path(f"/proc/{pid}/stat")
                self.assertEqual(stat.read_text().split()[2] if stat.exists() else "Z", "Z", f"pid {pid} survived")
            except ProcessLookupError:
                pass
        row = json.loads(next((self.job / "checks").glob("tests-*.json")).read_text())
        self.assertEqual(row["status"], "error")
        self.assertEqual(list((self.job / "checks").glob(".*")), [])
        pids.unlink()

    def test_missing_executable_fails_the_check_with_127_instead_of_raising(self):
        """Overrides the host behaviour inherited from ParentVerification: inside the container the
        missing program is reported by the shell, so the check fails (it never passes) with 127."""
        command = [str(self.repo / "missing-program")]
        self.requirements([{"id": "tests", "argv": command}])
        checked = self.check("tests", command)
        self.assertEqual((checked["status"], checked["exit_code"]), ("failed", 127))
        self.assertFalse((self.job / "check-running.json").exists())
        self.assertEqual(self.reservation()["stage"], "verifying")

    def test_host_provider_checks_are_unchanged_and_never_call_dc(self):
        harness = self.repo / ".rig" / "harness.toml"
        harness.write_text(harness.read_text().replace('provider = "devcontainer"', 'provider = "host"'))
        self.requirements()
        checked = self.check("tests", self.command())
        self.assertNotIn("sandbox", checked)
        self.assertEqual(self.calls(), [])


if __name__ == "__main__":
    unittest.main()
