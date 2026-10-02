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

# Durable per-character memory (#6968, redesigned in review round 3). This
# lives at repo-root `data/`, same family as EPISODES_DIR/SIMULATIONS_DIR
# above and deliberately NOT under backend/data/characters/ — that
# directory ships inside the Vercel Lambda bundle read-only, which is why
# the original writer there (backend/data/characters/<slug>/memory.json)
# silently stopped persisting in production.
#
# One blob per character PER WEEK: character_memory/<slug>/<YYYY-Www>.json.
# There is no read-modify-write and no merge: a write is always a single,
# complete week's body, PUT to its own key. Re-running the same week
# overwrites only that key — never another week's blob — so a stale read
# can never cause data loss (round-3 review finding 1: the prior single-
# file-per-character design read-then-merged-then-wrote, and Vercel's CDN
# can serve an overwritten blob's stale body for up to 60s, letting a
# repair's write get silently undone by a merge racing against that
# staleness). Nothing is ever deleted (retention/"show the latest 2" is a
# READ-time choice — see scripts.simulate_dialogue_week._load_memories —
# never a write-time eviction).
#
# The legacy bundled backend/data/characters/<slug>/memory.json files
# remain in place, read-only, as a DISPLAY-ONLY fallback for a character
# with zero weeks in durable storage — never written to, never merged
# into durable storage, and never deduped: round-3 review finding 2 is
# that those files' 3 same-labelled "week" entries are actually 3
# DIFFERENT recipes the old buggy writer mislabelled with the same week,
# so deduping them (as an earlier round of this fix did) permanently
# destroyed 2 of 3 real histories. See
# backend.admin.cron_routes._load_legacy_memory_entries and
# scripts.simulate_dialogue_week's equivalent.
CHARACTER_MEMORY_DIR = ROOT / "data" / "character_memory"

SOCIAL_IMAGE_SIZE = (1200, 630)
SOCIAL_IMAGE_SUFFIX = ".social.jpg"

# #6755 — width-limited WebP variants generated alongside the full-size
# sibling so the renderer's srcset can offer a phone something smaller than
# the 1536px original. Widths are deterministic and public (baked into blob
# keys and srcset markup), so changing this tuple requires re-running
# scripts/backfill_image_variants.py for every already-published image.
WEBP_VARIANT_WIDTHS: tuple[int, ...] = (400, 800)

# #7185 — sized JPEG sibling for the <picture> <img> fallback. The <source>
# above already offers a WebP srcset; the <img> is what a browser that
# cannot decode WebP at all falls through to, and until this shipped that
# fallback was the raw ~1536px/3-4MB PNG. One width, not a tuple: <img> has
# exactly one src, unlike <source>'s srcset which offers several candidates
# for the browser to pick by viewport. Changing this value requires
# re-running scripts/backfill_image_variants.py for already-published images.
JPEG_FALLBACK_WIDTH: int = 1200

# The hero's own display shape (#7185 review round 4) — MUST match
# `.recipe-hero__image { aspect-ratio: 16 / 9; ... overflow: hidden }` /
# `.recipe-hero__image img { object-fit: cover }` in src/assets/site.css.
# The fallback is encoded to exactly this aspect ratio (see
# _encode_jpeg_fallback) so `object-fit: cover` into that box crops NOTHING
# further — a square (or any other) crop would discard real content for a
# source whose own aspect ratio differs from the box's (round 3 got this
# wrong: a 1200x1200 crop of a 1600x800 source throws away half its width
# before the browser crops again). tests/test_image_compression.py parses
# site.css's actual rule and fails if it ever drifts from this constant.
HERO_ASPECT: tuple[int, int] = (16, 9)

# The fallback's height, derived from HERO_ASPECT so it can never disagree
# with JPEG_FALLBACK_WIDTH's own aspect ratio. round() is defensive — with
# today's values (1200, (16, 9)) this is already an exact 675.
JPEG_FALLBACK_HEIGHT: int = round(JPEG_FALLBACK_WIDTH * HERO_ASPECT[1] / HERO_ASPECT[0])

# Public, prefix-free base of the (public) blob store. Existence checks HEAD
# this host: a HEAD against the API host (https://blob.vercel-storage.com/<key>)
# returns 404 even for blobs that exist — verified 2026-09-05 against a live
# variant that the public URL served with 200 — so it must never be used as a
# presence probe. Same host the catalog and title readers hardcode.
BLOB_PUBLIC_BASE = "https://gtczmjysc51nh8fq.public.blob.vercel-storage.com"

# Historical uploads from before the deterministic-pathname fix (#5251) landed
# at Vercel's auto-appended `<stem>-<20+ char hash>.png`, while every sibling
# (WebP, JPEG fallback) is always keyed off the clean `<stem>.png` identity.
_VERCEL_RANDOM_SUFFIX_RE = re.compile(r"-[A-Za-z0-9]{20,}\.png$", re.IGNORECASE)


