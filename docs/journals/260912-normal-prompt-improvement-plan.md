---
date: 2026-09-12
status: planning
---

# Normal prompt improvement decisions

## Context

The user requested a five-phase improvement plan. This session produced planning artifacts; no runtime implementation. Evidence-backed plan review resolved routing, acceptance, ownership, and HUD contract gaps.

## What happened

Source inspection found exit-zero and dry-run execution both become `ok`; neither establishes verified completion. Admission checks precede metadata registration, exposing concurrent launch races. Existing validation passed 118 tests across four focused suites. These results establish a baseline, not new behavior or measured latency gains.

## Reflection

The main reliability boundary is ownership across execution and acceptance. Process status, file ownership, and verification evidence need separate lifecycles. Native jobs and legacy claims lack sufficient provenance for automatic recovery based only on elapsed time.

## Decisions

Preserve legacy execution statuses. Plan short repository-local flock transactions with durable reservations; release execution slots separately from file ownership through parent verification and review. Preserve explicit parent intent and fix fallback collisions. Correct the cheap documentation route: shipped Codex mini uses a read-only explorer, so document edits require a write-capable role.

## Next

When implementation is requested, maintainers should benchmark harness overhead separately from inference, validate native owner identity, and test concurrent admission and continuous ownership before rollout. Preserve live/ASK work during recovery. No speedup percentage is promised. The detailed plan is at `plans/260912-normal-prompt-improvements/plan.md`.
