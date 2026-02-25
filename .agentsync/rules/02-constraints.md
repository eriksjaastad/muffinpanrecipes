---
targets: ["*"]
---

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
