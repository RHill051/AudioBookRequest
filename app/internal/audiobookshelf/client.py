from __future__ import annotations

import posixpath
import re
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from aiohttp import ClientSession
from pydantic import BaseModel, TypeAdapter
from sqlmodel import Session

from app.internal.audiobookshelf.config import abs_config
from app.internal.audiobookshelf.types import (
    ABSBookItemMinified,
    ABSLibrary,
    ABSPodcastItem,
)
from app.internal.models import Audiobook
from app.util.connection import USER_AGENT
from app.util.db import get_session
from app.util.log import logger

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _headers(session: Session) -> dict[str, str]:
    token = abs_config.get_api_token(session)
    assert token is not None
    return {"Authorization": f"Bearer {token}", "User-Agent": USER_AGENT}


def _normalize(s: str) -> str:
    s = s.lower().strip()
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


# ---------------------------------------------------------------------------
# Pydantic response models
# ---------------------------------------------------------------------------


class _LibraryArray(BaseModel):
    libraries: list[ABSLibrary] = []


class _ListResponseBook(BaseModel):
    results: list[ABSBookItemMinified] = []
    mediaType: Literal["book"]


class _ListResponsePodcast(BaseModel):
    results: list[ABSPodcastItem] = []
    mediaType: Literal["podcast"]


_ListResponse: TypeAdapter[_ListResponseBook | _ListResponsePodcast] = TypeAdapter(
    _ListResponseBook | _ListResponsePodcast
)


# ---------------------------------------------------------------------------
# Library index cache
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ABSLibraryIndex:
    """Immutable in-memory index of the ABS library for O(1) membership checks."""

    asins: frozenset[str]
    norm_titles: frozenset[str]


_LIBRARY_CACHE_TTL = 600  # seconds (10 minutes)
_library_cache: dict[str, tuple[float, ABSLibraryIndex]] = {}


def flush_abs_library_cache() -> None:
    """Invalidate the in-memory library index so the next check fetches fresh data."""
    _library_cache.clear()


async def _fetch_all_library_items(
    session: Session,
    client_session: ClientSession,
) -> list[ABSBookItemMinified]:
    """Paginate through the entire ABS library and return all book items."""
    base_url = abs_config.get_base_url(session)
    lib_id = abs_config.get_library_id(session)
    if not base_url or not lib_id:
        return []

    url = posixpath.join(base_url, f"api/libraries/{lib_id}/items")
    all_items: list[ABSBookItemMinified] = []
    page = 0
    limit = 500

    while True:
        params = {"limit": str(limit), "page": str(page), "minified": "1"}
        try:
            async with client_session.get(
                url, headers=_headers(session), params=params
            ) as resp:
                if not resp.ok:
                    logger.warning(
                        "ABS: failed to fetch library page",
                        status=resp.status,
                        page=page,
                    )
                    break
                payload = _ListResponse.validate_python(await resp.json())
                if payload.mediaType == "podcast":
                    break
                batch = payload.results
        except Exception as e:
            logger.debug(
                "ABS: exception fetching library page", page=page, error=str(e)
            )
            break

        all_items.extend(batch)
        if len(batch) < limit:
            break  # last page
        page += 1

    return all_items


def _build_library_index(items: list[ABSBookItemMinified]) -> ABSLibraryIndex:
    asins: set[str] = set()
    norm_titles: set[str] = set()
    for item in items:
        meta = item.media.metadata
        if meta.asin:
            asins.add(meta.asin)
        if meta.title:
            norm_titles.add(_normalize(meta.title))
    return ABSLibraryIndex(asins=frozenset(asins), norm_titles=frozenset(norm_titles))


async def abs_get_library_index(
    session: Session,
    client_session: ClientSession,
) -> ABSLibraryIndex | None:
    """Return the cached library index, refreshing it if stale or absent."""
    lib_id = abs_config.get_library_id(session)
    if not lib_id:
        return None

    entry = _library_cache.get(lib_id)
    if entry is not None:
        cached_at, index = entry
        if time.time() - cached_at < _LIBRARY_CACHE_TTL:
            logger.debug("ABS: library index cache hit", lib_id=lib_id)
            return index

    logger.debug("ABS: refreshing library index", lib_id=lib_id)
    items = await _fetch_all_library_items(session, client_session)
    index = _build_library_index(items)
    _library_cache[lib_id] = (time.time(), index)
    logger.info(
        "ABS: library index built",
        lib_id=lib_id,
        asin_count=len(index.asins),
        title_count=len(index.norm_titles),
    )
    return index


