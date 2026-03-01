"""Storage abstraction layer for Muffin Pan Recipes.

Provides a unified interface for reading/writing episodes, simulations, and
images. In LOCAL_DEV mode, everything falls back to the local filesystem
(same paths as before). On Vercel, cloud storage backends are used.

Usage:
    from backend.storage import storage

    # Episodes
    data = storage.load_episode("2026-W09")
    storage.save_episode("2026-W09", data)
    episodes = storage.list_episodes()

    # Simulations
    data = storage.load_simulation("abc123")
    storage.save_simulation("abc123", data)
    runs = storage.list_simulations()

    # Images
    url = storage.get_image_url("data/images/abc123/editorial.png")
    storage.save_image("data/images/abc123/editorial.png", image_bytes)
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Optional

from backend.config import config


# ---------------------------------------------------------------------------
# Paths (used for filesystem backend; also as relative key names in cloud)
# ---------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parents[1]
EPISODES_DIR = ROOT / "data" / "episodes"
SIMULATIONS_DIR = ROOT / "data" / "simulations"
IMAGES_DIR = ROOT / "data" / "images"


class _FilesystemBackend:
    """Local filesystem storage — used for LOCAL_DEV."""

    def load_episode(self, episode_id: str) -> Optional[dict]:
        path = EPISODES_DIR / f"{episode_id}.json"
        if not path.exists():
            return None
        return json.loads(path.read_text())

    def save_episode(self, episode_id: str, data: dict) -> None:
        EPISODES_DIR.mkdir(parents=True, exist_ok=True)
        path = EPISODES_DIR / f"{episode_id}.json"
        path.write_text(json.dumps(data, indent=2))

    def list_episodes(self) -> list[dict]:
        """Return episode summary dicts sorted newest first."""
        if not EPISODES_DIR.exists():
            return []
        results = []
        for p in sorted(EPISODES_DIR.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
            try:
                data = json.loads(p.read_text())
                results.append({"episode_id": p.stem, **data})
            except Exception:
                pass
        return results

    def load_simulation(self, sim_id: str) -> Optional[dict]:
        path = SIMULATIONS_DIR / f"{sim_id}.json"
        if not path.exists():
            return None
        return json.loads(path.read_text())

    def save_simulation(self, sim_id: str, data: dict) -> None:
        SIMULATIONS_DIR.mkdir(parents=True, exist_ok=True)
        path = SIMULATIONS_DIR / f"{sim_id}.json"
        path.write_text(json.dumps(data, indent=2))

    def list_simulations(self, limit: int = 100) -> list[dict]:
        """Return simulation summary dicts sorted newest first."""
        if not SIMULATIONS_DIR.exists():
            return []
        results = []
        paths = sorted(SIMULATIONS_DIR.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True)
        for p in paths[:limit]:
            try:
                data = json.loads(p.read_text())
                results.append({"sim_id": p.stem, "path": str(p), **data})
            except Exception:
                pass
        return results

    def save_image(self, relative_path: str, image_bytes: bytes) -> str:
        """Save image bytes and return the local URL path."""
        dest = ROOT / relative_path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(image_bytes)
        # Return URL-style path for templates
        return f"/{relative_path}"

    def get_image_url(self, relative_path: str) -> str:
        """Return URL for serving the image."""
        return f"/{relative_path}"

    def image_exists(self, relative_path: str) -> bool:
        return (ROOT / relative_path).exists()


class _CloudBackend:
    """Vercel-compatible cloud storage backend.

    For Vercel Blob (recommended at our scale — ~$0). Falls back to
    filesystem reads for any episode that was migrated before cloud was set up.

    NOTE: Full Vercel Blob implementation requires the @vercel/blob SDK
    or direct REST API calls. This stub provides the interface so that
    #4973 is structurally complete — wire up the actual SDK in #4974
    when the Vercel Cron routes are built.
    """

    def __init__(self) -> None:
        self._blob_token = os.environ.get("BLOB_READ_WRITE_TOKEN", "")
        self._fs = _FilesystemBackend()  # fallback for local data

    def _has_cloud(self) -> bool:
        return bool(self._blob_token)

    # --- Episodes ---

    def load_episode(self, episode_id: str) -> Optional[dict]:
        if not self._has_cloud():
            # Graceful fallback — cloud not yet configured
            return self._fs.load_episode(episode_id)
        # TODO: fetch from Vercel Blob
        # url = f"https://blob.vercel-storage.com/episodes/{episode_id}.json"
        # response = requests.get(url, headers={"Authorization": f"Bearer {self._blob_token}"})
        # return response.json() if response.ok else None
        return self._fs.load_episode(episode_id)  # stub fallback

    def save_episode(self, episode_id: str, data: dict) -> None:
        if not self._has_cloud():
            self._fs.save_episode(episode_id, data)
            return
        # TODO: PUT to Vercel Blob
        # import requests
        # requests.put(f"https://blob.vercel-storage.com/episodes/{episode_id}.json",
        #              data=json.dumps(data), headers={"Authorization": f"Bearer {self._blob_token}"})
        self._fs.save_episode(episode_id, data)  # stub fallback

    def list_episodes(self) -> list[dict]:
        if not self._has_cloud():
            return self._fs.list_episodes()
        # TODO: LIST from Vercel Blob with prefix=episodes/
        return self._fs.list_episodes()  # stub fallback

    # --- Simulations ---

    def load_simulation(self, sim_id: str) -> Optional[dict]:
        return self._fs.load_simulation(sim_id)  # stub fallback

    def save_simulation(self, sim_id: str, data: dict) -> None:
        self._fs.save_simulation(sim_id, data)  # stub fallback

    def list_simulations(self, limit: int = 100) -> list[dict]:
        return self._fs.list_simulations(limit=limit)  # stub fallback

    # --- Images ---

    def save_image(self, relative_path: str, image_bytes: bytes) -> str:
        if not self._has_cloud():
            return self._fs.save_image(relative_path, image_bytes)
        # TODO: upload to Vercel Blob and return the CDN URL
        # key = relative_path.replace("data/images/", "images/")
        # url = vercel_blob_upload(key, image_bytes, token=self._blob_token)
        # return url
        return self._fs.save_image(relative_path, image_bytes)  # stub fallback

    def get_image_url(self, relative_path: str) -> str:
        if not self._has_cloud():
            return self._fs.get_image_url(relative_path)
        # TODO: return CDN URL from Vercel Blob
        return self._fs.get_image_url(relative_path)  # stub fallback

    def image_exists(self, relative_path: str) -> bool:
        return self._fs.image_exists(relative_path)


# ---------------------------------------------------------------------------
# Singleton — import this everywhere
# ---------------------------------------------------------------------------

def _make_backend() -> _FilesystemBackend | _CloudBackend:
    if config.storage_backend == "filesystem":
        return _FilesystemBackend()
    return _CloudBackend()


storage = _make_backend()
