# AI Creative Team - Core Framework (`backend/core/`)

This directory contains the foundational components for the multi-agent orchestration system.

## 🏗️ Core Components

- **`agent.py`**: Defines the base `Agent` class.
  - Implements the **5-step task processing loop**:
    1. `receive_task()` - Context initialization.
    2. `consult_memory()` - Retrieve relevant past experiences.
    3. `influence_personality()` - Apply character-driven modifications to the prompt.
    4. `execute_task()` - LLM invocation (via `model_router`).
    5. `record_experience()` - Store the outcome and emotional response.
- **`personality.py`**: The personality configuration system.
  - Manages `Core Traits` (Traditionalism, Anxiety, etc. on a 0.0-1.0 scale).
  - Handles `Triggers` and `Quirks` that influence agent output.
- **`task.py`**: Standardized task and result definitions.
  - Ensures consistent data flow between different agent types.
- **`types.py`**: Common type definitions and enums for the system.

## 🛠️ Implementation Details

### Personality-Driven Logic
The `Agent` class uses `PersonalityConfig` to wrap every LLM request. This is what prevents the agents from sounding like generic chatbots and ensures they maintain their specific "grumpy baker" or "anxious director" vibes.

### Memory & Context
Agents aren't just stateless functions. The `AgentMemory` (in `../memory/`) allows them to remember previous interactions, which is critical for the "Creative Tension" loop.
