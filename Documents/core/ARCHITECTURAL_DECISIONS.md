# Architectural Decisions - Muffin Pan Recipes

## AD 001: Static Site Architecture
**Status:** Accepted
**Date:** 2026-01-03
**Context:** We need a high-speed, mobile-first experience that is easy to deploy and maintain on Dreamhost.
**Decision:** We will use a static site approach (HTML/Tailwind) for the initial prototype. AI-generated recipes will be stored as Markdown files and rendered into HTML.
**Consequences:** 
- Extremely fast load times.
- No database management required initially.
- SEO friendly out of the box.

## AD 002: Dreamhost Deployment via GitHub Actions
**Status:** Accepted
**Date:** 2026-01-03
**Context:** Automated deployment is required to maintain the "Clean Slate" philosophy of rapid iteration.
**Decision:** Use SSH + Rsync via GitHub Actions to deploy the `src/` folder to Dreamhost.
**Consequences:** 
- 0-manual-step deployment.
- Requires SSH keys to be configured in GitHub Secrets (`DREAMHOST_SSH_KEY`).

## AD 003: "No-Fluff" UI Design
**Status:** Accepted
**Date:** 2026-01-03
**Context:** Recipe sites are notoriously cluttered with ads and long preambles.
**Decision:** The UI will prioritize the "Jump to Recipe" button and put the core ingredients and instructions front and center.
**Consequences:** 
- Higher user satisfaction.
- Better mobile experience.
- Differentiator from traditional food blogs.

