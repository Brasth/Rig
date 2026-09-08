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

find_bin() {
  command -v "$1" 2>/dev/null || true
}

repo_root() {
  local d
  d="$(pwd)"
  while [[ "$d" != "/" ]]; do
    if [[ -d "$d/.rig" || -d "$d/.git" ]]; then
      printf '%s\n' "$d"
      return 0
    fi
    d="$(dirname "$d")"
  done
  pwd
}

harness_path() {
  printf '%s\n' "$(repo_root)/.rig/harness.toml"
}

# Sets HARNESS_PARENT, HARNESS_PROFILE, HARNESS_WORKER_{CODEX,GROK,CLAUDE}.
parse_harness() {
  local file="${1:-$(harness_path)}"
  HARNESS_PARENT="codex"
  HARNESS_PROFILE="sol"
  HARNESS_WORKER_CODEX="false"
  HARNESS_WORKER_GROK="true"
  HARNESS_WORKER_CLAUDE="true"
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
        parent)
          if [[ "$key" == "profile" ]]; then
            HARNESS_PROFILE="$val"
          fi
          ;;
        workers)
          case "$key" in
            codex) HARNESS_WORKER_CODEX="$val" ;;
            grok) HARNESS_WORKER_GROK="$val" ;;
            claude) HARNESS_WORKER_CLAUDE="$val" ;;
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

parent_profile() {
  parse_harness "$(harness_path)"
  printf '%s\n' "$HARNESS_PROFILE"
}

worker_flag() {
  local name="$1"
  parse_harness "$(harness_path)"
  case "$name" in
    codex) printf '%s\n' "$HARNESS_WORKER_CODEX" ;;
    grok) printf '%s\n' "$HARNESS_WORKER_GROK" ;;
    claude) printf '%s\n' "$HARNESS_WORKER_CLAUDE" ;;
    *) printf '%s\n' "false" ;;
  esac
}

_ps_comm() {
  ps -o comm= -p "$1" 2>/dev/null | tr -d ' '
}

_ps_ppid() {
  ps -o ppid= -p "$1" 2>/dev/null | tr -d ' '
}

live_parent() {
  local forced="${RIG_PARENT:-}"
  case "$forced" in
    grok|codex|claude)
      printf '%s\n' "$forced"
      return 0
      ;;
  esac

  if [[ -n "${CLAUDECODE:-}" || "${CLAUDE_CODE:-}" == "1" ]]; then
    printf '%s\n' "claude"
    return 0
  fi

  local pid="${1:-$PPID}"
  local i=0 comm
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
  bin="$(find_bin "$name")"
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
lines = path.read_text().splitlines() if path.exists() else []


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
tmp.write_text(text)
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
text = Path(sys.argv[1]).read_text()
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
text = Path(sys.argv[1]).read_text()
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
  python3 - "$file" "$HOME/.grok" "$HOME/.claude" <<'PY'
import sys
from pathlib import Path

path = Path(sys.argv[1])
needed = [sys.argv[2], sys.argv[3]]
text = path.read_text()
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
    tmp.write_text(text)
    tmp.replace(path)
    print(",".join(changed))
else:
    print("keep")
PY
}

# Replace the interior of a marked block, or append the block if missing.
# Args: FILE START_MARKER END_MARKER BLOCK_TEXT
# Prints: updated | appended | wrote
rig_upsert_marked_block() {
  local file="$1" start="$2" end="$3" block="$4"
  if ! command -v python3 >/dev/null 2>&1; then
    echo "rig: python3 is required to edit $file" >&2
    return 1
  fi
  RIG_UPSERT_FILE="$file" \
  RIG_UPSERT_START="$start" \
  RIG_UPSERT_END="$end" \
  RIG_UPSERT_BLOCK="$block" \
  python3 - <<'PY'
import os
from pathlib import Path

path = Path(os.environ["RIG_UPSERT_FILE"])
start = os.environ["RIG_UPSERT_START"]
end = os.environ["RIG_UPSERT_END"]
block = os.environ["RIG_UPSERT_BLOCK"].strip() + "\n"
text = path.read_text() if path.exists() else ""
if start in text and end in text:
    i = text.find(start)
    j = text.find(end, i)
    if j == -1:
        raise SystemExit("markers out of order")
    j += len(end)
    new = text[:i] + block + text[j:]
    path.write_text(new if new.endswith("\n") else new + "\n")
    print("updated")
elif text:
    extra = "" if text.endswith("\n") else "\n"
    path.write_text(text + extra + "\n" + block)
    print("appended")
else:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(block)
    print("wrote")
PY
}

WORKER_PREAMBLE='You are a worker, not the orchestrator. Do not spawn codex, grok, or claude. Do not drive the user desktop or chrome profile unless the brief says so. Write code, fix, review, SSH/debug, or gather facts. Print a short summary. Stop.'
