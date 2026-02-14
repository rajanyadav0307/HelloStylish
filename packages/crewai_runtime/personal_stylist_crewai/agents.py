from dataclasses import dataclass


@dataclass(frozen=True)
class AgentSpec:
    key: str
    role: str
    tools: tuple[str, ...] = ()
    model_capabilities: tuple[str, ...] = ()


AGENTS = {
    "stylist": AgentSpec(
        key="stylist",
        role="Build style brief from Google Drive photos using a multimodal LLM",
        tools=(
            "drive_tools.list_drive_photos",
            "drive_tools.select_analysis_photos",
            "drive_tools.build_multimodal_messages",
            "product_extract_tools.normalize_style_brief",
        ),
        model_capabilities=("vision", "structured_json"),
    ),
    "a1": AgentSpec(key="a1", role="Search brand catalogs"),
    "a2": AgentSpec(key="a2", role="Find active deals"),
    "ranker": AgentSpec(key="ranker", role="Rank outfit options"),
    "tryon": AgentSpec(key="tryon", role="Create try-on previews"),
    "checkout": AgentSpec(key="checkout", role="Prepare checkout draft"),
}
