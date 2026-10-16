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
    stop: threading.Event  # set by the test's cleanup: hang up mid-body

    def do_POST(self):  # noqa: N802 - stdlib method name
        self.rfile.read(int(self.headers["Content-Length"]))
        body = b" " * 40 + b"{}"
        if self.mode == "stall":
            time.sleep(0.8)
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            # Returning early closes the connection short of Content-Length, so
            # the abandoned worker fails instead of logging a cost into a later test.
            if self.mode == "stall":
                self.wfile.flush()
                if self.stop.wait(8):
                    return
            for byte in body:
                self.wfile.write(bytes([byte]))
                self.wfile.flush()
                if self.stop.wait(0.2):
                    return
        # governance: allow-silent SF001: the client hanging up at its deadline is the behaviour under test
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, *_args):
        pass


@pytest.mark.parametrize("mode", ["trickle", "stall"])
def test_a_slow_response_stops_at_the_total_timeout(monkeypatch, mode):
    stop = threading.Event()
    handler = type("Handler", (_SlowHandler,), {"mode": mode, "stop": stop})
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
        stop.set()
        server.shutdown()
        server.server_close()


class _LateHandler(http.server.BaseHTTPRequestHandler):
    """Finishes a billed image at 1.6s, past a 1s deadline, in four parts
    0.4s apart: well inside requests' own 1s read timeout, so only the
    caller's deadline can end the wait."""

    def do_POST(self):  # noqa: N802 - stdlib method name
        self.rfile.read(int(self.headers["Content-Length"]))
        body = json.dumps(_image_payload(b"late")).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        quarter = -(-len(body) // 4)
        for start in range(0, len(body), quarter):
            time.sleep(0.4)
            self.wfile.write(body[start:start + quarter])
            self.wfile.flush()

    def log_message(self, *_args):
        pass


def test_an_image_billed_after_the_deadline_still_reaches_the_cost_log(monkeypatch):
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _LateHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setattr(model_router, "OPENROUTER_BASE_URL", f"http://127.0.0.1:{server.server_address[1]}")
    # conftest blocks requests.post suite-wide (IndexNow guard); this call only reaches loopback.
    monkeypatch.setattr(image_generation.requests, "post", requests.api.post)
    model_router.reset_cost_log()
    try:
        with pytest.raises(RuntimeError, match="exceeded 1s"):
            image_generation.generate_nano_banana_image("prompt", "or-key", timeout_s=1)
        deadline = time.monotonic() + 5
        while not model_router.get_cost_entries() and time.monotonic() < deadline:
            time.sleep(0.05)
        (entry,) = model_router.get_cost_entries()
        assert entry["actual_cost"] == 0.067
    finally:
        server.shutdown()
        server.server_close()
