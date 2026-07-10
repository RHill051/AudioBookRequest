"""
Unit tests for how get_series_books collapses Audible's many editions-per-book
relationships into a single representative per series position.
"""

import pytest

from app.internal.audible.series import get_series_books, parse_series_sequence


class _FakeResponse:
    def __init__(self, payload: object):
        self._payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc: object):
        return False

    def raise_for_status(self):
        pass

    async def json(self):
        return self._payload


class _FakeClientSession:
    """Duck-types aiohttp.ClientSession.get() for the two calls get_series_books makes:
    one for the series product's relationships, one per representative book ASIN."""

    def __init__(self, series_asin: str, relationships_payload: object, book_payloads: dict[str, object]):
        self.series_asin = series_asin
        self.relationships_payload = relationships_payload
        self.book_payloads = book_payloads
        self.requested_asins: list[str] = []

    def get(self, url: str, params: object = None):
        trailing = url.rsplit("/", 1)[-1]
        if trailing == self.series_asin:
            return _FakeResponse(self.relationships_payload)
        self.requested_asins.append(trailing)
        return _FakeResponse(self.book_payloads[trailing])


def _relationship(asin: str, sequence: str, sort: str):
    return {
        "asin": asin,
        "relationship_to_product": "child",
        "relationship_type": "series",
        "sequence": sequence,
        "sort": sort,
    }


def _series_payload(title: str, relationships: list[dict[str, str]]):
    return {"product": {"title": title, "relationships": relationships}}


def _book_payload(asin: str, title: str):
    return {"product": {"asin": asin, "title": title, "release_date": "2020-01-01"}}


def test_parse_series_sequence():
    assert parse_series_sequence("1") == 1.0
    assert parse_series_sequence("3.5") == 3.5
    assert parse_series_sequence("") is None
    assert parse_series_sequence(None) is None
    assert parse_series_sequence("1-7") is None  # box set range
    assert parse_series_sequence("not a number") is None


async def test_edition_explosion_collapses_to_one_per_slot():
    """Mirrors the real Narnia data: many editions/box sets sharing a sequence number."""
    relationships = [
        _relationship("BOXSET1", "1-7", "2"),  # box set, should be dropped
        _relationship("NOSEQ", "", "1"),  # no sequence, should be dropped
        _relationship("BOOK1_EDA", "1", "3"),
        _relationship("BOOK1_EDB", "1", "3"),  # duplicate edition of book 1
        _relationship("BOOK1_EDC", "1", "3"),  # another duplicate edition
        _relationship("BOOK2_EDA", "2", "4"),
        _relationship("BOOK2_EDB", "2", "4"),
    ]
    fake_session = _FakeClientSession(
        "SERIES1",
        _series_payload("The Chronicles of Test", relationships),
        {
            "BOOK1_EDA": _book_payload("BOOK1_EDA", "The First Book"),
            "BOOK2_EDA": _book_payload("BOOK2_EDA", "The Second Book"),
        },
    )

    books = await get_series_books(fake_session, "SERIES1")

    assert [b.asin for b in books] == ["BOOK1_EDA", "BOOK2_EDA"]
    assert [b.series_number for b in books] == ["1", "2"]
    assert all(b.series_asin == "SERIES1" for b in books)
    assert all(b.series_name == "The Chronicles of Test" for b in books)
    # only one edition per slot should ever be fetched, not all 5 numbered relationship children
    assert fake_session.requested_asins == ["BOOK1_EDA", "BOOK2_EDA"]


async def test_decimal_sequence_sorted_between_integers():
    relationships = [
        _relationship("BOOK1", "1", "3"),
        _relationship("BOOK1_5", "1.5", "4"),
        _relationship("BOOK2", "2", "5"),
    ]
    fake_session = _FakeClientSession(
        "SERIES2",
        _series_payload("Test Series", relationships),
        {
            "BOOK1": _book_payload("BOOK1", "Book One"),
            "BOOK1_5": _book_payload("BOOK1_5", "Book One And A Half"),
            "BOOK2": _book_payload("BOOK2", "Book Two"),
        },
    )

    books = await get_series_books(fake_session, "SERIES2")

    assert [b.series_number for b in books] == ["1", "1.5", "2"]


async def test_representative_fetch_failure_is_skipped():
    """If the chosen representative's own detail fetch fails, drop that slot rather than crash."""
    relationships = [
        _relationship("BOOK1", "1", "3"),
        _relationship("BOOK2", "2", "4"),
    ]
    fake_session = _FakeClientSession(
        "SERIES3",
        _series_payload("Test Series", relationships),
        {"BOOK1": _book_payload("BOOK1", "Book One")},  # BOOK2 deliberately missing
    )

    books = await get_series_books(fake_session, "SERIES3")

    assert [b.asin for b in books] == ["BOOK1"]


async def test_relationship_fetch_failure_returns_empty():
    class _RaisingResponse:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc: object):
            return False

        def raise_for_status(self):
            raise RuntimeError("boom")

        async def json(self):
            return {}

    class _RaisingSession:
        def get(self, url: str, params: object = None):
            return _RaisingResponse()

    books = await get_series_books(_RaisingSession(), "SERIES4")
    assert books == []
