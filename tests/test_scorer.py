import json
from pathlib import Path

from backend.config import load_resale_research, load_scoring_rules
from backend.ebay_client import clean_description
from backend.alerting import evaluate_alert_decision
from backend.main import _build_decision_trace, should_notify_item
from backend.scorer import detect_model, detect_storage, extract_description_signals, score_listing
from backend.storage import Storage


REPAIR_VALUES = {
    "default": {
        "resale_value": 250,
        "estimated_parts_cost": 80,
        "risk_buffer": 50,
        "screen_cost": 100,
        "battery_cost": 50,
        "back_glass_cost": 80,
    },
    "iPhone 14 Pro": {
        "resale_value": 600,
        "estimated_parts_cost": 120,
        "risk_buffer": 80,
        "screen_cost": 180,
        "battery_cost": 65,
        "back_glass_cost": 130,
    },
    "iPhone 14 Pro Max": {
        "resale_value": 700,
        "estimated_parts_cost": 140,
        "risk_buffer": 90,
        "screen_cost": 220,
        "battery_cost": 70,
        "back_glass_cost": 150,
    },
    "iPhone 13 Pro": {
        "resale_value": 470,
        "estimated_parts_cost": 115,
        "risk_buffer": 75,
        "screen_cost": 165,
        "battery_cost": 60,
        "back_glass_cost": 125,
    },
}

SCORING_RULES = load_scoring_rules(Path("backend/data/scoring_rules.json"))
SAMPLES = {
    sample["name"]: sample["listing"]
    for sample in json.loads(Path("tests/fixtures/sample_listings.json").read_text(encoding="utf-8"))
}


def score_sample(name):
    return score_listing(
        SAMPLES[name],
        REPAIR_VALUES,
        scoring_rules=SCORING_RULES,
        min_score_to_alert=70,
        min_profit_to_alert=75,
        risky_score_range=(35, 69.99),
    )


def test_clean_description_strips_html_entities_and_boilerplate():
    raw = """
    <html><body>
      <style>.x { color: red; }</style>
      <script>alert("x")</script>
      <p>Apple iPhone 13 Pro &amp; charger.</p>
      <div>No IC READ&nbsp;but Face ID works.<br>Clean IMEI verified.</div>
      <p>Powered by eBay template</p>
    </body></html>
    """

    cleaned = clean_description(raw)

    assert cleaned == "Apple iPhone 13 Pro & charger.\nNo IC READ but Face ID works.\nClean IMEI verified."
    assert "<" not in cleaned
    assert ">" not in cleaned
    assert "&amp;" not in cleaned
    assert "Powered by" not in cleaned


def test_hard_reject_overrides_good_signs():
    result = score_listing(
        {
            "title": "iPhone 14 Pro cracked screen powers on clean IMEI",
            "condition": "Used",
            "raw_description": "Activation locked. Face ID works.",
            "total_cost": 100,
        },
        REPAIR_VALUES,
        scoring_rules=SCORING_RULES,
    )

    assert result.status == "rejected"
    assert result.score == -100
    assert "activation_locked" in result.hard_reject_flags


def test_candidate_when_profit_and_score_clear_thresholds():
    result = score_listing(
        {
            "title": "iPhone 14 Pro cracked screen powers on unlocked clean IMEI Face ID works",
            "condition": "For parts or repair",
            "raw_description": "Only issue is cracked screen.",
            "total_cost": 190,
        },
        REPAIR_VALUES,
        scoring_rules=SCORING_RULES,
        min_score_to_alert=70,
        min_profit_to_alert=75,
    )

    assert result.status == "candidate"
    assert result.estimated_profit == 150
    assert result.score >= 70
    assert "cracked_screen" in result.positive_flags


def test_detail_risk_phrase_no_ic_read_blocks_best_find():
    result = score_listing(
        {
            "title": "iPhone 14 Pro cracked screen powers on clean IMEI No IC READ",
            "condition": "For parts or repair",
            "raw_description": "Face ID works.",
            "total_cost": 190,
        },
        REPAIR_VALUES,
        scoring_rules=SCORING_RULES,
        min_score_to_alert=70,
        min_profit_to_alert=75,
    )

    assert result.status == "risky"
    assert result.alert_eligible is False
    assert "no_ic_read" in result.risk_flags
    assert "No IC READ" in result.manual_review_reason


def test_low_profit_listing_is_risky_even_with_positive_signs():
    result = score_listing(
        {
            "title": "iPhone 14 Pro cracked screen powers on unlocked clean IMEI",
            "condition": "Used",
            "raw_description": "",
            "total_cost": 380,
        },
        REPAIR_VALUES,
        scoring_rules=SCORING_RULES,
        min_score_to_alert=70,
        min_profit_to_alert=75,
    )

    assert result.status == "risky"
    assert result.estimated_profit < 75


def test_detect_model_uses_longest_match_first():
    model = detect_model("Apple iPhone 14 Pro Max cracked back glass", REPAIR_VALUES)

    assert model == "iPhone 14 Pro Max"


def test_detect_model_knows_unpriced_modern_models():
    repair_values = {
        "default": REPAIR_VALUES["default"],
        "iPhone 13": {"resale_value": 330},
    }
    examples = {
        "Apple iPhone 12 Pro Max 128GB Unlocked Cracked Screen": "iPhone 12 Pro Max",
        "Apple iPhone 12 cracked back": "iPhone 12",
        "Apple iPhone 11 bad LCD": "iPhone 11",
        "iPhone X screen lines": "iPhone X",
        "iPhone XR black spot": "iPhone XR",
        "iPhone XS Max damaged LCD": "iPhone XS Max",
        "iPhone SE 2nd Gen bad battery": "iPhone SE 2nd Gen",
        "iPhone SE 3rd Gen cracked screen": "iPhone SE 3rd Gen",
        "iPhone SE for repair": "iPhone SE",
    }

    for title, expected_model in examples.items():
        assert detect_model(title, repair_values) == expected_model


def test_storage_detection_from_title_supported_capacities():
    examples = {
        "Apple iPhone 12 64GB Unlocked Cracked Screen": "64GB",
        "Apple iPhone 13 Pro 128GB Unlocked Cracked Screen": "128GB",
        "iPhone 13 Pro 256GB": "256GB",
        "Apple iPhone 14 Pro Max 512GB": "512GB",
        "Apple iPhone 15 Pro A2848 - Unlocked 1TB": "1TB",
    }

    for title, expected in examples.items():
        detected = detect_storage({"title": title})
        assert detected["storage_capacity"] == expected
        assert detected["storage_confidence"] == "high"
        assert detected["storage_source"] == "title"


def test_storage_detection_from_spaced_title_and_raw_aspects():
    spaced = detect_storage({"title": "Apple iPhone 14 Pro 128 GB Cracked Screen"})
    from_aspects = detect_storage(
        {
            "title": "Apple iPhone 14 Pro Cracked Screen",
            "raw_json": {
                "details": {
                    "localizedAspects": [
                        {"name": "Storage Capacity", "value": "256 GB"},
                    ]
                }
            },
        }
    )

    assert spaced["storage_capacity"] == "128GB"
    assert from_aspects["storage_capacity"] == "256GB"
    assert from_aspects["storage_source"] == "item_aspects"


def test_storage_detection_handles_128gb_title_variants():
    titles = [
        "Apple iPhone 14 128GB Blue (Network Unlocked) BH 84% - Cracked Back - For Parts",
        "Apple iPhone 14 - 128GB Blue",
        "iPhone 14 128 GB Network Unlocked",
        "iPhone 14 128gb",
        "Apple iPhone 14 128GB",
    ]

    for title in titles:
        assert detect_storage({"title": title})["storage_capacity"] == "128GB"

    assert detect_storage({"title": "Apple iPhone 14 Blue Network Unlocked"})["storage_capacity"] is None


def test_nested_parts_structure_is_used_for_costs():
    repair_values = {
        "default": REPAIR_VALUES["default"],
        "iPhone 14 Pro": {
            "resale_value": 610,
            "risk_buffer": 85,
            "estimated_parts_cost": 135,
            "parts": {
                "screen_budget": 80,
                "screen_safe": 125,
                "screen_premium": 180,
                "battery": 35,
                "back_glass": 90,
                "camera_lens": 20,
                "charging_port": 50,
            },
        },
    }

    result = score_listing(
        {
            "title": "iPhone 14 Pro cracked screen powers on unlocked clean IMEI",
            "condition": "Used",
            "raw_description": "",
            "total_cost": 200,
        },
        repair_values,
        scoring_rules=SCORING_RULES,
        min_score_to_alert=70,
        min_profit_to_alert=75,
    )

    assert result.estimated_parts_cost == 125


def test_verified_parts_pricing_allows_normal_profit_scoring():
    repair_values = {
        "iPhone 15 Pro": {
            "resale_value": 760,
            "risk_buffer": 95,
            "manual_review_allowed": True,
            "parts_pricing_status": "verified_screenshot_and_page",
            "parts_pricing_note": "Verified from screenshot and page.",
            "parts": {"screen_safe": 75},
        },
    }

    result = score_listing(
        {
            "title": "iPhone 15 Pro cracked screen powers on unlocked clean IMEI",
            "condition": "Used",
            "raw_description": "Face ID works.",
            "total_cost": 240,
        },
        repair_values,
        scoring_rules=SCORING_RULES,
        min_score_to_alert=70,
        min_profit_to_alert=75,
    )

    assert result.status == "candidate"
    assert result.estimated_parts_cost == 75
    assert result.estimated_profit == 350
    assert result.estimated_profit_available is True
    assert result.parts_pricing_label == "Verified parts"


def test_estimated_parts_pricing_calculates_profit_but_warns():
    repair_values = {
        "iPhone 14 Pro": {
            "resale_value": 610,
            "risk_buffer": 85,
            "manual_review_allowed": True,
            "parts_pricing_status": "estimated",
            "parts_pricing_note": "Estimated from neighboring model.",
            "parts": {"screen_safe": 45},
        },
    }

    result = score_listing(
        {
            "title": "iPhone 14 Pro cracked screen powers on unlocked clean IMEI",
            "condition": "Used",
            "raw_description": "Face ID works.",
            "total_cost": 220,
        },
        repair_values,
        scoring_rules=SCORING_RULES,
        min_score_to_alert=70,
        min_profit_to_alert=75,
    )

    assert result.status == "candidate"
    assert result.estimated_profit == 260
    assert result.estimated_profit_available is True
    assert result.parts_pricing_label == "Parts estimate not verified"
    assert result.pricing_warning == "Parts estimate not verified"


