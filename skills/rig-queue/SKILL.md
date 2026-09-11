---
name: queue
description: >
  Park a Rig work item in .rig/queue/. Use when the user types /queue, wants to
  enqueue work while a child is running, or asks to list/cancel the work queue.
  Does not spawn a child. Does not wait. Does not allow/deny.
user-invocable: true
disable-model-invocation: true
argument-hint: "[text|cancel <id>]"
---

# Rig queue

Park user work in `.rig/queue/`. This is **not** a spawn. This is **not** ASK. This is **not** child inbox.

Works in Grok, Codex, OpenCode, OMP, Pi, and agy. Same files for every parent.

## Do

- If the user passed text (and it is not `cancel …`): add it.
- If they passed `cancel <id>`: cancel that pending item.
- If they passed nothing: list pending + `live/max`.
- MCP: `rig_queue_add` / `rig_queue_list` / `rig_queue_cancel`. Do not shell `rig queue` when those tools are listed.
- Bash fallback if MCP is missing: `rig queue add "…"`, `rig queue list`, `rig queue cancel <id>`.
- Print the id (on add), then the QUEUE block. Stop.

## Do not

- Do not spawn a worker.
- Do not call `rig_job_wait`, allow, or deny.
- Do not encode `/queue` into `rig pick --case`.
- Do not spawn from `/queue` itself. Parking is this command. Drain is the parent on a **free** turn (claim → brief → spawn → wait all live ids).
- On Grok, a UserPromptSubmit hook parks `/queue text` and blocks the prompt (disk even mid-wait). Bare `/queue` still needs a free turn to list. Other parent CLIs during a wait: MCP `rig_queue_add`, or human `rig tui` `e`.

MCP drain (not this `/queue` command): `rig_queue_list` → `rig_queue_claim` with `id` + files → brief → `run-worker.sh` → `rig_queue_spawned` → MCP `rig_job_wait` on all live ids.

List shows occupied files. Claim **by id** when more than one item is pending (omit id only if exactly one pending). Drain is **not** this command — see `skills/delegate-harness/SKILL.md` Queue drain: skip overlap, try the next id, never FIFO-fill conflicting writers.

After park, if this turn is free, live jobs < cap, and the item is implement-like: parent checks, claims **that id** with listed files, briefs, spawns (`RIG_JOB_FILES=...`), `rig_queue_spawned`, then MCP `rig_job_wait` on **all** live ids. Stay/advise: answer here, do not spawn. `parent_writes`: do not drain more writers this turn.

Bash fallback if MCP is missing:

```bash
rig queue add "fix pagination on the jobs list"
rig queue list
rig queue cancel <id>
rig queue claim --files src/a.py src/b.py <id>
```
