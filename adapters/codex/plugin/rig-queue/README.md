# Rig Queue (Codex plugin)

Parks `/queue` and `$queue` into the current repo’s `.rig/queue/`. Does **not** spawn a child.

Install from Codex `/plugins` (marketplace **Rig**). Then `/hooks` trust. Enabling this plugin is the park hook — `rig setup` will drop the duplicate `~/.codex/hooks.json` Rig entry once `[plugins."rig-queue@rig"] enabled = true`.

The hook script always runs `~/.rig/scripts/queue_submit_hook.py` so `rig update` stays the source of truth.
