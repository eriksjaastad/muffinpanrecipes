#!/usr/bin/env python3
"""Corpus-wide dialogue heat map (card #6492 slices 2-3).

WHY: `scripts/conversation_metrics.py`'s structure-tell rates (dash_clause_rate,
frame_claim_rate, etc.) tell you a single week's dialogue is robotic, but not
WHERE the robotic phrasing lives - one character's habit, a handful of
recurring stock phrases, or uniform across the whole cast. This module reads
every published episode under `data/episodes/`, finds the phrases that recur
across many DISTINCT weeks (not just many times in one week - a week's own
recipe repeats its own ingredient names constantly, that is not a tell), buckets
each hot phrase and each dialogue line into an AREA (Frames, Agree-openers,
Brand reinforcement, Boilerplate, Pitch vocabulary, Other), and reports the
structure-tell rates themselves per week, per character, and per day so the
matrix shows whether a tell is uniform across the cast, concentrated in one
character, or - like brand_term_rate - concentrated on one day of the week.

The phrase-matrix computation itself lives in the public `phrase_heat()`
function (slice 3), which works over an arbitrary dict of {label: messages}
rather than only "week -> lines" - `build_report` below is the file-based
CLI path over `data/episodes/`, calling `phrase_heat(lines_by_week, ...)`,
but `conversation_lab.py`'s offline A/B runner can call the same function
directly over two experiment arms without touching the filesystem.
`area_rates()` is the same idea for the per-area rate view: a thin wrapper
over `conversation_metrics.summarize()` + `AREA_METRICS` for one in-memory
transcript.

Zero network calls, zero LLM calls: this only reads local JSON files under
`data/episodes/` and does word-level counting.

Output:
  - a markdown report to stdout (phrase x week presence matrix, area
    summaries, per-week, per-character, and per-day structure-rate tables),
    and
  - a JSON file under --results-dir (default docs/conversation-lab/results/)
    named "<UTC stamp>-heatmap.json" containing everything above, so
    something else (e.g. an HTML page) can render its own view of the same
    data. --json prints that JSON to stdout instead of the markdown report
    (the file is still written either way).

See scripts/conversation_metrics.py's lexicon block for FRAME_CLAIM_PATTERNS,
AGREE_OPENER_TOKENS, AGREE_NAME_RIGHT_RE, PITCH_VOCAB_PHRASES,
BRAND_TERM_PATTERNS, AREA_METRICS, and STOPWORDS, all imported from there
rather than redefined here so a hot phrase and a structure rate always agree
on what counts as which tell.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from scripts.conversation_metrics import (
    AGREE_NAME_RIGHT_RE,
    AGREE_OPENER_TOKENS,
    AREA_METRICS,
    BRAND_TERM_PATTERNS,
    FRAME_CLAIM_PATTERNS,
    PITCH_VOCAB_PHRASES,
    STOPWORDS,
    agree_opener_rate,
    brand_term_rate,
    dash_clause_rate,
    frame_claim_rate,
    length_stdev,
    opener_diversity,
    pitch_vocab_rate,
    question_rate,
    short_line_rate,
)
from scripts.conversation_metrics import summarize as cm_summarize

ROOT = Path(__file__).resolve().parents[1]
EPISODES_DIR = ROOT / "data" / "episodes"
DEFAULT_RESULTS_DIR = ROOT / "docs" / "conversation-lab" / "results"

# Exact filename shape only - this is what naturally skips every scratch and
# test file already sitting in data/episodes/ ("2026-W09-strawberry-test.json",
# "2026-W10-bookend-a.json", "test-local-001.json", ...): none of them are
# "2026-W" + exactly two digits + ".json".
_EPISODE_FILENAME_RE = re.compile(r"^2026-W(\d{2})\.json$")

# The 6 areas a hot phrase or a dialogue line can land in. "Structure" tells
# (dash-clause lines, uniform length, lack of short lines) are deliberately
# NOT one of these - they are reported as rates (see _structure_rates below),
# never as phrases, because a rate is what actually shows whether a line is
# structurally uniform; no single n-gram represents "this line was 24 words
# long like every other line." "Brand reinforcement" (card #6492 slice 3) IS
# a phrase area, unlike Structure - "muffin pan", "each cup" etc. are
# concrete recurring n-grams, just ones that concentrate by DAY rather than
# by character (see the per-day structure_rates_by_day table below).
AREAS: tuple[str, ...] = (
    "Frames", "Agree-openers", "Pitch vocabulary", "Boilerplate", "Brand reinforcement", "Other",
)

# Day keys as they appear in an episode's `stages` dict (data/episodes/2026-W??.json).
_DAYS: tuple[str, ...] = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")

# Production/photo-staging vocabulary: language about MAKING the content
# (staging a shot, publishing on schedule) rather than about the dish being
# cooked. A phrase containing one of these words is "boilerplate" even when
# it also contains a generic recipe noun like "muffin pan" - "staging the
# muffin pan" is about photography, not about this week's specific recipe -
# because "muffin pan" itself is the show's one constant prop, not a dish-
# specific ingredient, so its recurrence is exactly what boilerplate is.
PROCESS_VOCAB: frozenset[str] = frozenset(
    {
        "staging", "staged", "stage", "cross", "section", "quarter", "shot",
        "shoot", "shooting", "frame", "framing", "angle", "light", "lighting",
        "camera", "live", "should", "few", "minutes", "schedule", "scheduled",
        "publish", "published", "publishing", "deploy", "deployed", "render",
        "rendered", "upload", "uploaded", "draft", "caption", "post", "posting",
    }
)

_WORD_RE = re.compile(r"[a-z0-9']+")


def _words(text: str) -> list[str]:
    """Lowercase word tokens - kept local (not imported) since `_words` is a
    private helper in conversation_metrics.py; the tokenization itself
    (same regex) is duplicated here on purpose so this module only imports
    conversation_metrics's public names."""
    return _WORD_RE.findall((text or "").lower())


