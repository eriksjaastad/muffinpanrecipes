---
targets: ["*"]
---

# Workflow

## Agent Hierarchy

### 1. The Conductor (Erik)
- **Role:** Human-in-the-Loop / Vision / Command
- **Context:** Projects root (`$PROJECTS_ROOT/`) — moves in and out of projects
- **Authority:** Final approval on all architecture, logic, and project direction

### 2. The Architect (Claude Code CLI)
- **Role:** Strategic Planner — always Claude Code, always at the projects root level
- **Context:** Sits with the Conductor at `$PROJECTS_ROOT/`, thinks across all projects
- **Constraint:** **STRICTLY PROHIBITED** from writing code or using tools
- **Mandate:** Drafts prompts with acceptance criteria checklists, does final Judge sign-off on completed work

### 3. The Floor Manager (Antigravity / Gemini)
- **Role:** Project Orchestrator — one per project, lives at the project level
- **Context:** Works inside a single project directory
- **Constraint:** **STRICTLY PROHIBITED** from generating logic or writing code
- **Mandate:**
  1. Read Kanban tickets and their full prompts
  2. Analyze the project and organize work (identify what can run in parallel)
  3. Delegate tasks to low-cost Workers (see below for which type)
  4. Review Worker output against acceptance criteria
  5. Move tasks to **Review** status when satisfied — The Architect does final sign-off

### 4. The Workers (Low-cost Subagents)
- **Role:** Primary implementers of logic and code generation
- **Which workers to use depends on which machine you're on:**
  ```bash
  hostname
  # "eriks-mac-mini" → local Ollama models (Qwen, DeepSeek-R1, etc.)
  # Anything else → cloud subagents (Claude Haiku, Gemini Flash, GPT-mini)
  ```
- **Mandate:** Generate code, report completion to Floor Manager for inspection

## Workflow Steps

1. **Planning:** The Conductor and Architect discuss tickets and strategy at projects root
2. **Delegation:** Architect drafts task prompts with **[ACCEPTANCE CRITERIA]** and hands to Floor Manager
3. **Orchestration:** Floor Manager reads all tickets, plans parallel vs sequential work, dispatches to Workers
4. **Execution:** Workers generate code. Floor Manager performs all file operations.
5. **Inspection:** Floor Manager checks each acceptance criteria item, reviews diffs
6. **Review:** Floor Manager moves task to **Review** status when satisfied
7. **Sign-off:** The Architect gives final PASS/FAIL verdict before merge

**CRITICAL:** Only Workers write code. The Architect and Floor Manager never generate code.

