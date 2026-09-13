#!/usr/bin/env python3
"""stdio MCP server: pick/status/jobs/wait/allow/memory for the parent agent.

When RIG_JOB_ID (or RIG_JOB_DIR) is set, this is the child surface: doing/note/ask
only. Parent tools stay hidden.
"""
from __future__ import annotations

import json
import os
import sys
import threading
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import ask as rig_ask  # noqa: E402
import harness as rig_harness  # noqa: E402
import inbox as rig_inbox  # noqa: E402
import jobs as rig_jobs  # noqa: E402
import work_queue as rig_queue  # noqa: E402
import memory as rig_memory  # noqa: E402
import route as rig_route  # noqa: E402
import verification as rig_verification  # noqa: E402

PICK_ROLES = ("explore", "mini", "bulk", "implement", "hard", "review", "stay")
JOB_WORKERS = ("grok", "codex", "claude", "cursor", "opencode", "omp", "pi", "agy", "parent")
JOB_FINISH_STATUSES = ("ok", "fail", "timeout")
REVIEW_PROPERTIES = {
    "writer_job_id": {"type": "string"},
    "writer_cli": {"type": "string"},
    "writer_model": {"type": "string"},
    "writer_provider": {"type": "string"},
    "review_mode": {"type": "string", "enum": ["standalone", "independent"], "default": "standalone"},
}
PARENT_METADATA_PROPERTIES = {
    "parent_model": {
        "type": "string",
        "description": "Actual current parent-session model, only when explicitly known.",
    },
    "parent_effort": {
        "type": "string",
        "description": "Actual current parent-session reasoning effort, only when known.",
    },
}
JOB_EXECUTION_PROPERTIES = {
    "model": {"type": "string", "description": "Executing model. For parent work, pass only the actual known model."},
    "effort": {"type": "string", "description": "Executing model reasoning effort, when known."},
    "executor_kind": {
        "type": "string",
        "enum": ["parent", "native_child"],
        "description": "Default parent for role/worker parent; otherwise native_child.",
    },
}

