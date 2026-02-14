from dataclasses import dataclass


@dataclass(frozen=True)
class TaskSpec:
    key: str
    agent_key: str
    objective: str
    requires_inputs: tuple[str, ...] = ()


TASK_KEYS = [
    "STYLE_BRIEF",
    "DEALS",
    "BRAND_SEARCH",
    "RANK",
    "TRYON",
    "CHECKOUT_DRAFT",
]

TASK_SPECS = {
    "STYLE_BRIEF": TaskSpec(
        key="STYLE_BRIEF",
        agent_key="stylist",
        objective="Analyze selected Google Drive photos with a multimodal LLM and produce a structured style brief.",
        requires_inputs=("drive_connection", "selected_drive_folder", "drive_photos"),
    ),
    "DEALS": TaskSpec(
        key="DEALS",
        agent_key="a2",
        objective="Find current discounts for shortlisted brands.",
        requires_inputs=("style_brief",),
    ),
    "BRAND_SEARCH": TaskSpec(
        key="BRAND_SEARCH",
        agent_key="a1",
        objective="Pull product candidates from brand catalogs.",
        requires_inputs=("style_brief", "deals"),
    ),
    "RANK": TaskSpec(
        key="RANK",
        agent_key="ranker",
        objective="Rank candidate items based on style fit, budget, and deal quality.",
        requires_inputs=("style_brief", "brand_search"),
    ),
    "TRYON": TaskSpec(
        key="TRYON",
        agent_key="tryon",
        objective="Generate try-on previews for top-ranked items.",
        requires_inputs=("rank",),
    ),
    "CHECKOUT_DRAFT": TaskSpec(
        key="CHECKOUT_DRAFT",
        agent_key="checkout",
        objective="Build a human-approval checkout draft for selected products.",
        requires_inputs=("rank",),
    ),
}
