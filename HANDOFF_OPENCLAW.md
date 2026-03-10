# Muffin Pan Recipes — OpenClaw Handoff

> **Last updated:** 2026-03-10
> **Handoff from:** Erik + Claude (Architect)
> **Handoff to:** OpenClaw (Operations Manager)

## What This Project Is

A recipe website at **https://muffinpanrecipes.com** where 5 AI characters collaborate on a new muffin-tin recipe each week. The process is automated via Vercel cron jobs that fire Mon-Sun, generating dialogue, images, and a recipe page that grows progressively through the week.

**The characters:** Margaret Chen (Head Baker), Marcus Reid (Copywriter), Steph Whitmore (Project Manager), Julian Torres (Art Director), Devon Park (Site Architect).

---

## Current State (as of 2026-03-10)

### What's Working
- **Cron pipeline:** 7 daily stages fire automatically (Mon-Sat 2:30pm UTC, Sun midnight UTC)
- **Episode storage:** JSON saved to Vercel Blob after each stage
- **Progressive page rendering:** Each stage regenerates an HTML page at `/this-week`
- **Main page teaser:** Fetches latest conversation preview, links to `/this-week`
- **Judge system:** Opus 4.6 reviews each day's dialogue, retries 2x on FAIL
- **Photography:** Wednesday generates 3 rounds of AI images via Stability AI
- **W11 is live:** Monday fired successfully, Tuesday cron will auto-fire

### What's NOT Working / Not Connected
- **Discord webhook:** Points to Discord but we're moving to Slack. Needs rewiring.
- **Newsletter:** Commented out in HTML. No backend. Not a priority.
- **Social accounts:** Not set up. See task #5088 for the plan.
- **Some tasks reference fixed bugs:** #5039 (blob storage), #5038 (NoneType), #5042 (send2trash) — these are FIXED but still show as open in the Kanban.

---

## Infrastructure

### Hosting
- **Platform:** Vercel (Pro plan, $20/mo)
- **Team:** erik-sjaastads-projects
- **Project ID:** `prj_zQgUD5QXAc1OHpgyzyTlbqxvN1Ww`
- **Domain:** muffinpanrecipes.com

### Secrets (Doppler)
- **Project:** `muffinpanrecipes`
- **Configs:** `prd` (production), `stg` (staging), `dev` (local)
- **Key secrets:** `CRON_SECRET`, `BLOB_READ_WRITE_TOKEN`, `STABILITY_API_KEY`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`
- **Model config:** `DIALOGUE_MODEL`, `RECIPE_MODEL`, `JUDGE_MODEL` (in Doppler prd)

### Vercel Blob Store
- **Store ID:** `store_GtcZMjYsc51nH8fq` (iad1 region)
- **Public URL base:** `https://gtczmjysc51nh8fq.public.blob.vercel-storage.com/`
- **Content:** `episodes/`, `images/`, `pages/`, `test/`

### Key URLs
| URL | What |
|-----|------|
| https://muffinpanrecipes.com | Main site |
| https://muffinpanrecipes.com/this-week | Current episode page |
| https://muffinpanrecipes.com/api/episodes/teaser | Teaser JSON for main page |
| https://muffinpanrecipes.com/api/cron/{day} | Cron endpoints (auth required) |

---

## Monitoring Responsibilities (Phase 1)

### Daily Check: Did the Cron Fire?

After each scheduled cron time (2:30pm UTC Mon-Sat, midnight UTC Sun), verify:

```bash
# Check if today's stage produced data
doppler run --project muffinpanrecipes --config prd -- uv run python -c "
from backend.storage import storage
ep = storage.load_episode('$(python3 -c "from datetime import datetime,timezone; n=datetime.now(timezone.utc); i=n.isocalendar(); print(f\"{i.year}-W{i.week:02d}\")")')
if ep:
    stages = [d for d in ['monday','tuesday','wednesday','thursday','friday','saturday','sunday'] if ep.get('stages',{}).get(d,{}).get('status')=='complete']
    print(f'Completed stages: {stages}')
else:
    print('NO EPISODE FOUND')
"
```

### If a Stage Didn't Fire

1. **Check Vercel function logs** (via dashboard or CLI)
2. **Retry the stage:**
   ```bash
   doppler run --project muffinpanrecipes --config prd -- \
     uv run python scripts/run_full_week.py --days <day>
   ```
3. If retry fails, **alert Erik** with the error message

### If Judge Fails

The judge retries 2x automatically. If all retries fail:
- A Discord notification is sent (moving to Slack)
- The episode is paused at that stage
- **Don't retry automatically** — flag for Erik to review the dialogue quality

### Weekly Verification (Sunday night)

After Sunday's cron fires:
1. Verify `/this-week` shows the complete page with all 7 days
2. Verify the recipe card has ingredients + instructions
3. Verify hero image is present (from Wednesday)
4. Check the main page teaser is showing

---

## How to Run Things

### Preflight (always do this first)
See `OPENCLAW_PREFLIGHT.md` — verify you're in the right directory and secrets are available.

### Fire a Specific Day
```bash
doppler run --project muffinpanrecipes --config prd -- \
  uv run python scripts/run_full_week.py --days monday
```

### Fire Multiple Days
```bash
doppler run --project muffinpanrecipes --config prd -- \
  uv run python scripts/run_full_week.py --days monday,tuesday,wednesday
```

### Run a Full Test Week (doesn't affect production)
```bash
doppler run --project muffinpanrecipes --config prd -- \
  uv run python scripts/run_full_week.py --test
```

### Clean Up Test Artifacts
```bash
doppler run --project muffinpanrecipes --config prd -- \
  uv run python scripts/run_full_week.py --cleanup
```

