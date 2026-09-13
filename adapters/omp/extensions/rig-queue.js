// OMP/Pi extension: /queue parks via queue_submit_hook.py even while streaming.
// Read-only HUD via jobs.py hud. Does not spawn.
import { spawnSync } from "child_process";
import { existsSync } from "fs";
import { homedir } from "os";
import { join } from "path";

function kitScript(name) {
  const home = process.env.RIG_HOME
    ? join(process.env.RIG_HOME, "scripts", name)
    : "";
  const fallback = join(homedir(), ".rig", "scripts", name);
  if (home && existsSync(home)) return home;
  if (existsSync(fallback)) return fallback;
  return "";
}

function runPython(args, cwd, input) {
  const script = args[0] === "hud" ? kitScript("jobs.py") : kitScript("queue_submit_hook.py");
  if (!script) return { stdout: "", status: 1 };
  const argv = args[0] === "hud" ? [script, "hud", "--json"] : [script, ...args.slice(1)];
  return spawnSync("python3", argv, {
    input: input || "",
    encoding: "utf8",
    timeout: args[0] === "hud" ? 5000 : 10000,
    cwd: cwd || process.cwd(),
  });
}

function notify(ctx, text) {
  const line = String(text || "").trim();
  if (!line) return;
  try {
    if (ctx && ctx.ui && typeof ctx.ui.notify === "function") {
      ctx.ui.notify(line, "info");
      return;
    }
  } catch {
    /* no UI */
  }
}

function hudLines(cwd) {
  const r = runPython(["hud"], cwd, "");
  const out = String(r.stdout || "").trim();
  if (!out) return ["rig · idle · QUEUE 0"];
  try {
    const obj = JSON.parse(out);
    const lines = Array.isArray(obj.lines) ? obj.lines.filter(Boolean) : [];
    if (lines.length) return lines.map((x) => String(x));
    if (obj.text) return String(obj.text).split("\n");
  } catch {
    return out.split("\n").slice(0, 5);
  }
  return ["rig · idle · QUEUE 0"];
}

function paint(ctx, cwd) {
  if (!ctx || !ctx.ui) return;
  const lines = hudLines(cwd);
  try {
    if (typeof ctx.ui.setWidget === "function") {
      ctx.ui.setWidget("rig", lines, "below");
    }
  } catch {
    /* old Pi */
  }
  try {
    if (typeof ctx.ui.setStatus === "function") {
      const action = lines.find((line) => /\brig job (?:allow|deny|reconcile)\b/.test(line));
      ctx.ui.setStatus("rig", action || lines[1] || lines[0] || "rig");
    }
  } catch {
    /* no status */
  }
}

export default function (pi) {
  const onTurn = async (_event, ctx) => {
    const cwd = (ctx && ctx.cwd) || process.cwd();
    paint(ctx, cwd);
  };
  for (const ev of ["session_start", "turn_start", "turn_end"]) {
    try {
      if (typeof pi.on === "function") pi.on(ev, onTurn);
    } catch {
      /* event missing */
    }
  }
  pi.registerCommand("queue", {
    description: "Park a Rig work item. Does not spawn a child.",
    handler: async (args, ctx) => {
      const cwd = (ctx && ctx.cwd) || process.cwd();
      const rest = String(args || "").trim();
      const prompt = "/queue" + (rest ? " " + rest : "");
      const r = runPython(["hook"], cwd, JSON.stringify({ prompt, cwd }));
      const out = String(r.stdout || "").trim();
      let obj = null;
      if (out) {
        try {
          obj = JSON.parse(out);
        } catch {
          obj = null;
        }
      }
      if (obj && obj.decision === "block") {
        notify(ctx, obj.systemMessage || obj.reason);
        paint(ctx, cwd);
        return;
      }
      const listed = runPython(["hook", "--print-list"], cwd, "");
      notify(ctx, listed.stdout || "QUEUE empty");
      paint(ctx, cwd);
    },
  });
}
