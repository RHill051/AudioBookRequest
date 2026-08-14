from datetime import datetime
from typing import Annotated

from aiohttp import ClientSession
from fastapi import APIRouter, Depends, HTTPException, Security
from sqlmodel import Session

from app.internal.audiobookshelf.client import (
    abs_mark_downloaded_flags,
    flush_abs_library_cache,
)
from app.internal.audiobookshelf.config import abs_config
from app.internal.auth.authentication import ABRAuth, DetailedUser
from app.internal.db_queries import get_wishlist_counts, get_wishlist_results
from app.internal.models import Audiobook, GroupEnum
from app.routers.api.requests import delete_request as api_delete_request
from app.routers.api.requests import start_auto_download_endpoint
from app.util.connection import get_connection
from app.util.db import get_session
from app.util.templates import catalog_response, catalog_response_toast

from . import downloaded, manual, sources

router = APIRouter(prefix="/wishlist")

router.include_router(downloaded.router)
router.include_router(manual.router)
router.include_router(sources.router)


@router.get("")
async def wishlist(
    session: Annotated[Session, Depends(get_session)],
    user: Annotated[DetailedUser, Security(ABRAuth())],
    sort: str = "title",
    sort_dir: str = "asc",
    hide_not_found: bool = False,
):
    username = None if user.is_admin() else user.username
    results = get_wishlist_results(
        session, username, "not_downloaded", sort, sort_dir, hide_not_found
    )
    counts = get_wishlist_counts(session, user)
    return catalog_response(
        "Wishlist.Index",
        user=user,
        results=results,
        counts=counts,
        abs_configured=abs_config.is_valid(session),
        sort=sort,
        sort_dir=sort_dir,
        hide_not_found=hide_not_found,
    )


@router.post("/hx-abs-sync")
async def abs_sync(
    session: Annotated[Session, Depends(get_session)],
    client_session: Annotated[ClientSession, Depends(get_connection)],
    user: Annotated[DetailedUser, Security(ABRAuth(GroupEnum.admin))],
    sort: str = "title",
    sort_dir: str = "asc",
    hide_not_found: bool = False,
):
    flush_abs_library_cache()
    results = get_wishlist_results(
        session, None, "not_downloaded", sort, sort_dir, hide_not_found
    )
    books = [r.book for r in results]
    await abs_mark_downloaded_flags(session, client_session, books)
    newly_downloaded = [b for b in books if b.downloaded]

    results = get_wishlist_results(
        session, None, "not_downloaded", sort, sort_dir, hide_not_found
    )
    counts = get_wishlist_counts(session, user)

    if newly_downloaded:
        msg = f"Marked {len(newly_downloaded)} book(s) as downloaded via AudioBookShelf"
        toast_type = "success"
    else:
        msg = "No new books found in AudioBookShelf library"
        toast_type = "info"

    return catalog_response_toast(
        "Wishlist.Wishlist",
        message=msg,
        toast_type=toast_type,
        user=user,
        results=results,
        page="wishlist",
        counts=counts,
        update_tablist=True,
        sort=sort,
        sort_dir=sort_dir,
        hide_not_found=hide_not_found,
    )


@router.post("/hx-auto-download/{asin}")
async def start_auto_download(
    asin: str,
    session: Annotated[Session, Depends(get_session)],
    client_session: Annotated[ClientSession, Depends(get_connection)],
    user: Annotated[DetailedUser, Security(ABRAuth(GroupEnum.trusted))],
    sort: str = "title",
    sort_dir: str = "asc",
    hide_not_found: bool = False,
):
    await start_auto_download_endpoint(asin, session, client_session, user)
    username = None if user.is_admin() else user.username
    results = get_wishlist_results(
        session, username, "not_downloaded", sort, sort_dir, hide_not_found
    )
    counts = get_wishlist_counts(session, user)

    return catalog_response(
        "Wishlist.Wishlist",
        user=user,
        results=results,
        page="wishlist",
        counts=counts,
        update_tablist=True,
        sort=sort,
        sort_dir=sort_dir,
        hide_not_found=hide_not_found,
    )


@router.patch("/hx-not-found/{asin}")
async def toggle_not_found(
    asin: str,
    session: Annotated[Session, Depends(get_session)],
    admin_user: Annotated[DetailedUser, Security(ABRAuth(GroupEnum.admin))],
    downloaded: bool | None = None,
    sort: str = "title",
    sort_dir: str = "asc",
    hide_not_found: bool = False,
):
    book = session.get(Audiobook, asin)
    if not book:
        raise HTTPException(status_code=404, detail="Book not found")

    book.not_found = not book.not_found
    book.not_found_at = datetime.now() if book.not_found else None
    session.add(book)
    session.commit()

    response_type = "downloaded" if downloaded else "not_downloaded"
    page = "downloaded" if downloaded else "wishlist"
    results = get_wishlist_results(
        session, None, response_type, sort, sort_dir, hide_not_found
    )
    counts = get_wishlist_counts(session, admin_user)

    return catalog_response(
        "Wishlist.Wishlist",
        user=admin_user,
        results=results,
        page=page,
        counts=counts,
        update_tablist=False,
        sort=sort,
        sort_dir=sort_dir,
        hide_not_found=hide_not_found,
    )


@router.delete("/hx-delete/{asin}")
async def delete_request(
    asin: str,
    session: Annotated[Session, Depends(get_session)],
    user: Annotated[DetailedUser, Security(ABRAuth())],
    downloaded: bool | None = None,
    sort: str = "title",
    sort_dir: str = "asc",
    hide_not_found: bool = False,
):
    await api_delete_request(asin, session, user)

    counts = get_wishlist_counts(session, user)

    if downloaded:  # download page
        results = get_wishlist_results(
            session,
            None if user.is_admin() else user.username,
            "downloaded",
            sort,
            sort_dir,
            hide_not_found,
        )
        return catalog_response(
            "Wishlist.Wishlist",
            user=user,
            results=results,
            page="downloaded",
            counts=counts,
            update_tablist=True,
            sort=sort,
            sort_dir=sort_dir,
            hide_not_found=hide_not_found,
        )
    else:
        results = get_wishlist_results(
            session,
            None if user.is_admin() else user.username,
            "not_downloaded",
            sort,
            sort_dir,
            hide_not_found,
        )
        return catalog_response(
            "Wishlist.Wishlist",
            user=user,
            results=results,
            page="wishlist",
            counts=counts,
            update_tablist=True,
            sort=sort,
            sort_dir=sort_dir,
            hide_not_found=hide_not_found,
        )
