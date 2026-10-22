#!/usr/bin/env python3
"""Draw rig-shell screens as pictures.

A frame is what ``rig-shell --scene FILE --color`` (an app screen) or ``rig-shell --once --color`` (the board) prints:
the screen's lines with their colours as SGR sequences. This module turns one into an HTML page that a headless
Chromium draws with JetBrains Mono, and then into PNG and WebP. ``render.py`` makes the screenshots, ``record.py`` the
walkthrough video; both only compose what is here.

No Rig project, API server or parent CLI is involved: a scene file says what is on screen, so a screenshot is the
same on every machine and can be remade after a design change with one command.
"""
from __future__ import annotations

import glob
import hashlib
import html
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SHELL_DIR = ROOT / "shell"
BUILD_DIR = Path(__file__).resolve().parent / ".build"

# JetBrains Mono: advance width 600/1000 em; the row is the line height
CELL_W = 0.6
LINE_H = 1.3
FONT_STACK = "'JetBrains Mono', 'DejaVu Sans Mono', 'Menlo', monospace"

SGR = re.compile(r"\x1b\[([0-9;]*)m")
OTHER_ESC = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b_[^\x1b]*\x1b\\|\x1b\[[0-?]*[ -/]*[@-~]|\x1b.")


def shell_binary(build: bool = True) -> Path:
    """The rig-shell to draw with: a fresh build of the clone's shell when Go is here, else the prebuilt one."""
    override = os.environ.get("RIG_SHELL_BIN")
    if override:
        return Path(override)
    exe = BUILD_DIR / "rig-shell"
    if build and shutil.which("go"):
        BUILD_DIR.mkdir(parents=True, exist_ok=True)
        subprocess.run(["go", "build", "-o", str(exe), "./cmd/rig-shell"], cwd=SHELL_DIR, check=True)
        return exe
    if exe.exists():
        return exe
    system = {"Darwin": "darwin", "Linux": "linux"}.get(platform.system(), "")
    arch = {"x86_64": "amd64", "AMD64": "amd64", "arm64": "arm64", "aarch64": "arm64"}.get(platform.machine(), "")
    prebuilt = ROOT / "prebuilt" / f"rig-shell-{system}-{arch}"
    if not prebuilt.exists():
        sys.exit(f"no rig-shell: install Go 1.24+ or set RIG_SHELL_BIN (looked for {prebuilt})")
    return prebuilt


def render_scene(shell: Path, scene: Path | dict, size: str, theme: str | None = None) -> list[str]:
    """The lines of an app screen, coloured. A dict scene is written to a temporary file first."""
    args = [str(shell), "--size", size, "--color"]
    if theme:
        args += ["--theme", theme]
    if isinstance(scene, dict):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, dir=scene.get("_dir")) as f:
            json.dump({k: v for k, v in scene.items() if not k.startswith("_")}, f)
            path = f.name
        try:
            return _run(args + ["--scene", path])
        finally:
            os.unlink(path)
    return _run(args + ["--scene", str(scene)])


def render_board(shell: Path, board: Path, tab: str, size: str, theme: str | None = None, **fixtures: Path) -> list[str]:
    """The lines of a board tab (Jobs, Queue, Workflows, Env, Settings or Logs) from fixture files, coloured."""
    args = [str(shell), "--once", "--no-ensure", "--color", "--size", size, "--tab", tab, "--fixture", str(board)]
    if theme:
        args += ["--theme", theme]
    for name, path in fixtures.items():  # workspace, fleet, settings, evidence
        args += [f"--{name}-fixture", str(path)]
    return _run(args)


def _run(args: list[str]) -> list[str]:
    out = subprocess.run(args, capture_output=True, text=True, encoding="utf-8")
    if out.returncode != 0:
        sys.exit(f"{' '.join(args[:1])} failed: {out.stderr.strip()}")
    lines = out.stdout.split("\n")
    while lines and lines[-1] == "":
        lines.pop()
    return lines


# --- SGR to HTML -----------------------------------------------------------------------------------------------------

