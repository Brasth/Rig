# Context packages

Include specific files as reference data for a worker. A package bundles selected source files, decisions, and test commands into one immutable artifact: a reusable pinned reference.

A brief is the task instructions, a context package is selected file contents, and a [workflow recipe](workflow-recipes.md) is an optional multi-stage template.

**What it does:** Included contents are reference data. Worker reads the files alongside your brief; permitted reads/writes still follow the admitted job scope (a package does NOT sandbox arbitrary access itself).

**What it doesn't:** Discover files, run tests, call providers, or grant special access.

## Build a package

Create `selection.json` with files to include:

```json
{
  "files": [
    {"path": "src/parser.py", "reason": "Implementation under discussion"},
    {"path": "docs/parser.md", "reason": "Expected behavior", "provenance": "Parent-selected design reference"}
  ],
  "decisions": ["Preserve the existing public API"],
  "constraints": ["Use the standard library only"],
  "test_commands": ["python3 -m unittest tests.test_parser"]
}
```

```sh
rig context preview --file selection.json
rig context build --file selection.json
```

The equivalent parent MCP tools are `rig_context_preview` and
`rig_context_build`, each with `selection` and optional `repo`. Preview is
read-only: it creates no package, directory, lock, or project state. Its output
lists source names, sizes, SHA-256 hashes, selection reasons, provenance,
statement counts, limits, and a pinned `context_package` reference; it does not
return source text. A reference returned by preview is not yet built.

Build uses Rig's admission/update barrier and creates an exclusive, private
`.rig/context-packages/ctx-<fingerprint>.json` artifact (directory 0700,
file 0600). The canonical manifest includes the exact UTF-8 contents,
source hashes, reasons/provenance, parent-stated text, and optional links.
Identical builds return the same reference without replacing the original.
Different bytes or stated text produce a new reference. No mutable "latest"
reference exists.

Save just the `context_package` object from the build result when using:

```sh
rig context show --file reference.json
rig context validate --file reference.json
```

`show` validates the artifact and returns metadata. `validate` additionally
checks current source bytes. Neither writes. A successful check is an observation,
not a guarantee that the repository will remain unchanged afterward.

## Bounds and screening

Version 1 permits at most 16 explicitly named files, 32 KiB per file, 128 KiB
of combined file and statement contents, and 256 KiB for the complete canonical
artifact. Selection JSON is limited to 32 KiB. Each of decisions, constraints,
and test commands permits at most 32 entries of 2 KiB each; selection reason
and provenance are each limited to 512 bytes. Limits are fixed, not configurable.
A limit violation refuses the whole package; Rig never silently truncates it.

Sources must be literal repository-relative regular files. No absolute paths,
URLs, glob expansion, traversal, symlinks (including internal ones), devices,
pipes, or directories are allowed. Only strict UTF-8 text without binary control
characters is supported. The suffix allowlist covers common source/documentation
formats (`.md`, `.txt`, `.rst`, `.py`, `.js`, `.jsx`, `.ts`, `.tsx`, `.json`,
`.toml`, `.yaml`, `.yml`, `.sh`, `.bash`, `.css`, `.scss`, `.html`, `.xml`,
`.sql`, `.rs`, `.go`, `.java`, `.c`, `.h`, `.cc`, `.cpp`, `.hpp`, `.rb`,
`.swift`, `.kt`) and conventional extensionless README, LICENSE, Makefile,
Dockerfile, CONTRIBUTING, and Justfile names. Other formats are refused.

Rig refuses `.env` variants, private-key and credential-looking filenames,
private state/configuration locations, Git internals, and personal-memory
locations. It scans selected file contents and parent-supplied text, reasons,
and provenance for common credential-looking values. On a finding it refuses
the entire package, reporting the source/category without the matched value.
It does not redact a value and then silently transmit the remainder.

Screening is deliberately conservative and incomplete. It can produce false
positives and **cannot guarantee that text contains no secrets**. Parents must
review selections. Do not select credentials, history dumps, private notes, or
unrelated files, and do not weaken these safeguards to include a refused source.

## Attach to a job

Pass the exact built reference to parent `rig_job_launch` or `rig_job_start`:

```json
{"context_package": {"package_id": "ctx-<64-character SHA-256>", "fingerprint": "<same SHA-256>"}}
```

At launch/start, Rig verifies the artifact fingerprint, strict manifest,
embedded content hashes, and current source hashes. It rechecks sources when
copying the artifact into the new admitted attempt's
`evidence/context-package.json`. Wrapper briefs append clearly labeled reference
data. Native registration returns `context_data` for the parent's handoff and
stores a brief containing the same snapshot. Existing brief text is preserved.

The rendered reference data is readable: a header with `package_id` and
fingerprint, then one block per file with its path, reason, provenance,
encoding, byte count and SHA-256, followed by the complete, untruncated content
between `----- BEGIN FILE DATA <sha256> -----` and
`----- END FILE DATA <sha256> -----` (markers carry the content's own hash, so
the content cannot contain them; `final_newline=absent` notes a missing final
newline). Stated decisions, constraints and test commands follow as JSON string
data. All of it remains data, not instructions or commands.
The `files` permission list is never expanded by selected context paths.

Results expose `context_package` and an evidence reference
`{path, sha256, kind: "context-package"}`. Acceptance-contract review assertions
can cite that job-relative immutable artifact using the ordinary evidence rules.
The package itself is neither acceptance nor evidence that a test ran.

Optional `links` in the selection can pin `job_id`, `attempt_id` (requires
`job_id`), and/or `contract_fingerprint`. These are expected target bindings,
not discovery requests or permissions. Before execution they must match the
actual admission reservation, including its normalized acceptance-contract
fingerprint when supplied. A different/missing contract or attempt fails closed;
no new data is substituted into an existing attempt. Omit target job/attempt
links for a package intended for a node whose job has not yet been allocated.

## Workflow freshness and explicit recovery

Set `context_package` on an individual workflow node. It is included in the
workflow spec fingerprint and immutable once that node executes. Existing
frozen `shared_context` remains unchanged. A global context-package binding is
not supported in version 1. Acceptance contracts are currently supplied at
direct job start/launch, so omit `contract_fingerprint` for workflow node
packages until the workflow contract API supports one. A missing actual contract
is always refused; the package never creates or assumes it.

An earlier writer may change files selected for a later node. Rig refuses the
later launch with the changed source path; it does not silently run with stale
context. The workflow's failure identifies the affected node. To continue:

1. Inspect and resolve/release any failed, never-started attempt using the normal
   lifecycle. Use `rig_workflow_resolve` with `action: "retry"` after those
   conditions are satisfied; this returns the unlaunched node to pending
2. Explicitly preview/build a new package from current selected inputs
3. Use the existing authenticated `rig_workflow_extend` operation with
   `context_packages: {"later-node": <new reference>}` (or pass this object in
   the JSON for `rig workflow extend ID --file extension.json`)
4. Re-approve gated effects if the spec change invalidated their approval, then
   advance normally; Rig makes a fresh routing pick

This is a narrow rebind, not an edit to scope, files, resources, acceptance
contracts, or previous attempt artifacts. It requires a never-executed node,
no active/reserved attempt, no execution history, and normal workflow ownership.
Executed nodes, unresolved failed nodes, held attempts, terminal/cancelled
workflows, and extension after final verification launches remain protected.
A launched-node retry cannot use this operation to replace its context; start
explicitly authorized new work instead. Concurrent rebind and advance operations
share admission serialization: a launch claim prevents the rebind.
