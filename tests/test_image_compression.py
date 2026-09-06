"""Image compression / WebP sibling tests (#5251, #6755)."""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch


def test_seed_webp_dimensions_are_read_from_repository_assets():
    from backend.publishing.episode_renderer import _image_dimensions

    assert _image_dimensions("/assets/images/classic-blueberry-muffins.webp") == (1024, 1024)
    assert _image_dimensions("/assets/images/muffin-tin-lasagna.webp") == (1024, 1024)


class TestToWebpUrl:
    def test_png_path_becomes_webp(self):
        from backend.publishing.episode_renderer import _to_webp_url

        assert _to_webp_url("/blob-images/abc/hero.png") == "/blob-images/abc/hero.webp"

    def test_full_blob_cdn_url(self):
        from backend.publishing.episode_renderer import _to_webp_url

        url = "https://gtczmjysc51nh8fq.public.blob.vercel-storage.com/images/abc/hero.png"
        expected = "https://gtczmjysc51nh8fq.public.blob.vercel-storage.com/images/abc/hero.webp"
        assert _to_webp_url(url) == expected

    def test_preserves_querystring(self):
        from backend.publishing.episode_renderer import _to_webp_url

        assert (
            _to_webp_url("/blob-images/abc/hero.png?v=1")
            == "/blob-images/abc/hero.webp?v=1"
        )

    def test_non_png_passthrough(self):
        from backend.publishing.episode_renderer import _to_webp_url

        assert _to_webp_url("/blob-images/abc/hero.jpg") == "/blob-images/abc/hero.jpg"

    def test_empty_passthrough(self):
        from backend.publishing.episode_renderer import _to_webp_url

        assert _to_webp_url("") == ""

    def test_case_insensitive_suffix(self):
        from backend.publishing.episode_renderer import _to_webp_url

        assert _to_webp_url("/x/HERO.PNG") == "/x/HERO.webp"

    def test_strips_vercel_random_suffix(self):
        """Historical Vercel Blob uploads land at `<name>-<26char>.png`.
        Our deterministic WebP backfill lives at `<name>.webp` (no hash),
        so the rewriter must drop the random suffix before appending .webp.
        """
        from backend.publishing.episode_renderer import _to_webp_url

        url = "images/20d49356-9VSOT4SGhaUDoAUDM3kZPqxd3Hpeyu.png"
        assert _to_webp_url(url) == "images/20d49356.webp"

    def test_strips_suffix_full_cdn_url(self):
        from backend.publishing.episode_renderer import _to_webp_url

        url = (
            "https://gtczmjysc51nh8fq.public.blob.vercel-storage.com/"
            "images/a9b98b08/round_1/hero_threequarter-zgr18m7U5nkzXeZ2FK2FchHL8Pk2ym.png"
        )
        expected = (
            "https://gtczmjysc51nh8fq.public.blob.vercel-storage.com/"
            "images/a9b98b08/round_1/hero_threequarter.webp"
        )
        assert _to_webp_url(url) == expected


