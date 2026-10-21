# Release Notes




## Conflicts resolved side by side; a search result opens on the match (2026-10-21)

A conflicted file (after a pull or a merge that did not go through) opens from the git view as its conflicts side by side: ours and theirs for each block, `o` keeps ours, `t` theirs, `b` both, `e` edits by hand, `]` `[` move between them. Each choice writes the file; once none is left the file is marked resolved. A file with conflict markers opened from the explorer gets the same view. `⏎` on a search result now puts the cursor on the match.

## Pause a helper, stage one hunk, and the thread keeps every message (2026-10-21)

`p` in the agents panel or a helper's thread pauses the helper (SIGSTOP to its process group; the board shows Paused) and `p` again lets it go on; a stop wakes a paused helper first (new API method `job.pause`). In a file's diff in the git view, `]` `[` choose a hunk and `h` stages it alone (or unstages it from the staged diff). A helper's thread now keeps every message the parent sent it, not only the one still unread (`messages.jsonl` in the job's folder).

## Pull, switch branch and buttons in the git view; inline blame (2026-10-21)

The git view has a commit message box and `✓ Commit`, `↓ Pull` and `↑ Push` buttons. `P` (or the button) pulls, and a click on the branch (or `b`) lists the branches to switch to, or makes a new one from a typed name. The line the cursor is on ends with its blame, faint, as GitLens shows it: who changed it, when, and the commit.

## ⌃q quits from anywhere (2026-10-21)

`⌃q` quits `rig shell` from any screen, including while a file is edited (it asks first when edits are not saved; `⌃q` again quits anyway) and in the launcher; the footer shows it. `⌃d` on an empty reply box quits too, as in codex and claude.

## Go to definition in the code tab (2026-10-21)

`F12`, or a `⌃` or `⌥` click on a name, opens where it is defined: an import's path opens that file, a name imported from the repository opens its file at the definition, any other name is found in the file, its folder or the repository, and a package's name says it is outside the repository. `⌃t` goes back. The palette and the explorer now list new, untracked files too.

## Git in the code tab: blame, change marks, source control (2026-10-21)

The code tab now has git as an IDE does. The gutter marks the lines added, changed and removed since the last commit. `b` shows blame beside the line numbers (who last changed each line and when) and the cursor line's author, age and commit in the status bar. `g`, or a click on `GIT` above the explorer, turns the side bar into source control: the branch (ahead and behind its upstream), staged and changed files, and the recent commits; `space` stages or unstages, `a` stages everything, `⏎` shows a file's diff, `u` discards after a question, `c` commits what is staged, and `p` pushes through the ship screen. Hover now underlines only the text of a button or a file name, not the padding around it.

## Switching parent keeps the conversation (2026-10-21)

Switching parent used to start a new conversation, because one CLI cannot resume another's session. Now the conversation goes on: the new parent takes it over with your next message (`chat.send` with the session and another `parent`), told once what was said since it last took part (the last 12 messages, each shortened), and from then on its own session carries it. Each parent keeps its own native session, so switching back resumes it. The transcript marks each switch (a `switch` entry) and names the parent of each answer (`parent` on each entry); the shell shows a divider and, with several parents, who wrote what. On opening, `rig shell` picks up the newest conversation whichever parent had it. `/new` starts a clean one.

## A helper's thread with the parent, finished helpers in the panel (2026-10-21)

The agents panel kept only the helpers at work, so a helper's thread could not be opened once it finished. The panel now also lists the last five that finished, with how each ended, and `⏎` opens any of them. The thread reads as a conversation between the parent and the helper: the task the parent gave it, its work step by step, a message waiting for it, a question it asks, what it reported and how it ended (new API method `job.thread`).

## The changes of a job that edited in place (2026-10-21)

The changes tab showed only the file name for a job that edited the repository in place (no worktree): Rig had kept just the files' fingerprints. It now keeps the files' contents in Git's object store when a job begins and finishes (`change-contents.json` in the job's folder; best effort, the evidence does not depend on it), and `job.diff` shows exactly that change (`source: contents`), even after later edits. A job from before this shows its files as they are now against the last commit (`source: working tree`), labelled so.

## Every model in /model, a fuller code editor, and tabs that close (2026-10-21)

