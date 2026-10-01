"""MCP tool schemas and tool order. Do not import the rig_mcp facade."""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import context_packages  # noqa: E402
import doctor as rig_doctor  # noqa: E402
import harness as rig_harness  # noqa: E402
import routing_domains  # noqa: E402
import preparation_inputs  # noqa: E402

PICK_ROLES = ("explore", "mini", "bulk", "implement", "hard", "review", "verify", "stay")
JOB_WORKERS = ("grok", "codex", "claude", "cursor", "opencode", "omp", "pi", "agy", "devin", "mimo", "parent")
JOB_RECORD_STATUSES = ("ok", "fail", "timeout")
JOB_FINISH_STATUSES = ("ok", "fail", "timeout", "cancelled")
REVIEW_PROPERTIES = {
    "writer_job_id": {"type": "string"},
    "writer_cli": {"type": "string"},
    "writer_model": {"type": "string"},
    "writer_provider": {"type": "string"},
    "writer_job_ids": {"type": "array", "items": {"type": "string"}},
    "writer_snapshot_ids": {"type": "array", "items": {"type": "string"}},
    "writer_providers": {"type": "array", "items": {"type": "string"}},
    "review_mode": {"type": "string", "enum": ["standalone", "independent"], "default": "standalone"},
}
TASK_DOMAIN_PROPERTIES = {
    "task_domain": {"type": "string", "enum": list(routing_domains.DOMAINS),
                    "description": "Task domain, separate from role and capability tier. Explicit domain is authoritative."},
    "research_sources": {"type": "array", "items": {"type": "string"},
                         "description": "Repository-contained research source files. Research delegation requires role explore and verified files."},
}
ASSESSMENT_LEVELS = ["low", "medium", "high"]
RAW_ASSESSMENT_SCHEMA = {
    "type": "object",
    "properties": {
        "complexity": {"type": "string", "enum": ASSESSMENT_LEVELS},
        "risk": {"type": "string", "enum": ASSESSMENT_LEVELS},
        "uncertainty": {"type": "string", "enum": ASSESSMENT_LEVELS},
        "reason": {"type": "string"},
        "defaulted": {"type": "array", "items": {"type": "string"}},
        "supplied": {"type": "boolean"},
    },
    "additionalProperties": False,
}
ASSESSMENT_PROPERTIES = {
    "complexity": {"type": "string", "enum": ASSESSMENT_LEVELS},
    "risk": {"type": "string", "enum": ASSESSMENT_LEVELS},
    "uncertainty": {"type": "string", "enum": ASSESSMENT_LEVELS},
    "assessment_reason": {"type": "string"},
    "assessment": RAW_ASSESSMENT_SCHEMA,
    "explain": {"type": "boolean", "default": False, "description": "Expand routing trace in text output."},
    "policy_mode": {"type": "string", "enum": ["smart", "legacy"]},
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
    "model": {
        "type": "string",
        "description": (
            "Executing model. Native child admission requires a selected model; "
            "parent work may omit it when the actual parent model is unknown."
        ),
    },
    "effort": {
        "type": "string",
        "description": (
            "Executing model reasoning effort. Native child keeps the selected pick effort; "
            "parent work may omit it when unknown."
        ),
    },
    "executor_kind": {
        "type": "string",
        "enum": ["parent", "native_child"],
        "description": "Default parent for role/worker parent; otherwise native_child.",
    },
}

ACCEPTANCE_CONTRACT_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "description": "Optional immutable acceptance contract. Frozen before work; revisions require a fresh attempt. Grants no tools or permissions.",
    "properties": {
        "schema_version": {"type": "integer", "enum": [1]},
        "contract_id": {"type": "string"}, "revision": {"type": "integer", "minimum": 1, "maximum": 1000000},
        "criteria": {"type": "array", "minItems": 1, "maxItems": 64, "items": {
            "type": "object", "additionalProperties": False,
            "properties": {
                "id": {"type": "string"}, "description": {"type": "string", "maxLength": 2048},
                "scope": {"type": "array", "minItems": 1, "maxItems": 256, "items": {"type": "string"}},
                "evidence_type": {"type": "string", "enum": ["check", "review_assertion"]},
                "verifier_role": {"type": "string", "enum": ["parent", "independent-review"]},
                "artifact_kind": {"type": "string"},
                "check": {"type": "object", "additionalProperties": False, "properties": {
                    "id": {"type": "string"}, "argv": {"type": "array", "minItems": 1, "maxItems": 128, "items": {"type": "string"}},
                    "cwd": {"type": "string"}}, "required": ["id", "argv"]},
            }, "required": ["id", "description", "scope", "evidence_type", "verifier_role"],
        }},
    }, "required": ["schema_version", "contract_id", "revision", "criteria"],
}