TOOLS = [
    {
        "name": "rig_jobs",
        "description": (
            "List Rig worker jobs in this project: which agent is running, "
            "the task, status, and what it is doing now. Status ask or running: "
            "you MUST call rig_job_allow or rig_job_deny for ask. "
            "Do not kill that job. Never spawn another worker because a child asked. "
            "After implement+verify ok, you MAY start seed (disjoint listed files) "
            "in parallel with a read-only review; wait those ids together."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "repo": {"type": "string", "description": "Project root. Default cwd."},
                "status": {
                    "type": "string",
                    "description": "Filter: running, ok, fail, timeout, stale",
                },
                "thread": {
                    "type": "string",
                    "description": "Filter by parent thread id. Omit to list every job in this repo (new threads still see running work).",
                },
            },
        },
    },
    {
        "name": "rig_job_show",
        "description": "Show one Rig job: agent, task, status, session, and recent log.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "Job id. Default: running, else latest."},
                "repo": {"type": "string"},
            },
        },
    },
    {
        "name": "rig_job_log",
        "description": "Decoded child log so you can see what the worker is doing.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {"type": "string"},
                "repo": {"type": "string"},
                "lines": {"type": "integer", "default": 40},
            },
        },
    },
    {
        "name": "rig_job_wait",
        "description": (
            "Block until the job (or jobs) asks for permission or finishes. "
            "Do not pass timeout unless you must cap the wait. Do not poll. "
            "If the text starts with ASK, call rig_job_allow or rig_job_deny next "
            "so that child can continue; then wait the same ids again. "
            "Do not kill an ask or running job because the child asked. Never spawn a replacement. "
            "If this wait is cancelled (user Esc / notifications/cancelled), those ids abort; "
            "do not re-pick. After implement+verify ok, pass ids for read-only review "
            "and disjoint seed together (barrier; wakes on first ASK). "
            "Spawn-infra fail may re-pick with exclude once."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {
                    "type": "string",
                    "description": "Job id. Default: asking, else running. Comma-separated is wait-all.",
                },
                "ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Wait these jobs together. Wakes on first ASK. "
                        "Exit 0 only if every id is ok."
                    ),
                },
                "repo": {"type": "string"},
                "timeout": {
                    "type": "number",
                    "description": (
                        "Optional cap in seconds. Omit to block until ASK or result. "
                        "0 snapshots once. 124 only if still running when the cap hits."
                    ),
                },
            },
        },
    },
    {
        "name": "rig_job_allow",
        "description": (
            "Allow a pending permission prompt (Claude ask). "
            "Call this when rig_jobs or rig_job_wait shows status ask and the command is safe worker work "
            "(read, edit, test, ssh gather, git status/diff/add/commit). "
            "This is how the child continues. Do not close the job instead."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "Job id. Default: the asking job."},
                "repo": {"type": "string"},
            },
        },
    },
    {
        "name": "rig_job_deny",
        "description": (
            "Deny a pending permission prompt (Claude ask). "
            "Use for destructive, prod, or secrets commands. Optional reason is shown to the child."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {"type": "string"},
                "repo": {"type": "string"},
                "reason": {"type": "string"},
            },
        },
    },
    {
        "name": "rig_job_cancel",
        "description": (
            "Abort a live job (running or ask) and its worker process. "
            "Use when the user cancelled the parent turn/wait (Esc/Stop). "
            "Writes cancel.json, SIGTERM, status cancelled. Do not re-pick. "
            "Does not cancel parked queue items. Does not abort jobs in other threads."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {
                    "type": "string",
                    "description": "Job id. Default: asking, else running.",
                },
                "ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Cancel these jobs (this wait's ids).",
                },
                "repo": {"type": "string"},
                "reason": {"type": "string", "description": "Stored on cancel.json."},
            },
        },
    },
    {
        "name": "rig_memory",
        "description": (
            "Show standing project facts in .rig/MEMORY.md. "
            "Call this at the start of a new thread."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "repo": {"type": "string"},
            },
        },
    },
    {
        "name": "rig_memory_add",
        "description": (
            "Save one standing project fact to .rig/MEMORY.md. "
            "One short bullet. No transcripts. Duplicates and the 120-line cap are handled."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "fact": {
                    "type": "string",
                    "description": "One durable fact, not a transcript or job log.",
                },
                "repo": {"type": "string"},
            },
            "required": ["fact"],
        },
    },
    {
        "name": "rig_pick",
        "description": (
            "Pick worker, spawn kind, model, and effort for a task. "
            "Same JSON as rig pick --json. Live parent is this MCP process "
            "(PPID walk / RIG_PARENT), so a Grok parent does not pick a Grok "
            "run-worker child. Does not launch a worker."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "case": {"type": "string", "description": "The task text."},
                **PARENT_METADATA_PROPERTIES,
                "role": {
                    "type": "string",
                    "enum": list(PICK_ROLES),
                    "description": (
                        "Optional pick kind. Omit to classify from case. "
                        "Pass stay|explore|mini|bulk|implement|hard|review "
                        "when the parent already knows the kind."
                    ),
                },
                "repo": {"type": "string", "description": "Project root. Default cwd."},
                "exclude": {
                    "type": "string",
                    "description": (
                        "Comma worker names to skip after a dead spawn "
                        "(example: grok). Skips native if live is excluded."
                    ),
                },
            },
            "required": ["case"],
        },
    },
    {
        "name": "rig_status",
        "description": (
            "Show live parent (this process / RIG_PARENT, not the toml parent key), "
            "preferred parent, effective workers, and job count."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "repo": {"type": "string", "description": "Project root. Default cwd."},
            },
        },
    },
    {
        "name": "rig_job_start",
        "description": (
            "Record a running job in .rig/jobs (meta.json + STATE). "
            "Files only. Does not launch a worker. Returns the job id."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                **JOB_EXECUTION_PROPERTIES,
                "worker": {
                    "type": "string",
                    "enum": list(JOB_WORKERS),
                    "description": "Worker name. Default live parent, else preferred.",
                },
                "role": {"type": "string", "description": "Default worker."},
                "id": {"type": "string", "description": "Job id. Allocated if omitted."},
                "summary": {"type": "string"},
                "repo": {"type": "string"},
            },
        },
    },
    {
        "name": "rig_job_finish",
        "description": (
            "Finish a recorded job (ok|fail|timeout). Writes result.json. "
            "Does not kill a process."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "Job id."},
                "status": {
                    "type": "string",
                    "enum": list(JOB_FINISH_STATUSES),
                    "description": "Default ok.",
                },
                "summary": {"type": "string"},
                "repo": {"type": "string"},
            },
            "required": ["id"],
        },
    },
    {
        "name": "rig_job_record",
        "description": (
            "Record a retrospective terminal read-only result. Scoped writes must use rig_job_start before editing. "
            "A retrospective result cannot be verified."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                **JOB_EXECUTION_PROPERTIES,
                "worker": {
                    "type": "string",
                    "enum": list(JOB_WORKERS),
                },
                "role": {"type": "string"},
                "status": {
                    "type": "string",
                    "enum": list(JOB_FINISH_STATUSES),
                    "description": "Default ok.",
                },
                "summary": {"type": "string"},
                "id": {"type": "string"},
                "repo": {"type": "string"},
            },
        },
    },
    {
        "name": "rig_session",
        "description": (
            "One call: memory, jobs, status, and pick JSON. "
            "Same as rig memory + rig jobs + rig status + rig pick. "
            "Call this first when present. Does not launch a worker."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                **PARENT_METADATA_PROPERTIES,
                "compact": {
                    "type": "boolean",
                    "default": False,
                    "description": "Return compact schema v2 JSON, retaining every active/ASK job.",
                },
                "terminal_limit": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 100,
                    "default": 10,
                    "description": "Newest terminal rows in compact mode. Full history remains in rig_jobs.",
                },
                "case": {
                    "type": "string",
                    "description": "Task text for pick. Required to include pick JSON.",
                },
                "role": {
                    "type": "string",
                    "enum": list(PICK_ROLES),
                    "description": "Optional pick kind. Omit to classify from case.",
                },
                "exclude": {
                    "type": "string",
                    "description": "Comma worker names to skip. Same as rig_pick exclude.",
                },
                "repo": {"type": "string", "description": "Project root. Default cwd."},
            },
            "required": ["case"],
        },
    },
    {
        "name": "rig_job_doing",
        "description": (
            "Child only. Set what this job is doing now. Writes job files. "
            "Do not pick, wait, or spawn."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Short doing line."},
            },
            "required": ["text"],
        },
    },
    {
        "name": "rig_job_note",
        "description": "Child only. Append a short activity note. Does not change ask state.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string"},
            },
            "required": ["text"],
        },
    },
    {
        "name": "rig_job_ask",
        "description": (
            "Child only. Ask the parent a question and block until allow/deny. "
            "Same file protocol as Claude permission prompts."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "preview": {"type": "string"},
                "text": {"type": "string"},
                "tool_name": {"type": "string"},
                "input": {"type": "object"},
                "tool_input": {"type": "object"},
                "tool_use_id": {"type": "string"},
            },
        },
    },
    {
        "name": "permission_prompt",
        "description": "Answer a Claude Code permission prompt for this Rig job.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "tool_name": {"type": "string"},
                "input": {"type": "object"},
                "tool_input": {"type": "object"},
                "tool_use_id": {"type": "string"},
                "description": {"type": "string"},
            },
        },
    },
    {
        "name": "rig_job_inbox",
        "description": (
            "Child only. Pull the parent inbox message once and ack it. "
            "Empty if the parent has not sent mail. Not ASK."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "rig_job_message",
        "description": (
            "Parent only. Leave one message for a running child. "
            "The child pulls it with rig_job_inbox. Does not change ASK/wait."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "Job id. Default: running."},
                "text": {"type": "string"},
                "repo": {"type": "string"},
            },
            "required": ["text"],
        },
    },
    {
        "name": "rig_queue_add",
        "description": (
            "Parent only. Park a work item in .rig/queue/. Does not spawn. "
            "Does not wait. Use while a child is running so the next free turn can drain it."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Work to park."},
                "repo": {"type": "string"},
            },
            "required": ["text"],
        },
    },
    {
        "name": "rig_queue_list",
        "description": (
            "Parent only. List pending queue items and live/max slots. "
            "Does not spawn."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"repo": {"type": "string"}},
        },
    },
    {
        "name": "rig_queue_cancel",
        "description": "Parent only. Cancel a pending queue item. Does not spawn.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "Queue item id."},
                "repo": {"type": "string"},
            },
            "required": ["id"],
        },
    },
    {
        "name": "rig_queue_claim",
        "description": (
            "Parent only. Claim a pending queue item by id if live < max_running "
            "and listed files are disjoint. Id required when more than one item is pending. "
            "Does not spawn. Brief after claim; unclaim if the brief fails."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {
                    "type": "string",
                    "description": "Queue item id. Required when more than one item is pending. Default: the only pending item.",
                },
                "files": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Listed files for this item. Required if other writers are live.",
                },
                "repo": {"type": "string"},
            },
        },
    },
    {
        "name": "rig_queue_unclaim",
        "description": "Parent only. Return a claimed item to pending (brief failed). Does not spawn.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {"type": "string"},
                "repo": {"type": "string"},
            },
            "required": ["id"],
        },
    },
    {
        "name": "rig_queue_spawned",
        "description": "Parent only. After run-worker.sh, mark a claimed item spawned with its job id.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "Queue item id."},
                "job_id": {"type": "string", "description": "Running job id."},
                "files": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "repo": {"type": "string"},
            },
            "required": ["id", "job_id"],
        },
    },
]

