"""Unit tests for the SeriesGapNavBadge component shown on the navbar icon."""

from app.util.templates import catalog


def test_no_badge_when_count_is_zero():
    html = catalog.render("SeriesGapNavBadge", total_count=0)
    assert "badge" not in html


def test_shows_exact_count_under_a_hundred():
    html = catalog.render("SeriesGapNavBadge", total_count=7)
    assert "7" in html
    assert "99+" not in html


def test_caps_display_at_99_plus():
    html = catalog.render("SeriesGapNavBadge", total_count=142)
    assert "99+" in html
    assert "142" not in html
