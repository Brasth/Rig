#!/usr/bin/env python3
"""Normal prompt compatibility, declared routing intent, and structural budgets."""
import json
import os
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from unittest.mock import patch

from benchmark_normal_prompt import (
    CORPUS, Fixture, native_mini_observation, nearest_rank, routing_observations, seed_job,
)
import harness
import jobs
import rig_mcp
import route
import work_queue

PICK_KEYS = {"kind", "worker", "spawn", "model", "effort", "native_agent", "reason", "parent_writes"}
JOB_KEYS = {"job_id", "worker", "role", "kind", "status", "effective", "pid", "alive",
            "session_id", "thread", "model", "effort", "model_inferred", "open", "watch",
            "summary", "started_at", "ended_at", "elapsed_s", "task", "doing", "ask",
            "inbox", "activities", "dir", "log", "log_pruned", "mtime", "files"}


class RoutingCorpus(unittest.TestCase):
    def test_corpus_covers_categories_and_explicit_kinds(self):
        rows = json.loads(CORPUS.read_text())
        self.assertEqual(len(rows), len({row["id"] for row in rows}))
        required = {"id", "role", "case", "expected_kind", "access", "category"}
        for row in rows:
            self.assertTrue(required <= row.keys(), row)
            self.assertIn(row["access"], {"read", "write"})
        self.assertTrue({"question", "plan", "review", "collision", "risk", "documentation",
                         "bulk", "ssh", "mixed", "queue"} <= {row["category"] for row in rows})
        self.assertEqual({row["role"] for row in rows if row["category"] == "explicit"},
                         set(rig_mcp.PICK_ROLES))

    def test_parent_semantic_roles_always_override_prompt_text(self):
        for row in routing_observations():
            with self.subTest(case=row["id"], category=row["category"]):
                self.assertEqual(row["parent_role_result"], row["expected_kind"])
        for alias, expected in route.EXPLICIT_KIND.items():
            with self.subTest(alias=alias):
                self.assertEqual(route.classify(alias, "Review tiny security architecture"), expected)

    def test_fallback_matches_every_declared_intent(self):
        for row in routing_observations():
            with self.subTest(case=row["id"], category=row["category"]):
                self.assertEqual(row["observed_kind"], row["expected_kind"])

    def test_native_mini_matches_shipped_model_and_write_contract(self):
        observed = native_mini_observation()
        self.assertEqual(observed["choice"]["model"], observed["adapter_model"])
        self.assertEqual(observed["choice"]["kind"], "mini")
        self.assertTrue(observed["write_capable"], observed)


