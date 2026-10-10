from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from backend import main
from backend.auth import hash_password
from backend.config import Settings
from backend.storage import Storage


def _configure(monkeypatch, tmp_path):
    storage = Storage(tmp_path / "dashboard-fast.sqlite3")
    settings = Settings(
        sqlite_path=tmp_path / "dashboard-fast.sqlite3",
        auth_required=True,
        auth_secret_key="dashboard-fast-test-secret-long-enough",
        access_token_expire_minutes=60,
        dashboard_hot_hours=36,
    )
    monkeypatch.setattr(main, "storage", storage)
    monkeypatch.setattr(main, "settings", settings)
    monkeypatch.setattr(main, "repair_values", {})
    monkeypatch.setattr(main, "resale_research", {})
    monkeypatch.setattr(main, "scoring_rules", {})
    main._dashboard_counts_cache.clear()
    main._dashboard_freshness_cache.clear()
    main._pricing_context_cache.clear()
    return storage


def _user(storage: Storage, email: str) -> dict:
    return storage.create_user(
        email=email,
        password_hash=hash_password("dashboard-pass"),
        role="admin",
        account_status="active",
        baseline_keywords=[],
    )


def _headers(client: TestClient, email: str) -> dict[str, str]:
    response = client.post("/auth/login", json={"email": email, "password": "dashboard-pass"})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _item(item_id: str, *, when: datetime, user_status: str = "new") -> dict:
    stamp = when.astimezone(timezone.utc).isoformat()
    return {
        "item_id": item_id,
        "title": f"iPhone 15 cracked screen {item_id}",
        "model": "iPhone 15",
        "status": "candidate",
        "user_status": user_status,
        "item_origin_at": stamp,
        "found_at": stamp,
        "price": 210,
        "total_cost": 225,
        "estimated_profit": 130,
        "estimated_profit_available": True,
        "profit_mid": 130,
        "profit_high": 160,
        "resale_mid": 500,
        "resale_value": 500,
        "item_url": "https://www.ebay.com/itm/12345",
        "risk_flags": ["screen_repair_required"],
        "availability_status": "in_stock",
        "buying_option_summary": "buy_it_now",
    }


def test_dashboard_counts_are_hot_windowed_cached_and_user_scoped(monkeypatch, tmp_path):
    storage = _configure(monkeypatch, tmp_path)
    user_a = _user(storage, "a@example.test")
    user_b = _user(storage, "b@example.test")
    now = datetime.now(timezone.utc)
    for item_id in ("gem", "profitable", "review"):
        storage.upsert_user_item(int(user_a["id"]), _item(item_id, when=now))
    storage.upsert_user_item(int(user_a["id"]), _item("too-old", when=now - timedelta(hours=37)))
    storage.upsert_user_item(int(user_a["id"]), _item("ignored", when=now, user_status="ignored"))
    storage.upsert_user_item(int(user_b["id"]), _item("b-review", when=now))

    tiers = {"gem": "GEM", "profitable": "PROFITABLE", "review": "REVIEW", "b-review": "REVIEW"}

    def decorate(_user, rows):
        return [
            {**row, "alert_tier": tiers.get(row["item_id"]), "fresh_for_active_queue": True}
            for row in rows
        ]

    monkeypatch.setattr(main, "_decorate_dashboard_items", decorate)
    with TestClient(main.app) as client:
        headers_a = _headers(client, "a@example.test")
        headers_b = _headers(client, "b@example.test")
        first = client.get("/items/dashboard/counts", headers=headers_a)
        cached = client.get("/items/dashboard/counts", headers=headers_a)
        isolated = client.get("/items/dashboard/counts", headers=headers_b)

    assert first.status_code == 200
    assert first.json()["counts"]["high_quality"] == 1
    assert first.json()["counts"]["profitable"] == 1
    assert first.json()["counts"]["review"] == 1
    assert first.json()["counts"]["actionable"] == 3
    assert first.json()["performance"]["hot_hours"] == 36
    assert cached.json()["cache_hit"] is True
    assert cached.json()["computed_at"] == first.json()["computed_at"]
    assert isolated.json()["counts"]["actionable"] == 1
    assert isolated.json()["counts"]["review"] == 1


def test_empty_dashboard_returns_zero_primary_counts_and_empty_preview(monkeypatch, tmp_path):
    storage = _configure(monkeypatch, tmp_path)
    _user(storage, "empty@example.test")
    monkeypatch.setattr(main, "_decorate_dashboard_items", lambda _user, rows: rows)
    with TestClient(main.app) as client:
        headers = _headers(client, "empty@example.test")
        counts = client.get("/items/dashboard/counts", headers=headers)
        preview = client.get("/items/dashboard/preview", headers=headers)

    assert counts.status_code == 200
    assert counts.json()["counts"] == {
        "high_quality": 0,
        "profitable": 0,
        "review": 0,
        "actionable": 0,
    }
    assert preview.status_code == 200
    assert preview.json()["items"] == []
    assert preview.json()["has_more"] is False


def test_dashboard_preview_returns_small_newest_actionable_page(monkeypatch, tmp_path):
    storage = _configure(monkeypatch, tmp_path)
    user = _user(storage, "preview@example.test")
    now = datetime.now(timezone.utc)
    for index in range(4):
        storage.upsert_user_item(int(user["id"]), _item(f"item-{index}", when=now - timedelta(minutes=index)))

    monkeypatch.setattr(main, "_decorate_dashboard_items", lambda _user, rows: [
        {**row, "alert_tier": "PROFITABLE", "fresh_for_active_queue": True}
        for row in rows
    ])
    with TestClient(main.app) as client:
        headers = _headers(client, "preview@example.test")
        response = client.get("/items/dashboard/preview?limit=2", headers=headers)

    assert response.status_code == 200
    assert [item["item_id"] for item in response.json()["items"]] == ["item-0", "item-1"]
    assert response.json()["has_more"] is True
    assert response.json()["performance"]["returned_items"] == 2


def test_item_detail_query_loads_old_user_owned_listing_directly(monkeypatch, tmp_path):
    storage = _configure(monkeypatch, tmp_path)
    owner = _user(storage, "owner@example.test")
    other = _user(storage, "other@example.test")
    old = datetime.now(timezone.utc) - timedelta(hours=40)
    storage.upsert_user_item(int(owner["id"]), _item("old/phone-1", when=old))

    with TestClient(main.app) as client:
        owner_headers = _headers(client, "owner@example.test")
        other_headers = _headers(client, "other@example.test")
        response = client.get("/items/detail", params={"item_id": "old/phone-1"}, headers=owner_headers)
        isolated = client.get("/items/detail", params={"item_id": "old/phone-1"}, headers=other_headers)

    assert response.status_code == 200
    item = response.json()["item"]
    assert item["item_id"] == "old/phone-1"
    assert item["price"] == 210
    assert item["total_cost"] == 225
    assert item["estimated_profit"] == 130
    assert item["risk_flags"] == ["screen_repair_required"]
    assert item["item_url"] == "https://www.ebay.com/itm/12345"
    assert isolated.status_code == 404