def _xterm256(n: int) -> str:
    if n < 16:
        base = ["000000", "cd0000", "00cd00", "cdcd00", "0000ee", "cd00cd", "00cdcd", "e5e5e5",
                "7f7f7f", "ff0000", "00ff00", "ffff00", "5c5cff", "ff00ff", "00ffff", "ffffff"]
        return "#" + base[n]
    if n < 232:
        n -= 16
        r, g, b = n // 36, (n // 6) % 6, n % 6
        lv = [0, 95, 135, 175, 215, 255]
        return "#%02x%02x%02x" % (lv[r], lv[g], lv[b])
    v = 8 + (n - 232) * 10
    return "#%02x%02x%02x" % (v, v, v)


class Pen:
    __slots__ = ("fg", "bg", "bold", "dim", "italic", "under", "reverse")

    def __init__(self):
        self.fg = self.bg = None
        self.bold = self.dim = self.italic = self.under = self.reverse = False

    def apply(self, params: str, defaults: tuple[str | None, str | None]):
        codes = [int(c or 0) for c in params.split(";")] if params else [0]
        i = 0
        while i < len(codes):
            c = codes[i]
            if c == 0:
                self.__init__()
            elif c == 1:
                self.bold = True
            elif c == 2:
                self.dim = True
            elif c == 3:
                self.italic = True
            elif c == 4:
                self.under = True
            elif c == 7:
                self.reverse = True
            elif c == 22:
                self.bold = self.dim = False
            elif c == 23:
                self.italic = False
            elif c == 24:
                self.under = False
            elif c == 27:
                self.reverse = False
            elif 30 <= c <= 37:
                self.fg = _xterm256(c - 30)
            elif 90 <= c <= 97:
                self.fg = _xterm256(c - 90 + 8)
            elif 40 <= c <= 47:
                self.bg = _xterm256(c - 40)
            elif 100 <= c <= 107:
                self.bg = _xterm256(c - 100 + 8)
            elif c == 39:
                self.fg = None
            elif c == 49:
                self.bg = None
            elif c in (38, 48) and i + 1 < len(codes):
                colour = None
                if codes[i + 1] == 2 and i + 4 < len(codes):
                    colour = "#%02x%02x%02x" % tuple(min(255, x) for x in codes[i + 2:i + 5])
                    i += 4
                elif codes[i + 1] == 5 and i + 2 < len(codes):
                    colour = _xterm256(codes[i + 2])
                    i += 2
                if c == 38:
                    self.fg = colour
                else:
                    self.bg = colour
            i += 1

    def css(self, defaults: tuple[str | None, str | None]) -> str:
        fg = self.fg or defaults[0]
        bg = self.bg or defaults[1]
        if self.reverse:
            fg, bg = bg, fg
        parts = []
        if fg and fg != defaults[0]:
            parts.append(f"color:{fg}")
        if bg and bg != defaults[1]:
            parts.append(f"background:{bg}")
        if self.bold:
            parts.append("font-weight:700")
        if self.dim:
            parts.append("opacity:.7")
        if self.italic:
            parts.append("font-style:italic")
        if self.under:
            parts.append("text-decoration:underline")
        return ";".join(parts)


def frame_defaults(lines: list[str]) -> tuple[str | None, str | None]:
    """The frame's own text and ground colours: the first SGR of the first line sets both (ui.Paint's base)."""
    pen = Pen()
    for line in lines[:3]:
        m = SGR.match(line)
        if m:
            pen.apply(m.group(1), (None, None))
            if pen.fg and pen.bg:
                return pen.fg, pen.bg
    return pen.fg, pen.bg


def line_html(line: str, defaults: tuple[str | None, str | None]) -> str:
    out: list[str] = []
    pen = Pen()
    pos = 0
    line = OTHER_ESC.sub(lambda m: m.group(0) if m.group(0).startswith("\x1b[") and m.group(0).endswith("m") else "", line)
    for m in SGR.finditer(line):
        if m.start() > pos:
            out.append(_span(line[pos:m.start()], pen.css(defaults)))
        pen.apply(m.group(1), defaults)
        pos = m.end()
    if pos < len(line):
        out.append(_span(line[pos:], pen.css(defaults)))
    return "".join(out)


