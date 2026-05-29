import json
from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from backend import main
from backend.auth import hash_password
from backend.config import Settings
from backend.storage import Storage


class _TestAsyncLock:
    def __init__(self):
        self._locked = False

    def locked(self) -> bool:
        return self._locked

    async def __aenter__(self):
        self._locked = True
        return self

    async def __aexit__(self, exc_type, exc, tb):
        self._locked = False
        return False


def _repair_values_payload():
    return {
        "default": {
            "resale_value": 250,
            "risk_buffer": 40,
            "min_profit": 75,
            "manual_review_allowed": True,
            "parts_pricing_status": "verified_screenshot",
            "parts_pricing_note": "Default baseline",
            "parts": {
                "screen_budget": 60,
                "screen_safe": 80,
                "screen_premium": 110,
                "battery": 20,
                "back_glass": 30,
                "camera_lens": 10,
                "charging_port": 18,
            },
        },
        "iPhone 14": {
            "risk_buffer": 40,
            "min_profit": 75,
            "manual_review_allowed": True,
            "parts_pricing_status": "verified_screenshot",
            "parts_pricing_note": "Model baseline",
            "parts": {
                "screen_budget": 55,
                "screen_safe": 80,
                "screen_premium": 125,
                "battery": 18,
                "back_glass": 25,
                "camera_lens": 8,
                "charging_port": 16,
            },
            "resale": {
                "low": 420,
                "mid": 520,
                "high": 580,
                "confidence": "baseline",
                "sample_size": 5,
            },
        },
    }


def _configure_app(monkeypatch, tmp_path, **settings_overrides):
    repair_path = tmp_path / "repair_values.json"
    repair_path.write_text(json.dumps(_repair_values_payload()), encoding="utf-8")
    db_path = Path(":memory:")
    storage = Storage(db_path)
    auth_required = settings_overrides.pop("auth_required", True)
    ebay_client_id = settings_overrides.pop("ebay_client_id", "test-ebay-client-id")
    ebay_client_secret = settings_overrides.pop("ebay_client_secret", "test-ebay-client-secret")
    settings = Settings(
        sqlite_path=db_path,
        repair_values_path=repair_path,
        resale_research_path=tmp_path / "resale_research.json",
        auth_required=auth_required,
        auth_secret_key="test-auth-secret-value-is-long-enough",
        access_token_expire_minutes=60,
        ebay_client_id=ebay_client_id,
        ebay_client_secret=ebay_client_secret,
        search_keywords=["iphone shared"],
        **settings_overrides,
    )
    monkeypatch.setattr(main, "storage", storage)
    monkeypatch.setattr(main, "settings", settings)
    monkeypatch.setattr(main, "repair_values", _repair_values_payload())
    monkeypatch.setattr(main, "resale_research", {})
    monkeypatch.setattr(main, "scoring_rules", {})
    monkeypatch.setattr(main, "_scan_lock", _TestAsyncLock())
    monkeypatch.setattr(main, "_should_fetch_selective_detail", lambda listing, result, current_settings: False)
    return storage, settings, repair_path


