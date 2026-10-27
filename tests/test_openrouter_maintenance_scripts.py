"""Maintenance scripts must use OpenRouter for provider access."""

from __future__ import annotations

import json
import sys
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image


def _image_bytes(image_format: str) -> bytes:
    output = BytesIO()
    Image.new("RGB", (2, 3), (20, 40, 60)).save(output, format=image_format)
    return output.getvalue()


def test_direct_harvest_uses_openrouter_image_route(monkeypatch, tmp_path):
    from scripts import direct_harvest

    monkeypatch.delenv("STABILITY_API_KEY", raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "openrouter-test")
    monkeypatch.setattr(direct_harvest, "OUTPUT_ROOT", str(tmp_path))
    calls = []

    def fake_generate(prompt, api_key, **kwargs):
        calls.append((prompt, api_key, kwargs))
        return _image_bytes("PNG")

    monkeypatch.setattr(direct_harvest, "generate_nano_banana_image", fake_generate)

    assert direct_harvest.generate_image("recipe", "hero", "a prompt")

    assert calls == [
        (
            "a prompt",
            "openrouter-test",
            {
                "model": "gemini-3.1-flash-image",
                "aspect_ratio": "1:1",
                "image_size": "1K",
                "timeout_s": 60,
            },
        )
    ]
    with Image.open(tmp_path / "recipe" / "hero.png") as image:
        assert image.format == "PNG"
        assert image.size == (2, 3)


@pytest.mark.parametrize("source_format", ["JPEG", "PNG"])
def test_direct_harvest_normalizes_provider_image_to_png(monkeypatch, tmp_path, source_format):
    from scripts import direct_harvest

    monkeypatch.setenv("OPENROUTER_API_KEY", "openrouter-test")
    monkeypatch.setattr(direct_harvest, "OUTPUT_ROOT", str(tmp_path))
    monkeypatch.setattr(
        direct_harvest,
        "generate_nano_banana_image",
        lambda *_args, **_kwargs: _image_bytes(source_format),
    )

    assert direct_harvest.generate_image("recipe", source_format.lower(), "a prompt")

    output = (tmp_path / "recipe" / f"{source_format.lower()}.png").read_bytes()
    assert output.startswith(b"\x89PNG\r\n\x1a\n")
    with Image.open(BytesIO(output)) as image:
        assert image.format == "PNG"


def test_direct_harvest_skips_existing_output_without_provider_call(monkeypatch, tmp_path):
    from scripts import direct_harvest

    monkeypatch.setattr(direct_harvest, "OUTPUT_ROOT", str(tmp_path))
    output = tmp_path / "recipe" / "hero.png"
    output.parent.mkdir()
    original = b"existing-output"
    output.write_bytes(original)

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("existing output must not trigger an image call")

    monkeypatch.setattr(direct_harvest, "generate_nano_banana_image", fail_if_called)

    assert direct_harvest.generate_image("recipe", "hero", "a prompt")
    assert output.read_bytes() == original


def test_direct_harvest_rejects_malformed_provider_bytes_without_writing(monkeypatch, tmp_path):
    from scripts import direct_harvest

    monkeypatch.setenv("OPENROUTER_API_KEY", "openrouter-test")
    monkeypatch.setattr(direct_harvest, "OUTPUT_ROOT", str(tmp_path))
    monkeypatch.setattr(
        direct_harvest,
        "generate_nano_banana_image",
        lambda *_args, **_kwargs: b"not-an-image",
    )

    assert not direct_harvest.generate_image("recipe", "hero", "a prompt")
    assert not (tmp_path / "recipe" / "hero.png").exists()


def test_list_openai_models_reads_openrouter_catalog(monkeypatch, capsys):
    from scripts import list_openai_models

    monkeypatch.setenv("OPENROUTER_API_KEY", "openrouter-test")
    seen = {}

    class FakeOpenAI:
        def __init__(self, **kwargs):
            seen.update(kwargs)
            self.models = SimpleNamespace(
                list=lambda: SimpleNamespace(
                    data=[
                        SimpleNamespace(id="openai/gpt-5-mini"),
                        SimpleNamespace(id="openai/gpt-5.2"),
                        SimpleNamespace(id="openai/text-embedding-3-small"),
                        SimpleNamespace(id="anthropic/claude-haiku-4.5"),
                    ]
                )
            )

    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=FakeOpenAI))
    list_openai_models.main()

    output = capsys.readouterr().out
    payload = json.loads(output.split("=== json ===", 1)[1])
    assert seen == {
        "base_url": "https://openrouter.ai/api/v1",
        "api_key": "openrouter-test",
    }
    assert payload["allowlisted_chat_models"] == ["gpt-5-mini"]
    assert payload["blocked_chat_models"] == ["gpt-5.2"]
    assert payload["all_models"] == [
        "anthropic/claude-haiku-4.5",
        "openai/gpt-5-mini",
        "openai/gpt-5.2",
        "openai/text-embedding-3-small",
    ]


def test_list_openai_models_honors_openai_model_allowlist(monkeypatch, capsys):
    from scripts import list_openai_models

    monkeypatch.setenv("OPENROUTER_API_KEY", "openrouter-test")
    monkeypatch.setenv("OPENAI_MODEL_ALLOWLIST", "gpt-5-mini")

    class FakeOpenAI:
        def __init__(self, **_kwargs):
            self.models = SimpleNamespace(
                list=lambda: SimpleNamespace(
                    data=[
                        SimpleNamespace(id="openai/gpt-5-mini"),
                        SimpleNamespace(id="openai/gpt-5-nano"),
                    ]
                )
            )

    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=FakeOpenAI))
    list_openai_models.main()

    payload = json.loads(capsys.readouterr().out.split("=== json ===", 1)[1])
    assert payload["allowlisted_chat_models"] == ["gpt-5-mini"]
    assert payload["blocked_chat_models"] == ["gpt-5-nano"]


def test_list_openai_models_requires_openrouter_key(monkeypatch):
    from scripts import list_openai_models

    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    try:
        list_openai_models.main()
    except RuntimeError as exc:
        assert str(exc) == "OPENROUTER_API_KEY is not set"
    else:
        raise AssertionError("missing OpenRouter key must fail closed")


def test_env_template_has_one_ai_provider_key():
    template = (Path(__file__).parents[1] / ".env.example").read_text()
    ai_keys = template.split("# --- Model Selection ---", 1)[0]

    assert "OPENROUTER_API_KEY=" in ai_keys
    for direct_key in (
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "STABILITY_API_KEY",
        "GOOGLE_API_KEY",
    ):
        assert f"{direct_key}=" not in ai_keys
