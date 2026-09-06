"""backend.utils.catalog — the one strict catalog reader (#6854, #6858).

The old readers fell back to ``src/recipes.json`` (the ten launch seeds) when
the live catalog could not be read, so a CDN hiccup silently shrank every
Monday duplicate gate to a ten-recipe view. These tests pin the replacement:
retry briefly, then raise — never fall back.
"""

from __future__ import annotations

import io
import json
from unittest.mock import patch

import pytest

from backend.utils import catalog as cat


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


def _ok(payload) -> _Resp:
    return _Resp(json.dumps(payload).encode("utf-8"))


def test_returns_parsed_catalog_on_first_success() -> None:
    payload = {"recipes": [{"title": "Kimchi Cheddar Rice Cups", "category": "party"}]}
    with patch.object(cat.urllib.request, "urlopen", return_value=_ok(payload)) as fetch:
        result = cat.load_published_catalog(attempts=3)
    assert result == payload
    assert fetch.call_count == 1


def test_retries_transient_failures_then_succeeds() -> None:
    payload = {"recipes": []}
    with patch.object(
        cat.urllib.request, "urlopen",
        side_effect=[OSError("timed out"), OSError("reset"), _ok(payload)],
    ) as fetch, patch.object(cat.time, "sleep") as sleep:
        result = cat.load_published_catalog(attempts=3)
    assert result == payload
    assert fetch.call_count == 3
    assert sleep.call_count == 2  # no sleep after the final attempt


def test_raises_after_exhausting_attempts_never_falls_back() -> None:
    with patch.object(cat.urllib.request, "urlopen", side_effect=OSError("down")), \
         patch.object(cat.time, "sleep"):
        with pytest.raises(cat.CatalogUnavailableError, match="after 3 attempts"):
            cat.load_published_catalog(attempts=3)


def test_malformed_payload_is_not_retried() -> None:
    """A 200 with the wrong shape is a real bug, not a blip — fail immediately."""
    with patch.object(cat.urllib.request, "urlopen", return_value=_ok(["not", "a", "dict"])) as fetch, \
         patch.object(cat.time, "sleep") as sleep:
        with pytest.raises(cat.CatalogUnavailableError, match="malformed"):
            cat.load_published_catalog(attempts=3)
    assert fetch.call_count == 1
    sleep.assert_not_called()


def test_bad_json_is_retried_like_a_network_error() -> None:
    with patch.object(
        cat.urllib.request, "urlopen",
        side_effect=[_Resp(b"<html>502</html>"), _ok({"recipes": []})],
    ) as fetch, patch.object(cat.time, "sleep"):
        assert cat.load_published_catalog(attempts=2) == {"recipes": []}
    assert fetch.call_count == 2


def test_helpers_normalise_titles_and_categories() -> None:
    catalog = {
        "recipes": [
            {"title": "  Kimchi Cheddar Rice Cups ", "category": "party"},
            {"title": "", "category": "Sweet"},
            {"title": "Lemon Ricotta Polenta Cups", "category": "SWEET"},
            "not-a-dict",
            {"title": "Custard Tart Cups"},
        ]
    }
    assert cat.catalog_titles(catalog) == [
        "kimchi cheddar rice cups",
        "lemon ricotta polenta cups",
        "custard tart cups",
    ]
    assert cat.category_counts(catalog) == {"Party": 1, "Sweet": 2}
    assert len(cat.catalog_recipes(catalog)) == 4


def test_zero_attempts_is_a_programming_error() -> None:
    with pytest.raises(ValueError):
        cat.load_published_catalog(attempts=0)


def test_stray_dessert_label_is_an_alias_for_sweet() -> None:
    """The baker produced 'Dessert' in W10 and it had to be hand-corrected; the
    Monday path must not turn that stray into a null category or a lost count."""
    assert cat.normalize_category("Dessert") == "Sweet"
    assert cat.normalize_category("desserts") == "Sweet"
    assert cat.normalize_category("sweet") == "Sweet"
    assert cat.normalize_category("Brunch") is None
    assert cat.normalize_category(None) is None
    counts = cat.category_counts({"recipes": [
        {"category": "Dessert"}, {"category": "Dessert"}, {"category": "Sweet"},
        {"category": "party"}, {"category": "Brunch"},
    ]})
    assert counts == {"Sweet": 3, "Party": 1}
