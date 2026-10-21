# The Rig shell, workspace and environment

An optional terminal layout for working with a parent agent while Rig runs workers. It adds no new way to run work: workers still start through the parent and the same admission, ownership and acceptance rules apply.

## What you get

| Command | What it does |
| --- | --- |
| `rig shell` | The board (`rig-shell`) as the design's one screen in a tmux window, so the session survives a detach. `--parent NAME` picks the parent; `--layout split` gives the earlier arrangement instead: the parent CLI in its own native tmux pane, a narrow board beside it and a log pane |
| `rig shell --no-tmux` | The same screen in this terminal, with nothing else installed |
| `rig shell print`, `rig shell kill` | Show the tmux commands it would run, or remove this repository's session |
| `rig shell build` | Build `rig-shell` from a Rig clone with Go 1.24+ (no project needed) |
| `rig init --sandbox` | Detect the workspace and, for a folder with no devcontainer, store dc-cli's generated config under `.rig/sandbox/override.json`; `--write-devcontainer` turns it into `.devcontainer/devcontainer.json` |
| `rig env up`, `down`, `status`, `ports [--json]` | Start or stop the workspace container, show what dc-cli reports, and list the ports it forwards |
| `rig env recover [--apply ID]` | Show the one next step `dc recover` proposes; apply it only by naming the plan id, and only if Rig allows that action (see [Environment recovery](#environment-recovery)) |
| `rig chat send, show, sessions, cancel, status` | EXPERIMENT, off unless `RIG_EXPERIMENT_CHAT=1` or `[experiments] chat = true`: a headless parent CLI with a Rig-rendered chat ([details](#the-conversation-with-the-parent)) |
| `rig web [--port N] [--open]` | A read-only board in the browser: the same Jobs, Queue, Workflows, Workspace, Fleet and Logs views, and the evidence drawer, from the same server-built data. 127.0.0.1 only |
| `rig adapters list`, `validate DIR`, `check DIR` | Add a worker CLI as an adapter package ([reference](adapters.md)) |
| `rig worktrees status [--json]`, `plan`, `create`, `commit`, `remove --job ID` | Per-job worktrees, below. `status` lists leftover job worktrees and the stopped containers keyed to them, and removes nothing |
| `rig doctor [--workspace]` | Adds `workspace`, `dc-cli` and `rig-shell` checks. `--workspace` also runs dc-cli's own diagnosis |
| `rig api serve`, `ensure`, `call`, `methods` | The local API behind all of this ([reference](local-api.md)) |

`rig shell` needs tmux 3.3+ (not with `--no-tmux`) and a built `rig-shell`; the split layout also needs the parent CLI on `PATH` (`codex`, `grok`, `claude`, `opencode`, `omp`, `pi` or `agy`), while the board layout reports a missing one on screen. It uses its own tmux server unless you are already inside tmux, never binds the F8/F9 companion popups, and reuses the session if one exists for the repository.

## The app (rig shell's default)

`rig shell` (or `rig agent`, `rig-agent`, `agent-rig`) opens the design's ADE screens, after a splash of the rig mark while it starts (the stem is the parent, the three dots its helpers, pulsing in turn; Ghostty and kitty draw the mark itself; any key skips it, `RIG_SPLASH=0` turns it off): one conversation with the parent in the middle of the window, as codex and grok draw theirs, in the design's light theme unless you saved another (Settings › Theme, `/theme`). The board below is still there: `rig shell --layout board`, or `⌃k` › the board.

- **Agent logos:** each agent is drawn as the design's tile: its logo on a white tile ringed in its colour (claude, codex, opencode, pi, cursor, grok), three cells wide. Ghostty and kitty draw them, with the kitty graphics protocol's Unicode placeholders, also inside the tmux `rig shell` opens (it turns on `allow-passthrough` for its window). Other terminals, and agents without a logo, get the design's fallback: the agent's letter on its colour. `RIG_LOGOS=1` or `0` forces either. The logos are their owners' marks, used to name their agents.
- **Top bar:** the mark, the repo and branch, the parent with its tile; the three tabs `1 conversation`, `2 changes`, `3 code`; how many helpers are working, `⌃k` and `⇥ panel`.
- **First conversation:** "What should we work on in my-repo?", the ask box, and four suggestions (`1`-`4` send one).
- **Conversation:** what you asked as a bubble on the right, the parent's answer under it (streamed as it is written, with a spinner and the elapsed time), a card for each helper's work the parent accepted (its files and lines and whether its checks passed), and a request waiting on you (the parent's own, or a helper's "codex asks, via claude") with the answer chips: `← →` picks one and `⏎` sends it, or type. The reply box at the bottom always has the keyboard; `/` lists the commands. The footer under its rule is the design's: `d` see changes, `u` undo the newest applied work, `h` hold the newest unapplied work (both ask first, `⏎` yes), `⌃p` files, `?` help. `d`, `u` and `h` act only with the box empty and something to act on; otherwise the letter is typed, and typing on after `u` or `h` cancels the question and keeps the letters. The parent's inline markdown (code, bold) is shown without its markers.
- **Agents panel (`⇥`):** done today, the agents (the parent, then each helper with what it is doing; `↑↓` chooses), the queue, and the sandbox. `⏎` watches a helper's thread (its log, followed), `s` stops it after a confirmation.
- **Commands (`⌃k`, files with `⌃p`):** type to ask the parent, open a file by name (it opens in the code tab), or run a command: undo last apply, hold all applies (both ask first), switch parent, ship changes, new conversation, queue a task, change theme, search the code, done today, settings, start the sandbox, the board, help, quit. The screen behind fades, as in the design. `@` in the reply box adds a file to your message.
- **Who do you want to talk to?** A project with no parent of its own opens this first: the installed parents, `↑↓` and `⏎` to save the choice in `.rig/harness.toml`. `⌃k` › switch parent opens it again.
- **A helper started:** when the parent starts a helper, a card says so once ("claude started codex"), with the task, its worktree and what it is doing; `⏎` watches its thread, `s` stops it.
- **Changes (`2`):** the design's review of one helper's work: its files across the top (`[` `]` move between them), the diff with numbered lines (removed lines red, added green), what you said to the helper, its explanation, and its checks. `←→` moves to another helper; `c` leaves a comment in the helper's inbox (API `job.message`). The diff is the helper's real one (new API method `job.diff`): its committed branch, or its worktree against its base while it works.
- **Code (`3`):** the file `⌃k` or search opened, with line numbers and colours, a tree of its folder and of the files helpers are changing (a dot in the helper's colour, and "claude is changing this in its copy" above the file). `v` selects lines and `a` asks the parent about them (or `@` adds them to your message), `/` finds a word, `e` opens the file in `$EDITOR`. Markdown is rendered and a CSV is a table (`s` shows the source); an image, PDF or video opens in your default app with `o`.
- **Search (`⌃f`, or `⌃k` › search the code):** a page of its own: the repository's tracked files, with match case (`alt+c`), whole word (`alt+w`), regex (`alt+r`) and a path filter (`alt+p`) in the box; results grouped by file with the word marked and the helper changing each file, files past the first two folded to a count; `⏎` opens a result in the code tab, `⇥` asks the parent instead, `⌃r` replaces the matches on the lines found after a question.
- **Settings (`⌃k` › settings, or `/settings`):** the design's sections (general, agents, routing, approvals, sandbox, editor, git, notify, toml; billing is left out), laid out as the design draws them. `⇥` changes section, `↑↓` the row, `←→` picks a value (shown underlined; nothing changes yet) and `⏎` applies it, `esc` drops the pick, space turns an on/off row over, `g` whether the theme or the bell is kept for all repos or this repo. Real settings only: the theme, the default parent, when rig starts (the last project or the project picker), Rig on or off here (after a question), each helper on or off and `i` to install a missing one, how many work at once, routing's objective (with the table of who routing picks for each kind of task), where agents work and a worktree and container per helper (with the sandbox's state and `s` stop, `u` start, `d` doctor), the code view's tab size, what ship offers first, and notifications: something needs me and work was applied, by terminal bell and/or a desktop notice through the terminal. Shown as they are: versions, the GitHub account gh uses, where clones go, commit signing, the commands allowed today. Toml shows the harness and whether it is valid; `E` opens it in `$EDITOR`.
- **First run:** a project with no parent goes through the design's three steps: who you talk to (1 of 3), who it can hand work to (2 of 3: the installed helpers on or off, and how many at once, saved in `.rig/harness.toml`), and where agents work (3 of 3: a new sandbox, the dev container, started with `rig env up`; or this machine, with no boundary around your files).
- **No agent found:** "Let's get you an agent" (1 of 3): install one for me, with claude code, codex and opencode to choose with `←→`; `⏎` runs the chosen install command on this computer after a question, `r` scans again. (The design's built-in agent and local model are not part of Rig.)
- **The parent signed out:** when its last turn failed for want of a sign-in, `1` opens the parent CLI here to sign in again, and `2`-`4` hand over to another installed parent.
- **The sandbox stopped:** when the project works in a dev container that is not running, the screen lists what the workspace found and offers `1` start it (`rig env up`), `2` dc-cli's safe fix when it proposes one, `3` run on this machine instead.
- **Done today (`⌃k` › done today, or `a` in the agents panel):** each helper's work the parent applied today, what you undid and what you hold, and the commands that were allowed (by the parent for a helper, or by you for the parent; kept in `.rig/applies/allowed.jsonl`), newest first. The chosen change shows its files and lines, the checks it ran and the review of it. `u` undoes it after a confirmation: a revert of its merge commit, so history keeps both, and the parent is then told so it can handle anything built on it; `r` applies it again, `d` opens its diff on the changes tab, and `a` lets the parent apply a held one. Undo and redo need the checkout on the job's base branch with no uncommitted changes, and refuse otherwise (new API methods `applies.today`, `applies.undo`, `applies.redo`).
- **Hold (`h` on the changes tab):** keeps a helper's branch from being merged when the parent accepts it, until you release it (`rig_job_accept` refuses a held job; API `applies.hold`).
- **Ship (`⌃k` › ship changes):** the commits your branch is ahead of its upstream (a helper's merge shown as its task), then `←→` between opening a pull request (pushed to a new `rig/<slug>` branch, titled from the parent's tasks, `e` edits the title, needs `gh`) and pushing to the branch itself, with the checks those helpers ran; `⏎` asks first. Agents never push; you do (new API methods `ship.status`, `ship.run`).
- **Open a project:** `rig shell` where nothing is set up opens the design's launcher: the recent projects (`~/.rig/recent.json`, newest first; "not set up" when Rig is not set up there), a path, or a git URL, which is cloned into `~/code/<name>` with each step shown ("Getting api-gateway ready"). `⌃g` lists your GitHub repositories (with `gh`). The box starts on `~/code/`, `⌃n` names a new project there, and each recent project shows how many agents are at work in it. A project that is not set up asks before running `rig init` in it; a folder that does not exist asks before creating it.

With the box empty, `2` and `3` switch tabs, `?` lists every key, and `1` and `2` still answer a waiting request first. `Esc` clears the box or closes what is open; `Ctrl-C` clears the box, then quits.

Signing in to an agent, plans, billing and the marketplace (the design's pro and paid screens and sign-in) are left out on purpose.

## The board

Tabs, as in the design mockup: **Session, Jobs, Queue, Workflows, Env, Settings**. Every state shows a glyph and a word, and labels, ordering and counts come from the same code as `rig tui`, so the two never disagree. The Jobs list is attention first, with a "Finished" line before the finished jobs. Colours are the mockup's palette (xterm-256, so any 256-colour terminal shows the same thing); the selected row is a dark bar.

| Key | Action |
| --- | --- |
| `Tab`, `Shift-Tab` | Next or previous tab |
| `j`, `k` | Move, or scroll on Env, Settings, logs and the evidence drawer |
| `Enter`, `Esc` | Open or close details on narrow terminals; on Session, `Enter` types into the running program |
| `a`, `d` | Allow or deny the selected worker prompt |
| `e` | Park a task in the queue (`Enter` saves, `Esc` discards; a failed save keeps the draft) |
| `v` | Evidence drawer for the selected job (`Esc` closes it) |
| `w` | Jump to Env |
| `l` | Jobs: follow the selected job's log (`Esc` returns to Jobs) |
| `s` | Interactive shell in the selected job's container (on Env: the repository's), by handing the terminal to `dc exec --no-start`; exit the shell to return. On Session, `p` and `s` start a program in the pane instead |
| `x` | Stop the selected job or queue item, after a confirmation. On a narrow Session tab, close the program |
| `X` | Session screen: close the program in the middle column, after a confirmation |
| `k`, `j`, `G` | Log screen: scroll back, forward, and follow the tail again |
| `R` | Env: apply the proposed environment fix, after a confirmation, when Rig allows it |
| `r`, `?`, `q` | Refresh, the full key list, quit |

The footer shows only the keys that matter on the current screen; `?` lists them all. Pasted text is never run as commands. `RIG_TUI_ASCII=1`, `RIG_TUI_CJK=1` or a non-UTF locale selects ASCII glyphs. If the API server is not running the board starts one and shows "refresh failed" with the snapshot age if it cannot reach it, keeping the last data on screen.

### The Session screen

On a terminal of at least **120 columns by 30 rows** the Session tab is the mockup's screen: the workspace on the left (container, ports, worktrees, doctor, memory), the conversation with the parent in the middle, and the jobs and queue on the right, with the selected job's diff, checks, contract and ports docked along the bottom. The header names the container (`devcontainer a3f9c1`). Job keys (`a`, `d`, `x`, `v`, `j`, `k`) act on the selected job. Wider terminals keep the mockup's proportions (the side columns grow to 44 and 72 columns). The whole screen is drawn in the mockup's colours, #eeeeee on #121212, whatever your terminal's theme.

**The middle column** is the conversation, as the mockup shows it: `you ›`, `parent ›` and the parent's tool calls in blue, then a rule and the `› Ask the parent, or /queue to park a task▏` line. The screen opens as the mockup draws it, with the jobs holding the keyboard and the mockup's footer; `Tab`, `i` or `m` give the ask line the keyboard (and `/` starts a command there). Type and press `Enter` to send. `↑`/`↓` move through the jobs while you type, `PgUp`/`PgDn` scroll the conversation, and `Ctrl-C` clears a draft (and quits on an empty line). Typing `/` lists the commands above the line (`↑`/`↓` choose, `Tab` completes, `Enter` runs): `/parent` chooses the parent, `/queue TEXT` parks a task instead of sending it, `/new` starts a new conversation, `/stop` stops the running turn, `/jobs` and `/env` open those tabs, `/help` lists every key and `/quit` leaves. While the parent works, a spinner shows how long it has taken and its answer appears as it is written (claude with `--include-partial-messages`, codex's message deltas, and the streamed text of the others). Each message runs the parent once in its headless mode and resumes its session (see [the chat](#the-conversation-with-the-parent) for what that means).

**Requests.** When the parent, or a job, wants to run something that needs your approval, the conversation shows the mockup's block: `parent › job-7f2 asks to run:` and the command in amber, the container it runs in, and `a allow  d deny  m message`. Before you type anything, `a` and `d` answer it and `m` starts a message instead (after `m`, `a` and `d` are text again). The line above the footer says who is waiting.

**Panes.** `Tab` moves the keyboard between the three panes, as the mockup's footer says (`Tab pane`): the jobs, then the ask line, then the workspace, then back; the workspace's title is lit while it has the keyboard, and `Esc` on an empty ask line goes back to the jobs. On the jobs the footer is the mockup's: `Enter` opens the job's details on the Jobs tab, `a`/`d` answer its request, `e` parks a task, `v` opens its evidence, `w` the workspace, `x` stops it. On the workspace, `u` starts the container (`rig env up`, after a confirmation), `x` opens a shell in it and `d` opens the doctor's full report on the Env tab. `Shift-Tab` leaves the Session screen for the Jobs tab.

**Choosing the parent.** A project whose `.rig/harness.toml` names no parent opens the parent picker the first time: every parent the chat can run, the installed ones first, `↑`/`↓` to choose and `Enter` to save it as the top-level `parent = "..."` (API `chat.parent`, the same key `rig use` writes). `/parent` opens it again at any time; changing the parent starts a new conversation, because one CLI cannot resume another's session. `rig shell --parent NAME` still picks a parent for one board without saving it. The column's title names the parent and, when its own settings name one, its model: `Parent · claude opus`.

**Size and colours.** The board fills the window inside the mockup's thin border (when the window has the two extra rows and columns for it); a 122 by 38 window is the mockup cell for cell. Wider windows keep the side columns' widths (growing up to a cap) and give the rest to the conversation. Everything sits on the mockup's #121212 with #eeeeee text; the selected job is the mockup's bold #262626 bar.

**Themes.** As in the design's Settings › General, the board has a colour theme: `rig` (the ADE mockup's colours, the default), `light`, `dark`, `material` or `dracula`. The Settings tab's first row shows them; `←`/`→` switch at once, and `g` keeps the choice for all repos (`~/.rig/config.toml`) or only this repo (`.rig/harness.toml`, which wins), both as `[ui] theme = "..."` (API `ui.theme`, `ui.set_theme`). `/theme NAME` does the same from the ask line, and `rig shell --theme NAME` draws one board in a theme without saving it. The design's themes are drawn in true colour where the terminal has it and in the nearest 256 colours where it does not. The left column is the workspace in the mockup's short words (`kind`, `image`, `● up a3f9c1 · 2h`, ports, worktrees, doctor, memory) with `u up  x shell  d doctor` on its last row. `rig shell` turns the conversation on for its board; elsewhere it follows the chat setting. When you need the parent's own screen, `p` runs it in the middle column on an emulated terminal (sized to the column, following resizes; `Enter` types into it, `Ctrl-]` returns to the board) and `X` closes it, which gives the conversation back. On a window this size there is no separate Chat tab. On a smaller terminal the Session tab is the single-program view described under [The Session tab](#the-session-tab-one-program).

### Env and Settings

**Env** is the workspace panel and the fleet panel together (what the old Workspace and Fleet tabs showed; `--tab Workspace` and `--tab Fleet` still work and mean Env). If one source fails, its failure is shown in place and the other is still shown.

**Settings** is read-only. It shows what `.rig/harness.toml` makes effective: the project state and parent, which workers are on, routing, orchestration, queue limits, sandbox and worktree choices, the MCP profile and the experiments, with any bad value flagged and the tab marked `!`. It is read through the same parser Rig uses, and the board has no write path to configuration: change a setting in `.rig/harness.toml` and the board picks it up on refresh.

## Logs and shells without tmux

The log screen (`l` on a job) follows the log of the job selected on the Jobs tab (the job worth watching if none is) as plain text, refreshed every two seconds while the tab shows, with terminal escape sequences removed. Scrolling back stops following; `G` follows the tail again.

**`s`** (outside the Session tab) opens a real interactive shell in the job's container. The server decides the command: `dc exec --no-start <workspace>`, where the workspace is the job's own worktree when its container is keyed by that worktree and the repository otherwise. `--no-start` means a shell can enter a container that is running and never start one. If there is no running container, or dc-cli is missing, the board says why and runs nothing. While the shell is open the board is suspended and the terminal is the shell's; exiting returns to the board.

### The Session tab, one program

The **Session** tab runs one interactive program on a pseudo-terminal inside the board, so the parent CLI and a container shell work without tmux. `p` starts the parent CLI for the project (the server decides which; the same one `rig shell` puts in its tmux pane), `s` starts a shell in the project's container (`dc exec --no-start`). A program starts with the keyboard: **every key goes to it, Ctrl-C included**, except **Ctrl-]**, which returns to the board; **Enter** types into it again. The program sees the real size and is resized with the window; a full-screen program such as `vi` works. `x` closes it (after a confirmation), a program that exits keeps its last screen until you start another, and quitting the board ends it.

Limits: one program at a time; the live screen only, with no scrollback; the mouse is not forwarded; pasted text goes in as plain input (newlines become Enter), so a paste into a shell runs; wide characters (CJK, emoji) take one cell and can misalign. The tmux layout is unchanged and still gives the side-by-side view; on a terminal too small for the Session screen this tab is full-screen.

## The conversation with the parent

Off by default, except in the board `rig shell` starts. With `RIG_EXPERIMENT_CHAT=1` (or `[experiments] chat = true`, exactly `true`) the Session screen's middle column is the conversation, a Chat tab appears on terminals too small for that screen, and `rig chat` works.

**Every parent.** claude, codex, grok, opencode, omp, pi and agy each run in the headless mode Rig's worker launcher already uses for them, and what they say is read the way Rig reads their job logs. claude, codex, grok and opencode continue their own session from message to message; omp, pi and agy cannot, so each message carries the recent conversation.

**Approvals.** Rig adds no permission flags: the parent runs with your own settings. claude asks through Rig's `rig-ask` permission server (`--permission-prompt-tool`, the same one Rig's workers use), and codex runs as `codex mcp-server`, whose exec and patch approvals come to Rig as requests. Either way the request waits in the conversation until you press `a` or `d` (API `chat.answer`), and your answer is recorded in the transcript. If `codex mcp-server` is not available or does not start, that turn falls back to `codex exec`, which cannot ask. grok, opencode, omp, pi and agy have no headless way to ask, so they follow their own settings and the conversation says so once; `p` opens a parent's own screen when you need its interactive approvals.

On the Chat tab: `c` writes a message (`Enter` sends, `Esc` discards, a failed send keeps the draft), `N` starts a new session on the next message, `x` stops a running turn after a confirmation, `k`/`j` scroll and `G` follows the newest. The web board shows the same transcript read-only; it has no way to send or answer.

Messages reach the parent as one argument after `--`, in a prompt file, or on the runner's standard input, never through a shell. Only one turn per session runs at a time. `rig doctor` names the chat while it is on.

**Not verified.** No real parent CLI is available where this was built: the decoders, the claude permission exchange and the codex server protocol (`codex`/`codex-reply` tools, `codex/event` notifications, `elicitation/create` approvals) follow the CLIs' documented shapes and are tested with stand-ins. A transcript lives under `.rig/chat/`; `rig chat sessions` lists them.

## Web board

`rig web` prints a URL such as `http://127.0.0.1:41603/?token=…` and serves until you stop it (`--idle N` exits after N quiet seconds, `--open` opens your browser). The first visit trades the token for a cookie and drops it from the address bar. The page polls the local API every two seconds while it is visible and shows exactly what the server built: labels, tones, glyphs, ordering, sections. It never works out job state itself. Deep links work: `#Fleet`, `#Jobs/<job id>`, `#Jobs/<job id>/evidence`, `#Logs`.

It cannot change anything. The only methods reachable are `api.ping`, `board.snapshot`, `workspace.status`, `fleet.status`, `job.evidence`, `log.follow` and, with the chat experiment on, `chat.status` and `chat.transcript`, from an allowlist (so a method added to the API later is not reachable until someone lists it), each with validated parameters; every request that is not GET or HEAD gets 405. Hardening: it binds 127.0.0.1 and has no host option; the token is 256 bits and compared in constant time; the `Host` header must be the bound address (DNS rebinding); cross-site requests are refused; there are no CORS headers; the Content-Security-Policy allows scripts and styles only from the page's own origin (no inline code, no external loads); and worker-supplied text only ever enters the page as text, never as HTML.

What it is not: a way to answer a prompt, stop a job or enqueue work. Those stay in the terminal board, where a confirmation can be given at the keyboard that is running the project.

## Evidence drawer, Fleet and recovery

**Evidence drawer** (`v` on a job): where the job ran (host, container with name and state, worktree with branch, base and merge state), what changed (file list with added and removed lines from git for worktree jobs, the recorded changed paths otherwise, files outside the declared scope flagged), the acceptance contract with each criterion's outcome, the checks with their exit codes and where they ran, and the forwarded ports observed now. Everything is read from records Rig wrote or from git and dc-cli as they are at that moment.

**Fleet** (the second half of the Env tab): every container dc-cli can see on the engine (`dc ls --all`), this repository's first, then other workspaces with their folder. Running ones show CPU and memory as `dc stats` reports them, and the engine's own cpus and memory. These are observed values only: Rig sums nothing, estimates nothing and never turns them into a cost ([cost accounting](cost-accounting.md)).

### Environment recovery

When `dc recover` has a next step, the Env tab shows it with the command to run. Rig applies a step itself only when the owner confirms that exact plan id and its action is one of `colima_start`, `create_nets` or `take_ports`, which covers an engine that is down and a port held by one of dc-cli's own containers. Everything else dc-cli offers (installing or launching Docker Desktop, anything needing `sudo`, switching Docker contexts, stopping another engine, pruning, starting a sandbox for a folder with no config) is shown as text for you to run; Rig never prunes. Each attempt, applied, failed or refused, adds one line to `.rig/recovery/environment.jsonl` with who asked, the plan, the action and the outcome. A plan that changed between your confirmation and the apply is refused.

## Parallel writers in worktrees

Off by default. `[worktrees] mode = "per-job"` in `.rig/harness.toml` runs each write job in its own git worktree (`.rig/worktrees/<job>`, branch `rig/<job>`, made from the base checkout's current commit) while all job state stays in the main `.rig`. Two writers may then be admitted on the same files at the same time, because neither edits the base checkout. The overlap is decided when the parent accepts a job:

1. the job's branch must change only the files it declared;
2. it must merge onto the base branch as it is now without conflict (`git merge-tree`, git 2.38+; nothing is touched to check);
3. the base checkout must have no staged changes and no uncommitted change in a file the merge touches.

When all three hold, `rig_job_accept` with `next = complete` merges the branch (`--no-ff`), records the merge commit, removes the worktree and deletes the branch. A refused gate names the reason and the paths (`merge_gate:conflict`, `out_of_scope`, `base_dirty`, `base_moved`) and leaves the job, its worktree and its reservation as they were so you can resolve it and accept again. A job that changed nothing is accepted without a merge. Rejecting merges nothing; the worktree is kept until `rig worktrees remove --job ID` (it refuses while a process is running there and keeps an unmerged branch unless `--discard`).

A continuation (`continues_job_id`) of an unmerged worktree job runs in that job's worktree, on its branch, so a native session resume wakes up where it left off and sees the predecessor's edits; no second checkout is made. The branch then carries the whole chain, and accepting any job in it merges everything on it once (the gate's scope check is the union of the chain's declared scopes, and its result names the chain). A predecessor that edited the base checkout, or whose worktree was removed before a merge, cannot be continued in this mode; one that was already merged gives its successor a fresh worktree from the new base. A gate is refused while a process is still running in the worktree. Design: [261020-worktree-continuation-design.md](journals/261020-worktree-continuation-design.md).

Admission still refuses writers that clash on a resource (a port, a URL), exceed the slot limits, or run outside worktree mode against a worktree writer's files. A reviewer of a worktree job reads that job's worktree until it is merged. With `[worktrees] container = "per-job"` and `[sandbox] provider = "devcontainer"`, the launcher runs `dc up <worktree>` for each job and the worker runs in that container; the project's devcontainer config must mount the main repository (its `.rig` state and its git directory) at its own path as well as the worktree, and the image needs `git` (the probe checks that `git` works there). dc-cli leaves a stopped container behind when a job ends; Rig never removes containers, so `rig worktrees status` lists them and you clear them with `dc prune`. `rig doctor` reports the mode and checks the git version.

## Workspace and dc-cli

Rig never calls docker. dc-cli owns the container and Rig runs it as a subprocess. Rig never edits your own `.devcontainer`. `dc up` refuses a folder with no devcontainer or compose file, so for such a folder `rig env up` stops and tells you to run `rig env init --write-devcontainer`, which writes a new `.devcontainer/devcontainer.json` (never over an existing one) with the repository and runtime mounted at their own paths. Worker execution inside the container is a separate opt-in, described below.

**dc-cli contract status.** Checked against dc-cli 0.23.0 with devcontainer CLI 0.89.0 on a real Docker engine (recorded outputs in `tests/fixtures/dc/`): `exec --no-start [workspace] -- CMD` (exit code and arguments pass through), `ls --json` (`id`, `name`, `status`, `local_folder`, `kind`; no image or digest, so evidence leaves them empty), `doctor --json` (checks with `id`, `status` ok, warning or blocker, `remediation`), `forward --status` (text only, no `--json`), and `try --print` (prints the path of the config it generated). Fields are still read tolerantly. `templates/dc-cli.json` holds the version pin; it is empty, which means any dc-cli is accepted and `rig doctor` says "unpinned". Setting it makes a different version fail `rig doctor --workspace`.

## Building and versions

`rig setup` builds `rig-shell` when Go is installed (`--shell-binary` insists, `--no-shell-binary` skips) and stamps it with the source commit. `rig update` does not carry shell sources, so after updating, `rig doctor` reports a mismatch between the binary and the installed core and says to run `rig shell build` from a clone (`RIG_SRC=/path/to/Rig rig shell build`).

Developing the shell: see `shell/README.md`.

## Running workers in the container

Off by default. Opt in per repository, then check it before relying on it:

```toml
[sandbox]
provider = "devcontainer"

[sandbox.workers]      # an explicit choice for every worker that should run there
claude = "mount-ro"    # its login directory is mounted read-only into the container
codex = "none"         # it needs no mounted login
```

```sh
rig init --sandbox     # generate the container config if the project has none
rig env up
rig doctor --workspace # per enabled worker: can it run in the container?
```

**Which workers.** Only `claude` and `codex`, because their MCP wiring is scoped to the job directory. Every other worker is refused with the reason (for example, it edits global host configuration a container cannot share). A worker with no `[sandbox.workers]` entry is refused and told what to add.

**One rule: same paths.** Every absolute path Rig puts in a command (repository, job directory, the Rig runtime, your home) must exist at the same path inside the container. Rig does not translate paths. `rig env init --write-devcontainer` writes a config that mounts the repository and the Rig runtime that way (runtime read-only), plus the login directories of workers set to `mount-ro`. If you use your own devcontainer config Rig does not change it, so it must mount those paths itself. dc-cli reuses an existing container, so changed mounts only apply to a recreated container. Before each live launch Rig probes the container and refuses with one specific reason when something is missing: repository or runtime not visible, no `python3`, no `setsid`, the agent CLI not installed, or no credentials mounted. Nothing here installs the agent CLIs into your image.

**Never the host.** A worker runs only through `dc exec --no-start`, so a job cannot start a container. If the container is not reachable the launch is refused; the worker is never run on the host as a fallback. The container sees only `HOME` and the job's own `RIG_*` variables. Owner tokens and the rest of your environment are not forwarded, and API keys are not forwarded either.

**Stopping.** Killing the `dc exec` client on the host does not stop a process inside the container, so the worker runs under a small supervisor that kills its whole process group when a stop file appears or when the host's heartbeat stops changing for 15 seconds. A stop waits for that, so files are not released while the agent still runs. If the engine hangs and the supervisor cannot act, the heartbeat lease still ends the agent, so it can outlive the host process for roughly 15 seconds at most; Rig cannot do better from the host.

**Checks.** Acceptance checks run through the same container with the exact argv, so a job is verified where it ran, and under the same stop lease. A missing executable fails the check with exit 127 instead of raising. Check records and `sandbox.json` in the job directory record where each ran; the board's detail pane shows the container, image and digest when dc-cli reports them, and says plainly when it does not.

**Ports.** In container mode a localhost URL on a port dc-cli does not forward would reach the host, not the app in the container, so the browser lane (`rig_bsk_navigate`) and named Chrome profile captures refuse it and list the forwarded ports. If dc-cli cannot list them the URL is refused. `rig env ports` shows them.

**Limits.** Not verified against a real dc-cli, Docker engine, or the real `claude` and `codex` CLIs inside a container. The tests run the real supervisor, probe and launcher against a fake `dc` whose `exec` runs locally, which proves Rig's behavior, not dc-cli's. Read-only credential mounts may break an agent that must write to its own configuration directory. Rig does not use `dc actions`, so no trust hash joins the acceptance contract.

## Verifying against a real engine

`tests/test_live_dc.py` runs a job, a cancel, a check and the port guard against a real dc-cli and Docker engine with a stand-in `claude` (it does the real MCP handshake; it is not a model). It is skipped unless `RIG_TEST_LIVE_DC=1`. Prepare a devcontainer workspace whose config mounts the repository, the Rig checkout (read-only), a home directory with `.claude` (read-only) at their own paths and the stand-in at `/usr/local/bin/claude`, start it with `dc up`, then set `RIG_DC_BIN`, `RIG_TEST_LIVE_REPO` and `RIG_TEST_LIVE_HOME`. A run with the real `claude` or `codex` needs your credentials and is not covered.
