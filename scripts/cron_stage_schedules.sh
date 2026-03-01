#!/usr/bin/env bash
# ============================================================
# Muffin Pan Recipes — Cron Job Manager
#
# Usage:
#   bash scripts/cron_stage_schedules.sh install-test
#       Install a compressed 7-stage test run (8 min between stages,
#       all stages complete within ~60 min). Picks concept automatically.
#
#   bash scripts/cron_stage_schedules.sh install-production
#       Install the real Mon–Sun weekly cadence (one stage per day at 7:30am).
#
#   bash scripts/cron_stage_schedules.sh remove
#       Remove all muffinpanrecipes cron entries.
#
#   bash scripts/cron_stage_schedules.sh status
#       Show currently installed muffinpanrecipes cron entries.
#
#   bash scripts/cron_stage_schedules.sh dry-run
#       Print what would be installed without touching crontab.
# ============================================================

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && cd .. && pwd)"
PY="$ROOT/.venv/bin/python"
LOG_DIR="$ROOT/logs"
MARKER="# muffinpanrecipes-cron"

mkdir -p "$LOG_DIR"

# ---------------------------------------------------------------------------
# Helper: emit one cron line
# ---------------------------------------------------------------------------
# pick_concept() is called at run time by run_pipeline_stage.py monday stage
# so we don't embed the concept in the cron line — it auto-discovers it.
# Episode ID uses ISO week: YYYY-WNN (e.g. 2026-W09)
# ---------------------------------------------------------------------------

_stage_cmd() {
    local stage="$1"
    local extra="${2:-}"
    # doppler run -- injects STABILITY_API_KEY and all project secrets into the subprocess
    echo "cd '$ROOT' && PYTHONPATH=. doppler run -- '$PY' scripts/run_pipeline_stage.py --stage $stage --episode \$(date +%G-W%V) $extra >> '$LOG_DIR/cron_${stage}.log' 2>&1"
}

_compressed_cmd() {
    # Single command runs all 7 stages with --delay N seconds
    # doppler run -- injects STABILITY_API_KEY and all project secrets
    # pick_concept.py selects a fresh concept each run for variety
    local delay="${1:-300}"  # default: 5 min between stages
    echo "cd '$ROOT' && CONCEPT=\$(doppler run -- '$PY' scripts/pick_concept.py 2>/dev/null || echo 'Weekly Muffin Pan Recipe') && PYTHONPATH=. doppler run -- '$PY' scripts/run_compressed_week.py --episode-id \$(date +%G-W%V-test) --concept \"\$CONCEPT\" --delay $delay >> '$LOG_DIR/cron_compressed.log' 2>&1"
}

# ---------------------------------------------------------------------------
# Install: compressed test (all 7 stages within ~60 min)
# Fires once at the next :00 minute after install, then each stage is
# staggered by 8 minutes via a wrapper loop in run_compressed_week.py.
# We schedule ONE cron job that launches the whole compressed run.
# ---------------------------------------------------------------------------
install_test() {
    local now_min
    now_min=$(date +%M)
    # Round up to next :00 or :30 boundary
    if [ "$now_min" -lt 30 ]; then
        local fire_min=0
        local fire_hour
        fire_hour=$(date -v+1H +%H)
    else
        local fire_min=30
        local fire_hour
        fire_hour=$(date -v+1H +%H)
    fi

    # Use 8-minute delay between stages → 7 stages ≈ 56 min total
    local cmd
    cmd="$(_compressed_cmd 480)"  # 480 seconds = 8 min

    local cron_line="$fire_min $fire_hour * * * $cmd $MARKER-test"
    _upsert_cron "$cron_line" "test"
    echo "✅ Compressed test run scheduled for $fire_hour:$(printf '%02d' $fire_min) today"
    echo "   Stages spaced 8 minutes apart → all 7 complete in ~60 min"
    echo "   Log: $LOG_DIR/cron_compressed.log"
}

# ---------------------------------------------------------------------------
# Install: production weekly cadence (Mon–Sun, one stage per day at 7:30am)
# Concept is auto-picked on Monday by run_pipeline_stage.py (calls pick_concept.py
# when --concept is not provided — TODO: wire that up in run_pipeline_stage.py).
# For now we embed --concept from pick_concept.py at install time.
# ---------------------------------------------------------------------------
install_production() {
    echo "Picking concept for this week..."
    local concept
    concept=$(PYTHONPATH=. "$PY" scripts/pick_concept.py 2>/dev/null || echo "Mini Lemon Ricotta Cheesecakes")
    local episode
    episode=$(date +%G-W%V)
    echo "  Concept: $concept"
    echo "  Episode: $episode"

    local lines=(
        "30 7 * * 1 $(_stage_cmd monday "--concept '$concept'") $MARKER-prod"
        "30 7 * * 2 $(_stage_cmd tuesday "--concept '$concept'") $MARKER-prod"
        "30 7 * * 3 $(_stage_cmd wednesday "--concept '$concept'") $MARKER-prod"
        "30 7 * * 4 $(_stage_cmd thursday "--concept '$concept'") $MARKER-prod"
        "30 7 * * 5 $(_stage_cmd friday "--concept '$concept'") $MARKER-prod"
        "30 7 * * 6 $(_stage_cmd saturday "--concept '$concept'") $MARKER-prod"
        "0 17 * * 0 $(_stage_cmd sunday "--concept '$concept'") $MARKER-prod"
    )

    # Remove existing prod entries then add fresh
    _remove_marker "prod"
    (crontab -l 2>/dev/null; printf '%s\n' "${lines[@]}") | crontab -
    echo "✅ Production cron installed for week $episode — concept: $concept"
    echo "   Mon–Sat: 7:30am  |  Sun: 5:00pm"
}

# ---------------------------------------------------------------------------
# Upsert / remove helpers
# ---------------------------------------------------------------------------
_upsert_cron() {
    local line="$1"
    local tag="$2"
    _remove_marker "$tag"
    (crontab -l 2>/dev/null; echo "$line") | crontab -
}

_remove_marker() {
    local tag="${1:-}"
    if [ -n "$tag" ]; then
        (crontab -l 2>/dev/null | grep -v "$MARKER-$tag") | crontab - 2>/dev/null || true
    else
        (crontab -l 2>/dev/null | grep -v "$MARKER") | crontab - 2>/dev/null || true
    fi
}

# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------
case "${1:-help}" in
    install-test)
        install_test
        ;;
    install-production)
        install_production
        ;;
    remove)
        _remove_marker ""
        echo "✅ All muffinpanrecipes cron entries removed"
        ;;
    status)
        echo "=== muffinpanrecipes cron entries ==="
        crontab -l 2>/dev/null | grep "$MARKER" || echo "  (none installed)"
        ;;
    dry-run)
        echo "=== DRY RUN — would install: ==="
        echo ""
        echo "  [test] One compressed run (8 min between stages):"
        echo "  $(_compressed_cmd 480) $MARKER-test"
        echo ""
        echo "  [production] 7 daily stage jobs at 7:30am Mon-Sat, 5pm Sun:"
        for stage in monday tuesday wednesday thursday friday saturday sunday; do
            echo "  $(_stage_cmd "$stage" "--concept '<auto-picked>'") $MARKER-prod"
        done
        ;;
    help|*)
        grep '^#' "$0" | grep -v '^#!/' | sed 's/^# \?//'
        ;;
esac