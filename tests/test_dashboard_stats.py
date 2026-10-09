from datetime import datetime, timedelta, timezone

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from backend.storage import Storage, _convert_qmark_sql


def _user(storage: Storage, email: str) -> int:
    return int(storage.create_user(email=email, password_hash="test-hash")["id"])


def _item(item_id: str, *, created_at: datetime, status: str = "candidate") -> dict:
    timestamp = created_at.astimezone(timezone.utc).isoformat()
    return {
        "item_id": item_id,
        "title": f"Apple iPhone listing {item_id}",
        "status": status,
        "score": 70,
        "item_origin_at": timestamp,
        "found_at": timestamp,
        "updated_at": timestamp,
    }


def test_stats_for_user_empty_dashboard_with_and_without_cutoff(tmp_path):
    storage = Storage(tmp_path / "empty-dashboard.sqlite3")
    user_id = _user(storage, "empty@example.test")

    without_cutoff = storage.stats_for_user(user_id, source_max_age_hours=None)
    with_cutoff = storage.stats_for_user(user_id, source_max_age_hours=36)

    for stats in (without_cutoff, with_cutoff):
        assert stats["total"] == 0
        assert stats["by_status"] == {}
        assert stats["by_user_status"] == {}
        assert stats["found_today"] == 0


def test_stats_for_user_cutoff_counts_recent_items_and_isolates_users(tmp_path):
    storage = Storage(tmp_path / "populated-dashboard.sqlite3")
    owner_id = _user(storage, "owner@example.test")
    other_id = _user(storage, "other@example.test")
    now = datetime.now(timezone.utc)

    storage.upsert_user_item(owner_id, _item("recent-candidate", created_at=now))
    storage.upsert_user_item(owner_id, _item("recent-risky", created_at=now - timedelta(hours=3), status="risky"))
    storage.upsert_user_item(owner_id, _item("outside-window", created_at=now - timedelta(hours=37)))
    storage.upsert_user_item(other_id, _item("other-user", created_at=now))

    hot_stats = storage.stats_for_user(owner_id, source_max_age_hours=36)
    all_stats = storage.stats_for_user(owner_id, source_max_age_hours=None)
    other_stats = storage.stats_for_user(other_id, source_max_age_hours=36)

    assert hot_stats["total"] == 2
    assert hot_stats["by_status"] == {"candidate": 1, "risky": 1}
    assert hot_stats["by_user_status"] == {"new": 2}
    assert all_stats["total"] == 3
    assert all_stats["by_status"] == {"candidate": 2, "risky": 1}
    assert other_stats["total"] == 1
    assert other_stats["by_status"] == {"candidate": 1}


def test_stats_for_user_null_source_age_includes_all_items(tmp_path):
    storage = Storage(tmp_path / "unbounded-dashboard.sqlite3")
    user_id = _user(storage, "all-items@example.test")
    old_timestamp = datetime.now(timezone.utc) - timedelta(days=90)
    storage.upsert_user_item(user_id, _item("old-item", created_at=old_timestamp))

    stats = storage.stats_for_user(user_id, source_max_age_hours=None)

    assert stats["total"] == 1
    assert stats["by_status"] == {"candidate": 1}


def test_dashboard_cutoff_compiles_as_typed_postgresql_parameter():
    statement, parameters = _convert_qmark_sql(
        "SELECT 1 WHERE COALESCE(item_origin_at, found_at) >= CAST(? AS TEXT)",
        ["2026-10-09T00:00:00+00:00"],
    )
    compiled = sa.text(statement).compile(dialect=postgresql.dialect())

    assert "CAST(%(p0)s AS TEXT)" in str(compiled)
    assert "IS NULL" not in str(compiled)
    assert parameters == {"p0": "2026-10-09T00:00:00+00:00"}
