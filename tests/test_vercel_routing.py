"""Regression tests for Vercel's public route ordering and fallback behavior."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Reader routes the weekly cron writes to Blob. These must always reach the
# FastAPI app, never a frozen static file, and must be listed BEFORE
# `{"handle": "filesystem"}` so a stale committed copy of the same path can
# never shadow them (see test_lambda_only_reader_routes_precede_filesystem_handling).
_LAMBDA_ONLY_READER_ROUTES = (
    "/this-week/?$",
    "/recipes/?$",
    "/recipes\\.json",
    "/sitemap\\.xml",
)


def _routes() -> list[dict]:
    with (ROOT / "vercel.json").open(encoding="utf-8") as config_file:
        return json.load(config_file)["routes"]


def _config() -> dict:
    with (ROOT / "vercel.json").open(encoding="utf-8") as config_file:
        return json.load(config_file)


def test_unknown_urls_use_branded_404_instead_of_homepage() -> None:
    routes = _routes()
    catch_all = routes[-1]

    assert catch_all["src"] == "/(.*)"
    assert catch_all["dest"] == "/src/404.html"
    assert catch_all["status"] == 404
    assert catch_all["dest"] != "/src/index.html"


def test_public_routes_are_present_and_ordered() -> None:
    routes = _routes()
    sources = [route.get("src") for route in routes]

    assert sources == [
        "/(.*)",
        "/(.*)",
        "/api/(.*)",
        "/admin/static/(.*)",
        "/admin/(.*)",
        "/auth/(.*)",
        "/health",
        "/this-week/?$",
        "/recipes/?$",
        "/recipes\\.json",
        "/sitemap\\.xml",
        "/recipes/([^/]+)$",
        None,  # {"handle": "filesystem"} has no "src" key
        "/recipes/([^/]+)$",
        "^/src/recipes/([^/]+)/index\\.html$",  # the rewritten path a check:true miss continues with
        "/blob-images/(.*)",
        "/assets/(.*)",
        "/robots\\.txt",
        "/about",
        "/",
        "/(.*)",
    ]

    assert routes[1]["has"] == [{"type": "host", "value": "www.muffinpanrecipes.com"}]
    assert routes[1]["status"] == 301
    assert routes[-2] == {"src": "/", "dest": "/src/index.html"}


def test_legacy_headers_run_before_existing_routes() -> None:
    routes = _routes()

    assert routes[0] == {
        "src": "/(.*)",
        "headers": {
            "X-Frame-Options": "DENY",
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "strict-origin-when-cross-origin",
            "Content-Security-Policy": "default-src 'self'; script-src 'self' 'unsafe-inline' https://www.googletagmanager.com; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com; img-src 'self' data: https://www.google-analytics.com https://*.google-analytics.com; connect-src 'self' https://www.google-analytics.com https://*.google-analytics.com https://*.analytics.google.com https://*.googletagmanager.com; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'",
        },
        "continue": True,
    }


def test_404_page_is_branded() -> None:
    page = (ROOT / "src/404.html").read_text(encoding="utf-8")

    assert "<title>Page Not Found | Muffin Pan Recipes</title>" in page
    assert "Muffin Pan Recipes" in page
    assert "Return to the recipe collection" in page
    assert page.count("https://www.googletagmanager.com/gtag/js?id=G-05P73D3237") == 1
    assert page.count("gtag('config', 'G-05P73D3237')") == 1


def test_homepage_js_reserves_intrinsic_space_for_seed_and_generated_images() -> None:
    page = (ROOT / "src/index.html").read_text(encoding="utf-8")

    assert 'width="${imageDimension(recipe, \'image_width\')}"' in page
    assert "recipe.image.startsWith('assets/images/')" in page
    assert "return isSeedAsset ? 1024 : 1536;" in page


def test_every_static_dest_points_at_a_file_that_exists() -> None:
    """The PR #88 regression: a `dest` may not name an artifact nothing builds.

    PR #88 repointed five reader routes at `src/recipes/`, `src/this-week/` and
    `src/sitemap.xml`, none of which are ever generated — `vercel.json` has a
    top-level `builds` array, and Vercel ignores `buildCommand` whenever
    `builds` is present, so the build step that was meant to create them never
    ran. A missing artifact behind an explicit `dest` returns Vercel's raw
    NOT_FOUND rather than falling through to a later route, so all five 404'd.

    The pre-existing ordering test could not catch this: it asserts `src`
    values only and never looks at `dest`.

    This still applies to the hybrid routing added for #6684: a hard `dest`
    with no `check`/fallback (e.g. `/about`) must name a committed file, same
    as before. A `check: true` rewrite (`/recipes/<slug>`) is exempt because a
    miss there falls through to the lambda by design — see
    test_recipe_slug_route_has_static_candidate_and_lambda_fallback.
    """
    missing: list[str] = []
    for route in _routes():
        if route.get("check"):
            continue  # static-first-with-fallback; a miss here is not an error
        dest = route.get("dest", "")
        if not dest.startswith("/src/"):
            continue  # lambda, external rewrite, or no dest
        if "$" in dest:
            continue  # capture-group substitution; path is not statically known
        if not (ROOT / dest.lstrip("/")).exists():
            missing.append(f"{route.get('src')} -> {dest}")

    assert not missing, (
        "vercel.json routes point at static artifacts that do not exist and "
        "are never built; these return NOT_FOUND in production: " + "; ".join(missing)
    )


def test_reader_routes_are_served_by_the_lambda() -> None:
    """The four unconditional reader routes must reach the FastAPI app.

    These serve content the weekly cron writes to Blob. Pointing them at a
    committed artifact freezes them at whatever was last committed — that is
    how `/recipes.json` came to serve 10 seed recipes while the live catalog
    had 34, and how the dynamic sitemap from PR #55 was replaced by a file
    that PR #55 had deleted. Unlike `/recipes/<slug>`, none of these four have
    a static counterpart the builder generates that we'd ever want served
    instead, so they carry no `check`/fallback pair.
    """
    routes = _routes()
    for src in _LAMBDA_ONLY_READER_ROUTES:
        matches = [route for route in routes if route.get("src") == src]
        assert len(matches) == 1, f"reader route {src!r} must appear exactly once, found {len(matches)}"
        assert matches[0].get("dest") == "backend/admin/app.py", (
            f"reader route {src!r} must be served by the lambda, got {matches[0].get('dest')!r}"
        )


def test_lambda_only_reader_routes_precede_filesystem_handling() -> None:
    """The four reader routes above must be checked before `handle: filesystem`.

    A hard `dest` with no `check` matches regardless of phase, but keeping
    them physically before the filesystem marker documents intent and
    guarantees a committed `src/recipes.json` or `src/sitemap.xml` (if one
    ever reappears, e.g. from a careless `--full-rebuild` copy) can never be
    reached first.
    """
    routes = _routes()
    handle_index = next(i for i, route in enumerate(routes) if route.get("handle") == "filesystem")
    reader_indexes = [i for i, route in enumerate(routes) if route.get("src") in _LAMBDA_ONLY_READER_ROUTES]

    assert reader_indexes, "none of the lambda-only reader routes were found"
    assert max(reader_indexes) < handle_index, (
        "a lambda-only reader route sits at or after {'handle': 'filesystem'}; "
        "move it earlier so a stale static copy can never shadow it"
    )


def test_recipe_slug_route_has_static_candidate_and_lambda_fallback() -> None:
    """`/recipes/<slug>` is served static-first with a lambda fallback (#6684).

    A published recipe with a committed page at `src/recipes/<slug>/index.html`
    is served as a true static file (no cold Lambda). A week published to Blob
    since the last deploy has no committed file yet, so a second, identical
    `src` after `{"handle": "filesystem"}` falls back to the lambda. The
    `check: true` on the first entry is what makes the fall-through legal — see
    docs/DEPLOYMENT.md for the doc citations backing this incantation.
    """
    routes = _routes()
    slug_routes = [route for route in routes if route.get("src") == "/recipes/([^/]+)$"]

    assert len(slug_routes) == 2, (
        f"expected exactly 2 routes for /recipes/([^/]+)$ (static candidate + "
        f"lambda fallback), found {len(slug_routes)}"
    )

    static_candidate, lambda_fallback = slug_routes
    assert static_candidate["dest"] == "/src/recipes/$1/index.html"
    assert static_candidate.get("check") is True
    assert lambda_fallback["dest"] == "backend/admin/app.py"
    assert "check" not in lambda_fallback

    handle_index = next(i for i, route in enumerate(routes) if route.get("handle") == "filesystem")
    static_index = routes.index(static_candidate)
    lambda_index = len(routes) - 1 - routes[::-1].index(lambda_fallback)
    assert static_index < handle_index < lambda_index, (
        "the static-first candidate must precede {'handle': 'filesystem'}, and "
        "the lambda fallback must follow it, or a miss can't fall through"
    )

    # Observed on the 2026-09-05 preview: after a `check: true` miss Vercel keeps
    # routing against the REWRITTEN path (/src/recipes/<slug>/index.html), so a
    # fallback keyed on the original path never matched and a week published
    # to Blob since the last deploy would have hit the static 404. The fallback
    # must match the rewritten path too, and sit before the catch-all.
    rewritten_fallback = next(
        (r for r in routes if r.get("src") == "^/src/recipes/([^/]+)/index\\.html$"), None
    )
    assert rewritten_fallback is not None, "missing the rewritten-path lambda fallback"
    assert rewritten_fallback["dest"] == "backend/admin/app.py"
    catch_all_index = next(i for i, r in enumerate(routes) if r.get("src") == "/(.*)" and r.get("status") == 404)
    assert handle_index < routes.index(rewritten_fallback) < catch_all_index


def test_about_route_exists() -> None:
    """`/about` (card #6667) serves the builder-generated static page directly.

    No lambda fallback: there is no dynamic backend equivalent for the about
    page, so unlike `/recipes/<slug>` this is a plain hard `dest`.
    """
    routes = _routes()
    about = next((route for route in routes if route.get("src") == "/about"), None)

    assert about is not None, "/about route is missing from vercel.json"
    assert about["dest"] == "/src/about.html"
    assert "check" not in about


def test_builds_array_is_unchanged() -> None:
    """#6684's routing change must not touch `builds` — see PR #90's root cause.

    Vercel ignores `buildCommand` whenever a top-level `builds` array is
    present. That is exactly why PR #88's static artifacts never got built.
    The fix here is routing (static-first with a lambda fallback for content
    that isn't committed yet), not a build-system migration — `builds` stays
    exactly as-is: the Python lambda plus `@vercel/static` for `src/**` and
    admin static.
    """
    config = _config()

    assert config["builds"] == [
        {
            "src": "backend/admin/app.py",
            "use": "@vercel/python",
            "config": {
                "maxLambdaSize": "50mb",
                "maxDuration": 300,
            },
        },
        {
            "src": "src/**",
            "use": "@vercel/static",
        },
        {
            "src": "backend/admin/static/**",
            "use": "@vercel/static",
        },
    ]
    assert "buildCommand" not in config