for _tool in TOOLS:
    if _tool["name"] in {"rig_pick", "rig_session"}:
        _tool["inputSchema"]["properties"].update(REVIEW_PROPERTIES)
    if _tool["name"] == "rig_job_start":
        _tool["inputSchema"]["properties"].update({
            "files": {"type": "array", "items": {"type": "string"}},
            "writer_job_id": {"type": "string"},
            "writer_snapshot_id": {"type": "string"},
        })

_JOB_REF_PROPERTIES = {"id": {"type": "string"}, "repo": {"type": "string"}}
_OWNERSHIP_PROPERTIES = {key: {"type": "string"} for key in
                         ("reservation_id", "attempt_id", "owner_token", "owner_session")}
TOOLS.extend([
    {
        "name": "rig_job_requirements",
        "description": "Parent declares the complete acceptance manifest before checks. Existing requirements are immutable once checks start.",
        "inputSchema": {"type": "object", "properties": {
            **_JOB_REF_PROPERTIES,
            "requirements": {"type": "array", "items": {"type": "object", "properties": {
                "id": {"type": "string"}, "argv": {"type": "array", "items": {"type": "string"}}, "cwd": {"type": "string"},
            }, "required": ["id", "argv"]}},
            "manual_criteria": {"type": "array", "items": {"type": "string"}},
        }, "required": ["id"]},
    },
    {
        "name": "rig_job_check",
        "description": "Run the exact parent-authorized argv for a declared required check, recording actual output and before/after content snapshots. Does not accept the work.",
        "inputSchema": {"type": "object", "properties": {
            **_JOB_REF_PROPERTIES, "name": {"type": "string"},
            "argv": {"type": "array", "items": {"type": "string"}, "minItems": 1}, "cwd": {"type": "string"},
        }, "required": ["id", "name", "argv"]},
    },
    {
        "name": "rig_job_accept",
        "description": "Parent accepts or rejects current scoped content against EVERY manifest requirement. Check IDs cannot omit failed or missing requirements. next=review retains files for independent review.",
        "inputSchema": {"type": "object", "properties": {
            **_JOB_REF_PROPERTIES, "decision": {"type": "string", "enum": ["accept", "reject"]},
            "snapshot_id": {"type": "string"}, "check_ids": {"type": "array", "items": {"type": "string"}},
            "rationale": {"type": "string"}, "next": {"type": "string", "enum": ["complete", "review"], "default": "complete"},
        }, "required": ["id", "decision", "snapshot_id", "rationale"]},
    },
])

TOOLS.extend([
    {"name": "rig_job_close", "description": "Parent deliberately releases a confirmed-stopped attempt without accepting or retrying it. Requires exact ownership credentials and rationale.",
     "inputSchema": {"type": "object", "properties": {
         **_JOB_REF_PROPERTIES, **_OWNERSHIP_PROPERTIES, "rationale": {"type": "string"},
     }, "required": ["id", "reservation_id", "attempt_id", "owner_token", "rationale"]}},
    {"name": "rig_job_reconcile", "description": "Report held ownership by default. Apply only provably dead unlaunched recovery; explicit adopt/release reconciles legacy queue claims with parent attestation. Never expires live or unknown execution.",
     "inputSchema": {"type": "object", "properties": {
         **_JOB_REF_PROPERTIES, "queue_id": {"type": "string"}, "apply": {"type": "boolean", "default": False},
         "action": {"type": "string", "enum": ["report", "adopt", "release"], "default": "report"},
         "owner_session": {"type": "string"}, "worker": {"type": "string"},
         "access": {"type": "string", "enum": ["read", "write"]},
         "files": {"type": "array", "items": {"type": "string"}},
         "rationale": {"type": "string"}, "completion": {"type": "object"},
     }}},
])
for _tool in TOOLS:
    _properties = _tool["inputSchema"]["properties"]
    if _tool["name"] in {"rig_job_start", "rig_job_finish", "rig_job_requirements", "rig_job_check", "rig_job_accept", "rig_job_reconcile", "rig_queue_unclaim", "rig_queue_spawned"}:
        _properties.update(_OWNERSHIP_PROPERTIES)
    if _tool["name"] == "rig_job_start":
        _properties.update({"access": {"type": "string", "enum": ["read", "write"]},
                            "queue_id": {"type": "string"}, "native_agent_id": {"type": "string"}})
    if _tool["name"] == "rig_job_finish":
        _properties["completion"] = {"type": "object", "description": "Owning parent task-completion or the specific native agent's observed terminal outcome."}
    if _tool["name"] in {"rig_queue_claim", "rig_queue_spawned"}:
        _properties.update({"worker": {"type": "string"}, "access": {"type": "string", "enum": ["read", "write"]},
                            "owner_session": {"type": "string"}})
    if _tool["name"] == "rig_queue_claim":
        _properties["job_id"] = {"type": "string"}

