# Figma / canvas to code (parent)

Stay. Recreate a named Figma (or other canvas) frame as **web** HTML/CSS/Sass/Tailwind only after you download every image, record inspect tokens, record spacing and gap for every section and every item, record colours, and record typography. Then write the style-guide token config (`skills/style-guide/SKILL.md`). Children never click. They receive files and numbers in `brief_block`.

This is the **web** HOW for the loop in `../SKILL.md`. Extract tables here are also the source for mobile (`figma-to-mobile.md`) and screenshot-only (`screenshot-to-ui.md`). Capture / act / confirm stay in `../SKILL.md`.

## Routes (do not mix)

| Need | Route |
| --- | --- |
| File/node JSON, typed export of a layer | Parent **Figma MCP** (file/node). Not a clicker. |
| Logged-in Figma chrome (layers, inspect, toolbar) | CU `rig_cu_capture` with `profile_key` + `url`, then **ref** |
| Canvas / WebGL artboard | CU **px** off that same screenshot after `degraded` / `escalate_px` |
| No Driver | chrome-devtools only. Never Playwright. Never Figma MCP as CU. |

Isolated Driver Chrome is not the Figma path. Existing-profile grant is human (`cua-driver serve --grant existing-profile`). Write screenshots under `.rig/cu-evidence` (not `/tmp`).

## Bar

Done means a screenshot of the HTML at the frame’s width × height matches a screenshot of that Figma frame. “Almost” is only allowed when the user called it a demo. Otherwise iterate.

Forbidden substitutes when the design has a raster or illustration:

- SVG avatar / geometric stand-in
- Emoji, Lucide, or generated image
- Cropped screenshot that still includes Figma chrome, other frames, or the page gradient around the asset
- Stock photo

A **Figma-exported** SVG (the layer is actually a vector) is a real asset, not a stand-in.

## Procedure

Do not write markup until the inventory file, the spacing table, the colour table, the typography table, the token file, and every image path exist.

### 1. Open the named frame

User must name the file URL and node (or frame). Do not guess another page.

1. `rig_cu_capture` with `profile_key` + that url (chrome-profile open, then existing-profile bind).
2. Confirm the screenshot is the intended file, not an overlay window or a New Tab.
3. Select the node from the URL (`node-id=`) or click the frame on the canvas (px). Confirm inspect shows that frame name.

If Figma MCP is authenticated, pull file/node JSON **and** export images here. Still keep a CU screenshot of the frame for the later compare.

### 2. Inventory (all content)

Click each layer (or read MCP node JSON) and write a table. Empty cells are not optional.

For the **frame / auto-layout**:

- Name, node id
- W × H (Fill vs Hug vs fixed)
- Direction, alignment
- Radius per corner
- Clip (fills, strokes, and effect colours go in **Colours**)

For **each text layer**:

- Exact string (no rewrite, no lorem)
- Align, decoration (type metrics go in **Typography** below)

For **each image / icon / illustration**:

- Layer name, node id, W × H
- Kind: raster, vector export, icon
- Destination path in the **codebase assets folder** (example: `src/assets/hero-portrait.png` — use this repo’s existing folder)

Copy every string off the canvas. Do not paraphrase headlines or stats.

Then fill **Spacing and gap**, **Colours**, and **Typography** below. Do not stop at the outer frame.

### 2b. Spacing and gap (every section, every item)

Do not skip nested frames. The outer frame’s Gap is not the gap inside a stats row, a card, or a text stack.

**How to read inspect (CU click the layer, then the right Design panel):**

1. Select the auto-layout frame. Record **Padding** T / R / B / L (four numbers; unlinked sides stay unlinked).
2. Record **Gap** (Figma: “spacing between items”). That is the space between **direct children**, not grandchildren.
3. Repeat for **every nested auto-layout**: each section, each item, each inner row. A hero with a copy stack + portrait + a 3-stat row is at least four gap records (hero, copy stack, stats row, and each stat if those are auto-layout).
4. Record **space to the next sibling** when two sections are not in the same Gap (absolute layout, or mixed). From inspect **Position** X/Y and W/H: vertical space = `next.y − (prev.y + prev.h)`; horizontal space = `next.x − (prev.x + prev.w)`.
5. Item-level: each card/stat/row also has its own padding and inner gap. Write those rows too.
6. Line-height is type, not layout gap. Keep it on the type row.

If inspect is locked, measure from the frame screenshot in the same pixel space and mark `source=visual`. Do not invent `1rem` / `16px`.

**Required table** (one row per auto-layout **and** one row per sibling pair that is not covered by a parent Gap):

| parent | item | direction | padding T R B L | gap | space to next sibling | source |
| --- | --- | --- | --- | --- | --- | --- |
| Frame 4 | Frame 4 | vertical | 32 120 86 120 | 80 | — | inspect |
| Frame 4 | copy-stack | vertical | 0 0 0 0 | 24 | 80 to portrait | inspect |
| copy-stack | stats-row | horizontal | 0 0 0 0 | 40 | — | inspect |

Empty gap/padding cells fail the inventory. Those numbers go into the style-guide `--space-*` tokens, then CSS `padding` / `gap` / sibling `margin`.

### 2c. Colours (every fill, text, stroke, effect)

Click each layer. Right Design panel: **Fill**, **Stroke**, **Selection colors**, text fill, effect colour.

