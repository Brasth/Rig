# Sourced only: host functions are intentionally not exported to child shells.
case $- in *i*) ;; *) return 0 ;; esac
[ -z "${_RIG_SHELL_LOADED:-}" ] || return 0
_RIG_SHELL_LOADED=1
_RIG_SHELL_HOME="${RIG_SHELL_HOME:-${RIG_HOME:-$HOME/.rig}}"
_rig_shell_launch() {
  local host="$1" real
  shift
  if [ -n "${ZSH_VERSION:-}" ]; then
    real="$(whence -p "$host")"
  else
    real="$(type -P "$host")"
  fi
  if [ -z "$real" ]; then
    printf '%s: command not found\n' "$host" >&2
    return 127
  fi
  if [ -z "${RIG_JOB_ID:-}" ] && [ -z "${RIG_UI_ACTIVE:-}" ] && [ -z "${RIG_WORKER:-}" ] && [ "${RIG_LIVE:-}" != 1 ] && [ -t 0 ] && [ -t 1 ] && [ -f "$_RIG_SHELL_HOME/ui/shell-enabled" ] && [ -f "$_RIG_SHELL_HOME/scripts/rig_ui.py" ] && command -v python3 >/dev/null 2>&1; then
    RIG_HOME="$_RIG_SHELL_HOME" command python3 "$_RIG_SHELL_HOME/scripts/rig_ui.py" exec "$host" "$real" -- "$@"
  else
    "$real" "$@"
  fi
}
if ! alias codex >/dev/null 2>&1 && ! typeset -f codex >/dev/null 2>&1; then
  eval 'codex() { _rig_shell_launch codex "$@"; }'
else
  printf '%s\n' 'Rig UI: preserving existing codex alias/function.' >&2
fi
if ! alias grok >/dev/null 2>&1 && ! typeset -f grok >/dev/null 2>&1; then
  eval 'grok() { _rig_shell_launch grok "$@"; }'
else
  printf '%s\n' 'Rig UI: preserving existing grok alias/function.' >&2
fi