TOOL_ORDER = (
    "rig_session",
    "rig_job_wait",
    "rig_job_allow",
    "rig_job_deny",
    "rig_job_cancel",
    "rig_jobs",
    "rig_pick",
    "rig_status",
    "rig_job_start",
    "rig_job_show",
    "rig_job_log",
    "rig_job_finish",
    "rig_job_record",
    "rig_job_requirements",
    "rig_job_check",
    "rig_job_close",
    "rig_job_reconcile",
    "rig_job_accept",
    "rig_memory",
    "rig_memory_add",
    "rig_job_message",
    "rig_queue_add",
    "rig_queue_list",
    "rig_queue_cancel",
    "rig_queue_claim",
    "rig_queue_unclaim",
    "rig_queue_spawned",
)
CHILD_TOOL_ORDER = (
    "rig_job_doing",
    "rig_job_note",
    "rig_job_ask",
    "permission_prompt",
    "rig_job_inbox",
    "rig_job_show",
    "rig_memory",
)
PARENT_TOOL_NAMES = frozenset(TOOL_ORDER)
CHILD_TOOL_NAMES = frozenset(CHILD_TOOL_ORDER)
_TOOLS_BY_NAME = {t["name"]: t for t in TOOLS}
TOOLS = [_TOOLS_BY_NAME[n] for n in TOOL_ORDER if n in _TOOLS_BY_NAME] + [
    t for t in TOOLS if t["name"] not in TOOL_ORDER and t["name"] in PARENT_TOOL_NAMES
]
CHILD_TOOLS = [_TOOLS_BY_NAME[n] for n in CHILD_TOOL_ORDER if n in _TOOLS_BY_NAME]


def child_job_id() -> str:
    jid = (os.environ.get("RIG_JOB_ID") or "").strip()
    if jid:
        return jid
    raw = (os.environ.get("RIG_JOB_DIR") or "").strip()
    return Path(raw).name if raw else ""


def is_child() -> bool:
    return bool(child_job_id() or (os.environ.get("RIG_JOB_DIR") or "").strip())


def listed_tools() -> list[dict]:
    return list(CHILD_TOOLS) if is_child() else list(TOOLS)


def child_job_dir(repo: Path) -> Path:
    raw = (os.environ.get("RIG_JOB_DIR") or "").strip()
    if raw:
        return Path(raw)
    jid = child_job_id()
    if not jid:
        raise SystemExit("child MCP needs RIG_JOB_ID")
    return rig_jobs.jobs_dir(repo) / jid


def _ok(text: str) -> dict:
    return {"content": [{"type": "text", "text": text}]}


def _err(text: str) -> dict:
    return {"content": [{"type": "text", "text": text}], "isError": True}


def _repo(args: dict) -> Path:
    return rig_jobs.repo_root(args.get("repo") if isinstance(args, dict) else None)


def _optional_string(args: dict, name: str) -> str:
    value = args.get(name, "")
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    return value


def _ownership_args(args: dict) -> dict:
    return {key: _optional_string(args, key) for key in _OWNERSHIP_PROPERTIES}


def _execution_args(args: dict) -> dict:
    values = {name: _optional_string(args, name) for name in JOB_EXECUTION_PROPERTIES}
    if values["executor_kind"] not in {"", "parent", "native_child"}:
        raise ValueError("executor_kind must be parent|native_child")
    return values


def _review_args(args: dict) -> dict:
    values = {name: _optional_string(args, name) for name in REVIEW_PROPERTIES if name != "review_mode"}
    values["review_mode"] = args.get("review_mode", "standalone")
    if values["review_mode"] not in {"standalone", "independent"}:
        raise ValueError("review_mode must be standalone|independent")
    return values


def _compact_rows(listing: list[dict], terminal_limit: int, *, repo: Path, cache=None) -> list[dict]:
    active_states = {"running", "ask", "reserved"}
    def is_active(job):
        return job.get("effective") in active_states or bool(job.get("reservation") and job["reservation"].get("stage") != "released")
    active = [job for job in listing if is_active(job)]
    terminal = [job for job in listing if not is_active(job)]
    terminal.sort(key=lambda job: job.get("mtime", 0), reverse=True)
    keys = (
        "job_id", "worker", "role", "task", "status", "effective", "doing",
        "model", "effort", "model_source", "model_inferred", "executor_kind",
        "files", "reservation_id", "attempt_id", "execution_mode",
        "writer_job_id", "writer_provider", "ownership_established",
    )
    rows = []
    for job in active + terminal[:terminal_limit]:
        row = {key: job.get(key, False if key == "model_inferred" else "") for key in keys}
        projection = rig_jobs.project_job(job, repo, refresh=True, cache=cache)
        for key in ("verification_summary", "display_state", "display_reason", "display_action", "display_model", "independence", "review_completed"):
            row[key] = projection[key]
        if job.get("reservation"):
            row["reservation"] = {key: job["reservation"].get(key) for key in
                                  ("stage", "slot_held", "access", "stopped", "needs_reconciliation", "reconciliation_reason")}
        rows.append(row)
    return rows


