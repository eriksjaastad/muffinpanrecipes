# Muffin Pan Recipes PRD

## 1) Overview

Muffin Pan Recipes is an AI-driven content platform disguised as a recipe website. The recipes are real and useful, but the actual product is **AI personalities working as a creative team**. The audience experiences a living creative studio that happens to produce muffin tin meals - with character arcs, creative tension, and emotional continuity across "seasons" of content.

The site prioritizes the "No-Fluff" philosophy: no bloggy preambles, no life stories before recipes. Just beautiful food photography, precise instructions, and behind-the-scenes drama from the AI editorial team.

## 2) Goals

- Create a multi-agent AI creative team with distinct personalities, functions, and evolving relationships.
- Produce high-quality muffin tin recipes with professional-grade AI photography.
- Surface the "creative tension" between AI personalities as entertainment content.
- Implement a meta-learning layer where Erik + AI partner periodically tune the worker agents.
- Build a sustainable content engine that improves over time through agent learning.

## 3) Non-Goals

- Becoming a generic recipe aggregator.
- User-generated content (Phase 1).
- Video content (deferred to Phase 6).
- Monetization via ads (anti-pattern for "No-Fluff" philosophy).

## 4) Target Users

- Home cooks who hate recipe blog preambles and want "Jump to Recipe" immediately.
- Meal preppers who appreciate the modular, portion-controlled nature of muffin tin cooking.
- AI/tech enthusiasts interested in watching AI personalities collaborate and conflict.
- Pinterest/Instagram users drawn to high-end food photography.

## 5) Problem Statement

Recipe websites are cluttered with ads, life stories, and SEO fluff. Users want fast, reliable recipes. Meanwhile, AI-generated content is often soulless and interchangeable. Muffin Pan Recipes solves both: clean, efficient recipes produced by AI personalities with depth, conflict, and growth arcs.

## 6) Core Concept: The Living Creative Studio

### Layer 1: The Creative Team (Worker Agents)

Six distinct AI personalities with functional roles:

| Role | Function | Personality | Model Suggestion |
|------|----------|-------------|------------------|
| **Creative Director** | Final arbiter of quality | Sophisticated, decisive, "Grumpy Architect" | DeepSeek-R1 |
| **Art Director** | Commands the photographer, selects visuals | Obsessed with lighting and texture | GPT-4o |
| **Editorial Copywriter** | Recipe titles, descriptions, voice | Professional, witty, precise | Claude |
| **Site Architect** | HTML/Tailwind, SEO, mobile-first | Technical, fast, "Speed is a feature" | Qwen |
| **Social Dispatcher** | Pinterest, Instagram, TikTok presence | Outgoing, trend-aware, engaging | Gemini Flash |
| **Screenwriter** | Captures creative tension as narrative | Observational, witty, documentarian | Claude |

### Layer 2: The Writers Room (Story Planners)

A meta-layer of AI "story writers" above the creative team:

- Plan character arcs over "seasons" of content (batches of 10-20 recipes)
- Inject life events that affect work quality and relationships
- Create tension and resolution between team members
- Ensure emotional continuity across months of content

Example arc: "Over the next 20 recipes, the Copywriter gradually finds their voice while the Photographer has a creative block around recipe 15, then a breakthrough."

### Layer 3: Meta-Learning (Erik + AI Partner)

Weekly or monthly review sessions:

- Evaluate if memory formats are helping agents retain patterns
- Introduce "curriculum" (study food photography from a specific era)
- Tune control parameters based on output quality
- Track what's working in the agent learning environment

## 7) Data Model (MVP)

### Recipe

```yaml
id: string
slug: string
title: string
description: string
why_muffin_pans: string
category: breakfast | lunch | dinner | snack | dessert
yield: { count: int, pan_type: standard | jumbo | mini }
prep_time_minutes: int
cook_time_minutes: int
ingredients: [{ amount: string, metric: string, imperial: string, item: string }]
instructions: [{ step: int, action: string }]
tips: [string]
image_path: string
created_at: datetime
editorial_notes: { director: string, art_director: string }  # NEW
```

### AgentPersona

```yaml
id: string
name: string
role: creative_director | art_director | copywriter | site_architect | social_dispatcher | screenwriter
model: string
personality_traits: [string]
backstory: string
current_mood: string  # Updated by Writers Room
active_arc: string    # Current character arc
memory_file: path     # Persistent memory for this agent
```

### CreativeTension (Screenwriter Output)

```yaml
recipe_id: string
date: datetime
participants: [agent_id]
tension_type: disagreement | collaboration | breakthrough | conflict
dialogue_log: string  # The "behind the scenes" content
resolution: string
```

### Season

```yaml
season_id: string
start_date: date
end_date: date
theme: string
planned_arcs: [{ agent_id: string, arc_description: string }]
life_events: [{ agent_id: string, event: string, impact: string }]
```

## 8) Functional Requirements

### Recipe Pipeline

