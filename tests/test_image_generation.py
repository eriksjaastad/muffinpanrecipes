import base64

import pytest

from backend.utils import image_generation, model_router


class _Response:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.text = str(payload)

    def json(self):
        return self._payload


def _image_payload(data: bytes) -> dict:
    url = "data:image/png;base64," + base64.b64encode(data).decode("ascii")
    return {
        "choices": [{"message": {"content": "", "images": [{"type": "image_url", "image_url": {"url": url}}]}}],
        "usage": {"cost": 0.067},
    }


def test_image_comes_back_from_openrouter_as_bytes(monkeypatch):
    sent = {}

    def fake_post(url, **kwargs):
        sent["url"] = url
        sent.update(kwargs)
        return _Response(_image_payload(b"pngdata"))

    monkeypatch.setattr(image_generation.requests, "post", fake_post)
    model_router.reset_cost_log()

    out = image_generation.generate_nano_banana_image(
        "prompt", "or-key", model="gemini-3.1-flash-image", image_size="2K",
    )

    assert out == b"pngdata"
    assert sent["url"] == "https://openrouter.ai/api/v1/chat/completions"
    assert sent["headers"] == {"Authorization": "Bearer or-key"}
    body = sent["json"]
    assert body["model"] == "google/gemini-3.1-flash-image"
    assert body["modalities"] == ["image", "text"]
    assert body["image_config"] == {"aspect_ratio": "1:1", "image_size": "2K"}
    assert body["provider"] == {"order": ["google-ai-studio"], "allow_fallbacks": False}
    (entry,) = model_router.get_cost_entries()
    assert (entry["provider"], entry["model"], entry["actual_cost"]) == (
        "openrouter", "google/gemini-3.1-flash-image", 0.067,
    )


def test_a_response_without_an_image_raises(monkeypatch):
    payload = {"choices": [{"message": {"content": "I can't draw that."}}], "usage": {"cost": 0.001}}
    monkeypatch.setattr(image_generation.requests, "post", lambda *_a, **_k: _Response(payload))
    with pytest.raises(RuntimeError, match="No inline image data"):
        image_generation.generate_nano_banana_image("prompt", "or-key")
