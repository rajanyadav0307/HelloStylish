def build_checkout_draft(items: list[dict]) -> dict:
    return {"items": items, "approval_required": True}
