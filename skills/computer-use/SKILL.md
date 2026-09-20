---
name: computer-use
description: >
  Parent-only desktop eyes and hands. Use Cua Driver when this repo has
  [computer-use] enabled=true and cua-driver is present; otherwise chrome-devtools.
  Recreating a Figma/canvas/screenshot as UI, driving a desktop app, logged-in
  Chrome, or real GUI tests: download every image into the codebase folder when
  matching a design; otherwise capture → act → confirm with evidence. Stay.
  Never spawn a clicker.
  Never a Rig worker.
user-invocable: true
---

# Computer-use (parent only)

Stay. Do not spawn a clicker. Do not call `computer_use(...)`. Children never click.

## When Driver is effective

First call parent-only `rig_cu_status` when action tools are absent or recently failed. It reports machine opt-in, binary availability, and the repository flag without changing them. Resolve the reported blocker with `rig computer-use setup` / `rig computer-use on`, then restart parent/MCP discovery if the client cached the tool list. If status itself is absent, the running Rig MCP is outdated: update it after safely completing active jobs and restart the parent. Do not bypass a failed or missing Rig tool with raw Driver shell/MCP, native CU, or another browser tool. Report the blocker; use the documented chrome-devtools fallback only when Driver is not effective.

This repo `[computer-use] enabled=true` **and** `cua-driver` is on PATH. Every legal parent (Grok, Codex, OpenCode, OMP, Pi, agy) uses the **same** Rig MCP tools — not raw cua-driver MCP, not `computer_use(...)`. Do not shell cua-driver for capture, act, confirm, or recording:

1. `rig_cu_capture` — snapshot (fresh, 30s). Inspect the text summary, `rig.cu.v1` receipt, and image content when present. Record `snapshot_id`. For a logged-in site (Figma), pass `profile_key` + `url`. Parent `chrome-profile open --json --no-activate` materializes that Chrome mapping; Driver binds the exact window (`existing_profile`). Isolated Driver profile is not the Figma path.
2. `rig_cu_act` — **one** action on that fresh snapshot. Prefer a fresh `element_token` (AX) or browser `ref` (`click` / `type` / `key`). A valid act consumes the snapshot; a second act is `capture_required` / stale and does not call Driver.
3. `rig_cu_confirm` — **mandatory** after a successful act, before the next action or the report. Confirm is the only `confirmed` outcome and yields a fresh successor snapshot. Confirm before act, expired, consumed, or unknown snapshots return `capture_required` / stale and do not call Driver.
4. `rig_cu_record` — GUI-test video (`action=start` then `action=stop`). Output under `.rig/cu-evidence`. Structured/text only unless an image is actually returned. See `skills/computer-test/references/record-video.md`.
5. Px (`x`,`y`) only after that snapshot is `degraded` or the last act/confirm is `escalate_px`. Token/ref and `x,y` are mutually exclusive. Native PNG is window-local; browser PNG is `viewport_css_px` (Driver scale). Foreground only if Driver says `escalate_foreground`.
6. Existing-profile CDP requires a **human** `cua-driver serve --grant existing-profile`. Rig never silent-grants. Missing grant → refuse with that hint. Human daemon start is the only cua-driver CLI.
7. Semantic Astra parity: the parent gets a visual observation (MCP image when the local PNG is readable) plus a Rig-owned `rig.cu.v1` receipt (operation, status, snapshot id/freshness/coord space, observation, target, effect/next_action, image metadata, redacted `brief_block`). This is **not** Astra wire-format cloning and does **not** give CUA to workers. Image bytes are response-only.
8. Paste `brief_block` into the worker brief (including Devin). Tell the child not to click.

Figma MCP stays parent **file/node** work. It is not computer-use and not a clicker. Canvas / WebGL is CU px off the same screenshot.

## Drive, test, or match a design

Pick the HOW. Do not default to Figma-to-HTML.

| Job | Read |
| --- | --- |
| Native desktop app | `references/desktop-drive.md` |
| Logged-in Chrome (real profile) | `references/logged-in-browser.md` |
| Real GUI test (click UI, pass/fail, record video) | `skills/computer-test/SKILL.md` |
| Match a Figma/canvas/screenshot as UI | section below |

## Figma / canvas / screenshot to UI

When the user wants UI that matches a Figma frame, canvas, or screenshot, **stay**. Pick the HOW (do not default to HTML if the repo is mobile):

| Target | Read before writing |
| --- | --- |
| Web HTML/CSS/Sass/Tailwind | `references/figma-to-code.md` |
| React Native, Flutter, SwiftUI, Compose | `references/figma-to-mobile.md` (extract tables still from `figma-to-code.md`) |
| Screenshot only (no Figma inspect) | `references/screenshot-to-ui.md` |

Bar: the rendered output matches the named frame. Guessed spacing, guessed colour, guessed type, invented copy, or a generated/SVG/emoji stand-in for a design image is not done.

Do this in order. Do not write UI until steps 1–7 exist as files:

1. Bind the logged-in tab (`profile_key` + url). Prefer Figma MCP **file/node export** when it is connected. Inspect and canvas crop stay CU.
2. Inventory the frame: every text string, effect, and **every image/icon/illustration**.
3. Record spacing and gap for every section and every item — not only the outer frame. Click each nested auto-layout in inspect: padding T/R/B/L, Gap, sibling space.
4. Record colours for every fill, text, stroke, and effect (hex + opacity, or gradient stops + angle). No “close purple”.
5. Record typography for every text layer (family, weight, size, line-height, letter-spacing). Record inspect tokens, not Inter-by-default.
6. Always download every image into the **codebase assets folder** (MCP/node export, then native Export, then high-zoom CU crop). Reuse `src/assets`, `public/`, `static/`, `images/`, or whatever this repo already uses — do not leave files in Downloads, `/tmp`, or `.rig/cu-evidence`. Keep the real pixels.
7. Write the style-guide config from those tables (`skills/style-guide/SKILL.md`). If this repo already has a style-guide skill or rule, follow that for file path and stack. If a token config already exists, reuse it — do not create a new or custom file. Inspect tables still supply the values. Else CSS variables, Sass maps, or Tailwind config (`theme.extend` / `@theme`). Visual estimate only when inspect is blocked; say so in the brief.
8. Implement using those tokens (web: `var(--color-*)` / Sass / Tailwind; mobile: theme/Color.kt/SwiftUI Color — see the chosen reference), not raw magic numbers.
9. Screenshot the UI at the frame size. Compare to the Figma frame (or source screenshot). Iterate until they match, including gaps, colours, and type. If a lock or missing export still leaves a diff, name the leftover pixels — do not call it exact.

Put spacing / colour / type tables, the **repo token-config path**, **repo-relative image paths in the assets folder**, and both screenshots in `brief_block`. Put the absolute `skills/style-guide/SKILL.md` path in the worker brief, plus any **repo** style-guide skill or rule path. Children never click.

## Otherwise

Do not call Driver, even if cua-driver MCP tools are listed. Use **chrome-devtools** only.

Never Figma MCP or Playwright as the computer-use fallback. Never the Hermes `computer_use` skill.

## Hard no

- Passwords, 2FA, payment, OS permission dialogs
- `[workers].cua` / spawn Driver as a job
- Wiring Driver into child MCP
- `--remote-debugging-port` on a personal Chrome profile, editing `Preferences` / `Local State`, copying the profile
- Shelling `cua-driver` for capture, act, confirm, or recording (use Rig MCP)
- Passing cua-driver, chrome-devtools, chrome-profile, or `rig_cu_*` to children
