import json
from pathlib import Path

from backend import main
from backend.main import PartCostRequest
from backend.storage import Storage


def test_manual_part_update_updates_only_selected_model_part_and_preserves_json(tmp_path):
    path = tmp_path / "repair_values.json"
    path.write_text(
        json.dumps(
            {
                "iPhone 14": {
                    "resale_value": 430,
                    "risk_buffer": 40,
                    "parts_pricing_status": "estimated",
                    "parts_pricing_note": "Existing note.",
                    "parts": {"screen_safe": 90, "back_glass": None},
                },
                "iPhone 13": {"parts": {"back_glass": 70}},
            }
        ),
        encoding="utf-8",
    )

    updated = main._update_repair_values_part(
        path,
        "iPhone 14",
        PartCostRequest(part="back_glass", cost=25, note="eBay replacement back glass estimate"),
    )
    parsed = json.loads(path.read_text(encoding="utf-8"))

    assert updated["iPhone 14"]["parts"]["back_glass"] == 25
    assert updated["iPhone 14"]["parts"]["screen_safe"] == 90
    assert updated["iPhone 13"]["parts"]["back_glass"] == 70
    assert parsed == updated
    assert updated["iPhone 14"]["parts_pricing_status"] == "manual_part_update"
    assert "Manual dashboard part price updated on" in updated["iPhone 14"]["parts_pricing_note"]
    assert "eBay replacement back glass estimate" in updated["iPhone 14"]["parts_pricing_note"]


def test_manual_part_update_creates_minimal_model_entry(tmp_path):
    path = tmp_path / "repair_values.json"
    path.write_text("{}", encoding="utf-8")

    updated = main._update_repair_values_part(
        path,
        "iPhone 18",
        PartCostRequest(part="battery", cost=35),
    )

    assert updated["iPhone 18"]["manual_review_allowed"] is True
    assert updated["iPhone 18"]["parts"]["battery"] == 35


def test_item_can_be_rescored_after_manual_part_update(monkeypatch, tmp_path):
    repair_path = tmp_path / "repair_values.json"
    repair_path.write_text(
        json.dumps(
            {
                "iPhone 14": {
                    "resale": {"low": 420, "mid": 500, "high": 580},
                    "risk_buffer": 40,
                    "manual_review_allowed": True,
                    "parts_pricing_status": "estimated",
                    "parts": {"back_glass": None},
                }
            }
        ),
        encoding="utf-8",
    )
    storage = Storage(Path(":memory:"))
    storage.upsert_item(
        {
            "item_id": "manual-rescore",
            "title": "Apple iPhone 14 128GB Blue Network Unlocked Cracked Back For Parts",
            "condition": "For parts or not working",
            "total_cost": 160,
            "status": "risky",
            "model": "iPhone 14",
            "estimated_parts_cost_available": False,
            "manual_review_reason": "Missing part price",
        }
    )
    repair_values = main._update_repair_values_part(
        repair_path,
        "iPhone 14",
        PartCostRequest(part="back_glass", cost=25),
    )
    monkeypatch.setattr(main, "repair_values", repair_values)
    monkeypatch.setattr(main, "resale_research", {})
    monkeypatch.setattr(main, "storage", storage)

    rescored = main._rescore_stored_item(storage.get_item("manual-rescore"))

    assert rescored["estimated_parts_cost_available"] is True
    assert rescored["estimated_parts_cost"] == 25
    assert rescored["estimated_profit_available"] is True
    assert rescored["profit_mid"] == 275
