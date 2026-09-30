# Local runtime evidence and benchmarks

Rig's reports are read-only local evidence. They do not contact providers, upload
telemetry, change routing preferences, or participate in admission, permission,
Stop/Resume, or parent-acceptance decisions. No model leaderboard or inferred
prices/savings are produced. Keep the existing receipt-backed billing gates in
[cost accounting](cost-accounting.md).

## Recorded reports

```sh
rig routing report --days 30 --json
rig workflow report WORKFLOW_ID --json
```

The routing report retains its existing fields and adds `definitions`, `domains`
and `execution_latency` distributions. Domain slices use validated recorded domain
evidence; absent/legacy evidence is `unknown`, never inferred retrospectively.
Every slice separates execution failures, cancellations, and current parent
acceptance. Acceptance's denominator is explicitly assessed attempts, not all
attempts. Domain slices pool their recorded attempts and are descriptive, not
controlled model comparisons.

Execution latency is terminal `ok`/`fail`/`timeout` execution wall time, excluding
`not_started` and cancelled attempts. It includes elapsed waiting inside that
execution; it is not provider-response latency. Reports include eligible/sample/
missing counts and coverage, min/max, median (p50), and nearest-rank p90/p95.
Malformed, negative, nonfinite and reversed timestamps are omitted. Live/open
attempts are not completed latency samples. Small samples stay visible and do
not establish model quality or a reliable population percentile.

Token coverage is both per job and per component. A job reporting only input
usage has known input and unknown output/total; a reported zero stays zero.
Missing totals are never reconstructed from partial components. Dollars require
actual billing evidence and are outside these runtime metrics.

Workflow `runtime_metrics` adds:

- `workflow_retry_count`: recorded explicit `resolve retry` events, including a
  retry approved but not yet launched. Automatic never-started repicks are not
  these explicit retries
- `continuation_count`: loaded attempt records with `continues_job_id`, counted
  separately. A retry is not assumed to be a continuation
- Historical/current attempt coverage, cancellation counts, token coverage and
  terminal execution latency. Missing/pruned job files stay missing, and a
  replaced attempt cannot borrow the new attempt's timing or tokens
- `blocked_time`: recorded ASK, coordination and execution-unconfirmed intervals

A workflow report no longer calls lifecycle refresh. It reads recorded state,
including the durable Stop-intent projection, without modifying state, creating
timing events, marking acceptance, or releasing ownership. `report_freshness`
explains this; normal workflow advance/wait performs lifecycle reconciliation.
The existing `node_times`, outcomes and concurrency fields are retained.

## Blocked-time scope and coverage

Forward transition evidence is additive in existing workflow `events/` files.
`runtime-state` records content-free snapshots when supported state changes are
saved; `runtime-ask` records every new ASK/reply with job, attempt, and ASK IDs.
Pending ASK replacements retain their predecessor identity even when timestamps
share one second or event writes arrive out of order. Prompt contents, tool
inputs, credentials and replies are not copied into timing
evidence. Coordination requests retain their originating attempt ID.

`observed_wall_s` is the interval union across recorded supported causes and jobs.
Overlapping jobs never double-count workflow wall time. Each `by_cause` value is
also a union; cause totals may overlap and must not be added together. ASK ends
at its matching reply, replacement prompt, or recorded terminal attempt/workflow
endpoint. Open intervals are reported through the read's current time, with
`open_interval_count`. Unconfirmed execution remains unresolved until the
existing lifecycle actually records a transition; metrics cannot confirm stop.

Coverage has three values:

- `recorded`: supported evidence was recorded from workflow creation, with no
  detected transition gap or malformed timestamps. Zero is explicitly marked
  `known_zero`, within this supported observed scope only
- `partial`: forward observations exist, but the workflow is legacy, transition
  revisions have gaps, timing is malformed, or a persistence gap was recorded
- `unknown`: there is no usable forward history. A legacy latest `ask.json`
  cannot reconstruct previously answered/replaced prompts

`complete_wall_s` remains null. Approval, admission/capacity, queue, provider,
network, and other waits are not instrumented. Coordination/unconfirmed timing
is observed at existing state writes, so it is not an exact continuously sampled
measurement. Reporting never adds observations. Missing job endpoints or reversed
clocks degrade coverage rather than producing negative durations. Metrics I/O
failure or admission-lock contention cannot change permission or lifecycle
results; timing takes the lock nonblockingly and detectable gaps remain
partial. As with all local evidence, an unreadable or externally removed history
cannot be recovered by a report.

## Opt-in offline performance benchmark

The synthetic benchmark is separate from production runtime instrumentation:

```sh
python3 tests/benchmark_workflows.py \
  --surfaces session,workflows,status,jobs,routing,startup \
  --history-sizes 0,100,1000 --warmup 2 --samples 11 \
  --output /tmp/rig-runtime.json
```

The default command still measures only session and workflow-list surfaces.
Expanded surfaces measure local status, job listing, a fixed smart-routing
decision, and a fresh guarded Python/MCP initialization plus EOF teardown. The
fixture uses temporary synthetic histories, isolated environments and process/
network guards. No vendor process, live provider request, package install, or
telemetry is used. Fixtures and guard setup are outside the timed regions.

JSON records revision/source hashes, working-tree state, Python/OS, exact
parameters, warmup/sample counts, raw latency samples, median/p95/min/max and
UTF-8 payload byte sizes. In-process outer JSON serialization is outside timing;
startup includes its initialize response and process teardown. The startup
interpreter is fresh each sample, but filesystem/OS caches are not flushed.
It does not measure `bin/rig` shell startup, installed-runtime leases, worker or
provider startup, network/model latency, concurrent load, or end-to-end UI time.
Routing uses fixed eligibility and empty catalogs; it is not a catalog-discovery
benchmark. The JSON lists all these coverage limits.

To compare, pass `--compare /path/to/previous.json` for a deliberately recorded
baseline. A missing scenario has absolute measurements only. Keep environments,
fixtures and source provenance comparable before interpreting differences;
shared-host contention can distort results. There is no invented baseline or
savings claim, and this benchmark never changes runtime policy automatically.

## Accepted-outcome metrics

Routing and workflow reports add `outcome_metrics` without changing existing
fields. Attempt latency starts at the exact admission reservation `created_at`
and ends at the earliest bound parent acceptance. This includes post-admission
work and verification, but excludes earlier parent thinking and queue time.
Workflow latency starts at creation and ends at the first recorded verified
transition. Explicit continuation chains start at root admission and end at their
first recorded acceptance; later linked corrections are counted separately.

Distributions include eligible/measured/missing/pending counts and p50/p90/p95.
Historical first acceptance is independent of current content freshness: later
source changes do not erase it, and metrics do not revalidate artifacts.
First pass means an initial accepted attempt with complete bound local history,
without earlier failed checks, failed criteria, rejection, retry or continuation.
`first_pass.rate` uses known assessed outcomes only; unknown coverage is explicit.
New verification writes bind pending/failed history and all check records to their
attempt. Legacy incomplete records remain unknown; they are never backfilled.

These are local observed outcomes, not provider latency, model rankings or proof
of defect-free code. Reports perform no lifecycle refresh or writes. Malformed,
missing, oversized and linked evidence remains unknown. Replaced attempts cannot
borrow timing from their successors. Chain traversal is bounded and cycles fail
closed. Preserve recorded history when retaining timing evidence.