def _ngrams(words: list[str], n: int) -> list[tuple[str, ...]]:
    if len(words) < n:
        return []
    return [tuple(words[i : i + n]) for i in range(len(words) - n + 1)]


def _first_name(character: str | None) -> str:
    """Matches cron_routes.py:369's `m.get("character", "?").split()[0]`."""
    name = (character or "?").strip()
    return name.split()[0] if name else "?"


def classify_line(text: str) -> str:
    """AREA for one dialogue line - same priority order as classify_phrase.

    Boilerplate is checked BEFORE Brand reinforcement on purpose: "staging
    the muffin pan" is a production/photography tell (PROCESS_VOCAB's
    "staging") that happens to also name the pan, and the more specific
    process-vocabulary signal should win - a line about staging a shot is
    Boilerplate even when the shot is of the pan.
    """
    if any(p.search(text) for p in FRAME_CLAIM_PATTERNS):
        return "Frames"
    stripped = text.strip()
    first_words = _words(stripped)
    opens_with_token = bool(first_words) and first_words[0] in AGREE_OPENER_TOKENS
    if opens_with_token or AGREE_NAME_RIGHT_RE.match(stripped):
        return "Agree-openers"
    lowered = text.lower()
    if any(phrase in lowered for phrase in PITCH_VOCAB_PHRASES):
        return "Pitch vocabulary"
    if any(word in PROCESS_VOCAB for word in _words(text)):
        return "Boilerplate"
    if any(p.search(text) for p in BRAND_TERM_PATTERNS):
        return "Brand reinforcement"
    return "Other"


def classify_phrase(phrase: str) -> str:
    """AREA for one hot n-gram phrase (already lowercase, space-joined).

    Same priority order and same lexicons as classify_line (see its
    docstring for why Boilerplate is checked before Brand reinforcement) -
    a hot phrase and the lines it came from should never disagree about
    which area they belong to.
    """
    if any(p.search(phrase) for p in FRAME_CLAIM_PATTERNS):
        return "Frames"
    words = phrase.split()
    if (words and words[0] in AGREE_OPENER_TOKENS) or AGREE_NAME_RIGHT_RE.match(phrase):
        return "Agree-openers"
    if any(pitch_phrase in phrase for pitch_phrase in PITCH_VOCAB_PHRASES):
        return "Pitch vocabulary"
    if any(word in PROCESS_VOCAB for word in words):
        return "Boilerplate"
    if any(p.search(phrase) for p in BRAND_TERM_PATTERNS):
        return "Brand reinforcement"
    return "Other"


