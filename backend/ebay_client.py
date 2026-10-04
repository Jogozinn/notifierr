from __future__ import annotations

import base64
from email.utils import parsedate_to_datetime
import html
import logging
import re
from datetime import datetime, timezone
from typing import Any, Optional

import httpx

from .config import Settings
from .scorer import now_iso


logger = logging.getLogger(__name__)
EBAY_HTTP_TIMEOUT_SECONDS = 20


class EbayRateLimitError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        retry_after_seconds: int,
        source: str = "ebay",
        http_status: int = 429,
        keyword: str | None = None,
        item_id: str | None = None,
    ):
        super().__init__(message)
        self.retry_after_seconds = max(1, int(retry_after_seconds))
        self.source = source
        self.http_status = int(http_status)
        self.keyword = keyword
        self.item_id = item_id


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
        async with self._http_client() as client:
            for keyword in keywords:
                logger.info("Searching eBay keyword=%s limit=%s", keyword, limit)
                response = await client.get(
                    f"{self.settings.ebay_api_base}/buy/browse/v1/item_summary/search",
                    headers=headers,
                    params={"q": keyword, "limit": limit, "sort": "newlyListed"},
                )
                if response.status_code == 429:
                    retry_after = _retry_after_seconds(
                        response,
                        default_seconds=self.settings.ebay_rate_limit_backoff_seconds,
                    )
                    logger.warning(
                        "eBay search rate limited keyword=%s retry_after_seconds=%s",
                        keyword,
                        retry_after,
                    )
                    raise EbayRateLimitError(
                        f"eBay rate limit exceeded; retry after {retry_after} seconds",
                        retry_after_seconds=retry_after,
                        keyword=keyword,
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
        async with self._http_client() as client:
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
            if response.status_code == 429:
                retry_after = _retry_after_seconds(
                    response,
                    default_seconds=self.settings.ebay_rate_limit_backoff_seconds,
                )
                logger.warning("eBay item detail rate limited item_id=%s retry_after_seconds=%s", item_id, retry_after)
                raise EbayRateLimitError(
                    f"eBay rate limit exceeded while fetching item details; retry after {retry_after} seconds",
                    retry_after_seconds=retry_after,
                    item_id=item_id,
                )
            response.raise_for_status()
            return response.json()
        except EbayRateLimitError:
            raise
        except httpx.HTTPError as exc:
            logger.warning("Could not fetch eBay item details item_id=%s error=%s", item_id, exc)
            return {}

    async def fetch_item_detail(self, item_id: str) -> dict[str, Any]:
        if not self.settings.ebay_configured:
            raise RuntimeError("EBAY_CLIENT_ID and EBAY_CLIENT_SECRET are required to fetch eBay item details")

        access_token = await self._get_access_token()
        headers = {
            "Authorization": f"Bearer {access_token}",
            "X-EBAY-C-MARKETPLACE-ID": self.settings.ebay_marketplace_id,
        }
        async with self._http_client() as client:
            details = await self._fetch_details(client, headers, item_id)
        if not details:
            return {}
        return normalize_item({"itemId": item_id}, details)

    def _http_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=EBAY_HTTP_TIMEOUT_SECONDS, trust_env=False)


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
    availability_status = _availability_status(summary, details)
    buying_option_summary = _buying_option_summary(summary, details)

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
        "raw_description": clean_description(details.get("description")),
        "availability_status": availability_status,
        "buying_option_summary": buying_option_summary,
        "item_end_at": summary.get("itemEndDate") or details.get("itemEndDate"),
        "last_availability_checked_at": now_iso() if summary or details else None,
        "availability_note": _availability_note(availability_status, buying_option_summary, summary, details),
        "found_at": listing_origin_at,
        "item_origin_at": listing_origin_at,
        "raw_json": {"summary": summary, "details": details},
    }


