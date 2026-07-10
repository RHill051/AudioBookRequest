"""Unit tests for Audible series data parsing."""

import pytest

from app.internal.audible.types import AudibleProduct


def _make_product(series: list[dict]) -> AudibleProduct:
    return AudibleProduct.model_validate(
        {
            "asin": "B0036I5LA2",
            "title": "Leviathan Wakes",
            "release_date": "2011-06-15",
            "product_images": {"500": "https://example.com/cover.jpg"},
            "series": series,
        }
    )


def test_series_fields_populated():
    book = _make_product(
        [{"asin": "B00BVZJFOK", "title": "The Expanse", "sequence": "1"}]
    ).to_audiobook()

    assert book.series_name == "The Expanse"
    assert book.series_asin == "B00BVZJFOK"
    assert book.series_number == "1"


def test_series_number_can_be_decimal():
    book = _make_product(
        [{"asin": "B00BVZJFOK", "title": "The Expanse", "sequence": "1.5"}]
    ).to_audiobook()

    assert book.series_number == "1.5"


def test_series_number_optional():
    book = _make_product(
        [{"asin": "B00BVZJFOK", "title": "The Expanse"}]
    ).to_audiobook()

    assert book.series_name == "The Expanse"
    assert book.series_asin == "B00BVZJFOK"
    assert book.series_number is None


def test_no_series_yields_none_fields():
    book = _make_product([]).to_audiobook()

    assert book.series_name is None
    assert book.series_asin is None
    assert book.series_number is None


def test_defaults_to_first_series_with_no_preference():
    """With no preferred_series_asins, the first-listed series is used."""
    book = _make_product(
        [
            {"asin": "ASIN1", "title": "Primary Series", "sequence": "3"},
            {"asin": "ASIN2", "title": "Secondary Series", "sequence": "1"},
        ]
    ).to_audiobook()

    assert book.series_name == "Primary Series"
    assert book.series_asin == "ASIN1"


def test_prefers_already_known_series_over_first_listed():
    """
    Regression test: a book can carry more than one series tag (e.g. Narnia
    lists both a "Publication Order" and an "Author's Preferred Order" series),
    and which one a given edition lists first isn't consistent across editions.
    If one of the candidates is already used elsewhere in the library, prefer
    it even if it's not first, so the same conceptual series doesn't split.
    """
    book = _make_product(
        [
            {"asin": "ASIN1", "title": "Publication Order", "sequence": "3"},
            {"asin": "ASIN2", "title": "Author's Preferred Order", "sequence": "1"},
        ]
    ).to_audiobook(preferred_series_asins={"ASIN2"})

    assert book.series_name == "Author's Preferred Order"
    assert book.series_asin == "ASIN2"
    assert book.series_number == "1"


def test_falls_back_to_first_listed_when_no_candidate_is_known():
    book = _make_product(
        [
            {"asin": "ASIN1", "title": "Publication Order", "sequence": "3"},
            {"asin": "ASIN2", "title": "Author's Preferred Order", "sequence": "1"},
        ]
    ).to_audiobook(preferred_series_asins={"SOME_UNRELATED_SERIES_ASIN"})

    assert book.series_asin == "ASIN1"