`/model` now lists every model the parent offers: what the CLI itself lists (codex's own model list, `opencode models`, `pi --list-models`, `omp models`), claude's aliases, the models in the CLIs' own files and the ones Rig routes them to (new method `chat.models`, cached for an hour). Choosing one no longer fails with "parent_model.codex is not a setting the board changes": that came from an API server started before the update, still running the old code. A server now leaves when Rig's files change under it (once idle), and `rig shell` replaces one that runs other code (`api.ping` reports its `build`).

The code tab's editor does more: a click in the file starts editing there (`⇧`-click selects, a third click selects the line), `⌃r` finds and replaces (one at a time or all), find says which match of how many, `⌥↑` `⌥↓` move lines and `⇧⌥↑` `⇧⌥↓` copy them, `⌃l` selects lines, `⌃o` goes to a function or type, the matching bracket and the word under the cursor are lit (`⌃]` jumps to the bracket), and `⌃p` and `⌃k` work while editing. Each tab has a `×` to close it (a middle click closes it too), and a row of tabs too long for the window keeps the open one in view with `‹` `›`.

## Choose the parent's model, and Settings in the top bar (2026-10-21)

`/model` lists the parent's models (its own setting, claude's `opus` `sonnet` `haiku`, the models codex's config names, or any name typed) and keeps the choice for all repos; the parent gets it as its own `--model` from the next message (`settings.app` reports `parent_models`, `chat.status` the model in use and `model_chosen`; new setting `parent_model.<parent>`). Settings › General shows the parent's model with `⏎ change`. The top bar has a `settings` button, and a click on the parent switches parent.

## The code tab edits as an IDE does (2026-10-21)

Editing a file in the code tab now works as an IDE: a selection with the mouse, Shift and the arrows, or `⌃a`; copy, cut and paste (`⌃c` `⌃x` `⌃v`, the line with nothing selected); undo and redo (`⌃z` `⌃y`); indent and outdent (`⇥` `⇧⇥`) and comments (`⌃/`); brackets and quotes that close themselves and `⏎` that indents; find in the file (`⌃f`, then `⏎`; `n` `N` while not editing) and go to line (`⌃g`). Each open file keeps its own unsaved changes across tabs (`⌃PgUp` `⌃PgDn`), marked `◆`; `esc` keeps them, and closing a tab or quitting asks first. The explorer makes (`n`), renames (`r`) and deletes (`d`) files. The wheel scrolls the file without moving the cursor.

## The mouse places the cursor and selects in the reply box (2026-10-21)

As in codex's and grok's box, a click in the reply box puts the cursor there, dragging selects text (and copies it), a double click selects a word, and Shift with the arrows, Home and End selects from the keyboard; typing, a paste, `⌫` or Delete replace or remove the selection. While a file is edited in the code tab, a click puts the cursor where you click.

## The panel docks in a wide terminal, and the code tab is an editor (2026-10-21)

Following the design's updated shell spec, the agents panel stays docked beside the main view in a terminal 160 columns wide or more (`⇥` hides it) and floats as a card over the faded screen in a narrower one. The code tab follows the new Code board: an explorer of the whole repository with folders that open and close, a filter, and the helpers' `A`/`M` marks; open files as tabs (`⌃w` closes); a breadcrumb to the code around the cursor; marks on the lines a helper's change touches; `x` to have the parent explain the selected lines; a status bar; and `e` to edit the file in place (`⌃s` saves), with `E` for `$EDITOR`.

## The box edits as codex's and grok's do (2026-10-21)

Every screen of the app and the launcher keeps a row of room above its top bar and below its footer. `⌥⏎` (or `⌃j`) starts a new line in the reply box, which grows with the message and wraps long lines; `↑` `↓` move between its lines and Home and End go to a line's ends. The cursor is a block on the character under it, as in codex and grok, so moving it no longer opens a gap in the text. `←` `→` move the cursor in the reply box and typing goes in where it is, with word moves (`⌥←` `⌥→`), `⌃a` `⌃e`, Home and End, Delete, `⌃w`, `⌥⌫`, `⌥d` and `⌃u`; `↑` `↓` bring back the messages you sent, the conversation's own included, and `↓` past the newest restores your draft.

## The box names skills as each parent does (2026-10-21)

The parent's skills are named in the box as its own box names them: `$name` with codex (and not under `/`), `/skill:name` with pi and omp, `/name` with claude and the others, where `$` stays plain text.

## Select and copy, $ skills, and a thread that scrolls (2026-10-21)

Dragging over the conversation, a thread or any screen selects text and letting go copies it (OSC 52, also through tmux), as in opencode and grok; a click now happens as the button comes up, so a drag never clicks. `$` in the box lists the parent's skills, as in codex (with codex's bundled `.system` skills), completes the word in place, and the parent is told where each named skill is kept. A helper's thread wraps its lines and scrolls with the wheel and PgUp/PgDn (`G` follows the newest again), and both it and the conversation stop at their first line. A long conversation draws in about 1.5 ms instead of 35 ms, so scrolling keeps up with the wheel.

## The mouse, the parent's skills, and files dropped in (2026-10-21)

`rig shell` works with the mouse as codex and grok do: a click on a tab, a footer key, an answer chip, a palette or `/` menu row, a helper in the agents panel, a Settings value or any other row does what its key would; a click outside an overlay closes it; the wheel scrolls the conversation, the diff and the code; what the pointer is over is underlined. `RIG_MOUSE=0` turns it off. `/` also lists the parent's own skills and slash commands (new API method `chat.commands`), and for a parent whose headless mode cannot run them Rig sends the command's prompt or an instruction to use the skill. Files dragged onto the window go in the box as `@` references. Fixed: the first settings load while the server was still starting said "Could not save the setting".
## A splash, rig-agent, and Settings that apply on ⏎ (2026-10-21)

