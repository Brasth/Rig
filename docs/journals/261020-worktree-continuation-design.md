# Design note: continuing a job in worktree mode

Status: accepted. Closes the Phase 3 deviation "continuations are refused in worktree mode".

## Problem

A continuation (`continues_job_id`) resumes a confirmed-stopped predecessor: same worker, same owner, equal or narrower file scope, and a native session resume where the CLI supports it. In `[worktrees] mode = "per-job"` the predecessor's edits live on its branch in its own worktree, unmerged until the parent accepts. A successor given a fresh worktree from the base `HEAD` would start without those edits, and a native resume (`claude --resume`, `codex exec resume`) would wake up in a different directory from the one the session remembers. Phase 3 refused the case rather than lose work.

## Decision

**A continuation shares its predecessor's worktree and branch.** No new checkout, no new branch, no rebase.

1. **Plan.** `worktrees.plan(..., continues=P)` for an isolated write job returns P's worktree path and branch when P has a worktree record that is neither merged nor removed and whose directory exists. It refuses, with the reason, when P edited the base checkout (no record: the work is uncommitted in the base and a worktree cannot see it), when P's worktree was removed without a merge (the work is gone), or when the record is unreadable. When P was already merged (or had nothing to merge), the successor gets a fresh worktree from the current `HEAD`, which already contains P's work; nothing is shared.
2. **Create.** The successor's `worktree.json` copies P's `path`, `branch`, `base_branch`, `base_head` and `common_dir`, and adds `continues: P` and `worktree_job: R` where R is the job that created the worktree (the root of the chain). P's record gains `continued_by: [S]`. Rig checks that the worktree is still on the branch before handing it over. The worker's cwd is the same directory the predecessor used, so a native session resume sees its files.
3. **Commit.** Unchanged: when the successor stops, everything in the worktree is committed on the shared branch.
4. **Gate and merge.** The gate reads the whole branch (`base_head..branch_head`), so a successor's merge includes the predecessor's edits. The scope check uses the union of the declared scopes of every job in the chain (admission only lets a successor be equal or narrower, and each ancestor's edits were admitted under that ancestor's scope). Accepting **any** job in the chain merges the branch once and marks every record in the chain `merged` with the same `merge_commit`; the gate result names the chain. The evidence the parent accepts is a snapshot of the shared worktree, so it already shows everything the merge carries. A gate is refused (`merge_gate:worktree_busy`) while a process is running in the worktree, so accepting a predecessor cannot commit a continuation's half-done work; admission already keeps two jobs of a chain from running at once.
5. **Remove.** `rig worktrees remove --job X` resolves the worktree through `worktree_job`, so any job in the chain can name it, and the same checks apply (no live process, no uncommitted change, an unmerged branch needs `--discard`). Removing marks every record in the chain `removed`.
6. **Containers.** With `container = "per-job"`, dc-cli keys the container by workspace folder, so the successor reuses the predecessor's container (`dc up` on the same folder starts the existing one). Nothing to add.

## What it does not do

- It does not continue across a merge: once P is merged, a successor is an ordinary fresh job on the new base.
- It does not let two live jobs share a worktree. Admission already requires the predecessor to be confirmed stopped, and a predecessor with a live continuation cannot be continued again.
- Accepting a predecessor after a continuation ran merges the continuation's commits too; there is one branch. A parent that wants only part of it rejects and starts over.
- Lineage, the cap of three, owner and worker checks stay in admission unchanged.

## Tests

Plan: shared path for an unmerged predecessor; refusal for a base-checkout predecessor and a removed one; fresh worktree after a merge. Create: records on both sides, branch check. Gate: union scope admits an ancestor's edit outside the successor's narrower scope; a busy worktree is refused; merging the successor marks the chain. Remove through the successor. End to end through `run-worker.sh`: a stand-in worker edits in job one, a continuation edits the same worktree with a native resume, the successor is accepted and both edits land on the base.
