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
