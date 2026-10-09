"""Per-character episode memories written after Sunday publishes (#5027, #6968, #8148).

Moved verbatim from backend/admin/cron_routes.py; cron_routes' Sunday handler calls
`_generate_episode_memories` from here. Tests that stub the memory model, storage or config
inside it patch THIS module.
"""

from __future__ import annotations

from datetime import datetime, timezone

from backend.config import config
from backend.storage import storage
from backend.utils.episode_integrity import DAY_ORDER
from backend.utils.logging import get_logger
from backend.utils.model_router import generate_response
from backend.utils.text_sanitize import sanitize_text

# The cron routes' own logger, so these log lines read exactly as before the split.
logger = get_logger("backend.admin.cron_routes")


# ---------------------------------------------------------------------------
# Per-character episode memories (#5027, redesigned in #6968 review round 3)
# ---------------------------------------------------------------------------

_CHAR_SLUG_OVERRIDES: dict[str, str] = {
    "Stephanie 'Steph' Whitmore": "steph-whitmore",
}


def _char_dir_slug(name: str) -> str:
    import re as _re
    if name in _CHAR_SLUG_OVERRIDES:
        return _CHAR_SLUG_OVERRIDES[name]
    return _re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def _generate_episode_memories(episode: dict, concept: str, *, dry_run: bool = False) -> dict[str, list[str]]:
    """Generate per-character memories from a completed episode (#6968).

    Called after Sunday publish. One LLM call per character (~100 tokens
    each). Each character's memory is ONE durable blob per week
    (character_memory/<slug>/<week>.json, storage.save_character_memory_week)
    — this function never reads existing memory before writing. There is no
    merge and nothing to make stale: a write is always the complete body for
    THIS week, PUT to its own key, so replaying the same week is an
    idempotent overwrite of only that key, and it is structurally
    impossible for this write to touch or drop any other week's blob
    (round-3 review finding 1 — the prior read-modify-write design could
    merge against a stale CDN-served body and silently undo a just-written
    week). Retention/legacy-fallback/dedup concerns are entirely a read-time
    concern of the prompt builder (scripts.simulate_dialogue_week
    ._load_memories_or_unavailable); this writer has none of that.

    With dry_run=True, no storage write happens; the return value still
    describes what each character's outcome would be. Used by
    scripts/repair_character_memory.py to default to a dry run.

    Returns {"saved": [...], "absent": [...], "failed": [...]} — character
    names bucketed by outcome, for the caller to fold into episode events.
    A per-character LLM or storage failure lands in "failed" and never
    raises past this function; a genuinely unexpected error (e.g. the
    roster lookup itself failing) is allowed to propagate so the caller's
    outer guard records a total failure instead of silently reporting an
    empty outcome.
    """
    all_dialogue: list[dict] = []
    for day in DAY_ORDER:
        day_data = episode.get("stages", {}).get(day, {})
        all_dialogue.extend(day_data.get("dialogue", []))

    outcome: dict[str, list[str]] = {"saved": [], "absent": [], "failed": []}

    # Full weekly roster (#6968): a cast member who produced no accepted
    # dialogue this week is an observable absence, not silently skipped.
    # Lazy import mirrors _get_run_simulation()'s pattern above.
    from scripts.simulate_dialogue_week import participants_for_day
    roster = sorted({name for day in DAY_ORDER for name in participants_for_day(day)})

    if not all_dialogue:
        logger.warning("No dialogue in episode — skipping memory generation")
        outcome["absent"] = roster
        return outcome

    # Group messages by character
    by_char: dict[str, list[str]] = {}
    for m in all_dialogue:
        char = m.get("character", "")
        if char:
            by_char.setdefault(char, []).append(f"[{m.get('day', '?')}] {' '.join((m.get('message') or '').split())}")

    week_label = episode.get("episode_id", "unknown")
    model = config.dialogue_model  # cheap model for summaries

    for char_name in roster:
        char_msgs = by_char.get(char_name)
        if not char_msgs:
            outcome["absent"].append(char_name)
            continue

        transcript_excerpt = "\n".join(char_msgs[-15:])

        prompt = (
            f"Summarize {char_name}'s week in 2 sentences.\n"
            f"Concept: {concept}\n\n"
            f"Their messages:\n{transcript_excerpt}\n\n"
            "Rules:\n"
            "- Write from their POV in third person past tense\n"
            "- Focus on relationships and emotions, NOT technical specs\n"
            "- Include one specific interpersonal moment\n"
            "- Keep it under 40 words total\n"
            "- Use plain hyphens and straight quotes only"
        )

        try:
            summary = generate_response(
                prompt=prompt,
                system_prompt="You write concise character summaries. Exactly 2 sentences, under 40 words.",
                model=model,
                temperature=0.4,
            ).strip()
            summary = sanitize_text(summary)
        except Exception as e:
            # TRIAGE (#6856): GENUINELY NON-FATAL, logged and recorded.
            # Character memories are flavour for next week's prompts; a gap
            # costs continuity, never correctness, and the publish already
            # happened by the time this runs. Recorded on the episode by
            # the caller so a missing memory is visible in the JSON rather
            # than only in a Lambda log nobody reads.
            logger.error(f"Memory generation failed for {char_name}: {type(e).__name__}: {e}")
            outcome["failed"].append(char_name)
            continue

        sentences = summary.split(". ")
        key_moment = sentences[-1].rstrip(".") + "." if len(sentences) > 1 else ""

        mem_entry = {
            "week": week_label,
            "episode_id": week_label,
            "concept": concept,
            "summary": summary,
            "key_moment": key_moment,
            "written_at": datetime.now(timezone.utc).isoformat(),
        }

        slug = _char_dir_slug(char_name)
        try:
            if not dry_run:
                storage.save_character_memory_week(slug, week_label, mem_entry)
            logger.info(f"{'Would save' if dry_run else 'Saved'} memory for {char_name}: {summary[:80]}")
            outcome["saved"].append(char_name)
        except Exception as e:
            # A write failure must never claim success (#6968) — recorded as
            # failed even though the summary above succeeded.
            logger.error(f"Memory write failed for {char_name}: {type(e).__name__}: {e}")
            outcome["failed"].append(char_name)

    return outcome
