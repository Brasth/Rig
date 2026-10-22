# Screenshots and the walkthrough

Pictures of `rig shell` for the README, brasth.com and rig.brasth.com. Nothing here was captured from a screen: every
file is drawn from a scene file by `rig-shell --scene` (an app screen) or `rig-shell --once` (the board), so it is the
same on every machine and is remade in a minute after the shell changes. The scenes and the fixtures they use live in
`scripts/media/`.

| File | What it shows |
| --- | --- |
| `rig-shell-conversation-{light,dark}.webp` | The conversation: a turn's tool calls, its answer, the diff it made, and the Review, Commit and Undo bar |
| `rig-shell-panel-{light,dark}.webp` | The agents panel (⇥): helpers at work, who started them, and the queue |
| `rig-shell-welcome-{light,dark}.webp` | First run: pick who to talk to and you are in the conversation |
| `rig-shell-code-{light,dark}.webp` | The code tab: explorer, tabs, git marks in the gutter, a check's problem on its line |
| `rig-shell-doctor-{light,dark}.webp` | `/doctor`: what is wrong with the project in plain words, each with its fix |
| `rig-shell-settings-{light,dark}.webp` | Settings: theme, default parent and its model, where rig starts |
| `rig-shell-jobs-{light,dark}.webp` | The board (`rig shell --layout board`): jobs needing attention first, then active and finished |
| `rig-shell-env-{light,dark}.webp` | The Env tab: the dev container, its ports, per-job worktrees and the fleet |
| `rig-shell-walkthrough.{webm,mp4}` | 24 seconds: first run, a request typed in, the parent working with helpers beside it, the answer and its diff, review on the code tab, commit |
| `rig-shell-walkthrough-poster.webp` | The clip's poster: the finished turn with its diff and the bar |

Every screenshot is 120×36 cells drawn into 1440×900 pixels; `@2x` is the same picture at 2880×1800 for high-density
screens. The clip is 1280×800, with no sound.

## Remaking them

```sh
python3 scripts/media/render.py                 # every screenshot, light and dark
python3 scripts/media/render.py conversation --theme dark --png
python3 scripts/media/record.py                 # the walkthrough, webm and mp4, and its poster
```

Needs Go 1.24+ (the clone's shell is built; set `RIG_SHELL_BIN` to use another), a Chromium (Playwright's
`headless_shell` under `/opt/pw-browsers` or `~/.cache/ms-playwright` is found by itself; else set `RIG_CHROME`),
Pillow, JetBrains Mono installed as a system font, and ffmpeg with libvpx-vp9 and libx264 for the clip.

## Changing what they show

- A screenshot is a scene in `scripts/media/scenes/`: the screen (`conversation`, `welcome`, `code`, `settings`,
  `doctor`), the theme, the conversation (`chat`, a `chat.transcript` reply), who answers (`status`), the open file, the
  doctor's findings, the settings. The helpers on screen come from the board snapshot the scene names
  (`scripts/media/boards/busy.json`); the board shots use `full.json` with the workspace and fleet fixtures beside it.
- The clip's story is `story()` in `scripts/media/record.py`: a list of moments, each a scene and how long it stays.
  Typing and the streamed answer are made of many short moments; identical frames are drawn once.
- After a change to the shell's screens, rerun both scripts and commit what changed. `rig-shell --scene FILE` with no
  `--color` prints the frame as plain text, which is the quickest way to check a scene.
