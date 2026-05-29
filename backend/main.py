from __future__ import annotations

import asyncio
import copy
import inspect
import json
import logging
import secrets
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import InvalidTokenError
from pydantic import BaseModel, Field

from .auth import create_access_token, decode_access_token, hash_password, verify_password
from .config import Settings, load_repair_values, load_resale_research, load_scoring_rules, load_settings
from .db import create_storage
from .ebay_client import EbayClient
from .notifier import DiscordNotifier
from .scorer import score_listing
from .secrets import SecretConfigurationError, decrypt_secret, encrypt_secret, is_encrypted_secret
from .storage import USER_ROLES


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)

settings: Settings = load_settings()
repair_values: dict[str, Any] = load_repair_values(settings.repair_values_path)
resale_research: dict[str, Any] = load_resale_research(settings.resale_research_path)
scoring_rules: dict[str, Any] = load_scoring_rules(settings.scoring_rules_path)
storage = create_storage(settings)
_scan_lock = asyncio.Lock()
_bearer = HTTPBearer(auto_error=False)


class ScanRequest(BaseModel):
    keywords: Optional[list[str]] = None
    limit: Optional[int] = Field(default=None, ge=1, le=100)
    notify: bool = True


class NoteRequest(BaseModel):
    note: str = Field(default="", max_length=1000)


class IgnoreRequest(BaseModel):
    reason: str = Field(default="", max_length=300)


class PartCostRequest(BaseModel):
    part: str = Field(min_length=1, max_length=80)
    cost: float = Field(ge=0)
    note: str = Field(default="", max_length=500)
    source: str = Field(default="manual_dashboard", max_length=80)
    item_id: Optional[str] = Field(default=None, max_length=200)


class RepairOverrideRequest(BaseModel):
    model: str = Field(min_length=1, max_length=120)
    part: str = Field(min_length=1, max_length=40)
    cost: float = Field(ge=0)
    source: str = Field(default="user_override", max_length=80)
    note: str = Field(default="", max_length=500)


class ResaleOverrideRequest(BaseModel):
    model: str = Field(min_length=1, max_length=120)
    storage_capacity: str = Field(min_length=1, max_length=20)
    condition: str = Field(min_length=1, max_length=20)
    low: float = Field(ge=0)
    mid: float = Field(ge=0)
    high: float = Field(ge=0)
    confidence: str = Field(default="", max_length=40)
    source: str = Field(default="user_override", max_length=80)
    note: str = Field(default="", max_length=500)


class ItemCorrectionRequest(BaseModel):
    corrected_model: Optional[str] = Field(default=None, max_length=120)
    corrected_storage_capacity: Optional[str] = Field(default=None, max_length=20)
    corrected_issue_type: Optional[str] = Field(default=None, max_length=40)
    corrected_part_cost: Optional[float] = Field(default=None, ge=0)
    note: str = Field(default="", max_length=1000)


class IgnoredKeywordRequest(BaseModel):
    keyword: str = Field(min_length=1, max_length=120)
    reason: str = Field(default="", max_length=300)


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=200)


class RegisterRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=8, max_length=200)
    display_name: str = Field(default="", max_length=120)
    invite_code: Optional[str] = Field(default=None, max_length=120)


class AdminCreateUserRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=8, max_length=200)
    role: str = Field(default="user", max_length=20)
    account_status: str = Field(default="active", max_length=20)
    display_name: str = Field(default="", max_length=120)
    plan_name: str = Field(default="", max_length=120)
    monthly_price: float = Field(default=0, ge=0)
    billing_status: str = Field(default="trial", max_length=80)
    paid_until: Optional[str] = Field(default=None, max_length=40)
    billing_note: str = Field(default="", max_length=500)


class AdminUpdateUserRequest(BaseModel):
    email: Optional[str] = Field(default=None, min_length=3, max_length=320)
    role: Optional[str] = Field(default=None, max_length=20)
    account_status: Optional[str] = Field(default=None, max_length=20)
    display_name: Optional[str] = Field(default=None, max_length=120)
    plan_name: Optional[str] = Field(default=None, max_length=120)
    monthly_price: Optional[float] = Field(default=None, ge=0)
    billing_status: Optional[str] = Field(default=None, max_length=80)
    paid_until: Optional[str] = Field(default=None, max_length=40)
    billing_note: Optional[str] = Field(default=None, max_length=500)


class AdminCreateInviteRequest(BaseModel):
    email: Optional[str] = Field(default=None, max_length=320)
    role: str = Field(default="user", max_length=20)
    expires_at: Optional[str] = Field(default=None, max_length=40)


class UserSettingsUpdateRequest(BaseModel):
    min_score_to_alert: float = Field(ge=0, le=100)
    min_profit_to_alert: float = Field(ge=0)
    risky_score_min: float = Field(ge=0, le=100)
    risky_score_max: float = Field(ge=0, le=100)
    max_alert_item_age_minutes: int = Field(ge=1, le=10080)
    max_priority_review_item_age_hours: int = Field(ge=1, le=720)
    max_active_queue_item_age_hours: int = Field(ge=1, le=720)
    default_resale_condition: str = Field(default="good", max_length=40)
    allow_mint_for_alerts: bool = False
    target_min_model_generation: int = Field(default=0, ge=0, le=30)
    background_poll_enabled: bool = False
    background_poll_seconds: int = Field(default=300, ge=0, le=86400)
    active_start: Optional[str] = Field(default=None, max_length=10)
    active_end: Optional[str] = Field(default=None, max_length=10)
    timezone: str = Field(default="America/New_York", max_length=80)


class UserNotificationSettingsUpdateRequest(BaseModel):
    discord_webhook: Optional[str] = Field(default=None, max_length=2000)
    clear_discord_webhook: bool = False
    discord_enabled: bool = False
    alerts_enabled: bool = True
    notify_best_finds: bool = True
    notify_priority_review: bool = True


class UserKeywordCreateRequest(BaseModel):
    keyword: str = Field(min_length=1, max_length=120)
    enabled: bool = True


class UserKeywordUpdateRequest(BaseModel):
    keyword: Optional[str] = Field(default=None, min_length=1, max_length=120)
    enabled: Optional[bool] = None


@dataclass(frozen=True)
class EffectiveUserSettings:
    user: dict[str, Any] | None
    min_score_to_alert: float
    min_profit_to_alert: float
    risky_score_min: float
    risky_score_max: float
    max_alert_item_age_minutes: int
    max_priority_review_item_age_hours: int
    max_active_queue_item_age_hours: int
    default_resale_condition: str
    allow_mint_for_alerts: bool
    target_min_model_generation: int
    background_poll_enabled: bool
    background_poll_seconds: int
    active_start: str | None
    active_end: str | None
    timezone: str
    keywords: list[str]
    notification_settings: dict[str, Any]

    @property
    def risky_score_range(self) -> tuple[float, float]:
        return (self.risky_score_min, self.risky_score_max)

    @property
    def background_poll_active_start(self) -> str | None:
        return self.active_start

    @property
    def background_poll_active_end(self) -> str | None:
        return self.active_end

    @property
    def background_poll_timezone(self) -> str:
        return self.timezone

    @property
    def resolved_discord_webhook_url(self) -> str:
        return str(self.notification_settings.get("_resolved_webhook_url") or "")

    @property
    def discord_enabled_for_alerts(self) -> bool:
        return bool(self.notification_settings.get("discord_enabled_effective"))

    @property
    def alerts_enabled(self) -> bool:
        return bool(self.notification_settings.get("alerts_enabled"))

    @property
    def notify_best_finds(self) -> bool:
        return bool(self.notification_settings.get("notify_best_finds"))

    def freshness_kwargs(self) -> dict[str, int]:
        return {
            "max_alert_item_age_minutes": self.max_alert_item_age_minutes,
            "max_priority_review_item_age_hours": self.max_priority_review_item_age_hours,
            "max_active_queue_item_age_hours": self.max_active_queue_item_age_hours,
        }


@dataclass
class SharedSearchPlanEntry:
    signature: str
    keyword: str
    limit: int
    sort: str
    marketplace: str
    marketplace_id: str
    subscribers: list[EffectiveUserSettings]


@dataclass(frozen=True)
class UserPricingContext:
    user_id: int
    repair_values: dict[str, Any]
    resale_research: dict[str, Any]
    repair_override_map: dict[tuple[str, str], dict[str, Any]]
    resale_override_map: dict[tuple[str, str, str], dict[str, Any]]


SUPPORTED_OVERRIDE_PARTS = {
    "screen_budget",
    "screen_safe",
    "screen_premium",
    "battery",
    "back_glass",
    "camera_lens",
    "charging_port",
}
SUPPORTED_RESALE_CONDITIONS = {"good", "mint"}
SUPPORTED_STORAGE_CAPACITIES = {"64GB", "128GB", "256GB", "512GB", "1TB"}
SUPPORTED_CORRECTION_ISSUE_TYPES = {
    "cracked_screen",
    "back_glass_cracked",
    "bad_battery",
    "camera_lens_cracked",
    "charging_port_issue",
    "screen_display_issue",
    "multiple_issues",
    "unknown",
}
SUPPORTED_BILLING_STATUSES = {"trial", "active", "past_due", "manual", "comped", ""}


def _public_user(user: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "id": user["id"],
        "email": user["email"],
        "role": user["role"],
        "account_status": user["account_status"],
        "display_name": user.get("display_name") or "",
        "plan_name": user.get("plan_name") or "",
        "monthly_price": float(user.get("monthly_price") or 0),
        "billing_status": user.get("billing_status") or "",
        "paid_until": user.get("paid_until"),
        "billing_note": user.get("billing_note") or "",
        "created_at": user.get("created_at"),
        "updated_at": user.get("updated_at"),
        "last_login_at": user.get("last_login_at"),
    }
    for key in (
        "has_settings",
        "has_notification_settings",
        "discord_configured",
        "keyword_count",
        "usage_summary",
        "billing_access_limited",
        "billing_access_reason",
    ):
        if key in user:
            payload[key] = user[key]
    return payload


def _public_invite(invite: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": invite["id"],
        "code": invite["code"],
        "email": invite.get("email"),
        "role": invite.get("role") or "user",
        "created_by_user_id": invite.get("created_by_user_id"),
        "used_by_user_id": invite.get("used_by_user_id"),
        "used_at": invite.get("used_at"),
        "expires_at": invite.get("expires_at"),
        "created_at": invite.get("created_at"),
        "revoked_at": invite.get("revoked_at"),
        "is_used": bool(invite.get("is_used")),
        "is_revoked": bool(invite.get("is_revoked")),
        "is_expired": bool(invite.get("is_expired")),
    }


def _settings_seed_from_current_settings() -> dict[str, Any]:
    return {
        "min_score_to_alert": settings.min_score_to_alert,
        "min_profit_to_alert": settings.min_profit_to_alert,
        "risky_score_range": settings.risky_score_range,
        "max_alert_item_age_minutes": settings.max_alert_item_age_minutes,
        "max_priority_review_item_age_hours": settings.max_priority_review_item_age_hours,
        "max_active_queue_item_age_hours": settings.max_active_queue_item_age_hours,
        "background_poll_enabled": settings.background_poll_enabled,
        "background_poll_seconds": settings.background_poll_seconds,
        "active_start": settings.background_poll_active_start,
        "active_end": settings.background_poll_active_end,
        "timezone": settings.background_poll_timezone,
    }


def _baseline_keywords_from_current_settings() -> list[str]:
    return list(settings.search_keywords)


def _ensure_user_defaults(user_id: int) -> None:
    storage.ensure_user_settings(
        user_id,
        settings_seed=_settings_seed_from_current_settings(),
        baseline_keywords=_baseline_keywords_from_current_settings(),
    )


