from __future__ import annotations

import base64
import hashlib
import json
import logging
from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import quote

from cryptography.hazmat.primitives import serialization

from .config import Settings
from .secrets import SecretConfigurationError, decrypt_secret


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PushResult:
    selected: int = 0
    accepted: int = 0
    failed: int = 0
    disabled: int = 0

    @property
    def sent(self) -> bool:
        return self.accepted > 0

    def as_dict(self) -> dict[str, int]:
        return {
            "selected": self.selected,
            "accepted": self.accepted,
            "failed": self.failed,
            "disabled": self.disabled,
        }


def endpoint_hash(endpoint: str) -> str:
    return hashlib.sha256(endpoint.strip().encode("utf-8")).hexdigest()


def push_destination_identity(user_id: int) -> str:
    return f"push:user:{int(user_id)}"


def _vapid_private_key(settings: Settings) -> str:
    if not settings.vapid_private_key_b64:
        raise ValueError("VAPID_PRIVATE_KEY_B64 is not configured")
    pem = base64.b64decode(settings.vapid_private_key_b64)
    key = serialization.load_pem_private_key(pem, password=None)
    der = key.private_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return base64.urlsafe_b64encode(der).rstrip(b"=").decode("ascii")


def push_payload(item: dict[str, Any], *, tier: str, test: bool = False) -> dict[str, str]:
    if test:
        return {
            "title": "Notifierr test",
            "body": "Background notifications are working on this device.",
            "url": "/?push_test=1",
            "tag": "notifierr-test",
            "tier": "TEST",
        }
    item_id = str(item.get("item_id") or "")
    model = str(item.get("model") or "iPhone opportunity")
    total = float(item.get("total_cost") or item.get("price") or 0)
    repair = float(item.get("estimated_parts_cost") or 0)
    resale = float(item.get("resale_mid") or item.get("resale_value") or 0)
    profit = float(item.get("profit_mid") or item.get("estimated_profit") or 0)
    age_minutes = item.get("item_age_minutes")
    if isinstance(age_minutes, (int, float)):
        if age_minutes < 1:
            age = "just now"
        elif age_minutes < 60:
            age = f"{int(age_minutes)}m ago"
        elif age_minutes < 24 * 60:
            age = f"{int(age_minutes // 60)}h ago"
        else:
            age = f"{int(age_minutes // (24 * 60))}d ago"
    else:
        age = str(item.get("item_age_label") or item.get("listing_age_label") or item.get("freshness_label") or "Just listed")
    issue = str(
        item.get("principal_damage")
        or item.get("damage_summary")
        or next((flag for flag in (item.get("positive_flags") or []) if flag in {
            "cracked_screen", "back_glass_cracked", "bad_battery", "screen_display_issue",
            "charging_port_issue", "camera_lens_cracked",
        }), "")
        or "repair opportunity"
    ).replace("_", " ").strip().capitalize()
    tier_label = {"GEM": "GEM", "PROFITABLE": "PROFITABLE", "REVIEW": "REVIEW"}.get(str(tier).upper(), str(tier).upper())
    profit_label = f"+${profit:,.0f}" if profit >= 0 else f"-${abs(profit):,.0f}"
    title = f"{model} · ${total:,.0f} · {profit_label} projected"
    body_parts = [tier_label, issue]
    if repair > 0:
        body_parts.append(f"repair ${repair:,.0f}")
    if resale > 0:
        body_parts.append(f"resale ${resale:,.0f}")
    body_parts.append(age)
    return {
        "title": title[:120],
        "body": " · ".join(body_parts)[:240],
        "item_id": item_id,
        "url": f"/?item={quote(item_id, safe='')}",
        "tag": f"notifierr-{item_id or tier.lower()}",
        "tier": tier,
    }


def send_push_to_user(
    storage: Any,
    settings: Settings,
    *,
    user_id: int,
    item: dict[str, Any],
    tier: str,
    scan_cycle_id: int | None = None,
    test: bool = False,
    sender: Callable[..., Any] | None = None,
) -> PushResult:
    """Send one notice to every enabled device and retain per-device evidence."""
    notification_settings = storage.get_user_notification_settings(user_id) or {}
    if not bool(notification_settings.get("push_enabled", True)):
        return PushResult()
    if not settings.push_configured or not settings.app_encryption_key:
        return PushResult()
    subscriptions = storage.list_push_subscriptions(user_id, enabled_only=True)
    if not subscriptions:
        return PushResult()

    if sender is None:
        from pywebpush import webpush

        sender = webpush

    payload = json.dumps(push_payload(item, tier=tier, test=test), separators=(",", ":"))
    private_key = _vapid_private_key(settings)
    accepted = failed = disabled = 0
    for subscription in subscriptions:
        subscription_id = int(subscription["id"])
        item_id = None if test else str(item.get("item_id") or "") or None
        storage.record_push_delivery(
            user_id=user_id,
            subscription_id=subscription_id,
            item_id=item_id,
            scan_cycle_id=scan_cycle_id,
            notification_tier="TEST" if test else tier,
            status="selected",
        )
        try:
            endpoint = decrypt_secret(subscription["endpoint"], settings.app_encryption_key)
            p256dh = decrypt_secret(subscription["p256dh"], settings.app_encryption_key)
            auth = decrypt_secret(subscription["auth"], settings.app_encryption_key)
            storage.record_push_delivery(
                user_id=user_id,
                subscription_id=subscription_id,
                item_id=item_id,
                scan_cycle_id=scan_cycle_id,
                notification_tier="TEST" if test else tier,
                status="attempted",
            )
            response = sender(
                subscription_info={"endpoint": endpoint, "keys": {"p256dh": p256dh, "auth": auth}},
                data=payload,
                vapid_private_key=private_key,
                vapid_claims={"sub": settings.vapid_subject},
                ttl=300,
            )
            provider_status = int(getattr(response, "status_code", 201) or 201)
            storage.record_push_delivery(
                user_id=user_id,
                subscription_id=subscription_id,
                item_id=item_id,
                scan_cycle_id=scan_cycle_id,
                notification_tier="TEST" if test else tier,
                status="accepted",
                provider_status=provider_status,
            )
            accepted += 1
        except Exception as exc:
            response = getattr(exc, "response", None)
            status = getattr(response, "status_code", None)
            invalid = status in {404, 410}
            category = "subscription_invalid" if invalid else "provider_rejected" if status else "provider_exception"
            if isinstance(exc, SecretConfigurationError):
                category = "subscription_decryption_failed"
            storage.record_push_delivery(
                user_id=user_id,
                subscription_id=subscription_id,
                item_id=item_id,
                scan_cycle_id=scan_cycle_id,
                notification_tier="TEST" if test else tier,
                status="invalid" if invalid else "failed",
                provider_status=int(status) if isinstance(status, int) else None,
                error_category=category,
                error_message=type(exc).__name__,
            )
            if invalid:
                storage.disable_push_subscription(user_id, str(subscription["endpoint_hash"]), invalid=True)
                disabled += 1
            else:
                failed += 1
            logger.warning(
                "Web Push delivery failed user_id=%s subscription_id=%s category=%s provider_status=%s",
                user_id,
                subscription_id,
                category,
                status,
            )
    return PushResult(selected=len(subscriptions), accepted=accepted, failed=failed, disabled=disabled)
