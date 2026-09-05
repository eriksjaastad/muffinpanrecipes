#!/usr/bin/env python3
"""Production and preview health check for muffinpanrecipes (#5918).

Read-only synthetic monitor. Asserts production invariants that would
have caught the #5911 test-mode contamination incident within 60 seconds.
Exits 0 on pass, non-zero on any failure. Preview runs are automatically
side-effect free; production runs optionally post a Discord alert when
MUFFINPAN_DISCORD_WEBHOOK is set.

Run modes:
    # Manual
    doppler run -- uv run python scripts/health_check.py

    # Fail on catalog drop
    uv run python scripts/health_check.py --baseline 15

    # Post-deploy preview (CI)
    uv run python scripts/health_check.py --base-url https://preview.example

See RUNBOOK.md Incident 1 for the incident this is designed to catch.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urlunsplit
from xml.etree import ElementTree

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.utils.episode_integrity import (  # noqa: E402
    episode_integrity_failures,
    episode_summary,
)

# Persisted last-run status so we only ping "recovered" on an actual
# FAIL -> PASS transition (not on every healthy run). The synthetic monitor
# runs from a fixed machine, so a small on-disk file is enough; override the
# path with MUFFINPAN_HEALTH_STATE_FILE if it ever runs somewhere ephemeral.
DEFAULT_STATE_FILE = str(
    Path.home() / ".local" / "state" / "muffinpanrecipes" / "health_status"
)


def _state_file() -> Path:
    # Resolved at call time so the env override actually takes effect (and so
    # tests can point it at a temp path) — a module-level constant would
    # freeze the path at import, before any override is set.
    return Path(os.environ.get("MUFFINPAN_HEALTH_STATE_FILE", DEFAULT_STATE_FILE))

PRODUCTION_BASE_URL = "https://muffinpanrecipes.com"
BLOB_CDN = "https://gtczmjysc51nh8fq.public.blob.vercel-storage.com"
GA4_MEASUREMENT_ID = "G-05P73D3237"
CATALOG_BLOB_URL = f"{BLOB_CDN}/pages/recipes.json"
UNMATCHED_PATH = "/__health_check_unmatched__"
REQUIRED_SECURITY_HEADERS = (
    "x-frame-options",
    "x-content-type-options",
    "referrer-policy",
    "content-security-policy",
)
REQUIRED_SECURITY_HEADER_VALUES = {
    "x-frame-options": "DENY",
    "x-content-type-options": "nosniff",
    "referrer-policy": "strict-origin-when-cross-origin",
}
REQUIRED_CSP_DIRECTIVES = {
    "default-src": ("'self'",),
    "script-src": ("'self'", "'unsafe-inline'", "https://www.googletagmanager.com"),
    "style-src": ("'self'", "'unsafe-inline'", "https://fonts.googleapis.com"),
    "font-src": ("'self'", "https://fonts.gstatic.com"),
    "img-src": (
        "'self'",
        "data:",
        "https://www.google-analytics.com",
        "https://*.google-analytics.com",
    ),
    "connect-src": (
        "'self'",
        "https://www.google-analytics.com",
        "https://*.google-analytics.com",
        "https://*.analytics.google.com",
        "https://*.googletagmanager.com",
    ),
    "object-src": ("'none'",),
    "base-uri": ("'self'",),
    "form-action": ("'self'",),
    "frame-ancestors": ("'none'",),
}


def _normalize_base_url(base_url: str) -> str:
    """Return a safe absolute origin for all requests in one health run."""
    value = str(base_url).strip().rstrip("/")
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"base URL must be an absolute http(s) URL: {base_url!r}")
    if parsed.path or parsed.query or parsed.fragment:
        raise ValueError(f"base URL must not include a path, query, or fragment: {base_url!r}")
    return urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))


def _url(base_url: str, path: str) -> str:
    """Resolve a public path against the run's base URL."""
    return f"{base_url.rstrip('/')}/{path.lstrip('/')}"


def _header(headers: object, name: str) -> str | None:
    """Get a response header from requests or a plain dict, case-insensitively."""
    if headers is None:
        return None
    getter = getattr(headers, "get", None)
    if callable(getter):
        value = getter(name)
        if value:
            return str(value)
    items = getattr(headers, "items", None)
    if callable(items):
        for key, value in items():
            if str(key).lower() == name.lower() and value:
                return str(value)
    return None


