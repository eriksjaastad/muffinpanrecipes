"""A missing character bio is loud, never a silent fallback (#8146).

`_load_bio` used to return None for a missing bio.md, and the prompt then used
backstory[:600]. That is how production ran without any bio until 2026-10-08
(#8113) with nothing to say so. A missing or empty bio now raises, /health
lists it, and scripts/health_check.py fails on it.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

import scripts.health_check as hc
import scripts.simulate_dialogue_week as sdw


@pytest.fixture
def chars_dir(tmp_path, monkeypatch):
    """A characters dir holding a bio for every cast member, and no cached prompts."""
    for name in sdw.load_personas():
        bio = tmp_path / sdw._char_dir_slug(name) / "bio.md"
        bio.parent.mkdir(parents=True)
        bio.write_text(f"# {name}\n\nA real bio.\n")
    monkeypatch.setattr(sdw, "CHARACTERS_DIR", tmp_path)
    monkeypatch.setattr(sdw, "_system_prompt_cache", {})
    return tmp_path


def _bio(chars_dir, name: str):
    return chars_dir / sdw._char_dir_slug(name) / "bio.md"


def test_every_shipped_character_has_a_bio():
    assert sdw.missing_bios() == []


def test_a_missing_bio_raises_instead_of_using_the_backstory(chars_dir):
    _bio(chars_dir, "Margaret Chen").unlink()  # governance: allow-delete DS001: file the fixture wrote under pytest tmp_path
    persona = sdw.load_personas()["Margaret Chen"]

    with pytest.raises(FileNotFoundError):
        sdw.build_system_prompt(persona)


def test_an_empty_bio_raises(chars_dir):
    _bio(chars_dir, "Devon Park").write_text("  \n")

    with pytest.raises(ValueError, match="empty"):
        sdw._load_bio("Devon Park")


def test_the_prompt_carries_the_bio(chars_dir):
    persona = sdw.load_personas()["Ria Castillo"]

    assert "A real bio." in sdw.build_system_prompt(persona)


def test_missing_bios_names_missing_and_empty_ones(chars_dir):
    _bio(chars_dir, "Margaret Chen").unlink()  # governance: allow-delete DS001: file the fixture wrote under pytest tmp_path
    _bio(chars_dir, "Devon Park").write_text("")

    assert sorted(sdw.missing_bios()) == ["Devon Park", "Margaret Chen"]


def test_health_endpoint_reports_missing_bios(chars_dir, monkeypatch):
    from fastapi.testclient import TestClient

    from backend.admin.app import create_admin_app

    _bio(chars_dir, "Ria Castillo").unlink()  # governance: allow-delete DS001: file the fixture wrote under pytest tmp_path
    monkeypatch.delenv("VERCEL_ENV", raising=False)
    with TestClient(create_admin_app()) as client:
        resp = client.get("/health")

    assert resp.status_code == 200
    assert resp.json()["missing_bios"] == ["Ria Castillo"]


@pytest.mark.parametrize(
    "health, failure",
    [
        ({"status": "healthy", "missing_bios": ["Ria Castillo"]}, "missing bios for ['Ria Castillo']"),
        ({"status": "healthy"}, "predates #8146"),
    ],
    ids=["a-bio-missing", "old-deploy-without-the-field"],
)
def test_health_check_fails_on_a_missing_bio(health, failure):
    with patch.object(hc, "_fetch_json", return_value=health) as fetch:
        report = hc.Report()
        hc.check_character_bios(report, "https://preview.example")

    fetch.assert_called_once_with("https://preview.example/health")
    assert report.failed
    assert failure in report.failed[0][1]


def test_health_check_passes_when_every_bio_ships():
    with patch.object(hc, "_fetch_json", return_value={"missing_bios": []}):
        report = hc.Report()
        hc.check_character_bios(report, "https://preview.example")

    assert not report.failed
    assert report.passed
