#!/usr/bin/env python3
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import token_usage as usage  # noqa: E402


CANON = {"input": 10, "output": 4, "reasoning": 2, "cached_input": 1, "total": 17}


class TokenUsage(unittest.TestCase):
    def test_normalize_aliases_and_preserve_reported_subset(self):
        got = usage.normalize_token_usage({
            "input_tokens": 10, "output_tokens": 4, "reasoning_tokens": 2,
            "cache_read_input_tokens": 1, "total_tokens": 17,
        })
        self.assertEqual(got, CANON)
        partial = usage.normalize_token_usage({"input": 10, "output": 4, "total": 14})
        self.assertEqual(partial, {"input": 10, "output": 4, "total": 14})
        self.assertNotIn("reasoning", partial)
        self.assertNotIn("cached_input", partial)
        no_total = usage.normalize_token_usage({
            "input": 10, "output": 4, "reasoning": 0, "cached_input": 0,
        })
        self.assertEqual(no_total, {"input": 10, "output": 4, "reasoning": 0, "cached_input": 0})
        self.assertNotIn("total", no_total)
        with self.assertRaises(usage.UsageError):
            usage.normalize_token_usage({})
        with self.assertRaises(usage.UsageError):
            usage.normalize_token_usage({"total_cost_usd": 1.25})

    def test_reject_negative_bool_float_and_conflict(self):
        for bad in (
            {**CANON, "input": -1},
            {**CANON, "total": True},
            {**CANON, "output": 1.5},
            {**CANON, "input": 10, "input_tokens": 11},
        ):
            with self.assertRaises(usage.UsageError):
                usage.normalize_token_usage(bad)
        self.assertIsNone(usage.load_token_usage({**CANON, "input": -1}))
        self.assertIsNone(usage.normalize_token_usage(None))

    def test_only_final_result_events(self):
        events = [
            {"type": "assistant", "usage": CANON},
            {"type": "result", "usage": {
                "input": 10, "output": 4, "reasoning": 2, "cached_input": 1, "total": 17,
            }},
        ]
        self.assertEqual(usage.usage_from_events(events), CANON)
        self.assertIsNone(usage.usage_from_events([{"type": "assistant", "usage": CANON}]))
        self.assertIsNone(usage.usage_from_events([{"type": "result"}]))

    def test_grok_end_event_with_stop_reason(self):
        grok = {
            "type": "end",
            "stopReason": "end_turn",
            "usage": {
                "input_tokens": 10,
                "output_tokens": 4,
                "reasoning_tokens": 2,
                "cache_read_input_tokens": 1,
                "total_tokens": 17,
                "total_cost_usd": 1.25,
            },
        }
        self.assertEqual(usage.usage_from_events([grok]), CANON)
        self.assertNotIn("total_cost_usd", usage.usage_from_events([grok]))
        self.assertIsNone(usage.usage_from_events([{"type": "end", "usage": CANON}]))
        self.assertIsNone(usage.usage_from_events([
            {"type": "tool_call", "stopReason": "end_turn", "usage": CANON},
        ]))

    def test_reject_malformed_nonfinal_does_not_count_but_bad_result_does(self):
        self.assertIsNone(usage.usage_from_events([
            {"type": "result", "usage": {**CANON, "input": -1}},
        ]))
        self.assertIsNone(usage.usage_from_events([
            {"type": "result", "usage": CANON},
            {"type": "result", "token_usage": {**CANON, "total": 99}},
        ]))
        self.assertEqual(usage.usage_from_events([
            {"type": "result", "usage": CANON},
            {"type": "result", "token_usage": CANON},
        ]), CANON)

    def test_does_not_synthesize_total(self):
        raw = json.dumps({
            "type": "result",
            "usage": {"input": 10, "output": 4, "reasoning": 2, "cached_input": 1},
        })
        got = usage.usage_from_text(raw)
        self.assertEqual(got, {"input": 10, "output": 4, "reasoning": 2, "cached_input": 1})
        self.assertNotIn("total", got)

    def test_partial_final_usage_persists_only_reported_fields(self):
        events = [{"type": "result", "usage": {"input_tokens": 8, "output_tokens": 2}}]
        got = usage.usage_from_events(events)
        self.assertEqual(got, {"input": 8, "output": 2})
        self.assertNotIn("reasoning", got)
        self.assertNotIn("cached_input", got)
        self.assertNotIn("total", got)
        grok = {
            "type": "end",
            "stopReason": "end_turn",
            "usage": {"reasoning_tokens": 3, "cache_read_input_tokens": 1},
        }
        grok_got = usage.usage_from_events([grok])
        self.assertEqual(grok_got, {"reasoning": 3, "cached_input": 1})
        self.assertNotIn("total", grok_got)
        self.assertIsNone(usage.usage_from_events([
            {"type": "result", "usage": {"input": 8}},
            {"type": "result", "usage": {"input": 8, "output": 1}},
        ]))

    def test_source_provenance_strict(self):
        self.assertEqual(usage.usage_source({"source": "parent"}, default="parent"), "parent")
        self.assertEqual(usage.usage_source({"input": 1}, default="wrapper"), "wrapper")
        self.assertEqual(usage.usage_source({"source": "native_child"}), "native_child")
        self.assertEqual(usage.usage_source(None), "")
        with self.assertRaises(usage.UsageError):
            usage.usage_source({"source": "guessed"})
        with self.assertRaises(usage.UsageError):
            usage.usage_source({"source": "parent"}, default="wrapper")
        self.assertNotIn("cost", usage.FIELDS)

    def test_job_dir_reads_stdout_only(self):
        with tempfile.TemporaryDirectory() as temp:
            job = Path(temp)
            (job / "stdout.log").write_text(json.dumps({"type": "result", "usage": CANON}) + "\n")
            self.assertEqual(usage.usage_from_job_dir(job), CANON)
            (job / "stdout.log").write_text(json.dumps({"type": "result", "usage": {**CANON, "total": -3}}) + "\n")
            self.assertIsNone(usage.usage_from_job_dir(job))


if __name__ == "__main__":
    unittest.main()
