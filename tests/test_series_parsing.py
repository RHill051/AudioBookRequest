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


def test_only_first_series_used():
    """When a book belongs to multiple series, only the first is stored."""
    book = _make_product(
        [
            {"asin": "ASIN1", "title": "Primary Series", "sequence": "3"},
            {"asin": "ASIN2", "title": "Secondary Series", "sequence": "1"},
        ]
    ).to_audiobook()

    assert book.series_name == "Primary Series"
    assert book.series_asin == "ASIN1"
