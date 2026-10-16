import base64
import http.server
import json
import threading
import time

import pytest
import requests

from backend.utils import image_generation, model_router


class _Response:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.text = json.dumps(payload)

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


def test_a_response_without_an_image_raises_with_what_came_back(monkeypatch):
    payload = {"choices": [{"message": {"content": "I can't draw that."}}], "usage": {"cost": 0.001}}
    monkeypatch.setattr(image_generation.requests, "post", lambda *_a, **_k: _Response(payload))
    with pytest.raises(RuntimeError, match="No inline image data.*I can't draw that"):
        image_generation.generate_nano_banana_image("prompt", "or-key")


def test_a_non_200_response_raises_with_its_status_and_body(monkeypatch):
    monkeypatch.setattr(
        image_generation.requests, "post",
        lambda *_a, **_k: _Response({"error": {"message": "upstream overloaded"}}, status_code=502),
    )
    with pytest.raises(RuntimeError, match="502.*upstream overloaded"):
        image_generation.generate_nano_banana_image("prompt", "or-key")


class _SlowHandler(http.server.BaseHTTPRequestHandler):
    """Answers too slowly in a way requests' own per-read timeout can't stop
    in time: `trickle` declares a Content-Length and sends one byte every 0.2s;
    `stall` sends the headers just before the deadline, then nothing, so the
    per-read waits add up to nearly twice the timeout."""

    mode = "trickle"

    def do_POST(self):  # noqa: N802 - stdlib method name
        self.rfile.read(int(self.headers["Content-Length"]))
        body = b" " * 40 + b"{}"
        if self.mode == "stall":
            time.sleep(0.8)
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            if self.mode == "stall":
                self.wfile.flush()
                time.sleep(8)
            for byte in body:
                self.wfile.write(bytes([byte]))
                self.wfile.flush()
                time.sleep(0.2)
        # governance: allow-silent SF001: the client hanging up at its deadline is the behaviour under test
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, *_args):
        pass


@pytest.mark.parametrize("mode", ["trickle", "stall"])
def test_a_slow_response_stops_at_the_total_timeout(monkeypatch, mode):
    handler = type("Handler", (_SlowHandler,), {"mode": mode})
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setattr(model_router, "OPENROUTER_BASE_URL", f"http://127.0.0.1:{server.server_address[1]}")
    # conftest blocks requests.post suite-wide (IndexNow guard); this call only reaches loopback.
    monkeypatch.setattr(image_generation.requests, "post", requests.api.post)
    try:
        started = time.monotonic()
        with pytest.raises(RuntimeError, match="exceeded 1s"):
            image_generation.generate_nano_banana_image("prompt", "or-key", timeout_s=1)
        assert time.monotonic() - started < 3  # either response takes 8s or more
    finally:
        server.shutdown()
        server.server_close()
