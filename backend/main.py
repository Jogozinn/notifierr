from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, time, timezone
from typing import Any, Optional
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from .config import Settings, load_repair_values, load_scoring_rules, load_settings
from .ebay_client import EbayClient
from .notifier import DiscordNotifier
from .scorer import score_listing
from .storage import Storage


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)

settings: Settings = load_settings()
repair_values: dict[str, Any] = load_repair_values(settings.repair_values_path)
scoring_rules: dict[str, Any] = load_scoring_rules(settings.scoring_rules_path)
storage = Storage(settings.sqlite_path)
_scan_lock = asyncio.Lock()


class ScanRequest(BaseModel):
    keywords: Optional[list[str]] = None
    limit: Optional[int] = Field(default=None, ge=1, le=100)
    notify: bool = True


class NoteRequest(BaseModel):
    note: str = Field(default="", max_length=1000)


class IgnoreRequest(BaseModel):
    reason: str = Field(default="", max_length=300)


class IgnoredKeywordRequest(BaseModel):
    keyword: str = Field(min_length=1, max_length=120)
    reason: str = Field(default="", max_length=300)


@asynccontextmanager
async def lifespan(app: FastAPI):
    task: Optional[asyncio.Task] = None
    if settings.background_poll_enabled:
        task = asyncio.create_task(_background_poll())
        logger.info("Started background poll loop every %s seconds", settings.background_poll_seconds)
    try:
        yield
    finally:
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass


app = FastAPI(title="Notifierr", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict[str, Any]:
    return {"ok": True, "ebay_configured": settings.ebay_configured, "discord_configured": settings.discord_configured}


@app.post("/scan/run")
async def run_scan(request: Optional[ScanRequest] = None) -> dict[str, Any]:
    request = request or ScanRequest()
    return await scan_once(
        keywords=request.keywords or settings.search_keywords,
        limit=request.limit or settings.max_results_per_keyword,
        notify=request.notify,
    )


@app.get("/items")
def list_items(
    status: Optional[str] = None,
    user_status: Optional[str] = None,
    limit: int = Query(default=100, ge=1, le=500),
    include_ignored: bool = False,
    include_stale: bool = False,
) -> list[dict[str, Any]]:
    return storage.list_items(
        status=status,
        user_status=user_status,
        limit=limit,
        include_ignored=include_ignored,
        include_stale=include_stale,
        max_alert_item_age_minutes=settings.max_alert_item_age_minutes,
        max_priority_review_item_age_hours=settings.max_priority_review_item_age_hours,
        max_active_queue_item_age_hours=settings.max_active_queue_item_age_hours,
    )


@app.get("/stats")
def stats() -> dict[str, Any]:
    return storage.stats(
        max_alert_item_age_minutes=settings.max_alert_item_age_minutes,
        max_priority_review_item_age_hours=settings.max_priority_review_item_age_hours,
        max_active_queue_item_age_hours=settings.max_active_queue_item_age_hours,
    )


@app.get("/config")
def config() -> dict[str, Any]:
    return {
        "settings": settings.public_dict(),
        "repair_values": repair_values,
        "scoring_rules": scoring_rules,
    }


@app.post("/config/reload")
def reload_config() -> dict[str, Any]:
    global settings, repair_values, scoring_rules, storage
    settings = load_settings()
    repair_values = load_repair_values(settings.repair_values_path)
    scoring_rules = load_scoring_rules(settings.scoring_rules_path)
    storage = Storage(settings.sqlite_path)
    logger.info("Reloaded config")
    return config()


@app.post("/items/{item_id}/review")
def review_item(item_id: str) -> dict[str, Any]:
    return _set_item_status(item_id, "reviewed")


@app.post("/items/{item_id}/watch")
def watch_item(item_id: str) -> dict[str, Any]:
    return _set_item_status(item_id, "watched")


@app.post("/items/{item_id}/ignore")
def ignore_item(item_id: str, request: Optional[IgnoreRequest] = None) -> dict[str, Any]:
    request = request or IgnoreRequest()
    return _set_item_status(item_id, "ignored", ignored_reason=request.reason or "Ignored item")


@app.post("/items/{item_id}/promote")
async def promote_item(item_id: str) -> dict[str, Any]:
    item = storage.get_item(item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    already_promoted = bool(item.get("promoted_at"))
    updated = _set_item_status(item_id, "promoted")
    discord_sent = False
    if settings.discord_webhook_url and not already_promoted:
        notifier = DiscordNotifier(settings.discord_webhook_url)
        discord_sent = await notifier.send_deal(
            updated,
            content="Manually promoted iPhone listing",
        )
    updated["discord_sent"] = discord_sent
    updated["already_promoted"] = already_promoted
    return updated


@app.post("/items/{item_id}/note")
def note_item(item_id: str, request: NoteRequest) -> dict[str, Any]:
    try:
        return storage.set_note(item_id, request.note)
    except KeyError:
        raise HTTPException(status_code=404, detail="Item not found") from None


@app.post("/items/{item_id}/ignore-seller")
def ignore_item_seller(item_id: str, request: Optional[IgnoreRequest] = None) -> dict[str, Any]:
    request = request or IgnoreRequest()
    try:
        return storage.ignore_seller_from_item(item_id, reason=request.reason or "Ignored seller")
    except KeyError:
        raise HTTPException(status_code=404, detail="Item not found") from None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None


@app.get("/ignored-sellers")
def ignored_sellers() -> list[dict[str, Any]]:
    return storage.list_ignored_sellers()


@app.get("/ignored-keywords")
def ignored_keywords() -> list[dict[str, Any]]:
    return storage.list_ignored_keywords()


@app.post("/ignored-keywords")
def add_ignored_keyword(request: IgnoredKeywordRequest) -> dict[str, Any]:
    try:
        storage.add_ignored_keyword(request.keyword, reason=request.reason or "Ignored keyword")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return {"ok": True, "keyword": request.keyword}


async def scan_once(keywords: list[str], limit: int, notify: bool) -> dict[str, Any]:
    if _scan_lock.locked():
        logger.info("Scan skipped because another scan is already running")
        return {
            "skipped": True,
            "reason": "scan_already_running",
            "scanned": 0,
            "new_items_found": 0,
            "fresh_items_found": 0,
            "stale_items_seen": 0,
            "best_finds": 0,
            "priority_review": 0,
            "rejected": 0,
            "alerts_sent": 0,
            "alerted": 0,
            "duplicates_skipped": 0,
            "keywords": keywords,
        }
    async with _scan_lock:
        return await _scan_once_unlocked(keywords, limit, notify)


async def _scan_once_unlocked(keywords: list[str], limit: int, notify: bool) -> dict[str, Any]:
    if not settings.ebay_configured:
        raise HTTPException(status_code=400, detail="Configure EBAY_CLIENT_ID and EBAY_CLIENT_SECRET before scanning")

    ebay = EbayClient(settings)
    notifier = DiscordNotifier(settings.discord_webhook_url)
    listings = await ebay.search(keywords, limit)

    scanned = 0
    new_items_found = 0
    fresh_items_found = 0
    stale_items_seen = 0
    best_finds = 0
    priority_review = 0
    candidates = 0
    rejected = 0
    alerts_sent = 0
    duplicates_skipped = 0

    for listing in listings:
        scanned += 1
        already_seen = storage.item_exists(str(listing.get("item_id")))
        if already_seen:
            duplicates_skipped += 1
        else:
            new_items_found += 1
        result = score_listing(
            listing,
            repair_values,
            scoring_rules=scoring_rules,
            min_score_to_alert=settings.min_score_to_alert,
            min_profit_to_alert=settings.min_profit_to_alert,
            risky_score_range=settings.risky_score_range,
        )
        item = {**listing, **result.as_item_fields()}
        ignored = storage.ignored_match(listing)
        if ignored:
            item.update(ignored)
        storage.upsert_item(item)
        stored_item = storage.get_item(item["item_id"]) or item

        if stored_item.get("fresh_for_active_queue"):
            fresh_items_found += 1
        if stored_item.get("stale"):
            stale_items_seen += 1

        if result.status == "rejected":
            rejected += 1
            continue
        if result.status == "candidate":
            candidates += 1
        if _is_fresh_best_find(stored_item, result, settings):
            best_finds += 1
        elif _is_priority_review_candidate(stored_item):
            priority_review += 1

        should_alert = (
            notify
            and result.status == "candidate"
            and should_notify_item(stored_item, result, settings)
            and not result.hard_reject_flags
            and stored_item.get("user_status") != "ignored"
            and not storage.was_alerted(stored_item["item_id"])
        )
        if should_alert and await notifier.send_deal(stored_item):
            storage.mark_alerted(stored_item["item_id"])
            alerts_sent += 1

    logger.info(
        "Scan finished scanned=%s new=%s fresh=%s stale=%s best_finds=%s priority_review=%s rejected=%s alerts_sent=%s duplicates=%s",
        scanned,
        new_items_found,
        fresh_items_found,
        stale_items_seen,
        best_finds,
        priority_review,
        rejected,
        alerts_sent,
        duplicates_skipped,
    )
    return {
        "scanned": scanned,
        "new_items_found": new_items_found,
        "fresh_items_found": fresh_items_found,
        "stale_items_seen": stale_items_seen,
        "best_finds": best_finds,
        "priority_review": priority_review,
        "candidates": candidates,
        "rejected": rejected,
        "alerts_sent": alerts_sent,
        "alerted": alerts_sent,
        "duplicates_skipped": duplicates_skipped,
        "keywords": keywords,
    }


async def _background_poll() -> None:
    while True:
        try:
            if _background_poll_is_active(settings):
                summary = await scan_once(settings.search_keywords, settings.max_results_per_keyword, notify=True)
                logger.info("Background scan summary %s", summary)
            else:
                logger.info("Background scan skipped outside active window")
        except Exception:
            logger.exception("Background scan failed")
        await asyncio.sleep(settings.background_poll_seconds)


def should_notify_item(item: dict[str, Any], result: Any, current_settings: Settings) -> bool:
    if item.get("status") != "candidate" or result.score < current_settings.min_score_to_alert:
        return False
    if item.get("stale"):
        return False
    if not item.get("fresh_for_alert", True):
        return False
    if item.get("item_age_minutes") is not None and item["item_age_minutes"] > getattr(current_settings, "max_alert_item_age_minutes", 180):
        return False
    if item.get("user_status") == "ignored":
        return False
    if result.hard_reject_flags:
        return False
    if not getattr(result, "whole_phone_confidence_passed", False):
        return False
    if not getattr(result, "has_repair_issue", False):
        return False
    if not getattr(result, "estimated_profit_available", True):
        return False
    if hasattr(result, "alert_eligible"):
        return bool(result.alert_eligible)
    return result.estimated_profit >= current_settings.min_profit_to_alert


def _is_fresh_best_find(item: dict[str, Any], result: Any, current_settings: Settings) -> bool:
    return should_notify_item(item, result, current_settings)


def _is_priority_review_candidate(item: dict[str, Any]) -> bool:
    if item.get("user_status") != "new" or item.get("status") == "rejected" or item.get("alert_eligible"):
        return False
    if not item.get("fresh_for_priority_review", True):
        return False
    if not item.get("whole_phone_confidence_passed") or not item.get("has_repair_issue"):
        return False
    if not item.get("model") or item.get("model") == "unknown":
        return False
    flags = [*(item.get("hard_reject_flags") or []), *(item.get("listing_classification_flags") or [])]
    if any(flag.endswith("_not_phone") or flag in {"old_model_ignored", "lot_not_single_phone", "no_power", "does_not_turn_on"} for flag in flags):
        return False
    reason = item.get("manual_review_reason") or ""
    return (
        float(item.get("profit_mid") or item.get("estimated_profit") or 0) >= 37.5
        or float(item.get("profit_high") or 0) >= 75
        or "Too cheap without proof" in reason
        or (
            item.get("estimated_parts_cost_available") is False
            and float(item.get("resale_mid") or item.get("resale_value") or 0) > 0
        )
    )


def _background_poll_is_active(current_settings: Settings) -> bool:
    start = _parse_clock_time(current_settings.background_poll_active_start)
    end = _parse_clock_time(current_settings.background_poll_active_end)
    if not start or not end:
        return True
    tz = _background_poll_timezone(current_settings.background_poll_timezone)
    now_time = datetime.now(tz).time()
    if start <= end:
        return start <= now_time <= end
    return now_time >= start or now_time <= end


def _background_poll_timezone(timezone_name: str | None):
    timezone_name = timezone_name or "UTC"
    try:
        return ZoneInfo(timezone_name)
    except Exception as exc:
        logger.warning(
            "Invalid BACKGROUND_POLL_TIMEZONE=%s; using UTC. error=%s: %s",
            timezone_name,
            type(exc).__name__,
            exc,
        )
        return timezone.utc


def _parse_clock_time(value: Optional[str]) -> Optional[time]:
    if not value:
        return None
    try:
        hour, minute = value.split(":", 1)
        return time(hour=int(hour), minute=int(minute))
    except ValueError:
        logger.warning("Invalid background poll time value=%s; ignoring active window", value)
        return None


def _set_item_status(item_id: str, user_status: str, *, ignored_reason: str = "") -> dict[str, Any]:
    try:
        return storage.set_user_status(item_id, user_status, ignored_reason=ignored_reason)
    except KeyError:
        raise HTTPException(status_code=404, detail="Item not found") from None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