def _local_settings_user() -> dict[str, Any]:
    user = storage.get_local_settings_user()
    if user:
        _ensure_user_defaults(int(user["id"]))
        refreshed = storage.get_user(int(user["id"]), include_password_hash=True)
        if refreshed:
            return refreshed
    fallback = storage.create_user(
        email="local@notifierr.local",
        password_hash=hash_password("local-development-only"),
        role="admin",
        account_status="active",
        display_name="Local User",
        settings_seed=_settings_seed_from_current_settings(),
        baseline_keywords=_baseline_keywords_from_current_settings(),
    )
    created = storage.get_user(int(fallback["id"]), include_password_hash=True)
    if not created:
        raise HTTPException(status_code=500, detail="Could not initialize local settings user")
    return created


def require_settings_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
) -> dict[str, Any]:
    if settings.auth_required:
        user = _get_current_user(credentials, require_token=True)
        if not user:
            raise HTTPException(status_code=401, detail="Authentication required")
        _ensure_user_defaults(int(user["id"]))
        refreshed = storage.get_user(int(user["id"]), include_password_hash=True)
        return refreshed or user
    user = _get_current_user(credentials, require_token=False, ignore_invalid_token=True)
    if user:
        _ensure_user_defaults(int(user["id"]))
        refreshed = storage.get_user(int(user["id"]), include_password_hash=True)
        return refreshed or user
    return _local_settings_user()


def _default_notification_settings_payload() -> dict[str, Any]:
    return {
        "discord_enabled": False,
        "alerts_enabled": True,
        "notify_best_finds": True,
        "notify_priority_review": True,
        "discord_webhook": "",
        "discord_webhook_configured": False,
        "_discord_webhook_raw": "",
        "created_at": None,
        "updated_at": None,
    }


def _parse_paid_until(value: str | None) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _billing_access_state(user: dict[str, Any] | None) -> tuple[bool, str]:
    if not user or user.get("role") == "admin" or not settings.auth_required:
        return (False, "")
    billing_status = str(user.get("billing_status") or "").strip().lower()
    if billing_status == "past_due":
        return (True, "Billing status is past_due")
    if billing_status == "comped":
        return (False, "")
    paid_until = _parse_paid_until(user.get("paid_until"))
    if paid_until and paid_until < datetime.now(timezone.utc).date():
        return (True, f"Paid access expired on {paid_until.isoformat()}")
    return (False, "")


def _user_with_access_summary(user: dict[str, Any]) -> dict[str, Any]:
    limited, reason = _billing_access_state(user)
    return {
        **user,
        "billing_access_limited": limited,
        "billing_access_reason": reason,
    }


def _warn_if_hosted_without_encryption() -> None:
    if settings.auth_required and not settings.app_encryption_key:
        logger.warning(
            "AUTH_REQUIRED=true but APP_ENCRYPTION_KEY is not configured; new Discord webhook saves will be rejected and legacy plaintext secrets remain unsafe until migrated"
        )


def _normalize_notification_settings(user_id: int, notifications: dict[str, Any] | None) -> dict[str, Any] | None:
    if not notifications:
        return None
    stored_value = str(notifications.get("_discord_webhook_raw") or notifications.get("discord_webhook") or "").strip()
    resolved_webhook = ""
    if stored_value:
        try:
            resolved_webhook = decrypt_secret(
                stored_value,
                settings.app_encryption_key,
                allow_plaintext_fallback=True,
            )
        except SecretConfigurationError as exc:
            logger.warning("Could not decrypt Discord webhook user_id=%s error=%s", user_id, exc)
            resolved_webhook = ""
        else:
            if not is_encrypted_secret(stored_value):
                if settings.app_encryption_key:
                    try:
                        encrypted_value = encrypt_secret(resolved_webhook, settings.app_encryption_key)
                        notifications = storage.update_user_notification_settings(user_id, {"discord_webhook": encrypted_value})
                    except Exception as exc:
                        logger.warning("Could not migrate plaintext Discord webhook to encrypted storage user_id=%s error=%s", user_id, exc)
                elif settings.auth_required:
                    logger.warning(
                        "Plaintext Discord webhook remains stored for user_id=%s because APP_ENCRYPTION_KEY is missing",
                        user_id,
                    )
    normalized = {**notifications}
    normalized["_resolved_webhook_raw"] = resolved_webhook
    normalized["discord_webhook_configured"] = bool(stored_value)
    return normalized


def _untouched_local_notification_defaults(data: dict[str, Any]) -> bool:
    return (
        not data.get("discord_webhook_configured")
        and not data.get("_discord_webhook_raw")
        and not bool(data.get("discord_enabled"))
        and bool(data.get("alerts_enabled", True))
        and bool(data.get("notify_best_finds", True))
        and bool(data.get("notify_priority_review", True))
        and data.get("created_at") is not None
        and data.get("created_at") == data.get("updated_at")
    )


def _resolve_notification_settings(
    data: dict[str, Any] | None,
    *,
    allow_global_webhook_fallback: bool,
) -> dict[str, Any]:
    resolved = {**_default_notification_settings_payload(), **(data or {})}
    raw_webhook = str(resolved.get("_resolved_webhook_raw") or resolved.get("_discord_webhook_raw") or "").strip()
    global_webhook = str(settings.discord_webhook_url or "").strip()
    use_global_fallback = bool(allow_global_webhook_fallback and not raw_webhook and global_webhook)
    resolved["_resolved_webhook_url"] = raw_webhook or (global_webhook if use_global_fallback else "")
    resolved["uses_global_webhook_fallback"] = use_global_fallback
    resolved["discord_enabled_effective"] = bool(resolved.get("discord_enabled")) or (
        use_global_fallback and (data is None or _untouched_local_notification_defaults(resolved))
    )
    return resolved


def _resolve_scan_keywords(user: dict[str, Any] | None) -> list[str]:
    if not user:
        return list(settings.search_keywords)
    keywords = storage.list_user_keywords(int(user["id"]))
    if not keywords:
        return list(settings.search_keywords)
    return [entry["keyword"] for entry in keywords if entry.get("enabled")]


def _resolve_effective_user_settings(
    user: dict[str, Any] | None = None,
    *,
    allow_local_fallback: bool = True,
    ensure_defaults: bool = True,
) -> EffectiveUserSettings:
    selected_user = user
    if not selected_user and allow_local_fallback:
        if settings.auth_required:
            selected_user = storage.get_local_settings_user()
        else:
            selected_user = _local_settings_user()
    if selected_user and ensure_defaults:
        _ensure_user_defaults(int(selected_user["id"]))
        refreshed = storage.get_user(int(selected_user["id"]), include_password_hash=True)
        if refreshed:
            selected_user = refreshed

    if selected_user:
        selected_user = _user_with_access_summary(selected_user)

    user_settings = storage.get_user_settings(int(selected_user["id"])) if selected_user else None
    notifications = (
        _normalize_notification_settings(
            int(selected_user["id"]),
            storage.get_user_notification_settings(int(selected_user["id"])),
        )
        if selected_user
        else None
    )
    resolved_notifications = _resolve_notification_settings(
        notifications,
        allow_global_webhook_fallback=not settings.auth_required,
    )
    keywords = _resolve_scan_keywords(selected_user)
    limited, reason = _billing_access_state(selected_user)
    if limited:
        user_settings = {**(user_settings or {}), "background_poll_enabled": False}
        resolved_notifications = {
            **resolved_notifications,
            "alerts_enabled": False,
            "discord_enabled_effective": False,
            "billing_access_reason": reason,
        }

    return EffectiveUserSettings(
        user=selected_user,
        min_score_to_alert=float((user_settings or {}).get("min_score_to_alert", settings.min_score_to_alert)),
        min_profit_to_alert=float((user_settings or {}).get("min_profit_to_alert", settings.min_profit_to_alert)),
        risky_score_min=float((user_settings or {}).get("risky_score_min", settings.risky_score_range[0])),
        risky_score_max=float((user_settings or {}).get("risky_score_max", settings.risky_score_range[1])),
        max_alert_item_age_minutes=int((user_settings or {}).get("max_alert_item_age_minutes", settings.max_alert_item_age_minutes)),
        max_priority_review_item_age_hours=int((user_settings or {}).get("max_priority_review_item_age_hours", settings.max_priority_review_item_age_hours)),
        max_active_queue_item_age_hours=int((user_settings or {}).get("max_active_queue_item_age_hours", settings.max_active_queue_item_age_hours)),
        default_resale_condition=str((user_settings or {}).get("default_resale_condition", "good") or "good"),
        allow_mint_for_alerts=bool((user_settings or {}).get("allow_mint_for_alerts", False)),
        target_min_model_generation=int((user_settings or {}).get("target_min_model_generation", 0) or 0),
        background_poll_enabled=bool((user_settings or {}).get("background_poll_enabled", settings.background_poll_enabled)),
        background_poll_seconds=int((user_settings or {}).get("background_poll_seconds", settings.background_poll_seconds)),
        active_start=(user_settings or {}).get("active_start", settings.background_poll_active_start),
        active_end=(user_settings or {}).get("active_end", settings.background_poll_active_end),
        timezone=str((user_settings or {}).get("timezone", settings.background_poll_timezone) or settings.background_poll_timezone),
        keywords=keywords,
        notification_settings=resolved_notifications,
    )


def _build_user_pricing_context(user_id: int) -> UserPricingContext:
    repair_override_rows = storage.list_user_repair_value_overrides(user_id)
    resale_override_rows = storage.list_user_resale_research_overrides(user_id)
    merged_repair_values = copy.deepcopy(repair_values)
    merged_resale_research = copy.deepcopy(resale_research)
    repair_override_map: dict[tuple[str, str], dict[str, Any]] = {}
    resale_override_map: dict[tuple[str, str, str], dict[str, Any]] = {}

    for row in repair_override_rows:
        model = str(row.get("model") or "").strip()
        part = str(row.get("part") or "").strip()
        if not model or not part:
            continue
        repair_override_map[(model, part)] = row
        estimate = merged_repair_values.setdefault(model, {})
        parts = estimate.setdefault("parts", {})
        parts[part] = float(row.get("cost") or 0)
        estimate["parts_pricing_status"] = "user_override"
        override_note = str(row.get("note") or "").strip()
        override_source = str(row.get("source") or "user_override").strip() or "user_override"
        estimate["parts_pricing_note"] = override_note or f"User override from {override_source}"

    for row in resale_override_rows:
        model = str(row.get("model") or "").strip()
        storage_capacity = str(row.get("storage_capacity") or "").strip()
        condition = str(row.get("condition") or "").strip().lower()
        if not model or not storage_capacity or not condition:
            continue
        resale_override_map[(model, storage_capacity, condition)] = row
        model_entry = merged_resale_research.setdefault(model, {})
        resale_by_storage = model_entry.setdefault("resale_by_storage", {})
        storage_entry = resale_by_storage.setdefault(storage_capacity, {})
        storage_entry[condition] = {
            "low": float(row.get("low") or 0),
            "mid": float(row.get("mid") or 0),
            "high": float(row.get("high") or 0),
        }
        if row.get("confidence"):
            storage_entry["confidence"] = str(row["confidence"])
        if row.get("note"):
            storage_entry["note"] = str(row["note"])
        if row.get("source"):
            storage_entry["source"] = str(row["source"])

    return UserPricingContext(
        user_id=user_id,
        repair_values=merged_repair_values,
        resale_research=merged_resale_research,
        repair_override_map=repair_override_map,
        resale_override_map=resale_override_map,
    )


def _pricing_context_for_user(user_id: int, cache: dict[int, UserPricingContext] | None = None) -> UserPricingContext:
    if cache is not None and user_id in cache:
        return cache[user_id]
    context = _build_user_pricing_context(user_id)
    if cache is not None:
        cache[user_id] = context
    return context


