"""Read-only doctor, routing, billing, benchmark, and timeline MCP handlers.

Facade names are resolved through the call context. Do not import rig_mcp.
"""
from __future__ import annotations

import json
import recovery_guide

def dispatch(ctx, state):
    """Handle this module's tools. Bindings are read from the facade on each call."""
    name = state.name
    args = state.args
    repo = state.repo
    on_tick = state.on_tick
    wait_paths = state.wait_paths
    cancel_event = state.cancel_event
    wait_targets = state.wait_targets
    _ok = ctx._ok
    _err = ctx._err
    if name == "rig_recovery_guide":
        if set(args) - {"repo", "job_id", "workflow_id"}:
            return _err("unsupported recovery guide arguments")
        result = recovery_guide.build(repo, job_id=args.get("job_id"), workflow_id=args.get("workflow_id"))
        return {**_ok(json.dumps(result, indent=2)), "structuredContent": result}
    _optional_string = ctx._optional_string
    rig_doctor = ctx.rig_doctor
    _DOCTOR_HOST_SNAPSHOT = ctx._DOCTOR_HOST_SNAPSHOT
    if name == "rig_doctor":
        report = rig_doctor.build_report(repo, task=_optional_string(args, "task") or "coding",
                                        parent=_optional_string(args, "parent"),
                                        model=_optional_string(args, "model"),
                                        research_sources=args.get("research_sources"),
                                        smoke=args.get("smoke", False),
                                        host_snapshot=_DOCTOR_HOST_SNAPSHOT)
        return {**_ok(rig_doctor.format_report(report)), "structuredContent": report}

    if name == "rig_routing_report":
        import routing_report

        days = args.get("days", 30)
        if days is None:
            days = 30
        if type(days) is not int or days < 1:
            return _err("days must be a positive integer")
        report = routing_report.build_report(repo, days=days)
        return {**_ok(routing_report.format_report(report)), "structuredContent": report}
    if name == "rig_billing_report":
        import billing_ledger

        report = billing_ledger.build_report(repo, scope=_optional_string(args, "scope"))
        report = billing_ledger.redact_secrets(report)
        return {**_ok(billing_ledger.format_report(report)), "structuredContent": report}
    if name == "rig_billing_import":
        import billing_ledger

        receipt = args.get("receipt")
        if not isinstance(receipt, dict):
            return _err("receipt must be an object")
        if args.get("dry_run") not in (None, True, False):
            return _err("dry_run must be a boolean")
        result = billing_ledger.import_receipt(
            repo, receipt, scope=_optional_string(args, "scope"),
            dry_run=args.get("dry_run") is True,
        )
        result = billing_ledger.redact_secrets(result)
        return {**_ok(billing_ledger.public_json(result)), "structuredContent": result}
    if name == "rig_billing_sync":
        import billing_ledger

        provider = _optional_string(args, "provider")
        receipts = args.get("receipts")
        if receipts is not None and not isinstance(receipts, list):
            return _err("receipts must be an array of objects")
        if args.get("dry_run") not in (None, True, False) or args.get("network") not in (None, True, False):
            return _err("dry_run and network must be booleans")
        result = billing_ledger.sync_receipts(
            repo, provider, receipts=receipts, scope=_optional_string(args, "scope"),
            dry_run=args.get("dry_run") is True, network=args.get("network") is True,
        )
        result = billing_ledger.redact_secrets(result)
        return {**_ok(billing_ledger.public_json(result)), "structuredContent": result}
    if name == "rig_benchmark_report":
        import benchmark_reporting as bench

        bid = _optional_string(args, "id").strip()
        if not bid:
            return _err("rig_benchmark_report needs id")
        report = bench.build_report(repo, bid, scope=_optional_string(args, "scope"))
        return {**_ok(bench.format_report(report)), "structuredContent": report}
    if name == "rig_benchmark_create":
        import benchmark_reporting as bench

        spec = args.get("spec")
        if not isinstance(spec, dict):
            return _err("spec must be an object")
        result = bench.create_spec(repo, spec)
        return {**_ok(json.dumps(result, indent=2)), "structuredContent": result}
    if name == "rig_benchmark_outcome":
        import benchmark_reporting as bench

        result = bench.record_outcome(
            repo, _optional_string(args, "id"),
            job_id=_optional_string(args, "job_id"),
            task=_optional_string(args, "task"),
            arm=_optional_string(args, "arm"),
        )
        return {**_ok(json.dumps(result, indent=2)), "structuredContent": result}

    if name == "rig_task_timeline":
        import task_timeline
        result = task_timeline.build(repo, workflow_id=_optional_string(args, "workflow_id"),
            job_id=_optional_string(args, "job_id"), attempt_id=_optional_string(args, "attempt_id"),
            limit=args.get("limit", 100))
        return {**_ok(task_timeline.format_timeline(result)), "structuredContent": result}
    return _err(f"unknown tool {name}")

HANDLERS = {
    "rig_recovery_guide": dispatch,
    "rig_doctor": dispatch,
    "rig_routing_report": dispatch,
    "rig_billing_report": dispatch,
    "rig_billing_import": dispatch,
    "rig_billing_sync": dispatch,
    "rig_benchmark_report": dispatch,
    "rig_benchmark_create": dispatch,
    "rig_benchmark_outcome": dispatch,
    "rig_task_timeline": dispatch,
}
