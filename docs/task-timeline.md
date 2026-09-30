# Recorded task timeline

Parent MCP: `rig_task_timeline` with exactly one `workflow_id` or `job_id`.
Human CLI:

```sh
rig timeline --workflow WORKFLOW_ID --json
rig timeline --job JOB_ID --json
rig timeline --job JOB_ID --attempt ATTEMPT_ID --limit 200 --json
```

`limit` defaults to 100 and supports 1–200 entries. Omitted job attempts bind to
the persisted current attempt and return that selected ID. Supply the returned
ID on later reads when following one attempt. An unavailable old attempt is
reported as partial/unavailable; it never borrows the replacement job's records.

## What the timeline shows

The projection joins recorded workflow events with only the exact job attempts
referenced by those events or saved node bindings. Sources include:

- Durable workflow event sequence, including recorded ASK/coordination events
- Attempt-bound checks, with recorded status, timestamps and exit code
- Immutable criterion-assertion history, clearly labeled parent assertions
- Recorded acceptance/rejection history, with content/contract hashes when present
- Matching persisted Stop intent, which does not prove execution terminated

Each row retains its source-relative path, SHA-256 of the source bytes read,
source-local sequence/index, and known job/attempt IDs. Referenced evidence files
are listed as **not revalidated**; screenshot/log bytes are not read. An earlier
acceptance record does not establish current acceptance after content changes.
The timeline does not inspect current source content or execute checks.

No prompts, command arguments, private rationales, receipt payloads, raw logs,
credentials or inferred failure causes are returned. Conservative filtering
omits sensitive-looking identifiers/references and records a coverage issue
without echoing their values. This is not a universal secret detector.

## Ordering and coverage

Workflow and check lanes preserve recorded sequence. Acceptance lanes preserve
stored history order. Assertion receipts have no durable sequence, so their
explicit timestamps and stable assertion IDs determine display order; no causal
order or clock-reversal conclusion is inferred for those lanes.

The display merges timestamped lane heads deterministically while preserving
source-local order. A recorded clock reversal is flagged instead of reordering
the affected source. Untimed/malformed-time records are returned separately;
file modification time is never used to invent an event timestamp. Timestamps,
including equal timestamps, do not prove causality. When the output limit is
reached, oldest display entries are omitted and the exact omission count is
returned.

Coverage is `recorded`, `partial` or `unknown`. Missing sources/attempts, legacy
unbound records, malformed data, event/revision gaps, source changes during a
read, unsafe paths, privacy filtering and input/output limits are explicit.
`recorded` means the supported records were read; it never promises a complete
task history. Current workflow status is not synthesized into past transitions.
Standalone latest-only ASK/inbox files cannot reconstruct earlier messages and
are not read as history.

## Read-only limits

The timeline performs no workflow refresh, admission operation, lifecycle
transition, process inspection, snapshot generation, repair or write. It does
not call `workflow.show`, which refreshes state. The implementation uses bounded
reads of the same persisted sources as read-only workflow reporting, rather than
invoking an unbounded reader or credential-dependent cancellation helper.
Disabled/uninitialized projects stay unchanged. The surface is parent-only.

Limits: 256 KiB per source, 2 MiB total input, 1,024 source files, 64 matched job
attempts, and 512 directory entries per scoped evidence directory. An oversized
directory is refused as a whole rather than selecting an arbitrary filesystem
prefix. Sources must be regular non-hardlinked files; symlink paths and special
files are refused. All reads are confined to the selected workflow and matched
job directories under this repository's `.rig` data.
