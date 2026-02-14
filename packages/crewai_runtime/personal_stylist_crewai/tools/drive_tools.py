def list_drive_photos(folder_id: str, photos: list[dict] | None = None, limit: int = 40) -> list[dict]:
    """Return normalized Drive photo records for STYLE_BRIEF analysis."""
    if photos is None:
        photos = []

    normalized: list[dict] = []
    for item in photos[: max(1, min(limit, 200))]:
        photo_id = item.get("id") or item.get("photo_id")
        if not photo_id:
            continue
        normalized.append(
            {
                "folder_id": folder_id,
                "id": str(photo_id),
                "name": str(item.get("name", "")),
                "mimeType": str(item.get("mimeType", "image/jpeg")),
                "createdTime": item.get("createdTime"),
                "webViewLink": item.get("webViewLink"),
                "thumbnailLink": item.get("thumbnailLink"),
                "image_uri": item.get("image_uri"),
                "data_uri": item.get("data_uri"),
            }
        )

    if normalized:
        return normalized

    # Keep a deterministic placeholder for local dry-runs when Drive is not wired.
    return [
        {
            "folder_id": folder_id,
            "id": "placeholder-photo-1",
            "name": "placeholder.jpg",
            "mimeType": "image/jpeg",
            "createdTime": None,
            "webViewLink": None,
            "thumbnailLink": None,
            "image_uri": None,
            "data_uri": None,
        }
    ]


def select_analysis_photos(photos: list[dict], max_images: int = 4) -> list[dict]:
    """Pick up to max_images photos to send to the multimodal model."""
    selected: list[dict] = []
    for photo in photos:
        photo_id = photo.get("id") or photo.get("photo_id")
        if not photo_id:
            continue
        selected.append(
            {
                "id": str(photo_id),
                "name": str(photo.get("name", "")),
                "data_uri": photo.get("data_uri"),
                "image_uri": photo.get("image_uri") or photo.get("webViewLink"),
            }
        )
        if len(selected) >= max(1, max_images):
            break
    return selected


def build_multimodal_messages(prompt: str, analysis_photos: list[dict]) -> list[dict]:
    """Build OpenAI-compatible multimodal user content blocks."""
    content: list[dict] = [{"type": "text", "text": prompt}]
    for photo in analysis_photos:
        image_url = photo.get("data_uri") or photo.get("image_uri")
        if not image_url:
            continue
        content.append(
            {
                "type": "image_url",
                "image_url": {"url": image_url},
            }
        )
    return content
