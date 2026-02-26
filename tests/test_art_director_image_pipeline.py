from __future__ import annotations

from pathlib import Path

import pytest

from backend.agents.factory import create_agent
from backend.core.task import Task


def _png_bytes() -> bytes:
    # 1x1 transparent PNG
    return (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\x0cIDATx\x9cc\x00\x01"
        b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
    )


def test_art_director_generates_three_variants_and_featured_image(tmp_path: Path, monkeypatch) -> None:
    agent = create_agent("art_director")

    monkeypatch.setattr(agent, "_repo_root", lambda: tmp_path)
    monkeypatch.setenv("STABILITY_API_KEY", "test-key")
    monkeypatch.setattr(agent, "_call_stability", lambda api_key, prompt: _png_bytes())

    task = Task(
        type="photograph_recipe",
        content="Photograph this muffin recipe",
        context={"recipe_id": "test-recipe", "recipe_data": {"title": "Test Muffins"}},
    )

    result = agent.process_task(task)

    assert result.success
    assert result.output["generated_with"] == "stability_api"

    variant_dir = tmp_path / "data" / "images" / "test-recipe"
    assert variant_dir.exists()

    expected_files = [
        variant_dir / "editorial.png",
        variant_dir / "action_steam.png",
        variant_dir / "the_spread.png",
    ]
    for file_path in expected_files:
        assert file_path.exists(), f"Expected variant file missing: {file_path}"

    assert len(result.output["selected_shots"]) == 3
    winner = result.output["winner"]
    assert winner["variant"] in {"editorial", "action_steam", "the_spread"}
    assert "Matched" in winner["rationale"]

    featured = tmp_path / "src" / "assets" / "images" / "test-recipe.png"
    assert featured.exists()


def test_art_director_fails_without_stability_key(tmp_path: Path, monkeypatch) -> None:
    agent = create_agent("art_director")
    monkeypatch.setattr(agent, "_repo_root", lambda: tmp_path)
    monkeypatch.delenv("STABILITY_API_KEY", raising=False)

    task = Task(
        type="photograph_recipe",
        content="Photograph this muffin recipe",
        context={"recipe_id": "missing-key", "recipe_data": {"title": "No Key Muffins"}},
    )

    with pytest.raises(RuntimeError, match="STABILITY_API_KEY"):
        agent.process_task(task)
