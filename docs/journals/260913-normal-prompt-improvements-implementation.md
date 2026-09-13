---
date: 2026-09-13
status: completed
---

# Normal prompt implementation decisions

## Context

Implemented the authorized five-stage Rig plan in one shared checkout, with bounded file ownership and review between dependent stages. This journal records implementation and validation, not installation or shipping. The earlier planning journal remains unchanged.

## What changed

The parent now supplies semantic intent explicitly. Bounded English fallback avoids question, preview/reviewer, and tiny-security collisions. Codex mini uses the shipped write-capable worker. Parent model identity remains observed or unknown; routing suggestions do not claim a model switch.

One session snapshot replaces repeated history reads; waited IDs are resolved once and remain pinned for wait/cancel. Full output stays compatible, while the normal protocol explicitly requests compact output. Phase 2 measured 54 local scenarios with stable one-scan session and K-load exact-wait gates. The final benchmark confirms those structural contracts with the completed evidence and presentation readers.

Execution, file ownership, and parent verification have separate lifetimes. Checks use an authoritative requirement manifest, exact commands, real output, and before/after content snapshots. Acceptance is tied to current content. A failed required check remains failed even if a different check passes later; only a successful retry of that same requirement supersedes it. Independent review requires current writer acceptance and a different known actual model provider.

Admission uses one fail-closed local flock, explicit attempt credentials, canonical read/write scopes, and gated child launch. Stopped execution frees a slot while files remain protected through assessment and reviewer handoff. Failed reviewer launch retains scope; fresh retry rotates credentials. Cancellation never silently retries work. Reconciliation distinguishes observed process death from unknown native ownership and requires authenticated confirmation for interrupted checks.

## Validation and review

Phases 1–4 passed review. Phase 4 passed 511 integrated tests plus one final dry-run interruption regression; current phase-boundary discovery had 512 cases. Actual subprocess races covered wrappers/native/MCP/queue, scope and cap conflicts, cancellation, launch/persistence failures, and interrupted recovery. Process identity checks required approved process visibility outside the sandbox; denial remained conservative.

Dry-run registration now uses fresh IDs under the admission transaction and cannot overwrite live attempts. Deterministic worker fixtures and temporary Git repositories exercised real CLI/MCP framing and lifecycle artifacts without pretending to measure model quality. Temporary install/setup tests preserved configuration, memory, unrelated AGENTS content, and overrides.

Final [integrated validation](../../plans/260912-normal-prompt-improvements/reports/phase-05-test-results.md) passed **535 tests in 97.897 seconds**, with zero failures/errors/skips. Bash/system Bash, Python, Node, and diff checks passed. Three existing unclosed-stream ResourceWarnings remain recorded. [Final source review](../../plans/260912-normal-prompt-improvements/reports/final-review.md) passed after the required-check summary correction and its regression test.

The final [local benchmark](../../plans/260912-normal-prompt-improvements/reports/final-local-overhead.md) passed all structural/hash/catalog/routing gates across **81 scenarios and 4,050 measured samples**, with matching production source hashes. At 1,000 static-catalog jobs, full JSON session median fell from 344.975 to 106.690 ms; exact three-job wait fell from 250.388 to 0.382 ms. Compact JSON emitted 237,478 bytes versus full 2,913,723 bytes. Accepted-history HUD hashes only the selected subject; foreground ASK and expired history hash none. These measurements cover local reader overhead, excluding inference and vendor latency.

## Limits and next steps

Local advisory coordination cannot control unrelated editors; their changes invalidate acceptance. Unknown ownership never expires by age. The source native worker is write-capable, but the inspected local installed worker override is absent: actual machine refresh remains rollout work. No actual machine installation, staging, commit, push, or release occurred.

All five implementation phases and 24 acceptance criteria are complete. The user explicitly approved local finalization and retained ownership of Git operations. [Rollout validation](../../plans/260912-normal-prompt-improvements/reports/rollout-validation.md) records the future installation and rollback procedure: quiesce, drain/reconcile, update every launcher, and restart parent/MCP sessions. Mixed-version admission is unsupported.

The five [workflow artifacts](../../plans/260912-normal-prompt-improvements/reports/harness/) are complete. Their [validator result](../../plans/260912-normal-prompt-improvements/reports/harness/validator-result.json) passes with no errors or warnings after recording human approval. Local workflow finalization is complete; Git operations remain with the user.

Unresolved questions: none. Human-approved local finalization complete.
