import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Form, Security
from sqlmodel import Session

from app.internal.audible.catalog import CATEGORY_CATALOG
from app.internal.audible.preferences import (
    UserCategoryEntry,
    get_user_categories,
    save_user_categories,
)
from app.internal.auth.authentication import ABRAuth, DetailedUser
from app.util.db import get_session
from app.util.templates import catalog_response, catalog_response_toast

router = APIRouter(prefix="/categories")


@router.get("")
def read_categories(
    session: Annotated[Session, Depends(get_session)],
    user: Annotated[DetailedUser, Security(ABRAuth())],
):
    entries = get_user_categories(session, user.username)
    return catalog_response(
        "Settings.Categories.Index",
        user=user,
        page="categories",
        entries=entries,
        catalog=CATEGORY_CATALOG,
    )


@router.post("/hx-toggle/{entry_id}")
def toggle_category(
    entry_id: str,
    session: Annotated[Session, Depends(get_session)],
    user: Annotated[DetailedUser, Security(ABRAuth())],
):
    entries = get_user_categories(session, user.username)
    for entry in entries:
        if entry.id == entry_id:
            entry.enabled = not entry.enabled
            break
    save_user_categories(session, user.username, entries)
    return catalog_response(
        "Settings.Categories.List",
        entries=entries,
        catalog=CATEGORY_CATALOG,
    )


@router.post("/hx-reorder")
def reorder_categories(
    session: Annotated[Session, Depends(get_session)],
    user: Annotated[DetailedUser, Security(ABRAuth())],
    order: Annotated[str, Form()],
):
    id_list = [i.strip() for i in order.split(",") if i.strip()]
    entries = get_user_categories(session, user.username)
    order_map = {entry_id: idx for idx, entry_id in enumerate(id_list)}
    for entry in entries:
        if entry.id in order_map:
            entry.order = order_map[entry.id]
    save_user_categories(session, user.username, entries)
    entries_sorted = sorted(entries, key=lambda e: e.order)
    return catalog_response(
        "Settings.Categories.List",
        entries=entries_sorted,
        catalog=CATEGORY_CATALOG,
    )


@router.post("/hx-add-custom")
def add_custom_category(
    session: Annotated[Session, Depends(get_session)],
    user: Annotated[DetailedUser, Security(ABRAuth())],
    display_name: Annotated[str, Form()],
    search_terms_raw: Annotated[str, Form()],
):
    search_terms = [t.strip() for t in search_terms_raw.split(",") if t.strip()]
    if not display_name.strip() or not search_terms:
        return catalog_response_toast(
            "Settings.Categories.List",
            message="Name and at least one search term are required.",
            toast_type="error",
            entries=get_user_categories(session, user.username),
            catalog=CATEGORY_CATALOG,
        )

    entries = get_user_categories(session, user.username)
    new_entry = UserCategoryEntry(
        id=str(uuid.uuid4()),
        display_name=display_name.strip(),
        search_terms=search_terms,
        enabled=True,
        order=max((e.order for e in entries), default=-1) + 1,
        is_custom=True,
    )
    entries.append(new_entry)
    save_user_categories(session, user.username, entries)
    return catalog_response_toast(
        "Settings.Categories.List",
        message=f'Added category "{new_entry.display_name}".',
        toast_type="success",
        entries=entries,
        catalog=CATEGORY_CATALOG,
    )


@router.delete("/hx-delete/{entry_id}")
def delete_custom_category(
    entry_id: str,
    session: Annotated[Session, Depends(get_session)],
    user: Annotated[DetailedUser, Security(ABRAuth())],
):
    entries = get_user_categories(session, user.username)
    entries = [e for e in entries if not (e.id == entry_id and e.is_custom)]
    save_user_categories(session, user.username, entries)
    return catalog_response(
        "Settings.Categories.List",
        entries=entries,
        catalog=CATEGORY_CATALOG,
    )
