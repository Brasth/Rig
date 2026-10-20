# The Rig shell, workspace and environment

An optional terminal layout for working with a parent agent while Rig runs workers. It adds no new way to run work: workers still start through the parent and the same admission, ownership and acceptance rules apply.

## What you get

| Command | What it does |
| --- | --- |
| `rig shell` | One tmux window: the parent CLI unchanged in its own pane, the board (`rig-shell`) beside it, and a log pane following the most relevant job |
| `rig shell print`, `rig shell kill` | Show the tmux commands it would run, or remove this repository's session |
| `rig shell build` | Build `rig-shell` from a Rig clone with Go 1.24+ (no project needed) |
| `rig init --sandbox` | Detect the workspace and, for a folder with no devcontainer, store a generated config under `.rig/sandbox/override.json` |
| `rig env up`, `down`, `status [--json]` | Start or stop the workspace container and show what dc-cli reports |
| `rig doctor [--workspace]` | Adds `workspace`, `dc-cli` and `rig-shell` checks. `--workspace` also runs dc-cli's own diagnosis |
| `rig api serve`, `ensure`, `call`, `methods` | The local API behind all of this ([reference](local-api.md)) |

`rig shell` needs tmux 3.3+, the parent CLI on `PATH` (`codex`, `grok`, `claude`, `opencode`, `omp`, `pi` or `agy`), and a built `rig-shell`. It uses its own tmux server unless you are already inside tmux, never binds the F8/F9 companion popups, and reuses the session if one exists for the repository.

## The board

Tabs: Jobs, Queue, Workflows, Workspace. Every state shows a glyph and a word, and labels, ordering and counts come from the same code as `rig tui`, so the two never disagree.

| Key | Action |
| --- | --- |
| `Tab`, `Shift-Tab` | Next or previous tab |
| `j`, `k` | Move, or scroll on the Workspace tab |
| `Enter`, `Esc` | Open or close details on narrow terminals |
| `a`, `d` | Allow or deny the selected worker prompt |
| `x` | Stop the selected job or queue item, after a confirmation |
| `e` | Park a task in the queue (`Enter` saves, `Esc` discards; a failed save keeps the draft) |
| `r`, `q` | Refresh, quit |

Pasted text is never run as commands. `RIG_TUI_ASCII=1`, `RIG_TUI_CJK=1` or a non-UTF locale selects ASCII glyphs. If the API server is not running the board starts one and shows "refresh failed" with the snapshot age if it cannot reach it, keeping the last data on screen.

## Workspace and dc-cli

Rig never calls docker. dc-cli owns the container and Rig runs it as a subprocess. Rig never edits your own `.devcontainer`; for a folder with none it passes the generated override with `--override-config`. `provider = "devcontainer"` in `[sandbox]` is still refused at worker launch: nothing here moves workers into a container yet.

**dc-cli contract status.** The commands and JSON fields Rig reads are assumptions, documented at the top of `scripts/dc_client.py`, and have not been checked against a dc-cli release. Fields are read tolerantly, so a renamed key shows as "unknown" instead of failing, but the panel and `rig env status` need verifying against a real release before relying on them. `templates/dc-cli.json` holds the version pin; it is empty, which means any dc-cli is accepted and `rig doctor` says "unpinned". Setting it makes a different version fail `rig doctor --workspace`.

## Building and versions

`rig setup` builds `rig-shell` when Go is installed (`--shell-binary` insists, `--no-shell-binary` skips) and stamps it with the source commit. `rig update` does not carry shell sources, so after updating, `rig doctor` reports a mismatch between the binary and the installed core and says to run `rig shell build` from a clone (`RIG_SRC=/path/to/Rig rig shell build`).

Developing the shell: see `shell/README.md`.
