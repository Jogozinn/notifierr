from pathlib import Path
from datetime import datetime, timezone
from fastapi.testclient import TestClient

from backend import main
from backend.auth import hash_password
from backend.config import Settings
from backend.storage import Storage


def _configure_app(monkeypatch, tmp_path, **settings_overrides):
    del tmp_path
    db_path = Path(":memory:")
    storage = Storage(db_path)
    auth_required = settings_overrides.pop("auth_required", True)
    search_keywords = settings_overrides.pop("search_keywords", ["baseline one"])
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
    return storage, settings


def _login(client: TestClient, email: str, password: str) -> str:
    response = client.post("/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def _auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _legacy_item_insert_sql() -> str:
    return """
        INSERT INTO items (
            item_id, title, price, shipping, total_cost, condition, item_url, image_url,
            seller_username, seller_feedback_percentage, seller_feedback_score, raw_description,
            item_origin_at, found_at, updated_at, model, score, status, resale_value, resale_low,
            resale_mid, resale_high, profit_low, profit_mid, profit_high, resale_confidence,
            resale_sample_size, resale_note, resale_source, resale_market_source, resale_condition_used,
            resale_storage_used, storage_resale_warning, mint_resale_low, mint_resale_mid, mint_resale_high,
            mint_profit_low, mint_profit_mid, mint_profit_high, storage_capacity, storage_confidence,
            storage_source, estimated_parts_cost, estimated_parts_cost_available, risk_buffer,
            estimated_profit, estimated_profit_available, parts_pricing_status, parts_pricing_note,
            parts_pricing_label, pricing_warning, manual_review_allowed, whole_phone_confidence_passed,
            whole_phone_score, has_repair_issue, manual_review_needed, manual_review_reason, alert_eligible,
            listing_classification_flags, user_status, user_note, reviewed_at, ignored_at, watched_at,
            promoted_at, rejected_by_user_at, user_reject_reason, ignored_reason, ignored_seller,
            updated_by_user_at, hard_reject_flags, positive_flags, risk_flags, alerted_at,
            availability_status, buying_option_summary, item_end_at, last_availability_checked_at,
            availability_note, raw_json
        ) VALUES (
            'legacy-1', 'Apple iPhone 14 cracked screen', 150, 20, 170, 'Used', 'https://item',
            'https://image', 'seller-a', 99.5, 200, 'desc',
            '2026-05-20T12:00:00+00:00', '2026-05-20T12:00:00+00:00', '2026-05-20T12:05:00+00:00',
            'iPhone 14', 82, 'candidate', 520, 480, 520, 560, 100, 140, 180, 'high',
            10, '', 'storage_specific', 'resale_research', 'good',
            '128GB', '', 530, 550, 580, 110, 150, 190, '128GB', 'high',
            'title', 90, 1, 40, 140, 1, 'verified_screenshot', '',
            'Verified parts', '', 0, 1, 100, 1, 0, '', 1,
            '[]', 'watched', 'legacy note', NULL, NULL, '2026-05-20T12:06:00+00:00',
            NULL, NULL, '', '', '', '2026-05-20T12:06:00+00:00',
            '[]', '[]', '[]', '2026-05-20T12:07:00+00:00',
            'in_stock', 'buy_it_now', NULL, '2026-05-20T12:07:00+00:00',
            '', '{}'
        )
    """


def _base_scanned_item(item_id: str = "shared-1") -> dict[str, object]:
    now = datetime.now(timezone.utc).isoformat()
    return {
        "item_id": item_id,
        "title": "Apple iPhone 14 128GB Unlocked Cracked Screen Powers On Clean IMEI",
        "price": 160,
        "shipping": 20,
        "total_cost": 180,
        "condition": "Used",
        "item_url": "https://item",
        "image_url": "https://image",
        "seller_username": "seller-a",
        "seller_feedback_percentage": 99.4,
        "seller_feedback_score": 120,
        "raw_description": "desc",
        "item_origin_at": now,
        "found_at": now,
        "availability_status": "in_stock",
        "buying_option_summary": "buy_it_now",
        "raw_json": {},
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
    sent: list[str] = []
    calls: list[list[str]] = []

    class FakeEbayClient:
        def __init__(self, settings):
            self.settings = settings

        async def search(self, keywords, limit):
            calls.append(list(keywords))
            return [dict(item) for item in listings]

    class FakeNotifier:
        def __init__(self, webhook_url):
            self.webhook_url = webhook_url

        async def send_deal(self, item, *, content=None):
            sent.append(self.webhook_url)
            return True

    def fake_score_listing(*args, **kwargs):
        del args, kwargs
        return FakeScoreResult()

    monkeypatch.setattr(main, "EbayClient", FakeEbayClient)
    monkeypatch.setattr(main, "DiscordNotifier", FakeNotifier)
    monkeypatch.setattr(main, "score_listing", fake_score_listing)
    monkeypatch.setattr(main, "_should_fetch_selective_detail", lambda listing, result, current_settings: False)
    return sent, calls


def test_legacy_items_migrate_into_marketplace_and_user_state_tables(tmp_path):
    db_path = tmp_path / "legacy-split.sqlite3"
    first = Storage(db_path)
    with first.connect() as connection:
        connection.execute(_legacy_item_insert_sql())

    storage = Storage(db_path)

    with storage.connect() as connection:
        marketplace_count = connection.execute("SELECT COUNT(*) AS count FROM marketplace_items").fetchone()["count"]
        state_count = connection.execute("SELECT COUNT(*) AS count FROM user_item_states").fetchone()["count"]
        migrated = connection.execute(
            """
            SELECT mi.marketplace_item_id, uis.user_note, uis.user_status, uis.alerted_at
            FROM user_item_states uis
            INNER JOIN marketplace_items mi ON mi.id = uis.marketplace_item_id
            LIMIT 1
            """
        ).fetchone()

    assert marketplace_count == 1
    assert state_count == 1
    assert migrated["marketplace_item_id"] == "legacy-1"
    assert migrated["user_note"] == "legacy note"
    assert migrated["user_status"] == "watched"
    assert migrated["alerted_at"] == "2026-05-20T12:07:00+00:00"


def test_legacy_split_migration_is_idempotent(tmp_path):
    db_path = tmp_path / "legacy-split-idempotent.sqlite3"
    first = Storage(db_path)
    first.upsert_item(
        {
            "item_id": "legacy-repeat",
            "title": "Apple iPhone 14 cracked screen",
            "status": "candidate",
            "user_status": "watched",
            "user_note": "persist me",
        }
    )

    second = Storage(db_path)

    with second.connect() as connection:
        marketplace_count = connection.execute("SELECT COUNT(*) AS count FROM marketplace_items").fetchone()["count"]
        state_count = connection.execute("SELECT COUNT(*) AS count FROM user_item_states").fetchone()["count"]

    assert marketplace_count == 1
    assert state_count == 1


def test_scan_writes_shared_marketplace_item_and_current_user_state(monkeypatch, tmp_path):
    storage, settings = _configure_app(
        monkeypatch,
        tmp_path,
        auth_required=True,
        ebay_client_id="id",
        ebay_client_secret="secret",
    )
    user = storage.create_user(
        email="scan-a@example.com",
        password_hash=hash_password("scan-pass"),
        role="user",
        account_status="active",
        baseline_keywords=settings.search_keywords,
    )
    storage.update_user_notification_settings(
        int(user["id"]),
        {
            "discord_webhook": "https://discord.example/a",
            "discord_enabled": 1,
            "alerts_enabled": 1,
            "notify_best_finds": 1,
            "notify_priority_review": 1,
        },
    )
    sent, _calls = _install_scan_fakes(monkeypatch, [_base_scanned_item("shared-1")])

    with TestClient(main.app) as client:
        token = _login(client, "scan-a@example.com", "scan-pass")
        response = client.post("/scan/run", headers=_auth_headers(token), json={"notify": True})
        items = client.get("/items", headers=_auth_headers(token))

    with storage.connect() as connection:
        marketplace_count = connection.execute("SELECT COUNT(*) AS count FROM marketplace_items").fetchone()["count"]
        state_count = connection.execute("SELECT COUNT(*) AS count FROM user_item_states").fetchone()["count"]

    assert response.status_code == 200
    assert items.status_code == 200
    assert len(items.json()) == 1
    assert items.json()[0]["item_id"] == "shared-1"
    assert response.json()["alerts_sent"] == 1
    assert sent == ["https://discord.example/a"]
    assert marketplace_count == 1
    assert state_count == 1


def test_get_items_and_stats_are_scoped_to_current_user_and_actions_are_isolated(monkeypatch, tmp_path):
    storage, settings = _configure_app(monkeypatch, tmp_path, auth_required=True)
    user_a = storage.create_user(
        email="a@example.com",
        password_hash=hash_password("a-pass"),
        role="user",
        account_status="active",
        baseline_keywords=settings.search_keywords,
    )
    user_b = storage.create_user(
        email="b@example.com",
        password_hash=hash_password("b-pass"),
        role="user",
        account_status="active",
        baseline_keywords=settings.search_keywords,
    )
    item = {
        **_base_scanned_item("shared-2"),
        **FakeScoreResult().as_item_fields(),
    }
    storage.upsert_user_item(int(user_a["id"]), item)
    storage.upsert_user_item(int(user_b["id"]), item)

    with TestClient(main.app) as client:
        token_a = _login(client, "a@example.com", "a-pass")
        token_b = _login(client, "b@example.com", "b-pass")
        note = client.post("/items/shared-2/note", headers=_auth_headers(token_a), json={"note": "user a note"})
        reject = client.post("/items/shared-2/reject", headers=_auth_headers(token_a), json={"reason": "not for me"})
        items_a = client.get("/items", headers=_auth_headers(token_a))
        items_b = client.get("/items", headers=_auth_headers(token_b))
        stats_a = client.get("/stats", headers=_auth_headers(token_a))
        stats_b = client.get("/stats", headers=_auth_headers(token_b))

    assert note.status_code == 200
    assert reject.status_code == 200
    assert items_a.status_code == 200
    assert items_b.status_code == 200
    assert items_a.json()[0]["user_note"] == "user a note"
    assert items_a.json()[0]["user_status"] == "rejected"
    assert items_b.json()[0]["user_note"] == ""
    assert items_b.json()[0]["user_status"] == "new"
    assert stats_a.json()["user_rejected"] == 1
    assert stats_b.json()["user_rejected"] == 0


def test_user_alert_dedupe_is_per_user_and_marketplace_item_is_shared_once(monkeypatch, tmp_path):
    storage, settings = _configure_app(
        monkeypatch,
        tmp_path,
        auth_required=True,
        ebay_client_id="id",
        ebay_client_secret="secret",
    )
    user_a = storage.create_user(
        email="alert-a@example.com",
        password_hash=hash_password("a-pass"),
        role="user",
        account_status="active",
        baseline_keywords=settings.search_keywords,
    )
    user_b = storage.create_user(
        email="alert-b@example.com",
        password_hash=hash_password("b-pass"),
        role="user",
        account_status="active",
        baseline_keywords=settings.search_keywords,
    )
    storage.update_user_notification_settings(int(user_a["id"]), {"discord_webhook": "https://discord.example/a", "discord_enabled": 1, "alerts_enabled": 1, "notify_best_finds": 1, "notify_priority_review": 1})
    storage.update_user_notification_settings(int(user_b["id"]), {"discord_webhook": "https://discord.example/b", "discord_enabled": 1, "alerts_enabled": 1, "notify_best_finds": 1, "notify_priority_review": 1})
    sent, _calls = _install_scan_fakes(monkeypatch, [_base_scanned_item("shared-3")])

    with TestClient(main.app) as client:
        token_a = _login(client, "alert-a@example.com", "a-pass")
        token_b = _login(client, "alert-b@example.com", "b-pass")
        first_a = client.post("/scan/run", headers=_auth_headers(token_a), json={"notify": True})
        second_a = client.post("/scan/run", headers=_auth_headers(token_a), json={"notify": True})
        first_b = client.post("/scan/run", headers=_auth_headers(token_b), json={"notify": True})

    with storage.connect() as connection:
        marketplace_count = connection.execute("SELECT COUNT(*) AS count FROM marketplace_items WHERE marketplace_item_id = 'shared-3'").fetchone()["count"]
        state_count = connection.execute(
            """
            SELECT COUNT(*) AS count
            FROM user_item_states uis
            INNER JOIN marketplace_items mi ON mi.id = uis.marketplace_item_id
            WHERE mi.marketplace_item_id = 'shared-3'
            """
        ).fetchone()["count"]

    assert first_a.json()["alerts_sent"] == 1
    assert second_a.json()["alerts_sent"] == 0
    assert first_b.json()["alerts_sent"] == 1
    assert sent == ["https://discord.example/a", "https://discord.example/b"]
    assert marketplace_count == 1
    assert state_count == 2


def test_auth_not_required_local_fallback_uses_split_tables(monkeypatch, tmp_path):
    storage, _settings = _configure_app(
        monkeypatch,
        tmp_path,
        auth_required=False,
        ebay_client_id="id",
        ebay_client_secret="secret",
        discord_webhook_url="https://discord.example/local",
    )
    _sent, _calls = _install_scan_fakes(monkeypatch, [_base_scanned_item("shared-4")])

    with TestClient(main.app) as client:
        scan = client.post("/scan/run", json={"notify": False})
        items = client.get("/items")
        stats = client.get("/stats")

    fallback = storage.get_user_by_email("local@notifierr.local")
    with storage.connect() as connection:
        state_user_id = connection.execute("SELECT user_id FROM user_item_states LIMIT 1").fetchone()["user_id"]

    assert scan.status_code == 200
    assert items.status_code == 200
    assert stats.status_code == 200
    assert len(items.json()) == 1
    assert items.json()[0]["item_id"] == "shared-4"
    assert stats.json()["total"] == 1
    assert fallback is not None
    assert state_user_id == int(fallback["id"])


def test_item_action_updates_user_item_states_not_shared_marketplace_row(monkeypatch, tmp_path):
    storage, settings = _configure_app(monkeypatch, tmp_path, auth_required=True)
    user_a = storage.create_user(
        email="state-a@example.com",
        password_hash=hash_password("a-pass"),
        role="user",
        account_status="active",
        baseline_keywords=settings.search_keywords,
    )
    user_b = storage.create_user(
        email="state-b@example.com",
        password_hash=hash_password("b-pass"),
        role="user",
        account_status="active",
        baseline_keywords=settings.search_keywords,
    )
    item = {
        **_base_scanned_item("shared-5"),
        **FakeScoreResult().as_item_fields(),
    }
    storage.upsert_user_item(int(user_a["id"]), item)
    storage.upsert_user_item(int(user_b["id"]), item)

    with TestClient(main.app) as client:
        token_a = _login(client, "state-a@example.com", "a-pass")
        watch = client.post("/items/shared-5/watch", headers=_auth_headers(token_a))

    with storage.connect() as connection:
        shared = connection.execute(
            "SELECT title, seller_username FROM marketplace_items WHERE marketplace_item_id = 'shared-5'"
        ).fetchone()
        states = connection.execute(
            """
            SELECT user_id, user_status
            FROM user_item_states uis
            INNER JOIN marketplace_items mi ON mi.id = uis.marketplace_item_id
            WHERE mi.marketplace_item_id = 'shared-5'
            ORDER BY user_id ASC
            """
        ).fetchall()

    assert watch.status_code == 200
    assert shared["title"] == "Apple iPhone 14 128GB Unlocked Cracked Screen Powers On Clean IMEI"
    assert shared["seller_username"] == "seller-a"
    assert [(row["user_id"], row["user_status"]) for row in states] == [
        (int(user_a["id"]), "watched"),
        (int(user_b["id"]), "new"),
    ]
