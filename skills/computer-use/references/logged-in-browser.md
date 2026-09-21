# Logged-in Chrome (parent)

Stay. Real cookies, real profile. Isolated Driver Chrome is **not** this path.

Prefer parent-only BrowserSkill for logged-in Chromium. Cua Driver stays desktop/canvas (AX / px). One backend per turn. Never run `bsk install-skill`. Children never receive `bsk` or `rig_bsk_*`.

## Bind (BrowserSkill)

Four gates: machine `~/.rig/browser-skill.json` `opt_in=true`, `bsk` on PATH, this repo `[browser-skill] enabled=true`, extension connected. Human installs the Chrome/Edge extension and leaves Confirm before borrowing tabs ON. Rig never silent-grants, sideloads, or opens the store.

1. `rig_bsk_status` when action tools are hidden. nonempty `status.browsers` is connected.
2. `rig_bsk_session` start (`bsk session start --json`, optional `--no-focus`; retain `session_id`). `session stop` uses the positional ID. Pass `--session` on every scoped command.
3. `rig_bsk_navigate` for a new page, or explicit tab list/borrow/return for a user tab. Then `rig_bsk_observe` → snapshot + `@eN` refs + optional PNG.
4. `rig_bsk_act` one `click`/`fill`/`press` on a fresh ref (maps to those `bsk` commands, not a fictional `act` subcommand).
5. `rig_bsk_confirm` re-observe; confirm is the only `confirmed` outcome.

Evidence under `.rig/bsk-evidence`.

## Bind (Driver — desktop/canvas)

1. Parent `chrome-profile open --json --no-activate --force <key> <url>`.
2. `rig_cu_capture` with that `profile_key` + `url`.
3. Existing-profile grant is **human** (`cua-driver serve --grant existing-profile`). Rig never silent-grants.
4. Prefer the on-screen titled window, not an overlay / empty New Tab. `window_id` 0 → refuse and recapture.

DOM **ref** for page chrome. Canvas / WebGL (Figma artboard) is **px** after `degraded` / `escalate_px`. Trusted CSS px on macOS may refuse; then AX/ref or recapture.

## Useful loops

- **Figma inspect** — then `figma-to-code.md` / `figma-to-mobile.md`.
- **Logged-in dashboard smoke** — click a nav ref, confirm heading text, save PNG.
- **Local HTML vs Figma** — open `file://` or localhost in the same profile window, screenshot at frame size, compare.
- **Form (no secrets)** — type into a ref, confirm the value, no passwords/2FA/payment.

Evidence under `.rig/bsk-evidence` (BSK) or `.rig/cu-evidence` (Driver). `brief_block` to workers. Child must not click. Children never get chrome-profile, `rig_cu_*`, `bsk`, or `rig_bsk_*`.
