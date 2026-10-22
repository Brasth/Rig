#!/usr/bin/env python3
"""Record the rig shell walkthrough as a video, from scenes rather than a screen.

    python3 scripts/media/record.py                 # docs/media/rig-shell-walkthrough.{webm,mp4} and its poster
    python3 scripts/media/record.py --theme dark --fps 12 --out /tmp/clip

The story below is a list of moments: the screen as a scene (what ``rig-shell --scene`` draws), with typing and the
parent's answer streaming in made of many small frames. Every distinct frame is drawn once (frame.py) and ffmpeg joins
them with their durations, so the clip is the same on every machine and takes about a minute to make. Needs what
render.py needs, and ffmpeg with libvpx-vp9 and libx264.
"""
from __future__ import annotations

import argparse
import copy
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import frame  # noqa: E402

HERE = Path(__file__).resolve().parent
OUT = frame.ROOT / "docs" / "media"
SIZE = "120x36"
PX = (1280, 800)
TYPE_EVERY = 0.045  # seconds per typed character
WORD_EVERY = 0.07  # seconds per streamed word
SPIN_EVERY = 0.25  # the working spinner's frames
T0 = "2026-10-22T09:08:10Z"


def at(seconds: float) -> str:
    base = 9 * 3600 + 8 * 60 + 10 + seconds
    h, rem = divmod(int(base), 3600)
    m, s = divmod(rem, 60)
    return f"2026-10-22T{h:02d}:{m:02d}:{s:02d}Z"


def load_scene(name: str, theme: str) -> dict:
    """A scene file as the story's starting point, in the clip's theme, its snapshot path made absolute."""
    sc = json.loads((HERE / "scenes" / f"{name}.json").read_text(encoding="utf-8"))
    sc["theme"] = theme
    if sc.get("snapshot"):
        sc["snapshot"] = str((HERE / "scenes" / sc["snapshot"]).resolve())
    return sc