TOOLS = [
    {
        "name": "rig_jobs",
        "annotations": {
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
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
        "annotations": {
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
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
        "annotations": {
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
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
        "annotations": {
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": True,
        },
        "description": (
            "Block until the job (or jobs) asks for permission or finishes. "
            "Do not pass timeout unless you must cap the wait. Do not poll. "
            "If the text starts with ASK, call rig_job_allow or rig_job_deny next "
            "so that child can continue; then wait the same ids again. "
            "Do not kill an ask or running job because the child asked. Never spawn a replacement. "
            "If this wait is cancelled (user Esc / notifications/cancelled), stop is requested for those exact attempts; "
            "do not re-pick or re-wait. Unknown/native liveness returns reconciliation guidance. After implement+verify ok, pass ids for read-only review "
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
        "annotations": {
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
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
        "annotations": {
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": True,
            "openWorldHint": True,
        },
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
        "annotations": {
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": True,
            "openWorldHint": True,
        },
        "description": (
            "Abort a live job (running or ask) and its worker process. "
            "Use when the user cancelled the parent turn/wait (Esc/Stop). "
            "Persists stop intent promptly. Completion requires confirmed termination. Do not re-pick or re-wait. "
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
        "annotations": {
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
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
        "annotations": {
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
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
        "annotations": {
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
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
                        "Pass stay|explore|mini|bulk|implement|hard|review|verify "
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
                **ASSESSMENT_PROPERTIES,
            },
            "required": ["case"],
        },
    },
    {
        "name": "rig_routing_report",
        "annotations": {
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        "description": (
            "Read-only routing evidence report. Does not influence pick. "
            "Groups policy version, required tier, profile, and actual model/effort."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "days": {"type": "integer", "minimum": 1, "default": 30},
                "repo": {"type": "string"},
            },
        },
    },
    {
        "name": "rig_billing_report",
        "annotations": {
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        "description": "Read-only invoice-dollar report. Actual receipts only; never estimates.",
        "inputSchema": {"type": "object", "properties": {
            "repo": {"type": "string"}, "scope": {"type": "string"},
        }},
    },
    {
        "name": "rig_billing_import",
        "annotations": {
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        "description": "Parent-only. Import one actual USD receipt into the local billing ledger. Idempotent. Never stores credentials. dry_run validates without writing.",
        "inputSchema": {"type": "object", "properties": {
            "repo": {"type": "string"}, "scope": {"type": "string"},
            "receipt": {"type": "object"},
            "dry_run": {"type": "boolean", "default": False},
        }, "required": ["receipt"]},
    },
    {
        "name": "rig_billing_sync",
        "annotations": {
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": True,
        },
        "description": "Parent-only. OpenAI or Anthropic read-only receipt adapters. Network is explicit/opt-in. Generic providers use rig_billing_import. Never prints credential values.",
        "inputSchema": {"type": "object", "properties": {
            "repo": {"type": "string"}, "scope": {"type": "string"},
            "provider": {"type": "string", "enum": ["openai", "anthropic"]},
            "receipts": {"type": "array", "items": {"type": "object"}},
            "dry_run": {"type": "boolean", "default": False},
            "network": {"type": "boolean", "default": False},
        }, "required": ["provider"]},
    },
    {
        "name": "rig_benchmark_report",
        "annotations": {
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        "description": "Read-only benchmark coverage report. Separates observed tokens from actual invoice dollars. Savings is gated.",
        "inputSchema": {"type": "object", "properties": {
            "repo": {"type": "string"}, "id": {"type": "string"}, "scope": {"type": "string"},
        }, "required": ["id"]},
    },
    {
        "name": "rig_benchmark_create",
        "annotations": {
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        "description": "Parent-only. Freeze a local benchmark spec under .rig/benchmarks/<id>.",
        "inputSchema": {"type": "object", "properties": {
            "repo": {"type": "string"}, "spec": {"type": "object"},
        }, "required": ["spec"]},
    },
    {
        "name": "rig_benchmark_outcome",
        "annotations": {
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        "description": "Parent-only. Attribute a currently accepted job to a frozen benchmark task and arm.",
        "inputSchema": {"type": "object", "properties": {
            "repo": {"type": "string"}, "id": {"type": "string"},
            "job_id": {"type": "string"}, "task": {"type": "string"},
            "arm": {"type": "string", "enum": ["rig", "baseline"]},
        }, "required": ["id", "job_id", "task", "arm"]},
    },
    {
        "name": "rig_doctor",
        "annotations": {"readOnlyHint": True, "destructiveHint": False,
                        "idempotentHint": True, "openWorldHint": False},
        "description": "Parent-only task readiness. Offline config/cache evidence, missing auth evidence and stale MCP detection. Optional smoke only tests a temporary child inbox fixture; never invokes providers, browsers, installers, grants or user repo writes.",
        "inputSchema": {"type": "object", "properties": {
            "repo": {"type": "string"},
            "task": {"type": "string", "enum": list(rig_doctor.TASKS), "default": "coding"},
            "parent": {"type": "string", "enum": sorted(rig_harness.PARENTS)},
            "model": {"type": "string", "description": "Exact selector to check against cached model evidence only."},
            "research_sources": {"type": "array", "items": {"type": "string"}},
            "smoke": {"type": "boolean", "default": False},
        }},
    },
    {
        "name": "rig_status",
        "annotations": {
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
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
        "annotations": {
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
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
        "annotations": {
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        "description": (
            "Finish a recorded job (ok|fail|timeout|cancelled). Writes result.json. "
            "Native cancelled finish still requires matching host completion, not caller attestation. "
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
                "token_usage": {
                    "type": "object",
                    "description": "Optional observed canonical token usage. Never estimated. Opaque parent usage stays unknown if omitted.",
                },
            },
            "required": ["id"],
        },
    },
    {
        "name": "rig_job_record",
        "annotations": {
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
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
                    "enum": list(JOB_RECORD_STATUSES),
                    "description": "Default ok.",
                },
                "summary": {"type": "string"},
                "id": {"type": "string"},
                "repo": {"type": "string"},
                "token_usage": {
                    "type": "object",
                    "description": "Optional observed canonical token usage. Never estimated.",
                },
            },
        },
    },
    {
        "name": "rig_session",
        "annotations": {
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
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
                    "description": (
                        "Optional pick kind. Omit to classify from case. "
                        "Pass stay|explore|mini|bulk|implement|hard|review|verify "
                        "when the parent already knows the kind."
                    ),
                },
                "exclude": {
                    "type": "string",
                    "description": "Comma worker names to skip. Same as rig_pick exclude.",
                },
                "repo": {"type": "string", "description": "Project root. Default cwd."},
                **ASSESSMENT_PROPERTIES,
            },
            "required": ["case"],
        },
    },
    {
        "name": "rig_job_doing",
        "annotations": {
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
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
        "annotations": {
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
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
        "annotations": {
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": True,
        },
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
        "annotations": {
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": False,
            "openWorldHint": True,
        },
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
        "name": "rig_job_launch",
        "annotations": {
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": True,
        },
        "description": (
            "Parent only. Admit a scoped wrapper child and detach the installed "
            "run-worker.sh. Returns immediately with job_id, worker, role, "
            "wrapper_pid, status, reservation_id, attempt_id, and credentials_path. "
            "Public text and structuredContent never include owner_token. Does not wait. Shell launch is the "
            "internal/human fallback only."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "repo": {"type": "string"},
                "id": {"type": "string", "description": "Job id. Allocated if omitted."},
                "case": {"type": "string", "description": "Task text used when role is omitted."},
                "role": {"type": "string", "description": "Pick kind or job role."},
                "worker": {
                    "type": "string",
                    "enum": ["grok", "codex", "claude", "cursor", "opencode", "omp", "pi", "agy", "devin", "mimo"],
                },
                "model": {"type": "string"},
                "effort": {"type": "string"},
                "access": {"type": "string", "enum": ["read", "write"]},
                "files": {"type": "array", "items": {"type": "string"}},
                "brief": {"type": "string", "description": "Worker brief markdown."},
                "queue_id": {"type": "string"},
                "writer_job_id": {"type": "string"},
                "continues_job_id": {"type": "string"},
                "writer_snapshot_id": {"type": "string"},
                "writer_cli": {"type": "string"},
                "writer_model": {"type": "string"},
                "writer_provider": {"type": "string"},
                "review_mode": {"type": "string", "enum": ["standalone", "independent"], "default": "standalone"},
                "routing": {"type": "object", "description": "Untrusted pick routing metadata. Never an ownership credential."},
                "assessment": RAW_ASSESSMENT_SCHEMA,
                "reservation_id": {"type": "string"},
                "attempt_id": {"type": "string"},
                "owner_token": {"type": "string"},
                "owner_session": {"type": "string"},
            },
            "required": ["brief"],
            "additionalProperties": False,
        },
    },
    {
        "name": "rig_job_inbox",
        "annotations": {
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": True,
        },
        "description": (
            "Child only. Required first Rig operation each session: pull the parent "
            "inbox once and ack it. Records the child MCP handshake. Empty if the "
            "parent has not sent mail. Not ASK."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "rig_job_message",
        "annotations": {
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": True,
        },
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
        "annotations": {
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        "description": (
            "Parent only. Park a work item in .rig/queue/. Does not spawn. "
            "Does not wait. Use while a child is running so the next free turn can drain it."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Work to park."},
                "idempotency_key": {"type": "string", "description": "Optional stable submission identity for retries."},
                "repo": {"type": "string"},
            },
            "required": ["text"],
        },
    },
    {
        "name": "rig_queue_list",
        "annotations": {
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
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
        "annotations": {
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        "description": "Parent only. Cancel a queue item. Held execution reservations require confirmed shutdown; an owned, unlaunched claim can be released. Does not spawn.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "Queue item id."},
                "repo": {"type": "string"},
                "reservation_id": {"type": "string"},
                "attempt_id": {"type": "string"},
                "owner_token": {"type": "string"},
            },
            "required": ["id"],
        },
    },
    {
        "name": "rig_queue_claim",
        "annotations": {
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
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
        "annotations": {
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
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
        "annotations": {
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
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

CONTEXT_REFERENCE_SCHEMA = context_packages.REFERENCE_SCHEMA
for _operation in ("preview", "build"):
    TOOLS.append({
        "name": "rig_context_" + _operation,
        "description": "Parent-only. " + ("Read-only preview" if _operation == "preview" else "Explicit private artifact build")
            + " of bounded, selected local text context. No harvesting, command execution, or authority expansion.",
        "annotations": {"readOnlyHint": _operation == "preview", "destructiveHint": False,
                        "idempotentHint": True, "openWorldHint": False},
        "inputSchema": {"type": "object", "properties": {"repo": {"type": "string"},
            "selection": context_packages.SELECTION_SCHEMA},
            "required": ["selection"], "additionalProperties": False},
    })

for _name, _fields in (("rig_memory_replace", ("old", "new", "expected_sha256")),
                        ("rig_memory_remove", ("fact", "expected_sha256"))):
    TOOLS.append({"name": _name,
                  "description": "Parent-only exact memory edit guarded by the SHA256 returned by rig_memory.",
                  "annotations": {"readOnlyHint": False, "destructiveHint": False,
                                  "idempotentHint": False, "openWorldHint": False},
                  "inputSchema": {"type": "object", "properties": {
                      **{key: {"type": "string"} for key in _fields}, "repo": {"type": "string"}},
                      "required": list(_fields)}})
for _tool in TOOLS:
    if _tool["name"] in {"rig_job_start", "rig_job_launch"}:
        _tool["inputSchema"]["properties"]["acceptance_contract"] = ACCEPTANCE_CONTRACT_SCHEMA
        _tool["inputSchema"]["properties"]["context_package"] = CONTEXT_REFERENCE_SCHEMA
    if _tool["name"] in {"rig_pick", "rig_session", "rig_job_start", "rig_job_launch"}:
        _tool["inputSchema"]["properties"].update(TASK_DOMAIN_PROPERTIES)
    if _tool["name"] in {"rig_pick", "rig_session"}:
        _tool["inputSchema"]["properties"].update(REVIEW_PROPERTIES)
    if _tool["name"] == "rig_job_start":
        _tool["inputSchema"]["properties"].update({
            "files": {"type": "array", "items": {"type": "string"}},
            "writer_job_id": {"type": "string"},
            "continues_job_id": {"type": "string"},
            "writer_snapshot_id": {"type": "string"},
            "routing": {"type": "object", "description": "Untrusted pick routing metadata. Never an ownership credential."},
            "assessment": RAW_ASSESSMENT_SCHEMA,
        })

_JOB_REF_PROPERTIES = {"id": {"type": "string"}, "repo": {"type": "string"}}
_OWNERSHIP_PROPERTIES = {key: {"type": "string"} for key in
                         ("reservation_id", "attempt_id", "owner_token", "owner_session",
                          "credentials_path")}
_OWNERSHIP_PROPERTIES["credentials_path"]["description"] = (
    "Saved canonical private receipt. Restores its validated original owner session as well as "
    "attempt credentials; an explicitly conflicting owner_session is rejected. Does not transfer ownership."
)
TOOLS.append({
    "name": "rig_job_ui_evidence",
    "annotations": {"readOnlyHint": False, "destructiveHint": False,
                    "idempotentHint": True, "openWorldHint": False},
    "description": "Parent-only. Assemble already captured, privacy-reviewed UI receipts and PNGs into immutable private attempt evidence. Never captures, acts, or accepts work. Supplied receipts and privacy review are not authenticated backend or automatic privacy proof.",
    "inputSchema": {"type": "object", "properties": {
        **_JOB_REF_PROPERTIES, **_OWNERSHIP_PROPERTIES,
        "pack": {"type": "object", "description": "Bounded pack with privacy_reviewed=true, contract_fingerprint, content_snapshot_id, criterion_ids and steps; see docs/ui-evidence.md."},
    }, "required": ["id", "pack"]},
})

TOOLS.extend([
    {
        "name": "rig_job_requirements",
        "annotations": {
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
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
        "annotations": {
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": True,
        },
        "description": "Run the exact parent-authorized argv for a declared required check, recording actual output and before/after content snapshots. Does not accept the work.",
        "inputSchema": {"type": "object", "properties": {
            **_JOB_REF_PROPERTIES, "name": {"type": "string"},
            "argv": {"type": "array", "items": {"type": "string"}, "minItems": 1}, "cwd": {"type": "string"},
        }, "required": ["id", "name", "argv"]},
    },
    {
        "name": "rig_job_criterion",
        "annotations": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False},
        "description": "Parent-only explicit review assertion for a frozen criterion and current content. Requires matching existing evidence artifacts. This is an assertion, never objective check execution or automatic acceptance.",
        "inputSchema": {"type": "object", "properties": {
            **_JOB_REF_PROPERTIES,
            "criterion_id": {"type": "string"}, "contract_fingerprint": {"type": "string"},
            "snapshot_id": {"type": "string"}, "result": {"type": "string", "enum": ["pass", "fail"]},
            "rationale": {"type": "string", "maxLength": 4096},
            "evidence_refs": {"type": "array", "minItems": 1, "maxItems": 16, "items": {
                "type": "object", "additionalProperties": False,
                "properties": {key: {"type": "string"} for key in ("path", "sha256", "kind")},
                "required": ["path", "sha256", "kind"],
            }},
        }, "required": ["id", "criterion_id", "contract_fingerprint", "snapshot_id", "result", "rationale", "evidence_refs"]},
    },
    {
        "name": "rig_job_accept",
        "annotations": {
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
        "description": "Parent accepts or rejects current scoped content against EVERY manifest requirement. Check IDs cannot omit failed or missing requirements. next=review retains files for independent review.",
        "inputSchema": {"type": "object", "properties": {
            **_JOB_REF_PROPERTIES, "decision": {"type": "string", "enum": ["accept", "reject"]},
            "snapshot_id": {"type": "string"}, "check_ids": {"type": "array", "items": {"type": "string"}},
            "rationale": {"type": "string"}, "next": {"type": "string", "enum": ["complete", "review"], "default": "complete"},
        }, "required": ["id", "decision", "snapshot_id", "rationale"]},
    },
])

TOOLS.extend([
    {"name": "rig_job_close",
     "annotations": {
         "readOnlyHint": False,
         "destructiveHint": True,
         "idempotentHint": True,
         "openWorldHint": False,
     },
     "description": "Parent deliberately releases a confirmed-stopped attempt without accepting or retrying it. Requires exact ownership credentials or a validated credentials_path, plus rationale.",
     "inputSchema": {"type": "object", "properties": {
         **_JOB_REF_PROPERTIES, **_OWNERSHIP_PROPERTIES, "rationale": {"type": "string"},
     }, "required": ["id", "rationale"]}},
    {"name": "rig_job_reconcile",
     "annotations": {
         "readOnlyHint": False,
         "destructiveHint": True,
         "idempotentHint": False,
         "openWorldHint": False,
     },
     "description": "Report held ownership by default. Apply only provably dead unlaunched recovery; explicit adopt/release reconciles legacy queue claims with parent attestation. Never expires live or unknown execution.",
     "inputSchema": {"type": "object", "properties": {
         **_JOB_REF_PROPERTIES, "queue_id": {"type": "string"}, "apply": {"type": "boolean", "default": False},
         "action": {"type": "string", "enum": ["report", "adopt", "release"], "default": "report"},
         "owner_session": {"type": "string"}, "worker": {"type": "string"},
         "access": {"type": "string", "enum": ["read", "write"]},
         "files": {"type": "array", "items": {"type": "string"}},
         "rationale": {"type": "string"}, "completion": {"type": "object"},
     }}},
    {"name": "rig_job_recover_cancelled",
     "annotations": {
         "readOnlyHint": False,
         "destructiveHint": True,
         "idempotentHint": True,
         "openWorldHint": False,
     },
     "description": "Parent-only. Recover a cancelled native child after the original parent CLI is dead, using exact credentials or a validated credentials_path, plus Codex host evidence. Dry-run unless apply=true. Does not accept caller terminal=true attestation. Releases files without acceptance.",
     "inputSchema": {"type": "object", "properties": {
         **_JOB_REF_PROPERTIES, **_OWNERSHIP_PROPERTIES, "rationale": {"type": "string"},
         "apply": {"type": "boolean", "default": False},
         "credentials_path": {"type": "string", "description": "Validates the original owner's saved receipt without replacing the current recovery caller's identity."},
         "owner_session": {"type": "string", "description": "Replacement caller session for the recovery audit; defaults to the current caller, not the original receipt owner."},
     }, "required": ["id", "rationale"]}},
    {"name": "rig_job_recover_parent_write",
     "annotations": {
         "readOnlyHint": False,
         "destructiveHint": True,
         "idempotentHint": True,
         "openWorldHint": False,
     },
     "description": (
         "Parent-only Codex/Pi self-service recovery for a native parent write whose owning turn was "
         "explicitly cancelled/stopped and whose owner_token or job artifacts are unavailable. "
         "Binds to the exact current owner session/initiating identity on the reservation; does not "
         "require, reveal, or reconstruct owner_token. Repeated allow/approve is not completion. "
         "Stop/cancel the task first, then call with confirmed_stopped=true and a non-empty rationale. "
         "Marks that parent attempt cancelled/unverified, frees its slot, and releases files. "
         "Never accepts or verifies work. finish/close remain the authenticated completion path."
     ),
     "inputSchema": {"type": "object", "properties": {
         **_JOB_REF_PROPERTIES,
         "owner_session": {
             "type": "string",
             "description": "Optional consistency check against this current parent's session; cannot override caller identity.",
         },
         "confirmed_stopped": {
             "type": "boolean",
             "description": "Required true attestation that this parent task was already stopped/cancelled. Allow/approve is not completion.",
         },
         "rationale": {"type": "string"},
     }, "required": ["id", "confirmed_stopped", "rationale"]}},
    {"name": "rig_job_recover_wrapper_receipt",
     "annotations": {
         "readOnlyHint": True,
         "destructiveHint": False,
         "idempotentHint": True,
         "openWorldHint": False,
     },
     "description": (
         "Parent-only read-only recovery of a stopped wrapper's ownership receipt. "
         "Returns non-secret metadata including credentials_path. Rejects released scopes, "
         "active work, missing/malformed/insecure/mismatched artifacts, and non-wrapper executions. "
         "Never accepts, closes, releases, mutates a reservation, or invents a token."
     ),
     "inputSchema": {"type": "object", "properties": {**_JOB_REF_PROPERTIES}, "required": ["id"]}},
    {"name": "rig_job_break_glass_close",
     "annotations": {
         "readOnlyHint": False,
         "destructiveHint": True,
         "idempotentHint": True,
         "openWorldHint": False,
     },
     "description": (
         "Parent-only audited break-glass close of a confirmed-stopped failed or cancelled wrapper. "
         "Requires the canonical mode-0600 owner-credentials path, confirmed_stopped=true, and rationale. "
         "Validates current job/reservation/attempt binding, wrapper executor, stopped state, and no "
         "active/accepted verification. Atomically releases only that scope and writes redacted audit "
         "evidence. Idempotent. Does not accept raw owner tokens. Ordinary authenticated close is unchanged."
     ),
     "inputSchema": {"type": "object", "properties": {
         **_JOB_REF_PROPERTIES,
         "credentials_path": {"type": "string"},
         "confirmed_stopped": {"type": "boolean"},
         "rationale": {"type": "string"},
         "owner_session": {"type": "string"},
     }, "required": ["id", "credentials_path", "confirmed_stopped", "rationale"]}},
])
TOOLS.append({
    "name": "rig_task_timeline",
    "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
    "description": "Parent-only read-only timeline of bounded persisted workflow/job-attempt evidence. Does not refresh lifecycle or revalidate acceptance, read logs/credentials, or reconstruct missing history. Exactly one workflow_id or job_id is required.",
    "inputSchema": {"type": "object", "properties": {
        "repo": {"type": "string"}, "workflow_id": {"type": "string"}, "job_id": {"type": "string"},
        "attempt_id": {"type": "string", "description": "Optional exact job attempt. Omitted binds the persisted current attempt and returns that ID."},
        "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 100},
    }},
})

_WORKFLOW_ID = {"id": {"type": "string", "description": "Workflow id."}, "repo": {"type": "string"}}
_WORKFLOW_OWNER = {"owner_token": {"type": "string"}, "owner_session": {"type": "string"}}
TOOLS.extend([
    {"name": "rig_workflow_recipe_list",
     "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
     "description": "Parent-only. List the bundled versioned workflow recipes. Offline and read-only; never creates or launches work.",
     "inputSchema": {"type": "object", "properties": {"repo": {"type": "string"}}, "additionalProperties": False}},
    {"name": "rig_workflow_recipe_show",
     "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
     "description": "Parent-only. Inspect a built-in workflow recipe, parameter schema and hash. No provider calls or remote templates.",
     "inputSchema": {"type": "object", "properties": {"repo": {"type": "string"}, "name": {"type": "string"},
                     "version": {"type": "integer", "default": 1}}, "required": ["name"], "additionalProperties": False}},
    {"name": "rig_workflow_recipe_preview",
     "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
     "description": "Parent-only. Compile typed parameters into a reviewable existing workflow spec, recipe/spec hashes and node scopes/effects. Never creates, advances, launches, writes files, runs checks or probes providers. Contracts map explicitly to named nodes; UI validation stays parent-only.",
     "inputSchema": {"type": "object", "properties": {"repo": {"type": "string"}, "name": {"type": "string"},
                     "version": {"type": "integer", "default": 1}, "parameters": {"type": "object"}},
                     "required": ["name", "parameters"], "additionalProperties": False}},
    {"name": "rig_workflow_create",
     "annotations": {
         "readOnlyHint": False,
         "destructiveHint": False,
         "idempotentHint": False,
         "openWorldHint": False,
     },
     "description": "Parent-only. Create a repository-local adaptive workflow DAG. Accepts a spec object. Never prints owner tokens in text; structuredContent includes credentials_path.",
     "inputSchema": {"type": "object", "properties": {
         "repo": {"type": "string"}, "spec": {"type": "object"}, "queue_id": {"type": "string"},
         "owner_session": {"type": "string"},
     }, "required": ["spec"]}},
    {"name": "rig_workflows",
     "annotations": {
         "readOnlyHint": True,
         "destructiveHint": False,
         "idempotentHint": True,
         "openWorldHint": False,
     },
     "description": "Parent-only. List workflows with accepted/required counts, running/ASK counts, blocker, and next parent action. No ETA.",
     "inputSchema": {"type": "object", "properties": {"repo": {"type": "string"}, "include_terminal": {"type": "boolean"}}}},
    {"name": "rig_workflow_show",
     "annotations": {
         "readOnlyHint": True,
         "destructiveHint": False,
         "idempotentHint": True,
         "openWorldHint": False,
     },
     "description": "Parent-only. Show one workflow spec, node state, coordination, and next parent action. Sanitizes owner tokens.",
     "inputSchema": {"type": "object", "properties": {**_WORKFLOW_ID}, "required": ["id"]}},
    {"name": "rig_workflow_advance",
     "annotations": {
         "readOnlyHint": False,
         "destructiveHint": False,
         "idempotentHint": False,
         "openWorldHint": True,
     },
     "description": "Parent-only. Refresh then launch ready workflow nodes up to capacity. Refuses any repo ASK. Stops on cancel, unresolved failure, or coordination. parent_writes returns one registered parent action and launches no siblings that turn.",
     "inputSchema": {"type": "object", "properties": {**_WORKFLOW_ID, **_WORKFLOW_OWNER}, "required": ["id"]}},
    {"name": "rig_workflow_wait",
     "annotations": {
         "readOnlyHint": True,
         "destructiveHint": False,
         "idempotentHint": False,
         "openWorldHint": True,
     },
     "description": "Parent-only. Wait on a workflow. Pass its retained owner_token for host Stop to durably cancel this workflow; without credentials this is observation-only. EOF always preserves execution. Wakes COORDINATION and ASK. Shows sibling node status. Do not pass timeout unless you must cap the wait.",
     "inputSchema": {"type": "object", "properties": {**_WORKFLOW_ID, **_WORKFLOW_OWNER, "timeout": {"type": "number"}}}},
    {"name": "rig_workflow_extend",
     "annotations": {
         "readOnlyHint": False,
         "destructiveHint": False,
         "idempotentHint": False,
         "openWorldHint": False,
     },
     "description": "Parent-only. Append nodes or explicitly rebind context_packages for never-executed nodes after resolution/release. No scope changes, held attempts, launched-node changes, or extension after final verify launches.",
     "inputSchema": {"type": "object", "properties": {**_WORKFLOW_ID, **_WORKFLOW_OWNER, "nodes": {"type": "array", "items": {"type": "object"}}, "context_packages": {"type": "object", "additionalProperties": CONTEXT_REFERENCE_SCHEMA}}, "required": ["id"]}},
    {"name": "rig_workflow_resolve",
     "annotations": {
         "readOnlyHint": False,
         "destructiveHint": False,
         "idempotentHint": False,
         "openWorldHint": True,
     },
     "description": "Parent-only. Resolve a failed node: identical stopped/released retry, skip with rationale, or final failure. Required nodes cannot be silently waived. Accepted nodes cannot retry.",
     "inputSchema": {"type": "object", "properties": {**_WORKFLOW_ID, **_WORKFLOW_OWNER, "node_id": {"type": "string"}, "action": {"type": "string", "enum": ["retry", "skip", "fail"]}, "rationale": {"type": "string"}}, "required": ["id", "node_id"]}},
    {"name": "rig_workflow_approve",
     "annotations": {
         "readOnlyHint": False,
         "destructiveHint": False,
         "idempotentHint": True,
         "openWorldHint": True,
     },
     "description": "Parent-only. Approve a gated side-effect node. Bound to workflow+node+owner session+current spec hash; invalidated by spec change.",
     "inputSchema": {"type": "object", "properties": {**_WORKFLOW_ID, **_WORKFLOW_OWNER, "node_id": {"type": "string"}, "rationale": {"type": "string"}}, "required": ["id", "node_id", "rationale"]}},
    {"name": "rig_workflow_cancel",
     "annotations": {
         "readOnlyHint": False,
         "destructiveHint": True,
         "idempotentHint": True,
         "openWorldHint": True,
     },
     "description": "Parent-only. Freeze advancement, request cancellation only for active workflow attempts, cancel unstarted nodes. Unrelated jobs and queues stay. Confirmed-stop semantics unchanged.",
     "inputSchema": {"type": "object", "properties": {**_WORKFLOW_ID, **_WORKFLOW_OWNER, "rationale": {"type": "string"}}, "required": ["id"]}},
    {"name": "rig_workflow_report",
     "annotations": {
         "readOnlyHint": True,
         "destructiveHint": False,
         "idempotentHint": True,
         "openWorldHint": False,
     },
     "description": "Parent-only read-only report: observed wall time, node time, max concurrency, outcomes, acceptance. No estimated savings or ranking.",
     "inputSchema": {"type": "object", "properties": {**_WORKFLOW_ID}, "required": ["id"]}},
    {"name": "rig_job_coordination_reply",
     "annotations": {
         "readOnlyHint": False,
         "destructiveHint": False,
         "idempotentHint": False,
         "openWorldHint": True,
     },
     "description": "Parent-only. Reply to or stop a child coordination request. Coordination never expands files, resources, effects, or frozen contracts.",
     "inputSchema": {"type": "object", "properties": {**_WORKFLOW_ID, **_WORKFLOW_OWNER, "request_id": {"type": "string"}, "decision": {"type": "string", "enum": ["reply", "stop"]}, "text": {"type": "string"}}, "required": ["id", "request_id"]}},
    {"name": "rig_job_coordination_request",
     "annotations": {
         "readOnlyHint": False,
         "destructiveHint": False,
         "idempotentHint": False,
         "openWorldHint": True,
     },
     "description": "Child only after inbox handshake. Request parent coordination: dependency, contract, or scope. Never expands files, resources, effects, or frozen contracts.",
     "inputSchema": {"type": "object", "properties": {"kind": {"type": "string", "enum": ["dependency", "contract", "scope"]}, "text": {"type": "string"}, "payload": {"type": "object"}}, "required": ["kind", "text"]}},
    {"name": "rig_cu_status",
     "annotations": {
         "readOnlyHint": True,
         "destructiveHint": False,
         "idempotentHint": True,
         "openWorldHint": False,
     },
     "description": "Parent-only read-only Cua Driver readiness diagnostics. Always discoverable, even when action tools are hidden. Reports machine opt-in, binary, project flag and recovery steps. Does not enable, install or grant access.",
     "inputSchema": {"type": "object", "properties": {"repo": {"type": "string"}}}},
    {"name": "rig_cu_serve",
     "annotations": {
         "readOnlyHint": False,
         "destructiveHint": False,
         "idempotentHint": True,
         "openWorldHint": False,
     },
     "description": "Parent-only detached Cua Driver daemon start. Starts cua-driver serve in the background when gates are on and a display exists. Passes --grant existing-profile only after a remembered unlock grant. Never silent-grants OS permissions. Hidden unless computer-use is effective. Children never receive this tool.",
     "inputSchema": {"type": "object", "properties": {"repo": {"type": "string"}}}},
    {"name": "rig_cu_capture",
     "annotations": {
         "readOnlyHint": False,
         "destructiveHint": False,
         "idempotentHint": False,
         "openWorldHint": True,
     },
     "description": "Parent-only computer-use capture. Cua Driver get_window_state or named Chrome profile bind. Returns a concise summary, rig.cu.v1 receipt, and image content when the PNG is readable. Hidden unless computer-use is effective. Children never receive this tool.",
     "inputSchema": {"type": "object", "properties": {
         "repo": {"type": "string"}, "pid": {"type": "integer"}, "window_id": {"type": "integer"},
         "bundle_id": {"type": "string"}, "app_name": {"type": "string"},
         "profile_key": {"type": "string"}, "url": {"type": "string"},
     }}},
    {"name": "rig_cu_act",
     "annotations": {
         "readOnlyHint": False,
         "destructiveHint": False,
         "idempotentHint": False,
         "openWorldHint": True,
     },
     "description": "Parent-only computer-use act. One action on a fresh snapshot: element_token or browser ref from rig_cu_capture, or x,y after degraded/escalate_px. Consumes the snapshot until confirm. Returns receipt plus image when available. Children never receive this tool.",
     "inputSchema": {"type": "object", "properties": {
         "repo": {"type": "string"}, "snapshot_id": {"type": "string"},
         "element_token": {"type": "string"}, "ref": {"type": "string"},
         "action": {"type": "string", "enum": ["click", "type", "key"]},
         "text": {"type": "string"}, "key": {"type": "string"},
         "x": {"type": "number"}, "y": {"type": "number"},
     }, "required": ["snapshot_id"]}},
    {"name": "rig_cu_confirm",
     "annotations": {
         "readOnlyHint": False,
         "destructiveHint": False,
         "idempotentHint": False,
         "openWorldHint": True,
     },
     "description": "Parent-only computer-use recapture/confirm. Allowed only after a successful act. Yields a fresh successor snapshot. Confirm is the only confirmed outcome. Returns receipt plus image when available. Children never receive this tool.",
     "inputSchema": {"type": "object", "properties": {
         "repo": {"type": "string"}, "snapshot_id": {"type": "string"},
     }, "required": ["snapshot_id"]}},
    {"name": "rig_cu_record",
     "annotations": {
         "readOnlyHint": False,
         "destructiveHint": False,
         "idempotentHint": False,
         "openWorldHint": True,
     },
     "description": "Parent-only computer-use recording. Start/stop Cua Driver session video (recording.mp4) under .rig/cu-evidence. Structured/text only unless an image is actually returned. Do not shell cua-driver. Hidden unless computer-use is effective. Children never receive this tool.",
     "inputSchema": {"type": "object", "properties": {
         "repo": {"type": "string"},
         "action": {"type": "string", "enum": ["start", "stop"]},
         "output_dir": {"type": "string"},
         "record_video": {"type": "boolean"},
     }, "required": ["action"]}},
    {"name": "rig_bsk_status",
     "annotations": {
         "readOnlyHint": True,
         "destructiveHint": False,
         "idempotentHint": True,
         "openWorldHint": False,
     },
     "description": "Parent-only read-only BrowserSkill readiness diagnostics. Always discoverable, even when action tools are hidden. Reports machine opt-in, binary, project flag, and extension connection without changing them. Does not enable, install, or run bsk install-skill. Children never receive this tool.",
     "inputSchema": {"type": "object", "properties": {"repo": {"type": "string"}}}},
    {"name": "rig_bsk_session",
     "annotations": {
         "readOnlyHint": False,
         "destructiveHint": False,
         "idempotentHint": False,
         "openWorldHint": True,
     },
     "description": "Parent-only BrowserSkill session start/stop. bsk session start --json (optional --no-focus); retain session_id; stop with positional ID. Hidden unless browser-skill is effective. Never --unattended. Children never receive this tool.",
     "inputSchema": {"type": "object", "properties": {
         "repo": {"type": "string"},
         "action": {"type": "string", "enum": ["start", "stop"]},
         "no_focus": {"type": "boolean"},
     }}},
    {"name": "rig_bsk_observe",
     "annotations": {
         "readOnlyHint": False,
         "destructiveHint": False,
         "idempotentHint": False,
         "openWorldHint": True,
     },
     "description": "Parent-only BrowserSkill observe. bsk observe / screenshot with --session ID → snapshot plus @eN refs and optional PNG. Hidden unless browser-skill is effective. Children never receive this tool.",
     "inputSchema": {"type": "object", "properties": {"repo": {"type": "string"}}}},
    {"name": "rig_bsk_act",
     "annotations": {
         "readOnlyHint": False,
         "destructiveHint": False,
         "idempotentHint": False,
         "openWorldHint": True,
     },
     "description": "Parent-only BrowserSkill act. One click/fill/press on a fresh @eN ref from rig_bsk_observe. Maps to bsk click/fill/press with --session ID. Consumes the snapshot until confirm. Hidden unless browser-skill is effective. Never evaluate, upload, or download. Children never receive this tool.",
     "inputSchema": {"type": "object", "properties": {
         "repo": {"type": "string"}, "snapshot_id": {"type": "string"},
         "ref": {"type": "string"},
         "action": {"type": "string", "enum": ["click", "fill", "press"]},
         "text": {"type": "string"}, "key": {"type": "string"},
     }, "required": ["snapshot_id"]}},
    {"name": "rig_bsk_confirm",
     "annotations": {
         "readOnlyHint": False,
         "destructiveHint": False,
         "idempotentHint": False,
         "openWorldHint": True,
     },
     "description": "Parent-only BrowserSkill re-observe/confirm. Allowed only after a successful act. Confirm is the only confirmed outcome. Hidden unless browser-skill is effective. Children never receive this tool.",
     "inputSchema": {"type": "object", "properties": {
         "repo": {"type": "string"}, "snapshot_id": {"type": "string"},
     }, "required": ["snapshot_id"]}},
    {"name": "rig_bsk_navigate",
     "annotations": {
         "readOnlyHint": False,
         "destructiveHint": False,
         "idempotentHint": False,
         "openWorldHint": True,
     },
     "description": "Parent-only BrowserSkill navigate. bsk navigate URL --session ID. Hidden unless browser-skill is effective. Children never receive this tool.",
     "inputSchema": {"type": "object", "properties": {
         "repo": {"type": "string"},
         "url": {"type": "string"},
     }, "required": ["url"]}},
    {"name": "rig_bsk_tab",
     "annotations": {
         "readOnlyHint": False,
         "destructiveHint": False,
         "idempotentHint": False,
         "openWorldHint": True,
     },
     "description": "Parent-only BrowserSkill user-tab list/borrow/return. Explicit bsk tab list --scope user, tab borrow <id>, tab return <id> with --session ID. Never --unattended or --no-confirm. Hidden unless browser-skill is effective. Children never receive this tool.",
     "inputSchema": {"type": "object", "properties": {
         "repo": {"type": "string"},
         "action": {"type": "string", "enum": ["list", "borrow", "return"]},
         "tab_id": {"type": "string"},
     }}},
])
for _tool in TOOLS:
    _properties = _tool["inputSchema"]["properties"]
    if _tool["name"] in {"rig_job_start", "rig_job_launch", "rig_job_finish", "rig_job_requirements", "rig_job_check", "rig_job_criterion", "rig_job_accept", "rig_job_reconcile", "rig_job_recover_cancelled", "rig_queue_unclaim", "rig_queue_spawned"}:
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

TOOLS.extend([
    {"name": "rig_recovery_guide", "description": "Parent-only read-only recovery guidance from recorded evidence; never authorizes or executes repair.",
     "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
     "inputSchema": {"type": "object", "additionalProperties": False, "properties": {
         "repo": {"type": "string"}, "job_id": {"type": "string"}, "workflow_id": {"type": "string"}},
         "oneOf": [{"required": ["job_id"], "not": {"required": ["workflow_id"]}},
                   {"required": ["workflow_id"], "not": {"required": ["job_id"]}}]}},
    {"name": "rig_task_prepare", "description": "Parent-selected, validated task draft. Never discovers files, runs checks, builds context, or launches work.",
     "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
     "inputSchema": {"type": "object", "additionalProperties": False, "required": ["selection"],
         "properties": {"repo": {"type": "string"}, "selection": preparation_inputs.INPUT_SCHEMA}}},
])

TOOL_ORDER = (
    "rig_session",
    "rig_context_preview",
    "rig_context_build",
    "rig_job_wait",
    "rig_job_allow",
    "rig_job_deny",
    "rig_job_cancel",
    "rig_jobs",
    "rig_pick",
    "rig_status",
    "rig_doctor",
    "rig_cu_serve",
    "rig_cu_capture",
    "rig_cu_act",
    "rig_cu_confirm",
    "rig_cu_record",
    "rig_bsk_session",
    "rig_bsk_observe",
    "rig_bsk_act",
    "rig_bsk_confirm",
    "rig_bsk_navigate",
    "rig_bsk_tab",
    "rig_routing_report",
    "rig_billing_report",
    "rig_billing_import",
    "rig_billing_sync",
    "rig_benchmark_report",
    "rig_benchmark_create",
    "rig_benchmark_outcome",
    "rig_job_start",
    "rig_job_launch",
    "rig_job_show",
    "rig_job_log",
    "rig_job_finish",
    "rig_job_record",
    "rig_job_requirements",
    "rig_job_ui_evidence",
    "rig_job_check",
    "rig_job_criterion",
    "rig_job_close",
    "rig_job_reconcile",
    "rig_job_recover_cancelled",
    "rig_job_recover_parent_write",
    "rig_job_recover_wrapper_receipt",
    "rig_job_break_glass_close",
    "rig_job_accept",
    "rig_memory",
    "rig_memory_add",
    "rig_memory_replace",
    "rig_memory_remove",
    "rig_job_message",
    "rig_queue_add",
    "rig_queue_list",
    "rig_queue_cancel",
    "rig_queue_claim",
    "rig_queue_unclaim",
    "rig_queue_spawned",
    "rig_workflow_create",
    "rig_workflow_recipe_list",
    "rig_workflow_recipe_show",
    "rig_workflow_recipe_preview",
    "rig_workflows",
    "rig_workflow_show",
    "rig_workflow_advance",
    "rig_workflow_wait",
    "rig_workflow_extend",
    "rig_workflow_resolve",
    "rig_workflow_approve",
    "rig_workflow_cancel",
    "rig_workflow_report",
    "rig_task_timeline",
    "rig_job_coordination_reply",
    "rig_cu_status",
    "rig_bsk_status",
    "rig_recovery_guide",
    "rig_task_prepare",
)
CHILD_TOOL_ORDER = (
    "rig_job_doing",
    "rig_job_note",
    "rig_job_ask",
    "permission_prompt",
    "rig_job_inbox",
    "rig_job_show",
    "rig_memory",
    "rig_job_coordination_request",
)
PARENT_TOOL_NAMES = frozenset(TOOL_ORDER)
CHILD_TOOL_NAMES = frozenset(CHILD_TOOL_ORDER)
_TOOLS_BY_NAME = {t["name"]: t for t in TOOLS}
TOOLS = [_TOOLS_BY_NAME[n] for n in TOOL_ORDER if n in _TOOLS_BY_NAME] + [
    t for t in TOOLS if t["name"] not in TOOL_ORDER and t["name"] in PARENT_TOOL_NAMES
]
CHILD_TOOLS = [_TOOLS_BY_NAME[n] for n in CHILD_TOOL_ORDER if n in _TOOLS_BY_NAME]

__all__ = ['PICK_ROLES', 'JOB_WORKERS', 'JOB_RECORD_STATUSES', 'JOB_FINISH_STATUSES', 'REVIEW_PROPERTIES', 'TASK_DOMAIN_PROPERTIES', 'ASSESSMENT_LEVELS', 'RAW_ASSESSMENT_SCHEMA', 'ASSESSMENT_PROPERTIES', 'PARENT_METADATA_PROPERTIES', 'JOB_EXECUTION_PROPERTIES', 'ACCEPTANCE_CONTRACT_SCHEMA', 'TOOLS', 'CONTEXT_REFERENCE_SCHEMA', '_JOB_REF_PROPERTIES', '_OWNERSHIP_PROPERTIES', '_WORKFLOW_ID', '_WORKFLOW_OWNER', 'TOOL_ORDER', 'CHILD_TOOL_ORDER', 'PARENT_TOOL_NAMES', 'CHILD_TOOL_NAMES', '_TOOLS_BY_NAME', 'CHILD_TOOLS']
