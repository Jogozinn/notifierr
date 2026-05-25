import json
from pathlib import Path

from backend.config import load_scoring_rules
from backend.main import should_notify_item
from backend.scorer import detect_model, score_listing
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


def test_frame_face_id_and_no_power_samples_are_hard_rejected():
    samples = {
        "bent_frame": "bent_frame",
        "face_id_not_working": "face_id_not_working",
        "no_power": "no_power",
    }

    for sample_name, flag in samples.items():
        result = score_sample(sample_name)
        assert result.status == "rejected"
        assert flag in result.hard_reject_flags


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


def test_duplicate_items_do_not_notify_twice():
    listing = SAMPLES["cracked_screen_powers_on_unlocked"]
    result = score_sample("cracked_screen_powers_on_unlocked")
    item = {**listing, **result.as_item_fields()}
    storage = Storage(Path(":memory:"))

    storage.upsert_item(item)
    assert should_notify_item(item, result, _settings())
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
    assert "Only optimistic profit clears threshold" in result.manual_review_reason


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
