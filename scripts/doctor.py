#!/usr/bin/env python3
"""Offline task readiness. Configuration is evidence, never a live provider test."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import catalog
import child_mcp
import harness
import jobs
import routing_config
import routing_domains

HERE = Path(__file__).resolve().parent
TASKS = ("coding", "research", "browser", "computer-use")
SMOKE_TIMEOUT = 8


def check(name: str, status: str, detail: str, *, required: bool = True, **evidence) -> dict:
    return {"name": name, "status": status, "required": required, "detail": detail, **evidence}


def parent_config_path(parent: str) -> Path | None:
    if parent == "grok" and os.environ.get("GROK_HOME"):
        return Path(os.environ["GROK_HOME"]).expanduser() / "config.toml"
    path = child_mcp.config_path(parent)
    if path is not None:
        return path
    if parent == "cursor":
        return Path.home() / ".cursor" / "mcp.json"
    if parent == "claude":
        return Path.home() / ".claude.json"
    return None


def _json_doc(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError, UnicodeError):
        return None
    return data if isinstance(data, dict) else None


def parent_entry(parent: str, repo: Path | None = None) -> dict | None:
    path = parent_config_path(parent)
    if path is None:
        return None
    try:
        data = child_mcp._load_toml(path) if parent in {"grok", "codex"} else _json_doc(path)
    except (OSError, ValueError, UnicodeError):
        data = None
    data = data if isinstance(data, dict) else {}
    if repo is not None and parent in {"cursor", "claude"}:
        if parent == "claude":
            # Claude CLI: local-project entry > shared project > user.
            projects = data.get("projects")
            local = projects.get(str(repo.resolve())) if isinstance(projects, dict) else None
            servers = local.get("mcpServers") if isinstance(local, dict) else None
            if isinstance(servers, dict) and "rig" in servers:
                return servers["rig"]
        project_path = repo / (".cursor/mcp.json" if parent == "cursor" else ".mcp.json")
        if project_path.exists():
            project = _json_doc(project_path)
            if project is None:
                return None
            servers = project.get("mcpServers")
            if isinstance(servers, dict) and "rig" in servers:
                return servers["rig"]
    if parent in {"grok", "codex"}:
        servers = data.get("mcp_servers")
        return servers.get("rig") if isinstance(servers, dict) else None
    return child_mcp._json_rig_entry(data, parent)


def runtime_snapshot(repo: Path | None = None) -> dict:
    """Internal comparison only; never print config contents, env, or digests."""
    repo = jobs.repo_root(repo or os.environ.get("RIG_REPO"))
    sources = {}
    for path in sorted(HERE.glob("*.py")):
        try:
            sources[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            sources[path.name] = "unreadable"
    configs = {}
    for parent in harness.PARENTS:
        entry = parent_entry(parent, repo)
        configs[parent] = hashlib.sha256(json.dumps(entry, sort_keys=True, default=str).encode()).hexdigest()
    return {"repo": str(repo), "sources": sources, "configs": configs}


def model_check(parent: str, model: str = "", *, required: bool = True) -> dict:
    """Only read cache. Never load_catalog_info: it may refresh or call a CLI."""
    entry = catalog.cache_entry(parent)
    if entry is None:
        detail = "No successful cached model catalog; availability was not checked"
        return check("model", "missing-evidence", detail, required=required,
                     selector=model, source="none", freshness="unknown")
    age = entry["age_s"]
    freshness = "fresh" if age <= catalog.TTL_SECONDS else "stale" if age <= catalog.STALE_MAX_SECONDS else "expired"
    evidence = {"selector": model, "source": "cached-catalog", "freshness": freshness,
                "age_seconds": round(age), "model_count": len(entry["ids"])}
    if freshness != "fresh":
        return check("model", "missing-evidence", "Cached model catalog is " + freshness + "; no refresh was attempted",
                     required=required, **evidence)
    if entry["status"] != "ok" or not entry["ids"]:
        return check("model", "missing-evidence", "Cached catalog is empty; it does not confirm model availability",
                     required=required, **evidence)
    if model and model not in entry["ids"]:
        return check("model", "unavailable", "Requested exact model selector is absent from the fresh cached catalog",
                     required=required, **evidence)
    return check("model", "ready", "Exact selector is in the fresh cached catalog; no inference/auth test" if model else
                 "Fresh cached models exist; no model was selected or tested", required=required, **evidence)


def optional_check(repo: Path, task: str, *, required: bool) -> dict:
    if task == "browser":
        import browser_skill as backend
        binary_name = "bsk"
    else:
        import computer_use as backend
        binary_name = "cua-driver"
    machine, project = backend.machine_state(), backend.project_state(repo)
    evidence = {"machine": machine, "project": project}
    # Do not even look for or invoke the optional executable after a decline.
    if machine != "on" or project != "true":
        return check(task, "optional-disabled", f"machine={machine}, project={project}; opt-in unchanged",
                     required=required, **evidence)
    binary = backend.binary_path()
    if not binary:
        return check(task, "missing", binary_name + " executable is missing", required=required, **evidence)
    return check(task, "configured-host-unverified", binary_name + " configured; " +
                 ("extension connection/session not tested" if task == "browser" else "display, permissions and grants not tested"),
                 required=required, **evidence)


def smoke_check() -> dict:
    """Exercise only our Python child server and inbox in a self-owned fixture."""
    with tempfile.TemporaryDirectory(prefix="rig-doctor-") as raw:
        temp = Path(raw)
        repo = temp / "repo"
        job = repo / ".rig" / "jobs" / "doctor-fixture"
        job.mkdir(parents=True)
        (temp / "home").mkdir()
        (repo / ".rig" / "harness.toml").write_text("[project]\nenabled = true\n")
        (job / "meta.json").write_text(json.dumps({
            "job_id": "doctor-fixture", "worker": "fixture", "status": "running",
            "child_mcp_status": "unknown", "child_mcp_protocol": child_mcp.PROTOCOL,
        }))
        # Allowlist, not a filtered copy: no provider credentials, user config,
        # Python startup hooks, inherited job binding, or executable search path.
        env = {"HOME": str(temp / "home"), "PATH": os.defpath,
               "RIG_HOME": str(temp / "home" / ".rig"), "RIG_REPO": str(repo),
               "RIG_JOB_ID": "doctor-fixture", "RIG_JOB_DIR": str(job),
               "RIG_SKIP_MODEL_CATALOG": "1", "RIG_SKIP_UPDATE_CHECK": "1",
               "PYTHONDONTWRITEBYTECODE": "1"}
        requests = [
            {"id": 1, "method": "initialize", "params": {"protocolVersion": "2025-03-26",
             "capabilities": {}, "clientInfo": {"name": "rig-doctor-fixture", "version": "1"}}},
            {"method": "notifications/initialized"},
            {"id": 2, "method": "tools/list"},
            {"id": 3, "method": "tools/call", "params": {"name": "rig_job_inbox", "arguments": {}}},
        ]
        payload = "".join(json.dumps({"jsonrpc": "2.0", **r}) + "\n" for r in requests)
        try:
            result = subprocess.run([sys.executable, "-E", "-s", "-B", "-u", str(HERE / "rig_mcp.py")],
                                    input=payload, capture_output=True, text=True, cwd=repo,
                                    env=env, timeout=SMOKE_TIMEOUT)
            replies = {r.get("id"): r for r in map(json.loads, result.stdout.splitlines())}
            init = replies.get(1, {}).get("result", {})
            tools = replies.get(2, {}).get("result", {}).get("tools", [])
            inbox = replies.get(3, {}).get("result", {})
            valid = (result.returncode == 0 and init.get("serverInfo", {}).get("name") == "rig"
                     and init.get("protocolVersion") == "2025-03-26"
                     and any(t.get("name") == "rig_job_inbox" for t in tools)
                     and not any(t.get("name") == "rig_doctor" for t in tools)
                     and bool(inbox.get("content")) and not inbox.get("isError")
                     and child_mcp.handshake_connected(job))
        except (OSError, ValueError, AttributeError, TypeError, subprocess.TimeoutExpired):
            valid = False
        return check("smoke", "ready" if valid else "failed",
                     "Temporary child MCP initialize, scoped tools/list and persisted inbox handshake " +
                     ("passed" if valid else "failed or timed out") +
                     "; no provider, selected host, browser, or user repository execution",
                     scope="temporary-child-mcp-fixture", timeout_seconds=SMOKE_TIMEOUT)


def build_report(repo: Path, *, task: str = "coding", parent: str = "", model: str = "",
                 research_sources=None, smoke: bool = False, host_snapshot: dict | None = None) -> dict:
    if task not in TASKS:
        raise ValueError("task must be " + "|".join(TASKS))
    if not isinstance(parent, str) or (parent and parent not in harness.PARENTS):
        raise ValueError("parent must be a supported parent CLI")
    if not isinstance(model, str):
        raise ValueError("model must be a string")
    if type(smoke) is not bool:
        raise ValueError("smoke must be a boolean")
    sources = routing_domains.normalize_source_inputs(research_sources)
    if sources and task != "research":
        raise ValueError("research_sources requires task=research")
    repo = jobs.repo_root(repo)
    config = harness.parse_harness(harness.harness_path(repo))
    live = harness.live_parent()
    selected = parent or live or config["parent"]
    parent_source = "explicit" if parent else "detected" if live else "preferred" if selected else "unknown"
    checks = []
    state = config["project"]
    checks.append(check("project", "ready" if state["enabled"] else "missing" if state["state"] == "uninitialized" else "disabled",
                        state.get("error") or state["state"]))
    observed = host_snapshot is not None and bool(live) and live == selected
    binary = harness.find_worker_bin(selected) if selected in harness.PARENTS else ""
    if observed:
        checks.append(check("parent", "ready", f"{selected} host detected and its Rig MCP request observed; no separate worker CLI required"))
    elif selected == "cursor":
        checks.append(check("parent", "configured-host-unverified", "Cursor Desktop host not observed; cursor-agent is only required for wrapper children"))
    else:
        checks.append(check("parent", "ready" if binary else "missing",
                            f"{selected} executable found" if binary else "Select an installed parent CLI; preferred configuration is not a running host"))
    try:
        policy = routing_config.load_config(repo, harness=config)
        if task == "research" and policy.mode == "legacy":
            routing_domains.reject_legacy("research", sources)
        checks.append(check("routing", "ready", policy.mode + " configuration valid"))
    except ValueError as exc:
        checks.append(check("routing", "missing", str(exc)))
    script_ready = all((HERE / name).is_file() for name in ("rig_mcp.py", "run-worker.sh"))
    checks.append(check("scripts", "ready" if script_ready else "missing", "Rig runtime scripts present" if script_ready else "Rig runtime scripts missing"))
    entry = parent_entry(selected, repo)
    configured = child_mcp._stdio_entry_ready(entry)
    if selected == "pi" and configured:
        configured = child_mcp._pi_adapter_ready(parent_config_path(selected))
    stale = False
    same_repo = observed and host_snapshot.get("repo") == str(repo)
    if observed:
        current = runtime_snapshot(repo)
        stale = (host_snapshot.get("sources") != current["sources"] or
                 (same_repo and host_snapshot.get("configs", {}).get(selected) != current["configs"].get(selected)))
    if stale:
        checks.append(check("mcp", "restart-needed", "Rig runtime or selected parent MCP configuration changed after this server loaded; fully restart the parent/MCP session"))
    elif observed and not same_repo:
        checks.append(check("mcp", "configured-host-unverified", "MCP transport observed; this repository was not the startup snapshot, so config freshness is unverified"))
    elif observed:
        checks.append(check("mcp", "ready", "This parent Rig MCP handled the request; transport observed, provider/auth not tested"))
    elif configured:
        checks.append(check("mcp", "configured-host-unverified", "Enabled parent MCP entry and launcher found; call rig_doctor from the selected host after restarting it"))
    else:
        checks.append(check("mcp", "missing", "Selected parent Rig MCP configuration/launcher is missing or invalid; job-scoped child support does not prove parent wiring"))
    checks.append(model_check(selected, model))
    checks.append(check("auth", "missing-evidence", "Provider authentication was not tested; credentials/configuration and cached models cannot prove a live request works"))
    if task == "research":
        try:
            verified = routing_domains.validate_sources(repo, sources)
            checks.append(check("research-sources", "ready" if verified else "missing",
                                f"{len(verified)} readable repository-contained source file(s); acquisition stays with the parent" if verified else
                                "No local sources supplied; delegated research needs --research-source FILE; parent source acquisition remains unverified"))
        except ValueError as exc:
            checks.append(check("research-sources", "missing", str(exc)))
    checks.extend(optional_check(repo, name, required=task == name) for name in ("browser", "computer-use"))
    if smoke:
        checks.append(smoke_check())
    required = [c for c in checks if c["required"]]
    states = {c["status"] for c in required}
    status = next((s for s in ("failed", "missing", "disabled", "unavailable", "restart-needed", "optional-disabled") if s in states),
                  "configured-host-unverified" if states - {"ready"} else "ready")
    # Auth deliberately remains unknown: offline doctor cannot certify execution.
    local = [c for c in required if c["name"] not in {"model", "auth", "mcp", "browser", "computer-use"}]
    workers = []
    for worker in harness.WORKERS:
        if config["workers"].get(worker) != "true":
            continue
        if worker == live:
            workers.append(check(worker, "optional-disabled", "Live parent is not a wrapper child", required=False))
            continue
        ready, reason = child_mcp.worker_mcp_ready(worker, repo=repo)
        workers.append(check(worker, "configured-host-unverified" if ready else "missing", reason or
                             "Child launch prerequisites configured; runtime inbox handshake/model/auth unverified", required=False))
        if worker in catalog.CATALOG_WORKERS:
            workers[-1]["model_evidence"] = model_check(worker, required=False)
    return {"schema_version": 1, "task": task, "status": status, "repo": str(repo),
            "parent": {"selected": selected, "source": parent_source, "detected": live,
                       "preferred": config["parent"]},
            "local_prerequisites": ("ready" if all(c["status"] == "ready" for c in local) else
                                    "blocked" if any(c["status"] in {"missing", "disabled", "failed"} for c in local) else "unverified"),
            "checks": checks, "workers": workers, "smoke_requested": smoke,
            "limits": ["No provider calls, login, installs, grants, catalog refresh, or user repository edits",
                       "Fixture smoke does not verify a provider, selected host, browser extension, desktop permissions, or user task execution"]}


def format_report(report: dict) -> str:
    parent = report["parent"]
    lines = [f"Rig doctor: {report['task']} — {report['status']}",
             f"  parent: {parent['selected'] or '(unknown)'} ({parent['source']}); local prerequisites: {report['local_prerequisites']}"]
    for row in report["checks"]:
        suffix = "" if row["required"] else " (optional for this task)"
        lines.append(f"  {row['name']}: {row['status']}{suffix} — {row['detail']}")
    if report["workers"]:
        lines.append("  opted-in workers: " + ", ".join(f"{w['name']}={w['status']}" for w in report["workers"]))
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="rig doctor", description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--task", "--capability", choices=TASKS, default="coding")
    parser.add_argument("--parent", choices=sorted(harness.PARENTS), default="")
    parser.add_argument("--model", default="", help="Exact model selector to check against cache; never invokes a provider")
    parser.add_argument("--research-source", action="append", dest="research_sources")
    parser.add_argument("--smoke", action="store_true", help="Run an 8-second-bounded temporary child MCP fixture; no provider/host/browser execution")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        report = build_report(args.repo, task=args.task, parent=args.parent, model=args.model,
                              research_sources=args.research_sources, smoke=args.smoke)
    except ValueError as exc:
        parser.error(str(exc))
    print(json.dumps(report, indent=2) if args.json else format_report(report))
    return 1 if report["status"] in {"failed", "missing", "disabled", "unavailable", "restart-needed", "optional-disabled"} else 0


if __name__ == "__main__":
    raise SystemExit(main())
