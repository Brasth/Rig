#!/usr/bin/env python3
"""Forward-only runtime evidence, pure aggregation, and lifecycle preservation."""
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import ask
import admission
import runtime_metrics as metrics
import workflow_cancellation
import workflow_scheduler as sched
import workflow_state as wf


def stamp(second):
    return f"2026-09-01T00:{second // 60:02d}:{second % 60:02d}Z"


def at(second):
    return metrics.parse_stamp(stamp(second))


def event(second, revision, blocked=None):
    return {"kind": "runtime-state", "schema_version": 1, "at": stamp(second),
            "runtime_revision": revision, "blocked": blocked or []}


def ask_event(second, phase, ask_id="q1", job="j1", attempt="a1"):
    return {"kind": "runtime-ask", "schema_version": 1, "observed_at": stamp(second),
            "job_id": job, "attempt_id": attempt, "ask_id": ask_id, "phase": phase}


def blocked(cause="coordination", job="j1", attempt="a1"):
    return {"cause": cause, "key": "request", "job_id": job, "attempt_id": attempt}


def state(revision=1, **fields):
    return {"status": "running", "metrics": {"runtime_from_creation": True, "runtime_revision": revision}, **fields}


class PureMetrics(unittest.TestCase):
    def test_distribution_explicit_coverage_and_quantiles(self):
        out = metrics.distribution([0, 1, 2, 3, 4, -1, None, float("nan"), float("inf"), True])
        self.assertEqual(out["sample_count"], 5)
        self.assertEqual(out["missing_count"], 5)
        self.assertEqual(out["coverage"], .5)
        self.assertEqual((out["p50_s"], out["p95_s"]), (2, 4))
        self.assertIsNone(metrics.distribution([])["coverage"])

    def test_timestamp_timezone_and_malformed(self):
        self.assertEqual(metrics.parse_stamp("2026-09-01T01:00:00+01:00"), at(0))
        self.assertEqual(metrics.parse_stamp("2026-09-01T00:00:00"), at(0))
        for value in [None, [], 0, "broken", "2026-99-01T00:00:00Z"]:
            self.assertIsNone(metrics.parse_stamp(value))
        self.assertIsNone(metrics.duration(stamp(10), stamp(1)))

    def test_execution_latency_excludes_cancel_running_and_launch_failure(self):
        base = {"status": "ok", "started_at": stamp(0), "ended_at": stamp(10), "elapsed_s": 99}
        self.assertEqual(metrics.execution_latency(base), 10)
        for fields in [{"status": "cancelled"}, {"status": "running"}, {"execution_mode": "not_started"},
                       {"started_at": stamp(11)}, {"ended_at": "malformed"}]:
            self.assertIsNone(metrics.execution_latency({**base, **fields}))
        self.assertEqual(metrics.execution_latency({"status": "ok", "elapsed_s": 0}), 0)
        for field in ("started_at", "ended_at"):
            for bad in ("bad", False, 123):
                self.assertIsNone(metrics.execution_latency({"status": "ok", field: bad, "elapsed_s": 5}))

    def test_known_zero_distinct_from_legacy_unknown(self):
        known = metrics.blocked_report(state(), [event(0, 1)], [], now=at(20))
        self.assertEqual(known["coverage"], "recorded")
        self.assertEqual(known["observed_wall_s"], 0)
        self.assertTrue(known["known_zero"])
        unknown = metrics.blocked_report({"status": "running"}, [], [], now=at(20))
        self.assertEqual(unknown["coverage"], "unknown")
        self.assertIsNone(unknown["observed_wall_s"])
        self.assertFalse(unknown["known_zero"])

    def test_repeated_ask_and_overlapping_jobs_use_union(self):
        rows = [event(0, 1), ask_event(1, "open"), ask_event(4, "close"),
                ask_event(5, "open", "q2"), ask_event(6, "open", "q3", "j2", "a2"),
                ask_event(9, "close", "q2"), ask_event(12, "close", "q3", "j2", "a2")]
        out = metrics.blocked_report(state(), rows, [], now=at(20))
        self.assertEqual(out["observed_wall_s"], 10)  # [1,4] union [5,12]
        self.assertEqual(out["interval_count"], 3)
        self.assertEqual(out["by_cause"]["ask"]["observed_wall_s"], 10)

    def test_delayed_replacement_append_order_does_not_reopen_old_ask(self):
        rows = [event(0, 1), ask_event(4, "open", "q2"), ask_event(7, "close", "q2"), ask_event(1, "open")]
        job = {"job_id": "j1", "attempt_id": "a1", "status": "running"}
        out = metrics.blocked_report(state(), rows, [job], now=at(20))
        self.assertEqual(out["observed_wall_s"], 6)
        self.assertEqual(out["open_interval_count"], 0)
        self.assertEqual(out["invalid_event_count"], 0)

    def test_same_clock_replacement_uses_causal_edge(self):
        rows = [event(0, 1), {**ask_event(1, "open", "q2"), "replaces_ask_id": "q1"},
                ask_event(7, "close", "q2"), ask_event(1, "open")]
        job = {"job_id": "j1", "attempt_id": "a1", "status": "running"}
        out = metrics.blocked_report(state(), rows, [job], now=at(20))
        self.assertEqual(out["observed_wall_s"], 6)
        self.assertEqual(out["open_interval_count"], 0)
        self.assertEqual(out["coverage"], "recorded")

    def test_same_clock_replacement_chain_retains_latest_open(self):
        rows = [event(0, 1), {**ask_event(1, "open", "q3"), "replaces_ask_id": "q2"},
                {**ask_event(1, "open", "q2"), "replaces_ask_id": "q1"}, ask_event(1, "open")]
        job = {"job_id": "j1", "attempt_id": "a1", "status": "running"}
        out = metrics.blocked_report(state(), rows, [job], now=at(20))
        self.assertEqual(out["observed_wall_s"], 19)
        self.assertEqual(out["coverage"], "recorded")
        self.assertEqual(out["open_interval_count"], 1)

    def test_cyclic_or_backwards_causal_replacement_is_partial(self):
        job = {"job_id": "j1", "attempt_id": "a1", "status": "running"}
        cases = [
            [{**ask_event(1, "open"), "replaces_ask_id": "q2"},
             {**ask_event(2, "open", "q2"), "replaces_ask_id": "q1"}],
            [ask_event(2, "open"), {**ask_event(1, "open", "q2"), "replaces_ask_id": "q1"}],
            [{**ask_event(1, "open"), "replaces_ask_id": []}],
        ]
        for case in cases:
            out = metrics.blocked_report(state(), [event(0, 1), *case], [job], now=at(20))
            self.assertEqual(out["coverage"], "partial")
            self.assertGreaterEqual(out["observed_wall_s"], 0)
            self.assertGreater(out["invalid_event_count"], 0)

    def test_same_clock_answered_prompt_does_not_hide_current_open(self):
        rows = [event(0, 1), ask_event(1, "open"), ask_event(1, "close"), ask_event(1, "open", "q2")]
        job = {"job_id": "j1", "attempt_id": "a1", "status": "running"}
        out = metrics.blocked_report(state(), rows, [job], now=at(20))
        self.assertEqual(out["observed_wall_s"], 19)
        self.assertEqual(out["coverage"], "recorded")
        self.assertEqual(out["open_interval_count"], 1)

    def test_ambiguous_same_clock_open_is_partial(self):
        rows = [event(0, 1), ask_event(1, "open", "q2"), ask_event(7, "close", "q2"), ask_event(1, "open")]
        job = {"job_id": "j1", "attempt_id": "a1", "status": "running"}
        out = metrics.blocked_report(state(), rows, [job], now=at(20))
        self.assertEqual(out["observed_wall_s"], 6)
        self.assertEqual(out["coverage"], "partial")
        self.assertEqual(out["open_interval_count"], 0)

    def test_replaced_pending_prompt_closes_previous(self):
        rows = [event(0, 1), ask_event(1, "open"), ask_event(4, "open", "q2"), ask_event(7, "close", "q2")]
        out = metrics.blocked_report(state(), rows, [], now=at(20))
        self.assertEqual(out["observed_wall_s"], 6)
        self.assertEqual(out["open_interval_count"], 0)

    def test_cross_cause_overlap_is_not_summed(self):
        rows = [event(0, 1), event(2, 2, [blocked()]), ask_event(4, "open"),
                event(8, 3), ask_event(10, "close")]
        out = metrics.blocked_report(state(3), rows, [], now=at(20))
        self.assertEqual(out["observed_wall_s"], 8)
        self.assertEqual(out["by_cause"]["coordination"]["observed_wall_s"], 6)
        self.assertEqual(out["by_cause"]["ask"]["observed_wall_s"], 6)
        self.assertIsNone(out["complete_wall_s"])

    def test_live_open_ask_and_cancelled_attempt_endpoint(self):
        rows = [event(0, 1), ask_event(2, "open")]
        job = {"job_id": "j1", "attempt_id": "a1", "status": "running"}
        out = metrics.blocked_report(state(), rows, [job], now=at(20))
        self.assertEqual(out["observed_wall_s"], 18)
        self.assertEqual(out["open_interval_count"], 1)
        stopped = {**job, "status": "cancelled", "ended_at": stamp(8)}
        self.assertEqual(metrics.blocked_report(state(), rows, [stopped], now=at(20))["observed_wall_s"], 6)

    def test_terminal_workflow_caps_open_interval(self):
        rows = [event(0, 1), event(2, 2, [blocked()])]
        out = metrics.blocked_report(state(2, status="cancelled", updated_at=stamp(8)), rows, [], now=at(20))
        self.assertEqual(out["observed_wall_s"], 6)

    def test_stale_attempt_does_not_use_replacement_endpoint(self):
        rows = [event(0, 1), ask_event(2, "open")]
        replacement = {"job_id": "j1", "attempt_id": "different", "status": "cancelled", "ended_at": stamp(8)}
        out = metrics.blocked_report(state(), rows, [replacement], now=at(20))
        self.assertEqual(out["coverage"], "partial")
        self.assertEqual(out["observed_wall_s"], 0)
        self.assertFalse(out["known_zero"])

    def test_malformed_and_backwards_timestamps_are_partial(self):
        rows = [event(0, 1), event(8, 2, [blocked()]), event(4, 3),
                ask_event(12, "open"), ask_event(3, "close"), ask_event(2, "nonsense")]
        out = metrics.blocked_report(state(3), rows, [], now=at(20))
        self.assertEqual(out["coverage"], "partial")
        self.assertGreater(out["invalid_event_count"], 0)
        self.assertGreaterEqual(out["observed_wall_s"], 0)
        self.assertFalse(out["known_zero"])

    def test_delayed_event_append_uses_matching_timestamps(self):
        rows = [event(0, 1), ask_event(9, "close"), ask_event(2, "open")]
        out = metrics.blocked_report(state(), rows, [], now=at(20))
        self.assertEqual(out["observed_wall_s"], 7)
        self.assertEqual(out["invalid_event_count"], 0)

    def test_missing_revision_and_legacy_forward_observations_partial(self):
        rows = [event(0, 1), event(2, 3, [blocked()])]
        self.assertEqual(metrics.blocked_report(state(3), rows, [], now=at(20))["coverage"], "partial")
        self.assertEqual(metrics.blocked_report({"metrics": {"runtime_revision": 1}}, [event(0, 1)], [], now=at(20))["coverage"], "partial")

    def test_retry_continuation_cancel_and_partial_tokens_are_distinct(self):
        jobs = [{"status": "cancelled", "elapsed_s": 9},
                {"status": "ok", "elapsed_s": 4, "continues_job_id": "old", "token_usage": {"input": 0}},
                {"status": "fail", "elapsed_s": 2, "token_usage": {"total": 12}}]
        events = [{"kind": "resolved", "action": "retry"}, {"kind": "resolved", "action": "skip"}]
        out = metrics.workflow_metrics({}, {}, events, jobs, now=at(20))
        self.assertEqual(out["workflow_retry_count"], 1)
        self.assertEqual(out["continuation_count"], 1)
        self.assertEqual(out["cancelled_attempts"], 1)
        self.assertEqual(out["execution_latency"]["sample_count"], 2)
        self.assertEqual(out["token_components"]["input"], {"known": 1, "unknown": 2, "sum": 0})
        self.assertEqual(out["token_components"]["total"], {"known": 1, "unknown": 2, "sum": 12})
        self.assertIsNone(out["token_components"]["output"]["sum"])


