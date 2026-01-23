# AI Creative Team Backend

Python-based multi-agent orchestration system for Muffin Pan Recipes.

## Overview

This backend implements five AI personalities that collaborate to produce muffin tin recipes:

- **Baker** - 50s traditionalist who's skeptical of trendy ingredients
- **Creative Director** - 28yo, first leadership role, well-intentioned but pressured
- **Art Director** - Pretentious art school grad & failed Instagram influencer
- **Editorial Copywriter** - Failed novelist who over-writes everything
- **Site Architect** - Lazy but competent college grad who lied on resume

## Project Structure

```
backend/
├── core/              # Core framework components
│   ├── agent.py       # Base Agent class
│   ├── personality.py # Personality configuration system
│   ├── task.py        # Task definitions and results
│   └── types.py       # Common type definitions
├── agents/            # Individual agent implementations (TBD)
├── messaging/         # Inter-agent messaging system (TBD)
├── memory/            # Agent memory and experience tracking (TBD)
├── pipeline/          # Recipe production pipeline (TBD)
└── utils/             # Logging, errors, and utilities
```

## Development

### Setup

```bash
# Install dependencies with uv
uv sync

# Run tests
uv run pytest

# Run tests with coverage
uv run pytest --cov=backend tests/
```

### Testing Strategy

The project uses a dual testing approach:

1. **Unit Tests** - Test specific components and edge cases
2. **Property-Based Tests** (Hypothesis) - Test universal properties with 100+ iterations

Each property test references its corresponding design document property using:
```python
# Feature: ai-creative-team, Property N: [Property Name]
```

## Core Concepts

### Personality-Driven Behavior

Every agent has a `PersonalityConfig` that defines:
- **Core Traits** - Base personality characteristics (0.0-1.0 scale)
- **Backstory** - Character background and history  
- **Communication Style** - How the agent speaks and writes
- **Quirks** - Unique behavioral patterns
- **Triggers** - Things that cause strong reactions

These traits influence:
- Task execution approach
- Communication style
- Emotional responses
- Decision-making patterns

### Agent Workflow

1. Agent receives a `Task`
2. Consults `Memory` for relevant context
3. `Personality` influences approach
4. Executes task with personality-driven modifications
5. Records experience with emotional response

## Current Status

✅ **Task 1 Complete**: Project structure and core interfaces
- Python project configured with `uv` and `pyproject.toml`
- Base classes defined (`Agent`, `PersonalityConfig`, `Task`, `TaskResult`)
- Testing framework set up with pytest + hypothesis
- Logging and error handling configured
- Initial sanity tests passing (4/4)

✅ **Task 2 Complete**: Agent Framework & Personality System  
- `AgentMemory` class with emotion-driven storage
- `MessageHandler` for inter-agent communication
- Ollama integration client (ready for MCP)
- Enhanced `Agent` base class with 5-step task processing
- Property tests for personality persistence (100+ iterations)
- All tests passing (11/11)

✅ **Task 3 Complete**: Individual Agent Implementations
- **Margaret Chen (Baker)** - 54yo traditionalist with trigger-based muttering
- **Steph Whitmore (Creative Director)** - 28yo anxious leader who apologizes constantly
- **Julian Torres (Art Director)** - 26yo pretentious artist taking 47 shots per recipe
- **Marcus Reid (Copywriter)** - 31yo failed novelist writing 800-word muffin backstories
- **Devon Park (Site Architect)** - 23yo who automated his job in week one
- Property test for Baker recipe creation
- Personality-driven behavior tests for all agents
- All tests passing (15/15)

## The Creative Team

### Margaret Chen - The Baker
- **Age**: 54 | **Traits**: Traditionalism (0.9), Perfectionism (0.85), Grumpiness (0.7)
- **Background**: James Beard-nominated pastry chef who lost her restaurant in 2008
- **Quirks**: Mutters under breath, measures twice, brings coffee for everyone at 6am
- **Triggers**: Matcha, activated charcoal, edible flowers, "just eyeball it"
- **Secret**: Still loves the science of baking, keeps notebook of perfected ratios

### Stephanie "Steph" Whitmore - The Creative Director  
- **Age**: 28 | **Traits**: Anxiety (0.9), People-Pleasing (0.8), Insecurity (0.85)
- **Background**: Trust fund kid in first leadership role, got job through connections
- **Quirks**: Apologizes before feedback, rewrites emails 6 times, buys team coffee
- **Triggers**: Being questioned, deadlines without consensus, comparisons to other CDs
- **Secret**: Actually has good creative instincts, just doesn't trust them

### Julian Torres - The Art Director
- **Age**: 26 | **Traits**: Pretentiousness (0.85), Aesthetic Obsession (0.95), Insecurity (0.8)
- **Background**: RISD grad whose 47k Instagram followers weren't enough to pay rent
- **Quirks**: Takes 47+ shots, references unknown photographers, wears all black to shoots
- **Triggers**: "Just use a phone camera", stock photos, comparison to food bloggers
- **Secret**: Actually talented at making people want to cook, which feels too small

### Marcus Reid - The Editorial Copywriter
- **Age**: 31 | **Traits**: Literary Pretension (0.9), Bitterness (0.75), Verbosity (0.95)
- **Background**: Columbia MFA whose novel sold 347 copies (40 to family)
- **Quirks**: Uses "whom" in Slack, references Proust, brings French press to office
- **Triggers**: "Keep it simple", word count limits, "people don't read descriptions"
- **Secret**: 47,000 people read his muffin descriptions; occasionally brilliant when he stops trying

### Devon Park - The Site Architect
- **Age**: 23 | **Traits**: Laziness (0.75), Competence (0.8), Impostor Syndrome (0.7)
- **Background**: Fresh grad who lied on resume about enterprise experience
- **Quirks**: Automated job in month one, shows up late, responds to Slack hours later
- **Triggers**: Being asked to explain what he doesn't know yet, documentation requests
- **Secret**: His "laziness" is strategic efficiency; site runs better than it needs to

## Next Steps

**✅ Checkpoint at Task 4**: All agent personalities working correctly!

Ready for:
- Task 5: Message System implementation
- Task 6: Agent Memory System enhancement  
- Task 7: Recipe Pipeline Controller
- Task 8: Checkpoint - verify core pipeline functionality
