"""Unit tests for computing series gaps against a user's owned/downloaded books."""

from datetime import datetime, timedelta

import pytest
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, SQLModel, create_engine, text

from app.internal.audible.series_gaps import (
    dismiss_book,
    get_dismissed_asins,
    get_dismissed_by,
    get_series_gaps,
    get_slot_status,
    undismiss_book,
)
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


@pytest.fixture
def other_user(session):
    u = User(username="bob", password="hash", group=GroupEnum.trusted)
    session.add(u)
    session.commit()
    return DetailedUser.model_validate(u, update={"login_type": LoginTypeEnum.forms})


@pytest.fixture
def admin_user(session):
    u = User(username="carol", password="hash", group=GroupEnum.admin)
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
    release_date: datetime | None = None,
):
    return Audiobook(
        asin=asin,
        title=f"Book {asin}",
        subtitle=None,
        authors=["An Author"],
        narrators=[],
        cover_image=None,
        release_date=release_date or datetime(2020, 1, 1),
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
        "A1",
        series_asin="S1",
        series_name="The Series",
        series_number="1",
        downloaded=True,
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
    assert {s.book.asin for s in gap.gap_slots} == {"A2", "A3"}
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
    missing = gap.gap_slots
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
        _book(
            "OTHER_EDITION_OF_BOOK1",
            series_asin="S1",
            series_name="The Series",
            series_number="1",
        ),
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
    assert {s.book.asin for s in gap.gap_slots} == {"A2"}
    # the representative edition at slot 1 is still shown, just marked owned
    owned_slot = next(s for s in gap.slots if s.book.asin == "OTHER_EDITION_OF_BOOK1")
    assert owned_slot.owned is True


async def test_non_downloaded_book_not_counted_as_owned(session, user, monkeypatch):
    """A requested-but-not-yet-downloaded book shouldn't count toward owned_count."""
    session.add(
        _book("A1", series_asin="S1", series_name="The Series", downloaded=False)
    )
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


# --- dismiss/undismiss primitives -------------------------------------------------


def test_dismiss_book_is_idempotent(session, user):
    session.add(_book("A1"))
    session.commit()

    dismiss_book(session, "A1", user.username)
    dismiss_book(
        session, "A1", user.username
    )  # calling twice shouldn't error or duplicate

    assert get_dismissed_asins(session, user.username) == {"A1"}


def test_undismiss_book_is_a_no_op_when_not_dismissed(session, user):
    session.add(_book("A1"))
    session.commit()

    undismiss_book(session, "A1", user.username)  # never dismissed; shouldn't error

    assert get_dismissed_asins(session, user.username) == set()


def test_undismiss_removes_the_dismissal(session, user):
    session.add(_book("A1"))
    session.commit()

    dismiss_book(session, "A1", user.username)
    undismiss_book(session, "A1", user.username)

    assert get_dismissed_asins(session, user.username) == set()


def test_dismissals_are_isolated_per_user(session, user, other_user):
    session.add(_book("A1"))
    session.commit()

    dismiss_book(session, "A1", user.username)

    assert get_dismissed_asins(session, user.username) == {"A1"}
    assert get_dismissed_asins(session, other_user.username) == set()


def test_get_dismissed_by_maps_asin_to_usernames(session, user, other_user):
    session.add(_book("A1"))
    session.add(_book("A2"))
    session.commit()

    dismiss_book(session, "A1", user.username)
    dismiss_book(session, "A1", other_user.username)
    dismiss_book(session, "A2", other_user.username)

    result = get_dismissed_by(session, ["A1", "A2"])
    assert set(result["A1"]) == {user.username, other_user.username}
    assert result["A2"] == [other_user.username]


def test_dismiss_book_requires_an_existing_audiobook_row():
    """
    Regression test: DismissedSeriesBook.asin has a foreign key to audiobook.asin.
    A book that's only ever appeared as a "missing" slot -- never requested, never
    backfilled -- has no Audiobook row yet, so callers (the dismiss endpoint) must
    fetch and persist one before calling dismiss_book, or this raises. Caught live:
    dismissing a never-requested book 500'd until the endpoint was fixed to ensure
    the row exists first.
    """
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as fk_session:
        fk_session.execute(text("PRAGMA foreign_keys=ON"))
        fk_session.add(User(username="alice", password="hash", group=GroupEnum.trusted))
        fk_session.commit()

        with pytest.raises(IntegrityError):
            dismiss_book(fk_session, "NEVER_PERSISTED_ASIN", "alice")


# --- get_slot_status ----------------------------------------------------------


def test_get_slot_status_returns_none_for_unknown_asin(session, user):
    assert get_slot_status(session, "DOES_NOT_EXIST", user) is None


def test_get_slot_status_reflects_dismissed_and_requested(session, user):
    session.add(_book("A1"))
    session.add(AudiobookRequest(asin="A1", user_username=user.username))
    session.commit()
    dismiss_book(session, "A1", user.username)

    slot = get_slot_status(session, "A1", user)

    assert slot is not None
    assert slot.owned is False
    assert slot.requested is True
    assert slot.dismissed is True


def test_get_slot_status_only_names_names_for_admins(
    session, user, other_user, admin_user
):
    session.add(_book("A1"))
    session.commit()
    dismiss_book(session, "A1", other_user.username)

    # the requesting user isn't the one who dismissed it, and isn't admin: no names
    regular_view = get_slot_status(session, "A1", user)
    assert regular_view is not None
    assert regular_view.dismissed_by == []

    admin_view = get_slot_status(session, "A1", admin_user)
    assert admin_view is not None
    assert admin_view.dismissed_by == [other_user.username]


# --- dismiss/upcoming integrated into get_series_gaps --------------------------


async def test_dismissed_book_excluded_from_gap_count_but_still_shown(
    session, user, monkeypatch
):
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

    full_series = [
        _book("A1", series_asin="S1", series_name="The Series", series_number="1"),
        _book("A2", series_asin="S1", series_name="The Series", series_number="2"),
        _book("A3", series_asin="S1", series_name="The Series", series_number="3"),
    ]

    async def fake_get_series_books(client_session, series_asin, region=None):
        return full_series

    monkeypatch.setattr(
        "app.internal.audible.series_gaps.get_series_books", fake_get_series_books
    )
    dismiss_book(session, "A2", user.username)

    gaps, _ = await get_series_gaps(session, object(), user)

    assert len(gaps) == 1
    gap = gaps[0]
    # A2 is dismissed so it's not a "gap" anymore -- only A3 counts
    assert gap.gap_count == 1
    assert {s.book.asin for s in gap.gap_slots} == {"A3"}
    # but it's still visible in the full slot list so it can be un-dismissed
    dismissed_slot = next(s for s in gap.slots if s.book.asin == "A2")
    assert dismissed_slot.dismissed is True


async def test_series_drops_off_list_when_only_gap_is_dismissed(
    session, user, monkeypatch
):
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

    full_series = [
        _book("A1", series_asin="S1", series_name="The Series", series_number="1"),
        _book("A2", series_asin="S1", series_name="The Series", series_number="2"),
    ]

    async def fake_get_series_books(client_session, series_asin, region=None):
        return full_series

    monkeypatch.setattr(
        "app.internal.audible.series_gaps.get_series_books", fake_get_series_books
    )
    dismiss_book(session, "A2", user.username)

    gaps, _ = await get_series_gaps(session, object(), user)
    assert gaps == []


async def test_dismissal_is_personal_to_the_viewer(
    session, user, other_user, monkeypatch
):
    """Bob dismissing a book must not affect what Alice sees as missing."""
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

    full_series = [
        _book("A1", series_asin="S1", series_name="The Series", series_number="1"),
        _book("A2", series_asin="S1", series_name="The Series", series_number="2"),
    ]

    async def fake_get_series_books(client_session, series_asin, region=None):
        return full_series

    monkeypatch.setattr(
        "app.internal.audible.series_gaps.get_series_books", fake_get_series_books
    )
    dismiss_book(session, "A2", other_user.username)

    alice_gaps, _ = await get_series_gaps(session, object(), user)
    assert len(alice_gaps) == 1
    assert alice_gaps[0].gap_count == 1  # Alice never dismissed it, still a gap for her

    bob_gaps, _ = await get_series_gaps(session, object(), other_user)
    assert bob_gaps == []  # Bob dismissed the only gap, so the series drops for him


async def test_upcoming_book_excluded_from_gap_count(session, user, monkeypatch):
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

    future_release = datetime.now() + timedelta(days=90)
    full_series = [
        _book("A1", series_asin="S1", series_name="The Series", series_number="1"),
        _book(
            "A2",
            series_asin="S1",
            series_name="The Series",
            series_number="2",
            release_date=future_release,
        ),
        _book("A3", series_asin="S1", series_name="The Series", series_number="3"),
    ]

    async def fake_get_series_books(client_session, series_asin, region=None):
        return full_series

    monkeypatch.setattr(
        "app.internal.audible.series_gaps.get_series_books", fake_get_series_books
    )

    gaps, _ = await get_series_gaps(session, object(), user)

    assert len(gaps) == 1
    gap = gaps[0]
    assert gap.gap_count == 1  # only A3 is an actionable gap; A2 isn't out yet
    upcoming_slot = next(s for s in gap.slots if s.book.asin == "A2")
    assert upcoming_slot.upcoming is True
    assert upcoming_slot.dismissed is False


async def test_series_drops_off_list_when_only_gap_is_upcoming(
    session, user, monkeypatch
):
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

    future_release = datetime.now() + timedelta(days=90)
    full_series = [
        _book("A1", series_asin="S1", series_name="The Series", series_number="1"),
        _book(
            "A2",
            series_asin="S1",
            series_name="The Series",
            series_number="2",
            release_date=future_release,
        ),
    ]

    async def fake_get_series_books(client_session, series_asin, region=None):
        return full_series

    monkeypatch.setattr(
        "app.internal.audible.series_gaps.get_series_books", fake_get_series_books
    )

    gaps, _ = await get_series_gaps(session, object(), user)
    assert gaps == []
