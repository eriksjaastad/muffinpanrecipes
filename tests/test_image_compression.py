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