### Check Episode State
```bash
doppler run --project muffinpanrecipes --config prd -- \
  uv run python -c "
from backend.storage import storage
import json
ep = storage.load_episode('2026-W11')  # change week as needed
print(json.dumps({k: v for k, v in ep.items() if k != 'stages'}, indent=2))
for day, stage in ep.get('stages', {}).items():
    print(f'{day}: {stage.get(\"status\", \"?\")}, dialogue: {len(stage.get(\"dialogue\", []))} msgs')
"
```

---

## Open Tasks for OpenClaw

### Can Do Now (operational)
| Task | Description | Priority |
|------|-------------|----------|
| #5083 | ~~Add character titles~~ — **DONE** as of 2026-03-10 | Close it |
| #5043 | Set MUFFINPAN_ADMIN_BASE_URL in Doppler | Quick fix |
| #5039 | ~~Blob storage stub~~ — **FIXED** | Close it |
| #5038 | ~~NoneType error~~ — **FIXED** | Close it |
| #5042 | ~~send2trash crash~~ — **FIXED** | Close it |
| #5088 | Social accounts setup (Instagram + Facebook) | Research task |
| #5080 | Dialogue quality tuning (prompt tweaks) | Creative task |

### Close These (already done)
- #5039, #5038, #5042, #5083 — all fixed, mark as done

### Don't Touch (need Architect/Erik)
- #5055 and blocked tasks — full lifecycle test, needs architectural decisions
- #5032, #5030, #5029, #5028, #5027 — character memory experiments, need design direction
- Anything involving schema changes, new API providers, or infrastructure changes

---

## First Task: Set Up Slack Notifications

**This is your onboarding task, OpenClaw.** Do this before anything else so you have a channel to report to.

### Steps
1. Create a `#muffinpanrecipes` channel in Erik's Slack workspace
2. Create a Slack Incoming Webhook for that channel (Slack API → Apps → Incoming Webhooks)
3. Add the webhook URL to Doppler: `doppler secrets set SLACK_WEBHOOK_URL '<url>' --config prd --project muffinpanrecipes`
4. Refactor `backend/utils/discord.py`:
   - Rename to `backend/utils/notifications.py`
   - Replace Discord webhook format with Slack webhook format (Slack uses `{"text": "..."}` or Block Kit)
   - Update the import in `backend/admin/cron_routes.py` (currently `from backend.utils.discord import notify_judge_failure`)
   - Keep the function signature the same so nothing else breaks
5. Test by triggering a judge failure (or just POST to the webhook manually)
6. Remove `DISCORD_WEBHOOK_URL` from Doppler once Slack is confirmed working

### Notification Rules — READ THIS CAREFULLY
- **Only notify on STATE CHANGES.** If nothing changed, say nothing.
- **Never send periodic "all clear" messages.** Erik does not want to hear from you unless something happened.
- **No duplicate notifications.** If you already reported Tuesday failed, don't report it again until you've retried and have a new result.
- **Batch when possible.** If Monday and Tuesday both completed while Erik was away, send ONE message with both, not two.

### What Should Trigger Notifications
- **Stage completion (daily):** "Monday complete: 8 messages, judge PASS"
- **Stage failure:** "Tuesday FAILED: timeout after 120s"
- **Judge failure (urgent):** "Judge rejected Wednesday after 3 attempts — episode paused"
- **Weekly summary (Sunday):** "W11 complete: Sunday-Prep Veggie Egg & Potato Muffin Pan Meal, 7/7 stages"
- **Cron no-show:** If a stage was expected but blob didn't grow within 30 min of scheduled time

### Slack Message Format
Keep it scannable. Example:
```
:white_check_mark: *muffinpanrecipes* — Monday complete
Recipe: Mini Shepherd Pies | 8 messages | Judge: PASS
```
```
:x: *muffinpanrecipes* — Tuesday FAILED
Error: Request timed out after 120s
Episode: 2026-W11 | Retry with: `run_full_week.py --days tuesday`
```

---

## Hard Lessons (Read These)

These are in memory and Project-workflow.md but worth repeating:

1. **Vercel crons send GET, not POST.** All routes must accept both methods.
2. **Silent fallbacks kill.** If something returns a default instead of failing loudly, you won't know it's broken for weeks.
3. **Test what the platform sends, not what your test client sends.** Our test harness used POST; Vercel sends GET. Tests passed, production 405'd.
4. **CDN staleness is real.** Vercel Blob CDN can serve stale content for 10-30s after overwrite. We use in-memory caches to work around this.
5. **Check `.vercelignore` when imports fail on Lambda.** Excluded files cause silent ModuleNotFoundError.
6. **Build minutes are 95% of Vercel cost.** Batch pushes to reduce builds.

---

## File Map (Key Files Only)

| File | Purpose |
|------|---------|
| `backend/admin/cron_routes.py` | All 7 cron stage handlers |
| `backend/admin/episode_routes.py` | `/this-week` and `/api/episodes/teaser` |
| `backend/publishing/episode_renderer.py` | Renders episode JSON → HTML page |
| `backend/storage.py` | Blob storage abstraction (save/load episodes, pages, images) |
| `backend/config.py` | Model names, feature flags, env config |
| `scripts/run_full_week.py` | Manual pipeline runner (fires stages via HTTP) |
| `scripts/simulate_dialogue_week.py` | Dialogue generation engine |
| `src/index.html` | Main page (static + JS for teaser) |
| `vercel.json` | Routes, crons, build config |
| `CREATIVE_BIBLE.md` | Character bios, voice guidelines, content rules |
