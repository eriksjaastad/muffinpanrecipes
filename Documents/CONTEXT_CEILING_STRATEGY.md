---
tags:
  - p/muffinpanrecipes
  - type/architecture
  - domain/ai-strategy
status: #status/active
created: 2026-01-25
---

# Context Ceiling Strategy

## Overview

Context ceiling refers to the maximum amount of text (measured in tokens) that can be safely sent to an AI model in a single request. Exceeding this limit causes failures, degraded quality, or errors.

This document tracks context limits across the muffinpanrecipes project and strategies for staying within them.

---

## Current Limits

### Local Models (Ollama)

| Setting | Value | Location |
|---------|-------|----------|
| Default max tokens | 2000 | `backend/utils/ollama.py:18` |
| Actual limit | ~4096 | Model-dependent (Qwen 2.5, DeepSeek-R1) |
| Safety margin | 20% | Reserve for response generation |

**Current strategy:** Set conservatively to 2000 to ensure safety. Can be increased per-request if needed.

### Cloud Models

| Model | Context | Notes |
|-------|---------|-------|
| OpenAI GPT-4 | 128,000 tokens | Expensive; use Ollama first |
| Gemini 3 Flash | ~200,000 tokens | Rare usage for batch operations |

---

## When Limits Are Exceeded

### Problem: Recipe Generation Failing

**Symptoms:**
- Generation times out
- Error: "Context length exceeded"
- Response is truncated or malformed

**Root Causes:**
1. **Single recipe too large** - Very long instructions or many ingredients
2. **Batch operation** - Trying to generate 10 recipes in one request
3. **System prompt bloat** - Too much context in the prompt prefix

### Solution: Decomposition

#### For Single Recipes
```python
# DON'T: Send entire recipe to model
result = generate_recipe(full_recipe_json, max_tokens=2000)

# DO: Split into steps
title = generate_title(category, style)
ingredients = generate_ingredients(servings, prep_time)
instructions = generate_instructions(ingredients, technique)
```

#### For Batch Operations
```python
# DON'T: Generate 10 recipes in one call
results = generate_recipes(recipes=[r1, r2, ..., r10])

# DO: Generate sequentially with token counting
for recipe in recipes:
    if token_count(recipe) < limit:
        generate_recipe(recipe)
    else:
        log_oversized_recipe(recipe)
```

---

## Monitoring & Measurement

### Token Counting

**Before sending to API:**
```python
from backend.utils.ollama import estimate_tokens

# Estimate tokens in prompt
token_count = estimate_tokens(prompt)
if token_count > limit:
    # Truncate or decompose
    pass
```

**Current implementation:** Manual observation during generation. No automated counters yet.

### Logging

All model invocations log:
- Request token count
- Response token count
- Execution time

Check logs for patterns:
```bash
grep "token" logs/*.log | grep -i recipe
```

---

## Future Improvements

### 1. Token Counting Before API Calls
**Status:** Not implemented
**Effort:** Low (1-2 hours)
**Benefit:** Prevents timeouts by catching oversized requests early

```python
def safe_generate_recipe(recipe, model="ollama"):
    """Generate recipe with token ceiling protection."""
    tokens = estimate_tokens(recipe)
    if tokens > SAFE_LIMIT:
        raise TokenCeilingExceeded(f"{tokens} > {SAFE_LIMIT}")
    return generate_recipe(recipe, model)
```

### 2. Batching Strategy (Map-Reduce)
**Status:** Not implemented
**Effort:** Medium (4-6 hours)
**Benefit:** 10x throughput for batch operations

```
INPUT: [Recipe1, Recipe2, ..., Recipe100]
  ↓
SPLIT into chunks of N recipes (N = limit / avg_recipe_size)
  ↓
MAP: Generate each chunk in parallel (or sequentially)
  ↓
REDUCE: Aggregate results
  ↓
OUTPUT: [Generated1, Generated2, ..., Generated100]
```

### 3. Context Compression
**Status:** Not implemented
**Effort:** High (8-10 hours)
**Benefit:** 30-50% reduction in tokens sent

Techniques:
- Remove duplicate ingredients across recipes
- Use abbreviations for common terms
- Compress instruction templates
- Strip non-essential metadata

### 4. Adaptive Limits
**Status:** Not implemented
**Effort:** Medium (3-4 hours)
**Benefit:** Automatic optimization based on available resources

```python
def adaptive_context_limit():
    """Adjust limit based on model and system state."""
    model = get_active_model()
    
    if model == "ollama_local":
        # Check available VRAM
        vram_gb = psutil.virtual_memory().available / 1e9
        return int(vram_gb * 250)  # ~250 tokens per GB
    
    elif model == "openai":
        # Check rate limit status
        return 128000  # Max for GPT-4
    
    return DEFAULT_LIMIT
```

---

## Testing & Validation

### Unit Tests
Currently in: `tests/test_recipe_pipeline.py`

```python
def test_recipe_within_token_limit():
    """Verify recipes don't exceed context ceiling."""
    recipe = generate_sample_recipe()
    tokens = estimate_tokens(json.dumps(recipe))
    assert tokens < TOKEN_LIMIT
```

### Integration Tests
- Batch generation with varying recipe counts
- Long instruction sets (200+ steps)
- Large ingredient lists (100+ items)

### Manual Testing Checklist
- [ ] Generate a single large recipe
- [ ] Generate a batch of 5 recipes
- [ ] Generate a batch of 20 recipes
- [ ] Test with long recipe titles and descriptions

---

## Related Documentation

- [Publishing Pipeline](../backend/publishing/pipeline.py) - Recipe publishing flow
- [Recipe Schema](RECIPE_SCHEMA.md) - Data structure definition
- [AI Strategy](../README.md#-ai-strategy) - Overall model selection strategy
- [LOCAL_MODEL_LEARNINGS](../../Documents/reference/LOCAL_MODEL_LEARNINGS.md) - Lessons from local model usage
