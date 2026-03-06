# Learning Loop Pattern

> **Purpose:** Guide for creating reinforcement learning cycles in any project
> **Status:** Active
> **Created:** January 10, 2026
> **Updated:** January 11, 2026

---

## What This Pattern Is

This is a **meta-pattern** - guidance on how to create learning loops in any project, not a rigid structure. Every project will have different learnings, but the mechanism for capturing and reinforcing them is universal.

**Project-scaffolding does NOT store other projects' learnings.** It teaches how to create your own.

---

## When a Learning Loop Begins

A learning loop is triggered when:

1. **Instructions were given** (to a human, AI, or system)
2. **Instructions were followed or ignored** (outcome observed)
3. **The moment is documented** (what happened, why)
4. **Reinforcement is applied** (prevent recurrence or encourage repetition)

**Examples of learning loop triggers:**
- AI model timed out on a task → Model-specific learning
- Floor Manager did work instead of delegating → Workflow/governance learning
- Security vulnerability discovered → Safety learning
- A pattern worked exceptionally well → Success pattern

---

## The Problem Learning Loops Solve

**Knowledge externalization:** When we document a learning but don't apply it, the cost is deferred to a future session. The person who documents isn't the one who pays when it fails to get applied.

This is the same failure mode Erik identified in societal systems:

> *"We have an accounting system that rewards speed and spectacle, and an accountability system that arrives after the decision-makers are gone."*

In our system:
- We document learnings (speed/spectacle of "look, we learned something!")
- We don't apply them consistently (accountability arrives in future sessions when things break)
- Future sessions pay the cost of re-learning

**The documentation is write-only. The learning loop has a gap.**

---

## Insight from Society/Governance Thinking

Erik's late-night conversation with GPT about economic externalities revealed parallel patterns:

| Society Problem | Our Problem |
|-----------------|-------------|
| Externalities get pushed to future generations | Learning costs get pushed to future sessions |
| "Book it now or pay later with interest" | Document it now, but who applies it? |
| No mechanism to make decision-makers feel the cost | No mechanism to make prompters feel skipped learnings |

**The solution in both cases:** Make the cost visible and immediate, not deferred and diffuse.

---

## Proposed Mechanism: The Four Bridges

### Bridge 1: Downstream Harm Estimate

Every task prompt should include a **Downstream Harm Estimate**:

```markdown
### ⚠️ DOWNSTREAM HARM ESTIMATE
- **If this fails:** [What breaks? Who pays? How long to recover?]
- **Known pitfalls:** [What patterns from LOCAL_MODEL_LEARNINGS.md apply here?]
- **Assumptions that would break this:** [What's the confidence range?]
```

**Why it works:**
- Forces the Super Manager to consult learnings BEFORE drafting
- Makes hidden costs visible upfront
- Creates a "50-year receipt" for tasks (who pays if it goes wrong?)

**Example:**
```markdown
### ⚠️ DOWNSTREAM HARM ESTIMATE
- **If this fails:** Script corrupts cursorrules files. Recovery requires git restore. ~15 min per affected project.
- **Known pitfalls:** DeepSeek-R1 needs 300s timeout for file-heavy tasks (LOCAL_MODEL_LEARNINGS.md). Task requires file writes.
- **Assumptions that would break this:** Assumes all projects have .cursorrules in root. If nested, detection fails silently.
```

---

### Bridge 2: Pre-Flight Block (Not Reminder)

**Current state:** "Remember to read learnings before prompting"
**Problem:** Remembering is optional. Optional things get skipped.

**Proposed:** A structural block that prevents work without acknowledgment.

Option A: **Learnings Checksum in Prompts**
```markdown
### 📚 LEARNINGS APPLIED
- [x] Timeout: 300s for file-heavy tasks (learned Jan 10)
- [x] Decomposition: Micro-tasks for DeepSeek (learned Jan 10)
- [x] Diff style: StrReplace over full rewrites (learned Jan 10)

_Last consulted LOCAL_MODEL_LEARNINGS.md: 2026-01-10_
```

If this section is missing or stale, the Floor Manager should **block execution** and request it.

Option B: **Automated Prompt Validator**
A script that parses prompts and checks for required sections before they go to Workers. Fails fast if harm estimate or learnings acknowledgment is missing.

---

### Bridge 3: Preventable Failure Flag

When a task fails, the Floor Manager should ask:

> "Was this failure covered by an existing learning that wasn't applied?"

If yes, log it as a **Preventable Failure**:

```markdown
| Date | Failure | Was it Preventable? | Which Learning? |
|------|---------|---------------------|-----------------|
| Jan 10 | Timeout | YES | "300s for file-heavy" |
| Jan 10 | Full rewrite timeout | YES | "Use StrReplace" |
```

