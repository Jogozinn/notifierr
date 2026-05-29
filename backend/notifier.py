from __future__ import annotations

import logging
from typing import Any, Optional

import httpx


logger = logging.getLogger(__name__)


class DiscordNotifier:
    def __init__(self, webhook_url: Optional[str]):
        self.webhook_url = webhook_url

    async def send_message(self, content: str) -> bool:
        if not self.webhook_url:
            logger.info("Discord webhook not configured; skipping generic notification")
            return False
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                response = await client.post(self.webhook_url, json={"content": content[:1900]})
                response.raise_for_status()
        except httpx.HTTPError:
            logger.warning("Discord notification request failed")
            return False
        logger.info("Sent Discord notification")
        return True

    async def send_deal(self, item: dict[str, Any], *, content: str | None = None) -> bool:
        if not self.webhook_url:
            logger.info("Discord webhook not configured; skipping notification item_id=%s", item["item_id"])
            return False

        embed = {
            "title": item["title"][:256],
            "url": item.get("item_url"),
            "color": 0x2ECC71,
            "fields": [
                {"name": "Model", "value": item.get("model") or "unknown", "inline": True},
                {"name": "Freshness", "value": item.get("item_age_label") or "Age unknown", "inline": True},
                {
                    "name": "Price + shipping",
                    "value": f"{_money(item.get('price'))} + {_money(item.get('shipping'))} = {_money(item.get('total_cost'))}",
                    "inline": True,
                },
                {"name": "Score", "value": str(item.get("score")), "inline": True},
                {"name": "Issue summary", "value": _field_list(item.get("positive_flags")), "inline": False},
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
                    "name": "Floor profit",
                    "value": _floor_profit_value(item),
                    "inline": True,
                },
                {
                    "name": "Expected profit",
                    "value": _profit_value(item),
                    "inline": True,
                },
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
                {"name": "eBay link", "value": item.get("item_url") or "missing", "inline": False},
            ],
        }
        if item.get("image_url"):
            embed["thumbnail"] = {"url": item["image_url"]}

        payload = {"content": content or _notification_title(item), "embeds": [embed]}
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(self.webhook_url, json=payload)
            response.raise_for_status()
        logger.info("Sent Discord alert item_id=%s", item["item_id"])
        return True


def _field_list(values: Optional[list[str]]) -> str:
    if not values:
        return "none"
    return ", ".join(value.replace("_", " ") for value in values)[:1024]


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
    if item.get("manual_review_needed") or not item.get("whole_phone_confidence_passed"):
        return "Manual-review iPhone listing"
    if item.get("fresh_for_alert", True):
        return "Fresh Best Find"
    return "High-quality broken iPhone candidate"
