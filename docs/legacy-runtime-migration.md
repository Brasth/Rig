# Legacy runtime migration

For an unversioned `~/.rig` without `runtime-state.json`. `rig update --migrate`
moves you to a fresh, separate, pinned runtime root. It never modifies, adopts or
deletes the legacy root, never manufactures provenance, and never enables
projects or backends.

## Run it

An older installed `rig` does not know these flags. Use a Rig checkout's
`bin/rig` (it runs its own helper even while `RIG_HOME` points at the legacy root):

```sh
/path/to/Rig/bin/rig update --migrate --latest --dry-run --project "$PWD"
/path/to/Rig/bin/rig update --migrate --latest --project "$PWD"
```

- `--latest` resolves official `main` once to a full SHA; `--revision SHA` pins
  an exact commit instead. One of them is required.
- `--project PATH` is optional and must be an existing, enabled Rig project. It is
  the only project initialized; others are left alone (initialize them later
  explicitly with the new runtime).
- `--new-root PATH` defaults to `~/.rig-versioned` and must not exist.
- `--dry-run` fetches into a temporary directory and prints the concrete plan. It
  changes no installed, project or integration file.
- Without `--dry-run`, a TTY asks for consent after the same plan. Without a TTY,
  `--yes` is required; otherwise it prints the preview/apply commands and exits 2.
- In a terminal, bare `rig update` from the checkout offers this flow and offers
  the current project only when it is an existing enabled one.

## Refusals (nothing changed)

Pending update marker; root already versioned; new root already exists
(including from an interrupted migration: the error names its restore command);
disabled or uninitialized project; active/uncertain jobs, workflows, reservations,
runtime leases, processes referencing the legacy root, unavailable registered
repositories or failed process inspection; an unknown revision or a candidate
with unsupported compatibility. Stop sessions and resolve these normally first:
finish or cancel work, confirm termination, accept or close scopes, quit every
parent/MCP/worker/companion using the old runtime.

## What it does after consent

1. Creates `~/.rig-migrations/<UTC>-<sha12>/` (0700) with the clean checkout
   (`source/`, detached at the pinned commit, hooks disabled) and `migration.json`.
2. Holds the legacy update gate (exclusive) and lifecycle lock. If those lock files
   are absent it creates them empty (0600); that is the only change made to the
   legacy root and it is shown in the preview.
3. Backs up privately (dirs 0700, files 0600, links kept as links, original modes
   and SHA-256 in `backup/inventory.json`), then verifies every copy:
   the complete legacy root (manifest and consent files included); the project's
   `.rig` data, `.agents/skills`, `AGENTS.md`, `CLAUDE.md`, `.gitignore`; every
   integration destination from `install_paths()` for the root and the project,
   honouring `GROK_HOME`, `OPENCODE_CONFIG`, `OMP_MCP`, `AGY_MCP` and
   `PI_CODING_AGENT_DIR`/`PI_AGENT_DIR` overrides, `~/.cursor/mcp.json`, and the
   `~/.local/bin/rig` launcher. Backups can contain authentication data: do not
   commit, upload or print them.
4. Creates the new root empty (0700) and copies only `cua-driver.json` and
   `browser-skill.json` (regular files, same bytes, 0600).
5. Runs the checkout's `bin/rig setup --no-mimo` with `RIG_HOME=<new root>`,
   `RIG_SRC=<checkout>`, `RIG_SKIP_CUA_DRIVER=1`, `RIG_SKIP_BROWSER_SKILL=1`,
   `RIG_SKIP_TMUX_INSTALL=1` and no TTY: no optional installer runs and remembered
   consent is unchanged. Setup writes integrations as usual; it may still fill a
   missing Codex/Grok default (the preview lists the destinations it may change).
6. Verifies the full baseline: `runtime-state.json` for exactly the pinned commit,
   owned-file hashes and installation ownership.
7. With `--project`, runs the checkout's `bin/rig init` there and verifies every
   original harness line is kept, effective worker/cap/routing settings are equal,
   and every existing `.rig` history/data file is byte-identical (init may add a
   missing template such as `STATE.md`); then re-verifies the baseline.
8. Replaces `~/.local/bin/rig` (file or link) with a generated script that exports
   `RIG_HOME=<new root>` and runs the new `bin/rig`, and records it as that path's
   after-image in the new installation manifest (setup's preimage kept, so a later
   `rig uninstall` restores the previous launcher). Then re-verifies the baseline,
   re-checks legacy quiescence and that legacy root contents are unchanged.

Success prints the new root, launcher and restore command. Agent MCP configs that
point at the new root's `scripts/rig-mcp.sh` bind `RIG_HOME` to that versioned
runtime when the variable is unset, so a shell-profile export is not required for
ordinary agent sessions. An explicit nonempty `RIG_HOME` still wins; migration
warns only when that override already differs from the new root. Fully restart
every parent/MCP session. Later updates: `rig update --latest`.

## Failure and restore

Any failure after the backup keeps the backup, leaves the new root for
inspection and prints:

```sh
/path/to/Rig/bin/rig update --restore-migration ~/.rig-migrations/ID --dry-run
/path/to/Rig/bin/rig update --restore-migration ~/.rig-migrations/ID
```

Restore reinstates only integration destinations, the launcher and project
guidance (`AGENTS.md`, `CLAUDE.md`, `.gitignore`, `.rig/harness.toml`,
`.agents/skills`) from the backup; it never replaces `.rig` data wholesale, never
deletes the new root and never claims a runtime rollback. It refuses (no files
changed) while the new runtime is busy, when a parent directory now resolves
elsewhere, or when a path changed after the recorded migration result. If the
migration was hard-interrupted before that result was recorded, or you have
reviewed the listed later edits, `--force` overwrites just those listed paths. A
failed backup changed nothing outside the migration directory (except any listed
empty lock files) and has nothing to restore. Never delete reservations, kill
processes or use `rig update --rollback` for legacy recovery.

## After success

Keep the legacy root and backup. In the project check task readiness, run a
small scoped job and its acceptance, and inspect unavailable workers without
changing flags. The first later compatible controller update creates the first
rollback snapshot; migration alone does not provide legacy rollback.
