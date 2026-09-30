---
name: computer-use
description: >
  Rig-only parent desktop eyes and hands. Activate only in an enabled Rig
  project with parent Rig MCP and an opted-in, available Rig backend.
  Generic computer-use requests do not select Rig; when Rig is off or absent,
  use the host computer/browser instructions instead. Cua Driver requires
  [computer-use] enabled=true, machine opt-in, and cua-driver on PATH.
  Recreating a Figma/canvas/screenshot as UI, driving a desktop app, logged-in
  Chromium via parent-only BrowserSkill (`rig_bsk_*`), or real GUI tests:
  download every image into the codebase folder when matching a design;
  otherwise capture → act → confirm with evidence. Stay.
  Never spawn a clicker.
  Never a Rig worker.
user-invocable: true
---

# Computer-use (parent only)

## Scope and opt-in first

Generic computer-use requests do not select Rig. First check that `.rig/harness.toml` exists and the project is enabled (`[project] enabled=false` disables Rig; an existing legacy harness without that section stays enabled), parent Rig MCP is available, and the selected backend is opted in and available. Global skill installation or a tool name alone is not opt-in. If Rig is uninitialized, disabled, or unavailable, or its backend is not opted in, use an available host-native computer/browser capability under its own instructions. Do not initialize, enable, install, unlock, or repair Rig merely because the user mentioned computer use. If the user explicitly requests Rig, explain the blocker and ask before setup instead of silently switching providers. Once a Rig backend is selected, preserve its permission, grant, freshness, child-isolation, and no-bypass rules; a denial is never a reason to switch tools. The Rig-specific routing and fallback rules below apply only after this selection gate.

Check project state with read-only `rig_status` when it is available, or read `.rig/harness.toml`. Check Driver with `rig_cu_status` only after choosing the Rig path; BrowserSkill has its separate `rig_bsk_status` and opt-in gates. If no authorized host capability is available, explain that limitation or ask for a screenshot. Do not claim a missing capability exists.

Stay. Do not spawn a clicker. Do not call `computer_use(...)`. Children never click. Children never receive `bsk` or `rig_bsk_*`. Never run `bsk install-skill`.

## Pick the backend

- Website / localhost preview → chrome-devtools or BrowserSkill (`rig_bsk_*`). Do not open Driver for a local HTML check.
- Native app / canvas / Figma artboard px → Cua Driver after `rig_cu_status` is `ready`.

## When Driver is effective

First call parent-only `rig_cu_status` when action tools are absent or recently failed. It reports `state` (`ready` | `needs_human` | `unavailable`), `blocker_code`, `user_prompt`, and `exact_command` without changing opt-in or starting the daemon.

- `ready` → `rig_cu_capture` / `rig_cu_act` / `rig_cu_confirm`.
- `blocker_code=daemon_stopped` → parent calls `rig_cu_serve`, then `rig_cu_status` again. Do **not** ask the user to run `rig computer-use serve`.
- `needs_human` with a nonempty `user_prompt` → ask the user **once** with `user_prompt` plus `exact_command` (unlock TTY or OS permission sheets only), then stop. No retry loop. No native CU. No chrome-devtools until they say skip Driver.
- `unavailable` → chrome-devtools or ask for a screenshot. Do not ask them to unlock Driver.

If status itself is absent, Rig is unavailable in this session; absence alone does not prove an outdated MCP. For an explicit Rig request, explain the missing tool and ask before setup or repair. For an already selected Rig backend, do not bypass a failed or missing Rig tool with raw Driver shell/MCP, native CU, or another browser tool.

Logged-in Chromium (real cookies) uses parent-only BrowserSkill when this repo `[browser-skill] enabled=true`, machine `~/.rig/browser-skill.json` opt_in=true, `bsk` on PATH, and the extension is connected: `rig_bsk_status` (always listed) then `rig_bsk_session` (`bsk session start --json`, optional `--no-focus`; retain `session_id`) → `rig_bsk_navigate` or explicit tab list/borrow/return → `rig_bsk_observe` → one `rig_bsk_act` (`click`/`fill`/`press` on a fresh `@eN` ref) → `rig_bsk_confirm`. `--session` on every scoped command; `session stop` with positional ID. nonempty `status.browsers` is connected. Never run `bsk install-skill`. Children never receive `bsk` or `rig_bsk_*`. Native / canvas px stays Cua Driver. One backend per turn.

This repo `[computer-use] enabled=true` **and** `cua-driver` is on PATH. Every parent (live process + Rig MCP, including Cursor Desktop and Claude Code when wired) uses the **same** Rig MCP tools — not raw cua-driver MCP, not `computer_use(...)`. Do not shell cua-driver for capture, act, confirm, or recording. Parent starts the daemon with `rig_cu_serve` (passes `--grant existing-profile` only after a remembered unlock grant). CLI `rig computer-use serve` is a human attached fallback:

1. `rig_cu_capture` — snapshot (fresh, 30s). Inspect the text summary, `rig.cu.v1` receipt, and image content when present. Record `snapshot_id`. For a logged-in site (Figma), pass `profile_key` + `url`. Parent `chrome-profile open --json --no-activate` materializes that Chrome mapping; Driver binds the exact window (`existing_profile`). Isolated Driver profile is not the Figma path.
2. `rig_cu_act` — **one** action on that fresh snapshot. Prefer a fresh `element_token` (AX) or browser `ref` (`click` / `type` / `key`). A valid act consumes the snapshot; a second act is `capture_required` / stale and does not call Driver.
3. `rig_cu_confirm` — **mandatory** after a successful act, before the next action or the report. Confirm is the only `confirmed` outcome and yields a fresh successor snapshot. Confirm before act, expired, consumed, or unknown snapshots return `capture_required` / stale and do not call Driver.
4. `rig_cu_record` — GUI-test video (`action=start` then `action=stop`). Output under `.rig/cu-evidence`. Structured/text only unless an image is actually returned. See `skills/computer-test/references/record-video.md`.
5. Px (`x`,`y`) only after that snapshot is `degraded` or the last act/confirm is `escalate_px`. Token/ref and `x,y` are mutually exclusive. Native PNG is window-local; browser PNG is `viewport_css_px` (Driver scale). Foreground only if Driver says `escalate_foreground`.
6. Existing-profile CDP requires a **remembered** unlock grant. `rig_cu_serve` may pass `--grant existing-profile` only after that. Rig never silent-grants. Missing grant → refuse with that hint. Do not tell the user to type `rig computer-use serve`.
7. Semantic Astra parity: the parent gets a visual observation (MCP image when the local PNG is readable) plus a Rig-owned `rig.cu.v1` receipt (operation, status, snapshot id/freshness/coord space, observation, target, effect/next_action, image metadata, redacted `brief_block`). This is **not** Astra wire-format cloning and does **not** give CUA to workers. Image bytes are response-only.
8. Paste `brief_block` into the worker brief (including Devin). Tell the child not to click.

Figma MCP stays parent **file/node** work. It is not computer-use and not a clicker. Canvas / WebGL is CU px off the same screenshot.

## Drive, test, or match a design

Pick the HOW. Do not default to Figma-to-HTML.

| Job | Read |
| --- | --- |
| Native desktop app | `references/desktop-drive.md` |
| Logged-in Chromium (real cookies; prefer BSK) | `references/logged-in-browser.md` |
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
- Passing cua-driver, chrome-devtools, chrome-profile, `rig_cu_*`, `bsk`, or `rig_bsk_*` to children
- `bsk install-skill`
