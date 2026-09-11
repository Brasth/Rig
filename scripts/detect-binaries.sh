# Rig shared helpers. Source this file; do not execute it.

rig_home() {
  printf '%s\n' "${RIG_HOME:-$HOME/.rig}"
}

rig_script_dir() {
  local src="${BASH_SOURCE[0]}"
  (cd "$(dirname "$src")" && pwd)
}

iso_now() {
  date -u +%Y-%m-%dT%H:%M:%SZ
}

RIG_WORKERS=(grok claude codex cursor opencode omp pi agy)

find_bin() {
  command -v "$1" 2>/dev/null || true
}

# cursor → cursor-agent, else `agent` only if it is Cursor's binary.
find_worker_bin() {
  local name="$1" p real
  case "$name" in
    grok|claude|codex|opencode|omp|pi|agy)
      find_bin "$name"
      ;;
    cursor)
      p="$(find_bin cursor-agent)"
      if [[ -n "$p" ]]; then
        printf '%s\n' "$p"
        return 0
      fi
      p="$(find_bin agent)"
      [[ -n "$p" ]] || return 0
      real="$(readlink "$p" 2>/dev/null || true)"
      if [[ "$p" == *cursor-agent* || "$real" == *cursor-agent* ]]; then
        printf '%s\n' "$p"
      fi
      ;;
    *)
      find_bin "$name"
      ;;
  esac
}

find_app() {
  local name="$1" p=""
  case "$name" in
    grok-bot) p="/Applications/Grok Bot.app" ;;
    cursor) p="/Applications/Cursor.app" ;;
    *) return 0 ;;
  esac
  if [[ -d "$p" ]]; then
    printf '%s\n' "$p"
  fi
}

repo_root() {
  local d kit marker
  d="$(pwd -P 2>/dev/null || pwd)"
  kit="$(rig_home)"
  if [[ -d "$kit" ]]; then
    kit="$(cd "$kit" && pwd -P)"
  fi
  while [[ "$d" != "/" ]]; do
    if [[ -n "$kit" && "$d" == "$kit" ]]; then
      d="$(dirname "$d")"
      continue
    fi
    if [[ -f "$d/.rig/harness.toml" ]]; then
      marker="$d/.rig"
      if [[ -d "$marker" ]]; then
        marker="$(cd "$marker" && pwd -P)"
      fi
      if [[ -z "$kit" || "$marker" != "$kit" ]]; then
        printf '%s\n' "$d"
        return 0
      fi
    fi
    if [[ -d "$d/.git" ]]; then
      printf '%s\n' "$d"
      return 0
    fi
    d="$(dirname "$d")"
  done
  pwd -P 2>/dev/null || pwd
}

harness_path() {
  printf '%s\n' "$(repo_root)/.rig/harness.toml"
}

# Sets HARNESS_PARENT, HARNESS_WORKER_{CODEX,GROK,CLAUDE,CURSOR,OPENCODE,OMP,PI,AGY}.
# [parent] profile in old harness files is ignored (not a spawn/pick input).
parse_harness() {
  local file="${1:-$(harness_path)}"
  HARNESS_PARENT="codex"
  HARNESS_WORKER_CODEX="false"
  HARNESS_WORKER_GROK="false"
  HARNESS_WORKER_CLAUDE="false"
  HARNESS_WORKER_CURSOR="false"
  HARNESS_WORKER_OPENCODE="false"
  HARNESS_WORKER_OMP="false"
  HARNESS_WORKER_PI="false"
  HARNESS_WORKER_AGY="false"
  HARNESS_FILE="$file"
  [[ -f "$file" ]] || return 0

  local section="" line key val
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line%%$'\r'}"
    if [[ "$line" =~ ^[[:space:]]*# ]]; then
      continue
    fi
    if [[ "$line" =~ ^[[:space:]]*\[([^]]+)\][[:space:]]*$ ]]; then
      section="${BASH_REMATCH[1]}"
      continue
    fi
    if [[ "$line" =~ ^[[:space:]]*([A-Za-z0-9_]+)[[:space:]]*=[[:space:]]*(.*)$ ]]; then
      key="${BASH_REMATCH[1]}"
      val="${BASH_REMATCH[2]}"
      val="${val%%#*}"
      val="${val#"${val%%[![:space:]]*}"}"
      val="${val%"${val##*[![:space:]]}"}"
      val="${val%\"}"
      val="${val#\"}"
      case "$section" in
        "")
          if [[ "$key" == "parent" ]]; then
            HARNESS_PARENT="$val"
          fi
          ;;
        workers)
          case "$key" in
            codex) HARNESS_WORKER_CODEX="$val" ;;
            grok) HARNESS_WORKER_GROK="$val" ;;
            claude) HARNESS_WORKER_CLAUDE="$val" ;;
            cursor) HARNESS_WORKER_CURSOR="$val" ;;
            opencode) HARNESS_WORKER_OPENCODE="$val" ;;
            omp) HARNESS_WORKER_OMP="$val" ;;
            pi) HARNESS_WORKER_PI="$val" ;;
            agy) HARNESS_WORKER_AGY="$val" ;;
          esac
          ;;
      esac
    fi
  done < "$file"
}

