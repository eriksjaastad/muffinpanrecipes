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
import re
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

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

# Durable per-character memory (#6968). This lives at repo-root `data/`,
# same family as EPISODES_DIR/SIMULATIONS_DIR above and deliberately NOT
# under backend/data/characters/ — that directory ships inside the Vercel
# Lambda bundle read-only, which is why the original writer there
# (backend/data/characters/<slug>/memory.json) silently stopped persisting
# in production. Those legacy files remain in place as the initial seed for
# a character with nothing in durable storage yet; callers own that
# fallback (see backend.admin.cron_routes._load_character_memory_seeded and
# scripts.simulate_dialogue_week's equivalent).
CHARACTER_MEMORY_DIR = ROOT / "data" / "character_memory"

# A character memory record keeps at most this many distinct weeks; the
# prompt-building side displays fewer (see scripts/simulate_dialogue_week.py
# _load_memories, which shows the latest 2).
MAX_CHARACTER_MEMORY_WEEKS = 3

SOCIAL_IMAGE_SIZE = (1200, 630)
SOCIAL_IMAGE_SUFFIX = ".social.jpg"

# #6755 — width-limited WebP variants generated alongside the full-size
# sibling so the renderer's srcset can offer a phone something smaller than
# the 1536px original. Widths are deterministic and public (baked into blob
# keys and srcset markup), so changing this tuple requires re-running
# scripts/backfill_image_variants.py for every already-published image.
WEBP_VARIANT_WIDTHS: tuple[int, ...] = (400, 800)

# Public, prefix-free base of the (public) blob store. Existence checks HEAD
# this host: a HEAD against the API host (https://blob.vercel-storage.com/<key>)
# returns 404 even for blobs that exist — verified 2026-09-05 against a live
# variant that the public URL served with 200 — so it must never be used as a
# presence probe. Same host the catalog and title readers hardcode.
BLOB_PUBLIC_BASE = "https://gtczmjysc51nh8fq.public.blob.vercel-storage.com"


def _social_jpeg_key(png_key: str) -> str:
    """Return the deterministic social-image sibling key for a PNG key."""
    if not png_key.lower().endswith(".png"):
        raise ValueError(f"Social JPEG siblings require a PNG key: {png_key!r}")
    return png_key[:-4] + SOCIAL_IMAGE_SUFFIX


def _webp_variant_key(png_key: str, width: int) -> str:
    """Return the deterministic width-limited WebP variant key for a PNG key.

    Mirrors the full-size sibling's '<stem>.webp' naming with a '-{width}w'
    suffix (#6755) so the renderer can construct srcset URLs by string
    rewrite alone — same deterministic-pathname contract as #5251
    (x-add-random-suffix=0 / x-allow-overwrite=1, see the comment at
    save_image's headers below).
    """
    if not png_key.lower().endswith(".png"):
        raise ValueError(f"WebP variants require a PNG key: {png_key!r}")
    return f"{png_key[:-4]}-{width}w.webp"


def _encode_webp(png_bytes: bytes, width: int | None = None) -> bytes:
    """Encode PNG bytes as WebP, optionally downscaled to ``width`` (#6755).

    Quality=82, method=6 matches the original full-size sibling encode
    (#5251) so a downscaled variant looks identical to the full image, just
    smaller. Resizing is by width only, preserving aspect ratio — generated
    photography is square (1536x1536) today but this must not assume that.
    """
    from io import BytesIO

    from PIL import Image

    with Image.open(BytesIO(png_bytes)) as source:
        image = source
        if width is not None:
            orig_width, orig_height = source.size
            if orig_width <= 0:
                raise ValueError("Cannot resize image with zero width")
            target_height = max(1, round(orig_height * (width / orig_width)))
            image = source.resize((width, target_height), Image.Resampling.LANCZOS)
        buf = BytesIO()
        image.save(buf, format="WEBP", quality=82, method=6)
        return buf.getvalue()


def _encode_jpeg(png_bytes: bytes, size: tuple[int, int] | None = None) -> bytes:
    """Encode PNG bytes as RGB JPEG, optionally fitting to ``size``.

    The source PNG remains the canonical upload. ``ImageOps.fit`` gives the
    social asset a fixed aspect ratio while preserving the important center;
    without ``size`` the original dimensions are preserved. Transparent PNGs
    are composited onto white because JPEG has no alpha channel. No source
    bytes are modified.
    """
    from io import BytesIO

    from PIL import Image, ImageOps

    with Image.open(BytesIO(png_bytes)) as source:
        rgba = source.convert("RGBA")
        fitted = (
            ImageOps.fit(
                rgba,
                size,
                method=Image.Resampling.LANCZOS,
                centering=(0.5, 0.5),
            )
            if size
            else rgba
        )
        background = Image.new("RGB", fitted.size, (255, 255, 255))
        background.paste(fitted, mask=fitted.getchannel("A"))
        output = BytesIO()
        background.save(
            output,
            format="JPEG",
            quality=85,
            optimize=True,
            progressive=True,
        )
        return output.getvalue()


