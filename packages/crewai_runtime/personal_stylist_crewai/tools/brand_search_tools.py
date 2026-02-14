import hashlib
import os
import re

import requests

SERPAPI_API_KEY = os.getenv("SERPAPI_API_KEY", "")
SERPAPI_ENDPOINT = os.getenv("SERPAPI_ENDPOINT", "https://serpapi.com/search.json")


def _price_value(raw) -> float:
    if isinstance(raw, (int, float)):
        return max(0.0, float(raw))
    if isinstance(raw, str):
        match = re.search(r"(\d+(?:\.\d+)?)", raw.replace(",", ""))
        if match:
            try:
                return max(0.0, float(match.group(1)))
            except ValueError:
                return 0.0
    return 0.0


def _discount_pct(row: dict, old_price: float, sale_price: float) -> float:
    if old_price > sale_price > 0:
        return round(((old_price - sale_price) / old_price) * 100, 2)
    text = f"{row.get('title', '')} {row.get('snippet', '')}"
    match = re.search(r"(\d{1,2})\s*%\s*off", text, flags=re.IGNORECASE)
    if match:
        try:
            return min(90.0, max(0.0, float(match.group(1))))
        except ValueError:
            return 0.0
    return 0.0


def _sku(brand: str, query: str, title: str, link: str) -> str:
    brand_key = re.sub(r"[^a-z0-9]+", "", brand.lower())[:8] or "item"
    q_key = re.sub(r"[^a-z0-9]+", "", query.lower())[:4] or "gen"
    digest = hashlib.sha1(f"{brand}|{query}|{title}|{link}".encode("utf-8")).hexdigest()[:10]
    return f"{brand_key}-{q_key}-{digest}"


def search_brand_catalog(brand: str, query: str, limit: int = 12) -> list[dict]:
    if not SERPAPI_API_KEY:
        return []

    response = requests.get(
        SERPAPI_ENDPOINT,
        params={
            "api_key": SERPAPI_API_KEY,
            "engine": "google_shopping",
            "q": f"{brand} {query}",
            "gl": "us",
            "hl": "en",
            "num": max(1, min(limit, 50)),
        },
        timeout=35,
    )
    if response.status_code >= 400:
        return []

    payload = response.json()
    rows = payload.get("shopping_results")
    if not isinstance(rows, list):
        return []

    products: list[dict] = []
    for row in rows:
        title = str(row.get("title", "")).strip()
        link = str(row.get("link") or row.get("product_link") or "").strip()
        sale_price = _price_value(row.get("extracted_price", row.get("price")))
        if sale_price <= 0:
            continue
        old_price = _price_value(row.get("extracted_old_price", row.get("old_price")))
        products.append(
            {
                "sku": _sku(brand, query, title, link),
                "title": title or f"{brand} {query}",
                "brand": brand,
                "query": query,
                "price": old_price if old_price > sale_price else sale_price,
                "sale_price": sale_price,
                "discount_pct": _discount_pct(row, old_price, sale_price),
                "product_url": link,
                "image_url": row.get("thumbnail") or row.get("image"),
                "source": row.get("source"),
            }
        )

    return products[:limit]