def test_missing_required_part_cost_becomes_manual_review_candidate():
    repair_values = {
        "iPhone 16 Pro": {
            "resale_value": 700,
            "risk_buffer": 95,
            "manual_review_allowed": True,
            "parts_pricing_status": "verified_screenshot",
            "parts_pricing_note": "Screen price was not visible.",
            "parts": {"screen_safe": None},
        },
    }

    result = score_listing(
        {
            "title": "iPhone 16 Pro cracked screen powers on unlocked clean IMEI",
            "condition": "Used",
            "raw_description": "Face ID works.",
            "total_cost": 230,
        },
        repair_values,
        scoring_rules=SCORING_RULES,
        min_score_to_alert=70,
        min_profit_to_alert=75,
    )

    assert result.status == "risky"
    assert result.estimated_parts_cost_available is False
    assert result.estimated_profit_available is False
    assert result.estimated_profit == 0
    assert result.parts_pricing_label == "Actual part price not on file"
    assert result.pricing_warning == "Estimated profit unavailable — part price missing or issue unknown"
    assert result.manual_review_needed is True


def test_missing_required_part_cost_can_notify_once_for_manual_review():
    repair_values = {
        "iPhone 16 Pro": {
            "resale_value": 700,
            "risk_buffer": 95,
            "manual_review_allowed": True,
            "parts_pricing_status": "verified_screenshot",
            "parts": {"screen_safe": None},
        },
    }
    listing = {
        "item_id": "manual-review-1",
        "title": "iPhone 16 Pro cracked screen powers on unlocked clean IMEI",
        "condition": "Used",
        "raw_description": "Face ID works.",
        "total_cost": 230,
    }

    result = score_listing(
        listing,
        repair_values,
        scoring_rules=SCORING_RULES,
        min_score_to_alert=70,
        min_profit_to_alert=75,
    )
    item = {**listing, **result.as_item_fields()}
    storage = Storage(Path(":memory:"))

    storage.upsert_item(item)
    assert not should_notify_item(item, result, _settings())
    storage.mark_alerted(item["item_id"])
    assert storage.was_alerted(item["item_id"])


def test_missing_resale_value_is_not_reported_as_real_profit():
    repair_values = {
        "iPhone 17 Pro": {
            "resale_value": 0,
            "risk_buffer": 110,
            "manual_review_allowed": True,
            "parts_pricing_status": "verified_screenshot",
            "parts_pricing_note": "Parts visible, resale not configured.",
            "parts": {"screen_safe": 103},
        },
    }

    result = score_listing(
        {
            "title": "iPhone 17 Pro cracked screen powers on unlocked clean IMEI",
            "condition": "Used",
            "raw_description": "Face ID works.",
            "total_cost": 350,
        },
        repair_values,
        scoring_rules=SCORING_RULES,
        min_score_to_alert=70,
        min_profit_to_alert=75,
    )

    assert result.status == "risky"
    assert result.estimated_profit_available is False
    assert result.estimated_profit == 0
    assert result.parts_pricing_label == "Resale value missing"
    assert result.pricing_warning == "Estimated profit unavailable — resale value missing"


def test_accessory_and_part_listings_are_rejected_before_model_scoring():
    listings = {
        "reader_adapter_not_phone": "USB C SD Card Reader for iPhone 15 16 iPad Mac",
        "housing_not_phone": "OEM Apple iPhone 14 Plus Back Glass Rear Housing Midnight Camera Ring Crack",
        "housing_not_phone": "Genuine iPhone 13 Midnight Cracked Glass back Housing with small parts",
        "camera_lens_part_not_phone": "iPhone 14 camera ring camera protector replacement",
        "screen_protector_not_phone": "iPhone 15 Pro Max tempered glass screen protector privacy glass",
        "battery_part_not_phone": "Battery for iPhone 14 replacement part",
        "charging_port_part_not_phone": "Charging port flex cable for iPhone 13",
        "motherboard": "iPhone 14 Pro motherboard logic board repair part",
    }

    for expected_flag, title in listings.items():
        result = score_listing(
            {"title": title, "condition": "New", "total_cost": 20},
            REPAIR_VALUES,
            scoring_rules=SCORING_RULES,
        )
        assert result.status == "rejected"
        assert result.model == "unknown"
        assert expected_flag in result.hard_reject_flags or expected_flag in result.listing_classification_flags
        assert result.whole_phone_confidence_passed is False
        assert "Accessory/part listing" in result.manual_review_reason


def test_display_screen_part_listings_are_rejected_as_not_whole_phone():
    listings = {
        "screen_part_not_phone": "CRACKED GLASS iPhone 15 Screen OEM OLED Original PARTS ONLY",
        "oled_lcd_part_not_phone": "CRACKED GLASS iPhone 14 Plus Screen Glass OLED LCD Original OEM PARTS ONLY",
        "digitizer_not_phone": "iPhone 15 Screen OLED OEM Display Screen Digitizer CRACKED PARTS ONLY",
        "display_assembly_not_phone": "iPhone 13 Pro Oem Oled LCD Screen Glass Digitizer Assembly PARTS ONLY",
        "lot_not_single_phone": "2x OEM Original Apple iPhone 14 Pro Max PARTS ONLY LOT OF 2",
    }

    for expected_flag, title in listings.items():
        result = score_listing(
            {"title": title, "condition": "For parts or not working", "total_cost": 55},
            {
                **REPAIR_VALUES,
                "iPhone 15": {"resale_value": 580, "parts": {"screen_safe": 78}},
                "iPhone 14 Plus": {"resale_value": 380, "parts": {"screen_safe": 50}},
            },
            scoring_rules=SCORING_RULES,
        )

        assert result.status == "rejected"
        assert result.model == "unknown"
        assert result.whole_phone_confidence_passed is False
        assert expected_flag in result.hard_reject_flags or expected_flag in result.listing_classification_flags
        assert "Screen/display part listing" in result.manual_review_reason or expected_flag == "lot_not_single_phone"
        assert result.estimated_profit_available is False


def test_card_reader_for_iphone_does_not_detect_iphone_15_model():
    result = score_listing(
        {
            "title": "USB C SD Card Reader for iPhone 15 16 iPad Mac",
            "condition": "New",
            "total_cost": 12,
        },
        {"default": REPAIR_VALUES["default"], "iPhone 15": {"resale_value": 580, "parts": {"screen_safe": 80}}},
        scoring_rules=SCORING_RULES,
    )

    assert result.status == "rejected"
    assert result.model == "unknown"
    assert "reader_adapter_not_phone" in result.hard_reject_flags
    assert result.estimated_profit_available is False


def test_locked_to_owner_is_hard_rejected():
    for title in ["Apple iPhone 14 - Locked To Owner", "Apple iPhone 13 - owner locked"]:
        result = score_listing(
            {"title": title, "condition": "Used", "total_cost": 120},
            {"iPhone 14": {"resale_value": 430}, "iPhone 13": {"resale_value": 330}},
            scoring_rules=SCORING_RULES,
        )

        assert result.status == "rejected"
        assert "activation_locked" in result.hard_reject_flags


def test_does_not_power_on_is_hard_rejected_without_positive_powers_on():
    result = score_listing(
        {
            "title": "Apple iPhone 14 128GB Unlocked Does Not Power On",
            "condition": "For parts or not working",
            "total_cost": 120,
        },
        {"iPhone 14": {"resale_value": 430, "parts": {"screen_safe": 44}}},
        scoring_rules=SCORING_RULES,
    )

    assert result.status == "rejected"
    assert "no_power" in result.hard_reject_flags
    assert "powers_on" not in result.positive_flags


def test_accessories_housing_and_lots_do_not_pass_whole_phone_confidence():
    listings = [
        ("accessory_not_phone", "Sony MHC-EC909iP Hi-Fi System w/ iPod/iPhone"),
        ("accessory_not_phone", "hohem iSteady X3 SE Plus Gimbal Stabilizer for iPhone"),
        ("housing_not_phone", "OEM Apple iPhone 11 Housing Black Housing Back Glass"),
        ("lot_not_single_phone", "Set Of 3 Apple iPhone Only Parts"),
        ("lot_not_single_phone", "Phone lot of iPhones and android phone"),
        ("lot_not_single_phone", "Apple iPhone Lot 8 Smartphones Mixed Models Parts Repair"),
    ]

    for expected_flag, title in listings:
        result = score_listing(
            {"title": title, "condition": "Used", "total_cost": 80},
            {"iPhone 11": {"resale_value": 250}, "iPhone 14": {"resale_value": 430}},
            scoring_rules=SCORING_RULES,
        )

        assert result.status == "rejected"
        assert result.whole_phone_confidence_passed is False
        assert expected_flag in result.hard_reject_flags or expected_flag in result.listing_classification_flags


def test_screen_lines_and_bad_lcd_count_as_screen_repair_issues():
    repair_values = {
        "iPhone 14": {
            "resale_value": 430,
            "risk_buffer": 70,
            "parts": {"screen_safe": 44},
        }
    }
    titles = [
        "Apple iPhone 14 128GB Unlocked screen lines",
        "Apple iPhone 14 128GB Unlocked black spot",
        "Apple iPhone 14 128GB Unlocked vertical lines",
        "Apple iPhone 14 128GB Unlocked bad LCD",
        "Apple iPhone 14 128GB Unlocked damaged LCD",
        "Apple iPhone 14 128GB Unlocked LCD screen damaged",
        "Apple iPhone 14 128GB Unlocked screen has lines",
        "Apple iPhone 14 128GB Unlocked display lines",
    ]

    for title in titles:
        result = score_listing(
            {"title": title, "condition": "Used", "total_cost": 160},
            repair_values,
            scoring_rules=SCORING_RULES,
            min_score_to_alert=70,
            min_profit_to_alert=75,
        )

        assert result.whole_phone_confidence_passed is True
        assert result.has_repair_issue is True
        assert "screen_display_issue" in result.positive_flags
        assert result.estimated_parts_cost == 44
        assert result.estimated_profit_available is True


