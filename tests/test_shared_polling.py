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


class _TestAsyncEvent:
    def __init__(self):
        self._is_set = False
        self._waiters = []

    def set(self):
        self._is_set = True
        for waiter in self._waiters:
            if not waiter.done():
                waiter.set_result(True)
        self._waiters.clear()

    async def wait(self):
        if self._is_set:
            return True
        loop = asyncio.get_running_loop()
        waiter = loop.create_future()
        self._waiters.append(waiter)
        await waiter
        return True


def _configure_app(monkeypatch, tmp_path, **settings_overrides):
    del tmp_path
    db_path = Path(":memory:")
    storage = Storage(db_path)
    auth_required = settings_overrides.pop("auth_required", True)
    search_keywords = settings_overrides.pop("search_keywords", ["shared keyword"])
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


def _create_user(storage: Storage, settings: Settings, email: str, password: str, *, role: str = "user", account_status: str = "active") -> dict:
    return storage.create_user(
        email=email,
        password_hash=hash_password(password),
        role=role,
        account_status=account_status,
        baseline_keywords=settings.search_keywords,
    )


def _listing(item_id: str = "shared-item") -> dict[str, object]:
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


class FakeScoreResult:
    def __init__(self, *, score: float = 82.0, status: str = "candidate", alert_eligible: bool = True):
        self.score = score
        self.status = status
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
        self.alert_eligible = alert_eligible
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


def _install_shared_scan_fakes(monkeypatch, listings, *, score_factory=None, hold_search: bool = False):
    captured = {"search_calls": [], "thresholds": [], "sent_urls": []}
    started = _TestAsyncEvent()
    release = _TestAsyncEvent()

    class FakeEbayClient:
        def __init__(self, settings):
            self.settings = settings

        async def search(self, keywords, limit):
            del limit
            captured["search_calls"].append(list(keywords))
            if hold_search:
                started.set()
                await release.wait()
            return [dict(item) for item in listings]

    class FakeNotifier:
        def __init__(self, webhook_url):
            self.webhook_url = webhook_url

        async def send_deal(self, item, *, content=None):
            del item, content
            captured["sent_urls"].append(self.webhook_url)
            return True

        async def send_message(self, content):
            del content
            captured["sent_urls"].append(self.webhook_url)
            return True

    def fake_score_listing(
        listing,
        repair_values,
        *,
        resale_research=None,
        scoring_rules=None,
        min_score_to_alert=None,
        min_profit_to_alert=None,
        risky_score_range=None,
        score_threshold=70.0,
        profit_threshold=75.0,
    ):
        del listing, repair_values, resale_research, scoring_rules, score_threshold, profit_threshold
        captured["thresholds"].append(
            (
                float(min_score_to_alert or 0),
                float(min_profit_to_alert or 0),
                tuple(float(value) for value in (risky_score_range or ())),
            )
        )
        if score_factory:
            return score_factory(
                min_score_to_alert=float(min_score_to_alert or 0),
                min_profit_to_alert=float(min_profit_to_alert or 0),
                risky_score_range=tuple(float(value) for value in (risky_score_range or ())),
            )
        return FakeScoreResult()

    monkeypatch.setattr(main, "EbayClient", FakeEbayClient)
    monkeypatch.setattr(main, "DiscordNotifier", FakeNotifier)
    monkeypatch.setattr(main, "score_listing", fake_score_listing)
    return captured, started, release


def test_shared_search_plan_dedupes_identical_keywords_and_creates_shared_rows(monkeypatch, tmp_path):
    storage, settings = _configure_app(
        monkeypatch,
        tmp_path,
        auth_required=True,
        ebay_client_id="id",
        ebay_client_secret="secret",
    )
    _create_user(storage, settings, "u1@example.com", "pass-one")
    _create_user(storage, settings, "u2@example.com", "pass-two")
    _create_user(storage, settings, "u3@example.com", "pass-three")
    captured, _started, _release = _install_shared_scan_fakes(monkeypatch, [_listing("shared-1")])

    summary = asyncio.run(
        main.scan_shared_once(
            limit=10,
            notify=False,
            triggered_by_user=None,
            background_mode=False,
        )
    )

    with storage.connect() as connection:
        marketplace_count = connection.execute("SELECT COUNT(*) AS count FROM marketplace_items").fetchone()["count"]
        state_count = connection.execute("SELECT COUNT(*) AS count FROM user_item_states").fetchone()["count"]

    assert summary["mode"] == "shared"
    assert summary["unique_searches"] == 1
    assert summary["unique_marketplace_items"] == 1
    assert captured["search_calls"] == [["shared keyword"]]
    assert marketplace_count == 1
    assert state_count == 3