**Why it works:**
- Creates a "shame log" for skipped learnings (immediate cost)
- Makes the cost of not reading visible
- Over time, tracks which learnings are most frequently ignored

---

### Bridge 4: Learning Debt Tracker

Documented learnings have a lifecycle:

```
OBSERVED → DOCUMENTED → COMPILED → APPLIED
```

**Learning Debt** = Documented but not yet Compiled into templates.

Track this explicitly:

```markdown
## Learning Debt (Uncompiled)

| Learning | Documented | Compiled Into |
|----------|------------|---------------|
| 300s timeout | ✅ Jan 10 | ❌ Not in templates yet |
| StrReplace preference | ✅ Jan 10 | ❌ Not in templates yet |
| Micro-task decomposition | ✅ Jan 10 | ❌ Not in templates yet |
```

**Trigger for compilation:** When a learning has caused 2+ preventable failures, it MUST be compiled into templates. No more deferral.

---

## How This Closes the Loop

```
┌─────────────────────────────────────────────────────────────────┐
│                                                                 │
│   FAILURE/SUCCESS                                               │
│         │                                                       │
│         ▼                                                       │
│   ┌─────────────┐                                               │
│   │  DOCUMENT   │  ← Floor Manager writes to                    │
│   │  (Learnings)│    LOCAL_MODEL_LEARNINGS.md                   │
│   └─────────────┘                                               │
│         │                                                       │
│         ▼                                                       │
│   ┌─────────────────────────────────────────────────────────┐   │
│   │  TRACK DEBT │  ← Learning added to "uncompiled" tracker │   │  ◄── NEW
│   └─────────────────────────────────────────────────────────┘   │
│         │                                                       │
│         ▼                                                       │
│   ┌─────────────┐                                               │
│   │   REVIEW    │  ← Super Manager reads learnings              │
│   │             │    BEFORE drafting (blocked if stale)         │  ◄── ENFORCED
│   └─────────────┘                                               │
│         │                                                       │
│         ▼                                                       │
│   ┌─────────────────────────────────────────────────────────┐   │
│   │   ESTIMATE  │  ← Downstream Harm Estimate required        │   │  ◄── NEW
│   └─────────────────────────────────────────────────────────┘   │
│         │                                                       │
│         ▼                                                       │
│   ┌─────────────┐                                               │
│   │   APPLY     │  ← Floor Manager uses prompt with             │
│   │             │    baked-in learnings                         │
│   └─────────────┘                                               │
│         │                                                       │
│         ▼                                                       │
│   ┌─────────────────────────────────────────────────────────┐   │
│   │  FAIL CHECK │  ← If failed: Was it preventable?           │   │  ◄── NEW
│   │             │    If yes: Flag + increment debt trigger     │
│   └─────────────────────────────────────────────────────────┘   │
│         │                                                       │
│         └───────────────────────────────────────────────────────┘
```

---

## The Fuzzy vs Explicit Tension

Erik's concern: "I really wanted to get around relying on prompts. Prompts are fragile."

**This pattern doesn't eliminate prompts, but it:**

1. **Reduces prompt fragility** by baking known pitfalls into the prompt structure (Harm Estimate section)
2. **Creates feedback loops** that catch when fragility causes failure (Preventable Failure Flag)
3. **Evolves toward robustness** by forcing compilation after repeated failures

**The fuzzy part:** Learnings can stay "documented but uncompiled" until they prove their importance (2+ preventable failures).

**The explicit part:** Once compiled, they're structural. Can't be skipped.

---

## What This Doesn't Solve

1. **Cross-project learning transfer** - How does `project-scaffolding` pass learnings to other projects? (Future pattern needed)

2. **Model-specific prompt optimization** - Each model might need different compiled rules. (Future: model-specific templates)

3. **The "who triggers compilation" question** - Still manual. Could be automated with a script that reads the debt tracker.

---

## Relation to Societal Governance Ideas

From Erik's GPT conversation, these principles apply:

| Principle | Application Here |
|-----------|------------------|
| **"Externality as Debt"** | Learning Debt Tracker - uncompiled learnings are explicit debt |
| **"50-Year Receipt"** | Downstream Harm Estimate - who pays if this fails? |
| **"Pay for outcomes, not outputs"** | Preventable Failure Flag - measure "did we apply learning" not "did we document" |
| **"AI as adversarial auditor"** | Floor Manager checks "was this preventable?" after every failure |
| **"Discount rate fight"** | Refusing to defer learning application to future sessions |

---

## Creating a Learning Loop in Your Project

This section provides guidance for implementing learning loops in any project. Adapt to your needs.

### Universal Components (All Learning Loops Have These)

Every learning loop, regardless of project type, has four core components:

