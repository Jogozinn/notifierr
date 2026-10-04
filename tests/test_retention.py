from datetime import datetime, timezone

import pytest

from backend.retention import RetentionPolicy, _rollup_day, run_retention
from backend.storage import Storage


OLD = "2025-01-01T00:00:00+00:00"
NOW = datetime(2026, 10, 3, tzinfo=timezone.utc)
POLICY = RetentionPolicy(trace_days=14, search_days=30, notification_days=90,
                         stale_item_days=30, batch_size=1)


def _fixture(tmp_path):
    storage = Storage(tmp_path / "retention.sqlite3")
    user = storage.create_user(email="retention@example.com", password_hash="test")
    for item_id in ("forward", "reviewed", "legacy"):
        storage.upsert_user_item(user["id"], {"item_id": item_id, "title": f"iPhone {item_id}",
                                                   "status": "risky", "_scored_for_user": True})
    storage.upsert_user_item_feedback(user["id"], "reviewed", label="GOOD")
    run = storage.create_shared_scan_run(mode="shared", triggered_by_user_id=None,
                                          active_users=1, unique_searches=2)
    searches = [storage.record_shared_scan_search(run, search_signature="same", keyword=f"keyword{i}",
                                                   limit_value=10, sort_order="newlyListed", marketplace_id=None,
                                                   subscribed_user_count=1, items_returned=2)
                for i in range(2)]
    for search in searches:
        storage.record_shared_scan_results(search, ["forward", "reviewed"])
        storage.update_shared_scan_result(search, "forward", newly_discovered=True, detail_status="success",
                                          item_type="whole_phone", tier="GEM", needs_data=False,
                                          rejected=False, alert_eligible=True, notification_status="sent")
    storage.finish_shared_scan_run(run, status="completed", api_calls_made=2, total_items_returned=4,
                                   total_users_evaluated=1, total_items_scored=2, total_alerts_sent=1)
    with storage.connect() as conn:
        conn.execute("UPDATE marketplace_items SET first_seen_at=?, updated_at=? WHERE marketplace_item_id IN ('forward','reviewed','legacy')", (OLD, OLD))
        conn.execute("UPDATE marketplace_items SET retention_managed=0, first_seen_at=NULL WHERE marketplace_item_id='legacy'")
        conn.execute("UPDATE shared_scan_runs SET started_at=?, finished_at=? WHERE id=?", (OLD, OLD, run))
        ids = {r["marketplace_item_id"]: r["id"] for r in conn.execute("SELECT id,marketplace_item_id FROM marketplace_items")}
        for item_id in ids:
            conn.execute("INSERT INTO listing_decision_traces(user_id,marketplace_item_id,scan_cycle_id,trace_json,created_at,retention_managed) VALUES (?,?,?,?,?,?)",
                         (user["id"], ids[item_id], 999, "{}",
                          "2026-08-15T00:00:00+00:00" if item_id == "reviewed" else OLD,
                          int(item_id != "legacy")))
    return storage


def test_dry_run_apply_rollup_retry_and_legacy_safety(tmp_path):
    storage = _fixture(tmp_path)
    before = storage.storage_metrics()
    preview = run_retention(storage, policy=POLICY, dry_run=True, now=NOW)
    assert preview["counts"]["rollup_days"] == 1
    assert preview["counts"]["search_results"] == 4
    assert preview["counts"]["searches"] == 2
    assert preview["counts"]["scan_runs"] == 1
    assert preview["counts"]["marketplace_items"] == 1
    assert preview["counts"]["user_item_states"] == 1
    assert storage.storage_metrics()["trace_rows"] == before["trace_rows"]
    assert storage.storage_metrics()["aggregate_rows"] == 0
    applied = run_retention(storage, policy=POLICY, dry_run=False, now=NOW)
    assert applied["counts"]["search_results"] == 4
    assert applied["counts"]["traces"] == 1
    with storage.connect() as conn:
        rollup = conn.execute("SELECT * FROM search_daily_rollups").fetchone()
        assert rollup["executions"] == 2
        assert rollup["results_returned"] == 4
        assert rollup["distinct_listings"] == 2
        assert rollup["alerts_sent"] == 2
        assert conn.execute("SELECT COUNT(*) n FROM shared_scan_results").fetchone()["n"] == 0
        assert conn.execute("SELECT COUNT(*) n FROM listing_decision_traces").fetchone()["n"] == 2
        assert conn.execute("SELECT COUNT(*) n FROM marketplace_items WHERE marketplace_item_id='legacy'").fetchone()["n"] == 1
        assert conn.execute("SELECT COUNT(*) n FROM user_item_feedback").fetchone()["n"] == 1
    retry = run_retention(storage, policy=POLICY, dry_run=False, now=NOW)
    assert retry["counts"]["rollup_days"] == 0
    assert retry["counts"]["search_results"] == 0
    assert storage.storage_metrics()["retention_rows_removed"]["search_results"] == 4