preferred_parent() {
  parse_harness "$(harness_path)"
  printf '%s\n' "$HARNESS_PARENT"
}

worker_flag() {
  local name="$1"
  parse_harness "$(harness_path)"
  case "$name" in
    codex) printf '%s\n' "$HARNESS_WORKER_CODEX" ;;
    grok) printf '%s\n' "$HARNESS_WORKER_GROK" ;;
    claude) printf '%s\n' "$HARNESS_WORKER_CLAUDE" ;;
    cursor) printf '%s\n' "$HARNESS_WORKER_CURSOR" ;;
    opencode) printf '%s\n' "$HARNESS_WORKER_OPENCODE" ;;
    omp) printf '%s\n' "$HARNESS_WORKER_OMP" ;;
    pi) printf '%s\n' "$HARNESS_WORKER_PI" ;;
    agy) printf '%s\n' "$HARNESS_WORKER_AGY" ;;
    *) printf '%s\n' "false" ;;
  esac
}

_ps_comm() {
  ps -o comm= -p "$1" 2>/dev/null | tr -d ' '
}

_ps_ppid() {
  ps -o ppid= -p "$1" 2>/dev/null | tr -d ' '
}

_ps_command() {
  ps -o command= -p "$1" 2>/dev/null
}

live_parent() {
  local forced="${RIG_PARENT:-}"
  case "$forced" in
    grok|codex|claude|cursor|opencode|omp|pi|agy)
      printf '%s\n' "$forced"
      return 0
      ;;
  esac

  if [[ -n "${CLAUDECODE:-}" || "${CLAUDE_CODE:-}" == "1" ]]; then
    printf '%s\n' "claude"
    return 0
  fi

  local pid="${1:-$PPID}"
  local i=0 comm cmd
  while [[ "$i" -lt 8 && -n "$pid" && "$pid" != "0" && "$pid" != "1" ]]; do
    comm="$(_ps_comm "$pid")"
    comm="${comm##*/}"
    comm="${comm%:*}"
    case "$comm" in
      grok|grok-*)
        printf '%s\n' "grok"
        return 0
        ;;
      codex|codex-*)
        printf '%s\n' "codex"
        return 0
        ;;
      claude|claude-*)
        printf '%s\n' "claude"
        return 0
        ;;
      cursor-agent|cursor-agent-*)
        printf '%s\n' "cursor"
        return 0
        ;;
      opencode|opencode-*)
        printf '%s\n' "opencode"
        return 0
        ;;
      omp|omp-*)
        printf '%s\n' "omp"
        return 0
        ;;
      pi|pi-*)
        printf '%s\n' "pi"
        return 0
        ;;
      agy|agy-*)
        printf '%s\n' "agy"
        return 0
        ;;
      agent|agent-*)
        cmd="$(_ps_command "$pid")"
        if [[ "$cmd" == *cursor-agent* ]]; then
          printf '%s\n' "cursor"
          return 0
        fi
        ;;
    esac
    pid="$(_ps_ppid "$pid")"
    i=$((i + 1))
  done
  printf '%s\n' ""
}

# effective = flag true AND binary on PATH AND worker != live parent
effective_worker() {
  local name="$1"
  local live flag bin
  live="$(live_parent)"
  flag="$(worker_flag "$name")"
  bin="$(find_worker_bin "$name")"
  [[ "$flag" == "true" ]] || return 1
  [[ -n "$bin" ]] || return 1
  [[ "$name" != "$live" ]] || return 1
  return 0
}

json_escape() {
  local s="$1"
  if command -v python3 >/dev/null 2>&1; then
    RESULT_JSON_ESCAPE_IN="$s" python3 -c 'import json,os; print(json.dumps(os.environ["RESULT_JSON_ESCAPE_IN"])[1:-1], end="")'
    return 0
  fi
  s="${s//\\/\\\\}"
  s="${s//\"/\\\"}"
  s="${s//$'\n'/\\n}"
  s="${s//$'\r'/\\r}"
  s="${s//$'\t'/\\t}"
  printf '%s' "$s"
}

