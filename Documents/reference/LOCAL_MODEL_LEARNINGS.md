# Local Model Learnings

> **Purpose:** Institutional memory for working with local AI models (Ollama)
> **Created:** January 10, 2026
> **Updated:** January 12, 2026

---

## 🚨 CRITICAL: Workers Cannot Execute Commands (Jan 12, 2026)

Workers (Ollama local models) can only generate text. They CANNOT:
- Execute bash commands
- Write files directly
- Run cp, sed, chmod, etc.

All file operations must be done by Floor Manager directly.

---

## Why This Document Exists

Local models are a black box. When they fail or succeed, the knowledge evaporates between sessions. This document captures:
- What works and what doesn't
- Model-specific quirks
- Prompt patterns that improve results
- Failure modes to avoid

**Goal:** Stop re-learning the same lessons. Make prompts better over time.

---

## Model Profiles

### DeepSeek-R1 (14b)

**Best for:**
- Complex reasoning tasks
- Multi-step problem solving
- Code generation (with caveats)

**Known limitations:**
- Timeout issues on long tasks (observed Jan 10, 2026). Requires 300s+ for large file writes.
- Reasoning overhead: Can spend 60-120s "thinking" before generating, consuming timeout budget.
- Connection management: Sometimes fails to close streams properly, leading to false-positive timeouts.

**Prompt tips that work:**
- Be explicit about output format
- Break complex tasks into steps
- Include examples when possible

**Prompt anti-patterns:**
- (Add as discovered)

---

### Qwen 2.5 / Qwen 3 (4b, 14b)

**Best for:**
- General-purpose tasks
- Fast code generation (significantly less reasoning overhead than R1)
- Repetitive boilerplate and integration tasks

**Known limitations:**
- Lacks the deep "safety awareness" and self-correction of DeepSeek-R1.
- May hallucinate imports or skip constraints if not explicitly repeated.

**Prompt tips that work:**
- (Add as discovered)

**Prompt anti-patterns:**
- (Add as discovered)

---

### Llama 3.2 (3b)

**Best for:**
- Speed-critical tasks
- Simple classification
- High-volume filtering

**Known limitations:**
- Lower quality on complex tasks
- May miss nuance

**Prompt tips that work:**
- Keep prompts short and direct
- Binary yes/no questions work well

**Prompt anti-patterns:**
- (Add as discovered)

---

## Prompt Pattern Library

### Pattern: Acceptance Criteria Checklist

**What:** Structure prompts with explicit checkboxes for the model to verify against.

**Why it works:** Local models respond well to concrete, binary success criteria. Reduces ambiguity.

**Example:**
```markdown
### [ACCEPTANCE CRITERIA] (MANDATORY CHECKLIST)
- [ ] **Functional:** Code correctly implements X
- [ ] **Syntax:** File passes linting
- [ ] **Standards:** Uses pathlib.Path
- [ ] **Verification:** Tests pass
```

**First observed:** Warden Enhancement prompts (Jan 10, 2026)
**Models tested:** DeepSeek-R1, Qwen 2.5

---

### Pattern: Context Bridge

**What:** Explicitly provide file contents and context rather than assuming the model can find them.

**Why it works:** Local models don't have tool access like cloud models. They need context spoon-fed.

**Example:**
```markdown
**Current code (lines 73-82):**
\`\`\`python
try:
    os.replace(temp_name, path)
except Exception as e:
    # ... show actual code
\`\`\`
```

**First observed:** Safety Audit prompts (Jan 10, 2026)
**Models tested:** DeepSeek-R1

---

### Pattern: Micro-Task Decomposition

**What:** Break tasks into the smallest possible atomic units (5-10 min each) rather than larger cohesive tasks (20-30 min).

**Why it works:** Reasoning models like DeepSeek-R1 spend significant time in their "thinking" phase before generating output. A 30-minute task might timeout before the model finishes reasoning. Smaller tasks let the model complete both reasoning AND generation within the timeout window.

**Signs you need to decompose further:**
- Model times out partway through code generation
- Model starts implementing features not in the current task scope
- Model deviates from provided patterns (inventing its own approach)

**Decomposition strategy:**
```
BAD (too large):
- Task 1: Create script with scanning, detection, and output (30 min)

GOOD (atomic):
- Task 1a: Create file skeleton with imports and argparse (5 min)
- Task 1b: Add scanner function (5 min)
- Task 1c: Add detection function (5 min)
- Task 1d: Wire up main() and output (5 min)
```

**Additional safeguards:**
- Add explicit "DO NOT" constraints to prevent scope creep
- Provide exact code patterns to copy (reduce invention)
- Specify what's OUT of scope for this micro-task

