# Muffin Pan Recipes - Static Site Source (`src/`)

This directory contains the front-end source for the Muffin Pan Recipes platform. The site is a high-performance static site built with HTML and Tailwind CSS, deployed via Vercel.

## 📂 Directory Structure

- `recipes/` - Individual recipe pages (Markdown).
- `assets/` - Images, fonts, and global CSS.
  - `images/recipes/` - AI-generated food photography.
- `templates/` - HTML templates used by the build system.
- `index.html` - The main landing page.
- `recipes.json` - The canonical data source for the recipe index.

## 📋 Recipe Schema (v1.1)

All recipes must follow the standard Markdown structure:
- **US Measurements Only:** Cups, tablespoons, teaspoons, ounces, pounds, Fahrenheit.
- **Yield:** Must specify pan size (Standard, Jumbo, or Mini).
- **No Fluff:** Strictly forbidden to include long personal stories.
- **Jump to Recipe:** Always include a jump link at the top.

See `Documents/core/RECIPE_SCHEMA.md` for the full specification.

## 🎨 Visual Identity & Image Style

To maintain a consistent "Editorial Cookbook" aesthetic:
- **Lighting:** High-key, soft natural daylight from the side.
- **Composition:** 45-degree angle on a white marble or neutral stone surface.
- **Vessel:** Rustic, slightly weathered muffin tin.
- **Macro Focus:** Detail on food texture; shallow depth of field (f/2.8).
- **Negative Constraints:** No people, no text, no clutter, no "dark mode" lighting.

See `Documents/core/IMAGE_STYLE_GUIDE.md` for the full specification.

## 🛠️ Build Process

The site is "compiled" from templates and raw recipe data.
1. Recipes are generated/edited in `data/recipes/`.
2. `scripts/build_site.py` processes these into the `src/` directory.
3. Vercel serves the `src/` directory as the root.

## 🚀 Deployment

The `src/` directory is configured as the **Root Directory** in Vercel. 
- **Framework:** Other (Static Site)
- **Build Command:** (Handled by GitHub Actions or local pre-push scripts)
- **Output Directory:** `.` (relative to `src/`)
