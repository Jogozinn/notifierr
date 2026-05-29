import asyncio
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


def _configure_app(monkeypatch, tmp_path, **settings_overrides):
    del tmp_path
    db_path = Path(":memory:")
    storage = Storage(db_path)
    auth_required = settings_overrides.pop("auth_required", True)
    search_keywords = settings_overrides.pop("search_keywords", ["iphone shared"])
    settings = Settings(
        sqlite_path=db_path,
        auth_required=auth_required,
        auth_secret_key="test-auth-secret-value-is-long-enough",
        access_token_expire_minutes=60,
        search_keywords=search_keywords,
        **settings_overrides,
    )
    monkeypatch.setattr(main, "storage", storage)
    monkeypatch.setattr(main, "settings", settings)
    monkeypatch.setattr(main, "repair_values", {})
    monkeypatch.setattr(main, "resale_research", {})
    monkeypatch.setattr(main, "scoring_rules", {})
    monkeypatch.setattr(main, "_scan_lock", _TestAsyncLock())
    monkeypatch.setattr(main, "_should_fetch_selective_detail", lambda listing, result, current_settings: False)
    return storage, settings


def _login(client: TestClient, email: str, password: str) -> str:
    response = client.post("/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def _auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _create_user(storage: Storage, settings: Settings, email: str, password: str) -> dict:
    return storage.create_user(
        email=email,
        password_hash=hash_password(password),
        role="user",
        account_status="active",
        baseline_keywords=settings.search_keywords,
    )


def _item(item_id: str, *, title: str = "Apple iPhone 14 128GB Unlocked Cracked Screen", seller_username: str = "seller-one") -> dict[str, object]:
    now = datetime.now(timezone.utc).isoformat()
    return {
        "item_id": item_id,
        "title": title,
        "price": 160,
        "shipping": 20,
        "total_cost": 180,
        "condition": "Used",
        "seller_username": seller_username,
        "item_origin_at": now,
        "found_at": now,
        "availability_status": "in_stock",
        "buying_option_summary": "buy_it_now",
        "status": "candidate",
        "alert_eligible": True,
        "whole_phone_confidence_passed": True,
        "has_repair_issue": True,
        "estimated_profit_available": True,
        "estimated_parts_cost_available": True,
        "estimated_profit": 140,
        "profit_mid": 140,
        "resale_mid": 520,
        "resale_value": 520,
        "model": "iPhone 14",
    }


class FakeScoreResult:
    def __init__(self):
        self.score = 82.0
        self.status = "candidate"
        self.model = "iPhone 14"
        self.estimated_profit = 140.0
        self.resale_value = 520.0
        self.estimated_parts_cost = 90.0
        self.risk_buffer = 40.0
        self.resale_low = 480.0
        self.resale_mid = 520.0
        self.resale_high = 560.0
        self.profit_low = 100.0
        self.profit_mid = 140.0
        self.profit_high = 180.0
        self.resale_confidence = "high"
        self.resale_sample_size = 10
        self.resale_note = ""
        self.resale_source = "storage_specific"
        self.resale_market_source = "resale_research"
        self.resale_condition_used = "good"
        self.resale_storage_used = "128GB"
        self.storage_resale_warning = ""
        self.mint_resale_low = 530.0
        self.mint_resale_mid = 550.0
        self.mint_resale_high = 580.0
        self.mint_profit_low = 110.0
        self.mint_profit_mid = 150.0
        self.mint_profit_high = 190.0
        self.storage_capacity = "128GB"
        self.storage_confidence = "high"
        self.storage_source = "title"
        self.parts_pricing_status = "verified_screenshot"
        self.parts_pricing_note = ""
        self.parts_pricing_label = "Verified parts"
        self.pricing_warning = ""
        self.estimated_profit_available = True
        self.estimated_parts_cost_available = True
        self.manual_review_allowed = False
        self.whole_phone_confidence_passed = True
        self.whole_phone_score = 100.0
        self.has_repair_issue = True
        self.manual_review_needed = False
        self.manual_review_reason = ""
        self.alert_eligible = True
        self.listing_classification_flags = []
        self.hard_reject_flags = []
        self.positive_flags = ["cracked_screen", "powers_on", "clean_imei"]
        self.risk_flags = []

    def as_item_fields(self) -> dict[str, object]:
        return {
            "score": self.score,
            "status": self.status,
            "model": self.model,
            "estimated_profit": self.estimated_profit,
            "resale_value": self.resale_value,
            "resale_low": self.resale_low,
            "resale_mid": self.resale_mid,
            "resale_high": self.resale_high,
            "profit_low": self.profit_low,
            "profit_mid": self.profit_mid,
            "profit_high": self.profit_high,
            "resale_confidence": self.resale_confidence,
            "resale_sample_size": self.resale_sample_size,
            "resale_note": self.resale_note,
            "resale_source": self.resale_source,
            "resale_market_source": self.resale_market_source,
            "resale_condition_used": self.resale_condition_used,
            "resale_storage_used": self.resale_storage_used,
            "storage_resale_warning": self.storage_resale_warning,
            "mint_resale_low": self.mint_resale_low,
            "mint_resale_mid": self.mint_resale_mid,
            "mint_resale_high": self.mint_resale_high,
            "mint_profit_low": self.mint_profit_low,
            "mint_profit_mid": self.mint_profit_mid,
            "mint_profit_high": self.mint_profit_high,
            "storage_capacity": self.storage_capacity,
            "storage_confidence": self.storage_confidence,
            "storage_source": self.storage_source,
            "estimated_parts_cost": self.estimated_parts_cost,
            "risk_buffer": self.risk_buffer,
            "parts_pricing_status": self.parts_pricing_status,
            "parts_pricing_note": self.parts_pricing_note,
            "parts_pricing_label": self.parts_pricing_label,
            "pricing_warning": self.pricing_warning,
            "estimated_profit_available": self.estimated_profit_available,
            "estimated_parts_cost_available": self.estimated_parts_cost_available,
            "manual_review_allowed": self.manual_review_allowed,
            "whole_phone_confidence_passed": self.whole_phone_confidence_passed,
            "whole_phone_score": self.whole_phone_score,
            "has_repair_issue": self.has_repair_issue,
            "manual_review_needed": self.manual_review_needed,
            "manual_review_reason": self.manual_review_reason,
            "alert_eligible": self.alert_eligible,
            "listing_classification_flags": self.listing_classification_flags,
            "hard_reject_flags": self.hard_reject_flags,
            "positive_flags": self.positive_flags,
            "risk_flags": self.risk_flags,
        }


def _install_scan_fakes(monkeypatch, listings):
    class FakeEbayClient:
        def __init__(self, settings):
            self.settings = settings

        async def search(self, keywords, limit):
            del keywords, limit
            return [dict(item) for item in listings]

    def fake_score_listing(*args, **kwargs):
        del args, kwargs
        return FakeScoreResult()

    monkeypatch.setattr(main, "EbayClient", FakeEbayClient)
    monkeypatch.setattr(main, "score_listing", fake_score_listing)


def test_user_a_ignoring_seller_does_not_affect_user_b_and_is_excluded_from_active_queue(monkeypatch, tmp_path):
    storage, settings = _configure_app(monkeypatch, tmp_path, auth_required=True)
    user_a = _create_user(storage, settings, "a@example.com", "a-pass")
    user_b = _create_user(storage, settings, "b@example.com", "b-pass")
    item = {**_item("seller-shared"), **FakeScoreResult().as_item_fields()}
    storage.upsert_user_item(int(user_a["id"]), item)
    storage.upsert_user_item(int(user_b["id"]), item)

    with TestClient(main.app) as client:
        token_a = _login(client, "a@example.com", "a-pass")
        token_b = _login(client, "b@example.com", "b-pass")
        ignored = client.post(
            "/items/seller-shared/ignore-seller",
            headers=_auth_headers(token_a),
            json={"reason": "Avoid this seller"},
        )
        ignored_sellers_a = client.get("/ignored-sellers", headers=_auth_headers(token_a))
        ignored_sellers_b = client.get("/ignored-sellers", headers=_auth_headers(token_b))
        items_a = client.get("/items", headers=_auth_headers(token_a))
        items_a_ignored = client.get("/items?include_ignored=true", headers=_auth_headers(token_a))
        items_b = client.get("/items", headers=_auth_headers(token_b))

    assert ignored.status_code == 200
    assert ignored_sellers_a.json()[0]["seller_username"] == "seller-one"
    assert ignored_sellers_b.json() == []
    assert items_a.json() == []
    assert items_a_ignored.json()[0]["user_status"] == "ignored"
    assert items_b.json()[0]["user_status"] == "new"


def test_user_a_ignoring_keyword_does_not_affect_user_b_and_keyword_api_is_scoped(monkeypatch, tmp_path):
    storage, settings = _configure_app(monkeypatch, tmp_path, auth_required=True)
    user_a = _create_user(storage, settings, "kw-a@example.com", "a-pass")
    user_b = _create_user(storage, settings, "kw-b@example.com", "b-pass")
    keyword_item = {
        **_item("keyword-shared", title="Apple iPhone 14 screen digitizer cracked"),
        **FakeScoreResult().as_item_fields(),
    }
    storage.upsert_user_item(int(user_a["id"]), keyword_item)
    storage.upsert_user_item(int(user_b["id"]), keyword_item)

    with TestClient(main.app) as client:
        token_a = _login(client, "kw-a@example.com", "a-pass")
        token_b = _login(client, "kw-b@example.com", "b-pass")
        created = client.post(
            "/ignored-keywords",
            headers=_auth_headers(token_a),
            json={"keyword": "digitizer", "reason": "Part-only listings"},
        )
        keywords_a = client.get("/ignored-keywords", headers=_auth_headers(token_a))
        keywords_b = client.get("/ignored-keywords", headers=_auth_headers(token_b))
        items_a = client.get("/items", headers=_auth_headers(token_a))
        items_b = client.get("/items", headers=_auth_headers(token_b))

    assert created.status_code == 200
    assert keywords_a.json()[0]["keyword"] == "digitizer"
    assert keywords_b.json() == []
    assert items_a.json() == []
    assert items_b.json()[0]["user_status"] == "new"


def test_shared_polling_applies_user_specific_ignore_rules(monkeypatch, tmp_path):
    storage, settings = _configure_app(
        monkeypatch,
        tmp_path,
        auth_required=True,
        ebay_client_id="id",
        ebay_client_secret="secret",
    )
    user_a = _create_user(storage, settings, "scan-a@example.com", "a-pass")
    user_b = _create_user(storage, settings, "scan-b@example.com", "b-pass")
    storage.add_ignored_seller("seller-one", reason="Ignore seller one", user_id=int(user_a["id"]))
    _install_scan_fakes(monkeypatch, [_item("shared-scan-1", seller_username="seller-one")])

    summary = asyncio.run(
        main.scan_shared_once(
            limit=10,
            notify=False,
            triggered_by_user=None,
            background_mode=False,
        )
    )

    item_a = storage.get_user_item(int(user_a["id"]), "shared-scan-1")
    item_b = storage.get_user_item(int(user_b["id"]), "shared-scan-1")

    assert summary["unique_marketplace_items"] == 1
    assert item_a["user_status"] == "ignored"
    assert item_a["ignored_seller"] == "seller-one"
    assert item_b["user_status"] == "new"


def test_legacy_ignored_tables_migrate_to_fallback_user_only_and_idempotently(tmp_path):
    db_path = tmp_path / "legacy-ignores.sqlite3"
    first = Storage(db_path)
    with first.connect() as connection:
        connection.execute(
            "INSERT INTO ignored_sellers (seller_username, reason, source_item_id, created_at) VALUES (?, ?, ?, ?)",
            ("legacy-seller", "legacy seller reason", "legacy-item", "2026-05-01T00:00:00+00:00"),
        )
        connection.execute(
            "INSERT INTO ignored_keywords (keyword, reason, created_at) VALUES (?, ?, ?)",
            ("legacy keyword", "legacy keyword reason", "2026-05-01T00:00:00+00:00"),
        )

    second = Storage(db_path)
    fallback = second.get_user_by_email("local@notifierr.local")
    assert fallback is not None

    second.create_user(
        email="other@example.com",
        password_hash=hash_password("other-pass"),
        role="user",
        account_status="active",
    )
    third = Storage(db_path)

    with third.connect() as connection:
        fallback_seller_count = connection.execute(
            "SELECT COUNT(*) AS count FROM user_ignored_sellers WHERE user_id = ?",
            (int(fallback["id"]),),
        ).fetchone()["count"]
        fallback_keyword_count = connection.execute(
            "SELECT COUNT(*) AS count FROM user_ignored_keywords WHERE user_id = ?",
            (int(fallback["id"]),),
        ).fetchone()["count"]
        other_user_id = third.get_user_by_email("other@example.com")["id"]
        other_seller_count = connection.execute(
            "SELECT COUNT(*) AS count FROM user_ignored_sellers WHERE user_id = ?",
            (int(other_user_id),),
        ).fetchone()["count"]
        other_keyword_count = connection.execute(
            "SELECT COUNT(*) AS count FROM user_ignored_keywords WHERE user_id = ?",
            (int(other_user_id),),
        ).fetchone()["count"]

    assert fallback_seller_count == 1
    assert fallback_keyword_count == 1
    assert other_seller_count == 0
    assert other_keyword_count == 0


def test_auth_not_required_local_mode_respects_user_scoped_ignored_keywords(monkeypatch, tmp_path):
    _storage, _settings = _configure_app(
        monkeypatch,
        tmp_path,
        auth_required=False,
        ebay_client_id="id",
        ebay_client_secret="secret",
    )
    _install_scan_fakes(monkeypatch, [_item("local-ignored", title="Apple iPhone 14 screen digitizer cracked")])

    with TestClient(main.app) as client:
        created = client.post("/ignored-keywords", json={"keyword": "digitizer", "reason": "Part-only listings"})
        scan = client.post("/scan/run", json={"notify": False})
        visible = client.get("/items")
        ignored = client.get("/items?include_ignored=true")
        ignored_keywords = client.get("/ignored-keywords")

    assert created.status_code == 200
    assert scan.status_code == 200
    assert visible.json() == []
    assert ignored.json()[0]["user_status"] == "ignored"
    assert ignored_keywords.json()[0]["keyword"] == "digitizer"
