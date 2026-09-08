#!/usr/bin/env bash
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RIG_HOME="${RIG_HOME:-$HOME/.rig}"
REPO_URL="${RIG_REPO_URL:-https://github.com/Brasth/Rig.git}"
CLEANUP=""

if [[ -f "$HERE/bin/rig" && -f "$HERE/scripts/detect-binaries.sh" && -f "$HERE/skills/delegate-harness/SKILL.md" ]]; then
  SRC="$HERE"
else
  TMP="$(mktemp -d)"
  CLEANUP="$TMP"
  git clone --depth 1 "$REPO_URL" "$TMP/Rig"
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
  mkdir -p "$RIG_HOME"/{bin,scripts,skills/delegate-harness,adapters/codex/agents,adapters/grok,adapters/claude,templates}
  install_file "$SRC/bin/rig" "$RIG_HOME/bin/rig"
  install_file "$SRC/scripts/detect-binaries.sh" "$RIG_HOME/scripts/detect-binaries.sh"
  install_file "$SRC/scripts/run-worker.sh" "$RIG_HOME/scripts/run-worker.sh"
  install_file "$SRC/skills/delegate-harness/SKILL.md" "$RIG_HOME/skills/delegate-harness/SKILL.md"
  local f
  for f in "$SRC/adapters/codex/agents/"*.toml; do
    install_file "$f" "$RIG_HOME/adapters/codex/agents/$(basename "$f")"
  done
  install_file "$SRC/adapters/grok/config.toml.snippet" "$RIG_HOME/adapters/grok/config.toml.snippet"
  install_file "$SRC/adapters/claude/CLAUDE.worker.md" "$RIG_HOME/adapters/claude/CLAUDE.worker.md"
  for f in "$SRC/templates/"*; do
    install_file "$f" "$RIG_HOME/templates/$(basename "$f")"
  done
  chmod +x "$RIG_HOME/bin/rig" "$RIG_HOME/scripts/"*.sh
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
