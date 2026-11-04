"""Read-only inventory of canonical test-result sources for /admin/tests (#8608).

The page reads a fixed list of sources under ``app.state.project_root`` on every
request; nothing is copied into another store. Each source is one directory or
one file, named in ``SOURCES``. A file that cannot be read or parsed still gets
a row that names the error, so a broken artifact is visible instead of silently
dropped.

A row's ID is ``<source>/<filename>``. The detail route only serves files found
by the same enumeration as the list, so a name cannot escape its source. Two
files in one source whose names differ only by case are both marked ambiguous
and neither is served.

Rows carry a date and say where it came from. File modification times are never
used: a git checkout sets them, so they say nothing about when a run happened.
"""

from __future__ import annotations

import csv
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Source:
    key: str
    rel: Path
    is_dir: bool
    # A required source that is absent is shown as missing; an optional one is
    # local-only (gitignored) and is shown as not present.
    required: bool


SOURCES = (
    Source("results", Path("docs/conversation-lab/results"), True, True),
    Source("experiments", Path("docs/conversation-lab/EXPERIMENTS.md"), False, True),
    Source("simulations", Path("data/simulations"), True, True),
    Source("prompt-research", Path("prompt-research/results.tsv"), False, False),
)
SOURCES_BY_KEY = {source.key: source for source in SOURCES}
RESULTS_SUBDIR = Path("docs") / "conversation-lab" / "results"
# Directory sources list only these; a single-file source may be any of the three.
RESULT_SUFFIXES = (".json", ".md")

_FILENAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*\.(?:json|md|tsv)$")
_STAMP_RE = re.compile(r"(?<!\d)(\d{4})(\d{2})(\d{2})(?:T(\d{2})(\d{2})(\d{2})Z)?(?!\d)")
_MD_DATE_RE = re.compile(r"^Date:\s*(\d{4}-\d{2}-\d{2})\s*$", re.MULTILINE)
# generated_at is when a run finished. A filename stamp beats created_at, which on a
# budget-ledger snapshot is when the ledger was opened, not when it was saved.
_JSON_RUN_DATE_KEYS = ("generated_at",)
_JSON_FALLBACK_DATE_KEYS = ("created_at", "generated_on")
_JSON_LABEL_KEYS = (
    "label",
    "variant_name",
    "panel_id",
    "study",
    "artifact_type",
    "purpose",
    "metric",
)


class SourceUnavailable(Exception):
    """A source that cannot be read; the message says why, for display."""


def _source_path(project_root: Path, source: Source) -> Path:
    """The source's path if it is present and not a symlink, else SourceUnavailable."""
    path = project_root / source.rel
    if path.is_symlink():
        raise SourceUnavailable("is a symlink; refused")
    if source.is_dir and path.is_dir():
        return path
    if not source.is_dir and path.is_file():
        return path
    raise SourceUnavailable("not present")


def _plain_file(directory: Path, filename: str) -> Path | None:
    """``directory/filename`` if it is a regular, non-symlink file directly inside it."""
    if ".." in filename or not _FILENAME_RE.match(filename):
        return None
    candidate = directory / filename
    if candidate.is_symlink() or not candidate.is_file():
        return None
    if candidate.resolve().parent != directory.resolve():
        return None
    return candidate


def _case_twins(directory: Path, filename: str) -> bool:
    """True when another entry in ``directory`` differs from ``filename`` only by case."""
    return sum(1 for p in directory.iterdir() if p.name.lower() == filename.lower()) > 1


def resolve_result(directory: Path, filename: str) -> Path | None:
    """Return the result file named ``filename``, or None if it is not one.

    It must be a plain name with a result suffix that ``list_results`` would
    list: a regular, non-symlink file directly inside ``directory`` with no
    case-twin.
    """
    path = _plain_file(directory, filename)
    if path is None or path.suffix not in RESULT_SUFFIXES or _case_twins(directory, filename):
        return None
    return path


