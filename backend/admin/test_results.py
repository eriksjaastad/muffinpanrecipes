"""Read-only inventory of conversation-lab result files for /admin/tests (#8608).

The page reads ``docs/conversation-lab/results`` under ``app.state.project_root``
directly on every request; nothing is copied into another store. Every ``.json``
and ``.md`` file in that directory becomes one row, newest first. A file that
cannot be read or parsed still gets a row that names the error, so a broken
artifact is visible instead of silently dropped.

Rows carry a date and say where it came from. File modification times are never
used: a git checkout sets them, so they say nothing about when a run happened.
"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any

RESULTS_SUBDIR = Path("docs") / "conversation-lab" / "results"
RESULT_SUFFIXES = (".json", ".md")

_FILENAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*\.(?:json|md)$")
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


def results_dir(project_root: Path) -> Path:
    return project_root / RESULTS_SUBDIR


def resolve_result(directory: Path, filename: str) -> Path | None:
    """Return the result file named ``filename``, or None if it is not one.

    Only a plain name made of letters, digits, ``.``, ``_`` and ``-`` with a
    result suffix is accepted, and it must resolve to a regular, non-symlink
    file directly inside ``directory``.
    """
    if ".." in filename or not _FILENAME_RE.match(filename):
        return None
    candidate = directory / filename
    if candidate.is_symlink() or not candidate.is_file():
        return None
    if candidate.resolve().parent != directory.resolve():
        return None
    return candidate


def list_results(directory: Path) -> list[dict[str, Any]]:
    """One summary row per result file in ``directory``, newest first, undated last.

    Raises FileNotFoundError when the directory itself is absent, so the caller
    can report that rather than an empty inventory.
    """
    if not directory.is_dir():
        raise FileNotFoundError(str(directory))
    rows = [
        summarize(path)
        for path in directory.iterdir()
        if path.suffix in RESULT_SUFFIXES and resolve_result(directory, path.name)
    ]
    rows.sort(key=lambda r: r["name"], reverse=True)
    rows.sort(key=lambda r: r["date"] or "", reverse=True)
    return rows


def summarize(path: Path) -> dict[str, Any]:
    """Summary row for one file. Read and parse errors land in ``error``."""
    row: dict[str, Any] = {
        "name": path.name,
        "kind": path.suffix.lstrip("."),
        "size": None,
        "date": None,
        "date_source": None,
        "label": None,
        "rate": None,
        "cost": None,
        "verdict": None,
        "error": None,
    }
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
