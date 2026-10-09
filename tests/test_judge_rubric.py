"""Production and the lab judge from one rubric, and neither prompt drifts (#8149).

The lab stores each judge prompt's SHA-256 with its results, and `rejudge` refuses a
source judged under a different prompt, so a byte change to either prompt must be a
deliberate decision: update the pin here and record it in EXPERIMENTS.md.
"""

import hashlib
import re

import scripts.conversation_lab as cl
from backend import judge_rubric
from backend.admin import cron_routes

PRODUCTION_PROMPT_SHA256 = "a41b5e607098c53deebc19cd65928bae3cdb5c9f1aed035e827b8f17d119acd3"
PAIRWISE_PROMPT_SHA256 = "c6570e4d93e3470eff97f779e4998b4aec0f8c04e6499f46c402c0af50d67329"


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def test_production_judge_prompt_is_pinned():
    assert _sha(cron_routes._JUDGE_SYSTEM_PROMPT) == PRODUCTION_PROMPT_SHA256


def test_lab_pairwise_judge_prompt_is_pinned():
    assert _sha(cl.PAIRWISE_JUDGE_SYSTEM_PROMPT) == PAIRWISE_PROMPT_SHA256


def test_both_prompts_carry_the_shared_character_rules():
    assert judge_rubric.CHARACTER_RULES in cron_routes._JUDGE_SYSTEM_PROMPT
    assert judge_rubric.CHARACTER_RULES in cl.PAIRWISE_JUDGE_SYSTEM_PROMPT


def test_production_schema_scores_exactly_the_shared_dimensions():
    schema = cron_routes._JUDGE_SYSTEM_PROMPT.split('{"scores": {', 1)[1].split("}", 1)[0]
    keys = tuple(re.findall(r'"([a-z_]+)": 1-5', schema))
    assert keys == judge_rubric.JUDGE_DIMENSIONS


def test_the_lab_uses_the_shared_dimensions_and_score_range():
    assert cl.JUDGE_DIMENSIONS is judge_rubric.JUDGE_DIMENSIONS
    assert cl._JUDGE_SCORE_RANGE == judge_rubric.JUDGE_SCORE_RANGE == (1, 5)
