"""History-independent lookup and truthful executor metadata contracts."""
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import jobs


class JobLookup(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        self.root = self.repo / ".rig" / "jobs"
        self.root.mkdir(parents=True)

    def seed(self, name, status="ok"):
        path = self.root / name
        path.mkdir()
        (path / "meta.json").write_text(json.dumps({
            "job_id": name, "status": status, "worker": "codex", "role": "worker",
            "pid": os.getpid() if status == "running" else None,
        }))
        return path

    def test_exact_wait_cost_depends_only_on_target_count(self):
        for index in range(1000):
            self.seed(f"history-{index}")
        names = [f"active-{i}" for i in range(3)]
        for name in names:
            self.seed(name, "running")
        with patch.object(Path, "iterdir", side_effect=AssertionError("history scan")), \
                patch.object(jobs, "load_job", wraps=jobs.load_job) as load:
            self.assertEqual(jobs.wait_job(self.repo, None, ids=names, timeout=0)[0], 124)
            self.assertEqual(load.call_count, 3)

    def test_unique_partial_names_share_one_scan_and_pin_targets(self):
        first = self.seed("prefix-alpha", "running")
        self.seed("prefix-beta", "running")
        original = Path.iterdir
        scans = []

        def scan(path):
            scans.append(path)
            return original(path)

        def finish(_seconds):
            for path in self.root.iterdir():
                if (path / "meta.json").is_file():
                    meta = json.loads((path / "meta.json").read_text())
                    meta["status"] = "ok"
                    (path / "meta.json").write_text(json.dumps(meta))
            self.seed("new-alpha")
            scans.clear()  # Only measure the next pinned refresh.

        with patch.object(Path, "iterdir", scan):
            paths = jobs.resolve_job_paths(self.repo, ["alpha", "beta"])
            self.assertEqual(scans, [self.root])
            self.assertEqual(paths[0], first.resolve())
            stop = Mock()
            stop.is_set.return_value = False
            stop.wait.side_effect = finish
            code, _ = jobs.wait_job(self.repo, None, ids=[p.name for p in paths], cancel_event=stop)
            self.assertEqual(code, 0)
            self.assertEqual(scans, [])

    def test_ambiguous_partial_cannot_cancel_any_job(self):
        for name in ("one-task", "two-task"):
            self.seed(name, "running")
        with self.assertRaisesRegex(SystemExit, "ambiguous"):
            jobs.cancel_job(self.repo, "task")
        self.assertFalse(any(self.root.glob("*/cancel.json")))

    def test_bad_exact_target_never_switches_to_partial_match(self):
        self.seed("target-other")
        exact = self.root / "target"
        exact.mkdir()
        (exact / "meta.json").write_text("{")
        with self.assertRaisesRegex(SystemExit, "invalid meta.json"):
            jobs.resolve_job(self.repo, "target")

    def test_unsafe_ids_and_escaped_symlinks_are_rejected(self):
        for name in (".", "..", "../outside", "a/b"):
            with self.subTest(name=name), self.assertRaisesRegex(SystemExit, "invalid job"):
                jobs.resolve_job(self.repo, name)
        (self.root / "escaped").symlink_to(self.repo)
        with self.assertRaisesRegex(SystemExit, "invalid job path"):
            jobs.resolve_job(self.repo, "escaped")

    def test_disappearing_wait_target_errors_instead_of_switching(self):
        folder = self.seed("target", "running")

        def remove(_seconds):
            (folder / "meta.json").unlink()
            self.seed("target-new")

        stop = Mock()
        stop.is_set.return_value = False
        stop.wait.side_effect = remove
        code, text = jobs.wait_job(self.repo, "target", cancel_event=stop)
        self.assertEqual(code, 1)
        self.assertIn("NEEDS_RECONCILIATION", text)
        self.assertIn("identity changed", text)
        self.assertNotIn("target-new", text)

    def test_parent_models_are_observed_or_unknown(self):
        folder = self.root / "parent-job"
        for model, source in (("", "unknown"), ("actual-session-model", "observed")):
            jobs.write_job_files(folder, "parent-job", "codex", "parent", "ok", 0,
                                 "", "", "done", model=model)
            loaded = jobs.load_job(folder)
            self.assertEqual(loaded["model"], model)
            self.assertEqual(loaded["model_source"], source)
            self.assertEqual(loaded["executor_kind"], "parent")
            self.assertFalse(loaded["model_inferred"])

    def test_legacy_parent_without_model_never_gets_child_pin(self):
        folder = self.seed("legacy-parent")
        (folder / "meta.json").write_text(json.dumps({"role": "parent", "worker": "codex", "status": "ok"}))
        loaded = jobs.load_job(folder)
        self.assertEqual(loaded["model"], "")
        self.assertEqual(loaded["model_source"], "unknown")

    def test_cancelling_legacy_jobs_does_not_upgrade_model_provenance(self):
        for role, model in (("parent", "gpt-5.6-luna"), ("worker", "")):
            folder = self.seed("legacy-" + role)
            (folder / "meta.json").write_text(json.dumps({
                "role": role, "worker": "codex", "status": "running", "model": model,
            }))
            before = jobs.load_job(folder)
            jobs.cancel_job(self.repo, folder.name)
            after = jobs.load_job(folder)
            self.assertEqual(after["model_source"], "unknown")
            self.assertEqual(after["model_inferred"], before["model_inferred"])

    def test_new_native_default_uses_selected_worker_catalog(self):
        cache = self.repo / "catalog.json"
        available = "anthropic/claude-haiku-4-5"
        cache.write_text(json.dumps({"opencode": {"ids": [available], "fetched_at": time.time()}}))
        folder = self.root / "catalog-child"
        with patch.dict(os.environ, {
            "RIG_MODEL_CATALOG_CACHE": str(cache), "RIG_SKIP_MODEL_CATALOG": "0", "RIG_REFRESH_MODELS": "0",
        }):
            jobs.write_job_files(folder, folder.name, "opencode", "mini", "running", 0, "", "", "")
        child = jobs.load_job(folder)
        self.assertEqual(child["model"], available)
        self.assertEqual(child["model_source"], "selected")
        self.assertFalse(child["model_inferred"])
        self.assertEqual(child["display_model"], available)

    def test_fresh_native_child_rejects_failed_model_resolution(self):
        folder = self.root / "unresolved-child"
        with patch("route.resolved_model_for", side_effect=RuntimeError("catalog unavailable")):
            with self.assertRaisesRegex(SystemExit, "selected model"):
                jobs.write_job_files(folder, folder.name, "codex", "mini", "running", 0, "", "", "")
        self.assertFalse((folder / "meta.json").is_file())
        self.assertIsNone(jobs.load_job(folder))

    def test_fresh_native_child_rejects_empty_selection(self):
        folder = self.root / "empty-child"
        with patch("route.resolved_model_for", return_value=("", "")):
            with self.assertRaisesRegex(SystemExit, "selected model"):
                jobs.write_job_files(folder, folder.name, "codex", "mini", "running", 0, "", "", "")
        self.assertFalse((folder / "meta.json").is_file())

    def test_selected_native_child_remains_displayed(self):
        folder = self.root / "selected-child"
        jobs.write_job_files(
            folder, folder.name, "codex", "mini", "running", 0, "", "", "",
            model="gpt-5.6-luna", effort="low",
        )
        child = jobs.load_job(folder)
        self.assertEqual(child["model"], "gpt-5.6-luna")
        self.assertEqual(child["effort"], "low")
        self.assertEqual(child["model_source"], "selected")
        self.assertFalse(child["model_inferred"])
        self.assertEqual(child["executor_kind"], "native_child")
        self.assertEqual(child["display_model"], "gpt-5.6-luna")

    def test_legacy_native_child_unknown_stays_readable_without_backfill(self):
        folder = self.seed("legacy-child")
        (folder / "meta.json").write_text(json.dumps({
            "job_id": folder.name, "role": "mini", "worker": "codex", "status": "ok",
            "executor_kind": "native_child", "model": "", "model_source": "unknown",
        }))
        before = (folder / "meta.json").read_text()
        loaded = jobs.load_job(folder)
        self.assertEqual(loaded["model_source"], "unknown")
        self.assertEqual(loaded["display_model"], "unknown")
        self.assertEqual((folder / "meta.json").read_text(), before)


if __name__ == "__main__":
    unittest.main()
