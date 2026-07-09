import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from backend import main
from backend.config import Settings
from backend.storage import Storage


def _iso(minutes_ago: int = 0, days_ago: int = 0) -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago, days=days_ago)).isoformat()


def _base_item(item_id: str, *, age_hours: int = 0, **overrides):
    item = {
        "item_id": item_id,
        "title": "Apple iPhone 14 128GB Unlocked Cracked Screen",
        "status": "candidate",
        "user_status": "new",
        "model": "iPhone 14",
        "alert_eligible": True,
        "whole_phone_confidence_passed": True,
        "has_repair_issue": True,
        "estimated_profit_available": True,
        "estimated_parts_cost_available": True,
        "resale_value": 500,
        "resale_mid": 500,
        "estimated_profit": 150,
        "profit_mid": 150,
        "found_at": _iso(minutes_ago=age_hours * 60),
    }
    item.update(overrides)
    return item


def test_fresh_alert_eligible_item_appears_in_best_finds():
    storage = Storage(Path(":memory:"))
    storage.upsert_item(_base_item("fresh-best", age_hours=1))

    stats = storage.stats()

    assert stats["best_finds"] == 1
    assert storage.list_items()[0]["fresh_for_alert"] is True


def test_auction_with_more_than_six_hours_left_is_not_best_find():
    storage = Storage(Path(":memory:"))
    item = main._apply_availability_and_auction_policy(
        _base_item(
            "auction-later",
            buying_option_summary="auction",
            item_end_at=(datetime.now(timezone.utc) + timedelta(hours=12)).isoformat(),
        )
    )
    storage.upsert_item(item)

    stored = storage.get_item("auction-later")

    assert storage.stats()["best_finds"] == 0
    assert stored["alert_eligible"] is False
    assert "Auction - not urgent" in stored["manual_review_reason"]


def test_auction_ending_soon_can_stay_priority_review():
    storage = Storage(Path(":memory:"))
    item = main._apply_availability_and_auction_policy(
        _base_item(
            "auction-soon",
            buying_option_summary="auction",
            item_end_at=(datetime.now(timezone.utc) + timedelta(hours=2)).isoformat(),
        )
    )
    storage.upsert_item(item)

    stats = storage.stats()

    assert stats["best_finds"] == 0
    assert stats["priority_review"] == 1


def test_buy_it_now_item_remains_best_find_eligible():
    storage = Storage(Path(":memory:"))
    storage.upsert_item(_base_item("bin-best", buying_option_summary="buy_it_now"))

    assert storage.stats()["best_finds"] == 1


def test_user_note_sold_keyword_is_stored_for_dashboard_warning():
    storage = Storage(Path(":memory:"))
    storage.upsert_item(_base_item("note-sold"))

    item = storage.set_note("note-sold", "sold - ignore")

    assert "sold" in item["user_note"]


def test_old_alert_eligible_item_is_hidden_from_best_finds_but_kept_for_dedupe():
    storage = Storage(Path(":memory:"))
    storage.upsert_item(_base_item("old-best", age_hours=25))

    stats = storage.stats()

    assert stats["best_finds"] == 0
    assert storage.item_exists("old-best") is True
    assert storage.get_item("old-best")["stale"] is True


def test_priority_review_and_needs_data_exclude_stale_by_default():
    storage = Storage(Path(":memory:"))
    storage.upsert_item(
        _base_item(
            "fresh-priority",
            age_hours=2,
            status="risky",
            alert_eligible=False,
            profit_mid=40,
            estimated_profit=40,
            manual_review_reason="Expected profit below threshold",
        )
    )
    storage.upsert_item(
        _base_item(
            "old-priority",
            age_hours=26,
            status="risky",
            alert_eligible=False,
            profit_mid=40,
            estimated_profit=40,
            manual_review_reason="Expected profit below threshold",
        )
    )
    storage.upsert_item(
        _base_item(
            "old-needs-data",
            age_hours=26,
            status="risky",
            alert_eligible=False,
            estimated_parts_cost_available=False,
            manual_review_reason="Missing part price",
        )
    )

    stats = storage.stats()

    assert stats["priority_review"] == 1
    assert stats["needs_data"] == 0
    assert [item["item_id"] for item in storage.list_items()] == ["fresh-priority"]


def test_all_tab_can_include_stale_items_when_requested():
    storage = Storage(Path(":memory:"))
    storage.upsert_item(_base_item("fresh", age_hours=1))
    storage.upsert_item(_base_item("stale", age_hours=48))

    assert {item["item_id"] for item in storage.list_items()} == {"fresh"}
    assert {item["item_id"] for item in storage.list_items(include_stale=True)} == {"fresh", "stale"}


