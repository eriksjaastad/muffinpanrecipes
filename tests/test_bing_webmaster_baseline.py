"""Bing Webmaster API baseline script (#7806).

The apikey is a query parameter, so `requests`' own exception messages (and
its HTTP error bodies) can carry it. Every failure path here must scrub the
key before it reaches stderr, a log, or a file. No network: requests.get is
mocked throughout; a missing key is exercised without mocking at all, since
that check runs before any request.
"""

from __future__ import annotations

from unittest.mock import Mock, patch

import pytest
import requests

from scripts.bing_webmaster_baseline import BingApiError, _call, _scrub, fetch_baseline, main

FAKE_API_KEY = "synthetic-test-fixture-not-a-real-credential"


def test_scrub_removes_the_key_from_arbitrary_text():
    text = f"GET https://ssl.bing.com/webmaster/api.svc/json/GetCrawlStats?apikey={FAKE_API_KEY}&siteUrl=x"
    assert FAKE_API_KEY not in _scrub(text, FAKE_API_KEY)


def test_call_scrubs_key_on_connection_error():
    with patch(
        "scripts.bing_webmaster_baseline.requests.get",
        side_effect=requests.ConnectionError(
            f"Failed to establish a new connection: apikey={FAKE_API_KEY} unreachable"
        ),
    ):
        with pytest.raises(BingApiError) as excinfo:
            _call("GetCrawlStats", FAKE_API_KEY)

    assert FAKE_API_KEY not in str(excinfo.value)


def test_call_scrubs_key_on_http_error_body():
    response = Mock(status_code=403, text=f"Forbidden: apikey {FAKE_API_KEY} is invalid")
    with patch("scripts.bing_webmaster_baseline.requests.get", return_value=response):
        with pytest.raises(BingApiError) as excinfo:
            _call("GetCrawlStats", FAKE_API_KEY)

    assert FAKE_API_KEY not in str(excinfo.value)
    assert "403" in str(excinfo.value)


def test_call_raises_without_retry_on_429():
    response = Mock(status_code=429, text="rate limited")
    with patch("scripts.bing_webmaster_baseline.requests.get", return_value=response) as get:
        with pytest.raises(BingApiError, match="429"):
            _call("GetCrawlStats", FAKE_API_KEY)
    get.assert_called_once()  # one attempt only — no sleep-and-retry


def test_call_returns_the_d_payload():
    response = Mock(status_code=200)
    response.json.return_value = {"d": {"PagesCrawled": 42}}
    with patch("scripts.bing_webmaster_baseline.requests.get", return_value=response):
        assert _call("GetCrawlStats", FAKE_API_KEY) == {"PagesCrawled": 42}


def test_fetch_baseline_calls_all_three_methods_with_domain_prefixed_url_info():
    response = Mock(status_code=200)
    response.json.return_value = {"d": {}}
    with patch("scripts.bing_webmaster_baseline.requests.get", return_value=response) as get:
        fetch_baseline(FAKE_API_KEY)

    called_methods = [call.args[0].rsplit("/", 1)[-1] for call in get.call_args_list]
    assert called_methods == ["GetCrawlStats", "GetQueryStats", "GetUrlInfo"]
    url_info_call = get.call_args_list[2]
    assert url_info_call.kwargs["params"]["url"] == "domain:muffinpanrecipes.com"


def test_main_fails_loudly_without_a_key(monkeypatch, capsys):
    monkeypatch.delenv("BING_WEBMASTER_API_KEY", raising=False)
    exit_code = main()
    captured = capsys.readouterr()

    assert exit_code == 1
    assert "BING_WEBMASTER_API_KEY" in captured.err


def test_main_fails_loudly_on_api_error_never_writing_an_empty_result(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("BING_WEBMASTER_API_KEY", FAKE_API_KEY)
    monkeypatch.setenv("SEO_AUDIT_DIR", str(tmp_path))

    with patch(
        "scripts.bing_webmaster_baseline.fetch_baseline",
        side_effect=BingApiError("GetCrawlStats failed: HTTP 500: ***"),
    ):
        exit_code = main()

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "GetCrawlStats failed" in captured.err
    assert list(tmp_path.rglob("*.json")) == []  # no partial/empty output on failure


def test_main_writes_the_baseline_json_on_success(monkeypatch, tmp_path):
    monkeypatch.setenv("BING_WEBMASTER_API_KEY", FAKE_API_KEY)
    monkeypatch.setenv("SEO_AUDIT_DIR", str(tmp_path))

    response = Mock(status_code=200)
    response.json.return_value = {"d": {"ok": True}}
    with patch("scripts.bing_webmaster_baseline.requests.get", return_value=response):
        exit_code = main()

    assert exit_code == 0
    written = list(tmp_path.rglob("bing_webmaster.json"))
    assert len(written) == 1
    assert FAKE_API_KEY not in written[0].read_text(encoding="utf-8")
