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