def format_session(
    repo: Path,
    case: str,
    role: str = "",
    exclude: str = "",
    *,
    as_json: bool = False,
    compact: bool = False,
    terminal_limit: int = 10,
    parent_model: str = "",
    parent_effort: str = "",
    writer_job_id: str = "",
    writer_cli: str = "",
    writer_model: str = "",
    writer_provider: str = "",
    review_mode: str = "standalone",
) -> str:
    if type(compact) is not bool:
        raise ValueError("rig_session: compact must be a boolean")
    if type(terminal_limit) is not int or not 0 <= terminal_limit <= 100:
        raise ValueError("rig_session: terminal_limit must be an integer from 0 to 100")
    live = rig_harness.live_parent()
    effective = rig_harness.effective_workers(repo, live)
    mem = rig_memory.show_memory(repo)
    listing = rig_jobs.list_jobs(repo)
    hash_cache = {}
    status = "" if compact and as_json else rig_harness.format_status(
        repo, live=live, jobs_snapshot=listing, include_jobs=not compact, effective=effective,
    )
    role_n = (role or "").strip()
    pick_err = ""
    choice: dict = {}
    if role_n and role_n not in PICK_ROLES:
        pick_err = "rig_pick: role must be explore|mini|bulk|implement|hard|review|stay"
    else:
        choice = rig_route.pick(
            live, effective, role_n, case or "", exclude=exclude,
            parent_model=parent_model, parent_effort=parent_effort,
            writer_job_id=writer_job_id, writer_cli=writer_cli, writer_model=writer_model,
            writer_provider=writer_provider, review_mode=review_mode, repo=repo, jobs_snapshot=listing,
            hash_cache=hash_cache,
        )
    shown = _compact_rows(listing, terminal_limit, repo=repo, cache=hash_cache) if compact else listing
    history = {
        "total": len(listing),
        "shown": len(shown),
        "omitted": len(listing) - len(shown),
        "invalid_directories": getattr(listing, "directory_count", len(listing)) - getattr(listing, "real_job_count", len(listing)),
    }
    if as_json:
        payload = {
            "memory": mem,
            "jobs": shown,
            "status": status,
            "pick": choice if not pick_err else {"error": pick_err},
        }
        if compact:
            payload.update(schema_version=2, mode="compact", history=history)
            payload["status"] = {
                "live": live or "",
                "preferred": rig_harness.preferred_parent(repo),
                "effective": effective,
                "jobs": getattr(listing, "directory_count", len(listing)),
                "thread": rig_jobs.current_thread(repo),
                "memory_facts": len(rig_memory.parse_bullets(mem)),
                "reserved": sum(1 for reservation in getattr(listing, "reservations", None) or [] if reservation.get("stage") == "reserved"),
            }
        return json.dumps(payload, indent=2, default=str)
    jobs_text = rig_jobs.format_table(shown, repo, jobs_snapshot=listing)
    if compact:
        jobs_text += (
            f"\nhistory: {history['shown']} shown / {history['total']} valid jobs; "
            f"{history['omitted']} omitted; {history['invalid_directories']} invalid directories"
        )
    parts = [
        "# memory",
        mem.strip() or "(empty)",
        "# jobs",
        jobs_text,
        "# status",
        status.strip() or "(empty)",
        "# pick",
        pick_err or json.dumps(choice, indent=2),
    ]
    return "\n".join(parts)


def _progress_token(params: dict):
    meta = params.get("_meta") if isinstance(params, dict) else None
    if not isinstance(meta, dict) or "progressToken" not in meta:
        return None
    token = meta.get("progressToken")
    if token is None or token == "":
        return None
    return token


def _progress_on_tick(token):
    n = 0

    def on_tick(job: dict) -> None:
        nonlocal n
        n += 1
        status = str((job or {}).get("effective") or "").strip()
        job_id = str((job or {}).get("job_id") or "").strip()
        doing = str((job or {}).get("doing") or "").strip()
        write_message(
            {
                "jsonrpc": "2.0",
                "method": "notifications/progress",
                "params": {
                    "progressToken": token,
                    "progress": n,
                    "message": " ".join(p for p in (status, job_id, doing) if p),
                },
            }
        )

    return on_tick


def _child_ask(job_dir: Path, args: dict, *, permission: bool) -> dict:
    preview_text = str(args.get("preview") or args.get("text") or "").strip()
    tool, inp, uid = rig_ask.parse_prompt_args(args)
    if not permission:
        if preview_text and (tool == "tool" or not args.get("tool_name")):
            tool = str(args.get("tool_name") or "ask")
            if not inp:
                inp = {"text": preview_text}
        rig_ask.write_ask(job_dir, tool, inp, uid, preview_text=preview_text)
    else:
        rig_ask.write_ask(job_dir, tool, inp, uid)
    reply = rig_ask.wait_reply(job_dir)
    decision = rig_ask.decision_from_reply(reply, inp)
    rig_ask.consume_ask(job_dir)
    return _ok(json.dumps(decision))