def _book_in_index(book: Audiobook, index: ABSLibraryIndex) -> bool:
    if book.asin and book.asin in index.asins:
        return True
    if book.title and _normalize(book.title) in index.norm_titles:
        return True
    return False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def abs_get_libraries(
    session: Session, client_session: ClientSession
) -> list[ABSLibrary]:
    base_url = abs_config.get_base_url(session)
    if not base_url:
        return []
    url = posixpath.join(base_url, "api/libraries")
    try:
        async with client_session.get(url, headers=_headers(session)) as resp:
            if not resp.ok:
                logger.error(
                    "ABS: failed to fetch libraries",
                    status=resp.status,
                    reason=resp.reason,
                )
                return []
            data = _LibraryArray.model_validate(await resp.json())
            return data.libraries
    except Exception as e:
        logger.error("ABS: exception fetching libraries", error=str(e))
        return []


async def abs_trigger_scan(session: Session, client_session: ClientSession) -> bool:
    base_url = abs_config.get_base_url(session)
    lib_id = abs_config.get_library_id(session)
    if not base_url or not lib_id:
        return False
    url = posixpath.join(base_url, f"api/libraries/{lib_id}/scan")
    logger.debug("ABS: triggering library scan", library_id=lib_id, url=url)
    async with client_session.post(url, headers=_headers(session), json={}) as resp:
        if not resp.ok:
            logger.warning(
                "ABS: failed to trigger scan", status=resp.status, reason=resp.reason
            )
            return False
        return True


async def background_abs_trigger_scan():
    with next(get_session()) as session:
        async with ClientSession() as client_session:
            logger.debug("ABS: running background library scan trigger")
            success = await abs_trigger_scan(session, client_session)
            logger.info(
                "ABS: background library scan trigger complete", success=success
            )


async def abs_list_library_items(
    session: Session,
    client_session: ClientSession,
    limit: int = 10,
) -> list[Audiobook]:
    """Fetch the N most recently added items from ABS for homepage display."""
    base_url = abs_config.get_base_url(session)
    lib_id = abs_config.get_library_id(session)
    if not base_url or not lib_id:
        return []

    url = posixpath.join(base_url, f"api/libraries/{lib_id}/items")
    params = {
        "limit": str(limit),
        "page": "0",
        "minified": "1",
        "sort": "addedAt",
        "desc": "1",
    }

    try:
        async with client_session.get(
            url, headers=_headers(session), params=params
        ) as resp:
            if not resp.ok:
                logger.debug(
                    "ABS: failed to list library items",
                    status=resp.status,
                    reason=resp.reason,
                )
                return []
            payload = _ListResponse.validate_python(await resp.json())
            if payload.mediaType == "podcast":
                logger.warning(
                    "ABS: podcasts not supported in library listing", lib_id=lib_id
                )
                return []
    except Exception as e:
        logger.debug("ABS: exception listing library items", error=str(e))
        return []

    books: list[Audiobook] = []
    for item in payload.results:
        try:
            metadata = item.media.metadata
            title = metadata.title
            subtitle = metadata.subtitle
            authors = [metadata.authorName]
            narrators = [metadata.narratorName]
            cover_image = posixpath.join(base_url, f"api/items/{item.id}/cover")
            try:
                runtime_length_min = int(round(item.media.duration / 60))
            except Exception:
                runtime_length_min = 0

            if metadata.publishedDate:
                try:
                    release_date = datetime.fromisoformat(
                        metadata.publishedDate.replace("Z", "+00:00")
                    )
                except Exception:
                    release_date = datetime.now()
            else:
                release_date = datetime.now()

            if not metadata.asin or not title:
                logger.warning(
                    "ABS: skipping library item with missing ASIN or title",
                    item_id=item.id,
                    asin=metadata.asin,
                    title=title,
                )
                continue

            books.append(
                Audiobook(
                    asin=metadata.asin,
                    title=title,
                    subtitle=subtitle,
                    authors=authors,
                    narrators=narrators,
                    cover_image=cover_image,
                    release_date=release_date,
                    runtime_length_min=runtime_length_min,
                    downloaded=True,
                    downloaded_at=datetime.now(),
                )
            )
        except Exception as e:
            logger.debug("ABS: failed to map library item", error=str(e))

    return books


async def abs_mark_downloaded_flags(
    session: Session,
    client_session: ClientSession,
    books: list[Audiobook],
    commit: bool = True,
) -> None:
    """
    Check each book against the ABS library index and mark matches as downloaded.
    Uses a 10-minute in-memory cache so only the first call per window hits the API.
    Pass commit=False (search context) to mark in-memory only without writing to the DB.
    """
    if not abs_config.get_check_downloaded(session):
        return

    to_check = [b for b in books if not b.downloaded]
    if not to_check:
        return

    index = await abs_get_library_index(session, client_session)
    if index is None:
        return

    for b in to_check:
        if _book_in_index(b, index):
            b.downloaded = True
            if not b.downloaded_at:
                b.downloaded_at = datetime.now()
            if commit:
                session.add(b)

    if commit:
        session.commit()
