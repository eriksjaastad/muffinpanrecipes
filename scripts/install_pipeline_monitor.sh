#!/usr/bin/env bash
# Render and install the muffinpanrecipes pipeline monitor launchd agent (#7006).
#
# Renders ops/launchd/com.eriksjaastad.muffinpan-pipeline-monitor.plist.template
# for the current user (no hard-coded paths live in the template itself — see
# ops/launchd/README.md) into ~/Library/LaunchAgents/, then bootstraps it with
# launchctl so the monitor runs hourly, always, whether or not a Claude
# session ever opens in this repo.
#
# Usage:
#   scripts/install_pipeline_monitor.sh              # render + install
#   scripts/install_pipeline_monitor.sh --dry-run     # print, do nothing
#   scripts/install_pipeline_monitor.sh --uninstall   # unload + trash the plist
#   scripts/install_pipeline_monitor.sh --uninstall --dry-run
#
# This script only ever prints or acts on the CURRENT user's own
# ~/Library/LaunchAgents — it never touches another user's files and never
# uses `rm` (uninstall moves the plist to the Trash via `trash`).

set -euo pipefail

LABEL="com.eriksjaastad.muffinpan-pipeline-monitor"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
TEMPLATE="$REPO_DIR/ops/launchd/${LABEL}.plist.template"
DEST="$HOME/Library/LaunchAgents/${LABEL}.plist"
LOG_DIR="$HOME/Library/Logs/muffinpan-pipeline-monitor"

DRY_RUN=0
UNINSTALL=0

for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
    --uninstall) UNINSTALL=1 ;;
    -h|--help)
      sed -n '1,20p' "${BASH_SOURCE[0]}"
      exit 0
      ;;
    *)
      echo "unknown argument: $arg" >&2
      exit 1
      ;;
  esac
done

_resolve_bin() {
  # Prefer an already-resolved absolute path; fall back to `command -v` so
  # this works whether or not the caller's shell has the usual aliases/functions
  # loaded (this script runs under launchd's minimal environment too).
  local name="$1"
  command -v "$name" 2>/dev/null || true
}

DOPPLER_BIN="$(_resolve_bin doppler)"
UV_BIN="$(_resolve_bin uv)"

# Unload the agent if it is loaded. Always attempts the unload (by service
# target, so it works without the plist) rather than trusting a
# `launchctl print` query first: a failed query is not proof the job is
# unloaded. A failed unload counts as "nothing was loaded" only when a
# follow-up query also finds no such service; otherwise this exits 1 and
# leaves the plist in place.
_unload_agent() {
  local action="$1"
  local target="gui/$(id -u)/${LABEL}"
  if launchctl bootout "$target" 2>/dev/null; then
    return 0
  fi
  if launchctl print "$target" >/dev/null 2>&1; then
    echo "${action} FAILED: launchctl bootout could not unload ${LABEL}, which is still loaded; $DEST left in place" >&2
    exit 1
  fi
}

if [[ "$UNINSTALL" -eq 1 ]]; then
  if [[ "$DRY_RUN" -eq 1 ]]; then
    echo "# --uninstall --dry-run: would run:"
    echo "launchctl bootout gui/$(id -u)/${LABEL}"
    echo "trash '$DEST'"
    exit 0
  fi

  _unload_agent "Uninstall"
  if [[ -f "$DEST" ]]; then
    trash "$DEST"
    echo "Uninstalled: unloaded and trashed $DEST"
  else
    echo "Nothing to uninstall: $DEST does not exist"
  fi
  exit 0
fi

if [[ ! -f "$TEMPLATE" ]]; then
  echo "template not found: $TEMPLATE" >&2
  exit 1
fi

if [[ -z "$DOPPLER_BIN" ]]; then
  echo "warning: 'doppler' not found on PATH — rendering with a bare command name;" >&2
  echo "         fix PATH or install Doppler before relying on this job to alert." >&2
  DOPPLER_BIN="doppler"
fi
if [[ -z "$UV_BIN" ]]; then
  echo "warning: 'uv' not found on PATH — rendering with a bare command name;" >&2
  echo "         fix PATH before relying on this job to run at all." >&2
  UV_BIN="uv"
fi

RENDERED="$(
  sed \
    -e "s#{{REPO}}#$REPO_DIR#g" \
    -e "s#{{HOME}}#$HOME#g" \
    -e "s#{{UV}}#$UV_BIN#g" \
    -e "s#{{DOPPLER}}#$DOPPLER_BIN#g" \
    "$TEMPLATE"
)"

if [[ "$DRY_RUN" -eq 1 ]]; then
  echo "# Rendered plist (would be written to $DEST):"
  echo "$RENDERED"
  echo
  echo "# Would then run:"
  echo "mkdir -p '$LOG_DIR'"
  echo "launchctl bootstrap gui/$(id -u) '$DEST'"
  echo "launchctl enable gui/$(id -u)/${LABEL}"
  exit 0
fi

mkdir -p "$HOME/Library/LaunchAgents"
mkdir -p "$LOG_DIR"
printf '%s\n' "$RENDERED" > "$DEST"

# Bootout any existing copy first so re-running this script after an edit
# actually picks up the new plist instead of launchd keeping the old one
# loaded (bootstrap alone refuses if the label is already loaded).
_unload_agent "Install"

launchctl bootstrap "gui/$(id -u)" "$DEST"
launchctl enable "gui/$(id -u)/${LABEL}"

echo "Installed and loaded: $DEST"
echo "Logs: $LOG_DIR"