| Component | Purpose | Questions to Answer |
|-----------|---------|---------------------|
| **1. Trigger** | Detects when learning is needed | What event signals "something happened worth learning from"? |
| **2. Documentation** | Captures what happened | Where do learnings get recorded? What format? |
| **3. Analysis** | Understands why it happened | Was this preventable? What caused it? |
| **4. Reinforcement** | Prevents recurrence (or encourages repetition) | How do we update instructions/processes? |

### Types of Learning Loops

Your project may have multiple learning loops for different domains:

| Learning Type | Trigger Examples | Where to Document |
|---------------|------------------|-------------------|
| **Model/Tool Behavior** | Timeout, unexpected output, hallucination | `Documents/reference/MODEL_LEARNINGS.md` |
| **Workflow/Governance** | Role bypass, skipped approval, wrong escalation | `Documents/reference/WORKFLOW_LEARNINGS.md` or incident report |
| **Safety/Security** | Vulnerability found, data at risk, near-miss | `Documents/safety/` or incident report |
| **Success Patterns** | Something worked exceptionally well | Pattern library or success report |

### Minimum Viable Learning Loop

For simple projects, a single file may suffice:

```markdown
# Project Learnings

## Active Learnings (Apply These)
| Learning | Date | Applied To |
|----------|------|------------|
| Always use --dry-run first | 2026-01-10 | AGENTS.md constraints |

## Learning Log
| Date | What Happened | Why | Reinforcement |
|------|---------------|-----|---------------|
| 2026-01-10 | Script deleted files | No dry-run | Added constraint |
```

### Full Learning Loop (Complex Projects)

For projects with significant AI collaboration or automation:

**1. Learnings Document** (`Documents/reference/LEARNINGS.md`)
- Categorized by type (model, workflow, safety)
- Includes failure log with root cause
- Learning Debt Tracker (documented but not yet compiled)

**2. Prompt Template Integration**
- "Downstream Harm Estimate" section in prompts
- "Learnings Applied" acknowledgment
- Reference to learnings document

**3. Incident Reports** (`Documents/reports/`)
- Detailed analysis of significant failures
- Lessons learned
- Changes made as a result

**4. Preventable Failure Tracking**
- After each failure: "Was this covered by an existing learning?"
- If yes, flag as preventable and increment debt

### Starting a Learning Loop

When something happens worth learning from:

```markdown
## Learning Loop Initiated: [Brief Description]

**Date:** YYYY-MM-DD
**Trigger:** [What happened that prompted this]

### 1. What Was Expected
[Instructions given, expected behavior]

### 2. What Actually Happened
[Observed behavior, outcome]

### 3. Why (Root Cause)
[Analysis of why this happened]

### 4. Reinforcement (Action Taken)
- [ ] Document added to learnings file
- [ ] Instructions/constraints updated
- [ ] Relevant templates modified
- [ ] Team/AI informed of change

### 5. Verification
How will we know this learning is being applied?
```

### The Reinforcement Question

The key question that makes a learning loop effective:

> **"Was this failure preventable based on existing knowledge?"**

If yes → The learning loop has a gap. Fix it.
If no → New learning discovered. Document and reinforce.

### Flexibility Principle

**These are guidelines, not rigid rules.** Every project's learning loop will look different:

- A solo script might just have a comment at the top listing gotchas
- A multi-AI system might have elaborate tracking with debt counters
- A data pipeline might focus on data quality learnings

**The mechanism is universal. The implementation is yours.**

---

## Applying This to Your Project

1. **Start simple** - A single learnings file is enough initially
2. **Document triggers** - Define what events should start the loop
3. **Choose your reinforcement** - How will learnings get applied?
4. **Review periodically** - Are learnings actually being used?
5. **Evolve as needed** - Add complexity only when simple isn't working

---

## Related Documents

- `Documents/reference/LOCAL_MODEL_LEARNINGS.md` - Example implementation for local AI models
- `AGENTS.md` - Prompt template with learning integration
- `Documents/planning/KNOWLEDGE_CYCLE_DISCUSSION.md` - Original design discussion

---

*This pattern provides guidance, not prescription. Adapt it to your project's needs. The goal is closing the gap between "we learned something" and "we apply what we learned."*

## Related Documentation

- [Local Model Learnings](Documents/reference/LOCAL_MODEL_LEARNINGS.md) - local AI
- [Automation Reliability](patterns/automation-reliability.md) - automation
- [Cost Management](Documents/reference/MODEL_COST_COMPARISON.md) - cost management
- [Tiered AI Sprint Planning](patterns/tiered-ai-sprint-planning.md) - prompt engineering
- [AI Model Cost Comparison](Documents/reference/MODEL_COST_COMPARISON.md) - AI models
- [Safety Systems](patterns/safety-systems.md) - security
- [Project Scaffolding](../project-scaffolding/README.md) - Project Scaffolding
