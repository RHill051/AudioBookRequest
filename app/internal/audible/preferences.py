import json

from pydantic import BaseModel, TypeAdapter
from sqlmodel import Session

from app.internal.audible.catalog import CATEGORY_CATALOG
from app.internal.models import UserCategoryPreference


class UserCategoryEntry(BaseModel):
    id: str
    display_name: str
    search_terms: list[str]
    enabled: bool = True
    order: int = 0
    is_custom: bool = False


_entry_list: TypeAdapter[list[UserCategoryEntry]] = TypeAdapter(list[UserCategoryEntry])


def _default_entries() -> list[UserCategoryEntry]:
    entries: list[UserCategoryEntry] = []
    for i, entry in enumerate(CATEGORY_CATALOG.values()):
        entries.append(
            UserCategoryEntry(
                id=entry.id,
                display_name=entry.display_name,
                search_terms=entry.search_terms,
                enabled=entry.default_enabled,
                order=i,
                is_custom=False,
            )
        )
    return entries


def get_user_categories(session: Session, username: str) -> list[UserCategoryEntry]:
    row = session.get(UserCategoryPreference, username)
    if row is None or not row.config_json:
        return _default_entries()
    try:
        return _entry_list.validate_json(row.config_json)
    except Exception:
        return _default_entries()


def save_user_categories(
    session: Session, username: str, entries: list[UserCategoryEntry]
) -> None:
    row = session.get(UserCategoryPreference, username)
    if row is None:
        row = UserCategoryPreference(user_username=username)
    row.config_json = json.dumps([e.model_dump() for e in entries])
    session.add(row)
    session.commit()
