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
    search_keywords = settings_overrides.pop("search_keywords", ["baseline one", "baseline two"])
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


def _listing(item_id: str = "scan-1") -> dict[str, object]:
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
    }


class FakeScoreResult:
    def __init__(self, *, score: float, status: str, alert_eligible: bool):
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
        self.mint_resale_low = 500.0
        self.mint_resale_mid = 540.0
        self.mint_resale_high = 580.0
        self.mint_profit_low = 120.0
        self.mint_profit_mid = 160.0
        self.mint_profit_high = 200.0
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


def _install_scan_fakes(monkeypatch, listings, *, score_factory=None):
    captured = {"keywords": [], "thresholds": [], "notifier_urls": [], "sent_urls": []}

    class FakeEbayClient:
        def __init__(self, settings):
            self.settings = settings

        async def search(self, keywords, limit):
            captured["keywords"].append(list(keywords))
            return [dict(item) for item in listings]

    class FakeNotifier:
        def __init__(self, webhook_url):
            captured["notifier_urls"].append(webhook_url)
            self.webhook_url = webhook_url

        async def send_deal(self, item, *, content=None):
            captured["sent_urls"].append(self.webhook_url)
            return True

        async def send_message(self, content):
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
        return FakeScoreResult(score=82.0, status="candidate", alert_eligible=True)

    monkeypatch.setattr(main, "EbayClient", FakeEbayClient)
    monkeypatch.setattr(main, "DiscordNotifier", FakeNotifier)
    monkeypatch.setattr(main, "score_listing", fake_score_listing)
    return captured


def test_resolved_settings_does_not_fallback_to_global_without_opt_in(monkeypatch, tmp_path):
    storage, settings = _configure_app(
        monkeypatch,
        tmp_path,
        auth_required=False,
        discord_webhook_url="https://discord.example/global-hook",
        min_score_to_alert=81,
        min_profit_to_alert=135,
        risky_score_range=(28.0, 62.0),
        max_alert_item_age_minutes=90,
        max_priority_review_item_age_hours=12,
        max_active_queue_item_age_hours=18,
        background_poll_enabled=True,
        background_poll_seconds=420,
        background_poll_active_start="07:00",
        background_poll_active_end="22:00",
        background_poll_timezone="America/Chicago",
        search_keywords=["fallback one", "fallback two"],
    )
    user = main._local_settings_user()
    with storage.connect() as connection:
        connection.execute("DELETE FROM user_settings WHERE user_id = ?", (int(user["id"]),))
        connection.execute("DELETE FROM user_notification_settings WHERE user_id = ?", (int(user["id"]),))
        connection.execute("DELETE FROM user_keywords WHERE user_id = ?", (int(user["id"]),))

    resolved = main._resolve_effective_user_settings(user, allow_local_fallback=False, ensure_defaults=False)

    assert resolved.min_score_to_alert == 81
    assert resolved.min_profit_to_alert == 135
    assert resolved.risky_score_range == (28.0, 62.0)
    assert resolved.max_alert_item_age_minutes == 90
    assert resolved.max_priority_review_item_age_hours == 12
    assert resolved.max_active_queue_item_age_hours == 18
    assert resolved.background_poll_enabled is True
    assert resolved.background_poll_seconds == 420
    assert resolved.active_start == "07:00"
    assert resolved.active_end == "22:00"
    assert resolved.timezone == "America/Chicago"
    assert resolved.keywords == ["fallback one", "fallback two"]
    assert resolved.resolved_discord_webhook_url == ""
    assert resolved.discord_enabled_for_alerts is False
    assert resolved.notification_settings["notification_block_reason"] == "discord_disabled"


