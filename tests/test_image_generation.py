import base64


def test_generate_stability_image_decodes_base64(monkeypatch):
    from backend.utils.image_generation import generate_stability_image

    class DummyResponse:
        status_code = 200

        def json(self):
            return {"artifacts": [{"base64": base64.b64encode(b"pngdata").decode("utf-8")}]}

    def fake_post(*args, **kwargs):
        return DummyResponse()

    monkeypatch.setattr("backend.utils.image_generation.requests.post", fake_post)
    result = generate_stability_image("prompt", "key")
    assert result == b"pngdata"


def test_extract_inline_image_bytes_from_response():
    from backend.utils.image_generation import _extract_inline_image_bytes

    class Inline:
        def __init__(self, data):
            self.data = data

    class Part:
        def __init__(self, data):
            self.inline_data = Inline(data)

    class Response:
        def __init__(self, data):
            self.parts = [Part(data)]

    response = Response(base64.b64encode(b"img").decode("utf-8"))
    assert _extract_inline_image_bytes(response) == b"img"
