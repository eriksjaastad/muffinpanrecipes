# .agentsync Directory

This directory contains the source of truth for agent configurations.

## Structure

Edit files in `rules/` - they will be synced to CLAUDE.md, .cursorrules, and .agent/rules/agents.md.

## Manual Sync

```bash
uv run $TOOLS_ROOT/agentsync/sync_rules.py muffinpanrecipes
```
