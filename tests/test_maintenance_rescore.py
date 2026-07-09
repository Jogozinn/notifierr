from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from backend import main
from backend.auth import hash_password
from backend.config import Settings
from backend.maintenance_rescore import MaintenanceRescoreRequest, run_maintenance_rescore
from backend.scripts.maintenance_rescore import build_parser, request_from_args
from backend.storage import Storage


class FakeScoreResult:
    def __init__(self, *, score: float = 82.0, status: str = "candidate", alert_eligible: bool = True):
        self.score = score
        self.status = status
        self.model = "iPhone 14"
        self.estimated_profit = 140.0
        self.resale_value = 520.0
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
        self.estimated_parts_cost = 90.0
        self.estimated_parts_cost_available = True
        self.risk_buffer = 40.0
        self.estimated_profit_available = True
        self.parts_pricing_status = "verified_screenshot"
        self.parts_pricing_note = ""
        self.parts_pricing_label = "Verified parts"
        self.pricing_warning = ""
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
            "estimated_parts_cost_available": self.estimated_parts_cost_available,
            "risk_buffer": self.risk_buffer,
            "estimated_profit_available": self.estimated_profit_available,
            "parts_pricing_status": self.parts_pricing_status,
            "parts_pricing_note": self.parts_pricing_note,
            "parts_pricing_label": self.parts_pricing_label,
            "pricing_warning": self.pricing_warning,
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


def _configure_app(monkeypatch, tmp_path, **settings_overrides):
    del tmp_path
    db_path = Path(":memory:")
    storage = Storage(db_path)
    settings = Settings(
        sqlite_path=db_path,
        auth_required=False,
        auth_secret_key="test-auth-secret-value-is-long-enough",
        access_token_expire_minutes=60,
        search_keywords=["maintenance keyword"],
        **settings_overrides,
    )
    monkeypatch.setattr(main, "storage", storage)
    monkeypatch.setattr(main, "settings", settings)
    monkeypatch.setattr(main, "repair_values", {})
    monkeypatch.setattr(main, "resale_research", {})
    monkeypatch.setattr(main, "scoring_rules", {})
    return storage, settings


def _create_user(storage: Storage, settings: Settings) -> dict:
    return storage.create_user(
        email="maintenance@example.com",
        password_hash=hash_password("maintenance-pass"),
        role="user",
        account_status="active",
        baseline_keywords=settings.search_keywords,
    )


def _listing(item_id: str = "maintenance-item") -> dict[str, object]:
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
        "raw_description": "stored raw description",
        "raw_json": {},
    }


def _run_request(user_id: int, tmp_path, **overrides):
    return run_maintenance_rescore(
        MaintenanceRescoreRequest(
            user_id=user_id,
            report_dir=tmp_path / "audit",
            **overrides,
        )
    )


def test_maintenance_rescore_dry_run_does_not_mutate_user_item_state_or_call_integrations(monkeypatch, tmp_path):
    storage, settings = _configure_app(monkeypatch, tmp_path)
    user = _create_user(storage, settings)
    storage.upsert_user_item(
        int(user["id"]),
        {**_listing("dry-run-maintenance"), **FakeScoreResult(status="candidate", alert_eligible=True).as_item_fields()},
    )
    monkeypatch.setattr(main, "score_listing", lambda *args, **kwargs: FakeScoreResult(score=10.0, status="rejected", alert_eligible=False))
    monkeypatch.setattr(main, "EbayClient", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("no eBay")))
    monkeypatch.setattr(main, "DiscordNotifier", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("no alerts")))

    result = _run_request(int(user["id"]), tmp_path, item_ids=["dry-run-maintenance"])

    item = storage.get_user_item(int(user["id"]), "dry-run-maintenance")
    assert result["dry_run"] is True
    assert result["written_count"] == 0
    assert result["scan_cycle_id"] is None
    assert result["status_changes"] == 1
    assert result["alert_eligible_true_to_false"] == 1
    assert Path(result["report_path"]).exists()
    assert item["status"] == "candidate"
    assert item["alert_eligible"] is True
    assert item["score"] == 82.0
    assert storage.list_scan_cycles() == []


def test_maintenance_rescore_cli_defaults_to_dry_run_and_write_requires_flag():
    parser = build_parser()

    dry_run = request_from_args(parser.parse_args(["--user-id", "1"]))
    write = request_from_args(parser.parse_args(["--user-id", "1", "--write"]))

    assert dry_run.write is False
    assert write.write is True


