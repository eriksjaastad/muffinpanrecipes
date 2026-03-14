"""Integration tests for Vercel Blob episode persistence (#5048).

Tests save/load/list operations on _CloudBackend, mocking the Vercel Blob
REST API to verify correct request structure, caching, and fallback behavior.
"""

import os
from unittest.mock import patch, MagicMock

import pytest


@pytest.fixture(autouse=True)
def _no_vercel_env(monkeypatch):
    """Ensure VERCEL_ENV is not set so _CloudBackend doesn't raise on init."""
    monkeypatch.delenv("VERCEL_ENV", raising=False)
    monkeypatch.delenv("BLOB_READ_WRITE_TOKEN", raising=False)


@pytest.fixture
def cloud_backend():
    """Create a _CloudBackend with a fake blob token."""
    from backend.storage import _CloudBackend

    with patch.dict(os.environ, {"BLOB_READ_WRITE_TOKEN": "fake-token-for-test"}):
        backend = _CloudBackend()
    return backend


@pytest.fixture
def sample_episode():
    return {
        "episode_id": "ep-test-001",
        "concept": "Test Muffins",
        "messages": [{"character": "Steph", "message": "Let's go!", "day": "monday"}],
        "created_at": "2026-03-14T00:00:00Z",
    }


class TestCloudBackendSaveEpisode:
    def test_save_puts_to_blob_api(self, cloud_backend, sample_episode):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"url": "https://blob.vercel-storage.com/episodes/ep-test-001.json"}
        mock_resp.raise_for_status = MagicMock()

        with patch("requests.put", return_value=mock_resp) as mock_put:
            # Also mock filesystem fallback
            with patch.object(cloud_backend._fs, "save_episode"):
                cloud_backend.save_episode("ep-test-001", sample_episode)

        mock_put.assert_called_once()
        call_args = mock_put.call_args
        assert "episodes/ep-test-001.json" in call_args[0][0]
        headers = call_args[1]["headers"]
        assert headers["Authorization"] == "Bearer fake-token-for-test"
        assert headers["x-allow-overwrite"] == "1"
        assert headers["x-add-random-suffix"] == "0"

    def test_save_updates_memory_cache(self, cloud_backend, sample_episode):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"url": "https://example.com/ep.json"}
        mock_resp.raise_for_status = MagicMock()

        with patch("requests.put", return_value=mock_resp):
            with patch.object(cloud_backend._fs, "save_episode"):
                cloud_backend.save_episode("ep-test-001", sample_episode)

        assert cloud_backend._episode_cache["ep-test-001"] == sample_episode

    def test_save_raises_on_api_failure(self, cloud_backend, sample_episode):
        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = Exception("500 Server Error")

        with patch("requests.put", return_value=mock_resp):
            with pytest.raises(Exception, match="500 Server Error"):
                cloud_backend.save_episode("ep-test-001", sample_episode)

    def test_save_with_prefix(self, cloud_backend, sample_episode):
        cloud_backend.set_prefix("test/")

        mock_resp = MagicMock()
        mock_resp.json.return_value = {"url": "https://example.com/ep.json"}
        mock_resp.raise_for_status = MagicMock()

        with patch("requests.put", return_value=mock_resp) as mock_put:
            with patch.object(cloud_backend._fs, "save_episode"):
                cloud_backend.save_episode("ep-test-001", sample_episode)

        url = mock_put.call_args[0][0]
        assert "test/episodes/ep-test-001.json" in url


class TestCloudBackendLoadEpisode:
    def test_load_returns_cached_data(self, cloud_backend, sample_episode):
        cloud_backend._episode_cache["ep-cached"] = sample_episode
        result = cloud_backend.load_episode("ep-cached")
        assert result == sample_episode

    def test_load_fetches_from_blob_api(self, cloud_backend, sample_episode):
        # Mock list API response
        mock_list = MagicMock()
        mock_list.json.return_value = {
            "blobs": [{"url": "https://cdn.example.com/ep.json"}]
        }
        mock_list.raise_for_status = MagicMock()

        # Mock content fetch
        mock_content = MagicMock()
        mock_content.json.return_value = sample_episode
        mock_content.raise_for_status = MagicMock()

        with patch("requests.get", side_effect=[mock_list, mock_content]):
            result = cloud_backend.load_episode("ep-test-001")

        assert result == sample_episode
        # Should also cache the result
        assert cloud_backend._episode_cache["ep-test-001"] == sample_episode

    def test_load_falls_back_to_filesystem_on_empty_blobs(self, cloud_backend, sample_episode):
        mock_list = MagicMock()
        mock_list.json.return_value = {"blobs": []}
        mock_list.raise_for_status = MagicMock()

        with patch("requests.get", return_value=mock_list):
            with patch.object(cloud_backend._fs, "load_episode", return_value=sample_episode) as mock_fs:
                result = cloud_backend.load_episode("ep-missing")

        mock_fs.assert_called_once_with("ep-missing")
        assert result == sample_episode

    def test_load_falls_back_to_filesystem_on_api_error(self, cloud_backend, sample_episode):
        with patch("requests.get", side_effect=Exception("Network error")):
            with patch.object(cloud_backend._fs, "load_episode", return_value=sample_episode) as mock_fs:
                result = cloud_backend.load_episode("ep-broken")

        mock_fs.assert_called_once_with("ep-broken")
        assert result == sample_episode


