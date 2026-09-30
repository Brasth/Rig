# Acceptance contracts

An optional acceptance contract states the required result **before work starts**.
It extends Rig's admission, required-check execution, scoped change evidence and
parent acceptance. Worker `ok` is still execution success, not acceptance.

## Version 1

Pass `acceptance_contract` to `rig_job_start` or `rig_job_launch`. The native CLI
uses `rig job start --acceptance-contract contract.json` with its normal scope,
executor and ownership options. No contract is added to old jobs automatically;
jobs without one retain the existing requirements/manual-criteria behavior.
Workflow nodes may explicitly include their own `acceptance_contract`; contracts are never silently inherited. The workflow normalizer validates each named node scope, includes the contract in its spec hash, and forwards it unchanged to fresh job admission. See [workflow recipes](workflow-recipes.md) for explicit stage mappings and examples.

```json
{
  "schema_version": 1,
  "contract_id": "fix-save-button",
  "revision": 1,
  "criteria": [
    {
      "id": "save-tests",
      "description": "The save regression test passes",
      "scope": ["app.py", "tests/test_save.py"],
      "evidence_type": "check",
      "verifier_role": "parent",
      "check": {
        "id": "save-tests",
        "argv": ["python3", "-m", "unittest", "discover", "-s", "tests", "-p", "test_save.py"],
        "cwd": "."
      }
    },
    {
      "id": "save-reviewed",
      "description": "Review the save flow and report remaining limitations",
      "scope": ["app.py"],
      "evidence_type": "review_assertion",
      "verifier_role": "parent",
      "artifact_kind": "review-note"
    }
  ]
}
```

Every criterion is required. IDs must be unique simple names. There are at most
64 criteria, each description at most 2,048 characters; the normalized contract
is at most 256 KiB. Criterion scopes are concrete repository-relative source
files within the admitted job's scope, not globs, directories, `.git`, `.rig` or
paths outside the repository. Scope does not grant permission to access a file.
Checks specify exact argv and cwd; a shared check ID must use identical values.
Unsupported fields, versions, kinds or malformed data are rejected.

At admission, Rig freezes the normalized contract and its SHA-256 fingerprint in
the reservation, then writes `acceptance-contract.json` and projects the existing
`requirements.json` before activation. The binding is:

- `job_id`, `reservation_id`, `attempt_id`
- `contract_fingerprint`, `contract_id`, `revision`
- The existing full admitted-scope content `snapshot_id` for each check, assertion
  and acceptance; a criterion's smaller scope does not weaken that binding

Changing descriptions, scopes, checks, evidence kinds or revision changes the
fingerprint. There is no in-place rewrite API: create a fresh admitted attempt
with the revised contract and rerun checks/reviews. A new attempt cannot reuse
old acceptance or check records. Existing requirements cannot be removed,
redefined or appended for a contracted attempt. Re-submitting the identical
projected requirements is allowed. Fingerprints are drift detection, not
signatures or an OS security boundary against another process under the same
user account.

## Checks and review assertions

1. Start or launch with the complete contract, then do the admitted work
2. Confirm execution stopped through the existing authenticated finish flow
3. Run every `rig_job_check` using its exact declared ID, argv and cwd
4. Record each deliberate review assertion with `rig_job_criterion`
5. Inspect `rig_job_show`, then call the existing `rig_job_accept` against the
   current content snapshot and explain the outcome

A check criterion is computed only from actual parent-authorized check execution,
including its exit code, unchanged before/after snapshots, preserved output and
current contract binding. A text claim cannot pass a check criterion.

A review assertion is explicitly labeled `parent_assertion`, with the recording
parent's owner session/CLI and the declared verifier role. It is not an objective
check result or proof of human review. A screenshot, file, model statement or
successful worker exit never records or accepts an assertion automatically.
The parent must inspect the evidence and judge the specific criterion.

`rig_job_criterion` takes normal job ownership credentials and these fields:

```json
{
  "id": "writer-job",
  "criterion_id": "save-reviewed",
  "contract_fingerprint": "<current 64-character SHA-256>",
  "snapshot_id": "<current scoped-content snapshot>",
  "result": "pass",
  "rationale": "Reviewed the requested behavior and the attached local notes.",
  "evidence_refs": [
    {
      "path": "evidence/review.txt",
      "sha256": "<SHA-256 of the existing file bytes>",
      "kind": "review-note"
    }
  ]
}
```

For CLI use, save the fields above **without `id`** in an assertion JSON and run
`rig job criterion writer-job --file assertion.json` with the same reservation,
attempt and owner-session flags used for checks (`RIG_OWNER_TOKEN` carries the
token). This command records a finding; it does not create the referenced files.

Each assertion requires 1–16 explicit existing files below this job's `evidence/`
directory, each at most 16 MiB. Kind must match the criterion's `artifact_kind`.
Paths escaping that root, symlinks, hard links, non-regular files and incorrect
hashes are rejected. Artifacts are hashed locally; nothing is uploaded or fetched.
The generic artifact validator verifies the referenced file bytes, not external
URLs or arbitrary transitive references inside it. Evidence producers must copy
self-contained artifacts or provide a validator for a structured evidence kind.

Current assertions live in `criteria/<criterion-id>.json`; immutable previous
receipts remain in `criteria/history/`. Acceptance binds the complete canonical
assertion fingerprint, including its rationale and parent provenance; a changed
pointer or receipt invalidates that acceptance. Passing assertions remain pending until
explicit acceptance. A later failed assertion invalidates acceptance. Missing,
stale, changed or failed required evidence cannot be omitted by choosing a subset
of check IDs. Checks, assertions and acceptance serialize through the existing
admission mutation guard and verification lock.

## Independent review and existing protection

`verifier_role: "independent-review"` is supported only in a **separately admitted
reviewer job** using Rig's existing protected writer-snapshot handoff and different
known actual provider. Putting it on an ordinary writer job is rejected before
work rather than creating an impossible completion condition.

Accept the writer's own contract with `next=review` to retain protection, then
admit the read-only independent reviewer with its own contract. The reviewer
still needs confirmed stopped execution, its own checks/assertions and explicit
parent acceptance. This field does not spawn reviewers, waive provider gates,
assert human approval or grant capabilities. Review unavailability remains
explicit. Contracts do not change user approvals, permissions, tool access,
worker eligibility or production/merge commitments.

## Inspection and failure behavior

`rig_job_show` lists the contract, requirements, check, assertion and acceptance
artifacts. Current accepted assessments expose the contract fingerprint, revision
and per-criterion outcomes with `computed_check` versus `parent_assertion`
provenance. Changed source, contract, assertion or referenced artifact downgrades
current acceptance; retained files are not silently released. An accepted
`next=complete` still uses the existing authenticated release path. Historical
acceptance remains an audit record, never permission for new work.
