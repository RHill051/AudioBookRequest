from datetime import datetime

from aiohttp import ClientSession
from pydantic import BaseModel
from sqlmodel import Session, col, select

from app.internal.audible.series import get_series_books, parse_series_sequence
from app.internal.audible.series_backfill import backfill_missing_series_data
from app.internal.audible.types import audible_region_type, get_region_from_settings
from app.internal.audiobookshelf.client import abs_get_all_library_asins
from app.internal.audiobookshelf.config import abs_config
from app.internal.auth.authentication import DetailedUser
from app.internal.models import Audiobook, AudiobookRequest, DismissedSeriesBook


class SeriesGapSlot(BaseModel):
    book: Audiobook
    owned: bool
    requested: bool
    dismissed: bool
    dismissed_by: list[str] = []
    upcoming: bool

    @property
    def actionable(self) -> bool:
        """A real, current gap: not owned, not opted out of, not unreleased yet."""
        return not self.owned and not self.dismissed and not self.upcoming


class SeriesGap(BaseModel):
    series_asin: str
    series_name: str
    owned_count: int
    total_count: int
    slots: list[SeriesGapSlot]

    @property
    def gap_slots(self) -> list[SeriesGapSlot]:
        return [s for s in self.slots if s.actionable]

    @property
    def gap_count(self) -> int:
        return len(self.gap_slots)

    @property
    def requestable_slots(self) -> list[SeriesGapSlot]:
        return [s for s in self.gap_slots if not s.requested]

    @property
    def requestable_count(self) -> int:
        return len(self.requestable_slots)


def get_dismissed_asins(session: Session, username: str) -> set[str]:
    return set(
        session.exec(
            select(DismissedSeriesBook.asin).where(
                DismissedSeriesBook.user_username == username
            )
        ).all()
    )


def get_dismissed_by(session: Session, asins: list[str]) -> dict[str, list[str]]:
    """asin -> usernames who dismissed it. Meant for admin-only display."""
    if not asins:
        return {}
    rows = session.exec(
        select(DismissedSeriesBook).where(col(DismissedSeriesBook.asin).in_(asins))
    ).all()
    result: dict[str, list[str]] = {}
    for row in rows:
        result.setdefault(row.asin, []).append(row.user_username)
    return result


def dismiss_book(session: Session, asin: str, username: str) -> None:
    already = session.exec(
        select(DismissedSeriesBook).where(
            DismissedSeriesBook.asin == asin,
            DismissedSeriesBook.user_username == username,
        )
    ).first()
    if already is None:
        session.add(DismissedSeriesBook(asin=asin, user_username=username))
        session.commit()


def undismiss_book(session: Session, asin: str, username: str) -> None:
    existing = session.exec(
        select(DismissedSeriesBook).where(
            DismissedSeriesBook.asin == asin,
            DismissedSeriesBook.user_username == username,
        )
    ).first()
    if existing is not None:
        session.delete(existing)
        session.commit()


def get_slot_status(
    session: Session, asin: str, user: DetailedUser
) -> SeriesGapSlot | None:
    """
    Rebuild a single slot's status (used after a dismiss/undismiss/request action to
    re-render just that slot). Only meant for non-owned slots -- owned is always False
    here since owned books never show request/dismiss controls in the first place.
    """
    book = session.get(Audiobook, asin)
    if book is None:
        return None

    requested = (
        session.exec(
            select(AudiobookRequest).where(
                AudiobookRequest.asin == asin,
                AudiobookRequest.user_username == user.username,
            )
        ).first()
        is not None
    )
    dismissed_by = (
        get_dismissed_by(session, [asin]).get(asin, []) if user.is_admin() else []
    )
    dismissed = asin in get_dismissed_asins(session, user.username)

    return SeriesGapSlot(
        book=book,
        owned=False,
        requested=requested,
        dismissed=dismissed,
        dismissed_by=dismissed_by,
        upcoming=book.release_date > datetime.now(),
    )


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
    dismissed_asins = get_dismissed_asins(session, user.username)
    is_admin = user.is_admin()
    now = datetime.now()

    gaps: list[SeriesGap] = []
    for series_asin, owned_sequences in owned_by_series.items():
        full_list = await get_series_books(client_session, series_asin, audible_region)
        if not full_list:
            continue

        dismissed_by_map = (
            get_dismissed_by(session, [b.asin for b in full_list]) if is_admin else {}
        )

        slots: list[SeriesGapSlot] = []
        for b in full_list:
            owned_book = parse_series_sequence(b.series_number) in owned_sequences
            slots.append(
                SeriesGapSlot(
                    book=b,
                    owned=owned_book,
                    requested=b.asin in requested_asins,
                    dismissed=(not owned_book) and b.asin in dismissed_asins,
                    dismissed_by=dismissed_by_map.get(b.asin, []),
                    upcoming=(not owned_book) and b.release_date > now,
                )
            )

        owned_count = sum(1 for s in slots if s.owned)
        gap_count = sum(1 for s in slots if s.actionable)
        if gap_count == 0:
            continue  # nothing left that's owned, dismissed, or not yet released

        gaps.append(
            SeriesGap(
                series_asin=series_asin,
                series_name=full_list[0].series_name
                or series_names.get(series_asin, "Series"),
                owned_count=owned_count,
                total_count=len(slots),
                slots=slots,
            )
        )

    gaps.sort(key=lambda g: g.series_name.lower())
    return gaps, backfilled > 0
