"""Unit tests for backfilling series data onto library-only audiobooks."""

from datetime import datetime, timedelta

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from app.internal.audible.series_backfill import backfill_missing_series_data
from app.internal.models import Audiobook


@pytest.fixture
def session():
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def _book(asin: str, *, series_asin: str | None = None, series_name: str | None = None):
    return Audiobook(
        asin=asin,
        title=f"Book {asin}",
        subtitle=None,
        authors=["An Author"],
        narrators=[],
        cover_image=None,
        release_date=datetime(2020, 1, 1),
        runtime_length_min=600,
        series_asin=series_asin,
        series_name=series_name,
        series_number="1" if series_asin else None,
    )


async def test_creates_row_for_library_only_book(session, monkeypatch):
    """A library book with no existing Audiobook row gets one created with series data."""
    fetched = _book("ASIN1", series_asin="SERIES1", series_name="Some Series")

    async def fake_get_single_book(client_session, asin, region=None):
        assert asin == "ASIN1"
        return fetched

    monkeypatch.setattr(
        "app.internal.audible.series_backfill.get_single_book", fake_get_single_book
    )

    count = await backfill_missing_series_data(session, object(), ["ASIN1"])

    assert count == 1
    row = session.exec(select(Audiobook).where(Audiobook.asin == "ASIN1")).one()
    assert row.series_asin == "SERIES1"
    assert row.series_name == "Some Series"
    assert row.downloaded is True
    assert row.series_checked_at is not None


async def test_updates_existing_row_missing_check(session, monkeypatch):
    """An existing row that was never checked gets enriched in place."""
    existing = _book("ASIN2")
    session.add(existing)
    session.commit()

    fetched = _book("ASIN2", series_asin="SERIES2", series_name="Other Series")

    async def fake_get_single_book(client_session, asin, region=None):
        return fetched

    monkeypatch.setattr(
        "app.internal.audible.series_backfill.get_single_book", fake_get_single_book
    )

    count = await backfill_missing_series_data(session, object(), ["ASIN2"])

    assert count == 1
    row = session.exec(select(Audiobook).where(Audiobook.asin == "ASIN2")).one()
    assert row.series_asin == "SERIES2"
    # existing row shouldn't be flipped to downloaded just because it was re-checked
    assert row.downloaded is False


async def test_skips_recently_checked_rows(session, monkeypatch):
    """A row checked within the TTL window isn't re-queried."""
    existing = _book("ASIN3")
    existing.series_checked_at = datetime.now()
    session.add(existing)
    session.commit()

    calls: list[str] = []

    async def fake_get_single_book(client_session, asin, region=None):
        calls.append(asin)
        raise AssertionError("should not be called for a fresh row")

    monkeypatch.setattr(
        "app.internal.audible.series_backfill.get_single_book", fake_get_single_book
    )

    count = await backfill_missing_series_data(session, object(), ["ASIN3"])

    assert count == 0
    assert calls == []


async def test_marks_checked_even_when_no_series_found(session, monkeypatch):
    """A standalone book (no series) is still marked checked so it isn't retried forever."""
    fetched = _book("ASIN4", series_asin=None, series_name=None)

    async def fake_get_single_book(client_session, asin, region=None):
        return fetched

    monkeypatch.setattr(
        "app.internal.audible.series_backfill.get_single_book", fake_get_single_book
    )

    await backfill_missing_series_data(session, object(), ["ASIN4"])

    row = session.exec(select(Audiobook).where(Audiobook.asin == "ASIN4")).one()
    assert row.series_asin is None
    assert row.series_checked_at is not None


async def test_stale_check_is_retried(session, monkeypatch):
    """A row checked long before the TTL window is treated as needing a re-check."""
    existing = _book("ASIN5")
    existing.series_checked_at = datetime.now() - timedelta(days=30)
    session.add(existing)
    session.commit()

    fetched = _book("ASIN5", series_asin="SERIES5", series_name="Newly Found Series")

    async def fake_get_single_book(client_session, asin, region=None):
        return fetched

    monkeypatch.setattr(
        "app.internal.audible.series_backfill.get_single_book", fake_get_single_book
    )

    count = await backfill_missing_series_data(session, object(), ["ASIN5"])

    assert count == 1
    row = session.exec(select(Audiobook).where(Audiobook.asin == "ASIN5")).one()
    assert row.series_asin == "SERIES5"


async def test_respects_max_lookups(session, monkeypatch):
    """Only up to max_lookups ASINs are checked in a single call."""
    calls: list[str] = []

    async def fake_get_single_book(client_session, asin, region=None):
        calls.append(asin)
        return _book(asin)

    monkeypatch.setattr(
        "app.internal.audible.series_backfill.get_single_book", fake_get_single_book
    )

    asins = [f"ASIN{i}" for i in range(10)]
    count = await backfill_missing_series_data(session, object(), asins, max_lookups=3)

    assert count == 3
    assert len(calls) == 3


async def test_no_row_created_when_fetch_fails_for_unknown_asin(session, monkeypatch):
    """If Audible lookup fails and we have no existing row, don't write a placeholder."""

    async def fake_get_single_book(client_session, asin, region=None):
        return None

    monkeypatch.setattr(
        "app.internal.audible.series_backfill.get_single_book", fake_get_single_book
    )

    count = await backfill_missing_series_data(session, object(), ["ASIN6"])

    assert count == 1
    assert session.exec(select(Audiobook).where(Audiobook.asin == "ASIN6")).first() is None
