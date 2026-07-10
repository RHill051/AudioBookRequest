from typing import Annotated

from aiohttp import ClientSession
from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, Security
from fastapi.responses import HTMLResponse
from sqlmodel import Session, col, select

from app.internal.audible.series import get_series_books
from app.internal.audible.series_gaps import get_series_gaps
from app.internal.audible.types import audible_region_type, get_region_from_settings
from app.internal.audiobookshelf.client import abs_book_in_index, abs_get_library_index
from app.internal.audiobookshelf.config import abs_config
from app.internal.auth.authentication import AnyAuth, DetailedUser
from app.internal.models import Audiobook, AudiobookRequest
from app.internal.ranking.quality import quality_config
from app.routers.api.requests import create_request
from app.util.connection import get_connection
from app.util.db import get_session
from app.util.log import logger
from app.util.templates import catalog_response
from app.util.toast import ToastException

router = APIRouter(prefix="/series")


@router.get("/hx-drawer/{series_asin}")
async def series_drawer(
    series_asin: str,
    session: Annotated[Session, Depends(get_session)],
    client_session: Annotated[ClientSession, Depends(get_connection)],
    user: Annotated[DetailedUser, Security(AnyAuth())],
    region: audible_region_type | None = None,
):
    if region is None:
        region = get_region_from_settings()

    books = await get_series_books(
        client_session=client_session,
        series_asin=series_asin,
        audible_region=region,
    )

    merged_books: list[Audiobook] = [session.merge(b) for b in books]

    asins = {b.asin for b in merged_books}
    existing_requests = session.exec(
        select(AudiobookRequest).where(
            col(AudiobookRequest.asin).in_(asins),
            AudiobookRequest.user_username == user.username,
        )
    ).all()
    requested_asins = {r.asin for r in existing_requests}

    abs_configured = abs_config.is_valid(session)
    library_asins: set[str] = set()
    if abs_configured:
        index = await abs_get_library_index(session, client_session)
        if index:
            library_asins = {
                b.asin for b in merged_books if abs_book_in_index(b, index)
            }

    series_name = next(
        (b.series_name for b in merged_books if b.series_name),
        "Series",
    )

    return catalog_response(
        "SeriesDrawer",
        books=merged_books,
        series_name=series_name,
        series_asin=series_asin,
        requested_asins=requested_asins,
        library_asins=library_asins,
        abs_configured=abs_configured,
        region=region,
        user=user,
        auto_start_download=quality_config.get_auto_download(session),
    )


@router.post("/hx-request/{asin}")
async def series_request_book(
    asin: str,
    session: Annotated[Session, Depends(get_session)],
    client_session: Annotated[ClientSession, Depends(get_connection)],
    background_task: BackgroundTasks,
    user: Annotated[DetailedUser, Security(AnyAuth())],
    region: Annotated[audible_region_type | None, Form()] = None,
    auto_start_download: Annotated[bool, Form()] = False,
):
    """Request a book from the series drawer; returns just a status badge to swap inline."""
    try:
        await create_request(
            asin_or_uuid=asin,
            session=session,
            client_session=client_session,
            background_task=background_task,
            user=user,
            region=region,
        )
    except HTTPException as e:
        logger.warning(e.detail, asin=asin)
        raise ToastException(e.detail) from e

    if auto_start_download and user.can_download():
        badge = '<span class="badge badge-success badge-sm text-xs whitespace-nowrap">Downloaded</span>'
    else:
        badge = '<span class="badge badge-info badge-sm text-xs whitespace-nowrap">Requested</span>'

    return HTMLResponse(badge)


@router.get("/gaps")
async def series_gaps_page(
    session: Annotated[Session, Depends(get_session)],
    client_session: Annotated[ClientSession, Depends(get_connection)],
    user: Annotated[DetailedUser, Security(AnyAuth())],
    region: audible_region_type | None = None,
):
    if region is None:
        region = get_region_from_settings()

    gaps, still_discovering = await get_series_gaps(
        session=session,
        client_session=client_session,
        user=user,
        audible_region=region,
    )

    return catalog_response(
        "Series.Gaps",
        gaps=gaps,
        still_discovering=still_discovering,
        user=user,
    )


@router.get("/hx-gap-detail/{series_asin}")
async def series_gap_detail(
    series_asin: str,
    session: Annotated[Session, Depends(get_session)],
    client_session: Annotated[ClientSession, Depends(get_connection)],
    user: Annotated[DetailedUser, Security(AnyAuth())],
    region: audible_region_type | None = None,
):
    if region is None:
        region = get_region_from_settings()

    gaps, _ = await get_series_gaps(
        session=session, client_session=client_session, user=user, audible_region=region
    )
    gap = next((g for g in gaps if g.series_asin == series_asin), None)
    if gap is None:
        raise ToastException(
            "That series is no longer missing any books",
            type="success",
            cause_refresh=True,
        )

    return catalog_response(
        "SeriesGapDetail",
        gap=gap,
        region=region,
        user=user,
        auto_start_download=quality_config.get_auto_download(session),
    )


@router.post("/hx-request-all/{series_asin}")
async def series_request_all(
    series_asin: str,
    session: Annotated[Session, Depends(get_session)],
    client_session: Annotated[ClientSession, Depends(get_connection)],
    background_task: BackgroundTasks,
    user: Annotated[DetailedUser, Security(AnyAuth())],
    region: Annotated[audible_region_type | None, Form()] = None,
):
    """Request every not-yet-requested missing book in a series; re-renders the detail view."""
    if region is None:
        region = get_region_from_settings()

    gaps, _ = await get_series_gaps(
        session=session, client_session=client_session, user=user, audible_region=region
    )
    gap = next((g for g in gaps if g.series_asin == series_asin), None)
    if gap is None:
        raise ToastException(
            "That series no longer has any missing books",
            type="success",
            cause_refresh=True,
        )

    for slot in gap.missing_slots:
        if slot.requested:
            continue
        try:
            await create_request(
                asin_or_uuid=slot.book.asin,
                session=session,
                client_session=client_session,
                background_task=background_task,
                user=user,
                region=region,
            )
        except HTTPException as e:
            logger.warning(e.detail, asin=slot.book.asin, series_asin=series_asin)

    gaps, _ = await get_series_gaps(
        session=session, client_session=client_session, user=user, audible_region=region
    )
    gap = next((g for g in gaps if g.series_asin == series_asin), None)
    if gap is None:
        raise ToastException(
            "All requested — that series is fully covered now",
            type="success",
            cause_refresh=True,
        )

    return catalog_response(
        "SeriesGapDetail",
        gap=gap,
        region=region,
        user=user,
        auto_start_download=quality_config.get_auto_download(session),
    )
