from datetime import datetime

from pydantic import BaseModel


class CatalogEntry(BaseModel):
    id: str
    display_name: str
    search_terms: list[str]
    default_enabled: bool = False


CATEGORY_CATALOG: dict[str, CatalogEntry] = {
    "trending": CatalogEntry(
        id="trending",
        display_name="Trending This Week",
        search_terms=["trending", "viral", "popular now", "hot"],
        default_enabled=True,
    ),
    "business": CatalogEntry(
        id="business",
        display_name="Business & Self-Help",
        search_terms=[
            "business",
            "entrepreneurship",
            "leadership",
            "productivity",
            "success",
        ],
        default_enabled=True,
    ),
    "fiction": CatalogEntry(
        id="fiction",
        display_name="Fiction & Literature",
        search_terms=["fiction", "novel", "literature", "story", "fantasy", "mystery"],
        default_enabled=True,
    ),
    "biography": CatalogEntry(
        id="biography",
        display_name="Biography & History",
        search_terms=["biography", "memoir", "autobiography", "life story", "history"],
        default_enabled=True,
    ),
    "science": CatalogEntry(
        id="science",
        display_name="Science & Technology",
        search_terms=["science", "technology", "physics", "psychology", "innovation"],
        default_enabled=True,
    ),
    "recent_releases": CatalogEntry(
        id="recent_releases",
        display_name="New Releases",
        search_terms=[
            str(datetime.now().year),
            "new release",
            "latest",
            "just released",
        ],
        default_enabled=True,
    ),
    "horror_thriller": CatalogEntry(
        id="horror_thriller",
        display_name="Horror & Thriller",
        search_terms=["horror", "thriller", "suspense", "psychological thriller"],
    ),
    "romance": CatalogEntry(
        id="romance",
        display_name="Romance",
        search_terms=["romance", "love story", "romantic fiction"],
    ),
    "self_help": CatalogEntry(
        id="self_help",
        display_name="Self-Help & Personal Development",
        search_terms=[
            "self-help",
            "personal development",
            "motivation",
            "habits",
            "mindset",
        ],
    ),
    "history": CatalogEntry(
        id="history",
        display_name="History",
        search_terms=["history", "historical", "war history", "ancient history"],
    ),
    "sports": CatalogEntry(
        id="sports",
        display_name="Sports & Fitness",
        search_terms=["sports", "fitness", "athletics", "health", "exercise"],
    ),
    "kids_ya": CatalogEntry(
        id="kids_ya",
        display_name="Kids & Young Adult",
        search_terms=["young adult", "children", "middle grade", "teen", "ya fiction"],
    ),
}