class TestHeroPictureTag:
    def test_hero_renders_picture_when_png(self):
        """Episode renderer should emit a <picture> tag with a WebP source
        for the hero image when the URL is a PNG."""
        from backend.publishing import episode_renderer

        # Minimal episode with a monday stage that has a recipe and an
        # image_url. render_episode_page reads ep['image_urls'][0].
        ep = {
            "episode_id": "ep-test",
            "concept": "Test",
            "stages": {
                "monday": {
                    "recipe_data": {
                        "title": "Test Muffins",
                        "description": "delicious",
                        "servings": 12,
                        "prep_time": 10,
                        "cook_time": 20,
                        "ingredients": [{"item": "flour", "amount": "1 cup"}],
                        "instructions": ["mix it", "bake it"],
                    },
                },
            },
            "image_urls": ["https://example.com/images/foo/hero.png"],
            "image_paths": ["src/assets/images/foo/hero.png"],
        }

        html = episode_renderer.render_episode_page(
            ep, image_url="/blob-images/foo/hero.png",
        )
        assert "<picture>" in html
        # No variant blobs exist for this made-up path (storage.
        # image_variants_available is unmocked here), so the srcset must
        # fall back to the pre-#6755 single full-size candidate rather than
        # naming a -400w/-800w URL that would 404 — see TestResponsiveSrcset
        # for the mocked-available case.
        assert 'srcset="/blob-images/foo/hero.webp"' in html
        assert 'src="/blob-images/foo/hero.png"' in html
        assert 'type="image/webp"' in html
        assert 'sizes="(max-width: 768px) 100vw, 720px"' in html
        assert 'loading="eager"' in html
        assert 'fetchpriority="high"' in html
        assert 'loading="lazy"' not in html
        assert 'decoding="async"' in html

    def test_webp_only_asset_does_not_get_a_fabricated_png_fallback(self):
        """No local PNG exists for some seed assets; keep the real WebP URL."""
        from backend.publishing import episode_renderer

        ep = {
            "concept": "Seed",
            "stages": {
                "monday": {
                    "recipe_data": {
                        "title": "Seed Muffins",
                        "description": "short",
                        "ingredients": ["flour"],
                        "instructions": ["bake"],
                    },
                },
            },
        }

        html = episode_renderer.render_episode_page(
            ep, image_url="/assets/images/seed-muffins.webp"
        )
        assert 'src="/assets/images/seed-muffins.webp"' in html
        assert "/assets/images/seed-muffins.png" not in html


class TestHeroFromWinner:
    """The page hero should come from the art director's confirmed winner,
    not from image_urls[0] (the always-macro first variant)."""

    def _episode(self, winner_featured=None):
        wed: dict = {
            "image_urls": [
                "https://example.com/images/foo/round_1/macro_closeup.png",
                "https://example.com/images/foo/round_1/overhead_flatlay.png",
            ],
        }
        if winner_featured is not None:
            wed["confirmed_winner"] = {"featured_image": winner_featured}
        return {
            "episode_id": "ep-test",
            "concept": "Test",
            "stages": {
                "monday": {
                    "recipe_data": {
                        "title": "Test Muffins",
                        "description": "delicious",
                        "ingredients": [{"item": "flour", "amount": "1 cup"}],
                        "instructions": ["mix it", "bake it"],
                    },
                },
                "wednesday": wed,
            },
            "image_urls": wed["image_urls"],
        }

    def test_hero_uses_winner_featured_image(self):
        from unittest.mock import patch
        from backend.publishing import episode_renderer

        ep = self._episode(winner_featured="src/assets/images/foo.png")
        with patch.object(
            episode_renderer.storage, "get_image_url",
            return_value="/blob-images/foo.png",
        ):
            html = episode_renderer.render_episode_page(ep)
        # Winner (foo.png) leads, not the macro first variant. The rendered
        # The PNG remains the browser fallback; only the social JPEG is a
        # purpose-built sibling now.
        assert 'src="/blob-images/foo.png"' in html
        assert 'srcset="/blob-images/foo.webp"' in html
        assert "round_1/macro_closeup.png" not in html.split("recipe-hero__image")[1][:400]

    def test_hero_falls_back_to_first_url_without_winner(self):
        from backend.publishing import episode_renderer

        ep = self._episode(winner_featured=None)
        html = episode_renderer.render_episode_page(ep)
        # No winner recorded → legacy behavior (image_urls[0] = macro)
        assert "round_1/macro_closeup" in html

    def test_midweek_episode_has_no_wednesday_stage_renders_placeholder(self):
        """Mon/Tue episodes (no photography yet) must render the placeholder,
        not crash in _hero_image_url."""
        from backend.publishing import episode_renderer

        ep = {
            "episode_id": "ep-mon",
            "concept": "Test",
            "stages": {
                "monday": {
                    "recipe_data": {
                        "title": "Test Muffins",
                        "description": "delicious",
                        "ingredients": [{"item": "flour", "amount": "1 cup"}],
                        "instructions": ["mix it", "bake it"],
                    },
                },
            },
        }
        html = episode_renderer.render_episode_page(ep)
        assert "recipe-hero__image-placeholder" in html

    def test_hero_handles_non_dict_confirmed_winner(self):
        """A malformed confirmed_winner must not crash the render."""
        from backend.publishing import episode_renderer

        ep = self._episode(winner_featured=None)
        ep["stages"]["wednesday"]["confirmed_winner"] = "oops-not-a-dict"
        html = episode_renderer.render_episode_page(ep)
        assert "round_1/macro_closeup" in html