def call_tool(name: str, args: dict, on_tick=None, *, wait_paths: list[Path] | None = None) -> dict:
    args = args or {}
    try:
        child = is_child()
        allowed = CHILD_TOOL_NAMES if child else PARENT_TOOL_NAMES
        if name not in allowed:
            who = "child" if child else "parent"
            return _err(f"{name} is not a {who} tool")
        repo = _repo(args)
        if child and name == "rig_job_show":
            jid = child_job_id()
            want = str(args.get("id") or jid).strip()
            if want and jid and want != jid:
                return _err("child can only show its own job")
            args = {**args, "id": jid}
        if name == "rig_job_doing":
            text = str(args.get("text") or "").strip()
            if not text:
                return _err("rig_job_doing needs text")
            return _ok(rig_jobs.set_doing(child_job_dir(repo), text))
        if name == "rig_job_note":
            text = str(args.get("text") or "").strip()
            if not text:
                return _err("rig_job_note needs text")
            return _ok(rig_jobs.add_note(child_job_dir(repo), text))
        if name == "permission_prompt":
            return _child_ask(child_job_dir(repo), args, permission=True)
        if name == "rig_job_ask":
            preview = str(args.get("preview") or args.get("text") or "").strip()
            if not preview and not args.get("tool_name") and not args.get("input"):
                return _err("rig_job_ask needs preview or text")
            return _child_ask(child_job_dir(repo), args, permission=False)
        if name == "rig_job_inbox":
            obj = rig_inbox.consume_inbox(child_job_dir(repo))
            if not obj:
                return _ok("(empty)")
            return _ok(str(obj.get("text") or ""))
        if name == "rig_job_message":
            text = str(args.get("text") or "").strip()
            if not text:
                return _err("rig_job_message needs text")
            job = rig_jobs.resolve_job(repo, args.get("id"))
            obj = rig_inbox.write_inbox(Path(job["dir"]), text)
            return _ok(f"message {job['job_id']} inbox pending\n{obj['text']}")
        if name == "rig_queue_add":
            text = str(args.get("text") or "").strip()
            if not text:
                return _err("rig_queue_add needs text")
            obj = rig_queue.add_item(repo, text)
            return _ok(f"queued {obj['id']}\n{obj['text']}\n{rig_queue.format_block(repo)}")
        if name == "rig_queue_list":
            return _ok(rig_queue.format_list(repo))
        if name == "rig_queue_cancel":
            qid = str(args.get("id") or "").strip()
            if not qid:
                return _err("rig_queue_cancel needs id")
            try:
                obj = rig_queue.cancel_item(repo, qid)
            except FileNotFoundError:
                return _err(f"queue item not found: {qid}")
            except ValueError as exc:
                return _err(str(exc))
            return _ok(f"cancelled {obj['id']}\n{rig_queue.format_block(repo)}")
        if name == "rig_queue_claim":
            files = args.get("files")
            try:
                obj = rig_queue.claim_next(
                    repo,
                    files=files,
                    item_id=str(args.get("id") or "").strip(),
                    worker=_optional_string(args, "worker"), access=args.get("access", "write"),
                    owner_session=_optional_string(args, "owner_session"), job_id=_optional_string(args, "job_id"),
                )
            except rig_queue.QueueError as exc:
                return _err(str(exc))
            except FileNotFoundError as exc:
                return _err(f"queue item not found: {exc}")
            return {**_ok(f"claimed {obj['id']}\n{obj.get('text') or ''}\n{rig_queue.format_block(repo)}"), "structuredContent": obj}
        if name == "rig_queue_unclaim":
            qid = str(args.get("id") or "").strip()
            if not qid:
                return _err("rig_queue_unclaim needs id")
            try:
                obj = rig_queue.unclaim(repo, qid, **_ownership_args(args))
            except FileNotFoundError:
                return _err(f"queue item not found: {qid}")
            except rig_queue.QueueError as exc:
                return _err(str(exc))
            return _ok(f"unclaimed {obj['id']}\n{rig_queue.format_block(repo)}")
        if name == "rig_queue_spawned":
            qid = str(args.get("id") or "").strip()
            jid = str(args.get("job_id") or "").strip()
            try:
                obj = rig_queue.mark_spawned(
                    repo, qid, jid, files=args.get("files"), worker=_optional_string(args, "worker"),
                    access=_optional_string(args, "access"), **_ownership_args(args),
                )
            except FileNotFoundError:
                return _err(f"queue item not found: {qid}")
            except rig_queue.QueueError as exc:
                return _err(str(exc))
            return _ok(f"{obj.get('status') or 'spawned'} {obj['id']} job {obj.get('job_id')}\n{rig_queue.format_block(repo)}")
        if name == "rig_jobs":
            want_thread = str(args.get("thread") or "").strip() or None
            listing = rig_jobs.list_jobs(repo, thread=want_thread)
            want = str(args.get("status") or "").strip()
            if want:
                listing = [j for j in listing if j["effective"] == want or j["status"] == want]
            return _ok(rig_jobs.format_table(listing, repo))
        if name == "rig_job_show":
            job = rig_jobs.resolve_job(repo, args.get("id"))
            return _ok(rig_jobs.format_show(job))
        if name == "rig_job_log":
            job = rig_jobs.resolve_job(repo, args.get("id"))
            n = args.get("lines") or 40
            try:
                n = int(n)
            except (TypeError, ValueError):
                n = 40
            return _ok(rig_jobs.format_log(job, n))
        if name == "rig_job_wait":
            timeout = args.get("timeout")
            if timeout is None:
                timeout_s = None
            else:
                try:
                    timeout_s = float(timeout)
                except (TypeError, ValueError):
                    timeout_s = None
            code, text = rig_jobs.wait_job(
                repo,
                args.get("id"),
                timeout_s,
                on_tick=on_tick,
                resolved_paths=wait_paths,
                ids=args.get("ids"),
            )
            if code == 1:
                return _err(text)
            return _ok(text)
        if name == "rig_job_allow":
            job = rig_jobs.resolve_job(repo, args.get("id"))
            text = rig_jobs.answer_pending(job, "allow")
            return _ok(text) if text.startswith("allow") else _err(text)
        if name == "rig_job_deny":
            job = rig_jobs.resolve_job(repo, args.get("id"))
            text = rig_jobs.answer_pending(job, "deny", str(args.get("reason") or ""))
            return _ok(text) if text.startswith("deny") else _err(text)
        if name == "rig_job_cancel":
            reason = str(args.get("reason") or "parent").strip() or "parent"
            names = rig_jobs._normalize_wait_ids(args.get("id"), args.get("ids"))
            if not names:
                names = [None]
            lines = []
            ok = True
            for jid in names:
                try:
                    text = rig_jobs.cancel_job(repo, jid, reason)
                except SystemExit as exc:
                    return _err(str(exc) or "rig error")
                lines.append(text)
                if not str(text).startswith("cancelled"):
                    ok = False
            body = "\n".join(lines)
            return _ok(body) if ok else _err(body)
        if name == "rig_memory":
            return _ok(rig_memory.show_memory(repo))
        if name == "rig_memory_add":
            fact = str(args.get("fact") or "")
            if not rig_memory.normalize_fact(fact):
                return _err("rig_memory_add needs fact")
            return _ok(rig_memory.add_memory(repo, fact))
        if name == "rig_pick":
            role = str(args.get("role") or "").strip()
            if role and role not in PICK_ROLES:
                return _err(
                    "rig_pick: role must be explore|mini|bulk|implement|hard|review|stay"
                )
            case = str(args.get("case") or "")
            live = rig_harness.live_parent()
            effective = rig_harness.effective_workers(repo, live)
            exclude = str(args.get("exclude") or "")
            choice = rig_route.pick(
                live, effective, role, case, exclude=exclude,
                parent_model=_optional_string(args, "parent_model"),
                parent_effort=_optional_string(args, "parent_effort"),
                repo=repo, **_review_args(args),
            )
            return _ok(json.dumps(choice, indent=2))
        if name == "rig_session":
            role = str(args.get("role") or "").strip()
            if role and role not in PICK_ROLES:
                return _err(
                    "rig_session: role must be explore|mini|bulk|implement|hard|review|stay"
                )
            case = str(args.get("case") or "")
            if not case.strip():
                return _err("rig_session needs case")
            return _ok(
                format_session(
                    repo,
                    case,
                    role,
                    str(args.get("exclude") or ""),
                    as_json=args.get("compact", False) is True,
                    compact=args.get("compact", False),
                    terminal_limit=args.get("terminal_limit", 10),
                    parent_model=_optional_string(args, "parent_model"),
                    parent_effort=_optional_string(args, "parent_effort"),
                    **_review_args(args),
                )
            )
        if name == "rig_status":
            return _ok(rig_harness.format_status(repo, live=rig_harness.live_parent()))
        if name == "rig_job_close":
            result = rig_jobs.close_job(repo, _optional_string(args, "id"),
                                        rationale=_optional_string(args, "rationale"), **_ownership_args(args))
            return _ok(json.dumps(result, indent=2))
        if name == "rig_job_reconcile":
            if not isinstance(args.get("apply", False), bool):
                raise ValueError("apply must be a boolean")
            result = rig_jobs.reconcile_jobs(
                repo, _optional_string(args, "id"), queue_id=_optional_string(args, "queue_id"),
                apply=args.get("apply", False), action=args.get("action", "report"),
                worker=_optional_string(args, "worker"), **_ownership_args(args),
                access=args.get("access", "write"), files=args.get("files"),
                rationale=_optional_string(args, "rationale"), completion=args.get("completion"),
            )
            return _ok(json.dumps(result, indent=2))
        if name in {"rig_job_requirements", "rig_job_check", "rig_job_accept"}:
            job_id = _optional_string(args, "id").strip()
            if not job_id:
                return _err(f"{name} needs id")
            job_dir = Path(rig_jobs.resolve_job(repo, job_id)["dir"])
            if name == "rig_job_requirements":
                result = rig_verification.record_requirements(
                    repo, job_dir, args.get("requirements", []), args.get("manual_criteria", []),
                    **_ownership_args(args),
                )
            elif name == "rig_job_check":
                result = rig_verification.run_check(
                    repo, job_dir, _optional_string(args, "name"), args.get("argv"),
                    cwd=args.get("cwd"), on_tick=on_tick,
                    **_ownership_args(args),
                )
            else:
                result = rig_verification.accept(
                    repo, job_dir, _optional_string(args, "decision"),
                    _optional_string(args, "snapshot_id"), check_ids=args.get("check_ids", []),
                    rationale=_optional_string(args, "rationale"), next=args.get("next", "complete"),
                    **_ownership_args(args),
                )
            return _ok(json.dumps(result, indent=2))
        if name in {"rig_job_start", "rig_job_finish", "rig_job_record"}:
            live = rig_harness.live_parent()
            preferred = rig_harness.preferred_parent(repo)
            worker = str(args.get("worker") or "")
            role = str(args.get("role") or "")
            summary = str(args.get("summary") or "")
            job_id = str(args.get("id") or "")
            if name == "rig_job_start":
                result = rig_jobs.start_job(
                        repo,
                        worker=worker,
                        role=role or "worker",
                        job_id=job_id,
                        summary=summary,
                        live=live,
                        preferred=preferred,
                        **_execution_args(args),
                        files=args.get("files"),
                        writer_job_id=_optional_string(args, "writer_job_id"),
                        writer_snapshot_id=_optional_string(args, "writer_snapshot_id"),
                        access=_optional_string(args, "access"), queue_id=_optional_string(args, "queue_id"),
                        native_agent_id=_optional_string(args, "native_agent_id"), return_details=True,
                        **_ownership_args(args),
                )
                return {**_ok(result["job_id"]), "structuredContent": result}
            status = str(args.get("status") or "ok")
            if name == "rig_job_finish":
                result = rig_jobs.finish_job(
                        repo,
                        job_id,
                        status=status,
                        summary=summary,
                        worker=worker,
                        role=role,
                        live=live,
                        preferred=preferred,
                        completion=args.get("completion"), return_details=True, **_ownership_args(args),
                )
                return {**_ok(result["text"]), "structuredContent": result}
            return _ok(
                rig_jobs.record_job(
                    repo,
                    worker=worker,
                    role=role or "worker",
                    status=status,
                    summary=summary,
                    job_id=job_id,
                    live=live,
                    preferred=preferred,
                    **_execution_args(args),
                )
            )
        return _err(f"unknown tool {name}")
    except SystemExit as exc:
        return _err(str(exc) or "rig error")
    except Exception as exc:  # noqa: BLE001 — MCP must not crash the parent
        return _err(str(exc))


