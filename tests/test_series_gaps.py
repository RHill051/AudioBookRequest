"""Unit tests for computing series gaps against a user's owned/downloaded books."""

from datetime import datetime

import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.internal.audible.series_gaps import get_series_gaps
from app.internal.auth.login_types import LoginTypeEnum
from app.internal.auth.authentication import DetailedUser
from app.internal.models import Audiobook, AudiobookRequest, GroupEnum, User


@pytest.fixture
def session():
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


@pytest.fixture
def user(session):
    u = User(username="alice", password="hash", group=GroupEnum.trusted)
    session.add(u)
    session.commit()
    return DetailedUser.model_validate(u, update={"login_type": LoginTypeEnum.forms})


def _book(
    asin: str,
    *,
    series_asin: str | None = None,
    series_name: str | None = None,
    series_number: str | None = None,
    downloaded: bool = False,
):
    return Audiobook(
        asin=asin,
        title=f"Book {asin}",
        subtitle=None,
        authors=["An Author"],
        narrators=[],
        cover_image=None,
        release_date=datetime(2020, 1, 1),
        runtime_length_min=600,
        downloaded=downloaded,
        series_asin=series_asin,
        series_name=series_name,
        series_number=series_number,
    )


async def test_no_owned_series_books_returns_empty(session, user):
    gaps, still_discovering = await get_series_gaps(session, object(), user)
    assert gaps == []
    assert still_discovering is False


async def test_gap_found_for_partially_owned_series(session, user, monkeypatch):
    owned = _book("A1", series_asin="S1", series_name="The Series", downloaded=True)
    session.add(owned)
    session.commit()

    full_series = [
        _book("A1", series_asin="S1", series_name="The Series"),
        _book("A2", series_asin="S1", series_name="The Series"),
        _book("A3", series_asin="S1", series_name="The Series"),
    ]

    async def fake_get_series_books(client_session, series_asin, region=None):
        assert series_asin == "S1"
        return full_series

    monkeypatch.setattr(
        "app.internal.audible.series_gaps.get_series_books", fake_get_series_books
    )

    gaps, _ = await get_series_gaps(session, object(), user)

    assert len(gaps) == 1
    gap = gaps[0]
    assert gap.series_asin == "S1"
    assert gap.owned_count == 1
    assert gap.total_count == 3
    assert {b.asin for b in gap.missing_books} == {"A2", "A3"}


async def test_fully_owned_series_yields_no_gap(session, user, monkeypatch):
    for asin in ("A1", "A2"):
        session.add(
            _book(asin, series_asin="S1", series_name="The Series", downloaded=True)
        )
    session.commit()

    full_series = [
        _book("A1", series_asin="S1", series_name="The Series"),
        _book("A2", series_asin="S1", series_name="The Series"),
    ]

    async def fake_get_series_books(client_session, series_asin, region=None):
        return full_series

    monkeypatch.setattr(
        "app.internal.audible.series_gaps.get_series_books", fake_get_series_books
    )

    gaps, _ = await get_series_gaps(session, object(), user)
    assert gaps == []


async def test_requested_missing_book_is_flagged(session, user, monkeypatch):
    session.add(_book("A1", series_asin="S1", series_name="The Series", downloaded=True))
    session.add(_book("A2", series_asin="S1", series_name="The Series", downloaded=False))
    session.add(AudiobookRequest(asin="A2", user_username="alice"))
    session.commit()

    full_series = [
        _book("A1", series_asin="S1", series_name="The Series"),
        _book("A2", series_asin="S1", series_name="The Series"),
    ]

    async def fake_get_series_books(client_session, series_asin, region=None):
        return full_series

    monkeypatch.setattr(
        "app.internal.audible.series_gaps.get_series_books", fake_get_series_books
    )

    gaps, _ = await get_series_gaps(session, object(), user)

    assert len(gaps) == 1
    assert gaps[0].requested_asins == {"A2"}


async def test_series_lookup_failure_is_skipped(session, user, monkeypatch):
    session.add(_book("A1", series_asin="S1", series_name="The Series", downloaded=True))
    session.commit()

    async def fake_get_series_books(client_session, series_asin, region=None):
        return []  # simulate an Audible lookup failure

    monkeypatch.setattr(
        "app.internal.audible.series_gaps.get_series_books", fake_get_series_books
    )

    gaps, _ = await get_series_gaps(session, object(), user)
    assert gaps == []


async def test_non_downloaded_book_not_counted_as_owned(session, user, monkeypatch):
    """A requested-but-not-yet-downloaded book shouldn't count toward owned_count."""
    session.add(_book("A1", series_asin="S1", series_name="The Series", downloaded=False))
    session.commit()

    full_series = [_book("A1", series_asin="S1", series_name="The Series")]

    async def fake_get_series_books(client_session, series_asin, region=None):
        return full_series

    monkeypatch.setattr(
        "app.internal.audible.series_gaps.get_series_books", fake_get_series_books
    )

    gaps, _ = await get_series_gaps(session, object(), user)
    # A1 isn't downloaded, so it never enters owned_by_series and no gap is computed at all
    assert gaps == []
