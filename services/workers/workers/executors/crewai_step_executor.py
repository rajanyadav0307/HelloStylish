import base64
import colorsys
import hashlib
import json
import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from io import BytesIO
from urllib.parse import quote_plus

import requests
from PIL import Image

from workers.common.db import exec_one, exec_write

GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_DRIVE_API = "https://www.googleapis.com/drive/v3"
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_API_BASE = os.getenv("OPENAI_API_BASE", "https://api.openai.com/v1")
OPENAI_VISION_MODEL = os.getenv("OPENAI_VISION_MODEL", "gpt-4.1-mini")
SERPAPI_API_KEY = os.getenv("SERPAPI_API_KEY", "")
SERPAPI_ENDPOINT = os.getenv("SERPAPI_ENDPOINT", "https://serpapi.com/search.json")
PRODUCT_DATA_MODE = os.getenv("PRODUCT_DATA_MODE", "auto").strip().lower()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _parse_json(value):
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return {}
    return {}


def _get_artifact(run_id, kind: str) -> dict:
    row = exec_one(
        """
        SELECT inline_json
        FROM artifacts
        WHERE run_id=:run_id AND kind=:kind
        ORDER BY created_at DESC
        LIMIT 1
        """,
        {"run_id": run_id, "kind": kind},
    )
    if not row:
        return {}
    return _parse_json(row.get("inline_json"))


def _get_run_user(run_id):
    row = exec_one("SELECT user_id FROM runs WHERE id=:run_id", {"run_id": run_id})
    return row.get("user_id") if row else None


def _get_selected_drive_folder(user_id):
    return exec_one(
        """
        SELECT folder_id, folder_name
        FROM drive_folders
        WHERE user_id=:user_id AND is_selected=TRUE
        ORDER BY created_at DESC
        LIMIT 1
        """,
        {"user_id": user_id},
    )


def _get_drive_connection(user_id):
    return exec_one(
        """
        SELECT access_token, refresh_token, token_expiry
        FROM drive_connections
        WHERE user_id=:user_id
        """,
        {"user_id": user_id},
    )


def _refresh_drive_access_token(user_id, refresh_token: str):
    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
        raise RuntimeError("Google OAuth client credentials are not configured in worker env")

    response = requests.post(
        GOOGLE_TOKEN_URL,
        data={
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        },
        timeout=20,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"Failed refreshing Google Drive token: {response.text[:200]}")

    payload = response.json()
    access_token = payload.get("access_token")
    if not access_token:
        raise RuntimeError("Refresh token flow returned no access_token")

    expires_in = payload.get("expires_in", 3600)
    token_expiry = _utcnow() + timedelta(seconds=max(0, int(expires_in) - 30))

    exec_write(
        """
        UPDATE drive_connections
        SET access_token=:access_token, token_expiry=:token_expiry, updated_at=:updated_at
        WHERE user_id=:user_id
        """,
        {
            "access_token": access_token,
            "token_expiry": token_expiry,
            "updated_at": _utcnow(),
            "user_id": user_id,
        },
    )
    return access_token


def _ensure_drive_access_token(user_id):
    conn = _get_drive_connection(user_id)
    if not conn:
        return None

    token_expiry = conn.get("token_expiry")
    if conn.get("access_token") and token_expiry and token_expiry > _utcnow() + timedelta(seconds=60):
        return conn.get("access_token")

    if conn.get("refresh_token"):
        return _refresh_drive_access_token(user_id, conn["refresh_token"])

    return conn.get("access_token")


def _drive_list_images(access_token: str, folder_id: str, limit: int = 40) -> list[dict]:
    response = requests.get(
        f"{GOOGLE_DRIVE_API}/files",
        headers={"Authorization": f"Bearer {access_token}"},
        params={
            "q": f"'{folder_id}' in parents and mimeType contains 'image/' and trashed=false",
            "fields": "files(id,name,mimeType,createdTime,imageMediaMetadata(width,height,time))",
            "orderBy": "createdTime desc",
            "pageSize": max(1, min(limit, 200)),
            "includeItemsFromAllDrives": "true",
            "supportsAllDrives": "true",
        },
        timeout=25,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"Drive image list failed: {response.status_code} {response.text[:200]}")
    return response.json().get("files", [])


