#!/usr/bin/env python3
"""Workflow facade and CLI: create, list, show, advance, wait, extend, resolve, approve, cancel, report."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import admission  # noqa: E402
import jobs as rig_jobs  # noqa: E402
import workflow_scheduler as sched  # noqa: E402
import workflow_state as wf  # noqa: E402

WorkflowError = wf.WorkflowError
WAIT_CODES = frozenset({0, 1, 2, 124, 130})


def public_payload(obj):
    """JSON-safe copy with owner tokens removed. credentials_path is retained."""
    def strip(value):
        if isinstance(value, dict):
            return {key: strip(item) for key, item in value.items() if key != "owner_token"}
        if isinstance(value, list):
            return [strip(item) for item in value]
        return value

    return strip(json.loads(json.dumps(obj, default=str)))


def _load_json_source(path="", text=""):
    if text:
        raw = text
    elif path in {"", "-", None}:
        if sys.stdin.isatty():
            raise WorkflowError("workflow create/extend needs --file PATH or JSON on stdin")
        raw = sys.stdin.read()
    else:
        try:
            raw = Path(path).read_text()
        except OSError as error:
            raise WorkflowError(f"cannot read workflow JSON: {error}") from error
    try:
        return json.loads(raw)
    except ValueError as error:
        raise WorkflowError("workflow spec must be JSON") from error


def _load_spec_source(path="", text=""):
    spec = _load_json_source(path, text)
    if not isinstance(spec, dict):
        raise WorkflowError("workflow spec must be an object")
    return spec


def create(repo, spec, *, owner=None, owner_session="", queue_id=""):
    return wf.create_workflow(repo, spec, owner=owner, owner_session=owner_session, queue_id=queue_id)


def listing(repo, *, include_terminal=True):
    return wf.list_workflows(repo, include_terminal=include_terminal)


def show(repo, workflow_id):
    wf.load_pair(repo, workflow_id, required=True)
    public = wf.refresh(repo, workflow_id)
    public["events"] = wf.list_events(repo, workflow_id)
    return public_payload(public)


def advance(repo, workflow_id, **options):
    return public_payload(sched.advance(repo, workflow_id, **options))


def wait(repo, workflow_id, timeout=None, **options):
    return sched.wait_workflow(repo, workflow_id, timeout, **options)


def extend(repo, workflow_id, nodes, **options):
    return public_payload(wf.extend_workflow(repo, workflow_id, nodes, **options))


def resolve(repo, workflow_id, node_id, **options):
    return public_payload(sched.resolve_node(repo, workflow_id, node_id, **options))


def approve(repo, workflow_id, node_id, **options):
    return public_payload(sched.approve_node(repo, workflow_id, node_id, **options))


def cancel(repo, workflow_id, **options):
    return public_payload(sched.cancel_workflow(repo, workflow_id, **options))


def report(repo, workflow_id):
    return public_payload(sched.report(repo, workflow_id))


def format_list(rows):
    if not rows:
        return "no workflows"
    lines = ["STATUS               ACC/REQ  RUN ASK  WORKFLOW                         BLOCKER"]
    for row in rows:
        action = row.get("next_parent_action") or {}
        blocker = row.get("blocker") or (action.get("kind") if action else "")
        lines.append(
            f"{str(row.get('status') or '-'):<18} {row.get('accepted', 0)}/{row.get('required', 0):<5} "
            f"{row.get('running', 0):<3} {row.get('ask', 0):<3} {str(row.get('workflow_id') or ''):<32} {blocker}"
        )
    return "\n".join(lines)


def _print_public(obj, as_json=False):
    payload = public_payload(obj)
    if as_json:
        print(json.dumps(payload, indent=2, default=str))
        return
    if isinstance(payload, dict) and payload.get("credentials_path"):
        print(f"workflow {payload.get('workflow_id')} credentials {payload.get('credentials_path')}")
        return
    if isinstance(payload, list):
        print(format_list(payload))
        return
    print(json.dumps(payload, indent=2, default=str))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="rig workflow",
        description="Create and operate repository-local adaptive workflows.",
        epilog="create reads JSON from --file PATH or stdin. Never prints owner tokens; retain credentials_path.",
    )
    parser.add_argument(
        "cmd",
        nargs="?",
        default="list",
        choices=["list", "create", "show", "advance", "wait", "extend", "resolve",
                 "approve", "cancel", "report"],
    )
    parser.add_argument("target", nargs="?")
    parser.add_argument("--repo")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--file", dest="spec_file", default="")
    parser.add_argument("--queue-id", default="")
    parser.add_argument("--node", default="")
    parser.add_argument("--nodes-json", default="")
    parser.add_argument("--action", choices=["retry", "skip", "fail"], default="retry")
    parser.add_argument("--decision", choices=["reply", "stop"], default="")
    parser.add_argument("--rationale", default="")
    parser.add_argument("--reason", default="")
    parser.add_argument("--text", default="")
    parser.add_argument("--timeout", type=float, default=None)
    parser.add_argument("--owner-session", default=os.environ.get("RIG_OWNER_SESSION", ""))
    parser.add_argument("--owner-token", default=os.environ.get("RIG_OWNER_TOKEN", ""))
    parser.add_argument("--include-terminal", action=argparse.BooleanOptionalAction, default=True)
    args = parser.parse_args(argv)
    repo = rig_jobs.repo_root(args.repo)
    owner_session = args.owner_session
    token = args.owner_token
    try:
        if args.cmd == "list":
            rows = listing(repo, include_terminal=bool(args.include_terminal))
            if args.json:
                _print_public(rows, as_json=True)
            else:
                print(format_list(rows))
            return 0
        if args.cmd == "create":
            spec = _load_spec_source(args.spec_file or args.target or "")
            result = create(
                repo, spec, owner_session=owner_session,
                queue_id=args.queue_id or spec.get("queue_id") or "",
            )
            _print_public(result, as_json=args.json)
            return 0
        wid = args.target
        if not wid:
            raise WorkflowError(f"rig workflow {args.cmd} needs a workflow id")
        if args.cmd == "show":
            _print_public(show(repo, wid), as_json=args.json)
            return 0
        if args.cmd == "advance":
            _print_public(
                advance(repo, wid, owner_session=owner_session, owner_token=token),
                as_json=args.json,
            )
            return 0
        if args.cmd == "wait":
            code, text = wait(repo, wid, args.timeout)
            print(text)
            return code if code in WAIT_CODES else 1
        if args.cmd == "extend":
            if args.nodes_json:
                try:
                    raw = json.loads(args.nodes_json)
                except ValueError as error:
                    raise WorkflowError("workflow spec must be JSON") from error
            else:
                raw = _load_json_source(args.spec_file or "")
            nodes = raw if isinstance(raw, list) else (raw or {}).get("nodes")
            if not isinstance(nodes, list) or not nodes:
                raise WorkflowError("extension needs additional nodes")
            _print_public(
                extend(repo, wid, nodes, owner_token=token, owner_session=owner_session),
                as_json=args.json,
            )
            return 0
        if args.cmd == "resolve":
            node_id = args.node or ""
            if not node_id:
                raise WorkflowError("resolve needs --node")
            _print_public(
                resolve(
                    repo, wid, node_id, action=args.action,
                    rationale=args.rationale or args.reason,
                    owner_token=token, owner_session=owner_session,
                ),
                as_json=args.json,
            )
            return 0
        if args.cmd == "approve":
            node_id = args.node or ""
            if not node_id:
                raise WorkflowError("approve needs --node")
            _print_public(
                approve(
                    repo, wid, node_id, owner_token=token, owner_session=owner_session,
                    rationale=args.rationale or args.reason or args.text,
                ),
                as_json=args.json,
            )
            return 0
        if args.cmd == "cancel":
            _print_public(
                cancel(
                    repo, wid, owner_token=token, owner_session=owner_session,
                    rationale=args.rationale or args.reason or "parent",
                ),
                as_json=args.json,
            )
            return 0
        if args.cmd == "report":
            _print_public(report(repo, wid), as_json=True)
            return 0
    except (WorkflowError, admission.AdmissionError, OSError, ValueError) as error:
        print(f"rig workflow: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
