# Dialogue Implementation Plan (Pre-Monday Readiness)

Goal: Ship reliable, character-consistent weekly dialogue by Monday.

## Source of Truth
- `CREATIVE_BIBLE.md` (especially dialogue generation philosophy + episode model)
- `backend/data/agent_personalities.json` (full persona cards)

## Non-Negotiables
1. Name-strip test passes (character identity obvious without labels)
2. No generic assistant voice
3. Scene context always included (day, deadline pressure, prior turn, relationship tension)
4. Fail-loud if dialogue generation stage cannot produce acceptable output

## Architecture (MVP This Week)

### 1) Dialogue Prompt Builder
Create a dedicated builder that always includes, in order:
1. Character full persona card (verbatim from JSON)
2. Scene packet (episode id, day beat, deadline clock, participants, conflict)
3. Recent thread context (last N messages)
4. Generation directive (short, minimal, avoid over-directing)

### 2) Dialogue Quality Gate
Add automated checks before accepting generated turns:
- Voice markers (per-character lexical and rhythm indicators)
- Length/rhythm sanity (not all characters writing in same sentence pattern)
- Prohibited phrases list (generic AI helper tone)
- Distinctiveness score across speakers in same scene

If quality gate fails -> retry with reinforced context; if still fails -> hard fail + notify.

### 3) Practice Conversation Harness
Add a script that simulates 3-5 scene types:
- Monday brainstorm spark
- Tuesday Margaret/Steph tension
- Wednesday Julian selection argument
- Thursday Marcus/Steph headline disagreement
- Friday approval cliffhanger

Outputs:
- JSON transcript
- Name-strip version (names removed)
- QA score report

### 4) Episode Data Model Integration
Dialogue stored under episode object as canonical weekly thread:
- day
- character
- message
- timestamp
- tension tags (optional)

## Test Plan

### Unit tests
- Prompt builder always includes full persona card
- Scene packet fields mandatory
- Dialogue QA catches banned generic phrasing

### Integration tests
- Simulated multi-turn scene preserves distinct voice across 10+ turns
- Name-strip heuristic confidence above threshold
- Failure path sends pipeline failure notification and blocks progression

## Delivery Sequence
1. Build dialogue prompt builder + tests
2. Build quality gate + tests
3. Build practice harness + report output
4. Wire into orchestrator dialogue generation path
5. Run 5 practice scenes/day through Monday

## Research Notes
Web search tool is currently unavailable in this runtime (missing Brave API key).
If key is added, pull in references on persona reinforcement + long-context character drift mitigation.
