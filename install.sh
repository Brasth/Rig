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
  if git clone --depth 1 "$REPO_URL" "$dest"; then
    return 0
  fi
  if command -v gh >/dev/null 2>&1 && gh auth status >/dev/null 2>&1; then
    gh repo clone "$REPO_SLUG" "$dest" -- --depth 1
    return 0
  fi
  echo "install: could not clone $REPO_URL (need git, or gh auth)" >&2
  exit 1
}

if [[ -n "$HERE" && -f "$HERE/bin/rig" && -f "$HERE/scripts/detect-binaries.sh" && -f "$HERE/skills/delegate-harness/SKILL.md" ]]; then
  SRC="$HERE"
else
  TMP="$(mktemp -d)"
  CLEANUP="$TMP"
  clone_source "$TMP/Rig"
  SRC="$TMP/Rig"
fi

export RIG_HOME
export RIG_SRC="$SRC"
if command -v python3 >/dev/null 2>&1; then
  python3 "$SRC/scripts/install-tmux.py"
else
  echo "tmux setup: Python 3 unavailable; install tmux 3.3+ manually to use the terminal companion."
fi
bash "$SRC/bin/rig" setup "$@"

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
