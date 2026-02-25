---
targets: ["*"]
---

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
