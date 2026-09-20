# Figma / canvas to mobile (parent)

Stay. Same extract as web (`figma-to-code.md` inventory, spacing, colours, typography, download every image). This file is **implement** for React Native, Flutter, SwiftUI, and Android Compose — not HTML.

Children never click. Follow the codebase skill/rule and **reuse existing** theme/token files. Do not create a custom parallel theme.

## Extract

Do `figma-to-code.md` steps 1–3 first (bind, tables, images into the **codebase assets folder** for this stack). Then `skills/style-guide/SKILL.md` + `token-homes.md` native section.

## Assets folder (reuse)

| Stack | Put images here (existing dir wins) |
| --- | --- |
| React Native | `src/assets/`, `assets/`, or the folder `require()` already uses |
| Flutter | `assets/images/` (and `pubspec.yaml` assets) |
| iOS / SwiftUI | `Assets.xcassets` image set |
| Android / Compose | `res/drawable` or `res/drawable-xxhdpi` |

Do not leave UI bitmaps in Downloads or `.rig/cu-evidence`. Repo-relative paths in the brief.

## Tokens then UI

Reuse the repo theme. Consume inspect tables through that theme, not magic hex/pt in the widget.

| Stack | Token home (reuse) | UI uses |
| --- | --- | --- |
| React Native | existing `theme.ts` / `colors.ts` / NativeWind `tailwind.config` | `theme.colors.heading`, `gap: theme.space[80]` |
| Flutter | existing `ThemeData` / `AppTheme` / `color_scheme` | `Theme.of(context).colorScheme` / `AppSpacing.s80` |
| SwiftUI | existing `Color` assets / `Theme` | `Color.heading`, `spacing.hero` |
| Compose | existing `Color.kt` / `Type.kt` / `Dimens` | `MaterialTheme.colorScheme` / `Dimens.gap80` |

Figma px → dp/pt using the repo’s density convention (often 1 Figma px = 1 dp at 1x). Do not invent a second scale.

## Compare

Capture the Figma frame. Render the screen at the **same frame size** (simulator/emulator or preview). Diff gaps, colours, type, and whether the real image is in the assets folder. Iterate.

Honest leftover: locked Figma export, missing licensed font on device, 3D crop. Name it.
