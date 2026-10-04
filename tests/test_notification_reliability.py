from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend import main
from backend.alerting import AlertDecision, evaluate_alert_decision
from backend.notification_delivery import destination_identity, notification_snapshot
from backend.storage import NOTIFICATION_CLAIM_LEASE_SECONDS, Storage


def _decision(tier="PROFITABLE", *, profit=80, roi=0.25, confidence="medium"):
    return AlertDecision(True, tier, expected_profit=profit, expected_roi=roi, confidence=confidence)


def _item(**updates):
    item = {
        "item_id": "item-1", "title": "iPhone 14 cracked screen powers on",
        "price": 180, "shipping": 0, "total_cost": 180, "profit_mid": 80,
        "positive_flags": ["cracked_screen", "powers_on", "unlocked"],
        "availability_status": "active", "fresh_for_active_queue": True,
        "stale": False, "item_age_minutes": 10,
    }
    item.update(updates)
    return item


def _resolved(**updates):
    notification = {
        "send_gem_immediately": True, "send_profitable_immediately": True,
        "review_delivery_mode": "immediate", "max_review_alerts_per_hour": 2,
        "duplicate_suppression_hours": 72, "meaningful_price_drop_amount": 20,
        "meaningful_price_drop_percent": 0.05, "meaningful_profit_increase_amount": 25,
        "meaningful_profit_increase_percent": 0.15, "meaningful_roi_increase": 0.10,
        "notification_ready": True, "_resolved_webhook_url": "https://discord.example/channel-secret",
        "webhook_source": "per_user", "catchup_enabled": True, "catchup_batch_size": 5,
    }
    notification.update(updates)
    return SimpleNamespace(notification_settings=notification, notify_best_finds=True,
                           resolved_discord_webhook_url=notification["_resolved_webhook_url"])


def _successful(storage, decision=None, item=None):
    decision = decision or _decision()
    item = item or _item()
    snapshot = notification_snapshot(item, decision, destination=destination_identity(_resolved().resolved_discord_webhook_url))
    return storage.create_notification_attempt(
        user_id=1, item_id=item["item_id"], notification_type=decision.tier.lower(),
        attempted=True, sent=True, status="sent", fingerprint=snapshot.fingerprint,
        notification_tier=snapshot.tier, effective_price=snapshot.effective_price,
        expected_profit=snapshot.expected_profit, expected_roi=snapshot.expected_roi,
        principal_damage=snapshot.principal_damage, availability_state=snapshot.availability_state,
        confidence=snapshot.confidence, destination_identity=snapshot.destination_identity,
    )


@pytest.fixture
def delivery(monkeypatch):
    storage = Storage(Path(":memory:"))
    monkeypatch.setattr(main, "storage", storage)
    return storage


def _gate(item, decision, resolved=None):
    return main._notification_delivery_gate(item, SimpleNamespace(), resolved or _resolved(), decision=decision, notify=True, user_id=1)


def test_previously_stored_but_never_notified_profitable_is_sent(delivery):
    assert _gate(_item(), _decision())[0] is True


def test_prior_needs_data_classification_does_not_suppress_profitable(delivery):
    assert _gate(_item(status="risky"), _decision())[0] is True


def test_failed_attempt_does_not_suppress_retry(delivery):
    delivery.create_notification_attempt(user_id=1, item_id="item-1", notification_type="profitable", attempted=True, failed=True)
    assert _gate(_item(), _decision())[0] is True


def test_skipped_attempt_does_not_suppress_valid_attempt(delivery):
    delivery.create_notification_attempt(user_id=1, item_id="item-1", notification_type="profitable", skipped=True)
    assert _gate(_item(), _decision())[0] is True


def test_successful_unchanged_profitable_is_suppressed(delivery):
    _successful(delivery)
    assert _gate(_item(), _decision()) == (False, "already_alerted", True)


def test_review_success_does_not_suppress_profitable_upgrade(delivery):
    _successful(delivery, _decision("REVIEW", profit=35, roi=0.08))
    assert _gate(_item(), _decision("PROFITABLE"))[0] is True


