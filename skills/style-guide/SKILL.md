---
name: style-guide
description: >
  Write colour, spacing, and typography tokens into the codebase from inspect
  tables — CSS variables, Sass maps, Tailwind config (v3 theme.extend / v4
  @theme), or native theme (React Native, Flutter, SwiftUI, Compose). Follow the repo’s own style-guide skill or rules when present. If a
  token config already exists, reuse it — do not create a new or custom file.
  Do not invent a 4/8/16 scale or swap Inter. Children never click Figma.
user-invocable: true
---

# Style-guide config (tokens in the repo)

Use this after parent computer-use (or Figma MCP) has recorded inspect tables. Parent stays on Figma. This skill **writes the token config** (CSS, Sass, or Tailwind) and makes UI consume it.

If the parent used computer-use, also follow `skills/computer-use/references/figma-to-code.md` for the tables (mobile: `figma-to-mobile.md`; screenshot: `screenshot-to-ui.md`). Shapes for each stack: `references/token-homes.md`. Do not click. Do not invent numbers.

## When

- Recreating a Figma/canvas/screenshot as web, mobile, or native UI
- User asks for a style guide, theme, design tokens, Sass variables, Tailwind config, or native theme

Do not paint the UI from magic hex/px first and “extract tokens later”.

## 0. Follow the codebase skill or rule first

Before using Rig’s CSS/Sass/Tailwind shapes, search **this project tree** (not only `~/.agents` / `~/.grok`):

| Look for | Examples |
| --- | --- |
| Project skill | `skills/style-guide/SKILL.md`, `skills/design-tokens/`, `.agents/skills/*style*`, `.claude/skills/*` in the repo |
| Agent rules | `AGENTS.md`, `CLAUDE.md`, `.claude/rules/`, `.cursor/rules/` |
| Docs | `docs/design-guidelines.md`, `docs/code-standards.md`, `STYLEGUIDE.md`, `CONTRIBUTING.md` |

If one of those says how this repo stores colours, spacing, or type — **follow it**. That local skill/rule wins for file path, naming, stack, and token layout. Put its absolute path in the worker brief.

This Rig skill still supplies:

- Inspect tables as the **values** (hex, px, family/weight/size)
- The fallback shapes in `references/token-homes.md` when the repo has no style-guide skill or rule

Do not ignore a repo rule because this global skill exists. Do not copy `~/.claude/skills` into a project that already defined its own.

If the repo rule conflicts with inspect (forbids a new font, forces an 8px scale): keep the repo’s file shape; write inspect values into it; name leftovers instead of silently rounding or swapping Inter.

## Reuse existing config — do not create a new one

If the repo already has a token config, **follow and reuse that file** — do not create a new or custom file. Do not add `tokens.css`, `custom-theme.css`, a second `tailwind.config`, or a new `_tokens.scss` beside it.

| Already there | Do this |
| --- | --- |
| `tailwind.config.*` / `@theme` | extend that object / block |
| `_variables.scss` / `_tokens.scss` / `abstracts/` | add or alias keys in that partial |
| `tokens.css` / `theme.css` / `:root` | add keys on that `:root` |
| `tokens.json` / Style Dictionary / JS theme | extend that file |
| React Native `theme.ts` / Flutter `ThemeData` / SwiftUI Color / Compose `Color.kt` | extend that file |

Reuse **existing token names** when the inspect hex/px already matches (`--color-primary` is already `#020315` → use it, do not invent `--color-hero-navy`). Add a new key only when inspect has a value the config lacks. New file only when **no** token config exists.

## Bar

The token file is the style-guide config. Values are the inspect numbers. UI uses the tokens. A made-up 4/8/16 spacing scale, a guessed `#7c3aed`, Inter when inspect named another family, or `p-[32px]` / `text-[#fff]` instead of a token is not done.

## 1. Pick one token home

Do not add a second system. Order:

1. **This repo’s style-guide skill or rule** (section 0) — file path and stack it names.
2. **Existing token files in this repo** — extend them.
3. **User named Sass or Tailwind** — use that even on greenfield.
4. Else greenfield **CSS variables**.

| If the repo has | Write / extend |
| --- | --- |
| Tailwind v4 `@theme` in CSS | that `@theme` block |
| Tailwind v3 `tailwind.config.{js,ts,mjs,cjs}` | `theme.extend` colours, spacing, fontFamily, fontSize |
| Sass/SCSS `_variables.scss` / `_tokens.scss` / `abstracts/` | `$color-*`, `$space-*`, maps |
| `:root` / `tokens.css` / `theme.css` | CSS custom properties |
| `tokens.json` / Style Dictionary / CSS-in-JS theme | that file |
| React Native / Flutter / SwiftUI / Compose theme | that existing theme file |

Sass **and** Tailwind in one repo (SCSS compiled, then Tailwind): fill **both** from the same inspect tables. Do not invent two palettes.

Read `references/token-homes.md` for CSS, Sass, Tailwind v3/v4, and native theme shapes.

## 2. Map inspect tables → tokens

Same roles on every stack. Deduplicate identical values; extra roles may alias.

**Colours** (`--color-*` / `$color-*` / Tailwind `colors`)

- One token per unique hex (and opacity). Roles from the layer (`bg`, `heading`, `muted`, `accent`).
- Gradients keep inspect angle + stops.
- Stroke/effect colours are tokens too.

**Spacing** (`--space-*` / `$space-*` / Tailwind `spacing`)

- Every unique padding/gap/sibling-space number.
- Keep inspect px (`80px`), not a rem scale Figma did not use.
- Semantic aliases (`hero-gap` → the 80px token) are extra, not a replacement.

**Typography** (`--font-*` / `$font-*` / Tailwind `fontFamily` / `fontSize`)

- Family, size, weight, line-height, letter-spacing from each text-layer row.
- Load the inspect family. Do not substitute Inter/system-ui.

Names by stack (same inspect value):

| Role | CSS | Sass | Tailwind |
| --- | --- | --- | --- |
| heading colour | `--color-heading` | `$color-heading` | `theme.extend.colors.heading` / `--color-heading` in `@theme` |
| 80px gap | `--space-80` | `$space-80` | `theme.extend.spacing.80` or `hero-gap` |
| stat size | `--font-size-stat` | `$font-size-stat` | `theme.extend.fontSize.stat` |

## 3. Consume the tokens

| Stack | UI uses | Raw hex/px stay in |
| --- | --- | --- |
| CSS variables | `var(--color-heading)`, `var(--space-80)` | `tokens.css` |
| Sass | `$color-heading`, `map.get($space, 80)` | `_tokens.scss` |
| Tailwind | `text-heading`, `gap-80`, `text-stat` (or `@theme` utilities) | `tailwind.config` / `@theme` |
| React Native / Flutter / SwiftUI / Compose | theme tokens from `token-homes.md` native section | that theme file |

Do not leave `text-[#ffffff]` / `p-[32px]` / hardcoded `$c: #fff` in components when a token exists.

## Hard no

- Inventing a type scale or spacing scale the design does not have
- Painting UI with raw hex/px while the token file sits unused
- Creating a new or custom token file when a config already exists
- Adding CSS vars *and* a new Tailwind config *and* new Sass when the repo already has one of them
- Children never click Figma, chrome-profile, or `rig_cu_*`
- Passing cua-driver / chrome-devtools / chrome-profile to children
