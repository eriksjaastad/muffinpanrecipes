# PROGRESS.md — Session 2026-04-09

## What's Happening
Kanban cleanup and code hygiene session. No feature work.

## Decisions Made
- Sticking with Anthropic models — cancelled #5034 (Gemini provider). google-genai dep stays for Nano Banana image gen.
- Vercel auto-deploy remains OFF (manual deploy only).
- Failing tests are unacceptable — fixed all 8 failures this session.

## What Got Done
- Cancelled 18 dead/stale Kanban cards (33 → 15 remaining)
- Moved #5251 (image compression) to To Do / High priority
- Fixed missing `import os` bug in `backend/agents/copywriter.py`
- Removed dead `pytest-asyncio` dependency, regenerated requirements.txt
- Trashed 2 orphan scripts (verify_memory_persistence*.py)
- Registered 25 env vars in `pt info -p muffinpanrecipes`
- Fixed `test_art_director_image_pipeline` — updated stale variant names, paths, and output keys
- Marked 7 tests requiring API keys with `skipif(RUN_LIVE_PROVIDER_TESTS)`
- Test suite: 164 passed, 11 skipped, 0 failed

## Next Steps
- #5251: Image compression (High priority, To Do)
- #5466: Integrate Ria into dialogue schedule
- #5080: Dialogue quality tuning
- Commit this session's changes

## Don't Forget
- `test_art_director_image_pipeline` was deeply stale — variant names changed from editorial/action_steam/the_spread to macro_closeup/overhead_flatlay/hero_threequarter
- 11 skipped tests require `RUN_LIVE_PROVIDER_TESTS=true` + Doppler secrets to run