`rig shell` opens on the design's rig mark while it starts: the stem and three dots pulsing in turn, the wordmark and what it is opening; Ghostty and kitty draw the mark as an image, other terminals in blocks; any key skips it and `RIG_SPLASH=0` turns it off. `rig agent`, `rig-agent` and `agent-rig` open the shell as `rig shell` does (`rig setup` links the two commands next to `rig`). In Settings, `←→` now only picks a value, shown underlined, and `⏎` applies it, so the theme no longer changes as you move.
## Every ADE screen checked against the design again (2026-10-21)

Each terminal screen was compared with its design board and brought in line: cards and panels on a white surface with the screen faded behind overlays, the agents panel full height with its own keys and today's allowed commands, the palette, the new-helper card, the launcher (light theme, `~/code/`, `⌃n` new project, running agents per project), the first conversation (repo facts, `@` to add a file), changes, done today, ship, search (case, word, regex, path filter, `⌃r` replace after a question), a preview page (markdown, tables with `/` to filter, pictures inline in Ghostty and kitty), Settings in the design's layout with new real settings (when rig starts, ship default, tab size, which events notify you and how), the no-agent screen and the problem screens.
## The conversation follows the updated design; switching parent sticks (2026-10-21)

The conversation screen follows the design's updated `Light-TUI-Focus`: agents shown as the design's logo tiles in Ghostty and kitty (kitty graphics Unicode placeholders, also through tmux; `RIG_LOGOS=0` turns them off) and as its letter tile elsewhere, a full-width footer under a rule (`d see changes`, `← → pick answer`, `⏎ send` on the left; `u undo`, `h hold`, `⌃p files`, `? help` on the right), answer chips picked with `← →` and sent with `⏎`, a light reply box, a change card with the files, lines and checks, and the parent's inline markdown without its markers. The changes and code screens share the footer. Fixed: after switching parent (or starting a new conversation), the next status poll brought the old parent's conversation back, so claude stayed the parent.
## rig shell opens the design's ADE screens (2026-10-21)

`rig shell` now opens the app from the design's "ADE screens" page: one centred conversation in the light theme with the top bar and its three tabs, the first-conversation screen with suggestions, the agents panel on `⇥`, `⌃k` commands (ask, files, commands), "Who do you want to talk to?", the "claude started codex" card, a helper's thread, the changes tab with each helper's real diff (new API method `job.diff`) and comments to it, the code tab with selection, find and `$EDITOR`, search (`⌃f`), markdown and CSV previews, and Settings with its sections (real settings only, new API methods `settings.app` and `settings.set`) a terminal bell, the first-run steps (parent, helpers, where agents work), the no-agent, signed-out and sandbox-stopped screens, and the launcher that opens, clones or sets up a project. Done today lists what helpers applied, with their checks and review, and the commands allowed, with undo (a revert of the merge, after a confirmation, then the parent is told), redo and the diff, and hold keeps a helper's branch from being merged until released (new API methods `applies.today`, `applies.undo`, `applies.redo`, `applies.hold`); ship pushes what is ahead of the upstream or opens a pull request with `gh` (`ship.status`, `ship.run`). The jobs board stays as `rig shell --layout board`. The design's pro and paid screens and sign-in are left out.

## The Session screen matches the ADE mockup cell for cell, with an ask line that has the keyboard (2026-10-21)

