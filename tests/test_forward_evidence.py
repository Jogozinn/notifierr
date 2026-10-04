from types import SimpleNamespace

import pytest

from backend.outcomes import actual_net_profit
from backend.decision_identity import decision_identity
from backend.scorer import score_listing
from backend.storage import Storage


def test_forward_timestamps_feedback_and_outcome(tmp_path):
    storage = Storage(tmp_path / "forward.sqlite3")
    user = storage.create_user(email="forward@example.com", password_hash="test")
    item = {"item_id": "forward-1", "title": "Apple iPhone 16 cracked screen",
            "price": 100, "shipping": 0, "total_cost": 100,
            "status": "risky", "_scored_for_user": True,
            "item_type": "whole_phone", "scorer_hash": "scorer-a", "rules_hash": "rules-a"}
    storage.upsert_user_item(user["id"], item)
    first = storage.get_user_item(user["id"], "forward-1")
    assert first["first_seen_at"] and first["first_scored_at"]
    assert first["marketplace_origin_at"] is None
    storage.upsert_user_item(user["id"], {**item, "scorer_hash": "scorer-b", "found_at": "2020-01-01T00:00:00+00:00"})
    again = storage.get_user_item(user["id"], "forward-1")
    assert (again["first_seen_at"], again["first_scored_at"]) == (first["first_seen_at"], first["first_scored_at"])
    feedback = storage.upsert_user_item_feedback(user["id"], "forward-1", label="GOOD", note="Useful")
    assert feedback["scorer_hash"] == "scorer-b"
    storage.upsert_user_item_feedback(user["id"], "forward-1", label="BAD")
    assert storage.get_user_item_feedback(user["id"], "forward-1")["scorer_hash"] == "scorer-b"
    assert storage.get_user_item_feedback(user["id"], "forward-1")["note"] == "Useful"
    outcome = storage.upsert_user_item_outcome(user["id"], "forward-1", values={
        "status": "SOLD", "purchase_price": 100, "purchase_tax": 5,
        "parts_cost": 20, "sale_price": 200, "selling_fees": 10,
    })
    assert outcome["actual_net_profit"] == 65
    assert storage.get_user_item(user["id"], "forward-1")["actual_net_profit"] == 65


def test_legacy_rows_remain_unmarked_and_without_invented_timestamps(tmp_path):
    storage = Storage(tmp_path / "legacy.sqlite3")
    user = storage.create_user(email="legacy@example.com", password_hash="test")
    storage.upsert_user_item(user["id"], {"item_id": "old", "title": "Old phone", "status": "risky"})
    with storage.connect() as conn:
        conn.execute("UPDATE marketplace_items SET first_seen_at=NULL, marketplace_origin_at=NULL, retention_managed=0")
        conn.execute("UPDATE user_item_states SET first_scored_at=NULL")
    storage.upsert_user_item(user["id"], {"item_id": "old", "title": "Old phone", "status": "risky", "_scored_for_user": True})
    row = storage.get_user_item(user["id"], "old")
    assert row["first_seen_at"] is None
    assert row["first_scored_at"] is None


def test_legacy_split_import_does_not_mark_old_rows_for_retention(tmp_path):
    path = tmp_path / "legacy_import.sqlite3"
    storage = Storage(path)
    storage.upsert_item({"item_id": "old-split", "title": "Old iPhone", "status": "risky",
                         "found_at": "2024-01-01T00:00:00+00:00"})
    with storage.connect() as conn:
        conn.execute("UPDATE items SET updated_at='2024-01-02T00:00:00+00:00' WHERE item_id='old-split'")
    reopened = Storage(path)
    with reopened.connect() as conn:
        row = conn.execute(
            "SELECT mi.first_seen_at, mi.retention_managed, uis.first_scored_at, uis.item_type "
            "FROM marketplace_items mi JOIN user_item_states uis ON uis.marketplace_item_id=mi.id "
            "WHERE mi.marketplace_item_id='old-split'"
        ).fetchone()
    assert dict(row) == {"first_seen_at": None, "retention_managed": 0,
                         "first_scored_at": None, "item_type": None}


