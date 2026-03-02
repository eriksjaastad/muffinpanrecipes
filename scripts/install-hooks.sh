#!/usr/bin/env bash
# install-hooks.sh
# Installs project git hooks from scripts/hooks/ into .git/hooks/
#
# Usage:
#   bash scripts/install-hooks.sh
#
# Run once after cloning, or again after updating a hook in scripts/hooks/.

set -euo pipefail

SCRIPTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPTS_DIR/.." && pwd)"
HOOKS_SRC="$SCRIPTS_DIR/hooks"
HOOKS_DST="$REPO_ROOT/.git/hooks"

if [[ ! -d "$HOOKS_SRC" ]]; then
    echo "❌ No hooks directory found at $HOOKS_SRC"
    exit 1
fi

echo "📦 Installing git hooks from scripts/hooks/ → .git/hooks/"
for hook in "$HOOKS_SRC"/*; do
    name="$(basename "$hook")"
    dest="$HOOKS_DST/$name"
    cp "$hook" "$dest"
    chmod +x "$dest"
    echo "  ✅ Installed: $name"
done

echo ""
echo "Done. Run 'git commit' to verify hooks are active."
