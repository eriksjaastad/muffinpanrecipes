#!/usr/bin/env python3
"""Zero-cost weekly measurement CLI for a single episode's dialogue (#6492).

Reads only public data (the blob CDN, or the local data/episodes mirror with
`--local`) and never calls a paid model or judge. Built because reading raw
episode JSON by hand is how W36's degradation got noticed five days late
(RUNBOOK Incident-adjacent; see backend/utils/episode_integrity.py) and
because the placeholder concept ("Weekly Muffin Pan Recipe") sat in
episode['concept'] for 23 of 25 weeks -- printing that field as a "title"
would make every report look broken. The real title always comes from the
baker's own output, `stages.monday.recipe_data.title`, via
`backend.utils.episode_integrity._recipe_title`.

Four report shapes, one CLI:
  1. `review_episode.py 2026-W37`            -- one episode, day by day.
  2. `review_episode.py 2026-W37 --json`     -- same data as JSON on stdout,
                                                 for the lab runner or Claude.
  3. `review_episode.py --coverage`          -- catalog x episode coverage:
                                                 one row per published recipe,
                                                 plus orphan episode JSONs and
                                                 catalog/episode title drift.
  4. `review_episode.py --rubric`            -- the 6-dimension scoring rubric
                                                 and the tuning-log row template.

`--local` reads data/episodes/<id>.json (and src/recipes.json for --coverage)
instead of the CDN and NEVER touches the network -- useful offline, and the
only path exercised by tests. Local files are write-through mirrors of the
CDN; they go stale the moment prod regenerates a week without a matching
local write, so every local read prints a loud STALE MIRROR warning naming
the file's mtime. Without --local, a CDN read failure falls back to the local
mirror with the same warning instead of dying -- staleness is a warning, a
missing file either way is not.

Usage:
    uv run python scripts/review_episode.py 2026-W37
    uv run python scripts/review_episode.py 2026-W37 --local --json
    uv run python scripts/review_episode.py --coverage --local
    uv run python scripts/review_episode.py --rubric
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.utils.episode_integrity import (  # noqa: E402
    _recipe_title,
    episode_summary,
    parse_episode_id,
)
from scripts.simulate_dialogue_week import DAY_ORDER, participants_for_day  # noqa: E402

BLOB_CDN = "https://gtczmjysc51nh8fq.public.blob.vercel-storage.com"
EPISODES_DIR = ROOT / "data" / "episodes"
RECIPES_JSON_PATH = ROOT / "src" / "recipes.json"
TIMEOUT_SECONDS = 8.0

# Matches a plain weekly episode id (2026-W37.json) so orphan-scanning skips
# the ad-hoc test/bookend files that also live in data/episodes/ (e.g.
# 2026-W10-bookend-a.json, 2026-W09-tiramisu-1.json).
_PLAIN_EPISODE_ID_RE = re.compile(r"^\d{4}-W\d{2}$")

_RUBRIC_DIMENSIONS = [
    "Title fidelity",
    "Arc resolution",
    "Voice distinctiveness",
    "Technical credibility",
    "Natural progression",
    "Promise/delivery alignment",
]


# ---------------------------------------------------------------------------
# Loading (CDN with a local fallback, or --local straight to disk)
# ---------------------------------------------------------------------------


def _cache_busted(url: str) -> str:
    """A fresh token per request -- a fixed cache-buster gets cached by the
    CDN too and serves the same (possibly stale) body back on the next read.
    Mirrors scripts/remove_w12_from_live.py's `_cb()`.
    """
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}cb={time.time_ns()}"


def _fetch_cdn_json(url: str) -> Any:
    """Public CDN GET, no token. Raises on any failure; callers decide the
    fallback. Pattern copied from scripts/session_pipeline_status.py:44-54.
    """
    req = urllib.request.Request(
        _cache_busted(url), headers={"User-Agent": "review-episode/1.0"}
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _read_local_json(path: Path) -> Any:
    if not path.exists():
        raise FileNotFoundError(f"no local mirror at {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _stale_mirror_warning(path: Path, reason: str | None = None) -> str:
    if path.exists():
        mtime = datetime.fromtimestamp(
            path.stat().st_mtime, tz=timezone.utc
        ).strftime("%Y-%m-%d %H:%M:%S UTC")
    else:
        mtime = "unknown (file does not exist)"
    cause = f"CDN read failed ({reason})" if reason else "--local requested"
    return (
        f"STALE MIRROR: read {path} from local disk ({cause}); file mtime "
        f"{mtime}. Local files are write-through mirrors that go stale after "
        f"prod-side regeneration."
    )


def load_episode(episode_id: str, local: bool) -> tuple[dict, list[str]]:
    """Return (episode, warnings). When local=True this NEVER touches the
    network -- the local branch below calls no urllib code at all.
    """
    path = EPISODES_DIR / f"{episode_id}.json"
    if local:
        data = _read_local_json(path)
        if not isinstance(data, dict):
            raise ValueError(f"{path} did not contain a JSON object")
        return data, [_stale_mirror_warning(path)]

    try:
        data = _fetch_cdn_json(f"{BLOB_CDN}/episodes/{episode_id}.json")
    except Exception as exc:
        data = _read_local_json(path)
        if not isinstance(data, dict):
            raise ValueError(f"{path} did not contain a JSON object") from exc
        return data, [_stale_mirror_warning(path, reason=f"{type(exc).__name__}: {exc}")]

    if not isinstance(data, dict):
        raise ValueError(f"CDN episode {episode_id} was not a JSON object")
    return data, []


def _extract_recipes(data: Any, source: str) -> list[dict]:
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and isinstance(data.get("recipes"), list):
        return data["recipes"]
    raise ValueError(f"{source} did not contain a recipes list")


def load_catalog(local: bool) -> tuple[list[dict], list[str]]:
    """Return (recipes, warnings). Same never-touches-network guarantee for
    local=True as load_episode.
    """
    if local:
        data = _read_local_json(RECIPES_JSON_PATH)
        recipes = _extract_recipes(data, str(RECIPES_JSON_PATH))
        return recipes, [_stale_mirror_warning(RECIPES_JSON_PATH)]

    try:
        data = _fetch_cdn_json(f"{BLOB_CDN}/pages/recipes.json")
        return _extract_recipes(data, "CDN pages/recipes.json"), []
    except Exception as exc:
        data = _read_local_json(RECIPES_JSON_PATH)
        recipes = _extract_recipes(data, str(RECIPES_JSON_PATH))
        return recipes, [
            _stale_mirror_warning(RECIPES_JSON_PATH, reason=f"{type(exc).__name__}: {exc}")
        ]


# ---------------------------------------------------------------------------
# Cast normalization + transcript formatting
# ---------------------------------------------------------------------------


def normalize_first_name(name: str) -> str:
    """First-name normalization matching cron_routes._judge_dialogue's
    `m.get("character", "?").split()[0]` (backend/admin/cron_routes.py:369).
    A quoted nickname is the SECOND token (e.g. "Stephanie 'Steph'
    Whitmore"), so splitting on whitespace and taking the first token already
    yields the plain first name -- no quote-stripping needed.
    """
    tokens = str(name or "?").split()
    return tokens[0] if tokens else "?"


def expected_actual_cast(day: str, dialogue: list[dict]) -> dict[str, list[str]]:
    """Expected roster (from scripts.simulate_dialogue_week.participants_for_day)
    vs who actually spoke, both normalized to first name.
    """
    expected = [normalize_first_name(n) for n in participants_for_day(day)]
    expected_set = set(expected)

    actual: list[str] = []
    seen: set[str] = set()
    for msg in dialogue:
        first = normalize_first_name(msg.get("character", "?"))
        if first not in seen:
            seen.add(first)
            actual.append(first)
    actual_set = set(actual)

    return {
        "expected": expected,
        "actual": actual,
        "missing": [n for n in expected if n not in actual_set],
        "unexpected": [n for n in actual if n not in expected_set],
    }


def format_transcript(dialogue: list[dict]) -> list[str]:
    """"Name: message" lines, whitespace collapsed."""
    lines = []
    for msg in dialogue:
        name = normalize_first_name(msg.get("character", "?"))
        message = " ".join((msg.get("message") or "").split())
        lines.append(f"{name}: {message}")
    return lines


# ---------------------------------------------------------------------------
# Judge + QA readback
# ---------------------------------------------------------------------------


def judge_meta(stage: dict, episode: dict, day: str) -> dict[str, Any]:
    """judge_scores / judge_weakest / judge_reason.

    Written after #6861 onto BOTH the stage dict (via cron_routes.py's
    `_judge_meta_fields`, spread next to `judge_verdict`) and
    `episode['judge_scores'][day]` / `judge_weakest` / `judge_reason` (the
    source `_judge_dialogue` writes, cron_routes.py:401-404). In production
    the two agree; this checks the stage first and falls back to the
    episode-level copy so either shape is read correctly. Pre-#6861 episodes
    (W10-W36) have neither -- only the `judge_verdict` string exists.
    """
    stage_scores = stage.get("judge_scores")
    scores = stage_scores if isinstance(stage_scores, dict) and stage_scores else (
        (episode.get("judge_scores") or {}).get(day) or {}
    )

    stage_weakest = stage.get("judge_weakest")
    weakest = stage_weakest if isinstance(stage_weakest, list) and stage_weakest else (
        (episode.get("judge_weakest") or {}).get(day) or []
    )

    stage_reason = stage.get("judge_reason")
    reason = stage_reason if isinstance(stage_reason, str) and stage_reason else (
        (episode.get("judge_reason") or {}).get(day) or ""
    )

    return {"judge_scores": scores, "judge_weakest": weakest, "judge_reason": reason}


def qa_summary(episode: dict, day: str) -> dict[str, Any] | None:
    """score / prompt_echo_hits / min_content_failures /
    cross_character_overlap_penalty for `day`, or None when qa_scores was
    never computed for this episode (pre-#6840 weeks).

    `score` is duplicated at both qa[day]['score'] and
    qa[day]['details']['score']; the other three fields live only under
    'details'. Some call sites may write the fields flat with no 'details'
    wrapper, so fall back to the qa dict itself in that case.
    """
    qa = (episode.get("qa_scores") or {}).get(day)
    if not isinstance(qa, dict):
        return None
    details = qa.get("details")
    details = details if isinstance(details, dict) else qa
    return {
        "score": qa.get("score", details.get("score")),
        "prompt_echo_hits": details.get("prompt_echo_hits"),
        "min_content_failures": details.get("min_content_failures"),
        "cross_character_overlap_penalty": details.get("cross_character_overlap_penalty"),
    }


# ---------------------------------------------------------------------------
# Single-episode report
# ---------------------------------------------------------------------------


def build_header(episode: dict) -> dict[str, Any]:
    stages = episode.get("stages", {}) or {}
    monday = stages.get("monday", {}) or {}
    recipe_data = monday.get("recipe_data") or {}
    gate_trace = monday.get("gate_trace") or []
    attempts = max(
        (int(entry.get("attempt", 0)) for entry in gate_trace if isinstance(entry, dict)),
        default=0,
    )
    return {
        "episode_id": episode.get("episode_id"),
        # NEVER episode['concept'] -- it is the placeholder
        # "Weekly Muffin Pan Recipe" on 23 of 25 published weeks.
        "title": _recipe_title(episode) or None,
        "category": recipe_data.get("category"),
        "cuisine": recipe_data.get("cuisine"),
        "ingredient_count": len(recipe_data.get("ingredients") or []),
        "published_at": episode.get("published_at"),
        "concept_source": monday.get("concept_source"),
        "gate_trace_attempts": attempts,
        "gate_trace_entries": len(gate_trace),
    }


def build_day_report(day: str, episode: dict) -> dict[str, Any]:
    stage = (episode.get("stages", {}) or {}).get(day) or {}
    dialogue = stage.get("dialogue") or []
    judge = judge_meta(stage, episode, day)
    return {
        "day": day,
        "stage_label": stage.get("stage") or day,
        "status": stage.get("status"),
        "message_count": len(dialogue),
        "cast": expected_actual_cast(day, dialogue),
        "transcript": format_transcript(dialogue),
        "judge_verdict": stage.get("judge_verdict"),
        "judge_scores": judge["judge_scores"],
        "judge_weakest": judge["judge_weakest"],
        "judge_reason": judge["judge_reason"],
        "qa_scores": qa_summary(episode, day),
    }


def build_episode_report(episode_id: str, local: bool) -> dict[str, Any]:
    episode, warnings = load_episode(episode_id, local=local)
    return {
        "episode_id": episode_id,
        "summary_line": episode_summary(episode),
        "header": build_header(episode),
        "days": [build_day_report(day, episode) for day in DAY_ORDER],
        "warnings": warnings,
    }


def render_episode_text(report: dict) -> str:
    h = report["header"]
    lines = [
        f"=== {report['episode_id']} ===",
        report["summary_line"],
        f"Title: {h['title'] or '(no recipe yet)'}",
        f"Category: {h['category'] or '?'}  Cuisine: {h['cuisine'] or '?'}  "
        f"Ingredients: {h['ingredient_count']}",
        f"Published at: {h['published_at'] or 'not published'}",
        f"Concept source: {h['concept_source'] or '(not recorded)'}",
        f"Gate trace attempts: {h['gate_trace_attempts']} "
        f"({h['gate_trace_entries']} trace entries)",
        "",
    ]
    for day in report["days"]:
        cast = day["cast"]
        lines.append(f"--- {day['day'].capitalize()} ({day['stage_label']}) ---")
        lines.append(f"status: {day['status'] or 'missing'}  messages: {day['message_count']}")
        lines.append(f"expected cast: {', '.join(cast['expected'])}")
        lines.append(f"actual cast:   {', '.join(cast['actual']) or '(none)'}")
        if cast["missing"]:
            lines.append(f"MISSING: {', '.join(cast['missing'])}")
        if cast["unexpected"]:
            lines.append(f"UNEXPECTED: {', '.join(cast['unexpected'])}")
        if day["message_count"] == 0:
            lines.append("(no dialogue messages)")
        else:
            lines.extend(day["transcript"])
        if day["judge_verdict"] is not None:
            lines.append(f"judge_verdict: {day['judge_verdict']}")
        if day["judge_scores"]:
            lines.append(f"judge_scores: {day['judge_scores']}")
        if day["judge_weakest"]:
            lines.append(f"judge_weakest: {day['judge_weakest']}")
        if day["judge_reason"]:
            lines.append(f"judge_reason: {day['judge_reason']}")
        qa = day["qa_scores"]
        if qa:
            lines.append(
                f"qa_scores: score={qa['score']} "
                f"prompt_echo_hits={qa['prompt_echo_hits']} "
                f"min_content_failures={qa['min_content_failures']} "
                f"cross_character_overlap_penalty={qa['cross_character_overlap_penalty']}"
            )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------------------
# --coverage: catalog x episode
# ---------------------------------------------------------------------------


def local_episode_ids(episodes_dir: Path) -> set[str]:
    """Plain weekly episode ids that exist on disk. This is the only way to
    discover episode JSONs the catalog does not reference -- the CDN has no
    public directory listing, and the authenticated blob list API needs
    BLOB_READ_WRITE_TOKEN, which this zero-cost tool never touches. So this
    always scans local disk, independent of the --local flag.
    """
    if not episodes_dir.exists():
        return set()
    return {p.stem for p in episodes_dir.glob("*.json") if _PLAIN_EPISODE_ID_RE.match(p.stem)}


def build_coverage_rows(catalog: list[dict], episodes_by_id: dict[str, dict | None]) -> list[dict]:
    """One row per catalog recipe. `episodes_by_id` maps episode_id -> episode
    dict, or None when the id is in the catalog but no episode JSON was found.
    """
    rows: list[dict[str, Any]] = []
    for entry in catalog:
        episode_id = entry.get("episode_id")
        slug = entry.get("slug")
        catalog_title = entry.get("title")

        if not episode_id:
            rows.append({
                "slug": slug, "episode_id": None, "seed": True,
                "episode_found": None, "days_with_dialogue": 0,
                "message_count": 0, "judge_scores_present": False,
                "catalog_title": catalog_title, "episode_title": None,
            })
            continue

        episode = episodes_by_id.get(episode_id)
        found = episode is not None
        days_with_dialogue = 0
        message_count = 0
        judge_present = False
        episode_title = None

        if found:
            stages = episode.get("stages", {}) or {}
            for day in DAY_ORDER:
                stage = stages.get(day) or {}
                dialogue = stage.get("dialogue") or []
                if dialogue:
                    days_with_dialogue += 1
                    message_count += len(dialogue)
                if judge_meta(stage, episode, day)["judge_scores"]:
                    judge_present = True
            episode_title = _recipe_title(episode) or None

        rows.append({
            "slug": slug, "episode_id": episode_id, "seed": False,
            "episode_found": found, "days_with_dialogue": days_with_dialogue,
            "message_count": message_count, "judge_scores_present": judge_present,
            "catalog_title": catalog_title, "episode_title": episode_title,
        })
    return rows


def build_coverage_summary(rows: list[dict]) -> dict[str, int]:
    with_episode_id = sum(1 for r in rows if r["episode_id"])
    with_dialogue = sum(1 for r in rows if r["episode_id"] and r["message_count"] > 0)
    return {
        "total_recipes": len(rows),
        "with_episode_id": with_episode_id,
        "with_dialogue": with_dialogue,
    }


def find_orphan_episode_ids(catalog: list[dict], local_ids: set[str]) -> list[str]:
    catalog_ids = {e.get("episode_id") for e in catalog if e.get("episode_id")}
    return sorted(local_ids - catalog_ids)


def find_title_mismatches(rows: list[dict]) -> list[dict]:
    mismatches = []
    for r in rows:
        if r["seed"] or not r["episode_found"]:
            continue
        catalog_title = (r["catalog_title"] or "").strip()
        episode_title = (r["episode_title"] or "").strip()
        if catalog_title and episode_title and catalog_title.lower() != episode_title.lower():
            mismatches.append({
                "episode_id": r["episode_id"],
                "catalog_title": r["catalog_title"],
                "episode_title": r["episode_title"],
            })
    return mismatches


def build_coverage_report(local: bool) -> dict[str, Any]:
    catalog, warnings = load_catalog(local=local)

    episodes_by_id: dict[str, dict | None] = {}
    for entry in catalog:
        episode_id = entry.get("episode_id")
        if not episode_id or episode_id in episodes_by_id:
            continue  # fetch each episode at most once
        try:
            episode, ep_warnings = load_episode(episode_id, local=local)
            episodes_by_id[episode_id] = episode
            warnings.extend(ep_warnings)
        except FileNotFoundError:
            episodes_by_id[episode_id] = None
            warnings.append(
                f"episode {episode_id} is in the catalog but no episode JSON "
                f"was found (local or CDN)"
            )

    rows = build_coverage_rows(catalog, episodes_by_id)
    return {
        "rows": rows,
        "summary": build_coverage_summary(rows),
        "orphan_episode_ids": find_orphan_episode_ids(catalog, local_episode_ids(EPISODES_DIR)),
        "title_mismatches": find_title_mismatches(rows),
        "warnings": warnings,
    }


def render_coverage_text(report: dict) -> str:
    lines = ["=== Coverage ==="]
    for r in report["rows"]:
        if r["seed"]:
            lines.append(f"{r['slug']}: seed - no conversation by design")
            continue
        lines.append(
            f"{r['slug']} ({r['episode_id']}): found={r['episode_found']} "
            f"days_with_dialogue={r['days_with_dialogue']} "
            f"messages={r['message_count']} "
            f"judge_scores_present={r['judge_scores_present']}"
        )

    s = report["summary"]
    lines.append("")
    lines.append(
        f"Totals: {s['total_recipes']} recipes, {s['with_episode_id']} with "
        f"episode_id, {s['with_dialogue']} with dialogue"
    )

    if report["orphan_episode_ids"]:
        lines.append("")
        lines.append("Episode JSONs on the local mirror not referenced by the catalog:")
        for episode_id in report["orphan_episode_ids"]:
            lines.append(f"  {episode_id}")

    if report["title_mismatches"]:
        lines.append("")
        lines.append("Title mismatches (catalog vs episode):")
        for m in report["title_mismatches"]:
            lines.append(
                f"  {m['episode_id']}: catalog={m['catalog_title']!r} "
                f"episode={m['episode_title']!r}"
            )

    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------------------
# --rubric
# ---------------------------------------------------------------------------


def build_rubric_report() -> dict[str, Any]:
    return {
        "dimensions": list(_RUBRIC_DIMENSIONS),
        "table_header": "| Week (Title) | Tit | Arc | Voice | Tech | Prog | P/D | promptV | Notes |",
        "table_row_template": "| ___ | _ | _ | _ | _ | _ | _ | ___ | ___ |",
        "warnings": [],
    }


def render_rubric_text(report: dict) -> str:
    lines = [
        "Six dimensions: " + ", ".join(report["dimensions"]),
        report["table_header"],
        report["table_row_template"],
    ]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Zero-cost weekly measurement of a muffinpanrecipes episode: real "
            "title (never the placeholder concept), expected-vs-actual cast "
            "per day, transcripts, judge verdicts/scores, and QA scores. No "
            "paid API calls -- reads the public blob CDN or the local "
            "data/episodes mirror."
        ),
    )
    parser.add_argument(
        "episode_id",
        nargs="?",
        default=None,
        help="ISO week episode id, e.g. 2026-W37. Required unless --coverage or --rubric.",
    )
    parser.add_argument(
        "--local",
        action="store_true",
        help=(
            "Read data/episodes/<id>.json (and src/recipes.json for "
            "--coverage) instead of the CDN. Never touches the network. "
            "Prints a STALE MIRROR warning since these files are "
            "write-through mirrors that go stale."
        ),
    )
    parser.add_argument(
        "--json", action="store_true", help="Emit the report as JSON on stdout instead of text."
    )
    parser.add_argument(
        "--coverage",
        action="store_true",
        help="Catalog x episode coverage report instead of a single episode (ignores episode_id).",
    )
    parser.add_argument(
        "--rubric",
        action="store_true",
        help="Print the 6-dimension conversation rubric and the tuning-log table row template.",
    )
    return parser


def _emit(report: dict, renderer, as_json: bool) -> None:
    for warning in report.get("warnings", []):
        print(f"!! {warning}", file=sys.stderr)
    if as_json:
        print(json.dumps(report, indent=2, default=str))
    else:
        print(renderer(report), end="")


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    if args.rubric:
        _emit(build_rubric_report(), render_rubric_text, args.json)
        return 0

    if args.coverage:
        _emit(build_coverage_report(local=args.local), render_coverage_text, args.json)
        return 0

    if not args.episode_id:
        parser.error("episode_id is required unless --coverage or --rubric is given")

    parse_episode_id(args.episode_id)  # raises a clear error on a malformed id
    _emit(build_episode_report(args.episode_id, local=args.local), render_episode_text, args.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
