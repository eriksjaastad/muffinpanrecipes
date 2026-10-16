import base64
import http.server
import json
import threading
import time

import pytest
import requests

from backend.utils import image_generation, model_router


class _Raw:
    def __init__(self, body: bytes):
        self._body = body

    def read1(self, amt, decode_content=True):
        chunk, self._body = self._body[:amt], self._body[amt:]
        return chunk


class _Response:
    """A streamed requests response whose body is read through `raw.read1`."""

    def __init__(self, payload, status_code=200):
        self.status_code = status_code
        self.raw = _Raw(json.dumps(payload).encode())

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


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


class _TrickleHandler(http.server.BaseHTTPRequestHandler):
    """Declares a Content-Length, then sends the body one byte every 0.2s, so
    every read arrives well inside requests' own timeout."""

    def do_POST(self):  # noqa: N802 - stdlib method name
        self.rfile.read(int(self.headers["Content-Length"]))
        body = b" " * 40 + b"{}"
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            for byte in body:
                self.wfile.write(bytes([byte]))
                self.wfile.flush()
                time.sleep(0.2)
        # governance: allow-silent SF001: the client hanging up at its deadline is the behaviour under test
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, *_args):
        pass


def test_a_response_that_trickles_bytes_stops_at_the_total_timeout(monkeypatch):
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _TrickleHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setattr(model_router, "OPENROUTER_BASE_URL", f"http://127.0.0.1:{server.server_address[1]}")
    # conftest blocks requests.post suite-wide (IndexNow guard); this call only reaches loopback.
    monkeypatch.setattr(image_generation.requests, "post", requests.api.post)
    try:
        started = time.monotonic()
        with pytest.raises(RuntimeError, match="exceeded 1s"):
            image_generation.generate_nano_banana_image("prompt", "or-key", timeout_s=1)
        assert time.monotonic() - started < 3  # the full trickle takes ~8s
    finally:
        server.shutdown()
        server.server_close()
