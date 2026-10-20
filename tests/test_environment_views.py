#!/usr/bin/env python3
"""Fleet panel, environment recovery and the job evidence drawer, against a fake dc serving recorded dc-cli 0.23.0 output."""
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
sys.path.insert(0, str(ROOT / "tests"))

import dc_client  # noqa: E402
import env_recovery  # noqa: E402
import fleet  # noqa: E402
import job_evidence_view  # noqa: E402
import local_api  # noqa: E402
import verification  # noqa: E402
import worktrees  # noqa: E402
from test_worktree_merge import Gate  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures" / "dc"
FAKE = r'''#!/bin/sh
echo "$*" >> "$FAKE_DC_LOG"
case "$1" in
  --version) echo "dc 0.23.0" ;;
  ls) cat "$FAKE_LS" ;;
  stats) if [ -n "$FAKE_STATS_FAIL" ]; then echo "no docker" >&2; exit 1; fi; cat "$FAKE_STATS" ;;
  forward) printf 'desired:\n  3000:3000  (fallback)\nactual:\nsidecar  dc-fwd-x-3000\tUp 2 minutes\t\n' ;;
  recover)
    case "$*" in
      *--yes*) cat "$FAKE_RECOVER_AFTER"; exit "${FAKE_APPLY_EXIT:-0}" ;;
      *) cat "$FAKE_RECOVER"; grep -q '"id":"ready"' "$FAKE_RECOVER" && exit 0; exit 1 ;;
    esac ;;
  *) exit 64 ;;
esac
'''