**First observed:** Global Rules Injection Task 1 timeout (Jan 10, 2026)
**Models affected:** DeepSeek-R1 (14b) - reasoning overhead consumes timeout
**Models less affected:** Qwen 2.5 Coder - faster code generation, less reasoning overhead

---

### Pattern: Explicit DO NOT Constraints

**What:** Add a prominent "CONSTRAINTS" section listing what the model should NOT do.

**Why it works:** Local models sometimes get creative and add features, use different approaches, or implement future task scope. Explicit prohibitions prevent scope creep.

**Example:**
```markdown
## CONSTRAINTS (READ FIRST)
- DO NOT implement execute/update logic - this task is DRY-RUN ONLY
- DO NOT use os.walk - use pathlib.iterdir() as shown in the template
- DO NOT add features beyond what's listed in acceptance criteria
- COPY the code patterns provided verbatim - do not reinvent
```

**First observed:** Global Rules Injection Task 1 (Jan 10, 2026) - Worker used os.walk instead of iterdir, started implementing execute logic
**Models tested:** DeepSeek-R1

---

### Pattern: 3-Strike Escalation Rule

**What:** A strict protocol for handling Worker timeouts or failures.

**Why it works:** Prevents "Session Momentum" from overriding architectural guardrails. Ensures that if models struggle, the process is paused and analyzed rather than patched.

**Protocol:**
1. **Strike 1:** Retry with the same model (check if it was a transient connection issue).
2. **Strike 2:** Switch model (e.g., DeepSeek-R1 → Qwen 2.5 Coder).
3. **Strike 3:** **HALT** and alert the Conductor. Do NOT manually integrate logic.

**First observed:** Global Rules Injection implementation (Jan 10, 2026)

---

### Pattern: Incremental Diff Style

**What:** Asking models to provide only the code delta (using `Edit` or `StrReplace` parameters) rather than rewriting the entire file.

**Why it works:** File writing is the most time-consuming part of the worker's task. Rewriting a 100-line file to add 5 lines is inefficient and prone to timeout.

**Example:**
```markdown
## TASK
- Add `new_function` to `script.py`.
- Output ONLY the `old_string` and `new_string` for a `StrReplace` call.
```

**First observed:** Global Rules Injection implementation (Jan 10, 2026)

---

### Pattern: Raw Output Limitation

**What:** Local models (qwen3, deepseek-r1) cannot reliably output "raw code only" even when explicitly instructed. They include `<Thinking>`, reasoning blocks, or conversational text in their `stdout` JSON output, corrupting files if written directly.

**Why it matters:** When a prompt says "OUTPUT ONLY RAW CODE - no thinking, no conversation," cloud models comply. Local models don't - their reasoning process appears in the actual `ollama_run` result, not just console streaming.

**Example (verbatim from B1b worker output):**
```json
{
  "stdout": "Thinking...\nWe are given three functions...\n(300+ lines of deliberation)...\n...done thinking.\n\ndef _parse_rclone_config():\n..."
}
```

