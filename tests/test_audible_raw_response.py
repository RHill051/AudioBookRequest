"""
Debug test — dumps raw Audible API responses to find correct field and param names.
Run with: uv run pytest tests/test_audible_raw_response.py -v -s -m integration
"""

import json
import pytest
import aiohttp

EXPANSE_SERIES_ASIN = "B008Y45GCQ"


@pytest.mark.integration
async def test_dump_comma_separated_asins():
    """Check whether catalog endpoint accepts comma-separated asin value."""
    child_asins = ["B00P9XDQFY", "B008XKUPQ8"]  # sequence 1 and 2

    url = "https://api.audible.com/1.0/catalog/products"
    params = {
        "asin": ",".join(child_asins),
        "response_groups": "media,series",
        "num_results": "5",
    }
    async with aiohttp.ClientSession() as session:
        async with session.get(url, params=params) as resp:
            print(f"\nStatus: {resp.status}")
            data = await resp.json()

    print(f"total_results: {data.get('total_results')}")
    for p in data.get("products", []):
        print(f"  {p.get('asin')} | {p.get('title')}")


@pytest.mark.integration
async def test_dump_single_asin_fetch():
    """Fetch a single known ASIN to confirm individual product fetch works."""
    asin = "B00P9XDQFY"  # Leviathan Wakes (newer edition)
    url = f"https://api.audible.com/1.0/catalog/products/{asin}"
    params = {"response_groups": "media,series"}
    async with aiohttp.ClientSession() as session:
        async with session.get(url, params=params) as resp:
            print(f"\nStatus: {resp.status}")
            data = await resp.json()

    p = data.get("product", {})
    print(f"title: {p.get('title')} | series: {p.get('series')}")
