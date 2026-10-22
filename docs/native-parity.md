# What each assistant can do in rig shell

Generated from `scripts/parent_caps.py` (`python3 scripts/parent_caps.py doc > docs/native-parity.md`); a test keeps it current.
Each row is something you do in the assistant's own TUI. **yes** is the same in rig shell, **partly** is done differently or in part,
**no** cannot be done, with the reason below. Codex and Claude have a protocol that carries everything; the others are driven through
their headless mode, which says less, and the table says so. The rows for grok, opencode, pi, omp and agy are read from how rig runs
them and are not yet checked against the live CLIs (`/selftest` checks part of it).

| | codex | claude | grok | opencode | pi | omp | agy |
|---|---|---|---|---|---|---|---|
| The answer streams in as it is written | yes | yes | partly | partly | partly | partly | no |
| Tool calls and what they answered | yes | yes | partly | partly | partly | partly | no |
| File changes shown as a diff | yes | yes | yes | yes | yes | yes | yes |
| Asks before running a command or editing | yes | yes | no | no | no | no | no |
| Stop mid-turn, cleanly | yes | partly | partly | partly | partly | partly | partly |
| Say something while it works | partly | partly | partly | partly | partly | partly | partly |
| Continue an earlier conversation | yes | yes | yes | yes | no | no | no |
| Compact a long conversation | partly | partly | partly | partly | partly | partly | partly |
| How full the context is | yes | yes | no | no | no | no | no |
| Choose the model and how hard it thinks | yes | yes | yes | yes | yes | yes | partly |
| Plan first, change nothing | partly | yes | partly | yes | partly | partly | partly |
| Use its MCP servers | yes | yes | partly | partly | partly | partly | partly |
| Its skills and slash commands | partly | yes | partly | partly | partly | partly | partly |
| Say when it is reconnecting, retrying or compacting | yes | partly | no | no | no | no | no |

## codex

