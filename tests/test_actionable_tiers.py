from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from backend.alerting import evaluate_alert_decision
from backend.main import MAX_DETAIL_REFRESHES_PER_SCAN, _detail_refresh_reasons, _rotating_system_searches, scoring_rules
from backend.scorer import ScoreResult, detect_model_from_listing, extract_description_signals, score_listing


def _settings(**notification_overrides):
    notification = {
        "gem_min_expected_profit": 75,
        "profitable_min_expected_profit": 50,
        "review_min_expected_profit": 25,
        "review_min_upside_profit": 60,
        "gem_min_roi": 0.25,
        "profitable_min_roi": 0.15,
        "review_min_roi": 0.05,
        "max_listing_age_minutes": 360,
        **notification_overrides,
    }
    return SimpleNamespace(
        notification_settings=notification,
        min_profit_to_alert=75,
        min_score_to_alert=70,
        max_alert_item_age_minutes=360,
    )


def _result(**overrides):
    values = {
        "score": 90,
        "status": "candidate",
        "model": "iPhone 15 Pro",
        "estimated_profit": 120,
        "resale_value": 500,
        "estimated_parts_cost": 70,
        "risk_buffer": 50,
        "resale_low": 450,
        "resale_mid": 500,
        "resale_high": 560,
        "profit_low": 90,
        "profit_mid": 140,
        "profit_high": 200,
        "storage_capacity": "256GB",
        "parts_pricing_status": "verified_screenshot",
        "estimated_parts_cost_available": True,
        "whole_phone_confidence_passed": True,
        "whole_phone_score": 7,
        "has_repair_issue": True,
        "positive_flags": ["cracked_screen", "powers_on", "unlocked", "clean_imei"],
    }
    values.update(overrides)
    return ScoreResult(**values)


def _item(result, **overrides):
    values = {
        "item_id": "tier-fixture",
        "title": "Apple iPhone 15 Pro 256GB Unlocked cracked screen powers on clean IMEI",
        "raw_description": "Phone is fully functional except for the cracked screen.",
        "total_cost": 200,
        "availability_status": "active",
        "fresh_for_active_queue": True,
        "item_age_minutes": 15,
        **result.as_item_fields(),
    }
    values.update(overrides)
    return values


def test_tier_evaluator_separates_gem_profitable_and_review():
    gem_result = _result()
    assert evaluate_alert_decision(_item(gem_result), gem_result, _settings()).tier == "GEM"

    profitable_result = _result(
        profit_low=10,
        profit_mid=75,
        profit_high=130,
        parts_pricing_status="estimated",
    )
    profitable = evaluate_alert_decision(_item(profitable_result), profitable_result, _settings())
    assert profitable.tier == "PROFITABLE"
    assert "PARTS_PRICE_ESTIMATED" in profitable.soft_warnings

    review_result = _result(
        status="risky",
        profit_low=-40,
        profit_mid=30,
        profit_high=100,
        parts_pricing_status="missing",
        estimated_parts_cost_available=False,
        whole_phone_confidence_passed=False,
        whole_phone_score=4,
    )
    review = evaluate_alert_decision(_item(review_result), review_result, _settings())
    assert review.tier == "REVIEW"
    assert "PARTS_PRICE_MISSING" in review.soft_warnings


def test_hard_risks_and_components_never_enter_actionable_tier():
    for hard_flag in ("icloud_locked", "blacklisted", "financed", "stolen", "motherboard_issue"):
        result = _result(hard_reject_flags=[hard_flag])
        decision = evaluate_alert_decision(_item(result), result, _settings())
        assert decision.eligible is False
        assert f"HARD_RISK_{hard_flag.upper()}" in decision.blocking_reasons

    component = _result(hard_reject_flags=["screen_part_not_phone"])
    decision = evaluate_alert_decision(_item(component), component, _settings())
    assert decision.eligible is False
    assert "COMPONENT_OR_ACCESSORY" in decision.blocking_reasons