def _span(text: str, css: str) -> str:
    text = html.escape(text, quote=False)
    return f'<span style="{css}">{text}</span>' if css else text


def plain(lines: list[str]) -> list[str]:
    return [OTHER_ESC.sub("", SGR.sub("", l)) for l in lines]


def page_html(lines: list[str], cols: int, rows: int, px: tuple[int, int], pad: int = 0) -> str:
    """One HTML page the size of ``px`` with the frame centred in it, the font as large as fits."""
    fg, bg = frame_defaults(lines)
    bg = bg or "#fafafa"
    fg = fg or "#18181b"
    w, h = px
    fs = min((w - 2 * pad) / (cols * CELL_W), (h - 2 * pad) / (rows * LINE_H))
    fs = max(6.0, int(fs * 2) / 2)
    body = "\n".join(line_html(l, (fg, bg)) for l in lines)
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
html,body{{margin:0;width:{w}px;height:{h}px;overflow:hidden;background:{bg}}}
body{{display:flex;align-items:center;justify-content:center}}
pre{{margin:0;font:{fs}px/{LINE_H} {FONT_STACK};color:{fg};white-space:pre;letter-spacing:0;font-variant-ligatures:none;
-webkit-font-smoothing:antialiased;text-rendering:geometricPrecision}}
</style></head><body><pre>{body}</pre></body></html>"""


# --- Chromium ----------------------------------------------------------------------------------------------------------

def chrome() -> str:
    env = os.environ.get("RIG_CHROME") or os.environ.get("CHROME_PATH")
    if env:
        return env
    candidates = (
        glob.glob("/opt/pw-browsers/chromium_headless_shell-*/chrome-linux/headless_shell")
        + glob.glob(os.path.expanduser("~/.cache/ms-playwright/chromium_headless_shell-*/chrome-linux/headless_shell"))
        + glob.glob(os.path.expanduser("~/.cache/ms-playwright/chromium_headless_shell-*/chrome-mac*/headless_shell"))
        + glob.glob("/opt/pw-browsers/chromium-*/chrome-linux/chrome")
        + [shutil.which(n) or "" for n in ("chromium", "chromium-browser", "google-chrome", "google-chrome-stable")]
        + ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", "/Applications/Chromium.app/Contents/MacOS/Chromium"]
    )
    for c in sorted(set(filter(None, candidates)), key=candidates.index):
        if os.path.exists(c):
            return c
    sys.exit("no Chromium: set RIG_CHROME to a chrome, chromium or Playwright headless_shell binary")


def screenshot(html_text: str, out_png: Path, px: tuple[int, int], scale: int = 2) -> None:
    binary = chrome()
    with tempfile.TemporaryDirectory() as tmp:
        page = Path(tmp) / "frame.html"
        page.write_text(html_text, encoding="utf-8")
        args = [binary]
        if not binary.endswith("headless_shell"):
            args.append("--headless")
        args += ["--no-sandbox", "--disable-gpu", "--hide-scrollbars", "--no-first-run", "--disable-dev-shm-usage",
                 f"--user-data-dir={tmp}/profile", "--run-all-compositor-stages-before-draw", "--virtual-time-budget=2000",
                 f"--force-device-scale-factor={scale}", f"--window-size={px[0]},{px[1]}", f"--screenshot={out_png}",
                 page.as_uri()]
        out = subprocess.run(args, capture_output=True, text=True)
        if out.returncode != 0 or not out_png.exists():
            sys.exit(f"chromium failed: {out.stderr.strip()[-800:]}")


def to_webp(png: Path, out: Path, width: int | None = None, quality: int = 82) -> None:
    from PIL import Image  # pillow: pip install pillow

    im = Image.open(png).convert("RGB")
    if width and im.width != width:
        im = im.resize((width, round(im.height * width / im.width)), Image.LANCZOS)
    im.save(out, "WEBP", quality=quality, method=6)


def digest(lines: list[str]) -> str:
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()[:16]


def size_of(size: str) -> tuple[int, int]:
    cols, rows = size.lower().split("x")
    return int(cols), int(rows)
