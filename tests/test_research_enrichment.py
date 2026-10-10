"""Research detail enrichment never turns incomplete evidence into automatic alerts."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from backend import main
from backend.alerting import evaluate_alert_decision
from backend.scorer import score_listing
from backend.storage import Storage


def _listing(title: str, **overrides):
    now = datetime.now(timezone.utc).isoformat()
    return {
        "item_id": "v1|research-demo|0", "title": title, "condition": "For parts or not working",
        "category": "Cell Phones & Smartphones", "total_cost": 150,
        "item_origin_at": now, "found_at": now, "availability_status": "in_stock",
        "raw_description": None, "raw_json": {}, **overrides,
    }


def _score(listing):
    return score_listing(
        listing, main.repair_values, resale_research=main.resale_research,
        scoring_rules=main.scoring_rules,
    )


def test_research_shortlists_ambiguous_handset_with_supported_model_storage():
    listing = _listing("Apple iPhone 15 Pro 256GB Unlocked Read Description")
    result = _score(listing)
    assert result.item_type == "ambiguous"
    assert main._research_detail_reasons(listing, result, main.resale_research) == [
        "research_ambiguous_handset", "description_missing",
    ]


def test_component_and_hard_risk_can_never_consume_research_budget():
    for title in (
        "OEM iPhone 17 Pro Max 256GB Display Screen Replacement Only",
        "Apple iPhone 15 Pro 256GB iCloud Locked Read Description",
        "Apple iPhone 15 Pro 256GB Liquid Damage Read Description",
    ):
        listing = _listing(title)
        result = _score(listing)
        assert main._research_detail_reasons(listing, result, main.resale_research) == []


def test_research_does_not_refetch_after_success_or_during_backoff():
    listing = _listing("Apple iPhone 15 Pro 256GB Unlocked Read Description")
    result = _score(listing)
    now = datetime.now(timezone.utc)
    for existing in (
        {"detail_fetch_status": "succeeded"},
        {"raw_description": "It is a whole phone"},
        {"detail_fetch_attempted_at": (now - timedelta(hours=1)).isoformat()},
        {"detail_fetch_retry_after": (now + timedelta(hours=2)).isoformat()},
    ):
        assert main._research_detail_reasons(listing, result, main.resale_research, existing=existing, now=now) == []


def test_research_budget_disabled_or_limited_across_scans(monkeypatch):
    monkeypatch.setattr(main, "settings", SimpleNamespace(research_detail_per_scan=1, research_detail_daily_limit=24))
    monkeypatch.setattr(main, "storage", SimpleNamespace(research_detail_attempts_today=lambda: 23))
    assert main._research_quota_for_scan() == 1
    monkeypatch.setattr(main, "storage", SimpleNamespace(research_detail_attempts_today=lambda: 24))
    assert main._research_quota_for_scan() == 0
    monkeypatch.setattr(main, "settings", SimpleNamespace(research_detail_per_scan=0, research_detail_daily_limit=24))
    assert main._research_quota_for_scan() == 0


def test_research_origin_requires_human_review_before_push():
    from backend.scorer import ScoreResult
    result = ScoreResult(
        score=90, status="candidate", model="iPhone 15 Pro", estimated_profit=140,
        resale_value=500, estimated_parts_cost=70, risk_buffer=50,
        resale_low=450, resale_mid=500, resale_high=560, profit_low=90,
        profit_mid=140, profit_high=200, storage_capacity="256GB",
        parts_pricing_status="verified_screenshot", estimated_parts_cost_available=True,
        whole_phone_confidence_passed=True, whole_phone_score=10,
        has_repair_issue=True, positive_flags=["cracked_screen", "unlocked", "clean_imei"],
    )
    candidate = {
        **result.as_item_fields(), "item_id": "demo", "total_cost": 200,
        "item_age_minutes": 10, "fresh_for_active_queue": True,
        "availability_status": "active", "user_status": "new",
        "detail_fetch_reason": "research_ambiguous_handset,description_missing",
    }
    settings = SimpleNamespace(min_score_to_alert=70, min_profit_to_alert=75,
                               notification_settings={}, max_alert_item_age_minutes=360)
    decision = evaluate_alert_decision(candidate, result, settings)
    assert not decision.eligible
    assert "RESEARCH_ENRICHMENT_REQUIRES_REVIEW" in decision.blocking_reasons
    reviewed = evaluate_alert_decision({**candidate, "user_status": "reviewed"}, result, settings)
    assert "RESEARCH_ENRICHMENT_REQUIRES_REVIEW" not in reviewed.blocking_reasons


def test_lightweight_diagnostics_can_see_persisted_description_without_body(tmp_path):
    from backend.needs_data_diagnostics import diagnose_listing
    db = Storage(tmp_path / "notifierr-research.sqlite3")
    # An active user can inspect a persisted description without loading it.
    user = db.create_user(email="research@example.com", password_hash="unused", role="user")
    listing = _listing("Apple iPhone 14 128GB Unlocked Cracked Screen")
    listing["raw_description"] = "Phone works"
    db.upsert_marketplace_item(listing)
    db.upsert_user_item_state(user["id"], {**listing, "status": "risky", "item_type": "ambiguous"})
    rows = db.list_user_items(user["id"], limit=3, lightweight=True, include_stale=True)
    assert len(rows) == 1
    assert rows[0]["raw_description"] is None
    assert rows[0]["has_raw_description"] == 1
    assert diagnose_listing(rows[0])["description_available"] is True


def test_enriched_candidate_is_held_for_human_review_and_not_alerted():
    item = {
        "status": "candidate", "alert_eligible": True, "manual_review_needed": False,
        "detail_fetch_reason": "research_ambiguous_handset,description_missing",
        "manual_review_reason": "Low-confidence pricing",
    }
    main._hold_research_enrichment_for_review(item)
    assert item["status"] == "risky"
    assert item["alert_eligible"] is False
    assert item["manual_review_needed"] is True
    assert "Low-confidence pricing" in item["manual_review_reason"]
    main._hold_research_enrichment_for_review(item)
    assert item["manual_review_reason"].count("Research detail enriched") == 1


def test_verified_title_phrases_and_component_suppression():
    phone = _score(_listing("iPhone 14 Pro Max unlocked 128GB Used Good Camera Glass Broken No Charger"))
    assert phone.item_type == "whole_phone"
    assert "camera_lens_cracked" in phone.positive_flags
    fully_working = _score(_listing("Apple iPhone 15 Pro 512GB Unlocked Fully Working Glass Cracked"))
    assert fully_working.item_type == "whole_phone"
    bad_face_id = _score(_listing("Apple iPhone 14 Pro Max 128GB Unlocked BAD FACE ID/FRONT CAMERA"))
    assert "face_id_issue" in bad_face_id.positive_flags
    screen = _score(_listing("OEM Apple iPhone 17 Pro Max OLED Display Screen Assembly Replacement"))
    assert screen.item_type == "component"
    assert screen.estimated_profit_available is False
