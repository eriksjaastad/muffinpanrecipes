## Project-Specific Decisions (Post-Scaffold)

### 2026-02-25: Branch-only execution for Codex changes

**Context:** We are establishing trust and safe collaboration while Codex starts making active changes.

**Decision:** All Codex changes must be developed on dedicated feature branches and reviewed before merge.

**Reasoning:** Keeps `main` stable while enabling proactive work. Makes diffs reviewable and reversible.

**Alternatives considered:** Direct pushes to `main` were rejected due to risk and low auditability.

### 2026-02-25: Replace task-list framing with outcomes framing

**Context:** `TODO` style docs were creating implementation bias and stress rather than clarity.

**Decision:** Shift planning language toward outcomes/intentions. Keep work aligned to `PROJECT_DOD.md` and an intentions document rather than a step-by-step TODO list.

**Reasoning:** Encourages solution design by the agent while preserving clear human intent and success criteria.

**Alternatives considered:** Keeping a traditional TODO board as the primary planning artifact was rejected.

### 2026-02-25: Move toward hosted Kanban for shared visibility

**Context:** Work is happening across multiple machines and users.

**Decision:** Use a hosted Kanban model (likely GitHub Projects) for shared real-time task visibility.

**Reasoning:** Single source of truth across devices, fewer sync issues, easier PR/task traceability.

**Alternatives considered:** Local-only Kanban was rejected for multi-device collaboration.
