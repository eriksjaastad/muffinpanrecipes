#!/usr/bin/env python3
"""Deterministic, LLM-free metrics over episode dialogue transcripts.

WHY: `scripts/simulate_dialogue_week.score_quality` is the production
heuristic grader that gates what ships, but it blends many signals into one
composite score. `scripts/conversation_lab.py`'s offline A/B experiments need
individual, orthogonal numbers instead - cast coverage, turn-to-turn
continuity, question/answer follow-through, cross-character phrase
repetition, length spread - so a nudge can be attributed to a specific lever
rather than read off one composite. Every function here is pure and
deterministic: no network calls, no LLM calls, so `conversation_lab.py
--dry-run` and this module's own test suite can run with zero paid API
calls.

This module stays import-light at module scope on purpose - it does NOT
import `scripts.simulate_dialogue_week` at the top of the file, so it stays
importable (and fast to import) even in a context that never needs the full
dialogue simulator. `legacy_quality()` is the one function that needs the
simulator's `score_quality`/`load_personas`/`Message`; it imports them lazily,
inside the function body.

Input shape: a list of message dicts, minimally {"character": str,
"message": str}, the same shape as an episode's `stages.<day>.dialogue`
list (see data/episodes/2026-W36.json, e.g. stages.monday.dialogue[0]) which
also carries "day", "stage", "timestamp", "model", "attachments".
"""

from __future__ import annotations

import re
from collections import Counter
from statistics import mean
from typing import Any

# Small stopword list used only to isolate "content words" for the
# continuity (Jaccard) and question/answer checks below. Deliberately short
# - this is not a general-purpose NLP stopword list, just enough noise
# removal that "the pan holds it" and "it holds the batter" register as
# related without every turn trivially overlapping on "the"/"it"/"a".
STOPWORDS: frozenset[str] = frozenset(
    {
        "a", "an", "the", "and", "or", "but", "if", "so", "to", "of", "in",
        "on", "for", "with", "is", "are", "was", "were", "be", "been",
        "being", "it", "its", "this", "that", "these", "those", "i", "you",
        "he", "she", "we", "they", "them", "his", "her", "our", "your",
        "their", "not", "no", "yes", "do", "does", "did", "can", "could",
        "will", "would", "should", "just", "as", "at", "by", "from", "up",
        "out", "about", "into", "over", "then", "than", "too", "very",
        "what", "which", "who", "how", "when", "where", "why", "there",
        "here", "all", "some", "one", "get", "got", "let", "us", "my", "me",
        "am", "have", "has", "had", "okay", "ok", "well", "like", "really",
    }
)

_WORD_RE = re.compile(r"[a-z0-9']+")
_PLAIN_WORD_RE = re.compile(r"\w+")


def _words(text: str) -> list[str]:
    """Lowercase word tokens, apostrophes kept (so "that's" stays one token)."""
    return _WORD_RE.findall((text or "").lower())


def _content_words(text: str) -> set[str]:
    """Word tokens with stopwords and single-character tokens removed."""
    return {w for w in _words(text) if w not in STOPWORDS and len(w) > 1}


def _first_name(character: str | None) -> str:
    """Normalise a character name to its first token.

    Matches the cast normalisation in backend/admin/cron_routes.py
    (`m.get("character", "?").split()[0]`, in `_judge_dialogue`, ~line 370)
    so cast_coverage compares against the same identity the judge and
    dialogue prompts use - e.g. "Stephanie 'Steph' Whitmore" -> "Stephanie".
    """
    name = (character or "?").strip()
    return name.split()[0] if name else "?"


def _ngrams(words: list[str], n: int) -> list[tuple[str, ...]]:
    if len(words) < n:
        return []
    return [tuple(words[i : i + n]) for i in range(len(words) - n + 1)]


def cast_coverage(expected: list[str], messages: list[dict[str, Any]]) -> dict[str, Any]:
    """Compare who was supposed to speak against who actually did.

    `expected` is normally `scripts.simulate_dialogue_week.participants_for_day`
    output (full names); both sides are reduced to first names before
    comparing, per `_first_name` above.
    """
    expected_norm = sorted({_first_name(e) for e in expected})
    present_raw = {_first_name(m.get("character")) for m in messages if m.get("character")}
    present = sorted(set(expected_norm) & present_raw)
    missing = sorted(set(expected_norm) - present_raw)
    unexpected = sorted(present_raw - set(expected_norm))
    ratio = (len(present) / len(expected_norm)) if expected_norm else 1.0
    return {
        "expected": expected_norm,
        "present": present,
        "missing": missing,
        "unexpected": unexpected,
        "ratio": round(ratio, 4),
    }


