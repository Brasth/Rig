# Task preparation

A clear brief gives the worker the findings, decisions, files and checks it needs to begin. Normally your parent agent prepares this for you. Try:

> "Prepare a brief with the relevant findings, decisions, files and checks, then implement the save fix."

The parent reads the code; `rig_task_prepare` only assembles the selected inputs. It does not launch work or run checks.

Save as `task.json` after adapting paths, checks and evidence to your project (illustrative example):

```json
{
  "task": "Fix save handler to be atomic",
  "files": ["src/save.py", "tests/test_save.py"],
  "checks": [{"id": "tests", "argv": ["python3", "-m", "unittest", "discover", "-s", "tests", "-p", "test_save.py"], "cwd": "."}],
  "findings": [
    {"statement": "save() truncates on error, leaving partial files", "status": "verified",
     "evidence": [{"path": "src/save.py", "line": 42}]}
  ],
  "decisions": [{"choice": "Write to temp file, then rename", "reason": "Atomic on POSIX"}],
  "changes": [
    {"path": "src/save.py", "change": "Use atomic write pattern"},
    {"path": "tests/test_save.py", "change": "Add interrupted-write test"}
  ],
  "reading_order": [
    {"path": "src/save.py", "reason": "Entrypoint"},
    {"path": "tests/test_save.py", "reason": "Existing test structure"}
  ],
  "unknowns": [],
  "remaining_work": "low"
}
```

MCP `rig_task_prepare` takes `repo` and `selection` with the same JSON. Supported recipes: `bugfix`, `research-implement`, `ui-validation`.

## How it works

The tool validates the input structure (it does not prove the facts), then produces:

- **Brief** ready to show the worker (Goal, Findings, Decisions, Changes)
- **Acceptance contract** with checks to run after work
- **Preparation object** (fingerprinted, inert) to track the task

Fields (most optional):

| Field | Notes |
|-------|-------|
| `task` | Required; what to do |
| `files` | Writer files needed for executable work; helpful in draft |
| `findings` | Optional. `verified` requires evidence (path, optional line); `hypothesis` allowed without evidence; tool does not validate factual truth |
| `decisions` | Optional. Explicit choices made; `[]` means no decision needed; omitting signals readiness gap |
| `changes` | Intended change per declared writer file, new or existing; `execution_ready` requires one for every writer |
| `reading_order` | Entrypoints and patterns; execution_ready requires every existing writer file listed here with reason |
| `checks` | Tests/validations to run after work; OR use `manual_criteria` for parent-review (recorded evidence required, no auto-approval) |
| `unknowns` | `[]` means none remain; omission means not stated; otherwise list the unresolved questions |
| `remaining_work` | `"low"` \| `"medium"` \| `"high"` — work estimate after findings |

**ready** = required draft inputs present. **execution_ready** additionally needs: change for every writer file, reading entry for every existing writer, explicit decisions (or `[]`), unknowns explicitly `[]`, checks or manual_criteria. `readiness_gaps` lists missing items. Stale files, new files, edited brief, changed scope/contract → re-prepare & re-pick.

## Using the preparation

The returned **preparation object** is a fingerprinted record: inert (no access granted) and tied to exact source state.

```bash
rig task prepare --file task.json --json > prepared.json
rig pick implement --preparation prepared.json --json
```

Those commands do NOT launch work. The parent then calls MCP `rig_job_launch` or `rig_job_start` with exact returned brief, files, acceptance_contract, preparation, plus selected worker/model/effort/routing. Source runtime must support policy v3; re-pick after update/config change, full parent/MCP restart after runtime update.

Stale files (edited/new after preparation), edited brief, different scope/contract, or tampered object requires re-prepare & re-pick.

## Readiness gaps

Check `readiness_gaps` in the output:

- `draft-not-ready`, `change-missing`, `entrypoint-missing`, `decisions-missing`, `unknowns-missing`, `unknowns-open`, `acceptance-missing`

Fill gaps, re-run `rig task prepare`, then `rig pick` with the new preparation.

**Manual criteria:** For parent-review tasks (design, security), use `manual_criteria` array instead of `checks`. Each criterion becomes a parent review assertion; a `parent-review` evidence artifact must be recorded before acceptance (no automatic approval).

**Limits:** task.json ≤32 KiB, evidence/reading files ≤16 MiB each (separate [context limits](context-packages.md#bounds-and-screening) apply). Paths must be repo-relative (no absolute, URLs, `..`, or symlinks).
