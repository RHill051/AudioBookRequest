import asyncio
import time

from aiohttp import ClientSession
from pydantic import BaseModel

from app.internal.audible.search import CacheResult
from app.internal.audible.single import get_single_book
from app.internal.audible.types import (
    REFETCH_TTL,
    audible_region_type,
    audible_regions,
    get_region_from_settings,
)
from app.internal.models import Audiobook
from app.util.log import logger

_MAX_CONCURRENT = 5  # limit simultaneous Audible API calls


class _SeriesCacheKey(BaseModel, frozen=True):
    series_asin: str
    region: str


_series_cache: dict[_SeriesCacheKey, CacheResult[list[Audiobook]]] = {}


class _Relationship(BaseModel):
    asin: str
    relationship_to_product: str
    relationship_type: str
    sequence: str = ""
    sort: str = "0"


class _SeriesProductResponse(BaseModel):
    class _Product(BaseModel):
        title: str | None = None
        relationships: list[_Relationship] = []

    product: _Product


def parse_series_sequence(sequence: str | None) -> float | None:
    """
    Parse a raw Audible series sequence string into a comparable number.

    Returns None for anything that isn't a single numbered position: an empty
    string, a range like "1-7" (used for box sets/compilations), or anything
    else that doesn't parse as a plain number.
    """
    if not sequence:
        return None
    sequence = sequence.strip()
    if not sequence or "-" in sequence:
        return None
    try:
        return float(sequence)
    except ValueError:
        return None


def _format_sequence(sequence: float) -> str:
    if sequence == int(sequence):
        return str(int(sequence))
    return str(sequence)


async def get_series_books(
    client_session: ClientSession,
    series_asin: str,
    audible_region: audible_region_type | None = None,
) -> list[Audiobook]:
    """
    Fetch one book per numbered position in a series, sorted by sequence.

    A series' "relationships" on Audible list every edition/narration tied to it
    (different narrators, publishers, abridged/unabridged, box sets) rather than
    one entry per book, so we group children by their sequence number and keep a
    single representative ASIN per position. Box sets and other unnumbered extras
    (a range like "1-7", or no sequence at all) are dropped entirely, since they
    aren't an individual missing book.

    Step 1 — fetch the series product to get its title and ordered child ASINs.
    Step 2 — concurrently fetch full details for one representative book per slot.
    Results are cached for one week.
    """
    if audible_region is None:
        audible_region = get_region_from_settings()

    cache_key = _SeriesCacheKey(series_asin=series_asin, region=audible_region)
    cached = _series_cache.get(cache_key)
    if cached and time.time() - cached.timestamp < REFETCH_TTL:
        return cached.value

    base = f"https://api.audible{audible_regions[audible_region]}/1.0/catalog/products"

    # Step 1: fetch series product to get its title and ordered child ASINs
    try:
        async with client_session.get(
            f"{base}/{series_asin}",
            params={"response_groups": "relationships,media"},
        ) as resp:
            resp.raise_for_status()
            series_data = _SeriesProductResponse.model_validate(await resp.json())
    except Exception as e:
        logger.error(
            "Failed to fetch series product from Audible",
            series_asin=series_asin,
            region=audible_region,
            error=str(e),
        )
        return []

    series_name = series_data.product.title or "Series"

    children = [
        r
        for r in series_data.product.relationships
        if r.relationship_to_product == "child" and r.relationship_type == "series"
    ]

    def _sort_key(r: _Relationship) -> tuple[int, float]:
        try:
            sort_pos = int(r.sort)
        except ValueError:
            sort_pos = 9999
        seq = parse_series_sequence(r.sequence)
        return (sort_pos, seq if seq is not None else 9999.0)

    children.sort(key=_sort_key)

    # One representative ASIN per numbered slot; first one encountered (in sort
    # order above) wins. Unnumbered/range entries (box sets, compilations) are
    # skipped since they aren't an individual book position.
    slots: dict[float, str] = {}
    for child in children:
        seq = parse_series_sequence(child.sequence)
        if seq is None or seq in slots:
            continue
        slots[seq] = child.asin

    if not slots:
        return []

    ordered_sequences = sorted(slots.keys())
    representative_asins = [slots[seq] for seq in ordered_sequences]

    # Step 2: concurrently fetch full details for each representative book
    semaphore = asyncio.Semaphore(_MAX_CONCURRENT)

    async def _fetch(asin: str) -> Audiobook | None:
        async with semaphore:
            try:
                return await get_single_book(client_session, asin, audible_region)
            except Exception as e:
                logger.warning(
                    "Failed to fetch series book",
                    asin=asin,
                    error=str(e),
                )
                return None

    results = await asyncio.gather(*[_fetch(asin) for asin in representative_asins])

    books: list[Audiobook] = []
    for seq, fetched in zip(ordered_sequences, results):
        if fetched is None:
            continue
        # A book's own product data may list multiple series (e.g. both
        # "Publication Order" and "Author's Preferred Order"); pin the fields to
        # the series we're actually browsing rather than whichever the product
        # happened to list first.
        fetched.series_asin = series_asin
        fetched.series_name = series_name
        fetched.series_number = _format_sequence(seq)
        books.append(fetched)

    _series_cache[cache_key] = CacheResult(value=books, timestamp=time.time())
    logger.debug(
        "Series books fetched",
        series_asin=series_asin,
        region=audible_region,
        count=len(books),
    )
    return books
