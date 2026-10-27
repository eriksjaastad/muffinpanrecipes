"""
Playwright smoke tests for the Muffin Pan Recipes admin dashboard.

Covers every admin route (happy path + error/empty states) and verifies:
- Response is 200 (or expected status)
- No raw JSON on HTML pages
- No 'FUNCTION_INVOCATION_FAILED' text
- Page contains expected structural elements (header nav)
- Interactive UI elements respond correctly (lightbox, run button)

How to run: ``scripts/test_browser.sh``
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

    def test_dashboard(self, page: Page, live_server_url: str) -> None:
        url = f"{live_server_url}/admin/"
        page.goto(url, wait_until="networkidle")
        _check_page_basics(page, url)
        _is_html_page(page, url)
        assert _has_nav(page), "Dashboard should have a nav header"

    def test_episodes_list(self, page: Page, live_server_url: str) -> None:
        url = f"{live_server_url}/admin/episodes"
        page.goto(url, wait_until="networkidle")
        _check_page_basics(page, url)
        _is_html_page(page, url)
        assert _has_nav(page), "Episodes page should have a nav header"
        expect(page.get_by_text("Deterministic Browser Fixture")).to_be_visible()

    def test_recipes_list_is_html(self, page: Page, live_server_url: str) -> None:
        """The recipes page must render HTML (not raw JSON)."""
        url = f"{live_server_url}/admin/recipes"
        page.goto(url, wait_until="networkidle")
        _check_page_basics(page, url)
        _is_html_page(page, url)
        assert _has_nav(page), "Recipes page should have a nav header"

    def test_simulations(self, page: Page, live_server_url: str) -> None:
        url = f"{live_server_url}/admin/simulations"
        page.goto(url, wait_until="networkidle")
        _check_page_basics(page, url)
        _is_html_page(page, url)
        assert _has_nav(page), "Simulations page should have a nav header"

    def test_health_returns_json(self, page: Page, live_server_url: str) -> None:
        url = f"{live_server_url}/health"
        page.goto(url, wait_until="networkidle")
        content = page.content()
        assert "healthy" in content, "Health endpoint should return status: healthy"


# ---------------------------------------------------------------------------
# Empty / error states
# ---------------------------------------------------------------------------

class TestEmptyAndErrorStates:
    """Admin pages should render styled HTML even when data is missing."""

    def test_recipes_pending_filter_is_html(
        self, page: Page, live_server_url: str
    ) -> None:
        """Filtered recipes page with no results must show a styled empty state."""
        url = f"{live_server_url}/admin/recipes?status_filter=pending"
        page.goto(url, wait_until="networkidle")
        _check_page_basics(page, url)
        _is_html_page(page, url)
        assert _has_nav(page), "Filtered recipes page needs nav"
        # Should NOT show raw 'recipes' JSON key
        content = page.content()
        assert '"recipes"' not in content, (
            "Raw JSON key found — page should render HTML, not JSON"
        )

    def test_nonexistent_episode_shows_error_page(
        self, page: Page, live_server_url: str
    ) -> None:
        url = f"{live_server_url}/admin/episodes/nonexistent-id"
        page.goto(url, wait_until="networkidle")
        _check_page_basics(page, url)
        _is_html_page(page, url)
        # Should show an error state (either in main content or in error template)
        content = page.content().lower()
        assert (
            "not found" in content
            or "error" in content
            or "no episode" in content
        ), "Nonexistent episode should show error/not-found message"

    def test_nonexistent_recipe_view_shows_error(
        self, page: Page, live_server_url: str
    ) -> None:
        url = f"{live_server_url}/admin/recipes/nonexistent/view"
        response = page.goto(url, wait_until="networkidle")
        assert response is not None and response.status == 404
        _check_page_basics(page, url)
        _is_html_page(page, url)
        expect(page.get_by_role("alert")).to_contain_text("not found")
        expect(page.get_by_role("button", name="Approve")).to_have_count(0)

    def test_unknown_admin_route_shows_404_page(
        self, page: Page, live_server_url: str
    ) -> None:
        """Unknown /admin/* paths should render the styled 404 page."""
        url = f"{live_server_url}/admin/nonexistent"
        page.goto(url, wait_until="networkidle")
        _check_page_basics(page, url)
        _is_html_page(page, url)
        assert _has_nav(page), "404 page should have nav header"
        content = page.content().lower()
        assert (
            "not found" in content or "404" in content
        ), "Unknown route should render styled 404"
        # Must NOT be raw FastAPI JSON error
        assert '"detail"' not in page.content(), (
            "Should not return raw FastAPI JSON 404"
        )

    def test_recipes_invalid_status_filter_shows_error(
        self, page: Page, live_server_url: str
    ) -> None:
        url = f"{live_server_url}/admin/recipes?status_filter=garbage"
        page.goto(url, wait_until="networkidle")
        _check_page_basics(page, url)
        _is_html_page(page, url)
        content = page.content().lower()
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
        self, page: Page, live_server_url: str
    ) -> None:
        """Open the deterministic local fixture episode."""
        response = page.goto(
            f"{live_server_url}/admin/episodes/2026-W01", wait_until="networkidle"
        )
        assert response is not None and response.status == 200

    def test_run_compressed_week_button_is_clickable(
        self, page: Page, live_server_url: str
    ) -> None:
        """The 'Run Compressed Week' button should be present and enabled."""
        btn = page.locator("button", has_text=re.compile("run.*week", re.IGNORECASE))
        expect(btn).to_have_count(1)
        expect(btn).to_be_enabled()

    def test_image_lightbox_opens_on_thumbnail_click(
        self, page: Page, live_server_url: str
    ) -> None:
        """Clicking an image thumbnail should open the lightbox overlay."""
        thumbnail = page.locator("[data-lightbox-src], img[onclick], img.cursor-pointer").first
        expect(thumbnail).to_be_visible()
        thumbnail.click()
        lightbox = page.locator("#lightbox, [id*='lightbox'], .lightbox-overlay").first
        expect(lightbox).to_be_visible(timeout=2000)

    def test_lightbox_closes_on_overlay_click(
        self, page: Page, live_server_url: str
    ) -> None:
        """Clicking the lightbox overlay should close it."""
        thumbnail = page.locator("[data-lightbox-src], img[onclick], img.cursor-pointer").first
        expect(thumbnail).to_be_visible()
        thumbnail.click()
        lightbox = page.locator("#lightbox, [id*='lightbox'], .lightbox-overlay").first
        expect(lightbox).to_be_visible(timeout=2000)
        # Click the overlay itself (not the inner image)
        lightbox.click()
        expect(lightbox).to_be_hidden(timeout=2000)
