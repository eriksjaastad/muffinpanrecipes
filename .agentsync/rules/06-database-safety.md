---
targets: ["*"]
---

# Database Safety Rules

## CRITICAL: Databases Are Stateful - Treat With Extreme Care

Databases contain accumulated work that cannot be recreated. One careless command can destroy hours or days of data.

## Forbidden Operations

**NEVER execute these without explicit user approval:**

1. `DROP TABLE` - Destroys table and all data
2. `DELETE FROM table` (without WHERE) - Deletes all rows
3. `TRUNCATE TABLE` - Empties entire table
4. `rm *.db` or `rm *.sqlite` - Deletes database file
5. Recreating tables that contain data
6. Any "reset", "init", or "recreate" that would wipe existing data

## Required Practices

### Before Any Schema Change
```bash
# 1. Check if table has data
sqlite3 database.db "SELECT COUNT(*) FROM table_name;"

# 2. If data exists, STOP and ask user
# 3. Never auto-migrate tables with data
```

### For Migrations
- Use `ALTER TABLE ADD COLUMN` (additive only)
- Never drop columns or tables with data
- If schema is incompatible, REFUSE and explain - don't auto-fix

### For Deletions
- Always use the application's API (e.g., `DatabaseManager.delete_task()`)
- Never run raw SQL DELETE outside the application
- The API creates backups automatically

## If You Need to Reset a Database

**DO NOT** just drop tables or delete the file.

**DO:**
1. Ask the user explicitly: "This will delete X rows. Proceed?"
2. Create a backup first: `cp database.db database.db.backup`
3. Export data: `./pt tasks export` (if available)
4. Only then proceed with the reset

## Why This Exists

On 2026-01-27, an AI agent ran a migration that dropped the tasks table without backup, destroying 94 tasks. This rule exists to prevent that from ever happening again.

## Quick Reference

| Want to do... | Do this instead |
|---------------|-----------------|
| Add a column | `ALTER TABLE x ADD COLUMN y` |
| Delete one row | Use app's delete method (has backup) |
| Delete many rows | Ask user first, backup, then delete |
| Change column type | Create new column, migrate data, drop old |
| Reset database | Ask user, backup, export, then reset |
| Fix schema issues | REFUSE and print manual instructions |
