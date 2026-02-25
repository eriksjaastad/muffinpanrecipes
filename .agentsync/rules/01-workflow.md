---
targets: ["*"]
---

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
