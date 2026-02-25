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