def test_working_phone_without_repair_issue_is_risky_and_not_alerted():
    listing = {
        "item_id": "clean-working-14",
        "title": "Apple iPhone 14 128GB Unlocked Works 90% Battery",
        "condition": "Used",
        "total_cost": 260,
    }

    result = score_listing(
        listing,
        {
            "iPhone 14": {
                "resale_value": 430,
                "risk_buffer": 70,
                "parts": {"battery": 15, "screen_safe": 44},
            }
        },
        scoring_rules=SCORING_RULES,
        min_score_to_alert=70,
        min_profit_to_alert=75,
    )
    item = {**listing, **result.as_item_fields()}

    assert result.status == "risky"
    assert result.has_repair_issue is False
    assert result.estimated_profit_available is False
    assert result.pricing_warning == "Estimated profit unavailable — no specific repair issue detected"
    assert "no_detected_repair_issue" in result.risk_flags
    assert not should_notify_item(item, result, _settings())


def test_generic_parts_only_with_no_specific_issue_has_no_fake_profit():
    result = score_listing(
        {
            "title": "Apple iPhone 14 128GB Unlocked Parts Only Read Description",
            "condition": "For parts or not working",
            "total_cost": 100,
        },
        {
            "iPhone 14": {
                "resale_value": 430,
                "risk_buffer": 70,
                "manual_review_allowed": True,
                "parts": {"screen_safe": 44, "battery": 15},
            }
        },
        scoring_rules=SCORING_RULES,
        min_score_to_alert=70,
        min_profit_to_alert=75,
    )

    assert result.whole_phone_confidence_passed is True
    assert result.has_repair_issue is True
    assert result.estimated_profit_available is False
    assert result.estimated_profit == 0
    assert result.pricing_warning == "Estimated profit unavailable — part price missing or issue unknown"
    assert "Parts-only ambiguous" in result.manual_review_reason
    assert "Read description listing" in result.manual_review_reason


def test_unknown_old_iphone_model_does_not_use_default_resale_for_alerts():
    listing = {
        "item_id": "iphone-3g",
        "title": "Apple iPhone 3G 8GB Unlocked Cracked Screen For Repair",
        "condition": "For parts or not working",
        "total_cost": 20,
    }

    result = score_listing(
        listing,
        REPAIR_VALUES,
        scoring_rules=SCORING_RULES,
        min_score_to_alert=70,
        min_profit_to_alert=75,
    )
    item = {**listing, **result.as_item_fields()}

    assert result.model == "unknown"
    assert result.status == "rejected"
    assert "old_model_ignored" in result.hard_reject_flags
    assert result.estimated_profit_available is False
    assert result.pricing_warning == "Model unknown — manual review needed"
    assert not should_notify_item(item, result, _settings())


def test_complete_phone_repair_titles_can_be_candidates():
    repair_values = {
        "iPhone 14 Pro": {
            "resale_value": 610,
            "risk_buffer": 85,
            "parts": {"back_glass": 30, "screen_safe": 45},
        },
        "iPhone 12 Pro Max": {
            "resale_value": 420,
            "risk_buffer": 70,
            "parts": {"battery": 25},
        },
        "iPhone 14": {
            "resale_value": 430,
            "risk_buffer": 70,
            "parts": {"back_glass": 17, "screen_safe": 44},
        },
    }
    listings = [
        {
            "title": "Apple iPhone 14 Pro 128GB Unlocked Space Black Cracked Back Glass",
            "condition": "Used",
            "total_cost": 260,
        },
        {
            "title": "Broken Unlocked Apple iPhone 12 Pro Max 128GB Bad Battery",
            "condition": "For parts or not working",
            "total_cost": 170,
        },
        {
            "title": "Apple iPhone 14 128GB Cracked Screen & Back Parts/Repair",
            "condition": "For parts or not working",
            "total_cost": 180,
        },
    ]

    for listing in listings:
        result = score_listing(
            listing,
            repair_values,
            scoring_rules=SCORING_RULES,
            min_score_to_alert=70,
            min_profit_to_alert=75,
        )
        assert result.status == "candidate"
        assert result.whole_phone_confidence_passed is True
        assert result.has_repair_issue is True


def test_ic_issue_whole_phone_goes_to_risky_manual_review():
    listing = {
        "title": "iPhone 15 128GB Unlocked Cracked Back Bad OLED Swollen Battery No IC",
        "condition": "For parts or not working",
        "total_cost": 180,
    }
    repair_values = {
        "iPhone 15": {
            "resale_value": 580,
            "risk_buffer": 85,
            "manual_review_allowed": True,
            "parts": {"screen_safe": 78, "back_glass": 29, "battery": 10},
        }
    }

    result = score_listing(
        listing,
        repair_values,
        scoring_rules=SCORING_RULES,
        min_score_to_alert=70,
        min_profit_to_alert=75,
    )

    assert result.whole_phone_confidence_passed is True
    assert result.has_repair_issue is True
    assert result.status == "risky"
    assert "ic_issue" in result.risk_flags
    assert result.manual_review_needed is True


def test_live_good_whole_phone_titles_still_pass_whole_phone_confidence():
    repair_values = {
        "iPhone 14 Pro Max": {
            "resale_value": 690,
            "risk_buffer": 90,
            "parts": {"screen_safe": 50, "back_glass": 35, "battery": 13},
        },
        "iPhone 13 Pro Max": {
            "resale_value": 540,
            "risk_buffer": 80,
            "parts": {"screen_safe": 62, "back_glass": 45, "battery": 14},
        },
        "iPhone 14 Pro": {
            "resale_value": 610,
            "risk_buffer": 85,
            "parts": {"screen_safe": 45, "back_glass": 30},
        },
        "iPhone 14": {
            "resale_value": 430,
            "risk_buffer": 70,
            "parts": {"back_glass": 17, "screen_safe": 44},
        },
        "iPhone 13 Pro": {
            "resale_value": 470,
            "risk_buffer": 75,
            "parts": {"battery": 11, "screen_safe": 52},
        },
    }
    listings = [
        "Apple iPhone 14 Pro Max 512 GB A2651 Silver Unlocked Cracked Screen LCD",
        "iPhone 14 Pro Max 128GB Unlocked Cracked Screen 84% Battery",
        "Apple iPhone 13 Pro Max 256GB Gold Unlocked Cracked Screen & Back FOR REPAIR",
        "Apple iPhone 14 Pro 128GB Unlocked Space Black Cracked Back Glass",
        "Apple iPhone 14 Blue Cracked Back Glass Damage FOR PARTS/REPAIR",
        "Broken Unlocked Apple iPhone 13 Pro 256GB Bad Battery",
    ]

    for title in listings:
        result = score_listing(
            {"title": title, "condition": "Used", "total_cost": 200},
            repair_values,
            scoring_rules=SCORING_RULES,
            min_score_to_alert=70,
            min_profit_to_alert=75,
        )

        assert result.status in {"candidate", "risky"}
        assert result.whole_phone_confidence_passed is True
        assert result.has_repair_issue is True
        assert not any(flag.endswith("_not_phone") for flag in result.hard_reject_flags)


def test_estimated_parts_profit_is_labeled_as_rough():
    result = score_listing(
        {
            "title": "Apple iPhone 14 Pro 128GB Unlocked Cracked Screen",
            "condition": "Used",
            "total_cost": 220,
        },
        {
            "iPhone 14 Pro": {
                "resale_value": 610,
                "risk_buffer": 85,
                "manual_review_allowed": True,
                "parts_pricing_status": "estimated",
                "parts": {"screen_safe": 45},
            }
        },
        scoring_rules=SCORING_RULES,
        min_score_to_alert=70,
        min_profit_to_alert=75,
    )

    assert result.estimated_profit_available is True
    assert result.parts_pricing_label == "Parts estimate not verified"
    assert "Parts estimate not verified" in result.manual_review_reason


def test_water_or_liquid_damage_is_hard_rejected():
    result = score_sample("water_damaged")

    assert result.status == "rejected"
    assert "water_damage" in result.hard_reject_flags


def test_icloud_or_activation_lock_is_hard_rejected():
    result = score_sample("icloud_locked")

    assert result.status == "rejected"
    assert "icloud_locked" in result.hard_reject_flags
    assert "activation_locked" in result.hard_reject_flags


def test_baseband_or_no_service_is_hard_rejected():
    result = score_sample("no_service_baseband")

    assert result.status == "rejected"
    assert "baseband" in result.hard_reject_flags
    assert "no_service" in result.hard_reject_flags


def test_frame_and_no_power_samples_are_hard_rejected_while_face_id_is_repairable():
    samples = {
        "bent_frame": "bent_frame",
        "no_power": "no_power",
    }

    for sample_name, flag in samples.items():
        result = score_sample(sample_name)
        assert result.status == "rejected"
        assert flag in result.hard_reject_flags

    face_id = score_sample("face_id_not_working")
    assert face_id.status != "rejected"
    assert "face_id_not_working" not in face_id.hard_reject_flags
    assert "face_id_issue" in face_id.positive_flags


def test_cracked_screen_powers_on_can_become_candidate():
    result = score_sample("cracked_screen_powers_on_unlocked")

    assert result.status == "candidate"
    assert result.estimated_profit >= 75
    assert result.score >= 70
    assert {"cracked_screen", "powers_on", "unlocked"}.issubset(result.positive_flags)


def test_battery_issue_can_become_candidate():
    result = score_sample("bad_battery_only")

    assert result.status == "candidate"
    assert result.estimated_profit >= 75
    assert result.score >= 70
    assert "bad_battery" in result.positive_flags


def test_back_glass_sample_stays_profitable_candidate():
    result = score_sample("back_glass_cracked")

    assert result.status == "candidate"
    assert "back_glass_cracked" in result.positive_flags
    assert result.estimated_parts_cost == REPAIR_VALUES["iPhone 13 Pro"]["back_glass_cost"]


def test_low_profit_does_not_alert():
    listing = SAMPLES["clean_high_price_low_profit"]
    result = score_sample("clean_high_price_low_profit")
    item = {**listing, **result.as_item_fields()}

    assert result.status == "risky"
    assert result.estimated_profit < 75
    assert not should_notify_item(item, result, _settings())


def test_vague_for_parts_listing_is_risky_instead_of_alerted():
    listing = SAMPLES["vague_for_parts"]
    result = score_sample("vague_for_parts")
    item = {**listing, **result.as_item_fields()}

    assert result.status == "risky"
    assert {"for_parts", "read_description", "as_is", "untested", "unknown_issue"}.issubset(result.risk_flags)
    assert not should_notify_item(item, result, _settings())