def discover_episode_files(episodes_dir: Path) -> dict[str, Path]:
    """Map "W36" -> path, for every data/episodes/2026-W??.json file present."""
    found: dict[str, Path] = {}
    for path in sorted(episodes_dir.glob("2026-W??.json")):
        m = _EPISODE_FILENAME_RE.match(path.name)
        if not m:
            continue
        found[f"W{m.group(1)}"] = path
    return found


def _normalize_week_arg(raw: str) -> str:
    """"W36", "36", "2026-W36" all normalise to "W36"."""
    value = raw.strip().upper()
    if value.startswith("2026-"):
        value = value[len("2026-") :]
    if not value.startswith("W"):
        value = f"W{value}"
    digits = value[1:]
    if len(digits) == 1:
        digits = f"0{digits}"
    return f"W{digits}"


def _load_week_lines(path: Path) -> tuple[bool, list[dict[str, Any]]]:
    """Return (is_published, lines) for one episode file.

    `lines` is a flat list of {"character": first_name, "message": text,
    "day": day} dicts across every stage that has dialogue.
    """
    episode = json.loads(path.read_text(encoding="utf-8"))
    is_published = bool(episode.get("published_at"))
    lines: list[dict[str, Any]] = []
    for day, stage_data in (episode.get("stages") or {}).items():
        for msg in (stage_data or {}).get("dialogue") or []:
            lines.append(
                {
                    "character": _first_name(msg.get("character")),
                    "message": msg.get("message") or "",
                    "day": day,
                }
            )
    return is_published, lines


def collect_corpus(
    episodes_dir: Path,
    weeks: str | None,
    include_unpublished: bool,
) -> dict[str, Any]:
    """Load every qualifying episode into per-week and per-character line lists.

    Returns a dict with "lines_by_week", "lines_by_character", "lines_by_day"
    (keyed by the `_DAYS` names), "all_lines" (each line additionally tagged
    "week"), "used_weeks" (sorted), and "excluded_weeks" (discovered but
    filtered out - unpublished, or not in --weeks - kept for transparency in
    the report).
    """
    discovered = discover_episode_files(episodes_dir)
    requested: set[str] | None = None
    if weeks:
        requested = {_normalize_week_arg(w) for w in weeks.split(",") if w.strip()}

    lines_by_week: dict[str, list[dict[str, Any]]] = {}
    excluded_weeks: list[str] = []
    for week, path in discovered.items():
        if requested is not None and week not in requested:
            excluded_weeks.append(week)
            continue
        is_published, lines = _load_week_lines(path)
        if not include_unpublished and not is_published:
            excluded_weeks.append(week)
            continue
        if lines:
            lines_by_week[week] = lines

    used_weeks = sorted(lines_by_week)

    lines_by_character: dict[str, list[dict[str, Any]]] = {}
    lines_by_day: dict[str, list[dict[str, Any]]] = {}
    all_lines: list[dict[str, Any]] = []
    for week in used_weeks:
        for line in lines_by_week[week]:
            tagged = dict(line, week=week)
            all_lines.append(tagged)
            lines_by_character.setdefault(line["character"], []).append(line)
            lines_by_day.setdefault(line["day"], []).append(line)

    return {
        "lines_by_week": lines_by_week,
        "lines_by_character": lines_by_character,
        "lines_by_day": lines_by_day,
        "all_lines": all_lines,
        "used_weeks": used_weeks,
        "excluded_weeks": sorted(excluded_weeks),
    }


