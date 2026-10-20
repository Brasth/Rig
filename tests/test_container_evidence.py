#!/usr/bin/env python3
"""Where a job ran, as shown on the board."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import board_view  # noqa: E402
import tui_runtime  # noqa: E402
import tui_view  # noqa: E402

FOUND = {"provider": "devcontainer", "container": {
    "found": True, "id": "a3f9c1d2e4b5f6", "name": "my-dev", "image": "node:22-bookworm",
    "image_digest": "sha256:9f1c2d3e4a5b6c7d8e9f0a1b2c3d4e5f", "state": "running"}}


class ReadSandbox(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.dir = Path(self.td.name)

    def tearDown(self):
        self.td.cleanup()

    def write(self, value):
        (self.dir / "sandbox.json").write_text(value if isinstance(value, str) else json.dumps(value))

    def test_keeps_only_known_string_fields(self):
        self.write({**FOUND, "token": "SECRET", "container": {**FOUND["container"], "env": {"KEY": "SECRET"}, "id": 5}})
        box = tui_runtime.read_sandbox(self.dir)
        self.assertEqual(box["provider"], "devcontainer")
        self.assertNotIn("SECRET", json.dumps(box))
        self.assertNotIn("id", box["container"], "a non-string id is dropped, not coerced")
        self.assertEqual(box["container"]["image"], "node:22-bookworm")
        self.assertIs(box["container"]["found"], True)

    def test_unusable_files_give_none_without_raising(self):
        self.assertIsNone(tui_runtime.read_sandbox(self.dir))
        for bad in ("not json", "[]", '{"provider": 7}', "{}", "x" * 20000):
            self.write(bad)
            self.assertIsNone(tui_runtime.read_sandbox(self.dir), bad[:20])

    def test_long_values_are_truncated(self):
        self.write({"provider": "p" * 500, "container": {"found": True, "image": "i" * 500}})
        box = tui_runtime.read_sandbox(self.dir)
        self.assertEqual((len(box["provider"]), len(box["container"]["image"])), (40, 200))

    def test_found_must_be_literally_true(self):
        self.write({"provider": "devcontainer", "container": {"found": "yes", "id": "x"}})
        self.assertIs(tui_runtime.read_sandbox(self.dir)["container"]["found"], False)


class OnTheBoard(unittest.TestCase):
    def test_visible_jobs_attach_the_evidence_only_when_it_exists(self):
        with tempfile.TemporaryDirectory() as td:
            with_box, without = Path(td) / "a", Path(td) / "b"
            for folder in (with_box, without):
                folder.mkdir()
            (with_box / "sandbox.json").write_text(json.dumps(FOUND))
            rows = {"a": {"job_id": "a", "dir": str(with_box)}, "b": {"job_id": "b", "dir": str(without)}}
            with mock.patch.object(tui_runtime.rig_jobs, "project_job", side_effect=lambda job, **k: dict(rows[job["job_id"]])):
                out = tui_runtime.visible_jobs([{"job_id": "a"}, {"job_id": "b"}], Path(td), 0, 2)
        self.assertEqual(out[0]["sandbox"]["container"]["id"], "a3f9c1d2e4b5f6")
        self.assertNotIn("sandbox", out[1])

    def test_detail_lines_say_where_the_job_ran(self):
        lines = tui_view._detail_lines({"job_id": "j", "display_state": "working", "worker": "claude", "sandbox": FOUND})
        self.assertIn("sandbox devcontainer · a3f9c1d2e4b5 · node:22-bookworm · sha256:9f1c2d3e4a5b", lines)

    def test_unknown_container_facts_are_stated_as_unknown(self):
        box = {"provider": "devcontainer", "container": {"found": False, "note": "dc ls reported no container"}}
        lines = tui_view._detail_lines({"job_id": "j", "display_state": "working", "sandbox": box})
        self.assertIn("sandbox devcontainer; container facts unavailable (dc ls reported no container)", lines)
        bare = tui_view._detail_lines({"job_id": "j", "display_state": "working", "sandbox": {"provider": "devcontainer", "container": {"found": False}}})
        self.assertIn("sandbox devcontainer; container facts unavailable", bare)

    def test_host_jobs_show_no_sandbox_line_and_are_otherwise_unchanged(self):
        job = {"job_id": "j", "display_state": "working", "worker": "claude"}
        self.assertFalse([l for l in tui_view._detail_lines(job) if l.startswith("sandbox")])
        with_box = tui_view._detail_lines({**job, "sandbox": FOUND})
        self.assertEqual([l for l in with_box if not l.startswith("sandbox")], tui_view._detail_lines(job))

    def test_the_go_shell_payload_carries_the_same_text(self):
        job = {"job_id": "job-1", "worker": "claude", "effective": "running", "display_state": "working",
               "task": "t", "sandbox": FOUND}
        view = board_view.build([job], [], [], 1, 3, "/r")
        self.assertTrue(any(l.startswith("sandbox devcontainer · a3f9c1d2e4b5") for l in view["jobs"][0]["detail"]))


if __name__ == "__main__":
    unittest.main()
