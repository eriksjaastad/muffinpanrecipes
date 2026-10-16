"""api_cost_tracker instrumentation in the model router."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from backend.utils import model_router


def test_openrouter_response_is_recorded_centrally_with_its_billed_cost(monkeypatch):
    """Intended with api-cost-tracker 0.2.0: OpenRouter calls reach the central
    tracker with tokens and the billed usage.cost (0.1.x dropped the provider
    silently) (#8038). Runs the real client's extractor; only the POST is captured."""
    client = pytest.importorskip("api_cost_tracker.tracker")  # in the dev extra

    posted = []
    monkeypatch.setattr(client, "_post_to_server", lambda endpoint, payload: posted.append((endpoint, payload)) or True)
    monkeypatch.setattr(model_router, "_central_track", client.track)
    monkeypatch.setattr(model_router, "_record_cost", lambda *args, **kwargs: None)

    response = SimpleNamespace(
        model="openai/gpt-4o-mini",
        usage=SimpleNamespace(prompt_tokens=12, completion_tokens=5, total_tokens=17, cost=0.0031),
        choices=[SimpleNamespace(message=SimpleNamespace(content=" hi "), finish_reason="stop")],
    )
    fake_client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **kwargs: response))
    )

    text, finish, completion_tokens = model_router._openrouter_attempt(
        fake_client, "openai/gpt-4o-mini", [{"role": "user", "content": "x"}], 0.2
    )

    assert (text, finish, completion_tokens) == ("hi", "stop", 5)
    ((endpoint, payload),) = posted
    assert endpoint == "/track"
    assert payload["provider"] == "openrouter"
    assert payload["caller"] == "model_router.openrouter"
    assert (payload["prompt_tokens"], payload["completion_tokens"]) == (12, 5)
    assert payload["estimated_cost_usd"] == 0.0031
