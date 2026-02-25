---
targets: ["*"]
---

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
