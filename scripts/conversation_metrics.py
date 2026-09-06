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
from statistics import mean, pstdev
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

# ---------------------------------------------------------------------------
# Structure-tell lexicons (card #6492 slice 2).
#
# WHY: Erik read the W36 dialogue and called it robotic. These are the
# measured tells across the W11-W36 corpus (917 lines, see
# scripts/conversation_heatmap.py's corpus-wide heat map): dash-clause
# lines 86%, "X is the story" pronouncement frames 30%, agree-and-extend
# openers 21%, lines under 8 words 1%, mean line length 24.1 words with a
# stdev of 8.1 - i.e. almost every line is a similarly-long clause stapled
# to another clause with a hyphen, opens by agreeing with the last speaker,
# and rarely lands a short reactive beat. The functions below turn each of
# those observations into a deterministic rate. Every lexicon here is a
# module constant on purpose - Erik or a future session should be able to
# tune the list without touching the counting logic, and
# scripts/conversation_heatmap.py imports these same constants so a hot
# phrase and a structure rate agree on what counts as which tell.
#
# House style note: no literal em dash, en dash, or curly quote appears
# anywhere below - matching scripts/simulate_dialogue_week.py's own
# TYPOGRAPHIC_TELLS (~line 1341), the unicode escapes are typed instead so
# this file itself never trips the same hard-fail it is measuring.
# ---------------------------------------------------------------------------

# " - " (space hyphen space) is the corpus's dominant clause-joiner; a raw
# (un-sanitized) em dash or en dash is the same tell before sanitize_typographic_tells
# (scripts/simulate_dialogue_week.py) ever runs on it.
DASH_CLAUSE_RE = re.compile(r" - |\u2014|\u2013")

# Pronouncement frames: a line that announces what "the story" / "the hook"
# etc. is, rather than just being part of the conversation. Two shapes:
# (1) a copula ("is"/"that's"/"it's") immediately followed by a
#     definite/interrogative pivot ("the", "what", "actually", ...), and
# (2) "the <payoff noun>" on its own, which catches the frame even when the
#     copula is a few words earlier ("Honestly, the story here is ...").
FRAME_CLAIM_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b(?:is|that's|it's)\s+(?:the|what|actually|where|why|how)\b", re.IGNORECASE),
    re.compile(r"\bthe\s+(?:hook|story|key|point|thing|tell|payoff|move|win)\b", re.IGNORECASE),
)

# Single-token agree-and-extend openers - a line that starts by agreeing
# with whoever spoke last instead of adding something new.
AGREE_OPENER_TOKENS: frozenset[str] = frozenset(
    {"fair", "yeah", "yes", "right", "exactly", "agreed", "true", "fine", "okay", "ok"}
)

# The two-token version of the same tell: "<Name> is right" / "<Name>'s
# right" as the opening of a line. `\w+` stands in for any character's
# first name so this does not need updating when the cast changes.
AGREE_NAME_RIGHT_RE = re.compile(r"^\w+(?:'s|\s+is)\s+right\b", re.IGNORECASE)

# Marketing-register phrases: language a photographer/social character
# would use about the CONTENT (a shot, a thumbnail, engagement) rather than
# language about the DISH - a tell that the scene has drifted into a pitch
# meeting instead of a kitchen conversation.
PITCH_VOCAB_PHRASES: tuple[str, ...] = (
    "stops the scroll",
    "on camera",
    "the visual",
    "reads on",
    "hero shot",
    "thumbnail",
    "the copy",
    "engagement",
)

# Brand-reinforcement lexicon (card #6492 slice 3): the show's one constant
# prop is the muffin pan itself, and the cast leans on a small set of terms
# to keep pointing at it - the pan, its cups/wells, portioning language, and
# "twelve" as the pan's own serving count. Measured across the W11-W36
# corpus: 13% of lines overall, but 32% on Monday against 7-12% on every
# other day (the concept-pick day, where the pan gets introduced and sold),
# and a flat 11-15% across every character - i.e. this tell concentrates by
# DAY, not by character, unlike most of the other lexicons above. See
# scripts/conversation_heatmap.py's per-day rates table for that view.
BRAND_TERM_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bmuffin\s+pan\b", re.IGNORECASE),
    re.compile(r"\bmuffin\s+tin\b", re.IGNORECASE),
    re.compile(r"\bthe\s+pan\b", re.IGNORECASE),
    re.compile(r"\bpan'?s?\s+walls\b", re.IGNORECASE),
    re.compile(r"\b(?:each|every|per)\s+cup\b", re.IGNORECASE),
    re.compile(r"\bthe\s+cups?\b", re.IGNORECASE),
    re.compile(r"\bthe\s+wells?\b", re.IGNORECASE),
    re.compile(r"\bportions?\b", re.IGNORECASE),
    re.compile(r"\bgrab-and-go\b", re.IGNORECASE),
    re.compile(r"\btwelve\s+(?:muffins?|cups?|servings?|portions?)\b", re.IGNORECASE),
    re.compile(r"\b(?:makes|yields)\s+twelve\b", re.IGNORECASE),
)