The Session screen is now the ADE mockup line for line (at 122x38, border included), and larger windows are filled inside the same border with the side columns kept and the conversation grown. It has the mockup's one ground (no tinted panels), its rule-and-`›` ask line, whole-row job colours with short second lines (`claude · job-7f2 · ASK`, `opencode · finished 4m`), `↓ N more below` on the last row, the workspace in short words (new `brief` in `workspace.status`) with `u up  x shell  d doctor`, `Parent · claude opus` (new `model` in `chat.status`), a job's request in the conversation with `a allow  d deny  m message`, the mockup's attention line and footer, and its tab bar. The screen opens as drawn, with the jobs holding the keyboard and the mockup's footer; `Tab` moves between the panes as that footer says (`Tab`, `i` or `m` reach the ask line). The whole window is filled inside the mockup's thin border. `/` lists commands (`/parent`, `/queue`, `/new`, `/stop`, `/jobs`, `/env`, `/help`, `/quit`). A project with no parent opens a parent picker that saves the choice in `.rig/harness.toml` (new API method `chat.parent`). While the parent works, a spinner shows the elapsed time and the answer streams in (`chat.transcript` returns it as `partial`). Themes, as in the design's Settings › General: `rig` (the mockup's, default), `light`, `dark`, `material` and `dracula`, chosen on the Settings tab (`←→`, `g` for all repos or this repo), with `/theme NAME` or with `rig shell --theme NAME` (new API methods `ui.theme` and `ui.set_theme`).

## Every parent in the conversation, and approvals answered there (2026-10-21)

The Session screen's conversation now runs all seven parents (claude, codex, grok, opencode, omp, pi, agy) in their headless modes. claude's and codex's approval requests wait in the conversation as the mockup's `parent › asks to run:` block and are answered with `a`/`d` (new API method `chat.answer`); codex runs as `codex mcp-server` for this and falls back to `codex exec` when that mode is unavailable. Parents that cannot ask headless say so. Built against the CLIs' documented formats and tested with stand-ins; no real parent CLI was run.

## One screen in both modes, in the mockup's colours, with the conversation in the middle (2026-10-21)

`rig shell` now opens the design's one screen in a tmux window (the earlier arrangement is `rig shell --layout split`), so it looks the same as `rig shell --no-tmux`. The Session screen's middle column is the conversation with the parent, as in the mockup: `you ›`, `parent ›`, tool calls, and a `› Ask the parent, or /queue to park a task` box; `p` still runs the parent's own screen there. The board paints the mockup's #eeeeee on #121212 over every cell whatever the terminal theme, keeps the mockup's column proportions on wide terminals, titles the middle column `Parent · codex`, shows each job's worker and id, and no longer flashes "API not running" while it starts. `shell.parent` and `chat.send` take a parent name (`rig shell --parent NAME`).

## The board matches the design mockup (2026-10-20)

The `rig-shell` board now follows the ADE mockup. **Tabs:** Session, Jobs, Queue, Workflows, Env, Settings (Workspace and Fleet became Env, the program pane became Session, the log is a screen under Jobs: `l`; the old names still work as `--tab` aliases). **Session screen:** on a terminal of 120x30 or more, the workspace (now with worktrees and memory) on the left, the parent's own terminal in the middle, jobs and queue on the right, and the selected job's diff, checks, contract and ports docked below; the header names the container. **Settings** is a read-only view of what `.rig/harness.toml` makes effective. **Look:** the mockup's palette, a dark selected-row bar, an attention-first Jobs list with a "Finished" line, the mockup's footer keys and a `?` key list. **Job titles** are the task, not the first line of the worker preamble: a launch stores its `case` as the title, and older briefs are read with the preamble sentences dropped. New API method `settings.view` (read-only). `rig shell --no-tmux` opens on Session. See [the guide](ade-shell.md#the-board).

## Native board, web board and adapter packages (2026-10-20)

New and opt-in. **Adapter packages:** a worker CLI is added as `adapters/<name>/adapter.json` (a command template, how the job-scoped MCP config reaches it, permission flags per mode, container support, an output decoder) with no code change; `rig adapters check DIR` runs it through the real launcher with a stand-in CLI. The ten built-in workers are unchanged. **Board without tmux:** a Logs tab follows the selected job as plain text, `s` opens a shell in the job's container (`dc exec --no-start`), and `rig shell --no-tmux` runs the board alone. **`rig web`:** a read-only board in the browser, 127.0.0.1 only, token and Host checks, an allowlist of read methods, text-only rendering. **Experiment:** `rig chat` and a Chat tab drive a headless `claude` or `codex` parent, off unless `RIG_EXPERIMENT_CHAT=1`, tested only against stand-in parents. `bin/rig` stays bash (decision recorded). See [adapters](adapters.md), [the guide](ade-shell.md) and the [design note](journals/261020-phase-4-design.md).

## Parallel writers in worktrees, evidence drawer, fleet and environment recovery (2026-10-20)

Opt-in. `[worktrees] mode = "per-job"` runs each write job in its own git worktree and branch, so two writers on the same files can run at the same time. Overlap is decided when you accept a job: `rig_job_accept` refuses until the branch changes only its declared files, merges cleanly onto the base and does not collide with uncommitted work, then merges it and removes the worktree. `[worktrees] container = "per-job"` gives each worktree its own dc-cli container. `rig worktrees plan, create, commit, remove` manage them, and `rig doctor` checks the mode and git version. The board gains an evidence drawer (`v`: where it ran, the diff, the contract, checks, ports) and a Fleet tab with the engine's containers and observed CPU and memory. `rig env recover` shows the one step `dc recover` proposes and applies it only for a confirmed plan whose action is `colima_start`, `create_nets` or `take_ports`, with an audit line in `.rig/recovery/environment.jsonl`; the Workspace tab offers the same with `R`. Default behavior is unchanged. See [the guide](ade-shell.md#parallel-writers-in-worktrees) and the [design note](journals/261020-worktree-merge-gate-design.md).

## Workers in the workspace container (2026-10-20)

Opt-in. With `[sandbox] provider = "devcontainer"` and a `[sandbox.workers]` choice (`mount-ro` or `none`) per worker, `claude` and `codex` run through `dc exec --no-start` under a supervisor that ends the agent's whole process tree on a stop or when the host heartbeat stops for 15 seconds; every other worker is refused with the reason. Rig probes the container before each live launch and refuses with a specific reason, never falling back to the host. Acceptance checks run in the container too, and job and check records say where they ran, which the board's detail pane shows. A localhost URL on a port dc-cli does not forward is refused by the browser lane. `rig env init --write-devcontainer` writes a container config that mounts the repository and the Rig runtime at the same paths, `rig env ports` lists forwarded ports, and `rig doctor --workspace` reports each enabled worker. Default behavior is unchanged. Verified against dc-cli 0.23.0 and Docker with a stand-in agent, not yet with the real agent CLIs; see [the guide](ade-shell.md#running-workers-in-the-container).

## The Rig shell, workspace panel and environment commands (2026-10-20)

New, all optional and off unless you use them. `rig shell` opens a tmux layout with your parent CLI, the new Go board `rig-shell` and a log pane. The board reads everything through the local API, shows Jobs, Queue, Workflows and a Workspace tab, and can allow, deny, stop and enqueue. `rig init --sandbox` and `rig env up | down | status` manage a workspace container through dc-cli without editing your own devcontainer files. `rig doctor` gains `workspace`, `dc-cli` and `rig-shell` checks, and `--workspace` adds dc-cli's own diagnosis. `rig setup` builds `rig-shell` when Go is installed (`--no-shell-binary` skips) and `rig shell build` rebuilds it. New API methods: `workspace.status`, and `board.snapshot` now carries a ready-to-render `view`. Workers still run on the host; `[sandbox] provider = "devcontainer"` remains refused at launch. The dc-cli commands Rig relies on are assumptions not yet verified against a release, and the version pin in `templates/dc-cli.json` is empty. See [the shell guide](ade-shell.md).

## One local API for the board and the companion (2026-10-20)

The board's enqueue, stop, answer and recovery-guidance actions now go through the local API (`queue.add`, `queue.cancel`, `job.cancel`, `job.allow`, `job.deny`, `recovery.guide`). An action falls back to the old direct call only when the API server cannot be reached at all; a request that may already have reached the server is never re-run, and an error the API reports (a disabled project, an unknown item) is shown, not bypassed. Tool results now include a `structured` field where the MCP tool returns one.

The tmux companion's separate `control.sock` is gone. The API server now hosts the same companion service: `ui.request` carries the old operations unchanged, `events.list` and `events.ack` expose notices, and every call needs the API token. `rig ui serve` starts that server. It stays up while a companion session is registered or an action is running and exits after 15 idle seconds otherwise. Companion behavior, receipts and notices are unchanged. Curses board settings panels (Jev key, picker engine, objective, routing domains) still edit configuration directly.

## Board reads through the local API (2026-10-20)

`rig tui` now fetches its jobs, queue, slot, settings and workflow data from the local API (new read-only `board.snapshot` method). If no API server answers, the board starts a detached one that exits after 60 idle seconds and, for that frame and until the server is up, falls back to the previous direct reads, labelled "direct read, API unavailable" in the status line, so the board never blanks. `rig api serve --idle SECONDS` sets the idle exit (default: never). Set `RIG_TUI_API=0` to keep fully direct reads. Board actions (enqueue, stop, answer, routing and key settings, recovery guidance) and the tmux companion are unchanged in this step.

## MCP tool profiles (2026-10-20)

`.rig/harness.toml` gains an optional `[mcp] profile = "full" | "core"` (default and template value `full`, so nothing changes). `core` stops listing eight cost-reporting, benchmark and analysis tools (billing report, import and sync; benchmark create, outcome and report; routing report; task timeline) to shrink the parent's tool list; everything on the launch, wait, check, accept, close, workflow and recovery path stays. `RIG_MCP_PROFILE` overrides the file, and unrecognised values mean `full`. Profiles only filter `tools/list`: they add no authority, hidden tools remain callable by name, and the worker child set is always the same eight tools. `rig doctor` reports the active profile as an optional check.

## Local API v1 (2026-10-20)

`rig api serve` starts a versioned JSON-RPC 2.0 server on a private Unix socket (mode 0600, in the 0700 runtime directory) bound to one repository; `rig api call METHOD [JSON]` and `rig api methods` are the clients. Every request carries the token from `api.token`. Methods map one-to-one onto existing parent MCP tools (session, doctor, status, jobs, queue, workflows, allow, deny, cancel, message), so project-enabled and ownership checks are unchanged, a client-supplied `repo` is ignored, owner tokens are redacted from every response, and a worker child cannot start the server. `docs/local-api.md` is generated from the method registry and a test keeps them in step. Nothing changes for existing workflows; the board and companion move onto this API in a later step.

## Sandbox provider seam (2026-10-20)

`.rig/harness.toml` gains an optional `[sandbox]` section with `provider = "host"` (the default and today's behavior). `scripts/sandbox_provider.py` defines the provider interface a container backend will implement; the host provider changes nothing. `provider = "devcontainer"` is reserved: `run-worker.sh` refuses to launch with it, and so does any unknown value, rather than silently running on the host. `rig doctor` lists the provider as an optional check. Existing repos need no change.

## OpenCode cheap-model policy (2026-10-15)

Every install and update now limits OpenCode children to three exact OpenCode Go selectors: `opencode-go/glm-5.3-flash` (default, profile `opencode-glm-5.3-flash`), `opencode-go/deepseek-v4.1-flash` (second, `opencode-deepseek-v4.1-flash`), and region-dependent `opencode-go/muse-spark-1.3-contributor` (optional third, `opencode-muse-spark-1.3-contributor`). OpenCode Go is required; the selectors must be listed by `opencode models` for your account and region. Rig does not set up authentication, purchase anything, enable OpenCode, update its binary, or edit your global OpenCode configuration.

Behavior changes:

- OpenCode profiles and legacy defaults cover explore, mini, bulk, implement, and verify only, with an empty effort and no `--variant`. OpenCode no longer has hard, review, or strong-tier profiles; legacy hard/review picks skip OpenCode and use the normal fallback to other workers or the parent.
- Explicit overrides are enforced on every path: smart and legacy picks, manual and MCP launches, native-child admission, and the direct `run-worker.sh` wrapper. Unsupported, look-alike, or empty models, any effort, and hard/review roles are rejected with the approved list. Nothing is silently substituted, and Rig does not retry another model after a runtime quota or model failure.
- The exact selector must appear in the OpenCode catalog before launch. A missing CLI, authentication, or catalog entry makes OpenCode unavailable for that pick while other workers keep their existing routing.
- New canonical providers `zai`, `deepseek`, and `meta` identify the model family across transport prefixes (`opencode-go` is a transport, not a provider), so review independence compares actual families. Billing scopes and provider enums include them.
- The profiles `opencode-gpt-5.4-mini-minimal`, `opencode-gpt-5.6-luna-high`, and `opencode-gpt-5.6-terra-max`, and OpenCode `openai/gpt-*` selectors, are removed. Smart mode rejects a `.rig/routing.json` that still references them with a migration message; legacy mode ignores it. Files on disk are never rewritten. Disallowed overrides of the new profiles fail validation clearly.

No performance, quality, or pricing claims are made for the approved models; their built-in capability scores are neutral. Schema versions are unchanged. See [OpenCode cheap-model policy](smart-routing.md#opencode-cheap-model-policy).

### Rollout / restart

Install or update the runtime as usual (the new `scripts/opencode_policy.py` ships with the other scripts), then fully restart parent and MCP sessions before the next admission. Replace removed profile IDs in `.rig/routing.json` and any OpenCode selector overrides before using smart mode. Confirm `opencode models` lists the approved selectors.

### Rollback

Reinstall the previous revision with the runtime rollback procedure and restart parent and MCP sessions. Rollback does not delete routing data or touch your OpenCode configuration.

## Task-domain routing v1 (2026-09-30)

Smart routing now accepts a task domain separately from role and capability tier: `general`, `ui-design`, `frontend`, `ui-verification`, `research`, `backend`, `debugging`, and `review`. CLI pick/session and job start use `--task-domain` and repeatable `--research-source`; MCP pick/session/job start/job launch accept `task_domain` and `research_sources`. Workflow nodes carry the same inputs. Explicit domain wins; omitted domains use bounded English inference without changing an explicit role.

Schema 4 `.rig/routing.json` adds per-domain ordered `preferred_profiles` and `fallback: scored|parent|none`. The first eligible preference wins. Existing schema 1–3 configurations remain valid. Domain policies never enable workers or relax role, tier, availability, catalog, or independent-review provider gates. High-risk tasks still require strong profiles. Configured domains run their preferences and fallback locally instead of the direct-parent shortcut, ordered picker, or Jev. Model preferences and capability scores are heuristics, not new benchmark claims or guaranteed model winners.

UI design and visual/browser verification remain parent-only. Research workers must be explore/read with existing readable repository-contained source files; source acquisition without those inputs stays with the parent. Workers gain no browser, vision, Figma, or computer-use access. The parent must separately verify its tools and authorization.

Picks, explanation output, and attempt-bound routing sidecars include domain evidence. Admission revalidates the current domain policy and source access, and research sources are protected read scope. Changed policy or conflicting inputs require a new pick. Manual and legacy launches cannot silently discard an explicit domain/source contract. No ownership, queue, acceptance, or cancellation rule is relaxed. Policy version 2 requires domain evidence on every new smart launch; re-pick older policy-v1 selections. Historical sidecars remain readable.

See [Smart routing](smart-routing.md#task-domains) and the [example domain configuration](examples/task-domain-routing.json). The example uses existing Sonnet, Luna, and Grok profile IDs as project customizations; it does not change default pins or enable those workers.

### Rollout / restart

Stop new admissions, finish or cancel existing work, confirm stopped, accept or close scopes, and preserve routing, queue, reservation, and workflow data. Update every launcher and managed source skill/protocol, then fully restart all parent/MCP sessions before new admissions. Validate merged schema 4 configuration with `rig doctor` and inspect domain picks with `--explain`. A tested checkout is not an installed or live-runtime-accepted upgrade; mixed-version admission writers are unsupported.

### Rollback

Remove `domains` to return to the existing smart picker preferences, retaining only fields supported by the chosen schema. Smart mode still applies built-in domain classification and parent-only boundaries. For the old ladder, set `[routing] mode = "legacy"` and stop passing domain/source inputs; legacy rejects explicit domain contracts. Re-pick after any policy change. For a runtime rollback, use the same stop/confirm/accept-or-close/preserve/restart procedure and restore a routing config compatible with the older runtime. Do not delete job or workflow data.

## Claude Sonnet 5.5 and computer-use opt-in (2026-09-30)

Claude Code implement/verify and the wrapper default now select `claude-sonnet-5-5` at effort `medium`. The stable profile ID remains `claude-sonnet-5-medium` so existing routing preferences continue to work. Haiku and the already updated Opus 5.5 selectors are unchanged. See the [official Sonnet 5.5 model ID](https://platform.claude.com/docs/en/models/sonnet-5-5/overview). Rig launches Claude Code rather than constructing Messages API requests; live model availability still depends on the installed Claude Code and account.

Globally installed computer-use and computer-test skills no longer select Rig for a generic request. Rig requires an enabled project, available parent MCP, and an opted-in available backend. Otherwise follow available host-native capabilities and their instructions. Explicit Rig requests report setup blockers without silently choosing another provider or enabling anything. Permission denials on a selected Rig path still cannot be bypassed. CU and BrowserSkill action tools are hidden while the project is disabled or uninitialized, even if backend flags remain enabled.

Refresh managed project skills and protocol text, then safely restart parent/MCP sessions using the existing rollout procedure. This change does not enable a project or backend. Historical job metadata and stable routing profile IDs are not rewritten.

## OpenAI Luna and Claude Opus pins (2026-09-23)

Codex explorer and routine Luna pins are `gpt-6-luna` at effort `low` on profiles `codex-explorer-low` and `codex-luna-low`. Role limits stay the same: the explorer profile is explore-only. Codex hard and review stay `gpt-5.6-terra` on `codex-terra-medium` and `codex-terra-high`.

OpenCode standard Luna is exact selector `openai/gpt-6-luna` with alias `gpt-6-luna` on profile `opencode-gpt-5.6-luna-high`. That profile stays `catalog_required`. OpenCode cheap and Terra pins are unchanged.

Claude Code hard/review and OMP/Pi review selectors are `claude-opus-5-5`. Cursor review is `claude-opus-5-5-thinking-high`. Profile IDs stay `claude-opus-5-high`, `omp-claude-opus-5-high`, `pi-claude-opus-5-high`, and `cursor-opus-thinking-high`.

Default fast/standard worker order is Codex, Grok, Claude, conditional Devin, conditional MiMo, OpenCode, OMP, Pi, agy. Strong/review is conditional Devin, Claude, Codex, Grok, OpenCode, OMP, Pi, agy. MiMo is fast/standard only. Devin stays disabled unless the user enables it; MiMo stays opt-in. Preference order does not enable workers and does not bypass live-parent exclusion or other gates.

This supersedes the 2026-09-16 shipped explorer selector.

### Rollout / restart

Install the checkout into `~/.rig`, then fully restart parent and MCP sessions before the next admission. Mixed-version admission writers are unsupported.

### Rollback

Restore Codex and OpenCode Luna selectors to `gpt-5.6-luna` and `openai/gpt-5.6-luna` (alias `gpt-5.6-luna`), Opus selectors to `claude-opus-5` (Cursor `claude-opus-5-thinking-high`, alias `anthropic/claude-opus-5`), and fast/standard worker order to Grok, Claude, OpenCode, OMP, Pi, agy, Codex. Fully restart parent and MCP sessions. Rollback does not delete routing data.

## Grok 4.7 CLI pin (2026-09-21)

The Grok CLI strong pin is `grok-4.7` at effort `high` on profile `grok-4.7-high`. That covers implement, hard, verify, and review. Explore, mini, and bulk stay on `grok-4.5` low (`grok-4.5-low`).

OMP, Pi, and Cursor keep their current Grok 4.6 selectors. Pi and the local OMP catalog do not list `grok-4.7`. `grok-4.7-build-fast` stays off the cheap rung: it is the same model at twice the token rate.

A `.rig/routing.json` preference that names `grok-4.6-high` no longer matches a built-in profile. Point it at `grok-4.7-high`.

### Rollout / restart

Install the checkout into `~/.rig`, then fully restart parent and MCP sessions before the next admission. Mixed-version admission writers are unsupported.

### Rollback

Restore the Grok CLI rows in `scripts/route.py` to `grok-4.6` / `high` and the profile id `grok-4.6-high`.

## Live TUI and Codex Explorer (2026-09-16)

Accepted release for live TUI activity, Codex JSON streaming, and explorer defaults.

Rig TUI now shows normalized live Pi and Codex assistant replies, concise tool status, errors, and final output. Reasoning, raw arguments, and raw tool output remain hidden. Raw logs remain available.

The Codex worker uses JSON streaming.

The shipped explorer default in this release was `gpt-5.6-luna` on profile `codex-explorer-low`. Unsupported `gpt-5.3-codex-mini` is no longer the shipped explorer selector. The 2026-09-23 note moves that selector to `gpt-6-luna`.

`gpt-5.3-codex-spark` is an opt-in `codex-explorer-low` selector override. It is explore-only and cannot be assigned to write roles:

```json
{
  "schema_version": 1,
  "profiles": {
    "codex-explorer-low": { "selector": "gpt-5.3-codex-spark" }
  }
}
```

### Rollout / restart

Finish or cancel existing work, confirm stopped, accept or close scopes, then fully restart all parent/MCP sessions before admitting new work. Mixed-version admission writers are unsupported.

Spark smoke-test: after the override, explore-only routing may select Spark; write roles must still refuse it. Do not make Spark a baseline.

### Rollback

Remove the `codex-explorer-low` Spark selector override to restore the shipped explorer selector (`gpt-6-luna` as of 2026-09-23).

## Adaptive Workflows (combined wait-cancel rollout)

Parent-owned DAG orchestration is locally parent-verified. Documented contracts:

- Durable `.rig/workflows/<id>/` holds `spec.json`, `state.json`, `events/`, and `owner-credentials.json` (mode 0600).
- `[orchestration]` mode is `adaptive` (default) or `single`; `max_nodes` is 12. Queue and worker caps remain authoritative.
- Parent MCP: `rig_workflow_create`, `rig_workflows`, `rig_workflow_show`, `rig_workflow_advance`, `rig_workflow_wait`, `rig_workflow_extend`, `rig_workflow_resolve`, `rig_workflow_approve`, `rig_workflow_cancel`, `rig_workflow_report`, `rig_job_coordination_reply`. Child after handshake may `rig_job_coordination_request`. CLI: `rig workflows`; `rig workflow create|show|advance|wait|extend|resolve|approve|cancel|report`.
- `verify` is parent/final integration; `review` is independent post-write review. Independent review unavailable stays explicit. Children never spawn or message children. Parent owns the graph, briefs, and acceptance and uses workflow advance/wait.
- Review+seed and parallel writers require file AND resource disjointness. Overlapping writer scopes are rejected, not sequenced.
- UI fields: workflow id, status, accepted/required, running, ASK, blocker, next parent action, title. No estimated progress, savings, or ETA.

### Combined rollout with wait-cancel

Stop new admissions, finish or cancel existing work, confirm stopped, accept or close scopes, preserve data (queue text, credentials, workflow spec/state/events, reservations), update every launcher and managed protocol, then fully restart all parent/MCP sessions. Mixed-version admission writers are unsupported.

### Rollback

Set `[orchestration] mode = "single"`. Rollback never deletes data.

## Native-Child Model Provenance (2026-09-15)

Accepted fix for native-child model provenance on admission and the job board.

- Fresh running native-child admission now requires a selected model and fails before a live job record if selection is missing.
- Selected native children show their model/effort on the job board.
- Parent and historical jobs remain unknown only when actual provenance is unavailable.
- Rollout requires a fully restarted parent CLI/MCP session after update.

## Devin Child Worker

Devin is a child-only worker. It is never a parent and defaults off.

### Opt-in

Enable Devin via routing preferences in `.rig/routing.json`. Default fast/standard order is Codex, Grok, Claude, OpenCode, OMP, Pi, agy. Strong/review stays Claude, Grok, OpenCode, OMP, Pi, agy, Codex.

### Role pins

Exact selectors only:

- `swe-2-medium` — explore, mini, bulk
- `swe-2-high` — implement
- `swe-2-max` — hard, review

### Catalog confirmation

Live catalog confirmation is JSON-only (`devin models list --format json`). No aliases, SWE-1.x, Fusion, defaults, or role mismatch.

### Child wrapper

The wrapper launches with accept-edits and a temporary job-scoped MCP config, then restores the previous config. One Devin job is permitted per repository.

### Rollout

1. Run `rig update`.
2. Per repo: `rig init`, then `rig doctor`.
3. Fully restart the parent CLI / MCP session before admitting new work.

### Rollback

Disable `workers.devin` and remove Devin preference IDs.

## Cost-aware Smart Routing

Optional cost-aware lane for smart mode. Off by default.

### Opt-in

Schema 2 `.rig/routing.json` may set `execution.direct_parent_low_risk` (boolean, default `false`). Schema 1 remains valid. Example:

```json
{
  "schema_version": 2,
  "execution": {
    "direct_parent_low_risk": true
  }
}
```

### Direct-parent eligibility

When enabled, pick returns native parent writes (`execution_strategy=direct-parent`) only when **all** of these hold:

- smart mode
- role is **mini** or **implement**
- complexity, risk, and uncertainty are all **low**
- live parent is an eligible tracked native parent (codex, grok, opencode, omp, pi, or agy) and not excluded

All other roles, medium/high assessments, excluded parents, and legacy mode keep the existing wrapper / parent-fallback path. Catalog confirmation is not consulted on the direct-parent lane.

### Parent lifecycle

Direct-parent jobs still use the same write path as other parent writes: `rig_job_start`, authenticated `rig_job_finish`, deliberate checks, and parent acceptance of the current snapshot. Execution success alone is not verification.

### Token telemetry and reporting

`rig routing report` groups attempts by policy, tier, profile, actual model, effort, and `execution_strategy`, and counts direct-parent separately from wrapper. Token coverage comes only from observed structured worker usage. Totals and costs are never inferred. Unknown usage is not zero and is excluded from token aggregates.

### Rollout / upgrade

1. Finish or explicitly cancel active work; confirm termination and reconcile held scopes.
2. Run `rig update`.
3. Per repo: `rig init`, then `rig doctor`.
4. Fully restart the parent CLI / MCP session before admitting new work.

Mixed old/new admission writers are unsupported.

### Rollback

- Keep smart mode but disable the lane: set `execution.direct_parent_low_risk` to `false` (or omit it / stay on schema 1).
- Or switch to legacy: `[routing] mode = "legacy"` in harness config.

### Not included

No pricing integration, automatic model switching/ranking, or untracked bypass of the parent write lifecycle.