def _download_drive_image(access_token: str, file_id: str) -> Image.Image | None:
    response = requests.get(
        f"{GOOGLE_DRIVE_API}/files/{file_id}",
        headers={"Authorization": f"Bearer {access_token}"},
        params={"alt": "media", "supportsAllDrives": "true"},
        timeout=25,
    )
    if response.status_code >= 400:
        return None
    try:
        return Image.open(BytesIO(response.content)).convert("RGB")
    except Exception:
        return None


def _image_to_data_uri(image: Image.Image, max_size: int = 1024, quality: int = 82) -> str:
    img = image.copy()
    img.thumbnail((max_size, max_size))
    buffer = BytesIO()
    img.save(buffer, format="JPEG", quality=quality, optimize=True)
    encoded = base64.b64encode(buffer.getvalue()).decode("utf-8")
    return f"data:image/jpeg;base64,{encoded}"


def _prepare_images_for_multimodal_analysis(
    access_token: str, photos: list[dict], max_images: int = 4
) -> list[dict]:
    prepared: list[dict] = []
    for photo in photos[:max_images]:
        photo_id = photo.get("id")
        if not photo_id:
            continue
        img = _download_drive_image(access_token, photo_id)
        if img is None:
            continue
        prepared.append(
            {
                "id": photo_id,
                "name": str(photo.get("name", "")),
                "data_uri": _image_to_data_uri(img),
            }
        )
    return prepared


def _extract_json_object(text: str) -> dict:
    text = (text or "").strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        pass

    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return {}

    snippet = text[start : end + 1]
    try:
        parsed = json.loads(snippet)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        return {}


def _chat_content_to_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                txt = item.get("text")
                if isinstance(txt, str):
                    parts.append(txt)
        return "\n".join(parts)
    return ""


def _str_list(value, default: list[str], max_items: int = 6) -> list[str]:
    if not isinstance(value, list):
        return default[:max_items]
    items = [str(v).strip() for v in value if str(v).strip()]
    return (items[:max_items] or default[:max_items])


def _normalize_budget(value, default: int = 150) -> int:
    try:
        budget = int(float(value))
        return max(50, min(budget, 1200))
    except (TypeError, ValueError):
        return default


def _normalize_llm_style_brief(raw: dict) -> dict:
    observed = raw.get("observed_features")
    if not isinstance(observed, dict):
        observed = {}

    return {
        "style_summary": str(raw.get("style_summary", "Personalized style brief from multimodal analysis.")),
        "observed_features": observed,
        "palette": _str_list(raw.get("palette"), ["navy", "cream", "olive"], max_items=5),
        "inferred_vibes": _str_list(raw.get("inferred_vibes"), ["casual"], max_items=4),
        "recommended_categories": _str_list(raw.get("recommended_categories"), ["dress", "top", "bottom"], max_items=5),
        "recommended_brands": _str_list(raw.get("recommended_brands"), ["Zara", "H&M", "Mango"], max_items=6),
        "avoid_colors": _str_list(raw.get("avoid_colors"), [], max_items=5),
        "budget_max": _normalize_budget(raw.get("budget_max"), default=150),
        "confidence_notes": str(raw.get("confidence_notes", "")),
    }