1. Solid: hex + opacity (or RGBA). Record the inspect hex, not a named guess (`purple`, `navy`).
2. Gradient: type (linear/radial), angle, each stop hex + position.
3. Text colour is its own row even if it matches a fill.
4. Stroke and shadow/blur colours are rows too.
5. Deduplicate identical hex later in the token file; the table still lists every use.

**Required table:**

| layer | role | kind | value | opacity | source |
| --- | --- | --- | --- | --- | --- |
| Frame 4 | bg | gradient | 180deg #020315 0%, #5D35DD 55%, #AF87FF 100% | 1 | inspect |
| Heading | heading | solid | #FFFFFF | 1 | inspect |
| Stat label | muted | solid | #C4B5FD | 1 | inspect |

Empty colour cells fail the inventory. “Close enough” hex is not inspect.

### 2d. Typography (every text layer)

Click each text layer. Right Design panel: **Font**, size, weight, line-height, letter-spacing.

1. Family as inspect spelled it (including fallback if shown).
2. Weight as a number (400/500/600/700), not only “Bold”.
3. Size in px. Line-height as inspect shows it (px or %). Letter-spacing in px or %.
4. One row per layer even when two layers share a style — then the token file can alias.
5. Do not default to Inter, Roboto, or system-ui when inspect named something else.

**Required table:**

| layer | string (verbatim) | family | weight | size | line-height | letter-spacing | source |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Heading | Huy Nguyen | Inter | 700 | 64 | 1.05 | -1.5 | inspect |
| Stat | 6+ | Inter | 700 | 48 | 1 | 0 | inspect |

Empty type cells fail the inventory. Line-height stays here, not in the spacing table.

### 3. Download every image

For each image row, first path that yields the **layer pixels**:

1. Figma MCP export of that node (PNG or SVG as the layer is).
2. Figma inspect **Export** on that layer (CU ref/px), then the browser download.
3. Zoom the canvas so the layer fills the viewport; CU screenshot; crop **only** that layer. Punch out surrounding frame fill if it is not part of the asset.

Locked / view-only files block 1–2. Ask the user to unlock or export. Until then use 3 and mark the crop as a fallback, not a native export.

**Put the file in the codebase’s assets folder**, not Downloads, `/tmp`, Desktop, or `.rig/cu-evidence` (evidence PNGs stay there; UI assets do not).

Pick the folder (do not invent a second assets tree):

1. Path named by this repo’s style-guide skill or rule.
2. Existing convention: `src/assets/`, `assets/`, `public/`, `static/`, `images/`, `app/assets/`, Next `public/`.
3. Greenfield only: `assets/` next to the section.

Name from the layer (`hero-portrait.png`), not `image1.png`. HTML `src` / `url()` uses that **repo-relative** path (`src/assets/hero-portrait.png`), not an absolute machine path.

### 4. Style-guide config, then implement

Follow `skills/style-guide/SKILL.md` (and `skills/style-guide/references/token-homes.md`). If this **codebase** already has a style-guide skill or rule, follow that for file path, naming, and stack. If a token config already exists, reuse it — do not create a new or custom file. Write the token config from the spacing, colour, and typography tables **before** painting the section — CSS variables, Sass maps, or Tailwind config (`tailwind.config` `theme.extend` or v4 `@theme`) only when the repo has no local rule and no existing config.

HTML/CSS/Sass/Tailwind (or the requested component) uses:

- Recorded W × H, radius
- Token config: `--color-*` / Sass `$color-*` / Tailwind `theme.extend` or `@theme`
- **Local files** from step 3 in the codebase assets folder (`src="src/assets/hero-portrait.png"` or the repo’s equivalent — not a CDN, not an inline SVG you drew, not an absolute `/Users/...` path)

Do not pick a “close” Google font when inspect named one. If the face is missing locally, load that family (or the inspect fallback), and say so.

### 5. Compare until it matches

1. CU-capture the Figma frame at a known zoom (prefer 100% / fit to the frame, no other pages in view). Save `figma-frame.png`.
2. Render the HTML at the **same W × H** (browser at that viewport, not a padded desktop screenshot). Save `html-frame.png`.
3. Read both images. Diff layout, **gaps between sections and items**, **colours**, **typography**, and whether the real image is in place.
4. Fix tokens or crops. Recapture. Repeat.

Stop calling it exact when:

- An image is still a stand-in
- Type size/weight/spacing was guessed and inspect is available
- Colour hex/gradient was guessed and inspect is available
- Section or item gap/padding was taken from the outer frame only, or guessed in rem/px
- UI uses raw hex/px instead of the style-guide token file
- HTML viewport is not the frame size
- Leftover Figma UI (toolbar, rulers, other frames) is in `figma-frame.png`

Honest leftover: locked file, missing font file, or a 3D asset you could only crop. List those pixels. Do not hide them behind “100%”.

## Worker brief

If a child writes the markup, the parent already finished 1–4. `brief_block` includes:

- Frame URL + node id
- Spacing table (every section and every item: padding, gap, sibling space)
- Colour table (every fill, text, stroke, effect)
- Typography table (every text layer)
- Token config path **in the codebase** (the reused existing file, or the new file only if none existed)
- Downloaded images as **repo-relative paths in the codebase assets folder** (not Downloads, not `.rig/cu-evidence`)
- `figma-frame.png` (evidence; may stay under `.rig/cu-evidence`)
- Absolute path of `references/figma-to-code.md` and `skills/style-guide/SKILL.md`, plus any repo style-guide skill or rule (they still must not click)

Tell the child not to use computer-use, chrome-profile, Figma MCP, or `rig_cu_*`.