def _lines(messages: list[dict[str, Any]]) -> list[str]:
    """Raw message text per turn, casing and punctuation intact.

    Unlike `_words`/`_content_words`, the structure-tell metrics below need
    the literal line - to check for " - ", a trailing "?", or an opening
    token - not a bag of lowercased content words.
    """
    return [m.get("message") or "" for m in messages]


def dash_clause_rate(messages: list[dict[str, Any]]) -> dict[str, Any]:
    """Share of lines containing a " - " clause-join or an em/en dash.

    Measured at 86% across the W11-W36 corpus - the single biggest robotic
    tell Erik flagged in W36: nearly every line is two clauses stapled
    together with a hyphen instead of being one plain sentence.
    """
    lines = _lines(messages)
    if not lines:
        return {"rate": 0.0, "hit_count": 0, "line_count": 0}
    hits = sum(1 for line in lines if DASH_CLAUSE_RE.search(line))
    return {"rate": round(hits / len(lines), 4), "hit_count": hits, "line_count": len(lines)}


def frame_claim_rate(messages: list[dict[str, Any]]) -> dict[str, Any]:
    """Share of lines that announce a frame ("that's the story") - measured at 30%."""
    lines = _lines(messages)
    if not lines:
        return {"rate": 0.0, "hit_count": 0, "line_count": 0}
    hits = sum(1 for line in lines if any(p.search(line) for p in FRAME_CLAIM_PATTERNS))
    return {"rate": round(hits / len(lines), 4), "hit_count": hits, "line_count": len(lines)}


def agree_opener_rate(messages: list[dict[str, Any]]) -> dict[str, Any]:
    """Share of lines opening with an agree-and-extend token - measured at 21%."""
    lines = _lines(messages)
    if not lines:
        return {"rate": 0.0, "hit_count": 0, "line_count": 0}
    hits = 0
    for line in lines:
        stripped = line.strip()
        first_words = _words(stripped)
        opens_with_token = bool(first_words) and first_words[0] in AGREE_OPENER_TOKENS
        if opens_with_token or AGREE_NAME_RIGHT_RE.match(stripped):
            hits += 1
    return {"rate": round(hits / len(lines), 4), "hit_count": hits, "line_count": len(lines)}


def short_line_rate(messages: list[dict[str, Any]]) -> dict[str, Any]:
    """Share of lines under 8 words - measured at just 1%: almost nothing lands a quick beat."""
    lines = _lines(messages)
    if not lines:
        return {"rate": 0.0, "hit_count": 0, "line_count": 0}
    hits = sum(1 for line in lines if len(_PLAIN_WORD_RE.findall(line)) < 8)
    return {"rate": round(hits / len(lines), 4), "hit_count": hits, "line_count": len(lines)}


def question_rate(messages: list[dict[str, Any]]) -> dict[str, Any]:
    """Share of lines containing a "?"."""
    lines = _lines(messages)
    if not lines:
        return {"rate": 0.0, "hit_count": 0, "line_count": 0}
    hits = sum(1 for line in lines if "?" in line)
    return {"rate": round(hits / len(lines), 4), "hit_count": hits, "line_count": len(lines)}


def length_stdev(messages: list[dict[str, Any]]) -> dict[str, Any]:
    """Population stdev of per-line word count - measured at 8.1 around a mean of 24.1.

    Uses `statistics.pstdev` (population, not sample) so a single-line
    transcript returns 0.0 instead of raising `StatisticsError`.
    """
    lines = _lines(messages)
    if len(lines) < 2:
        return {"stdev": 0.0, "line_count": len(lines)}
    counts = [len(_PLAIN_WORD_RE.findall(line)) for line in lines]
    return {"stdev": round(pstdev(counts), 4), "line_count": len(lines)}


