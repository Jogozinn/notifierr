from __future__ import annotations

import logging
from typing import Any, Optional

import httpx


logger = logging.getLogger(__name__)


class DiscordNotifier:
    def __init__(self, webhook_url: Optional[str]):
        self.webhook_url = webhook_url
        self.last_provider_status: int | None = None
        self.last_failure_category = ""
        self.last_error_message = ""

    def _record_failure(self, exc: httpx.HTTPError) -> None:
        response = getattr(exc, "response", None)
        self.last_provider_status = getattr(response, "status_code", None)
        if isinstance(exc, httpx.TimeoutException):
            self.last_failure_category = "provider_timeout"
        elif isinstance(exc, httpx.HTTPStatusError):
            self.last_failure_category = "provider_http_error"
        else:
            self.last_failure_category = "provider_network_error"
        self.last_error_message = type(exc).__name__

    async def send_message(self, content: str) -> bool:
        if not self.webhook_url:
            logger.info("Discord webhook not configured; skipping generic notification")
            return False
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                response = await client.post(self.webhook_url, json={"content": content[:1900]})
                self.last_provider_status = response.status_code
                response.raise_for_status()
        except httpx.HTTPError as exc:
            self._record_failure(exc)
            logger.warning(
                "Discord notification request failed category=%s provider_status=%s",
                self.last_failure_category,
                self.last_provider_status,
            )
            return False
        logger.info("Sent Discord notification")
        return True

    async def send_deal(self, item: dict[str, Any], *, content: str | None = None) -> bool:
        if not self.webhook_url:
            logger.info("Discord webhook not configured; skipping notification item_id=%s", item["item_id"])
            return False

        decision = item.get("alert_decision") or {}
        tier = str(item.get("alert_tier") or decision.get("tier") or "REVIEW").upper()
        embed = {
            "title": item["title"][:256],
            "url": item.get("item_url"),
            "color": {"GEM": 0x2ECC71, "PROFITABLE": 0x3498DB, "REVIEW": 0xF1C40F}.get(tier, 0x95A5A6),
            "fields": [
                {"name": "Alert tier", "value": tier, "inline": True},
                {"name": "Model / storage", "value": f"{item.get('model') or 'unknown'} / {item.get('storage_capacity') or 'unknown'}", "inline": True},
                {"name": "Freshness", "value": item.get("item_age_label") or "Age unknown", "inline": True},
                {
                    "name": "Price + shipping",
                    "value": f"{_money(item.get('price'))} + {_money(item.get('shipping'))} = {_money(item.get('total_cost'))}",
                    "inline": True,
                },
                {"name": "Score", "value": str(item.get("score")), "inline": True},
                {"name": "Described damage", "value": _field_list(_repair_flags(item.get("positive_flags"))), "inline": False},
                {"name": "Functionality evidence", "value": _field_list(_functionality_evidence(item)), "inline": False},
                {"name": "Carrier / activation evidence", "value": _activation_evidence(item), "inline": False},
                {"name": "Risk flags", "value": _field_list(item.get("risk_flags")), "inline": True},
                {"name": "Reject flags", "value": _field_list(item.get("hard_reject_flags")), "inline": True},
                {"name": "Classification flags", "value": _field_list(item.get("listing_classification_flags")), "inline": False},
                {
                    "name": "Whole-phone confidence",
                    "value": "passed" if item.get("whole_phone_confidence_passed") else "manual review needed",
                    "inline": True,
                },
                {
                    "name": "Manual-review reason",
                    "value": _truncate(item.get("manual_review_reason") or "none"),
                    "inline": False,
                },
                {
                    "name": "Estimated parts",
                    "value": _parts_value(item),
                    "inline": True,
                },
                {"name": _resale_field_name(item), "value": _resale_value(item), "inline": True},
                {
                    "name": "Conservative profit",
                    "value": _floor_profit_value(item),
                    "inline": True,
                },
                {
                    "name": "Expected profit",
                    "value": _profit_value(item),
                    "inline": True,
                },
                {"name": "Upside profit", "value": _money(item.get("profit_high")), "inline": True},
                {"name": "Expected ROI", "value": _roi_value(item, decision), "inline": True},
                {"name": "Confidence", "value": str(decision.get("confidence") or "unknown"), "inline": True},
                {
                    "name": "Parts pricing status",
                    "value": str(item.get("parts_pricing_status") or "fallback"),
                    "inline": True,
                },
                {
                    "name": "Parts pricing note",
                    "value": _truncate(item.get("parts_pricing_note") or "none"),
                    "inline": False,
                },
                {
                    "name": "Pricing warning",
                    "value": item.get("pricing_warning") or item.get("parts_pricing_label") or "Parts estimate not verified",
                    "inline": False,
                },
                {"name": "Missing / estimated", "value": _field_list(decision.get("soft_warnings")), "inline": False},
                {"name": "Why surfaced", "value": _field_list(decision.get("surfaced_reasons")), "inline": False},
            ],
        }
        if item.get("image_url"):
            embed["thumbnail"] = {"url": item["image_url"]}

        payload = {"content": (content or _notification_title(item))[:1900], "embeds": [_discord_safe_embed(embed)]}
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                response = await client.post(self.webhook_url, json=payload)
                self.last_provider_status = response.status_code
                response.raise_for_status()
        except httpx.HTTPError as exc:
            self._record_failure(exc)
            logger.warning(
                "Discord alert request failed item_id=%s category=%s provider_status=%s",
                item["item_id"],
                self.last_failure_category,
                self.last_provider_status,
            )
            return False
        logger.info("Sent Discord alert item_id=%s", item["item_id"])
        return True


