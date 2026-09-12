/** @jsxImportSource @opentui/solid */
// OpenCode TUI HUD: read-only jobs.py hud in footer + sidebar. Does not spawn.
// Load from an absolute file path in tui.json (not an npm spec).
import { spawnSync } from "child_process"
import { existsSync } from "fs"
import { homedir } from "os"
import { join } from "path"
import { For, createSignal, onCleanup } from "solid-js"

function jobsPy(): string {
  const home = process.env.RIG_HOME
    ? join(process.env.RIG_HOME, "scripts", "jobs.py")
    : ""
  const fallback = join(homedir(), ".rig", "scripts", "jobs.py")
  if (home && existsSync(home)) return home
  if (existsSync(fallback)) return fallback
  return ""
}

function hudLines(cwd: string): string[] {
  const script = jobsPy()
  if (!script) return ["rig · idle · QUEUE 0"]
  const r = spawnSync("python3", [script, "hud", "--json"], {
    encoding: "utf8",
    timeout: 5000,
    cwd: cwd || process.cwd(),
  })
  const out = String(r.stdout || "").trim()
  if (!out) return ["rig · idle · QUEUE 0"]
  try {
    const obj = JSON.parse(out)
    const lines = Array.isArray(obj.lines) ? obj.lines.filter(Boolean) : []
    if (lines.length) return lines.map((x: unknown) => String(x))
    if (obj.text) return String(obj.text).split("\n")
  } catch {
    return out.split("\n").slice(0, 5)
  }
  return ["rig · idle · QUEUE 0"]
}

function HudLines(props: { cwd: string; max?: number }) {
  const [lines, setLines] = createSignal(hudLines(props.cwd))
  const tick = () => setLines(hudLines(props.cwd))
  tick()
  const id = setInterval(tick, 5000)
  onCleanup(() => clearInterval(id))
  const shown = () => lines().slice(0, props.max ?? 5)
  return (
    <box flexDirection="column">
      <For each={shown()}>{(line) => <text>{line}</text>}</For>
    </box>
  )
}

export async function tui(api: {
  slots: { register: (plugin: unknown) => string }
  state: { path: { directory?: string } }
}) {
  const cwd = () => api.state?.path?.directory || process.cwd()
  api.slots.register({
    order: 50,
    slots: {
      home_footer() {
        return <HudLines cwd={cwd()} max={2} />
      },
      sidebar_content() {
        return <HudLines cwd={cwd()} max={5} />
      },
    },
  })
}

export default { id: "rig-hud", tui }
