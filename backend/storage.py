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
    url = storage.get_image_url("src/assets/images/abc123/editorial.png")
    storage.save_image("src/assets/images/abc123/editorial.png", image_bytes)
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

from backend.config import config
from backend.utils.logging import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Paths (used for filesystem backend; also as relative key names in cloud)
# ---------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parents[1]
EPISODES_DIR = ROOT / "data" / "episodes"
SIMULATIONS_DIR = ROOT / "data" / "simulations"
IMAGES_DIR = ROOT / "src" / "assets" / "images"


class _FilesystemBackend:
    """Local filesystem storage — used for LOCAL_DEV."""

    def _safe_path(self, relative_path: str) -> Path:
        """Resolve path and validate it stays under ROOT (prevents path traversal)."""
        dest = (ROOT / relative_path).resolve()
        if not dest.is_relative_to(ROOT.resolve()):
            raise ValueError(f"Path traversal blocked: {relative_path!r}")
        return dest

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
            logger.warning(f"Episodes directory does not exist: {EPISODES_DIR}")
            return []
        results = []
        for p in sorted(EPISODES_DIR.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
            try:
                data = json.loads(p.read_text())
                results.append({"episode_id": p.stem, **data})
            except Exception as exc:
                logger.warning(f"Skipping invalid episode file {p.name}: {exc}")
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
            logger.warning(f"Simulations directory does not exist: {SIMULATIONS_DIR}")
            return []
        results = []
        paths = sorted(SIMULATIONS_DIR.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True)
        for p in paths[:limit]:
            try:
                data = json.loads(p.read_text())
                results.append({"sim_id": p.stem, "path": str(p), **data})
            except Exception as exc:
                logger.warning(f"Skipping invalid simulation file {p.name}: {exc}")
        return results

    def save_image(self, relative_path: str, image_bytes: bytes) -> str:
        """Save image bytes and return the local URL path."""
        dest = self._safe_path(relative_path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(image_bytes)
        # Strip src/ prefix — static mount serves src/ at /static, assets at /assets
        url_path = relative_path.removeprefix("src/")
        return f"/{url_path}"

    def get_image_url(self, relative_path: str) -> str:
        """Return URL for serving the image."""
        self._safe_path(relative_path)  # validate — raises if traversal detected
        url_path = relative_path.removeprefix("src/")
        return f"/{url_path}"

    def image_exists(self, relative_path: str) -> bool:
        return self._safe_path(relative_path).exists()

    def cleanup_image_variants(self, recipe_id: str) -> list[str]:
        """Trash the round directories for a recipe. Keeps {recipe_id}.png (the winner).

        Returns list of paths that were trashed.
        """
        from send2trash import send2trash

        variant_dir = IMAGES_DIR / recipe_id
        trashed: list[str] = []

        if variant_dir.exists() and variant_dir.is_dir():
            try:
                send2trash(str(variant_dir))
                trashed.append(str(variant_dir))
                logger.info(f"Trashed image variants directory: {variant_dir}")
            except Exception as e:
                logger.warning(f"Failed to trash {variant_dir}: {e}")

        return trashed


class _CloudBackend:
    """Vercel Blob storage backend.

    Uses the Vercel Blob REST API for episode persistence across
    serverless invocations. Falls back to filesystem for local data
    and for simulations (admin-only, not cron-critical).

    REST API pattern (same as save_image which already works):
      PUT  https://blob.vercel-storage.com/{pathname}  → upload
      GET  blob URL from list/put response              → download
      GET  https://blob.vercel-storage.com?prefix=...   → list
    """

    _BLOB_API = "https://blob.vercel-storage.com"

    def __init__(self) -> None:
        self._blob_token = os.environ.get("BLOB_READ_WRITE_TOKEN", "")
        self._fs = _FilesystemBackend()  # fallback for local data
        if not self._blob_token and os.environ.get("VERCEL_ENV"):
            raise RuntimeError(
                "FATAL: Running on Vercel without BLOB_READ_WRITE_TOKEN. "
                "Episode data WILL NOT persist. Refusing to start. "
                "Create a Vercel Blob store and add the token to Doppler."
            )

    def _has_cloud(self) -> bool:
        return bool(self._blob_token)

    def _auth_headers(self) -> dict:
        return {"Authorization": f"Bearer {self._blob_token}"}

    def _blob_key(self, relative_path: str) -> str:
        """Map a repo-relative path to a stable blob key.

        src/assets/images/abc123/editorial.png -> images/abc123/editorial.png
        """
        key = relative_path.removeprefix("src/")
        key = key.removeprefix("assets/")
        return key

    # --- Episodes ---

    def load_episode(self, episode_id: str) -> Optional[dict]:
        if not self._has_cloud():
            return self._fs.load_episode(episode_id)

        import requests as _requests

        # List blobs with exact prefix to find this episode's URL
        pathname = f"episodes/{episode_id}.json"
        try:
            resp = _requests.get(
                self._BLOB_API,
                params={"prefix": pathname, "limit": "1"},
                headers=self._auth_headers(),
                timeout=15,
            )
            resp.raise_for_status()
            blobs = resp.json().get("blobs", [])
            if not blobs:
                # Not in cloud — try filesystem fallback (deployed episode files)
                return self._fs.load_episode(episode_id)

            blob_url = blobs[0]["url"]
            # Cache-bust: Vercel Blob CDN can serve stale content for ~1-2s
            # after a PUT. Adding a unique query param forces a fresh read.
            import time as _time
            cache_bust = f"?t={int(_time.time() * 1000)}"
            content_resp = _requests.get(
                blob_url + cache_bust,
                headers=self._auth_headers(),
                timeout=15,
            )
            content_resp.raise_for_status()
            return content_resp.json()
        except Exception as e:
            logger.warning(f"Blob load_episode failed for {episode_id}, falling back to filesystem: {e}")
            return self._fs.load_episode(episode_id)

    def save_episode(self, episode_id: str, data: dict) -> None:
        if not self._has_cloud():
            self._fs.save_episode(episode_id, data)
            return

        import requests as _requests

        pathname = f"episodes/{episode_id}.json"
        body = json.dumps(data, indent=2)
        headers = {
            **self._auth_headers(),
            "Content-Type": "application/json",
            "x-api-version": "7",
            "x-content-type": "application/json",
            "x-add-random-suffix": "0",
            "x-allow-overwrite": "1",
        }
        try:
            resp = _requests.put(
                f"{self._BLOB_API}/{pathname}",
                data=body.encode("utf-8"),
                headers=headers,
                timeout=30,
            )
            resp.raise_for_status()
            blob_url = resp.json().get("url", "")
            logger.info(f"Saved episode to Vercel Blob: {blob_url}")
        except Exception as e:
            logger.error(f"Blob save_episode failed for {episode_id}: {e}")
            raise

        # Try local filesystem cache for same-invocation reads (may fail on read-only FS)
        try:
            self._fs.save_episode(episode_id, data)
        except OSError:
            pass  # Read-only filesystem (Vercel Lambda) — blob save already succeeded

    def list_episodes(self) -> list[dict]:
        if not self._has_cloud():
            return self._fs.list_episodes()

        import requests as _requests

        results = []
        cursor: Optional[str] = None
        try:
            while True:
                params: dict = {"prefix": "episodes/", "limit": "100"}
                if cursor:
                    params["cursor"] = cursor
                resp = _requests.get(
                    self._BLOB_API,
                    params=params,
                    headers=self._auth_headers(),
                    timeout=15,
                )
                resp.raise_for_status()
                data = resp.json()
                for blob in data.get("blobs", []):
                    pathname = blob.get("pathname", "")
                    if pathname.endswith(".json"):
                        episode_id = pathname.removeprefix("episodes/").removesuffix(".json")
                        # Fetch full episode data
                        try:
                            content_resp = _requests.get(
                                blob["url"],
                                headers=self._auth_headers(),
                                timeout=15,
                            )
                            content_resp.raise_for_status()
                            ep_data = content_resp.json()
                            results.append({"episode_id": episode_id, **ep_data})
                        except Exception as e:
                            logger.warning(f"Skipping blob episode {episode_id}: {e}")
                if not data.get("hasMore"):
                    break
                cursor = data.get("cursor")
        except Exception as e:
            logger.warning(f"Blob list_episodes failed, falling back to filesystem: {e}")
            return self._fs.list_episodes()

        # Sort newest first by created_at
        results.sort(key=lambda x: x.get("created_at", ""), reverse=True)
        return results

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

        import requests as _requests

        key = self._blob_key(relative_path)
        upload_url = f"https://blob.vercel-storage.com/{key}"
        headers = {
            "Authorization": f"Bearer {self._blob_token}",
            "Content-Type": "image/png",
            "x-vercel-access": "public",
        }
        resp = _requests.put(upload_url, data=image_bytes, headers=headers, timeout=60)
        resp.raise_for_status()
        blob_url: str = resp.json()["url"]
        logger.info(f"Uploaded image to Vercel Blob: {blob_url}")
        return blob_url

    def get_image_url(self, relative_path: str) -> str:
        if not self._has_cloud():
            return self._fs.get_image_url(relative_path)

        import requests as _requests

        key = self._blob_key(relative_path)
        # HEAD the blob to check existence and get the canonical URL.
        # Vercel Blob URLs are at: https://blob.vercel-storage.com/{key}
        # The response Location or url field gives us the CDN URL.
        check_url = f"https://blob.vercel-storage.com/{key}"
        headers = {"Authorization": f"Bearer {self._blob_token}"}
        try:
            resp = _requests.head(check_url, headers=headers, timeout=10, allow_redirects=True)
            if resp.ok:
                # Use the final URL after any redirects (CDN URL)
                return resp.url
        except Exception as e:
            logger.warning(f"Blob HEAD check failed for {key}: {e}")

        # Fallback: construct URL from known Vercel Blob pattern
        return f"https://blob.vercel-storage.com/{key}"

    def image_exists(self, relative_path: str) -> bool:
        return self._fs.image_exists(relative_path)

    def cleanup_image_variants(self, recipe_id: str) -> list[str]:
        return self._fs.cleanup_image_variants(recipe_id)


# ---------------------------------------------------------------------------
# Singleton — import this everywhere
# ---------------------------------------------------------------------------

def _make_backend() -> _FilesystemBackend | _CloudBackend:
    if config.storage_backend == "filesystem":
        return _FilesystemBackend()
    return _CloudBackend()


storage = _make_backend()
