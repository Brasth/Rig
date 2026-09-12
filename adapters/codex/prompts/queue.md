---
description: Park a Rig work item. Does not spawn a child. Codex 0.154 has no /prompts:queue slash — prefer the UserPromptSubmit hook or rig tui e.
argument-hint: "[text|cancel <id>]"
---

Park this as a Rig queue item in `.rig/queue/`. Do not spawn a worker. Do not wait. Do not allow/deny. Do not encode `/queue` into `rig pick --case`.

If `$ARGUMENTS` is empty, list the queue (`rig_queue_list` or `rig queue list`).

If `$ARGUMENTS` starts with `cancel `, cancel that id (`rig_queue_cancel` or `rig queue cancel <id>`).

Otherwise add the text (`rig_queue_add` or `rig queue add "$ARGUMENTS"`).

Print the id (on add) and the QUEUE block (`pending / live/max`). Stop.

While a child wait is blocking this session, enqueue from another terminal with `rig queue add "…"` — this prompt runs on the next free turn.
