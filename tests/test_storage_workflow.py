import sqlite3
from pathlib import Path

from backend.storage import Storage


def test_user_workflow_statuses_and_note_are_stored():
    storage = Storage(Path(":memory:"))
    storage.upsert_item(
        {
            "item_id": "workflow-1",
            "title": "Apple iPhone 14 Pro 128GB Unlocked Cracked Screen",
            "status": "candidate",
            "seller_username": "good-seller",
        }
    )

    watched = storage.set_user_status("workflow-1", "watched")
    assert watched["user_status"] == "watched"
    assert watched["watched_at"]

    noted = storage.set_note("workflow-1", "Check seller photos before bidding.")
    assert noted["user_note"] == "Check seller photos before bidding."

    promoted = storage.set_user_status("workflow-1", "promoted")
    assert promoted["user_status"] == "promoted"
    assert promoted["promoted_at"]


def test_ignored_seller_and_keyword_mark_matching_items():
    storage = Storage(Path(":memory:"))
    storage.upsert_item(
        {
            "item_id": "ignored-seller-source",
            "title": "Apple iPhone 14 128GB Cracked Screen",
            "status": "candidate",
            "seller_username": "parts-store",
        }
    )

    storage.ignore_seller_from_item("ignored-seller-source", reason="Accessory-heavy seller")
    ignored_item = storage.get_item("ignored-seller-source")
    assert ignored_item["user_status"] == "ignored"
    assert ignored_item["ignored_seller"] == "parts-store"

    seller_match = storage.ignored_match({"seller_username": "parts-store", "title": "Apple iPhone 15"})
    assert seller_match["user_status"] == "ignored"
    assert seller_match["ignored_reason"] == "Accessory-heavy seller"

    storage.add_ignored_keyword("screen digitizer", reason="Display part listings")
    keyword_match = storage.ignored_match({"seller_username": "other", "title": "iPhone 15 screen digitizer cracked"})
    assert keyword_match["user_status"] == "ignored"
    assert keyword_match["ignored_reason"] == "Display part listings"


def test_list_items_excludes_ignored_by_default():
    storage = Storage(Path(":memory:"))
    storage.upsert_item({"item_id": "new-1", "title": "Apple iPhone 14 cracked screen", "status": "candidate"})
    storage.upsert_item(
        {
            "item_id": "ignored-1",
            "title": "Apple iPhone 14 screen part",
            "status": "rejected",
            "user_status": "ignored",
            "ignored_at": "2026-01-01T00:00:00Z",
        }
    )

    assert [item["item_id"] for item in storage.list_items()] == ["new-1"]
    assert {item["item_id"] for item in storage.list_items(include_ignored=True)} == {"new-1", "ignored-1"}


def test_old_database_without_workflow_columns_migrates(tmp_path):
    db_path = tmp_path / "old-notifierr.sqlite3"
    with sqlite3.connect(str(db_path)) as connection:
        connection.execute("PRAGMA journal_mode=MEMORY")
        connection.execute(
            """
            CREATE TABLE items (
                item_id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                price REAL NOT NULL DEFAULT 0,
                shipping REAL NOT NULL DEFAULT 0,
                total_cost REAL NOT NULL DEFAULT 0,
                found_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                score REAL NOT NULL DEFAULT 0,
                status TEXT NOT NULL,
                estimated_profit REAL NOT NULL DEFAULT 0,
                hard_reject_flags TEXT NOT NULL DEFAULT '[]',
                positive_flags TEXT NOT NULL DEFAULT '[]',
                risk_flags TEXT NOT NULL DEFAULT '[]',
                raw_json TEXT
            )
            """
        )
        connection.execute(
            """
            INSERT INTO items (
                item_id, title, found_at, updated_at, status
            )
            VALUES (
                'old-1', 'Apple iPhone 14 cracked screen', '2026-01-01T00:00:00Z',
                '2026-01-01T00:00:00Z', 'candidate'
            )
            """
        )

    storage = Storage(db_path)
    columns = _item_columns(db_path)

    assert "user_status" in columns
    assert "user_note" in columns
    assert "reviewed_at" in columns
    assert "ignored_at" in columns
    assert "watched_at" in columns
    assert "promoted_at" in columns
    assert "ignored_reason" in columns
    assert "ignored_seller" in columns
    assert "updated_by_user_at" in columns
    assert "model" in columns
    assert "resale_low" in columns
    assert "resale_mid" in columns
    assert "resale_high" in columns
    assert "profit_low" in columns
    assert "profit_mid" in columns
    assert "profit_high" in columns
    assert "resale_confidence" in columns
    assert "resale_sample_size" in columns
    assert "resale_note" in columns
    assert "alert_eligible" in columns
    assert storage.get_item("old-1")["user_status"] == "new"
    assert storage.stats()["new_items"] == 1


