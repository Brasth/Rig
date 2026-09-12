// OMP/Pi extension: /queue parks via queue_submit_hook.py even while streaming. Does not spawn.
import { spawnSync } from "child_process";
import { existsSync } from "fs";
import { homedir } from "os";
import { join } from "path";

function hookScript() {
  const home = process.env.RIG_HOME
    ? join(process.env.RIG_HOME, "scripts", "queue_submit_hook.py")
    : "";
  const fallback = join(homedir(), ".rig", "scripts", "queue_submit_hook.py");
  if (home && existsSync(home)) return home;
  if (existsSync(fallback)) return fallback;
  return "";
}

function runPython(args, cwd, input) {
  const script = hookScript();
  if (!script) return { stdout: "", status: 1 };
  return spawnSync("python3", [script, ...args], {
    input: input || "",
    encoding: "utf8",
    timeout: 10000,
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

export default function (pi) {
  pi.registerCommand("queue", {
    description: "Park a Rig work item. Does not spawn a child.",
    handler: async (args, ctx) => {
      const cwd = (ctx && ctx.cwd) || process.cwd();
      const rest = String(args || "").trim();
      const prompt = "/queue" + (rest ? " " + rest : "");
      const r = runPython([], cwd, JSON.stringify({ prompt, cwd }));
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
        return;
      }
      const listed = runPython(["--print-list"], cwd, "");
      notify(ctx, listed.stdout || "QUEUE empty");
    },
  });
}