class EvidenceIntegration(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        (self.repo / ".git").mkdir()
        (self.repo / ".rig").mkdir()
        (self.repo / ".rig/harness.toml").write_text('[project]\nenabled=true\n[orchestration]\nmode="adaptive"\n')
        self.environment = patch.dict(os.environ, {"RIG_OWNER_SESSION": "metrics-tests"})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.created = wf.create_workflow(self.repo, {"workflow_id": "w", "nodes": [
            {"id": "n", "role": "explore", "files": []}]}, owner_session="metrics-tests")
        self.folder = self.repo / ".rig/jobs/j"
        self.folder.mkdir(parents=True)
        self.meta = {"job_id": "j", "attempt_id": "a", "reservation_id": "r", "status": "running",
                     "workflow_id": "w", "workflow_node_id": "n"}
        self.write_meta()
        self.spec, self.current = wf.load_pair(self.repo, "w")
        self.current["status"] = "running"
        self.current["nodes"]["n"].update(job_id="j", attempt_id="a", reservation_id="r", status="running")
        wf.save_state(self.repo, self.current)

    def write_meta(self):
        (self.folder / "meta.json").write_text(json.dumps(self.meta))

    def snapshot(self):
        return {str(path.relative_to(self.repo)): (path.read_bytes(), path.stat().st_mtime_ns)
                for path in self.repo.rglob("*") if path.is_file()}

    def test_repeated_ask_records_content_free_attempt_scoped_events(self):
        with patch.object(ask, "iso_now", side_effect=[stamp(1), stamp(4), stamp(5), stamp(8)]):
            first = ask.write_ask(self.folder, "tool", {"secret": "NEVER_COPY"})
            ask.write_reply(self.folder, "allow", expected=first)
            second = ask.write_ask(self.folder, "tool", {"secret": "NEVER_COPY"})
            ask.write_reply(self.folder, "deny", expected=second)
        rows = [row for row in wf.list_events(self.repo, "w") if row["kind"] == "runtime-ask"]
        self.assertEqual([row["phase"] for row in rows], ["open", "close", "open", "close"])
        self.assertTrue(all(row["attempt_id"] == "a" for row in rows))
        self.assertNotIn("NEVER_COPY", json.dumps(rows))
        self.assertNotIn("owner_token", json.dumps(rows))

    def test_report_is_byte_and_mtime_read_only(self):
        before = self.snapshot()
        with patch.object(wf, "refresh", side_effect=AssertionError("report must not refresh")):
            first = sched.report(self.repo, "w")
            second = sched.report(self.repo, "w")
        self.assertEqual(before, self.snapshot())
        self.assertEqual(first["runtime_metrics"]["blocked_time"]["observed_wall_s"], 0)
        self.assertEqual(first["runtime_metrics"]["blocked_time"], second["runtime_metrics"]["blocked_time"])

    def test_durable_stop_visible_without_refresh_or_new_events(self):
        target = workflow_cancellation.capture(self.repo, "w", owner_token=self.created["owner_token"], owner_session="metrics-tests")
        with patch.object(workflow_cancellation, "cancel_jobs", return_value=[]):
            workflow_cancellation.publish(target)
        before = self.snapshot()
        result = sched.report(self.repo, "w")
        self.assertEqual(result["status"], "cancel-requested")
        self.assertEqual(before, self.snapshot())
        self.assertFalse(wf.node_ready(self.spec, wf.load_pair(self.repo, "w")[1], self.spec["nodes"][0]))

    def test_unscoped_historical_launch_cannot_borrow_new_attempt(self):
        self.current["nodes"]["n"].update(job_id="", attempt_id="", reservation_id="", status="pending")
        wf.save_state(self.repo, self.current)
        self.meta.update(attempt_id="replacement", status="ok", elapsed_s=5, token_usage={"total": 999})
        self.write_meta()
        wf.append_event(self.repo, "w", "launched", {"job_id": "j", "node_id": "n"})
        result = sched.report(self.repo, "w")["runtime_metrics"]
        self.assertEqual(result["attempt_coverage"]["loaded"], 0)
        self.assertGreater(result["attempt_coverage"]["missing"], 0)
        self.assertEqual(result["execution_latency"]["sample_count"], 0)
        self.assertIsNone(result["token_components"]["total"]["sum"])

    def test_metrics_write_failure_never_changes_permission_or_state_result(self):
        with patch.object(wf, "append_event", side_effect=OSError("evidence disk unavailable")):
            pending = ask.write_ask(self.folder, "tool", {})
            reply = ask.write_reply(self.folder, "allow", expected=pending)
            self.assertEqual(reply["behavior"], "allow")
            self.current["nodes"]["n"]["status"] = "unconfirmed"
            wf.save_state(self.repo, self.current)
        report = sched.report(self.repo, "w")
        self.assertEqual(report["runtime_metrics"]["blocked_time"]["coverage"], "partial")

    def test_admission_busy_or_load_failure_marks_coverage_gap(self):
        for target, name, error in [(admission, "transaction", admission.AdmissionBusy("busy")),
                                    (wf, "load_pair", OSError("unreadable"))]:
            with self.subTest(name=name):
                with patch.object(target, name, side_effect=error):
                    pending = ask.write_ask(self.folder, "tool", {})
                    self.assertEqual(ask.write_reply(self.folder, "allow", expected=pending)["behavior"], "allow")
                result = sched.report(self.repo, "w")["runtime_metrics"]["blocked_time"]
                self.assertEqual(result["coverage"], "partial")
                self.assertFalse(result["known_zero"])
                self.assertTrue(result["persistence_gap"])

    def test_pending_replacement_records_causal_identity(self):
        with patch.object(ask, "iso_now", return_value=stamp(1)):
            first = ask.write_ask(self.folder, "tool", {})
            second = ask.write_ask(self.folder, "tool", {})
        rows = [row for row in wf.list_events(self.repo, "w") if row["kind"] == "runtime-ask"]
        self.assertEqual(rows[-1]["replaces_ask_id"], first["ask_id"])
        self.assertEqual(rows[-1]["ask_id"], second["ask_id"])

    def test_lost_ask_event_is_partial_even_without_state_transition(self):
        with patch.object(wf, "append_event", side_effect=OSError("evidence disk unavailable")):
            pending = ask.write_ask(self.folder, "tool", {})
            self.assertEqual(ask.write_reply(self.folder, "deny", expected=pending)["behavior"], "deny")
        result = sched.report(self.repo, "w")["runtime_metrics"]["blocked_time"]
        self.assertEqual(result["coverage"], "partial")
        self.assertFalse(result["known_zero"])
        self.assertTrue(result["persistence_gap"])

    def test_repeated_state_write_has_no_new_transition(self):
        before = wf.list_events(self.repo, "w")
        wf.save_state(self.repo, self.current)
        self.assertEqual(before, wf.list_events(self.repo, "w"))

    def test_stale_ask_attempt_cannot_append_current_evidence(self):
        first = ask.write_ask(self.folder, "tool", {})
        self.meta["attempt_id"] = "replacement"
        self.write_meta()
        before = wf.list_events(self.repo, "w")
        with self.assertRaisesRegex(ValueError, "earlier job attempt"):
            ask.write_reply(self.folder, "allow", expected=first)
        self.assertEqual(before, wf.list_events(self.repo, "w"))

    def test_legacy_latest_ask_does_not_create_history_on_read(self):
        path = self.repo / ".rig/workflows/w/state.json"
        value = json.loads(path.read_text())
        value["metrics"] = {}
        path.write_text(json.dumps(value))
        for row in (path.parent / "events").glob("*.json"):
            row.unlink()
        (self.folder / "ask.json").write_text(json.dumps({"ask_id": "legacy", "attempt_id": "a", "asked_at": stamp(1)}))
        before = self.snapshot()
        result = sched.report(self.repo, "w", now=at(20))
        self.assertEqual(result["runtime_metrics"]["blocked_time"]["coverage"], "unknown")
        self.assertIsNone(result["runtime_metrics"]["blocked_time"]["observed_wall_s"])
        self.assertEqual(before, self.snapshot())


if __name__ == "__main__":
    unittest.main()
