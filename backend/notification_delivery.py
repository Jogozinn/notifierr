from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


TIER_RANK = {"REVIEW": 1, "PROFITABLE": 2, "GEM": 3}
CONFIDENCE_RANK = {"low": 1, "medium": 2, "high": 3}


@dataclass(frozen=True)
class NotificationSnapshot:
    item_id: str
    tier: str
    effective_price: float
    expected_profit: float
    expected_roi: float
    principal_damage: str
    availability_state: str
    confidence: str
    destination_identity: str

    @property
    def fingerprint(self) -> str:
        payload = json.dumps(self.__dict__, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def destination_identity(webhook_url: str) -> str:
    """Return a stable channel identity without retaining or exposing the webhook."""
    value = str(webhook_url or "").strip()
    return f"discord:{hashlib.sha256(value.encode('utf-8')).hexdigest()[:20]}" if value else "discord:missing"


def notification_snapshot(
    item: dict[str, Any],
    decision: Any,
    *,
    destination: str,
) -> NotificationSnapshot:
    damage = sorted(
        set(item.get("positive_flags") or [])
        & {
            "cracked_screen", "screen_display_issue", "bad_oled", "bad_battery",
            "back_glass_cracked", "camera_lens_cracked", "charging_port_issue",
            "camera_fault", "face_id_issue", "digitizer_issue",
        }
    )
    return NotificationSnapshot(
        item_id=str(item.get("item_id") or ""),
        tier=str(getattr(decision, "tier", None) or item.get("alert_tier") or "").upper(),
        effective_price=round(float(item.get("total_cost") or item.get("price") or 0), 2),
        expected_profit=round(float(getattr(decision, "expected_profit", 0) or item.get("profit_mid") or 0), 2),
        expected_roi=round(float(getattr(decision, "expected_roi", 0) or 0), 4),
        principal_damage=damage[0] if damage else "unknown",
        availability_state=str(item.get("availability_status") or "unknown").lower(),
        confidence=str(getattr(decision, "confidence", None) or "low").lower(),
        destination_identity=destination,
    )


def successful_dedupe_reason(
    current: NotificationSnapshot,
    prior: dict[str, Any],
    *,
    price_amount: float,
    price_percent: float,
    profit_amount: float,
    profit_percent: float,
    roi_amount: float,
) -> str | None:
    """Return suppression reason, or None when the change warrants a re-alert."""
    prior_tier = str(prior.get("notification_tier") or prior.get("notification_type") or "").upper()
    if TIER_RANK.get(current.tier, 0) > TIER_RANK.get(prior_tier, 0):
        return None
    if not str(prior.get("fingerprint") or ""):
        return "legacy_success_same_or_higher_tier"
    prior_price = float(prior.get("effective_price") or 0)
    price_drop = prior_price - current.effective_price
    if prior_price > 0 and price_drop > 0 and (
        price_drop >= max(0, price_amount)
        or price_drop / prior_price >= max(0, price_percent)
    ):
        return None
    prior_profit = float(prior.get("expected_profit") or 0)
    profit_gain = current.expected_profit - prior_profit
    if profit_gain > 0 and (
        profit_gain >= max(0, profit_amount)
        or (abs(prior_profit) > 0 and profit_gain / abs(prior_profit) >= max(0, profit_percent))
    ):
        return None
    if current.expected_roi - float(prior.get("expected_roi") or 0) >= max(0, roi_amount):
        return None
    if CONFIDENCE_RANK.get(current.confidence, 0) > CONFIDENCE_RANK.get(str(prior.get("confidence") or "low"), 0):
        return None
    prior_availability = str(prior.get("availability_state") or "unknown").lower()
    if prior_availability in {"ended", "sold", "unavailable"} and current.availability_state not in {"ended", "sold", "unavailable"}:
        return None
    if current.fingerprint == str(prior.get("fingerprint") or ""):
        return "exact_fingerprint"
    return "equivalent_or_stronger_success"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
