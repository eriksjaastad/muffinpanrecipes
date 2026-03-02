---
description: How to execute any code-writing task in this project
---

# Code Task Workflow

This workflow applies to **every task that involves writing or modifying code**.
It is mandatory and automatic — do not skip steps.

## Rules

- **Use Fast Mode** for execution. You (Flash) generate code directly. Do NOT use Planning Mode for tasks where acceptance criteria are already defined.
- **Use `browser_subagent` for VERIFICATION, not generation.** The browser_subagent is a free QA inspector — it reads files, checks UI, and confirms acceptance criteria. It does NOT write code.
- **You handle ALL file operations.** Writing code, moving files, copying files, renaming files, creating directories. The `browser_subagent` is sandboxed and CANNOT write to project files.
- **Always review the output** before moving a task to Review. You are the QA gate.
- **Use Skills for complex patterns.** Check `.agent/skills/` for instruction sets that make you smarter on specific tasks (e.g., file migration, image consolidation) without needing a sub-agent.

---

## Steps

1. **Read the task card**
   ```
   $PROJECTS_ROOT/project-tracker/pt tasks show <id>
   ```
   Understand the full context, acceptance criteria, and review notes.

2. **Read the relevant source files**
   Use `view_file`, `grep_search`, or `run_command` (read-only) to gather enough context. Do not skip this — context prevents mistakes.

3. **Generate the code yourself (Fast Mode)**
   - You ARE the code generator. Read the acceptance criteria, read the existing code, and write the changes.
   - Apply changes with `replace_file_content` / `multi_replace_file_content`.
   - For complex or unfamiliar patterns, check `.agent/skills/` for a relevant skill first.

## Bulk Operations (e.g., Image Migration)
- If a task involves moving more than 3 files:
  1. Use `ls` to list all source files.
  2. Map out the destination paths in a comment.
  3. Execute using `run_command` with standard terminal `mv` or `cp` commands.
  4. Do NOT use `replace_file_content` for binary image moves; use the terminal.

4. **Verify with `browser_subagent`**
   - After applying changes, use `browser_subagent` to verify:
     - Open modified files via `file://` URLs and confirm the code looks correct
     - If the task affects UI, open the local dev server and visually confirm
     - Check each acceptance criteria item from the task card
   - If the sub-agent finds issues, fix them yourself and re-verify.

5. **Move to Review** (only after passing verification)
   ```
   $PROJECTS_ROOT/project-tracker/pt tasks review <id>
   ```

---

## Role Split

| Role | Responsibility |
|---|---|
| **You (Antigravity, Flash)** | Read context, generate code, apply all file operations, QA gate, pt CLI |
| **browser_subagent** | Verify output — read files, check UI, confirm acceptance criteria met |
| **Skills (`.agent/skills/`)** | Instruction sets that enhance your ability on specific task patterns |

**You generate code and write files. Sub-agent verifies. Sub-agent never writes files or moves cards.**

---

## When to use Planning Mode vs Fast Mode

| Situation | Mode |
|---|---|
| Task has clear acceptance criteria | **Fast Mode** (you execute) |
| Architecture decision needed, unclear scope | **Planning Mode** (reason first) |
| File migrations, bulk operations | **Fast Mode** + relevant Skill |
| Debugging a tricky issue | **Planning Mode** to diagnose, then **Fast Mode** to fix |