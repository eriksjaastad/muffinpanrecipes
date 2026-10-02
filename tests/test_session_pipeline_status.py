"""Session-start pipeline banner must not print a plain OK when a check was
skipped because the catalog could not be read (silent-failure sweep, #7587)."""

from __future__ import annotations

import importlib
import sys
from unittest.mock import patch

sps = importlib.import_module("scripts.session_pipeline_status")


def _run(catalog_payload, capsys):
    episode = {"episode_id": "2026-W36", "concept": "Custard cups", "stages": {}}

    def fake_get_json(url):
        return episode if "/episodes/" in url else catalog_payload

    seen_catalogs = []

    def fake_failures(ep, catalog=None):
        seen_catalogs.append(catalog)
        return []

    with patch.object(sps, "_get_json", fake_get_json), \
         patch.object(sps, "episode_integrity_failures", fake_failures), \
         patch.object(sps, "episode_summary", lambda ep: "2026-W36 summary"), \
         patch.object(sys, "argv", ["session_pipeline_status.py", "--episode", "2026-W36"]):
        assert sps.main() == 0
    return capsys.readouterr().out.strip(), seen_catalogs


def test_unreadable_catalog_is_flagged_on_the_ok_line(capsys):
    out, seen = _run(None, capsys)
    assert seen == [None]
    assert out.startswith("muffinpanrecipes pipeline: OK")
    assert "catalog unreadable — title-collision check skipped" in out


def test_readable_catalog_prints_plain_ok(capsys):
    out, seen = _run({"recipes": [{"title": "Other Cups"}]}, capsys)
    assert seen == [[{"title": "Other Cups"}]]
    assert out == "muffinpanrecipes pipeline: OK — 2026-W36 summary"