_FRAMING = "lsp"
_out_lock = threading.Lock()
_wait_lock = threading.Lock()
_inflight_waits: dict[str, dict] = {}
_wait_threads: list[threading.Thread] = []


def _abort_wait(rid) -> None:
    key = str(rid)
    with _wait_lock:
        item = _inflight_waits.get(key)
    if not item:
        return
    repo = item.get("repo")
    for path in item.get("paths") or []:
        try:
            rig_jobs.cancel_job(repo, path.name, "wait-cancelled", job_path=path)
        except SystemExit:
            continue


def read_message() -> dict | None:
    global _FRAMING
    line = sys.stdin.buffer.readline()
    if not line:
        return None
    stripped = line.lstrip()
    if stripped.startswith(b"{"):
        _FRAMING = "ndjson"
        return json.loads(stripped)
    headers: dict[str, str] = {}
    while True:
        if line in (b"\r\n", b"\n"):
            break
        key, _, val = line.decode("utf-8", errors="replace").partition(":")
        headers[key.strip().lower()] = val.strip()
        line = sys.stdin.buffer.readline()
        if not line:
            return None
    n = int(headers.get("content-length") or "0")
    if n <= 0:
        return None
    _FRAMING = "lsp"
    body = sys.stdin.buffer.read(n)
    return json.loads(body.decode("utf-8"))