def _canonical_png_key(png_key: str) -> str:
    """Strip Vercel's legacy random-hash suffix from a PNG path or key (#7185 review).

    The ONE place that decides what "clean" means for a PNG identity, so
    every sibling-key builder below (and episode_renderer's URL rewriters,
    which import this) always agree on the same key for the same photo —
    whether the caller is the upload path (already clean, x-add-random-
    suffix=0), a backfill script walking historical episode JSON (which can
    still carry the suffix), or the renderer's own existence probe. Before
    this was centralized, the probe (unstripped) and the rendered <img> src
    (stripped) could disagree about which JPEG-fallback key existed —
    HIGH-severity review finding on #7185.
    """
    return _VERCEL_RANDOM_SUFFIX_RE.sub(".png", png_key)


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
    save_image's headers below). Canonicalizes the input first (#7185
    review) so a historical suffixed png_key (e.g. from the backfill script
    walking old episode JSON) still lands on the same key the renderer's
    stripped rewrite expects.
    """
    if not png_key.lower().endswith(".png"):
        raise ValueError(f"WebP variants require a PNG key: {png_key!r}")
    canonical = _canonical_png_key(png_key)
    return f"{canonical[:-4]}-{width}w.webp"


def _jpeg_fallback_key(png_key: str) -> str:
    """Return the deterministic sized-JPEG fallback key for a PNG key (#7185).

    Distinct from SOCIAL_IMAGE_SUFFIX's '.social.jpg' (a fixed 1200x630 crop
    for OG/Twitter cards): this sibling preserves the source aspect ratio —
    same resize semantics as the WebP width variants above — so it is a
    faithful, smaller stand-in for the full photo, suitable as the <picture>
    <img> fallback. Mirrors _webp_variant_key's naming: '<stem>-{width}w.jpg'.
    Canonicalizes the input first — this is the single function the uploader
    (_upload_jpeg_fallback), the backfill script, the renderer's existence
    probe (via jpeg_fallback_available), AND the renderer's rendered URL
    (episode_renderer._to_jpeg_fallback_url calls this directly) all share,
    so none of the four can ever disagree about the key for a suffixed
    historical PNG (review finding on #7185).
    """
    if not png_key.lower().endswith(".png"):
        raise ValueError(f"JPEG fallback variant requires a PNG key: {png_key!r}")
    canonical = _canonical_png_key(png_key)
    return f"{canonical[:-4]}-{JPEG_FALLBACK_WIDTH}w.jpg"


# Matches any width-limited WebP/JPEG variant key this module produces —
# '-{digits}w.webp' or '-{digits}w.jpg' — regardless of which specific
# widths WEBP_VARIANT_WIDTHS/JPEG_FALLBACK_WIDTH currently configure, so
# _source_png_key keeps working if those values ever change.
_WEBP_SIBLING_SUFFIX_RE = re.compile(r"-\d+w\.webp$", re.IGNORECASE)
_JPEG_FALLBACK_SUFFIX_RE = re.compile(r"-\d+w\.jpg$", re.IGNORECASE)


def _source_png_key(sibling_key: str) -> str:
    """Return the canonical source-PNG key for any UNAMBIGUOUS upload-time sibling.

    The inverse of _webp_variant_key / _jpeg_fallback_key / _social_jpeg_key:
    a width-limited WebP variant ('-{N}w.webp'), the sized JPEG <img>
    fallback ('-{N}w.jpg'), or the social crop ('.social.jpg') can ONLY be
    one of this module's derived siblings — that exact suffix shape is never
    used for anything else — so each resolves unambiguously back to the one
    PNG it was derived FROM. A key that is already a '.png' passes through
    unchanged (nothing to invert).

    A bare, width-less '.webp' is deliberately NOT inverted here (#7185
    review round 3, MEDIUM): _upload_webp_sibling names the full-size
    derived sibling exactly '<stem>.webp', but a filename alone cannot prove
    a given '.webp' IS that derived sibling rather than an original,
    hand-authored 'custom.webp' hero with no PNG behind it at all — inverting
    it unconditionally would pin a hero to a PNG that was never uploaded.
    scripts/pin_published_heroes.py resolves that one ambiguous case itself,
    by HEADing the public Blob URL to confirm the candidate PNG actually
    exists before treating a bare '.webp' as a generated sibling. Any other
    key (a genuinely unrelated format, or a seed image with no PNG sibling
    at all) passes through unchanged — nothing here knows how to invert it.

    Anything that reads a hero's identity back out of RENDERED HTML — e.g.
    pin_published_heroes.py, which reads the live page's <img src> and
    writes it into episode.hero_image_url — must route through this before
    storing that value. Since #7185 shipped, that <img src> can be the sized
    JPEG fallback instead of the raw PNG; storing it verbatim would
    permanently swap the hero's source of truth from the canonical PNG to a
    lossy JPEG, and every future render would then derive the WebP <source>
    and the JPEG fallback FROM that JPEG's own (nonexistent) '.png'-shaped
    sibling names, silently losing all of them (HIGH finding on #7185
    review round 2).
    """
    lowered = sibling_key.lower()
    if lowered.endswith(".png"):
        return sibling_key
    if lowered.endswith(SOCIAL_IMAGE_SUFFIX):
        return sibling_key[: -len(SOCIAL_IMAGE_SUFFIX)] + ".png"
    if lowered.endswith(".webp"):
        stripped = _WEBP_SIBLING_SUFFIX_RE.sub(".png", sibling_key)
        if stripped != sibling_key:
            return stripped
        # Bare full-size '.webp' — ambiguous, see docstring above. Left
        # un-inverted; the caller decides (existence-check, etc.).
        return sibling_key
    if lowered.endswith(".jpg") or lowered.endswith(".jpeg"):
        stripped = _JPEG_FALLBACK_SUFFIX_RE.sub(".png", sibling_key)
        if stripped != sibling_key:
            return stripped
    return sibling_key


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


def _encode_jpeg_fallback(png_bytes: bytes) -> bytes:
    """Encode PNG bytes as a fixed HERO_ASPECT-shaped JPEG (#7185 review round 4).

    TRUE BY CONSTRUCTION, not a guess: every fallback is exactly
    JPEG_FALLBACK_WIDTH x JPEG_FALLBACK_HEIGHT (1200x675 — HERO_ASPECT's
    16:9, not a square), center-cropped with ImageOps.fit — the same
    fixed-size crop _encode_social_jpeg already uses for the OG/Twitter
    sibling, just a different ratio — so the renderer can always state the
    <img>'s width/height exactly, with no per-source aspect-ratio
    computation, guess, or omission (round 1 assumed the source's own
    1536x1536 dimensions; round 2 computed a real scaled height and, failing
    that, omitted it, which broke scripts/health_check.py's requirement that
    every <img> carry both width and height; round 3 fixed THAT by cropping
    to a fixed 1200x1200 SQUARE — true by construction, but WRONG: cropping
    a 1600x800 source to a square first, then having the browser's
    `object-fit: cover` crop that square again to fit the hero's real 16:9
    box, throws away roughly half the source's width before the visible
    crop even happens — real content disappears for any browser that ends
    up on this fallback).

    HERO_ASPECT matches `.recipe-hero__image { aspect-ratio: 16 / 9; ...
    overflow: hidden }` / `.recipe-hero__image img { object-fit: cover }` in
    src/assets/site.css exactly, so `cover`-ing a 16:9 image into a 16:9 box
    crops NOTHING further — this fallback is pixel-for-pixel what that box
    already displays, not a second, lossy crop of it. If that CSS rule's
    ratio ever changes, HERO_ASPECT must change with it (a test parses the
    rule and fails on drift).
    """
    return _encode_jpeg(png_bytes, (JPEG_FALLBACK_WIDTH, JPEG_FALLBACK_HEIGHT))


_CHARACTER_MEMORY_MAX_LIST_PAGES = 20
# Bound on the prefix listing _CloudBackend._find_exact_blob scans for an
# exact pathname. Same-prefix siblings are rare; this only stops a runaway.
_EXACT_BLOB_MAX_LIST_PAGES = 20


class CharacterMemoryUnavailable(Exception):
    """A character-memory READ could not be completed (#6968).

    Distinct from a genuine "no weeks written yet" (which is an empty list
    from list_character_memory_weeks, not an exception): this means the
    attempt itself failed or returned something unusable — a transient
    Blob error, a network timeout, a corrupted local file, a listed name
    the list API itself couldn't be trusted for, or a fetched body that
    fails schema validation. A caller must never treat this the same as
    "not found": it must record the character's memory step as failed (or,
    for a prompt read, fall back to the truthful known-coworker text) and
    never silently substitute the legacy seed or emptiness for real,
    merely-unreadable data.
    """


class PageReadError(RuntimeError):
    """A page READ could not be completed (#7833).

    ``load_page`` returns None only when the page is genuinely absent (the
    Blob list API answered and listed nothing under that key). A non-OK
    list or content response, a network error or timeout, or an unusable
    list payload raises this instead. The two used to share the None
    return, so one Blob 5xx during the Sunday publish read as "no catalog
    yet" and the publisher re-seeded the live catalog from src/recipes.json
    over every cron-published recipe.
    """


_ISO_WEEK_RE = re.compile(r"^(\d{4})-W(\d{2})$")


def parse_iso_week(week: str) -> tuple[int, int]:
    """Parse a "YYYY-Www" ISO week string into a ``(year, week)`` sort key.

    Raises ValueError if ``week`` is not exactly this zero-padded shape or
    the week number is out of the 1-53 range — a synthetic/test label such
    as "test-week" is never a valid ISO week. Deliberately strict: this is
    the sort key that decides which weeks are shown as most recent, so an
    unparsed label must never silently participate in that ordering as if
    it were real.
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
    except ValueError:  # governance: allow-silent SF002: predicate; False is the true answer for a string parse_iso_week rejects
        return False


# The schema every per-week memory blob body must satisfy (#6968 round-3
# review finding 3: a valid-JSON-but-wrong-shape body, e.g. `{}`, must never
# be accepted as usable history). "week" and "episode_id" are the same
# value in this pipeline (the ISO week IS the episode ID) but both are
# required so the body is self-describing without needing its own blob key.
_REQUIRED_CHARACTER_MEMORY_FIELDS = ("week", "episode_id", "summary")


def validate_character_memory_entry(entry: object) -> dict:
    """Validate one per-week character-memory blob body.

    Required: ``week`` (a real ISO week string), ``episode_id`` and
    ``summary`` (non-empty strings). Optional fields (``concept``,
    ``key_moment``, ``recipe``, ``written_at``, ...) pass through
    unvalidated. Raises ValueError on any violation.

    This is the single schema gate for both directions: save_character_
    memory_week calls it before writing (a write can never put a bad shape
    into durable storage), and load_character_memory_week calls it after
    fetching (a read can never mistake a malformed or wrong-shaped body —
    including well-formed JSON like ``{}`` — for real history).
    """
    if not isinstance(entry, dict):
        raise ValueError(f"memory entry must be a dict, got {type(entry).__name__}")
    for field in _REQUIRED_CHARACTER_MEMORY_FIELDS:
        value = entry.get(field)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"memory entry missing required non-empty string field {field!r}: {entry!r}")
    if not is_valid_iso_week(entry["week"]):
        raise ValueError(f"memory entry 'week' is not a valid ISO week: {entry['week']!r}")
    return entry


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

    def load_episode_strict(self, episode_id: str) -> Optional[dict]:
        """Same as load_episode.

        The filesystem backend has no cloud fallback path to mask a read
        failure with — a missing file already returns None cleanly, and any
        other failure (a malformed JSON file, a permissions error) already
        propagates as a real exception. "Strict" is the filesystem backend's
        only mode, so this exists only so callers that need the not-found
        vs. error distinction (#7630) can call one method name regardless of
        which backend `storage` resolved to.
        """
        return self.load_episode(episode_id)

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

    def _character_memory_dir(self, slug: str) -> Path:
        """Prefix-scoped directory for one character's per-week blobs.

        Scoped by `self.prefix` the same way the cloud backend's blob key
        is — "test/" and "" resolve to different directories on disk, so
        test-mode data can never cross into production memory through this
        path.
        """
        return CHARACTER_MEMORY_DIR / f"{self.prefix}{slug}"

    def list_character_memory_weeks(self, slug: str) -> list[str]:
        """List the valid ISO week keys with a durable memory file (#6968).

        A filename that isn't a real ISO week (see parse_iso_week) is
        logged and ignored, never treated as evidence of absence for a
        real week. Returns weeks sorted ascending (oldest first). An empty
        result is a genuine "no weeks written yet" — filesystem globbing
        cannot fail the way a network list call can, so there is no
        CharacterMemoryUnavailable case here.
        """
        directory = self._character_memory_dir(slug)
        if not directory.exists():
            return []
        weeks: list[str] = []
        for p in directory.glob("*.json"):
            if is_valid_iso_week(p.stem):
                weeks.append(p.stem)
            else:
                logger.warning(f"Ignoring non-ISO-week memory filename for {slug}: {p.name!r}")
        weeks.sort(key=parse_iso_week)
        return weeks

    def load_character_memory_week(self, slug: str, week: str) -> dict:
        """Fetch and validate one week's memory blob body (#6968).

        Raises CharacterMemoryUnavailable on a read/parse failure OR a
        schema violation (validate_character_memory_entry) — a caller must
        never mistake a missing/corrupted/malformed body for real history.
        """
        path = self._character_memory_dir(slug) / f"{week}.json"
        try:
            data = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError) as e:
            raise CharacterMemoryUnavailable(f"local read failed for {slug!r}/{week!r}: {e}") from e
        try:
            return validate_character_memory_entry(data)
        except ValueError as e:
            raise CharacterMemoryUnavailable(f"malformed memory body for {slug!r}/{week!r}: {e}") from e

    def save_character_memory_week(self, slug: str, week: str, entry: dict) -> None:
        """Persist one week's memory blob body (#6968), scoped by prefix.

        Validates `entry` first (validate_character_memory_entry) so a
        malformed body can never be written. A single PUT of this week's
        own key — no read, no merge — so re-running the same week is an
        idempotent overwrite and can never touch any other week's blob.
        """
        validate_character_memory_entry(entry)
        if entry["week"] != week:
            raise ValueError(f"entry week {entry['week']!r} does not match target week {week!r}")
        path = self._character_memory_dir(slug) / f"{week}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(entry, indent=2))

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
            try:
                jpeg_variant_path = dest.with_name(f"{dest.stem}-{JPEG_FALLBACK_WIDTH}w.jpg")
                jpeg_variant_path.write_bytes(_encode_jpeg_fallback(image_bytes))
            except Exception as e:  # noqa: BLE001 - same rationale as above
                logger.warning(f"JPEG fallback write failed for {dest}: {e}")

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
        except ValueError:  # governance: allow-silent SF002: existence probe; a key with no derivable variant name has no variants, so the renderer correctly falls back to the single PNG candidate
            return False
        # Every width, not just the smallest: uploads are per-width and
        # best-effort, so one can be missing while another exists (review
        # finding, 2026-09-05) — and one 404 candidate breaks the image.
        return all((IMAGES_DIR / key).exists() for key in variant_keys)

    def jpeg_fallback_available(self, image_key: str) -> bool:
        """Whether the sized JPEG <img> fallback exists for a rendered image (#7185).

        Same '<subpath>/name.png' identity as image_variants_available.
        """
        if not image_key.lower().endswith(".png"):
            return False
        try:
            key = _jpeg_fallback_key(image_key)
        except ValueError:  # governance: allow-silent SF002: existence probe; a key with no derivable fallback name has no JPEG fallback, so the renderer keeps the raw PNG src
            return False
        return (IMAGES_DIR / key).exists()

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
        self._blob_token = os.environ.get("BLOB_READ_WRITE_TOKEN", "")  # governance: allow-silent SF003: empty is checked in __init__ below (raises on Vercel) and _has_cloud() routes every call to the filesystem backend off Vercel
        self._fs = _FilesystemBackend()  # fallback for local data
        self.prefix: str = ""  # "test/" for test mode, "" for production
        # In-memory cache: (storage prefix, episode_id) -> dict. Populated by
        # save_episode so that load_episode in the same Lambda invocation gets
        # fresh data without hitting the CDN (which may serve stale content
        # for seconds).
        self._episode_cache: dict[tuple[str, str], dict] = {}
        self._page_cache: dict[str, str] = {}
        # Character memory (#6968) deliberately has NO cache: each write is
        # a single PUT of one week's own key with no preceding read, so
        # there is nothing for a cache to make stale in a way that could
        # cause data loss (round-3 review finding 1) — see
        # list_character_memory_weeks/load_character_memory_week/
        # save_character_memory_week below.
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

    def _find_exact_blob(self, key: str, *, reject_non_object_entries: bool = False) -> Optional[dict]:
        """Return the listed blob whose pathname is exactly ``key``, or None.

        The list API matches by PREFIX, so ``prefix=pages/recipes.json`` also
        lists ``pages/recipes.json.bak``. Taking ``blobs[0]`` of a ``limit=1``
        listing let such a sibling stand in for a missing file, or hide the
        real one (#7833). Every page of the listing is scanned for an exact
        pathname match instead.

        None means the list API answered and no blob has exactly this
        pathname. Any failed or unusable listing raises PageReadError.
        ``reject_non_object_entries`` also treats a non-object entry as an
        unusable listing, for a caller whose not-found drives a decision
        (load_episode_strict, #7630); others skip such entries.
        """
        import requests as _requests

        cursor: Optional[str] = None
        for _ in range(_EXACT_BLOB_MAX_LIST_PAGES):
            params: dict = {"prefix": key, "limit": "100"}
            if cursor:
                params["cursor"] = cursor
            try:
                resp = _requests.get(
                    self._BLOB_API,
                    params=params,
                    headers=self._auth_headers(),
                    timeout=15,
                )
            except _requests.RequestException as e:
                raise PageReadError(f"Blob list failed for {key!r}: {type(e).__name__}: {e}") from e
            if not resp.ok:
                raise PageReadError(f"Blob list for {key!r} returned HTTP {resp.status_code}")
            try:
                payload = resp.json()
            except ValueError as e:
                raise PageReadError(f"Blob list for {key!r} returned an unusable payload: {e}") from e
            if not isinstance(payload, dict) or not isinstance(payload.get("blobs"), list):
                raise PageReadError(f"Blob list for {key!r} has no 'blobs' list")
            if reject_non_object_entries and not all(
                isinstance(blob, dict) for blob in payload["blobs"]
            ):
                raise PageReadError(f"Blob list for {key!r} has a non-object entry")

            for blob in payload["blobs"]:
                if isinstance(blob, dict) and blob.get("pathname") == key:
                    return blob

            if not payload.get("hasMore"):
                return None
            next_cursor = payload.get("cursor")
            if not isinstance(next_cursor, str) or not next_cursor or next_cursor == cursor:
                raise PageReadError(f"Blob list for {key!r}: hasMore without a new cursor")
            cursor = next_cursor
        raise PageReadError(
            f"Blob list for {key!r} exceeded {_EXACT_BLOB_MAX_LIST_PAGES} pages"
        )

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
            blob = self._find_exact_blob(pathname)
            if blob is None:
                return self._fs.load_episode(episode_id)

            blob_url = blob["url"]
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

        Also used by cron_routes._apply_week_off_note (#7630): a read error
        here must be distinguishable from a genuine "no episode for that
        week" — ``load_episode``'s fallback-on-any-exception behavior would
        turn a transient Blob outage into a false "the week never published"
        note. Not found (an empty ``blobs`` list from a SUCCESSFUL API call
        with a well-formed body) returns ``None``; any read/network/API
        failure, OR a malformed 200 body (not a dict, ``blobs`` not a list,
        or a non-dict item in it — e.g. ``{}`` or ``{"blobs": "x"}``) raises
        instead of returning ``None``, so callers that need the not-found
        vs. error distinction get it for free by calling this instead of
        ``load_episode``.
        """
        if not self._has_cloud():
            return self._fs.load_episode(episode_id)

        import requests as _requests

        cache_key = (self.prefix, episode_id)
        # Never answered from the cache (Codex, #7630): this is the read a
        # cron DECISION rests on, and a warm Lambda's cached copy can predate
        # a publish another instance has since made. The fresh result still
        # refreshes the cache below.

        pathname = f"{self.prefix}episodes/{episode_id}.json"
        blob = self._find_exact_blob(pathname, reject_non_object_entries=True)
        if blob is None:
            return None
        content_resp = _requests.get(blob["url"], timeout=15)
        content_resp.raise_for_status()
        data = content_resp.json()
        # A decision rests on this body: valid JSON that is not an episode
        # object is an unusable read, never "an unpublished week" (#7630).
        if not isinstance(data, dict):
            raise PageReadError(
                f"episode {episode_id!r} body is {type(data).__name__}, not an object"
            )
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

    # --- Character memory (#6968, redesigned in review round 3) ---

    def list_character_memory_weeks(self, slug: str) -> list[str]:
        """List the valid ISO week keys with a durable memory blob for `slug`.

        Uses the Blob LIST API — authoritative for which keys exist, unlike
        a blob's own body, which the CDN can serve stale for up to 60s
        after an overwrite (round-3 review finding 1). A listed pathname
        that doesn't parse as a real ISO week is logged and ignored, never
        treated as evidence of absence for a real week. Returns weeks
        sorted ascending (oldest first).

        Raises CharacterMemoryUnavailable if the list call itself fails or
        returns a malformed payload (round-3 review finding 3 extends the
        round-2 malformed-payload guard to this call too) — that is
        "unavailable", never "not found". A well-formed response with no
        matching blobs is the only genuine not-found, returned as `[]`.
        """
        if not self._has_cloud():
            return self._fs.list_character_memory_weeks(slug)

        import requests as _requests

        prefix = f"{self.prefix}character_memory/{slug}/"
        weeks: list[str] = []
        cursor: Optional[str] = None
        seen_cursors: set[str] = set()
        # A character gains one blob a week, so a handful of pages covers
        # years of history; the cap only stops a malformed paginated
        # response from looping forever inside a live cron.
        for _page in range(_CHARACTER_MEMORY_MAX_LIST_PAGES):
            params: dict = {"prefix": prefix, "limit": "100"}
            if cursor:
                params["cursor"] = cursor
            try:
                resp = _requests.get(
                    self._BLOB_API, params=params, headers=self._auth_headers(), timeout=15
                )
                resp.raise_for_status()
                payload = resp.json()
            except Exception as e:
                logger.error(f"Blob list_character_memory_weeks failed for {slug}: {type(e).__name__}: {e}")
                raise CharacterMemoryUnavailable(f"list failed for {slug!r}: {e}") from e

            if not isinstance(payload, dict) or not isinstance(payload.get("blobs"), list):
                logger.error(f"Blob list_character_memory_weeks malformed payload for {slug}: {payload!r}")
                raise CharacterMemoryUnavailable(f"malformed list payload for {slug!r}: {payload!r}")

            for blob in payload["blobs"]:
                pathname = blob.get("pathname", "") if isinstance(blob, dict) else ""
                if not pathname.endswith(".json"):
                    continue
                week = pathname.removeprefix(prefix).removesuffix(".json")
                if is_valid_iso_week(week):
                    weeks.append(week)
                else:
                    logger.warning(f"Ignoring non-ISO-week memory blob for {slug}: {pathname!r}")

            if not payload.get("hasMore"):
                break
            next_cursor = payload.get("cursor")
            if not isinstance(next_cursor, str) or not next_cursor or next_cursor in seen_cursors:
                logger.error(
                    f"Blob list_character_memory_weeks for {slug}: hasMore without a new cursor "
                    f"({next_cursor!r})"
                )
                raise CharacterMemoryUnavailable(f"malformed pagination for {slug!r}")
            seen_cursors.add(next_cursor)
            cursor = next_cursor
        else:
            logger.error(f"Blob list_character_memory_weeks for {slug}: more than "
                         f"{_CHARACTER_MEMORY_MAX_LIST_PAGES} pages")
            raise CharacterMemoryUnavailable(f"too many list pages for {slug!r}")

        weeks.sort(key=parse_iso_week)
        return weeks

    def load_character_memory_week(self, slug: str, week: str) -> dict:
        """Fetch and validate one week's memory blob body from Vercel Blob.

        Fetched via the CDN URL the list API returns — the only way to
        read blob content — which can serve a stale body for up to 60s
        after an overwrite. That staleness is no longer a data-loss risk
        (round-3 review finding 1 fixed the WRITE side: there is no
        read-modify-write, so a stale read here can at worst show slightly
        outdated prompt content for a short window, never cause a write to
        silently drop another week).

        Raises CharacterMemoryUnavailable on a "not found" (the specific
        week's blob doesn't exist — a caller that got this week from
        list_character_memory_weeks and then fails to fetch it has hit a
        genuine unavailability, not an absence), any other fetch/parse
        failure, or a schema violation (round-3 review finding 3: a
        well-formed-but-wrong-shape body like ``{}`` must never be treated
        as usable history).
        """
        if not self._has_cloud():
            return self._fs.load_character_memory_week(slug, week)

        import requests as _requests

        pathname = f"{self.prefix}character_memory/{slug}/{week}.json"
        try:
            blob = self._find_exact_blob(pathname)
        except Exception as e:
            logger.error(f"Blob load_character_memory_week list failed for {slug}/{week}: {type(e).__name__}: {e}")
            raise CharacterMemoryUnavailable(f"list failed for {slug!r}/{week!r}: {e}") from e

        if blob is None:
            logger.error(f"Blob load_character_memory_week: {slug}/{week} not found")
            raise CharacterMemoryUnavailable(f"blob not found for {slug!r}/{week!r}")

        try:
            content_resp = _requests.get(blob["url"], timeout=15)
            content_resp.raise_for_status()
            data = content_resp.json()
        except Exception as e:
            logger.error(
                f"Blob load_character_memory_week content fetch failed for {slug}/{week}: {type(e).__name__}: {e}"
            )
            raise CharacterMemoryUnavailable(f"content fetch failed for {slug!r}/{week!r}: {e}") from e

        try:
            return validate_character_memory_entry(data)
        except ValueError as e:
            raise CharacterMemoryUnavailable(f"malformed memory body for {slug!r}/{week!r}: {e}") from e

    def save_character_memory_week(self, slug: str, week: str, entry: dict) -> None:
        """Persist one week's memory blob body to Vercel Blob.

        Validates `entry` first (validate_character_memory_entry) so a
        malformed body can never be written. A single PUT of this week's
        own key — no read, no merge (round-3 review finding 1) — so
        re-running the same week is an idempotent overwrite that can never
        touch or lose any other week's blob. Raises on cloud failure — same
        contract as save_episode — so a caller can record the write as
        failed instead of silently claiming success.
        """
        validate_character_memory_entry(entry)
        if entry["week"] != week:
            raise ValueError(f"entry week {entry['week']!r} does not match target week {week!r}")

        if not self._has_cloud():
            self._fs.save_character_memory_week(slug, week, entry)
            return

        import requests as _requests

        pathname = f"{self.prefix}character_memory/{slug}/{week}.json"
        body = json.dumps(entry, indent=2)
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
            logger.info(f"Saved character memory week to Vercel Blob: {blob_url}")
        except Exception as e:
            logger.error(f"Blob save_character_memory_week failed for {slug!r}/{week!r}: {e}")
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
        """Load a page from Vercel Blob.

        Returns the content, or None only when the page is genuinely absent
        (the list API answered OK and no blob has exactly this pathname). Any
        failed read raises PageReadError — a caller must never mistake a
        Blob outage for "no such page" (#7833).
        """
        if not self._has_cloud():
            return self._fs.load_page(pathname)

        import requests as _requests

        key = f"{self.prefix}{pathname}"

        # Check in-memory cache first (avoids CDN staleness)
        if key in self._page_cache:
            return self._page_cache[key]

        blob = self._find_exact_blob(key)
        if blob is None:
            return None

        # Fetch content from CDN URL. The list just said the blob exists, so
        # any non-OK answer here (404 included) is a failed read, not absence.
        try:
            content_resp = _requests.get(blob["url"], timeout=15)
        except _requests.RequestException as e:
            raise PageReadError(f"Blob content fetch failed for {key!r}: {type(e).__name__}: {e}") from e
        except (KeyError, TypeError) as e:
            raise PageReadError(f"Blob list entry for {key!r} has no url: {e}") from e
        if not content_resp.ok:
            raise PageReadError(f"Blob content for {key!r} returned HTTP {content_resp.status_code}")
        # CRITICAL: Use .content.decode() not .text — requests defaults
        # to ISO-8859-1 for text/* without explicit charset, which
        # double-encodes UTF-8 smart quotes/symbols into mojibake.
        try:
            return content_resp.content.decode("utf-8")
        except UnicodeDecodeError as e:
            raise PageReadError(f"Blob content for {key!r} is not valid UTF-8: {e}") from e

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
            # #7185 — sized JPEG <img> fallback, independent of the social
            # crop above (different aspect ratio, different purpose).
            try:
                self._upload_jpeg_fallback(key, image_bytes)
            except Exception as e:  # noqa: BLE001 - optimization must not block publishing
                logger.warning(f"JPEG fallback pipeline failed for {key}: {e}")

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
        # governance: allow-silent SF002: WebP sibling is an optional optimization; the canonical PNG is already uploaded and image_variants_available probes before the renderer references a variant
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
        # governance: allow-silent SF002: WebP variant is an optional optimization; image_variants_available HEAD-checks every width before the renderer emits a srcset
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
        # governance: allow-silent SF002: social JPEG is optional metadata; the canonical PNG upload is the only publishing dependency
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

    def _upload_jpeg_fallback(self, png_key: str, png_bytes: bytes) -> None:
        """Best-effort upload of the sized JPEG <img> fallback sibling (#7185).

        Same one-way best-effort shape as _upload_webp_variant/_upload_social_
        jpeg_sibling: logs and swallows both encode and upload failures so the
        canonical PNG upload above always remains the only publishing
        dependency.
        """
        try:
            jpeg_bytes = _encode_jpeg_fallback(png_bytes)
        # governance: allow-silent SF002: JPEG fallback is optional; jpeg_fallback_available HEAD-checks it before the renderer references it
        except Exception as e:
            logger.warning(f"JPEG fallback encode failed for {png_key}: {e}")
            return

        import requests as _requests

        variant_key = _jpeg_fallback_key(png_key)
        upload_url = f"https://blob.vercel-storage.com/{variant_key}"
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
                f"Uploaded JPEG fallback: {variant_key} "
                f"({len(jpeg_bytes)}B from {len(png_bytes)}B PNG)"
            )
        except Exception as e:
            logger.warning(f"JPEG fallback upload failed for {variant_key}: {e}")

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
        except ValueError:  # governance: allow-silent SF002: existence probe; no derivable variant key means no variants, renderer falls back to the single PNG candidate
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
            # governance: allow-silent SF002: existence probe; an unverifiable variant must not be emitted in a srcset, so False (single PNG candidate) is the safe answer
            except Exception as e:
                logger.warning(f"Variant existence check failed for {key}: {e}")
                return False
            if resp.status_code != 200:
                return False
        return True

    def jpeg_fallback_available(self, image_key: str) -> bool:
        """HEAD-check the sized JPEG <img> fallback blob for a rendered image (#7185).

        Same public-host HEAD contract as image_variants_available above — the
        API host 404s for existing blobs (see BLOB_PUBLIC_BASE) — and the same
        missing-means-not-backfilled-yet semantics: the renderer falls back to
        the raw PNG <img> src rather than ever emitting a URL that 404s.
        """
        if not self._has_cloud():
            return self._fs.jpeg_fallback_available(image_key)
        if not image_key.lower().endswith(".png"):
            return False
        try:
            variant_key = _jpeg_fallback_key(f"images/{image_key}")
        except ValueError:  # governance: allow-silent SF002: existence probe; no derivable fallback key means no JPEG fallback, renderer keeps the raw PNG src
            return False

        import requests as _requests

        key = f"{self.prefix}{variant_key}"
        try:
            resp = _requests.head(f"{BLOB_PUBLIC_BASE}/{key}", timeout=10, allow_redirects=True)
        # governance: allow-silent SF002: existence probe; an unverifiable fallback must not be emitted, so False (raw PNG src) is the safe answer
        except Exception as e:
            logger.warning(f"JPEG fallback existence check failed for {key}: {e}")
            return False
        return resp.status_code == 200

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
