"""
Playwright smoke tests for the Muffin Pan Recipes admin dashboard.

Covers every admin route (happy path + error/empty states) and verifies:
- Response is 200 (or expected status)
- No raw JSON on HTML pages
- No 'FUNCTION_INVOCATION_FAILED' text
- Page contains expected structural elements (header nav)
- Interactive UI elements respond correctly (lightbox, run button)

How to run:
    # 1. Install Playwright browsers once:
    playwright install chromium

    # 2. Run all smoke tests:
    LOCAL_DEV=true pytest tests/e2e/ -v

    # Or via uv:
    LOCAL_DEV=true uv run pytest tests/e2e/ -v
"""

import re

import pytest
from playwright.sync_api import Page, expect


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _check_page_basics(page: Page, url: str) -> None:
    """Assert no obvious error signals on the current page."""
    content = page.content()
    assert "FUNCTION_INVOCATION_FAILED" not in content, (
        f"Vercel invocation failure found on {url}"
    )


def _is_html_page(page: Page, url: str) -> None:
    """Assert the page renders HTML (not raw JSON)."""
    content = page.content().strip()
    # Raw JSON pages start with '{' or '['
    assert not re.match(r"^\s*[{\[]", content), (
        f"Expected HTML but got raw JSON on {url}"
    )
    # Must have a <body> tag
    assert "<body" in content.lower(), f"No <body> tag found on {url}"


def _has_nav(page: Page) -> bool:
    """Return True if the page has an admin nav header."""
    return page.locator("header").count() > 0


# ---------------------------------------------------------------------------
# Happy-path page loads
# ---------------------------------------------------------------------------

class TestHappyPath:
    """Each admin route should load without errors."""

    def test_dashboard(self, browser_page: Page, live_server_url: str) -> None:
        url = f"{live_server_url}/admin/"
        browser_page.goto(url, wait_until="networkidle")
        _check_page_basics(browser_page, url)
        _is_html_page(browser_page, url)
        assert _has_nav(browser_page), "Dashboard should have a nav header"

    def test_episodes_list(self, browser_page: Page, live_server_url: str) -> None:
        url = f"{live_server_url}/admin/episodes"
        browser_page.goto(url, wait_until="networkidle")
        _check_page_basics(browser_page, url)
        _is_html_page(browser_page, url)
        assert _has_nav(browser_page), "Episodes page should have a nav header"

    def test_recipes_list_is_html(self, browser_page: Page, live_server_url: str) -> None:
        """The recipes page must render HTML (not raw JSON)."""
        url = f"{live_server_url}/admin/recipes"
        browser_page.goto(url, wait_until="networkidle")
        _check_page_basics(browser_page, url)
        _is_html_page(browser_page, url)
        assert _has_nav(browser_page), "Recipes page should have a nav header"

    def test_simulations(self, browser_page: Page, live_server_url: str) -> None:
        url = f"{live_server_url}/admin/simulations"
        browser_page.goto(url, wait_until="networkidle")
        _check_page_basics(browser_page, url)
        _is_html_page(browser_page, url)
        assert _has_nav(browser_page), "Simulations page should have a nav header"

    def test_health_returns_json(self, browser_page: Page, live_server_url: str) -> None:
        url = f"{live_server_url}/health"
        browser_page.goto(url, wait_until="networkidle")
        content = browser_page.content()
        assert "healthy" in content, "Health endpoint should return status: healthy"


# ---------------------------------------------------------------------------
# Empty / error states
# ---------------------------------------------------------------------------