class CompatibilityContracts(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="rig-contract-")
        self.addCleanup(self.temp.cleanup)
        self.fixture = Fixture(Path(self.temp.name))
        self.repo = self.fixture.repo
        self.enterContext(self.fixture.isolated())
        self.fixture.prepare_catalog("static")

    def test_full_session_keys_types_text_and_legacy_fields(self):
        for status in ("ok", "fail", "cancelled", "running", "ask"):
            seed_job(self.repo, "legacy-" + status, status)
        (self.repo / ".rig" / "jobs" / "empty").mkdir()
        corrupt = self.repo / ".rig" / "jobs" / "corrupt"
        corrupt.mkdir()
        (corrupt / "meta.json").write_text("{")
        full = json.loads(rig_mcp.format_session(self.repo, "Fix the fixture", "implement", as_json=True))
        # Additive metadata is allowed; these full-mode fields and types are stable.
        for key, kind in {"memory": str, "jobs": list, "status": str, "pick": dict}.items():
            self.assertIsInstance(full[key], kind)
        self.assertTrue(PICK_KEYS <= full["pick"].keys())
        self.assertEqual(len(full["jobs"]), 5)
        self.assertIn("jobs: 7", full["status"])
        for job in full["jobs"]:
            self.assertTrue(JOB_KEYS <= job.keys(), job)
            self.assertIsInstance(job["activities"], list)
        text = rig_mcp.format_session(self.repo, "Fix the fixture", "implement")
        for section in ("memory", "jobs", "status", "pick"):
            self.assertIn("# " + section + "\n", text)
        self.assertIn("legacy-ask", text)
        self.assertIn("legacy-running", text)

    def test_empty_session_and_stay_never_launch(self):
        self.fixture.counts.clear()
        result = json.loads(rig_mcp.format_session(self.repo, "Anything", "stay", as_json=True))
        self.assertEqual(result["jobs"], [])
        self.assertEqual(result["pick"]["spawn"], "stay")
        self.assertFalse(result["pick"]["parent_writes"])
        self.assertNotIn("run-worker.sh", route.format_text(result["pick"]))
        self.assertEqual(self.fixture.counts["job_directory_scans"], 1)
        self.assertEqual(self.fixture.counts["load_job_calls"], 0)

    def test_session_reuses_one_snapshot_through_all_rendering(self):
        self.fixture.seed(1000)
        (self.repo / ".rig" / "jobs" / "empty").mkdir()
        corrupt = self.repo / ".rig" / "jobs" / "corrupt"
        corrupt.mkdir()
        (corrupt / "meta.json").write_text("{")
        for compact, limit in ((False, 10), (True, 0), (True, 10), (True, 100)):
            for as_json in (False, True):
                with self.subTest(compact=compact, terminal_limit=limit, as_json=as_json):
                    self.fixture.counts.clear()
                    loaded_paths = Counter()
                    original_load = jobs.load_job
                    def load_once(path):
                        loaded_paths[path] += 1
                        return original_load(path)
                    with patch.object(jobs, "load_job", load_once):
                        text = rig_mcp.format_session(self.repo, "Review", "review", as_json=as_json,
                                                      compact=compact, terminal_limit=limit)
                    self.assertEqual(self.fixture.counts["job_directory_scans"], 1)
                    self.assertLessEqual(self.fixture.counts["load_job_calls"], 1002)
                    self.assertTrue(all(count <= 1 for count in loaded_paths.values()))
                    if as_json:
                        result = json.loads(text)
                        active = [job for job in result["jobs"] if job["effective"] in {"running", "ask"}]
                        self.assertEqual(len(active), 200)
                        self.assertEqual(len(result["jobs"]), 200 + limit if compact else 1000)

    def test_execution_status_and_wait_exit_contracts(self):
        for status, expected_code in (("ok", 0), ("fail", 1), ("timeout", 1),
                                      ("cancelled", 130), ("ask", 2), ("running", 124)):
            folder = seed_job(self.repo, "exit-" + status, status)
            loaded = jobs.load_job(folder)
            self.assertEqual(loaded["status"], "running" if status == "ask" else status)
            self.assertEqual(loaded["effective"], status)
            self.fixture.counts.clear()
            code, text = jobs.wait_job(self.repo, "exit-" + status,
                                       timeout=0 if status == "running" else None)
            self.assertEqual(code, expected_code, text)
            self.assertIn("exit-" + status, text)
            self.assertEqual(self.fixture.counts["job_directory_scans"], 0)
            self.assertEqual(self.fixture.counts["load_job_calls"], 1)
        self.fixture.counts.clear()
        code, text = jobs.wait_job(self.repo, None, ids=["exit-ok", "exit-fail", "exit-cancelled"])
        self.assertEqual(code, 1, text)
        self.assertEqual(self.fixture.counts["job_directory_scans"], 0)
        self.assertEqual(self.fixture.counts["load_job_calls"], 3)

    def test_disabled_missing_and_live_parent_boundaries(self):
        self.assertEqual(harness.effective_workers(self.repo, "opencode"), ["omp", "pi", "agy"])
        (self.fixture.bins / "omp").unlink()
        self.assertEqual(harness.effective_workers(self.repo, "opencode"), ["pi", "agy"])
        with self.assertRaisesRegex(SystemExit, "off in harness"):
            harness.assert_spawn_allowed(self.repo, "grok", live="codex")
        choice = route.pick("codex", [], "implement", "Fix the fixture", catalogs={})
        self.assertTrue(choice["parent_writes"])
        excluded = route.pick("codex", [], "implement", "Fix", catalogs={}, exclude="codex")
        self.assertEqual(excluded["spawn"], "none")

    def test_queue_parking_does_not_create_or_launch_jobs(self):
        parsed = work_queue.parse_slash("/queue fix the sidebar after this job")
        self.assertIsNotNone(parsed)
        queued = work_queue.add_item(self.repo, "fix the sidebar after this job")
        self.assertEqual(queued["status"], "pending")
        self.assertEqual(jobs.list_jobs(self.repo), [])
        self.assertEqual(len(work_queue.list_items(self.repo)), 1)

    def test_child_surface_rejects_parent_dispatch_and_permissions(self):
        folder = seed_job(self.repo, "own-job", "running")
        with patch.dict(os.environ, {"RIG_JOB_ID": "own-job", "RIG_JOB_DIR": str(folder)}):
            names = {tool["name"] for tool in rig_mcp.listed_tools()}
            self.assertEqual(names, {"rig_job_doing", "rig_job_note", "rig_job_ask",
                                     "permission_prompt", "rig_job_inbox", "rig_job_show", "rig_memory"})
            for name in ("rig_session", "rig_pick", "rig_job_wait", "rig_queue_claim",
                         "rig_job_start", "rig_job_allow", "rig_job_deny"):
                result = rig_mcp.call_tool(name, {"repo": str(self.repo), "case": "Fix"})
                self.assertTrue(result.get("isError"), name)

    def test_catalog_selected_worker_and_stale_refresh_invariants(self):
        for mode, probes, refreshes in (("static", 0, 0), ("warm", 0, 0),
                                        ("stale", 0, 1), ("cold", 1, 0)):
            with self.subTest(mode=mode):
                self.fixture.prepare_catalog(mode)
                selected = route.pick("codex", ["opencode", "omp", "pi", "agy"], "review", "Review")
                self.assertEqual(selected["worker"], "opencode")
                self.assertEqual(self.fixture.counts["catalog_probes"], probes)
                self.assertEqual(self.fixture.counts["catalog_refresh_schedules"], refreshes)
                self.assertTrue(set(self.fixture.probed) <= {"opencode", "refresh:opencode"})

    def test_nearest_rank_p95(self):
        self.assertEqual(nearest_rank(list(range(1, 21))), 19)


if __name__ == "__main__":
    unittest.main()
