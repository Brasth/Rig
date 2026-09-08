#!/usr/bin/env bash
set -euo pipefail

# Piped `curl | bash` has an empty BASH_SOURCE. Do not treat CWD as the source.
_SRC_FILE="${BASH_SOURCE[0]:-}"
HERE=""
if [[ -n "$_SRC_FILE" && -f "$_SRC_FILE" ]]; then
  HERE="$(cd "$(dirname "$_SRC_FILE")" && pwd)"
fi

RIG_HOME="${RIG_HOME:-$HOME/.rig}"
REPO_SLUG="${RIG_REPO_SLUG:-Brasth/Rig}"
REPO_URL="${RIG_REPO_URL:-https://github.com/${REPO_SLUG}.git}"
CLEANUP=""

clone_source() {
  local dest="$1"
  if command -v gh >/dev/null 2>&1 && gh auth status >/dev/null 2>&1; then
    gh repo clone "$REPO_SLUG" "$dest" -- --depth 1
  else
    git clone --depth 1 "$REPO_URL" "$dest"
  fi
}

if [[ -n "$HERE" && -f "$HERE/bin/rig" && -f "$HERE/scripts/detect-binaries.sh" && -f "$HERE/skills/delegate-harness/SKILL.md" ]]; then
  SRC="$HERE"
else
  TMP="$(mktemp -d)"
  CLEANUP="$TMP"
  clone_source "$TMP/Rig"
  SRC="$TMP/Rig"
fi

install_file() {
  local srcf="$1" destf="$2"
  local tmp
  tmp="$(mktemp "${destf}.XXXXXX")"
  cp "$srcf" "$tmp"
  mv "$tmp" "$destf"
}

copy_into_home() {
  mkdir -p "$RIG_HOME"/{bin,scripts,skills,adapters/codex/agents,adapters/grok,adapters/claude,adapters/cursor,templates}
  install_file "$SRC/bin/rig" "$RIG_HOME/bin/rig"
  local f skill
  for f in "$SRC/scripts/"*; do
    [[ -f "$f" ]] || continue
    install_file "$f" "$RIG_HOME/scripts/$(basename "$f")"
  done
  for skill in "$SRC/skills/"*; do
    [[ -d "$skill" ]] || continue
    mkdir -p "$RIG_HOME/skills/$(basename "$skill")"
    for f in "$skill/"*; do
      [[ -f "$f" ]] || continue
      install_file "$f" "$RIG_HOME/skills/$(basename "$skill")/$(basename "$f")"
    done
  done
  for f in "$SRC/adapters/codex/agents/"*.toml; do
    install_file "$f" "$RIG_HOME/adapters/codex/agents/$(basename "$f")"
  done
  install_file "$SRC/adapters/grok/config.toml.snippet" "$RIG_HOME/adapters/grok/config.toml.snippet"
  install_file "$SRC/adapters/claude/CLAUDE.worker.md" "$RIG_HOME/adapters/claude/CLAUDE.worker.md"
  if [[ -f "$SRC/adapters/cursor/CURSOR.worker.md" ]]; then
    install_file "$SRC/adapters/cursor/CURSOR.worker.md" "$RIG_HOME/adapters/cursor/CURSOR.worker.md"
  fi
  for f in "$SRC/templates/"*; do
    [[ -f "$f" ]] || continue
    install_file "$f" "$RIG_HOME/templates/$(basename "$f")"
  done
  chmod +x "$RIG_HOME/bin/rig" "$RIG_HOME/scripts/"*.sh "$RIG_HOME/scripts/"*.py 2>/dev/null || true
}

copy_into_home

mkdir -p "$HOME/.local/bin"
ln -sfn "$RIG_HOME/bin/rig" "$HOME/.local/bin/rig"

export RIG_HOME
export RIG_SRC="$SRC"
"$RIG_HOME/bin/rig" setup

if [[ -n "$CLEANUP" ]]; then
  rm -rf "$CLEANUP"
fi

case ":$PATH:" in
  *":$HOME/.local/bin:"*) ;;
  *)
    echo "add to PATH: export PATH=\"\$HOME/.local/bin:\$PATH\""
    ;;
esac

echo "install ok  rig -> $HOME/.local/bin/rig"
echo "next: cd your-repo && rig init && rig doctor"
