from backend.utils.discord import build_recipe_review_url


def test_build_recipe_review_url_defaults_to_admin_path():
    url = build_recipe_review_url("abc123", base_url="http://localhost:8000")
    assert url == "http://localhost:8000/admin/recipes/abc123"