def test_user_threshold_overrides_scoring_and_subsequent_scan_config(monkeypatch, tmp_path):
    storage, current_settings = _configure_app(
        monkeypatch,
        tmp_path,
        auth_required=True,
        ebay_client_id="id",
        ebay_client_secret="secret",
        discord_webhook_url="https://discord.example/global-hook",
    )
    user = storage.create_user(
        email="scan-user@example.com",
        password_hash=hash_password("scan-pass"),
        role="user",
        account_status="active",
        baseline_keywords=current_settings.search_keywords,
    )
    storage.update_user_notification_settings(
        int(user["id"]),
        {
            "discord_webhook": "https://discord.example/user-hook",
            "discord_enabled": 1,
            "alerts_enabled": 1,
            "notify_best_finds": 1,
            "notify_priority_review": 1,
        },
    )
    captured = _install_scan_fakes(
        monkeypatch,
        [_listing()],
        score_factory=lambda **kwargs: (
            FakeScoreResult(score=65.0, status="risky", alert_eligible=False)
            if kwargs["min_score_to_alert"] >= 80
            else FakeScoreResult(score=82.0, status="candidate", alert_eligible=True)
        ),
    )

    with TestClient(main.app) as client:
        token = _login(client, "scan-user@example.com", "scan-pass")
        first_settings = client.get("/settings", headers=_auth_headers(token)).json()["settings"]
        first = client.put(
            "/settings",
            headers=_auth_headers(token),
            json={**first_settings, "min_score_to_alert": 80},
        )
        first_scan = client.post("/scan/run", headers=_auth_headers(token), json={"notify": True})
        second_settings = client.get("/settings", headers=_auth_headers(token)).json()["settings"]
        second = client.put(
            "/settings",
            headers=_auth_headers(token),
            json={**second_settings, "min_score_to_alert": 60},
        )
        second_scan = client.post("/scan/run", headers=_auth_headers(token), json={"notify": True})

    assert first.status_code == 200
    assert second.status_code == 200
    assert first_scan.status_code == 200
    assert second_scan.status_code == 200
    assert captured["thresholds"][0][0] == 80.0
    assert captured["thresholds"][1][0] == 60.0
    assert first_scan.json()["best_finds"] == 0
    assert first_scan.json()["alerts_sent"] == 0
    assert second_scan.json()["profitable"] == 1
    assert second_scan.json()["alerts_sent"] == 1
    assert captured["sent_urls"] == ["https://discord.example/user-hook"]
    attempts = storage.list_notification_attempts(user_id=int(user["id"]))
    assert len(attempts) == 1
    assert attempts[0]["attempted"] is True
    assert attempts[0]["sent"] is True
    assert attempts[0]["failed"] is False


def test_user_keyword_disable_removes_keyword_and_custom_keyword_is_included(monkeypatch, tmp_path):
    storage, current_settings = _configure_app(
        monkeypatch,
        tmp_path,
        auth_required=True,
        ebay_client_id="id",
        ebay_client_secret="secret",
    )
    storage.create_user(
        email="keywords-scan@example.com",
        password_hash=hash_password("keywords-pass"),
        role="user",
        account_status="active",
        baseline_keywords=current_settings.search_keywords,
    )
    captured = _install_scan_fakes(monkeypatch, [])

    with TestClient(main.app) as client:
        token = _login(client, "keywords-scan@example.com", "keywords-pass")
        keywords = client.get("/settings/keywords", headers=_auth_headers(token)).json()["keywords"]
        baseline_one = next(entry for entry in keywords if entry["keyword"] == "baseline one")
        disabled = client.patch(
            f"/settings/keywords/{baseline_one['id']}",
            headers=_auth_headers(token),
            json={"enabled": False},
        )
        created = client.post(
            "/settings/keywords",
            headers=_auth_headers(token),
            json={"keyword": "iphone custom glass", "enabled": True},
        )
        scanned = client.post("/scan/run", headers=_auth_headers(token), json={"notify": False})

    assert disabled.status_code == 200
    assert created.status_code == 200
    assert scanned.status_code == 200
    assert captured["keywords"][-1] == ["baseline two", "iphone custom glass"]


def test_alerts_enabled_false_prevents_discord_notification(monkeypatch, tmp_path):
    storage, current_settings = _configure_app(
        monkeypatch,
        tmp_path,
        auth_required=True,
        ebay_client_id="id",
        ebay_client_secret="secret",
    )
    user = storage.create_user(
        email="alerts-off@example.com",
        password_hash=hash_password("alerts-off-pass"),
        role="user",
        account_status="active",
        baseline_keywords=current_settings.search_keywords,
    )
    storage.update_user_notification_settings(
        int(user["id"]),
        {
            "discord_webhook": "https://discord.example/user-hook",
            "discord_enabled": 1,
            "alerts_enabled": 0,
            "notify_best_finds": 1,
            "notify_priority_review": 1,
        },
    )
    captured = _install_scan_fakes(monkeypatch, [_listing("alerts-off")])

    with TestClient(main.app) as client:
        token = _login(client, "alerts-off@example.com", "alerts-off-pass")
        response = client.post("/scan/run", headers=_auth_headers(token), json={"notify": True})

    assert response.status_code == 200
    assert response.json()["alerts_sent"] == 0
    assert captured["sent_urls"] == []
    attempt = storage.list_notification_attempts(user_id=int(user["id"]))[0]
    assert attempt["skipped"] is True
    assert attempt["failure_category"] == "notifications_disabled"


