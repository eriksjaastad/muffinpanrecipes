"""Safety and determinism tests for the static reader build."""

from __future__ import annotations

import json
from contextlib import contextmanager

import pytest

from backend.publishing.site_builder import (
    ExistingPageMutationError,
    SiteBuildError,
    StaticSiteBuilder,
)


def _episode(episode_id: str = "2026-W34") -> dict:
    return {
        "episode_id": episode_id,
        "published_at": "2026-08-30T12:00:00+00:00",
        "stages": {
            "monday": {
                "status": "complete",
                "recipe_data": {
                    "title": "Kimchi Cheddar Rice Cups",
                    "description": "Crisp rice cups with kimchi and cheddar.",
                    "category": "savory",
                    "ingredients": [{"amount": "1 cup", "item": "rice"}],
                    "instructions": ["Mix the filling.", "Bake until crisp."],
                },
            },
            "sunday": {"status": "complete"},
        },
    }


class FakeStorage:
    def __init__(self, episode: dict | None = None, catalog: list[dict] | None = None):
        self.prefix = ""
        self.episode = episode or _episode()
        self.catalog = catalog or [{"slug": "existing-cup", "title": "Existing Cup"}]
        self.saved_pages: list[str] = []
        self.scope_values: list[str] = []

    def _has_cloud(self):
        return True

    @contextmanager
    def prefix_scope(self, prefix: str):
        previous = self.prefix
        self.prefix = prefix
        self.scope_values.append(prefix)
        try:
            yield
        finally:
            self.prefix = previous

    def load_page(self, pathname: str):
        if pathname == "pages/recipes.json":
            return json.dumps({"recipes": self.catalog})
        return None

    def load_episode(self, episode_id: str):
        return self.episode if episode_id == self.episode["episode_id"] else None

    def load_episode_strict(self, episode_id: str):
        return self.load_episode(episode_id)

    def list_episodes(self):
        return [self.episode]

    def list_episodes_strict(self):
        return self.list_episodes()

    def save_page(self, pathname: str, content: str):
        self.saved_pages.append(pathname)
        raise AssertionError("static builds must not write reader pages to storage")


def _write_local_sources(project_root):
    source_dir = project_root / "src"
    source_dir.mkdir()
    (source_dir / "recipes.json").write_text(
        json.dumps({"recipes": [{"slug": "local-cup", "title": "Local Cup"}]}),
        encoding="utf-8",
    )
    (source_dir / "seed_recipes.json").write_text(
        json.dumps(
            {
                "seed-cup": {
                    "image": "",
                    "recipe_data": {
                        "title": "Seed Cup",
                        "description": "A seed recipe.",
                        "category": "savory",
                        "ingredients": ["1 cup rice"],
                        "instructions": ["Bake."],
                    },
                }
            }
        ),
        encoding="utf-8",
    )


def test_incremental_build_refuses_existing_mutations_before_any_write(tmp_path):
    _write_local_sources(tmp_path)
    storage = FakeStorage()
    output = tmp_path / "site"
    existing = output / "recipes" / "kimchi-cheddar-rice-cups" / "index.html"
    existing.parent.mkdir(parents=True)
    existing.write_text("old page", encoding="utf-8")

    builder = StaticSiteBuilder(
        project_root=tmp_path,
        output_dir=output,
        storage_client=storage,
    )
    with pytest.raises(ExistingPageMutationError) as error:
        builder.build(["2026-W34"])

    assert "recipes/kimchi-cheddar-rice-cups/index.html" in error.value.paths
    assert existing.read_text(encoding="utf-8") == "old page"
    assert not (output / "recipes.json").exists()
    assert storage.saved_pages == []
    assert storage.prefix == ""


