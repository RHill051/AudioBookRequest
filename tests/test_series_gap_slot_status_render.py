"""Unit tests for the SeriesGapSlotStatus component's four visual states."""

from datetime import datetime, timedelta

from app.internal.audible.series_gaps import SeriesGapSlot
from app.internal.models import Audiobook
from app.util.templates import catalog


def _book(asin: str = "A1", release_date: datetime | None = None):
    return Audiobook(
        asin=asin,
        title=f"Book {asin}",
        subtitle=None,
        authors=["An Author"],
        narrators=[],
        cover_image=None,
        release_date=release_date or datetime(2020, 1, 1),
        runtime_length_min=600,
        series_asin="S1",
        series_name="The Series",
        series_number="1",
    )


def test_upcoming_shows_release_date_and_no_actions():
    slot = SeriesGapSlot(
        book=_book(release_date=datetime(2027, 3, 1)),
        owned=False,
        requested=False,
        dismissed=False,
        upcoming=True,
    )
    html = catalog.render("SeriesGapSlotStatus", slot=slot, region="us")

    assert "Coming Mar 2027" in html
    assert "Request" not in html
    assert "Not interested" not in html


def test_dismissed_shows_undo_without_names_for_non_admin():
    slot = SeriesGapSlot(
        book=_book(),
        owned=False,
        requested=False,
        dismissed=True,
        dismissed_by=["bob"],
        upcoming=False,
    )
    html = catalog.render("SeriesGapSlotStatus", slot=slot, region="us", is_admin=False)

    assert "Not interested" in html
    assert "Undo" in html
    assert "bob" not in html


def test_dismissed_shows_names_for_admin():
    slot = SeriesGapSlot(
        book=_book(),
        owned=False,
        requested=False,
        dismissed=True,
        dismissed_by=["bob", "carol"],
        upcoming=False,
    )
    html = catalog.render("SeriesGapSlotStatus", slot=slot, region="us", is_admin=True)

    assert "bob, carol" in html


def test_requested_shows_badge_with_no_actions():
    slot = SeriesGapSlot(
        book=_book(), owned=False, requested=True, dismissed=False, upcoming=False
    )
    html = catalog.render("SeriesGapSlotStatus", slot=slot, region="us")

    assert "Requested" in html
    assert "Not interested" not in html
    assert "hx-post" not in html


def test_just_downloaded_overrides_requested_badge():
    slot = SeriesGapSlot(
        book=_book(), owned=False, requested=True, dismissed=False, upcoming=False
    )
    html = catalog.render(
        "SeriesGapSlotStatus", slot=slot, region="us", just_downloaded=True
    )

    assert "Downloaded" in html
    assert "Requested" not in html


def test_plain_gap_shows_request_and_dismiss_actions():
    slot = SeriesGapSlot(
        book=_book(), owned=False, requested=False, dismissed=False, upcoming=False
    )
    html = catalog.render("SeriesGapSlotStatus", slot=slot, region="us")

    assert "Request" in html
    assert "Not interested" in html
    assert "/series/hx-gap-request/A1" in html
    assert "/series/hx-dismiss/A1" in html
