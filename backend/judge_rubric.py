"""The dialogue judge's shared rubric: one source for production and the lab (#8149).

Production's single-transcript judge (backend/admin/cron_routes.py `_JUDGE_SYSTEM_PROMPT`)
and the conversation lab's pairwise judge (scripts/conversation_lab.py
`PAIRWISE_JUDGE_SYSTEM_PROMPT`) are both assembled from these pieces, so the two cannot
drift apart on who the characters are or what gets scored.

Byte stability matters: the lab records each judge prompt's SHA-256 with its results, and
`rejudge` refuses a source judged under a different prompt. tests/test_judge_rubric.py pins
both assembled prompts.
"""

CHARACTER_RULES = (
    "CHARACTER RULES:\n"
    "- Margaret: Blunt, short sentences, zero fluff, standards enforcer\n"
    "- Steph: Warm, diplomatic, NOT a nervous intern\n"
    "- Julian: Visual thinker, theatrical, cares about light/composition\n"
    "- Marcus: Literary, verbose, metaphor-heavy\n"
    "- Devon: Efficient, understated, speaks only when needed\n"
    "- Ria: Direct, platform-savvy, thinks in hooks and engagement, impatient with process\n\n"
)

# The 8 dimensions the production judge scores; its JSON schema uses exactly these keys.
JUDGE_DIMENSIONS: tuple[str, ...] = (
    "title_fidelity",
    "arc_resolution",
    "voice_distinctiveness",
    "technical_credibility",
    "natural_progression",
    "promise_delivery",
    "turn_taking",
    "cast_coverage",
)

JUDGE_SCORE_RANGE = (1, 5)