def phrase_heat(
    transcripts: dict[str, list[dict[str, Any]]],
    *,
    min_groups: int = 2,
    top: int = 30,
) -> dict[str, Any]:
    """Phrase x label heat matrix over IN-MEMORY transcripts (card #6492 slice 3).

    `transcripts` maps an arbitrary label - a week id like "W36" for the
    corpus-wide file-based report below, or an experiment-arm name like
    "control"/"variant-a" for `conversation_lab.py`'s offline A/B runner -
    to a flat list of message dicts (the same {"character": ..., "message":
    ...} shape used everywhere else in this module and in
    conversation_metrics.py). Computes 2-, 3-, and 4-grams, stopword-filtered
    (a gram is skipped entirely when every one of its words is a stopword -
    "of the", "it was the" - structural filler, not a tell), counts how many
    DISTINCT labels each phrase shows up in (not raw occurrence count - a
    phrase used 10 times in one arm and never elsewhere is that arm's own
    habit, not a phrase shared across arms/weeks), and keeps only phrases
    meeting `min_groups`, sorted by distinct-label count then total
    occurrences, capped at `top`.

    `build_report` (the file-based CLI path) calls this with
    `transcripts=lines_by_week` so a hot phrase in the corpus-wide report and
    a hot phrase in an offline A/B comparison are computed by the exact same
    function - the refactor this signature exists for.
    """
    phrase_groups: dict[str, set[str]] = {}
    phrase_total: Counter[str] = Counter()
    phrase_by_character: dict[str, Counter[str]] = {}

    for label, messages in transcripts.items():
        for msg in messages:
            words = _words(msg.get("message") or "")
            character = _first_name(msg.get("character"))
            for n in (2, 3, 4):
                for gram in _ngrams(words, n):
                    if all(w in STOPWORDS for w in gram):
                        continue
                    phrase = " ".join(gram)
                    phrase_groups.setdefault(phrase, set()).add(label)
                    phrase_total[phrase] += 1
                    phrase_by_character.setdefault(phrase, Counter())[character] += 1

    hot = [
        {
            "phrase": phrase,
            "area": classify_phrase(phrase),
            "n": len(phrase.split()),
            "groups": len(labels),
            "group_labels": sorted(labels),
            "total_occurrences": phrase_total[phrase],
            "per_character": dict(phrase_by_character[phrase]),
        }
        for phrase, labels in phrase_groups.items()
        if len(labels) >= min_groups
    ]
    hot.sort(key=lambda h: (-h["groups"], -h["total_occurrences"], h["phrase"]))

    return {
        "labels": sorted(transcripts),
        "min_groups": min_groups,
        "top": top,
        "phrases": hot[:top],
    }