def write_message(msg: dict) -> None:
    raw = json.dumps(msg, ensure_ascii=False).encode("utf-8")
    with _out_lock:
        if _FRAMING == "ndjson":
            sys.stdout.buffer.write(raw + b"\n")
        else:
            sys.stdout.buffer.write(f"Content-Length: {len(raw)}\r\n\r\n".encode("ascii") + raw)
        sys.stdout.buffer.flush()


def handle(msg: dict) -> dict | None:
    method = msg.get("method")
    mid = msg.get("id")
    if method == "initialize":
        params = msg.get("params") if isinstance(msg.get("params"), dict) else {}
        proto = str(params.get("protocolVersion") or "2024-11-05")
        return {
            "jsonrpc": "2.0",
            "id": mid,
            "result": {
                "protocolVersion": proto,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "rig", "version": "1"},
            },
        }
    if method == "notifications/initialized" or method == "initialized":
        return None
    if method == "notifications/cancelled" or method == "cancelled":
        params = msg.get("params") if isinstance(msg.get("params"), dict) else {}
        _abort_wait(params.get("requestId"))
        return None
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": mid, "result": {"tools": listed_tools()}}
    if method == "tools/call":
        params = msg.get("params") or {}
        if not isinstance(params, dict):
            params = {}
        name = str(params.get("name") or "")
        args = params.get("arguments") or {}
        if not isinstance(args, dict):
            args = {}
        on_tick = None
        if name == "rig_job_wait" and not is_child():
            token = _progress_token(params)
            if token is not None:
                on_tick = _progress_on_tick(token)
            repo = _repo(args)
            try:
                names = rig_jobs._normalize_wait_ids(args.get("id"), args.get("ids"))
                paths = rig_jobs.resolve_job_paths(repo, names)
            except (SystemExit, Exception) as exc:  # Lookup failures must not stop the MCP server.
                return {"jsonrpc": "2.0", "id": mid, "result": _err(str(exc) or "rig error")}
            # Keep directory identities for both refresh and cancellation. A vanished
            # target must never be replaced by a newly matching partial job id.
            args = {**args, "id": "", "ids": [path.name for path in paths]}
            with _wait_lock:
                _inflight_waits[str(mid)] = {"repo": repo, "paths": paths}

            def _run_wait() -> None:
                try:
                    result = call_tool(name, args, on_tick=on_tick, wait_paths=paths)
                    write_message({"jsonrpc": "2.0", "id": mid, "result": result})
                except Exception as exc:  # noqa: BLE001
                    write_message(
                        {
                            "jsonrpc": "2.0",
                            "id": mid,
                            "result": _err(str(exc)),
                        }
                    )
                finally:
                    with _wait_lock:
                        _inflight_waits.pop(str(mid), None)

            t = threading.Thread(target=_run_wait, daemon=True)
            with _wait_lock:
                _wait_threads.append(t)
            t.start()
            return None
        if name == "rig_job_check" and not is_child():
            token = _progress_token(params)
            if token is not None:
                on_tick = _progress_on_tick(token)
            # Checks may run for minutes. Keep the reader available for ASK,
            # cancellation, and other requests while preserving request progress.
            def _run_check() -> None:
                try:
                    result = call_tool(name, args, on_tick=on_tick)
                except (SystemExit, Exception) as exc:
                    result = _err(str(exc) or "rig error")
                write_message({"jsonrpc": "2.0", "id": mid, "result": result})

            t = threading.Thread(target=_run_check, daemon=True)
            with _wait_lock:
                _wait_threads.append(t)
            t.start()
            return None
        result = call_tool(name, args, on_tick=on_tick)
        return {"jsonrpc": "2.0", "id": mid, "result": result}
    if method == "ping":
        return {"jsonrpc": "2.0", "id": mid, "result": {}}
    if mid is not None:
        return {
            "jsonrpc": "2.0",
            "id": mid,
            "error": {"code": -32601, "message": f"method not found: {method}"},
        }
    return None


def run_session_cli(argv: list[str]) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="rig_mcp")
    parser.add_argument("--session", action="store_true")
    parser.add_argument("--case", default="")
    parser.add_argument("--role", default="")
    parser.add_argument("--exclude", default="")
    parser.add_argument("--parent-model", default="")
    parser.add_argument("--parent-effort", default="")
    parser.add_argument("--compact", action="store_true")
    parser.add_argument("--terminal-limit", type=int, default=10)
    parser.add_argument("--repo", default="")
    parser.add_argument("--json", action="store_true")
    for name in ("writer-job-id", "writer-cli", "writer-model", "writer-provider"):
        parser.add_argument("--" + name, default="")
    parser.add_argument("--review-mode", choices=["standalone", "independent"], default="standalone")
    args = parser.parse_args(argv)
    if not 0 <= args.terminal_limit <= 100:
        parser.error("--terminal-limit must be an integer from 0 to 100")
    repo = rig_jobs.repo_root(args.repo or None)
    print(
        format_session(
            repo,
            args.case,
            args.role,
            args.exclude,
            as_json=args.json,
            compact=args.compact,
            terminal_limit=args.terminal_limit,
            parent_model=args.parent_model,
            parent_effort=args.parent_effort,
            writer_job_id=args.writer_job_id, writer_cli=args.writer_cli,
            writer_model=args.writer_model, writer_provider=args.writer_provider,
            review_mode=args.review_mode,
        )
    )
    return 0


def main() -> int:
    if "--session" in sys.argv:
        return run_session_cli(sys.argv[1:])
    while True:
        try:
            msg = read_message()
        except (OSError, json.JSONDecodeError, ValueError):
            return 1
        if msg is None:
            with _wait_lock:
                threads = list(_wait_threads)
            for t in threads:
                t.join()
            return 0
        reply = handle(msg)
        if reply is not None:
            write_message(reply)


if __name__ == "__main__":
    raise SystemExit(main())
