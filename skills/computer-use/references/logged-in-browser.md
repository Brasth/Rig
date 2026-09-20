# Logged-in Chrome (parent)

Stay. Real cookies, real profile. Isolated Driver Chrome is **not** this path.

## Bind

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

Evidence under `.rig/cu-evidence`. `brief_block` to workers. Children never get chrome-profile or `rig_cu_*`.
