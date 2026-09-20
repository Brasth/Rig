# Screenshot to UI (parent)

Stay. Use this when the user gives a **screenshot** (or a canvas with no inspect), not a Figma node you can click. Still download every image into the **codebase assets folder**. Still write spacing / colour / type tables. Mark `source=visual`.

Children never click Figma. If a Figma URL exists, use `figma-to-code.md` or `figma-to-mobile.md` instead.

## Extract (no inspect panel)

1. Read the screenshot. Inventory every string, image, and block.
2. Measure W×H, padding, gap, sibling space from the image in the same pixel space. Record one row per section and item (`figma-to-code.md` spacing table). `source=visual`.
3. Sample colours (hex) from the pixels. Gradients: note stops you can see.
4. Type: family guess only if you can name it; otherwise size/weight from the image and say the family is unknown.
5. Crop each illustration/photo from the screenshot into the codebase assets folder (reuse `src/assets`, `public/`, or the mobile folder in `figma-to-mobile.md`). No SVG/emoji stand-in.

Do not invent `1rem` or Inter-by-default.

## Implement

Same as web or mobile depending on the repo: `figma-to-code.md` (HTML/CSS/Sass/Tailwind) or `figma-to-mobile.md`. Style-guide: reuse existing config; do not create a new or custom file.

## Compare

Screenshot the implementation at the **source image’s** W×H. Diff against the user screenshot. Visual extract is weaker than Figma inspect — list leftovers instead of calling it exact.
