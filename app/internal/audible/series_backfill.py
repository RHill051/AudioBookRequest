import asyncio
import time
from datetime import datetime, timedelta

from aiohttp import ClientSession
from sqlmodel import Session, col, select

from app.internal.audible.single import get_single_book
from app.internal.audible.types import (
    REFETCH_TTL,
    audible_region_type,
    get_region_from_settings,
)
from app.internal.models import Audiobook
from app.util.log import logger

_MAX_CONCURRENT = 5  # limit simultaneous Audible API calls

_FAILED_LOOKUP_RETRY_SECONDS = 60 * 60 * 24  # 1 day

# ASINs with no persisted row whose Audible lookup failed. Tracked separately from
# series_checked_at (which lives on the row) so a failing ASIN doesn't get retried
# on every single call forever — but with a shorter cooldown than a successful
# check, since the failure may be transient (region mismatch, rate limit, etc).
_failed_lookups: dict[str, float] = {}


async def backfill_missing_series_data(
    session: Session,
    client_session: ClientSession,
    asins: list[str],
    audible_region: audible_region_type | None = None,
    max_lookups: int = 25,
) -> int:
    """
    Look up series data on Audible for any of the given ASINs that don't yet have it
    cached, and persist the result to the audiobook table (creating rows for ASINs
    that don't have one, e.g. books added to the library outside of ABR).

    A book with no series is still marked as checked so it isn't re-queried every
    call; `max_lookups` caps how many network calls a single invocation makes so a
    large library backfills gradually across repeated calls rather than all at once.

    Returns the number of ASINs looked up.
    """
    if audible_region is None:
        audible_region = get_region_from_settings()

    unique_asins = list(dict.fromkeys(asins))
    if not unique_asins:
        return 0

    existing = {
        b.asin: b
        for b in session.exec(
            select(Audiobook).where(col(Audiobook.asin).in_(unique_asins))
        ).all()
    }

    def _needs_check(asin: str) -> bool:
        row = existing.get(asin)
        if row is not None:
            return row.series_checked_at is None or row.series_checked_at < stale_cutoff
        failed_at = _failed_lookups.get(asin)
        return failed_at is None or time.time() - failed_at > _FAILED_LOOKUP_RETRY_SECONDS

    stale_cutoff = datetime.now() - timedelta(seconds=REFETCH_TTL)
    to_check = [asin for asin in unique_asins if _needs_check(asin)][:max_lookups]

    if not to_check:
        return 0

    semaphore = asyncio.Semaphore(_MAX_CONCURRENT)

    async def _fetch(asin: str) -> Audiobook | None:
        async with semaphore:
            try:
                return await get_single_book(client_session, asin, audible_region)
            except Exception as e:
                logger.warning(
                    "Failed to backfill series data for library book",
                    asin=asin,
                    error=str(e),
                )
                return None

    results = await asyncio.gather(*[_fetch(asin) for asin in to_check])

    checked_at = datetime.now()
    for asin, fetched in zip(to_check, results):
        row = existing.get(asin)
        if row is None:
            if fetched is None:
                _failed_lookups[asin] = time.time()
                continue  # can't resolve on Audible and nothing cached locally: skip
            row = fetched
            row.downloaded = True
            row.downloaded_at = checked_at
        elif fetched is not None:
            row.series_asin = fetched.series_asin
            row.series_name = fetched.series_name
            row.series_number = fetched.series_number
        row.series_checked_at = checked_at
        session.add(row)

    session.commit()
    logger.debug(
        "Backfilled series data for library books",
        checked=len(to_check),
        region=audible_region,
    )
    return len(to_check)
