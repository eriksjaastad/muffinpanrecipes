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
    monkeypatch.setattr(agent, "_call_stability", lambda _key, _prompt, variant=None: _png_bytes())
    monkeypatch.setattr(
        agent, "_evaluate_images_vision",
        lambda _variants, _title: {"passed": True, "recommended_winner": 1},
    )

    task = Task(
        type="photograph_recipe",
        content="Photograph this muffin recipe",
        context={"recipe_id": "test-recipe", "recipe_data": {"title": "Test Muffins"}},
    )

    result = agent.process_task(task)

    assert result.success
    assert result.output["generated_with"] == "stability_api_core"

    # Images written to src/assets/images/{recipe_id}/round_1/{variant}.png
    variant_dir = tmp_path / "src" / "assets" / "images" / "test-recipe" / "round_1"
    assert variant_dir.exists()

    for variant in ("macro_closeup", "overhead_flatlay", "hero_threequarter"):
        assert (variant_dir / f"{variant}.png").exists(), f"Missing variant: {variant}"

    # 3 shots per round, may have multiple rounds if diversity check fails on identical test PNGs
    assert len(result.output["selected_shots"]) >= 3
    assert len(result.output["selected_shots"]) % 3 == 0
    winner = result.output["winner"]
    assert winner["variant"] in {"macro_closeup", "overhead_flatlay", "hero_threequarter"}

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
