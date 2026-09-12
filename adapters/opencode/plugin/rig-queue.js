// OpenCode plugin: park /queue and $queue via queue_submit_hook.py. Does not spawn.
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

function runHook(prompt, cwd) {
  const script = hookScript();
  if (!script) return null;
  const r = spawnSync("python3", [script], {
    input: JSON.stringify({ prompt, cwd }),
    encoding: "utf8",
    timeout: 10000,
    cwd: cwd || process.cwd(),
  });
  const out = String(r.stdout || "").trim();
  if (!out) return null;
  try {
    const obj = JSON.parse(out);
    return obj && typeof obj === "object" ? obj : null;
  } catch {
    return null;
  }
}

function joinText(parts) {
  const bits = [];
  for (const part of parts || []) {
    if (part && part.type === "text" && typeof part.text === "string") {
      bits.push(part.text);
    }
  }
  return bits.join("\n").trim();
}

function replaceParts(output, text) {
  const parts = Array.isArray(output.parts) ? output.parts : [];
  let done = false;
  for (const part of parts) {
    if (part && part.type === "text") {
      if (!done) {
        part.text = text;
        done = true;
      } else {
        part.text = "";
      }
    }
  }
  if (!done) parts.push({ type: "text", text });
  output.parts = parts;
}

function parkText(text, cwd) {
  const result = runHook(text, cwd);
  if (result && result.decision === "block") return result;
  return null;
}

export async function RigQueuePlugin({ directory }) {
  const cwd = directory || process.cwd();
  return {
    "command.execute.before": async (input, output) => {
      const name = String(input.command || "").toLowerCase();
      if (name !== "queue" && name !== "rig-queue") return;
      const prompt = "/queue " + String(input.arguments || "").trim();
      const result = parkText(prompt, cwd);
      if (!result) return;
      const text = result.systemMessage || result.reason || "rig queued";
      replaceParts(output, text);
      // OpenCode 1.17 has no noReply API; throw is the only skip of prompt().
      // 1.17.5+ may show this as a TUI error. Set flags for hosts that honor them.
      if (output && typeof output === "object") {
        output.noReply = true;
        output.cancelled = true;
        output.abort = true;
      }
      throw new Error("__RIG_QUEUE_HANDLED__");
    },
    "chat.message": async (_input, output) => {
      const text = joinText(output.parts);
      const result = parkText(text, cwd);
      if (!result) return;
      replaceParts(output, result.systemMessage || result.reason || "rig queued");
    },
  };
}

export default RigQueuePlugin;
