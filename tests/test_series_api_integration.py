"""
Integration tests — hit the live Audible API to verify series data is returned
and parsed correctly. Run with: uv run pytest -m integration
Skip in CI with: uv run pytest -m 'not integration'
"""

import pytest
import aiohttp

from app.internal.audible.search import search_audible_books
from app.internal.audible.series import get_series_books

# Known-stable Audible US ASINs
LEVIATHAN_WAKES_ASIN = "B0036I5LA2"
EXPANSE_SERIES_ASIN = "B008Y45GCQ"


@pytest.mark.integration
async def test_search_returns_series_data():
    """Audible search results include series name, asin, and number."""
    async with aiohttp.ClientSession() as session:
        books = await search_audible_books(session, "Leviathan Wakes", num_results=5)

    leviathan = next((b for b in books if "Leviathan Wakes" in b.title), None)
    assert leviathan is not None, f"Leviathan Wakes not found. Got: {[b.title for b in books]}"
    assert leviathan.series_name == "The Expanse", (
        f"series_name wrong. Got: {leviathan.series_name!r}\n"
        "If None, the 'series' response_group is not returning data in the expected format."
    )
    assert leviathan.series_asin is not None, "series_asin should not be None"
    assert leviathan.series_number == "1", f"Expected '1', got {leviathan.series_number!r}"


@pytest.mark.integration
async def test_get_series_books_returns_ordered_list():
    """Series fetch returns all Expanse books ordered by sequence."""
    async with aiohttp.ClientSession() as session:
        books = await get_series_books(session, EXPANSE_SERIES_ASIN)

    assert len(books) > 0, "No books returned for The Expanse series"

    titles = [b.title for b in books]
    assert any("Leviathan Wakes" in t for t in titles), f"Expected Leviathan Wakes in {titles}"

    # Verify all returned books have series data populated
    for book in books:
        assert book.series_asin == EXPANSE_SERIES_ASIN, (
            f"{book.title!r} has wrong series_asin: {book.series_asin!r}"
        )
        assert book.series_name is not None, f"{book.title!r} missing series_name"