def _login(client: TestClient, email: str, password: str) -> str:
    response = client.post("/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def _auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _create_user(storage: Storage, settings: Settings, email: str, password: str, *, role: str = "user") -> dict:
    return storage.create_user(
        email=email,
        password_hash=hash_password(password),
        role=role,
        account_status="active",
        baseline_keywords=settings.search_keywords,
    )


def _listing(item_id: str = "shared-override-1") -> dict[str, object]:
    now = datetime.now(timezone.utc).isoformat()
    return {
        "item_id": item_id,
        "title": "Apple iPhone 14 128GB Unlocked Cracked Screen Powers On Clean IMEI",
        "condition": "Used",
        "price": 160,
        "shipping": 20,
        "total_cost": 180,
        "found_at": now,
        "item_origin_at": now,
        "seller_username": "seller-one",
        "availability_status": "in_stock",
        "buying_option_summary": "buy_it_now",
        "raw_json": {},
    }


def _install_scan_fakes(monkeypatch, listings):
    class FakeEbayClient:
        def __init__(self, settings):
            self.settings = settings

        async def search(self, keywords, limit):
            del keywords, limit
            return [dict(item) for item in listings]

    monkeypatch.setattr(main, "EbayClient", FakeEbayClient)


def test_user_repair_override_changes_only_that_users_scoring_in_shared_scan(monkeypatch, tmp_path):
    storage, settings, _repair_path = _configure_app(
        monkeypatch,
        tmp_path,
        auth_required=True,
        ebay_client_id="id",
        ebay_client_secret="secret",
    )
    user_a = _create_user(storage, settings, "a@example.com", "a-pass")
    user_b = _create_user(storage, settings, "b@example.com", "b-pass")
    storage.upsert_user_repair_value_override(
        int(user_a["id"]),
        model="iPhone 14",
        part="screen_safe",
        cost=130,
        source="user_override",
        note="My higher screen cost",
    )
    _install_scan_fakes(monkeypatch, [_listing("shared-repair")])

    with TestClient(main.app) as client:
        admin = _create_user(storage, settings, "admin@example.com", "admin-pass", role="admin")
        token = _login(client, "admin@example.com", "admin-pass")
        response = client.post("/scan/run", headers=_auth_headers(token), json={"notify": False})

    item_a = storage.get_user_item(int(user_a["id"]), "shared-repair")
    item_b = storage.get_user_item(int(user_b["id"]), "shared-repair")
    assert response.status_code == 200
    assert item_a["estimated_parts_cost"] == 130
    assert item_b["estimated_parts_cost"] == 80
    assert item_a["profit_mid"] < item_b["profit_mid"]


def test_user_resale_override_changes_only_that_users_scoring(monkeypatch, tmp_path):
    storage, settings, _repair_path = _configure_app(
        monkeypatch,
        tmp_path,
        auth_required=True,
        ebay_client_id="id",
        ebay_client_secret="secret",
    )
    user_a = _create_user(storage, settings, "resale-a@example.com", "a-pass")
    user_b = _create_user(storage, settings, "resale-b@example.com", "b-pass")
    storage.upsert_user_resale_research_override(
        int(user_a["id"]),
        model="iPhone 14",
        storage_capacity="128GB",
        condition="good",
        low=500,
        mid=650,
        high=700,
        confidence="user",
        source="user_override",
        note="Optimistic local comps",
    )
    _install_scan_fakes(monkeypatch, [_listing("shared-resale")])

    admin = _create_user(storage, settings, "admin2@example.com", "admin-pass", role="admin")
    with TestClient(main.app) as client:
        token = _login(client, "admin2@example.com", "admin-pass")
        response = client.post("/scan/run", headers=_auth_headers(token), json={"notify": False})

    item_a = storage.get_user_item(int(user_a["id"]), "shared-resale")
    item_b = storage.get_user_item(int(user_b["id"]), "shared-resale")
    assert response.status_code == 200
    assert item_a["resale_mid"] == 650
    assert item_b["resale_mid"] == 520
    assert item_a["profit_mid"] > item_b["profit_mid"]


def test_manual_part_update_creates_user_override_and_does_not_mutate_global_file(monkeypatch, tmp_path):
    storage, settings, repair_path = _configure_app(monkeypatch, tmp_path, auth_required=True)
    user = _create_user(storage, settings, "override@example.com", "override-pass")
    _install_scan_fakes(monkeypatch, [_listing("manual-override")])
    original_json = repair_path.read_text(encoding="utf-8")

    with TestClient(main.app) as client:
        token = _login(client, "override@example.com", "override-pass")
        first_scan = client.post("/scan/run", headers=_auth_headers(token), json={"notify": False})
        update = client.post(
            "/repair-values/iPhone 14/parts",
            headers=_auth_headers(token),
            json={"part": "screen_safe", "cost": 135, "item_id": "manual-override", "note": "private override"},
        )
        items = client.get("/items", headers=_auth_headers(token))

    assert first_scan.status_code == 200
    assert update.status_code == 200
    assert repair_path.read_text(encoding="utf-8") == original_json
    assert update.json()["repair_override"]["cost"] == 135
    assert items.json()[0]["effective_part_cost"] == 135
    assert items.json()[0]["effective_part_source"] == "user_override"
    assert items.json()[0]["baseline_part_cost"] == 80
    assert items.json()[0]["user_override_part_cost"] == 135


def test_admin_global_baseline_update_is_separate_and_mutates_file(monkeypatch, tmp_path):
    storage, settings, repair_path = _configure_app(monkeypatch, tmp_path, auth_required=True)
    _create_user(storage, settings, "admin@example.com", "admin-pass", role="admin")

    with TestClient(main.app) as client:
        token = _login(client, "admin@example.com", "admin-pass")
        response = client.post(
            "/admin/repair-values/iPhone 14/parts",
            headers=_auth_headers(token),
            json={"part": "screen_safe", "cost": 145, "note": "global baseline update"},
        )

    parsed = json.loads(repair_path.read_text(encoding="utf-8"))
    assert response.status_code == 200
    assert parsed["iPhone 14"]["parts"]["screen_safe"] == 145
    assert response.json()["scope"] == "global_baseline"


def test_auth_not_required_uses_fallback_user_override_without_mutating_global_file(monkeypatch, tmp_path):
    storage, settings, repair_path = _configure_app(monkeypatch, tmp_path, auth_required=False)
    _install_scan_fakes(monkeypatch, [_listing("local-override")])
    original_json = repair_path.read_text(encoding="utf-8")

    with TestClient(main.app) as client:
        scan = client.post("/scan/run", json={"notify": False})
        update = client.post(
            "/repair-values/iPhone 14/parts",
            json={"part": "screen_safe", "cost": 120, "item_id": "local-override", "note": "local private override"},
        )
        overrides = client.get("/settings/repair-overrides")
        items = client.get("/items")

    assert scan.status_code == 200
    assert update.status_code == 200
    assert repair_path.read_text(encoding="utf-8") == original_json
    assert overrides.json()["repair_overrides"][0]["cost"] == 120
    assert items.json()[0]["effective_part_cost"] == 120
    assert items.json()[0]["effective_part_source"] == "user_override"


def test_delete_repair_override_falls_back_to_global_baseline(monkeypatch, tmp_path):
    storage, settings, _repair_path = _configure_app(monkeypatch, tmp_path, auth_required=True)
    user = _create_user(storage, settings, "delete@example.com", "delete-pass")
    _install_scan_fakes(monkeypatch, [_listing("delete-override")])

    with TestClient(main.app) as client:
        token = _login(client, "delete@example.com", "delete-pass")
        client.post("/scan/run", headers=_auth_headers(token), json={"notify": False})
        create = client.post(
            "/settings/repair-overrides",
            headers=_auth_headers(token),
            json={"model": "iPhone 14", "part": "screen_safe", "cost": 140, "source": "user_override", "note": "temp"},
        )
        override_id = create.json()["repair_override"]["id"]
        client.post("/scan/run", headers=_auth_headers(token), json={"notify": False})
        before_delete = client.get("/items", headers=_auth_headers(token))
        deleted = client.delete(f"/settings/repair-overrides/{override_id}", headers=_auth_headers(token))
        client.post("/scan/run", headers=_auth_headers(token), json={"notify": False})
        after_delete = client.get("/items", headers=_auth_headers(token))

    assert before_delete.json()[0]["effective_part_cost"] == 140
    assert before_delete.json()[0]["effective_part_source"] == "user_override"
    assert deleted.status_code == 200
    assert after_delete.json()[0]["effective_part_cost"] == 80
    assert after_delete.json()[0]["effective_part_source"] == "global_default"
    assert after_delete.json()[0]["user_override_part_cost"] is None


def test_settings_resale_override_endpoints_round_trip(monkeypatch, tmp_path):
    storage, settings, _repair_path = _configure_app(monkeypatch, tmp_path, auth_required=True)
    _create_user(storage, settings, "settings@example.com", "settings-pass")

    with TestClient(main.app) as client:
        token = _login(client, "settings@example.com", "settings-pass")
        created = client.post(
            "/settings/resale-overrides",
            headers=_auth_headers(token),
            json={
                "model": "iPhone 14",
                "storage_capacity": "128GB",
                "condition": "good",
                "low": 510,
                "mid": 610,
                "high": 690,
                "confidence": "manual",
                "source": "user_override",
                "note": "manual local comps",
            },
        )
        listed = client.get("/settings/resale-overrides", headers=_auth_headers(token))
        override_id = created.json()["resale_override"]["id"]
        deleted = client.delete(f"/settings/resale-overrides/{override_id}", headers=_auth_headers(token))
        listed_after = client.get("/settings/resale-overrides", headers=_auth_headers(token))

    assert created.status_code == 200
    assert listed.json()["resale_overrides"][0]["mid"] == 610
    assert deleted.status_code == 200
    assert listed_after.json()["resale_overrides"] == []