def test_for_parts_label_alone_does_not_block_strong_repair_candidate():
    result = score_listing(
        {
            "title": "Apple iPhone 14 128GB Blue Network Unlocked Cracked Back For Parts",
            "condition": "For parts or not working",
            "total_cost": 140,
        },
        {
            "iPhone 14": {
                "resale": {"low": 420, "mid": 500, "high": 580},
                "risk_buffer": 40,
                "parts_pricing_status": "verified_screenshot",
                "parts": {"back_glass": 25},
            }
        },
        scoring_rules=SCORING_RULES,
        min_score_to_alert=70,
        min_profit_to_alert=75,
    )

    assert "for_parts" in result.risk_flags
    assert result.status == "candidate"
    assert result.alert_eligible is True


def test_as_is_label_alone_does_not_block_strong_repair_candidate():
    result = score_listing(
        {
            "title": "Apple iPhone 14 128GB Network Unlocked Cracked Screen As-Is",
            "condition": "Used",
            "total_cost": 150,
        },
        {
            "iPhone 14": {
                "resale": {"low": 420, "mid": 500, "high": 580},
                "risk_buffer": 40,
                "parts_pricing_status": "verified_screenshot",
                "parts": {"screen_safe": 60},
            }
        },
        scoring_rules=SCORING_RULES,
        min_score_to_alert=70,
        min_profit_to_alert=75,
    )

    assert "as_is" in result.risk_flags
    assert result.status == "candidate"
    assert result.alert_eligible is True


def test_no_power_plus_for_parts_still_hard_rejects():
    result = score_listing(
        {
            "title": "Apple iPhone 14 128GB Cracked Screen No Power For Parts",
            "condition": "For parts or not working",
            "total_cost": 50,
        },
        {"iPhone 14": {"resale_value": 430, "parts": {"screen_safe": 60}}},
        scoring_rules=SCORING_RULES,
        min_score_to_alert=70,
        min_profit_to_alert=75,
    )

    assert result.status == "rejected"
    assert "no_power" in result.hard_reject_flags


def test_duplicate_items_do_not_notify_twice():
    listing = SAMPLES["cracked_screen_powers_on_unlocked"]
    result = score_sample("cracked_screen_powers_on_unlocked")
    item = {**listing, **result.as_item_fields()}
    storage = Storage(Path(":memory:"))

    storage.upsert_item(item)
    decision = evaluate_alert_decision(item, result, _settings())
    assert decision.eligible is True
    assert decision.tier == "PROFITABLE"
    assert not storage.was_alerted(item["item_id"])

    storage.mark_alerted(item["item_id"])
    assert storage.was_alerted(item["item_id"])
    assert not (
        should_notify_item(item, result, _settings())
        and not storage.was_alerted(item["item_id"])
    )


def test_resale_range_calculates_low_mid_high_profit():
    repair_values = {
        "iPhone 14": {
            "resale": {
                "low": 520,
                "mid": 610,
                "high": 690,
                "confidence": "high",
                "sample_size": 18,
                "condition": "used repaired",
                "storage_baseline": "128GB",
                "updated_at": "2026-05-23",
                "note": "Sold comps from manual research",
            },
            "risk_buffer": 50,
            "parts": {"screen_safe": 100},
        }
    }
    result = score_listing(
        {
            "title": "Apple iPhone 14 128GB Unlocked Cracked Screen",
            "condition": "Used",
            "total_cost": 260,
        },
        repair_values,
        scoring_rules=SCORING_RULES,
        min_score_to_alert=70,
        min_profit_to_alert=75,
    )

    assert result.resale_value == 610
    assert result.resale_low == 520
    assert result.resale_mid == 610
    assert result.resale_high == 690
    assert result.profit_low == 110
    assert result.profit_mid == 200
    assert result.profit_high == 280
    assert result.estimated_profit == result.profit_mid
    assert result.resale_confidence == "high"
    assert result.resale_sample_size == 18
    assert result.resale_note == "Sold comps from manual research"
    assert result.storage_capacity == "128GB"
    assert result.resale_source == "model_range"


def test_resale_by_storage_exact_match_uses_storage_specific_profit_math():
    repair_values = {
        "iPhone 13 Pro": {
            "resale": {"low": 350, "mid": 470, "high": 590},
            "resale_by_storage": {
                "128GB": {"low": 330, "mid": 390, "high": 430, "confidence": "manual", "sample_size": 5},
                "256GB": {"low": 390, "mid": 440, "high": 480, "confidence": "manual", "sample_size": 8},
            },
            "risk_buffer": 50,
            "parts": {"screen_safe": 110},
        }
    }

    result = score_listing(
        {
            "title": "Apple iPhone 13 Pro 256GB Unlocked Cracked Screen",
            "condition": "Used",
            "total_cost": 180,
        },
        repair_values,
        scoring_rules=SCORING_RULES,
        min_score_to_alert=70,
        min_profit_to_alert=75,
    )

    assert result.storage_capacity == "256GB"
    assert result.resale_source == "storage_specific"
    assert result.resale_storage_used == "256GB"
    assert result.resale_low == 390
    assert result.resale_mid == 440
    assert result.resale_high == 480
    assert result.profit_low == 50
    assert result.profit_mid == 100
    assert result.profit_high == 140
    assert result.estimated_profit == 100
    assert result.resale_confidence == "manual"
    assert result.resale_sample_size == 8


def test_resale_by_storage_uses_closest_lower_storage_with_warning():
    repair_values = {
        "iPhone 15 Pro": {
            "resale_by_storage": {
                "128GB": {"low": 520, "mid": 580, "high": 620},
                "256GB": {"low": 590, "mid": 650, "high": 700},
            },
            "risk_buffer": 50,
            "parts": {"screen_safe": 120},
        }
    }

    result = score_listing(
        {
            "title": "Apple iPhone 15 Pro 512GB Unlocked Cracked Screen",
            "condition": "Used",
            "total_cost": 300,
        },
        repair_values,
        scoring_rules=SCORING_RULES,
        min_score_to_alert=70,
        min_profit_to_alert=75,
    )

    assert result.resale_source == "storage_specific"
    assert result.resale_storage_used == "256GB"
    assert "closest lower 256GB" in result.storage_resale_warning


def test_storage_missing_falls_back_to_model_range_with_warning():
    result = score_listing(
        {
            "title": "Apple iPhone 13 Pro Unlocked Cracked Screen",
            "condition": "Used",
            "total_cost": 180,
        },
        {
            "iPhone 13 Pro": {
                "resale": {"low": 350, "mid": 470, "high": 590},
                "resale_by_storage": {
                    "128GB": {"low": 330, "mid": 390, "high": 430},
                },
                "risk_buffer": 50,
                "parts": {"screen_safe": 110},
            }
        },
        scoring_rules=SCORING_RULES,
        min_score_to_alert=70,
        min_profit_to_alert=75,
    )

    assert result.storage_capacity is None
    assert result.resale_source == "model_range"
    assert result.resale_storage_used is None
    assert result.storage_resale_warning == "Storage unknown - model-level resale used"
    assert "Storage unknown - model-level resale used" in result.manual_review_reason


def test_missing_resale_research_file_loads_empty(tmp_path):
    assert load_resale_research(tmp_path / "missing-resale-research.json") == {}


def test_resale_research_overrides_repair_values_resale():
    result = score_listing(
        {
            "title": "Apple iPhone 13 Pro 128GB Unlocked Cracked Screen",
            "condition": "Used",
            "total_cost": 150,
        },
        {
            "iPhone 13 Pro": {
                "resale": {"low": 100, "mid": 120, "high": 140},
                "risk_buffer": 50,
                "parts": {"screen_safe": 110},
            }
        },
        resale_research={
            "iPhone 13 Pro": {
                "resale": {"low": 350, "mid": 390, "high": 430, "confidence": "research", "sample_size": 7}
            }
        },
        scoring_rules=SCORING_RULES,
        min_score_to_alert=70,
        min_profit_to_alert=75,
    )

    assert result.resale_mid == 390
    assert result.resale_market_source == "resale_research"
    assert result.resale_condition_used == "Good"
    assert result.resale_source == "model_range"


def test_repair_values_resale_fallback_still_works_without_research():
    result = score_listing(
        {
            "title": "Apple iPhone 13 Pro 128GB Unlocked Cracked Screen",
            "condition": "Used",
            "total_cost": 150,
        },
        {
            "iPhone 13 Pro": {
                "resale": {"low": 350, "mid": 390, "high": 430},
                "risk_buffer": 50,
                "parts": {"screen_safe": 110},
            }
        },
        resale_research={},
        scoring_rules=SCORING_RULES,
        min_score_to_alert=70,
        min_profit_to_alert=75,
    )

    assert result.resale_mid == 390
    assert result.resale_market_source == "repair_values"


def test_resale_research_storage_specific_good_condition_drives_scoring():
    result = score_listing(
        {
            "title": "Apple iPhone 13 Pro 256GB Unlocked Cracked Screen",
            "condition": "Used",
            "total_cost": 180,
        },
        {
            "iPhone 13 Pro": {
                "resale": {"low": 100, "mid": 120, "high": 140},
                "risk_buffer": 50,
                "parts": {"screen_safe": 110},
            }
        },
        resale_research={
            "iPhone 13 Pro": {
                "resale_by_storage": {
                    "256GB": {
                        "good": {"low": 390, "mid": 440, "high": 480},
                        "mint": {"low": 440, "mid": 500, "high": 560},
                        "confidence": "research",
                        "sample_size": 8,
                    }
                }
            }
        },
        scoring_rules=SCORING_RULES,
        min_score_to_alert=70,
        min_profit_to_alert=75,
    )

    assert result.resale_source == "storage_specific"
    assert result.resale_market_source == "resale_research"
    assert result.resale_condition_used == "Good"
    assert result.resale_storage_used == "256GB"
    assert result.estimated_profit == 100
    assert result.mint_profit_high == 220


def test_mint_resale_is_upside_only_and_does_not_make_alert_eligible():
    result = score_listing(
        {
            "title": "Apple iPhone 13 Pro 128GB Unlocked Cracked Screen",
            "condition": "Used",
            "total_cost": 200,
        },
        {
            "iPhone 13 Pro": {
                "risk_buffer": 50,
                "parts": {"screen_safe": 110},
            }
        },
        resale_research={
            "iPhone 13 Pro": {
                "resale_by_storage": {
                    "128GB": {
                        "good": {"low": 320, "mid": 350, "high": 370},
                        "mint": {"low": 440, "mid": 480, "high": 520},
                    }
                }
            }
        },
        scoring_rules=SCORING_RULES,
        min_score_to_alert=70,
        min_profit_to_alert=75,
    )

    assert result.estimated_profit == -10
    assert result.mint_profit_high == 160
    assert result.alert_eligible is False
    assert result.status == "risky"
    assert "Profit depends on mint resale" in result.manual_review_reason