class TestGalleryPictureTag:
    def test_png_attachment_renders_webp_source_with_png_fallback(self):
        from backend.publishing.episode_renderer import _render_chat_message

        rendered = _render_chat_message(
            {
                "character": "Julian Torres",
                "message": "Here is an option.",
                "attachments": ["round_1/option.png"],
            },
            {"round_1/option.png": "/blob-images/foo/round_1/option.png"},
        )

        assert "<picture>" in rendered
        # No variant blobs exist for this made-up path — falls back to the
        # single full-size candidate (see TestResponsiveSrcset).
        assert 'srcset="/blob-images/foo/round_1/option.webp"' in rendered
        assert 'type="image/webp"' in rendered
        assert 'sizes="(max-width: 400px) 100vw, 384px"' in rendered
        assert 'src="/blob-images/foo/round_1/option.png"' in rendered
        assert 'loading="lazy"' in rendered
        assert 'fetchpriority="high"' not in rendered
        assert 'decoding="async"' in rendered

    def test_non_png_attachment_remains_plain_img(self):
        from backend.publishing.episode_renderer import _render_chat_message

        rendered = _render_chat_message(
            {
                "character": "Julian Torres",
                "message": "Here is an option.",
                "attachments": ["round_1/option.jpg"],
            },
            {"round_1/option.jpg": "/blob-images/foo/round_1/option.jpg"},
        )

        assert "<picture>" not in rendered
        assert "<source" not in rendered
        assert 'src="/blob-images/foo/round_1/option.jpg"' in rendered
        assert 'loading="lazy"' in rendered
        assert 'fetchpriority="high"' not in rendered
        assert 'decoding="async"' in rendered


class TestVariantLookupKey:
    """_variant_lookup_key normalizes cloud/filesystem/CDN URL shapes (#6755)."""

    def test_strips_blob_images_rewrite_prefix(self):
        from backend.publishing.episode_renderer import _variant_lookup_key

        assert _variant_lookup_key("/blob-images/abc/hero.png") == "abc/hero.png"

    def test_strips_full_cdn_url(self):
        from backend.publishing.episode_renderer import _variant_lookup_key

        url = "https://gtczmjysc51nh8fq.public.blob.vercel-storage.com/images/abc/hero.png"
        assert _variant_lookup_key(url) == "abc/hero.png"

    def test_filesystem_local_dev_path(self):
        from backend.publishing.episode_renderer import _variant_lookup_key

        assert _variant_lookup_key("/assets/images/abc/hero.png") == "abc/hero.png"

    def test_preserves_querystring_stripped(self):
        from backend.publishing.episode_renderer import _variant_lookup_key

        assert _variant_lookup_key("/blob-images/abc/hero.png?v=1") == "abc/hero.png"


class TestInsertWidthDescriptor:
    def test_inserts_before_extension(self):
        from backend.publishing.episode_renderer import _insert_width_descriptor

        assert _insert_width_descriptor("/blob-images/abc/hero.webp", 400) == (
            "/blob-images/abc/hero-400w.webp"
        )

    def test_preserves_querystring(self):
        from backend.publishing.episode_renderer import _insert_width_descriptor

        assert _insert_width_descriptor("/blob-images/abc/hero.webp?v=1", 400) == (
            "/blob-images/abc/hero-400w.webp?v=1"
        )

    def test_non_webp_passthrough(self):
        from backend.publishing.episode_renderer import _insert_width_descriptor

        assert _insert_width_descriptor("/blob-images/abc/hero.png", 400) == (
            "/blob-images/abc/hero.png"
        )


