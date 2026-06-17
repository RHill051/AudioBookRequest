import time

from aiohttp import ClientSession
from pydantic import BaseModel

from app.internal.audible.search import CacheResult
from app.internal.audible.types import (
    REFETCH_TTL,
    AudibleSearchResponse,
    audible_region_type,
    audible_regions,
    get_region_from_settings,
)
from app.internal.models import Audiobook
from app.util.log import logger


class _SeriesCacheKey(BaseModel, frozen=True):
    series_asin: str
    region: str


_series_cache: dict[_SeriesCacheKey, CacheResult[list[Audiobook]]] = {}


async def get_series_books(
    client_session: ClientSession,
    series_asin: str,
    audible_region: audible_region_type | None = None,
) -> list[Audiobook]:
    """Fetch all books belonging to a series from Audible, sorted by sequence position."""
    if audible_region is None:
        audible_region = get_region_from_settings()

    cache_key = _SeriesCacheKey(series_asin=series_asin, region=audible_region)
    cached = _series_cache.get(cache_key)
    if cached and time.time() - cached.timestamp < REFETCH_TTL:
        return cached.value

    base_url = (
        f"https://api.audible{audible_regions[audible_region]}/1.0/catalog/products"
    )
    params = {
        "num_results": 50,
        "products_sort_by": "SeriesSequence",
        "series_asin": series_asin,
        "response_groups": ["media", "series"],
    }

    try:
        async with client_session.get(base_url, params=params) as response:
            response.raise_for_status()
            audible_response = AudibleSearchResponse.model_validate(
                await response.json()
            )
    except Exception as e:
        logger.error(
            "Failed to fetch series books from Audible",
            series_asin=series_asin,
            region=audible_region,
            error=str(e),
        )
        return []

    books = audible_response.audiobooks()
    _series_cache[cache_key] = CacheResult(value=books, timestamp=time.time())

    logger.debug(
        "Series books fetched",
        series_asin=series_asin,
        region=audible_region,
        count=len(books),
    )
    return books
