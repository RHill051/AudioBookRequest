"""Unit tests for the SeriesGapTile cover-fan layout (fixed tile size, count-based overlap)."""

import re
from datetime import datetime

import pytest

from app.internal.audible.series_gaps import SeriesGap, SeriesGapSlot
from app.internal.models import Audiobook
from app.util.templates import catalog


def _book(asin: str, series_number: str | None = None):
    return Audiobook(
        asin=asin,
        title=f"Book {asin}",
        subtitle=None,
        authors=["An Author"],
        narrators=[],
        cover_image="https://example.com/cover.jpg",
        release_date=datetime(2020, 1, 1),
        runtime_length_min=600,
        series_asin="S1",
        series_name="The Series",
        series_number=series_number,
    )


def _gap(n: int, owned_count: int = 1) -> SeriesGap:
    slots = [
        SeriesGapSlot(
            book=_book(f"A{i}", str(i + 1)), owned=(i < owned_count), requested=False
        )
        for i in range(n)
    ]
    return SeriesGap(
        series_asin="S1",
        series_name="The Series",
        owned_count=owned_count,
        total_count=n,
        slots=slots,
    )


def _cover_widths(html: str) -> list[float]:
    return [float(w) for w in re.findall(r"width:\s*([\d.]+)%", html)]


@pytest.mark.parametrize("n", [1, 2, 3, 8, 12])
def test_renders_without_error_for_various_series_sizes(n: int):
    html = catalog.render("SeriesGapTile", gap=_gap(n))
    assert "The Series" in html


def test_cover_count_is_capped_at_eight():
    html = catalog.render("SeriesGapTile", gap=_gap(12))
    assert html.count('src="https://example.com/cover.jpg"') == 8


def test_single_cover_fills_full_width():
    html = catalog.render("SeriesGapTile", gap=_gap(1))
    widths = _cover_widths(html)
    assert widths == [100.0]


@pytest.mark.parametrize("n", [2, 3, 5, 8])
def test_cover_widths_span_exactly_the_tile_width(n: int):
    """Regardless of how many covers are shown, their combined overlapped span must be 100%."""
    html = catalog.render("SeriesGapTile", gap=_gap(n))
    widths = _cover_widths(html)
    margins = [float(m) for m in re.findall(r"margin-left:\s*-([\d.]+)%", html)]

    assert len(widths) == n
    assert len(margins) == n - 1  # no margin on the first cover

    width = widths[0]
    shift = margins[0] if margins else 0.0
    total_span = width + (n - 1) * (width - shift)
    assert total_span == pytest.approx(100.0, abs=0.01)


def test_missing_count_badge_shows_correct_number():
    html = catalog.render("SeriesGapTile", gap=_gap(7, owned_count=3))
    assert re.search(r"badge-error[^>]*>\s*4\s*<", html)


def test_missing_count_badge_hidden_when_fully_owned():
    html = catalog.render("SeriesGapTile", gap=_gap(3, owned_count=3))
    assert "badge-error" not in html
