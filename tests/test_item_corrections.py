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


def _resale_research_payload():
    return {
        "iPhone 14": {
            "resale_by_storage": {
                "128GB": {
                    "good": {"low": 430, "mid": 520, "high": 590},
                    "mint": {"low": 470, "mid": 560, "high": 620},
                    "confidence": "research",
                    "sample_size": 7,
                },
                "256GB": {
                    "good": {"low": 500, "mid": 610, "high": 680},
                    "mint": {"low": 550, "mid": 660, "high": 730},
                    "confidence": "research",
                    "sample_size": 6,
                },
            }
        }
    }


def _configure_app(monkeypatch, tmp_path, **settings_overrides):
    repair_path = tmp_path / "repair_values.json"
    resale_path = tmp_path / "resale_research.json"
    repair_path.write_text(json.dumps(_repair_values_payload()), encoding="utf-8")
    resale_path.write_text(json.dumps(_resale_research_payload()), encoding="utf-8")
    db_path = Path(":memory:")
    storage = Storage(db_path)
    auth_required = settings_overrides.pop("auth_required", True)
    settings = Settings(
        sqlite_path=db_path,
        repair_values_path=repair_path,
        resale_research_path=resale_path,
        auth_required=auth_required,
        auth_secret_key="test-auth-secret-value-is-long-enough",
        access_token_expire_minutes=60,
        ebay_client_id=settings_overrides.pop("ebay_client_id", "test-ebay-client-id"),
        ebay_client_secret=settings_overrides.pop("ebay_client_secret", "test-ebay-client-secret"),
        search_keywords=["iphone shared"],
        **settings_overrides,
    )
    monkeypatch.setattr(main, "storage", storage)
    monkeypatch.setattr(main, "settings", settings)
    monkeypatch.setattr(main, "repair_values", _repair_values_payload())
    monkeypatch.setattr(main, "resale_research", _resale_research_payload())
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


def _listing(item_id: str = "correctable-1", *, title: str = "Apple iPhone 14 128GB Unlocked Cracked Screen Powers On Clean IMEI") -> dict[str, object]:
    now = datetime.now(timezone.utc).isoformat()
    return {
        "item_id": item_id,
        "title": title,
        "condition": "Used",
        "price": 160,
        "shipping": 20,
        "total_cost": 180,
        "found_at": now,
        "item_origin_at": now,
        "seller_username": "seller-one",
        "availability_status": "in_stock",
        "buying_option_summary": "buy_it_now",
        "raw_json": {"id": item_id, "title": title},
    }


def _install_scan_fakes(monkeypatch, listings):
    class FakeEbayClient:
        def __init__(self, settings):
            self.settings = settings

        async def search(self, keywords, limit):
            del keywords, limit
            return [dict(item) for item in listings]

    monkeypatch.setattr(main, "EbayClient", FakeEbayClient)


def _shared_scan(client: TestClient, admin_token: str):
    response = client.post("/scan/run", headers=_auth_headers(admin_token), json={"notify": False})
    assert response.status_code == 200, response.text


def test_user_can_create_item_correction_and_dashboard_payload_includes_it(monkeypatch, tmp_path):
    storage, settings, repair_path = _configure_app(monkeypatch, tmp_path, auth_required=True)
    user = _create_user(storage, settings, "correct@example.com", "correct-pass")
    admin = _create_user(storage, settings, "admin@example.com", "admin-pass", role="admin")
    _install_scan_fakes(monkeypatch, [_listing()])
    original_repair_json = repair_path.read_text(encoding="utf-8")

    with TestClient(main.app) as client:
        admin_token = _login(client, "admin@example.com", "admin-pass")
        user_token = _login(client, "correct@example.com", "correct-pass")
        _shared_scan(client, admin_token)
        response = client.put(
            "/items/correctable-1/correction",
            headers=_auth_headers(user_token),
            json={
                "corrected_model": "iPhone 14",
                "corrected_storage_capacity": "256GB",
                "corrected_issue_type": "screen_display_issue",
                "corrected_part_cost": 142,
                "note": "Manual correction",
            },
        )
        fetched = client.get("/items/correctable-1/correction", headers=_auth_headers(user_token))
        items = client.get("/items", headers=_auth_headers(user_token))

    assert response.status_code == 200
    assert fetched.status_code == 200
    assert repair_path.read_text(encoding="utf-8") == original_repair_json
    payload = response.json()
    assert payload["correction"]["corrected_storage_capacity"] == "256GB"
    assert payload["item"]["user_item_correction"]["corrected_part_cost"] == 142
    assert payload["item"]["raw_detected_model"] == "iPhone 14"
    assert payload["item"]["effective_issue_type"] == "screen_display_issue"
    assert items.json()[0]["user_item_correction"]["note"] == "Manual correction"


