# Mermaid diagram preview

`rig diagram` prints Mermaid diagrams as terminal text. Default output is Unicode box drawing on stdout, which you can use directly in tmux. It does **not** render diagrams inline in Codex, Grok, or other CLIs, and it does not open a browser.

## Command

```bash
./bin/rig diagram PATH [--ascii] [--popup] [--output PATH]
```

`PATH` is either:

- Markdown (`.md` or any non-raw suffix) containing ` ```mermaid ` or `~~~mermaid` fenced blocks
- A raw `.mmd` or `.mermaid` file (the whole file is one diagram)

Examples:

```bash
./bin/rig diagram README.md
./bin/rig diagram README.md --ascii
./bin/rig diagram docs/flow.mmd --popup
./bin/rig diagram docs/flow.mmd --output /tmp/flow.txt
```

## What it does

1. Reads at most 1 MiB and at most 20 diagrams.
2. Renders each diagram separately through a local Node helper. A bad or unsupported diagram prints a clear error plus sanitized source; later diagrams still render. The command exits nonzero if any diagram failed.
3. Default stdout is Unicode text. `--ascii` uses plain ASCII (`+`, `-`, `|`).
4. `--output PATH` writes that text only (mode 0600), never HTML, and is refused if the path already exists. It never overwrites the source file.
5. `--popup` shows a scrollable tmux `display-popup` with `less -S` on a private temp file. It uses `-S` with the socket from `TMUX` (value before the first comma) and `-t` with `TMUX_PANE`. It does not change global/root/copy-mode key tables.

No browser, CDN, or runtime network is used.

## Dependencies

- **Node.js** on `PATH` (the `node` binary). Missing Node fails with an install hint, not a traceback.
- **`--popup`:** tmux session (`TMUX` + `TMUX_PANE`) and `less` on `PATH`.

## Supported diagrams

The ASCII engine is beautiful-mermaid 1.1.3. It renders:

- flowchart / `graph` (including state diagrams)
- sequence
- class
- ER
- XYChart

Other Mermaid types (pie, gitGraph, mindmap, gantt, and similar) are **not** claimed to work. They fail per diagram with the source shown.

## Limits

- Not a substitute for GitHub or Codex markdown preview.
- Non-mermaid fenced blocks are skipped as a whole (inner ```mermaid examples are not diagrams).
- Unclosed or empty/whitespace mermaid fences and files with no diagrams fail with a line-numbered error.
- `--output` will not replace an existing file.
- Control and escape characters are stripped from terminal-displayed labels, errors, source, and output. Newline and tab are kept.
- Each Node render is bounded (timeout and child memory). Oversized renderer output is rejected.

## Renderer provenance

Terminal rendering vendors the MIT ASCII engine from **beautiful-mermaid 1.1.3**.

- npm: `beautiful-mermaid@1.1.3`
- tarball: `https://registry.npmjs.org/beautiful-mermaid/-/beautiful-mermaid-1.1.3.tgz`
- integrity: `sha512-TItrtrAyHp1vwFfFVYauWGrquouk/6SS21Aq3RsxindSYZODcN4xYrPZD6BiZRU+o5mKJzDPz9MUSMvELdylyg==`
- vendored files: `scripts/beautiful-mermaid-ascii.mjs` (entry `package/src/ascii/index.ts` only), `scripts/beautiful-mermaid-LICENSE.txt`
- not used: package main `src/index.ts` (pulls elkjs/entities for SVG)
- helper: `scripts/diagram-renderer.mjs` reads stdin JSON `{source,useAscii}`, calls `renderMermaidASCII` with `colorMode: "none"`, writes JSON

No `package.json` or `node_modules` is committed. The installer copies top-level `scripts/` files, so the bundle and runner stay there.

### Reproducible rebuild

From the repo root, in a temporary directory (pinned esbuild **0.25.9**):

```bash
tmpdir=$(mktemp -d)
tarball="$tmpdir/beautiful-mermaid-1.1.3.tgz"
curl -fsSL https://registry.npmjs.org/beautiful-mermaid/-/beautiful-mermaid-1.1.3.tgz -o "$tarball"
python3 - "$tarball" <<'PY'
import base64, hashlib, pathlib, sys
path = pathlib.Path(sys.argv[1])
got = "sha512-" + base64.b64encode(hashlib.sha512(path.read_bytes()).digest()).decode()
expected = "sha512-TItrtrAyHp1vwFfFVYauWGrquouk/6SS21Aq3RsxindSYZODcN4xYrPZD6BiZRU+o5mKJzDPz9MUSMvELdylyg=="
if got != expected:
    raise SystemExit("tarball sha512 mismatch: " + got)
print("hash ok")
PY
tar -xzf "$tarball" -C "$tmpdir"
npm install --prefix "$tmpdir/esbuild" --ignore-scripts esbuild@0.25.9
"$tmpdir/esbuild/node_modules/.bin/esbuild" \
  "$tmpdir/package/src/ascii/index.ts" \
  --bundle --format=esm --platform=neutral --target=es2020 \
  --legal-comments=none \
  --banner:js='/* Vendored from beautiful-mermaid 1.1.3 (MIT). Rebuild: docs/diagram-preview.md. Do not edit by hand. */' \
  --outfile=scripts/beautiful-mermaid-ascii.mjs
cp "$tmpdir/package/LICENSE" scripts/beautiful-mermaid-LICENSE.txt
```

## Files

- `scripts/diagram_preview.py` — CLI and bounded Markdown / `.mmd` extraction
- `scripts/diagram_terminal.py` — Node subprocess, sanitization, tmux popup
- `scripts/diagram-renderer.mjs` — stdin JSON → ASCII/Unicode text
- `scripts/beautiful-mermaid-ascii.mjs` — vendored ASCII renderer
- `scripts/beautiful-mermaid-LICENSE.txt` — MIT license from 1.1.3
