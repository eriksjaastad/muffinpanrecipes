#!/usr/bin/env bash
# Cron command presets for compressed testing + production stage cadence.
# Use: bash scripts/cron_stage_schedules.sh

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="$ROOT/.venv/bin/python"

cat <<'EOF'
# === Saturday/Sunday compressed test (every 30 min sequence) ===
# 00 * * * 6,0 cd /Users/eriksjaastad/projects/muffinpanrecipes && PYTHONPATH=. .venv/bin/python scripts/run_pipeline_stage.py --stage monday --episode 2026-W09 --concept "Compressed Week" --notify
# 30 * * * 6,0 cd /Users/eriksjaastad/projects/muffinpanrecipes && PYTHONPATH=. .venv/bin/python scripts/run_pipeline_stage.py --stage tuesday --episode 2026-W09 --concept "Compressed Week" --notify
# 00 */1 * * 6,0 cd /Users/eriksjaastad/projects/muffinpanrecipes && PYTHONPATH=. .venv/bin/python scripts/run_pipeline_stage.py --stage wednesday --episode 2026-W09 --concept "Compressed Week" --notify
# 30 */1 * * 6,0 cd /Users/eriksjaastad/projects/muffinpanrecipes && PYTHONPATH=. .venv/bin/python scripts/run_pipeline_stage.py --stage thursday --episode 2026-W09 --concept "Compressed Week" --notify
# 00 */2 * * 6,0 cd /Users/eriksjaastad/projects/muffinpanrecipes && PYTHONPATH=. .venv/bin/python scripts/run_pipeline_stage.py --stage friday --episode 2026-W09 --concept "Compressed Week" --notify
# 30 */2 * * 6,0 cd /Users/eriksjaastad/projects/muffinpanrecipes && PYTHONPATH=. .venv/bin/python scripts/run_pipeline_stage.py --stage saturday --episode 2026-W09 --concept "Compressed Week" --notify
# 00 */3 * * 6,0 cd /Users/eriksjaastad/projects/muffinpanrecipes && PYTHONPATH=. .venv/bin/python scripts/run_pipeline_stage.py --stage sunday --episode 2026-W09 --concept "Compressed Week" --notify

# === Monday production cadence ===
# 30 7 * * 1 cd /Users/eriksjaastad/projects/muffinpanrecipes && PYTHONPATH=. .venv/bin/python scripts/run_pipeline_stage.py --stage monday --episode $(date +\%G-W\%V) --concept "Weekly Recipe" --notify
# 30 7 * * 2 cd /Users/eriksjaastad/projects/muffinpanrecipes && PYTHONPATH=. .venv/bin/python scripts/run_pipeline_stage.py --stage tuesday --episode $(date +\%G-W\%V) --concept "Weekly Recipe" --notify
# 30 7 * * 3 cd /Users/eriksjaastad/projects/muffinpanrecipes && PYTHONPATH=. .venv/bin/python scripts/run_pipeline_stage.py --stage wednesday --episode $(date +\%G-W\%V) --concept "Weekly Recipe" --notify
# 30 7 * * 4 cd /Users/eriksjaastad/projects/muffinpanrecipes && PYTHONPATH=. .venv/bin/python scripts/run_pipeline_stage.py --stage thursday --episode $(date +\%G-W\%V) --concept "Weekly Recipe" --notify
# 30 7 * * 5 cd /Users/eriksjaastad/projects/muffinpanrecipes && PYTHONPATH=. .venv/bin/python scripts/run_pipeline_stage.py --stage friday --episode $(date +\%G-W\%V) --concept "Weekly Recipe" --notify
# 30 7 * * 6 cd /Users/eriksjaastad/projects/muffinpanrecipes && PYTHONPATH=. .venv/bin/python scripts/run_pipeline_stage.py --stage saturday --episode $(date +\%G-W\%V) --concept "Weekly Recipe" --notify
# 00 17 * * 0 cd /Users/eriksjaastad/projects/muffinpanrecipes && PYTHONPATH=. .venv/bin/python scripts/run_pipeline_stage.py --stage sunday --episode $(date +\%G-W\%V) --concept "Weekly Recipe" --notify
EOF

echo "Printed cron presets. Copy desired lines into crontab -e."