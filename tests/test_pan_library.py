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


@pytest.fixture(autouse=True)
def _featured_image_under_tmp(tmp_path, monkeypatch):
    # The art director also saves the winner through the storage singleton,
    # whose filesystem backend writes under storage.ROOT (#8169); keep that
    # copy and its WebP/JPEG siblings out of the repo's src/assets/images.
    import backend.storage as storage_module

    monkeypatch.setattr(storage_module, "ROOT", tmp_path)


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
    monkeypatch.setattr(agent, "_call_image_model", lambda _key, prompt, **_k: prompts.append(prompt) or png.getvalue())
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


def test_the_gemini_request_carries_a_timeout(monkeypatch):
    from google.genai import types

    from backend.utils import image_generation

    seen = {}

    class _Client:
        def __init__(self, api_key, http_options=None):
            seen["http_options"] = http_options

        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

        class models:  # noqa: N801
            @staticmethod
            def generate_content(**_k):
                raise RuntimeError("stop after the client is built")

    monkeypatch.setattr("google.genai.Client", _Client)
    with pytest.raises(RuntimeError, match="stop after"):
        image_generation.generate_nano_banana_image("p", "k", timeout_s=60)
    assert isinstance(seen["http_options"], types.HttpOptions)
    assert seen["http_options"].timeout == 60_000


def test_art_director_asks_for_a_bounded_call(monkeypatch):
    seen = {}

    def fake(prompt, api_key, **kwargs):
        seen.update(kwargs)
        return _jpeg_bytes()

    monkeypatch.setattr(art_director_module, "generate_nano_banana_image", fake)
    create_agent("art_director")._call_image_model("k", "p")
    assert seen["timeout_s"] == 60


def _shoot(agent, tmp_path, monkeypatch, image_call, evaluations):
    monkeypatch.setattr(agent, "_repo_root", lambda: tmp_path)
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    monkeypatch.setattr(agent, "_call_image_model", image_call)
    monkeypatch.setattr(art_director_module, "_check_visual_diversity", lambda _p: True)
    queue = list(evaluations)
    monkeypatch.setattr(agent, "_evaluate_images_vision", lambda *_a: dict(queue.pop(0)))
    return agent.process_task(Task(
        type="photograph_recipe", content="shoot",
        context={"recipe_id": "rid", "recipe_data": {"title": "Egg Cups"}, "episode_id": "2026-W42"},
    ))


def test_a_gemini_failure_fails_the_shoot_loudly(tmp_path: Path, monkeypatch):
    def boom(_key, _prompt, **_k):
        raise RuntimeError("No inline image data found in Nano Banana response")

    with pytest.raises(RuntimeError, match="No inline image data"):
        _shoot(create_agent("art_director"), tmp_path, monkeypatch, boom, [])


def test_the_reshoot_round_keeps_the_weeks_pan(tmp_path: Path, monkeypatch):
    prompts = []
    png = BytesIO()
    Image.new("RGB", (4, 4)).save(png, format="PNG")
    result = _shoot(
        create_agent("art_director"), tmp_path, monkeypatch,
        lambda _key, prompt, **_k: prompts.append(prompt) or png.getvalue(),
        [
            {"passed": False, "review_status": "failed", "retry_eligible": True, "reason": "pan unclear"},
            {"passed": True, "recommended_winner": 1},
        ],
    )
    assert len(result.output["rounds"]) == 2 and len(prompts) == 6
    clause = pan_for_week("2026-W42").clause
    assert all(clause in p for p in prompts)
    assert all("RESHOOT NOTE" in p for p in prompts[3:])


class _Clock:
    """Fake monotonic clock; each image call advances it by ``per_call`` seconds."""

    def __init__(self, per_call: float):
        self.now = 1000.0
        self.per_call = per_call
        self.timeouts: list[float] = []

    def __call__(self) -> float:
        return self.now

    def image(self, _key, _prompt, timeout_s=60):
        self.timeouts.append(timeout_s)
        self.now += self.per_call
        png = BytesIO()
        Image.new("RGB", (4, 4)).save(png, format="PNG")
        return png.getvalue()


_RESHOOT = {"passed": False, "review_status": "failed", "retry_eligible": True, "reason": "pan unclear"}


def test_round_one_out_of_budget_fails_the_shoot(tmp_path: Path, monkeypatch):
    clock = _Clock(per_call=85)  # two calls use 170s; 10s left is under the 20s minimum
    monkeypatch.setattr(art_director_module, "_clock", clock)
    with pytest.raises(art_director_module.ShootBudgetExceeded):
        _shoot(create_agent("art_director"), tmp_path, monkeypatch, clock.image, [])
    assert len(clock.timeouts) == 2
    assert clock.timeouts == [60, 60]


def test_per_call_timeout_is_clipped_to_the_remaining_budget(tmp_path: Path, monkeypatch):
    clock = _Clock(per_call=70)  # 70s, 70s, then 40s left for the third call
    monkeypatch.setattr(art_director_module, "_clock", clock)
    _shoot(create_agent("art_director"), tmp_path, monkeypatch, clock.image,
           [{"passed": True, "recommended_winner": 1}])
    assert clock.timeouts == [60, 60, 40]


def test_reshoot_is_skipped_when_another_round_will_not_fit(tmp_path: Path, monkeypatch):
    clock = _Clock(per_call=35)  # round 1 takes 105s; 75s left < 105s
    monkeypatch.setattr(art_director_module, "_clock", clock)
    result = _shoot(create_agent("art_director"), tmp_path, monkeypatch, clock.image, [_RESHOOT])
    rounds = result.output["rounds"]
    assert len(rounds) == 1 and rounds[0]["reshoot_skipped"] == "time budget"
    assert len(clock.timeouts) == 3
    assert result.output["automated_review_status"] == "failed"  # the human review decides
    assert result.output["winner"]["round"] == 1


def test_a_reshoot_that_runs_out_keeps_round_one_for_review(tmp_path: Path, monkeypatch):
    clock = _Clock(per_call=25)  # round 1 = 75s; 105s left >= 75s, so round 2 starts
    monkeypatch.setattr(art_director_module, "_clock", clock)
    agent = create_agent("art_director")
    calls = {"n": 0}

    def slow_reshoot(key, prompt, timeout_s=60):
        calls["n"] += 1
        if calls["n"] == 4:
            clock.per_call = 90  # round 2 stalls: 90s, then too little left
        return clock.image(key, prompt, timeout_s)

    result = _shoot(agent, tmp_path, monkeypatch, slow_reshoot, [_RESHOOT])
    rounds = result.output["rounds"]
    assert len(rounds) == 1 and rounds[0]["reshoot_skipped"].startswith("time budget:")
    assert result.output["winner"]["round"] == 1
    assert result.output["reshoot_happened"] is False