class TestCloudBackendListEpisodes:
    def test_list_returns_sorted_episodes(self, cloud_backend):
        ep1 = {"episode_id": "ep-1", "created_at": "2026-03-01T00:00:00Z"}
        ep2 = {"episode_id": "ep-2", "created_at": "2026-03-10T00:00:00Z"}

        mock_list = MagicMock()
        mock_list.json.return_value = {
            "blobs": [
                {"pathname": "episodes/ep-1.json", "url": "https://cdn.example.com/ep1.json"},
                {"pathname": "episodes/ep-2.json", "url": "https://cdn.example.com/ep2.json"},
            ],
            "hasMore": False,
        }
        mock_list.raise_for_status = MagicMock()

        mock_content1 = MagicMock()
        mock_content1.json.return_value = ep1
        mock_content1.raise_for_status = MagicMock()

        mock_content2 = MagicMock()
        mock_content2.json.return_value = ep2
        mock_content2.raise_for_status = MagicMock()

        with patch("requests.get", side_effect=[mock_list, mock_content1, mock_content2]):
            results = cloud_backend.list_episodes()

        assert len(results) == 2
        # Newest first
        assert results[0]["episode_id"] == "ep-2"
        assert results[1]["episode_id"] == "ep-1"

    def test_list_falls_back_on_error(self, cloud_backend):
        with patch("requests.get", side_effect=Exception("timeout")):
            with patch.object(cloud_backend._fs, "list_episodes", return_value=[]) as mock_fs:
                results = cloud_backend.list_episodes()

        mock_fs.assert_called_once()
        assert results == []

    def test_list_handles_pagination(self, cloud_backend):
        # First page
        page1 = MagicMock()
        page1.json.return_value = {
            "blobs": [{"pathname": "episodes/ep-1.json", "url": "https://cdn.example.com/ep1.json"}],
            "hasMore": True,
            "cursor": "abc123",
        }
        page1.raise_for_status = MagicMock()

        # Content for ep-1
        content1 = MagicMock()
        content1.json.return_value = {"episode_id": "ep-1", "created_at": "2026-03-01T00:00:00Z"}
        content1.raise_for_status = MagicMock()

        # Second page
        page2 = MagicMock()
        page2.json.return_value = {
            "blobs": [{"pathname": "episodes/ep-2.json", "url": "https://cdn.example.com/ep2.json"}],
            "hasMore": False,
        }
        page2.raise_for_status = MagicMock()

        # Content for ep-2
        content2 = MagicMock()
        content2.json.return_value = {"episode_id": "ep-2", "created_at": "2026-03-05T00:00:00Z"}
        content2.raise_for_status = MagicMock()

        with patch("requests.get", side_effect=[page1, content1, page2, content2]):
            results = cloud_backend.list_episodes()

        assert len(results) == 2


class TestCloudBackendNoToken:
    def test_falls_back_to_filesystem_without_token(self):
        """Without BLOB_READ_WRITE_TOKEN, all operations should use filesystem."""
        from backend.storage import _CloudBackend

        with patch.dict(os.environ, {}, clear=True):
            # Remove VERCEL_ENV so it doesn't raise
            os.environ.pop("VERCEL_ENV", None)
            os.environ.pop("BLOB_READ_WRITE_TOKEN", None)
            backend = _CloudBackend()

        assert backend._has_cloud() is False

        with patch.object(backend._fs, "save_episode") as mock_save:
            backend.save_episode("ep-local", {"test": True})
        mock_save.assert_called_once()

        with patch.object(backend._fs, "load_episode", return_value=None) as mock_load:
            backend.load_episode("ep-local")
        mock_load.assert_called_once()

    def test_raises_on_vercel_without_token(self, monkeypatch):
        """On Vercel (VERCEL_ENV set) without token, should raise RuntimeError."""
        from backend.storage import _CloudBackend

        monkeypatch.setenv("VERCEL_ENV", "production")
        monkeypatch.delenv("BLOB_READ_WRITE_TOKEN", raising=False)

        with pytest.raises(RuntimeError, match="BLOB_READ_WRITE_TOKEN"):
            _CloudBackend()
