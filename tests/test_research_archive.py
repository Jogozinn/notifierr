from __future__ import annotations

import json
from datetime import datetime, timezone

from backend.retention import RetentionPolicy, run_retention
from backend.storage import Storage


NOW = datetime(2026, 10, 9, 18, 0, tzinfo=timezone.utc)
OLD = "2026-10-07T00:00:00+00:00"


def _storage_with_old_listing(tmp_path):
    storage = Storage(tmp_path / "research.sqlite3")
    user = storage.create_user(email="research@example.com", password_hash="test", baseline_keywords=[])
    storage.upsert_user_item(
        int(user["id"]),
        {
            "item_id": "archive-me",
            "title": "Apple iPhone 16 Pro 256GB cracked back",
            "price": 345,
            "shipping": 0,
            "total_cost": 345,
            "raw_description": "Long marketplace description that should leave the hot path.",
            "raw_json": {"large": "payload", "nested": {"unused": True}},
            "status": "candidate",
            "model": "iPhone 16 Pro",
            "storage_capacity": "256GB",
            "score": 91,
            "estimated_parts_cost": 40,
            "resale_mid": 610,
            "profit_low": 105,
            "profit_mid": 168,
            "profit_high": 205,
            "estimated_profit": 168,
            "whole_phone_confidence_passed": True,
            "positive_flags": ["whole phone"],
            "risk_flags": ["cracked back"],
            "_scored_for_user": True,
        },
    )
    with storage.connect() as conn:
        row = conn.execute(
            "SELECT id FROM marketplace_items WHERE marketplace_item_id='archive-me'"
        ).fetchone()
        marketplace_id = int(row["id"])
        conn.execute(
            """UPDATE marketplace_items
               SET first_seen_at=?, item_origin_at=?, found_at=?, updated_at=?, retention_managed=1,
                   raw_description=?, raw_json=?
               WHERE id=?""",
            (OLD, OLD, OLD, OLD, "Verbose description", json.dumps({"large": "payload"}), marketplace_id),
        )
        conn.execute(
            """INSERT INTO listing_decision_traces(
                   user_id, marketplace_item_id, scan_cycle_id, trace_json, created_at, retention_managed
               ) VALUES (?, ?, ?, ?, ?, 1)""",
            (
                int(user["id"]),
                marketplace_id,
                1,
                json.dumps({
                    "alert_decision": {"tier": "PROFITABLE", "eligible": True, "blocking_reasons": []},
                    "verdict": {"normalized_bucket": "profitable"},
                }),
                OLD,
            ),
        )
    return storage, int(user["id"]), marketplace_id


def test_lightweight_listing_omits_heavy_marketplace_payload(tmp_path):
    storage, user_id, _ = _storage_with_old_listing(tmp_path)
    full = storage.list_user_items(user_id, include_stale=True, limit=10)[0]
    light = storage.list_user_items(user_id, include_stale=True, lightweight=True, limit=10)[0]

    assert full["raw_description"] == "Verbose description"
    assert full["raw_json"] == {"large": "payload"}
    assert light["raw_description"] is None
    assert light["raw_json"] == {}
    assert light["item_id"] == full["item_id"]
    assert light["profit_mid"] == full["profit_mid"]


def test_retention_compacts_old_listing_into_research_evidence_and_strips_payload(tmp_path):
    storage, user_id, marketplace_id = _storage_with_old_listing(tmp_path)
    policy = RetentionPolicy(
        trace_days=3650,
        reviewed_trace_days=3650,
        search_days=3650,
        notification_days=3650,
        stale_item_days=3650,
        compact_after_hours=36,
        batch_size=20,
    )

    report = run_retention(storage, policy=policy, dry_run=False, now=NOW)

    assert report["counts"]["research_evidence"] == 1
    assert report["counts"]["payloads_compacted"] == 1
    days = storage.list_research_bundle_days()
    assert len(days) == 1
    assert {key: days[0][key] for key in ("day", "records", "interesting_records", "first_id", "last_id")} == {
        "day": "2026-10-07",
        "records": 1,
        "interesting_records": 1,
        "first_id": 1,
        "last_id": 1,
    }
    assert days[0]["updated_at"]
    evidence_row = storage.list_research_evidence(evidence_day="2026-10-07")[0]
    evidence = evidence_row["evidence"]
    assert evidence["item_id"] == "archive-me"
    assert evidence["model"] == "iPhone 16 Pro"
    assert evidence["expected_net_profit"] == 168
    assert evidence["decision"]["tier"] == "PROFITABLE"
    assert "raw_description" not in evidence
    assert "raw_json" not in evidence

    with storage.connect() as conn:
        raw = conn.execute(
            "SELECT raw_description, raw_json FROM marketplace_items WHERE id=?", (marketplace_id,)
        ).fetchone()
        assert raw["raw_description"] is None
        assert raw["raw_json"] == "{}"

    # The archive deliberately has no marketplace-item FK, so evidence survives
    # eventual deletion of the bulky/raw operational listing row.
    with storage.connect() as conn:
        conn.execute("DELETE FROM marketplace_items WHERE id=?", (marketplace_id,))
    remaining = storage.list_research_evidence(evidence_day="2026-10-07", user_id=user_id)
    assert len(remaining) == 1
    assert remaining[0]["evidence"]["item_id"] == "archive-me"


def test_retention_compaction_is_idempotent(tmp_path):
    storage, _, _ = _storage_with_old_listing(tmp_path)
    policy = RetentionPolicy(
        trace_days=3650,
        reviewed_trace_days=3650,
        search_days=3650,
        notification_days=3650,
        stale_item_days=3650,
        compact_after_hours=36,
        batch_size=20,
    )

    first = run_retention(storage, policy=policy, dry_run=False, now=NOW)
    second = run_retention(storage, policy=policy, dry_run=False, now=NOW)

    assert first["counts"]["research_evidence"] == 1
    assert second["counts"]["research_evidence"] == 0
    assert second["counts"]["payloads_compacted"] == 0
    assert storage.list_research_bundle_days()[0]["records"] == 1


def test_research_archive_refreshes_when_human_feedback_arrives(tmp_path):
    storage, user_id, _ = _storage_with_old_listing(tmp_path)
    policy = RetentionPolicy(
        trace_days=3650,
        reviewed_trace_days=3650,
        search_days=3650,
        notification_days=3650,
        stale_item_days=3650,
        compact_after_hours=36,
        batch_size=20,
    )
    first = run_retention(storage, policy=policy, dry_run=False, now=NOW)
    first_meta = storage.list_research_bundle_days()[0]
    first_updated = first_meta["updated_at"]
    assert first["counts"]["research_evidence"] == 1

    storage.upsert_user_item_feedback(user_id, "archive-me", label="GOOD", note="Worth buying")
    later = datetime(2026, 10, 9, 19, 0, tzinfo=timezone.utc)
    second = run_retention(storage, policy=policy, dry_run=False, now=later)

    assert second["counts"]["research_evidence"] == 1
    evidence = storage.list_research_evidence(evidence_day="2026-10-07")[0]["evidence"]
    assert evidence["feedback"] == "GOOD"
    changed = storage.list_research_bundle_days(updated_after=first_updated)
    assert len(changed) == 1
    assert changed[0]["day"] == "2026-10-07"
    assert changed[0]["updated_at"] > first_updated
