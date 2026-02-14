from personal_stylist_crewai.tools.brand_search_tools import search_brand_catalog


def find_brand_deals(brands: list[str], category_hint: str = "clothing") -> dict:
    deals = []
    for brand in brands[:5]:
        products = search_brand_catalog(brand=brand, query=f"sale {category_hint}", limit=10)
        if not products:
            deals.append(
                {
                    "brand": brand,
                    "discount_pct": 0,
                    "source": "serpapi_google_shopping",
                    "result_count": 0,
                }
            )
            continue

        best_discount = max(float(p.get("discount_pct", 0) or 0) for p in products)
        min_sale_price = min(float(p.get("sale_price", p.get("price", 0)) or 0) for p in products)
        deals.append(
            {
                "brand": brand,
                "discount_pct": round(best_discount, 2),
                "source": "serpapi_google_shopping",
                "result_count": len(products),
                "min_sale_price": round(min_sale_price, 2),
            }
        )

    return {"brands": brands[:5], "deals": deals}