def test_face_id_failure_is_a_repairable_issue_not_a_catastrophic_reject():
    result = score_listing(
        {
            "item_id": "face-id-repair",
            "title": "Apple iPhone 15 Pro 256GB Unlocked Face ID not working",
            "raw_description": "Phone powers on, clean IMEI, and everything else works.",
            "total_cost": 250,
        },
        {
            "iPhone 15 Pro": {
                "resale": {"low": 520, "mid": 590, "high": 660},
                "parts": {"face_id": 90},
                "risk_buffer": 60,
                "parts_pricing_status": "verified_screenshot",
            }
        },
        scoring_rules=scoring_rules,
    )
    assert "face_id_not_working" not in result.hard_reject_flags
    assert "face_id_issue" in result.positive_flags


def test_model_detection_prefers_structured_specifics_and_ignores_template_models():
    repair_values = {"iPhone 14": {}, "iPhone 15 Pro": {}, "iPhone 16 Pro Max": {}}
    listing = {
        "title": "Apple smartphone cracked screen",
        "raw_description": "We also sell iPhone 14 and iPhone 16 Pro Max accessories.",
        "raw_json": {"localizedAspects": [{"name": "Model", "value": "Apple iPhone 15 Pro"}]},
    }
    assert detect_model_from_listing(listing, repair_values) == "iPhone 15 Pro"
    assert detect_model_from_listing(
        {"title": "Apple smartphone", "raw_description": "This is an iPhone 16 Pro Max with a cracked screen."},
        repair_values,
    ) == "iPhone 16 Pro Max"
    assert detect_model_from_listing(
        {"title": "Apple smartphone", "raw_description": "Template mentions iPhone 14 and iPhone 15 Pro."},
        repair_values,
    ) == "unknown"


def test_description_signals_cover_repairable_display_and_functionality_language():
    signals = extract_description_signals({
        "title": "iPhone bad screen no image",
        "raw_description": "Powers on and is fully functional except the OLED. Touch digitizer works and Face ID works.",
    })
    assert "bad_lcd" in signals["repair_detail_signals"]
    assert "powers_on" in signals["functionality_signals"]
    assert "fully_functional" in signals["functionality_signals"]
    assert "face_id_works" in signals["functionality_signals"]


def test_nearest_model_pricing_is_ranged_and_explicitly_estimated():
    result = score_listing(
        {
            "item_id": "nearest-pricing",
            "title": "Apple iPhone 16 Plus 256GB Unlocked cracked back glass",
            "raw_description": "Phone powers on, clean IMEI, and works except for cracked back glass.",
            "condition": "For parts or not working",
            "total_cost": 180,
        },
        {
            "iPhone 15 Plus": {
                "resale": {"low": 420, "mid": 480, "high": 550},
                "parts": {"back_glass": 95},
                "risk_buffer": 60,
                "parts_pricing_status": "verified_screenshot",
            }
        },
        min_score_to_alert=70,
        min_profit_to_alert=75,
    )
    assert result.model == "iPhone 16 Plus"
    assert result.resale_source == "estimated_nearest_model"
    assert result.resale_low < result.resale_high
    assert "Estimated from iPhone 15 Plus" in result.resale_note
    assert result.parts_pricing_status == "estimated"
    assert result.pricing_warning == "Parts estimate not verified"


def test_detail_refresh_prioritizes_repairable_phone_with_missing_fields():
    result = _result(
        storage_capacity=None,
        has_repair_issue=True,
        positive_flags=["cracked_screen"],
    )
    listing = {"title": "Apple iPhone 15 Pro cracked screen", "raw_description": ""}
    reasons = _detail_refresh_reasons(listing, result, _settings())
    assert {"description_missing", "storage_missing", "functionality_or_activation_missing", "carrier_missing"} <= set(reasons)

    accessory = _result(hard_reject_flags=["screen_part_not_phone"])
    assert _detail_refresh_reasons({"title": "iPhone 15 Pro replacement screen only"}, accessory, _settings()) == []


def test_rotating_searches_are_deterministic_partitioned_and_freshness_oriented():
    now = datetime(2026, 7, 22, 12, tzinfo=timezone.utc)
    current = _rotating_system_searches(now=now, count=8)
    repeated = _rotating_system_searches(now=now, count=8)
    later = _rotating_system_searches(now=now + timedelta(hours=1), count=8)
    assert current == repeated
    assert current != later
    assert len(current) == len(set(current)) == 8
    assert all("iPhone" in query for query in current)
    assert MAX_DETAIL_REFRESHES_PER_SCAN == 15