def _retry_after_seconds(response: httpx.Response, *, default_seconds: int) -> int:
    raw = (response.headers.get("Retry-After") or "").strip()
    if raw:
        try:
            return max(1, int(float(raw)))
        except ValueError:
            try:
                retry_at = parsedate_to_datetime(raw)
            except (TypeError, ValueError):
                retry_at = None
            if retry_at:
                if retry_at.tzinfo is None:
                    retry_at = retry_at.replace(tzinfo=timezone.utc)
                delta = retry_at.astimezone(timezone.utc) - datetime.now(timezone.utc)
                return max(1, int(delta.total_seconds()))
    return max(1, int(default_seconds))


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


def clean_description(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    if not text.strip():
        return None
    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", text)
    text = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</li>|</tr>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = text.replace("\xa0", " ")
    text = re.sub(r"\r\n?", "\n", text)
    text = re.sub(r"[ \t\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    lines = [_clean_boilerplate_line(line.strip()) for line in text.split("\n")]
    lines = [line for line in lines if line]
    cleaned = "\n".join(lines).strip()
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned or None


def _clean_boilerplate_line(line: str) -> str:
    boilerplate = (
        "powered by",
        "supreme widgets",
        "ebay template",
        "thanks for looking",
        "please see my other items",
    )
    lowered = line.lower()
    if any(phrase in lowered for phrase in boilerplate):
        return ""
    return line


def _availability_status(summary: dict[str, Any], details: dict[str, Any]) -> str:
    end_at = _parse_time(summary.get("itemEndDate") or details.get("itemEndDate"))
    if end_at and end_at <= datetime.now(timezone.utc):
        return "ended"
    text_parts = [
        summary.get("itemAffiliateWebUrl"),
        summary.get("itemWebUrl"),
        summary.get("itemLocation"),
        summary.get("itemEndDate"),
        details.get("itemEndDate"),
    ]
    for source in (summary, details):
        for key in ("itemStatus", "availability", "availabilityStatus", "legacyItemStatus"):
            value = source.get(key)
            if value:
                text_parts.append(value)
        for availability in source.get("estimatedAvailabilities") or []:
            text_parts.extend(str(value) for value in availability.values() if value is not None)
    text = " ".join(str(part) for part in text_parts if part).lower()
    if any(word in text for word in ("sold", "out_of_stock", "out of stock")):
        return "sold"
    if any(word in text for word in ("ended", "expired", "completed")):
        return "ended"
    if any(word in text for word in ("unavailable", "not available")):
        return "unavailable"
    if text:
        return "active"
    return "unknown"


def _parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _buying_option_summary(summary: dict[str, Any], details: dict[str, Any]) -> str:
    options = []
    for source in (summary, details):
        raw_options = source.get("buyingOptions") or []
        if isinstance(raw_options, str):
            raw_options = [raw_options]
        options.extend(str(option).upper() for option in raw_options)
    has_auction = any("AUCTION" in option for option in options)
    has_bin = any(option in {"FIXED_PRICE", "BUY_IT_NOW"} or "FIXED" in option or "BUY_IT_NOW" in option for option in options)
    has_offer = any("BEST_OFFER" in option or "OFFER" in option for option in options)
    if has_auction and has_bin:
        return "auction_and_buy_it_now"
    if has_auction:
        return "auction"
    if has_bin:
        return "best_offer" if has_offer else "buy_it_now"
    if has_offer:
        return "best_offer"
    return "unknown"


def _availability_note(
    availability_status: str,
    buying_option_summary: str,
    summary: dict[str, Any],
    details: dict[str, Any],
) -> str:
    notes = []
    if availability_status != "unknown":
        notes.append(f"Availability: {availability_status}")
    if buying_option_summary != "unknown":
        notes.append(f"Buying option: {buying_option_summary.replace('_', ' ')}")
    end_at = summary.get("itemEndDate") or details.get("itemEndDate")
    if end_at:
        notes.append(f"Ends: {end_at}")
    return "; ".join(notes)