def test_meaningful_price_drop_allows_realert(delivery):
    _successful(delivery)
    assert _gate(_item(total_cost=150, price=150), _decision())[0] is True


def test_tiny_price_or_score_change_stays_suppressed(delivery):
    _successful(delivery)
    assert _gate(_item(total_cost=179, price=179, score=99), _decision(profit=81, roi=0.251))[0] is False


def test_dry_run_and_replay_without_sent_attempt_create_no_dedupe(delivery):
    delivery.create_notification_attempt(user_id=1, item_id="item-1", notification_type="profitable", skipped=True, source="historical_replay")
    assert _gate(_item(), _decision())[0] is True


def test_review_hourly_limit_counts_sent_and_defers(delivery):
    for suffix in ("a", "b"):
        _successful(delivery, _decision("REVIEW", profit=35, roi=0.08), _item(item_id=suffix))
    allowed, reason, duplicate = _gate(_item(item_id="review-c"), _decision("REVIEW", profit=35, roi=0.08))
    assert (allowed, reason, duplicate) == (False, "review_hourly_limit", False)


def test_catchup_is_restart_safe_via_success_history(delivery):
    _successful(delivery)
    first_restart = _gate(_item(), _decision())
    second_restart = _gate(_item(), _decision())
    assert first_restart == second_restart == (False, "already_alerted", True)


def test_hard_risk_and_component_cannot_enter_catchup():
    settings = SimpleNamespace(notification_settings={"max_listing_age_minutes": 360}, min_profit_to_alert=50,
                               min_score_to_alert=70, max_alert_item_age_minutes=360)
    result = SimpleNamespace(score=90, model="iPhone 14", profit_low=50, profit_mid=100, profit_high=140,
                             estimated_parts_cost=20, hard_reject_flags=["icloud_locked"],
                             listing_classification_flags=["screen_part_not_phone"], whole_phone_confidence_passed=True,
                             whole_phone_score=7, has_repair_issue=True, estimated_parts_cost_available=True,
                             parts_pricing_status="estimated", risk_flags=[], storage_capacity="128GB", positive_flags=[])
    item = {**_item(score=90, resale_mid=400, estimated_parts_cost=20, estimated_parts_cost_available=True,
                    whole_phone_confidence_passed=True, whole_phone_score=7, has_repair_issue=True,
                    hard_reject_flags=result.hard_reject_flags, listing_classification_flags=result.listing_classification_flags),
            "model": "iPhone 14"}
    assert evaluate_alert_decision(item, result, settings).eligible is False


def test_concurrent_workers_only_one_claims_pending_attempt(tmp_path):
    storage = Storage(tmp_path / "claims.db")
    def claim():
        return storage.create_notification_attempt(
            user_id=1, item_id="same-item", notification_type="profitable", attempted=True,
            status="pending", destination_identity="discord:channel", claim=True,
        )
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: claim(), range(2)))
    assert sum(value is not None for value in results) == 1


@pytest.mark.parametrize("sent", [True, False])
def test_claimed_notification_finalizes_success_or_failure(delivery, sent):
    class FakeNotifier:
        webhook_url = _resolved().resolved_discord_webhook_url
        last_provider_status = 204 if sent else 503
        last_failure_category = "provider_http_error"
        last_error_message = "HTTPStatusError"

        async def send_deal(self, item):
            return sent

    assert asyncio.run(main._send_alert_notification(
        FakeNotifier(), _item(alert_tier="PROFITABLE"), user_id=1,
        scan_cycle_id=None, destination_source="per_user", decision=_decision(),
    )) is sent
    attempt = delivery.list_notification_attempts(user_id=1)[0]
    assert attempt["status"] == ("sent" if sent else "failed")
    assert attempt["sent"] is sent
    assert attempt["failed"] is not sent
    if sent:
        assert _gate(_item(), _decision()) == (False, "already_alerted", True)
    else:
        assert _gate(_item(), _decision())[1] == "retry_backoff"


