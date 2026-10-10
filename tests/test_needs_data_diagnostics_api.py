from datetime import datetime, timezone

from fastapi.testclient import TestClient
from backend import main
from backend.auth import hash_password
from backend.config import Settings
from backend.storage import Storage
from test_needs_data_diagnostics import _base

def test_auth_scoping_queue_parity_and_no_mutation(monkeypatch, tmp_path):
    store = Storage(tmp_path / "needs-data-diagnostics.sqlite3")
    settings = Settings(
        sqlite_path=tmp_path / "needs-data-diagnostics.sqlite3",
        auth_required=True, auth_secret_key="needs-data-test-secret-long-enough",
        access_token_expire_minutes=60, dashboard_hot_hours=36,
    )
    monkeypatch.setattr(main, "storage", store)
    monkeypatch.setattr(main, "settings", settings)
    monkeypatch.setattr(main, "repair_values", {})
    monkeypatch.setattr(main, "resale_research", {})
    monkeypatch.setattr(main, "scoring_rules", {})
    main._dashboard_freshness_cache.clear()
    main._pricing_context_cache.clear()
    a = store.create_user(email="a-nd@example.test", password_hash=hash_password("pw-for-tests"), role="admin", account_status="active", baseline_keywords=[])
    b = store.create_user(email="b-nd@example.test", password_hash=hash_password("pw-for-tests"), role="admin", account_status="active", baseline_keywords=[])
    now = datetime.now(timezone.utc).isoformat()
    for who, item_id in ((a, "a-1"), (b, "b-1")):
        store.upsert_user_item(int(who["id"]), {
            "item_id": item_id, "title": "iPhone 14 Pro Max unlocked 128GB broken camera glass",
            "found_at": now, "item_origin_at": now, "price": 100, "total_cost": 100,
            "status": "risky", "model": "iPhone 14 Pro Max", "user_status": "new",
        })
    # Force a single need-data record for each user so the test specifically
    # exercises endpoint isolation, not the scorer/alert thresholds.
    monkeypatch.setattr(main, "_decorate_dashboard_items", lambda user, rows: [
        {**row, **_base(str(row["item_id"])), "fresh_for_active_queue": True, "status": "risky", "user_status": "new"}
        for row in rows
    ])
    with TestClient(main.app) as client:
        unauthorized = client.get("/items/dashboard/needs-data/diagnostics")
        login_a = client.post("/auth/login", json={"email": "a-nd@example.test", "password": "pw-for-tests"})
        login_b = client.post("/auth/login", json={"email": "b-nd@example.test", "password": "pw-for-tests"})
        assert login_a.status_code == login_b.status_code == 200
        headers_a = {"Authorization": f"Bearer {login_a.json()['access_token']}"}
        headers_b = {"Authorization": f"Bearer {login_b.json()['access_token']}"}
        response_a = client.get("/items/dashboard/needs-data/diagnostics", headers=headers_a)
        response_b = client.get("/items/dashboard/needs-data/diagnostics", headers=headers_b)
        queue_a = client.get("/items/dashboard?queue=needs_data", headers=headers_a)
    assert unauthorized.status_code in (401, 403)
    assert response_a.status_code == response_b.status_code == 200
    assert response_a.json()["queue_total_within_source"] == queue_a.json()["total"] == 1
    assert [r["item_id"] for r in response_a.json()["records"]] == ["a-1"]
    assert [r["item_id"] for r in response_b.json()["records"]] == ["b-1"]
    assert response_a.json()["read_only"] is True
    assert store.get_user_item(int(a["id"]), "a-1")["status"] == "risky"
