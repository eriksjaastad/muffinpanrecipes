import pytest


def test_parse_google_model():
    from backend.utils.model_router import parse_model

    routed = parse_model("google/gemini-3.1-pro-preview")
    assert routed.provider == "google"
    assert routed.model == "gemini-3.1-pro-preview"


def test_google_allowlist_rejects_unknown(monkeypatch):
    from backend.utils.model_router import ensure_google_model_allowed

    monkeypatch.delenv("GOOGLE_MODEL_ALLOWLIST", raising=False)
    with pytest.raises(RuntimeError):
        ensure_google_model_allowed("gemini-2.5-flash")


def test_generate_response_requires_google_key(monkeypatch):
    from backend.utils import model_router

    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)

    with pytest.raises(RuntimeError):
        model_router.generate_response(
            prompt="hello",
            model="google/gemini-3.1-pro-preview",
            temperature=0.1,
        )
