# Design note: worktree isolation and the merge gate (Phase 3.0)

Status: accepted for Phase 3. This is the note the plan requires before the admission change (3.2).

## Problem

Admission keeps two writers from touching the same files by reserving file scopes in `.rig/reservations`. That is the right guard when every writer edits the one checkout, and it is also the limit on parallelism: two nodes that legitimately touch the same file cannot run at once, and a writer with no listed files is exclusive with everything.

Git already solves "two people edit the same file" with branches and a merge. Phase 3 lets each writer job edit its own checkout (a git worktree on its own branch) and moves the overlap check from launch time to the point where work is accepted into the base.

## Decisions

1. **Opt-in, per repository.** `[worktrees] mode = "off" | "per-job"`, default `off`. With `off` nothing in this note applies and no byte of existing behavior changes. `[worktrees] container = "shared" | "per-job"` (default `shared`) chooses whether jobs share the workspace container or get their own.
2. **Two roots.** The *state root* (the main checkout's `.rig`: jobs, reservations, queue, workflows, ownership) never moves. A job in worktree mode additionally has a *work root*: the checkout its worker edits. Everything that reads or snapshots files (change evidence, scope normalization, acceptance checks, the worker's cwd) uses the work root; everything that reads or writes Rig state uses the state root. The job directory records the split in `worktree.json`; no worktree record means the work root is the state root, which is today's behavior.
3. **Location.** `<repo>/.rig/worktrees/<job-id>` on branch `rig/<job-id>`, created from the base `HEAD` at launch. Inside the repository directory, so the same-path invariant of container mode holds with no extra mount, and the path is excluded through `.git/info/exclude` (never `.gitignore`, so no tracked file changes). Rig refuses when the repository has no commit, when `.rig/worktrees/<job-id>` exists, or when the branch exists.
4. **Who may use it.** Only write-access jobs launched through `run-worker.sh`. Read and review jobs keep the state root. A parent that writes directly (`parent_writes`) is unchanged.
5. **Committing.** Workers do not commit. When a worktree job ends, Rig commits everything in the work root to the job branch as `rig <rig@localhost>` so the merge gate has a commit to merge. Uncommitted work in a worktree is never merged.
6. **Admission.** In `_capacity`, a file-scope overlap between two reservations is admitted when **both** are worktree-isolated. An isolated writer with no listed files is also admitted beside other isolated writers. A non-isolated writer stays exclusive with everyone, as today, and an isolated writer is exclusive with a non-isolated one (they would edit the same checkout). A reader of the base checkout never conflicts with an isolated writer, because the writer's edits are not in that checkout; a reviewer of an isolated writer's job reads that writer's worktree instead (until it is merged). Resource conflicts (ports, URLs, browsers) and execution-slot limits apply unchanged because worktrees do not isolate them. Isolation is recorded on the reservation at reserve time and cannot change afterwards.
7. **The merge gate.** `rig_job_accept` (decision `accept`) on a worktree job first checks, in order, and refuses with the failing reason:
   - the job branch changes something. A branch with no changes is accepted with nothing to merge (the job's worktree and branch are removed), so a writer that finds nothing to change is not blocked;
   - the branch changes no file outside the job's declared scope (scope is what admission no longer guards; an isolated job with no declared scope skips this check and says so);
   - the branch merges onto the **current** base head without conflict, checked with `git merge-tree --write-tree`, which touches no working tree;
   - the base checkout has no uncommitted change in a file the merge would touch.

   When all hold, Rig merges the branch into the base checkout with `git merge --no-ff`, records `merge_commit` in `worktree.json`, and only then writes the acceptance. A repeated identical accept returns the stored result without merging again. A `reject` merges nothing. A refused gate leaves the job, its worktree and its reservation untouched, so the parent can resolve and accept again.
8. **Cleanup.** After a merge, Rig removes the worktree and deletes the merged branch. A rejected, cancelled or unmerged job keeps its worktree for inspection until `rig worktrees remove JOB`, which refuses while a process the job started is alive. Rig never removes a worktree it did not create (`worktree.json` is the proof) and never uses force.
9. **Container per worktree.** With `container = "per-job"`, the job's container is `dc up <worktree>`, found and stopped through the same dc-cli workspace argument (`dc exec --no-start <worktree>`, `dc down <worktree>`). dc-cli keys containers by workspace folder, so no naming flag is needed. The worktree contains the tracked `.devcontainer`, whose config must also mount the repository's git directory at its own path or git fails inside the container; the probe checks `git -C <worktree> rev-parse` and says so. Rig's own launcher (`run-worker.sh`, started by the parent) runs `dc up <worktree>` after creating the worktree and before the probe; a worker agent can never start a container, as the Phase 2 rules require. dc-cli leaves a stopped container behind when a job ends; Rig never removes containers, so cleaning them is `dc prune` by the owner.

## What this does not do

- It does not make two workers on the same file produce a merged result by themselves. Two jobs that edit the same lines conflict at the gate, and the parent resolves that. The gain is that they ran at the same time and the conflict is detected, not prevented.
- It does not isolate ports, browsers or databases. Those stay governed by resource reservations.
- It does not rebase. The gate merges onto whatever the base is now; a stale job either merges cleanly or is refused with the conflicting paths.

## Failure cases the tests must cover

- Branch or path collision; no commits; repository in detached HEAD (merge target is the recorded base branch, refuse when it is gone).
- Overlap admitted for two isolated writers, refused against a non-isolated one, refused for a resource clash.
- Gate: nothing to merge, out-of-scope file, conflict (with the conflicting paths named), dirty base file, clean merge, repeated accept.
- Cancel while a worktree job runs: worktree kept, no merge, reservation recovery unchanged.
- Cleanup refuses a live process and a worktree Rig did not create.
- `mode = "off"`: dry-run argv byte-identical to today for every worker.

## Exit check

Two writer nodes that touch the same file run at the same time in separate worktrees, are both accepted, and merge after acceptance (disjoint hunks merge; overlapping hunks are refused at the gate with the paths).