def resolve(project_root: Path, source_key: str, filename: str) -> Path | None:
    """The file ``filename`` of source ``source_key``, or None if it is not served."""
    source = SOURCES_BY_KEY.get(source_key)
    if source is None:
        return None
    try:
        path = _source_path(project_root, source)
    except SourceUnavailable:
        return None
    if source.is_dir:
        return resolve_result(path, filename)
    return path if filename == path.name else None


def list_results(directory: Path) -> list[dict[str, Any]]:
    """One summary row per result file in ``directory``, newest first, undated last.

    Raises FileNotFoundError when the directory itself is absent, so the caller
    can report that rather than an empty inventory.
    """
    if not directory.is_dir():
        raise FileNotFoundError(str(directory))
    names = [
        path.name
        for path in directory.iterdir()
        if path.suffix in RESULT_SUFFIXES and _plain_file(directory, path.name)
    ]
    rows = []
    for name in names:
        row = summarize(directory / name)
        if _case_twins(directory, name):
            row["error"] = "ambiguous: another file differs only by case; not served"
        rows.append(row)
    rows.sort(key=lambda r: r["name"], reverse=True)
    rows.sort(key=lambda r: r["date"] or "", reverse=True)
    return rows


def inventory(project_root: Path) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """Rows for every present source, newest first, plus ``{key: reason}`` for absent ones.

    Each row carries its ``source`` key. A missing source is reported, never
    created, and never turned into an empty list.
    """
    rows: list[dict[str, Any]] = []
    absent: dict[str, str] = {}
    for source in SOURCES:
        try:
            path = _source_path(project_root, source)
        except SourceUnavailable as exc:
            absent[source.key] = str(exc)
            continue
        if source.is_dir:
            found = list_results(path)
        elif source.key == "experiments":
            found = summarize_experiment_ledger(path)
        elif source.key == "prompt-research":
            found = summarize_tsv_runs(path)
        else:
            found = [summarize(path)]
        for row in found:
            row["source"] = source.key
        rows.extend(found)
    rows.sort(key=lambda r: r["name"], reverse=True)
    rows.sort(key=lambda r: r["date"] or "", reverse=True)
    return rows, absent


def _empty_row(name: str) -> dict[str, Any]:
    return {
        "name": name, "detail_name": name, "kind": None, "size": None,
        "date": None, "date_source": None, "label": None, "rate": None,
        "cost": None, "verdict": None, "error": None,
    }


def _child_row(path: Path, name: str) -> dict[str, Any]:
    """A parsed run row whose detail link still opens its authoritative source."""
    row = _empty_row(name)
    row["detail_name"] = path.name
    row["kind"] = path.suffix.lstrip(".")
    return row