def _scoring_overrides_for_item(user_id: int, item_id: str) -> dict[str, Any]:
    correction = storage.get_user_item_correction(user_id, item_id) or {}
    forced_model = str(correction.get("corrected_model") or "").strip() or None
    forced_storage_capacity = str(correction.get("corrected_storage_capacity") or "").strip() or None
    forced_issue_type = str(correction.get("corrected_issue_type") or "").strip() or None
    forced_part_cost = correction.get("corrected_part_cost")
    return {
        "correction": correction,
        "forced_model": forced_model,
        "forced_storage_capacity": forced_storage_capacity,
        "forced_issue_type": forced_issue_type,
        "forced_part_cost": float(forced_part_cost) if forced_part_cost is not None else None,
    }


def _score_listing_for_user(
    listing: dict[str, Any],
    resolved: EffectiveUserSettings,
    *,
    pricing_context: UserPricingContext | None = None,
) -> tuple[Any, dict[str, Any]]:
    if not resolved.user:
        raise HTTPException(status_code=400, detail="No effective user is available for scoring")
    user_id = int(resolved.user["id"])
    context = pricing_context or _build_user_pricing_context(user_id)
    item_overrides = _scoring_overrides_for_item(user_id, str(listing.get("item_id") or ""))
    score_kwargs: dict[str, Any] = {
        "resale_research": context.resale_research,
        "scoring_rules": scoring_rules,
        "min_score_to_alert": resolved.min_score_to_alert,
        "min_profit_to_alert": resolved.min_profit_to_alert,
        "risky_score_range": resolved.risky_score_range,
    }
    override_kwargs = {
        "forced_model": item_overrides["forced_model"],
        "forced_storage_capacity": item_overrides["forced_storage_capacity"],
        "forced_issue_type": item_overrides["forced_issue_type"],
        "forced_part_cost": item_overrides["forced_part_cost"],
    }
    try:
        signature = inspect.signature(score_listing)
    except (TypeError, ValueError):
        signature = None
    if signature and not any(param.kind == inspect.Parameter.VAR_KEYWORD for param in signature.parameters.values()):
        supported = set(signature.parameters)
        for key, value in override_kwargs.items():
            if key in supported:
                score_kwargs[key] = value
    else:
        score_kwargs.update(override_kwargs)
    result = score_listing(
        listing,
        context.repair_values,
        **score_kwargs,
    )
    return result, item_overrides


def _effective_issue_type_from_flags(flags: list[str] | None) -> str:
    flag_set = set(flags or [])
    if "multiple_issues" in flag_set:
        return "multiple_issues"
    categories: list[str] = []
    if {"screen_display_issue", "bad_oled"} & flag_set:
        categories.append("screen_display_issue")
    elif "cracked_screen" in flag_set:
        categories.append("cracked_screen")
    if "back_glass_cracked" in flag_set:
        categories.append("back_glass_cracked")
    if "bad_battery" in flag_set:
        categories.append("bad_battery")
    if "camera_lens_cracked" in flag_set:
        categories.append("camera_lens_cracked")
    if "charging_port_issue" in flag_set:
        categories.append("charging_port_issue")
    unique_categories = list(dict.fromkeys(categories))
    if len(unique_categories) > 1:
        return "multiple_issues"
    if unique_categories:
        return unique_categories[0]
    return "unknown"


def _raw_detection_snapshot_for_item(
    item: dict[str, Any],
    *,
    pricing_context: UserPricingContext,
    correction: dict[str, Any] | None = None,
) -> dict[str, Any]:
    correction = correction or {}
    if not any(
        correction.get(key)
        for key in ("corrected_model", "corrected_storage_capacity", "corrected_issue_type", "corrected_part_cost", "note")
    ):
        return {
            "raw_detected_model": item.get("model") or "unknown",
            "raw_detected_storage_capacity": item.get("storage_capacity"),
            "raw_detected_issue_type": _effective_issue_type_from_flags(item.get("positive_flags") or []),
        }
    try:
        raw_result = score_listing(
            item,
            pricing_context.repair_values,
            **{
                key: value
                for key, value in {
                    "resale_research": pricing_context.resale_research,
                    "scoring_rules": scoring_rules,
                    "min_score_to_alert": settings.min_score_to_alert,
                    "min_profit_to_alert": settings.min_profit_to_alert,
                    "risky_score_range": settings.risky_score_range,
                }.items()
            },
        )
    except TypeError:
        raw_result = score_listing(
            item,
            pricing_context.repair_values,
            resale_research=pricing_context.resale_research,
            scoring_rules=scoring_rules,
            min_score_to_alert=settings.min_score_to_alert,
            min_profit_to_alert=settings.min_profit_to_alert,
            risky_score_range=settings.risky_score_range,
        )
    raw_fields = raw_result.as_item_fields()
    return {
        "raw_detected_model": raw_fields.get("model") or "unknown",
        "raw_detected_storage_capacity": raw_fields.get("storage_capacity"),
        "raw_detected_issue_type": _effective_issue_type_from_flags(raw_fields.get("positive_flags") or []),
    }


def _repair_snapshot_for_item(
    item: dict[str, Any],
    *,
    user_id: int,
    pricing_context: UserPricingContext | None = None,
    correction: dict[str, Any] | None = None,
) -> dict[str, Any]:
    context = pricing_context or _build_user_pricing_context(user_id)
    correction = correction or {}
    model = str(item.get("model") or correction.get("corrected_model") or "").strip()
    part = str(item.get("effective_part_key") or _suggested_part_key(item)).strip()
    if not model or not part or part not in SUPPORTED_OVERRIDE_PARTS:
        return {
            "effective_part_key": part,
            "baseline_part_cost": None,
            "user_override_part_cost": None,
            "effective_part_cost": item.get("estimated_parts_cost"),
            "effective_part_source": "missing",
        }
    baseline_cost = (
        (repair_values.get(model) or {}).get("parts", {}).get(part)
        if isinstance((repair_values.get(model) or {}).get("parts"), dict)
        else None
    )
    default_cost = (
        (repair_values.get("default") or {}).get("parts", {}).get(part)
        if isinstance((repair_values.get("default") or {}).get("parts"), dict)
        else None
    )
    baseline = baseline_cost if baseline_cost is not None else default_cost
    override_row = context.repair_override_map.get((model, part))
    override_cost = override_row.get("cost") if override_row else None
    corrected_cost = correction.get("corrected_part_cost")
    effective_source = "global_default"
    effective_cost = baseline
    if override_cost is not None:
        effective_source = "user_override"
        effective_cost = override_cost
    if corrected_cost is not None:
        effective_source = "item_correction"
        effective_cost = corrected_cost
    return {
        "effective_part_key": part,
        "baseline_part_cost": float(baseline) if baseline is not None else None,
        "user_override_part_cost": float(override_cost) if override_cost is not None else None,
        "effective_part_cost": float(effective_cost) if effective_cost is not None else None,
        "effective_part_source": effective_source,
    }


def _decorate_item_for_user(
    item: dict[str, Any],
    *,
    user_id: int,
    pricing_context: UserPricingContext | None = None,
) -> dict[str, Any]:
    correction = storage.get_user_item_correction(user_id, item["item_id"]) or {}
    context = pricing_context or _build_user_pricing_context(user_id)
    repair_snapshot = _repair_snapshot_for_item(
        item,
        user_id=user_id,
        pricing_context=context,
        correction=correction,
    )
    raw_snapshot = _raw_detection_snapshot_for_item(
        item,
        pricing_context=context,
        correction=correction,
    )
    corrected_issue_type = str(correction.get("corrected_issue_type") or "").strip() or None
    effective_issue_type = corrected_issue_type or raw_snapshot["raw_detected_issue_type"]
    return {
        **item,
        **repair_snapshot,
        **raw_snapshot,
        "effective_issue_type": effective_issue_type,
        "user_item_correction": correction,
    }


def _suggested_part_key(item: dict[str, Any]) -> str:
    flags = item.get("positive_flags") or []
    if any(flag in {"cracked_screen", "screen_display_issue", "bad_oled"} for flag in flags):
        return "screen_safe"
    if "bad_battery" in flags:
        return "battery"
    if "back_glass_cracked" in flags:
        return "back_glass"
    if "camera_lens_cracked" in flags:
        return "camera_lens"
    if "charging_port_issue" in flags:
        return "charging_port"
    return ""


def _normalize_keyword(keyword: str) -> str:
    return " ".join(str(keyword or "").strip().split()).lower()


def _search_signature(keyword: str, limit: int, *, sort: str = "newlyListed") -> str:
    return json.dumps(
        {
            "marketplace": "ebay",
            "marketplace_id": settings.ebay_marketplace_id,
            "query": _normalize_keyword(keyword),
            "limit": int(limit),
            "sort": sort,
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def _active_shared_scan_users(*, background_mode: bool) -> list[EffectiveUserSettings]:
    resolved_users: list[EffectiveUserSettings] = []
    for user in storage.list_active_users(include_password_hash=True):
        limited, reason = _billing_access_state(user)
        if limited:
            logger.info("Skipping shared scan user_id=%s because billing access is limited reason=%s", user["id"], reason)
            continue
        resolved = _resolve_effective_user_settings(
            user,
            allow_local_fallback=False,
            ensure_defaults=True,
        )
        if not resolved.user:
            continue
        if background_mode and not resolved.background_poll_enabled:
            continue
        if background_mode and not _background_poll_is_active(resolved):
            continue
        resolved_users.append(resolved)
    return resolved_users


def _build_shared_search_plan(
    resolved_users: list[EffectiveUserSettings],
    *,
    limit: int,
    keyword_filter: list[str] | None = None,
) -> list[SharedSearchPlanEntry]:
    normalized_filter = {_normalize_keyword(keyword) for keyword in (keyword_filter or []) if _normalize_keyword(keyword)}
    plan_by_signature: dict[str, SharedSearchPlanEntry] = {}
    for resolved in resolved_users:
        seen_for_user: set[str] = set()
        for raw_keyword in resolved.keywords:
            normalized = _normalize_keyword(raw_keyword)
            if not normalized or normalized in seen_for_user:
                continue
            if normalized_filter and normalized not in normalized_filter:
                continue
            seen_for_user.add(normalized)
            signature = _search_signature(raw_keyword, limit)
            entry = plan_by_signature.get(signature)
            if not entry:
                entry = SharedSearchPlanEntry(
                    signature=signature,
                    keyword=" ".join(str(raw_keyword or "").strip().split()),
                    limit=int(limit),
                    sort="newlyListed",
                    marketplace="ebay",
                    marketplace_id=settings.ebay_marketplace_id,
                    subscribers=[],
                )
                plan_by_signature[signature] = entry
            entry.subscribers.append(resolved)
    return list(plan_by_signature.values())


def _empty_scan_summary(
    *,
    keywords: list[str],
    reason: str | None = None,
    mode: str = "user",
) -> dict[str, Any]:
    payload = {
        "mode": mode,
        "scanned": 0,
        "new_items_found": 0,
        "fresh_items_found": 0,
        "stale_items_seen": 0,
        "best_finds": 0,
        "priority_review": 0,
        "candidates": 0,
        "rejected": 0,
        "alerts_sent": 0,
        "alerted": 0,
        "duplicates_skipped": 0,
        "unique_searches": 0,
        "unique_marketplace_items": 0,
        "active_users": 0,
        "keywords": keywords,
    }
    if reason:
        payload.update({"skipped": True, "reason": reason})
    return payload


def _usage_weight(*, search_signatures_subscribed: int, items_scored: int, alerts_sent: int, detail_refreshes: int, shared_api_calls_attributed: float) -> float:
    return round(
        float(search_signatures_subscribed)
        + (float(items_scored) * 0.1)
        + float(alerts_sent)
        + (float(detail_refreshes) * 0.25)
        + float(shared_api_calls_attributed),
        4,
    )


def _public_notification_settings(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "user_id": data["user_id"],
        "discord_enabled": bool(data.get("discord_enabled")),
        "alerts_enabled": bool(data.get("alerts_enabled")),
        "notify_best_finds": bool(data.get("notify_best_finds")),
        "notify_priority_review": bool(data.get("notify_priority_review")),
        "discord_webhook": "",
        "discord_webhook_configured": bool(data.get("discord_webhook_configured")),
        "created_at": data.get("created_at"),
        "updated_at": data.get("updated_at"),
    }


def _config_payload() -> dict[str, Any]:
    return {
        "settings": settings.public_dict(),
        "repair_values": repair_values,
        "resale_research": resale_research,
        "scoring_rules": scoring_rules,
    }


def _inactive_account_error(user: dict[str, Any]) -> HTTPException:
    status = user.get("account_status") or "disabled"
    detail = "Account is disabled" if status == "disabled" else "Account is not active"
    return HTTPException(status_code=403, detail=detail)


def _get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials],
    *,
    require_token: bool,
    ignore_invalid_token: bool = False,
) -> Optional[dict[str, Any]]:
    if credentials is None or not credentials.credentials:
        if require_token:
            raise HTTPException(status_code=401, detail="Authentication required")
        return None
    try:
        payload = decode_access_token(credentials.credentials, settings)
        user = storage.get_user(int(payload.get("sub") or 0), include_password_hash=True)
    except (InvalidTokenError, TypeError, ValueError):
        if ignore_invalid_token:
            return None
        raise HTTPException(status_code=401, detail="Invalid access token") from None
    if not user:
        if ignore_invalid_token:
            return None
        raise HTTPException(status_code=401, detail="Invalid access token")
    if user.get("account_status") != "active":
        raise _inactive_account_error(user)
    return user