def test_correction_affects_only_that_users_score_and_part_cost(monkeypatch, tmp_path):
    storage, settings, _repair_path = _configure_app(monkeypatch, tmp_path, auth_required=True)
    user_a = _create_user(storage, settings, "a@example.com", "a-pass")
    user_b = _create_user(storage, settings, "b@example.com", "b-pass")
    _create_user(storage, settings, "admin@example.com", "admin-pass", role="admin")
    _install_scan_fakes(monkeypatch, [_listing("shared-corrected")])

    with TestClient(main.app) as client:
        admin_token = _login(client, "admin@example.com", "admin-pass")
        token_a = _login(client, "a@example.com", "a-pass")
        _shared_scan(client, admin_token)
        update = client.put(
            "/items/shared-corrected/correction",
            headers=_auth_headers(token_a),
            json={"corrected_part_cost": 155, "corrected_issue_type": "cracked_screen", "note": "One-off higher screen"},
        )

    item_a = storage.get_user_item(int(user_a["id"]), "shared-corrected")
    item_b = storage.get_user_item(int(user_b["id"]), "shared-corrected")
    assert update.status_code == 200
    assert item_a["estimated_parts_cost"] == 155
    assert item_b["estimated_parts_cost"] == 80
    assert item_a["profit_mid"] < item_b["profit_mid"]


def test_corrected_storage_changes_resale_selection(monkeypatch, tmp_path):
    storage, settings, _repair_path = _configure_app(monkeypatch, tmp_path, auth_required=True)
    _create_user(storage, settings, "storage@example.com", "storage-pass")
    _create_user(storage, settings, "admin@example.com", "admin-pass", role="admin")
    _install_scan_fakes(monkeypatch, [_listing("storage-correct", title="Apple iPhone 14 Unlocked Cracked Screen Powers On Clean IMEI")])

    with TestClient(main.app) as client:
        admin_token = _login(client, "admin@example.com", "admin-pass")
        user_token = _login(client, "storage@example.com", "storage-pass")
        _shared_scan(client, admin_token)
        before = client.get("/items", headers=_auth_headers(user_token))
        update = client.put(
            "/items/storage-correct/correction",
            headers=_auth_headers(user_token),
            json={"corrected_storage_capacity": "256GB", "note": "Storage confirmed from photos"},
        )

    before_item = before.json()[0]
    after_item = update.json()["item"]
    assert before_item["storage_capacity"] in (None, "", "unknown")
    assert after_item["storage_capacity"] == "256GB"
    assert after_item["resale_storage_used"] == "256GB"
    assert after_item["resale_mid"] == 610


def test_delete_correction_falls_back_to_defaults(monkeypatch, tmp_path):
    storage, settings, _repair_path = _configure_app(monkeypatch, tmp_path, auth_required=True)
    _create_user(storage, settings, "delete@example.com", "delete-pass")
    _create_user(storage, settings, "admin@example.com", "admin-pass", role="admin")
    _install_scan_fakes(monkeypatch, [_listing("delete-correction")])

    with TestClient(main.app) as client:
        admin_token = _login(client, "admin@example.com", "admin-pass")
        user_token = _login(client, "delete@example.com", "delete-pass")
        _shared_scan(client, admin_token)
        created = client.put(
            "/items/delete-correction/correction",
            headers=_auth_headers(user_token),
            json={"corrected_part_cost": 140, "corrected_issue_type": "cracked_screen", "note": "Temporary"},
        )
        deleted = client.delete("/items/delete-correction/correction", headers=_auth_headers(user_token))

    assert created.status_code == 200
    assert created.json()["item"]["effective_part_source"] == "item_correction"
    assert deleted.status_code == 200
    assert deleted.json()["item"]["effective_part_cost"] == 80
    assert deleted.json()["item"]["effective_part_source"] == "global_default"
    assert deleted.json()["item"]["user_item_correction"] == {}


def test_correction_does_not_mutate_marketplace_item_raw_data(monkeypatch, tmp_path):
    storage, settings, _repair_path = _configure_app(monkeypatch, tmp_path, auth_required=True)
    _create_user(storage, settings, "raw@example.com", "raw-pass")
    _create_user(storage, settings, "admin@example.com", "admin-pass", role="admin")
    _install_scan_fakes(monkeypatch, [_listing("raw-unchanged")])

    with TestClient(main.app) as client:
        admin_token = _login(client, "admin@example.com", "admin-pass")
        user_token = _login(client, "raw@example.com", "raw-pass")
        _shared_scan(client, admin_token)
        before = storage.get_marketplace_item("raw-unchanged")
        response = client.put(
            "/items/raw-unchanged/correction",
            headers=_auth_headers(user_token),
            json={"corrected_model": "iPhone 14", "corrected_part_cost": 150, "note": "Private correction"},
        )
        after = storage.get_marketplace_item("raw-unchanged")

    assert response.status_code == 200
    assert before is not None and after is not None
    assert before["title"] == after["title"]
    assert before["raw_json"] == after["raw_json"]
    assert before["seller_username"] == after["seller_username"]

