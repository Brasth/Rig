#!/usr/bin/env python3
"""Make the screenshots in docs/media from the scenes in scripts/media/scenes.

    python3 scripts/media/render.py              # every shot, light and dark, 1x and 2x
    python3 scripts/media/render.py conversation welcome --theme light --png

Each shot is an app screen (a scene file, drawn by ``rig-shell --scene``) or a board tab (fixtures, drawn by
``rig-shell --once``) at 120x36 cells, fitted into a 1440x900 picture, written as WebP at 1x and 2x. Nothing is read
from a real project, so the pictures come out the same on every machine; change a scene and rerun to refresh them.
Needs Go 1.24+ (or RIG_SHELL_BIN), a Chromium (Playwright's headless_shell is found by itself) and Pillow.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import frame  # noqa: E402

HERE = Path(__file__).resolve().parent
OUT = frame.ROOT / "docs" / "media"
SIZE = "120x36"
PX = (1440, 900)
THEMES = ("light", "dark")

# name: (kind, source, caption); a board shot's source is its tab and fixtures
SHOTS: dict[str, tuple[str, object, str]] = {
    "conversation": ("scene", "conversation.json", "The conversation: a turn's tool calls, answer, the diff it made and the Review, Commit and Undo bar"),
    "panel": ("scene", "panel.json", "The agents panel (⇥): helpers at work, started by the parent, and the queue"),
    "welcome": ("scene", "welcome.json", "First run: pick who to talk to and you are in the conversation"),
    "code": ("scene", "code.json", "The code tab: explorer, tabs, git marks in the gutter, a check's problem on its line"),
    "doctor": ("scene", "doctor.json", "/doctor: what is wrong with the project in plain words, each with its fix"),
    "settings": ("scene", "settings.json", "Settings: theme, default parent and its model, where rig starts"),
    "jobs": ("board", ("Jobs", {}), "The board (rig shell --layout board): jobs that need attention first, then active and finished"),
    "env": ("board", ("Env", {"workspace": "workspace.json", "fleet": "fleet.json"}), "The Env tab: the dev container, its ports, per-job worktrees and the fleet"),
}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("shots", nargs="*", help=f"which to make (default all): {', '.join(SHOTS)}")
    ap.add_argument("--theme", action="append", help="light, dark, rig, material or dracula (default light and dark)")
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--png", action="store_true", help="keep the PNG next to the WebP")
    ap.add_argument("--no-build", action="store_true", help="use the prebuilt rig-shell instead of building the clone's")
    args = ap.parse_args()
    names = args.shots or list(SHOTS)
    unknown = [n for n in names if n not in SHOTS]
    if unknown:
        ap.error(f"unknown shot(s) {', '.join(unknown)}; choose from {', '.join(SHOTS)}")
    themes = args.theme or list(THEMES)
    shell = frame.shell_binary(build=not args.no_build)
    args.out.mkdir(parents=True, exist_ok=True)
    cols, rows = frame.size_of(SIZE)
    for name in names:
        kind, source, _ = SHOTS[name]
        for theme in themes:
            if kind == "scene":
                lines = frame.render_scene(shell, HERE / "scenes" / source, SIZE, theme)
            else:
                tab, fixtures = source
                lines = frame.render_board(shell, HERE / "boards" / "full.json", tab, SIZE, theme,
                                           **{k: HERE / "boards" / v for k, v in fixtures.items()})
            page = frame.page_html(lines, cols, rows, PX)
            png = args.out / f"rig-shell-{name}-{theme}.png"
            frame.screenshot(page, png, PX, scale=2)
            frame.to_webp(png, args.out / f"rig-shell-{name}-{theme}@2x.webp", quality=80)
            frame.to_webp(png, args.out / f"rig-shell-{name}-{theme}.webp", width=PX[0], quality=82)
            if not args.png:
                png.unlink()
            print(f"wrote rig-shell-{name}-{theme}.webp (+@2x)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
