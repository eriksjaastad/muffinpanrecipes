from __future__ import annotations

from pathlib import Path

import pytest

from backend.agents.factory import create_agent
from backend.core.task import Task


@pytest.fixture(autouse=True)
def _featured_image_under_tmp(tmp_path, monkeypatch):
    # The art director also saves the winner through the storage singleton,
    # whose filesystem backend writes under storage.ROOT (#8169); keep that
    # copy and its WebP/JPEG siblings out of the repo's src/assets/images.
    import backend.storage as storage_module

    monkeypatch.setattr(storage_module, "ROOT", tmp_path)


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
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    monkeypatch.setattr(agent, "_call_image_model", lambda _key, _prompt, **_k: _png_bytes())
    monkeypatch.setattr(
        agent, "_evaluate_images_vision",
        lambda _variants, _title, _facts="": {"passed": True, "recommended_winner": 1},
    )

    task = Task(
        type="photograph_recipe",
        content="Photograph this muffin recipe",
        context={"recipe_id": "test-recipe", "recipe_data": {"title": "Test Muffins"}},
    )

    result = agent.process_task(task)

    assert result.success
    assert result.output["generated_with"] == "gemini-3.1-flash-image"

    # Images written to src/assets/images/{recipe_id}/{generation_id}/round_1/{variant}.png
    generation_id = result.output["generation_id"]
    assert result.output["winner"]["generation_id"] == generation_id
    variant_dir = tmp_path / "src" / "assets" / "images" / "test-recipe" / generation_id / "round_1"
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


def test_art_director_fails_without_google_key(tmp_path: Path, monkeypatch) -> None:
    agent = create_agent("art_director")
    monkeypatch.setattr(agent, "_repo_root", lambda: tmp_path)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)

    task = Task(
        type="photograph_recipe",
        content="Photograph this muffin recipe",
        context={"recipe_id": "missing-key", "recipe_data": {"title": "No Key Muffins"}},
    )

    with pytest.raises(RuntimeError, match="GOOGLE_API_KEY"):
        agent.process_task(task)


def _run_photograph(agent, tmp_path, monkeypatch, extra_context=None):
    monkeypatch.setattr(agent, "_repo_root", lambda: tmp_path)
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    monkeypatch.setattr(agent, "_call_image_model", lambda _key, _prompt, **_k: _png_bytes())
    monkeypatch.setattr(
        agent, "_evaluate_images_vision",
        lambda _variants, _title, _facts="": {"passed": True, "recommended_winner": 1},
    )
    context = {"recipe_id": "test-recipe", "recipe_data": {"title": "Test Muffins"}}
    context.update(extra_context or {})
    task = Task(type="photograph_recipe", content="Photograph", context=context)
    return agent.process_task(task)


def test_reruns_use_disjoint_generation_specific_paths(tmp_path: Path, monkeypatch) -> None:
    import re

    agent = create_agent("art_director")
    first = _run_photograph(agent, tmp_path, monkeypatch)
    second = _run_photograph(agent, tmp_path, monkeypatch)

    assert first.success and second.success
    gen1, gen2 = first.output["generation_id"], second.output["generation_id"]
    assert gen1 != gen2
    for gen in (gen1, gen2):
        assert re.fullmatch(r"g[0-9A-Za-z-]{6,40}", gen)

    paths1 = set(first.output["selected_shots"])
    paths2 = set(second.output["selected_shots"])
    assert paths1 and paths2 and paths1.isdisjoint(paths2)
    for gen, paths in ((gen1, paths1), (gen2, paths2)):
        prefix = f"src/assets/images/test-recipe/{gen}/round_"
        assert all(p.startswith(prefix) for p in paths)
        for p in paths:
            assert (tmp_path / p).exists()
    assert first.output["winner"]["generation_id"] == gen1


def test_valid_generation_id_override_is_used(tmp_path: Path, monkeypatch) -> None:
    agent = create_agent("art_director")
    result = _run_photograph(agent, tmp_path, monkeypatch, {"generation_id": "gOverride-01"})
    assert result.output["generation_id"] == "gOverride-01"
    assert all(
        p.startswith("src/assets/images/test-recipe/gOverride-01/round_")
        for p in result.output["selected_shots"]
    )


@pytest.mark.parametrize(
    "bad", ["../evil", "gshort", "x" + "a" * 10, "g" + "a" * 41, "g abc def", "g../../etc", 123, None, ["g123456"]]
)
def test_invalid_generation_id_override_is_ignored(tmp_path: Path, monkeypatch, bad) -> None:
    agent = create_agent("art_director")
    result = _run_photograph(agent, tmp_path, monkeypatch, {"generation_id": bad})
    gen = result.output["generation_id"]
    assert gen != bad
    assert gen.startswith("g")
    assert all(p.startswith(f"src/assets/images/test-recipe/{gen}/") for p in result.output["selected_shots"])
