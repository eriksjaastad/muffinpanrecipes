"""/admin/recipes lists the Published catalog from cloud storage (#8604).

The catalog is ``pages/recipes.json`` read through the storage singleton. A fake
store stands in for it, so nothing reads Blob or the network. The legacy
``data/recipes`` tree is populated with a recipe that must not appear: the page
does not read the filesystem at all.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.templating import Jinja2Templates
from fastapi.testclient import TestClient

import backend.storage
from backend.admin.routes import create_routes
from backend.auth.middleware import require_auth
from backend.data.recipe import Recipe
from backend.storage import PageReadError

TEMPLATES = Path(__file__).resolve().parents[1] / "backend" / "admin" / "templates"


class FakeStore:
    """Serves one catalog body (or raises) for ``pages/recipes.json``."""

    def __init__(self, catalog=None, *, error: Exception | None = None) -> None:
        self._catalog = catalog
        self._error = error

    @staticmethod
    def prefix_scope(prefix: str):
        from contextlib import nullcontext

        return nullcontext()

    def load_page(self, pathname: str):
        assert pathname == "pages/recipes.json"
        if self._error is not None:
            raise self._error
        return self._catalog


def _get(store: FakeStore, project_root: Path):
    app = FastAPI()
    app.state.templates = Jinja2Templates(directory=str(TEMPLATES))
    app.state.project_root = project_root
    create_routes(app)
    app.dependency_overrides[require_auth] = lambda: {"email": "admin@example.com"}
    with patch.object(backend.storage, "storage", store):
        return TestClient(app).get("/admin/recipes")


def _catalog(*recipes: dict) -> str:
    return json.dumps({"recipes": list(recipes)})


def test_blob_catalog_renders_newest_first_with_public_links(tmp_path: Path) -> None:
    # Filesystem-only content: a legacy pending recipe under data/recipes. It
    # must not appear, because the page reads only the catalog from storage.
    legacy = Recipe(
        recipe_id="legacy-fs",
        slug="legacy-filesystem-muffins",
        title="Legacy Filesystem Muffins",
        concept="legacy",
        description="old",
        ingredients=[],
        instructions=[],
        prep_time_minutes=5,
        cook_time_minutes=10,
    )
    (tmp_path / "data" / "recipes" / "pending").mkdir(parents=True)
    (tmp_path / "data" / "recipes" / "pending" / "legacy-fs.json").write_text(legacy.model_dump_json())

    store = FakeStore(
        _catalog(
            {"slug": "newest-muffins", "title": "Newest Muffins", "category": "Sweet", "episode_id": "2026-W45"},
            {"slug": "older-muffins", "title": "Older Muffins", "category": "Savory", "episode_id": "2026-W44"},
            {"slug": "../bad", "title": "Unsafe Slug"},
            {"slug": "no-title"},
        )
    )
    response = _get(store, tmp_path)

    assert response.status_code == 200
    body = response.text
    assert "Legacy Filesystem Muffins" not in body
    assert "Unsafe Slug" not in body
    assert "Published catalog" in body
    assert body.index("Newest Muffins") < body.index("Older Muffins")
    assert 'href="/recipes/newest-muffins"' in body
    assert 'href="/recipes/older-muffins"' in body
    assert "/admin/recipes/" not in body
    assert "Pending" not in body and "Approved" not in body
    assert "status_filter" not in body


@pytest.mark.parametrize(
    "store",
    [
        FakeStore(error=PageReadError("Blob content for 'pages/recipes.json' returned HTTP 500")),
        FakeStore(None),  # absent catalog
        FakeStore("{not json"),
        FakeStore(json.dumps(["not", "a", "dict"])),
        FakeStore(json.dumps({"recipes": "not-a-list"})),
    ],
    ids=["read-failed", "absent", "invalid-json", "top-level-list", "recipes-not-list"],
)
def test_unreadable_or_malformed_catalog_is_503_not_empty_page(store: FakeStore, tmp_path: Path) -> None:
    response = _get(store, tmp_path)

    assert response.status_code == 503
    assert "No published recipes yet" not in response.text


def test_valid_empty_catalog_renders_empty_state(tmp_path: Path) -> None:
    response = _get(FakeStore(_catalog()), tmp_path)

    assert response.status_code == 200
    assert "No published recipes yet" in response.text