def test_storage_init_is_idempotent_for_migrated_database(tmp_path):
    db_path = tmp_path / "idempotent.sqlite3"

    first = Storage(db_path)
    first.upsert_item({"item_id": "item-1", "title": "Apple iPhone 13 cracked screen", "status": "risky"})

    second = Storage(db_path)
    second_stats = second.stats()

    assert second.get_item("item-1")["user_status"] == "new"
    assert second_stats["total"] == 1
    assert "user_status" in _item_columns(db_path)


def test_storage_round_trips_resale_and_profit_range_fields():
    storage = Storage(Path(":memory:"))
    storage.upsert_item(
        {
            "item_id": "range-1",
            "title": "Apple iPhone 14 128GB Unlocked Cracked Screen",
            "status": "candidate",
            "resale_value": 610,
            "resale_low": 520,
            "resale_mid": 610,
            "resale_high": 690,
            "profit_low": 110,
            "profit_mid": 200,
            "profit_high": 280,
            "resale_confidence": "high",
            "resale_sample_size": 18,
            "resale_note": "Manual sold comps",
            "alert_eligible": True,
        }
    )

    item = storage.get_item("range-1")

    assert item["resale_value"] == 610
    assert item["resale_low"] == 520
    assert item["resale_mid"] == 610
    assert item["resale_high"] == 690
    assert item["profit_low"] == 110
    assert item["profit_mid"] == 200
    assert item["profit_high"] == 280
    assert item["resale_confidence"] == "high"
    assert item["resale_sample_size"] == 18
    assert item["resale_note"] == "Manual sold comps"
    assert item["alert_eligible"] is True


def test_previously_alerted_item_can_be_demoted_after_rescore():
    storage = Storage(Path(":memory:"))
    storage.upsert_item(
        {
            "item_id": "stale-alert",
            "title": "Apple iPhone 14 128GB Unlocked Cracked Screen",
            "status": "candidate",
            "alert_eligible": True,
        }
    )
    storage.mark_alerted("stale-alert")

    storage.upsert_item(
        {
            "item_id": "stale-alert",
            "title": "APPLE iPhone 16 Plus OEM Screen - NO DISPLAY Bad Lcd",
            "status": "rejected",
            "score": -100,
            "alert_eligible": False,
            "whole_phone_confidence_passed": False,
            "hard_reject_flags": ["screen_part_not_phone"],
            "listing_classification_flags": ["screen_part_not_phone"],
        }
    )

    item = storage.get_item("stale-alert")
    assert item["status"] == "rejected"
    assert item["alerted_at"]
    assert item["alert_eligible"] is False


def test_priority_review_stats_surface_only_worthwhile_uncertain_items():
    storage = Storage(Path(":memory:"))
    base_item = {
        "title": "Apple iPhone 14 128GB Unlocked Cracked Screen",
        "status": "risky",
        "user_status": "new",
        "model": "iPhone 14",
        "whole_phone_confidence_passed": True,
        "has_repair_issue": True,
        "resale_value": 430,
        "resale_mid": 430,
        "estimated_parts_cost_available": True,
    }
    storage.upsert_item(
        {
            **base_item,
            "item_id": "too-cheap-proof-needed",
            "total_cost": 35,
            "profit_mid": 20,
            "profit_high": 250,
            "manual_review_reason": "Too cheap without proof; Parts-only listing lacks power/iCloud/IMEI proof",
        }
    )
    storage.upsert_item(
        {
            **base_item,
            "item_id": "missing-part-strong-model",
            "estimated_parts_cost_available": False,
            "manual_review_reason": "Missing part price",
        }
    )
    storage.upsert_item(
        {
            **base_item,
            "item_id": "near-threshold-profit",
            "profit_mid": 40,
            "profit_high": 90,
            "manual_review_reason": "Expected profit below threshold",
        }
    )
    storage.upsert_item(
        {
            **base_item,
            "item_id": "no-repair-issue",
            "has_repair_issue": False,
            "manual_review_reason": "No specific repair issue detected",
        }
    )
    storage.upsert_item(
        {
            **base_item,
            "item_id": "accessory-part",
            "listing_classification_flags": ["screen_part_not_phone"],
            "manual_review_reason": "Accessory/part listing",
        }
    )
    storage.upsert_item(
        {
            **base_item,
            "item_id": "old-model",
            "hard_reject_flags": ["old_model_ignored"],
            "manual_review_reason": "Old model",
        }
    )
    storage.upsert_item(
        {
            **base_item,
            "item_id": "best-find",
            "status": "candidate",
            "alert_eligible": True,
            "profit_mid": 150,
            "profit_high": 240,
        }
    )

    stats = storage.stats()

    assert stats["priority_review"] == 3
    assert stats["best_finds"] == 1


def _item_columns(db_path: Path) -> set[str]:
    with sqlite3.connect(str(db_path)) as connection:
        return {row[1] for row in connection.execute("PRAGMA table_info(items)").fetchall()}