def test_deployment_build_requires_cloud_recipe_sources(tmp_path):
    _write_local_sources(tmp_path)
    storage = FakeStorage()
    storage._has_cloud = lambda: False

    builder = StaticSiteBuilder(
        project_root=tmp_path,
        output_dir=tmp_path / "site",
        storage_client=storage,
        require_cloud=True,
    )

    with pytest.raises(SiteBuildError, match="BLOB_READ_WRITE_TOKEN"):
        builder.build(["2026-W34"])


def test_full_rebuild_writes_local_artifacts_and_never_blob_pages(tmp_path):
    _write_local_sources(tmp_path)
    storage = FakeStorage()
    output = tmp_path / "preview"
    builder = StaticSiteBuilder(
        project_root=tmp_path,
        output_dir=output,
        full_rebuild=True,
        storage_client=storage,
        storage_prefix="preview/",
    )

    result = builder.build(["2026-W34"])

    assert result.full_rebuild is True
    assert "recipes.json" in result.written
    assert "recipes/kimchi-cheddar-rice-cups/index.html" in result.written
    assert (output / "recipes.json").is_file()
    assert (output / "recipes" / "kimchi-cheddar-rice-cups" / "index.html").is_file()
    assert storage.saved_pages == []
    assert storage.scope_values == ["preview/"]
    assert storage.prefix == ""


def test_second_incremental_build_is_byte_stable(tmp_path):
    _write_local_sources(tmp_path)
    storage = FakeStorage()
    output = tmp_path / "site"
    builder = StaticSiteBuilder(
        project_root=tmp_path,
        output_dir=output,
        storage_client=storage,
    )

    first = builder.build(["2026-W34"])
    second = builder.build(["2026-W34"])

    assert first.written
    assert second.written == []
    assert set(second.unchanged) == set(first.written)


def test_missing_seed_source_fails_before_writing(tmp_path):
    source_dir = tmp_path / "src"
    source_dir.mkdir()
    (source_dir / "recipes.json").write_text(
        json.dumps({"recipes": [{"slug": "local-cup"}]}), encoding="utf-8"
    )

    with pytest.raises(SiteBuildError, match="seed recipe source is missing"):
        StaticSiteBuilder(
            project_root=tmp_path,
            output_dir=tmp_path / "site",
            storage_client=FakeStorage(),
        ).build(["2026-W34"])
    assert not (tmp_path / "site" / "recipes.json").exists()

def test_cloud_catalog_failure_does_not_fall_back_to_local_catalog(tmp_path):
    _write_local_sources(tmp_path)

    class BrokenCloudStorage(FakeStorage):
        def load_page(self, pathname):
            raise OSError("cloud unavailable")

    with pytest.raises(SiteBuildError, match="cloud recipe catalog"):
        StaticSiteBuilder(
            project_root=tmp_path,
            output_dir=tmp_path / "site",
            storage_client=BrokenCloudStorage(),
        ).build(["2026-W34"])
    assert not (tmp_path / "site" / "recipes.json").exists()


def test_no_cloud_uses_validated_local_sources(tmp_path):
    _write_local_sources(tmp_path)

    class LocalStorage(FakeStorage):
        def _has_cloud(self):
            return False

        def load_page(self, pathname):
            raise AssertionError("no-cloud builds use local sources directly")

    result = StaticSiteBuilder(
        project_root=tmp_path,
        output_dir=tmp_path / "site",
        full_rebuild=True,
        storage_client=LocalStorage(),
    ).build(["2026-W34"])
    assert "recipes.json" in result.written


def test_storage_without_cloud_probe_uses_local_sources(tmp_path):
    _write_local_sources(tmp_path)

    class LocalStorageWithoutProbe(FakeStorage):
        _has_cloud = None

        def load_page(self, pathname):
            raise AssertionError("no-cloud builds use local sources directly")

    result = StaticSiteBuilder(
        project_root=tmp_path,
        output_dir=tmp_path / "site",
        full_rebuild=True,
        storage_client=LocalStorageWithoutProbe(),
    ).build(["2026-W34"])
    assert "recipes.json" in result.written
