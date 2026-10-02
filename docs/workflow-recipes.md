# Workflow recipes

Templates for common multi-step work. Preview shows what a workflow will do; create/advance to start it. No automatic launching.

**Available recipes:**

- `bugfix`: implement → independent review → final verify (parent)
- `research-implement`: read source files → implement → final verify
- `ui-validation`: parent visual checks + verify node (parent-only)

Preview (read-only):

```bash
rig workflow recipe list --json
rig workflow recipe show bugfix --version 1 --json
rig workflow recipe preview bugfix --file params.json --json
```

MCP: `rig_workflow_recipe_list`, `rig_workflow_recipe_show`, `rig_workflow_recipe_preview` (parent-only, no children access).

## Typed parameters

All recipes require:

- `task`: nonempty text, at most 16,384 characters
- `files`: 1–256 concrete repository-relative source file paths. Directories,
  repository control files, traversal, remote URLs, unresolved glob patterns and
  escaping aliases are refused. New concrete files are allowed. Resolved aliases
  join the same scope admission would protect

Only `research-implement` additionally requires `research_sources`: 1–100
existing readable repository-relative source files. Sources are read-only and
are not added to the writer's scope.

Optional parameters are explicit stage mappings:

- `resources`: node ID → array of `{ "name": "opaque-resource", "access":
  "read" | "write" }`. Only writer stages can claim write resources. Opaque
  resource names are validated by existing admission rules; do not put secrets
  in them
- `acceptance_contracts`: node ID → complete existing schema-1
  [acceptance contract](acceptance-contracts.md). Each supplied criterion belongs
  to exactly its named stage. There is no global criterion list, automatic
  splitting, inheritance, downgrade or implicit omission
- `context_packages`: node ID → exact existing `{ "package_id": "ctx-<SHA-256>",
  "fingerprint": "<SHA-256>" }` reference from the dedicated
  [context package API](context-packages.md). Preview validates the pinned package
  and source freshness through that API; the enabled-project gate still applies
  when referencing a package. It never builds, selects, refreshes or rebinds one,
  exposes package content, or adds source paths to writer scope
- `preparations`: writer node ID → the unmodified `preparation` object from
  [task preparation](task-preparation.md). Accepted by every recipe with a writer
  stage, only for writer stages. Preview checks it against current source bytes,
  uses the prepared brief byte-for-byte as that node's brief (no template text is
  appended) and stores the object on the node, inside the spec hash. The node's
  files and acceptance contract must equal the preparation. Reviewers and final
  verifiers never receive an invented preparation. `rig task prepare` with a
  `recipe` fills `acceptance_contracts` and `preparations` for the writer stage

Unknown parameters or node IDs, unsupported versions, malformed fields, cycles,
writer overlaps and out-of-scope criteria fail preview. The input is bounded to
1 MiB. Duplicate JSON keys are rejected by the CLI. Task and other string values
are serialized as data, never evaluated as shell, Python, template expressions,
hooks, provider selections or permission grants. Neither arbitrary recipe paths
nor remote/custom templates are accepted.

### Prepared nodes at advance

Advance passes a prepared node's preparation to pick (which recomputes readiness
and freshness) and to start/launch, which validates the node's raw brief, files,
contract and current source bytes before admission. Workflow shared context is
appended only after that validation. If an earlier stage changed a bound source,
the later node fails as stale. Resolve/retry it, re-run `rig_task_prepare`, and
rebind with authenticated `rig_workflow_extend` `preparations: {node_id: preparation}`;
this replaces the node's brief with the new prepared brief, resets its routing,
and is refused for executed, held or launched nodes, like context rebinding.

### Research scope limitation

The existing workflow validator allows a read overlap with a writer **only after
that writer is an accepted predecessor**. Therefore a research stage cannot read
files that its later implementation stage will edit, even with an explicit
research → implementation dependency. Preview rejects this overlap, including
aliases. Use disjoint source notes/design documents and implementation files; do
not claim this recipe can research and edit the same source files. A workflow
needing that behavior requires separately scoped parent planning or a separately
reviewed change to the workflow contract.

For a repository containing `docs/design.md` and `src/app.py`, this is a usable
`research-implement` parameter file:

```json
{
  "task": "Use the local design note to correct save behavior in src/app.py",
  "files": ["src/app.py"],
  "research_sources": ["docs/design.md"],
  "acceptance_contracts": {
    "implement": {
      "schema_version": 1,
      "contract_id": "save-regression",
      "revision": 1,
      "criteria": [
        {
          "id": "save-tests",
          "description": "The save regression tests pass",
          "scope": ["src/app.py"],
          "evidence_type": "check",
          "verifier_role": "parent",
          "check": {
            "id": "save-tests",
            "argv": ["python3", "-m", "unittest", "discover", "-s", "tests", "-p", "test_save.py"],
            "cwd": "."
          }
        }
      ]
    }
  }
}
```

The example requires the repository's own `tests/test_save.py` for the declared
check to pass later. Preview does not execute or assert that check.

## Review, then explicitly create and advance

Inspect the full preview before creating work. CLI users can separately capture
the spec with ordinary shell redirection (the shell writes this file):

```bash
rig workflow recipe preview research-implement --file params.json --spec-only > reviewed-spec.json
rig workflow create --file reviewed-spec.json --json
```

With MCP, pass the reviewed preview's `spec` object unchanged to
`rig_workflow_create`, retain its credentials, and then deliberately call
`rig_workflow_advance`. Creation adds the actual workflow ID and timestamp, so its
stored spec hash differs from the preview fingerprint. Preserve the full preview
if you want the recipe/parameter provenance as well as the created workflow.
There is no recipe `create`, `run`, automatic advance or direct launch command.

A workflow node may now carry an optional `acceptance_contract`. The existing
contract normalizer validates its concrete node scope at preview/create/extend;
normalization happens after the existing final-verify scope union. The complete
normalized contract is part of the workflow spec hash and frozen again with a
fresh job-attempt fingerprint at real admission. Native-parent and wrapper launch
both receive that exact node contract. A `verifier_role=independent-review`
criterion is valid only in a review node with a writer predecessor, followed by
the normal actual-provider/protected-snapshot admission gates. Writer and final
verification stages cannot self-label as independent reviewers.

Every check/assertion still needs existing job check/criterion/accept operations.
Worker exit success does not pass a contract. Changing a contract, receipt,
artifact or scoped source invalidates current contracted-node acceptance; a
previously verified workflow cannot retain verified status from stale evidence.
`verification.assessment(refresh=True)` performs this read-only current-evidence
check. Its `refresh=False` mode intentionally omits receipt/source freshness and
is insufficient for this workflow gate. Existing uncontracted nodes keep their
legacy behavior. Extend/retry cannot rebind executed contracts, and final
verification cannot be skipped or weakened.

Version 1 accepts only pinned `context_packages`, not ad-hoc context paths. If
a package source changes before its node launches, the existing launch gate
refuses it; explicitly build/select a fresh package and use the existing
never-executed-node rebind flow where allowed. No stale package is auto-refreshed.
Acceptance evidence references are job-local artifacts recorded after admission
through the existing criterion API; there is no generic recipe `evidence_refs`
field that could claim evidence or pre-accept work.
