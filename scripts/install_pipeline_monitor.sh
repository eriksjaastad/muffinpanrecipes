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
# follow-up query answers "no such service" (exit 113); otherwise this exits 1 and
# leaves the plist in place.
_unload_agent() {
  local action="$1"
  local target="gui/$(id -u)/${LABEL}"
  if launchctl bootout "$target" 2>/dev/null; then
    return 0
  fi
  # bootout failed. Only launchctl's own "no such service" answer (exit 113,
  # "Could not find service") means there was nothing to unload; any other
  # result, including a query that itself fails, is not proof.
  local rc=0
  launchctl print "$target" >/dev/null 2>&1 || rc=$?
  if [[ "$rc" -ne 113 ]]; then
    echo "${action} FAILED: launchctl bootout could not unload ${LABEL} and it is not confirmed unloaded (launchctl print exit ${rc}); $DEST left in place" >&2
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

# launchd runs with a minimal PATH, so the plist needs absolute paths to
# executables that exist. A missing one would install a job that can never
# run while this script reported success, so a real install refuses; a
# --dry-run still renders (with a bare name) and says so.
MISSING_BINS=()
for pair in "doppler:$DOPPLER_BIN" "uv:$UV_BIN"; do
  name="${pair%%:*}"
  path="${pair#*:}"
  if [[ "$path" != /* || ! -x "$path" ]]; then
    MISSING_BINS+=("$name")
  fi
done
if [[ "${#MISSING_BINS[@]}" -gt 0 ]]; then
  if [[ "$DRY_RUN" -eq 1 ]]; then
    echo "warning: not found as an absolute executable on PATH: ${MISSING_BINS[*]} (a real install would refuse)" >&2
    [[ -n "$DOPPLER_BIN" ]] || DOPPLER_BIN="doppler"
    [[ -n "$UV_BIN" ]] || UV_BIN="uv"
  else
    echo "Install FAILED: not found as an absolute executable on PATH: ${MISSING_BINS[*]}" >&2
    exit 1
  fi
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

# Prove the job's own command can run before installing anything: the
# plist runs `doppler run --project muffinpanrecipes --config prd -- uv run
# ...`, and executable paths alone do not show that Doppler is authenticated
# with access to prd. Runs `uv --version` under that same wrapper; no
# secret is printed.
if ! "$DOPPLER_BIN" run --project muffinpanrecipes --config prd -- "$UV_BIN" --version >/dev/null 2>&1; then
  echo "Install FAILED: '$DOPPLER_BIN run --project muffinpanrecipes --config prd -- $UV_BIN --version' failed; the hourly job could not start (check 'doppler login' and access to the prd config). Nothing was changed." >&2
  exit 1
fi

# Unload any existing copy BEFORE touching its plist, so a failed unload
# leaves the working install exactly as it was (bootstrap alone refuses if
# the label is already loaded, so a reinstall must unload first).
_unload_agent "Install"

mkdir -p "$HOME/Library/LaunchAgents"
mkdir -p "$LOG_DIR"
printf '%s\n' "$RENDERED" > "$DEST"

launchctl bootstrap "gui/$(id -u)" "$DEST"
launchctl enable "gui/$(id -u)/${LABEL}"

echo "Installed and loaded: $DEST"
echo "Logs: $LOG_DIR"