# rig_set_toml_key FILE SECTION KEY VALUE
# SECTION empty = top-level. VALUE is the raw RHS (include quotes for strings).
rig_set_toml_key() {
  local file="$1" section="$2" key="$3" value="$4"
  if ! command -v python3 >/dev/null 2>&1; then
    echo "rig: python3 is required to edit $file" >&2
    return 1
  fi
  python3 - "$file" "$section" "$key" "$value" <<'PY'
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
section = sys.argv[2]
key = sys.argv[3]
value = sys.argv[4]
target = section or None
lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []


def is_header(line: str) -> bool:
    s = line.strip()
    return s.startswith("[") and s.endswith("]") and not s.startswith("[[")


def header_name(line: str) -> str:
    return line.strip()[1:-1]


def is_key(line: str, want: str) -> bool:
    s = line.strip()
    if not s or s.startswith("#"):
        return False
    s = s.split("#", 1)[0].strip()
    return s.startswith(want + "=") or s.startswith(want + " =")


out = []
current = None
replaced = False
section_seen = target is None
i = 0
while i < len(lines):
    line = lines[i]
    if is_header(line):
        if current == target and not replaced:
            out.append(f"{key} = {value}")
            replaced = True
        current = header_name(line)
        if current == target:
            section_seen = True
        out.append(line)
        i += 1
        continue
    if current == target and is_key(line, key):
        out.append(f"{key} = {value}")
        replaced = True
        i += 1
        continue
    out.append(line)
    i += 1

if not replaced:
    if target is None:
        final = []
        inserted = False
        for line in out:
            if is_header(line) and not inserted:
                final.append(f"{key} = {value}")
                inserted = True
            final.append(line)
        if not inserted:
            if final and final[-1].strip() != "":
                final.append("")
            final.append(f"{key} = {value}")
        out = final
    else:
        if not section_seen:
            if out and out[-1].strip() != "":
                out.append("")
            out.append(f"[{target}]")
        out.append(f"{key} = {value}")

text = "\n".join(out)
if text and not text.endswith("\n"):
    text += "\n"
path.parent.mkdir(parents=True, exist_ok=True)
tmp = path.with_name(path.name + ".tmp")
tmp.write_text(text, encoding="utf-8")
tmp.replace(path)
PY
}

# Return 0 if a top-level (pre-table) key exists.
rig_has_top_key() {
  local file="$1" key="$2"
  [[ -f "$file" ]] || return 1
  python3 - "$file" "$key" <<'PY'
import sys
from pathlib import Path
text = Path(sys.argv[1]).read_text(encoding="utf-8")
key = sys.argv[2]
for line in text.splitlines():
    s = line.strip()
    if s.startswith("[") and s.endswith("]") and not s.startswith("[["):
        sys.exit(1)
    if s.startswith("#") or not s:
        continue
    body = s.split("#", 1)[0].strip()
    if body.startswith(key + "=") or body.startswith(key + " ="):
        sys.exit(0)
sys.exit(1)
PY
}

rig_has_section_key() {
  local file="$1" section="$2" key="$3"
  [[ -f "$file" ]] || return 1
  python3 - "$file" "$section" "$key" <<'PY'
import sys
from pathlib import Path
text = Path(sys.argv[1]).read_text(encoding="utf-8")
section, key = sys.argv[2], sys.argv[3]
current = None
for line in text.splitlines():
    s = line.strip()
    if s.startswith("[") and s.endswith("]") and not s.startswith("[["):
        current = s[1:-1]
        continue
    if current != section:
        continue
    if s.startswith("#") or not s:
        continue
    body = s.split("#", 1)[0].strip()
    if body.startswith(key + "=") or body.startswith(key + " ="):
        sys.exit(0)
sys.exit(1)
PY
}