def _encode_social_jpeg(png_bytes: bytes) -> bytes:
    """Create a deterministic 1200x630 JPEG suitable for social metadata."""
    return _encode_jpeg(png_bytes, SOCIAL_IMAGE_SIZE)


class CharacterMemoryUnavailable(Exception):
    """A character-memory READ could not be completed (#6968 review finding 1).

    Distinct from a genuine "no memory has ever been written" (which is
    `None`, not an exception): this means the read attempt itself failed —
    a transient Blob error, a network timeout, a corrupted local file. A
    caller must never treat this the same as "not found": seeding from the
    legacy bundled file, or writing a merge built on top of a missing read,
    would silently regress good durable memory to the stale seed plus one
    new week. Callers should record the character's memory step as failed
    and skip the write for this run instead.
    """


_ISO_WEEK_RE = re.compile(r"^(\d{4})-W(\d{2})$")


def parse_iso_week(week: str) -> tuple[int, int]:
    """Parse a "YYYY-Www" ISO week string into a ``(year, week)`` sort key.

    Raises ValueError if ``week`` is not exactly this zero-padded shape or
    the week number is out of the 1-53 range — a synthetic/test label such
    as "test-week" is never a valid ISO week (#6968 review finding 5).
    Deliberately strict: this is the sort key that decides which weeks
    survive retention and which one is shown as most recent, so an
    unparsed label must never silently participate in that ordering as if
    it were real (a raw string sort puts "test-week" after every real
    "2026-Wnn" week, letting it evict genuine history).
    """
    match = _ISO_WEEK_RE.match(week or "")
    if not match:
        raise ValueError(f"not a valid ISO week string (expected 'YYYY-Www'): {week!r}")
    year, week_num = int(match.group(1)), int(match.group(2))
    if not (1 <= week_num <= 53):
        raise ValueError(f"ISO week number out of range 1-53: {week!r}")
    return (year, week_num)


def is_valid_iso_week(week: object) -> bool:
    """True if ``week`` parses as a real ISO week string (see parse_iso_week)."""
    if not isinstance(week, str):
        return False
    try:
        parse_iso_week(week)
        return True
    except ValueError:
        return False


def _dedupe_episodes_by_week(episodes: list) -> list[dict]:
    """Collapse duplicate ``"week"`` entries to one per week (#6968 review
    finding 4).

    The legacy bundled seed files (backend/data/characters/*/memory.json)
    predate this card's idempotent merge and contain up to 3 entries
    sharing the same week (the old writer appended and truncated without
    deduping). Keeps the LAST occurrence in list order for a given week —
    assumed to be the most recently written/most complete one. Non-dict or
    weekless entries are dropped defensively rather than crashing a read.
    """
    by_week: dict[str, dict] = {}
    for e in episodes:
        if isinstance(e, dict) and e.get("week"):
            by_week[e["week"]] = e  # last occurrence for a given key wins
    return list(by_week.values())


def order_valid_episodes_by_week(episodes: list) -> list[dict]:
    """Dedupe, validate, and chronologically sort a character's episode list.

    Single ordering contract shared by the write path
    (merge_character_memory, below) and the read/display path
    (scripts.simulate_dialogue_week._load_memories), so a stored or
    legacy-seeded record can never show or retain the wrong weeks
    regardless of which end reads it (#6968 review findings 3-5):
      - duplicate weeks collapse to one entry (finding 4)
      - anything that isn't a real, parseable ISO week is dropped rather
        than sorted as if it were one (finding 5)
      - the result is chronological, oldest first
    """
    deduped = _dedupe_episodes_by_week(episodes)
    valid: list[dict] = []
    for e in deduped:
        week = e.get("week")
        if is_valid_iso_week(week):
            valid.append(e)
        else:
            logger.warning(f"Dropping non-ISO-week memory entry: {week!r}")
    valid.sort(key=lambda e: parse_iso_week(e["week"]))
    return valid


