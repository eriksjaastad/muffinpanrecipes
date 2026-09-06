"""Build the public reader as static deployment artifacts.

The builder has two deliberately different modes:

* incremental (the default) writes only missing or byte-identical pages and
  fails before writing if an existing page would change;
* full rebuild (explicit ``full_rebuild=True``) may replace existing output,
  which is the only supported way to roll out a renderer/design change.

The output is an ordinary directory (normally ``src/``), not a Blob prefix.
That makes preview output safe by construction: a preview cannot overwrite
production reader pages. ``storage.prefix_scope`` still scopes source reads so
the same builder can inspect an isolated test/preview dataset without leaking
the prefix into a warm process.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from backend.publishing.episode_renderer import (
    _catalog_duplicate_reason,
    _clean_title,
    _image_dimensions,
    _slugify,
    _to_local_image_url,
    _to_webp_url,
    render_episode_page,
    render_seed_recipe_page,
)
from backend.publishing.static_renderer import (
    render_home,
    render_placeholder_page,
    render_recipes_index,
    render_sitemap,
)
from backend.storage import storage
from backend.utils.atomic import atomic_write
from backend.utils.logging import get_logger

logger = get_logger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "src"
_CANONICAL_EPISODE_ID = re.compile(r"\d{4}-W\d{2}")


class SiteBuildError(RuntimeError):
    """Base class for safe-build failures."""


class ExistingPageMutationError(SiteBuildError):
    """Raised when an incremental build would change an existing artifact."""

    def __init__(self, paths: Iterable[str]):
        self.paths = tuple(paths)
        joined = ", ".join(self.paths)
        super().__init__(
            "Incremental build would mutate existing page(s): "
            f"{joined}. Re-run with --full-rebuild to approve this change."
        )


@dataclass
class BuildResult:
    """Summary of one static build."""

    output_dir: Path
    full_rebuild: bool
    written: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)

    @property
    def changed(self) -> int:
        return len(self.written)


def _read_json(path: Path, source_name: str) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SiteBuildError(f"Required {source_name} is missing: {path}") from exc
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SiteBuildError(f"Required {source_name} is unreadable or corrupt: {path}") from exc


def _recipe_data_to_catalog_entry(slug: str, recipe_data: dict, image: str) -> dict:
    """Convert seed data to the compact catalog shape used by the homepage."""
    image_url = image.removeprefix("/")
    width, height = _image_dimensions("/" + image_url if image_url else "")
    return {
        "slug": slug,
        "title": recipe_data.get("title", ""),
        "category": recipe_data.get("category", "").title(),
        "cuisine": recipe_data.get("cuisine", ""),
        "image": image_url,
        "image_width": width,
        "image_height": height,
        "description": recipe_data.get("description", ""),
        "prep": f"{recipe_data.get('prep_time', 15)} mins",
        "cook": f"{recipe_data.get('cook_time', 20)} mins",
        "yield": f"{recipe_data.get('servings', 12)} servings",
        "ingredients": [str(item) for item in recipe_data.get("ingredients", [])],
        "instructions": [str(item) for item in recipe_data.get("instructions", [])],
    }


def _episode_slug(episode: dict) -> str:
    recipe = episode.get("stages", {}).get("monday", {}).get("recipe_data", {})
    title = _clean_title(str(recipe.get("title") or episode.get("concept") or ""))
    return _slugify(title)


def _episode_is_published(episode: dict) -> bool:
    sunday = episode.get("stages", {}).get("sunday", {})
    return bool(episode.get("published_at") or sunday.get("status") == "complete")


def _current_episode_id() -> str:
    now = datetime.now(UTC).isocalendar()
    return f"{now.year}-W{now.week:02d}"


def _safe_episode_suffix(episode_id: str) -> str:
    """Make an episode identifier safe to append to a recipe slug."""
    return re.sub(r"[^a-z0-9]+", "-", episode_id.lower()).strip("-")


class StaticSiteBuilder:
    """Render public pages into a local, reviewable deployment artifact."""

    def __init__(
        self,
        *,
        project_root: Path | str = PROJECT_ROOT,
        output_dir: Path | str | None = None,
        full_rebuild: bool = False,
        storage_client=storage,
        storage_prefix: str = "",
        require_cloud: bool = False,
    ) -> None:
        self.project_root = Path(project_root).resolve()
        raw_output = Path(output_dir) if output_dir is not None else self.project_root / "src"
        self.output_dir = (self.project_root / raw_output).resolve() if not raw_output.is_absolute() else raw_output.resolve()
        if not self.output_dir.is_relative_to(self.project_root):
            raise ValueError("Static output must stay inside the project root")
        self.full_rebuild = full_rebuild
        self.storage = storage_client
        self.storage_prefix = storage_prefix
        self.require_cloud = require_cloud

    def _storage_has_cloud(self) -> bool:
        """Return whether storage is configured for authoritative cloud reads."""
        checker = getattr(self.storage, "_has_cloud", None)
        if not callable(checker):
            # Filesystem and alternate local storage implementations do not
            # expose the private cloud capability probe. Treat them as local
            # so the checked-in catalog remains an intentional fallback.
            return False
        try:
            return bool(checker())
        except Exception as exc:
            raise SiteBuildError("Unable to determine whether cloud storage is available") from exc

    @staticmethod
    def _catalog_from_data(data: object, source_name: str) -> dict:
        if isinstance(data, list):
            recipes = data
            catalog = {"recipes": recipes}
        elif isinstance(data, dict) and isinstance(data.get("recipes"), list):
            recipes = data["recipes"]
            catalog = data
        else:
            raise SiteBuildError(f"Required {source_name} has an invalid catalog shape")

        if not recipes:
            raise SiteBuildError(f"Required {source_name} is empty")
        invalid = [
            str(index)
            for index, recipe in enumerate(recipes)
            if (
                not isinstance(recipe, dict)
                or not str(recipe.get("slug") or "").strip()
                or not str(recipe.get("title") or "").strip()
            )
        ]
        if invalid:
            raise SiteBuildError(
                f"Required {source_name} contains invalid recipe entries: {', '.join(invalid[:5])}"
            )
        return catalog

    def _load_catalog(self) -> dict:
        if self._storage_has_cloud():
            try:
                raw = self.storage.load_page("pages/recipes.json")
            except Exception as exc:
                raise SiteBuildError("Unable to load the cloud recipe catalog") from exc
            if raw is None or raw == "":
                raise SiteBuildError("Cloud recipe catalog is missing")
            try:
                data = raw if isinstance(raw, (dict, list)) else json.loads(raw)
            except (TypeError, UnicodeError, json.JSONDecodeError) as exc:
                raise SiteBuildError("Cloud recipe catalog is corrupt") from exc
            return self._catalog_from_data(data, "cloud recipe catalog")

        # Local fallback is deliberately limited to a storage client that has
        # no cloud configured. It must never mask a cloud read failure.
        logger.info("Static build: cloud storage is unavailable; using local catalog source")
        source = self.project_root / "src" / "recipes.json"
        return self._catalog_from_data(_read_json(source, "local recipe catalog"), "local recipe catalog")

    def _load_seeds(self) -> dict:
        source = self.project_root / "src" / "seed_recipes.json"
        data = _read_json(source, "seed recipe source")
        if not isinstance(data, dict) or not data:
            raise SiteBuildError("Required seed recipe source is empty or has an invalid shape")
        invalid = [
            str(slug)
            for slug, record in data.items()
            if not isinstance(slug, str)
            or not slug.strip()
            or not isinstance(record, dict)
            or not isinstance(record.get("recipe_data"), dict)
        ]
        if invalid:
            raise SiteBuildError(
                "Required seed recipe source contains invalid entries: "
                + ", ".join(invalid[:5])
            )
        return data

    def _load_episodes(self, episode_ids: Iterable[str] | None) -> list[dict]:
        if episode_ids:
            episodes = []
            for episode_id in episode_ids:
                try:
                    loader = getattr(
                        self.storage,
                        "load_episode_strict",
                        self.storage.load_episode,
                    )
                    episode = loader(episode_id)
                except Exception as exc:
                    raise SiteBuildError(
                        f"Unable to load published episode from storage: {episode_id}"
                    ) from exc
                if not episode:
                    raise SiteBuildError(f"Published episode not found: {episode_id}")
                if not _episode_is_published(episode):
                    raise SiteBuildError(f"Episode is not published: {episode_id}")
                loaded = dict(episode)
                loaded["episode_id"] = episode_id
                episodes.append(loaded)
            return episodes

        episodes = []
        try:
            loader = getattr(
                self.storage,
                "list_episodes_strict",
                self.storage.list_episodes,
            )
            stored_episodes = loader()
        except Exception as exc:
            raise SiteBuildError("Unable to list published episodes from storage") from exc
        for episode in stored_episodes:
            episode_id = str(episode.get("episode_id") or "")
            if not _CANONICAL_EPISODE_ID.fullmatch(episode_id):
                continue
            if _episode_is_published(episode) and _episode_slug(episode):
                episodes.append(episode)
        return sorted(episodes, key=lambda value: str(value.get("episode_id", "")))

    def _seed_catalog_entries(self, seeds: dict) -> list[dict]:
        return [
            _recipe_data_to_catalog_entry(slug, record.get("recipe_data", {}), record.get("image", ""))
            for slug, record in sorted(seeds.items())
            if isinstance(record, dict) and isinstance(record.get("recipe_data"), dict)
        ]

    def _episode_image_url(self, episode: dict) -> str:
        """Return the same public image identity used by the episode renderer.

        Winner paths are local source paths in some historical episode JSON and
        public URLs in others. Prefer the storage client's URL resolver when it
        can resolve a local winner, then fall back to the episode's public URL.
        This keeps preview builds independent from the process-global storage
        singleton and avoids accidentally emitting a machine-local path.
        """
        # A published week's hero is pinned on the episode (Erik, 2026-08-22;
        # written by Sunday's publish and scripts/pin_published_heroes.py). It
        # wins over any picking rule here exactly as it does in
        # episode_renderer._hero_image_url — the first live full rebuild
        # (2026-09-05) re-picked 20 of 25 heroes because this helper had its
        # own copy of the rule and never saw the pin.
        pinned = str(episode.get("hero_image_url") or "").strip()
        if pinned:
            return _to_local_image_url(pinned)

        winner = episode.get("stages", {}).get("wednesday", {}).get("confirmed_winner")
        featured = winner.get("featured_image") if isinstance(winner, dict) else ""
        if featured:
            try:
                resolved = self.storage.get_image_url(featured)
            except (AttributeError, OSError, ValueError):
                resolved = ""
            if resolved:
                return _to_local_image_url(str(resolved))

        wednesday = episode.get("stages", {}).get("wednesday", {})
        image_urls = wednesday.get("image_urls") or episode.get("image_urls") or []
        if image_urls:
            return _to_local_image_url(str(image_urls[0]))
        return ""

    def _episode_catalog_entry(self, episode: dict, slug: str) -> dict:
        recipe = episode.get("stages", {}).get("monday", {}).get("recipe_data", {})
        image_url = self._episode_image_url(episode)
        image_width, image_height = _image_dimensions(image_url)
        return {
            "slug": slug,
            "title": recipe.get("title") or episode.get("concept", ""),
            "episode_id": episode.get("episode_id", ""),
            "recipe_id": episode.get("recipe_id", ""),
            "category": str(recipe.get("category", "Savory")).title(),
            "cuisine": str(recipe.get("cuisine", "")).strip(),
            "image": _to_webp_url(image_url),
            "image_width": image_width,
            "image_height": image_height,
            "description": recipe.get("description", ""),
            "prep": f"{recipe.get('prep_time', 15)} mins",
            "cook": f"{recipe.get('cook_time', 20)} mins",
            "yield": f"{recipe.get('servings', 12)} servings",
            "ingredients": [
                (
                    f"{item.get('amount', '')} {item.get('item', '')}".strip()
                    + (f" ({item['notes']})" if item.get("notes") else "")
                )
                if isinstance(item, dict)
                else str(item)
                for item in recipe.get("ingredients", [])
            ],
            "instructions": [
                item if isinstance(item, str) else str(item)
                for item in recipe.get("instructions", [])
            ],
        }

    def _catalog_and_episode_slugs(
        self, catalog: dict, seeds: dict, episodes: list[dict]
    ) -> tuple[list[dict], dict[str, str]]:
        """Merge seeds and published episodes without losing duplicate episodes."""
        recipes = [recipe for recipe in catalog.get("recipes", []) if isinstance(recipe, dict)]
        for recipe in recipes:
            if recipe.get("image") and not recipe.get("image_width", 0):
                # _image_dimensions only recognizes a local seed asset by its
                # leading "/assets/" prefix. The catalog's own "image" field
                # (unlike _recipe_data_to_catalog_entry's) is stored without
                # one — e.g. "assets/images/x.webp" — so every seed entry
                # silently fell through to the 1536x1536 generated-photo
                # fallback (visibly wrong for a 1024x1024 seed image, and a
                # source of layout shift once the real file loads).
                image = str(recipe["image"])
                width, height = _image_dimensions(image if image.startswith("/") else f"/{image}")
                recipe["image_width"] = width
                recipe["image_height"] = height
        known_slugs = {str(recipe.get("slug")) for recipe in recipes if recipe.get("slug")}

        for seed_entry in self._seed_catalog_entries(seeds):
            if seed_entry["slug"] not in known_slugs:
                recipes.append(seed_entry)
                known_slugs.add(seed_entry["slug"])

        episode_slugs: dict[str, str] = {}
        # Which episode already owns a slug in this build. Catalog entries
        # published before the pipeline stamped episode_id carry no owner, so
        # they are adoptable by the episode they were published from.
        slug_owner: dict[str, str] = {
            str(recipe.get("slug")): str(recipe.get("episode_id") or "").strip()
            for recipe in recipes
            if recipe.get("slug")
        }
        for episode in episodes:
            episode_id = str(episode.get("episode_id", ""))
            base_slug = _episode_slug(episode)
            candidate = self._episode_catalog_entry(episode, base_slug)

            # Reuse the catalog's own duplicate rule (the same one
            # publish_recipe_to_catalog uses on Sunday) so the builder and
            # the live catalog agree on what "the same recipe" means.
            # Without this, an older catalog entry that predates episode_id
            # stamping gets re-added under a "<slug>-2026-wNN" alias — two
            # pages, two sitemap URLs, two catalog rows for one recipe
            # (#6688: 11 published episodes duplicated this way on the
            # first real build).
            existing = None
            reason = ""
            for recipe in recipes:
                match_reason = _catalog_duplicate_reason(candidate, recipe)
                if match_reason:
                    existing = recipe
                    reason = match_reason
                    break

            if existing is not None:
                existing_slug = str(existing.get("slug") or base_slug)
                owner = slug_owner.get(existing_slug, "")
                if not owner or owner == episode_id:
                    # slug / recipe_id / episode_id are exact identity. An
                    # image or recipe-body match is a heuristic, and a false
                    # positive there silently merges two real recipes onto
                    # one page — a missing catalog row is the only other
                    # symptom. Log which rule fired, the way
                    # publish_recipe_to_catalog does, and raise heuristic
                    # matches to a warning so they get eyes before deploy.
                    if reason.split("=", 1)[0] in {"slug", "recipe_id", "episode_id"}:
                        logger.info(
                            "Static build: episode %s reuses catalog entry '%s' (%s)",
                            episode_id or "<unknown>",
                            existing_slug,
                            reason,
                        )
                    else:
                        logger.warning(
                            "Static build: episode %s adopted catalog entry '%s' on a "
                            "heuristic match (%s), not an exact identity match — "
                            "confirm they are the same recipe before deploying",
                            episode_id or "<unknown>",
                            existing_slug,
                            reason,
                        )
                    if episode_id:
                        existing["episode_id"] = episode_id
                        if candidate.get("recipe_id") and not str(
                            existing.get("recipe_id") or ""
                        ).strip():
                            existing["recipe_id"] = candidate["recipe_id"]
                        slug_owner[existing_slug] = episode_id
                    episode_slugs[episode_id] = existing_slug
                    continue
                # A different episode already owns that entry: this really is
                # a second recipe, so fall through and give it its own slug.

            slug = base_slug
            if slug in known_slugs:
                suffix = _safe_episode_suffix(episode_id)
                slug = f"{base_slug}-{suffix}" if suffix else f"{base_slug}-episode"
                counter = 2
                while slug in known_slugs:
                    slug = f"{base_slug}-{suffix or 'episode'}-{counter}"
                    counter += 1
                candidate = self._episode_catalog_entry(episode, slug)

            recipes.append(candidate)
            known_slugs.add(slug)
            slug_owner[slug] = episode_id
            episode_slugs[episode_id] = slug

        return recipes, episode_slugs

    def _files_to_build(
        self,
        catalog: dict,
        seeds: dict,
        episodes: list[dict],
        explicit_episode_ids: Iterable[str] | None,
    ) -> dict[str, str]:
        recipes, episode_slugs = self._catalog_and_episode_slugs(catalog, seeds, episodes)

        files: dict[str, str] = {
            "recipes.json": json.dumps({"recipes": recipes}, indent=2) + "\n",
            "index.html": render_home(recipes),
            "recipes/index.html": render_recipes_index(recipes),
            "sitemap.xml": render_sitemap(recipes),
        }

        # A missing seed page is safe to add incrementally. Once present, its
        # bytes are immutable until --full-rebuild is explicitly requested.
        for slug, record in sorted(seeds.items()):
            if not isinstance(record, dict):
                continue
            recipe_data = record.get("recipe_data")
            if not isinstance(recipe_data, dict):
                continue
            files[f"recipes/{slug}/index.html"] = render_seed_recipe_page(
                recipe_data,
                record.get("image", ""),
                slug,
                catalog=recipes,
            )

        for episode in episodes:
            episode_id = str(episode.get("episode_id", ""))
            slug = episode_slugs[episode_id]
            files[f"recipes/{slug}/index.html"] = render_episode_page(
                episode,
                image_url=self._episode_image_url(episode) or None,
                canonical_slug=slug,
                catalog=recipes,
            )

        current_id = _current_episode_id()
        current_episode = next(
            (episode for episode in episodes if episode.get("episode_id") == current_id),
            None,
        )
        if current_episode is None and explicit_episode_ids:
            requested_id = next(iter(explicit_episode_ids), "")
            current_episode = next(
                (episode for episode in episodes if episode.get("episode_id") == requested_id),
                None,
            )
        files["this-week/index.html"] = (
            render_episode_page(current_episode, catalog=recipes)
            if current_episode
            else render_placeholder_page(current_id)
        )
        return files

    def build(self, episode_ids: Iterable[str] | None = None, *, dry_run: bool = False) -> BuildResult:
        """Build all requested static artifacts with an atomic mutation gate."""
        requested_ids = tuple(episode_ids or ())
        with self.storage.prefix_scope(self.storage_prefix):
            if self.require_cloud and not self._storage_has_cloud():
                raise SiteBuildError(
                    "Vercel static build requires cloud recipe sources; "
                    "configure BLOB_READ_WRITE_TOKEN at build time"
                )
            catalog = self._load_catalog()
            seeds = self._load_seeds()
            episodes = self._load_episodes(requested_ids or None)
            files = self._files_to_build(catalog, seeds, episodes, requested_ids or None)

        mutations: list[str] = []
        unchanged: list[str] = []
        to_write: list[tuple[Path, str, str]] = []
        for relative_path, content in sorted(files.items()):
            target = (self.output_dir / relative_path).resolve()
            if not target.is_relative_to(self.output_dir):
                raise SiteBuildError(f"Output path escapes build directory: {relative_path}")
            if target.exists():
                existing = target.read_text(encoding="utf-8")
                if existing == content:
                    unchanged.append(relative_path)
                else:
                    if not self.full_rebuild:
                        mutations.append(relative_path)
                    to_write.append((target, content, relative_path))
            else:
                to_write.append((target, content, relative_path))

        if mutations:
            raise ExistingPageMutationError(mutations)

        written: list[str] = []
        if not dry_run:
            for target, content, relative_path in to_write:
                atomic_write(target, content)
                written.append(relative_path)
        else:
            written = [relative_path for _, _, relative_path in to_write]

        return BuildResult(
            output_dir=self.output_dir,
            full_rebuild=self.full_rebuild,
            written=written,
            unchanged=unchanged,
        )


def build_site(
    *,
    output_dir: Path | str | None = None,
    full_rebuild: bool = False,
    episode_ids: Iterable[str] | None = None,
    storage_prefix: str = "",
    dry_run: bool = False,
    require_cloud: bool = False,
) -> BuildResult:
    """Convenience entry point for scripts and deployment tooling."""
    return StaticSiteBuilder(
        output_dir=output_dir,
        full_rebuild=full_rebuild,
        storage_prefix=storage_prefix,
        require_cloud=require_cloud,
    ).build(episode_ids, dry_run=dry_run)
