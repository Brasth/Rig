# Token homes (CSS, Sass, Tailwind, native)

Shapes for `../SKILL.md`. Values must come from **this** frame’s inspect tables, not these samples. Pick one home (or Sass+Tailwind if the repo already has both).

## CSS variables (greenfield default)

`tokens.css` or `styles/tokens.css`:

```css
:root {
  --color-bg: #020315;
  --color-heading: #ffffff;
  --space-32: 32px;
  --space-80: 80px;
  --space-hero-gap: var(--space-80);
  --font-family-body: "Inter", sans-serif;
  --font-size-stat: 48px;
  --font-weight-stat: 700;
  --line-height-stat: 1;
}
```

Consume: `color: var(--color-heading); gap: var(--space-80);`

## Sass / SCSS

Prefer the repo’s existing abstracts file (`_variables.scss`, `_tokens.scss`, `src/styles/abstracts/_tokens.scss`). Greenfield Sass: `_tokens.scss` next to the section styles.

```scss
$color-bg: #020315;
$color-heading: #ffffff;
$color-muted: #c4b5fd;

$colors: (
  bg: $color-bg,
  heading: $color-heading,
  muted: $color-muted,
);

$space-32: 32px;
$space-80: 80px;
$space-hero-gap: $space-80;

$space: (
  32: $space-32,
  80: $space-80,
  hero-gap: $space-hero-gap,
);

$font-family-body: "Inter", sans-serif;
$font-size-stat: 48px;
$font-weight-stat: 700;
$line-height-stat: 1;
```

Consume: `color: $color-heading;` or `gap: map.get($space, 80);`. `@use` the tokens file; do not copy hex into component partials.

Optional: emit CSS variables from the same Sass maps if the repo already does runtime theming — still one inspect source.

## Tailwind v3 (`tailwind.config`)

Extend the existing config. Do not replace `theme` wholesale unless this design is the whole product.

```js
// tailwind.config.js (or .ts) — theme.extend only
module.exports = {
  theme: {
    extend: {
      colors: {
        bg: "#020315",
        heading: "#ffffff",
        muted: "#c4b5fd",
      },
      spacing: {
        32: "32px",
        80: "80px",
        "hero-gap": "80px",
      },
      fontFamily: {
        body: ["Inter", "sans-serif"],
      },
      fontSize: {
        stat: ["48px", { lineHeight: "1", fontWeight: "700" }],
      },
      letterSpacing: {
        heading: "-1.5px",
      },
    },
  },
};
```

Consume: `text-heading`, `bg-bg`, `gap-80`, `gap-hero-gap`, `font-body`, `text-stat`. Not `text-[#ffffff]` or `p-[32px]`.

## Tailwind v4 (`@theme`)

If the app already has an `@import "tailwindcss"` CSS file, put tokens in that file’s `@theme` (or `@theme inline`).

```css
@import "tailwindcss";

@theme {
  --color-bg: #020315;
  --color-heading: #ffffff;
  --color-muted: #c4b5fd;
  --spacing-32: 32px;
  --spacing-80: 80px;
  --spacing-hero-gap: 80px;
  --font-body: "Inter", sans-serif;
  --text-stat: 48px;
  --text-stat--line-height: 1;
  --text-stat--font-weight: 700;
}
```

Consume the generated utilities (`text-heading`, `gap-80`, `text-stat`). Do not add a v3 `tailwind.config` on a v4 app.

## Both Sass and Tailwind

Some repos compile SCSS and also run Tailwind. Write Sass maps **and** `theme.extend` / `@theme` from the same inspect hex/px. Do not let the two files drift.

## Native (reuse the existing theme file)

Do not add a web `tokens.css` to a mobile app. Extend what the repo already has. Same inspect hex/px as web.

**React Native** (`theme.ts` / `colors.ts` — or NativeWind `tailwind.config`):

```ts
export const colors = { bg: "#020315", heading: "#ffffff" };
export const space = { 32: 32, 80: 80, heroGap: 80 };
```

**Flutter** (existing `AppTheme` / `color_scheme.dart`):

```dart
class AppColors {
  static const bg = Color(0xFF020315);
  static const heading = Color(0xFFFFFFFF);
}
class AppSpace {
  static const s32 = 32.0;
  static const s80 = 80.0;
}
```

**SwiftUI** (existing Color assets / `Theme.swift`): `Color("heading")` after adding the color set; spacing constants in the existing Theme file.

**Compose** (existing `Color.kt` / `Dimens.kt`): `val Heading = Color(0xFFFFFFFF)`, `val Gap80 = 80.dp`.

Figma px → dp/pt with the repo’s density rule. Consume through the theme, not raw hex in the widget.
