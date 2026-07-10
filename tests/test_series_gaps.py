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
    owned = _book(
        "A1", series_asin="S1", series_name="The Series", series_number="1", downloaded=True
    )
    session.add(owned)
    session.commit()

    full_series = [
        _book("A1", series_asin="S1", series_name="The Series", series_number="1"),
        _book("A2", series_asin="S1", series_name="The Series", series_number="2"),
        _book("A3", series_asin="S1", series_name="The Series", series_number="3"),
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
    assert {s.book.asin for s in gap.missing_slots} == {"A2", "A3"}
    assert gap.requestable_count == 2

    # slots preserve series order and each carries its own owned flag
    assert [s.book.asin for s in gap.slots] == ["A1", "A2", "A3"]
    assert [s.owned for s in gap.slots] == [True, False, False]


async def test_fully_owned_series_yields_no_gap(session, user, monkeypatch):
    for asin, number in (("A1", "1"), ("A2", "2")):
        session.add(
            _book(
                asin,
                series_asin="S1",
                series_name="The Series",
                series_number=number,
                downloaded=True,
            )
        )
    session.commit()

    full_series = [
        _book("A1", series_asin="S1", series_name="The Series", series_number="1"),
        _book("A2", series_asin="S1", series_name="The Series", series_number="2"),
    ]

    async def fake_get_series_books(client_session, series_asin, region=None):
        return full_series

    monkeypatch.setattr(
        "app.internal.audible.series_gaps.get_series_books", fake_get_series_books
    )

    gaps, _ = await get_series_gaps(session, object(), user)
    assert gaps == []


async def test_requested_missing_book_is_flagged(session, user, monkeypatch):
    session.add(
        _book(
            "A1",
            series_asin="S1",
            series_name="The Series",
            series_number="1",
            downloaded=True,
        )
    )
    session.add(
        _book(
            "A2",
            series_asin="S1",
            series_name="The Series",
            series_number="2",
            downloaded=False,
        )
    )
    session.add(AudiobookRequest(asin="A2", user_username="alice"))
    session.commit()

    full_series = [
        _book("A1", series_asin="S1", series_name="The Series", series_number="1"),
        _book("A2", series_asin="S1", series_name="The Series", series_number="2"),
    ]

    async def fake_get_series_books(client_session, series_asin, region=None):
        return full_series

    monkeypatch.setattr(
        "app.internal.audible.series_gaps.get_series_books", fake_get_series_books
    )

    gaps, _ = await get_series_gaps(session, object(), user)

    assert len(gaps) == 1
    gap = gaps[0]
    missing = gap.missing_slots
    assert len(missing) == 1
    assert missing[0].book.asin == "A2"
    assert missing[0].requested is True
    # already requested, so it shouldn't count toward what's left to request
    assert gap.requestable_count == 0


async def test_series_lookup_failure_is_skipped(session, user, monkeypatch):
    session.add(
        _book(
            "A1",
            series_asin="S1",
            series_name="The Series",
            series_number="1",
            downloaded=True,
        )
    )
    session.commit()

    async def fake_get_series_books(client_session, series_asin, region=None):
        return []  # simulate an Audible lookup failure

    monkeypatch.setattr(
        "app.internal.audible.series_gaps.get_series_books", fake_get_series_books
    )

    gaps, _ = await get_series_gaps(session, object(), user)
    assert gaps == []


async def test_owned_edition_with_different_asin_than_series_representative(
    session, user, monkeypatch
):
    """
    Regression test: Audible lists a different edition-ASIN per book than the one
    you actually own (different narrator/publisher). Ownership must be recognized
    by series position, not exact ASIN equality, or every book you own shows up as
    "missing" under whichever edition-ASIN Audible happened to return.
    """
    owned = _book(
        "OWNED_EDITION_OF_BOOK1",
        series_asin="S1",
        series_name="The Series",
        series_number="1",
        downloaded=True,
    )
    session.add(owned)
    session.commit()

    full_series = [
        # a different edition-ASIN of the same book you own, at the same position
        _book("OTHER_EDITION_OF_BOOK1", series_asin="S1", series_name="The Series", series_number="1"),
        _book("A2", series_asin="S1", series_name="The Series", series_number="2"),
    ]

    async def fake_get_series_books(client_session, series_asin, region=None):
        return full_series

    monkeypatch.setattr(
        "app.internal.audible.series_gaps.get_series_books", fake_get_series_books
    )

    gaps, _ = await get_series_gaps(session, object(), user)

    assert len(gaps) == 1
    gap = gaps[0]
    assert gap.owned_count == 1
    assert gap.total_count == 2
    assert {s.book.asin for s in gap.missing_slots} == {"A2"}
    # the representative edition at slot 1 is still shown, just marked owned
    owned_slot = next(s for s in gap.slots if s.book.asin == "OTHER_EDITION_OF_BOOK1")
    assert owned_slot.owned is True


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
