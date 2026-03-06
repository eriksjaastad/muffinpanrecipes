# Muffin Pan Recipes - Test Suite (`tests/`)

The Muffin Pan Recipes project uses a dual testing approach to ensure both technical correctness and character consistency.

## 🧪 Testing Strategies

1.  **Unit Tests (`pytest`)**: Standard tests for individual components (e.g., `test_auth.py`, `test_message_system.py`).
2.  **Property-Based Testing (`Hypothesis`)**: Uses the `hypothesis` library to test universal properties by generating thousands of varied inputs (e.g., `test_agent_properties.py`).
3.  **End-to-End (E2E) Tests**: Full pipeline simulations located in `tests/e2e/`.

## 📂 Key Test Files

- `test_agent_behaviors.py`: Verifies personality-driven agent behavior.
- `test_art_director_image_pipeline.py`: Tests the image generation/harvest loop.
- `test_recipe_pipeline.py`: Validates the full 12-step recipe production process.
- `test_publishing_pipeline.py`: Ensures Markdown to static-HTML transformation works correctly.

## 🚀 Running Tests

```bash
# Run all tests
uv run pytest

# Run with coverage
uv run pytest --cov=backend

# Run only specific tests
uv run pytest tests/test_sanity.py
```

## 📋 Property Test Map

Property tests reference specific architectural requirements using this format:
```python
# Feature: ai-creative-team, Property N: [Property Name]
```
These link back to the design requirements defined in the [backend/README.md](../backend/README.md).
