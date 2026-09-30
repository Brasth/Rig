# Private UI evidence packs

`rig_job_ui_evidence` (human CLI: `rig job ui-evidence ID --file PACK.json`)
assembles screenshots and receipts the parent has **already captured and reviewed**.
It never captures, clicks, executes a browser/provider command, performs OCR,
changes an image, uploads evidence, grants capabilities, or accepts a job.
The normal parent-only browser/computer-use selection, consent and freshness
rules still apply when obtaining the original observations.

## Contract and ownership

The job must have a frozen acceptance contract with one or more
`review_assertion` criteria whose `artifact_kind` is `ui-pack`. Supply their
stable IDs, the contract fingerprint and the current **full admitted-scope
content** snapshot. That content snapshot is not a CUA/BrowserSkill observation
snapshot. Execution must be confirmed stopped; recording requires the same
owner/attempt credentials as parent verification, or a validated
`credentials_path`. A running, released, wrong-owner or stale attempt is refused.

The result includes `evidence_ref` with `path`, `sha256`, and `kind: "ui-pack"`.
Use it in a separate `rig_job_criterion` parent assertion. Recording the pack
creates no assertion or acceptance. Assertion/acceptance revalidates the manifest,
all images recorded as available, current content, contract, attempt and named
criterion. A manifest hash alone cannot conceal a changed or deleted image.
Local artifacts are not cryptographic protection against another process with
the same user's filesystem access.

## Inputs and privacy

Every request requires `privacy_reviewed: true`. This is the parent's assertion,
not automatic secret detection or certification. Review every supplied image and
note. Provide a separately redacted copy when needed; Rig does not edit or
overwrite originals. Do not include private typed values, credentials, unneeded
personal details or URL queries. Raw receipt outlines, element labels, typed
content and URLs are omitted from the stored receipt projection. Bounded
metadata that looks like credential material or a URL with query/fragment is
refused without echoing its value. This conservative check cannot detect every
secret, especially secrets visible inside pixels.

Sources must be explicit repository-relative regular files under
`.rig/cu-evidence/`, `.rig/bsk-evidence/`, or this job's
`.rig/jobs/ID/evidence/inputs/`. Symlinks, hard links, traversal and special files
are refused. If the original capture is elsewhere (for example CUA's temporary
PNG), the parent must first explicitly stage its reviewed copy in the job input
directory. No scanning or import of arbitrary external paths occurs.

PNG files must have valid bounded chunk structure, checksums and dimensions;
text/EXIF metadata chunks are refused. This validates the container, not visual
correctness. Images are copied byte-for-byte into a private immutable pack;
originals remain untouched. The parent must prepare any metadata-free/redacted
copy beforehand. Redacted inputs require a distinct `original_sha256`; if the
receipt supplies its selected-image hash, that hash must match the original.
An unredacted image must match the receipt hash when present.

Prefer `receipt_file: {"path": "...", "sha256": "..."}` for an existing recorded
`rig.cu.v1`/`rig.bsk.v1` JSON receipt (or a wrapper containing `receipt`). Rig checks
the file hash, saves a safe projection, and labels it `recorded_receipt_file`.
Alternatively pass the existing receipt object as `receipt`; it is labeled
`supplied_unverified`. Neither form is authenticated backend proof. Backend
receipt fields and `parent_observation` assertions remain separate.

## Example request shape

Replace placeholders with the real current IDs and SHA-256 values; these are not
commands to capture or stage files.

```json
{
  "privacy_reviewed": true,
  "contract_fingerprint": "<frozen contract SHA-256>",
  "content_snapshot_id": "<current acceptance content snapshot>",
  "criterion_ids": ["layout"],
  "steps": [
    {
      "id": "initial-screen",
      "receipt_file": {
        "path": ".rig/bsk-evidence/observe-example.json",
        "sha256": "<existing receipt file SHA-256>"
      },
      "image": {
        "path": ".rig/jobs/example/evidence/inputs/screen.png",
        "sha256": "<reviewed PNG SHA-256>",
        "privacy": "reviewed"
      },
      "viewport": {"width": 390, "height": 844, "coordinate_space": "viewport_css_px"},
      "parent_observation": {"expected": "Button is visible", "observed": "Button is visible"}
    }
  ]
}
```

Each ordered step needs a unique ID and exactly one receipt input. Image,
viewport and parent observations are optional. Missing images are explicitly
`unavailable`; unknown dimensions remain null. Pixel dimensions are recorded
separately from the parent-supplied viewport. Unavailable coverage does not become
a passing observation: the parent decides and records any criterion assertion
explicitly. Deletion of an image previously recorded as available invalidates
its evidence reference.

Limits: 32 steps/criterion references, 128 KiB JSON input/manifest, 4 MiB per PNG
and 16 MiB total images. Pack paths are content-addressed under
`evidence/ui-packs/`; files are created mode 0600 and new directories mode 0700.
Repeating identical inputs returns the existing validated pack. Different
content creates a different pack; no old pack is silently replaced.

## Interrupted recording

An interrupted writer may leave private `.pack-*` staging files and an evidence
operation marker. Staging is never returned as an acceptance reference. Inspect
the exact job and recorded operation executor. Live or unknown execution remains
protected. Only after that executor is confirmed dead, use `rig_job_reconcile`
with the exact job ID, `apply: true`, current owner/attempt credentials (or the
validated saved receipt), and a rationale. The human CLI is `rig job reconcile ID
--apply --credentials-path PATH --rationale TEXT`. Unscoped reconcile cannot
clear an evidence marker. Recovery clears only that operation, preserves held
files and accepts nothing. Retry recording with complete reviewed inputs; a new
private staging directory is used. Original screenshots and abandoned staging
are not deleted by recovery.
