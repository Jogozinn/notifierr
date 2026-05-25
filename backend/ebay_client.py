from __future__ import annotations

import base64
import logging
from typing import Any, Optional

import httpx

from .config import Settings
from .scorer import now_iso


logger = logging.getLogger(__name__)


class EbayClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._access_token: Optional[str] = None

    async def search(self, keywords: list[str], limit: int) -> list[dict[str, Any]]:
        if not self.settings.ebay_configured:
            raise RuntimeError("EBAY_CLIENT_ID and EBAY_CLIENT_SECRET are required to scan eBay")

        access_token = await self._get_access_token()
        headers = {
            "Authorization": f"Bearer {access_token}",
            "X-EBAY-C-MARKETPLACE-ID": self.settings.ebay_marketplace_id,
        }

        normalized: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        async with httpx.AsyncClient(timeout=20) as client:
            for keyword in keywords:
                logger.info("Searching eBay keyword=%s limit=%s", keyword, limit)
                response = await client.get(
                    f"{self.settings.ebay_api_base}/buy/browse/v1/item_summary/search",
                    headers=headers,
                    params={"q": keyword, "limit": limit, "sort": "newlyListed"},
                )
                response.raise_for_status()
                data = response.json()
                for item in data.get("itemSummaries", []):
                    item_id = item.get("itemId")
                    if not item_id or item_id in seen_ids:
                        continue
                    seen_ids.add(item_id)
                    details = {}
                    if self.settings.ebay_fetch_descriptions:
                        details = await self._fetch_details(client, headers, item_id)
                    normalized.append(normalize_item(item, details))
        return normalized

    async def _get_access_token(self) -> str:
        if self._access_token:
            return self._access_token

        credentials = f"{self.settings.ebay_client_id}:{self.settings.ebay_client_secret}".encode("utf-8")
        headers = {
            "Authorization": f"Basic {base64.b64encode(credentials).decode('ascii')}",
            "Content-Type": "application/x-www-form-urlencoded",
        }
        data = {
            "grant_type": "client_credentials",
            "scope": "https://api.ebay.com/oauth/api_scope",
        }
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(self.settings.ebay_oauth_url, headers=headers, data=data)
            response.raise_for_status()
            payload = response.json()
        self._access_token = payload["access_token"]
        return self._access_token

    async def _fetch_details(
        self,
        client: httpx.AsyncClient,
        headers: dict[str, str],
        item_id: str,
    ) -> dict[str, Any]:
        try:
            response = await client.get(
                f"{self.settings.ebay_api_base}/buy/browse/v1/item/{item_id}",
                headers=headers,
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPError as exc:
            logger.warning("Could not fetch eBay item details item_id=%s error=%s", item_id, exc)
            return {}


def normalize_item(summary: dict[str, Any], details: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    details = details or {}
    price = _money_value(summary.get("price"))
    shipping = _shipping_cost(summary)
    seller = summary.get("seller") or {}
    listing_origin_at = (
        summary.get("itemCreationDate")
        or summary.get("itemOriginDate")
        or details.get("itemCreationDate")
        or details.get("itemOriginDate")
        or now_iso()
    )

    return {
        "item_id": str(summary.get("itemId")),
        "title": summary.get("title") or "",
        "price": price,
        "shipping": shipping,
        "total_cost": round(price + shipping, 2),
        "condition": summary.get("condition"),
        "category": summary.get("categoryPath") or summary.get("categoryName"),
        "item_url": summary.get("itemWebUrl"),
        "image_url": (summary.get("image") or {}).get("imageUrl"),
        "seller_username": seller.get("username"),
        "seller_feedback_percentage": _safe_float(seller.get("feedbackPercentage")),
        "seller_feedback_score": _safe_int(seller.get("feedbackScore")),
        "raw_description": details.get("description"),
        "found_at": listing_origin_at,
        "item_origin_at": listing_origin_at,
        "raw_json": {"summary": summary, "details": details},
    }


def _money_value(value: Optional[dict[str, Any]]) -> float:
    if not value:
        return 0.0
    return round(_safe_float(value.get("value")) or 0.0, 2)


def _shipping_cost(summary: dict[str, Any]) -> float:
    options = summary.get("shippingOptions") or []
    costs = [
        _money_value(option.get("shippingCost"))
        for option in options
        if option.get("shippingCost")
    ]
    return min(costs) if costs else 0.0


def _safe_float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_int(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
