"""Pan library and the Gemini image call (#8067, #8068)."""

from __future__ import annotations

import tempfile
from datetime import date
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

from backend.agents import art_director as art_director_module
from backend.agents.factory import create_agent
from backend.core.task import Task
from backend.utils.pan_library import MATERIALS, SIZES, pan_for_week


def _standard_pool() -> list[str]:
    return [m for m, spec in MATERIALS.items() if "standard" in spec["sizes"]]


def test_the_same_week_always_gets_the_same_pan():
    assert pan_for_week("2026-W41") == pan_for_week("2026-W41")


def test_consecutive_weeks_never_repeat_a_material_across_a_year_boundary():
    pool = _standard_pool()
    weeks = [f"2026-W{w:02d}" for w in range(48, 54)] + [f"2027-W{w:02d}" for w in range(1, 4)]
    assert len(weeks) == len(pool)  # 2026 has 53 ISO weeks
    materials = [pan_for_week(w).material for w in weeks]
    assert sorted(materials) == sorted(pool)


def test_missing_or_malformed_episode_id_falls_back_to_the_current_week():
    today = date(2026, 10, 7)  # inside 2026-W41
    expected = pan_for_week("2026-W41").material
    assert pan_for_week(None, today=today).material == expected
    assert pan_for_week("not-an-id", today=today).material == expected
    assert pan_for_week("2026-W60", today=today).material == expected


def test_clause_carries_the_real_geometry_and_the_material():
    pan = pan_for_week("2026-W41")
    assert pan.size == "standard"
    assert SIZES["standard"]["geometry"] in pan.clause
    assert MATERIALS[pan.material]["description"] in pan.clause
    assert "3-by-4 grid" in pan.clause


def test_unknown_size_is_refused():
    with pytest.raises(ValueError):
        pan_for_week("2026-W41", size="popover")


def _jpeg_bytes() -> bytes:
    out = BytesIO()
    Image.new("RGB", (8, 8), (200, 120, 40)).save(out, format="JPEG")
    return out.getvalue()


def test_gemini_jpeg_is_stored_as_real_png(monkeypatch):
    seen = {}

    def fake(prompt, api_key, **kwargs):
        seen.update(kwargs, prompt=prompt, api_key=api_key)
        return _jpeg_bytes()

    monkeypatch.setattr(art_director_module, "generate_nano_banana_image", fake)
    data = create_agent("art_director")._call_image_model("k", "a prompt")
    assert data.startswith(b"\x89PNG\r\n\x1a\n")
    assert Image.open(BytesIO(data)).size == (8, 8)
    assert seen["model"] == "gemini-3.1-flash-image" and seen["image_size"] == "2K"


def test_a_2k_render_is_downscaled_to_1536(monkeypatch):
    big = BytesIO()
    Image.new("RGB", (2048, 2048)).save(big, format="PNG")
    monkeypatch.setattr(art_director_module, "generate_nano_banana_image", lambda *_a, **_k: big.getvalue())
    data = create_agent("art_director")._call_image_model("k", "p")
    assert Image.open(BytesIO(data)).size == (1536, 1536)
    assert data.startswith(b"\x89PNG\r\n\x1a\n")


def test_every_shot_prompt_describes_the_weeks_pan(tmp_path: Path, monkeypatch):
    agent = create_agent("art_director")
    prompts = []
    png = BytesIO()
    Image.new("RGB", (4, 4)).save(png, format="PNG")
    monkeypatch.setattr(agent, "_repo_root", lambda: tmp_path)
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    monkeypatch.setattr(agent, "_call_image_model", lambda _key, prompt: prompts.append(prompt) or png.getvalue())
    monkeypatch.setattr(art_director_module, "_check_visual_diversity", lambda _p: True)
    monkeypatch.setattr(agent, "_evaluate_images_vision",
                        lambda *_a: {"passed": True, "recommended_winner": 1})
    result = agent.process_task(Task(
        type="photograph_recipe", content="shoot",
        context={"recipe_id": "rid", "recipe_data": {"title": "Egg Cups"}, "episode_id": "2026-W42"},
    ))
    pan = pan_for_week("2026-W42")
    assert len(prompts) == 3 and all(pan.clause in p for p in prompts)
    assert result.output["pan"] == {"size": "standard", "material": pan.material, "library": "panV1"}
    assert result.output["generated_with"] == "gemini-3.1-flash-image"


def test_orchestrator_hands_the_episode_id_to_the_art_director():
    from backend.orchestrator import RecipeOrchestrator

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        orch = RecipeOrchestrator(
            data_dir=tmp / "output", message_storage=tmp / "messages", memory_storage=tmp / "memories",
        )
        seen = {}

        class _Result:
            success = True
            output = {"ok": True}

        def capture(task):
            seen.update(task.context)
            return _Result()

        orch.agents["art_director"].process_task = capture
        assert orch._execute_stage_photography("rid", {}, episode_id="2026-W41") == {"ok": True}
        assert seen["episode_id"] == "2026-W41"
