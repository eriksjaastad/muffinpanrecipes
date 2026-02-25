---
trigger: always_on
---

# Antigravity Rules for muffinpanrecipes

<!-- AGENTSYNC:START - Do not edit between markers -->
<!-- To modify synced rules: Edit .agentsync/rules/*.md, then run: -->
<!-- uv run $PROJECTS_ROOT/project-scaffolding/agentsync/sync_rules.py muffinpanrecipes -->

# muffinpanrecipes

> Brief description of the project's purpose

## Tech Stack

- **Language:** Python
- **Frameworks:** None

## Commands

- **Run:** `python main.py`
- **Test:** `pytest`

# Workflow

## Agent Hierarchy

### 1. The Conductor (Erik)
- **Role:** Human-in-the-Loop / Vision / Command
- **Authority:** Final approval on all architecture, logic, and project direction

### 2. The Super Manager (Strategy & Context)
- **Role:** Strategic Planner and Prompt Engineer
- **Constraint:** STRICTLY PROHIBITED from writing code or using tools
- **Mandate:** Drafts prompts with acceptance criteria as checklists

### 3. The Floor Manager (QA & Execution)
- **Role:** Orchestrator, Quality Assurance Lead, File Operator
- **Constraint:** STRICTLY PROHIBITED from generating logic or writing code
- **Mandate:** Verify work against checklists, perform file operations

### 4. The Workers (Local Models via Ollama)
- **Role:** Primary Implementers of logic and code generation
- **Mandate:** Generate code, report completion for inspection

## Workflow Steps

1. **Drafting:** Super Manager writes task prompt with acceptance criteria
2. **Handoff:** Pass to Floor Manager
3. **Execution:** Floor Manager delegates to Worker, provides context
4. **Inspection:** Floor Manager checks each acceptance criteria item
5. **Loop/Correction:** If fail, send back to Worker (max 3 attempts)
6. **Finalization:** Task marked complete after sign-off

**CRITICAL:** Only Workers write code. Super Manager and Floor Manager never generate code.

# Universal Constraints

## Never Do

- NEVER modify `.env` or `venv/`
- NEVER install dependencies globally (use project-local venv, uv, pipx, or poetry)
- NEVER hard-code API keys, secrets, or credentials (use `.env` and `os.getenv()`)
- NEVER use absolute paths (e.g., `/Users/...`) - use relative paths or env variables
- NEVER use `rm` for file deletion - use `trash` command instead
- NEVER use `--no-verify` or `-n` with git commit/push - fix the hook issue, don't bypass it

## Always Do

- ALWAYS update `EXTERNAL_RESOURCES.yaml` when adding external services
- ALWAYS use retry logic and cost tracking for API calls
- ALWAYS use `$HOME/.local/bin/uv run` for Python script execution in hooks/automation

# Safety Rules

## File Operations

- **Trash, Don't Delete:** NEVER use `rm` or permanent deletion
- Use `trash` CLI (preferred) or `send2trash` (Python)
- Use `git restore` for reverting tracked files

## Context Protocol

If context is missing or a file is unknown:
- **STOP** and request information from the Floor Manager
- **DO NOT GUESS**

## Failure Protocol

If Worker fails **3 times** on the same task:
- Halt and alert the Conductor
- Do not continue attempting

# AI-First Development Guidelines

## CLI Design
- **Plain text output**: Avoid rich formatting (colors, bold) in default output to ensure easy parsing by AI agents.
- **Single-line parseable formats**: For lists (like tasks), use single-line formats: `#<id> | <status> | <priority> | <text>`.
- **JSON support**: Always provide a `--json` flag for structured output.
- **Batch operations**: Support multiple IDs for commands like `show`, `start`, `done` to reduce round-trips.

## File Operations
- **Read before edit**: Always read the file content before performing a search-replace or write.
- **Preserve custom content**: Use marker-based updates (e.g., `<!-- SCAFFOLD:START -->`) to preserve project-specific logic while updating governed sections.
- **DNA Integrity**: Never use hardcoded absolute paths. Use relative paths or environment variables.

## Task Workflow
- **State Management**: Use `./pt tasks start <id>` when beginning work and `./pt tasks done <id>` when finished.
- **Context Awareness**: Use `./pt tasks show <id>` to read the full task prompt, including Overview, Execution, and Done Criteria.
- **Traceability**: All major changes should be linked to a task ID in the project tracker.

## Communication
- **Direct and Concise**: Avoid fluff in assistant responses.
- **Proactive Planning**: Use `todo_write` to maintain a clear plan of action.
- **Soulful Journaling**: Log strategic decisions and detours in the AI Journal for future context.

# Commit Message Task Linking

## Rule: Include Task ID in Commits

When committing work that completes or advances a tracked task, include the task ID in the commit message.

## Format

```
type: description (#TASK_ID)
```

**Examples:**
- `feat: Add versioning to agentsync (#4597)`
- `fix: Validate_project false positives (#4598)`
- `docs: Update 00_Index template (#4599)`

## Multiple Tasks

If a commit addresses multiple tasks:
```
feat: Major cleanup and template updates (#4540, #4541)
```

## Why This Matters

- Enables automatic task-to-commit linking in project-tracker
- Creates audit trail from kanban board to git history
- Makes "what got done" visible without manual reconciliation

## When to Skip

- Pure refactoring with no associated task
- Typo fixes and trivial changes
- Use your judgment - not every commit needs a task ID

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

<!-- Source: .agentsync/rules/*.md -->
<!-- AGENTSYNC:END - Custom rules below this line are preserved -->