def test_research_missing_storage_uses_model_level_research_before_repair_values():
    result = score_listing(
        {
            "title": "Apple iPhone 13 Pro Unlocked Cracked Screen",
            "condition": "Used",
            "total_cost": 180,
        },
        {
            "iPhone 13 Pro": {
                "resale": {"low": 250, "mid": 300, "high": 350},
                "risk_buffer": 50,
                "parts": {"screen_safe": 110},
            }
        },
        resale_research={
            "iPhone 13 Pro": {
                "resale_by_storage": {
                    "128GB": {"good": {"low": 330, "mid": 390, "high": 430}},
                },
                "resale": {"good": {"low": 360, "mid": 420, "high": 470}},
            }
        },
        scoring_rules=SCORING_RULES,
        min_score_to_alert=70,
        min_profit_to_alert=75,
    )

    assert result.storage_capacity is None
    assert result.resale_mid == 420
    assert result.resale_market_source == "resale_research"
    assert result.storage_resale_warning == "Storage unknown - model-level resale used"


def test_legacy_resale_value_still_sets_expected_profit():
    repair_values = {
        "iPhone 14": {
            "resale_value": 430,
            "risk_buffer": 50,
            "parts": {"battery": 40},
        }
    }
    result = score_listing(
        {
            "title": "Apple iPhone 14 128GB Unlocked Bad Battery",
            "condition": "Used",
            "total_cost": 200,
        },
        repair_values,
        scoring_rules=SCORING_RULES,
        min_score_to_alert=70,
        min_profit_to_alert=75,
    )

    assert result.resale_value == 430
    assert result.resale_low == 430
    assert result.resale_mid == 430
    assert result.resale_high == 430
    assert result.estimated_profit == 140
    assert result.profit_mid == 140
    assert result.resale_source == "legacy_resale_value"


def test_missing_resale_range_and_value_remains_unavailable():
    repair_values = {
        "iPhone 14": {
            "risk_buffer": 50,
            "parts": {"screen_safe": 100},
            "manual_review_allowed": True,
        }
    }
    result = score_listing(
        {
            "title": "Apple iPhone 14 128GB Unlocked Cracked Screen",
            "condition": "Used",
            "total_cost": 200,
        },
        repair_values,
        scoring_rules=SCORING_RULES,
        min_score_to_alert=70,
        min_profit_to_alert=75,
    )

    assert result.estimated_profit_available is False
    assert result.resale_value == 0
    assert result.resale_low == 0
    assert result.resale_mid == 0
    assert result.resale_high == 0
    assert result.profit_low == 0
    assert result.profit_mid == 0
    assert result.profit_high == 0
    assert result.pricing_warning == "Estimated profit unavailable — resale value missing"
    assert result.resale_source == "missing"


def test_score_result_serializes_resale_range_fields_for_api_consumers():
    result = score_listing(
        {
            "title": "Apple iPhone 14 128GB Unlocked Cracked Screen",
            "condition": "Used",
            "total_cost": 260,
        },
        {
            "iPhone 14": {
                "resale": {"low": 520, "mid": 610, "high": 690, "confidence": "medium", "sample_size": 9, "note": "Manual comps"},
                "risk_buffer": 50,
                "parts": {"screen_safe": 100},
            }
        },
        scoring_rules=SCORING_RULES,
        min_score_to_alert=70,
        min_profit_to_alert=75,
    )
    fields = result.as_item_fields()

    assert fields["resale_low"] == 520
    assert fields["resale_mid"] == 610
    assert fields["resale_high"] == 690
    assert fields["profit_low"] == 110
    assert fields["profit_mid"] == 200
    assert fields["profit_high"] == 280
    assert fields["resale_confidence"] == "medium"
    assert fields["resale_sample_size"] == 9
    assert fields["resale_note"] == "Manual comps"
    assert fields["storage_capacity"] == "128GB"
    assert fields["storage_confidence"] == "high"
    assert fields["storage_source"] == "title"
    assert fields["resale_source"] == "model_range"
    assert fields["resale_market_source"] == "repair_values"
    assert fields["resale_condition_used"] == "Good"
    assert fields["resale_storage_used"] is None
    assert fields["storage_resale_warning"] == ""
    assert fields["mint_resale_mid"] == 0
    assert fields["mint_profit_high"] == 0


def test_expected_profit_below_model_min_profit_does_not_alert():
    repair_values = {
        "iPhone 14 Pro Max": {
            "resale": {"low": 500, "mid": 629, "high": 766},
            "risk_buffer": 60,
            "min_profit": 75,
            "parts_pricing_status": "verified_screenshot",
            "parts": {"screen_safe": 140},
        }
    }
    listing = {
        "item_id": "low-mid-profit",
        "title": "Apple iPhone 14 Pro Max 128GB Unlocked Cracked Screen",
        "condition": "Used",
        "total_cost": 426,
    }
    result = score_listing(
        listing,
        repair_values,
        scoring_rules=SCORING_RULES,
        min_score_to_alert=70,
        min_profit_to_alert=75,
    )
    item = {**listing, **result.as_item_fields()}

    assert result.profit_low == -126
    assert result.profit_mid == 3
    assert result.profit_high == 140
    assert result.status == "risky"
    assert result.alert_eligible is False
    assert "Expected profit below threshold" in result.manual_review_reason
    assert not should_notify_item(item, result, _settings())


def test_negative_expected_profit_never_alerts():
    result = score_listing(
        {
            "item_id": "negative-mid-profit",
            "title": "Broken Unlocked Apple iPhone 14 Pro 128GB Weak Battery Bad LCD",
            "condition": "Used",
            "total_cost": 448,
        },
        {
            "iPhone 14 Pro": {
                "resale": {"low": 430, "mid": 520, "high": 650},
                "risk_buffer": 50,
                "parts_pricing_status": "verified_screenshot",
                "parts": {"screen_safe": 40},
            }
        },
        scoring_rules=SCORING_RULES,
        min_score_to_alert=70,
        min_profit_to_alert=75,
    )

    assert result.profit_mid == -18
    assert result.status == "risky"
    assert result.alert_eligible is False
    assert "Expected profit below threshold" in result.manual_review_reason


def test_optimistic_high_profit_only_goes_to_manual_review():
    result = score_listing(
        {
            "item_id": "optimistic-only",
            "title": "Apple iPhone 13 Pro Max 128GB Unlocked Cracked Screen",
            "condition": "Used",
            "total_cost": 408,
        },
        {
            "iPhone 13 Pro Max": {
                "resale": {"low": 430, "mid": 560, "high": 760},
                "risk_buffer": 50,
                "parts_pricing_status": "verified_screenshot",
                "parts": {"screen_safe": 80},
            }
        },
        scoring_rules=SCORING_RULES,
        min_score_to_alert=70,
        min_profit_to_alert=75,
    )

    assert result.profit_mid == 22
    assert result.profit_high == 222
    assert result.status == "risky"
    assert result.alert_eligible is False
    assert "Only upside case works" in result.manual_review_reason


def test_low_confidence_pricing_requires_stronger_expected_profit():
    listing = {
        "item_id": "low-confidence-profit",
        "title": "Apple iPhone 14 Pro 128GB Unlocked Cracked Back Glass",
        "condition": "Used",
        "total_cost": 300,
    }
    repair_values = {
        "iPhone 14 Pro": {
            "resale": {"low": 540, "mid": 595, "high": 680},
            "risk_buffer": 50,
            "parts_pricing_status": "estimated",
            "parts": {"back_glass": 120},
        }
    }
    result = score_listing(
        listing,
        repair_values,
        scoring_rules=SCORING_RULES,
        min_score_to_alert=70,
        min_profit_to_alert=75,
    )
    item = {**listing, **result.as_item_fields()}

    assert result.profit_mid == 125
    assert result.profit_mid >= 75
    assert result.profit_mid < 75 * 1.75
    assert result.status == "risky"
    assert result.alert_eligible is False
    assert "Low-confidence pricing needs stronger profit" in result.manual_review_reason
    assert not should_notify_item(item, result, _settings())


def test_strong_low_confidence_pricing_can_still_be_candidate():
    result = score_listing(
        {
            "item_id": "strong-low-confidence-profit",
            "title": "Apple iPhone 14 Pro 128GB Unlocked Cracked Back Glass",
            "condition": "Used",
            "total_cost": 250,
        },
        {
            "iPhone 14 Pro": {
                "resale": {"low": 600, "mid": 700, "high": 780},
                "risk_buffer": 50,
                "parts_pricing_status": "estimated",
                "parts": {"back_glass": 120},
            }
        },
        scoring_rules=SCORING_RULES,
        min_score_to_alert=70,
        min_profit_to_alert=75,
    )

    assert result.profit_mid == 280
    assert result.status == "candidate"
    assert result.alert_eligible is True


def test_screen_only_oled_lcd_parts_are_rejected():
    repair_values = {
        "iPhone 16 Plus": {"resale_value": 700, "risk_buffer": 50, "parts": {"screen_safe": 160}},
        "iPhone 16 Pro": {"resale_value": 850, "risk_buffer": 50, "parts": {"screen_safe": 220}},
    }
    samples = [
        "APPLE iPhone 16 Plus OEM Screen - NO DISPLAY Bad Lcd",
        "Original iPhone 16 Pro OLED Screen Cracked Glass Good LCD & Touch No Spots OEM",
    ]

    for title in samples:
        result = score_listing(
            {"title": title, "condition": "Used", "total_cost": 100},
            repair_values,
            scoring_rules=SCORING_RULES,
            min_score_to_alert=70,
            min_profit_to_alert=75,
        )

        assert result.status == "rejected"
        assert result.whole_phone_confidence_passed is False
        assert any(flag in result.hard_reject_flags for flag in ("screen_part_not_phone", "oled_lcd_part_not_phone"))