def merge_character_memory(existing: Optional[dict], entry: dict) -> dict:
    """Merge one completed week's memory entry into existing character memory (#6968).

    ``entry`` must carry a ``"week"`` key that is a real ISO week string
    (e.g. "2026-W40", validated by parse_iso_week) — a non-ISO/synthetic
    label is rejected with ValueError rather than silently entering durable
    memory, where it could corrupt retention/display ordering (#6968
    review finding 5). Any existing entry already recorded for that week is
    replaced rather than duplicated, so re-running the same week's Sunday
    publish, or the repair/backfill command, is idempotent.

    Retention and ``last_updated`` are keyed by the parsed (year, week)
    value, not by write order or raw string comparison (#6968 review
    finding 3): repairing an older week can never evict a newer one just
    because it was written more recently, and the
    ``MAX_CHARACTER_MEMORY_WEEKS`` entries kept (and the 2 shown by
    scripts.simulate_dialogue_week._load_memories) are always the
    chronologically most recent, in chronological order. Existing stored
    duplicates/invalid weeks are cleaned up via order_valid_episodes_by_week
    on every merge (finding 4).

    Pure function, no I/O — callers load, merge, then save.
    """
    week = entry.get("week")
    if not week:
        raise ValueError("memory entry must include a non-empty 'week' key")
    if not is_valid_iso_week(week):
        raise ValueError(
            f"memory entry 'week' must be a real ISO week string (e.g. '2026-W40'), "
            f"got {week!r} — non-ISO labels must never enter durable character "
            "memory (#6968 review finding 5)"
        )
    data: dict = dict(existing) if existing else {}
    episodes = [e for e in data.get("episodes", []) if e.get("week") != week]
    episodes.append(entry)
    episodes = order_valid_episodes_by_week(episodes)
    episodes = episodes[-MAX_CHARACTER_MEMORY_WEEKS:]
    data["episodes"] = episodes
    data["last_updated"] = episodes[-1]["week"] if episodes else week
    return data