def test_shared_scan_user_specific_thresholds_create_different_state_outcomes(monkeypatch, tmp_path):
    storage, settings = _configure_app(
        monkeypatch,
        tmp_path,
        auth_required=True,
        ebay_client_id="id",
        ebay_client_secret="secret",
    )
    strict_user = _create_user(storage, settings, "strict@example.com", "strict-pass")
    loose_user = _create_user(storage, settings, "loose@example.com", "loose-pass")
    storage.update_user_settings(int(strict_user["id"]), {"min_score_to_alert": 80})
    storage.update_user_settings(int(loose_user["id"]), {"min_score_to_alert": 60})
    _install_shared_scan_fakes(
        monkeypatch,
        [_listing("shared-2")],
        score_factory=lambda **kwargs: (
            FakeScoreResult(score=65.0, status="risky", alert_eligible=False)
            if kwargs["min_score_to_alert"] >= 80
            else FakeScoreResult(score=82.0, status="candidate", alert_eligible=True)
        ),
    )

    asyncio.run(
        main.scan_shared_once(
            limit=10,
            notify=False,
            triggered_by_user=None,
            background_mode=False,
        )
    )

    strict_item = storage.get_user_item(int(strict_user["id"]), "shared-2")
    loose_item = storage.get_user_item(int(loose_user["id"]), "shared-2")

    assert strict_item["status"] == "risky"
    assert strict_item["alert_eligible"] is False
    assert loose_item["status"] == "candidate"
    assert loose_item["alert_eligible"] is True


def test_shared_scan_notification_settings_respect_user_webhooks_and_disabled_flags(monkeypatch, tmp_path):
    storage, settings = _configure_app(
        monkeypatch,
        tmp_path,
        auth_required=True,
        ebay_client_id="id",
        ebay_client_secret="secret",
    )
    user_a = _create_user(storage, settings, "a@example.com", "a-pass")
    user_b = _create_user(storage, settings, "b@example.com", "b-pass")
    user_c = _create_user(storage, settings, "c@example.com", "c-pass")
    storage.update_user_notification_settings(int(user_a["id"]), {"discord_webhook": "https://discord.example/a", "discord_enabled": 1, "alerts_enabled": 1, "notify_best_finds": 1})
    storage.update_user_notification_settings(int(user_b["id"]), {"discord_webhook": "https://discord.example/b", "discord_enabled": 0, "alerts_enabled": 1, "notify_best_finds": 1})
    storage.update_user_notification_settings(int(user_c["id"]), {"discord_webhook": "https://discord.example/c", "discord_enabled": 1, "alerts_enabled": 0, "notify_best_finds": 1})
    captured, _started, _release = _install_shared_scan_fakes(monkeypatch, [_listing("shared-3")])

    summary = asyncio.run(
        main.scan_shared_once(
            limit=10,
            notify=True,
            triggered_by_user=None,
            background_mode=False,
        )
    )

    assert summary["alerts_sent"] == 1
    assert captured["sent_urls"] == ["https://discord.example/a"]


def test_disabled_user_is_skipped_by_shared_scan(monkeypatch, tmp_path):
    storage, settings = _configure_app(
        monkeypatch,
        tmp_path,
        auth_required=True,
        ebay_client_id="id",
        ebay_client_secret="secret",
    )
    active_user = _create_user(storage, settings, "active@example.com", "active-pass")
    _create_user(storage, settings, "disabled@example.com", "disabled-pass", account_status="disabled")
    _install_shared_scan_fakes(monkeypatch, [_listing("shared-4")])

    asyncio.run(
        main.scan_shared_once(
            limit=10,
            notify=False,
            triggered_by_user=None,
            background_mode=False,
        )
    )

    with storage.connect() as connection:
        state_count = connection.execute(
            """
            SELECT COUNT(*) AS count
            FROM user_item_states uis
            INNER JOIN marketplace_items mi ON mi.id = uis.marketplace_item_id
            WHERE mi.marketplace_item_id = 'shared-4'
            """
        ).fetchone()["count"]

    assert storage.get_user_item(int(active_user["id"]), "shared-4") is not None
    assert state_count == 1