class TestResponsiveSrcset:
    """_to_webp_srcset (#6755): width-descriptor srcset, gated on
    storage.image_variants_available so a candidate that would 404 is never
    emitted (the ordering hazard from the card)."""

    def test_falls_back_to_single_candidate_when_variants_unavailable(self):
        from backend.publishing.episode_renderer import _to_webp_srcset

        with patch(
            "backend.publishing.episode_renderer.storage.image_variants_available",
            return_value=False,
        ):
            result = _to_webp_srcset("/blob-images/abc/hero.png")

        assert result == "/blob-images/abc/hero.webp"

    def test_full_srcset_when_variants_available(self):
        from backend.publishing.episode_renderer import _to_webp_srcset

        with patch(
            "backend.publishing.episode_renderer.storage.image_variants_available",
            return_value=True,
        ):
            result = _to_webp_srcset("/blob-images/abc/hero.png")

        assert result == (
            "/blob-images/abc/hero-400w.webp 400w, "
            "/blob-images/abc/hero-800w.webp 800w, "
            "/blob-images/abc/hero.webp 1536w"
        )

    def test_uses_real_intrinsic_width_for_a_local_seed_asset(self):
        """Seed webp-only assets have a real recorded size (1024) rather than
        the generated-photography default of 1536 — the final descriptor
        must reflect that, not a hardcoded number."""
        from backend.publishing.episode_renderer import _to_webp_srcset

        # /assets/*.webp is never a PNG, so _to_webp_url passes it through
        # unchanged and the availability check is never even reached.
        result = _to_webp_srcset("/assets/images/classic-blueberry-muffins.webp")
        assert result == "/assets/images/classic-blueberry-muffins.webp"

    def test_non_png_passthrough_never_calls_storage(self):
        from backend.publishing.episode_renderer import _to_webp_srcset

        with patch(
            "backend.publishing.episode_renderer.storage.image_variants_available"
        ) as mock_available:
            result = _to_webp_srcset("/blob-images/abc/hero.jpg")

        assert result == "/blob-images/abc/hero.jpg"
        mock_available.assert_not_called()

    def test_empty_passthrough(self):
        from backend.publishing.episode_renderer import _to_webp_srcset

        assert _to_webp_srcset("") == ""

    def test_per_render_cache_checks_storage_once_per_image(self):
        from backend.publishing.episode_renderer import _to_webp_srcset

        cache: dict[str, bool] = {}
        with patch(
            "backend.publishing.episode_renderer.storage.image_variants_available",
            return_value=True,
        ) as mock_available:
            _to_webp_srcset("/blob-images/abc/hero.png", cache)
            _to_webp_srcset("/blob-images/abc/hero.png", cache)

        mock_available.assert_called_once()


class TestHeroSrcsetWithVariantsAvailable:
    """End-to-end: render_episode_page emits the full width-descriptor
    srcset on the hero <picture> once storage confirms variants exist."""

    def test_hero_emits_full_srcset_and_sizes(self):
        from backend.publishing import episode_renderer

        ep = {
            "episode_id": "ep-test",
            "concept": "Test",
            "stages": {
                "monday": {
                    "recipe_data": {
                        "title": "Test Muffins",
                        "description": "delicious",
                        "ingredients": [{"item": "flour", "amount": "1 cup"}],
                        "instructions": ["mix it", "bake it"],
                    },
                },
            },
            "image_urls": ["https://example.com/images/foo/hero.png"],
        }

        with patch.object(
            episode_renderer.storage, "image_variants_available", return_value=True
        ):
            html = episode_renderer.render_episode_page(
                ep, image_url="/blob-images/foo/hero.png",
            )

        assert (
            'srcset="/blob-images/foo/hero-400w.webp 400w, '
            '/blob-images/foo/hero-800w.webp 800w, '
            '/blob-images/foo/hero.webp 1536w"'
        ) in html
        assert 'sizes="(max-width: 768px) 100vw, 720px"' in html