def adjacency_continuity(messages: list[dict[str, Any]]) -> dict[str, Any]:
    """Turn-to-turn continuity: does each line pick up a content word from the last?

    For every turn after the first, computes the stopword-filtered
    content-word Jaccard similarity against the immediately preceding turn
    (regardless of whether the same character spoke). `share_with_link` is
    the fraction of turns that share at least one content word with the
    turn before them - a cheap proxy for "responded to what was just said"
    vs. "stacked monologue" (the turn_taking judge dimension in
    backend/admin/cron_routes.py's `_JUDGE_SYSTEM_PROMPT`).
    """
    if len(messages) < 2:
        return {"mean_overlap": 0.0, "share_with_link": 0.0, "turn_count": 0}

    overlaps: list[float] = []
    linked = 0
    for i in range(1, len(messages)):
        prev_words = _content_words(messages[i - 1].get("message", ""))
        cur_words = _content_words(messages[i].get("message", ""))
        union = prev_words | cur_words
        intersection = prev_words & cur_words
        overlaps.append((len(intersection) / len(union)) if union else 0.0)
        if intersection:
            linked += 1

    return {
        "mean_overlap": round(mean(overlaps), 4),
        "share_with_link": round(linked / len(overlaps), 4),
        "turn_count": len(overlaps),
    }


def question_answer_rate(messages: list[dict[str, Any]]) -> dict[str, Any]:
    """Fraction of questions that get an actual answer.

    A turn counts as a "question" if its stripped text ends with '?'. It
    counts as "answered" only if the very next turn (a) comes from a
    DIFFERENT character and (b) shares at least one content word with the
    question. A question with no following turn (last message of the
    transcript) counts toward the denominator but can never be answered.
    """
    questions = 0
    answered = 0
    for i, msg in enumerate(messages):
        text = (msg.get("message") or "").strip()
        if not text.endswith("?"):
            continue
        questions += 1
        if i + 1 >= len(messages):
            continue
        nxt = messages[i + 1]
        if nxt.get("character") == msg.get("character"):
            continue
        if _content_words(text) & _content_words(nxt.get("message", "")):
            answered += 1

    rate = (answered / questions) if questions else 0.0
    return {"rate": round(rate, 4), "question_count": questions, "answered_count": answered}


def repeated_phrases(messages: list[dict[str, Any]]) -> dict[str, Any]:
    """Find recurring n-grams: the "Marcus's that's-the-story tic" detector.

    `cross_character_3grams`: 3-word phrases that recur (2+ occurrences)
    AND are used by 2+ distinct characters - a phrase leaking across voices
    rather than one character's habit.
    `per_character_4gram_tics`: 4-word phrases a single character repeats
    (2+ occurrences) within their own lines - a personal tic, independent of
    anyone else's dialogue.
    """
    cross_counts: Counter[tuple[str, ...]] = Counter()
    cross_speakers: dict[tuple[str, ...], set[str]] = {}
    per_char_counts: dict[str, Counter[tuple[str, ...]]] = {}

    for msg in messages:
        words = _words(msg.get("message", ""))
        char = _first_name(msg.get("character"))
        for gram in _ngrams(words, 3):
            cross_counts[gram] += 1
            cross_speakers.setdefault(gram, set()).add(char)
        for gram in _ngrams(words, 4):
            per_char_counts.setdefault(char, Counter())[gram] += 1

    cross_hits = [
        (" ".join(gram), count)
        for gram, count in cross_counts.items()
        if count >= 2 and len(cross_speakers.get(gram, ())) >= 2
    ]
    cross_hits.sort(key=lambda item: (-item[1], item[0]))

    per_char_tics: dict[str, list[tuple[str, int]]] = {}
    for char, counter in per_char_counts.items():
        tics = [(" ".join(gram), count) for gram, count in counter.items() if count >= 2]
        tics.sort(key=lambda item: (-item[1], item[0]))
        if tics:
            per_char_tics[char] = tics

    return {
        "cross_character_3grams": cross_hits[:20],
        "per_character_4gram_tics": per_char_tics,
    }


