import json
from pathlib import Path


REPAIR_VALUES_PATH = Path("backend/data/repair_values.json")
BACK_GLASS_RESEARCH_PATH = Path("backend/data/back_glass_research.json")


def test_back_glass_research_values_are_merged():
    repair_values = _load_json(REPAIR_VALUES_PATH)
    research = _load_json(BACK_GLASS_RESEARCH_PATH)

    for model, research_entry in research.items():
        assert repair_values[model]["parts"]["back_glass"] == research_entry["parts"]["back_glass"]
        assert repair_values[model]["back_glass_research"] == research_entry["back_glass_research"]


def test_back_glass_merge_preserves_existing_non_back_glass_part_values():
    repair_values = _load_json(REPAIR_VALUES_PATH)

    assert repair_values["iPhone 15 Pro"]["parts"]["screen_safe"] == 74.95
    assert repair_values["iPhone 15 Pro"]["parts"]["battery"] == 18.95
    assert repair_values["iPhone 15 Pro"]["parts"]["charging_port"] == 22.0
    assert repair_values["iPhone 15 Pro"]["parts"]["camera_lens"] is None

    assert repair_values["iPhone 13"]["parts"]["screen_safe"] == 46.8
    assert repair_values["iPhone 13"]["parts"]["battery"] == 10.95
    assert repair_values["iPhone 13"]["parts"]["charging_port"] == 6.95
    assert repair_values["iPhone 13"]["parts"]["camera_lens"] == 1.95


def test_low_confidence_back_glass_keeps_conservative_warning_behavior():
    repair_values = _load_json(REPAIR_VALUES_PATH)

    low_confidence_models = [
        model
        for model, entry in repair_values.items()
        if (entry.get("back_glass_research") or {}).get("confidence") == "low"
    ]

    assert "iPhone 13" in low_confidence_models
    for model in low_confidence_models:
        entry = repair_values[model]
        assert entry["parts_pricing_status"] != "verified_screenshot"
        assert entry["parts_pricing_status"] != "verified_screenshot_and_page"
        assert "low-confidence eBay active listing research" in entry["parts_pricing_note"]


def _load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))
