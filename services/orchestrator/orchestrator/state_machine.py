LOCKED_STEP_ORDER = [
    ("STYLE_BRIEF", "stylist"),
    ("DEALS", "a2"),
    ("BRAND_SEARCH", "a1"),
    ("RANK", "ranker"),
    ("TRYON", "tryon"),
    ("CHECKOUT_DRAFT", "checkout"),
]


def ordered_step_keys() -> list[str]:
    return [key for key, _ in LOCKED_STEP_ORDER]


def next_step_key(step_key: str | None) -> str | None:
    keys = ordered_step_keys()
    if step_key is None:
        return keys[0] if keys else None
    if step_key not in keys:
        return None
    idx = keys.index(step_key)
    if idx + 1 >= len(keys):
        return None
    return keys[idx + 1]


def agent_for(step_key: str) -> str:
    for key, agent in LOCKED_STEP_ORDER:
        if key == step_key:
            return agent
    raise KeyError(step_key)
