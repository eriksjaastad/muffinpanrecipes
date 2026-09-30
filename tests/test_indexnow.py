"""IndexNow client (#7806): the key is public, but the submission call must
still be bounded — one attempt, no retry loop, and it never raises (the
Sunday publish path treats its result as a report, not an exception it must
catch). No network: every test mocks requests.post.
"""

from __future__ import annotations

from unittest.mock import Mock, patch

import requests

from backend.utils.indexnow import (
    INDEXNOW_HOST,
    INDEXNOW_KEY,
    INDEXNOW_KEY_LOCATION,
    submit_urls,
)


def _urls() -> list[str]:
    return ["https://muffinpanrecipes.com/recipes/some-recipe"]


def test_submit_urls_sends_the_documented_payload_shape():
    response = Mock(status_code=200)
    with patch("backend.utils.indexnow.requests.post", return_value=response) as post:
        result = submit_urls(_urls())

    assert result.ok is True
    assert result.status_code == 200
    post.assert_called_once()
    _args, kwargs = post.call_args
    assert kwargs["json"] == {
        "host": INDEXNOW_HOST,
        "key": INDEXNOW_KEY,
        "keyLocation": INDEXNOW_KEY_LOCATION,
        "urlList": _urls(),
    }
    assert kwargs["timeout"] == 10


def test_submit_urls_accepts_202_as_success():
    response = Mock(status_code=202)
    with patch("backend.utils.indexnow.requests.post", return_value=response):
        result = submit_urls(_urls())
    assert result.ok is True
    assert result.status_code == 202


def test_submit_urls_reports_429_as_failure_without_retrying():
    response = Mock(status_code=429, text="Too Many Requests")
    with patch("backend.utils.indexnow.requests.post", return_value=response) as post:
        result = submit_urls(_urls())

    assert result.ok is False
    assert result.status_code == 429
    post.assert_called_once()  # one attempt only — no sleep-and-retry


def test_submit_urls_reports_http_error_as_failure():
    response = Mock(status_code=403, text="Forbidden")
    with patch("backend.utils.indexnow.requests.post", return_value=response):
        result = submit_urls(_urls())
    assert result.ok is False
    assert result.status_code == 403
    assert "403" in result.detail


def test_submit_urls_never_raises_on_connection_error():
    with patch(
        "backend.utils.indexnow.requests.post",
        side_effect=requests.ConnectionError("boom"),
    ):
        result = submit_urls(_urls())  # must not raise
    assert result.ok is False
    assert result.status_code is None


def test_submit_urls_with_no_urls_is_a_reported_noop():
    with patch("backend.utils.indexnow.requests.post") as post:
        result = submit_urls([])
    assert result.ok is False
    post.assert_not_called()