# Let Codex-spawned Grok/Claude children write sessions and use the network.
# Only inserts missing keys. Does not shrink an existing writable_roots list.
rig_ensure_codex_child_sandbox() {
  local file="$1"
  [[ -f "$file" ]] || return 0
  python3 - "$file" "$HOME/.grok" "$HOME/.claude" "$HOME/.cursor" \
    "$HOME/.opencode" "$HOME/.config/opencode" "$HOME/.omp" "$HOME/.pi" \
    "$HOME/.gemini" <<'PY'
import sys
from pathlib import Path

path = Path(sys.argv[1])
needed = sys.argv[2:]
text = path.read_text(encoding="utf-8")
changed = []

if "network_access" not in text:
    if not text.endswith("\n"):
        text += "\n"
    if "[sandbox_workspace_write]" not in text:
        text += "\n[sandbox_workspace_write]\n"
    # append after the section header
    lines = text.splitlines()
    out = []
    inserted = False
    for i, line in enumerate(lines):
        out.append(line)
        if line.strip() == "[sandbox_workspace_write]" and not inserted:
            out.append("network_access = true")
            inserted = True
    if not inserted:
        out.append("")
        out.append("[sandbox_workspace_write]")
        out.append("network_access = true")
    text = "\n".join(out) + "\n"
    changed.append("network_access=true")

# writable_roots
missing = [p for p in needed if p not in text]
if missing:
    lines = text.splitlines()
    out = []
    i = 0
    done = False
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if stripped.startswith("writable_roots") and "=" in stripped and not done:
            # extend existing array: writable_roots = ["a"]
            rest = stripped.split("=", 1)[1].strip()
            extras = ", ".join(f'"{p}"' for p in missing)
            if rest.startswith("["):
                if rest.rstrip().endswith("]"):
                    inner = rest[1:-1].strip()
                    if inner:
                        line = f"writable_roots = [{inner}, {extras}]"
                    else:
                        line = f"writable_roots = [{extras}]"
                    out.append(line)
                    done = True
                    i += 1
                    continue
        out.append(line)
        if stripped == "[sandbox_workspace_write]" and not done:
            # peek: if next lines don't define writable_roots, add after header/network
            j = i + 1
            has_roots = False
            while j < len(lines):
                s = lines[j].strip()
                if s.startswith("[") and s.endswith("]"):
                    break
                if s.startswith("writable_roots"):
                    has_roots = True
                    break
                j += 1
            if not has_roots:
                extras = ", ".join(f'"{p}"' for p in missing)
                out.append(f"writable_roots = [{extras}]")
                done = True
        i += 1
    if not done:
        extras = ", ".join(f'"{p}"' for p in missing)
        if "[sandbox_workspace_write]" not in "\n".join(out):
            out.append("")
            out.append("[sandbox_workspace_write]")
        out.append(f"writable_roots = [{extras}]")
    text = "\n".join(out)
    if not text.endswith("\n"):
        text += "\n"
    changed.append("writable_roots")

if changed:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)
    print(",".join(changed))
else:
    print("keep")
PY
}

# Replace a marked block and keep it at the top of the file.
# Args: FILE START_MARKER END_MARKER BLOCK_TEXT
# Prints: updated | moved | prepended | wrote
rig_upsert_marked_block() {
  local file="$1" start="$2" end="$3" block="$4"
  local tmp
  if ! command -v python3 >/dev/null 2>&1; then
    echo "rig: python3 is required to edit $file" >&2
    return 1
  fi
  # Do not pass the block through env/argv: LANG=C Linux decodes those as
  # ASCII and Path.write_text then fails on AGENTS.md arrows/dashes.
  tmp="$(mktemp "${TMPDIR:-/tmp}/rig-upsert.XXXXXX")"
  printf '%s\n' "$block" > "$tmp" || { rm -f "$tmp"; return 1; }
  python3 - "$file" "$start" "$end" "$tmp" <<'PY'
import sys
from pathlib import Path

path = Path(sys.argv[1])
start = sys.argv[2]
end = sys.argv[3]
block_path = Path(sys.argv[4])
try:
    block = block_path.read_text(encoding="utf-8").strip() + "\n"
finally:
    try:
        block_path.unlink()
    except OSError:
        pass
text = path.read_text(encoding="utf-8") if path.exists() else ""
path.parent.mkdir(parents=True, exist_ok=True)
if start in text and end in text:
    i = text.find(start)
    j = text.find(end, i)
    if j == -1:
        raise SystemExit("markers out of order")
    j += len(end)
    rest = (text[:i] + text[j:]).strip("\n")
    new = block if not rest else block.rstrip() + "\n\n" + rest + "\n"
    path.write_text(new if new.endswith("\n") else new + "\n", encoding="utf-8")
    print("moved" if i > 0 else "updated")
elif text:
    rest = text.strip("\n")
    new = block.rstrip() + "\n\n" + rest + "\n"
    path.write_text(new, encoding="utf-8")
    print("prepended")
else:
    path.write_text(block if block.endswith("\n") else block + "\n", encoding="utf-8")
    print("wrote")
PY
}

WORKER_PREAMBLE='You are a worker, not the orchestrator. Do not spawn codex, grok, claude, cursor, opencode, omp, pi, or agy. Do not use computer-use, chrome-profile, or Figma MCP. Follow skill file paths listed in the brief. Write code, fix, review, SSH/debug, or gather facts. Do only the files and changes in the brief. Do not hunt extra updates. Print a short summary. Stop.'