def opener_diversity(messages: list[dict[str, Any]]) -> dict[str, Any]:
    """Distinct first-two-word openers divided by line count.

    Low diversity means many lines start the same way ("Fair, ...", "The
    ...") - a distinct signal from agree_opener_rate, which only counts a
    fixed token lexicon; this catches repetition in general, not just
    agreement.
    """
    lines = _lines(messages)
    if not lines:
        return {"ratio": 0.0, "distinct_openers": 0, "line_count": 0}
    openers = {tuple(_words(line)[:2]) for line in lines if _words(line)}
    return {
        "ratio": round(len(openers) / len(lines), 4),
        "distinct_openers": len(openers),
        "line_count": len(lines),
    }


def pitch_vocab_rate(messages: list[dict[str, Any]]) -> dict[str, Any]:
    """Share of lines containing a marketing-register phrase (see PITCH_VOCAB_PHRASES)."""
    lines = _lines(messages)
    if not lines:
        return {"rate": 0.0, "hit_count": 0, "line_count": 0}
    hits = sum(1 for line in lines if any(phrase in line.lower() for phrase in PITCH_VOCAB_PHRASES))
    return {"rate": round(hits / len(lines), 4), "hit_count": hits, "line_count": len(lines)}


def brand_term_rate(messages: list[dict[str, Any]]) -> dict[str, Any]:
    """Share of lines mentioning a muffin-pan brand term (see BRAND_TERM_PATTERNS).

    Measured at 13% of lines overall across the W11-W36 corpus, concentrated
    on Monday (32%) versus 7-12% every other day, and flat across characters
    (11-15% each) - the opposite concentration shape from the other
    structure tells above, so it is worth reading per-day rather than
    per-character.
    """
    lines = _lines(messages)
    if not lines:
        return {"rate": 0.0, "hit_count": 0, "line_count": 0}
    hits = sum(1 for line in lines if any(p.search(line) for p in BRAND_TERM_PATTERNS))
    return {"rate": round(hits / len(lines), 4), "hit_count": hits, "line_count": len(lines)}


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
    dash = dash_clause_rate(messages)
    frame = frame_claim_rate(messages)
    agree = agree_opener_rate(messages)
    short = short_line_rate(messages)
    question = question_rate(messages)
    stdev = length_stdev(messages)
    openers = opener_diversity(messages)
    pitch = pitch_vocab_rate(messages)
    brand = brand_term_rate(messages)

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
        # Structure-tell rates (card #6492 slice 2) - see the lexicon block
        # near the top of this module for what each one measures and why.
        "dash_clause_rate": dash["rate"],
        "frame_claim_rate": frame["rate"],
        "agree_opener_rate": agree["rate"],
        "short_line_rate": short["rate"],
        "question_rate": question["rate"],
        "length_stdev": stdev["stdev"],
        "opener_diversity": openers["ratio"],
        "pitch_vocab_rate": pitch["rate"],
        # Brand-reinforcement rate (card #6492 slice 3) - see BRAND_TERM_PATTERNS.
        "brand_term_rate": brand["rate"],
        "cast_detail": cast,
        "adjacency_detail": adjacency,
        "qa_detail": qa,
        "repeated_phrases_detail": repeated,
        "length_detail": lengths,
        "legacy_quality_detail": legacy,
        "dash_clause_detail": dash,
        "frame_claim_detail": frame,
        "agree_opener_detail": agree,
        "short_line_detail": short,
        "question_rate_detail": question,
        "length_stdev_detail": stdev,
        "opener_diversity_detail": openers,
        "pitch_vocab_detail": pitch,
        "brand_term_detail": brand,
    }


# ---------------------------------------------------------------------------
# AREA_METRICS (card #6492 slice 3): groups summarize()'s flat numeric keys
# into the same "area" buckets scripts/conversation_heatmap.py classifies
# phrases and lines into, so the lab runner can report one number per area
# instead of walking the whole flat dict. Every value here MUST be a key
# summarize() actually emits - see test_area_metrics_keys_exist_in_summarize
# in tests/test_conversation_metrics.py, which asserts exactly that so this
# constant can never silently drift out of sync with summarize()'s output.
# ---------------------------------------------------------------------------

AREA_METRICS: dict[str, list[str]] = {
    "Structure": ["dash_clause_rate", "short_line_rate", "length_stdev", "opener_diversity"],
    "Frames": ["frame_claim_rate"],
    "Agree-openers": ["agree_opener_rate"],
    "Brand reinforcement": ["brand_term_rate"],
    "Pitch vocabulary": ["pitch_vocab_rate"],
    "Engagement": ["question_rate", "qa_rate", "adjacency_mean_overlap", "adjacency_share_with_link"],
    "Cast": ["cast_ratio"],
}