def _pipe_cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def summarize_experiment_ledger(path: Path) -> list[dict[str, Any]]:
    """Expand the canonical ``## Experiments`` table into one row per run."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        row = summarize(path)
        row["error"] = f"unreadable experiment ledger: {type(exc).__name__}: {exc}"
        return [row]
    section = re.search(r"^## Experiments\s*$([\s\S]*?)(?=^## |\Z)", text, re.MULTILINE)
    if not section:
        return [summarize(path)]
    lines = [line for line in section.group(1).splitlines() if line.startswith("|")]
    if len(lines) < 2:
        return []
    header = _pipe_cells(lines[0])
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(lines[2:], start=3):
        cells = _pipe_cells(line)
        if len(cells) != len(header):
            row = _child_row(path, f"ledger line {line_number}")
            row["error"] = f"invalid ledger row: {len(cells)} fields; header has {len(header)}"
            rows.append(row)
            continue
        record = dict(zip(header, cells, strict=True))
        row = _child_row(path, record.get("Experiment ID") or f"ledger line {line_number}")
        row["date"] = record.get("Date") or None
        row["date_source"] = "ledger Date"
        row["label"] = record.get("Lever (one)") or None
        row["rate"] = record.get("Wins/Ties/Losses on target") or None
        row["verdict"] = record.get("Decision") or None
        rows.append(row)
    return rows


def _normalized_header(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")


def _first(record: dict[str, str], *keys: str) -> str | None:
    return next((record[key].strip() for key in keys if record.get(key, "").strip()), None)


def summarize_tsv_runs(path: Path) -> list[dict[str, Any]]:
    """Expand a prompt-research TSV into one source-linked row per record."""
    try:
        text = path.read_text(encoding="utf-8")
        records = [record for record in csv.reader(text.splitlines(), delimiter="\t") if record]
        if not records:
            raise ValueError("no header row")
        header = [_normalized_header(value) for value in records[0]]
        if any(not value for value in header) or len(set(header)) != len(header):
            raise ValueError("header names must be non-empty and unique")
        rows: list[dict[str, Any]] = []
        for line_number, values in enumerate(records[1:], start=2):
            if len(values) != len(header):
                raise ValueError(f"line {line_number} has {len(values)} fields; header has {len(header)}")
            record = dict(zip(header, values, strict=True))
            run_id = _first(record, "experiment_id", "run_id", "id", "variant", "name")
            row = _child_row(path, run_id or f"TSV row {line_number}")
            row["date"] = _first(record, "date", "timestamp", "created_at", "generated_at")
            row["date_source"] = "TSV date column" if row["date"] else None
            row["label"] = _first(record, "what_changed", "change", "lever", "prompt", "notes", "variant")
            row["rate"] = _first(record, "pass_rate", "win_rate", "score", "result")
            cost = _first(record, "cost", "cost_usd", "total_cost_usd")
            row["cost"] = f"${cost}" if cost and not cost.startswith("$") else cost
            row["verdict"] = _first(record, "verdict", "decision", "status", "outcome")
            rows.append(row)
        return rows
    except (OSError, UnicodeDecodeError, ValueError, csv.Error) as exc:
        row = summarize(path)
        row["error"] = f"invalid TSV: {exc}"
        return [row]


def summarize(path: Path) -> dict[str, Any]:
    """Summary row for one file. Read and parse errors land in ``error``."""
    row = _empty_row(path.name)
    row["kind"] = path.suffix.lstrip(".")
    data: Any = None
    try:
        raw = path.read_bytes()
        row["size"] = len(raw)
        text = raw.decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        row["error"] = f"unreadable: {type(exc).__name__}: {exc}"
        _apply_filename_date(row)
        return row

    if row["kind"] == "md":
        _summarize_markdown(row, text)
    elif row["kind"] == "tsv":
        try:
            _summarize_tsv(row, text)
        except (ValueError, csv.Error) as exc:
            row["error"] = f"invalid TSV: {exc}"
    else:
        try:
            data = json.loads(text, parse_constant=_reject_nonfinite_json)
        except ValueError as exc:
            row["error"] = f"invalid JSON: {exc}"
        else:
            if isinstance(data, dict):
                try:
                    _summarize_json(row, data)
                except ValueError as exc:
                    row["error"] = f"invalid result data: {exc}"
            else:
                row["error"] = f"unexpected JSON top level: {type(data).__name__}, not an object"
    if row["date"] is None:
        _apply_filename_date(row)
    if row["date"] is None and isinstance(data, dict):
        _apply_json_date(row, data, _JSON_FALLBACK_DATE_KEYS)
    return row


def _apply_filename_date(row: dict[str, Any]) -> None:
    match = _STAMP_RE.search(row["name"])
    if not match:
        return
    year, month, day, hh, mm, ss = match.groups()
    row["date"] = f"{year}-{month}-{day}" + (f"T{hh}:{mm}:{ss}Z" if hh else "")
    row["date_source"] = "filename"


def _reject_nonfinite_json(value: str) -> None:
    raise ValueError(f"non-finite number {value} is not valid result data")


def _apply_json_date(row: dict[str, Any], data: dict[str, Any], keys: tuple[str, ...]) -> None:
    key = next((k for k in keys if _str(data.get(k))), None)
    if key:
        row["date"], row["date_source"] = data[key], key


def _summarize_markdown(row: dict[str, Any], text: str) -> None:
    date = _MD_DATE_RE.search(text)
    if date:
        row["date"], row["date_source"] = date.group(1), "Date: line"
    heading = next((ln[2:].strip() for ln in text.splitlines() if ln.startswith("# ")), None)
    row["label"] = heading
    verdict = re.search(r"^## Verdict\s*\n+(.+?)(?:\n\s*\n|\Z)", text, re.MULTILINE | re.DOTALL)
    if verdict:
        row["verdict"] = " ".join(verdict.group(1).split())


def _summarize_tsv(row: dict[str, Any], text: str) -> None:
    """Header columns and data-row count. Every data row must match the header width."""
    records = [r for r in csv.reader(text.splitlines(), delimiter="\t") if r]
    if not records:
        raise ValueError("no header row")
    header, data = records[0], records[1:]
    for number, record in enumerate(data, start=2):
        if len(record) != len(header):
            raise ValueError(
                f"line {number} has {len(record)} fields; header has {len(header)}"
            )
    row["label"] = f"TSV · {len(data)} rows · columns: {', '.join(header)}"


def _str(value: Any) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except OverflowError:
        raise ValueError("numeric value is too large") from None
    if not math.isfinite(number):
        raise ValueError("numeric value is not finite")
    return number


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _summarize_json(row: dict[str, Any], data: dict[str, Any]) -> None:
    _apply_json_date(row, data, _JSON_RUN_DATE_KEYS)

    label = next((_str(data.get(k)) for k in _JSON_LABEL_KEYS if _str(data.get(k))), None)
    context = " · ".join(
        s for s in (_str(data.get("command")), _str(data.get("mode")), _str(data.get("stage"))) if s
    )
    if not label and "budget_microusd" in data:
        label = "budget ledger snapshot"
    row["label"] = " — ".join(s for s in (context, label) if s) or None

    aggregate = _dict(data.get("aggregate"))
    pass_rate = _num(aggregate.get("pass_rate"))
    target_win = _num(data.get("target_win_rate"))
    if pass_rate is not None:
        passed, judged = _num(aggregate.get("pass_count")), _num(aggregate.get("judged_runs"))
        counts = f" ({passed:g}/{judged:g})" if passed is not None and judged is not None else ""
        row["rate"] = f"pass {pass_rate:.0%}{counts}"
    elif target_win is not None and (_num(data.get("completed_pairs")) or 0) > 0:
        # A run that completed no pairs reports 0.0, which is not a measured rate.
        target = _str(data.get("target_dimension"))
        row["rate"] = f"target win {target_win:.0%} of {data['completed_pairs']:g} pairs" + (
            f" on {target}" if target else ""
        )

    totals = _dict(data.get("totals"))
    cost = next(
        (
            c
            for c in (
                _num(data.get("total_cost_usd")),
                _num(_dict(data.get("cost_summary")).get("total_cost")),
                _num(_dict(data.get("cost_usd")).get("total_all_attempts")),
            )
            if c is not None
        ),
        None,
    )
    if cost is not None:
        row["cost"] = f"${cost:.2f}"
    elif _num(totals.get("actual_microusd")) is not None and "budget_microusd" in data:
        row["cost"] = f"${totals['actual_microusd'] / 1_000_000:.2f} (ledger total)"

    verdict: list[str] = []
    if data.get("aborted") is True:
        verdict.append("aborted")
    if isinstance(data.get("meets_decision_rule"), bool):
        verdict.append(
            "meets decision rule" if data["meets_decision_rule"] else "fails decision rule"
        )
    if _str(data.get("human_read")):
        verdict.append(f"human read: {data['human_read']}")
    if _str(data.get("error")):
        verdict.append(f"error: {data['error']}")
    row["verdict"] = "; ".join(verdict) or None
