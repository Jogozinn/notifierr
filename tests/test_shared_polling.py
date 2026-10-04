import asyncio
import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from backend import main
from backend.auth import hash_password
from backend.config import Settings
from backend.storage import Storage, _has_reviewable_description_evidence, _is_priority_review_item


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


def _set_ebay_cooldown(storage: Storage, *, keyword: str = "shared keyword") -> dict:
    return storage.update_source_status(
        "ebay",
        status="cooling_down",
        cooldown_until=(datetime.now(timezone.utc) + timedelta(minutes=15)).isoformat(),
        last_failure_at=datetime.now(timezone.utc).isoformat(),
        last_http_status=429,
        last_error_category="ebay_rate_limited",
        last_error_message="EbayRateLimitError: eBay rate limit exceeded",
        last_keyword=keyword,
        retry_after_seconds=900,
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


def test_overlapping_searches_attribute_first_discovery_once(monkeypatch, tmp_path):
    storage, settings = _configure_app(
        monkeypatch, tmp_path, auth_required=True, ebay_client_id="id",
        ebay_client_secret="secret", search_keywords=["alpha", "beta"],
    )
    user = _create_user(storage, settings, "overlap@example.com", "overlap-pass")
    _install_shared_scan_fakes(monkeypatch, [_listing("overlap-1")])

    asyncio.run(main.scan_shared_once(limit=10, notify=False, triggered_by_user=None, background_mode=False))

    with storage.connect() as connection:
        results = connection.execute(
            "SELECT s.keyword, r.newly_discovered, r.scored FROM shared_scan_results r "
            "JOIN shared_scan_searches s ON s.id=r.scan_search_id ORDER BY s.id"
        ).fetchall()
    item = storage.get_user_item(int(user["id"]), "overlap-1")
    assert len(results) == 2
    assert sum(row["newly_discovered"] for row in results) == 1
    assert all(row["scored"] for row in results)
    assert item["first_seen_at"] and item["first_scored_at"]


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
    for user in (user_a, user_b):
        attempts = storage.list_notification_attempts(user_id=int(user["id"]))
        assert len(attempts) == 2
        assert attempts[0]["deduplicated"] is True
        assert attempts[0]["skipped"] is True
        assert attempts[0]["failure_category"] == "already_alerted"


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


def test_background_poll_loop_starts_only_when_process_should_poll(monkeypatch, tmp_path):
    async def fake_background_poll():
        await asyncio.Event().wait()

    async def run_test():
        _configure_app(
            monkeypatch,
            tmp_path,
            auth_required=True,
            background_poll_enabled=False,
        )
        monkeypatch.setattr(main, "_background_poll", fake_background_poll)
        monkeypatch.setattr(main, "_background_poll_task", None)
        main.polling_status.reset()
        assert main._start_background_poll_loop() is None
        assert main.polling_status.snapshot()["process_poll_task_started"] is False

        _configure_app(
            monkeypatch,
            tmp_path,
            auth_required=True,
            background_poll_enabled=True,
        )
        main.polling_status.reset()
        first = main._start_background_poll_loop()
        assert first is not None
        assert main.polling_status.snapshot()["process_poll_task_started"] is True
        await main._stop_background_poll_loop(first)

        _configure_app(
            monkeypatch,
            tmp_path,
            auth_required=False,
            background_poll_enabled=False,
        )
        main.polling_status.reset()
        local = main._start_background_poll_loop()
        assert local is None
        assert main.polling_status.snapshot()["process_poll_task_started"] is False

        _configure_app(
            monkeypatch,
            tmp_path,
            auth_required=False,
            background_poll_enabled=True,
        )
        monkeypatch.setattr(main, "_background_poll_task", None)
        main.polling_status.reset()
        local_enabled = main._start_background_poll_loop()
        assert local_enabled is not None
        assert main.polling_status.snapshot()["process_poll_task_started"] is True
        await main._stop_background_poll_loop(local_enabled)

    asyncio.run(run_test())


def test_background_poll_loop_does_not_start_twice_in_one_process(monkeypatch, tmp_path):
    async def fake_background_poll():
        await asyncio.Event().wait()

    async def run_test():
        _configure_app(
            monkeypatch,
            tmp_path,
            auth_required=True,
            background_poll_enabled=True,
        )
        monkeypatch.setattr(main, "_background_poll", fake_background_poll)
        monkeypatch.setattr(main, "_background_poll_task", None)
        main.polling_status.reset()

        first = main._start_background_poll_loop()
        second = main._start_background_poll_loop()

        assert first is not None
        assert second is first
        assert main.polling_status.snapshot()["duplicate_start_prevented"] is True
        await main._stop_background_poll_loop(first)

    asyncio.run(run_test())


def test_background_poll_uses_transactional_lease_instead_of_heartbeat(monkeypatch, tmp_path):
    storage, _settings = _configure_app(
        monkeypatch,
        tmp_path,
        auth_required=False,
        background_poll_enabled=True,
        background_poll_seconds=900,
    )
    now = datetime.now(timezone.utc)
    assert storage.acquire_worker_lease(
        "background_poll", "other-worker", hostname="other-host", process_id=999,
        now=now.isoformat(), expires_at=(now + timedelta(seconds=900)).isoformat(),
    )
    assert not storage.acquire_worker_lease(
        "background_poll", main.WORKER_ID, hostname=main._hostname(), process_id=main._process_id(),
        now=now.isoformat(), expires_at=(now + timedelta(seconds=900)).isoformat(),
    )


def test_offloaded_background_scan_keeps_health_and_status_responsive(monkeypatch, tmp_path):
    storage, settings = _configure_app(
        monkeypatch,
        tmp_path,
        auth_required=False,
        background_poll_enabled=True,
        background_poll_seconds=600,
    )
    user = main._local_settings_user()
    resolved = main._resolve_effective_user_settings(user)
    started = threading.Event()
    release = threading.Event()

    async def fake_unlocked(*args, **kwargs):
        del args, kwargs
        started.set()
        release.wait(timeout=5)
        return {
            "mode": "local",
            "scanned": 1,
            "new_items_found": 1,
            "fresh_items_found": 1,
            "stale_items_seen": 0,
            "best_finds": 0,
            "priority_review": 0,
            "candidates": 0,
            "rejected": 0,
            "alerts_sent": 0,
            "alerted": 0,
            "duplicates_skipped": 0,
            "keywords": ["iphone"],
        }

    async def run_test():
        monkeypatch.setattr(main, "_scan_once_unlocked", fake_unlocked)
        first = asyncio.create_task(
            main.scan_once(
                ["iphone"],
                1,
                notify=False,
                resolved_settings=resolved,
                cycle_mode="local_background",
                offload_scan_work=True,
            )
        )
        assert await asyncio.to_thread(started.wait, 1)
        before = time.perf_counter()
        health = main.health()
        status = main.admin_polling_status({})
        elapsed = time.perf_counter() - before
        second = await main.scan_once(
            ["iphone"],
            1,
            notify=False,
            resolved_settings=resolved,
            cycle_mode="local_background",
            offload_scan_work=True,
        )
        release.set()
        first_result = await first
        return health, status, elapsed, second, first_result

    health, status, elapsed, second, first_result = asyncio.run(run_test())

    assert elapsed < 0.5
    assert health["api_responsive"] is True
    assert status["api_responsive"] is True
    assert status["global_scan_lease_state"] == "owned"
    assert second["reason"] == "scan_already_running"
    assert first_result["scanned"] == 1
    cycles = storage.list_scan_cycles(limit=10)
    completed = [cycle for cycle in cycles if cycle["status"] == "completed"]
    skipped = [cycle for cycle in cycles if cycle["status"] == "skipped"]
    assert len(completed) == 1
    assert len(skipped) == 1


def test_background_poll_scan_failure_does_not_stop_future_cycles(monkeypatch, caplog):
    monkeypatch.setattr(main, "storage", Storage(Path(":memory:")))
    calls = {"scan": 0, "sleep": 0}
    resolved = SimpleNamespace(
        background_poll_enabled=True,
        background_poll_seconds=1,
        background_poll_active_start=None,
        background_poll_active_end=None,
        background_poll_timezone="UTC",
        keywords=["iphone"],
    )

    async def fake_scan_once(*args, **kwargs):
        del args, kwargs
        calls["scan"] += 1
        if calls["scan"] == 1:
            raise RuntimeError("temporary scan failure")
        return {"alerts_sent": 2}

    async def fake_sleep(seconds):
        del seconds
        calls["sleep"] += 1
        if calls["sleep"] >= 2:
            raise asyncio.CancelledError()

    async def run_test():
        try:
            await main._background_poll()
        except asyncio.CancelledError:
            pass

    monkeypatch.setattr(main, "settings", Settings(auth_required=False, background_poll_seconds=1))
    monkeypatch.setattr(main, "_resolve_effective_user_settings", lambda **kwargs: resolved)
    monkeypatch.setattr(main, "scan_once", fake_scan_once)
    monkeypatch.setattr(main.asyncio, "sleep", fake_sleep)
    main.polling_status.reset()

    caplog.set_level(logging.INFO)
    asyncio.run(run_test())

    status = main.polling_status.snapshot()
    assert calls["scan"] == 2
    assert status["cycles_attempted"] == 2
    assert status["cycles_succeeded"] == 1
    assert status["cycles_failed"] == 1
    assert status["last_alerts_sent"] == 2
    assert status["last_error"] == ""
    assert "Background poll cycle failed" in caplog.text
    assert "Background scan summary alerts_sent=2" in caplog.text


def test_background_poll_local_interval_uses_persisted_setting(monkeypatch, tmp_path, caplog):
    _storage, _settings = _configure_app(
        monkeypatch,
        tmp_path,
        auth_required=False,
        background_poll_enabled=True,
        background_poll_seconds=900,
    )
    resolved = SimpleNamespace(
        user=None,
        background_poll_enabled=True,
        background_poll_seconds=60,
        background_poll_active_start=None,
        background_poll_active_end=None,
        background_poll_timezone="UTC",
        keywords=["iphone"],
    )
    calls = {"sleep": 0}

    async def fake_sleep(seconds):
        calls["sleep"] += 1
        assert seconds == 300
        raise asyncio.CancelledError()

    async def run_test():
        try:
            await main._background_poll()
        except asyncio.CancelledError:
            pass

    monkeypatch.setattr(main, "_resolve_effective_user_settings", lambda **kwargs: resolved)
    monkeypatch.setattr(main, "_background_poll_is_active", lambda current_settings: False)
    monkeypatch.setattr(main.asyncio, "sleep", fake_sleep)
    main.polling_status.reset()

    caplog.set_level(logging.INFO)
    asyncio.run(run_test())

    status = main.polling_status.snapshot()
    assert calls["sleep"] == 1
    assert status["background_poll_config_seconds"] == 900
    assert status["background_poll_user_seconds"] == 300
    assert status["background_poll_interval_source"] == "persisted_user_min"
    assert status["last_sleep_seconds"] == 300
    assert "next_poll_seconds=300 config_poll_seconds=900 user_poll_seconds=300" in caplog.text


def test_background_poll_clamps_unsafe_poll_interval(monkeypatch, tmp_path, caplog):
    _storage, _settings = _configure_app(
        monkeypatch,
        tmp_path,
        auth_required=False,
        background_poll_enabled=True,
        background_poll_seconds=60,
    )
    resolved = SimpleNamespace(
        user=None,
        background_poll_enabled=True,
        background_poll_seconds=60,
        background_poll_active_start=None,
        background_poll_active_end=None,
        background_poll_timezone="UTC",
        keywords=["iphone"],
    )
    calls = {"sleep": 0}

    async def fake_sleep(seconds):
        calls["sleep"] += 1
        assert seconds == 300
        raise asyncio.CancelledError()

    async def run_test():
        try:
            await main._background_poll()
        except asyncio.CancelledError:
            pass

    monkeypatch.setattr(main, "_resolve_effective_user_settings", lambda **kwargs: resolved)
    monkeypatch.setattr(main, "_background_poll_is_active", lambda current_settings: False)
    monkeypatch.setattr(main.asyncio, "sleep", fake_sleep)
    main.polling_status.reset()

    caplog.set_level(logging.WARNING)
    asyncio.run(run_test())

    assert calls["sleep"] == 1
    assert main.polling_status.snapshot()["last_sleep_seconds"] == 300


def test_background_poll_rate_limit_uses_long_backoff(monkeypatch, caplog):
    storage, settings = _configure_app(
        monkeypatch,
        Path(":memory:"),
        auth_required=False,
        ebay_client_id="id",
        ebay_client_secret="secret",
        background_poll_seconds=60,
        ebay_rate_limit_backoff_seconds=900,
    )
    calls = {"sleep": 0}
    resolved = SimpleNamespace(
        background_poll_enabled=True,
        background_poll_seconds=60,
        background_poll_active_start=None,
        background_poll_active_end=None,
        background_poll_timezone="UTC",
        keywords=["iphone"],
    )

    async def fake_scan_once(*args, **kwargs):
        del args, kwargs
        raise main.EbayRateLimitError("eBay rate limit exceeded", retry_after_seconds=1200)

    async def fake_sleep(seconds):
        calls["sleep"] += 1
        assert seconds == 1200
        raise asyncio.CancelledError()

    async def run_test():
        try:
            await main._background_poll()
        except asyncio.CancelledError:
            pass

    monkeypatch.setattr(
        main,
        "settings",
        Settings(
            sqlite_path=settings.sqlite_path,
            auth_required=False,
            background_poll_seconds=60,
            ebay_rate_limit_backoff_seconds=900,
        ),
    )
    monkeypatch.setattr(main, "_resolve_effective_user_settings", lambda **kwargs: resolved)
    monkeypatch.setattr(main, "scan_once", fake_scan_once)
    monkeypatch.setattr(main.asyncio, "sleep", fake_sleep)
    main.polling_status.reset()

    caplog.set_level(logging.INFO)
    asyncio.run(run_test())

    status = main.polling_status.snapshot()
    assert calls["sleep"] == 1
    assert status["cycles_failed"] == 1
    assert status["last_sleep_seconds"] == 1200
    assert "EbayRateLimitError: eBay rate limit exceeded" in status["last_error"]
    assert "Background poll hit eBay rate limit" in caplog.text
    source = storage.get_source_status("ebay")
    assert source["status"] == "cooling_down"
    assert source["last_http_status"] == 429
    assert source["last_error_category"] == "ebay_rate_limited"
    assert source["retry_after_seconds"] == 1200
    cycle = storage.list_scan_cycles()[0]
    assert cycle["mode"] == "local_background"
    assert cycle["status"] == "failed"
    assert cycle["error_category"] == "ebay_rate_limited"
    assert cycle["http_status"] == 429
    assert cycle["cooldown_until"]
    heartbeat = storage.get_worker_heartbeats()[0]
    assert heartbeat["last_cycle_id"] == cycle["id"]
    assert heartbeat["next_wake_at"]


def test_manual_scan_rate_limit_returns_429(monkeypatch, tmp_path):
    storage, _settings = _configure_app(
        monkeypatch,
        tmp_path,
        auth_required=False,
        ebay_client_id="id",
        ebay_client_secret="secret",
        ebay_rate_limit_backoff_seconds=900,
    )

    class RateLimitedEbayClient:
        def __init__(self, settings):
            self.settings = settings

        async def search(self, keywords, limit):
            del keywords, limit
            raise main.EbayRateLimitError("eBay rate limit exceeded", retry_after_seconds=777)

    monkeypatch.setattr(main, "EbayClient", RateLimitedEbayClient)

    with TestClient(main.app) as client:
        response = client.post("/scan/run", json={"notify": False})

    assert response.status_code == 429
    assert response.headers["retry-after"] == "777"
    assert response.json()["detail"]["retry_after_seconds"] == 777
    source = storage.get_source_status("ebay")
    assert source["status"] == "cooling_down"
    assert source["last_http_status"] == 429
    assert source["last_error_category"] == "ebay_rate_limited"
    assert source["retry_after_seconds"] == 777


def test_scan_cycle_persists_on_successful_manual_scan(monkeypatch, tmp_path):
    storage, _settings = _configure_app(
        monkeypatch,
        tmp_path,
        auth_required=False,
        ebay_client_id="id",
        ebay_client_secret="secret",
    )
    user = main._local_settings_user()
    resolved = main._resolve_effective_user_settings(user)
    _install_shared_scan_fakes(monkeypatch, [_listing("cycle-success")])

    summary = asyncio.run(main.scan_once(["shared keyword"], 10, notify=False, resolved_settings=resolved))

    cycles = storage.list_scan_cycles()
    assert summary["scanned"] == 1
    assert len(cycles) == 1
    assert cycles[0]["mode"] == "manual"
    assert cycles[0]["status"] == "completed"
    assert cycles[0]["items_found"] == 1
    assert cycles[0]["items_scored"] == 1
    assert cycles[0]["final_bucket_counts"]["profitable"] == 1


def test_decision_trace_is_stored_for_scored_listing(monkeypatch, tmp_path):
    storage, _settings = _configure_app(
        monkeypatch,
        tmp_path,
        auth_required=False,
        ebay_client_id="id",
        ebay_client_secret="secret",
    )
    user = main._local_settings_user()
    resolved = main._resolve_effective_user_settings(user)
    _install_shared_scan_fakes(monkeypatch, [_listing("trace-success")])

    asyncio.run(main.scan_once(["shared keyword"], 10, notify=False, resolved_settings=resolved))

    cycle = storage.list_scan_cycles()[0]
    traces = storage.list_decision_traces_for_cycle(int(cycle["id"]))
    assert len(traces) == 1
    trace = traces[0]["trace"]
    assert trace["listing_id"] == "trace-success"
    assert trace["detected"]["model"] == "iPhone 14"
    assert trace["pricing"]["resale_mid"] == 520.0
    assert trace["verdict"]["app_status"] == "candidate"
    assert trace["verdict"]["current_app_bucket"] == "candidate"
    assert trace["verdict"]["bucket"] == "profitable"
    assert trace["verdict"]["normalized_bucket"] == "profitable"


def test_decision_trace_export_flattens_verdict_fields_from_json(monkeypatch, tmp_path):
    storage, _settings = _configure_app(
        monkeypatch,
        tmp_path,
        auth_required=False,
        ebay_client_id="id",
        ebay_client_secret="secret",
    )
    user = main._local_settings_user()
    storage.upsert_user_item(
        int(user["id"]),
        {**_listing("trace-flatten"), **FakeScoreResult(status="risky", alert_eligible=False).as_item_fields()},
    )
    cycle_id = storage.create_scan_cycle(mode="manual", user_id=int(user["id"]))
    storage.record_listing_decision_trace(
        user_id=int(user["id"]),
        item_id="trace-flatten",
        scan_cycle_id=cycle_id,
        trace={
            "listing_id": "trace-flatten",
            "title": "Apple iPhone 14 128GB Unlocked Cracked Screen",
            "detected": {},
            "pricing": {},
            "reasons": {},
            "verdict": {
                "current_app_bucket": "risky",
                "normalized_bucket": "watch",
                "bucket": "watch",
                "alert_eligible": False,
                "manual_review_needed": True,
                "score": 55,
            },
        },
    )

    row = storage.list_decision_traces_for_cycle(cycle_id)[0]

    assert row["current_app_bucket"] == "risky"
    assert row["normalized_bucket"] == "watch"
    assert row["alert_eligible"] is False
    assert row["manual_review_status"] == "needed"


def test_trace_replay_does_not_call_ebay_or_send_alerts_and_writes_traces(monkeypatch, tmp_path):
    storage, _settings = _configure_app(
        monkeypatch,
        tmp_path,
        auth_required=False,
        ebay_client_id="id",
        ebay_client_secret="secret",
    )
    user = main._local_settings_user()
    stored_item = {**_listing("replay-trace"), **FakeScoreResult(status="risky", alert_eligible=False).as_item_fields()}
    storage.upsert_user_item(int(user["id"]), stored_item)

    def fail_ebay(*args, **kwargs):
        del args, kwargs
        raise AssertionError("trace replay must not instantiate EbayClient")

    def fail_notifier(*args, **kwargs):
        del args, kwargs
        raise AssertionError("trace replay must not instantiate DiscordNotifier")

    _install_shared_scan_fakes(monkeypatch, [])
    monkeypatch.setattr(main, "EbayClient", fail_ebay)
    monkeypatch.setattr(main, "DiscordNotifier", fail_notifier)

    result = main.replay_listing_decision_traces(main.TraceReplayRequest(limit=10, write_traces=True, dry_run=False), admin_user=user)

    assert result["replayed"] == 1
    cycle = storage.get_scan_cycle(result["scan_cycle_id"])
    assert cycle["mode"] == "trace_replay"
    assert cycle["status"] == "completed"
    assert cycle["items_scored"] == 1
    assert cycle["alerts_attempted"] == 0
    assert cycle["alerts_sent"] == 0
    traces = storage.list_decision_traces_for_cycle(result["scan_cycle_id"])
    assert len(traces) == 1
    trace = traces[0]["trace"]
    assert trace["listing_id"] == "replay-trace"
    assert trace["verdict"]["current_app_status"] == "risky"
    assert trace["verdict"]["current_app_bucket"] == "risky"
    assert trace["verdict"]["normalized_bucket"] in {
        "gem", "profitable", "review", "good", "needs_data", "watch", "bad", "avoid"
    }


def test_trace_replay_rescore_from_raw_does_not_call_ebay_send_alerts_or_mutate_status(monkeypatch, tmp_path):
    storage, _settings = _configure_app(
        monkeypatch,
        tmp_path,
        auth_required=False,
        ebay_client_id="id",
        ebay_client_secret="secret",
    )
    user = main._local_settings_user()
    stored_item = {**_listing("raw-rescore"), **FakeScoreResult(status="risky", alert_eligible=False).as_item_fields()}
    storage.upsert_user_item(int(user["id"]), stored_item)
    captured_listing = {}

    def fail_ebay(*args, **kwargs):
        del args, kwargs
        raise AssertionError("raw rescore replay must not instantiate EbayClient")

    def fail_notifier(*args, **kwargs):
        del args, kwargs
        raise AssertionError("raw rescore replay must not instantiate DiscordNotifier")

    def score_from_raw(listing, repair_values, **kwargs):
        del repair_values, kwargs
        captured_listing.update(listing)
        return FakeScoreResult(status="candidate", alert_eligible=True)

    monkeypatch.setattr(main, "EbayClient", fail_ebay)
    monkeypatch.setattr(main, "DiscordNotifier", fail_notifier)
    monkeypatch.setattr(main, "score_listing", score_from_raw)

    result = main.replay_listing_decision_traces(main.TraceReplayRequest(limit=10, rescore_from_raw=True, write_traces=True, dry_run=False), admin_user=user)

    assert result["replayed"] == 1
    assert result["rescore_from_raw"] is True
    assert result["traces_written"] == 1
    assert "status" not in captured_listing
    assert "model" not in captured_listing
    assert storage.get_user_item(int(user["id"]), "raw-rescore")["status"] == "risky"
    traces = storage.list_decision_traces_for_cycle(result["scan_cycle_id"])
    assert len(traces) == 1
    comparison = traces[0]["trace"]["comparison"]
    assert comparison["persisted_status"] == "risky"
    assert comparison["rescored_status"] == "candidate"
    assert comparison["changed_status"] is True
    assert comparison["changed_alert_eligibility"] is True
    cycle = storage.get_scan_cycle(result["scan_cycle_id"])
    assert cycle["mode"] == "trace_replay"
    assert cycle["alerts_attempted"] == 0
    assert cycle["alerts_sent"] == 0


def test_trace_replay_rescore_can_target_source_cycle_and_item_ids(monkeypatch, tmp_path):
    storage, _settings = _configure_app(
        monkeypatch,
        tmp_path,
        auth_required=False,
        ebay_client_id="id",
        ebay_client_secret="secret",
    )
    user = main._local_settings_user()
    for item_id in ("source-a", "source-b", "source-c"):
        storage.upsert_user_item(
            int(user["id"]),
            {**_listing(item_id), **FakeScoreResult(status="risky", alert_eligible=False).as_item_fields()},
        )
    source_cycle_id = storage.create_scan_cycle(mode="manual", user_id=int(user["id"]))
    storage.record_listing_decision_trace(
        user_id=int(user["id"]),
        item_id="source-a",
        scan_cycle_id=source_cycle_id,
        trace={"listing_id": "source-a", "verdict": {}, "detected": {}, "pricing": {}, "reasons": {}},
    )
    storage.record_listing_decision_trace(
        user_id=int(user["id"]),
        item_id="source-b",
        scan_cycle_id=source_cycle_id,
        trace={"listing_id": "source-b", "verdict": {}, "detected": {}, "pricing": {}, "reasons": {}},
    )
    monkeypatch.setattr(main, "score_listing", lambda listing, repair_values, **kwargs: FakeScoreResult(status="candidate", alert_eligible=True))

    result = main.replay_listing_decision_traces(
        main.TraceReplayRequest(
            source_cycle_id=source_cycle_id,
            item_ids=["source-b", "source-c"],
            rescore_from_raw=True,
            limit=10,
            write_traces=True,
            dry_run=False,
        ),
        admin_user=user,
    )

    traces = storage.list_decision_traces_for_cycle(result["scan_cycle_id"])
    assert result["replayed"] == 1
    assert traces[0]["trace"]["listing_id"] == "source-b"
    assert result["filters"]["source_cycle_id"] == source_cycle_id
    assert result["filters"]["item_ids"] == ["source-b", "source-c"]


def test_trace_replay_rescore_dry_run_does_not_write_traces(monkeypatch, tmp_path):
    storage, _settings = _configure_app(
        monkeypatch,
        tmp_path,
        auth_required=False,
        ebay_client_id="id",
        ebay_client_secret="secret",
    )
    user = main._local_settings_user()
    storage.upsert_user_item(
        int(user["id"]),
        {**_listing("dry-run-rescore"), **FakeScoreResult(status="risky", alert_eligible=False).as_item_fields()},
    )
    monkeypatch.setattr(main, "score_listing", lambda listing, repair_values, **kwargs: FakeScoreResult(status="candidate", alert_eligible=True))

    result = main.replay_listing_decision_traces(
        main.TraceReplayRequest(limit=10, rescore_from_raw=True, dry_run=True)
    )

    assert result["replayed"] == 1
    assert result["traces_written"] == 0
    assert result["dry_run_traces"][0]["listing_id"] == "dry-run-rescore"
    assert result["scan_cycle_id"] is None
    assert result["cycle"]["status"] == "dry_run"
    assert storage.list_scan_cycles(limit=20) == []


def test_trace_replay_rescore_export_includes_comparison_and_change_counters(monkeypatch, tmp_path):
    storage, _settings = _configure_app(
        monkeypatch,
        tmp_path,
        auth_required=False,
        ebay_client_id="id",
        ebay_client_secret="secret",
    )
    user = main._local_settings_user()
    storage.upsert_user_item(
        int(user["id"]),
        {**_listing("comparison-rescore"), **FakeScoreResult(status="risky", alert_eligible=False).as_item_fields()},
    )
    monkeypatch.setattr(main, "score_listing", lambda listing, repair_values, **kwargs: FakeScoreResult(status="candidate", alert_eligible=True))

    result = main.replay_listing_decision_traces(main.TraceReplayRequest(limit=10, rescore_from_raw=True, write_traces=True, dry_run=False), admin_user=user)
    export = main.build_trace_audit_export(result["scan_cycle_id"])

    assert export["total_rescored_items"] == 1
    assert export["changed_status_count"] == 1
    assert export["changed_alert_eligibility_count"] == 1
    changed = export["samples"]["changed_traces"][0]
    assert changed["persisted_status"] == "risky"
    assert changed["rescored_status"] == "candidate"
    assert changed["changed_reasons"]


def test_trace_replay_rescore_rejects_display_part_from_raw_description(monkeypatch, tmp_path):
    storage, _settings = _configure_app(
        monkeypatch,
        tmp_path,
        auth_required=False,
        ebay_client_id="id",
        ebay_client_secret="secret",
    )
    user = main._local_settings_user()
    monkeypatch.setattr(
        main,
        "repair_values",
        {
            "iPhone 14": {
                "resale": {"low": 280, "mid": 337, "high": 410},
                "risk_buffer": 70,
                "parts_pricing_status": "verified_screenshot",
                "parts": {"screen_safe": 44},
            }
        },
    )
    storage.upsert_user_item(
        int(user["id"]),
        {
            **_listing("display-raw-rescore"),
            "title": "Apple iPhone 14 oem cracked screen parts Read bad OLED",
            "condition": "For parts or not working",
            "raw_description": "Touch works. Phone is not included, for flex parts only.",
            "total_cost": 40,
            **FakeScoreResult(status="candidate", alert_eligible=True).as_item_fields(),
        },
    )

    result = main.replay_listing_decision_traces(main.TraceReplayRequest(limit=10, rescore_from_raw=True, write_traces=True, dry_run=False), admin_user=user)
    trace = storage.list_decision_traces_for_cycle(result["scan_cycle_id"])[0]["trace"]

    assert trace["verdict"]["current_app_status"] == "candidate"
    assert trace["comparison"]["rescored_status"] == "rejected"
    assert trace["comparison"]["rescored_alert_eligible"] is False
    assert trace["detected"]["accessory_or_part_only"] is True
    assert "screen_part_not_phone" in trace["detected"]["hard_risks"]


def test_trace_replay_export_returns_reason_counters_and_needs_data_samples(monkeypatch, tmp_path):
    storage, _settings = _configure_app(
        monkeypatch,
        tmp_path,
        auth_required=False,
        ebay_client_id="id",
        ebay_client_secret="secret",
    )
    user = main._local_settings_user()
    stored_item = {**_listing("replay-needs-data"), **FakeScoreResult(status="risky", alert_eligible=False).as_item_fields()}
    storage.upsert_user_item(int(user["id"]), stored_item)

    def blocked_score(**kwargs):
        del kwargs
        result = FakeScoreResult(score=55.0, status="risky", alert_eligible=False)
        result.model = "unknown"
        result.storage_capacity = None
        result.estimated_profit_available = False
        result.estimated_parts_cost_available = False
        result.estimated_parts_cost = 0
        result.manual_review_needed = True
        result.manual_review_reason = "Missing part price; Model unknown"
        return result

    _install_shared_scan_fakes(monkeypatch, [], score_factory=blocked_score)

    result = main.replay_listing_decision_traces(main.TraceReplayRequest(limit=10, write_traces=True, dry_run=False), admin_user=user)
    export = main.build_trace_audit_export(result["scan_cycle_id"])

    assert export["total_traces"] == 1
    assert export["needs_data_reason_counts"]["model_unknown"] == 1
    assert export["needs_data_reason_counts"]["storage_unknown"] == 1
    assert export["alert_blocker_counts"]["estimated_profit_unavailable"] == 1
    assert export["model_unknown_count"] == 1
    assert export["storage_unknown_count"] == 1
    assert export["parts_cost_missing_count"] == 1
    assert export["samples"]["needs_data"][0]["item_id"] == "replay-needs-data"


def test_trace_audit_export_includes_description_extraction_miss_samples(monkeypatch, tmp_path):
    storage, _settings = _configure_app(
        monkeypatch,
        tmp_path,
        auth_required=False,
        ebay_client_id="id",
        ebay_client_secret="secret",
    )
    user = main._local_settings_user()
    listing = {
        **_listing("description-miss-trace"),
        "title": "Broken Unlocked Apple iPhone 13 128GB Bad Battery",
        "condition": "For parts or not working",
        "raw_description": """
        Items included in this sale: Broken Unlocked Apple iPhone 13 128GB Bad Battery.
        Functionality condition: Face ID works, cameras are fully functional, LCD/OLED has no issues,
        touch screen is fully functional, the charge port is clean and fully functional.
        This iPhone has a clean IMEI and is ready to be activated. Battery Health 84%.
        """,
        "total_cost": 180,
    }
    result = main.score_listing(
        listing,
        {
            "iPhone 13": {
                "resale": {"low": 240, "mid": 330, "high": 400},
                "risk_buffer": 50,
                "parts_pricing_status": "verified_screenshot_low_confidence",
                "parts": {"battery": 30},
            }
        },
        min_score_to_alert=70,
        min_profit_to_alert=75,
    )
    item = {**listing, **result.as_item_fields()}
    storage.upsert_user_item(int(user["id"]), item)
    cycle_id = storage.create_scan_cycle(mode="manual", user_id=int(user["id"]))
    trace = main._build_decision_trace(
        item,
        result,
        main._resolve_effective_user_settings(user),
        scan_cycle_id=cycle_id,
    )
    storage.record_listing_decision_trace(user_id=int(user["id"]), item_id="description-miss-trace", scan_cycle_id=cycle_id, trace=trace)

    export = main.build_trace_audit_export(cycle_id)

    assert export["description_extraction_miss_count"] == 1
    sample = export["samples"]["description_extraction_misses"][0]
    assert sample["category"] == "app_failed_to_extract_description_signals"
    assert sample["raw_description_evidence"]["included_device_signals"]
    assert "included_device" in sample["missed_signals"]


def test_trace_replay_handles_empty_db_gracefully(monkeypatch, tmp_path):
    storage, _settings = _configure_app(
        monkeypatch,
        tmp_path,
        auth_required=False,
        ebay_client_id="id",
        ebay_client_secret="secret",
    )
    _install_shared_scan_fakes(monkeypatch, [])
    user = main._local_settings_user()

    result = main.replay_listing_decision_traces(main.TraceReplayRequest(limit=10, write_traces=True, dry_run=False), admin_user=user)
    export = main.build_trace_audit_export(result["scan_cycle_id"])

    assert result["replayed"] == 0
    cycle = storage.get_scan_cycle(result["scan_cycle_id"])
    assert cycle["status"] == "completed"
    assert cycle["items_found"] == 0
    assert cycle["items_scored"] == 0
    assert export["total_traces"] == 0
    assert export["samples"]["needs_data"] == []


@pytest.mark.parametrize("settings_case", ["complete", "missing_defaults", "plaintext_webhook"])
@pytest.mark.parametrize("has_item", [False, True])
@pytest.mark.parametrize("request_kwargs", [{}, {"write_traces": True, "dry_run": True}])
def test_default_trace_replay_never_calls_storage_mutations(monkeypatch, tmp_path, settings_case, has_item, request_kwargs):
    storage, _settings = _configure_app(
        monkeypatch, tmp_path, auth_required=False,
        app_encryption_key="6xOPMctX4pJ4BoU5m6v6-55ZACFMy39YrOU8IZQ5lbY=",
    )
    user = main._local_settings_user()
    user_id = int(user["id"])
    if has_item:
        storage.upsert_user_item(
            user_id, {**_listing("read-only-replay"), **FakeScoreResult(status="risky", alert_eligible=False).as_item_fields()},
        )
    with storage.connect() as connection:
        if settings_case == "missing_defaults":
            connection.execute("DELETE FROM user_settings WHERE user_id = ?", (user_id,))
            connection.execute("DELETE FROM user_notification_settings WHERE user_id = ?", (user_id,))
        elif settings_case == "plaintext_webhook":
            connection.execute(
                "UPDATE user_notification_settings SET discord_webhook = ? WHERE user_id = ?",
                ("https://discord.example/plaintext-secret", user_id),
            )

    def forbid_write(*args, **kwargs):
        raise AssertionError("read-only replay called a storage mutation")

    prefixes = ("create_", "ensure_", "update_", "upsert_", "record_", "finish_", "set_", "delete_", "acquire_", "release_", "takeover_", "recover_", "mark_")
    for name in dir(storage):
        if name.startswith(prefixes) and callable(getattr(storage, name)):
            monkeypatch.setattr(storage, name, forbid_write)

    result = main.replay_listing_decision_traces(main.TraceReplayRequest(limit=10, **request_kwargs))

    assert result["replayed"] == int(has_item)
    assert result["scan_cycle_id"] is None
    assert result["traces_written"] == 0


def test_trace_replay_explicit_write_requires_authenticated_admin(monkeypatch, tmp_path):
    storage, _settings = _configure_app(monkeypatch, tmp_path, auth_required=False)
    user = main._local_settings_user()
    with pytest.raises(HTTPException) as exc:
        main.replay_listing_decision_traces(main.TraceReplayRequest(write_traces=True, dry_run=False))
    assert exc.value.status_code == 403
    assert storage.list_scan_cycles(limit=10) == []


def test_dashboard_queue_filters_and_sorts_before_pagination(monkeypatch, tmp_path):
    storage, _settings = _configure_app(monkeypatch, tmp_path, auth_required=False)
    user = main._local_settings_user()
    now = datetime.now(timezone.utc)
    for index in range(60):
        item = _listing(f"newer-{index}")
        item["title"] = f"newer-{index} iPhone 14"
        item["item_origin_at"] = (now - timedelta(minutes=index)).isoformat()
        item["found_at"] = item["item_origin_at"]
        storage.upsert_user_item(int(user["id"]), item)
    for rank, item_id in enumerate(("older-gem", "older-profitable", "older-review"), start=1):
        old = _listing(item_id)
        old["total_cost"] = rank
        old["item_origin_at"] = (now - timedelta(minutes=60 + rank)).isoformat()
        old["found_at"] = old["item_origin_at"]
        storage.upsert_user_item(int(user["id"]), old)
    storage.upsert_user_item(int(user["id"]), {
        **_listing("ignored-older"), "user_status": "ignored",
        "item_origin_at": (now - timedelta(minutes=64)).isoformat(),
    })
    monkeypatch.setattr(main, "_cached_pricing_context_for_user", lambda user_id: None)
    monkeypatch.setattr(main, "_decorate_item_for_user", lambda item, **kwargs: item)
    monkeypatch.setattr(main, "_decorate_alert_decision", lambda item, resolved: {
        **item, "alert_tier": {"older-gem": "GEM", "older-profitable": "PROFITABLE"}.get(item["item_id"], "REVIEW"),
        "alert_decision": {"blocking_reasons": []},
    })

    def page(queue, sort="newest", search="", limit=50, offset=0):
        return main.dashboard_items(
            queue=queue, sort=sort, search=search, limit=limit, offset=offset,
            include_ignored=False, include_stale=False, user=user,
        )

    gem = page("high_quality")
    assert [item["item_id"] for item in gem["items"]] == ["older-gem"]
    assert gem["total"] == gem["counts"]["high_quality"] == 1
    assert gem["counts"]["ignored"] == 1
    monkeypatch.setattr(main, "_should_start_background_poll_loop", lambda: False)
    with TestClient(main.app) as client:
        response = client.get("/items/dashboard", params={"queue": "high_quality", "limit": 50})
    assert response.status_code == 200
    assert [item["item_id"] for item in response.json()["items"]] == ["older-gem"]
    profitable = page("profitable")
    assert [item["item_id"] for item in profitable["items"]] == ["older-profitable"]
    assert profitable["counts"]["profitable"] == 1
    review = page("review", limit=10, offset=60)
    assert review["total"] == review["counts"]["review"] == 61
    assert [item["item_id"] for item in review["items"]] == ["older-review"]
    assert page("review", search="newer-59")["total"] == 1
    price_first = page("all", sort="price", limit=1)
    assert price_first["total"] == 63
    assert price_first["items"][0]["item_id"] == "older-gem"


def test_persisted_whole_phone_score_supports_description_review(monkeypatch, tmp_path):
    storage, _settings = _configure_app(monkeypatch, tmp_path, auth_required=False)
    user = main._local_settings_user()
    storage.upsert_user_item(int(user["id"]), {
        **_listing("description-review"),
        "status": "risky", "user_status": "new", "model": "iPhone 14", "storage_capacity": "128GB",
        "whole_phone_confidence_passed": True, "whole_phone_score": 7,
        "has_repair_issue": True, "estimated_parts_cost_available": False,
        "estimated_profit_available": False, "resale_value": 400, "resale_mid": 400,
        "raw_description": "The phone powers on; screen needs repair.",
        "listing_classification_flags": ["description_functionality_evidence"],
        "positive_flags": ["cracked_screen"],
    })
    persisted = storage.list_user_items(int(user["id"]))[0]
    assert persisted["whole_phone_score"] == 7
    assert _has_reviewable_description_evidence(persisted) is True
    assert main._has_reviewable_description_evidence(persisted) is True
    assert _is_priority_review_item(persisted) is True
    assert main._is_priority_review_candidate(persisted) is True


def test_alert_block_and_missing_data_reasons_appear_in_trace(monkeypatch, tmp_path):
    storage, _settings = _configure_app(
        monkeypatch,
        tmp_path,
        auth_required=False,
        ebay_client_id="id",
        ebay_client_secret="secret",
    )
    user = main._local_settings_user()
    resolved = main._resolve_effective_user_settings(user)

    def blocked_score(**kwargs):
        del kwargs
        result = FakeScoreResult(score=55.0, status="risky", alert_eligible=False)
        result.storage_capacity = None
        result.estimated_profit_available = False
        result.estimated_parts_cost_available = False
        result.manual_review_needed = True
        result.manual_review_reason = "Missing part price"
        return result

    _install_shared_scan_fakes(monkeypatch, [_listing("trace-blocked")], score_factory=blocked_score)

    asyncio.run(main.scan_once(["shared keyword"], 10, notify=False, resolved_settings=resolved))

    cycle = storage.list_scan_cycles()[0]
    trace = storage.list_decision_traces_for_cycle(int(cycle["id"]))[0]["trace"]
    assert "status_not_candidate" in trace["reasons"]["blocking_rules"]
    assert "score_below_threshold" in trace["reasons"]["blocking_rules"]
    assert "estimated_profit_unavailable" in trace["reasons"]["blocking_rules"]
    assert "storage_unknown" in trace["reasons"]["missing_data"]
    assert "part_price_missing" in trace["reasons"]["missing_data"]
    assert cycle["alert_block_reason_counts"]["estimated_profit_unavailable"] == 1
    assert cycle["missing_data_reason_counts"]["part_price_missing"] == 1


def test_scan_cycle_persists_on_failed_scan(monkeypatch, tmp_path):
    storage, _settings = _configure_app(
        monkeypatch,
        tmp_path,
        auth_required=False,
        ebay_client_id="id",
        ebay_client_secret="secret",
    )
    user = main._local_settings_user()
    resolved = main._resolve_effective_user_settings(user)

    class FailingEbayClient:
        def __init__(self, settings):
            self.settings = settings

        async def search(self, keywords, limit):
            del keywords, limit
            raise RuntimeError("search failed")

    monkeypatch.setattr(main, "EbayClient", FailingEbayClient)

    try:
        asyncio.run(main.scan_once(["shared keyword"], 10, notify=False, resolved_settings=resolved))
    except RuntimeError:
        pass

    cycle = storage.list_scan_cycles()[0]
    assert cycle["status"] == "failed"
    assert "RuntimeError: search failed" in cycle["error_message"]


def test_failed_shared_scan_persists_user_and_keyword_context(monkeypatch, tmp_path):
    storage, settings = _configure_app(
        monkeypatch,
        tmp_path,
        auth_required=True,
        ebay_client_id="id",
        ebay_client_secret="secret",
    )
    admin = _create_user(storage, settings, "failed-admin@example.com", "failed-admin-pass", role="admin")
    _create_user(storage, settings, "failed-member@example.com", "failed-member-pass")

    class FailingEbayClient:
        def __init__(self, settings):
            self.settings = settings

        async def search(self, keywords, limit):
            del keywords, limit
            raise RuntimeError("shared search failed")

    monkeypatch.setattr(main, "EbayClient", FailingEbayClient)

    try:
        asyncio.run(
            main.scan_shared_once(
                limit=10,
                notify=False,
                triggered_by_user=admin,
                background_mode=False,
                keyword_filter=["shared keyword"],
            )
        )
    except RuntimeError:
        pass

    cycle = storage.list_scan_cycles()[0]
    assert cycle["status"] == "failed"
    assert cycle["users_considered"] == 2
    assert cycle["users_scanned"] == 2
    assert cycle["keywords_searched"] == ["shared keyword"]


def test_ebay_429_scan_failure_preserves_rate_limit_context(monkeypatch, tmp_path):
    storage, settings = _configure_app(
        monkeypatch,
        tmp_path,
        auth_required=True,
        ebay_client_id="id",
        ebay_client_secret="secret",
    )
    admin = _create_user(storage, settings, "rate-limit-admin@example.com", "rate-limit-admin-pass", role="admin")

    class RateLimitedEbayClient:
        def __init__(self, settings):
            self.settings = settings

        async def search(self, keywords, limit):
            del limit
            raise main.EbayRateLimitError(
                "eBay rate limit exceeded; retry after 900 seconds",
                retry_after_seconds=900,
                keyword=keywords[0],
            )

    monkeypatch.setattr(main, "EbayClient", RateLimitedEbayClient)

    try:
        asyncio.run(
            main.scan_shared_once(
                limit=10,
                notify=False,
                triggered_by_user=admin,
                background_mode=False,
                keyword_filter=["shared keyword"],
            )
        )
    except main.EbayRateLimitError:
        pass

    cycle = storage.list_scan_cycles()[0]
    assert cycle["status"] == "failed"
    assert cycle["source"] == "ebay"
    assert cycle["error_category"] == "ebay_rate_limited"
    assert cycle["http_status"] == 429
    assert cycle["cooldown_until"]
    assert cycle["retry_after_seconds"] == 900
    assert cycle["users_considered"] == 1
    assert cycle["keywords_searched"] == ["shared keyword"]
    assert "EbayRateLimitError: eBay rate limit exceeded" in cycle["error_message"]
    assert '"category": "ebay_rate_limited"' in cycle["error_message"]
    assert '"source": "ebay"' in cycle["error_message"]
    assert '"http_status": 429' in cycle["error_message"]
    assert '"retry_after_seconds": 900' in cycle["error_message"]
    assert '"keyword": "shared keyword"' in cycle["error_message"]
    source = storage.get_source_status("ebay")
    assert source["status"] == "cooling_down"
    assert source["cooldown_until"]
    assert source["last_http_status"] == 429
    assert source["last_error_category"] == "ebay_rate_limited"
    assert source["last_keyword"] == "shared keyword"
    assert source["retry_after_seconds"] == 900


def test_manual_scan_during_ebay_cooldown_is_skipped_without_calling_ebay(monkeypatch, tmp_path):
    storage, _settings = _configure_app(
        monkeypatch,
        tmp_path,
        auth_required=False,
        ebay_client_id="id",
        ebay_client_secret="secret",
    )
    user = main._local_settings_user()
    resolved = main._resolve_effective_user_settings(user)
    _set_ebay_cooldown(storage)

    class FailingEbayClient:
        def __init__(self, settings):
            del settings
            raise AssertionError("cooldown scan must not instantiate EbayClient")

    monkeypatch.setattr(main, "EbayClient", FailingEbayClient)

    summary = asyncio.run(main.scan_once(["shared keyword"], 10, notify=False, resolved_settings=resolved))

    cycle = storage.list_scan_cycles()[0]
    assert summary["skipped"] is True
    assert summary["reason"] == "ebay_rate_limited"
    assert summary["cooldown_until"]
    assert cycle["status"] == "skipped"
    assert cycle["skip_reason"] == "ebay_rate_limited"
    assert cycle["source"] == "ebay"
    assert cycle["error_category"] == "ebay_rate_limited"
    assert cycle["http_status"] == 429
    assert cycle["cooldown_until"]
    assert cycle["retry_after_seconds"] == 900
    assert cycle["sources_checked"] == ["ebay"]
    assert cycle["users_considered"] == 1
    assert cycle["users_scanned"] == 0
    assert "cooldown_until" in cycle["error_message"]


def test_background_shared_scan_during_ebay_cooldown_is_skipped_without_calling_ebay(monkeypatch, tmp_path):
    storage, settings = _configure_app(
        monkeypatch,
        tmp_path,
        auth_required=True,
        ebay_client_id="id",
        ebay_client_secret="secret",
    )
    user = _create_user(storage, settings, "cooldown-bg@example.com", "cooldown-bg-pass")
    storage.update_user_settings(int(user["id"]), {"background_poll_enabled": 1})
    _set_ebay_cooldown(storage)

    class FailingEbayClient:
        def __init__(self, settings):
            del settings
            raise AssertionError("cooldown scan must not instantiate EbayClient")

    monkeypatch.setattr(main, "EbayClient", FailingEbayClient)

    summary = asyncio.run(
        main.scan_shared_once(
            limit=10,
            notify=False,
            triggered_by_user=None,
            background_mode=True,
        )
    )

    cycle = storage.list_scan_cycles()[0]
    assert summary["skipped"] is True
    assert summary["reason"] == "ebay_rate_limited"
    assert cycle["mode"] == "shared_background"
    assert cycle["status"] == "skipped"
    assert cycle["skip_reason"] == "ebay_rate_limited"
    assert cycle["source"] == "ebay"
    assert cycle["error_category"] == "ebay_rate_limited"
    assert cycle["http_status"] == 429
    assert cycle["cooldown_until"]
    assert cycle["retry_after_seconds"] == 900
    assert cycle["users_considered"] == 1
    assert cycle["users_scanned"] == 0


def test_source_status_endpoint_returns_cooling_down_state(monkeypatch, tmp_path):
    storage, _settings = _configure_app(
        monkeypatch,
        tmp_path,
        auth_required=False,
        ebay_client_id="id",
        ebay_client_secret="secret",
    )
    _set_ebay_cooldown(storage, keyword="endpoint keyword")
    monkeypatch.setattr(main, "_should_start_background_poll_loop", lambda: False)

    with TestClient(main.app) as client:
        response = client.get("/admin/sources/status")

    assert response.status_code == 200
    source = response.json()["sources"][0]
    assert source["source"] == "ebay"
    assert source["status"] == "cooling_down"
    assert source["cooldown_until"]
    assert source["last_http_status"] == 429
    assert source["last_error_category"] == "ebay_rate_limited"
    assert source["last_keyword"] == "endpoint keyword"


def test_scan_cycle_persists_on_skipped_active_window_scan(monkeypatch, tmp_path):
    storage, settings = _configure_app(
        monkeypatch,
        tmp_path,
        auth_required=False,
        ebay_client_id="id",
        ebay_client_secret="secret",
    )
    user = main._local_settings_user()
    storage.update_user_settings(int(user["id"]), {"background_poll_enabled": 1, "background_poll_seconds": 1})
    resolved = main._resolve_effective_user_settings(user)

    async def fake_sleep(seconds):
        del seconds
        raise asyncio.CancelledError()

    async def run_test():
        try:
            await main._background_poll()
        except asyncio.CancelledError:
            pass

    monkeypatch.setattr(main, "_resolve_effective_user_settings", lambda **kwargs: resolved)
    monkeypatch.setattr(main, "_background_poll_is_active", lambda current_settings: False)
    monkeypatch.setattr(main.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(main, "settings", Settings(sqlite_path=settings.sqlite_path, auth_required=False, background_poll_seconds=1))

    asyncio.run(run_test())

    cycle = storage.list_scan_cycles()[0]
    assert cycle["mode"] == "local_background"
    assert cycle["status"] == "skipped"
    assert cycle["skip_reason"] == "outside_active_window"


def test_worker_heartbeat_updates_from_background_poll(monkeypatch, tmp_path):
    storage, settings = _configure_app(
        monkeypatch,
        tmp_path,
        auth_required=False,
        ebay_client_id="id",
        ebay_client_secret="secret",
    )
    user = main._local_settings_user()
    storage.update_user_settings(int(user["id"]), {"background_poll_enabled": 0, "background_poll_seconds": 1})
    resolved = main._resolve_effective_user_settings(user)

    async def fake_sleep(seconds):
        del seconds
        raise asyncio.CancelledError()

    async def run_test():
        try:
            await main._background_poll()
        except asyncio.CancelledError:
            pass

    monkeypatch.setattr(main, "_resolve_effective_user_settings", lambda **kwargs: resolved)
    monkeypatch.setattr(main.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(main, "settings", Settings(sqlite_path=settings.sqlite_path, auth_required=False, background_poll_seconds=1))

    asyncio.run(run_test())

    heartbeat = storage.get_worker_heartbeats()[0]
    assert heartbeat["worker_name"] == "background_poll"
    assert heartbeat["status"] == "sleeping"
    assert heartbeat["last_seen_at"]
    assert heartbeat["next_wake_at"]


def test_admin_scan_instrumentation_endpoints_return_data(monkeypatch, tmp_path):
    storage, _settings = _configure_app(
        monkeypatch,
        tmp_path,
        auth_required=False,
        ebay_client_id="id",
        ebay_client_secret="secret",
    )
    user = main._local_settings_user()
    resolved = main._resolve_effective_user_settings(user)
    _install_shared_scan_fakes(monkeypatch, [_listing("endpoint-trace")])
    asyncio.run(main.scan_once(["shared keyword"], 10, notify=False, resolved_settings=resolved))
    cycle = storage.list_scan_cycles()[0]

    monkeypatch.setattr(main, "_should_start_background_poll_loop", lambda: False)
    with TestClient(main.app) as client:
        cycles = client.get("/admin/scan/cycles")
        cycle_detail = client.get(f"/admin/scan/cycles/{cycle['id']}")
        traces = client.get(f"/admin/scan/cycles/{cycle['id']}/decision-traces")
        item_traces = client.get("/admin/items/endpoint-trace/decision-traces")
        workers = client.get("/admin/worker/status")

    assert cycles.status_code == 200
    assert cycle_detail.status_code == 200
    assert traces.status_code == 200
    assert item_traces.status_code == 200
    assert workers.status_code == 200
    assert cycles.json()["cycles"][0]["id"] == cycle["id"]
    assert traces.json()["decision_traces"][0]["trace"]["listing_id"] == "endpoint-trace"
    assert item_traces.json()["decision_traces"][0]["scan_cycle_id"] == cycle["id"]
    assert "workers" in workers.json()


def test_admin_trace_replay_and_export_endpoints_return_data(monkeypatch, tmp_path):
    storage, _settings = _configure_app(
        monkeypatch,
        tmp_path,
        auth_required=False,
        ebay_client_id="id",
        ebay_client_secret="secret",
    )
    user = main._local_settings_user()
    storage.upsert_user_item(
        int(user["id"]),
        {**_listing("endpoint-replay"), **FakeScoreResult(status="risky", alert_eligible=False).as_item_fields()},
    )
    _install_shared_scan_fakes(monkeypatch, [])
    monkeypatch.setattr(main, "_should_start_background_poll_loop", lambda: False)

    with TestClient(main.app) as client:
        replay_response = client.post("/admin/scan/trace-replay", json={"limit": 10})
        rejected_write = client.post("/admin/scan/trace-replay", json={"limit": 10, "write_traces": True, "dry_run": False})
        written = main.replay_listing_decision_traces(main.TraceReplayRequest(limit=10, write_traces=True, dry_run=False), admin_user=user)
        cycle_id = written["scan_cycle_id"]
        export_response = client.get(f"/admin/scan/cycles/{cycle_id}/trace-export")

    assert replay_response.status_code == 200
    assert replay_response.json()["replayed"] == 1
    assert replay_response.json()["scan_cycle_id"] is None
    assert rejected_write.status_code == 403
    assert export_response.status_code == 200
    export = export_response.json()
    assert export["scan_cycle_id"] == cycle_id
    assert export["total_traces"] == 1
    assert "current_app_bucket_counts" in export
    assert export["bucket_field_definitions"]["current_app_bucket"].startswith("Existing app queue")
    assert export["bucket_field_definitions"]["normalized_bucket"].startswith("Audit-derived bucket")
    assert export["samples"]["bucket_disagreements"]


def test_fresh_scan_audit_export_ignores_trace_replay_cycles(monkeypatch, tmp_path):
    storage, _settings = _configure_app(
        monkeypatch,
        tmp_path,
        auth_required=False,
        ebay_client_id="id",
        ebay_client_secret="secret",
    )
    user = main._local_settings_user()
    resolved = main._resolve_effective_user_settings(user)
    _install_shared_scan_fakes(monkeypatch, [_listing("fresh-export")])

    asyncio.run(main.scan_once(["shared keyword"], 10, notify=False, resolved_settings=resolved))
    fresh_cycle = storage.list_scan_cycles()[0]
    replay = main.replay_listing_decision_traces(main.TraceReplayRequest(limit=10, write_traces=True, dry_run=False), admin_user=user)

    export = main.build_latest_fresh_scan_audit_export()

    assert replay["scan_cycle_id"] != fresh_cycle["id"]
    assert export["latest_successful_fresh_scan_cycle"]["id"] == fresh_cycle["id"]
    assert export["latest_successful_fresh_scan_cycle"]["mode"] == "manual"
    assert export["trace_export"]["scan_cycle_id"] == fresh_cycle["id"]
    assert export["trace_export"]["total_traces"] == 1


def test_shared_scan_notification_failure_is_logged_and_does_not_mark_alerted(monkeypatch, tmp_path, caplog):
    storage, settings = _configure_app(
        monkeypatch,
        tmp_path,
        auth_required=True,
        ebay_client_id="id",
        ebay_client_secret="secret",
    )
    user = _create_user(storage, settings, "notify-fail@example.com", "notify-fail-pass")
    storage.update_user_notification_settings(
        int(user["id"]),
        {
            "discord_webhook": "https://discord.example/fail",
            "discord_enabled": 1,
            "alerts_enabled": 1,
            "notify_best_finds": 1,
        },
    )
    _install_shared_scan_fakes(monkeypatch, [_listing("notify-fail")])

    class FailingNotifier:
        def __init__(self, webhook_url):
            self.webhook_url = webhook_url

        async def send_deal(self, item, *, content=None):
            del item, content
            raise RuntimeError("discord unavailable")

    monkeypatch.setattr(main, "DiscordNotifier", FailingNotifier)
    main.polling_status.reset()
    caplog.set_level(logging.INFO)

    summary = asyncio.run(
        main.scan_shared_once(
            limit=10,
            notify=True,
            triggered_by_user=None,
            background_mode=False,
        )
    )

    assert summary["profitable"] == 1
    assert summary["alerts_sent"] == 0
    assert storage.was_alerted_for_user(int(user["id"]), "notify-fail") is False
    attempt = storage.list_notification_attempts(user_id=int(user["id"]))[0]
    assert attempt["attempted"] is True
    assert attempt["sent"] is False
    assert attempt["failed"] is True
    assert attempt["failure_category"] == "provider_exception"
    assert main.polling_status.snapshot()["last_notifications_failed"] == 1
    assert "Alert notification send failed" in caplog.text


def test_polling_status_endpoint_returns_expected_keys_in_local_mode(monkeypatch, tmp_path):
    _configure_app(
        monkeypatch,
        tmp_path,
        auth_required=False,
        background_poll_enabled=False,
    )
    main.polling_status.reset()
    main.polling_status.task_started()
    main.polling_status.polling_enabled(False)

    with TestClient(main.app) as client:
        response = client.get("/admin/polling/status")

    assert response.status_code == 200
    payload = response.json()
    expected = {
        "process_poll_task_started",
        "duplicate_start_prevented",
        "auth_required",
        "background_poll_env_enabled",
        "background_poll_config_seconds",
        "background_poll_user_seconds",
        "background_poll_interval_source",
        "safe_min_background_poll_seconds",
        "local_or_user_polling_enabled",
        "cycle_running",
        "last_cycle_started_at",
        "last_cycle_finished_at",
        "last_success_at",
        "last_failure_at",
        "last_error",
        "last_sleep_seconds",
        "cycles_attempted",
        "cycles_succeeded",
        "cycles_failed",
        "last_scan_summary",
        "last_alerts_found",
        "last_alerts_sent",
        "last_notifications_attempted",
        "last_notifications_sent",
        "last_notifications_failed",
    }
    assert expected.issubset(payload.keys())
    assert payload["auth_required"] is False
    assert payload["process_poll_task_started"] is False


def test_polling_health_reports_blocked_orphan_and_durable_skip_counts(monkeypatch, tmp_path):
    storage, _settings = _configure_app(
        monkeypatch,
        tmp_path,
        auth_required=False,
        background_poll_enabled=True,
        background_poll_seconds=600,
    )
    now = datetime.now(timezone.utc)
    successful = storage.create_scan_cycle(
        mode="local_background", hostname="host", process_id=1, worker_id="host-1-worker",
        background_poll_enabled=True, background_poll_seconds=600,
    )
    storage.finish_scan_cycle(successful, status="completed", items_scored=1)
    with storage.connect() as connection:
        connection.execute(
            "UPDATE scan_cycles SET started_at = ?, finished_at = ? WHERE id = ?",
            ((now - timedelta(hours=3, minutes=2)).isoformat(), (now - timedelta(hours=3)).isoformat(), successful),
        )
    for _ in range(2):
        cycle_id = storage.create_scan_cycle(
            mode="local_background", hostname="host", process_id=2, worker_id=main.WORKER_ID,
            background_poll_enabled=True, background_poll_seconds=600,
        )
        storage.finish_scan_cycle(cycle_id, status="skipped", skip_reason="scan_already_running")
    storage.update_worker_heartbeat(
        worker_name="background_poll", process_id=main._process_id(), hostname=main._hostname(),
        started_at=(now - timedelta(hours=4)).isoformat(), status="sleeping",
    )
    assert storage.acquire_worker_lease(
        main.BACKGROUND_LEASE_NAME, main.WORKER_ID, hostname=main._hostname(), process_id=main._process_id(),
        now=now.isoformat(), expires_at=(now + timedelta(minutes=21)).isoformat(),
    )
    assert storage.acquire_worker_lease(
        main.GLOBAL_SCAN_LEASE_NAME, "host-15816-oldworker", hostname="host", process_id=15816,
        now=(now - timedelta(minutes=10)).isoformat(), expires_at=(now + timedelta(hours=5)).isoformat(),
    )
    monkeypatch.setattr(
        main,
        "inspect_lease_owner",
        lambda *args, **kwargs: SimpleNamespace(state="absent", reason="confirmed absent"),
    )
    monkeypatch.setattr(main, "_background_poll_is_active", lambda current_settings, now=None: True)

    payload = main.admin_polling_status({})

    assert payload["state"] == "blocked"
    assert payload["orphaned_lease_blocking_work"] is True
    assert payload["consecutive_skipped_cycles"] == 2
    assert payload["last_skip_reason"] == "scan_already_running"
    assert "dead worker PID 15816" in payload["reason"]


def test_fresh_scheduler_heartbeat_cannot_mask_overdue_success(monkeypatch, tmp_path):
    storage, _settings = _configure_app(
        monkeypatch,
        tmp_path,
        auth_required=False,
        background_poll_enabled=True,
        background_poll_seconds=600,
    )
    now = datetime.now(timezone.utc)
    cycle_id = storage.create_scan_cycle(
        mode="local_background", background_poll_enabled=True, background_poll_seconds=600,
    )
    storage.finish_scan_cycle(cycle_id, status="completed", items_scored=1)
    with storage.connect() as connection:
        connection.execute(
            "UPDATE scan_cycles SET started_at = ?, finished_at = ? WHERE id = ?",
            ((now - timedelta(hours=3, minutes=2)).isoformat(), (now - timedelta(hours=3)).isoformat(), cycle_id),
        )
    storage.update_worker_heartbeat(
        worker_name="background_poll", process_id=main._process_id(), hostname=main._hostname(),
        started_at=(now - timedelta(hours=4)).isoformat(), status="sleeping",
    )
    assert storage.acquire_worker_lease(
        main.BACKGROUND_LEASE_NAME, main.WORKER_ID, hostname=main._hostname(), process_id=main._process_id(),
        now=now.isoformat(), expires_at=(now + timedelta(minutes=21)).isoformat(),
    )
    monkeypatch.setattr(main, "_background_poll_is_active", lambda current_settings, now=None: True)

    payload = main.admin_polling_status({})

    assert payload["scheduler_process_state"] == "running"
    assert payload["state"] == "degraded"
    assert payload["last_success_age_seconds"] >= 3 * 60 * 60
    assert "overdue" in payload["reason"]


def test_polling_health_distinguishes_outside_active_window(monkeypatch, tmp_path):
    storage, _settings = _configure_app(
        monkeypatch,
        tmp_path,
        auth_required=False,
        background_poll_enabled=True,
        background_poll_seconds=600,
    )
    now = datetime.now(timezone.utc)
    storage.update_worker_heartbeat(
        worker_name="background_poll", process_id=main._process_id(), hostname=main._hostname(),
        started_at=now.isoformat(), status="sleeping",
    )
    assert storage.acquire_worker_lease(
        main.BACKGROUND_LEASE_NAME, main.WORKER_ID, hostname=main._hostname(), process_id=main._process_id(),
        now=now.isoformat(), expires_at=(now + timedelta(minutes=21)).isoformat(),
    )
    monkeypatch.setattr(main, "_background_poll_is_active", lambda current_settings, now=None: False)

    payload = main.admin_polling_status({})

    assert payload["state"] == "outside_window"
    assert payload["stale"] is False
    assert "outside configured active hours" in payload["reason"]


def test_admin_notification_test_uses_message_path_without_alert_dedupe_mutation(monkeypatch, tmp_path):
    storage, settings = _configure_app(
        monkeypatch,
        tmp_path,
        auth_required=True,
        ebay_client_id="id",
        ebay_client_secret="secret",
        discord_webhook_url="https://discord.example/global",
    )
    admin = _create_user(storage, settings, "admin-notify@example.com", "admin-notify-pass", role="admin")
    storage.update_user_notification_settings(
        int(admin["id"]),
        {
            "discord_webhook": "https://discord.example/admin",
            "discord_enabled": 1,
            "alerts_enabled": 1,
            "notify_best_finds": 1,
            "notify_priority_review": 1,
        },
    )
    _install_shared_scan_fakes(monkeypatch, [_listing("admin-test-dedupe")])

    sent_messages = []

    async def fake_send_message(self, content):
        sent_messages.append((self.webhook_url, content))
        return True

    monkeypatch.setattr(main.DiscordNotifier, "send_message", fake_send_message)
    main.polling_status.reset()

    with TestClient(main.app) as client:
        token = _login(client, "admin-notify@example.com", "admin-notify-pass")
        scan = client.post("/scan/run", headers=_auth_headers(token), json={"notify": False})
        before = storage.was_alerted_for_user(int(admin["id"]), "admin-test-dedupe")
        response = client.post("/admin/notifications/test", headers=_auth_headers(token))
        after = storage.was_alerted_for_user(int(admin["id"]), "admin-test-dedupe")

    assert scan.status_code == 200
    assert before is False
    assert response.status_code == 200
    assert response.json() == {"attempted": True, "sent": True, "failed": False, "error": ""}
    assert after is False
    assert sent_messages == [("https://discord.example/admin", main.TEST_NOTIFICATION_MESSAGE)]
    attempts = storage.list_notification_attempts(user_id=int(admin["id"]))
    test_attempt = next(entry for entry in attempts if entry["notification_type"] == "test")
    assert test_attempt["attempted"] is True
    assert test_attempt["sent"] is True
    assert test_attempt["item_id"] is None
    assert test_attempt["scan_cycle_id"] is None
    assert main.polling_status.snapshot()["last_notifications_sent"] == 1


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
