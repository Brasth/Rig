---
name: delegate-harness
description: >
  Activate only when `.rig/harness.toml` exists and the Rig project is enabled
  (not `[project] enabled=false`). Global installation alone does not activate Rig.
  MUST run MCP `rig_session`
  (or `rig_pick`) and spawn a Rig worker for code, review, fix, SSH, remote
  debug, or gather in the codebase unless pick parent_writes is true. Parent
  checks first and prepares brief TEXT for MCP launch. Delegate implement, review, explore, or
  split work across Codex, Grok, Claude, Cursor, OpenCode, OMP, Pi, agy, and Devin
  via MCP `rig_job_launch` (shell run-worker is fallback).
user-invocable: true
---

<!-- Generated from docs/agent-protocol.md; run scripts/generate_protocol.py. Do not edit. -->
This managed protocol applies only in an initialized, enabled Rig project. If `.rig/harness.toml` is missing or `[project] enabled=false`, use the host instructions instead; do not initialize or enable Rig implicitly. Existing legacy harnesses without `[project]` remain enabled. Global skill installation is not project or backend opt-in.

# Delegate harness

Read this bootstrap before any Rig action. These are hard gates, not optional hints. References below are mandatory before the named action; resolve them relative to this SKILL.md. If required guidance is missing or unreadable, stop and report the incomplete install. Do not improvise a replacement protocol.

## Always enforce

- Parent owns the graph, briefs, and acceptance. `RIG_JOB_ID` means child: first `rig_job_inbox`; fail `child MCP handshake missing` rather than claiming success without it. Child tools are only inbox/doing/note/ask/own show/project memory/coordination request. Children never spawn or message children, orchestrate, use the `rig` CLI, or receive vision, Figma, browser/computer-use tools. Cursor's non-Rig MCP tripwire stays enforced.
- Parent checks first and names files/change. `stay` keeps ask/plan/advise/vision/computer-use; docs/skills-only uses mini. Follow pick JSON for model/effort and worker eligibility; never bypass disabled flags, live-parent exclusion, exact catalog selectors, provider review gates, or stale routing evidence. Never Sol/Astra/Fable children. `parent_writes` is this parent, never a second same-CLI session.
- Orchestrate with MCP when listed. Before any edit, register actual worker/model/effort, concrete files, access, executor and ownership through `rig_job_start` or `rig_job_launch`. Prepare brief TEXT; launch creates the job directory. Retain exact returned reservation/attempt/token/session and credentials_path privately. A job ID or routing suggestion never authorizes ownership.
- One writer owns its scope through stopped execution and current parent acceptance. File AND resource disjointness and queue/worker caps are authoritative. ASK never justifies killing/replacing the child; answer allow/deny under the permission rules. No extra writers while parent_writes occupies the turn.
- Explicit Stop/cancel ends observation promptly, records durable intent where authenticated, and never triggers automatic re-wait, re-pick, queue drain, retry, or resume. EOF/transport loss alone preserves workers. stop-requested/stop-unconfirmed/native-cancel-required are not stopped: retain scope until real completion evidence. Never manufacture native completion or signal the parent as a substitute for its child.
- Child exit zero is completed-unverified. Declare every requirement; run deliberate exact checks; accept the current snapshot with ownership credentials. Changed content invalidates acceptance. Review requires a fresh read-only attempt and different known actual model provider. Failed/cancelled/rejected work stays unverified; explicit authenticated close requires confirmed stop. Never delete reservations or age-expire live/ASK/unknown ownership.
- Generic computer-use requests do not select Rig. Apply project/MCP/backend opt-in gates before selecting it. Browser/computer work stays parent-only with grants, fresh observations and no bypass; denial never permits switching tools. No implicit setup or enabling.

## Required reading before action

Read all references that apply, before the first action in that category:

- Every parent Rig operation (including session/pick): [orchestration](references/orchestration.md)
- Routing, session/pick, model/domain selection or any admission: [routing](references/routing.md)
- Every job operation, including start/launch/finish, editing, declaring requirements, running checks/verification, acceptance, independent review, close/reconcile and ownership recovery: [ownership and acceptance](references/ownership.md) and [launching](references/launching.md)
- Waiting, answering ASK, Stop/cancel, continuing/retrying or handling a failure: [waiting and recovery](references/waiting.md) and [ownership and acceptance](references/ownership.md)
- Every workflow operation, including create/show/report/advance/wait/cancel/extend/resolve/approve and coordination: [workflows](references/workflows.md), [ownership and acceptance](references/ownership.md), and [waiting and recovery](references/waiting.md)
- Every queue operation, including add/list/claim/unclaim/spawned/cancel, launch and drain: [queue](references/queue.md) and [ownership and acceptance](references/ownership.md)
- Choosing a browser/computer-use backend or any backend operation, including status/setup: [computer-use](references/computer-use.md)
- CLI fallback, provider launch details, permission handling: [provider commands](references/providers.md), [launching](references/launching.md), and [waiting and recovery](references/waiting.md)
- After a run, memory, installation upgrade/rollout/rollback: [maintenance](references/maintenance.md)
- Child before its first action: read [launching](references/launching.md) for its brief/handshake/scope contract; parent-only instructions never grant child authority