def require_auth_if_enabled(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
) -> Optional[dict[str, Any]]:
    return _get_current_user(
        credentials,
        require_token=settings.auth_required,
        ignore_invalid_token=not settings.auth_required,
    )


def require_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
) -> dict[str, Any]:
    user = _get_current_user(credentials, require_token=True)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    return user


def require_admin_user(user: dict[str, Any] = Depends(require_current_user)) -> dict[str, Any]:
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


def _first_user_setup_required() -> bool:
    return not storage.any_users_exist()


def _registration_flags() -> dict[str, bool]:
    first_user_setup_required = _first_user_setup_required()
    invite_required = not first_user_setup_required
    return {
        "registration_available": True,
        "first_user_setup_required": first_user_setup_required,
        "invite_required": invite_required,
    }


def _generate_invite_code() -> str:
    return f"ntf-{secrets.token_urlsafe(18)}"


def _bootstrap_admin_if_configured() -> None:
    if storage.any_admin_exists():
        return
    if not settings.admin_email or not settings.admin_password:
        return
    display_name = (settings.admin_display_name or settings.admin_email.split("@", 1)[0]).strip()
    try:
        admin = storage.create_user(
            email=settings.admin_email,
            password_hash=hash_password(settings.admin_password),
            role="admin",
            account_status="active",
            display_name=display_name,
            settings_seed=_settings_seed_from_current_settings(),
            baseline_keywords=_baseline_keywords_from_current_settings(),
        )
    except ValueError:
        logger.warning("Admin bootstrap skipped because ADMIN_EMAIL already exists")
        return
    logger.info("Bootstrapped admin account email=%s user_id=%s", admin["email"], admin["id"])


@asynccontextmanager
async def lifespan(app: FastAPI):
    task: Optional[asyncio.Task] = None
    _bootstrap_admin_if_configured()
    _warn_if_hosted_without_encryption()
    if settings.auth_required or settings.background_poll_enabled or not settings.auth_required:
        task = asyncio.create_task(_background_poll())
        logger.info("Started background poll loop")
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
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "ok": True,
        "ebay_configured": settings.ebay_configured,
        "discord_configured": settings.discord_configured,
        "auth_required": settings.auth_required,
    }


@app.get("/auth/status")
def auth_status(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
) -> dict[str, Any]:
    current_user = _get_current_user(
        credentials,
        require_token=False,
        ignore_invalid_token=True,
    )
    return {
        "auth_required": settings.auth_required,
        **_registration_flags(),
        "current_user": _public_user(_user_with_access_summary(current_user)) if current_user else None,
    }


@app.post("/auth/register")
def register(request: RegisterRequest) -> dict[str, Any]:
    first_user_setup_required = _first_user_setup_required()
    invite = None
    role = "admin" if first_user_setup_required else "user"
    display_name = request.display_name.strip() or request.email.split("@", 1)[0]
    if not first_user_setup_required:
        invite = storage.get_usable_invite(request.invite_code or "", email=request.email)
        if not invite:
            raise HTTPException(status_code=403, detail="A valid invite code is required")
        role = str(invite.get("role") or "user").strip().lower() or "user"
    try:
        user = storage.create_user(
            email=request.email,
            password_hash=hash_password(request.password),
            role=role,
            account_status="active",
            display_name=display_name,
            settings_seed=_settings_seed_from_current_settings(),
            baseline_keywords=_baseline_keywords_from_current_settings(),
        )
    except ValueError:
        raise HTTPException(status_code=400, detail="Registration could not be completed") from None
    if invite:
        try:
            storage.mark_invite_used(int(invite["id"]), used_by_user_id=int(user["id"]))
        except KeyError:
            raise HTTPException(status_code=409, detail="Invite code is no longer available") from None
    user = storage.touch_user_last_login(int(user["id"]))
    token = create_access_token(user, settings)
    return {
        "access_token": token,
        "token_type": "bearer",
        "user": _public_user(_user_with_access_summary(user)),
    }


@app.post("/auth/login")
def login(request: LoginRequest) -> dict[str, Any]:
    user = storage.get_user_by_email(request.email, include_password_hash=True)
    if not user or not verify_password(request.password, user.get("password_hash") or ""):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    if user.get("account_status") != "active":
        raise _inactive_account_error(user)
    user = storage.touch_user_last_login(int(user["id"]))
    token = create_access_token(user, settings)
    return {"access_token": token, "token_type": "bearer", "user": _public_user(_user_with_access_summary(user))}


@app.get("/auth/me")
def auth_me(user: dict[str, Any] = Depends(require_current_user)) -> dict[str, Any]:
    return {"user": _public_user(_user_with_access_summary(user))}


@app.post("/auth/logout")
def auth_logout(user: dict[str, Any] = Depends(require_current_user)) -> dict[str, Any]:
    return {"ok": True, "user_id": user["id"]}