- **The answer streams in as it is written**: yes. through codex app-server: item and text deltas.
- **Tool calls and what they answered**: yes. commands, MCP calls, searches, plans and helpers, each with its result.
- **File changes shown as a diff**: yes. from the files the turn changed (git), shown as a diff.
- **Asks before running a command or editing**: yes. codex's own approval requests, answered in rig; the edits mode lets file changes through.
- **Stop mid-turn, cleanly**: yes. turn/interrupt: the turn ends as interrupted and the thread stays valid.
- **Say something while it works**: partly. interrupt, then continue with the new message (codex's turn/steer is not used yet).
- **Continue an earlier conversation**: yes. thread/resume of the same thread id.
- **Compact a long conversation**: partly. rig starts a fresh conversation from codex's own summary; codex's thread/compact/start is not used yet.
- **How full the context is**: yes. from codex's token usage (best effort: the payload is not documented).
- **Choose the model and how hard it thinks**: yes. per turn: model and reasoning effort.
- **Plan first, change nothing**: partly. read-only sandbox and a plan instruction; codex's own plan mode is not used.
- **Use its MCP servers**: yes. the servers in codex's config, the project's own first; sign in with /mcp login NAME.
- **Its skills and slash commands**: partly. listed from codex's files and sent as text; codex's skills/list is not used yet.
- **Say when it is reconnecting, retrying or compacting**: yes. reconnecting 3/5, compacting and thinking show on the working line.

## claude

- **The answer streams in as it is written**: yes. stream-json with partial messages.
- **Tool calls and what they answered**: yes. every tool call and result.
- **File changes shown as a diff**: yes. from the files the turn changed (git), shown as a diff.
- **Asks before running a command or editing**: yes. through the rig-ask permission prompt tool.
- **Stop mid-turn, cleanly**: partly. the process is stopped; claude's own interrupt message is not used yet.
- **Say something while it works**: partly. stop, then continue with the new message.
- **Continue an earlier conversation**: yes. --resume of the same session.
- **Compact a long conversation**: partly. rig starts a fresh conversation from a summary; claude's /compact is not run.
- **How full the context is**: yes. the last call's input against the model's window.
- **Choose the model and how hard it thinks**: yes. --model and --effort.
- **Plan first, change nothing**: yes. --permission-mode plan.
- **Use its MCP servers**: yes. .mcp.json and ~/.claude.json, the project's own first.
- **Its skills and slash commands**: yes. the skills and slash commands in claude's files.
- **Say when it is reconnecting, retrying or compacting**: partly. retries, compaction and thinking show when this claude version reports them.

## grok

- **The answer streams in as it is written**: partly. streaming-json when grok sends it, else whole answers.
- **Tool calls and what they answered**: partly. what grok's stream names; less detail than the TUI.
- **File changes shown as a diff**: yes. from the files the turn changed (git), shown as a diff.
- **Asks before running a command or editing**: no. grok's headless mode cannot ask: it runs with its own permissions.
- **Stop mid-turn, cleanly**: partly. the process is stopped.
- **Say something while it works**: partly. stop, then continue with the new message.
- **Continue an earlier conversation**: yes. -r of the same session.
- **Compact a long conversation**: partly. rig starts a fresh conversation from a summary.
- **How full the context is**: no. grok does not report it in headless mode.
- **Choose the model and how hard it thinks**: yes. --model and effort when grok takes them.
- **Plan first, change nothing**: partly. a plan instruction only: grok cannot be made read-only.
- **Use its MCP servers**: partly. its own settings; rig does not list them.
- **Its skills and slash commands**: partly. sent as text.
- **Say when it is reconnecting, retrying or compacting**: no. no reconnect or compaction events.

## opencode

- **The answer streams in as it is written**: partly. json events per step.
- **Tool calls and what they answered**: partly. what opencode's json names.
- **File changes shown as a diff**: yes. from the files the turn changed (git), shown as a diff.
- **Asks before running a command or editing**: no. opencode run cannot ask: it runs with its own permissions.
- **Stop mid-turn, cleanly**: partly. the process is stopped.
- **Say something while it works**: partly. stop, then continue with the new message.
- **Continue an earlier conversation**: yes. --session of the same session.
- **Compact a long conversation**: partly. rig starts a fresh conversation from a summary.
- **How full the context is**: no. not reported in headless mode.
- **Choose the model and how hard it thinks**: yes. --model and effort when opencode takes them.
- **Plan first, change nothing**: yes. --agent plan.
- **Use its MCP servers**: partly. its own settings; rig does not list them.
- **Its skills and slash commands**: partly. sent as text.
- **Say when it is reconnecting, retrying or compacting**: no. no reconnect or compaction events.

## pi

- **The answer streams in as it is written**: partly. json mode: whole messages.
- **Tool calls and what they answered**: partly. what pi's json names.
- **File changes shown as a diff**: yes. from the files the turn changed (git), shown as a diff.
- **Asks before running a command or editing**: no. pi -p cannot ask.
- **Stop mid-turn, cleanly**: partly. the process is stopped.
- **Say something while it works**: partly. stop, then continue with the new message.
- **Continue an earlier conversation**: no. every message starts a new pi session; rig sends the conversation along instead.
- **Compact a long conversation**: partly. rig starts a fresh conversation from a summary.
- **How full the context is**: no. not reported.
- **Choose the model and how hard it thinks**: yes. --model and effort when pi takes them.
- **Plan first, change nothing**: partly. a plan instruction only.
- **Use its MCP servers**: partly. its own settings; rig does not list them.
- **Its skills and slash commands**: partly. sent as text.
- **Say when it is reconnecting, retrying or compacting**: no. no reconnect or compaction events.

## omp

- **The answer streams in as it is written**: partly. json mode: whole messages.
- **Tool calls and what they answered**: partly. what omp's json names.
- **File changes shown as a diff**: yes. from the files the turn changed (git), shown as a diff.
- **Asks before running a command or editing**: no. omp -p cannot ask.
- **Stop mid-turn, cleanly**: partly. the process is stopped.
- **Say something while it works**: partly. stop, then continue with the new message.
- **Continue an earlier conversation**: no. every message starts a new omp session; rig sends the conversation along instead.
- **Compact a long conversation**: partly. rig starts a fresh conversation from a summary.
- **How full the context is**: no. not reported.
- **Choose the model and how hard it thinks**: yes. --model and effort when omp takes them.
- **Plan first, change nothing**: partly. a plan instruction only.
- **Use its MCP servers**: partly. its own settings; rig does not list them.
- **Its skills and slash commands**: partly. sent as text.
- **Say when it is reconnecting, retrying or compacting**: no. no reconnect or compaction events.

## agy

- **The answer streams in as it is written**: no. the answer arrives whole, when the turn ends.
- **Tool calls and what they answered**: no. no tool calls in agy's json output.
- **File changes shown as a diff**: yes. from the files the turn changed (git), shown as a diff.
- **Asks before running a command or editing**: no. agy -p cannot ask.
- **Stop mid-turn, cleanly**: partly. the process is stopped.
- **Say something while it works**: partly. stop, then continue with the new message.
- **Continue an earlier conversation**: no. every message starts a new agy session; rig sends the conversation along instead.
- **Compact a long conversation**: partly. rig starts a fresh conversation from a summary.
- **How full the context is**: no. not reported.
- **Choose the model and how hard it thinks**: partly. effort only.
- **Plan first, change nothing**: partly. a plan instruction only.
- **Use its MCP servers**: partly. its own settings; rig does not list them.
- **Its skills and slash commands**: partly. sent as text.
- **Say when it is reconnecting, retrying or compacting**: no. no reconnect or compaction events.