@dataclass
class Report:
    passed: list[str] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)

    def check(self, name: str, fn: Callable[[], None]) -> None:
        try:
            fn()
            self.passed.append(name)
            print(f"  ✓ {name}")
        except AssertionError as e:
            self.failed.append((name, str(e)))
            print(f"  ✗ {name}: {e}")
        except Exception as e:
            self.failed.append((name, f"{type(e).__name__}: {e}"))
            print(f"  ✗ {name}: {type(e).__name__}: {e}")

    @property
    def ok(self) -> bool:
        return not self.failed


def _fetch_json(url: str, timeout: int = 15) -> dict | list:
    resp = requests.get(url, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def _fetch_text(url: str, timeout: int = 15) -> tuple[int, str]:
    resp = requests.get(url, timeout=timeout)
    return resp.status_code, resp.text


def _fetch_page(url: str, timeout: int = 15) -> tuple[int, str, object]:
    """Fetch a page while retaining headers needed by response-level checks."""
    resp = requests.get(url, timeout=timeout)
    return resp.status_code, resp.text, resp.headers


def current_iso_week_id() -> str:
    iso_year, iso_week, _ = date.today().isocalendar()
    return f"{iso_year}-W{iso_week:02d}"


def check_catalog_counts_match(
    report: Report, baseline: int, base_url: str = PRODUCTION_BASE_URL
) -> None:
    def _check():
        site_catalog = _fetch_json(_url(base_url, "/recipes.json"))
        site_count = (
            len(site_catalog)
            if isinstance(site_catalog, list)
            else len(site_catalog.get("recipes", []))
        )
        assert site_count >= baseline, (
            f"site catalog has {site_count} recipes, expected >= {baseline}. "
            f"Catalog shrinkage means something deleted or overwrote entries."
        )
        if base_url == PRODUCTION_BASE_URL:
            blob_catalog = _fetch_json(CATALOG_BLOB_URL)
            blob_count = (
                len(blob_catalog)
                if isinstance(blob_catalog, list)
                else len(blob_catalog.get("recipes", []))
            )
            assert site_count == blob_count, (
                f"site catalog has {site_count} recipes but blob has {blob_count}. "
                f"Drift here means FastAPI is reading stale or prefixed data."
            )

    report.check("catalog_counts_match_baseline", _check)


def check_teaser_current_week(report: Report, base_url: str = PRODUCTION_BASE_URL) -> None:
    def _check():
        data = _fetch_json(_url(base_url, "/api/episodes/teaser"))
        assert isinstance(data, dict), f"teaser returned non-dict: {type(data).__name__}"
        # On Sunday after publish, the endpoint suppresses the teaser
        # (read-side check in episode_routes.py) so the homepage Featured
        # hero isn't duplicated. {"status":"published"} is a healthy state.
        if data.get("status") == "published":
            return
        episode_id = data.get("episode_id") or ""
        assert episode_id, "teaser response missing episode_id"
        assert not episode_id.startswith("test-"), (
            f"teaser episode_id starts with 'test-': {episode_id!r}. "
            f"This is the #5911 contamination signature — test-mode prefix "
            f"is leaking into production reads."
        )
        expected = current_iso_week_id()
        assert episode_id == expected, (
            f"teaser episode_id is {episode_id!r}, expected {expected!r} (current ISO week)"
        )

    report.check("teaser_is_current_iso_week", _check)


def check_this_week_page(report: Report, base_url: str = PRODUCTION_BASE_URL) -> None:
    def _check():
        status, body = _fetch_text(_url(base_url, "/this-week"))
        assert status == 200, f"/this-week returned HTTP {status}"
        if len(body) > 20_000:
            return  # full episode page rendered — healthy

        # Thin page. Early in a new ISO week, /this-week is LEGITIMATELY a
        # placeholder until that week's Monday cron (14:30 UTC Mon) generates
        # the recipe. Only treat thin-ness as a failure once this week's Monday
        # stage is actually complete — otherwise it's the expected pre-cron
        # window and we must NOT alert (that was the Monday-morning false alarm).
        iso = datetime.now(timezone.utc).isocalendar()
        week_id = f"{iso.year}-W{iso.week:02d}"
        try:
            # A preview deployment may intentionally still be a placeholder
            # while the shared production Blob already has this week's
            # episode. Never let that shared state make a preview fail.
            episode = (
                _fetch_json(f"{BLOB_CDN}/episodes/{week_id}.json")
                if base_url == PRODUCTION_BASE_URL
                else None
            )
        except Exception:
            episode = None
        monday_done = (
            isinstance(episode, dict)
            and episode.get("stages", {}).get("monday", {}).get("status") == "complete"
        )
        assert not monday_done, (
            f"/this-week body is {len(body)} bytes, expected > 20000, and "
            f"{week_id} Monday IS complete — likely a real render failure."
        )
        print(
            f"    (this-week is the expected pre-cron placeholder for {week_id} "
            f"— Monday recipe not generated yet)"
        )

    report.check("this_week_renders", _check)


def _resolve_image_url(src: str, base_url: str = PRODUCTION_BASE_URL) -> str:
    """Resolve a page image reference to an absolute URL we can HEAD."""
    if src.startswith("/blob-images/"):
        return f"{base_url}/blob-images/{src[len('/blob-images/'):]}"
    if src.startswith("/"):
        return f"{base_url}{src}"
    return urljoin(f"{base_url.rstrip('/')}/", src)


def check_episode_integrity(
    report: Report,
    base_url: str = PRODUCTION_BASE_URL,
    expect_episode: str | None = None,
) -> None:
    """Assert the weekly episode is sound, not merely renderable (#6857).

    Every other check here looks at the SITE. This one looks at the PIPELINE
    that produced it, because the two can disagree completely: W36 rendered a
    real recipe with a real title on a healthy page while its concept was the
    placeholder, the novelty scorer had never run, and the recipe duplicated
    one already in the catalog. Six of seven checks passed. Nobody knew for
    five days.

    Reads the episode straight from the public blob CDN, so it needs no
    credentials and shares no state with the lambda. `base_url` is accepted
    for signature uniformity with the other checks and deliberately unused —
    the episode is pipeline state, identical whichever deployment you point at.
    """
    def _check() -> None:
        episode_id = expect_episode or current_iso_week_id()
        try:
            episode = _fetch_json(f"{BLOB_CDN}/episodes/{episode_id}.json")
        except Exception as exc:
            if expect_episode:
                # The operator asserted this episode must exist (#6828).
                raise AssertionError(
                    f"episode {episode_id} could not be read from blob: "
                    f"{type(exc).__name__}: {exc}"
                ) from exc
            # Before Monday's cron the current week legitimately has no
            # episode yet. Same pre-cron window check_this_week_page allows.
            print(f"    (no episode for {episode_id} yet — pre-Monday window)")
            return

        assert isinstance(episode, dict), (
            f"episode {episode_id} is not a JSON object"
        )

        catalog: list[dict] | None = None
        try:
            raw = _fetch_json(CATALOG_BLOB_URL)
            catalog = raw if isinstance(raw, list) else raw.get("recipes", [])
        except Exception as exc:
            # Skip only the title-collision assertion; the rest still run.
            print(f"    (catalog unavailable, skipping title check: {exc})")

        failures = episode_integrity_failures(episode, catalog=catalog)
        assert not failures, (
            f"{episode_id} is degraded ({episode_summary(episode)}):\n    - "
            + "\n    - ".join(failures)
        )
        print(f"    ({episode_summary(episode)})")

    report.check("episode_integrity", _check)


def check_expected_episode_renders(
    report: Report, base_url: str, expect_episode: str
) -> None:
    """Assert /this-week actually renders the episode the operator named (#6828).

    check_this_week_page only consults the Blob episode when base_url is
    production, because prod and preview share ONE Blob store and prod data
    saying "Monday complete" made previews fail. The cost of that guard is
    that a preview's `assert not monday_done` passes unconditionally, so a
    green 7/7 preview never proved /this-week was healthy. This closes it by
    having the operator state what the deploy must show instead of having the
    check guess from shared state.
    """
    def _check() -> None:
        status, body = _fetch_text(_url(base_url, "/this-week"))
        assert status == 200, f"/this-week returned HTTP {status}"

        episode = _fetch_json(f"{BLOB_CDN}/episodes/{expect_episode}.json")
        title = (
            (episode.get("stages", {}).get("monday", {}).get("recipe_data") or {})
            .get("title", "")
            .strip()
        )
        assert title, (
            f"{expect_episode} has no recipe title, so there is nothing to "
            f"assert /this-week against"
        )
        assert title in body, (
            f"/this-week does not render {expect_episode}'s recipe {title!r} "
            f"({len(body)} bytes returned)"
        )

    report.check("expected_episode_renders", _check)


def check_recipe_page_images(
    report: Report, base_url: str = PRODUCTION_BASE_URL
) -> None:
    """Every recipe page's hero image must actually load (HTTP 200).

    Checks the RENDERED pages, not the catalog: a recipe can carry a healthy
    catalog image while its pre-rendered blob page points at a stale/dead path.
    That is exactly the W10 lemon-meringue flat-vs-hierarchical bug — catalog
    image 200 but the rendered page 404s — which a catalog-only check misses.
    """
    def _check():
        catalog = _fetch_json(_url(base_url, "/recipes.json"))
        recipes = catalog if isinstance(catalog, list) else catalog.get("recipes", [])
        assert recipes, "catalog is empty — cannot verify recipe images"
        broken = []
        for r in recipes:
            slug = r.get("slug")
            if not slug:
                continue
            status, body = _fetch_text(_url(base_url, f"/recipes/{slug}"))
            if status != 200:
                broken.append(f"{slug}: page HTTP {status}")
                continue
            # Hero image = the first real image asset(s) on the page. The nav
            # uses an inline SVG, so the hero <picture>/<img> is first. Matching
            # on the URL (not a CSS class) keeps this working before and after
            # the vanilla-CSS migration.
            srcs = re.findall(r'<(?:img[^>]+src|source[^>]+srcset)="([^"]+)"', body)
            hero = [
                s for s in srcs
                if "/blob-images/" in s or "/assets/images" in s
                or "blob.vercel-storage.com" in s
            ][:2]
            for s in hero:
                url = _resolve_image_url(s, base_url)
                try:
                    code = requests.head(url, timeout=12, allow_redirects=True).status_code
                except Exception as exc:
                    code = type(exc).__name__
                if code != 200:
                    broken.append(f"{slug}: [{code}] {s}")
        assert not broken, (
            f"{len(broken)} recipe hero image(s) failed to load:\n    "
            + "\n    ".join(broken[:10])
        )

    report.check("recipe_page_hero_images_load", _check)


def _sitemap_urls(base_url: str) -> list[str]:
    """Return sitemap paths resolved against this run's base URL.

    The application currently emits the production origin in <loc> even from
    a preview deployment. Keeping only the path prevents a preview run from
    accidentally checking production pages.
    """
    status, body = _fetch_text(_url(base_url, "/sitemap.xml"))
    assert status == 200, f"/sitemap.xml returned HTTP {status}"
    try:
        root = ElementTree.fromstring(body)
    except ElementTree.ParseError as exc:
        raise AssertionError(f"/sitemap.xml is not valid XML: {exc}") from exc

    urls: list[str] = []
    seen: set[str] = set()
    for element in root.iter():
        if element.tag.rsplit("}", 1)[-1] != "loc":
            continue
        location = (element.text or "").strip()
        if not location:
            continue
        parsed = urlsplit(location)
        path = parsed.path or "/"
        if parsed.query:
            path += f"?{parsed.query}"
        target = _url(base_url, path)
        if target not in seen:
            seen.add(target)
            urls.append(target)
    assert urls, "/sitemap.xml contains no <loc> URLs"
    return urls


def _page_path(url: str) -> str:
    path = urlsplit(url).path or "/"
    return path if path == "/" else path.rstrip("/")


def _is_recipe_url(url: str) -> bool:
    path = _page_path(url)
    return path.startswith("/recipes/") and path.count("/") == 2


def _is_this_week_url(url: str) -> bool:
    return _page_path(url) == "/this-week"


def _tag_attribute(tag: str, attribute: str) -> str | None:
    match = re.search(
        rf"\b{re.escape(attribute)}\s*=\s*(?:(['\"])(.*?)\1|([^\s>]+))",
        tag,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not match:
        return None
    return (match.group(2) or match.group(3) or "").strip()


def _html_tags(body: str, tag_name: str) -> list[str]:
    return re.findall(
        rf"<{tag_name}\b[^>]*>", body, flags=re.IGNORECASE | re.DOTALL
    )


def _check_ga4_tag(body: str) -> None:
    loader = re.findall(
        rf"https://www\.googletagmanager\.com/gtag/js\?id={re.escape(GA4_MEASUREMENT_ID)}\b",
        body,
        flags=re.IGNORECASE,
    )
    config = re.findall(
        rf"gtag\s*\(\s*['\"]config['\"]\s*,\s*['\"]{re.escape(GA4_MEASUREMENT_ID)}['\"]\s*\)",
        body,
        flags=re.IGNORECASE,
    )
    assert len(loader) == 1, (
        f"GA4 loader occurs {len(loader)} times; expected exactly once"
    )
    assert len(config) == 1, (
        f"GA4 config occurs {len(config)} times; expected exactly once"
    )


def _json_ld_objects(body: str) -> list[object]:
    blocks = re.findall(
        r"<script\b[^>]*type\s*=\s*(['\"])application/ld\+json\1[^>]*>(.*?)</script\s*>",
        body,
        flags=re.IGNORECASE | re.DOTALL,
    )
    objects: list[object] = []
    for _quote, raw in blocks:
        try:
            objects.append(json.loads(raw.strip()))
        except json.JSONDecodeError as exc:
            raise AssertionError(f"invalid JSON-LD: {exc.msg}") from exc
    return objects


def _iter_json_ld_dicts(value: object) -> list[dict]:
    if isinstance(value, dict):
        values = [value]
        graph = value.get("@graph")
        if isinstance(graph, list):
            values.extend(item for item in graph if isinstance(item, dict))
        return values
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    return []


def _check_recipe_json_ld(body: str) -> None:
    objects = _json_ld_objects(body)
    recipe_objects = [
        item
        for obj in objects
        for item in _iter_json_ld_dicts(obj)
        if (
            item.get("@type") == "Recipe"
            or (
                isinstance(item.get("@type"), list)
                and "Recipe" in item["@type"]
            )
        )
    ]
    assert recipe_objects, "page has no Recipe JSON-LD object"
    recipe = recipe_objects[0]
    required = (
        "@context",
        "@type",
        "name",
        "image",
        "recipeIngredient",
        "recipeInstructions",
    )
    missing = [key for key in required if not recipe.get(key)]
    assert not missing, f"Recipe JSON-LD missing required fields: {', '.join(missing)}"
    assert isinstance(recipe["recipeIngredient"], list) and recipe["recipeIngredient"], (
        "Recipe JSON-LD recipeIngredient must be a non-empty list"
    )
    assert isinstance(recipe["recipeInstructions"], list) and recipe["recipeInstructions"], (
        "Recipe JSON-LD recipeInstructions must be a non-empty list"
    )


def _image_references(body: str) -> list[str]:
    """Extract the first picture/source and img URLs, preserving hero order."""
    references: list[str] = []
    for tag in _html_tags(body, "source") + _html_tags(body, "img"):
        value = _tag_attribute(tag, "srcset") or _tag_attribute(tag, "src")
        if not value:
            continue
        # For srcset, the first candidate is sufficient for a reachability
        # probe; the corresponding fallback <img> is also checked below.
        value = value.split(",", 1)[0].strip().split(None, 1)[0]
        if value.startswith("data:") or value in references:
            continue
        references.append(value)
    return references


def _check_hero_image(body: str, base_url: str, *, required: bool) -> None:
    references = _image_references(body)
    if not references:
        if required:
            raise AssertionError("page has no hero image URL")
        return

    broken: list[str] = []
    for source in references[:2]:
        image_url = _resolve_image_url(source, base_url)
        try:
            status = requests.head(
                image_url, timeout=12, allow_redirects=True
            ).status_code
        except Exception as exc:
            status = f"{type(exc).__name__}: {exc}"
        if status != 200:
            broken.append(f"[{status}] {source}")
    assert not broken, "hero image(s) failed to load: " + ", ".join(broken)


def _check_intrinsic_image_dimensions(body: str) -> None:
    missing: list[str] = []
    for index, tag in enumerate(_html_tags(body, "img"), start=1):
        width = _tag_attribute(tag, "width")
        height = _tag_attribute(tag, "height")
        if not width or not re.fullmatch(r"[1-9]\d*", width):
            missing.append(f"img {index} width={width!r}")
        if not height or not re.fullmatch(r"[1-9]\d*", height):
            missing.append(f"img {index} height={height!r}")
    assert not missing, "images missing intrinsic width/height: " + ", ".join(missing)


def _check_canonical_and_og(url: str, body: str, base_url: str) -> None:
    canonical = next(
        (
            _tag_attribute(tag, "href")
            for tag in _html_tags(body, "link")
            if "canonical" in (_tag_attribute(tag, "rel") or "").lower().split()
        ),
        None,
    )
    og_url = next(
        (
            _tag_attribute(tag, "content")
            for tag in _html_tags(body, "meta")
            if (_tag_attribute(tag, "property") or "").lower() == "og:url"
        ),
        None,
    )

    if _is_this_week_url(url) and not canonical and not og_url:
        # The pre-cron placeholder intentionally has no canonical. Once a
        # recipe exists, the rendered page canonicalises to that recipe URL.
        return

    assert canonical and og_url, "page must contain both canonical and og:url"
    assert canonical == og_url, "canonical and og:url must match"
    expected = _url(base_url, _page_path(url))
    if _is_this_week_url(url):
        canonical_path = _page_path(canonical)
        canonical_origin = urlsplit(canonical)
        allowed_origins = {
            urlsplit(base_url).netloc,
            urlsplit(PRODUCTION_BASE_URL).netloc,
        }
        assert (
            canonical_origin.scheme in {"http", "https"}
            and canonical_origin.netloc in allowed_origins
        ), f"/this-week canonical has unexpected origin: {canonical!r}"
        assert canonical_path.startswith("/recipes/") and canonical_path.count("/") == 2, (
            f"/this-week canonical must point to a recipe, got {canonical!r}"
        )
    else:
        accepted = {expected}
        if base_url != PRODUCTION_BASE_URL:
            # Preview artifacts intentionally retain the public production
            # canonical so search engines do not index a deployment URL.
            accepted.add(_url(PRODUCTION_BASE_URL, _page_path(url)))
        assert canonical in accepted, (
            f"canonical is {canonical!r}, expected one of {sorted(accepted)!r}"
        )


def _check_csp(headers: object) -> None:
    csp = _header(headers, "content-security-policy")
    assert csp, "response is missing Content-Security-Policy"
    directives: dict[str, list[str]] = {}
    for directive in csp.split(";"):
        parts = directive.strip().lower().split()
        if parts:
            assert parts[0] not in directives, (
                f"Content-Security-Policy repeats directive {parts[0]!r}"
            )
            directives[parts[0]] = parts[1:]
    missing = [name for name in REQUIRED_CSP_DIRECTIVES if name not in directives]
    assert not missing, (
        "Content-Security-Policy missing required directives: " + ", ".join(missing)
    )
    unexpected = sorted(set(directives) - set(REQUIRED_CSP_DIRECTIVES))
    assert not unexpected, (
        "Content-Security-Policy has unexpected directives: " + ", ".join(unexpected)
    )

    source_mismatches = []
    for name, expected in REQUIRED_CSP_DIRECTIVES.items():
        actual = directives[name]
        expected_sources = {source.lower() for source in expected}
        actual_sources = set(actual)
        missing_sources = sorted(expected_sources - actual_sources)
        extra_sources = sorted(actual_sources - expected_sources)
        duplicate_sources = sorted(
            source for source in set(actual) if actual.count(source) > 1
        )
        if missing_sources or extra_sources or duplicate_sources:
            details = []
            if missing_sources:
                details.append("missing " + " ".join(missing_sources))
            if extra_sources:
                details.append("unexpected " + " ".join(extra_sources))
            if duplicate_sources:
                details.append("duplicate " + " ".join(duplicate_sources))
            source_mismatches.append(f"{name}: {', '.join(details)}")
    assert not source_mismatches, (
        "Content-Security-Policy source policy mismatch: "
        + "; ".join(source_mismatches)
    )


def _check_security_headers(headers: object) -> None:
    failures = []
    for name, expected in REQUIRED_SECURITY_HEADER_VALUES.items():
        actual = _header(headers, name)
        if actual != expected:
            failures.append(f"{name}={actual!r}, expected {expected!r}")
    if failures:
        raise AssertionError("security headers have unexpected values: " + "; ".join(failures))
    _check_csp(headers)


def _page_checks(
    url: str, status: int, body: str, headers: object, base_url: str
) -> list[str]:
    failures: list[str] = []
    if status != 200:
        return [f"HTTP {status}"]
    checks: list[tuple[str, Callable[[], None]]] = [
        ("GA4 exactly once", lambda: _check_ga4_tag(body)),
        ("canonical/og:url", lambda: _check_canonical_and_og(url, body, base_url)),
        ("security headers", lambda: _check_security_headers(headers)),
        ("intrinsic img dimensions", lambda: _check_intrinsic_image_dimensions(body)),
    ]
    if _is_recipe_url(url):
        checks.extend(
            [
                ("Recipe JSON-LD", lambda: _check_recipe_json_ld(body)),
                (
                    "hero image HTTP 200",
                    lambda: _check_hero_image(body, base_url, required=True),
                ),
            ]
        )
    elif (
        _is_this_week_url(url)
        and "application/ld+json" in body
        and '"Recipe"' in body
    ):
        # A full /this-week page is a recipe page; its pre-cron placeholder is
        # valid without recipe data or an image.
        checks.extend(
            [
                ("Recipe JSON-LD", lambda: _check_recipe_json_ld(body)),
                (
                    "hero image HTTP 200",
                    lambda: _check_hero_image(body, base_url, required=True),
                ),
            ]
        )
    for label, check in checks:
        try:
            check()
        except AssertionError as exc:
            failures.append(f"{label}: {exc}")
        except Exception as exc:
            failures.append(f"{label}: {type(exc).__name__}: {exc}")
    return failures


def check_sitemap_pages(report: Report, base_url: str = PRODUCTION_BASE_URL) -> None:
    """Run the deploy-gating checks against every sitemap URL."""
    def _check() -> None:
        urls = _sitemap_urls(base_url)
        failures: list[str] = []
        print("Per-URL results:")
        for url in urls:
            path = _page_path(url)
            try:
                status, body, headers = _fetch_page(url)
                page_failures = _page_checks(url, status, body, headers, base_url)
            except Exception as exc:
                page_failures = [f"request: {type(exc).__name__}: {exc}"]
            if page_failures:
                result = "FAIL"
                failures.extend(f"{path}: {detail}" for detail in page_failures)
            else:
                result = "PASS"
            print(f"  {result:<4} {path} ({url})")
            for detail in page_failures:
                print(f"         - {detail}")
        assert not failures, "sitemap page checks failed:\n    " + "\n    ".join(failures)

    report.check("sitemap_pages", _check)


def check_static_security_headers(
    report: Report, base_url: str = PRODUCTION_BASE_URL
) -> None:
    """Verify headers on static routes and the lambda health route.

    Vercel owns the production CSP header, so checking ``/health`` verifies
    that the global route header also survives a lambda proxy response.
    """
    def _check() -> None:
        failures: list[str] = []
        for path, expected_status in (
            ("/", 200),
            (UNMATCHED_PATH, 404),
            ("/health", 200),
        ):
            status, _body, headers = _fetch_page(_url(base_url, path))
            if status != expected_status:
                failures.append(f"{path}: HTTP {status}, expected {expected_status}")
            try:
                _check_security_headers(headers)
            except AssertionError as exc:
                failures.append(f"{path}: {exc}")
        assert not failures, "static security headers failed:\n    " + "\n    ".join(failures)

    report.check("static_security_headers", _check)


def check_unmatched_url_404(
    report: Report, base_url: str = PRODUCTION_BASE_URL
) -> None:
    def _check() -> None:
        status, _body, _headers = _fetch_page(_url(base_url, UNMATCHED_PATH))
        assert status == 404, (
            f"{UNMATCHED_PATH} returned HTTP {status}; expected a hard 404"
        )

    report.check("unmatched_url_is_404", _check)


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def read_last_status() -> str | None:
    """Return the previous run's status ('passed'/'failed'), or None if unknown."""
    try:
        return _state_file().read_text(encoding="utf-8").strip() or None
    except FileNotFoundError:
        return None
    except Exception as e:
        print(f"(health state read failed: {e})", file=sys.stderr)
        return None


def write_status(status: str) -> None:
    try:
        path = _state_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(status, encoding="utf-8")
    except Exception as e:
        print(f"(health state write failed: {e})", file=sys.stderr)


def _post_discord(content: str) -> None:
    webhook = os.environ.get("MUFFINPAN_DISCORD_WEBHOOK")
    if not webhook:
        return
    try:
        requests.post(webhook, json={"content": content[:1900]}, timeout=10)
    except Exception as e:
        print(f"(Discord post failed: {e})", file=sys.stderr)


def post_discord_alert(report: Report) -> None:
    # Timestamp so a scrolled-back alert can't be mistaken for a live failure.
    lines = [f"🚨 **health_check.py FAILED** — {_utc_stamp()}", ""]
    for name, detail in report.failed:
        lines.append(f"• **{name}**: {detail[:300]}")
    _post_discord("\n".join(lines))


def post_discord_recovery(report: Report) -> None:
    names = ", ".join(report.passed)
    _post_discord(
        f"✅ **health_check.py RECOVERED** — {_utc_stamp()} — "
        f"all {len(report.passed)} checks passing again ({names})."
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-url",
        default=PRODUCTION_BASE_URL,
        help=f"Site origin to check (default: {PRODUCTION_BASE_URL}).",
    )
    parser.add_argument(
        "--baseline", type=int, default=15,
        help="Minimum expected catalog count. Fails if blob catalog has fewer recipes.",
    )
    parser.add_argument(
        "--no-alert",
        action="store_true",
        help="Suppress Discord alerts and persisted status writes.",
    )
    parser.add_argument(
        "--expect-episode",
        metavar="EPISODE_ID",
        help=(
            "ISO week the deploy must render, e.g. 2026-W36. Asserts that "
            "episode's integrity and that /this-week actually shows it, "
            "instead of inferring from shared prod/preview Blob state. Use "
            "this to verify a preview deploy (#6828)."
        ),
    )
    args = parser.parse_args()

    try:
        base_url = _normalize_base_url(args.base_url)
    except ValueError as exc:
        parser.error(str(exc))
    no_alert = args.no_alert or base_url != PRODUCTION_BASE_URL

    print(f"Health check against {base_url}")
    if no_alert:
        print("Alerts and persisted status writes: disabled")
    report = Report()
    check_catalog_counts_match(report, args.baseline, base_url=base_url)
    check_teaser_current_week(report, base_url=base_url)
    check_this_week_page(report, base_url=base_url)
    check_episode_integrity(
        report, base_url=base_url, expect_episode=args.expect_episode
    )
    if args.expect_episode:
        check_expected_episode_renders(report, base_url, args.expect_episode)
    check_recipe_page_images(report, base_url=base_url)
    check_sitemap_pages(report, base_url=base_url)
    check_static_security_headers(report, base_url=base_url)
    check_unmatched_url_404(report, base_url=base_url)

    print()
    print(f"Passed: {len(report.passed)}  Failed: {len(report.failed)}")

    if no_alert:
        return 1 if not report.ok else 0

    last_status = read_last_status()

    if not report.ok:
        post_discord_alert(report)
        write_status("failed")
        return 1

    # Healthy — only announce recovery when the previous run was failing,
    # so steady-state passes stay silent.
    if last_status == "failed":
        post_discord_recovery(report)
    write_status("passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
