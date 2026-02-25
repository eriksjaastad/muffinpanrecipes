
<!-- SCAFFOLD:START - Do not edit between markers -->
# Architectural Decisions - muffinpanrecipes

> *Documenting WHY we made decisions, not just WHAT we built.*
>
> Without documented reasoning, even the people who made the decisions forget why.

---

## How to Use This File

When you make a significant decision, add an entry:

```markdown
### YYYY-MM-DD: Brief Title

**Context:** What situation led to this decision?

**Decision:** What did we decide?

**Reasoning:** Why this choice over alternatives?

**Alternatives considered:** What else did we consider and why not?
```

Good candidates for entries:
- Choosing a library/framework over another
- Architectural patterns (why monolith vs microservices, why this folder structure)
- Build/deploy choices (why Railway vs Vercel, why SQLite vs Postgres)
- Process decisions (why we use X workflow)
- Anything where future-you might ask "why did we do it this way?"

---

## Decision Log

### 2026-01-27: Project Created

**Context:** Starting muffinpanrecipes.

**Decision:** Using project-scaffolding templates for initial structure.

**Reasoning:** Consistent setup across projects. Battle-tested patterns. Don't reinvent file structures.

---

## Principles (Optional)

Document recurring principles that guide decisions in this project:

<!-- Example:
### Local-First

We prefer local processing over cloud APIs when possible. Reduces cost, improves privacy, works offline.

### Simple Over Clever

Choose boring technology. Complexity must justify itself.
-->

---

## Open Questions

Decisions we haven't made yet but need to:

<!-- Example:
### Should we use a database or flat files?

**Current state:** Using JSON files for simplicity.

**Trigger to revisit:** If we hit performance issues or need concurrent writes.
-->

---

*Last updated: 2026-01-27*

<!-- SCAFFOLD:END - Custom content below is preserved -->