def test_scan_summary_counts_fresh_new_stale_and_does_not_resend_alerts(monkeypatch):
    sent: list[str] = []
    fresh_listing = {
        "item_id": "fresh-scan",
        "title": "Apple iPhone 14 128GB Unlocked Cracked Screen Powers On Clean IMEI",
        "condition": "Used",
        "total_cost": 180,
        "found_at": _iso(minutes_ago=20),
        "item_origin_at": _iso(minutes_ago=20),
    }
    stale_listing = {
        **fresh_listing,
        "item_id": "stale-scan",
        "found_at": _iso(days_ago=8),
        "item_origin_at": _iso(days_ago=8),
    }

    class FakeEbayClient:
        def __init__(self, settings):
            self.settings = settings

        async def search(self, keywords, limit):
            return [fresh_listing, stale_listing]

    class FakeNotifier:
        def __init__(self, webhook_url):
            self.webhook_url = webhook_url

        async def send_deal(self, item, *, content=None):
            sent.append(item["item_id"])
            return True

    async def run_test():
        monkeypatch.setattr(main, "_scan_lock", asyncio.Lock())
        monkeypatch.setattr(main, "settings", Settings(ebay_client_id="id", ebay_client_secret="secret", discord_webhook_url="hook"))
        monkeypatch.setattr(main, "storage", Storage(Path(":memory:")))
        monkeypatch.setattr(main, "resale_research", {})
        monkeypatch.setattr(
            main,
            "repair_values",
            {
                "iPhone 14": {
                    "resale": {"low": 420, "mid": 500, "high": 580},
                    "risk_buffer": 40,
                    "parts_pricing_status": "verified_screenshot",
                    "parts": {"screen_safe": 90},
                }
            },
        )
        monkeypatch.setattr(main, "EbayClient", FakeEbayClient)
        monkeypatch.setattr(main, "DiscordNotifier", FakeNotifier)

        first = await main.scan_once(["iphone"], 10, notify=True)
        second = await main.scan_once(["iphone"], 10, notify=True)
        return first, second

    first, second = asyncio.run(run_test())

    assert first["scanned"] == 2
    assert first["new_items_found"] == 2
    assert first["fresh_items_found"] == 1
    assert first["stale_items_seen"] == 1
    assert first["best_finds"] == 1
    assert first["alerts_sent"] == 1
    assert second["duplicates_skipped"] == 2
    assert second["alerts_sent"] == 0
    assert sent == ["fresh-scan"]


def test_background_polling_does_not_run_overlapping_scans(monkeypatch):
    async def run_test():
        started = asyncio.Event()
        release = asyncio.Event()

        class SlowEbayClient:
            def __init__(self, settings):
                self.settings = settings

            async def search(self, keywords, limit):
                started.set()
                await release.wait()
                return []

        monkeypatch.setattr(main, "_scan_lock", asyncio.Lock())
        monkeypatch.setattr(main, "settings", Settings(ebay_client_id="id", ebay_client_secret="secret"))
        monkeypatch.setattr(main, "storage", Storage(Path(":memory:")))
        monkeypatch.setattr(main, "EbayClient", SlowEbayClient)

        first = asyncio.create_task(main.scan_once(["iphone"], 10, notify=False))
        await started.wait()
        second = await main.scan_once(["iphone"], 10, notify=False)
        release.set()
        await first
        return second

    skipped = asyncio.run(run_test())

    assert skipped["skipped"] is True
    assert skipped["reason"] == "scan_already_running"


def test_background_poll_timezone_supports_america_new_york():
    tz = main._background_poll_timezone("America/New_York")

    assert isinstance(tz, ZoneInfo)
    assert tz.key == "America/New_York"


def test_background_poll_active_window_uses_new_york_local_time(monkeypatch):
    fixed_utc = datetime(2026, 5, 24, 2, 0, tzinfo=timezone.utc)

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is None:
                return fixed_utc.replace(tzinfo=None)
            return fixed_utc.astimezone(tz)

    monkeypatch.setattr(main, "datetime", FixedDateTime)
    settings = Settings(
        background_poll_active_start="08:00",
        background_poll_active_end="23:00",
        background_poll_timezone="America/New_York",
    )

    assert main._background_poll_is_active(settings) is True


def test_background_poll_without_active_window_stays_enabled():
    settings = Settings(
        background_poll_active_start=None,
        background_poll_active_end=None,
        background_poll_timezone="Definitely/Invalid",
    )

    assert main._background_poll_is_active(settings) is True
