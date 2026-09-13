#!/usr/bin/env node
import { renderMermaidASCII } from "./beautiful-mermaid-ascii.mjs";

const MAX_INPUT = 2 * 1024 * 1024;

function send(payload) {
  process.stdout.write(JSON.stringify(payload));
}

async function readStdin() {
  const chunks = [];
  let total = 0;
  for await (const chunk of process.stdin) {
    total += chunk.length;
    if (total > MAX_INPUT) {
      throw new Error("renderer input too large");
    }
    chunks.push(chunk);
  }
  return Buffer.concat(chunks).toString("utf8");
}

try {
  const raw = await readStdin();
  const msg = JSON.parse(raw);
  const source = typeof msg.source === "string" ? msg.source : "";
  const useAscii = Boolean(msg.useAscii);
  const text = renderMermaidASCII(source, { useAscii, colorMode: "none" });
  if (typeof text !== "string") {
    send({ ok: false, error: "renderer returned non-text" });
  } else {
    send({ ok: true, text });
  }
} catch (err) {
  const error = err && err.message ? String(err.message) : String(err);
  send({ ok: false, error });
}
