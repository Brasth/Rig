import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import ask
import cancellation
import change_evidence
import jobs
from ui_snapshot import Collector


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        (self.repo / ".git").mkdir()

    def job(self, name="job", **fields):
        folder = self.repo / ".rig" / "jobs" / name
        folder.mkdir(parents=True)
        value = {"job_id": name, "worker": "claude", "model": "claude-sonnet-5",
                 "model_source": "observed", "status": "ok", "task": name, **fields}
        (folder / "meta.json").write_text(json.dumps(value))
        return folder

    def test_linked_worktree_pins_own_root(self):
        worktree = self.repo / "linked"
        (worktree / "src" / "nested").mkdir(parents=True)
        (worktree / ".git").write_text("gitdir: /unused/linked-metadata\n")
        self.assertEqual(jobs.repo_root(str(worktree / "src" / "nested")), worktree.resolve())

    def test_cached_thousand_historical_jobs_never_decode_logs(self):
        for index in range(1000):
            folder = self.job(f"job-{index}")
            (folder / "stdout.log").write_text("not decoded by polling\n")
        collector = Collector(self.repo)
        with mock.patch.object(jobs, "decode_log_text", side_effect=AssertionError("history decoded")):
            first = collector.collect()
            with mock.patch.object(jobs, "load_job", side_effect=AssertionError("unchanged history loaded")):
                second = collector.collect()
        self.assertIsNone(first["error"])
        self.assertIsNone(second["error"])
        self.assertEqual(len(second["jobs"]), 1000)
        self.assertFalse((self.repo / ".rig" / "thread").exists())

    def test_metadata_and_activity_changes_refresh_without_read_timestamps(self):
        folder = self.job(status="running", pid=os.getpid())
        jobs.set_doing(folder, "reading code")
        collector = Collector(self.repo)
        first = collector.collect()["jobs"][0]
        second = collector.collect()["jobs"][0]
        self.assertEqual(first["last_activity_at"], second["last_activity_at"])
        self.assertEqual(second["last_activity_source"], "doing")
        self.assertTrue(second["last_activity_at"])
        jobs.set_doing(folder, "running checks")
        self.assertEqual(collector.collect()["jobs"][0]["doing"], "running checks")
        ask.write_ask(folder, "Bash", {"command": "git status"}, "tool")
        self.assertEqual(collector.collect()["jobs"][0]["effective"], "ask")

    def test_cancellation_identity_roundtrips_and_attempt_change_invalidates(self):
        folder = self.job(attempt_id="attempt-a", reservation_id="reservation-a")
        collector = Collector(self.repo)
        identity = json.loads(json.dumps(collector.collect()["jobs"][0]["identity"]))
        target = dict(identity, path=Path(identity["path"]), directory_identity=tuple(identity["directory_identity"]))
        self.assertEqual(target, cancellation.capture(folder))
        jobs.patch_meta(folder, attempt_id="attempt-b")
        with self.assertRaises(ValueError):
            cancellation.current(target)
        self.assertEqual(collector.collect()["jobs"][0]["identity"]["attempt_id"], "attempt-b")

    def test_stored_acceptance_is_not_claimed_current(self):
        folder = self.job(execution_mode="live", ownership_established=True)
        (folder / "verification.json").write_text(json.dumps({
            "version": 1, "state": "verified", "acceptance": "accepted", "method": "manual",
            "snapshot_id": "a" * 64, "rationale": "reviewed", "accepted_at": jobs.iso_now(), "check_ids": [],
        }))
        row = Collector(self.repo).collect()["jobs"][0]
        self.assertEqual(row["verification_summary"]["freshness"], "not_checked")
        self.assertNotEqual(row["display_state"], "verified")

    def test_new_acceptance_checks_only_changed_subject_and_invalidates_later_edit(self):
        subject = self.repo / "subject.txt"
        subject.write_text("accepted\n")
        folder = self.job(execution_mode="live", ownership_established=True, reservation_id="r",
                          attempt_id="a", files=["subject.txt"])
        (folder / "requirements.json").write_text(json.dumps({"version": 1, "requirements": [],
                                                               "manual_criteria": ["review"]}))
        collector = Collector(self.repo)
        collector.collect()
        accepted = {"version": 1, "state": "verified", "acceptance": "accepted", "method": "manual",
                    "snapshot_id": change_evidence.snapshot(self.repo, ["subject.txt"])["snapshot_id"],
                    "rationale": "reviewed", "accepted_at": jobs.iso_now(), "check_ids": [],
                    "manual_criteria": ["review"], "reservation_id": "r", "attempt_id": "a"}
        (folder / "verification.json").write_text(json.dumps(accepted))
        with mock.patch.object(change_evidence, "snapshot", wraps=change_evidence.snapshot) as refresh:
            first = collector.collect()
            self.assertIsNone(first["error"])
            self.assertEqual(first["jobs"][0]["display_state"], "verified")
            collector.collect()
            self.assertEqual(refresh.call_count, 1)
            subject.write_text("changed\n")
            changed = collector.collect()["jobs"][0]
            self.assertNotEqual(changed["display_state"], "verified")
            self.assertEqual(refresh.call_count, 2)

    def test_details_decodes_only_selected_log(self):
        selected = self.job("selected")
        self.job("other")
        (selected / "stdout.log").write_text("selected activity\n")
        collector = Collector(self.repo)
        collector.collect()
        with mock.patch.object(jobs, "decode_log_text", wraps=jobs.decode_log_text) as decode:
            detail = collector.details("selected")
        self.assertEqual(detail["job_id"], "selected")
        self.assertEqual(decode.call_count, 1)

    def test_details_rejects_attempt_replacement_after_display_data_is_loaded(self):
        for replace_directory in (False, True):
            with self.subTest(replace_directory=replace_directory):
                name = "directory" if replace_directory else "metadata"
                folder = self.job(name, task="original task", attempt_id="old", reservation_id="r")
                original = jobs._load_selected_job
                def replace_after_read(path):
                    row = original(path)
                    if replace_directory:
                        path.rename(path.with_name(path.name + "-retired"))
                        path.mkdir()
                    jobs.patch_meta(path, job_id=name, status="ok", worker="claude", model="claude-sonnet-5",
                                    task="replacement task", attempt_id="replacement", reservation_id="r")
                    return row
                with mock.patch.object(jobs, "_load_selected_job", side_effect=replace_after_read):
                    with self.assertRaisesRegex(ValueError, "identity changed"):
                        Collector(self.repo).details(name)
                self.assertEqual(jobs.load_job(folder)["attempt_id"], "replacement")
                self.assertFalse((folder / "cancel.json").exists())

    def test_slots_follow_released_ledger_and_deduplicate_legacy_queue(self):
        self.job("released", status="running", attempt_id="a", reservation_id="r")
        self.job("legacy", status="running")
        reservations = self.repo / ".rig" / "reservations"
        reservations.mkdir()
        (reservations / "r.json").write_text(json.dumps({"reservation_id": "r", "attempt_id": "a",
            "job_id": "released", "stage": "released", "slot_held": False, "owner_token": "secret"}))
        queue = self.repo / ".rig" / "queue"
        queue.mkdir()
        (queue / "q.json").write_text(json.dumps({"id": "q", "job_id": "legacy", "status": "spawned"}))
        snapshot = Collector(self.repo).collect()
        self.assertIsNone(snapshot["error"])
        self.assertEqual(snapshot["slots"], 1)
        self.assertNotIn("secret", json.dumps(snapshot))

    def test_malformed_metadata_reports_error_without_false_idle(self):
        folder = self.job()
        (folder / "meta.json").write_text("{")
        snapshot = Collector(self.repo).collect()
        self.assertTrue(snapshot["error"])
        self.assertIsNone(snapshot["slots"])


if __name__ == "__main__":
    unittest.main()