def test_discord_enabled_false_prevents_discord_notification(monkeypatch, tmp_path):
    storage, current_settings = _configure_app(
        monkeypatch,
        tmp_path,
        auth_required=True,
        ebay_client_id="id",
        ebay_client_secret="secret",
    )
    user = storage.create_user(
        email="discord-off@example.com",
        password_hash=hash_password("discord-off-pass"),
        role="user",
        account_status="active",
        baseline_keywords=current_settings.search_keywords,
    )
    storage.update_user_notification_settings(
        int(user["id"]),
        {
            "discord_webhook": "https://discord.example/user-hook",
            "discord_enabled": 0,
            "alerts_enabled": 1,
            "notify_best_finds": 1,
            "notify_priority_review": 1,
        },
    )
    captured = _install_scan_fakes(monkeypatch, [_listing("discord-off")])

    with TestClient(main.app) as client:
        token = _login(client, "discord-off@example.com", "discord-off-pass")
        response = client.post("/scan/run", headers=_auth_headers(token), json={"notify": True})

    assert response.status_code == 200
    assert response.json()["alerts_sent"] == 0
    assert captured["sent_urls"] == []
    attempt = storage.list_notification_attempts(user_id=int(user["id"]))[0]
    assert attempt["skipped"] is True
    assert attempt["failure_category"] == "discord_disabled"


def test_user_webhook_is_used_instead_of_global_webhook_when_configured(monkeypatch, tmp_path):
    storage, current_settings = _configure_app(
        monkeypatch,
        tmp_path,
        auth_required=True,
        ebay_client_id="id",
        ebay_client_secret="secret",
        discord_webhook_url="https://discord.example/global-hook",
    )
    user = storage.create_user(
        email="user-webhook@example.com",
        password_hash=hash_password("user-webhook-pass"),
        role="user",
        account_status="active",
        baseline_keywords=current_settings.search_keywords,
    )
    storage.update_user_notification_settings(
        int(user["id"]),
        {
            "discord_webhook": "https://discord.example/user-hook",
            "discord_enabled": 1,
            "alerts_enabled": 1,
            "notify_best_finds": 1,
            "notify_priority_review": 1,
        },
    )
    captured = _install_scan_fakes(monkeypatch, [_listing("user-webhook")])

    with TestClient(main.app) as client:
        token = _login(client, "user-webhook@example.com", "user-webhook-pass")
        response = client.post("/scan/run", headers=_auth_headers(token), json={"notify": True})

    assert response.status_code == 200
    assert captured["notifier_urls"] == ["https://discord.example/user-hook"]
    assert captured["sent_urls"] == ["https://discord.example/user-hook"]


def test_auth_not_required_scan_can_use_explicit_global_webhook_opt_in(monkeypatch, tmp_path):
    storage, _settings = _configure_app(
        monkeypatch,
        tmp_path,
        auth_required=False,
        ebay_client_id="id",
        ebay_client_secret="secret",
        discord_webhook_url="https://discord.example/global-hook",
    )
    user = main._local_settings_user()
    storage.update_user_notification_settings(
        int(user["id"]),
        {"discord_enabled": 1, "use_global_discord_webhook": 1},
    )
    captured = _install_scan_fakes(monkeypatch, [_listing("global-fallback")])

    with TestClient(main.app) as client:
        response = client.post("/scan/run", json={"notify": True})

    assert response.status_code == 200
    assert response.json()["alerts_sent"] == 1
    assert captured["notifier_urls"] == ["https://discord.example/global-hook"]
    assert captured["sent_urls"] == ["https://discord.example/global-hook"]