- Ingest recipe ideas (manual or AI-suggested).
- Copywriter drafts recipe text following RECIPE_SCHEMA.md.
- Art Director generates 3 image variants via RunPod/Stability AI.
- Art Director selects winner based on IMAGE_STYLE_GUIDE.md.
- Creative Director reviews full package (text + image).
- If PASS: Site Architect deploys to Vercel.
- If FAIL: Grumpy feedback triggers re-work loop.
- Screenwriter captures the tension/dialogue as content.

### Agent Memory System

- Each agent has a persistent memory file.
- Memory stores: past decisions, feedback received, creative preferences.
- Memory is consulted before each task.
- Erik + AI partner can update memory during meta-learning sessions.

### Writers Room System

- Define "seasons" of content (10-20 recipes).
- Plan character arcs for each agent.
- Inject life events mid-season.
- Track arc progress and adjust based on output quality.

### Editorial Dashboard (Admin)

- View current recipe pipeline status.
- Monitor agent "debates" and "grumpy reviews."
- Trigger new batch generation.
- Preview recipes before deployment.
- View agent memory and current mood/arc.

## 9) Non-Functional Requirements

- No hardcoded absolute paths; use relative paths or PROJECT_ROOT.
- No secrets in code; use .env and os.getenv() or Doppler.
- Safe file operations and validation before completion.
- Modular pipeline so each agent step is independently testable.

## 10) UX and UI Requirements

### Public Site (muffinpanrecipes.com)

- "No-Fluff" design: Jump to Recipe above the fold.
- High-key, white marble aesthetic matching IMAGE_STYLE_GUIDE.md.
- Mobile-first, fast load times.
- "Behind the Scenes" section showing Screenwriter's dialogue logs (optional).
- Editorial notes from Creative Director / Art Director on each recipe.

### Admin Dashboard (Local)

- Recipe pipeline kanban (Draft → Photography → Review → Deployed).
- Agent status panel (current mood, active arc).
- "Creative Tension" feed showing recent debates.
- One-click batch generation trigger.

## 11) Success Metrics

| Metric | Target |
|--------|--------|
| Recipes published per month | 8-12 |
| Creative Director rejection rate | < 30% |
| Agent memory utilization | Consulted on 100% of tasks |
| Pinterest engagement (saves) | Track baseline, improve 10%/month |
| "Behind the Scenes" page views | > 20% of recipe page views |
| Meta-learning sessions | 2-4 per month |

## 12) MVP Scope (Phase 4-5)

- [ ] Implement agent memory system (persistent files per agent).
- [ ] Build "Grumpy Review" protocol for Creative Director.
- [ ] Add Screenwriter agent to capture tension logs.
- [ ] Store editorial_notes on each recipe.
- [ ] Build local admin dashboard (FastAPI + Tailwind).
- [ ] Define first "season" with Writers Room arcs.

## 13) Post-MVP (Phase 6+)

- Video content ("Cinematic Build" 15-second clips).
- Social Dispatcher automation (Pinterest/Instagram posting).
- User-submitted recipes (with AI quality check).
- Merch store ("I didn't use the oven" t-shirts).
- The "Personality Responder" for comment engagement.

## 14) Technical Stack

| Component | Technology |
|-----------|------------|
| Frontend | Static HTML + Tailwind CSS |
| Hosting | Vercel (auto-deploy from GitHub) |
| Image Generation | Stability AI via RunPod |
| AI Orchestration | AI Router (local-first) + Agent Hub |
| Admin Dashboard | FastAPI + Tailwind |
| Database | JSON files (MVP) → SQLite (later) |
| Agent Memory | Markdown files with structured YAML |

## 15) Constraints and Governance

- Follow project governance and review protocol.
- Use validation commands before completion.
- All recipes must pass Creative Director review before deployment.
- No deployment without image selection by Art Director.
- Character arcs defined by Writers Room, not ad-hoc.

## 16) Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| Agent personalities feel shallow | Medium | Deep backstories, persistent memory, arc planning |
| Creative tension feels artificial | Medium | Screenwriter captures real disagreements, not scripted |
| Image generation costs escalate | Low | Batch processing, RunPod spot instances |
| Recipe quality inconsistent | Medium | Creative Director gate, template enforcement |
| Meta-learning sessions get skipped | Medium | Calendar reminders, measurable outcomes |

## 17) Open Items

- Confirm memory format (Markdown + YAML vs SQLite).
- Define first season theme and arcs.
- Decide if "Behind the Scenes" is public or Patreon-gated.
- Determine Social Dispatcher posting frequency.

## 18) The Vision

> "My goal is to make muffinpanrecipes super entertaining and have the AI personalities - that's the real content - but we're doing it through a recipe website."
>
> — Erik, January 4, 2026

This isn't an AI tool. It's a **living creative studio** that happens to produce recipes. A reality show where the cast improves over time and the audience watches it happen through the lens of muffin pan meals.

Procedurally generated serialized content with emotional coherence.

---

*PRD Version: 1.0*
*Created: January 19, 2026*
*Author: Claude Code (Opus 4.5) - Super Manager*