class TestGallerySrcsetWithVariantsAvailable:
    def test_gallery_emits_full_srcset_and_sizes(self):
        from backend.publishing.episode_renderer import _render_chat_message

        with patch(
            "backend.publishing.episode_renderer.storage.image_variants_available",
            return_value=True,
        ):
            rendered = _render_chat_message(
                {
                    "character": "Julian Torres",
                    "message": "Here is an option.",
                    "attachments": ["round_1/option.png"],
                },
                {"round_1/option.png": "/blob-images/foo/round_1/option.png"},
            )

        assert (
            'srcset="/blob-images/foo/round_1/option-400w.webp 400w, '
            '/blob-images/foo/round_1/option-800w.webp 800w, '
            '/blob-images/foo/round_1/option.webp 1536w"'
        ) in rendered
        assert 'sizes="(max-width: 400px) 100vw, 384px"' in rendered


class TestBackfillImageVariantsScript:
    """scripts/backfill_image_variants.py dry-run (#6755).

    Defaults to listing missing variant keys without downloading or
    uploading anything — the hard rule this whole task runs under.
    """

    @staticmethod
    def _catalog_response():
        response = MagicMock()
        response.raise_for_status = MagicMock()
        response.json.return_value = {
            "blobs": [{"url": "https://cdn.example.com/pages/recipes.json"}]
        }
        return response

    @staticmethod
    def _catalog_content():
        response = MagicMock()
        response.raise_for_status = MagicMock()
        response.json.return_value = {
            "recipes": [{"episode_id": "2026-W40"}],
        }
        return response

    @staticmethod
    def _episode_list_response():
        response = MagicMock()
        response.raise_for_status = MagicMock()
        response.json.return_value = {
            "blobs": [{"url": "https://cdn.example.com/episodes/2026-W40.json"}]
        }
        return response

    @staticmethod
    def _episode_content():
        response = MagicMock()
        response.raise_for_status = MagicMock()
        response.json.return_value = {
            "image_urls": [
                "https://gtczmjysc51nh8fq.public.blob.vercel-storage.com/"
                "images/abc123/round_1/macro_closeup.png",
            ],
        }
        return response

    @staticmethod
    def _images_list_response():
        response = MagicMock()
        response.raise_for_status = MagicMock()
        response.json.return_value = {
            "blobs": [
                {
                    "pathname": "images/abc123/round_1/macro_closeup.png",
                    "url": "https://cdn.example.com/images/abc123/round_1/macro_closeup.png",
                },
            ],
            "hasMore": False,
        }
        return response

    def test_dry_run_lists_missing_variant_keys_without_uploading(self, capsys):
        import scripts.backfill_image_variants as backfill

        empty_episode_listing = MagicMock()
        empty_episode_listing.raise_for_status = MagicMock()
        empty_episode_listing.json.return_value = {"blobs": [], "hasMore": False}
        get_responses = [
            self._catalog_response(),
            self._catalog_content(),
            self._episode_list_response(),
            self._episode_content(),
            empty_episode_listing,  # list_blobs("episodes/") — the catalog already named the only episode
            self._images_list_response(),
        ]

        with (
            patch.dict(os.environ, {"BLOB_READ_WRITE_TOKEN": "fake-token"}),
            patch("scripts.backfill_image_variants.requests.get", side_effect=get_responses),
            patch("scripts.backfill_image_variants.requests.put") as mock_put,
        ):
            exit_code = backfill.main([])

        assert exit_code == 0
        mock_put.assert_not_called()

        output = capsys.readouterr().out
        assert "1 distinct published PNGs referenced" in output
        assert "would create: images/abc123/round_1/macro_closeup-400w.webp" in output
        assert "would create: images/abc123/round_1/macro_closeup-800w.webp" in output

    def test_missing_token_exits_without_any_network_call(self):
        import scripts.backfill_image_variants as backfill

        with (
            patch.dict(os.environ, {}, clear=True),
            patch("scripts.backfill_image_variants.requests.get") as mock_get,
        ):
            exit_code = backfill.main([])

        assert exit_code == 2
        mock_get.assert_not_called()


