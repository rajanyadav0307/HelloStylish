from pathlib import Path
import sys
import types
import uuid

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

if "PIL" not in sys.modules:
    pil_module = types.ModuleType("PIL")
    pil_module.Image = types.SimpleNamespace(Image=object, open=lambda *_args, **_kwargs: None)
    sys.modules["PIL"] = pil_module

if "workers.common.db" not in sys.modules:
    db_module = types.ModuleType("workers.common.db")
    db_module.exec_one = lambda *_args, **_kwargs: None
    db_module.exec_write = lambda *_args, **_kwargs: 0
    sys.modules["workers.common.db"] = db_module

from workers.executors import crewai_step_executor as executor  # noqa: E402


def test_style_brief_onboarding_when_drive_not_connected(monkeypatch):
    monkeypatch.setattr(executor, "_get_run_user", lambda _run_id: uuid.uuid4())
    monkeypatch.setattr(executor, "_ensure_drive_access_token", lambda _user_id: None)
    monkeypatch.setattr(executor, "_get_selected_drive_folder", lambda _user_id: None)

    payload = executor._style_brief_payload(uuid.uuid4())

    assert payload["analysis_method"] == "none"
    assert payload["requires_drive_connection"] is True
    assert "Connect Google Drive" in payload["message"]


def test_style_brief_uses_multimodal_path(monkeypatch):
    user_id = uuid.uuid4()
    photos = [
        {"id": "p-1", "name": "look-1.jpg"},
        {"id": "p-2", "name": "look-2.jpg"},
    ]
    prepared = [
        {"id": "p-1", "name": "look-1.jpg", "data_uri": "data:image/jpeg;base64,AAAA"},
        {"id": "p-2", "name": "look-2.jpg", "data_uri": "data:image/jpeg;base64,BBBB"},
    ]
    llm_style = {
        "style_summary": "Relaxed smart-casual with neutral tones.",
        "observed_features": {"silhouette": "straight", "fit_preference": "tailored"},
        "palette": ["navy", "cream", "olive"],
        "inferred_vibes": ["casual", "formal"],
        "recommended_categories": ["dress", "top", "bottom"],
        "recommended_brands": ["Mango", "Zara"],
        "avoid_colors": ["neon green"],
        "budget_max": 180,
        "confidence_notes": "High confidence from 2 clear outfit photos.",
    }
    captured = {}

    monkeypatch.setattr(executor, "_get_run_user", lambda _run_id: user_id)
    monkeypatch.setattr(executor, "_ensure_drive_access_token", lambda _user_id: "token-123")
    monkeypatch.setattr(
        executor,
        "_get_selected_drive_folder",
        lambda _user_id: {"folder_id": "folder-1", "folder_name": "Outfits"},
    )
    monkeypatch.setattr(executor, "_drive_list_images", lambda _token, _folder_id, limit=40: photos)
    monkeypatch.setattr(
        executor,
        "_prepare_images_for_multimodal_analysis",
        lambda _token, _photos, max_images=4: prepared,
    )

    def fake_llm(images):
        captured["images"] = images
        return llm_style

    monkeypatch.setattr(executor, "_call_multimodal_style_agent", fake_llm)

    payload = executor._style_brief_payload(uuid.uuid4())

    assert payload["analysis_method"] == "multimodal_llm"
    assert payload["source"] == "google_drive"
    assert payload["analyzed_photo_ids"] == ["p-1", "p-2"]
    assert payload["style_summary"] == llm_style["style_summary"]
    assert captured["images"] == prepared


