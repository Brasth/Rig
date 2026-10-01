# Guided recovery

Recovery guidance explains recorded job or workflow evidence without changing lifecycle state, probing processes, executing commands, cancelling, retrying, releasing ownership or accepting work.

```sh
rig recovery guide --job JOB_ID --json
rig recovery guide --workflow WORKFLOW_ID --json
```

MCP: `rig_recovery_guide` with `repo` and exactly one of `job_id` or `workflow_id`. In `rig tui`, select a job/workflow and press **g**. The background projection keeps input responsive; **g** or **Esc** closes it, **Page Up/Down** scrolls, and **q** exits. Mutation keys are inactive while guidance is open. **r** still refreshes the board.

The projection lists recorded state, blockers, held files/resources/slot, evidence gaps, supported operations and their prerequisites. ASK points to answering the same attempt. Unconfirmed termination retains protection and requires actual executor completion. Interrupted verification requires supported scoped recovery. Failed execution/verification remains unverified. Recorded acceptance always requires checking current content freshness; review requires a different known actual provider.

Coverage is `recorded`, `partial` or `unknown`. Missing, malformed, oversized, linked or mismatched-attempt records are incomplete evidence. Historical acceptance is not proof of current content. The guide reads bounded records through the existing timeline reader and emits fixed advice, rather than instructions stored in artifacts. It omits credentials, logs, free-form rationales and private paths. No selector can grant ownership or bypass project/backend gates.

Inspect evidence and choose a supported operation deliberately with the required credentials. Explicit Stop/cancel never authorizes automatic waiting, retry or queue drain. Recovery guidance is available to parents only; children cannot list or call it.