def test_maintenance_rescore_write_updates_scorer_fields_and_preserves_user_fields(monkeypatch, tmp_path):
    storage, settings = _configure_app(monkeypatch, tmp_path)
    user = _create_user(storage, settings)
    storage.upsert_user_item(
        int(user["id"]),
        {
            **_listing("write-maintenance"),
            **FakeScoreResult(score=93.0, status="candidate", alert_eligible=True).as_item_fields(),
            "user_status": "watched",
            "user_note": "keep this note",
            "watched_at": "2026-07-08T12:00:00+00:00",
            "alerted_at": "2026-07-08T12:05:00+00:00",
        },
    )
    rejected = FakeScoreResult(score=-100.0, status="rejected", alert_eligible=False)
    rejected.manual_review_reason = "Accessory/part listing"
    rejected.hard_reject_flags = ["screen_part_not_phone"]
    monkeypatch.setattr(main, "score_listing", lambda *args, **kwargs: rejected)

    result = _run_request(
        int(user["id"]),
        tmp_path,
        item_ids=["write-maintenance"],
        write=True,
        write_traces=True,
        reason="test maintenance write",
    )

    item = storage.get_user_item(int(user["id"]), "write-maintenance")
    traces = storage.list_decision_traces_for_cycle(result["scan_cycle_id"])
    cycle = storage.get_scan_cycle(result["scan_cycle_id"])
    assert result["written_count"] == 1
    assert item["status"] == "rejected"
    assert item["alert_eligible"] is False
    assert item["score"] == -100.0
    assert item["manual_review_reason"] == "Accessory/part listing"
    assert item["hard_reject_flags"] == ["screen_part_not_phone"]
    assert item["user_status"] == "watched"
    assert item["user_note"] == "keep this note"
    assert item["watched_at"] == "2026-07-08T12:00:00+00:00"
    assert item["alerted_at"] == "2026-07-08T12:05:00+00:00"
    assert traces[0]["trace"]["comparison"]["rescored_status"] == "rejected"
    assert cycle["mode"] == "maintenance_rescore"
    assert cycle["alerts_sent"] == 0


def test_maintenance_rescore_preserves_ignored_rejected_and_watched_user_statuses(monkeypatch, tmp_path):
    storage, settings = _configure_app(monkeypatch, tmp_path)
    user = _create_user(storage, settings)
    for user_status in ("ignored", "rejected", "watched"):
        storage.upsert_user_item(
            int(user["id"]),
            {
                **_listing(f"preserve-{user_status}"),
                **FakeScoreResult(status="candidate", alert_eligible=True).as_item_fields(),
                "user_status": user_status,
                "user_note": f"note-{user_status}",
            },
        )
    monkeypatch.setattr(main, "score_listing", lambda *args, **kwargs: FakeScoreResult(status="rejected", alert_eligible=False))

    _run_request(int(user["id"]), tmp_path, write=True, limit=10)

    for user_status in ("ignored", "rejected", "watched"):
        item = storage.get_user_item(int(user["id"]), f"preserve-{user_status}")
        assert item["status"] == "rejected"
        assert item["user_status"] == user_status
        assert item["user_note"] == f"note-{user_status}"


def test_maintenance_rescore_alert_eligible_upgrade_does_not_send_alert(monkeypatch, tmp_path):
    storage, settings = _configure_app(monkeypatch, tmp_path)
    user = _create_user(storage, settings)
    storage.upsert_user_item(
        int(user["id"]),
        {**_listing("upgrade-no-alert"), **FakeScoreResult(score=20.0, status="risky", alert_eligible=False).as_item_fields()},
    )
    monkeypatch.setattr(main, "score_listing", lambda *args, **kwargs: FakeScoreResult(score=95.0, status="candidate", alert_eligible=True))
    monkeypatch.setattr(main, "DiscordNotifier", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("no alerts")))

    result = _run_request(int(user["id"]), tmp_path, item_ids=["upgrade-no-alert"], write=True)

    item = storage.get_user_item(int(user["id"]), "upgrade-no-alert")
    assert result["alert_eligible_false_to_true"] == 1
    assert result["written_count"] == 1
    assert item["status"] == "candidate"
    assert item["alert_eligible"] is True


def test_maintenance_rescore_known_component_style_fixture_backfills_candidate(monkeypatch, tmp_path):
    storage, settings = _configure_app(monkeypatch, tmp_path)
    user = _create_user(storage, settings)
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
            **_listing("component-stale"),
            "title": "Apple iPhone 14 oem cracked screen parts Read bad OLED",
            "condition": "For parts or not working",
            "raw_description": "Touch works. Phone is not included, for flex parts only.",
            "total_cost": 40,
            **FakeScoreResult(score=93.0, status="candidate", alert_eligible=True).as_item_fields(),
        },
    )

    dry_run = _run_request(int(user["id"]), tmp_path, item_ids=["component-stale"])
    still_stale = storage.get_user_item(int(user["id"]), "component-stale")
    write = _run_request(int(user["id"]), tmp_path, item_ids=["component-stale"], write=True)
    backfilled = storage.get_user_item(int(user["id"]), "component-stale")

    assert dry_run["sample_changed_rows"][0]["persisted_status"] == "candidate"
    assert dry_run["sample_changed_rows"][0]["rescored_status"] == "rejected"
    assert dry_run["sample_changed_rows"][0]["rescored_alert_eligible"] is False
    assert still_stale["status"] == "candidate"
    assert write["written_count"] == 1
    assert backfilled["status"] == "rejected"
    assert backfilled["alert_eligible"] is False
    assert backfilled["user_status"] == "new"
