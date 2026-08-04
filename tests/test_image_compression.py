"""Image compression / WebP sibling tests (#5251)."""

from __future__ import annotations


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
        assert 'srcset="/blob-images/foo/hero.webp"' in html
        assert 'type="image/webp"' in html
        assert 'loading="lazy"' in html
        assert 'decoding="async"' in html


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
        # Winner (foo.png) leads, not the macro first variant
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
        assert 'srcset="/blob-images/foo/round_1/option.webp"' in rendered
        assert 'type="image/webp"' in rendered
        assert 'src="/blob-images/foo/round_1/option.png"' in rendered
        assert 'loading="lazy"' in rendered
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
        assert 'decoding="async"' in rendered