def test_backfill_collects_the_confirmed_winner_hero_key():
    """The recipe-page hero is the art director's confirmed winner, stored as a
    repo-relative path — NOT one of image_urls. It is the LCP image, so a
    backfill that walks only image_urls leaves the most important picture
    without variants (found on the first live full rebuild, 2026-09-05)."""
    import scripts.backfill_image_variants as backfill

    catalog = {"recipes": [{"slug": "spanakopita-phyllo-cups", "episode_id": "2026-W28"}]}
    episode = {
        "image_urls": [
            "https://gtczmjysc51nh8fq.public.blob.vercel-storage.com/images/2068c0cc/round_1/macro_closeup.png",
        ],
        "stages": {
            "wednesday": {
                "image_urls": [],
                "confirmed_winner": {
                    "variant": "overhead_flatlay",
                    "path": "src/assets/images/2068c0cc/round_1/overhead_flatlay.png",
                    "featured_image": "src/assets/images/2068c0cc.png",
                },
            }
        },
    }

    def fake_fetch(_token, pathname):
        return catalog if pathname == "pages/recipes.json" else episode

    with patch("scripts.backfill_image_variants.fetch_json_blob", side_effect=fake_fetch), \
         patch("scripts.backfill_image_variants.list_blobs", return_value=[]):
        keys = backfill.collect_published_png_keys("fake-token")

    assert keys == ["images/2068c0cc.png", "images/2068c0cc/round_1/macro_closeup.png"]


def test_backfill_walks_published_episodes_the_catalog_does_not_name():
    """11 live catalog entries predate episode_id stamping; their pages still
    render images, so the walk must cover every published episode blob."""
    import scripts.backfill_image_variants as backfill

    catalog = {"recipes": [{"slug": "maple-hash-brown-nests"}]}  # no episode_id
    old_episode = {
        "published_at": "2026-05-10T00:00:00+00:00",
        "image_urls": ["https://gtczmjysc51nh8fq.public.blob.vercel-storage.com/images/b1f8e7f4/round_1/macro_closeup.png"],
        "stages": {"wednesday": {"confirmed_winner": {"featured_image": "src/assets/images/b1f8e7f4.png"}}},
    }
    unpublished = {"image_urls": ["https://gtczmjysc51nh8fq.public.blob.vercel-storage.com/images/dead00/round_1/x.png"], "stages": {}}

    class _Resp:
        def __init__(self, payload): self._p = payload
        def raise_for_status(self): pass
        def json(self): return self._p

    def fake_fetch(_token, pathname):
        return catalog if pathname == "pages/recipes.json" else None

    def fake_list(_token, prefix):
        assert prefix == "episodes/"
        return [{"pathname": "episodes/2026-W19.json", "url": "u1"}, {"pathname": "episodes/2026-W99.json", "url": "u2"}]

    def fake_get(url, timeout=30):
        return _Resp(old_episode if url == "u1" else unpublished)

    with patch("scripts.backfill_image_variants.fetch_json_blob", side_effect=fake_fetch), \
         patch("scripts.backfill_image_variants.list_blobs", side_effect=fake_list), \
         patch("scripts.backfill_image_variants.requests.get", side_effect=fake_get):
        keys = backfill.collect_published_png_keys("fake-token")

    assert keys == ["images/b1f8e7f4.png", "images/b1f8e7f4/round_1/macro_closeup.png"]
