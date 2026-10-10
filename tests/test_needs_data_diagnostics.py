"""Read-only Needs Data stage reporting; no scoring decisions changed."""
from backend.needs_data_diagnostics import diagnose_listing, summarize_needs_data, decision_consistency_scan


def _base(item_id: str, **overrides):
    return {
        "item_id": item_id,
        "title": "iPhone 14 Pro Max 128GB unlocked camera glass broken",
        "item_type": "ambiguous",
        "model": "iPhone 14 Pro Max",
        "storage_capacity": "128GB",
        "whole_phone_confidence_passed": False,
        "whole_phone_score": 5,
        "estimated_parts_cost_available": False,
        "estimated_profit_available": False,
        "has_repair_issue": False,
        "resale_mid": 0,
        "estimated_profit": 0,
        "manual_review_reason": "Not a whole phone; Parts-only ambiguous",
        "listing_classification_flags": ["not_whole_phone"],
        "hard_reject_flags": [],
        "positive_flags": [],
        **overrides,
    }


def test_ambiguous_listing_has_first_gate_not_independent_resale_or_zero_profit():
    diagnosed = diagnose_listing(_base("ambiguous"))
    assert diagnosed["primary_gate"] == "whole_phone_verification"
    assert diagnosed["resale_block_origin"] == "whole_phone_gate"
    assert "resale_unavailable" in diagnosed["observed_reasons"]
    assert diagnosed["estimated_profit"] is None
    assert diagnosed["storage_capacity"] == "128GB"


def test_component_cannot_gain_handset_valuation_in_diagnostics():
    diagnosed = diagnose_listing(_base(
        "display", title="OEM iPhone 17 Pro Max display screen",
        item_type="component", hard_reject_flags=["screen_part_not_phone"],
    ))
    assert diagnosed["primary_gate"] == "component_or_accessory_evidence"
    assert diagnosed["resale_block_origin"] == "component_gate"
    assert "component_evidence" in diagnosed["observed_reasons"]


def test_unprofitable_complete_listing_is_not_called_missing_data():
    diagnosed = diagnose_listing(_base(
        "negative", item_type="whole_phone", whole_phone_confidence_passed=True,
        estimated_parts_cost_available=True, estimated_profit_available=True,
        has_repair_issue=True, resale_mid=400, profit_mid=-37.49,
        manual_review_reason="Expected profit below threshold",
    ))
    assert diagnosed["primary_gate"] == "profit_qualification"
    assert diagnosed["estimated_profit"] == -37.49
    assert "resale_unavailable" not in diagnosed["observed_reasons"]


def test_summary_counts_first_gates_separately_and_marks_truncated():
    items = [_base("ambiguous"), _base(
        "negative", item_type="whole_phone", whole_phone_confidence_passed=True,
        estimated_parts_cost_available=True, estimated_profit_available=True,
        has_repair_issue=True, resale_mid=400, profit_mid=-3,
        manual_review_reason="Expected profit below threshold",
    )]
    result = summarize_needs_data(items, limit=1, source_rows=2, source_truncated=False, hot_hours=36)
    assert result["queue_total_within_source"] == 2
    assert result["records_returned"] == 1
    assert result["complete"] is False
    assert result["primary_gate_counts"] == {
        "profit_qualification": 1, "whole_phone_verification": 1,
    }
    result2 = summarize_needs_data(items, limit=2, source_rows=1500, source_truncated=True, hot_hours=36)
    assert result2["complete"] is False




def test_cross_queue_alert_flags_do_not_change_decisions():
    items = [
        _base("unexpected", alert_tier="PROFITABLE", item_type="component", estimated_profit_available=True,
              profit_mid=-10, manual_review_reason="Expected profit below threshold"),
        _base("plain", alert_tier=None),
    ]
    original = [dict(item) for item in items]
    check = decision_consistency_scan(items)
    assert check["counts"] == {
        "actionable_tier_with_nonpositive_profit": 1,
        "component_with_actionable_tier": 1,
        "tier_vs_scoring_threshold_note": 1,
    }
    assert len(check["examples"]) == 1
    assert items == original


def test_unverified_whole_phone_does_not_falsely_claim_resale_was_blocked_by_type():
    detail = diagnose_listing(_base("unverified", item_type="whole_phone"))
    assert detail["primary_gate"] == "whole_phone_verification"
    assert detail["resale_block_origin"] == "unconfirmed_resale_cause"