@app.post("/admin/users")
def create_admin_user(
    request: AdminCreateUserRequest,
    admin_user: dict[str, Any] = Depends(require_admin_user),
) -> dict[str, Any]:
    del admin_user
    try:
        user = storage.create_user(
            email=request.email,
            password_hash=hash_password(request.password),
            role=request.role,
            account_status=request.account_status,
            display_name=request.display_name,
            plan_name=request.plan_name,
            monthly_price=request.monthly_price,
            billing_status=request.billing_status,
            paid_until=request.paid_until,
            billing_note=request.billing_note,
            settings_seed=_settings_seed_from_current_settings(),
            baseline_keywords=_baseline_keywords_from_current_settings(),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return {"user": _public_user(_user_with_access_summary(user))}


@app.post("/admin/invites")
def create_admin_invite(
    request: AdminCreateInviteRequest,
    admin_user: dict[str, Any] = Depends(require_admin_user),
) -> dict[str, Any]:
    role = str(request.role or "user").strip().lower() or "user"
    if role not in USER_ROLES:
        raise HTTPException(status_code=400, detail="Unsupported role")
    invite = storage.create_invite(
        code=_generate_invite_code(),
        created_by_user_id=int(admin_user["id"]),
        email=request.email,
        role=role,
        expires_at=request.expires_at,
    )
    return {"invite": _public_invite(invite)}


@app.get("/admin/invites")
def list_admin_invites(admin_user: dict[str, Any] = Depends(require_admin_user)) -> dict[str, Any]:
    del admin_user
    return {"invites": [_public_invite(invite) for invite in storage.list_invites()]}


@app.post("/admin/invites/{invite_id}/revoke")
def revoke_admin_invite(invite_id: int, admin_user: dict[str, Any] = Depends(require_admin_user)) -> dict[str, Any]:
    del admin_user
    try:
        invite = storage.revoke_invite(invite_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Invite not found") from None
    return {"invite": _public_invite(invite)}


@app.get("/admin/users")
def admin_list_users(admin_user: dict[str, Any] = Depends(require_admin_user)) -> dict[str, Any]:
    del admin_user
    return {"users": [_public_user(_user_with_access_summary(user)) for user in storage.list_users()]}


@app.patch("/admin/users/{user_id}")
def update_admin_user(
    user_id: int,
    request: AdminUpdateUserRequest,
    admin_user: dict[str, Any] = Depends(require_admin_user),
) -> dict[str, Any]:
    del admin_user
    updates = request.dict(exclude_unset=True)
    if "billing_status" in updates:
        billing_status = str(updates["billing_status"] or "").strip().lower()
        if billing_status not in SUPPORTED_BILLING_STATUSES:
            raise HTTPException(status_code=400, detail="Unsupported billing_status")
        updates["billing_status"] = billing_status
    if "role" in updates and updates["role"] is not None:
        updates["role"] = str(updates["role"]).strip().lower()
    if "account_status" in updates and updates["account_status"] is not None:
        updates["account_status"] = str(updates["account_status"]).strip().lower()
    try:
        user = storage.update_user(user_id, updates)
    except KeyError:
        raise HTTPException(status_code=404, detail="User not found") from None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return {"user": _public_user(_user_with_access_summary(user))}


@app.get("/admin/scan/stats")
def admin_scan_stats(admin_user: dict[str, Any] = Depends(require_admin_user)) -> dict[str, Any]:
    del admin_user
    return storage.latest_shared_scan_stats()


@app.get("/admin/users/{user_id}/usage")
def admin_user_usage(user_id: int, admin_user: dict[str, Any] = Depends(require_admin_user)) -> dict[str, Any]:
    del admin_user
    user = storage.get_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return {
        "user": _public_user(_user_with_access_summary({**user, **storage.get_user_configuration_summary(user_id)})),
        "summary": storage.get_user_usage_summary(user_id),
        "daily": storage.list_user_usage_daily(user_id, limit=30),
    }


@app.post("/admin/users/{user_id}/disable")
def disable_user(user_id: int, admin_user: dict[str, Any] = Depends(require_admin_user)) -> dict[str, Any]:
    del admin_user
    try:
        user = storage.set_user_account_status(user_id, "disabled")
    except KeyError:
        raise HTTPException(status_code=404, detail="User not found") from None
    return {"user": _public_user(_user_with_access_summary(user))}


@app.post("/admin/users/{user_id}/enable")
def enable_user(user_id: int, admin_user: dict[str, Any] = Depends(require_admin_user)) -> dict[str, Any]:
    del admin_user
    try:
        user = storage.set_user_account_status(user_id, "active")
    except KeyError:
        raise HTTPException(status_code=404, detail="User not found") from None
    return {"user": _public_user(_user_with_access_summary(user))}


@app.get("/settings")
def get_current_user_settings(user: dict[str, Any] = Depends(require_settings_user)) -> dict[str, Any]:
    settings_row = storage.get_user_settings(int(user["id"]))
    if not settings_row:
        raise HTTPException(status_code=404, detail="Settings not found")
    return {"user": _public_user(_user_with_access_summary(user)), "settings": settings_row}


@app.put("/settings")
def update_current_user_settings(
    request: UserSettingsUpdateRequest,
    user: dict[str, Any] = Depends(require_settings_user),
) -> dict[str, Any]:
    if request.risky_score_min > request.risky_score_max:
        raise HTTPException(status_code=400, detail="risky_score_min cannot exceed risky_score_max")
    updated = storage.update_user_settings(
        int(user["id"]),
        {
            "min_score_to_alert": round(float(request.min_score_to_alert), 2),
            "min_profit_to_alert": round(float(request.min_profit_to_alert), 2),
            "risky_score_min": round(float(request.risky_score_min), 2),
            "risky_score_max": round(float(request.risky_score_max), 2),
            "max_alert_item_age_minutes": int(request.max_alert_item_age_minutes),
            "max_priority_review_item_age_hours": int(request.max_priority_review_item_age_hours),
            "max_active_queue_item_age_hours": int(request.max_active_queue_item_age_hours),
            "default_resale_condition": request.default_resale_condition.strip().lower() or "good",
            "allow_mint_for_alerts": int(bool(request.allow_mint_for_alerts)),
            "target_min_model_generation": int(request.target_min_model_generation),
            "background_poll_enabled": int(bool(request.background_poll_enabled)),
            "background_poll_seconds": int(request.background_poll_seconds),
            "active_start": request.active_start,
            "active_end": request.active_end,
            "timezone": request.timezone.strip() or "America/New_York",
        },
    )
    return {"settings": updated}


@app.get("/settings/keywords")
def get_current_user_keywords(user: dict[str, Any] = Depends(require_settings_user)) -> dict[str, Any]:
    return {"keywords": storage.list_user_keywords(int(user["id"]))}


@app.post("/settings/keywords")
def create_current_user_keyword(
    request: UserKeywordCreateRequest,
    user: dict[str, Any] = Depends(require_settings_user),
) -> dict[str, Any]:
    try:
        keyword = storage.add_user_keyword(
            int(user["id"]),
            keyword=request.keyword,
            enabled=request.enabled,
            is_baseline=False,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return {"keyword": keyword}


@app.patch("/settings/keywords/{keyword_id}")
def update_current_user_keyword(
    keyword_id: int,
    request: UserKeywordUpdateRequest,
    user: dict[str, Any] = Depends(require_settings_user),
) -> dict[str, Any]:
    try:
        keyword = storage.update_user_keyword(
            int(user["id"]),
            keyword_id,
            keyword=request.keyword,
            enabled=request.enabled,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="Keyword not found") from None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return {"keyword": keyword}


@app.delete("/settings/keywords/{keyword_id}")
def delete_current_user_keyword(
    keyword_id: int,
    user: dict[str, Any] = Depends(require_settings_user),
) -> dict[str, Any]:
    try:
        storage.delete_user_keyword(int(user["id"]), keyword_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Keyword not found") from None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return {"ok": True, "keyword_id": keyword_id}


@app.get("/settings/notifications")
def get_current_user_notifications(user: dict[str, Any] = Depends(require_settings_user)) -> dict[str, Any]:
    notifications = _normalize_notification_settings(
        int(user["id"]),
        storage.get_user_notification_settings(int(user["id"])),
    )
    if not notifications:
        raise HTTPException(status_code=404, detail="Notification settings not found")
    return {"notifications": _public_notification_settings(notifications)}


@app.put("/settings/notifications")
def update_current_user_notifications(
    request: UserNotificationSettingsUpdateRequest,
    user: dict[str, Any] = Depends(require_settings_user),
) -> dict[str, Any]:
    if request.discord_webhook is not None and request.discord_webhook.strip() and not settings.app_encryption_key and settings.auth_required:
        raise HTTPException(status_code=503, detail="APP_ENCRYPTION_KEY is required to save Discord webhooks when AUTH_REQUIRED=true")
    updates: dict[str, Any] = {
        "discord_enabled": int(bool(request.discord_enabled)),
        "alerts_enabled": int(bool(request.alerts_enabled)),
        "notify_best_finds": int(bool(request.notify_best_finds)),
        "notify_priority_review": int(bool(request.notify_priority_review)),
    }
    if request.clear_discord_webhook:
        updates["discord_webhook"] = ""
    elif request.discord_webhook is not None:
        webhook_value = request.discord_webhook.strip()[:2000]
        if webhook_value and settings.app_encryption_key:
            webhook_value = encrypt_secret(webhook_value, settings.app_encryption_key)
        updates["discord_webhook"] = webhook_value
    notifications = _normalize_notification_settings(
        int(user["id"]),
        storage.update_user_notification_settings(int(user["id"]), updates),
    )
    return {"notifications": _public_notification_settings(notifications)}


@app.post("/settings/notifications/test-discord")
async def test_current_user_discord_notification(
    user: dict[str, Any] = Depends(require_settings_user),
) -> dict[str, Any]:
    notifications = _normalize_notification_settings(
        int(user["id"]),
        storage.get_user_notification_settings(int(user["id"])),
    )
    if not notifications or not notifications.get("discord_webhook_configured"):
        raise HTTPException(status_code=400, detail="Discord webhook is not configured")
    if not notifications.get("discord_enabled"):
        raise HTTPException(status_code=400, detail="Discord notifications are disabled")
    decrypted_webhook = str(notifications.get("_resolved_webhook_raw") or "").strip()
    if not decrypted_webhook:
        raise HTTPException(status_code=503, detail="Discord webhook could not be decrypted server-side")
    notifier = DiscordNotifier(decrypted_webhook)
    sent = await notifier.send_message(
        f"Notifierr test notification for {user.get('display_name') or user.get('email') or 'user'}"
    )
    return {"ok": bool(sent)}


@app.get("/settings/repair-overrides")
def get_current_user_repair_overrides(user: dict[str, Any] = Depends(require_settings_user)) -> dict[str, Any]:
    return {"repair_overrides": storage.list_user_repair_value_overrides(int(user["id"]))}


@app.post("/settings/repair-overrides")
def create_current_user_repair_override(
    request: RepairOverrideRequest,
    user: dict[str, Any] = Depends(require_settings_user),
) -> dict[str, Any]:
    if request.part not in SUPPORTED_OVERRIDE_PARTS:
        raise HTTPException(status_code=400, detail="Unsupported part")
    try:
        override = storage.upsert_user_repair_value_override(
            int(user["id"]),
            model=request.model,
            part=request.part,
            cost=request.cost,
            source=request.source,
            note=request.note,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return {"repair_override": override}


@app.delete("/settings/repair-overrides/{override_id}")
def delete_current_user_repair_override(
    override_id: int,
    user: dict[str, Any] = Depends(require_settings_user),
) -> dict[str, Any]:
    try:
        storage.delete_user_repair_value_override(int(user["id"]), override_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Repair override not found") from None
    return {"ok": True, "override_id": override_id}


@app.get("/settings/resale-overrides")
def get_current_user_resale_overrides(user: dict[str, Any] = Depends(require_settings_user)) -> dict[str, Any]:
    return {"resale_overrides": storage.list_user_resale_research_overrides(int(user["id"]))}


@app.post("/settings/resale-overrides")
def create_current_user_resale_override(
    request: ResaleOverrideRequest,
    user: dict[str, Any] = Depends(require_settings_user),
) -> dict[str, Any]:
    if request.storage_capacity not in SUPPORTED_STORAGE_CAPACITIES:
        raise HTTPException(status_code=400, detail="Unsupported storage_capacity")
    if request.condition not in SUPPORTED_RESALE_CONDITIONS:
        raise HTTPException(status_code=400, detail="Unsupported condition")
    if request.low > request.mid or request.mid > request.high:
        raise HTTPException(status_code=400, detail="Resale override must satisfy low <= mid <= high")
    try:
        override = storage.upsert_user_resale_research_override(
            int(user["id"]),
            model=request.model,
            storage_capacity=request.storage_capacity,
            condition=request.condition,
            low=request.low,
            mid=request.mid,
            high=request.high,
            confidence=request.confidence,
            source=request.source,
            note=request.note,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return {"resale_override": override}


@app.delete("/settings/resale-overrides/{override_id}")
def delete_current_user_resale_override(
    override_id: int,
    user: dict[str, Any] = Depends(require_settings_user),
) -> dict[str, Any]:
    try:
        storage.delete_user_resale_research_override(int(user["id"]), override_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Resale override not found") from None
    return {"ok": True, "override_id": override_id}


@app.get("/items/{item_id}/correction")
def get_item_correction(
    item_id: str,
    user: dict[str, Any] = Depends(require_settings_user),
) -> dict[str, Any]:
    correction = storage.get_user_item_correction(int(user["id"]), item_id)
    return {"correction": correction}


@app.put("/items/{item_id}/correction")
def put_item_correction(
    item_id: str,
    request: ItemCorrectionRequest,
    user: dict[str, Any] = Depends(require_settings_user),
) -> dict[str, Any]:
    corrected_model = (request.corrected_model or "").strip() or None
    corrected_storage_capacity = (request.corrected_storage_capacity or "").strip() or None
    corrected_issue_type = (request.corrected_issue_type or "").strip() or None
    if corrected_storage_capacity and corrected_storage_capacity not in SUPPORTED_STORAGE_CAPACITIES:
        raise HTTPException(status_code=400, detail="Unsupported corrected_storage_capacity")
    if corrected_issue_type and corrected_issue_type not in SUPPORTED_CORRECTION_ISSUE_TYPES:
        raise HTTPException(status_code=400, detail="Unsupported corrected_issue_type")
    if not any(
        value
        for value in (
            corrected_model,
            corrected_storage_capacity,
            corrected_issue_type,
            request.corrected_part_cost,
            request.note.strip(),
        )
    ):
        raise HTTPException(status_code=400, detail="At least one correction field is required")

    resolved = _resolve_effective_user_settings(user)
    current_item = storage.get_user_item(int(user["id"]), item_id, **resolved.freshness_kwargs())
    if not current_item:
        raise HTTPException(status_code=404, detail="Item not found") from None
    try:
        correction = storage.upsert_user_item_correction(
            int(user["id"]),
            item_id,
            corrected_model=corrected_model,
            corrected_storage_capacity=corrected_storage_capacity,
            corrected_issue_type=corrected_issue_type,
            corrected_part_cost=request.corrected_part_cost,
            note=request.note,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="Item not found") from None
    pricing_context = _build_user_pricing_context(int(user["id"]))
    rescored = _rescore_stored_item(current_item, resolved, pricing_context=pricing_context)
    return {
        "ok": True,
        "correction": correction,
        "item": _decorate_item_for_user(
            rescored,
            user_id=int(user["id"]),
            pricing_context=pricing_context,
        ),
    }


@app.delete("/items/{item_id}/correction")
def delete_item_correction(
    item_id: str,
    user: dict[str, Any] = Depends(require_settings_user),
) -> dict[str, Any]:
    resolved = _resolve_effective_user_settings(user)
    current_item = storage.get_user_item(int(user["id"]), item_id, **resolved.freshness_kwargs())
    if not current_item:
        raise HTTPException(status_code=404, detail="Item not found") from None
    try:
        storage.delete_user_item_correction(int(user["id"]), item_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Correction not found") from None
    pricing_context = _build_user_pricing_context(int(user["id"]))
    rescored = _rescore_stored_item(current_item, resolved, pricing_context=pricing_context)
    return {
        "ok": True,
        "correction": None,
        "item": _decorate_item_for_user(
            rescored,
            user_id=int(user["id"]),
            pricing_context=pricing_context,
        ),
    }


@app.post("/scan/run")
async def run_scan(
    request: Optional[ScanRequest] = None,
    user: dict[str, Any] = Depends(require_settings_user),
) -> dict[str, Any]:
    request = request or ScanRequest()
    if settings.auth_required and user.get("role") == "admin":
        return await scan_shared_once(
            limit=request.limit or settings.max_results_per_keyword,
            notify=request.notify,
            triggered_by_user=user,
            background_mode=False,
            keyword_filter=request.keywords,
        )
    limited, reason = _billing_access_state(user)
    if settings.auth_required and limited:
        raise HTTPException(status_code=403, detail=f"Scanning is disabled: {reason}")
    resolved = _resolve_effective_user_settings(user)
    summary = await scan_once(
        keywords=request.keywords or resolved.keywords,
        limit=request.limit or settings.max_results_per_keyword,
        notify=request.notify,
        resolved_settings=resolved,
    )
    summary.setdefault("mode", "user" if settings.auth_required else "local")
    return summary


@app.get("/items")
def list_items(
    status: Optional[str] = None,
    user_status: Optional[str] = None,
    limit: int = Query(default=100, ge=1, le=500),
    include_ignored: bool = False,
    include_stale: bool = False,
    user: dict[str, Any] = Depends(require_settings_user),
) -> list[dict[str, Any]]:
    resolved = _resolve_effective_user_settings(user)
    if not resolved.user:
        return []
    user_id = int(resolved.user["id"])
    pricing_context = _build_user_pricing_context(user_id)
    items = storage.list_user_items(
        int(resolved.user["id"]),
        status=status,
        user_status=user_status,
        limit=limit,
        include_ignored=include_ignored,
        include_stale=include_stale,
        max_alert_item_age_minutes=resolved.max_alert_item_age_minutes,
        max_priority_review_item_age_hours=resolved.max_priority_review_item_age_hours,
        max_active_queue_item_age_hours=resolved.max_active_queue_item_age_hours,
    )
    return [
        _decorate_item_for_user(item, user_id=user_id, pricing_context=pricing_context)
        for item in items
    ]


@app.get("/stats")
def stats(user: dict[str, Any] = Depends(require_settings_user)) -> dict[str, Any]:
    resolved = _resolve_effective_user_settings(user)
    if not resolved.user:
        return storage.stats(
            max_alert_item_age_minutes=resolved.max_alert_item_age_minutes,
            max_priority_review_item_age_hours=resolved.max_priority_review_item_age_hours,
            max_active_queue_item_age_hours=resolved.max_active_queue_item_age_hours,
        )
    return storage.stats_for_user(
        int(resolved.user["id"]),
        max_alert_item_age_minutes=resolved.max_alert_item_age_minutes,
        max_priority_review_item_age_hours=resolved.max_priority_review_item_age_hours,
        max_active_queue_item_age_hours=resolved.max_active_queue_item_age_hours,
    )


@app.get("/config")
def config(user: Optional[dict[str, Any]] = Depends(require_auth_if_enabled)) -> dict[str, Any]:
    del user
    return _config_payload()


@app.post("/config/reload")
def reload_config(user: Optional[dict[str, Any]] = Depends(require_auth_if_enabled)) -> dict[str, Any]:
    del user
    global settings, repair_values, resale_research, scoring_rules, storage
    settings = load_settings()
    repair_values = load_repair_values(settings.repair_values_path)
    resale_research = load_resale_research(settings.resale_research_path)
    scoring_rules = load_scoring_rules(settings.scoring_rules_path)
    storage = create_storage(settings)
    _bootstrap_admin_if_configured()
    logger.info("Reloaded config")
    return _config_payload()


@app.post("/items/{item_id}/review")
def review_item(
    item_id: str,
    user: dict[str, Any] = Depends(require_settings_user),
) -> dict[str, Any]:
    return _set_item_status(int(user["id"]), item_id, "reviewed")


@app.post("/items/{item_id}/watch")
def watch_item(
    item_id: str,
    user: dict[str, Any] = Depends(require_settings_user),
) -> dict[str, Any]:
    return _set_item_status(int(user["id"]), item_id, "watched")


@app.post("/items/{item_id}/ignore")
def ignore_item(
    item_id: str,
    request: Optional[IgnoreRequest] = None,
    user: dict[str, Any] = Depends(require_settings_user),
) -> dict[str, Any]:
    request = request or IgnoreRequest()
    return _set_item_status(int(user["id"]), item_id, "ignored", ignored_reason=request.reason or "Ignored item")


@app.post("/items/{item_id}/reject")
def reject_item(
    item_id: str,
    request: Optional[IgnoreRequest] = None,
    user: dict[str, Any] = Depends(require_settings_user),
) -> dict[str, Any]:
    request = request or IgnoreRequest()
    return _set_item_status(int(user["id"]), item_id, "rejected", ignored_reason=request.reason or "Rejected by user")


@app.post("/items/{item_id}/promote")
async def promote_item(
    item_id: str,
    user: dict[str, Any] = Depends(require_settings_user),
) -> dict[str, Any]:
    resolved = _resolve_effective_user_settings(user)
    item = storage.get_user_item(int(user["id"]), item_id, **resolved.freshness_kwargs())
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    already_promoted = bool(item.get("promoted_at"))
    updated = _set_item_status(int(user["id"]), item_id, "promoted")
    discord_sent = False
    promotion_error = ""
    if resolved.resolved_discord_webhook_url and not already_promoted:
        notifier = DiscordNotifier(resolved.resolved_discord_webhook_url)
        try:
            discord_sent = await notifier.send_deal(
                updated,
                content="Manually promoted iPhone listing",
            )
        except Exception as exc:
            logger.exception("Manual promotion Discord send failed item_id=%s", item_id)
            promotion_error = str(exc)
    updated["discord_sent"] = discord_sent
    updated["already_promoted"] = already_promoted
    updated["promotion_error"] = promotion_error
    return updated


@app.post("/repair-values/{model}/parts")
def update_repair_value_part(
    model: str,
    request: PartCostRequest,
    user: dict[str, Any] = Depends(require_settings_user),
) -> dict[str, Any]:
    resolved = _resolve_effective_user_settings(user)
    if request.part not in SUPPORTED_OVERRIDE_PARTS:
        raise HTTPException(status_code=400, detail="Unsupported part")
    try:
        override = storage.upsert_user_repair_value_override(
            int(user["id"]),
            model=model,
            part=request.part,
            cost=request.cost,
            source=request.source or "manual_dashboard",
            note=request.note,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None

    pricing_context = _build_user_pricing_context(int(user["id"]))
    response: dict[str, Any] = {
        "ok": True,
        "model": model,
        "part": request.part,
        "cost": request.cost,
        "repair_override": override,
        "repair_values": pricing_context.repair_values.get(model, {}),
    }
    if request.item_id:
        item = storage.get_user_item(int(user["id"]), request.item_id, **resolved.freshness_kwargs())
        if item:
            rescored = _rescore_stored_item(item, resolved, pricing_context=pricing_context)
            response["item"] = _decorate_item_for_user(
                rescored,
                user_id=int(user["id"]),
                pricing_context=pricing_context,
            )
    return response


@app.post("/admin/repair-values/{model}/parts")
def update_global_repair_value_part(
    model: str,
    request: PartCostRequest,
    admin_user: dict[str, Any] = Depends(require_admin_user),
) -> dict[str, Any]:
    global repair_values
    del admin_user
    if request.part not in SUPPORTED_OVERRIDE_PARTS:
        raise HTTPException(status_code=400, detail="Unsupported part")
    try:
        repair_values = _update_repair_values_part(settings.repair_values_path, model, request)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None

    response: dict[str, Any] = {
        "ok": True,
        "model": model,
        "part": request.part,
        "cost": request.cost,
        "repair_values": repair_values.get(model, {}),
        "scope": "global_baseline",
    }
    return response


@app.post("/items/{item_id}/note")
def note_item(
    item_id: str,
    request: NoteRequest,
    user: dict[str, Any] = Depends(require_settings_user),
) -> dict[str, Any]:
    try:
        return storage.set_user_item_note(int(user["id"]), item_id, request.note)
    except KeyError:
        raise HTTPException(status_code=404, detail="Item not found") from None


@app.post("/items/{item_id}/ignore-seller")
def ignore_item_seller(
    item_id: str,
    request: Optional[IgnoreRequest] = None,
    user: dict[str, Any] = Depends(require_settings_user),
) -> dict[str, Any]:
    request = request or IgnoreRequest()
    try:
        return storage.ignore_seller_from_item(item_id, user_id=int(user["id"]), reason=request.reason or "Ignored seller")
    except KeyError:
        raise HTTPException(status_code=404, detail="Item not found") from None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None


@app.get("/ignored-sellers")
def ignored_sellers(user: dict[str, Any] = Depends(require_settings_user)) -> list[dict[str, Any]]:
    return storage.list_ignored_sellers(user_id=int(user["id"]))


@app.get("/ignored-keywords")
def ignored_keywords(user: dict[str, Any] = Depends(require_settings_user)) -> list[dict[str, Any]]:
    return storage.list_ignored_keywords(user_id=int(user["id"]))


@app.post("/ignored-keywords")
def add_ignored_keyword(
    request: IgnoredKeywordRequest,
    user: dict[str, Any] = Depends(require_settings_user),
) -> dict[str, Any]:
    try:
        storage.add_ignored_keyword(
            request.keyword,
            reason=request.reason or "Ignored keyword",
            user_id=int(user["id"]),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return {"ok": True, "keyword": request.keyword}


async def scan_once(
    keywords: Optional[list[str]],
    limit: Optional[int],
    notify: bool,
    *,
    resolved_settings: EffectiveUserSettings | None = None,
) -> dict[str, Any]:
    resolved = resolved_settings or _resolve_effective_user_settings()
    if not resolved.user:
        raise HTTPException(status_code=400, detail="No effective user is available for scan state storage")
    scan_keywords = list(keywords) if keywords is not None else list(resolved.keywords)
    scan_limit = limit or settings.max_results_per_keyword
    if _scan_lock.locked():
        logger.info("Scan skipped because another scan is already running")
        return _empty_scan_summary(
            keywords=scan_keywords,
            reason="scan_already_running",
            mode="user" if settings.auth_required else "local",
        )
    async with _scan_lock:
        return await _scan_once_unlocked(scan_keywords, scan_limit, notify, resolved)


async def _scan_once_unlocked(
    keywords: list[str],
    limit: int,
    notify: bool,
    resolved: EffectiveUserSettings,
) -> dict[str, Any]:
    if not settings.ebay_configured:
        raise HTTPException(status_code=400, detail="Configure EBAY_CLIENT_ID and EBAY_CLIENT_SECRET before scanning")

    ebay = EbayClient(settings)
    listings = await ebay.search(keywords, limit)
    pricing_context = _build_user_pricing_context(int(resolved.user["id"]))
    notifier = (
        DiscordNotifier(resolved.resolved_discord_webhook_url)
        if notify and resolved.resolved_discord_webhook_url
        else None
    )

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
        already_seen = storage.marketplace_item_exists(str(listing.get("item_id")))
        if already_seen:
            duplicates_skipped += 1
        else:
            new_items_found += 1
        result, _item_overrides = _score_listing_for_user(
            listing,
            resolved,
            pricing_context=pricing_context,
        )
        if _should_fetch_selective_detail(listing, result, resolved):
            detailed_listing = await _fetch_selective_detail(ebay, listing)
            if detailed_listing:
                listing = {**listing, **_detail_fields(detailed_listing)}
                result, _item_overrides = _score_listing_for_user(
                    listing,
                    resolved,
                    pricing_context=pricing_context,
                )
        item = {**listing, **result.as_item_fields()}
        item.update(_repair_snapshot_for_item(item, user_id=int(resolved.user["id"]), pricing_context=pricing_context, correction=_item_overrides["correction"]))
        item = _apply_availability_and_auction_policy(item)
        ignored = storage.ignored_match(listing, user_id=int(resolved.user["id"]))
        if ignored:
            item.update(ignored)
        storage.upsert_user_item(int(resolved.user["id"]), item)
        stored_item = storage.get_user_item(int(resolved.user["id"]), item["item_id"], **resolved.freshness_kwargs()) or item
        stored_item = _decorate_item_for_user(stored_item, user_id=int(resolved.user["id"]), pricing_context=pricing_context)

        if stored_item.get("fresh_for_active_queue"):
            fresh_items_found += 1
        if stored_item.get("stale"):
            stale_items_seen += 1

        if item.get("status") == "rejected":
            rejected += 1
            continue
        if item.get("status") == "candidate":
            candidates += 1
        if _is_fresh_best_find(stored_item, result, resolved):
            best_finds += 1
        elif _is_priority_review_candidate(stored_item):
            priority_review += 1

        should_alert = (
            notify
            and resolved.alerts_enabled
            and resolved.discord_enabled_for_alerts
            and resolved.notify_best_finds
            and notifier is not None
            and stored_item.get("status") == "candidate"
            and should_notify_item(stored_item, result, resolved)
            and not result.hard_reject_flags
            and stored_item.get("user_status") != "ignored"
            and not storage.was_alerted_for_user(int(resolved.user["id"]), stored_item["item_id"])
        )
        if should_alert and await notifier.send_deal(stored_item):
            storage.mark_alerted_for_user(int(resolved.user["id"]), stored_item["item_id"])
            alerts_sent += 1

    await _refresh_stored_availability(ebay, resolved)

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
        "mode": "user" if settings.auth_required else "local",
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


async def scan_shared_once(
    *,
    limit: int,
    notify: bool,
    triggered_by_user: dict[str, Any] | None,
    background_mode: bool,
    keyword_filter: list[str] | None = None,
) -> dict[str, Any]:
    if _scan_lock.locked():
        logger.info("Shared scan skipped because another scan is already running")
        return _empty_scan_summary(
            keywords=list(keyword_filter or []),
            reason="scan_already_running",
            mode="shared",
        )
    async with _scan_lock:
        return await _scan_shared_once_unlocked(
            limit=limit,
            notify=notify,
            triggered_by_user=triggered_by_user,
            background_mode=background_mode,
            keyword_filter=keyword_filter,
        )


async def _scan_shared_once_unlocked(
    *,
    limit: int,
    notify: bool,
    triggered_by_user: dict[str, Any] | None,
    background_mode: bool,
    keyword_filter: list[str] | None = None,
) -> dict[str, Any]:
    if not settings.ebay_configured:
        raise HTTPException(status_code=400, detail="Configure EBAY_CLIENT_ID and EBAY_CLIENT_SECRET before scanning")

    resolved_users = _active_shared_scan_users(background_mode=background_mode)
    if not resolved_users:
        return _empty_scan_summary(
            keywords=list(keyword_filter or []),
            reason="no_active_users",
            mode="shared",
        )

    scan_limit = int(limit or settings.max_results_per_keyword)
    plan = _build_shared_search_plan(
        resolved_users,
        limit=scan_limit,
        keyword_filter=keyword_filter,
    )
    if not plan:
        return _empty_scan_summary(
            keywords=list(keyword_filter or []),
            reason="no_enabled_keywords",
            mode="shared",
        )

    ebay = EbayClient(settings)
    run_id = storage.create_shared_scan_run(
        mode="shared_background" if background_mode else "shared_manual",
        triggered_by_user_id=int(triggered_by_user["id"]) if triggered_by_user else None,
        active_users=len(resolved_users),
        unique_searches=len(plan),
    )
    usage: dict[int, dict[str, float]] = {}
    resolved_by_user_id = {int(resolved.user["id"]): resolved for resolved in resolved_users if resolved.user}
    pricing_context_cache: dict[int, UserPricingContext] = {}
    unique_listings: dict[str, dict[str, Any]] = {}
    item_subscribers: dict[str, set[int]] = {}
    api_calls_made = 0
    total_items_returned = 0
    total_users_evaluated = 0
    total_items_scored = 0
    total_alerts_sent = 0
    new_items_found = 0
    duplicates_skipped = 0
    fresh_items_found = 0
    stale_items_seen = 0
    best_finds = 0
    priority_review = 0
    candidates = 0
    rejected = 0

    try:
        for entry in plan:
            listings = await ebay.search([entry.keyword], scan_limit)
            api_calls_made += 1
            total_items_returned += len(listings)
            total_users_evaluated += len(entry.subscribers)
            search_id = storage.record_shared_scan_search(
                run_id,
                search_signature=entry.signature,
                keyword=entry.keyword,
                limit_value=entry.limit,
                sort_order=entry.sort,
                marketplace_id=entry.marketplace_id,
                subscribed_user_count=len(entry.subscribers),
                items_returned=len(listings),
                api_calls_made=1,
                marketplace=entry.marketplace,
            )
            storage.record_shared_scan_results(
                search_id,
                [str(listing.get("item_id") or "") for listing in listings],
                marketplace=entry.marketplace,
            )
            shared_api_call_credit = 1.0 / max(1, len(entry.subscribers))
            for resolved in entry.subscribers:
                if not resolved.user:
                    continue
                user_id = int(resolved.user["id"])
                metrics = usage.setdefault(
                    user_id,
                    {
                        "search_signatures_subscribed": 0,
                        "items_scored": 0,
                        "alerts_sent": 0,
                        "detail_refreshes": 0,
                        "shared_api_calls_attributed": 0.0,
                    },
                )
                metrics["search_signatures_subscribed"] += 1
                metrics["shared_api_calls_attributed"] += shared_api_call_credit
            for listing in listings:
                item_id = str(listing.get("item_id") or "").strip()
                if not item_id:
                    continue
                if item_id not in unique_listings:
                    if storage.marketplace_item_exists(item_id):
                        duplicates_skipped += 1
                    else:
                        new_items_found += 1
                    unique_listings[item_id] = dict(listing)
                else:
                    unique_listings[item_id] = {**unique_listings[item_id], **dict(listing)}
                subscriber_ids = item_subscribers.setdefault(item_id, set())
                for resolved in entry.subscribers:
                    if resolved.user:
                        subscriber_ids.add(int(resolved.user["id"]))

        for item_id, listing in unique_listings.items():
            subscriber_ids = sorted(item_subscribers.get(item_id, set()))
            subscribed_users = [resolved_by_user_id[user_id] for user_id in subscriber_ids if user_id in resolved_by_user_id]
            if not subscribed_users:
                continue
            initial_results: dict[int, Any] = {}
            should_fetch_detail = False
            for resolved in subscribed_users:
                pricing_context = _pricing_context_for_user(int(resolved.user["id"]), pricing_context_cache)
                result, _item_overrides = _score_listing_for_user(
                    listing,
                    resolved,
                    pricing_context=pricing_context,
                )
                initial_results[int(resolved.user["id"])] = (result, _item_overrides)
                if _should_fetch_selective_detail(listing, result, resolved):
                    should_fetch_detail = True
            if should_fetch_detail:
                detailed_listing = await _fetch_selective_detail(ebay, listing)
                if detailed_listing:
                    listing = {**listing, **_detail_fields(detailed_listing)}
                    for resolved in subscribed_users:
                        usage[int(resolved.user["id"])]["detail_refreshes"] += 1
            storage.upsert_marketplace_item(listing)
            for resolved in subscribed_users:
                user_id = int(resolved.user["id"])
                pricing_context = _pricing_context_for_user(user_id, pricing_context_cache)
                result, item_overrides = initial_results[user_id]
                if should_fetch_detail and listing.get("raw_json", {}).get("details"):
                    result, item_overrides = _score_listing_for_user(
                        listing,
                        resolved,
                        pricing_context=pricing_context,
                    )
                item = {**listing, **result.as_item_fields()}
                item.update(_repair_snapshot_for_item(item, user_id=user_id, pricing_context=pricing_context, correction=item_overrides["correction"]))
                item = _apply_availability_and_auction_policy(item)
                ignored = storage.ignored_match(listing, user_id=user_id)
                if ignored:
                    item.update(ignored)
                storage.upsert_user_item_state(user_id, item)
                total_items_scored += 1
                usage[user_id]["items_scored"] += 1
                stored_item = storage.get_user_item(user_id, item["item_id"], **resolved.freshness_kwargs()) or item
                stored_item = _decorate_item_for_user(stored_item, user_id=user_id, pricing_context=pricing_context)

                if stored_item.get("fresh_for_active_queue"):
                    fresh_items_found += 1
                if stored_item.get("stale"):
                    stale_items_seen += 1
                if item.get("status") == "rejected":
                    rejected += 1
                    continue
                if item.get("status") == "candidate":
                    candidates += 1
                if _is_fresh_best_find(stored_item, result, resolved):
                    best_finds += 1
                elif _is_priority_review_candidate(stored_item):
                    priority_review += 1

                should_alert = (
                    notify
                    and resolved.alerts_enabled
                    and resolved.discord_enabled_for_alerts
                    and resolved.notify_best_finds
                    and stored_item.get("status") == "candidate"
                    and should_notify_item(stored_item, result, resolved)
                    and not result.hard_reject_flags
                    and stored_item.get("user_status") != "ignored"
                    and not storage.was_alerted_for_user(user_id, stored_item["item_id"])
                    and bool(resolved.resolved_discord_webhook_url)
                )
                if not should_alert:
                    continue
                notifier = DiscordNotifier(resolved.resolved_discord_webhook_url)
                if await notifier.send_deal(stored_item):
                    storage.mark_alerted_for_user(user_id, stored_item["item_id"])
                    total_alerts_sent += 1
                    usage[user_id]["alerts_sent"] += 1

        for resolved in resolved_users:
            if not resolved.user:
                continue
            user_id = int(resolved.user["id"])
            usage.setdefault(
                user_id,
                {
                    "search_signatures_subscribed": 0,
                    "items_scored": 0,
                    "alerts_sent": 0,
                    "detail_refreshes": 0,
                    "shared_api_calls_attributed": 0.0,
                },
            )
            usage[user_id]["detail_refreshes"] += await _refresh_stored_availability(ebay, resolved)

        for user_id, metrics in usage.items():
            storage.record_user_usage_daily(
                user_id,
                search_signatures_subscribed=int(metrics["search_signatures_subscribed"]),
                items_scored=int(metrics["items_scored"]),
                alerts_sent=int(metrics["alerts_sent"]),
                detail_refreshes=int(metrics["detail_refreshes"]),
                shared_api_calls_attributed=float(metrics["shared_api_calls_attributed"]),
                usage_weight=_usage_weight(
                    search_signatures_subscribed=int(metrics["search_signatures_subscribed"]),
                    items_scored=int(metrics["items_scored"]),
                    alerts_sent=int(metrics["alerts_sent"]),
                    detail_refreshes=int(metrics["detail_refreshes"]),
                    shared_api_calls_attributed=float(metrics["shared_api_calls_attributed"]),
                ),
            )

        storage.finish_shared_scan_run(
            run_id,
            status="completed",
            api_calls_made=api_calls_made,
            total_items_returned=total_items_returned,
            total_users_evaluated=total_users_evaluated,
            total_items_scored=total_items_scored,
            total_alerts_sent=total_alerts_sent,
        )
    except Exception:
        storage.finish_shared_scan_run(
            run_id,
            status="failed",
            api_calls_made=api_calls_made,
            total_items_returned=total_items_returned,
            total_users_evaluated=total_users_evaluated,
            total_items_scored=total_items_scored,
            total_alerts_sent=total_alerts_sent,
        )
        raise

    summary = {
        "mode": "shared",
        "scanned": total_items_returned,
        "new_items_found": new_items_found,
        "fresh_items_found": fresh_items_found,
        "stale_items_seen": stale_items_seen,
        "best_finds": best_finds,
        "priority_review": priority_review,
        "candidates": candidates,
        "rejected": rejected,
        "alerts_sent": total_alerts_sent,
        "alerted": total_alerts_sent,
        "duplicates_skipped": duplicates_skipped,
        "unique_searches": len(plan),
        "unique_marketplace_items": len(unique_listings),
        "active_users": len(resolved_users),
        "keywords": [entry.keyword for entry in plan],
    }
    logger.info("Shared scan summary %s", summary)
    return summary


async def _refresh_stored_availability(ebay: EbayClient, resolved: EffectiveUserSettings) -> int:
    if not resolved.user:
        return 0
    refreshed = 0
    for item in storage.list_user_items_for_availability_refresh(
        int(resolved.user["id"]),
        limit=25,
        max_alert_item_age_minutes=resolved.max_alert_item_age_minutes,
        max_priority_review_item_age_hours=resolved.max_priority_review_item_age_hours,
        max_active_queue_item_age_hours=resolved.max_active_queue_item_age_hours,
    ):
        detail = await _fetch_selective_detail(ebay, item)
        if not detail:
            continue
        updated = {**item, **_detail_fields(detail)}
        storage.upsert_user_item(int(resolved.user["id"]), _apply_availability_and_auction_policy(updated))
        refreshed += 1
    return refreshed


async def _background_poll() -> None:
    shared_logged = False
    while True:
        try:
            if settings.auth_required:
                resolved_users = _active_shared_scan_users(background_mode=False)
                sleep_seconds = settings.background_poll_seconds
                enabled_users = [resolved for resolved in resolved_users if resolved.background_poll_enabled]
                if enabled_users:
                    sleep_seconds = min(max(1, int(resolved.background_poll_seconds)) for resolved in enabled_users)
                if not shared_logged:
                    logger.info(
                        "Background polling is using shared multi-user polling active_users=%s background_enabled_users=%s",
                        len(resolved_users),
                        len(enabled_users),
                    )
                    shared_logged = True
                if not enabled_users:
                    logger.debug("Background shared scan skipped because no active users have polling enabled")
                else:
                    summary = await scan_shared_once(
                        limit=settings.max_results_per_keyword,
                        notify=True,
                        triggered_by_user=None,
                        background_mode=True,
                    )
                    if summary.get("reason") == "no_active_users":
                        logger.debug("Background shared scan skipped because no active users were eligible")
                    elif summary.get("reason") == "no_enabled_keywords":
                        logger.debug("Background shared scan skipped because no enabled keywords were available")
                    else:
                        logger.info("Background shared scan summary %s", summary)
            else:
                resolved = _resolve_effective_user_settings(
                    allow_local_fallback=True,
                    ensure_defaults=True,
                )
                sleep_seconds = max(1, int(resolved.background_poll_seconds))
                if not resolved.background_poll_enabled:
                    logger.debug("Background scan skipped because polling is disabled for the resolved settings user")
                elif _background_poll_is_active(resolved):
                    summary = await scan_once(
                        resolved.keywords,
                        settings.max_results_per_keyword,
                        notify=True,
                        resolved_settings=resolved,
                    )
                    logger.info("Background scan summary %s", summary)
                else:
                    logger.info("Background scan skipped outside active window")
        except Exception:
            logger.exception("Background scan failed")
            sleep_seconds = settings.background_poll_seconds
        await asyncio.sleep(max(1, int(sleep_seconds)))


def should_notify_item(item: dict[str, Any], result: Any, current_settings: Any) -> bool:
    if item.get("status") != "candidate" or result.score < current_settings.min_score_to_alert:
        return False
    if _is_unavailable(item) or _is_auction_only(item):
        return False
    if item.get("stale"):
        return False
    if not item.get("fresh_for_alert", True):
        return False
    if item.get("item_age_minutes") is not None and item["item_age_minutes"] > getattr(current_settings, "max_alert_item_age_minutes", 180):
        return False
    if item.get("user_status") in {"ignored", "rejected"}:
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


def _is_fresh_best_find(item: dict[str, Any], result: Any, current_settings: Any) -> bool:
    return should_notify_item(item, result, current_settings)


def _is_priority_review_candidate(item: dict[str, Any]) -> bool:
    if _is_unavailable(item):
        return False
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
    if _uses_model_resale_without_storage(item) and not _storage_fallback_priority_exception(item):
        return False
    reason = item.get("manual_review_reason") or ""
    return (
        float(item.get("profit_mid") or item.get("estimated_profit") or 0) >= 37.5
        or float(item.get("profit_high") or 0) >= 75
        or "Too cheap without proof" in reason
        or "Profit depends on mint resale" in reason
        or (
            item.get("estimated_parts_cost_available") is False
            and float(item.get("resale_mid") or item.get("resale_value") or 0) > 0
        )
    )


def _uses_model_resale_without_storage(item: dict[str, Any]) -> bool:
    return (
        not item.get("storage_capacity")
        and item.get("resale_source") in {"model_range", "legacy_resale_value"}
    )


def _storage_fallback_priority_exception(item: dict[str, Any]) -> bool:
    if item.get("user_status") in {"watched", "promoted"}:
        return True
    if float(item.get("profit_mid") or item.get("estimated_profit") or 0) >= 150:
        return True
    proof_flags = set(item.get("positive_flags") or [])
    strong_proof_count = len(proof_flags.intersection({"powers_on", "clean_imei", "face_id_works", "unlocked"}))
    return (
        item.get("has_repair_issue") is True
        and item.get("estimated_profit_available") is True
        and strong_proof_count >= 2
    )


def _should_fetch_selective_detail(listing: dict[str, Any], result: Any, current_settings: Any) -> bool:
    if not _is_selective_refresh_candidate({**listing, **result.as_item_fields()}):
        return False
    title = (listing.get("title") or "").lower()
    triggers = ("read description", "no ic", "board", "not original owner")
    if any(trigger in title for trigger in triggers):
        return True
    proof_flags = {"powers_on", "clean_imei", "face_id_works"}
    if not proof_flags.intersection(set(getattr(result, "positive_flags", []) or [])):
        return True
    close_score = getattr(result, "score", 0) >= current_settings.min_score_to_alert - 10
    close_profit = getattr(result, "profit_high", 0) >= current_settings.min_profit_to_alert
    return bool(close_score or close_profit)


async def _fetch_selective_detail(ebay: EbayClient, listing: dict[str, Any]) -> dict[str, Any]:
    if not hasattr(ebay, "fetch_item_detail"):
        return {}
    try:
        return await ebay.fetch_item_detail(str(listing.get("item_id")))
    except Exception:
        logger.exception("Selective eBay detail fetch failed item_id=%s", listing.get("item_id"))
        return {}


def _detail_fields(detail: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in detail.items()
        if key in {
            "raw_description",
            "availability_status",
            "buying_option_summary",
            "item_end_at",
            "last_availability_checked_at",
            "availability_note",
            "raw_json",
        }
        and value not in (None, "", {})
    }


def _update_repair_values_part(path: Path, model: str, request: PartCostRequest) -> dict[str, Any]:
    model = model.strip()
    if not model:
        raise ValueError("Model is required")
    if request.cost < 0:
        raise ValueError("Cost must be non-negative")
    data = load_repair_values(path)
    entry = data.setdefault(model, {"manual_review_allowed": True, "parts": {}})
    entry["manual_review_allowed"] = bool(entry.get("manual_review_allowed", True))
    parts = entry.setdefault("parts", {})
    parts[request.part] = round(float(request.cost), 2)
    status = str(entry.get("parts_pricing_status") or "fallback")
    if status in {"", "fallback", "missing", "estimated", "verified_screenshot_low_confidence"}:
        entry["parts_pricing_status"] = "manual_part_update"
    else:
        entry["parts_pricing_status"] = status
    entry["parts_pricing_note"] = _manual_part_pricing_note(
        str(entry.get("parts_pricing_note") or ""),
        request,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    json.loads(path.read_text(encoding="utf-8"))
    return data


def _manual_part_pricing_note(existing: str, request: PartCostRequest) -> str:
    date = datetime.now(timezone.utc).date().isoformat()
    update = f"Manual dashboard part price updated on {date}."
    details = f"{request.part}={request.cost:g}"
    if request.note:
        details = f"{details}; {request.note.strip()}"
    if request.source:
        details = f"{details}; source={request.source.strip()}"
    update = f"{update} {details}"
    lines = [line.strip() for line in existing.splitlines() if line.strip()]
    lines = [line for line in lines if not line.startswith("Manual dashboard part price updated on ")]
    lines.append(update)
    return " ".join(lines)


def _rescore_stored_item(
    item: dict[str, Any],
    resolved: EffectiveUserSettings | None = None,
    *,
    pricing_context: UserPricingContext | None = None,
) -> dict[str, Any]:
    effective_settings = resolved or _resolve_effective_user_settings()
    if not effective_settings.user:
        result = score_listing(
            item,
            repair_values,
            resale_research=resale_research,
            scoring_rules=scoring_rules,
            min_score_to_alert=effective_settings.min_score_to_alert,
            min_profit_to_alert=effective_settings.min_profit_to_alert,
            risky_score_range=effective_settings.risky_score_range,
        )
        updated = _apply_availability_and_auction_policy({
            **item,
            **result.as_item_fields(),
        })
        storage.upsert_item(updated)
        return storage.get_item(item["item_id"]) or updated
    user_id = int(effective_settings.user["id"])
    context = pricing_context or _build_user_pricing_context(user_id)
    result, item_overrides = _score_listing_for_user(
        item,
        effective_settings,
        pricing_context=context,
    )
    updated = _apply_availability_and_auction_policy({
        **item,
        **result.as_item_fields(),
        **_repair_snapshot_for_item(
            {**item, **result.as_item_fields()},
            user_id=user_id,
            pricing_context=context,
            correction=item_overrides["correction"],
        ),
    })
    storage.upsert_user_item(user_id, updated)
    rescored = storage.get_user_item(
        user_id,
        item["item_id"],
        **effective_settings.freshness_kwargs(),
    ) or updated
    return _decorate_item_for_user(rescored, user_id=user_id, pricing_context=context)


def _apply_availability_and_auction_policy(item: dict[str, Any]) -> dict[str, Any]:
    item = dict(item)
    reasons = [part.strip() for part in (item.get("manual_review_reason") or "").split(";") if part.strip()]
    if _is_unavailable(item):
        reasons.append(_availability_reason(item))
        item["alert_eligible"] = False
        if item.get("status") == "candidate":
            item["status"] = "risky"
        item["manual_review_needed"] = True
    elif _is_auction_only(item):
        item["alert_eligible"] = False
        if item.get("status") == "candidate":
            item["status"] = "risky"
        item["manual_review_needed"] = True
        if _auction_hours_remaining(item) is not None and _auction_hours_remaining(item) > 6:
            reasons.append("Auction - not urgent")
        else:
            reasons.append("Auction ending soon")
    elif item.get("buying_option_summary") == "best_offer":
        reasons.append("Best Offer available")
    item["manual_review_reason"] = "; ".join(dict.fromkeys(reasons))
    return item


def _is_selective_refresh_candidate(item: dict[str, Any]) -> bool:
    item = {"user_status": "new", **item}
    if item.get("user_status") in {"ignored", "rejected"} or item.get("status") == "rejected":
        return False
    if item.get("user_status") in {"watched", "promoted"}:
        return True
    if item.get("alert_eligible") is True and item.get("status") == "candidate":
        return True
    return _is_priority_review_candidate(item)


def _is_unavailable(item: dict[str, Any]) -> bool:
    return (item.get("availability_status") or "unknown") in {"sold", "ended", "unavailable"}


def _is_auction_only(item: dict[str, Any]) -> bool:
    return item.get("buying_option_summary") == "auction"


def _availability_reason(item: dict[str, Any]) -> str:
    status = item.get("availability_status") or "unavailable"
    if status == "sold":
        return "Listing sold"
    if status == "ended":
        return "Listing ended"
    return "Listing unavailable"


def _auction_hours_remaining(item: dict[str, Any]) -> float | None:
    raw = item.get("item_end_at")
    if not raw:
        return None
    text = str(raw).strip()
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return (parsed.astimezone(timezone.utc) - datetime.now(timezone.utc)).total_seconds() / 3600


def _background_poll_is_active(current_settings: Any) -> bool:
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


def _set_item_status(user_id: int, item_id: str, user_status: str, *, ignored_reason: str = "") -> dict[str, Any]:
    try:
        return storage.set_user_item_status(user_id, item_id, user_status, ignored_reason=ignored_reason)
    except KeyError:
        raise HTTPException(status_code=404, detail="Item not found") from None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