class _FilesystemBackend:
    """Local filesystem storage — used for LOCAL_DEV."""

    prefix: str = ""  # no-op for filesystem; test prefix only affects cloud

    def set_prefix(self, prefix: str) -> None:
        """Set storage path prefix (used by test mode). No-op for filesystem."""
        self.prefix = prefix

    @contextmanager
    def prefix_scope(self, prefix: str) -> Iterator[None]:
        """Structural guarantee that prefix resets after the block.

        Snapshots the current prefix on entry, restores it on exit (even on
        exception). Use this to wrap cron handlers so test-mode prefix never
        leaks into subsequent invocations on a warm Lambda — see RUNBOOK
        Incident 1 (#5911).
        """
        previous = self.prefix
        self.prefix = prefix
        try:
            yield
        finally:
            self.prefix = previous

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

    def load_character_memory(self, slug: str, *, force_refresh: bool = False) -> Optional[dict]:
        """Load durable per-character memory (#6968).

        Scoped by `self.prefix` the same way the cloud backend's blob key
        is (#6968 review finding 2) — "test/" and "" resolve to different
        files on disk, so test-mode data can never cross into production
        memory through this path. Returns None only when genuinely absent;
        raises CharacterMemoryUnavailable on a read/parse failure so a
        caller never confuses "never written" with "failed to read"
        (finding 1).

        `force_refresh` is accepted for interface parity with
        _CloudBackend (whose in-memory cache it bypasses) but is a no-op
        here: the filesystem backend has no cache — every call already
        reads the current file from disk.
        """
        path = CHARACTER_MEMORY_DIR / f"{self.prefix}{slug}.json"
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text())
        except (json.JSONDecodeError, OSError) as e:
            raise CharacterMemoryUnavailable(f"local read failed for {slug!r}: {e}") from e

    def save_character_memory(self, slug: str, data: dict) -> None:
        """Persist durable per-character memory (#6968), scoped by prefix (see load_character_memory)."""
        path = CHARACTER_MEMORY_DIR / f"{self.prefix}{slug}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2))

    def save_page(self, pathname: str, html_content: str) -> str:
        """Save an HTML page locally and return its URL path."""
        dest = ROOT / pathname
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(html_content, encoding="utf-8")
        return f"/{pathname}"

    def load_page(self, pathname: str) -> Optional[str]:
        """Load a page from local filesystem. Returns content or None."""
        path = ROOT / pathname
        if path.exists():
            return path.read_text(encoding="utf-8")
        return None

    def save_image(self, relative_path: str, image_bytes: bytes) -> str:
        """Save image bytes and return the local URL path.

        Mirrors the cloud backend's WebP sibling pipeline (#5251, #6755) so
        local dev renders the same <picture>/srcset markup production does.
        Best-effort, non-fatal: a bad encode must never block a local render.
        """
        dest = self._safe_path(relative_path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(image_bytes)

        if relative_path.lower().endswith(".png"):
            try:
                dest.with_suffix(".webp").write_bytes(_encode_webp(image_bytes))
            except Exception as e:  # noqa: BLE001 - optimization must not block a local save
                logger.warning(f"WebP sibling write failed for {dest}: {e}")
            for width in WEBP_VARIANT_WIDTHS:
                try:
                    variant_path = dest.with_name(f"{dest.stem}-{width}w.webp")
                    variant_path.write_bytes(_encode_webp(image_bytes, width=width))
                except Exception as e:  # noqa: BLE001 - same rationale as above
                    logger.warning(f"WebP {width}w variant write failed for {dest}: {e}")

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

    def image_variants_available(self, image_key: str) -> bool:
        """Whether the smallest WebP variant exists for a rendered image (#6755).

        ``image_key`` is the backend-agnostic '<subpath>/name.png' identity
        the renderer derives via episode_renderer._variant_lookup_key.
        save_image writes every configured width in the same call, so
        checking the smallest (400w) implies the rest exist too.
        """
        if not image_key.lower().endswith(".png"):
            return False
        try:
            variant_keys = [_webp_variant_key(image_key, w) for w in WEBP_VARIANT_WIDTHS]
        except ValueError:
            return False
        # Every width, not just the smallest: uploads are per-width and
        # best-effort, so one can be missing while another exists (review
        # finding, 2026-09-05) — and one 404 candidate breaks the image.
        return all((IMAGES_DIR / key).exists() for key in variant_keys)

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
        self.prefix: str = ""  # "test/" for test mode, "" for production
        # In-memory cache: (storage prefix, episode_id) -> dict. Populated by
        # save_episode so that load_episode in the same Lambda invocation gets
        # fresh data without hitting the CDN (which may serve stale content
        # for seconds).
        self._episode_cache: dict[tuple[str, str], dict] = {}
        self._page_cache: dict[str, str] = {}
        # Same same-invocation-freshness rationale as _episode_cache (#6968).
        self._character_memory_cache: dict[tuple[str, str], dict] = {}
        if not self._blob_token and os.environ.get("VERCEL_ENV"):
            raise RuntimeError(
                "FATAL: Running on Vercel without BLOB_READ_WRITE_TOKEN. "
                "Episode data WILL NOT persist. Refusing to start. "
                "Create a Vercel Blob store and add the token to Doppler."
            )

    def set_prefix(self, prefix: str) -> None:
        """Set storage path prefix. Use 'test/' for compressed timeline tests."""
        self.prefix = prefix

    @contextmanager
    def prefix_scope(self, prefix: str) -> Iterator[None]:
        """Structural guarantee that prefix resets after the block.

        Snapshots the current prefix on entry, restores it on exit (even on
        exception). Use this to wrap cron handlers so test-mode prefix never
        leaks into subsequent invocations on a warm Lambda — see RUNBOOK
        Incident 1 (#5911).
        """
        previous = self.prefix
        self.prefix = prefix
        try:
            yield
        finally:
            self.prefix = previous

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

        # Read episode from Vercel Blob via list API + CDN content URL.
        #
        # IMPORTANT: After a PUT with x-allow-overwrite, the CDN URL may serve
        # stale content for a few seconds. In production (24h between cron stages)
        # this is fine. For rapid testing, callers should add a delay between writes
        # and reads (see scripts/run_full_week.py --stage-delay).
        # Check in-memory cache first (same Lambda invocation)
        cache_key = (self.prefix, episode_id)
        if cache_key in self._episode_cache:
            logger.debug(f"load_episode cache hit for {episode_id}")
            return self._episode_cache[cache_key]

        pathname = f"{self.prefix}episodes/{episode_id}.json"
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
                return self._fs.load_episode(episode_id)

            blob_url = blobs[0]["url"]
            content_resp = _requests.get(blob_url, timeout=15)
            content_resp.raise_for_status()
            data = content_resp.json()
            self._episode_cache[cache_key] = data
            return data
        except Exception as e:
            logger.warning(f"Blob load_episode failed for {episode_id}, falling back to filesystem: {e}")
            return self._fs.load_episode(episode_id)

    def load_episode_strict(self, episode_id: str) -> Optional[dict]:
        """Load an episode without masking cloud failures with local data.

        Static deployment builds use this authoritative form so a transient
        Blob failure cannot publish a page set assembled from stale disk data.
        Runtime readers retain the compatibility fallback in ``load_episode``.
        """
        if not self._has_cloud():
            return self._fs.load_episode(episode_id)

        import requests as _requests

        cache_key = (self.prefix, episode_id)
        if cache_key in self._episode_cache:
            return self._episode_cache[cache_key]

        pathname = f"{self.prefix}episodes/{episode_id}.json"
        resp = _requests.get(
            self._BLOB_API,
            params={"prefix": pathname, "limit": "1"},
            headers=self._auth_headers(),
            timeout=15,
        )
        resp.raise_for_status()
        blobs = resp.json().get("blobs", [])
        if not blobs:
            return None
        content_resp = _requests.get(blobs[0]["url"], timeout=15)
        content_resp.raise_for_status()
        data = content_resp.json()
        self._episode_cache[cache_key] = data
        return data

    def save_episode(self, episode_id: str, data: dict) -> None:
        if not self._has_cloud():
            self._fs.save_episode(episode_id, data)
            return

        import requests as _requests

        pathname = f"{self.prefix}episodes/{episode_id}.json"
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
            # Update in-memory cache so same-invocation reads get fresh data
            self._episode_cache[(self.prefix, episode_id)] = data
        except Exception as e:
            logger.error(f"Blob save_episode failed for {episode_id}: {e}")
            raise

        # Try local filesystem cache for same-invocation reads (may fail on read-only FS)
        try:
            self._fs.save_episode(episode_id, data)
        except OSError as exc:
            # Read-only filesystem (Vercel Lambda) — blob save already succeeded.
            logger.debug("Local episode cache unavailable for %s: %s", episode_id, exc)

    def list_episodes(self) -> list[dict]:
        if not self._has_cloud():
            return self._fs.list_episodes()

        import requests as _requests

        results = []
        cursor: Optional[str] = None
        try:
            while True:
                params: dict = {"prefix": f"{self.prefix}episodes/", "limit": "100"}
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
                        episode_path = pathname.removeprefix(self.prefix)
                        episode_id = episode_path.removeprefix("episodes/").removesuffix(".json")
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

    def list_episodes_strict(self) -> list[dict]:
        """List episodes without falling back to stale local data.

        The static builder uses this authoritative form; public runtime code
        continues to use ``list_episodes`` for its existing compatibility
        behavior.
        """
        if not self._has_cloud():
            return self._fs.list_episodes()

        import requests as _requests

        results = []
        cursor: Optional[str] = None
        while True:
            params: dict = {"prefix": f"{self.prefix}episodes/", "limit": "100"}
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
                if not pathname.endswith(".json"):
                    continue
                episode_path = pathname.removeprefix(self.prefix)
                episode_id = episode_path.removeprefix("episodes/").removesuffix(".json")
                content_resp = _requests.get(
                    blob["url"], headers=self._auth_headers(), timeout=15
                )
                content_resp.raise_for_status()
                ep_data = content_resp.json()
                results.append({"episode_id": episode_id, **ep_data})
            if not data.get("hasMore"):
                break
            cursor = data.get("cursor")

        results.sort(key=lambda x: x.get("created_at", ""), reverse=True)
        return results

    # --- Character memory (#6968) ---

    def load_character_memory(self, slug: str, *, force_refresh: bool = False) -> Optional[dict]:
        """Load durable per-character memory from Vercel Blob.

        Same-invocation cache, CDN read through the list API — but, unlike
        load_episode, this deliberately does NOT fall back to the local
        filesystem on a "not found" or on any error (#6968 review finding
        2): the local character-memory path has no prefix of its own in a
        cloud-backed environment, so a fallback here could silently read or
        (via save's old mirror write) leak memory across the test/
        production boundary that `prefix` exists to enforce.

        Returns None only for a genuine not-found: the list API returned a
        well-formed response (a dict with a "blobs" list) with no matching
        key. Any other shape — a non-2xx status, a network error, a 200
        whose body isn't the expected `{"blobs": [...]}` shape — raises
        CharacterMemoryUnavailable instead of silently defaulting to "not
        found" (#6968 review finding 2: `.get("blobs", [])` used to treat
        an unexpected payload the same as a genuine empty list, which would
        make Sunday seed from the stale legacy file).

        `force_refresh=True` bypasses a cached value instead of returning
        it (#6968 review finding 1): this process's cache has no expiry and
        no cross-process invalidation, so a stale cached read taken right
        before a write could merge on top of an older snapshot than
        another process (a concurrent repair, another warm Lambda
        instance) has already written, silently reverting that newer
        write. The freshly fetched result still repopulates the cache
        afterward, so later same-invocation reads in this call chain see
        it. Every read that precedes a save — production Sunday, the local
        full-week writer, and the repair/backfill script, all via
        _load_character_memory_seeded — must pass force_refresh=True. Reads
        that only feed a prompt for this run (scripts.simulate_dialogue_week
        ._load_memories) keep using the cache: a stale read there is
        harmless and is exactly the "one immutable memory snapshot per
        run" contract from the prior review round.
        """
        if not self._has_cloud():
            return self._fs.load_character_memory(slug)

        import requests as _requests

        cache_key = (self.prefix, slug)
        if not force_refresh and cache_key in self._character_memory_cache:
            return self._character_memory_cache[cache_key]

        pathname = f"{self.prefix}character_memory/{slug}.json"
        try:
            resp = _requests.get(
                self._BLOB_API,
                params={"prefix": pathname, "limit": "1"},
                headers=self._auth_headers(),
                timeout=15,
            )
            resp.raise_for_status()
            payload = resp.json()
        except Exception as e:
            logger.error(f"Blob load_character_memory list failed for {slug}: {type(e).__name__}: {e}")
            raise CharacterMemoryUnavailable(f"list failed for {slug!r}: {e}") from e

        if not isinstance(payload, dict) or not isinstance(payload.get("blobs"), list):
            logger.error(f"Blob load_character_memory list returned malformed payload for {slug}: {payload!r}")
            raise CharacterMemoryUnavailable(f"malformed list payload for {slug!r}: {payload!r}")

        blobs = payload["blobs"]
        if not blobs:
            return None  # genuine not-found: nothing written for this character under this prefix

        try:
            content_resp = _requests.get(blobs[0]["url"], timeout=15)
            content_resp.raise_for_status()
            data = content_resp.json()
        except Exception as e:
            logger.error(f"Blob load_character_memory content fetch failed for {slug}: {type(e).__name__}: {e}")
            raise CharacterMemoryUnavailable(f"content fetch failed for {slug!r}: {e}") from e

        self._character_memory_cache[cache_key] = data
        return data

    def save_character_memory(self, slug: str, data: dict) -> None:
        """Persist durable per-character memory to Vercel Blob.

        Raises on cloud failure — same contract as save_episode — so a
        caller can record the write as failed instead of silently claiming
        success (#6968). Unlike save_episode, this does NOT mirror to the
        local filesystem: the local character-memory path has no prefix
        scoping of its own, so mirroring here would let a test-mode write
        overwrite (or be silently read back as) production memory, or vice
        versa (#6968 review finding 2).
        """
        if not self._has_cloud():
            self._fs.save_character_memory(slug, data)
            return

        import requests as _requests

        pathname = f"{self.prefix}character_memory/{slug}.json"
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
            logger.info(f"Saved character memory to Vercel Blob: {blob_url}")
            self._character_memory_cache[(self.prefix, slug)] = data
        except Exception as e:
            logger.error(f"Blob save_character_memory failed for {slug}: {e}")
            raise

    # --- Simulations ---

    def load_simulation(self, sim_id: str) -> Optional[dict]:
        return self._fs.load_simulation(sim_id)  # stub fallback

    def save_simulation(self, sim_id: str, data: dict) -> None:
        self._fs.save_simulation(sim_id, data)  # stub fallback

    def list_simulations(self, limit: int = 100) -> list[dict]:
        return self._fs.list_simulations(limit=limit)  # stub fallback

    # --- Pages ---

    def save_page(self, pathname: str, html_content: str) -> str:
        """Upload an HTML page to Vercel Blob. Returns the public URL."""
        if not self._has_cloud():
            return self._fs.save_page(pathname, html_content)

        import requests as _requests

        key = f"{self.prefix}{pathname}"
        upload_url = f"https://blob.vercel-storage.com/{key}"
        headers = {
            "Authorization": f"Bearer {self._blob_token}",
            "Content-Type": "text/html; charset=utf-8",
            "x-api-version": "7",
            "x-content-type": "text/html; charset=utf-8",
            "x-add-random-suffix": "0",
            "x-allow-overwrite": "1",
        }
        resp = _requests.put(
            upload_url, data=html_content.encode("utf-8"),
            headers=headers, timeout=30,
        )
        resp.raise_for_status()
        blob_url: str = resp.json()["url"]
        logger.info(f"Uploaded page to Vercel Blob: {blob_url}")
        # Cache for same-request reads (CDN propagation delay)
        self._page_cache[key] = html_content
        return blob_url

    def load_page(self, pathname: str) -> Optional[str]:
        """Load a page from Vercel Blob. Returns content or None."""
        if not self._has_cloud():
            return self._fs.load_page(pathname)

        import requests as _requests

        key = f"{self.prefix}{pathname}"

        # Check in-memory cache first (avoids CDN staleness)
        if key in self._page_cache:
            return self._page_cache[key]

        # List blobs to find the URL
        resp = _requests.get(
            self._BLOB_API,
            params={"prefix": key, "limit": "1"},
            headers=self._auth_headers(),
            timeout=15,
        )
        if not resp.ok:
            return None

        blobs = resp.json().get("blobs", [])
        if not blobs:
            return None

        # Fetch content from CDN URL
        content_resp = _requests.get(blobs[0]["url"], timeout=15)
        if content_resp.ok:
            # CRITICAL: Use .content.decode() not .text — requests defaults
            # to ISO-8859-1 for text/* without explicit charset, which
            # double-encodes UTF-8 smart quotes/symbols into mojibake.
            return content_resp.content.decode("utf-8")
        return None

    # --- Images ---

    def save_image(self, relative_path: str, image_bytes: bytes) -> str:
        if not self._has_cloud():
            return self._fs.save_image(relative_path, image_bytes)

        import requests as _requests

        key = f"{self.prefix}{self._blob_key(relative_path)}"
        upload_url = f"https://blob.vercel-storage.com/{key}"
        # x-add-random-suffix=0 + x-allow-overwrite=1 are required for
        # deterministic pathnames so the <picture>/srcset URL rewrite
        # in episode_renderer._to_webp_url actually resolves. Without
        # these, Vercel appends a random hash and png/webp land at
        # non-matching paths (root cause of #5251 deploy regression).
        headers = {
            "Authorization": f"Bearer {self._blob_token}",
            "Content-Type": "image/png",
            "x-vercel-access": "public",
            "x-add-random-suffix": "0",
            "x-allow-overwrite": "1",
        }
        resp = _requests.put(upload_url, data=image_bytes, headers=headers, timeout=60)
        resp.raise_for_status()
        blob_url: str = resp.json()["url"]
        logger.info(f"Uploaded image to Vercel Blob: {blob_url}")

        # #5251 — Also upload a WebP sibling at the same path for
        # bandwidth-constrained clients. Non-fatal on failure (PNG is
        # still the canonical source). <picture> tag in the renderer
        # picks up the .webp via srcset when available.
        if key.lower().endswith(".png"):
            try:
                self._upload_webp_sibling(key, image_bytes)
            except Exception as e:  # noqa: BLE001 - optimization must not block publishing
                # Sibling variants are an optimization. The canonical PNG
                # upload above must remain the only publishing dependency.
                logger.warning(f"WebP sibling pipeline failed for {key}: {e}")
            # #6755 — width-limited variants so the renderer can offer a
            # phone something smaller than the 1536px original. Each width
            # is independently best-effort: one bad resize must not cost
            # the other width or the full-size sibling above.
            for width in WEBP_VARIANT_WIDTHS:
                try:
                    self._upload_webp_variant(key, image_bytes, width)
                except Exception as e:  # noqa: BLE001 - optimization must not block publishing
                    logger.warning(f"WebP {width}w variant pipeline failed for {key}: {e}")
            try:
                self._upload_social_jpeg_sibling(key, image_bytes)
            except Exception as e:  # noqa: BLE001 - optimization must not block publishing
                # Keep the publishing path safe even if an unexpected
                # dependency/runtime error escapes the helper's guards.
                logger.warning(f"Social JPEG sibling pipeline failed for {key}: {e}")

        return blob_url

    def _upload_webp_sibling(self, png_key: str, png_bytes: bytes) -> None:
        """Encode png_bytes as WebP and upload to <png_key>.webp.

        One-way best-effort: logs and swallows failures so a bad
        encode never takes down the cron. Quality=82, method=6 gives
        ~30% of PNG size for photo content with no visible loss.
        """
        try:
            from io import BytesIO

            from PIL import Image

            with Image.open(BytesIO(png_bytes)) as im:
                buf = BytesIO()
                im.save(buf, format="WEBP", quality=82, method=6)
                webp_bytes = buf.getvalue()
        except Exception as e:
            logger.warning(f"WebP encode failed for {png_key}: {e}")
            return

        import requests as _requests

        webp_key = png_key[:-4] + ".webp"
        upload_url = f"https://blob.vercel-storage.com/{webp_key}"
        # Deterministic path so png → webp string rewrite resolves.
        # See save_image comment for the full reasoning.
        headers = {
            "Authorization": f"Bearer {self._blob_token}",
            "Content-Type": "image/webp",
            "x-vercel-access": "public",
            "x-add-random-suffix": "0",
            "x-allow-overwrite": "1",
        }
        try:
            resp = _requests.put(upload_url, data=webp_bytes, headers=headers, timeout=60)
            resp.raise_for_status()
            logger.info(
                f"Uploaded WebP sibling: {webp_key} "
                f"({len(webp_bytes)}B from {len(png_bytes)}B PNG, "
                f"{100 * len(webp_bytes) / max(len(png_bytes), 1):.0f}%)"
            )
        except Exception as e:
            logger.warning(f"WebP upload failed for {webp_key}: {e}")

    def _upload_webp_variant(self, png_key: str, png_bytes: bytes, width: int) -> None:
        """Encode a width-limited downscale of png_bytes and upload it (#6755).

        One-way best-effort, same shape as _upload_webp_sibling: logs and
        swallows both encode and upload failures so a bad resize never
        blocks publishing. Deterministic '<stem>-{width}w.webp' key —
        see _webp_variant_key — so the renderer's srcset can be built by
        string rewrite alone.
        """
        try:
            webp_bytes = _encode_webp(png_bytes, width=width)
        except Exception as e:
            logger.warning(f"WebP {width}w variant encode failed for {png_key}: {e}")
            return

        import requests as _requests

        variant_key = _webp_variant_key(png_key, width)
        upload_url = f"https://blob.vercel-storage.com/{variant_key}"
        headers = {
            "Authorization": f"Bearer {self._blob_token}",
            "Content-Type": "image/webp",
            "x-vercel-access": "public",
            "x-add-random-suffix": "0",
            "x-allow-overwrite": "1",
        }
        try:
            resp = _requests.put(upload_url, data=webp_bytes, headers=headers, timeout=60)
            resp.raise_for_status()
            logger.info(
                f"Uploaded WebP {width}w variant: {variant_key} "
                f"({len(webp_bytes)}B from {len(png_bytes)}B PNG)"
            )
        except Exception as e:
            logger.warning(f"WebP {width}w variant upload failed for {variant_key}: {e}")

    def _upload_social_jpeg_sibling(self, png_key: str, png_bytes: bytes) -> None:
        """Best-effort upload of a deterministic 1200x630 social JPEG.

        The source PNG remains canonical and the existing WebP sibling is
        independent of this pipeline. Both encoding and upload failures are
        swallowed so social metadata generation can never fail publishing.
        """
        try:
            jpeg_bytes = _encode_social_jpeg(png_bytes)
            social_key = _social_jpeg_key(png_key)
        except Exception as e:
            logger.warning(f"Social JPEG encode failed for {png_key}: {e}")
            return

        import requests as _requests

        upload_url = f"https://blob.vercel-storage.com/{social_key}"
        headers = {
            "Authorization": f"Bearer {self._blob_token}",
            "Content-Type": "image/jpeg",
            "x-vercel-access": "public",
            "x-add-random-suffix": "0",
            "x-allow-overwrite": "1",
        }
        try:
            resp = _requests.put(upload_url, data=jpeg_bytes, headers=headers, timeout=60)
            resp.raise_for_status()
            logger.info(
                f"Uploaded social JPEG sibling: {social_key} "
                f"({len(jpeg_bytes)}B from {len(png_bytes)}B PNG)"
            )
        except Exception as e:
            logger.warning(f"Social JPEG upload failed for {social_key}: {e}")

    def get_image_url(self, relative_path: str) -> str:
        if not self._has_cloud():
            return self._fs.get_image_url(relative_path)

        import requests as _requests

        key = f"{self.prefix}{self._blob_key(relative_path)}"
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

    def image_variants_available(self, image_key: str) -> bool:
        """HEAD-check the smallest WebP variant blob for a rendered image (#6755).

        ``image_key`` is the backend-agnostic '<subpath>/name.png' identity
        the renderer derives via episode_renderer._variant_lookup_key — the
        same slice both backends' keys share once the 'images/' segment is
        stripped. A missing variant means the PNG predates this feature or
        its best-effort encode/upload failed silently at publish time (see
        _upload_webp_variant); either way the renderer must fall back to the
        single full-size candidate — a srcset entry that 404s breaks the
        image for whichever browser happens to pick it.
        """
        if not self._has_cloud():
            return self._fs.image_variants_available(image_key)
        if not image_key.lower().endswith(".png"):
            return False
        try:
            variant_keys = [
                _webp_variant_key(f"images/{image_key}", w) for w in WEBP_VARIANT_WIDTHS
            ]
        except ValueError:
            return False

        import requests as _requests

        # HEAD the PUBLIC url, unauthenticated. The API host answers 404 for
        # existing blobs (see BLOB_PUBLIC_BASE), which made every rendered
        # srcset fall back to the single full-size candidate even after the
        # variants had been uploaded. Every width is checked: uploads are
        # per-width and best-effort, so one can exist without the other, and
        # a single 404 candidate breaks the image for the browser that picks it.
        for variant_key in variant_keys:
            key = f"{self.prefix}{variant_key}"
            try:
                resp = _requests.head(f"{BLOB_PUBLIC_BASE}/{key}", timeout=10, allow_redirects=True)
            except Exception as e:
                logger.warning(f"Variant existence check failed for {key}: {e}")
                return False
            if resp.status_code != 200:
                return False
        return True

    def cleanup_image_variants(self, recipe_id: str) -> list[str]:
        """No-op on cloud storage: round-1 variants are LIVE content, not discards (#6712).

        The original bug — this delegated to the filesystem backend's
        send2trash on a local round-candidates directory that only exists
        on a dev machine's disk — silently did nothing on Vercel while
        claiming to clean. Wiring up delete_by_prefix instead would be
        actively dangerous: every round_1 image is published editorial
        content (the BTS gallery renders each one under the Wednesday /
        Photography divider), not a discarded photography candidate, and
        there is no orphan-amplification problem to justify deleting them
        (see card #6712 scope correction, 2026-08-30 — vision QA converges
        in a single round, round_2/round_3 have never existed). So this is
        an honest no-op rather than a lying one.
        """
        logger.info(
            f"cleanup_image_variants: no-op for cloud backend (recipe_id={recipe_id}); "
            "round_1 variants are published BTS content, not discards — see #6712."
        )
        return []

    def delete_by_prefix(self, prefix: str) -> int:
        """Delete all blobs under a given prefix. Returns count deleted."""
        if not self._has_cloud():
            logger.warning("delete_by_prefix: no cloud backend, nothing to delete")
            return 0

        import requests as _requests

        deleted = 0
        cursor: Optional[str] = None
        while True:
            params: dict = {"prefix": prefix, "limit": "100"}
            if cursor:
                params["cursor"] = cursor
            resp = _requests.get(
                self._BLOB_API, params=params,
                headers=self._auth_headers(), timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            blobs = data.get("blobs", [])
            if not blobs:
                break

            urls = [b["url"] for b in blobs]
            del_resp = _requests.post(
                f"{self._BLOB_API}/delete",
                json={"urls": urls},
                headers=self._auth_headers(),
                timeout=30,
            )
            del_resp.raise_for_status()
            deleted += len(urls)
            logger.info(f"Deleted {len(urls)} blobs with prefix '{prefix}'")

            if not data.get("hasMore"):
                break
            cursor = data.get("cursor")

        return deleted


# ---------------------------------------------------------------------------
# Singleton — import this everywhere
# ---------------------------------------------------------------------------

def _make_backend() -> _FilesystemBackend | _CloudBackend:
    if config.storage_backend == "filesystem":
        return _FilesystemBackend()
    return _CloudBackend()


storage = _make_backend()
