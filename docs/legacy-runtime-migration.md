# Legacy runtime migration

This runbook is for an unversioned `~/.rig` installation without
`runtime-state.json`. Keep it intact. The new root is `~/.rig-versioned` and must
be absent before setup. Do not manufacture provenance, copy runtime-state from
another install, clear leases/reservations or invoke `rig update` to adopt it.

## Preparation and backup

Use a clean committed checkout of the tested reliability changes. Record its
full `git rev-parse HEAD` value, not a short hash. Run the offline suite and
`python3 scripts/generate_protocol.py --check`. Review setup in a disposable
HOME first; never forward provider credentials, tokens or configuration there.
Block installer/network commands and use `RIG_SKIP_CUA_DRIVER=1`,
`RIG_SKIP_BROWSER_SKILL=1`, and `RIG_SKIP_TMUX_INSTALL=1`. Do not use explicit
`--no-*` flags to change already remembered optional consent.

Before real setup, make a private (0700 directory, 0600 files) backup containing:

- Complete old `~/.rig`, including its installation manifest and consent files.
- This project's `.rig` data, `.agents/skills`, AGENTS.md, CLAUDE.md when present,
  and .gitignore. Record the worker/cap/routing configuration digest privately.
- Existing destinations returned by `scripts/ui_install.py`'s
  `install_paths(new_root, source)` and `install_paths(new_root, source, repo)`:
  agent MCP configs, skill symlinks, queue/plugin/hooks/status assets, agent TOMLs,
  and `~/.local/bin/rig`. Preserve symlinks as links and file modes.
- Any actual configured integration destination outside those defaults. Inspect
  environment override paths without printing config contents or credentials.

Setup writes integrations; an isolated setup is a rehearsal, not production
migration. Backups can contain authentication data: do not commit, upload or
print them. Capture pre-cutover ownership/lease state and an inventory of paths
and restore targets. Verify the backup is readable before switching anything.

## Stop and cut over

Finish or explicitly cancel active work via the owning parent. Confirm worker
termination and accept or close retained scopes with their exact credentials.
Stop new admissions. Fully quit every parent, Rig MCP, worker and terminal
companion using the old installation. Execute the following only from an
ordinary terminal after those sessions are stopped:

```sh
# Substitute the clean checkout directory and verified full commit.
SOURCE=/absolute/path/to/clean/Rig
REVISION=FULL_40_CHARACTER_COMMIT
NEW_ROOT="$HOME/.rig-versioned"
[ ! -e "$NEW_ROOT" ] || { echo "New root already exists; stop" >&2; exit 1; }
[ "$(git -C "$SOURCE" rev-parse HEAD)" = "$REVISION" ] || exit 1
[ -z "$(git -C "$SOURCE" status --porcelain)" ] || exit 1
# Preserve consent as described below before this setup invocation.
RIG_HOME="$NEW_ROOT" RIG_SRC="$SOURCE" \
  RIG_SKIP_CUA_DRIVER=1 RIG_SKIP_BROWSER_SKILL=1 RIG_SKIP_TMUX_INSTALL=1 \
  "$SOURCE/bin/rig" setup
```

Machine consent is stored in `cua-driver.json` and `browser-skill.json` in
`RIG_HOME`. If either exists in the old root, create the fresh new directory
(0700) after the absent-root check, then copy only these two regular non-linked
files into it (0600), preserving their bytes. Run setup after this preparation;
its skip environment variables prevent installers and preserve remembered
consent. Do not copy the old install manifest/runtime files to the new root.
Keep worker flags and project data where they are. No provider install, login,
browser/OS grant, or capability enablement is part of this migration.

Because setup can fill missing parent-model defaults or repair sandbox/MCP
configuration, compare affected integration files with their backed-up versions.
Restore unrelated preferences before restarting. Preserve existing consent and
manual skill/agent overrides. Refused path conflicts require inspection; do not
force overwrite them.

Initialize only the existing intended project with the new runtime (explicit
`RIG_HOME`), preserving its configuration/history and bytes outside managed
instructions. Set the shell's `RIG_HOME` to the new root for subsequent launches;
use the new binary path or its setup-created launcher. Do not alter unrelated
projects. Check `rig update --status` reports the expected full commit and a
valid fresh baseline; failure here is not a completed migration.

## Restart and verify

Restart all parent/MCP sessions. In the intended project check task readiness,
actual runtime/integration paths and the new memory tools. Run a small concrete
scoped job: inbox handshake, stopped execution, required check, current parent
acceptance. Verify outcome metrics and attempt provenance. Inspect unavailable
enabled workers without changing flags or installing alternatives. Keep old
runtime and backups after success. Later compatible controller updates can
create rollback snapshots; fresh setup alone does not establish legacy rollback.

## Failure and rollback

If setup/restart validation fails, stop the new sessions and admissions. Preserve
new job artifacts. Restore only integration/configuration pointers and managed
assets from the backup, then restart the old runtime. Reconcile any new retained
ownership through its owning generation; never replace current `.rig` wholesale
with an old snapshot or delete reservations. Record which checks failed and
leave the new runtime/backup for inspection. No age-based unlock, forced process
kill, or legacy `rig update --rollback` is allowed.
