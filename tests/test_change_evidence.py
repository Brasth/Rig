#!/usr/bin/env python3
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import change_evidence as evidence


class ContentEvidence(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name).resolve()
        self.git("init", "-q")
        self.git("config", "user.name", "Fixture")
        self.git("config", "user.email", "fixture@example.invalid")
        self.subject = self.repo / "a file.txt"
        self.subject.write_text("original\n")
        (self.repo / "other.txt").write_text("other\n")
        self.git("add", ".")
        self.git("commit", "-qm", "initial")
        self.job = self.repo / ".rig" / "jobs" / "evidence-job"
        self.job.mkdir(parents=True)
        (self.job / "meta.json").write_text(json.dumps({"job_id": self.job.name, "files": ["a file.txt"]}))

    def git(self, *arguments):
        return subprocess.run(["git", "-c", "core.hooksPath=/dev/null", "-c", "commit.gpgsign=false", *arguments],
                              cwd=self.repo, text=True, capture_output=True, check=True)

    def test_initial_dirty_content_changes_and_outside_observations(self):
        self.subject.write_text("already dirty\n")
        (self.repo / "other.txt").write_text("also already dirty\n")
        before = evidence.begin(self.repo, self.job, ["a file.txt"])
        self.subject.write_text("worker changed dirty content\n")
        (self.repo / "other.txt").write_text("concurrent disjoint edit\n")
        report = evidence.finish(self.repo, self.job)
        self.assertEqual(before["dirty_paths"], ["a file.txt", "other.txt"])
        self.assertEqual(report["observed_changed_paths"], ["a file.txt", "other.txt"])
        self.assertEqual(report["scoped_changed_paths"], ["a file.txt"])
        self.assertEqual(report["out_of_scope_observations"], [{"path": "other.txt", "attribution": "uncertain"}])
        self.assertEqual(report["attribution"], "uncertain")
        self.assertEqual(self.subject.read_text(), "worker changed dirty content\n")
        self.assertEqual((self.repo / "other.txt").read_text(), "concurrent disjoint edit\n")

    def test_nul_rename_source_destination_spaces_and_newline(self):
        target = "renamed\nfile.txt"
        evidence.begin(self.repo, self.job, ["a file.txt", target])
        self.git("mv", "a file.txt", target)
        report = evidence.finish(self.repo, self.job)
        self.assertEqual(set(report["scoped_changed_paths"]), {"a file.txt", target})
        rename = next(row for row in report["after"]["status"] if "R" in row["status"])
        self.assertEqual(rename["path"], target)
        self.assertEqual(rename["source"], "a file.txt")
        self.assertEqual(report["after"]["subject"]["entries"]["a file.txt"], {"kind": "absent"})

    def test_creation_deletion_modes_and_absence(self):
        names = ["a file.txt", "new.txt"]
        before = evidence.snapshot(self.repo, names)
        self.subject.chmod(0o755)
        mode = evidence.snapshot(self.repo, names)
        self.assertNotEqual(before["snapshot_id"], mode["snapshot_id"])
        self.subject.unlink()
        (self.repo / "new.txt").write_bytes(b"new\x00binary")
        after = evidence.snapshot(self.repo, names)
        self.assertEqual(after["entries"]["a file.txt"], {"kind": "absent"})
        self.assertNotEqual(before["snapshot_id"], after["snapshot_id"])

    def test_symlink_target_content_and_link_text_are_bound(self):
        link = self.repo / "link"
        link.symlink_to("a file.txt")
        cache = {}
        before = evidence.snapshot(self.repo, ["link"], cache)
        link.write_text("write through link\n")
        changed = evidence.snapshot(self.repo, ["link"], cache)
        self.assertNotEqual(before["snapshot_id"], changed["snapshot_id"])
        link.unlink()
        link.symlink_to("other.txt")
        self.assertNotEqual(changed["snapshot_id"], evidence.snapshot(self.repo, ["link"])["snapshot_id"])

    def test_symlink_external_target_is_not_read_or_verifiable(self):
        with tempfile.TemporaryDirectory() as outside:
            external = Path(outside) / "external"
            external.write_text("must not read")
            (self.repo / "link").symlink_to(external)
            with patch.object(Path, "open", side_effect=AssertionError("external file read")):
                with self.assertRaisesRegex(evidence.EvidenceError, "inside"):
                    evidence.snapshot(self.repo, ["link"])
            before = evidence.begin(self.repo, self.job, ["link"])
            self.assertIsNone(before["subject"])
            self.assertTrue(before["snapshot_error"])

    def test_symlink_directory_and_scope_escapes_rejected(self):
        (self.repo / "directory").mkdir()
        (self.repo / "directory-link").symlink_to("directory", target_is_directory=True)
        for files in (["directory"], ["../outside"], ["*.py"], [], [".git/config"]):
            with self.subTest(files=files), self.assertRaises(evidence.EvidenceError):
                evidence.snapshot(self.repo, files)
        with self.assertRaises(evidence.EvidenceError):
            evidence.snapshot(self.repo, ["directory-link"])
        self.assertEqual(evidence.normalize_files(self.repo, ["directory/../a file.txt"]), ["a file.txt"])

    def test_git_control_artifacts_do_not_pollute_observations(self):
        evidence.begin(self.repo, self.job, ["a file.txt"])
        (self.job / "stdout.log").write_text("worker output")
        (self.repo / ".rig" / "MEMORY.md").write_text("harness fact")
        report = evidence.finish(self.repo, self.job)
        self.assertEqual(report["observed_changed_paths"], [])
        self.assertFalse(any(path.startswith(".rig/") for path in report["after"]["inventory"]))

    def test_tracked_and_declared_rig_source_remains_observable(self):
        source = self.repo / ".rig" / "harness.toml"
        source.write_text("before")
        self.git("add", ".rig/harness.toml")
        self.git("commit", "-qm", "track config")
        evidence.begin(self.repo, self.job, [".rig/harness.toml"])
        source.write_text("after")
        self.assertIn(".rig/harness.toml", evidence.finish(self.repo, self.job)["scoped_changed_paths"])

    def test_unknown_scope_observes_dirty_files_without_verified_subject(self):
        self.subject.write_text("dirty before")
        before = evidence.begin(self.repo, self.job, [])
        self.subject.write_text("dirty after")
        after = evidence.finish(self.repo, self.job)
        self.assertIsNone(before["subject"])
        self.assertEqual(after["snapshot_id"], "")
        self.assertEqual(after["observed_changed_paths"], ["a file.txt"])
        self.assertEqual(after["attribution"], "uncertain")

    def test_index_and_head_changes_observe_committed_outside_edit(self):
        before = evidence.begin(self.repo, self.job, ["a file.txt"])
        (self.repo / "other.txt").write_text("committed concurrently")
        self.git("add", "other.txt")
        self.git("commit", "-qm", "concurrent change")
        after = evidence.finish(self.repo, self.job)
        self.assertNotEqual(before["head"], after["after"]["head"])
        self.assertNotEqual(before["index_id"], after["after"]["index_id"])
        self.assertEqual(after["observed_changed_paths"], ["other.txt"])

    def test_non_utf8_git_status_path_json_round_trips(self):
        name = os.fsdecode(b"odd-\xff.txt")
        # APFS rejects invalid UTF-8 names; the byte parser and JSON transport
        # still need to preserve such names produced on other Git filesystems.
        rows = evidence.parse_status(b"?? odd-\xff.txt\0")
        self.assertEqual(rows[0]["path"], name)
        evidence.write_json(self.job / "byte-paths.json", {"status": rows})
        persisted = json.loads((self.job / "byte-paths.json").read_text())
        self.assertEqual(persisted["status"][0]["path"], name)

    def test_snapshot_cache_still_observes_same_size_content_change(self):
        cache = {}
        previous = self.subject.stat()
        first = evidence.snapshot(self.repo, ["a file.txt"], cache)
        self.subject.write_text("modified\n")
        os.utime(self.subject, ns=(previous.st_atime_ns, previous.st_mtime_ns))
        second = evidence.snapshot(self.repo, ["a file.txt"], cache)
        self.assertNotEqual(first["snapshot_id"], second["snapshot_id"])

    def test_bracketed_route_creation_and_deletion_are_literal(self):
        name = "app/[id]/page.tsx"
        before = evidence.snapshot(self.repo, [name])
        self.assertEqual(before["files"], [name])
        target = self.repo / name
        target.parent.mkdir(parents=True)
        target.write_text("export default function Page() {}")
        created = evidence.snapshot(self.repo, [name])
        self.assertNotEqual(before["snapshot_id"], created["snapshot_id"])
        target.unlink()
        self.assertEqual(before["snapshot_id"], evidence.snapshot(self.repo, [name])["snapshot_id"])

    def test_leading_trailing_spaces_and_existing_question_mark_are_preserved(self):
        names = [" leading and trailing .txt ", "literal?.txt"]
        for name in names:
            (self.repo / name).write_text("before")
        before = evidence.begin(self.repo, self.job, names)
        self.assertEqual(before["files"], sorted(names))
        for name in names:
            (self.repo / name).write_text("after")
        after = evidence.finish(self.repo, self.job)
        self.assertEqual(after["scoped_changed_paths"], sorted(names))
        self.assertIn(" leading and trailing .txt ", after["after"]["inventory"])

    def test_before_evidence_is_idempotent_and_never_overwrites_malformed_history(self):
        first = evidence.begin(self.repo, self.job, ["a file.txt"])
        self.subject.write_text("changed later")
        self.assertEqual(first, evidence.begin(self.repo, self.job, ["a file.txt"]))
        with self.assertRaises(evidence.EvidenceError):
            evidence.begin(self.repo, self.job, ["other.txt"])
        (self.job / "change-before.json").write_text("{")
        with self.assertRaisesRegex(evidence.EvidenceError, "will not be replaced"):
            evidence.begin(self.repo, self.job, ["a file.txt"])
        self.assertEqual((self.job / "change-before.json").read_text(), "{")


if __name__ == "__main__":
    unittest.main()