def _field_list(values: Optional[list[str]]) -> str:
    if not values:
        return "none"
    return ", ".join(value.replace("_", " ") for value in values)[:1024]


def _discord_safe_embed(embed: dict[str, Any]) -> dict[str, Any]:
    """Stay comfortably below Discord's 25-field and 6,000-character embed limits."""
    safe = {**embed, "title": str(embed.get("title") or "")[:256]}
    fields = []
    budget = 5200 - len(safe["title"])
    for field in list(embed.get("fields") or [])[:20]:
        name = str(field.get("name") or "")[:256]
        value = str(field.get("value") or "none")[:600]
        cost = len(name) + len(value)
        if cost > budget:
            break
        fields.append({**field, "name": name, "value": value})
        budget -= cost
    safe["fields"] = fields
    return safe


def _money(value: Any) -> str:
    return f"${float(value or 0):.2f}"


def _parts_value(item: dict[str, Any]) -> str:
    if not item.get("estimated_parts_cost_available", True):
        return "Estimated profit unavailable — part price missing or issue unknown"
    return _money(item.get("estimated_parts_cost"))


def _profit_value(item: dict[str, Any]) -> str:
    if not item.get("estimated_profit_available", True):
        return item.get("pricing_warning") or "Estimated profit unavailable — resale value missing"
    if item.get("parts_pricing_label") == "Parts estimate not verified":
        return f"Rough profit — parts estimate not verified: {_money(item.get('profit_mid') or item.get('estimated_profit'))}"
    return _money(item.get("profit_mid") or item.get("estimated_profit"))


def _resale_field_name(item: dict[str, Any]) -> str:
    if item.get("resale_storage_used"):
        return f"Expected resale ({item.get('resale_storage_used')})"
    return "Expected resale"


def _resale_value(item: dict[str, Any]) -> str:
    resale = item.get("resale_mid") or item.get("resale_value")
    if float(resale or 0) <= 0:
        return "Resale value missing"
    return _money(resale)


def _floor_profit_value(item: dict[str, Any]) -> str:
    if not item.get("estimated_profit_available", True):
        return item.get("pricing_warning") or "Estimated profit unavailable — resale value missing"
    prefix = "Rough floor — parts estimate not verified: " if item.get("parts_pricing_label") == "Parts estimate not verified" else ""
    return f"{prefix}{_money(item.get('profit_low') or item.get('estimated_profit'))}"


def _truncate(value: str) -> str:
    return value[:1024]


def _notification_title(item: dict[str, Any]) -> str:
    if item.get("alert_tier"):
        return f"Notifierr {str(item['alert_tier']).upper()} opportunity"
    if item.get("manual_review_needed") or not item.get("whole_phone_confidence_passed"):
        return "Manual-review iPhone listing"
    if item.get("fresh_for_alert", True):
        return "Fresh Best Find"
    return "High-quality broken iPhone candidate"


def _repair_flags(values: Optional[list[str]]) -> list[str]:
    repair = {
        "cracked_screen", "screen_display_issue", "bad_oled", "bad_battery",
        "back_glass_cracked", "camera_lens_cracked", "charging_port_issue",
        "camera_fault", "face_id_issue", "digitizer_issue",
    }
    return [value for value in (values or []) if value in repair]


def _functionality_evidence(item: dict[str, Any]) -> list[str]:
    positive = item.get("positive_flags") or []
    return [value for value in positive if value in {"powers_on", "face_id_works", "clean_imei"}]


def _activation_evidence(item: dict[str, Any]) -> str:
    positive = set(item.get("positive_flags") or [])
    carrier = "unlocked" if "unlocked" in positive else "carrier unknown"
    activation = "clean IMEI/ESN stated" if "clean_imei" in positive else "IMEI/iCloud not verified"
    return f"{carrier}; {activation}"


def _roi_value(item: dict[str, Any], decision: dict[str, Any]) -> str:
    roi = decision.get("expected_roi")
    if roi is None:
        acquisition = float(item.get("total_cost") or 0) + float(item.get("estimated_parts_cost") or 0)
        roi = float(item.get("profit_mid") or 0) / acquisition if acquisition > 0 else 0
    return f"{float(roi) * 100:.1f}%"