def area_rates(messages: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Per-AREA_METRICS numbers for one transcript - a thin wrapper over
    `conversation_metrics.summarize()` (card #6492 slice 3).

    `messages` carries no cast roster of its own (a lab arm's transcript is
    not tied to a specific day's `expected_cast`), so this calls summarize()
    with an empty expected_cast; cast_coverage's own empty-expected rule
    (scripts/conversation_metrics.py) reports cast_ratio as 1.0 in that case
    rather than a misleading partial number. Returns one flat dict: area
    name -> {metric key: value} for every key AREA_METRICS lists under that
    area, read straight off summarize()'s flat numeric keys - so the lab
    runner (another implementer) can report per-area deltas between two
    experiment arms without re-deriving which keys belong to which area.
    """
    summary = cm_summarize(messages, [])
    return {area: {key: summary[key] for key in keys} for area, keys in AREA_METRICS.items()}


def area_summary(phrases: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    summary = {area: {"hot_phrase_count": 0, "total_occurrences": 0, "example_phrases": []} for area in AREAS}
    for hp in phrases:
        bucket = summary[hp["area"]]
        bucket["hot_phrase_count"] += 1
        bucket["total_occurrences"] += hp["total_occurrences"]
        if len(bucket["example_phrases"]) < 5:
            bucket["example_phrases"].append(hp["phrase"])
    return summary


def area_counts(lines: list[dict[str, Any]]) -> dict[str, int]:
    """How many of these lines fall into each AREA (line-level classification)."""
    counts = Counter(classify_line(line["message"]) for line in lines)
    return {area: counts.get(area, 0) for area in AREAS}


_RATE_KEYS: tuple[str, ...] = (
    "dash_clause_rate", "frame_claim_rate", "agree_opener_rate", "short_line_rate",
    "question_rate", "length_stdev", "opener_diversity", "pitch_vocab_rate",
    "brand_term_rate",
)


def structure_rates(messages: list[dict[str, Any]]) -> dict[str, Any]:
    """The structure-tell rates for one slice of lines (a week, a character,
    or a day), computed by calling straight into conversation_metrics so a
    heat-map rate and a lab `summarize()` rate never drift apart.

    `brand_term_rate` (card #6492 slice 3) concentrates by DAY (32% Monday
    vs. 7-12% every other day - see BRAND_TERM_PATTERNS's docstring) rather
    than by character, which is why `build_report` below adds a per-day
    table specifically for it, on top of the by-week/by-character tables
    every rate here already gets.
    """
    return {
        "dash_clause_rate": dash_clause_rate(messages)["rate"],
        "frame_claim_rate": frame_claim_rate(messages)["rate"],
        "agree_opener_rate": agree_opener_rate(messages)["rate"],
        "short_line_rate": short_line_rate(messages)["rate"],
        "question_rate": question_rate(messages)["rate"],
        "length_stdev": length_stdev(messages)["stdev"],
        "opener_diversity": opener_diversity(messages)["ratio"],
        "pitch_vocab_rate": pitch_vocab_rate(messages)["rate"],
        "brand_term_rate": brand_term_rate(messages)["rate"],
        "line_count": len(messages),
    }


def build_report(
    episodes_dir: Path,
    weeks: str | None = None,
    include_unpublished: bool = False,
    min_weeks: int = 3,
    top: int = 60,
) -> dict[str, Any]:
    """Assemble the full heat-map report as a JSON-serialisable dict.

    The primary, directly-testable entry point - `main()` is a thin argparse
    wrapper around this.
    """
    corpus = collect_corpus(episodes_dir, weeks, include_unpublished)
    matrix = phrase_heat(corpus["lines_by_week"], min_groups=min_weeks, top=top)
    phrases = matrix["phrases"]

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "weeks": corpus["used_weeks"],
        "excluded_weeks": corpus["excluded_weeks"],
        "min_weeks": min_weeks,
        "top": top,
        "include_unpublished": include_unpublished,
        "line_count": len(corpus["all_lines"]),
        "phrase_heat": phrases,
        "area_summary": area_summary(phrases),
        "area_counts_overall": area_counts(corpus["all_lines"]),
        "area_counts_by_week": {
            week: area_counts(lines) for week, lines in corpus["lines_by_week"].items()
        },
        "area_counts_by_character": {
            char: area_counts(lines) for char, lines in corpus["lines_by_character"].items()
        },
        "structure_rates_by_week": {
            week: structure_rates(lines) for week, lines in corpus["lines_by_week"].items()
        },
        "structure_rates_by_character": {
            char: structure_rates(lines) for char, lines in corpus["lines_by_character"].items()
        },
        # Per-day table (card #6492 slice 3): always 7 rows (monday..sunday),
        # even for a day with zero lines in the requested week set, so
        # brand_term_rate's Monday concentration is visible against a
        # complete week shape rather than only the days that happened to
        # have data.
        "structure_rates_by_day": {
            day: structure_rates(corpus["lines_by_day"].get(day, [])) for day in _DAYS
        },
    }


def _render_markdown(report: dict[str, Any]) -> str:
    weeks = report["weeks"]
    out: list[str] = []
    out.append("# Conversation corpus heat map")
    out.append("")
    out.append(f"Generated: {report['generated_at']}")
    out.append(f"Weeks covered ({len(weeks)}): {', '.join(weeks) if weeks else '(none)'}")
    out.append(
        f"min-weeks: {report['min_weeks']}  top: {report['top']}  "
        f"include-unpublished: {report['include_unpublished']}  lines: {report['line_count']}"
    )
    if report["excluded_weeks"]:
        out.append(f"Excluded weeks (unpublished or filtered by --weeks): {', '.join(report['excluded_weeks'])}")
    out.append("")

    out.append(f"## Phrase heat (top {len(report['phrase_heat'])} phrases by distinct-week count)")
    out.append("")
    if not report["phrase_heat"] or not weeks:
        out.append("(no phrase met --min-weeks)")
    else:
        out.append("| phrase | area | weeks | occurrences | " + " | ".join(weeks) + " |")
        out.append("| --- | --- | --- | --- | " + " | ".join("---" for _ in weeks) + " |")
        for hp in report["phrase_heat"]:
            present = set(hp["group_labels"])
            cells = " | ".join("X" if w in present else "" for w in weeks)
            out.append(
                f"| {hp['phrase']} | {hp['area']} | {hp['groups']} | "
                f"{hp['total_occurrences']} | {cells} |"
            )
    out.append("")

    out.append("## Area summary")
    out.append("")
    out.append("| area | hot phrases | total occurrences | examples |")
    out.append("| --- | --- | --- | --- |")
    for area in AREAS:
        s = report["area_summary"][area]
        out.append(f"| {area} | {s['hot_phrase_count']} | {s['total_occurrences']} | {'; '.join(s['example_phrases'])} |")
    out.append("")

    out.append("## Structure rates by week")
    out.append("")
    out.append("| week | " + " | ".join(_RATE_KEYS) + " | line_count |")
    out.append("| --- | " + " | ".join("---" for _ in _RATE_KEYS) + " | --- |")
    for week in weeks:
        r = report["structure_rates_by_week"][week]
        out.append(f"| {week} | " + " | ".join(str(r[k]) for k in _RATE_KEYS) + f" | {r['line_count']} |")
    out.append("")

    out.append("## Structure rates by character")
    out.append("")
    out.append("| character | " + " | ".join(_RATE_KEYS) + " | line_count |")
    out.append("| --- | " + " | ".join("---" for _ in _RATE_KEYS) + " | --- |")
    for char in sorted(report["structure_rates_by_character"]):
        r = report["structure_rates_by_character"][char]
        out.append(f"| {char} | " + " | ".join(str(r[k]) for k in _RATE_KEYS) + f" | {r['line_count']} |")
    out.append("")

    out.append("## Structure rates by day")
    out.append("")
    out.append("(brand_term_rate is the one to watch here - it concentrates on Monday)")
    out.append("")
    out.append("| day | " + " | ".join(_RATE_KEYS) + " | line_count |")
    out.append("| --- | " + " | ".join("---" for _ in _RATE_KEYS) + " | --- |")
    for day in _DAYS:
        r = report["structure_rates_by_day"][day]
        out.append(f"| {day} | " + " | ".join(str(r[k]) for k in _RATE_KEYS) + f" | {r['line_count']} |")
    out.append("")

    return "\n".join(out)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="conversation_heatmap",
        description=(
            "Corpus-wide heat map of recurring dialogue phrases and "
            "structural robotic tells across every published "
            "data/episodes/2026-W??.json (card #6492 slice 2). Zero "
            "network calls, zero LLM calls."
        ),
    )
    parser.add_argument(
        "--weeks", default=None,
        help="Comma-separated week filter, e.g. 'W11,W36' or '11,36' (default: every discovered week)",
    )
    parser.add_argument(
        "--include-unpublished", action="store_true",
        help="Include episodes with no published_at (e.g. W12, the one currently unpublished week)",
    )
    parser.add_argument(
        "--min-weeks", type=int, default=3,
        help="Minimum distinct weeks a phrase must appear in to count as 'hot' (default: 3)",
    )
    parser.add_argument("--top", type=int, default=60, help="Number of hot phrases to report (default: 60)")
    parser.add_argument(
        "--results-dir", type=Path, default=DEFAULT_RESULTS_DIR,
        help="Where to write the <UTC stamp>-heatmap.json result (default: docs/conversation-lab/results/)",
    )
    parser.add_argument("--json", action="store_true", help="Print the JSON report to stdout instead of markdown")
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = _build_parser()
    args = parser.parse_args(argv)

    report = build_report(
        EPISODES_DIR,
        weeks=args.weeks,
        include_unpublished=args.include_unpublished,
        min_weeks=args.min_weeks,
        top=args.top,
    )

    results_dir = Path(args.results_dir)
    results_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    result_path = results_dir / f"{stamp}-heatmap.json"
    result_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")

    if args.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        print(_render_markdown(report))
    print(f"\nresults written to: {result_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