def test_more_display_only_parts_are_rejected_or_suppressed():
    repair_values = {
        "iPhone 17 Pro": {"resale_value": 900, "risk_buffer": 50, "parts": {"screen_safe": 220}},
        "iPhone 15": {"resale_value": 520, "risk_buffer": 50, "parts": {"screen_safe": 120}},
        "iPhone 13 Pro": {"resale_value": 470, "risk_buffer": 50, "parts": {"screen_safe": 110}},
        "iPhone 11": {"resale_value": 250, "risk_buffer": 50, "parts": {"screen_safe": 70}},
    }
    samples = [
        "OEM Display for iPhone 13 Pro Dot Grade A Only Display Tested Original",
        "iPhone 15 Screen - No Cracks, Broken OLED Display OEM",
        "iPhone 17 Pro Display Screen Replacement Apple OEM - Cracked Glass Good OLED",
        "Apple iPhone 11 - Black For LCD Parts - BATTERY LCD",
    ]

    for title in samples:
        result = score_listing(
            {"title": title, "condition": "Used", "total_cost": 100},
            repair_values,
            scoring_rules=SCORING_RULES,
            min_score_to_alert=70,
            min_profit_to_alert=75,
        )

        assert result.status == "rejected"
        assert result.whole_phone_confidence_passed is False
        assert "screen_part_not_phone" in result.hard_reject_flags


def test_high_confidence_screen_component_phrases_are_rejected():
    repair_values = {
        "iPhone 14": {"resale_value": 430, "risk_buffer": 50, "parts": {"screen_safe": 90}},
        "iPhone 16": {"resale_value": 700, "risk_buffer": 50, "parts": {"screen_safe": 140}},
        "iPhone 15 Pro Max": {"resale_value": 900, "risk_buffer": 60, "parts": {"screen_safe": 180}},
        "iPhone X": {"resale_value": 180, "risk_buffer": 40, "parts": {"screen_safe": 80}},
    }
    samples = [
        "OEM Apple iPhone 16 Screen Display Assembly Cracked Glass Good OLED Touch Works",
        "OEM Apple iPhone 14 Screen Display Assembly Cracked Glass Good OLED Touch Works",
        "OEM screen assembly for iPhone 14",
        "Apple iPhone X oem cracked screen OLED only parts READ",
        "replacement display assembly for iPhone 16",
        "replacement screen for iPhone 14",
        "screen for iPhone 15 Pro Max",
    ]

    for title in samples:
        result = score_listing(
            {"title": title, "condition": "For parts or not working", "total_cost": 80},
            repair_values,
            scoring_rules=SCORING_RULES,
            min_score_to_alert=70,
            min_profit_to_alert=75,
        )

        assert result.status == "rejected"
        assert result.alert_eligible is False
        assert result.whole_phone_confidence_passed is False
        assert "screen_part_not_phone" in result.hard_reject_flags
        assert "Screen/display part listing" in result.manual_review_reason


def test_description_saying_phone_not_included_rejects_display_part_listing():
    descriptions = [
        "Pulled from a working phone. Touch works. Phone is not included.",
        "This is for flex parts only. No phone included.",
    ]

    for description in descriptions:
        result = score_listing(
            {
                "title": "Apple iPhone 14 oem cracked screen parts Read bad OLED",
                "condition": "For parts or not working",
                "raw_description": description,
                "total_cost": 40,
            },
            {
                "iPhone 14": {
                    "resale": {"low": 280, "mid": 337, "high": 410},
                    "risk_buffer": 70,
                    "parts_pricing_status": "verified_screenshot",
                    "parts": {"screen_safe": 44},
                }
            },
            scoring_rules=SCORING_RULES,
            min_score_to_alert=70,
            min_profit_to_alert=75,
        )

        assert result.status == "rejected"
        assert result.alert_eligible is False
        assert "screen_part_not_phone" in result.hard_reject_flags
        assert "Screen/display part listing" in result.manual_review_reason


def test_whole_phone_screen_repair_titles_are_not_component_rejected():
    repair_values = {
        "iPhone 14": {"resale": {"low": 300, "mid": 420, "high": 500}, "risk_buffer": 50, "parts_pricing_status": "verified_screenshot", "parts": {"screen_safe": 90}},
        "iPhone 16": {"resale": {"low": 520, "mid": 640, "high": 760}, "risk_buffer": 60, "parts_pricing_status": "verified_screenshot", "parts": {"screen_safe": 140}},
        "iPhone 15": {"resale": {"low": 380, "mid": 480, "high": 560}, "risk_buffer": 50, "parts_pricing_status": "verified_screenshot", "parts": {"back_glass": 60}},
        "iPhone 13": {"resale": {"low": 240, "mid": 330, "high": 400}, "risk_buffer": 50, "parts_pricing_status": "verified_screenshot", "parts": {"screen_safe": 80}},
    }
    samples = [
        "Apple iPhone 15 128GB Green Unlocked CRACKED BACK NON OEM SCREEN",
        "iPhone 14 cracked screen bad OLED for parts",
        "iPhone 13 cracked screen DOES STILL WORK For Parts?",
        "iPhone 16 128GB T-Mobile cracked screen clean IMEI non-OEM screen",
        "iPhone 15 cracked back as-is",
        "iPhone 13 bad screen powers on",
        "iPhone 12 screen does not work, phone still turns on",
    ]

    for title in samples:
        result = score_listing(
            {"title": title, "condition": "For parts or not working", "total_cost": 220},
            repair_values,
            scoring_rules=SCORING_RULES,
            min_score_to_alert=70,
            min_profit_to_alert=75,
        )

        assert result.status != "rejected"
        assert not any(flag.endswith("_not_phone") for flag in result.hard_reject_flags)


def test_paymore_template_description_extracts_whole_phone_and_functional_signals():
    description = """
    Items included in this sale: Broken Unlocked Apple iPhone 15 Pro Max 256GB MU673LL/A Read
    Specifications: Brand Apple Model iPhone 15 Pro Max Storage Size 256GB Lock Status Factory Unlocked
    Carrier Service Unlocked IMEI 359081510922047 Battery Health 84% Replaced Parts? No
    Cosmetic Condition: The back glass is cracked/damaged. The cameras are in good shape.
    Functionality condition: Both the front and rear cameras are fully functional with no issues.
    The Face ID functions properly and is ready to be set up. The LCD/OLED has no issues or damage.
    The Digitizer (Touch Screen) responds to touch and is fully functional. The WiFi abilities are available.
    This iPhone has a clean IMEI and is ready to be activated. The charge port is clean and fully functional.
    Shipping Info: no box or anything else included, such as power cables or other accessories.
    """
    result = score_listing(
        {
            "title": "Broken Unlocked Apple iPhone 15 Pro Max 256GB MU673LL/A Read",
            "condition": "For parts or not working",
            "raw_description": description,
            "total_cost": 450,
        },
        {
            "iPhone 15 Pro Max": {
                "resale": {"low": 720, "mid": 850, "high": 980},
                "risk_buffer": 80,
                "parts_pricing_status": "verified_screenshot_low_confidence",
                "parts": {"back_glass": 85},
            }
        },
        scoring_rules=SCORING_RULES,
        min_score_to_alert=70,
        min_profit_to_alert=75,
    )
    signals = extract_description_signals(
        {
            "title": "Broken Unlocked Apple iPhone 15 Pro Max 256GB MU673LL/A Read",
            "raw_description": description,
        }
    )

    assert result.status != "rejected"
    assert result.whole_phone_confidence_passed is True
    assert {"back_glass_cracked", "unlocked", "clean_imei", "face_id_works"}.issubset(result.positive_flags)
    assert "included_device_signals" in signals
    assert signals["included_device_signals"]
    assert {"battery_health", "face_id_works", "cameras_functional", "touch_functional", "display_functional", "charge_port_functional"}.issubset(
        set(signals["functionality_signals"])
    )
    assert signals["normal_not_included_accessory_list"]
    assert not result.hard_reject_flags


def test_description_included_device_and_normal_not_included_accessories_are_not_component_reject():
    description = """
    Included: Device
    NOT Included: SIM Card Charger Headphones Original Box
    Condition Rating: Broken. The device's back glass is cracked.
    A non-OEM screen replacement has been detected. Battery health percentage: 80%.
    """
    result = score_listing(
        {
            "title": "Apple iPhone 15 - 128GB - Green (Unlocked) - CRACKED BACK, NON OEM SCREEN",
            "condition": "For parts or not working",
            "raw_description": description,
            "total_cost": 250,
        },
        {
            "iPhone 15": {
                "resale": {"low": 380, "mid": 480, "high": 560},
                "risk_buffer": 50,
                "parts_pricing_status": "verified_screenshot_low_confidence",
                "parts": {"back_glass": 60},
            }
        },
        scoring_rules=SCORING_RULES,
        min_score_to_alert=70,
        min_profit_to_alert=75,
    )
    signals = extract_description_signals({"title": "Apple iPhone 15", "raw_description": description})

    assert result.status != "rejected"
    assert not any(flag.endswith("_not_phone") for flag in result.hard_reject_flags)
    assert "back_glass_cracked" in result.positive_flags
    assert "screen_display_issue" in result.positive_flags
    assert signals["included_device_signals"] == ["included_device"]
    assert signals["normal_not_included_accessory_list"]
    assert not signals["component_reject_signals"]


def test_whole_phone_working_language_and_aspects_add_functional_and_unlocked_evidence():
    raw_json = {
        "details": {
            "localizedAspects": [
                {"name": "Network", "value": "Unlocked"},
                {"name": "Model", "value": "Apple iPhone 13"},
                {"name": "Storage Capacity", "value": "128 GB"},
            ],
            "shortDescription": "WITH THE SCREEN BEING CRACKED I HAD TO LIST UNDER FOR PARTS.",
            "conditionDescription": "Needs Sim Card",
        }
    }
    result = score_listing(
        {
            "title": "Apple iPhone 13 128GB Black Damaged Cracked Screen DOES STILL WORK ~ For Parts?",
            "condition": "For parts or not working",
            "raw_description": "WITH THE SCREEN BEING CRACKED I HAD TO LIST UNDER FOR PARTS. Needs Sim Card to use.",
            "raw_json": raw_json,
            "total_cost": 139,
        },
        {
            "iPhone 13": {
                "resale": {"low": 240, "mid": 330, "high": 400},
                "risk_buffer": 50,
                "parts_pricing_status": "verified_screenshot_low_confidence",
                "parts": {"screen_safe": 80},
            }
        },
        scoring_rules=SCORING_RULES,
        min_score_to_alert=70,
        min_profit_to_alert=75,
    )

    assert result.status != "rejected"
    assert "cracked_screen" in result.positive_flags
    assert "powers_on" in result.positive_flags
    assert "unlocked" in result.positive_flags
    assert result.whole_phone_confidence_passed is True