class Env(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.base = Path(self.td.name)
        self.repo = (self.base / "repo")
        (self.repo / ".git").mkdir(parents=True)
        (self.repo / ".rig").mkdir()
        (self.repo / ".rig" / "harness.toml").write_text("[project]\nenabled = true\n")
        self.repo = self.repo.resolve()
        fake = self.base / "dc"
        fake.write_text(FAKE)
        fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
        self.files = {}
        for name in ("ls-all", "stats", "recover-ready", "recover-engine-missing", "recover-try-sandbox"):
            text = (FIXTURES / f"{name}.json").read_text().replace("/work/repo", str(self.repo))
            self.files[name] = self.base / f"{name}.json"
            self.files[name].write_text(text)
        self.log = self.base / "dc.log"
        self.env = mock.patch.dict(os.environ, {
            "RIG_DC_BIN": str(fake), "FAKE_DC_LOG": str(self.log), "FAKE_LS": str(self.files["ls-all"]),
            "FAKE_STATS": str(self.files["stats"]), "FAKE_RECOVER": str(self.files["recover-ready"]),
            "FAKE_RECOVER_AFTER": str(self.files["recover-ready"])})
        self.env.start()
        fleet._cache.clear()

    def tearDown(self):
        self.env.stop()
        fleet._cache.clear()
        self.td.cleanup()

    def calls(self):
        return self.log.read_text().splitlines() if self.log.exists() else []

    def plan_file(self, **next_fields):
        plan = json.loads(self.files["recover-engine-missing"].read_text())
        plan["next"].update(next_fields)
        path = self.base / "custom-plan.json"
        path.write_text(json.dumps(plan, separators=(",", ":")))
        return path


class Fleet(Env):
    def test_containers_are_split_by_repository_with_observed_stats(self):
        data = fleet.facts(self.repo)
        mine = [row for row in data["containers"] if row["mine"]]
        other = [row for row in data["containers"] if not row["mine"]]
        self.assertEqual((len(mine), len(other)), (1, 1))
        self.assertEqual(mine[0]["status"], "running")
        self.assertEqual((mine[0]["cpu_pct"], mine[0]["mem_used"]), (0.27, 6398410))
        self.assertEqual(mine[0]["image"], "python:3.12-slim-bookworm")
        self.assertIsNone(other[0]["cpu_pct"], "stopped containers have no observed usage")
        self.assertEqual(data["guest"]["cpus"], 4)
        self.assertEqual(data["containers"][0], mine[0], "this repository first")

    def test_a_worktree_container_is_attributed_to_its_job(self):
        rows = json.loads(self.files["ls-all"].read_text())
        rows[0]["local_folder"] = str(self.repo / ".rig" / "worktrees" / "j7")
        self.files["ls-all"].write_text(json.dumps(rows))
        row = fleet.facts(self.repo)["containers"][0]
        self.assertEqual((row["mine"], row["job"]), (True, "j7"))
        view = fleet.view(fleet.facts(self.repo))
        self.assertIn("job j7", view["sections"][0]["lines"][0]["text"])

    def test_view_lines_show_values_as_reported_and_nothing_derived(self):
        view = fleet.view(fleet.facts(self.repo))
        titles = [section["title"] for section in view["sections"]]
        self.assertEqual(titles, ["This repository", "Other workspaces", "Engine"])
        first = view["sections"][0]["lines"][0]
        self.assertEqual(first["tone"], "ok")
        self.assertIn("cpu 0.3%  mem 6.1M/15.7G", first["text"])
        self.assertNotRegex(json.dumps(view), r"cost|estimate|eta", "no derived figures")

    def test_results_are_cached_for_a_few_seconds(self):
        clock = [10.0]
        fleet.facts(self.repo, now=lambda: clock[0])
        fleet.facts(self.repo, now=lambda: clock[0])
        self.assertEqual(len([c for c in self.calls() if c.startswith("ls")]), 1)
        clock[0] += fleet.CACHE_SECONDS + 1
        fleet.facts(self.repo, now=lambda: clock[0])
        self.assertEqual(len([c for c in self.calls() if c.startswith("ls")]), 2)

    def test_a_stats_failure_keeps_the_listing(self):
        os.environ["FAKE_STATS_FAIL"] = "1"
        data = fleet.facts(self.repo)
        self.assertEqual(len(data["containers"]), 2)
        self.assertIsNone(data["containers"][0]["cpu_pct"])

    def test_missing_dc_is_reported_not_raised(self):
        with mock.patch.dict(os.environ, {"RIG_DC_BIN": ""}), mock.patch.object(dc_client.shutil, "which", return_value=None):
            fleet._cache.clear()
            view = fleet.view(fleet.facts(self.repo))
        self.assertTrue(view["attention"])
        self.assertIn("not installed", view["sections"][-1]["lines"][0]["text"])


class Recovery(Env):
    def test_plans_from_the_real_dc_output(self):
        ready = env_recovery.plan(self.repo)
        self.assertEqual((ready["ready"], ready["safe"]), (True, False))
        os.environ["FAKE_RECOVER"] = str(self.files["recover-engine-missing"])
        missing = env_recovery.plan(self.repo)
        self.assertEqual((missing["id"], missing["apply"], missing["apply_allowed"], missing["safe"]),
                         ("install_engine", "open_guide", True, False))
        self.assertEqual(missing["host_code"], "docker_engine_missing")
        os.environ["FAKE_RECOVER"] = str(self.files["recover-try-sandbox"])
        self.assertFalse(env_recovery.plan(self.repo)["safe"], "starting a sandbox is never applied by Rig")

    def test_an_unsafe_plan_is_refused_audited_and_never_applied(self):
        os.environ["FAKE_RECOVER"] = str(self.files["recover-engine-missing"])
        with self.assertRaisesRegex(ValueError, "not an action Rig applies; run it yourself"):
            env_recovery.apply(self.repo, "install_engine")
        self.assertEqual([c for c in self.calls() if "--yes" in c], [])
        audit = [json.loads(line) for line in (self.repo / ".rig" / "recovery" / "environment.jsonl").read_text().splitlines()]
        self.assertEqual((audit[0]["outcome"], audit[0]["reason"], audit[0]["action"]), ("refused", "not_safe", "open_guide"))

    def test_a_confirmed_safe_plan_is_applied_once_and_audited(self):
        os.environ["FAKE_RECOVER"] = str(self.plan_file(id="start_colima", apply="colima_start", applyAllowed=True))
        result = env_recovery.apply(self.repo, "start_colima", actor="test")
        self.assertEqual((result["applied"], result["exit"], result["after"]["id"]), ("colima_start", 0, "ready"))
        self.assertEqual(len([c for c in self.calls() if "--yes" in c]), 1)
        audit = json.loads((self.repo / ".rig" / "recovery" / "environment.jsonl").read_text().splitlines()[-1])
        self.assertEqual((audit["outcome"], audit["actor"], audit["after"]), ("applied", "test", "ready"))

    def test_port_held_faults_get_the_same_audited_action(self):
        os.environ["FAKE_RECOVER"] = str(self.plan_file(id="take_ports", apply="take_ports", applyAllowed=True))
        self.assertEqual(env_recovery.apply(self.repo, "take_ports")["applied"], "take_ports")

    def test_a_changed_or_unconfirmed_plan_is_refused(self):
        os.environ["FAKE_RECOVER"] = str(self.plan_file(id="start_colima", apply="colima_start", applyAllowed=True))
        with self.assertRaisesRegex(ValueError, "plan changed"):
            env_recovery.apply(self.repo, "reclaim_disk")
        with self.assertRaisesRegex(ValueError, "confirm_id is required"):
            env_recovery.apply(self.repo, "")
        self.assertEqual([c for c in self.calls() if "--yes" in c], [])

    def test_actions_dc_allows_but_rig_does_not_are_not_applied(self):
        for action in ("prune_safe", "sudo_start_docker", "context_use", "stop_extra_engine", "launch_desktop"):
            os.environ["FAKE_RECOVER"] = str(self.plan_file(id="x", apply=action, applyAllowed=True))
            with self.assertRaises(ValueError, msg=action):
                env_recovery.apply(self.repo, "x")
        self.assertEqual([c for c in self.calls() if "--yes" in c], [])

    def test_a_failed_apply_is_recorded_as_failed(self):
        os.environ.update(FAKE_RECOVER=str(self.plan_file(id="start_colima", apply="colima_start", applyAllowed=True)),
                          FAKE_APPLY_EXIT="1", FAKE_RECOVER_AFTER=str(self.files["recover-engine-missing"]))
        result = env_recovery.apply(self.repo, "start_colima")
        self.assertEqual(result["exit"], 1)
        audit = json.loads((self.repo / ".rig" / "recovery" / "environment.jsonl").read_text().splitlines()[-1])
        self.assertEqual((audit["outcome"], audit["exit"]), ("failed", 1))

    def test_workspace_view_shows_the_plan_and_the_apply_hint_only_when_safe(self):
        import workspace
        os.environ["FAKE_RECOVER"] = str(self.plan_file(id="start_colima", apply="colima_start", applyAllowed=True,
                                                         summary="Colima is stopped.", command="colima start"))
        workspace._cache.clear()
        status = workspace.status(self.repo, deep=True)
        section = next(s for s in workspace.view(status)["sections"] if s["title"] == "Recovery")
        texts = [line["text"] for line in section["lines"]]
        self.assertEqual(texts[:2], ["! Colima is stopped.", "  run: colima start"])
        self.assertTrue(any("R applies colima_start" in t for t in texts))
        os.environ["FAKE_RECOVER"] = str(self.files["recover-engine-missing"])
        workspace._cache.clear()
        texts = [l["text"] for s in workspace.view(workspace.status(self.repo, deep=True))["sections"]
                 if s["title"] == "Recovery" for l in s["lines"]]
        self.assertFalse(any("R applies" in t for t in texts))


class Api(Env):
    def setUp(self):
        super().setUp()
        self.api = local_api.Api(self.repo, "t" * 64)

    def call(self, method, params=None):
        return self.api.dispatch({"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}, "token": "t" * 64})

    def test_methods_are_listed_and_the_mutating_one_is_marked(self):
        rows = {row["method"]: row for row in self.call("api.methods")["result"]["methods"]}
        self.assertEqual([rows[m]["mutates"] for m in ("job.evidence", "fleet.status", "env.recovery", "env.recover")],
                         [False, False, False, True])

    def test_fleet_and_recovery_through_the_api(self):
        self.assertEqual(self.call("fleet.status")["result"]["view"]["sections"][0]["title"], "This repository")
        self.assertTrue(self.call("env.recovery")["result"]["ready"])

    def test_recover_needs_a_confirmed_plan_id(self):
        self.assertEqual(self.call("env.recover")["error"]["code"], local_api.INVALID_PARAMS)
        os.environ["FAKE_RECOVER"] = str(self.files["recover-engine-missing"])
        reply = self.call("env.recover", {"confirm_id": "install_engine"})
        self.assertEqual(reply["error"]["code"], local_api.INVALID_PARAMS)
        self.assertIn("run it yourself", reply["error"]["message"])

    def test_job_evidence_needs_a_known_job(self):
        self.assertEqual(self.call("job.evidence")["error"]["code"], local_api.INVALID_PARAMS)
        self.assertEqual(self.call("job.evidence", {"id": "nope"})["error"]["code"], local_api.INVALID_PARAMS)


class Drawer(Gate):
    def sections(self, job_id):
        return {s["title"]: [line["text"] for line in s["lines"]] for s in job_evidence_view.build(self.repo, job_id)["sections"]}

    def test_a_worktree_job_shows_where_it_ran_what_changed_and_its_contract(self):
        self.start("w1", edit={"a.txt": self.lines(l1="ONE")})
        folder, auth, meta = self.jobs["w1"]
        verification.record_requirements(self.repo, folder, [], ["Reviewed by the parent"], **auth)
        shown = self.sections("w1")
        self.assertEqual([s for s in shown], ["Ran", "Change", "Contract", "Checks", "Ports"])
        self.assertTrue(any(t.startswith("worktree   rig/w1  base main") for t in shown["Ran"]))
        self.assertIn("not merged", " ".join(shown["Ran"]))
        self.assertIn("+1 -1       a.txt", shown["Change"])
        self.assertEqual(shown["Checks"], ["no checks run"])
        self.assertEqual(shown["Ports"], ["no container"])
        self.assertTrue(any(t.startswith("acceptance") for t in shown["Contract"]))

    def test_after_the_merge_the_diff_still_comes_from_the_recorded_commits(self):
        self.start("w1", edit={"a.txt": self.lines(l1="ONE")})
        self.accept("w1")
        shown = self.sections("w1")
        self.assertIn("merged", " ".join(shown["Ran"]))
        self.assertIn("+1 -1       a.txt", shown["Change"])

    def test_out_of_scope_observations_are_flagged(self):
        self.start("w1", edit={"a.txt": self.lines(l1="ONE")})
        folder = self.jobs["w1"][0]
        (folder / "change-evidence.json").write_text(json.dumps({"scoped_changed_paths": ["a.txt"],
                                                                 "out_of_scope_observations": [{"path": "stray.txt"}]}))
        view = job_evidence_view.build(self.repo, "w1")
        change = next(s for s in view["sections"] if s["title"] == "Change")
        flagged = [line for line in change["lines"] if line["tone"] == "attention"]
        self.assertEqual(len(flagged), 1)
        self.assertIn("stray.txt", flagged[0]["text"])

    def test_a_missing_or_malformed_job_is_an_error_not_a_guess(self):
        with self.assertRaisesRegex(ValueError, "no such job"):
            job_evidence_view.build(self.repo, "ghost")
        with self.assertRaises(ValueError):
            job_evidence_view.build(self.repo, "../etc")


if __name__ == "__main__":
    unittest.main()