**Corruption patterns observed:**
- `Thinking...` / `...done thinking.` blocks (deepseek-r1)
- Markdown code fences (` ```python `) despite instructions
- Conversational preambles ("Sure, here's the code...")

**Transcription Paradox:** Models struggle MORE with "copy this exactly" than "write a function that does X" - they feel compelled to explain their transcription process.

**Workarounds:**
1. **FM Direct (after Strike 1 for transcription):** For pure transcription tasks where exact code is in the prompt, Floor Manager may bypass workers after first failure. Don't burn 3 strikes on copy-paste tasks.
2. **FM Cleaned:** Extract valid code from dirty output, strip thinking blocks, write with `cat <<EOF`
3. **Output Parsing (future):** Build automated cleaning in MCP to strip `<Thinking>...</thinking>` before returning

**Protocol update:** For transcription/stub tasks, FM Direct authorized after Strike 1 (not Strike 3).

**First observed:** Backup Audit B1a-B2 (Jan 11, 2026) - confirmed in stdout JSON, not just console. Floor Manager provided verbatim evidence.

---

### Pattern: Context Bridge Size Limit

**What:** Keep code examples in Context Bridge sections under 30 lines. For larger examples, split into multiple micro-tasks or provide as separate reference files.

**Why it works:** Both qwen3:4b and deepseek-r1:14b entered extended "Thinking..." analysis loops when presented with ~80 lines of example code. The models spent their reasoning budget parsing the example instead of executing the task. deepseek-r1:14b got further (started output at line 115) but still timed out at 120s.

**Decomposition strategy:**
```
BAD (too large):
- Context Bridge: 80 lines of complete file example
- Task: "Create this file"

GOOD (atomic):
- A1a: Imports + dataclasses only (~25 lines)
- A1b: Add definitions (~30 lines, StrReplace)
- A1c: Add functions (~20 lines, StrReplace)
```

**First observed:** Agent Dispatcher A1 prompt (Jan 11, 2026) - 2 strikes, both timeouts.

---

### Pattern: Prompt Brevity Principle

**What:** Keep worker prompts focused and minimal (100-200 lines ideal, not 400+).

**Why it works:** Less context = faster reasoning, less chance of timeout, clearer task focus. Workers don't need verbose explanations - they need clear instructions and exact code.

**Structure for worker prompts:**
- Task description (1-2 lines)
- Constraints (4-6 bullets max)
- Acceptance criteria (5-7 items)
- Exact code to write (if applicable)
- Simple verification (3-4 commands)

**What to EXCLUDE from worker prompts:**
- Floor Manager instructions (put in INDEX instead)
- Verbose explanations of "why"
- Downstream harm estimates (that's for planning phase, not execution)
- Learning justifications
- Detailed protocol descriptions

**Example (compare):**
- ❌ **Bad:** 400-line prompt with FM Protocol, Downstream Harm, verbose context, learning justifications
- ✅ **Good:** 150-line prompt with task, constraints, code, verification

**First observed:** Project-tracker Agent Dispatcher prompts (Jan 11, 2026) - consistently under 200 lines, very focused

---

### Pattern: Code-First Prompting

**What:** Show the exact code to write, not just describe what to write.

**Why it works:** Reduces interpretation errors, faster generation, less reasoning overhead. Models spend less time "figuring out" the solution and more time executing the known solution.

**Example (correct approach):**
```markdown
## Code to Add

\```python
def validate_project(path: Path) -> bool:
    """Validate project structure."""
    if not path.exists():
        return False
    # ... exact implementation here
\```
```

**Example (anti-pattern):**
```markdown
## Task

Add a function called validate_project that:
- Takes a Path parameter
- Checks if it exists
- Returns a boolean
- Should validate structure
```

**When to use:**
- Creating new files with known structure
- Adding functions with clear implementation
- Modifying code where you know the exact changes

**When not to use:**
- Open-ended design tasks
- When you want the model to choose the approach

**First observed:** Project-tracker prompts (PROMPT_A1a_SKELETON.md, PROMPT_A2_AGENT_EXECUTOR.md) provide verbatim code (Jan 11, 2026)

---

### Pattern: INDEX for Multi-Prompt Work

**What:** When you have 5+ related prompts for a feature, create an INDEX document that contains:
- Overall context and goal
- Prompt execution order table (with status tracking)
- Escalation protocol (don't repeat in each prompt)
- Progress tracking checkboxes
- Shared constraints across all prompts
- Floor Manager instructions

**Why it works:** 
- Floor Manager has one place to track progress
- Individual prompts stay focused (no repeated protocol)
- Easy to see what's done vs pending
- Shared context documented once, not N times

**Structure:**
```markdown
# Feature Name - Prompts Index

## Context
[Overall goal and why]

## Done Criteria (Overall Feature)
- [ ] Task 1
- [ ] Task 2
...

## Prompt Execution Order
| # | File | Description | Est Time | Status |
|---|------|-------------|----------|--------|
| 1 | PROMPT_1.md | ... | 5 min | [ ] |
...

## Escalation Protocol
[3-Strike Rule, etc.]

## Progress Tracking
[Worker models used, notes]
```

**When to use:**
- 5+ related atomic tasks
- Complex features spanning multiple files
- When you need to track progress across sessions

**When not to use:**
- Single atomic tasks
- 1-3 simple prompts

**Example:** `project-tracker/Documents/archives/planning/phase4_agent_dispatcher/AGENT_DISPATCHER_INDEX.md` (6 prompts for one feature)

**First observed:** Project-tracker Agent Dispatcher (Jan 11, 2026)

---

### Pattern: (Add more as discovered)

---

## Failure Log

Track specific failures to identify patterns.

| Date | Model | Task | Failure Mode | Resolution |
|------|-------|------|--------------|------------|
| Jan 10, 2026 | DeepSeek-R1 | Warden enhancement | Timeout on complex tasks | Floor Manager took over (Avoid this!) |
| Jan 10, 2026 | DeepSeek-R1/Qwen | Global Rules Injection | Multiple timeouts on integration | Floor Manager manual merge (Protocol Violation) |
| Jan 10, 2026 | Qwen 3 (14b) | Pre-Commit Hook | Success | First test of Learning Loop Pattern. Succeeded in 1 retry (python vs python3). |
| Jan 11, 2026 | qwen3:4b | Agent Dispatcher A1 | Timeout - analysis loop on Context Bridge | Strike 1, escalated to Strike 2 |
| Jan 11, 2026 | deepseek-r1:14b | Agent Dispatcher A1 | Timeout at line 115 - reasoning overhead | Strike 2, escalated to Strike 3 (HALT). Split into A1a/A1b/A1c |
| Jan 11, 2026 | qwen3:4b, deepseek-r1:14b | Backup Audit B1a | Output corruption - reasoning tags in code output | Strike 3, FM Direct execution. New limitation discovered. |

---

## Session Observations

### Jan 10, 2026 - Warden Enhancement Sprint

**Models used:** DeepSeek-R1, Qwen 2.5 (via Floor Manager)

**What happened:**
- DeepSeek had timeout issues on worker tasks
- Floor Manager (Gemini Flash) ended up doing the work directly
- Prompts written by Claude Opus worked well when Floor Manager executed them

**Learnings:**
- Structured prompts with acceptance criteria translate well across models
- Local models may need simpler, more atomic tasks
- Floor Manager as QA + backup executor is a good pattern

**Prompt refinements:**
- (Add any adjustments made)

---

### Jan 10, 2026 (Night) - Global Rules Injection Sprint

**Models used:** DeepSeek-R1, Qwen 2.5 (via Floor Manager)

**What happened:**
- Micro-tasks 1a-1b (Skeleton/Scanner) succeeded with DeepSeek-R1.
- Micro-tasks 1c-1d (Detection/Output) timed out (180s) on both DeepSeek and Qwen.
- The Floor Manager (Gemini) manually integrated the code to "keep momentum," violating `AGENTS.md`.

**Root cause analysis:**
- **Integration overhead:** Asking models to rewrite the whole file + new logic is too heavy.
- **Role confusion:** Floor Manager prioritized "completion" over "protocol."

**Learnings:**
- **Timeout Increase:** Set `timeout: 300000` (5 min) for file-heavy tasks.
- **Tool Selection:** Prefer `StrReplace` over `Write` for incremental updates.
- **Escalation Protocol:** Established the "3-Strike Rule" to prevent manual takeover.

**Key insight:** The Floor Manager must resist the urge to "just finish it." If the Worker can't do it, the task is either too big or the model is wrong for the job. Stop and rethink.

---

## Learning Debt Tracker

> **Purpose:** Track documented learnings that haven't been "compiled" into templates yet.
> **Rule:** When a learning causes 2+ preventable failures, it MUST be compiled. No more deferral.

| Learning | Documented | Compiled Into | Preventable Failures |
|----------|------------|---------------|---------------------|
| 300s timeout for file-heavy | ✅ Jan 10 | ❌ Not in templates | 2 (Jan 10 session) |
| Use python3 on macOS | ✅ Jan 10 | ❌ Not in templates | 1 |
| StrReplace over full rewrites | ✅ Jan 10 | ❌ Not in templates | 1 |
| Micro-task decomposition | ✅ Jan 10 | ❌ Not in templates | 1 |
| Explicit DO NOT constraints | ✅ Jan 10 | ❌ Not in templates | 1 |
| 3-Strike Escalation Rule | ✅ Jan 10 | ❌ Not in templates | 0 |
| Context Bridge <30 lines | ✅ Jan 11 | ❌ Not in templates | 2 (A1 double timeout) |
| Raw output corruption | ✅ Jan 11 | ❌ Not in templates | 3 (B1a triple failure) |

**Compilation trigger:** Any learning with 2+ preventable failures must be added to the prompt template structure.

---

## Improvement Backlog

Things to try or investigate:

- [x] Test if breaking tasks into smaller chunks helps DeepSeek timeout issues → YES, testing now (Jan 10, 2026)
- [ ] Compare same prompt across DeepSeek vs Qwen for quality
- [ ] Create prompt templates optimized for each model tier
- [ ] Add timing benchmarks to session observations
- [ ] Track success rate by task type + model combination
- [ ] Implement "Downstream Harm Estimate" in prompts (see patterns/learning-loop-pattern.md)

---

## Related Documents

- `patterns/local-ai-integration.md` - Model tiers and when to use each
- `AGENTS.md` - Caretaker Role (working with memory-less entities)
- `Documents/archives/planning/warden_evolution/WARDEN_PROMPTS_INDEX.md` - Example of structured prompts

---

*This is a living document. Update it when you learn something new about local models.*

## Related Documentation

- [[LOCAL_MODEL_LEARNINGS]] - local AI
- [[PROJECT_STRUCTURE_STANDARDS]] - project structure
- [[cost_management]] - cost management
- [[prompt_engineering_guide]] - prompt engineering
- [[ai_model_comparison]] - AI models
- [[case_studies]] - examples
- [[orchestration_patterns]] - orchestration
- [[performance_optimization]] - performance
- [[security_patterns]] - security
- [[testing_strategy]] - testing/QA
- [[project-tracker/README]] - Project Tracker
