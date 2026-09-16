#!/usr/bin/env python3
"""Measure session and workflow-list overhead for synthetic historical workflows.

Never launches vendor processes or networks. Runs in a temporary repo and cleans up.
Does not invent a baseline or savings; comparison requires an explicit recorded file.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import platform
import socket
import statistics
import subprocess
import sys
import tempfile
import time
from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))
import admission
import catalog
import harness
import jobs
import mcp_test_support
import rig_mcp
import workflow
import workflow_state as wf

PRODUCTION = (
    "scripts/workflow.py", "scripts/workflow_state.py", "scripts/workflow_scheduler.py",
    "scripts/rig_mcp.py", "scripts/jobs.py", "scripts/harness.py", "bin/rig",
)
STATUS_MIX = ("verified", "cancelled", "planned", "blocked", "completed-unverified")
DEFAULT_THRESHOLD = 0.20


def nearest_rank(values, percentile=95):
    return sorted(values)[max(0, math.ceil(percentile / 100 * len(values)) - 1)]


def csv_ints(value, *, positive=False):
    try:
        values = [int(part) for part in value.split(",")]
    except ValueError as error:
        raise argparse.ArgumentTypeError("expected comma-separated integers") from error
    if not values or any(number < (1 if positive else 0) for number in values):
        raise argparse.ArgumentTypeError("values must be positive" if positive else "values must be nonnegative")
    return list(dict.fromkeys(values))


class Fixture:
    """Real on-disk workflows plus isolation at process/network boundaries."""

    def __init__(self, repo):
        repo = repo.resolve()
        self.repo = repo
        self.bins = repo / "bins"
        self.home = repo / "home"
        self.bins.mkdir()
        self.home.mkdir()
        mcp_test_support.seed_installed_mcp(self.home)
        (repo / ".git").mkdir()
        (repo / ".rig" / "jobs").mkdir(parents=True)
        (repo / ".rig" / "workflows").mkdir(parents=True)
        (repo / ".rig" / "MEMORY.md").write_text("# Memory\n\n- Synthetic workflow fixture fact.\n")
        flags = []
        for worker in harness.WORKERS:
            enabled = worker == "grok"
            flags.append(f"{worker} = {'true' if enabled else 'false'}")
            if enabled:
                binary = self.bins / worker
                binary.write_text("#!/bin/sh\nexit 97\n")
                binary.chmod(0o755)
        (repo / ".rig" / "harness.toml").write_text(
            'parent = "codex"\n[workers]\n' + "\n".join(flags) +
            "\n[orchestration]\nmode = \"adaptive\"\nmax_nodes = 12\n[queue]\nmax_running = 3\n"
        )

    def seed(self, history):
        for index in range(history):
            status = STATUS_MIX[index % len(STATUS_MIX)]
            wid = f"hist-{index:06d}"
            spec = wf.normalize_spec({
                "workflow_id": wid,
                "title": f"synthetic {index}",
                "case": "historical workflow listing",
                "nodes": [{"id": "w1", "role": "implement", "files": [f"{wid}.py"]}],
            })
            state = wf._new_state(spec, owner={"kind": "parent", "session_id": "benchmark"})
            state["status"] = status
            row = state["nodes"]["w1"]
            if status == "verified":
                row.update(status="accepted", accepted=True, launched=True, ran=True)
            elif status == "cancelled":
                row.update(status="cancelled", launched=True, ran=True)
                state["cancel_requested"] = True
            elif status == "blocked":
                state["failure"] = {"node_id": "w1", "reason": "synthetic"}
                row.update(status="failed", launched=True, ran=True)
            elif status == "completed-unverified":
                row.update(status="completed-unverified", launched=True, ran=True, accepted=False)
            folder = wf.workflow_dir(self.repo, wid)
            folder.mkdir(parents=True, exist_ok=True)
            admission._write(folder / "spec.json", spec)
            admission._write(folder / "state.json", state)
        folder = self.repo / ".rig" / "jobs" / "session-anchor"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "meta.json").write_text(json.dumps({
            "job_id": "session-anchor", "worker": "grok", "role": "implement",
            "status": "ok", "task": "Synthetic session row",
        }) + "\n")

    @contextmanager
    def isolated(self):
        def forbidden(*args, **kwargs):
            raise RuntimeError("benchmark setup error: a process/network call escaped isolation")

        env = {
            "PATH": str(self.bins), "HOME": str(self.home), "RIG_PARENT": "codex",
            "RIG_THREAD": "benchmark-workflows", "RIG_SKIP_MODEL_CATALOG": "1",
            "RIG_REFRESH_MODELS": "0", "RIG_JOB_ID": "", "RIG_JOB_DIR": "",
            "CLAUDECODE": "", "CLAUDE_CODE": "",
            "OPENCODE_CONFIG": "", "OMP_MCP": "", "PI_CODING_AGENT_DIR": "",
            "PI_AGENT_DIR": "", "AGY_MCP": "",
        }
        with ExitStack() as stack:
            stack.enter_context(patch.dict(os.environ, env))
            stack.enter_context(patch.object(subprocess, "Popen", forbidden))
            stack.enter_context(patch.object(socket, "socket", forbidden))
            stack.enter_context(patch.object(jobs.time, "sleep", forbidden))
            if hasattr(catalog, "probe_worker"):
                stack.enter_context(patch.object(catalog, "probe_worker", forbidden))
            yield self


def measure(fixture, function, warmup, samples):
    timings, sizes = [], []
    with fixture.isolated():
        for index in range(warmup + samples):
            started = time.perf_counter_ns()
            payload = function()
            elapsed = (time.perf_counter_ns() - started) / 1_000_000
            if not isinstance(payload, str):
                payload = json.dumps(payload, ensure_ascii=False, default=str)
            if index >= warmup:
                timings.append(elapsed)
                sizes.append(len(payload.encode("utf-8")))
    return {
        "median_ms": statistics.median(timings),
        "p95_ms": nearest_rank(timings),
        "min_ms": min(timings),
        "max_ms": max(timings),
        "payload_bytes": {"median": statistics.median(sizes), "min": min(sizes), "max": max(sizes)},
        "samples_ms": timings,
        "samples_payload_bytes": sizes,
    }


def provenance():
    revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True,
                              capture_output=True, check=True).stdout.strip()
    dirty = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, text=True,
                           capture_output=True, check=True).stdout.splitlines()
    return {
        "source_revision": revision,
        "working_tree_status": dirty,
        "production_sha256": {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                              for name in PRODUCTION if (ROOT / name).is_file()},
        "evaluation_sha256": {str(Path(__file__).relative_to(ROOT)): hashlib.sha256(Path(__file__).read_bytes()).hexdigest()},
        "python": sys.version,
        "python_executable": sys.executable,
        "os": platform.platform(),
        "machine": platform.machine(),
        "recorded_at": datetime.now(timezone.utc).isoformat(),
    }


def comparison(current, baseline, threshold):
    previous = {row["id"]: row for row in baseline.get("scenarios") or []}
    rows = []
    regressions = []
    for row in current:
        old = previous.get(row["id"])
        if old is None:
            rows.append({"id": row["id"], "baseline_available": False,
                         "note": "New scenario; absolute metrics only. No savings claimed."})
            continue
        p95_delta = row["p95_ms"] - old["p95_ms"]
        median_delta = row["median_ms"] - old["median_ms"]
        limit = old["p95_ms"] * (1 + threshold) if old["p95_ms"] > 0 else 0
        within = row["p95_ms"] <= limit if old["p95_ms"] > 0 else True
        item = {
            "id": row["id"], "baseline_available": True,
            "median_ms_delta": median_delta, "p95_ms_delta": p95_delta,
            "baseline_p95_ms": old["p95_ms"], "current_p95_ms": row["p95_ms"],
            "threshold": threshold, "within_threshold": within,
        }
        rows.append(item)
        if not within:
            regressions.append(item)
    return rows, regressions


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--history-sizes", type=csv_ints, default=[0, 100, 1000])
    parser.add_argument("--warmup", type=int, default=2)
    parser.add_argument("--samples", type=int, default=11)
    parser.add_argument("--surfaces", default="session,workflows")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--compare", type=Path)
    parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD,
                        help="Max allowed p95 increase vs an explicit recorded baseline (default 0.20).")
    args = parser.parse_args()
    surfaces = list(dict.fromkeys(part.strip() for part in args.surfaces.split(",") if part.strip()))
    if args.samples < 1 or args.warmup < 0 or not set(surfaces) <= {"session", "workflows"}:
        parser.error("samples must be positive, warmup nonnegative; surfaces: session,workflows")
    if args.threshold < 0:
        parser.error("threshold must be nonnegative")
    if args.compare is not None and not args.compare.is_file():
        parser.error(f"baseline file not found: {args.compare}")

    report = {
        "schema_version": 1,
        **provenance(),
        "parameters": {
            "history_sizes": args.history_sizes, "warmup": args.warmup, "samples": args.samples,
            "surfaces": surfaces, "threshold": args.threshold,
        },
        "fixture": {
            "status_cycle": STATUS_MIX,
            "ownership": "synthetic historical workflows on disk; no vendor spawn",
            "timing": "perf_counter_ns; nearest-rank p95; setup excluded; temp repo cleaned up",
            "baseline": "optional --compare only; this script never invents a baseline or savings",
        },
        "scenarios": [],
    }

    for history in args.history_sizes:
        with tempfile.TemporaryDirectory(prefix="rig-workflow-benchmark-") as temporary:
            fixture = Fixture(Path(temporary))
            fixture.seed(history)
            for surface in surfaces:
                if surface == "session":
                    function = lambda: rig_mcp.format_session(
                        fixture.repo, "Report current work", "stay",
                        as_json=True, compact=True, terminal_limit=10,
                    )
                    identity = f"session/n={history}/compact-json"
                else:
                    function = lambda: workflow.listing(fixture.repo, include_terminal=True)
                    identity = f"workflows/n={history}"
                row = {
                    "id": identity, "surface": surface, "history_size": history,
                    **measure(fixture, function, args.warmup, args.samples),
                }
                report["scenarios"].append(row)
                print(f"{identity}: median={row['median_ms']:.3f}ms p95={row['p95_ms']:.3f}ms", file=sys.stderr)

    if args.compare:
        baseline = json.loads(args.compare.read_text())
        rows, regressions = comparison(report["scenarios"], baseline, args.threshold)
        report["comparison"] = rows
        report["compared_with"] = str(args.compare)
        report["threshold_regressions"] = regressions
        report["within_threshold"] = not regressions
    else:
        report["comparison"] = None
        report["within_threshold"] = None

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(f"Saved {len(report['scenarios'])} scenarios to {args.output}")
    if report.get("within_threshold") is False:
        raise SystemExit("p95 exceeded recorded baseline by more than the threshold; inspect the saved measurements")


if __name__ == "__main__":
    main()
