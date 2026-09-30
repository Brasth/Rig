# Built-in workflow recipes

Recipes are small, versioned JSON templates for recurring Rig workflows. Preview
compiles typed parameters into the **existing workflow spec**, including its
normal DAG, scope, resource and final-verification validation. It does not start
work. No new scheduler, criterion engine, provider policy or tool authority is
introduced.

## Catalog and preview

```bash
rig workflow recipe list --json
rig workflow recipe show bugfix --version 1 --json
rig workflow recipe preview bugfix --file params.json --json
```

Parent MCP equivalents are `rig_workflow_recipe_list`,
`rig_workflow_recipe_show(name, version)` and
`rig_workflow_recipe_preview(name, version, parameters)`, with optional `repo`.
These tools are read-only and unavailable to children. Inspection works in an
uninitialized or disabled project; it does not initialize or enable Rig.
Creating and advancing the result still requires the existing enabled-project
and adaptive-workflow gates.

The bundled catalog is in `templates/workflows/*.v1.json`:

- `bugfix`: `implement` → `review` → `final-verify`. One bounded local writer,
  independent protected review, then parent final verification. Accept the writer
  with `next=review` to retain protection. The existing scheduler resolves actual
  provider eligibility at advance. Missing or unknown independent-review provider
  stays explicitly blocked; preview neither probes availability nor falls back to
  self-review.
- `research-implement`: `research` → `implement` → `final-verify`. The research
  node uses `role=explore`, read access and explicit existing local source files.
  Remote source acquisition belongs to the parent before preview. Research
  findings still need parent inspection and an explicit brief/context handoff;
  this recipe does not automatically copy worker claims into implementation.
- `ui-validation`: `ui-validate`, a required final `verify` node with
  `task_domain=ui-verification`. It stays parent-only. It grants no browser,
  computer-use, screenshot or permission capability, and never spawns a clicker.

The result contains the recipe name/version/hash, normalized parameters and
parameter hash, normalized `spec` and `spec_fingerprint`, and each node's role,
access, effects, concrete file/resource scope and dependencies. Hashes are
SHA-256 of canonical JSON, not signatures or proof of provider availability,
safety, successful execution or acceptance. Identical normalized inputs and
catalog/configuration produce identical preview output. No timestamps, job IDs,
workflow IDs, reservations or owner tokens are allocated by preview.

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

Unknown parameters or node IDs, unsupported versions, malformed fields, cycles,
writer overlaps and out-of-scope criteria fail preview. The input is bounded to
1 MiB. Duplicate JSON keys are rejected by the CLI. Task and other string values
are serialized as data, never evaluated as shell, Python, template expressions,
hooks, provider selections or permission grants. Neither arbitrary recipe paths
nor remote/custom templates are accepted.

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
