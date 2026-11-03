"""Public OAuth endpoints answer 429 before any provider work once a client
exceeds its per-minute budget, while normal sign-ins still go through (#8861)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.admin import routes


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("JWT_SECRET", "test-secret-for-rate-limit-tests")
    monkeypatch.setattr(routes, "_LOGIN_LIMITER", routes._SlidingWindowRateLimiter("Auth login", limit=10))
    monkeypatch.setattr(
        routes, "_CALLBACK_LIMITER", routes._SlidingWindowRateLimiter("Auth callback", limit=10)
    )
    app = FastAPI()
    app.state.project_root = tmp_path
    app.state.oauth_client = MagicMock()
    app.state.oauth_client.get_authorization_url.return_value = ("https://accounts.example/auth", "st8")
    app.state.oauth_client.handle_callback = AsyncMock(return_value={"email": "admin@example.com"})
    app.state.session_manager = MagicMock()
    app.state.session_manager.create_token.return_value = "jwt"
    routes.create_routes(app)
    return TestClient(app, follow_redirects=False)


def test_login_is_limited_after_ten_requests_per_minute(client):
    oauth = client.app.state.oauth_client
    for _ in range(10):
        assert client.get("/auth/login").status_code == 307

    response = client.get("/auth/login")

    assert response.status_code == 429
    assert oauth.get_authorization_url.call_count == 10


def test_callback_is_limited_before_token_exchange_and_full_sign_in_still_works(client):
    oauth = client.app.state.oauth_client
    client.cookies.set("oauth_state", routes._sign_oauth_state("st8"))
    for _ in range(10):
        response = client.get("/auth/callback", params={"code": "c", "state": "st8"})
        assert response.status_code == 307
        assert response.headers["location"] == "/admin/"
        client.cookies.set("oauth_state", routes._sign_oauth_state("st8"))

    response = client.get("/auth/callback", params={"code": "c", "state": "st8"})

    assert response.status_code == 429
    assert oauth.handle_callback.await_count == 10
