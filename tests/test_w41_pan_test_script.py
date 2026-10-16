"""The W41 pan experiment script (#8068) bills its LLM and Gemini calls through
OpenRouter (#8343); only its Stability arms stay direct."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

from backend.utils import model_router

SCRIPT = Path(__file__).resolve().parents[1] / "docs/image-lab/results/2026-10-07-w41-pan/run_pan_test.py"


def test_paid_run_routes_photographer_and_gemini_through_openrouter(monkeypatch, tmp_path):
    monkeypatch.setenv("STABILITY_API_KEY", "stability-test")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-test")
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    spec = importlib.util.spec_from_file_location("w41_run_pan_test", SCRIPT)
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)

    material = next(m for m, s in script.LIB["materials"].items() if script.RECIPE_SIZE in s["sizes"])
    llm_models, gemini_keys = [], []
    monkeypatch.setattr(
        model_router, "generate_response",
        lambda prompt, model, temperature: llm_models.append(model) or json.dumps({"material": material, "reason": "r"}),
    )
    monkeypatch.setattr(script, "generate_nano_banana_image", lambda _p, key, **_k: gemini_keys.append(key) or b"jpg")
    monkeypatch.setattr(script, "call_stability", lambda *_a: b"\x89PNG\r\n\x1a\n")
    monkeypatch.setattr(sys, "argv", ["run_pan_test.py", "--repo", ".", "--out", str(tmp_path / "out")])

    script.main()

    assert llm_models == ["anthropic/claude-haiku-4-5-20251001"]
    assert gemini_keys and set(gemini_keys) == {"or-test"}