def test_component_only_description_guardrails_still_reject():
    repair_values = {
        "iPhone 14": {"resale": {"low": 280, "mid": 337, "high": 410}, "risk_buffer": 70, "parts": {"screen_safe": 44}},
        "iPhone 16": {"resale": {"low": 520, "mid": 640, "high": 760}, "risk_buffer": 60, "parts": {"screen_safe": 140}},
    }
    samples = [
        ("Apple iPhone 14 oem cracked screen parts Read bad OLED", "Phone is not included."),
        ("Apple iPhone 14 oem cracked screen parts Read bad OLED", "For flex parts only."),
        ("Apple iPhone 14 oem cracked screen parts Read bad OLED", "OLED only."),
        ("OEM Apple iPhone 16 Screen Display Assembly Cracked Glass Good OLED Touch Works", ""),
        ("Apple iPhone 16 display assembly replacement screen", ""),
    ]

    for title, description in samples:
        result = score_listing(
            {"title": title, "condition": "For parts or not working", "raw_description": description, "total_cost": 80},
            repair_values,
            scoring_rules=SCORING_RULES,
            min_score_to_alert=70,
            min_profit_to_alert=75,
        )

        assert result.status == "rejected"
        assert result.alert_eligible is False
        assert result.whole_phone_confidence_passed is False
        assert any(flag.endswith("_not_phone") for flag in result.hard_reject_flags)


def test_decision_trace_exposes_description_extraction_signal_groups():
    listing = {
        "item_id": "trace-description-signals",
        "title": "Broken Unlocked Apple iPhone 13 128GB Bad Battery",
        "condition": "For parts or not working",
        "raw_description": """
        Items included in this sale: Broken Unlocked Apple iPhone 13 128GB Bad Battery.
        Functionality condition: Face ID works, cameras are fully functional, LCD/OLED has no issues,
        touch screen is fully functional, the charge port is clean and fully functional.
        This iPhone has a clean IMEI and is ready to be activated. Battery Health 84%.
        """,
        "total_cost": 180,
    }
    result = score_listing(
        listing,
        {
            "iPhone 13": {
                "resale": {"low": 240, "mid": 330, "high": 400},
                "risk_buffer": 50,
                "parts_pricing_status": "verified_screenshot_low_confidence",
                "parts": {"battery": 30},
            }
        },
        scoring_rules=SCORING_RULES,
        min_score_to_alert=70,
        min_profit_to_alert=75,
    )
    trace = _build_decision_trace({**listing, **result.as_item_fields()}, result, _settings(), scan_cycle_id=321)
    detected = trace["detected"]

    assert detected["included_device_signals"]
    assert detected["functionality_signals"]
    assert detected["clean_activation_signals"]
    assert detected["repair_detail_signals"]
    assert detected["description_signals"]["included_device_signals"] == detected["included_device_signals"]


def test_high_resale_new_model_with_rough_pricing_does_not_alert():
    result = score_listing(
        {
            "item_id": "new-model-rough",
            "title": "iPhone 17 Pro 512GB Cracked Screen Apple Limited Warranty Until October",
            "condition": "For parts or not working",
            "total_cost": 795,
        },
        {
            "iPhone 17 Pro": {
                "resale": {"low": 1180, "mid": 1300, "high": 1450},
                "risk_buffer": 110,
                "parts_pricing_status": "manual_part_update",
                "parts": {"screen_safe": 103},
            }
        },
        scoring_rules=SCORING_RULES,
        min_score_to_alert=70,
        min_profit_to_alert=75,
    )

    assert result.profit_mid > 0
    assert result.status == "risky"
    assert result.alert_eligible is False
    assert "High-resale model needs stronger verification" in result.manual_review_reason


def test_storage_unknown_verified_low_price_routes_to_review_trace_not_dead_end_needs_data():
    listing = {
        "item_id": "storage-unknown-review",
        "title": "Apple iPhone 14 oem cracked screen parts Read bad OLED",
        "condition": "For parts or not working",
        "total_cost": 40,
        "price": 40,
        "shipping": 0,
        "user_status": "new",
    }
    result = score_listing(
        listing,
        {
            "iPhone 14": {
                "resale": {"low": 280, "mid": 337, "high": 410},
                "risk_buffer": 70,
                "parts_pricing_status": "verified_screenshot",
                "parts": {"screen_safe": 44},
            }
        },
        scoring_rules=SCORING_RULES,
        min_score_to_alert=70,
        min_profit_to_alert=75,
    )
    item = {**listing, **result.as_item_fields()}
    trace = _build_decision_trace(item, result, _settings(), scan_cycle_id=123)

    assert result.alert_eligible is False
    assert trace["verdict"]["normalized_bucket"] == "review"
    assert trace["detected"]["carrier_status"] == "unknown"
    assert "storage_unknown" in trace["reasons"]["missing_data"]
    assert "carrier_unknown" in trace["reasons"]["missing_data"]


def test_included_phone_clean_imei_functional_cracked_screen_routes_priority_review_not_needs_data():
    listing = {
        "item_id": "paymore-style-review",
        "title": "Apple iPhone 14 128GB Unlocked Cracked Screen Clean IMEI",
        "condition": "For parts or not working",
        "raw_description": """
        What's Included: Phone. This device has a cracked screen.
        It has been fully tested, charges, powers on, and has a clean IMEI.
        """,
        "total_cost": 285,
        "price": 285,
        "shipping": 0,
        "user_status": "new",
    }
    result = score_listing(
        listing,
        {
            "iPhone 14": {
                "resale": {"low": 280, "mid": 350, "high": 410},
                "risk_buffer": 65,
                "parts_pricing_status": "verified_screenshot_low_confidence",
                "parts": {"screen_safe": 80},
            }
        },
        scoring_rules=SCORING_RULES,
        min_score_to_alert=70,
        min_profit_to_alert=75,
    )
    trace = _build_decision_trace({**listing, **result.as_item_fields()}, result, _settings(), scan_cycle_id=124)

    assert result.status == "risky"
    assert result.alert_eligible is False
    assert trace["verdict"]["normalized_bucket"] == "good"
    assert trace["verdict"]["normalized_bucket"] != "needs_data"
    assert "Description supports whole-phone review" in result.manual_review_reason
    assert "Pricing confidence prevents Best Pick" in result.manual_review_reason
    assert "Reviewable despite parts/pricing gap" in result.manual_review_reason


def test_does_still_work_cracked_screen_unlocked_routes_to_review_not_needs_data():
    listing = {
        "item_id": "does-still-work-review",
        "title": "iPhone 13 128GB Unlocked cracked screen DOES STILL WORK For Parts",
        "condition": "For parts or not working",
        "total_cost": 230,
        "price": 230,
        "shipping": 0,
        "user_status": "new",
    }
    result = score_listing(
        listing,
        {
            "iPhone 13": {
                "resale": {"low": 240, "mid": 330, "high": 390},
                "risk_buffer": 70,
                "parts_pricing_status": "verified_screenshot_low_confidence",
                "parts": {"screen_safe": 90},
            }
        },
        scoring_rules=SCORING_RULES,
        min_score_to_alert=70,
        min_profit_to_alert=75,
    )
    trace = _build_decision_trace({**listing, **result.as_item_fields()}, result, _settings(), scan_cycle_id=125)

    assert result.status == "risky"
    assert result.alert_eligible is False
    assert trace["verdict"]["normalized_bucket"] in {"good", "watch"}
    assert trace["verdict"]["normalized_bucket"] != "needs_data"


def test_included_device_normal_accessory_exclusions_route_to_review_not_component_reject():
    listing = {
        "item_id": "included-device-review",
        "title": "Apple iPhone 15 128GB Green Unlocked CRACKED BACK NON OEM SCREEN",
        "condition": "For parts or not working",
        "raw_description": """
        Included: Device. Not included: SIM card, charger, headphones, or box.
        The back glass is cracked and the screen has been replaced with a non OEM screen.
        """,
        "total_cost": 440,
        "price": 440,
        "shipping": 0,
        "user_status": "new",
    }
    result = score_listing(
        listing,
        {
            "iPhone 15": {
                "resale": {"low": 420, "mid": 520, "high": 650},
                "risk_buffer": 85,
                "parts_pricing_status": "verified_screenshot_low_confidence",
                "parts": {"back_glass": 90, "screen_safe": 120},
            }
        },
        scoring_rules=SCORING_RULES,
        min_score_to_alert=70,
        min_profit_to_alert=75,
    )
    trace = _build_decision_trace({**listing, **result.as_item_fields()}, result, _settings(), scan_cycle_id=126)

    assert result.status == "risky"
    assert result.alert_eligible is False
    assert not any(flag.endswith("_not_phone") for flag in result.hard_reject_flags)
    assert "normal_accessory_exclusions" in result.listing_classification_flags
    assert trace["verdict"]["normalized_bucket"] in {"good", "watch"}
    assert trace["verdict"]["normalized_bucket"] != "needs_data"


def test_missing_raw_description_unknown_issue_stays_needs_data():
    listing = {
        "item_id": "missing-description-unknown-issue",
        "title": "Apple iPhone 14 128GB for parts",
        "condition": "For parts or not working",
        "total_cost": 150,
        "price": 150,
        "shipping": 0,
        "user_status": "new",
    }
    result = score_listing(
        listing,
        {
            "iPhone 14": {
                "resale": {"low": 280, "mid": 350, "high": 410},
                "risk_buffer": 65,
                "parts_pricing_status": "verified_screenshot",
                "parts": {"screen_safe": 80},
            }
        },
        scoring_rules=SCORING_RULES,
        min_score_to_alert=70,
        min_profit_to_alert=75,
    )
    trace = _build_decision_trace({**listing, **result.as_item_fields()}, result, _settings(), scan_cycle_id=127)

    assert result.alert_eligible is False
    assert trace["verdict"]["normalized_bucket"] == "needs_data"
    assert "Parts-only ambiguous" in result.manual_review_reason


def test_reviewable_low_confidence_pricing_does_not_become_best_pick_or_alert():
    listing = {
        "item_id": "reviewable-low-confidence-not-best-pick",
        "title": "Apple iPhone 16 128GB T-Mobile cracked screen clean IMEI non-OEM screen",
        "condition": "For parts or not working",
        "raw_description": "Phone is included. It powers on and has a clean IMEI.",
        "total_cost": 560,
        "price": 560,
        "shipping": 0,
        "user_status": "new",
    }
    result = score_listing(
        listing,
        {
            "iPhone 16": {
                "resale": {"low": 560, "mid": 680, "high": 780},
                "risk_buffer": 100,
                "parts_pricing_status": "manual_part_update",
                "parts": {"screen_safe": 120},
            }
        },
        scoring_rules=SCORING_RULES,
        min_score_to_alert=70,
        min_profit_to_alert=75,
    )
    trace = _build_decision_trace({**listing, **result.as_item_fields()}, result, _settings(), scan_cycle_id=128)

    assert result.status == "risky"
    assert result.alert_eligible is False
    assert trace["verdict"]["normalized_bucket"] != "gem"
    assert trace["verdict"]["normalized_bucket"] in {"good", "watch"}