def length_stats(messages: list[dict[str, Any]]) -> dict[str, Any]:
    """Word-count spread, overall and per character.

    Word counting matches `simulate_dialogue_week.score_quality`'s own
    `len(re.findall(r"\\w+", m.message))` (line ~1359) so this number is
    directly comparable to that module's rhythm/length penalties.
    """
    if not messages:
        return {"mean": 0.0, "min": 0, "max": 0, "per_character_mean": {}}

    lengths = [len(_PLAIN_WORD_RE.findall(m.get("message", ""))) for m in messages]
    per_char: dict[str, list[int]] = {}
    for msg, length in zip(messages, lengths):
        per_char.setdefault(_first_name(msg.get("character")), []).append(length)

    return {
        "mean": round(mean(lengths), 2),
        "min": min(lengths),
        "max": max(lengths),
        "per_character_mean": {c: round(mean(v), 2) for c, v in per_char.items()},
    }


def legacy_quality(
    messages: list[dict[str, Any]],
    concept: str = "",
    day: str | None = None,
) -> dict[str, Any]:
    """Wrap `scripts.simulate_dialogue_week.score_quality` for comparability.

    Imports the simulator lazily (see module docstring) so this metrics
    module keeps importing fine without pulling in the dialogue stack.
    """
    from scripts.simulate_dialogue_week import Message, load_personas, score_quality

    personas = load_personas()
    fallback_day = day or (messages[0].get("day") if messages else "") or ""
    fallback_stage = (messages[0].get("stage") if messages else "") or ""
    converted = [
        Message(
            day=m.get("day", fallback_day),
            stage=m.get("stage", fallback_stage),
            character=m["character"],
            message=m["message"],
            timestamp=m.get("timestamp", ""),
            model=m.get("model", ""),
            attachments=m.get("attachments") or [],
        )
        for m in messages
    ]
    return score_quality(converted, personas, concept=concept, day=day)


def summarize(
    messages: list[dict[str, Any]],
    expected_cast: list[str],
    concept: str = "",
    day: str | None = None,
) -> dict[str, Any]:
    """One flat dict combining every metric above.

    Top-level keys are numeric (or short list/dict) summaries suitable for
    `conversation_lab.py ab`'s per-pair delta computation (it walks this
    dict's int/float leaves); the `*_detail` keys carry each metric
    function's full return value for reporting/debugging.
    """
    cast = cast_coverage(expected_cast, messages)
    adjacency = adjacency_continuity(messages)
    qa = question_answer_rate(messages)
    repeated = repeated_phrases(messages)
    lengths = length_stats(messages)
    legacy = legacy_quality(messages, concept=concept, day=day)

    tic_count = sum(len(v) for v in repeated["per_character_4gram_tics"].values())
    unique_characters = len({_first_name(m.get("character")) for m in messages})

    return {
        "message_count": len(messages),
        "unique_characters": unique_characters,
        "cast_ratio": cast["ratio"],
        "cast_missing_count": len(cast["missing"]),
        "cast_unexpected_count": len(cast["unexpected"]),
        "adjacency_mean_overlap": adjacency["mean_overlap"],
        "adjacency_share_with_link": adjacency["share_with_link"],
        "qa_rate": qa["rate"],
        "qa_question_count": qa["question_count"],
        "length_mean_words": lengths["mean"],
        "length_min_words": lengths["min"],
        "length_max_words": lengths["max"],
        "cross_character_3gram_repeat_count": len(repeated["cross_character_3grams"]),
        "per_character_4gram_tic_count": tic_count,
        "legacy_score": legacy.get("score", 0),
        "legacy_prompt_echo_hits": legacy.get("prompt_echo_hits", 0),
        "legacy_prohibited_hits": legacy.get("prohibited_hits", 0),
        "cast_detail": cast,
        "adjacency_detail": adjacency,
        "qa_detail": qa,
        "repeated_phrases_detail": repeated,
        "length_detail": lengths,
        "legacy_quality_detail": legacy,
    }
