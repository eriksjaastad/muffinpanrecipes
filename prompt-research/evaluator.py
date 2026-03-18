"""Locked evaluator — DO NOT MODIFY during experiments.

Wraps the existing QA scorer and judge into a single scoring interface.
Returns both QA (0-100) and judge (1-10) scores for a set of messages.
"""

from __future__ import annotations

import json
import re
from typing import Any

from backend.utils.model_router import generate_response


def score_qa(messages: list[dict], personas: dict[str, dict[str, Any]], concept: str = "") -> dict[str, Any]:
    """Run the QA scorer on a list of messages.

    Imports score_quality from the main script to stay in sync with production.
    Messages can be full-week or single-day — the scorer works on whatever it gets.
    """
    # Import here to avoid circular imports and to always use the latest scorer
    from scripts.simulate_dialogue_week import Message, score_quality

    # Convert dicts to Message objects if needed
    msg_objects = []
    for m in messages:
        if isinstance(m, dict):
            msg_objects.append(Message(
                day=m["day"],
                stage=m["stage"],
                character=m["character"],
                message=m["message"],
                timestamp=m.get("timestamp", ""),
                model=m.get("model", ""),
            ))
        else:
            msg_objects.append(m)

    return score_quality(msg_objects, personas, concept=concept)


def score_judge(
    messages: list[dict],
    concept: str,
    target_day: str,
    frozen_history: list[dict] | None,
    judge_model: str = "anthropic/claude-sonnet-4-6",
) -> dict[str, Any]:
    """Run the judge model on a single day's dialogue.

    Returns a dict with 'score' (1-10) and 'verdict' (full text).
    The judge sees frozen history as context but only grades the target day.
    """
    # Build context from frozen history
    history_text = ""
    if frozen_history:
        history_lines = []
        current_day = None
        for m in frozen_history:
            if m["day"] != current_day:
                current_day = m["day"]
                history_lines.append(f"\n--- {current_day.upper()} ---")
            history_lines.append(f"{m['character']}: {m['message']}")
        history_text = "\n".join(history_lines)

    # Build target day transcript
    target_lines = []
    for m in messages:
        target_lines.append(f"{m['character']}: {m['message']}")
    target_text = "\n".join(target_lines)

    system_prompt = (
        "You are a dialogue quality judge for a fictional workplace sitcom set at a muffin pan recipe website. "
        "Five characters work together: Margaret Chen (head baker), Stephanie 'Steph' Whitmore (creative director), "
        "Julian Torres (art director), Marcus Reid (copywriter), Devon Park (site architect).\n\n"
        "You evaluate dialogue quality on a 1-10 scale. Be precise — use the full range.\n"
        "1-2: Broken (hallucinations, character breaks, incoherent)\n"
        "3-4: Weak (flat, no tension, characters sound alike)\n"
        "5-6: Acceptable (functional but unremarkable)\n"
        "7-8: Good (distinct voices, natural tension, specific details)\n"
        "9-10: Excellent (could publish as-is, memorable moments)\n\n"
        "Evaluate on these criteria:\n"
        "1. CHARACTER VOICE — Do characters sound distinct and consistent?\n"
        "2. TENSION — Is there authentic disagreement or friction?\n"
        "3. SPECIFICITY — Are details concrete and recipe-relevant?\n"
        "4. NATURALNESS — Does it read like real coworkers, not AI?\n"
        "5. CONTINUITY — Does it connect to prior days (if any)?\n"
        "6. BOOKENDS — Does the day open and close naturally?\n"
        "7. HALLUCINATIONS — Any fabricated ingredients, techniques, or references?\n\n"
        "IMPORTANT: Respond with ONLY a JSON object. No other text.\n"
        "Format: {\"score\": <1-10>, \"reasoning\": \"<2-3 sentences>\", "
        "\"strengths\": [\"<strength>\"], \"issues\": [\"<issue>\"]}"
    )

    context_block = ""
    if history_text:
        context_block = f"PRIOR DAYS (context only — do NOT grade these):\n{history_text}\n\n"

    prompt = (
        f"Recipe concept: {concept}\n\n"
        f"{context_block}"
        f"TARGET DAY TO EVALUATE — {target_day.upper()}:\n{target_text}\n\n"
        "Score this day's dialogue. Return JSON only."
    )

    raw = generate_response(
        prompt=prompt,
        system_prompt=system_prompt,
        model=judge_model,
        temperature=0.3,
    ).strip()

    # Parse JSON from response (handle markdown code blocks)
    json_match = re.search(r'\{[^{}]*"score"[^{}]*\}', raw, re.DOTALL)
    if json_match:
        try:
            result = json.loads(json_match.group())
            return {
                "score": int(result.get("score", 5)),
                "reasoning": result.get("reasoning", ""),
                "strengths": result.get("strengths", []),
                "issues": result.get("issues", []),
                "raw": raw,
            }
        except (json.JSONDecodeError, ValueError):
            pass

    # Fallback: try to extract just a number
    num_match = re.search(r'"score"\s*:\s*(\d+)', raw)
    score = int(num_match.group(1)) if num_match else 5

    return {
        "score": score,
        "reasoning": "Failed to parse structured response",
        "strengths": [],
        "issues": [],
        "raw": raw,
    }


def evaluate(
    messages: list[dict],
    personas: dict[str, dict[str, Any]],
    concept: str,
    target_day: str,
    frozen_history: list[dict] | None = None,
    judge_model: str = "anthropic/claude-sonnet-4-6",
    run_judge: bool = True,
) -> dict[str, Any]:
    """Combined evaluation: QA scorer + Judge.

    Returns dict with qa_score (0-100), judge_score (1-10), and combined_score.
    Set run_judge=False for fast iterations (QA only).
    """
    qa = score_qa(messages, personas, concept=concept)
    qa_score = qa["score"]

    judge_result = None
    judge_score = None
    combined = float(qa_score)

    if run_judge:
        judge_result = score_judge(
            messages=messages,
            concept=concept,
            target_day=target_day,
            frozen_history=frozen_history,
            judge_model=judge_model,
        )
        judge_score = judge_result["score"]
        # Blend: QA on 0-100 scale, judge on 1-10 scaled to 0-100
        # 60% QA (cheap, structural) + 40% Judge (expensive, semantic)
        combined = 0.6 * qa_score + 0.4 * (judge_score * 10)

    return {
        "qa_score": qa_score,
        "judge_score": judge_score,
        "combined_score": round(combined, 2),
        "qa_details": qa,
        "judge_details": judge_result,
    }