def test_user_alert_dedupe_remains_per_user_under_shared_scan(monkeypatch, tmp_path):
    storage, settings = _configure_app(
        monkeypatch,
        tmp_path,
        auth_required=True,
        ebay_client_id="id",
        ebay_client_secret="secret",
    )
    user_a = _create_user(storage, settings, "alert-a@example.com", "a-pass")
    user_b = _create_user(storage, settings, "alert-b@example.com", "b-pass")
    storage.update_user_notification_settings(int(user_a["id"]), {"discord_webhook": "https://discord.example/a", "discord_enabled": 1, "alerts_enabled": 1, "notify_best_finds": 1})
    storage.update_user_notification_settings(int(user_b["id"]), {"discord_webhook": "https://discord.example/b", "discord_enabled": 1, "alerts_enabled": 1, "notify_best_finds": 1})
    captured, _started, _release = _install_shared_scan_fakes(monkeypatch, [_listing("shared-5")])

    first = asyncio.run(
        main.scan_shared_once(
            limit=10,
            notify=True,
            triggered_by_user=None,
            background_mode=False,
        )
    )
    second = asyncio.run(
        main.scan_shared_once(
            limit=10,
            notify=True,
            triggered_by_user=None,
            background_mode=False,
        )
    )

    assert first["alerts_sent"] == 2
    assert second["alerts_sent"] == 0
    assert captured["sent_urls"] == ["https://discord.example/a", "https://discord.example/b"]


def test_auth_not_required_still_uses_local_bridge_scan(monkeypatch, tmp_path):
    _storage, _settings = _configure_app(
        monkeypatch,
        tmp_path,
        auth_required=False,
        ebay_client_id="id",
        ebay_client_secret="secret",
    )
    captured, _started, _release = _install_shared_scan_fakes(monkeypatch, [_listing("shared-6")])

    with TestClient(main.app) as client:
        response = client.post("/scan/run", json={"notify": False})

    assert response.status_code == 200
    assert response.json()["mode"] == "local"
    assert captured["search_calls"] == [["shared keyword"]]


def test_background_shared_scan_does_not_overlap(monkeypatch, tmp_path):
    storage, settings = _configure_app(
        monkeypatch,
        tmp_path,
        auth_required=True,
        ebay_client_id="id",
        ebay_client_secret="secret",
        background_poll_seconds=30,
    )
    user = _create_user(storage, settings, "bg@example.com", "bg-pass")
    storage.update_user_settings(int(user["id"]), {"background_poll_enabled": 1, "background_poll_seconds": 30})
    _captured, started, release = _install_shared_scan_fakes(monkeypatch, [_listing("shared-7")], hold_search=True)

    async def run_test():
        first = asyncio.create_task(
            main.scan_shared_once(
                limit=10,
                notify=False,
                triggered_by_user=None,
                background_mode=True,
            )
        )
        await started.wait()
        second = await main.scan_shared_once(
            limit=10,
            notify=False,
            triggered_by_user=None,
            background_mode=True,
        )
        release.set()
        await first
        return second

    skipped = asyncio.run(run_test())

    assert skipped["skipped"] is True
    assert skipped["reason"] == "scan_already_running"


def test_admin_scan_stats_reports_latest_shared_run(monkeypatch, tmp_path):
    storage, settings = _configure_app(
        monkeypatch,
        tmp_path,
        auth_required=True,
        ebay_client_id="id",
        ebay_client_secret="secret",
    )
    admin = _create_user(storage, settings, "admin@example.com", "admin-pass", role="admin")
    _create_user(storage, settings, "member@example.com", "member-pass")
    _install_shared_scan_fakes(monkeypatch, [_listing("shared-8")])

    asyncio.run(
        main.scan_shared_once(
            limit=10,
            notify=False,
            triggered_by_user=admin,
            background_mode=False,
        )
    )

    with TestClient(main.app) as client:
        token = _login(client, "admin@example.com", "admin-pass")
        response = client.get("/admin/scan/stats", headers=_auth_headers(token))

    assert response.status_code == 200
    assert response.json()["unique_searches"] == 1
    assert response.json()["shared_api_calls"] == 1
    assert response.json()["total_items_ingested"] == 1