def story(theme: str) -> list[tuple[dict, float]]:
    """(scene, seconds) for every frame of the clip."""
    base = load_scene("conversation", theme)
    welcome = load_scene("welcome", theme)
    final_chat = base["chat"]
    entries = final_chat["entries"]
    frames: list[tuple[dict, float]] = []

    def moment(seconds: float, **changes) -> dict:
        sc = copy.deepcopy(base)
        sc.pop("chat", None)
        sc.update(changes)
        frames.append((sc, seconds))
        return sc

    # 1. first run: who to talk to
    frames.append((welcome, 1.8))
    # 2. the empty first conversation, then the request typed in
    moment(1.2, prompt="")
    text = entries[0]["text"]
    for i in range(1, len(text) + 1):
        moment(TYPE_EVERY, prompt=text[:i])
    moment(0.6, prompt=text)
    # 3. the parent works: the tool calls arrive, the spinner turns
    clock = 0.0

    def running(tool_count: int, partial: str = "", seconds: float = 1.0):
        nonlocal clock
        n = max(1, round(seconds / SPIN_EVERY))
        for _ in range(n):
            clock += SPIN_EVERY
            chat = {"session": "c-1a2b3c4d", "parent": "claude", "state": "running", "turns": 0, "asks": True, "mode": "ask",
                    "entries": [dict(entries[0], at=T0)] + entries[1:1 + tool_count], "partial": partial}
            moment(SPIN_EVERY, chat=chat, now=at(clock))

    running(0, seconds=1.0)
    running(1, seconds=1.2)
    running(2, seconds=1.5)
    # 4. a look at the helpers while it works
    sc = copy.deepcopy(frames[-1][0])
    sc.update(panel=True, panel_sel=1)
    frames.append((sc, 2.2))
    # 5. the answer streams in
    words = entries[3]["text"].split(" ")
    for i in range(1, len(words) + 1):
        clock += WORD_EVERY
        chat = {"session": "c-1a2b3c4d", "parent": "claude", "state": "running", "turns": 0, "asks": True, "mode": "ask",
                "entries": [dict(entries[0], at=T0)] + entries[1:3], "partial": " ".join(words[:i])}
        moment(WORD_EVERY, chat=chat, now=at(clock))
    # 6. the turn ends: the diff and the test result under the answer, the Review / Commit / Undo bar
    moment(0.5, chat=dict(final_chat, state="running", turns=0, entries=entries[:4], marks=[]), now=at(clock + 0.3))
    moment(3.2, chat=final_chat, now=at(clock + 0.8))
    # 7. g: review the file the turn changed, on the code tab
    code = load_scene("code", theme)
    code["chat"] = final_chat
    code["file"]["line"] = 20
    code["file"]["gutter"] = {"20": "M", "21": "M", "22": "M"}
    frames.append((code, 2.8))
    # 8. c: committed, back in the conversation
    moment(2.6, chat=dict(final_chat, marks=[]), message="committed src/cli.py: Fix the failing tests in tests/test_cli.py (a1f3c9d)")
    moment(1.4, chat=dict(final_chat, marks=[]))
    return frames


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--theme", default="light")
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--name", default="rig-shell-walkthrough")
    ap.add_argument("--fps", type=int, default=24, help="the clip's frame rate; frames are held, not repeated, so this only sets smoothness")
    ap.add_argument("--no-build", action="store_true")
    args = ap.parse_args()
    if not shutil.which("ffmpeg"):
        sys.exit("ffmpeg is needed")
    shell = frame.shell_binary(build=not args.no_build)
    cols, rows = frame.size_of(SIZE)
    args.out.mkdir(parents=True, exist_ok=True)
    frames = story(args.theme)
    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        drawn: dict[str, Path] = {}
        concat = []
        total = 0.0
        for i, (scene, seconds) in enumerate(frames):
            lines = frame.render_scene(shell, scene, SIZE)
            key = frame.digest(lines)
            if key not in drawn:
                png = tmpdir / f"f{len(drawn):04d}.png"
                frame.screenshot(frame.page_html(lines, cols, rows, PX), png, PX, scale=1)
                drawn[key] = png
                print(f"\rframe {i + 1}/{len(frames)} ({len(drawn)} distinct)", end="", file=sys.stderr)
            concat.append(f"file '{drawn[key]}'\nduration {seconds:.3f}")
            total += seconds
        concat.append(f"file '{drawn[key]}'")  # the concat demuxer needs the last file once more
        print(f"\n{len(drawn)} distinct frames, {total:.1f}s", file=sys.stderr)
        listing = tmpdir / "frames.txt"
        listing.write_text("\n".join(concat) + "\n", encoding="utf-8")
        common = ["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(listing),
                  "-vf", f"fps={args.fps},scale=trunc(iw/2)*2:trunc(ih/2)*2", "-pix_fmt", "yuv420p"]
        subprocess.run(common + ["-c:v", "libvpx-vp9", "-b:v", "0", "-crf", "33", "-row-mt", "1", "-an",
                                 str(args.out / f"{args.name}.webm")], check=True)
        subprocess.run(common + ["-c:v", "libx264", "-preset", "slow", "-crf", "23", "-tune", "stillimage", "-movflags", "+faststart", "-an",
                                 str(args.out / f"{args.name}.mp4")], check=True)
        # the poster: the frame with the finished turn, the Review / Commit / Undo bar and the diff
        poster_lines = frame.render_scene(shell, frames[-4][0], SIZE)
        png = tmpdir / "poster.png"
        frame.screenshot(frame.page_html(poster_lines, cols, rows, PX), png, PX, scale=2)
        frame.to_webp(png, args.out / f"{args.name}-poster.webp", width=PX[0], quality=82)
    for ext in ("webm", "mp4", "poster.webp"):
        p = args.out / f"{args.name}{'-' if ext.endswith('webp') else '.'}{ext}"
        print(f"wrote {p.name} ({p.stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
