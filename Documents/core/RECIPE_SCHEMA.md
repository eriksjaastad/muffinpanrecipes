# Recipe Schema (Markdown) - Version 1.1

All AI-generated recipes for Muffin Pan Recipes must follow this standard Markdown structure to ensure consistency, mathematical accuracy for pan sizes, and ease of parsing.

## File Naming Convention
`slug-of-recipe.md` (e.g., `mini-quiche-florentine.md`)

## Schema Template

```markdown
---
title: "Recipe Name"
date: 2026-01-03
tags: [Category, Prep Style, Dietary]
prep_time: "XX mins"
cook_time: "XX mins"
total_time: "XX mins"
yield: "12 standard muffins" # MUST specify "standard", "jumbo", or "mini"
base_layer_type: "Naked" # e.g., "Naked", "Bacon Wrap", "Tortilla Cup", "Paper Liner"
calories: "XXX kcal per muffin"
image: "/images/recipes/slug.jpg"
description: "A short, punchy 1-2 sentence description for SEO."
---

# Recipe Name

[Jump to Recipe](#recipe-card)

## Why Muffin Pans?
*A brief (1 paragraph max) explanation of why this recipe works perfectly in a muffin tin. Focus on portion control, texture, or speed.*

## Ingredients
### For the Base
- 250g (2 cups) All-purpose flour
- 5ml (1 tsp) Baking powder

### For the Mix-ins
- 100g (1/2 cup) Blueberries

## Instructions
1. **Preheat:** Preheat oven to [TEMP]°F ([TEMP]°C). [Greasing/Lining Instruction].
2. **Action Word:** Description of the step.
3. **Action Word:** Description of the step.

<div id="recipe-card">

## Quick Reference Card
- **Muffin Pan Size:** [Standard/Jumbo/Mini]
- **Liners vs. Grease:** [Recommendation]
- **Oven Temp:** [TEMP]°F
- **Total Time:** [TIME] mins
- **Dietary:** [List]

</div>

## Muffin-Specific Pro Tips
- **Tip 1:** e.g., "Fill cups only 3/4 full to prevent overflow."
- **Tip 2:** e.g., "Use a toothpick to check for doneness at 18 minutes."

## AI Generation Metadata
- **Model:** Gemini 3 Flash
- **Prompt Version:** 1.1
- **Generated On:** 2026-01-03
```

## Key Requirements
1. **The "Jump to Recipe" Link:** Must always be present at the top.
2. **Dual Measurements:** Always include Metric (g/ml) followed by Imperial (cups/oz) in parentheses.
3. **Yield Specificity:** Must explicitly state if it's for 12 standard, 6 jumbo, or 24 mini cups.
4. **No Intro Fluff:** Strictly forbidden to include personal stories or unrelated anecdotes.
5. **Bolded Key Actions:** Step-by-step instructions should start with a bolded action word.

## Related Documentation

- [[prompt_engineering_guide]] - prompt engineering
- [[ai_model_comparison]] - AI models
- [[performance_optimization]] - performance
- [[recipe_system]] - recipe generation
- [[muffinpanrecipes/README]] - Muffin Pan Recipes