def test_style_brief_falls_back_when_multimodal_errors(monkeypatch):
    user_id = uuid.uuid4()
    photos = [{"id": "p-1", "name": "look-1.jpg"}]
    prepared = [{"id": "p-1", "name": "look-1.jpg", "data_uri": "data:image/jpeg;base64,AAAA"}]

    monkeypatch.setattr(executor, "_get_run_user", lambda _run_id: user_id)
    monkeypatch.setattr(executor, "_ensure_drive_access_token", lambda _user_id: "token-123")
    monkeypatch.setattr(
        executor,
        "_get_selected_drive_folder",
        lambda _user_id: {"folder_id": "folder-1", "folder_name": "Outfits"},
    )
    monkeypatch.setattr(executor, "_drive_list_images", lambda _token, _folder_id, limit=40: photos)
    monkeypatch.setattr(
        executor,
        "_prepare_images_for_multimodal_analysis",
        lambda _token, _photos, max_images=4: prepared,
    )
    monkeypatch.setattr(
        executor,
        "_call_multimodal_style_agent",
        lambda _images: (_ for _ in ()).throw(RuntimeError("vision-model-unavailable")),
    )

    def fake_fallback(access_token, folder, photos, reason):
        return {
            "analysis_method": "heuristic_fallback",
            "photo_count": len(photos),
            "reason": reason,
            "folder_name": folder["folder_name"],
            "access_token": access_token,
        }

    monkeypatch.setattr(executor, "_style_brief_fallback", fake_fallback)

    payload = executor._style_brief_payload(uuid.uuid4())

    assert payload["analysis_method"] == "heuristic_fallback"
    assert payload["photo_count"] == 1
    assert payload["folder_name"] == "Outfits"
    assert payload["access_token"] == "token-123"
    assert "vision-model-unavailable" in payload["reason"]


def test_call_multimodal_style_agent_requires_api_key(monkeypatch):
    monkeypatch.setattr(executor, "OPENAI_API_KEY", "")
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        executor._call_multimodal_style_agent(
            [{"id": "p-1", "name": "look-1.jpg", "data_uri": "data:image/jpeg;base64,AAAA"}]
        )


def test_deals_payload_mock_mode(monkeypatch):
    monkeypatch.setattr(executor, "PRODUCT_DATA_MODE", "mock")
    monkeypatch.setattr(executor, "SERPAPI_API_KEY", "")
    monkeypatch.setattr(
        executor,
        "_get_artifact",
        lambda _run_id, kind: {"recommended_brands": ["Zara", "H&M"]} if kind == "style_brief" else {},
    )

    payload = executor._deals_payload(uuid.uuid4())

    assert payload["data_mode"] == "mock"
    assert payload["provider"] == "synthetic"
    assert len(payload["deals"]) == 2


def test_brand_search_payload_real_mode(monkeypatch):
    monkeypatch.setattr(executor, "PRODUCT_DATA_MODE", "auto")
    monkeypatch.setattr(executor, "SERPAPI_API_KEY", "test-key")
    monkeypatch.setattr(
        executor,
        "_get_artifact",
        lambda _run_id, kind: (
            {
                "recommended_brands": ["Zara"],
                "recommended_categories": ["dress"],
                "palette": ["navy", "cream"],
                "budget_max": 200,
            }
            if kind == "style_brief"
            else {"deals": [{"brand": "Zara", "discount_pct": 20}]}
        ),
    )
    monkeypatch.setattr(
        executor,
        "_serpapi_shopping_search",
        lambda _query, num=20: [
            {
                "title": "Zara Navy Midi Dress - 20% off",
                "link": "https://example.com/zara-dress",
                "source": "Zara",
                "extracted_price": 80.0,
                "extracted_old_price": 100.0,
                "thumbnail": "https://example.com/image.jpg",
            }
        ],
    )

    payload = executor._brand_search_payload(uuid.uuid4())

    assert payload["data_mode"] == "serpapi"
    assert payload["provider"] == "serpapi_google_shopping"
    assert len(payload["product_candidates"]) >= 1
    candidate = payload["product_candidates"][0]
    assert candidate["brand"] == "Zara"
    assert candidate["product_url"] == "https://example.com/zara-dress"
    assert candidate["data_source"] == "serpapi"


def test_checkout_prefers_product_url(monkeypatch):
    monkeypatch.setattr(
        executor,
        "_get_artifact",
        lambda _run_id, _kind: {
            "ranked_items": [
                {
                    "sku": "zara-dre-123",
                    "title": "Zara Navy Dress",
                    "brand": "Zara",
                    "sale_price": 79.99,
                    "source": "Zara",
                    "product_url": "https://example.com/zara-dress",
                }
            ]
        },
    )

    payload = executor._checkout_payload(uuid.uuid4())
    checkout_item = payload["checkout_draft"]["items"][0]
    assert checkout_item["checkout_url"] == "https://example.com/zara-dress"