@pytest.mark.parametrize("title", [
    "Genuine Apple OEM iPhone 16 Pro Max OLED LCD Screen Works Cracked Glass As-IS",
    "iPhone 16 Pro Max OLED LCD screen", "OEM replacement screen for iPhone 16",
    "iPhone 16 digitizer assembly", "iPhone 16 back housing", "iPhone 16 chassis",
    "iPhone 16 logic board", "iPhone 16 motherboard", "iPhone 16 camera module",
    "iPhone 16 replacement battery", "iPhone 16 charging flex",
    "iPhone 16 camera lens assembly", "iPhone 16 display works",
])
def test_component_never_gets_phone_economics(title):
    result = score_listing({"title": title, "condition": "Used", "total_cost": 100},
                           {"iPhone 16 Pro Max": {"resale_value": 850}, "iPhone 16": {"resale_value": 600}})
    assert result.item_type == "component", title
    assert result.estimated_profit == 0
    assert result.resale_value == 0


@pytest.mark.parametrize("title", [
    "iPhone 16 Pro Max works, cracked screen", "iPhone works but LCD damaged",
    "iPhone for parts, phone powers on", "complete iPhone, Face ID not working",
    "iPhone 16 Pro Max cracked screen", "iPhone 16 Pro Max damaged LCD but working",
    "iPhone 16 cracked back glass", "iPhone 16 battery needs replacement",
    "iPhone 16 as-is read description",
])
def test_whole_phone_classification(title):
    result = score_listing({"title": title, "condition": "For parts or not working", "total_cost": 100},
                           {"iPhone 16 Pro Max": {"resale_value": 850}, "iPhone 16": {"resale_value": 600}})
    assert result.item_type == "whole_phone", title


@pytest.mark.parametrize("title", ["iPhone 15 screen issue", "iPhone parts only", "Apple iPhone read"])
def test_ambiguous_has_no_complete_phone_resale(title):
    result = score_listing({"title": title, "condition": "Used", "total_cost": 100},
                           {"iPhone 15": {"resale_value": 500}})
    assert result.item_type == "ambiguous"
    assert result.resale_value == 0


def test_actual_net_profit_requires_sale_and_purchase_price():
    assert actual_net_profit("PURCHASED", {"purchase_price": 100}) is None
    assert actual_net_profit("SOLD", {"sale_price": 200}) is None
    assert actual_net_profit("SOLD", {"purchase_price": 100, "sale_price": 200,
                                     "inbound_shipping": 8, "refund_amount": 20}) == 72
    with pytest.raises(ValueError):
        actual_net_profit("SOLD", {"purchase_price": -1, "sale_price": 200})


def test_decision_identity_is_compact_stable_and_data_sensitive():
    settings = SimpleNamespace(min_score_to_alert=70, min_profit_to_alert=75,
                               risky_score_min=35, risky_score_max=69.99)
    inputs = dict(scoring_rules={"rule": ["a"]}, repair_values={"screen": 40},
                  resale_research={"phone": 500}, effective_settings=settings)
    first = decision_identity(**inputs)
    assert first == decision_identity(**inputs)
    assert all(len(value) == 24 for value in first.values())
    assert decision_identity(**{**inputs, "repair_values": {"screen": 41}})["repair_hash"] != first["repair_hash"]
    assert decision_identity(**{**inputs, "scoring_rules": {"rule": ["b"]}})["rules_hash"] != first["rules_hash"]


def test_first_scored_waits_for_score_and_notification_marker_follows_listing(tmp_path):
    storage = Storage(tmp_path / "score.sqlite3")
    user = storage.create_user(email="score@example.com", password_hash="test")
    listing = {"item_id": "new", "title": "iPhone 16 cracked screen", "status": "risky",
               "item_origin_at": "2026-10-01T00:00:00+00:00"}
    storage.upsert_user_item(user["id"], listing)
    before = storage.get_user_item(user["id"], "new")
    assert before["marketplace_origin_at"] == listing["item_origin_at"]
    assert before["first_scored_at"] is None
    storage.upsert_user_item(user["id"], {**listing, "_scored_for_user": True})
    scored = storage.get_user_item(user["id"], "new")
    assert scored["first_scored_at"]
    assert scored["first_seen_at"] == before["first_seen_at"]
    attempt = storage.create_notification_attempt(user_id=user["id"], item_id="new", notification_type="review", skipped=True)
    with storage.connect() as conn:
        assert conn.execute("SELECT retention_managed FROM notification_attempts WHERE id=?", (attempt,)).fetchone()["retention_managed"] == 1
