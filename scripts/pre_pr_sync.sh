#!/usr/bin/env bash
set -euo pipefail

# Pre-PR sync guard:
# - Ensure working tree is clean
# - Fetch latest main
# - Rebase current branch onto origin/main
# - Show divergence summary

current_branch="$(git rev-parse --abbrev-ref HEAD)"

if [[ "$current_branch" == "main" ]]; then
  echo "❌ Refusing to run on main. Switch to a feature branch first."
  exit 1
fi

if ! git diff --quiet || ! git diff --cached --quiet; then
  echo "❌ Working tree is not clean. Commit or stash before syncing."
  exit 1
fi

echo "🔄 Fetching latest origin..."
git fetch origin --prune

echo "🔁 Rebasing ${current_branch} onto origin/main..."
git rebase origin/main

echo "📊 Divergence summary (HEAD...origin/main):"
git log --oneline --decorate --left-right --cherry-pick --boundary HEAD...origin/main | head -n 25 || true

echo "✅ Pre-PR sync complete."
