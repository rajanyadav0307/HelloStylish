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


def normalize_style_brief(raw_style_brief: dict) -> dict:
    observed = raw_style_brief.get("observed_features")
    if not isinstance(observed, dict):
        observed = {}

    return {
        "style_summary": str(raw_style_brief.get("style_summary", "Personalized style brief from multimodal analysis.")),
        "observed_features": observed,
        "palette": _str_list(raw_style_brief.get("palette"), ["navy", "cream", "olive"], max_items=5),
        "inferred_vibes": _str_list(raw_style_brief.get("inferred_vibes"), ["casual"], max_items=4),
        "recommended_categories": _str_list(
            raw_style_brief.get("recommended_categories"), ["dress", "top", "bottom"], max_items=5
        ),
        "recommended_brands": _str_list(raw_style_brief.get("recommended_brands"), ["Zara", "H&M", "Mango"], max_items=6),
        "avoid_colors": _str_list(raw_style_brief.get("avoid_colors"), [], max_items=5),
        "budget_max": _normalize_budget(raw_style_brief.get("budget_max"), default=150),
        "confidence_notes": str(raw_style_brief.get("confidence_notes", "")),
    }


def normalize_product(raw_product: dict) -> dict:
    return {
        "sku": str(raw_product.get("sku", "")),
        "brand": str(raw_product.get("brand", "")),
        "category": str(raw_product.get("category", "")),
        "color": str(raw_product.get("color", "")),
        "price": float(raw_product.get("price", 0) or 0),
        "sale_price": float(raw_product.get("sale_price", raw_product.get("price", 0)) or 0),
        "discount_pct": float(raw_product.get("discount_pct", 0) or 0),
    }