def test_retention_lease_blocks_second_owner(tmp_path):
    storage = Storage(tmp_path / "lease.sqlite3")
    assert storage.acquire_worker_lease("retention", "other", hostname="test", process_id=1,
                                        now="2026-10-03T00:00:00+00:00", expires_at="2026-10-04T00:00:00+00:00")
    with pytest.raises(RuntimeError, match="lease"):
        run_retention(storage, policy=POLICY, dry_run=False, now=NOW)


def test_interrupted_after_rollup_resumes_detail_cleanup(tmp_path):
    storage = _fixture(tmp_path)
    with storage.connect() as conn:
        assert _rollup_day(conn, "2025-01-01", "2026-10-03T00:00:00+00:00") == 1
    result = run_retention(storage, policy=POLICY, dry_run=False, now=NOW)
    assert result["counts"]["rollup_days"] == 0
    assert result["counts"]["search_results"] == 4
    with storage.connect() as conn:
        assert conn.execute("SELECT COUNT(*) n FROM search_daily_rollups").fetchone()["n"] == 1


def test_reviewed_trace_eventually_expires_but_feedback_survives(tmp_path):
    storage = _fixture(tmp_path)
    with storage.connect() as conn:
        conn.execute("UPDATE listing_decision_traces SET created_at=? WHERE marketplace_item_id=("
                     "SELECT id FROM marketplace_items WHERE marketplace_item_id='reviewed')", (OLD,))
    report = run_retention(storage, policy=POLICY, dry_run=False, now=NOW)
    assert report["counts"]["traces"] == 2
    with storage.connect() as conn:
        assert conn.execute("SELECT COUNT(*) n FROM user_item_feedback").fetchone()["n"] == 1
        assert conn.execute("SELECT COUNT(*) n FROM marketplace_items WHERE marketplace_item_id='reviewed'").fetchone()["n"] == 1


def test_metrics_size_permission_failure_is_graceful(tmp_path, monkeypatch):
    storage = Storage(tmp_path / "metrics.sqlite3")
    original = type(storage.path).stat
    def deny(path, *args, **kwargs):
        if path == storage.path:
            raise PermissionError("denied")
        return original(path, *args, **kwargs)
    monkeypatch.setattr(type(storage.path), "stat", deny)
    assert storage.storage_metrics()["database_size_bytes"] is None


def test_notification_retention_keeps_failures_and_legacy(tmp_path):
    storage = Storage(tmp_path / "notices.sqlite3")
    user = storage.create_user(email="notices@example.com", password_hash="test")
    storage.upsert_user_item(user["id"], {"item_id": "new", "title": "iPhone 16 cracked screen", "status": "risky"})
    routine = storage.create_notification_attempt(user_id=user["id"], item_id="new",
                                                  notification_type="review", sent=True)
    failure = storage.create_notification_attempt(user_id=user["id"], item_id="new",
                                                  notification_type="review", failed=True)
    legacy = storage.create_notification_attempt(user_id=user["id"], item_id="old",
                                                 notification_type="review", sent=True)
    with storage.connect() as conn:
        conn.execute("UPDATE notification_attempts SET created_at=?", (OLD,))
    result = run_retention(storage, policy=POLICY, dry_run=False, now=NOW)
    assert result["counts"]["notifications"] == 1
    with storage.connect() as conn:
        remaining = {row["id"] for row in conn.execute("SELECT id FROM notification_attempts")}
    assert remaining == {failure, legacy}
