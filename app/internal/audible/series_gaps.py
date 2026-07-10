from aiohttp import ClientSession
from pydantic import BaseModel
from sqlmodel import Session, col, select

from app.internal.audible.series import get_series_books, parse_series_sequence
from app.internal.audible.series_backfill import backfill_missing_series_data
from app.internal.audible.types import audible_region_type, get_region_from_settings
from app.internal.audiobookshelf.client import abs_get_all_library_asins
from app.internal.audiobookshelf.config import abs_config
from app.internal.auth.authentication import DetailedUser
from app.internal.models import Audiobook, AudiobookRequest


class SeriesGap(BaseModel):
    series_asin: str
    series_name: str
    owned_count: int
    total_count: int
    missing_books: list[Audiobook]
    requested_asins: set[str]


async def get_series_gaps(
    session: Session,
    client_session: ClientSession,
    user: DetailedUser,
    audible_region: audible_region_type | None = None,
    backfill_limit: int = 25,
) -> tuple[list[SeriesGap], bool]:
    """
    Find series that you own at least one book from but not all of.

    Returns the list of gaps (sorted by series name) plus a flag indicating whether
    a backfill lookup ran — if so, there may be more series to discover once the
    rest of the library has been checked on a later call.
    """
    if audible_region is None:
        audible_region = get_region_from_settings()

    backfilled = 0
    if abs_config.is_valid(session):
        library_asins = await abs_get_all_library_asins(session, client_session)
        backfilled = await backfill_missing_series_data(
            session,
            client_session,
            library_asins,
            audible_region,
            max_lookups=backfill_limit,
        )

    owned = session.exec(
        select(Audiobook).where(
            Audiobook.downloaded,
            col(Audiobook.series_asin).is_not(None),
        )
    ).all()

    # Different editions of the same book have different ASINs (narrator, publisher,
    # abridged/unabridged...), so "do I own this book" is decided by series position
    # (sequence number) rather than exact ASIN equality.
    owned_by_series: dict[str, set[float]] = {}
    series_names: dict[str, str] = {}
    for book in owned:
        assert book.series_asin is not None
        owned_sequences = owned_by_series.setdefault(book.series_asin, set())
        seq = parse_series_sequence(book.series_number)
        if seq is not None:
            owned_sequences.add(seq)
        if book.series_name:
            series_names[book.series_asin] = book.series_name

    if not owned_by_series:
        return [], backfilled > 0

    requested_asins = {
        r.asin
        for r in session.exec(
            select(AudiobookRequest).where(
                AudiobookRequest.user_username == user.username
            )
        ).all()
    }

    gaps: list[SeriesGap] = []
    for series_asin, owned_sequences in owned_by_series.items():
        full_list = await get_series_books(client_session, series_asin, audible_region)
        if not full_list:
            continue

        missing = [
            b
            for b in full_list
            if parse_series_sequence(b.series_number) not in owned_sequences
        ]
        if not missing:
            continue

        missing_asins = {b.asin for b in missing}
        gaps.append(
            SeriesGap(
                series_asin=series_asin,
                series_name=full_list[0].series_name
                or series_names.get(series_asin, "Series"),
                owned_count=len(full_list) - len(missing),
                total_count=len(full_list),
                missing_books=missing,
                requested_asins=requested_asins & missing_asins,
            )
        )

    gaps.sort(key=lambda g: g.series_name.lower())
    return gaps, backfilled > 0
