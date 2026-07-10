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
        relationships: list[_Relationship] = []

    product: _Product


async def get_series_books(
    client_session: ClientSession,
    series_asin: str,
    audible_region: audible_region_type | None = None,
) -> list[Audiobook]:
    """
    Fetch all books in a series from Audible, sorted by their sequence position.

    Step 1 — fetch the series product to get an ordered list of child ASINs.
    Step 2 — concurrently fetch each book individually (batch params are ignored by the API).
    Results are cached for one week.
    """
    if audible_region is None:
        audible_region = get_region_from_settings()

    cache_key = _SeriesCacheKey(series_asin=series_asin, region=audible_region)
    cached = _series_cache.get(cache_key)
    if cached and time.time() - cached.timestamp < REFETCH_TTL:
        return cached.value

    base = f"https://api.audible{audible_regions[audible_region]}/1.0/catalog/products"

    # Step 1: fetch series product to get ordered child ASINs
    try:
        async with client_session.get(
            f"{base}/{series_asin}",
            params={"response_groups": "relationships"},
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
        try:
            seq = float(r.sequence) if r.sequence else 9999.0
        except ValueError:
            seq = 9999.0
        return (sort_pos, seq)

    children.sort(key=_sort_key)

    seen_asins: set[str] = set()
    ordered_asins: list[str] = []
    for child in children:
        if child.asin not in seen_asins:
            seen_asins.add(child.asin)
            ordered_asins.append(child.asin)

    if not ordered_asins:
        return []

    # Step 2: concurrently fetch each book individually (batch endpoints not supported)
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

    results = await asyncio.gather(*[_fetch(asin) for asin in ordered_asins])
    asin_to_book = {b.asin: b for b in results if b is not None}

    # Re-apply step-1 ordering (gather results are unordered by wall-clock)
    books = [asin_to_book[asin] for asin in ordered_asins if asin in asin_to_book]

    _series_cache[cache_key] = CacheResult(value=books, timestamp=time.time())
    logger.debug(
        "Series books fetched",
        series_asin=series_asin,
        region=audible_region,
        count=len(books),
    )
    return books