def test_accessory_component_phrase_still_rejects_after_review_routing_patch():
    result = score_listing(
        {
            "item_id": "component-still-rejected",
            "title": "OEM Apple iPhone 16 Screen Display Assembly Cracked Glass Good OLED Touch Works",
            "condition": "For parts or not working",
            "total_cost": 80,
            "user_status": "new",
        },
        {
            "iPhone 16": {
                "resale": {"low": 560, "mid": 680, "high": 780},
                "risk_buffer": 100,
                "parts_pricing_status": "manual_part_update",
                "parts": {"screen_safe": 120},
            }
        },
        scoring_rules=SCORING_RULES,
        min_score_to_alert=70,
        min_profit_to_alert=75,
    )

    assert result.status == "rejected"
    assert result.alert_eligible is False
    assert any(flag.endswith("_not_phone") for flag in result.hard_reject_flags)


def test_carrier_unknown_does_not_hard_reject_but_lock_terms_still_reject():
    carrier_unknown = score_listing(
        {
            "title": "Apple iPhone 14 128GB Cracked Screen Clean IMEI",
            "condition": "For parts or not working",
            "total_cost": 170,
        },
        {"iPhone 14": {"resale_value": 430, "risk_buffer": 60, "parts_pricing_status": "verified_screenshot", "parts": {"screen_safe": 80}}},
        scoring_rules=SCORING_RULES,
        min_score_to_alert=70,
        min_profit_to_alert=75,
    )
    locked = score_listing(
        {
            "title": "Apple iPhone 14 128GB Cracked Screen Locked To Owner",
            "condition": "For parts or not working",
            "total_cost": 170,
        },
        {"iPhone 14": {"resale_value": 430, "risk_buffer": 60, "parts": {"screen_safe": 80}}},
        scoring_rules=SCORING_RULES,
    )

    assert carrier_unknown.status != "rejected"
    assert "activation_locked" not in carrier_unknown.hard_reject_flags
    assert locked.status == "rejected"
    assert "activation_locked" in locked.hard_reject_flags


def test_real_whole_phone_screen_line_and_bad_lcd_titles_still_pass():
    repair_values = {
        "iPhone 15 Pro": {
            "resale": {"low": 650, "mid": 760, "high": 860},
            "risk_buffer": 60,
            "parts_pricing_status": "verified_screenshot",
            "parts": {"screen_safe": 180},
        },
        "iPhone 14 Pro": {
            "resale": {"low": 520, "mid": 650, "high": 760},
            "risk_buffer": 50,
            "parts_pricing_status": "verified_screenshot",
            "parts": {"screen_safe": 120},
        },
    }
    samples = [
        (
            "iPhone 15 Pro 1TB Unlocked SCREEN LINES/BLACK SPOT",
            "iPhone 15 Pro",
            330,
        ),
        (
            "Broken Unlocked Apple iPhone 14 Pro 128GB Weak Battery Bad LCD",
            "iPhone 14 Pro",
            300,
        ),
    ]

    for title, model, total_cost in samples:
        result = score_listing(
            {"title": title, "condition": "Used", "total_cost": total_cost},
            repair_values,
            scoring_rules=SCORING_RULES,
            min_score_to_alert=70,
            min_profit_to_alert=75,
        )

        assert result.model == model
        assert result.whole_phone_confidence_passed is True
        assert "screen_display_issue" in result.positive_flags
        assert not any(flag.endswith("_not_phone") for flag in result.hard_reject_flags)


def test_too_cheap_parts_only_listing_without_proof_does_not_alert():
    listing = {
        "item_id": "too-cheap-no-proof",
        "title": "Apple iPhone 13 64GB Gray Verizon iOS 5.5in Cracked Screen/Back Parts",
        "condition": "For parts or not working",
        "raw_description": "I'm negotiable",
        "total_cost": 32.73,
    }
    result = score_listing(
        listing,
        {
            "iPhone 13": {
                "resale": {"low": 300, "mid": 360, "high": 430},
                "risk_buffer": 40,
                "parts_pricing_status": "verified_screenshot",
                "parts": {"screen_safe": 80, "back_glass": 70},
            }
        },
        scoring_rules=SCORING_RULES,
        min_score_to_alert=70,
        min_profit_to_alert=75,
    )
    item = {**listing, **result.as_item_fields()}

    assert result.status == "risky"
    assert result.alert_eligible is False
    assert "Too cheap without proof" in result.manual_review_reason
    assert "Parts-only listing lacks power/iCloud/IMEI proof" in result.manual_review_reason
    assert "Model/spec mismatch" in result.manual_review_reason
    assert "iphone_13_64gb_mismatch" in result.listing_classification_flags
    assert "iphone_13_5_5in_mismatch" in result.listing_classification_flags
    assert not should_notify_item(item, result, _settings())
    assert evaluate_alert_decision(item, result, _settings()).tier == "REVIEW"


def test_too_cheap_parts_only_listing_with_proof_can_still_be_eligible():
    listing = {
        "item_id": "too-cheap-with-proof",
        "title": "Apple iPhone 14 128GB Verizon Cracked Screen Parts Powers On Clean IMEI",
        "condition": "For parts or not working",
        "raw_description": "Only issue is cracked screen. Face ID works.",
        "total_cost": 35,
    }
    result = score_listing(
        listing,
        {
            "iPhone 14": {
                "resale": {"low": 420, "mid": 500, "high": 580},
                "risk_buffer": 40,
                "parts_pricing_status": "verified_screenshot",
                "parts": {"screen_safe": 90},
            }
        },
        scoring_rules=SCORING_RULES,
        min_score_to_alert=70,
        min_profit_to_alert=75,
    )
    item = {**listing, **result.as_item_fields()}

    assert result.profit_mid == 335
    assert result.status == "candidate"
    assert result.alert_eligible is True
    assert "Too cheap without proof" not in result.manual_review_reason
    assert should_notify_item(item, result, _settings())


def test_iphone_13_storage_and_screen_size_mismatch_forces_manual_review():
    result = score_listing(
        {
            "item_id": "spec-mismatch-proof",
            "title": "Apple iPhone 13 64GB Verizon 5.5in Cracked Screen Powers On Clean IMEI",
            "condition": "Used",
            "total_cost": 120,
        },
        {
            "iPhone 13": {
                "resale": {"low": 300, "mid": 360, "high": 430},
                "risk_buffer": 40,
                "parts_pricing_status": "verified_screenshot",
                "parts": {"screen_safe": 80},
            }
        },
        scoring_rules=SCORING_RULES,
        min_score_to_alert=70,
        min_profit_to_alert=75,
    )

    assert result.status == "risky"
    assert result.alert_eligible is False
    assert "Model/spec mismatch" in result.manual_review_reason
    assert "iphone_13_64gb_mismatch" in result.listing_classification_flags
    assert "iphone_13_5_5in_mismatch" in result.listing_classification_flags


def test_reasonably_priced_cracked_iphone_still_follows_profit_rules():
    result = score_listing(
        {
            "item_id": "normal-priced-cracked-phone",
            "title": "Apple iPhone 14 128GB Unlocked Cracked Screen Powers On Clean IMEI",
            "condition": "Used",
            "total_cost": 260,
        },
        {
            "iPhone 14": {
                "resale": {"low": 420, "mid": 500, "high": 580},
                "risk_buffer": 40,
                "parts_pricing_status": "verified_screenshot",
                "parts": {"screen_safe": 90},
            }
        },
        scoring_rules=SCORING_RULES,
        min_score_to_alert=70,
        min_profit_to_alert=75,
    )

    assert result.profit_mid == 110
    assert result.status == "candidate"
    assert result.alert_eligible is True
    assert "Too cheap without proof" not in result.manual_review_reason


def _settings():
    class SettingsStub:
        min_score_to_alert = 70
        min_profit_to_alert = 75

    return SettingsStub()


def test_swappa_exit_cost_model_turns_expected_profit_into_net_profit():
    resale_research = {
        "__exit_cost_model__": {
            "enabled": True,
            "marketplace": "swappa",
            "seller_fee_rate": 0.03,
            "buyer_fee_rate": 0.03,
            "payment_processing_rate": 0.0349,
            "payment_processing_fixed": 0.49,
            "payment_processing_base_includes_buyer_fee": True,
            "outbound_shipping_tiers": [
                {"max_sale_price": 300, "cost": 15},
                {"max_sale_price": 600, "cost": 20},
                {"max_sale_price": 1000, "cost": 26},
                {"max_sale_price": None, "cost": 30},
            ],
            "note": "test exit-cost model",
        },
        "iPhone 14": {
            "resale_by_storage": {
                "128GB": {
                    "good": {"low": 520, "mid": 610, "high": 690},
                }
            }
        },
    }
    result = score_listing(
        {
            "title": "Apple iPhone 14 128GB Unlocked Cracked Screen",
            "condition": "Used",
            "total_cost": 260,
        },
        {
            "iPhone 14": {
                "risk_buffer": 50,
                "parts": {"screen_safe": 100},
            }
        },
        resale_research=resale_research,
        scoring_rules=SCORING_RULES,
        min_score_to_alert=70,
        min_profit_to_alert=75,
    )

    assert result.profit_low == 55.22
    assert result.profit_mid == 133.28
    assert result.profit_high == 208.01
    assert result.estimated_profit == 133.28
    assert result.estimated_selling_fees == 40.72
    assert result.estimated_outbound_shipping == 26
    assert result.exit_cost_marketplace == "swappa"
    assert result.exit_cost_note == "test exit-cost model"


def test_bundled_resale_research_is_refreshed_and_enables_exit_costs():
    research = load_resale_research(Path("backend/data/resale_research.json"))

    assert research["__exit_cost_model__"]["enabled"] is True
    assert research["__exit_cost_model__"]["marketplace"] == "swappa"
    assert research["iPhone 14 Pro"]["resale_by_storage"]["512GB"]["good"]["mid"] == 435
    assert research["iPhone 16 Pro Max"]["resale_by_storage"]["256GB"]["good"]["mid"] == 750
    assert research["iPhone 17 Pro Max"]["resale_by_storage"]["1TB"]["good"]["mid"] == 1209
