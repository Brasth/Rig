# Pinned updates and rollback

`rig update` no longer downloads or runs `install.sh`, follows `main`, or repeats
setup. Bare `rig update` prints usage and exits 2. Choose a full, 40-character
commit SHA from the official [Brasth/Rig repository](https://github.com/Brasth/Rig).
There is no assumed release/tag process and no automatic update or migration.

```sh
rig update --revision FULL_COMMIT_SHA --dry-run
rig update --revision FULL_COMMIT_SHA
rig update --rollback --dry-run
rig update --rollback
rig update --status
rig update --recover
```

The updater fetches exactly that commit through Git from the official repository.
It checks the full resolved SHA, the explicit compatibility declaration, required
runtime files, Python syntax and shell syntax. It does not execute candidate
Python, shell, installers or provider commands during validation. `--dry-run`
uses temporary download storage and locks but changes no installed/project files.
No optional installer, login, permission grant or configuration setup is run.
Network access is needed only to fetch an explicitly requested commit; rollback,
status, recovery and all update tests are offline.

## Before changing a runtime

Stop admitting work. Finish or cancel active work using the normal authenticated
operations, confirm actual termination, and accept or close retained ownership.
Then fully quit every parent, MCP, worker and terminal companion using this Rig
installation. Run the update from an ordinary terminal. The updater never kills
processes, expires reservations, or unlocks a busy runtime by age.

Active/uncertain jobs, workflow execution, unreleased reservations, leases,
unavailable registered repositories, process-inspection failures, incompatible
protocols and modified owned files refuse the update. Preserve the evidence and
resolve the reported problem normally. Do not delete ownership/lease records to
make an update pass. Restart all parent/MCP sessions after update, rollback or
recovery; loaded sessions from an earlier runtime generation cannot admit work.

## What changes and what stays

Existing owned file modes are preserved; new runtime files are owner-only.
The owned `bin`, `scripts`, `skills`, `adapters` and `templates` runtime files are
refreshed together. Already-owned copied queue/plugin/status-line assets, copied
project skills (including new reference files), and the exact managed AGENTS
block are refreshed. Bytes outside that managed block are retained. An unchanged
owned skill symlink stays a symlink; manual assets and unowned skill links are
never adopted. A path conflict or symlink-parent write is refused.

Project enablement, harness/worker/cap/routing preferences, jobs, reservations,
workflow data, project memory, billing receipts, optional-component consent,
MCP/hooks/marketplace settings, agent TOML overrides and authentication/security
settings are not rewritten. Uninitialized projects stay uninitialized and
disabled projects stay disabled. No orchestration-mode change is made on rollback.
Updating a runtime template does not apply it to user configuration.

The compatibility declaration in `templates/runtime-compat.json` is deliberately
strict in v1: update, admission, child MCP, job/workflow data and managed-guidance
protocols must all match the controller's supported versions exactly. A declaration
is a maintainer compatibility promise, not proof of arbitrary candidate behavior.
A future incompatible revision needs a separately reviewed migration path.

## Recovery and rollback guarantees

The installed `runtime-state.json` records the full commit, compatibility versions,
owned-file hashes and last successful transaction. The original uninstall manifest
keeps its original `before` images; its `after` images advance with a successful
update. Per-update journals are separate, private files under `updates/transactions`
(directory mode 0700; snapshots/journals/state mode 0600).

The transaction states are prepared → applying → committed. Before the first
runtime replacement, the updater saves full preimages, a private copy of the
current recovery controller, and a durable pending marker. Each file replacement
uses a sibling temporary file, fsync and rename. This is recoverable multi-file
replacement, **not a globally atomic filesystem switch**. A crash can leave old
and new files together; admissions and lease startup remain blocked until recovery.
The CLI pending dispatcher runs before sourcing runtime scripts and uses the saved
controller outside the replaced set. Help and `rig update --status` remain
available; ordinary status/doctor commands are blocked while their imports could
be mixed-version.

After an interrupted operation, inspect `rig update --status`, then explicitly run
`rig update --recover`. Recovery checks every current file against the journal's
exact before/after images before writing anything, restores the full pre-transaction
snapshot, and clears the marker only after durable completion. If commit completed
and only marker cleanup was interrupted, recovery verifies the committed files and
finishes cleanup instead. A recovery conflict or uncertain journal stays blocked;
keep the journal and restore from an independently verified backup/manual repair.
Never remove the pending marker to bypass this check.

`--rollback` uses only the immediately previous successful controller update and
is itself journaled. It restores that runtime/integration snapshot and retains all
user data. It refuses incompatible snapshots, subsequent owned-file changes or
changed installation metadata. Repeating a successful rollback is a no-op. It is
not an arbitrary downgrade command, and does not promise to undo setup, init or
manual changes made afterward. Snapshots are not automatically pruned.

Lock order is exclusive update gate → lifecycle for updates; shared update gate →
repository lock for admissions. Lease registration holds shared update gate → lifecycle and checks the
pending marker/generation before any launch effects and again before publication. Updates never call admission while holding lifecycle.
Lock contention times out by refusing the request; no holder is forcibly unlocked.

## Legacy or unversioned installations

The older installer/update flow did not record a trustworthy full commit and
compatibility baseline. This controller does not infer that provenance from a
short VERSION string, adopt unknown assets, or promise rollback for earlier installs.
Legacy installations continue ordinary operation; only safe updating is unavailable.
Dirty/unversioned source setups also remain explicitly unsupported.

For transition, first preserve a manual backup of the existing runtime, its complete
installation manifest and all affected integrations/project data. Stop all sessions
as above. Review a clean checkout of the pinned controller revision and its setup
behavior. Establish a **fresh installation in a new empty RIG_HOME** through an
explicit, separately approved installation/setup, then explicitly initialize the
projects you choose. Setup can change configuration and ask about optional components;
those actions are outside `rig update` and must not be mistaken for an automatic
legacy migration. Keep the old runtime and backup until the new setup is verified.
Do not copy or fabricate `runtime-state.json`, delete an old installation to make
it appear fresh, or rerun setup expecting it to prove legacy rollback support.

A successful clean, fresh setup records the baseline. The first later compatible
controller update creates the first rollback snapshot. If baseline creation fails,
setup prints why; `rig update --status` explains that safe updates are unavailable.