def _call_multimodal_style_agent(prepared_images: list[dict]) -> dict:
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY is not configured for STYLE_BRIEF multimodal analysis")

    if not prepared_images:
        raise RuntimeError("No image payloads available for multimodal STYLE_BRIEF analysis")

    prompt = (
        "You are STYLE_BRIEF, a fashion stylist agent. Analyze the outfit/person photos and produce a concise style profile.\n"
        "Rules:\n"
        "- Focus on visible fashion/style cues only.\n"
        "- Do not infer sensitive traits (race, religion, health, politics).\n"
        "- Recommend practical outfit categories, colors, and brands.\n"
        "Return JSON with keys:\n"
        "{\n"
        "  \"style_summary\": string,\n"
        "  \"observed_features\": {\"silhouette\": string, \"fit_preference\": string, \"patterns_or_textures\": [string]},\n"
        "  \"palette\": [string],\n"
        "  \"inferred_vibes\": [string],\n"
        "  \"recommended_categories\": [string],\n"
        "  \"recommended_brands\": [string],\n"
        "  \"avoid_colors\": [string],\n"
        "  \"budget_max\": number,\n"
        "  \"confidence_notes\": string\n"
        "}\n"
        "Use 3-6 items for list fields where possible."
    )

    user_content: list[dict] = [{"type": "text", "text": prompt}]
    for image in prepared_images:
        user_content.append(
            {
                "type": "image_url",
                "image_url": {"url": image["data_uri"]},
            }
        )

    payload = {
        "model": OPENAI_VISION_MODEL,
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
        "messages": [
            {
                "role": "system",
                "content": "You are a personal stylist AI agent returning structured JSON only.",
            },
            {"role": "user", "content": user_content},
        ],
    }

    response = requests.post(
        f"{OPENAI_API_BASE.rstrip('/')}/chat/completions",
        headers={
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=90,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"Multimodal model request failed: {response.status_code} {response.text[:220]}")

    data = response.json()
    choices = data.get("choices") or []
    if not choices:
        raise RuntimeError("Multimodal model returned no choices")

    message = choices[0].get("message", {})
    content_text = _chat_content_to_text(message.get("content"))
    parsed = _extract_json_object(content_text)
    if not parsed:
        raise RuntimeError("Multimodal model response was not valid JSON")

    return _normalize_llm_style_brief(parsed)


def _classify_color_name(rgb: tuple[int, int, int]) -> str:
    r, g, b = rgb
    h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
    hue = h * 360

    if v < 0.2:
        return "black"
    if s < 0.15 and v > 0.85:
        return "white"
    if s < 0.2:
        return "gray"

    if hue < 15 or hue >= 345:
        return "red"
    if hue < 45:
        return "orange"
    if hue < 70:
        return "yellow"
    if hue < 170:
        return "green"
    if hue < 210:
        return "cyan"
    if hue < 255:
        return "blue"
    if hue < 300:
        return "purple"
    return "pink"


def _extract_palette(access_token: str, photos: list[dict]) -> list[str]:
    counts: dict[str, int] = {}

    for photo in photos[:8]:
        photo_id = photo.get("id")
        if not photo_id:
            continue
        img = _download_drive_image(access_token, photo_id)
        if img is None:
            continue

        img.thumbnail((96, 96))
        pixels = list(img.getdata())
        if not pixels:
            continue

        stride = max(1, len(pixels) // 800)
        sampled = pixels[::stride]
        if not sampled:
            sampled = pixels

        avg = (
            int(sum(p[0] for p in sampled) / len(sampled)),
            int(sum(p[1] for p in sampled) / len(sampled)),
            int(sum(p[2] for p in sampled) / len(sampled)),
        )
        color = _classify_color_name(avg)
        counts[color] = counts.get(color, 0) + 1

    ranked = sorted(counts.items(), key=lambda item: item[1], reverse=True)
    return [name for name, _ in ranked[:4]]


def _tokenize_filename(name: str) -> list[str]:
    return [tok for tok in re.split(r"[^a-zA-Z0-9]+", name.lower()) if tok]


def _infer_vibes(photos: list[dict]) -> list[str]:
    vibe_keywords = {
        "formal": {"formal", "office", "wedding", "blazer", "suit", "gown"},
        "casual": {"casual", "street", "daily", "day", "denim", "jeans"},
        "party": {"party", "night", "club", "cocktail"},
        "sporty": {"gym", "run", "sport", "yoga", "fitness"},
        "beach": {"beach", "vacation", "resort", "swim"},
    }

    counts = {k: 0 for k in vibe_keywords}
    for photo in photos:
        tokens = set(_tokenize_filename(photo.get("name", "")))
        for vibe, keywords in vibe_keywords.items():
            if tokens & keywords:
                counts[vibe] += 1

    ranked = [v for v, c in sorted(counts.items(), key=lambda item: item[1], reverse=True) if c > 0]
    return ranked[:3] if ranked else ["casual"]


def _infer_categories(photos: list[dict]) -> list[str]:
    category_keywords = {
        "dress": {"dress", "gown"},
        "top": {"top", "shirt", "tee", "blouse"},
        "bottom": {"jeans", "pants", "trouser", "skirt", "shorts"},
        "outerwear": {"jacket", "coat", "blazer", "hoodie"},
        "shoes": {"shoe", "heel", "sneaker", "boot"},
    }

    counts = {k: 0 for k in category_keywords}
    for photo in photos:
        tokens = set(_tokenize_filename(photo.get("name", "")))
        for category, keywords in category_keywords.items():
            if tokens & keywords:
                counts[category] += 1

    ranked = [c for c, n in sorted(counts.items(), key=lambda item: item[1], reverse=True) if n > 0]
    if ranked:
        return ranked[:3]
    return ["dress", "top", "bottom"]


def _brands_for_vibes(vibes: list[str]) -> list[str]:
    vibe_brand_map = {
        "formal": ["Massimo Dutti", "Mango", "Zara"],
        "casual": ["Uniqlo", "H&M", "Gap"],
        "party": ["Zara", "ASOS", "H&M"],
        "sporty": ["Nike", "Adidas", "Puma"],
        "beach": ["H&M", "Zara", "Mango"],
    }
    ordered: list[str] = []
    for vibe in vibes:
        for brand in vibe_brand_map.get(vibe, []):
            if brand not in ordered:
                ordered.append(brand)
    if not ordered:
        ordered = ["Zara", "H&M", "Mango"]
    return ordered[:5]


def _style_brief_fallback(access_token: str, folder: dict, photos: list[dict], reason: str) -> dict:
    palette = _extract_palette(access_token, photos)
    vibes = _infer_vibes(photos)
    categories = _infer_categories(photos)
    brands = _brands_for_vibes(vibes)

    return {
        "source": "google_drive",
        "analysis_method": "heuristic_fallback",
        "multimodal_error": reason[:220],
        "folder_id": folder["folder_id"],
        "folder_name": folder.get("folder_name"),
        "photo_count": len(photos),
        "sample_photo_ids": [photo.get("id") for photo in photos[:8]],
        "style_summary": "Fallback style brief generated because multimodal model was unavailable.",
        "observed_features": {},
        "palette": palette or ["navy", "cream", "olive"],
        "inferred_vibes": vibes,
        "recommended_categories": categories,
        "recommended_brands": brands,
        "avoid_colors": [],
        "budget_max": 150,
        "confidence_notes": "Use OPENAI_API_KEY to enable multimodal visual analysis in STYLE_BRIEF.",
    }


def _style_brief_payload(run_id) -> dict:
    user_id = _get_run_user(run_id)
    if not user_id:
        return {"source": "system", "error": "run user not found"}

    access_token = _ensure_drive_access_token(user_id)
    folder = _get_selected_drive_folder(user_id)

    if not access_token or not folder:
        return {
            "source": "onboarding",
            "analysis_method": "none",
            "requires_drive_connection": True,
            "message": "Connect Google Drive and select a folder to generate personalized style briefs.",
            "recommended_brands": ["Zara", "H&M", "Mango"],
            "recommended_categories": ["dress", "top", "bottom"],
            "palette": ["navy", "cream", "olive"],
            "budget_max": 120,
        }

    try:
        photos = _drive_list_images(access_token, folder["folder_id"], limit=40)
    except Exception as exc:
        return {
            "source": "google_drive",
            "analysis_method": "none",
            "requires_drive_connection": True,
            "folder_id": folder["folder_id"],
            "folder_name": folder.get("folder_name"),
            "message": "Drive access failed. Reconnect Google Drive and try again.",
            "drive_error": str(exc)[:220],
            "recommended_brands": ["Zara", "H&M", "Mango"],
            "recommended_categories": ["dress", "top", "bottom"],
            "palette": ["navy", "cream", "olive"],
            "budget_max": 120,
        }
    if not photos:
        return {
            "source": "google_drive",
            "analysis_method": "none",
            "folder_id": folder["folder_id"],
            "folder_name": folder.get("folder_name"),
            "requires_drive_connection": False,
            "message": "Selected folder has no image files.",
            "recommended_brands": ["Zara", "H&M", "Mango"],
            "recommended_categories": ["dress", "top", "bottom"],
            "palette": ["navy", "cream", "olive"],
            "budget_max": 120,
            "photo_count": 0,
        }

    prepared_images = _prepare_images_for_multimodal_analysis(access_token, photos, max_images=4)
    if not prepared_images:
        return _style_brief_fallback(
            access_token=access_token,
            folder=folder,
            photos=photos,
            reason="Unable to download photos for multimodal analysis",
        )

    try:
        llm_style = _call_multimodal_style_agent(prepared_images)
        return {
            "source": "google_drive",
            "analysis_method": "multimodal_llm",
            "folder_id": folder["folder_id"],
            "folder_name": folder.get("folder_name"),
            "photo_count": len(photos),
            "analyzed_photo_ids": [img["id"] for img in prepared_images],
            **llm_style,
        }
    except Exception as exc:
        return _style_brief_fallback(
            access_token=access_token,
            folder=folder,
            photos=photos,
            reason=str(exc),
        )


def _real_catalog_enabled() -> bool:
    if PRODUCT_DATA_MODE == "mock":
        return False
    if PRODUCT_DATA_MODE in {"serpapi", "real", "live"}:
        return bool(SERPAPI_API_KEY)
    return bool(SERPAPI_API_KEY)


def _price_value(raw) -> float:
    if isinstance(raw, (int, float)):
        return max(0.0, float(raw))
    if isinstance(raw, dict):
        for key in ("value", "amount", "price"):
            if key in raw:
                return _price_value(raw.get(key))
    if isinstance(raw, str):
        match = re.search(r"(\d+(?:\.\d+)?)", raw.replace(",", ""))
        if match:
            try:
                return max(0.0, float(match.group(1)))
            except ValueError:
                return 0.0
    return 0.0


def _discount_from_text(*values) -> float:
    best = 0.0
    for value in values:
        if value is None:
            continue
        text = str(value)
        for match in re.findall(r"(\d{1,2})\s*%\s*off", text, flags=re.IGNORECASE):
            try:
                pct = float(match)
            except ValueError:
                continue
            best = max(best, min(90.0, max(0.0, pct)))
    return best


def _estimate_discount_pct(row: dict, old_price: float, sale_price: float, hint: float = 0.0) -> float:
    if old_price > sale_price > 0:
        return round(min(90.0, ((old_price - sale_price) / old_price) * 100), 2)

    from_text = _discount_from_text(
        row.get("title"),
        row.get("snippet"),
        " ".join(row.get("extensions", [])) if isinstance(row.get("extensions"), list) else row.get("extensions"),
    )
    if from_text > 0:
        return from_text
    return min(90.0, max(0.0, float(hint or 0.0)))


def _guess_color_from_title(title: str, palette: list[str]) -> str:
    text = (title or "").lower()
    palette_norm = [str(color).strip().lower() for color in palette if str(color).strip()]
    for color in palette_norm:
        if color and color in text:
            return color

    common_colors = [
        "black",
        "white",
        "gray",
        "grey",
        "navy",
        "blue",
        "red",
        "green",
        "yellow",
        "pink",
        "purple",
        "orange",
        "brown",
        "beige",
        "cream",
        "olive",
    ]
    for color in common_colors:
        if re.search(rf"\b{re.escape(color)}\b", text):
            return color
    return palette_norm[0] if palette_norm else "neutral"


def _build_product_sku(brand: str, category: str, title: str, link: str) -> str:
    brand_key = re.sub(r"[^a-z0-9]+", "", brand.lower())[:8] or "item"
    cat_key = re.sub(r"[^a-z0-9]+", "", category.lower())[:4] or "gen"
    digest = hashlib.sha1(f"{brand}|{category}|{title}|{link}".encode("utf-8")).hexdigest()[:10]
    return f"{brand_key}-{cat_key}-{digest}"


def _serpapi_shopping_search(query: str, num: int = 20) -> list[dict]:
    if not SERPAPI_API_KEY:
        raise RuntimeError("SERPAPI_API_KEY is missing")

    response = requests.get(
        SERPAPI_ENDPOINT,
        params={
            "api_key": SERPAPI_API_KEY,
            "engine": "google_shopping",
            "q": query,
            "gl": "us",
            "hl": "en",
            "num": max(1, min(num, 100)),
        },
        timeout=35,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"SerpAPI request failed: {response.status_code} {response.text[:220]}")

    payload = response.json()
    for key in ("shopping_results", "inline_shopping_results", "products"):
        rows = payload.get(key)
        if isinstance(rows, list):
            return rows
    return []


def _mock_deals_payload(brands: list[str]) -> dict:
    deals = []
    for brand in brands[:5]:
        discount_pct = 10 + (sum(ord(ch) for ch in brand.lower()) % 31)
        deals.append(
            {
                "brand": brand,
                "discount_pct": discount_pct,
                "sale_end_in_days": 2 + (len(brand) % 5),
                "source": "synthetic",
            }
        )
    return {
        "brands_scanned": brands[:5],
        "deals": deals,
        "data_mode": "mock",
        "provider": "synthetic",
    }


def _mock_brand_search_payload(
    brands: list[str],
    categories: list[str],
    palette: list[str],
    deals_by_brand: dict[str, dict],
) -> dict:
    candidates = []
    for idx, brand in enumerate(brands[:5]):
        discount = float(deals_by_brand.get(brand, {}).get("discount_pct", 10))
        for jdx, category in enumerate(categories[:2]):
            color = palette[(idx + jdx) % len(palette)]
            base_price = 55 + (idx * 18) + (jdx * 12)
            sale_price = round(base_price * (1 - discount / 100), 2)
            sku = f"{brand.lower().replace(' ', '')}-{category[:3]}-{idx + 1}{jdx + 1}"
            candidates.append(
                {
                    "sku": sku,
                    "title": f"{brand} {category}",
                    "brand": brand,
                    "category": category,
                    "color": color,
                    "price": base_price,
                    "sale_price": sale_price,
                    "discount_pct": discount,
                    "source": "synthetic",
                    "data_source": "mock",
                }
            )
    return {
        "product_candidates": candidates[:12],
        "data_mode": "mock",
        "provider": "synthetic",
    }


def _real_deals_payload(style: dict, brands: list[str]) -> dict:
    categories = style.get("recommended_categories") or ["dress", "top", "bottom"]
    deals = []
    errors = []

    for brand in brands[:5]:
        query = f"{brand} sale {categories[0]}"
        try:
            results = _serpapi_shopping_search(query, num=20)
        except Exception as exc:
            errors.append(f"{brand}: {str(exc)[:160]}")
            continue

        prices = []
        best_discount = 0.0
        for row in results:
            sale_price = _price_value(row.get("extracted_price", row.get("price")))
            if sale_price > 0:
                prices.append(sale_price)
            old_price = _price_value(row.get("extracted_old_price", row.get("old_price")))
            best_discount = max(best_discount, _estimate_discount_pct(row, old_price, sale_price))

        deals.append(
            {
                "brand": brand,
                "discount_pct": round(best_discount, 2),
                "sale_end_in_days": 3,
                "source": "serpapi_google_shopping",
                "query": query,
                "result_count": len(results),
                "min_price": round(min(prices), 2) if prices else None,
            }
        )

    return {
        "brands_scanned": brands[:5],
        "deals": deals,
        "data_mode": "serpapi",
        "provider": "serpapi_google_shopping",
        "errors": errors[:5],
    }


def _real_brand_search_payload(style: dict, deals: dict) -> dict:
    categories = style.get("recommended_categories") or ["dress", "top", "bottom"]
    palette = [str(c).lower() for c in (style.get("palette") or ["black", "navy", "white"])]
    budget_max = float(style.get("budget_max") or 150)
    deals_by_brand = {row["brand"]: row for row in deals.get("deals", []) if row.get("brand")}
    brands = list(deals_by_brand.keys()) or style.get("recommended_brands") or ["Zara", "H&M", "Mango"]

    candidates = []
    seen_keys: set[str] = set()
    errors = []
    queries = []

    for brand in brands[:5]:
        deal_hint = float(deals_by_brand.get(brand, {}).get("discount_pct", 0) or 0)
        for category in categories[:2]:
            query = f"{brand} {category} women"
            queries.append(query)
            try:
                rows = _serpapi_shopping_search(query, num=20)
            except Exception as exc:
                errors.append(f"{brand}/{category}: {str(exc)[:160]}")
                continue

            for row in rows[:8]:
                title = str(row.get("title", "")).strip()
                link = str(row.get("link") or row.get("product_link") or "").strip()
                if not title and not link:
                    continue

                sale_price = _price_value(row.get("extracted_price", row.get("price")))
                if sale_price <= 0:
                    continue
                if sale_price > (budget_max * 2.2):
                    continue

                old_price = _price_value(row.get("extracted_old_price", row.get("old_price")))
                discount_pct = _estimate_discount_pct(row, old_price, sale_price, hint=deal_hint)
                if old_price > sale_price:
                    price = old_price
                elif 0 < discount_pct < 95:
                    price = round(sale_price / (1 - (discount_pct / 100.0)), 2)
                else:
                    price = sale_price

                sku = _build_product_sku(brand, category, title, link)
                dedupe_key = link or sku
                if dedupe_key in seen_keys:
                    continue
                seen_keys.add(dedupe_key)

                candidates.append(
                    {
                        "sku": sku,
                        "title": title or f"{brand} {category}",
                        "brand": brand,
                        "category": category,
                        "color": _guess_color_from_title(title, palette),
                        "price": round(price, 2),
                        "sale_price": round(sale_price, 2),
                        "discount_pct": round(discount_pct, 2),
                        "product_url": link,
                        "image_url": row.get("thumbnail") or row.get("image"),
                        "source": str(row.get("source") or row.get("seller") or "web"),
                        "query": query,
                        "data_source": "serpapi",
                    }
                )

    candidates.sort(key=lambda item: (float(item.get("discount_pct", 0)), -float(item.get("sale_price", 0))), reverse=True)
    return {
        "product_candidates": candidates[:24],
        "data_mode": "serpapi",
        "provider": "serpapi_google_shopping",
        "queries": queries[:12],
        "errors": errors[:5],
    }


def _deals_payload(run_id) -> dict:
    style = _get_artifact(run_id, "style_brief")
    brands = style.get("recommended_brands") or ["Zara", "H&M", "Mango"]

    if _real_catalog_enabled():
        try:
            live_payload = _real_deals_payload(style=style, brands=brands)
            if live_payload.get("deals"):
                return live_payload
            fallback = _mock_deals_payload(brands)
            fallback["data_mode"] = "mock_fallback"
            fallback["fallback_reason"] = "live_provider_returned_no_results"
            fallback["live_errors"] = live_payload.get("errors", [])
            return fallback
        except Exception as exc:
            fallback = _mock_deals_payload(brands)
            fallback["data_mode"] = "mock_fallback"
            fallback["fallback_reason"] = str(exc)[:220]
            return fallback

    return _mock_deals_payload(brands)


def _brand_search_payload(run_id) -> dict:
    style = _get_artifact(run_id, "style_brief")
    deals = _get_artifact(run_id, "deals")

    categories = style.get("recommended_categories") or ["dress", "top", "bottom"]
    palette = style.get("palette") or ["black", "navy", "white"]
    deals_by_brand = {row["brand"]: row for row in deals.get("deals", []) if row.get("brand")}
    brands = list(deals_by_brand.keys()) or style.get("recommended_brands") or ["Zara", "H&M", "Mango"]

    if _real_catalog_enabled():
        try:
            live_payload = _real_brand_search_payload(style=style, deals=deals)
            if live_payload.get("product_candidates"):
                return live_payload
            fallback = _mock_brand_search_payload(brands, categories, palette, deals_by_brand)
            fallback["data_mode"] = "mock_fallback"
            fallback["fallback_reason"] = "live_provider_returned_no_products"
            fallback["live_errors"] = live_payload.get("errors", [])
            return fallback
        except Exception as exc:
            fallback = _mock_brand_search_payload(brands, categories, palette, deals_by_brand)
            fallback["data_mode"] = "mock_fallback"
            fallback["fallback_reason"] = str(exc)[:220]
            return fallback

    return _mock_brand_search_payload(brands, categories, palette, deals_by_brand)


def _rank_payload(run_id) -> dict:
    style = _get_artifact(run_id, "style_brief")
    search = _get_artifact(run_id, "brand_search")

    palette = {str(color).lower() for color in (style.get("palette") or [])}
    budget_max = float(style.get("budget_max") or 150)

    ranked_items = []
    for candidate in search.get("product_candidates", []):
        sale_price = float(candidate.get("sale_price", candidate.get("price", 0)))
        discount_pct = float(candidate.get("discount_pct", 0))
        candidate_color = str(candidate.get("color", "")).lower()
        palette_bonus = 0.1 if candidate_color in palette else 0.0
        budget_score = max(0.0, 1.0 - (sale_price / max(budget_max, 1.0)))
        deal_score = min(discount_pct / 50.0, 1.0)
        score = round((budget_score * 0.45) + (deal_score * 0.45) + palette_bonus, 4)

        ranked_items.append({**candidate, "score": score})

    ranked_items.sort(key=lambda item: item["score"], reverse=True)
    return {"ranked_items": ranked_items[:10]}


def _tryon_payload(run_id) -> dict:
    ranked = _get_artifact(run_id, "rank")

    tryon_results = []
    for item in ranked.get("ranked_items", [])[:4]:
        tryon_results.append(
            {
                "sku": item.get("sku"),
                "brand": item.get("brand"),
                "status": "draft",
                "preview_prompt": (
                    f"Overlay a {item.get('color')} {item.get('category')} from {item.get('brand')} "
                    "on the user's portrait preserving pose and lighting."
                ),
            }
        )

    return {"tryon_results": tryon_results}


def _checkout_payload(run_id) -> dict:
    ranked = _get_artifact(run_id, "rank")

    items = []
    for item in ranked.get("ranked_items", [])[:3]:
        sku = item.get("sku", "")
        search_query = quote_plus(f"{item.get('brand', '')} {sku} buy")
        checkout_url = item.get("product_url") or f"https://www.google.com/search?q={search_query}"
        items.append(
            {
                "sku": sku,
                "title": item.get("title"),
                "brand": item.get("brand"),
                "sale_price": item.get("sale_price"),
                "source": item.get("source"),
                "checkout_url": checkout_url,
                "qty": 1,
            }
        )

    return {"checkout_draft": {"approval_required": True, "items": items}}


def _artifact_payload(step_key: str, run_id) -> dict:
    if step_key == "STYLE_BRIEF":
        return _style_brief_payload(run_id)
    if step_key == "DEALS":
        return _deals_payload(run_id)
    if step_key == "BRAND_SEARCH":
        return _brand_search_payload(run_id)
    if step_key == "RANK":
        return _rank_payload(run_id)
    if step_key == "TRYON":
        return _tryon_payload(run_id)
    if step_key == "CHECKOUT_DRAFT":
        return _checkout_payload(run_id)
    return {"note": "unknown-step"}


def execute_step_impl(step_id: str, run_id: str, step_key: str) -> dict:
    sid = uuid.UUID(step_id)
    rid = uuid.UUID(run_id)

    exec_write(
        """
        UPDATE run_steps
        SET status='RUNNING', started_at=COALESCE(started_at, :started_at), error=NULL
        WHERE id=:step_id AND run_id=:run_id AND status IN ('PENDING', 'QUEUED', 'RUNNING')
        """,
        {"step_id": sid, "run_id": rid, "started_at": _utcnow()},
    )

    try:
        payload = _artifact_payload(step_key, rid)

        exec_write(
            """
            INSERT INTO artifacts (run_id, run_step_id, user_id, kind, mime_type, storage_backend, inline_json)
            SELECT r.id, :step_id, r.user_id, :kind, 'application/json', 'inline', CAST(:payload AS JSONB)
            FROM runs r
            WHERE r.id = :run_id
            """,
            {
                "step_id": sid,
                "run_id": rid,
                "kind": step_key.lower(),
                "payload": json.dumps(payload),
            },
        )

        exec_write(
            """
            UPDATE run_steps
            SET status='SUCCEEDED', finished_at=:finished_at, error=NULL
            WHERE id=:step_id
            """,
            {"step_id": sid, "finished_at": _utcnow()},
        )

        return {"step_id": str(sid), "step_key": step_key, "status": "SUCCEEDED"}
    except Exception as exc:
        exec_write(
            """
            UPDATE run_steps
            SET status='FAILED', finished_at=:finished_at, error=:error
            WHERE id=:step_id
            """,
            {
                "step_id": sid,
                "finished_at": _utcnow(),
                "error": str(exc)[:1000],
            },
        )
        return {"step_id": str(sid), "step_key": step_key, "status": "FAILED", "error": str(exc)}