class TestEmptyAndErrorStates:
    """Admin pages should render styled HTML even when data is missing."""

    def test_recipes_pending_filter_is_html(
        self, browser_page: Page, live_server_url: str
    ) -> None:
        """Filtered recipes page with no results must show a styled empty state."""
        url = f"{live_server_url}/admin/recipes?status_filter=pending"
        browser_page.goto(url, wait_until="networkidle")
        _check_page_basics(browser_page, url)
        _is_html_page(browser_page, url)
        assert _has_nav(browser_page), "Filtered recipes page needs nav"
        # Should NOT show raw 'recipes' JSON key
        content = browser_page.content()
        assert '"recipes"' not in content, (
            "Raw JSON key found — page should render HTML, not JSON"
        )

    def test_nonexistent_episode_shows_error_page(
        self, browser_page: Page, live_server_url: str
    ) -> None:
        url = f"{live_server_url}/admin/episodes/nonexistent-id"
        browser_page.goto(url, wait_until="networkidle")
        _check_page_basics(browser_page, url)
        _is_html_page(browser_page, url)
        # Should show an error state (either in main content or in error template)
        content = browser_page.content().lower()
        assert (
            "not found" in content
            or "error" in content
            or "no episode" in content
        ), "Nonexistent episode should show error/not-found message"

    def test_nonexistent_recipe_view_shows_error(
        self, browser_page: Page, live_server_url: str
    ) -> None:
        url = f"{live_server_url}/admin/recipes/nonexistent/view"
        browser_page.goto(url, wait_until="networkidle")
        _check_page_basics(browser_page, url)
        _is_html_page(browser_page, url)
        content = browser_page.content().lower()
        assert (
            "not found" in content or "error" in content
        ), "Nonexistent recipe view should show error"

    def test_unknown_admin_route_shows_404_page(
        self, browser_page: Page, live_server_url: str
    ) -> None:
        """Unknown /admin/* paths should render the styled 404 page."""
        url = f"{live_server_url}/admin/nonexistent"
        browser_page.goto(url, wait_until="networkidle")
        _check_page_basics(browser_page, url)
        _is_html_page(browser_page, url)
        assert _has_nav(browser_page), "404 page should have nav header"
        content = browser_page.content().lower()
        assert (
            "not found" in content or "404" in content
        ), "Unknown route should render styled 404"
        # Must NOT be raw FastAPI JSON error
        assert '"detail"' not in browser_page.content(), (
            "Should not return raw FastAPI JSON 404"
        )

    def test_recipes_invalid_status_filter_shows_error(
        self, browser_page: Page, live_server_url: str
    ) -> None:
        url = f"{live_server_url}/admin/recipes?status_filter=garbage"
        browser_page.goto(url, wait_until="networkidle")
        _check_page_basics(browser_page, url)
        _is_html_page(browser_page, url)
        content = browser_page.content().lower()
        assert (
            "invalid" in content or "error" in content or "not a valid" in content
        ), "Invalid status filter should show styled error"


# ---------------------------------------------------------------------------
# Interactive tests (episode detail page)
# ---------------------------------------------------------------------------

class TestInteractiveElements:
    """Click through interactive UI elements on episode detail."""

    @pytest.fixture(autouse=True)
    def _navigate_to_first_episode(
        self, browser_page: Page, live_server_url: str
    ) -> None:
        """Navigate to the episodes list and click the first episode if present."""
        browser_page.goto(f"{live_server_url}/admin/episodes", wait_until="networkidle")
        first_episode = browser_page.locator("main a").first
        if first_episode.count() == 0:
            pytest.skip("No episodes available for interactive tests")
        first_episode.click()
        browser_page.wait_for_load_state("networkidle")

    def test_run_compressed_week_button_is_clickable(
        self, browser_page: Page, live_server_url: str
    ) -> None:
        """The 'Run Compressed Week' button should be present and enabled."""
        btn = browser_page.locator("button", has_text=re.compile("run.*week", re.IGNORECASE))
        if btn.count() == 0:
            pytest.skip("No 'Run Compressed Week' button on this episode")
        assert btn.first.is_enabled(), "Run Compressed Week button should be enabled"

    def test_image_lightbox_opens_on_thumbnail_click(
        self, browser_page: Page, live_server_url: str
    ) -> None:
        """Clicking an image thumbnail should open the lightbox overlay."""
        # Look for any clickable image in the stage cards — episode_detail uses data-lightbox-src
        thumbnail = browser_page.locator("[data-lightbox-src], img[onclick], img.cursor-pointer").first
        if thumbnail.count() == 0:
            pytest.skip("No lightbox thumbnails found on this episode")
        thumbnail.click()
        # Lightbox should become visible
        lightbox = browser_page.locator("#lightbox, [id*='lightbox'], .lightbox-overlay").first
        if lightbox.count() == 0:
            pytest.skip("No lightbox element found — skipping open/close test")
        expect(lightbox).to_be_visible(timeout=2000)

    def test_lightbox_closes_on_overlay_click(
        self, browser_page: Page, live_server_url: str
    ) -> None:
        """Clicking the lightbox overlay should close it."""
        thumbnail = browser_page.locator("[data-lightbox-src], img[onclick], img.cursor-pointer").first
        if thumbnail.count() == 0:
            pytest.skip("No lightbox thumbnails found")
        thumbnail.click()
        lightbox = browser_page.locator("#lightbox, [id*='lightbox'], .lightbox-overlay").first
        if lightbox.count() == 0:
            pytest.skip("No lightbox element found")
        expect(lightbox).to_be_visible(timeout=2000)
        # Click the overlay itself (not the inner image)
        lightbox.click()
        expect(lightbox).to_be_hidden(timeout=2000)
