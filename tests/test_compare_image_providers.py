from pathlib import Path


def test_flatten_prompts_and_select(tmp_path):
    from scripts.compare_image_providers import flatten_prompts, select_prompts

    jobs = [
        {"recipe_id": "r1", "prompts": {"hero": "a", "macro": "b"}},
        {"recipe_id": "r2", "prompts": {"hero": "c"}},
    ]
    entries = flatten_prompts(jobs)
    assert len(entries) == 3
    picked = select_prompts(entries, 2)
    assert len(picked) == 2


def test_build_comparison_html(tmp_path):
    from scripts.compare_image_providers import build_comparison_html

    rows = [
        {
            "recipe_id": "r1",
            "prompt": "prompt",
            "stability_path": "stability/r1-hero.png",
            "nano_path": "nano_banana/r1-hero.png",
            "stability_time_s": 1.2,
            "nano_time_s": 2.3,
        }
    ]
    out_path = Path(tmp_path) / "grid.html"
    build_comparison_html(rows, out_path)
    content = out_path.read_text()
    assert "Stability vs Nano Banana" in content
    assert "stability/r1-hero.png" in content


def test_cost_override_unset_is_none_and_malformed_raises(monkeypatch):
    import pytest

    from scripts.compare_image_providers import _cost_from_env, estimate_nano_banana_cost

    monkeypatch.delenv("NANOBANANA_COST_PER_IMAGE", raising=False)
    assert _cost_from_env("NANOBANANA_COST_PER_IMAGE") is None
    monkeypatch.setenv("NANOBANANA_COST_PER_IMAGE", "0.04")
    assert estimate_nano_banana_cost("gemini-2.5-flash-image") == 0.04
    monkeypatch.setenv("NANOBANANA_COST_PER_IMAGE", "four cents")
    with pytest.raises(ValueError, match="NANOBANANA_COST_PER_IMAGE must be a number"):
        estimate_nano_banana_cost("gemini-2.5-flash-image")