def test_dead_claim_waits_for_lease_then_recovers_with_backoff(delivery):
    identity = destination_identity(_resolved().resolved_discord_webhook_url)
    claimed = datetime.now(timezone.utc)
    attempt_id = delivery.create_notification_attempt(
        user_id=1, item_id="item-1", notification_type="profitable", attempted=True,
        status="pending", destination_identity=identity, claim=True,
    )
    assert attempt_id is not None
    assert delivery.create_notification_attempt(
        user_id=1, item_id="item-1", notification_type="profitable", attempted=True,
        status="pending", destination_identity=identity, claim=True,
    ) is None
    before = claimed + timedelta(seconds=NOTIFICATION_CLAIM_LEASE_SECONDS - 1)
    assert delivery.recover_stale_notification_claims(1, "item-1", identity, now=before) == []
    assert _gate(_item(), _decision())[0] is True  # The active claim is still protected by the unique index.
    assert delivery.create_notification_attempt(
        user_id=1, item_id="item-1", notification_type="profitable", attempted=True,
        status="pending", destination_identity=identity, claim=True,
    ) is None

    expired = claimed + timedelta(seconds=NOTIFICATION_CLAIM_LEASE_SECONDS + 1)
    assert delivery.recover_stale_notification_claims(1, "item-1", identity, now=expired) == [attempt_id]
    row = next(entry for entry in delivery.list_notification_attempts(user_id=1) if entry["id"] == attempt_id)
    assert row["status"] == "failed"
    assert row["failure_category"] == "claim_lease_expired"
    assert row["next_eligible_at"]
    assert _gate(_item(), _decision())[1] == "retry_backoff"
    assert delivery.finish_notification_attempt(attempt_id, sent=True, failed=False) is False
    row = next(entry for entry in delivery.list_notification_attempts(user_id=1) if entry["id"] == attempt_id)
    assert row["sent"] is False  # A late former owner cannot overwrite the recovered row.
    with delivery.connect() as connection:
        connection.execute(
            "UPDATE notification_attempts SET next_eligible_at = ? WHERE id = ?",
            ((datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(), attempt_id),
        )
    assert _gate(_item(), _decision())[0] is True


def test_only_one_worker_recovers_expired_claim(tmp_path):
    storage = Storage(tmp_path / "recovery.db")
    identity = "discord:channel"
    attempt_id = storage.create_notification_attempt(
        user_id=1, item_id="same-item", notification_type="gem", attempted=True,
        status="pending", destination_identity=identity, claim=True,
    )
    expired_at = datetime.now(timezone.utc) - timedelta(seconds=NOTIFICATION_CLAIM_LEASE_SECONDS + 1)
    with storage.connect() as connection:
        connection.execute("UPDATE notification_attempts SET claimed_at = ? WHERE id = ?", (expired_at.isoformat(), attempt_id))
    with ThreadPoolExecutor(max_workers=2) as pool:
        recovered = list(pool.map(
            lambda _: storage.recover_stale_notification_claims(1, "same-item", identity), range(2),
        ))
    assert sorted(len(ids) for ids in recovered) == [0, 1]
    assert [ids[0] for ids in recovered if ids] == [attempt_id]


def test_success_remains_deduped_after_stale_recovery(delivery):
    _successful(delivery)
    identity = destination_identity(_resolved().resolved_discord_webhook_url)
    stale_id = delivery.create_notification_attempt(
        user_id=1, item_id="item-1", notification_type="profitable", attempted=True,
        status="pending", destination_identity=identity, claim=True,
    )
    expired_at = datetime.now(timezone.utc) - timedelta(seconds=NOTIFICATION_CLAIM_LEASE_SECONDS + 1)
    with delivery.connect() as connection:
        connection.execute("UPDATE notification_attempts SET claimed_at = ? WHERE id = ?", (expired_at.isoformat(), stale_id))
    assert _gate(_item(), _decision()) == (False, "already_alerted", True)
    assert delivery.list_notification_attempts(user_id=1)[0]["failure_category"] == "claim_lease_expired"

