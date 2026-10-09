from __future__ import annotations

import asyncio
import concurrent.futures
import copy
import inspect
import hashlib
import json
import gzip
import logging
import os
import re
import secrets
import socket
import time as monotonic_time
import uuid
from collections import Counter
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from types import SimpleNamespace
from pathlib import Path
from typing import Any, Literal, Optional
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Response
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import InvalidTokenError
from pydantic import BaseModel, Field

from .alerting import AlertDecision, evaluate_alert_decision
from .auth import create_access_token, decode_access_token, hash_password, verify_password
from .config import Settings, load_repair_values, load_resale_research, load_scoring_rules, load_settings
from .db import create_storage
from .ebay_client import EbayClient, EbayRateLimitError
from .notifier import DiscordNotifier
from .notification_delivery import destination_identity, notification_snapshot, successful_dedupe_reason
from .push import endpoint_hash, push_destination_identity, send_push_to_user
from .polling import (
    MIN_POLL_SECONDS, consecutive_cycle_outcomes, resolve_poll_interval,
    scan_success_overdue_after_seconds, stale_after_seconds, utc_now,
)
from .scan_lease import (
    GLOBAL_SCAN_LEASE_RENEW_SECONDS,
    inspect_lease_owner,
    run_with_scan_lease,
)
from .scorer import extract_description_signals, score_listing
from .decision_identity import decision_identity
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
_background_poll_task: Optional[asyncio.Task] = None
_background_supervisor_task: Optional[asyncio.Task] = None
_background_poll_stopping = False
_background_worker_started_at: Optional[str] = None
_real_asyncio_sleep = asyncio.sleep
_scan_work_executor = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="notifierr-scan")
_dashboard_freshness_cache: dict[int, dict[str, int]] = {}
_pricing_context_cache: dict[int, UserPricingContext] = {}
_dashboard_stats_cache: dict[int, tuple[float, dict[str, Any]]] = {}
_DASHBOARD_STATS_CACHE_SECONDS = 30
_polling_status_cache: dict[int, tuple[float, dict[str, Any]]] = {}
_POLLING_STATUS_CACHE_SECONDS = 10
_bearer = HTTPBearer(auto_error=False)
MIN_BACKGROUND_POLL_SECONDS = MIN_POLL_SECONDS
WORKER_ID = f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:12]}"
BACKGROUND_LEASE_NAME = "background_poll"
GLOBAL_SCAN_LEASE_NAME = "global_scan"
BACKGROUND_LEASE_TTL_SECONDS = 180
BACKGROUND_LEASE_RENEW_SECONDS = 60
SCAN_WORKER_CANCEL_WAIT_SECONDS = 30
MAX_DETAIL_REFRESHES_PER_SCAN = 15
_background_leadership_acquired_at: Optional[str] = None
_background_leadership_lost_at: Optional[str] = None


class BackgroundLeadershipLostError(RuntimeError):
    pass


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _invalidate_dashboard_user_cache(user_id: int) -> None:
    _dashboard_freshness_cache.pop(int(user_id), None)
    _dashboard_stats_cache.pop(int(user_id), None)
    _polling_status_cache.pop(int(user_id), None)


def _invalidate_pricing_context_cache(user_id: int) -> None:
    _pricing_context_cache.pop(int(user_id), None)
    _dashboard_stats_cache.pop(int(user_id), None)


def _invalidate_all_dashboard_stats_cache() -> None:
    _dashboard_stats_cache.clear()


def _invalidate_polling_status_cache() -> None:
    _polling_status_cache.clear()


def _mark_background_leadership_acquired(now: datetime) -> None:
    global _background_leadership_acquired_at, _background_leadership_lost_at
    _background_leadership_acquired_at = now.isoformat()
    _background_leadership_lost_at = None


def _mark_background_leadership_lost(now: datetime) -> None:
    global _background_leadership_acquired_at, _background_leadership_lost_at
    if _background_leadership_lost_at is None:
        _background_leadership_lost_at = now.isoformat()
    _background_leadership_acquired_at = None


def _background_lease_expiry(now: datetime) -> datetime:
    return now + timedelta(seconds=BACKGROUND_LEASE_TTL_SECONDS)


def _background_lease_owner_state(lease: dict[str, Any] | None) -> Any | None:
    if not lease:
        return None
    return inspect_lease_owner(
        lease,
        local_hostname=_hostname(),
        current_worker_id=WORKER_ID,
        current_process_id=_process_id(),
    )


def _active_background_lease(lease: dict[str, Any] | None, now: datetime) -> bool:
    expiry = _parse_utc_datetime(str((lease or {}).get("expires_at") or ""))
    return bool(lease and expiry and expiry > now)


def _is_current_background_leader(lease: dict[str, Any] | None, now: datetime | None = None) -> bool:
    now = now or datetime.now(timezone.utc)
    return bool(
        lease
        and str(lease.get("worker_id") or "") == WORKER_ID
        and int(lease.get("process_id") or 0) == _process_id()
        and _active_background_lease(lease, now)
    )


def _acquire_background_leadership(now: datetime | None = None) -> tuple[bool, dict[str, Any] | None, Any | None]:
    """Acquire the scheduler leadership lease without stealing from live or unverifiable owners."""
    now = now or datetime.now(timezone.utc)
    expires_at = _background_lease_expiry(now)
    previous = storage.get_worker_lease(BACKGROUND_LEASE_NAME)
    previous_expiry = _parse_utc_datetime(str((previous or {}).get("expires_at") or ""))

    if previous is None:
        acquired = storage.acquire_worker_lease(
            BACKGROUND_LEASE_NAME,
            WORKER_ID,
            hostname=_hostname(),
            process_id=_process_id(),
            now=now.isoformat(),
            expires_at=expires_at.isoformat(),
        )
        if acquired:
            _mark_background_leadership_acquired(now)
        return acquired, storage.get_worker_lease(BACKGROUND_LEASE_NAME), None

    if str(previous.get("worker_id") or "") == WORKER_ID:
        acquired = storage.acquire_worker_lease(
            BACKGROUND_LEASE_NAME,
            WORKER_ID,
            hostname=_hostname(),
            process_id=_process_id(),
            now=now.isoformat(),
            expires_at=expires_at.isoformat(),
        )
        if acquired:
            _mark_background_leadership_acquired(now)
        return acquired, storage.get_worker_lease(BACKGROUND_LEASE_NAME), _background_lease_owner_state(previous)

    if previous_expiry is not None and previous_expiry <= now:
        acquired = storage.takeover_worker_lease(
            BACKGROUND_LEASE_NAME,
            WORKER_ID,
            hostname=_hostname(),
            process_id=_process_id(),
            now=now.isoformat(),
            expires_at=expires_at.isoformat(),
            expected_worker_id=str(previous.get("worker_id") or ""),
            expected_expires_at=str(previous.get("expires_at") or ""),
            reason="lease_expired",
        )
        if acquired:
            _mark_background_leadership_acquired(now)
        else:
            _mark_background_leadership_lost(now)
        return acquired, storage.get_worker_lease(BACKGROUND_LEASE_NAME), _background_lease_owner_state(previous)

    owner_state = _background_lease_owner_state(previous)
    if owner_state and owner_state.state == "absent":
        acquired = storage.takeover_worker_lease(
            BACKGROUND_LEASE_NAME,
            WORKER_ID,
            hostname=_hostname(),
            process_id=_process_id(),
            now=now.isoformat(),
            expires_at=expires_at.isoformat(),
            expected_worker_id=str(previous.get("worker_id") or ""),
            expected_expires_at=str(previous.get("expires_at") or ""),
            reason="confirmed_local_owner_absent",
        )
        if acquired:
            _mark_background_leadership_acquired(now)
            logger.warning(
                "Background poll leadership recovered from dead local owner previous_worker_id=%s previous_pid=%s",
                previous.get("worker_id"),
                previous.get("process_id"),
            )
        else:
            _mark_background_leadership_lost(now)
        return acquired, storage.get_worker_lease(BACKGROUND_LEASE_NAME), owner_state

    _mark_background_leadership_lost(now)
    return False, previous, owner_state


def _renew_background_leadership(now: datetime | None = None) -> bool:
    now = now or datetime.now(timezone.utc)
    renewed = storage.renew_worker_lease(
        BACKGROUND_LEASE_NAME,
        WORKER_ID,
        now=now.isoformat(),
        expires_at=_background_lease_expiry(now).isoformat(),
    )
    if renewed:
        _mark_background_leadership_acquired(now)
    else:
        _mark_background_leadership_lost(now)
    return renewed


async def _background_leadership_renewer() -> None:
    while True:
        await _real_asyncio_sleep(BACKGROUND_LEASE_RENEW_SECONDS)
        if not _renew_background_leadership():
            raise BackgroundLeadershipLostError("background poll leadership lease was lost")
        logger.info(
            "Renewed background poll leadership lease worker_id=%s ttl_seconds=%s",
            WORKER_ID,
            BACKGROUND_LEASE_TTL_SECONDS,
        )


async def _await_with_background_leadership(task: asyncio.Task, renewal_task: asyncio.Task) -> Any:
    done, _ = await asyncio.wait({task, renewal_task}, return_when=asyncio.FIRST_COMPLETED)
    if renewal_task in done:
        error = renewal_task.exception()
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        if error:
            raise error
        raise BackgroundLeadershipLostError("background poll leadership renewal stopped unexpectedly")
    return await task


async def _sleep_with_background_leadership(seconds: int, renewal_task: asyncio.Task) -> None:
    sleep_task = asyncio.create_task(asyncio.sleep(max(1, int(seconds))))
    try:
        await _await_with_background_leadership(sleep_task, renewal_task)
    finally:
        if not sleep_task.done():
            sleep_task.cancel()
            await asyncio.gather(sleep_task, return_exceptions=True)


def _run_scan_coroutine_sync(coro_factory: Any) -> Any:
    return asyncio.run(coro_factory())


async def _run_background_scan_work(coro_factory: Any, *, label: str) -> Any:
    """Run blocking scan work off the API loop while lease orchestration stays on-loop.

    The worker owns no long-lived DB session; scan code opens/closes storage connections
    per operation. The main loop still owns the global scan lease, process-local scan
    lock, cycle finalization, and scheduler leadership renewal.
    """
    loop = asyncio.get_running_loop()
    started = monotonic_time.perf_counter()
    worker_future = loop.run_in_executor(_scan_work_executor, _run_scan_coroutine_sync, coro_factory)
    try:
        return await asyncio.shield(worker_future)
    except asyncio.CancelledError:
        logger.warning(
            "Background scan worker cancellation requested label=%s wait_seconds=%s",
            label,
            SCAN_WORKER_CANCEL_WAIT_SECONDS,
        )
        try:
            await asyncio.wait_for(asyncio.shield(worker_future), timeout=SCAN_WORKER_CANCEL_WAIT_SECONDS)
        except asyncio.TimeoutError:
            logger.error(
                "Background scan worker still running after cancellation wait label=%s elapsed_seconds=%.3f",
                label,
                monotonic_time.perf_counter() - started,
            )
        raise


def _empty_polling_status() -> dict[str, Any]:
    return {
        "process_poll_task_started": False,
        "duplicate_start_prevented": False,
        "auth_required": settings.auth_required,
        "background_poll_env_enabled": settings.background_poll_enabled,
        "background_poll_config_seconds": settings.background_poll_seconds,
        "background_poll_user_seconds": None,
        "background_poll_interval_source": "",
        "safe_min_background_poll_seconds": MIN_BACKGROUND_POLL_SECONDS,
        "local_or_user_polling_enabled": False,
        "cycle_running": False,
        "last_cycle_started_at": None,
        "last_cycle_finished_at": None,
        "last_success_at": None,
        "last_failure_at": None,
        "last_error": "",
        "last_sleep_seconds": None,
        "cycles_attempted": 0,
        "cycles_succeeded": 0,
        "cycles_failed": 0,
        "consecutive_failures": 0,
        "last_scan_summary": {},
        "last_alerts_found": 0,
        "last_alerts_sent": 0,
        "last_notifications_attempted": 0,
        "last_notifications_sent": 0,
        "last_notifications_failed": 0,
    }


@dataclass
class PollingStatusTracker:
    state: dict[str, Any] = field(default_factory=_empty_polling_status)

    def reset(self) -> None:
        self.state = _empty_polling_status()

    def snapshot(self) -> dict[str, Any]:
        self.state["auth_required"] = settings.auth_required
        self.state["background_poll_env_enabled"] = settings.background_poll_enabled
        self.state["background_poll_config_seconds"] = settings.background_poll_seconds
        self.state["safe_min_background_poll_seconds"] = MIN_BACKGROUND_POLL_SECONDS
        return dict(self.state)

    def task_started(self) -> None:
        self.state.update(
            {
                "process_poll_task_started": True,
                "auth_required": settings.auth_required,
                "background_poll_env_enabled": settings.background_poll_enabled,
                "background_poll_config_seconds": settings.background_poll_seconds,
            }
        )

    def task_not_started(self) -> None:
        self.state.update(
            {
                "process_poll_task_started": False,
                "auth_required": settings.auth_required,
                "background_poll_env_enabled": settings.background_poll_enabled,
                "background_poll_config_seconds": settings.background_poll_seconds,
            }
        )

    def duplicate_start_prevented(self) -> None:
        self.state["duplicate_start_prevented"] = True

    def polling_enabled(self, enabled: bool) -> None:
        self.state["local_or_user_polling_enabled"] = bool(enabled)

    def interval_resolved(
        self,
        *,
        config_seconds: int,
        user_seconds: int | None,
        final_seconds: int,
        interval_source: str,
    ) -> None:
        self.state.update(
            {
                "background_poll_config_seconds": int(config_seconds),
                "background_poll_user_seconds": int(user_seconds) if user_seconds is not None else None,
                "background_poll_interval_source": interval_source,
                "last_sleep_seconds": int(final_seconds),
            }
        )

    def cycle_started(self) -> None:
        self.state.update(
            {
                "cycle_running": True,
                "last_cycle_started_at": _utc_now_iso(),
                "cycles_attempted": int(self.state.get("cycles_attempted") or 0) + 1,
                "last_scan_summary": {},
                "last_alerts_found": 0,
                "last_alerts_sent": 0,
                "last_notifications_attempted": 0,
                "last_notifications_sent": 0,
                "last_notifications_failed": 0,
            }
        )

    def cycle_succeeded(self, summary: dict[str, Any] | None = None) -> None:
        summary = summary or {}
        self.state.update(
            {
                "cycle_running": False,
                "last_cycle_finished_at": _utc_now_iso(),
                "last_success_at": _utc_now_iso(),
                "cycles_succeeded": int(self.state.get("cycles_succeeded") or 0) + 1,
                "consecutive_failures": 0,
                "last_error": "",
                "last_scan_summary": _compact_scan_summary(summary),
                "last_alerts_found": int(summary.get("best_finds") or summary.get("candidates") or 0),
                "last_alerts_sent": int(summary.get("alerts_sent") or summary.get("alerted") or 0),
            }
        )

    def cycle_failed(self, error: BaseException) -> None:
        self.state.update(
            {
                "cycle_running": False,
                "last_cycle_finished_at": _utc_now_iso(),
                "last_failure_at": _utc_now_iso(),
                "last_error": f"{type(error).__name__}: {error}",
                "cycles_failed": int(self.state.get("cycles_failed") or 0) + 1,
                "consecutive_failures": int(self.state.get("consecutive_failures") or 0) + 1,
            }
        )

    def sleep_scheduled(self, seconds: int) -> None:
        self.state["last_sleep_seconds"] = int(seconds)

    def notification_attempted(self) -> None:
        self.state["last_notifications_attempted"] = int(self.state.get("last_notifications_attempted") or 0) + 1

    def notification_sent(self) -> None:
        self.state["last_notifications_sent"] = int(self.state.get("last_notifications_sent") or 0) + 1

    def notification_failed(self) -> None:
        self.state["last_notifications_failed"] = int(self.state.get("last_notifications_failed") or 0) + 1


def _compact_scan_summary(summary: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "mode",
        "scanned",
        "new_items_found",
        "fresh_items_found",
        "stale_items_seen",
        "best_finds",
        "priority_review",
        "candidates",
        "rejected",
        "alerts_sent",
        "alerted",
        "duplicates_skipped",
        "unique_searches",
        "unique_marketplace_items",
        "active_users",
        "skipped",
        "reason",
    }
    return {key: summary[key] for key in allowed if key in summary}


polling_status = PollingStatusTracker()


def _process_id() -> int:
    return int(os.getpid())


def _hostname() -> str:
    try:
        return socket.gethostname()
    except Exception:
        return ""


def _positive_int(value: Any, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = int(default)
    return max(1, parsed)


def _resolve_background_poll_sleep_seconds(
    *,
    mode: str,
    current_settings: Any | None = None,
    enabled_users: list[Any] | None = None,
) -> dict[str, Any]:
    persisted = []
    if settings.auth_required and enabled_users:
        persisted = [getattr(resolved, "background_poll_seconds", None) for resolved in enabled_users]
    elif mode == "local_background" and current_settings is not None:
        persisted = [getattr(current_settings, "background_poll_seconds", None)]
    interval = resolve_poll_interval(configured=settings.background_poll_seconds, persisted=persisted)
    user_seconds = interval.effective_seconds if persisted else None
    return {
        "config_seconds": interval.configured_seconds,
        "user_seconds": user_seconds,
        "final_seconds": interval.effective_seconds,
        "interval_source": interval.source,
    }


def _jsonable_flags(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return [value] if value else []
        if isinstance(parsed, list):
            return [str(item) for item in parsed]
    return []


def _manual_reason_parts(item: dict[str, Any]) -> list[str]:
    return [part.strip() for part in str(item.get("manual_review_reason") or "").split(";") if part.strip()]


def _increment(counter: Counter[str], keys: list[str]) -> None:
    for key in keys:
        if key:
            counter[str(key)] += 1


@dataclass
class ScanCycleCounters:
    final_bucket_counts: Counter[str] = field(default_factory=Counter)
    alert_block_reason_counts: Counter[str] = field(default_factory=Counter)
    missing_data_reason_counts: Counter[str] = field(default_factory=Counter)
    risk_flag_counts: Counter[str] = field(default_factory=Counter)
    parts_pricing_status_counts: Counter[str] = field(default_factory=Counter)
    model_detection_failure_count: int = 0
    resale_missing_count: int = 0
    items_scored: int = 0
    alerts_attempted: int = 0
    alerts_failed: int = 0

    def record_trace(self, trace: dict[str, Any]) -> None:
        self.items_scored += 1
        self.final_bucket_counts[str(trace["verdict"].get("bucket") or "unknown")] += 1
        _increment(self.alert_block_reason_counts, trace["reasons"].get("blocking_rules") or [])
        _increment(self.missing_data_reason_counts, trace["reasons"].get("missing_data") or [])
        _increment(self.risk_flag_counts, trace["detected"].get("hard_risks") or [])
        _increment(self.risk_flag_counts, trace["detected"].get("soft_risks") or [])
        self.parts_pricing_status_counts[str(trace["pricing"].get("pricing_confidence") or "missing")] += 1
        if trace["detected"].get("model") in {"", "unknown", None}:
            self.model_detection_failure_count += 1
        if trace["pricing"].get("resale_mid") in (None, 0, 0.0):
            self.resale_missing_count += 1

    def as_finish_kwargs(self) -> dict[str, Any]:
        return {
            "final_bucket_counts": dict(self.final_bucket_counts),
            "alert_block_reason_counts": dict(self.alert_block_reason_counts),
            "missing_data_reason_counts": dict(self.missing_data_reason_counts),
            "risk_flag_counts": dict(self.risk_flag_counts),
            "model_detection_failure_count": self.model_detection_failure_count,
            "resale_missing_count": self.resale_missing_count,
            "parts_pricing_status_counts": dict(self.parts_pricing_status_counts),
            "items_scored": self.items_scored,
            "alerts_attempted": self.alerts_attempted,
            "alerts_failed": self.alerts_failed,
        }


def _scan_cycle_context(
    *,
    mode: str,
    user_id: int | None = None,
    current_settings: Any | None = None,
    users_considered: int = 0,
    users_scanned: int = 0,
    keywords_searched: list[str] | None = None,
    sources_checked: list[str] | None = None,
) -> dict[str, Any]:
    current_settings = current_settings or settings
    return {
        "mode": mode,
        "user_id": user_id,
        "process_id": _process_id(),
        "hostname": _hostname(),
        "worker_id": WORKER_ID,
        "auth_required": bool(settings.auth_required),
        "background_poll_enabled": bool(getattr(current_settings, "background_poll_enabled", settings.background_poll_enabled)),
        "background_poll_seconds": int(getattr(current_settings, "background_poll_seconds", settings.background_poll_seconds) or 0),
        "active_window_start": getattr(current_settings, "background_poll_active_start", settings.background_poll_active_start),
        "active_window_end": getattr(current_settings, "background_poll_active_end", settings.background_poll_active_end),
        "active_window_timezone": str(getattr(current_settings, "background_poll_timezone", settings.background_poll_timezone) or ""),
        "users_considered": users_considered,
        "users_scanned": users_scanned,
        "keywords_searched": keywords_searched or [],
        "sources_checked": sources_checked or ["ebay"],
    }


def _create_scan_cycle(
    *,
    mode: str,
    user_id: int | None = None,
    current_settings: Any | None = None,
    users_considered: int = 0,
    users_scanned: int = 0,
    keywords_searched: list[str] | None = None,
    sources_checked: list[str] | None = None,
    status: str = "started",
    skip_reason: str = "",
    error_message: str = "",
    source: str | None = None,
    error_category: str | None = None,
    http_status: int | None = None,
    cooldown_until: str | None = None,
    retry_after_seconds: int | None = None,
) -> int:
    return storage.create_scan_cycle(
        **_scan_cycle_context(
            mode=mode,
            user_id=user_id,
            current_settings=current_settings,
            users_considered=users_considered,
            users_scanned=users_scanned,
            keywords_searched=keywords_searched,
            sources_checked=sources_checked,
        ),
        status=status,
        skip_reason=skip_reason,
        error_message=error_message,
        source=source,
        error_category=error_category,
        http_status=http_status,
        cooldown_until=cooldown_until,
        retry_after_seconds=retry_after_seconds,
    )


def _finish_scan_cycle_from_summary(
    cycle_id: int | None,
    *,
    status: str,
    summary: dict[str, Any] | None = None,
    counters: ScanCycleCounters | None = None,
    skip_reason: str = "",
    error_message: str = "",
    users_considered: int | None = None,
    users_scanned: int | None = None,
    keywords_searched: list[str] | None = None,
    sources_checked: list[str] | None = None,
    source: str | None = None,
    error_category: str | None = None,
    http_status: int | None = None,
    cooldown_until: str | None = None,
    retry_after_seconds: int | None = None,
) -> None:
    if not cycle_id:
        return
    summary = summary or {}
    counters = counters or ScanCycleCounters()
    finish_kwargs = counters.as_finish_kwargs()
    storage.finish_scan_cycle(
        cycle_id,
        status=status,
        skip_reason=skip_reason or str(summary.get("reason") or ""),
        error_message=error_message,
        users_considered=users_considered,
        users_scanned=users_scanned,
        keywords_searched=keywords_searched or summary.get("keywords"),
        sources_checked=sources_checked or ["ebay"],
        items_found=int(summary.get("scanned") or summary.get("unique_marketplace_items") or 0),
        new_items_found=int(summary.get("new_items_found") or 0),
        duplicate_items=int(summary.get("duplicates_skipped") or 0),
        alerts_sent=int(summary.get("alerts_sent") or summary.get("alerted") or 0),
        source=source,
        error_category=error_category,
        http_status=http_status,
        cooldown_until=cooldown_until,
        retry_after_seconds=retry_after_seconds,
        **finish_kwargs,
    )
    if status == "completed":
        _invalidate_all_dashboard_stats_cache()
    _invalidate_polling_status_cache()


def _rate_limit_cycle_metadata(
    exc: EbayRateLimitError | None = None,
    *,
    keyword: str | None = None,
    status: dict[str, Any] | None = None,
) -> dict[str, Any]:
    status = status or _ebay_source_status()
    retry_after = int(
        (getattr(exc, "retry_after_seconds", 0) if exc else 0)
        or status.get("retry_after_seconds")
        or 0
    )
    http_status = int(
        (getattr(exc, "http_status", 0) if exc else 0)
        or status.get("last_http_status")
        or 429
    )
    return {
        "source": str((getattr(exc, "source", "") if exc else "") or status.get("source") or "ebay"),
        "error_category": str(status.get("last_error_category") or "ebay_rate_limited"),
        "http_status": http_status,
        "cooldown_until": status.get("cooldown_until"),
        "retry_after_seconds": retry_after,
    }


def _scan_cycle_error_message(
    exc: Exception,
    *,
    keyword: str | None = None,
    source_status: dict[str, Any] | None = None,
) -> str:
    message = f"{type(exc).__name__}: {exc}"
    if not isinstance(exc, EbayRateLimitError):
        return message
    source_status = source_status or _ebay_source_status()
    context = {
        "category": "ebay_rate_limited",
        "source": getattr(exc, "source", "ebay"),
        "http_status": int(getattr(exc, "http_status", 429) or 429),
        "retry_after_seconds": int(getattr(exc, "retry_after_seconds", 0) or 0),
    }
    if source_status.get("cooldown_until"):
        context["cooldown_until"] = source_status.get("cooldown_until")
    failed_keyword = keyword or getattr(exc, "keyword", None)
    if failed_keyword:
        context["keyword"] = str(failed_keyword)
    item_id = getattr(exc, "item_id", None)
    if item_id:
        context["item_id"] = str(item_id)
    return f"{message} context={json.dumps(context, sort_keys=True)}"


def _parse_utc_datetime(value: str | None) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _source_status_payload(row: dict[str, Any] | None, *, source: str = "ebay") -> dict[str, Any]:
    row = row or {}
    now = datetime.now(timezone.utc)
    cooldown_until = str(row.get("cooldown_until") or "").strip() or None
    cooldown_dt = _parse_utc_datetime(cooldown_until)
    cooling_down = bool(cooldown_dt and cooldown_dt > now)
    status = "cooling_down" if cooling_down else str(row.get("status") or "ok")
    if status == "cooling_down" and not cooling_down:
        status = "ok"
    return {
        "source": str(row.get("source") or source),
        "status": status,
        "cooldown_until": cooldown_until if cooling_down else None,
        "last_success_at": row.get("last_success_at"),
        "last_failure_at": row.get("last_failure_at"),
        "last_http_status": row.get("last_http_status"),
        "last_error_category": row.get("last_error_category") or "",
        "last_error_message_preview": str(row.get("last_error_message") or "")[:300],
        "last_keyword": row.get("last_keyword") or "",
        "last_item_id": row.get("last_item_id") or "",
        "retry_after_seconds": int(row.get("retry_after_seconds") or 0),
        "updated_at": row.get("updated_at"),
    }


def _ebay_source_status() -> dict[str, Any]:
    return _source_status_payload(storage.get_source_status("ebay"), source="ebay")


def _ebay_is_cooling_down() -> bool:
    return _ebay_source_status().get("status") == "cooling_down"


def _cooldown_until_for_rate_limit(exc: EbayRateLimitError) -> str:
    retry_after = int(getattr(exc, "retry_after_seconds", 0) or 0)
    configured = int(getattr(settings, "ebay_rate_limit_backoff_seconds", 0) or 0)
    cooldown_seconds = max(300, retry_after, configured)
    return (datetime.now(timezone.utc) + timedelta(seconds=cooldown_seconds)).isoformat()


def _record_ebay_rate_limit(exc: EbayRateLimitError, *, keyword: str | None = None) -> dict[str, Any]:
    cooldown_until = _cooldown_until_for_rate_limit(exc)
    failed_keyword = str(keyword or getattr(exc, "keyword", "") or "").strip()
    storage.update_source_status(
        "ebay",
        status="cooling_down",
        cooldown_until=cooldown_until,
        last_failure_at=_utc_now_iso(),
        last_http_status=int(getattr(exc, "http_status", 429) or 429),
        last_error_category="ebay_rate_limited",
        last_error_message=f"{type(exc).__name__}: {exc}",
        last_keyword=failed_keyword,
        last_item_id=str(getattr(exc, "item_id", "") or ""),
        retry_after_seconds=int(getattr(exc, "retry_after_seconds", 0) or 0),
    )
    return _ebay_source_status()


def _record_ebay_success() -> None:
    storage.update_source_status(
        "ebay",
        status="ok",
        cooldown_until=None,
        last_success_at=_utc_now_iso(),
    )


def _source_cooldown_skip_message(status: dict[str, Any]) -> str:
    context = {
        "category": status.get("last_error_category") or "ebay_rate_limited",
        "source": status.get("source") or "ebay",
        "http_status": status.get("last_http_status"),
        "cooldown_until": status.get("cooldown_until"),
        "retry_after_seconds": status.get("retry_after_seconds") or 0,
    }
    if status.get("last_keyword"):
        context["keyword"] = status["last_keyword"]
    if status.get("last_item_id"):
        context["item_id"] = status["last_item_id"]
    return f"Source cooling down context={json.dumps(context, sort_keys=True)}"


def _persist_source_cooldown_skip(
    *,
    mode: str,
    user_id: int | None,
    current_settings: Any | None = None,
    users_considered: int = 0,
    users_scanned: int = 0,
    keywords: list[str] | None = None,
    status: dict[str, Any] | None = None,
) -> int:
    status = status or _ebay_source_status()
    cycle_id = _create_scan_cycle(
        mode=mode,
        user_id=user_id,
        current_settings=current_settings,
        users_considered=users_considered,
        users_scanned=users_scanned,
        keywords_searched=keywords or [],
        sources_checked=["ebay"],
        status="started",
    )
    _finish_scan_cycle_from_summary(
        cycle_id,
        status="skipped",
        summary=_empty_scan_summary(keywords=keywords or [], reason="ebay_rate_limited", mode=mode),
        skip_reason="ebay_rate_limited",
        error_message=_source_cooldown_skip_message(status),
        users_considered=users_considered,
        users_scanned=users_scanned,
        keywords_searched=keywords or [],
        sources_checked=["ebay"],
        **_rate_limit_cycle_metadata(status=status),
    )
    return cycle_id


def _persist_skipped_scan_cycle(
    *,
    mode: str,
    reason: str,
    user_id: int | None = None,
    current_settings: Any | None = None,
    users_considered: int = 0,
    users_scanned: int = 0,
    keywords: list[str] | None = None,
) -> int:
    cycle_id = _create_scan_cycle(
        mode=mode,
        user_id=user_id,
        current_settings=current_settings,
        users_considered=users_considered,
        users_scanned=users_scanned,
        keywords_searched=keywords,
        status="started",
    )
    _finish_scan_cycle_from_summary(
        cycle_id,
        status="skipped",
        summary=_empty_scan_summary(keywords=keywords or [], reason=reason, mode=mode),
        skip_reason=reason,
        users_considered=users_considered,
        users_scanned=users_scanned,
        keywords_searched=keywords or [],
    )
    return cycle_id


class ScanRequest(BaseModel):
    keywords: Optional[list[str]] = None
    limit: Optional[int] = Field(default=None, ge=1, le=100)
    notify: bool = True
    force_source_call: bool = False


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
    feedback_code: Optional[str] = Field(default=None, max_length=40)
    note: str = Field(default="", max_length=1000)


class ItemFeedbackRequest(BaseModel):
    label: Literal["GOOD", "BAD", "UNSURE"]
    note: Optional[str] = Field(default=None, max_length=1000)


class ItemOutcomeRequest(BaseModel):
    status: Literal["SKIPPED", "PURCHASED", "SOLD", "FAILED_REPAIR"]
    purchase_price: Optional[float] = Field(default=None, ge=0)
    purchase_tax: Optional[float] = Field(default=None, ge=0)
    inbound_shipping: Optional[float] = Field(default=None, ge=0)
    purchase_date: Optional[str] = Field(default=None, max_length=32)
    actual_repair_type: Optional[str] = Field(default=None, max_length=120)
    parts_cost: Optional[float] = Field(default=None, ge=0)
    other_repair_cost: Optional[float] = Field(default=None, ge=0)
    sale_date: Optional[str] = Field(default=None, max_length=32)
    sale_price: Optional[float] = Field(default=None, ge=0)
    selling_fees: Optional[float] = Field(default=None, ge=0)
    outbound_shipping: Optional[float] = Field(default=None, ge=0)
    refund_amount: Optional[float] = Field(default=None, ge=0)
    other_cost: Optional[float] = Field(default=None, ge=0)
    note: Optional[str] = Field(default=None, max_length=1000)


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


class TraceReplayRequest(BaseModel):
    user_id: Optional[int] = Field(default=None, ge=1)
    limit: int = Field(default=100, ge=1, le=500)
    active_only: bool = False
    non_stale_only: bool = False
    not_ignored_only: bool = False
    source_cycle_id: Optional[int] = Field(default=None, ge=1)
    item_ids: list[str] = Field(default_factory=list)
    rescore_from_raw: bool = False
    write_traces: bool = False
    dry_run: bool = True


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
    background_poll_seconds: int = Field(default=600, ge=300, le=1200)
    active_start: Optional[str] = Field(default=None, max_length=10)
    active_end: Optional[str] = Field(default=None, max_length=10)
    timezone: str = Field(default="America/New_York", max_length=80)


class UserNotificationSettingsUpdateRequest(BaseModel):
    discord_webhook: Optional[str] = Field(default=None, max_length=2000)
    clear_discord_webhook: bool = False
    discord_enabled: bool = False
    push_enabled: bool = True
    use_global_discord_webhook: bool = False
    alerts_enabled: bool = True
    notify_best_finds: bool = True
    notify_priority_review: bool = True
    send_gem_immediately: bool = True
    send_profitable_immediately: bool = True
    review_delivery_mode: str = Field(default="immediate", pattern="^(immediate|digest|off)$")
    max_review_alerts_per_hour: int = Field(default=2, ge=0, le=60)
    duplicate_suppression_hours: int = Field(default=72, ge=1, le=720)
    meaningful_price_drop_amount: float = Field(default=20, ge=0)
    meaningful_price_drop_percent: float = Field(default=0.05, ge=0, le=1)
    meaningful_profit_increase_amount: float = Field(default=25, ge=0)
    meaningful_profit_increase_percent: float = Field(default=0.15, ge=0, le=10)
    meaningful_roi_increase: float = Field(default=0.10, ge=0, le=10)
    catchup_enabled: bool = True
    catchup_batch_size: int = Field(default=5, ge=1, le=50)
    catchup_include_review: bool = False
    gem_min_expected_profit: float = Field(default=75, ge=0)
    profitable_min_expected_profit: float = Field(default=50, ge=0)
    review_min_expected_profit: float = Field(default=25, ge=0)
    review_min_upside_profit: float = Field(default=60, ge=0)
    gem_min_roi: float = Field(default=0.25, ge=0, le=10)
    profitable_min_roi: float = Field(default=0.15, ge=0, le=10)
    review_min_roi: float = Field(default=0.05, ge=0, le=10)
    max_listing_age_minutes: int = Field(default=360, ge=15, le=10080)


class PushSubscriptionKeysRequest(BaseModel):
    p256dh: str = Field(min_length=1, max_length=1000)
    auth: str = Field(min_length=1, max_length=1000)


class PushSubscriptionRequest(BaseModel):
    endpoint: str = Field(min_length=10, max_length=4096)
    keys: PushSubscriptionKeysRequest
    device_label: str = Field(default="Browser/PWA", max_length=120)
    user_agent: str = Field(default="", max_length=500)


class PushSubscriptionDeleteRequest(BaseModel):
    endpoint: str = Field(min_length=10, max_length=4096)


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
SUPPORTED_FEEDBACK_CODES = {
    "good_deal",
    "not_profitable",
    "wrong_model",
    "wrong_storage",
    "wrong_damage",
    "accessory_not_phone",
    "too_risky",
    "already_sold",
    "pricing_wrong",
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


def require_polling_status_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
) -> dict[str, Any]:
    if settings.auth_required:
        user = _get_current_user(credentials, require_token=True)
        if not user:
            raise HTTPException(status_code=401, detail="Authentication required")
        return user
    user = _get_current_user(credentials, require_token=False, ignore_invalid_token=True)
    return user or {"id": 0, "role": "admin", "account_status": "active"}


def _default_notification_settings_payload() -> dict[str, Any]:
    return {
        "discord_enabled": False,
        "push_enabled": True,
        "use_global_discord_webhook": False,
        "alerts_enabled": True,
        "notify_best_finds": True,
        "notify_priority_review": True,
        "send_gem_immediately": True,
        "send_profitable_immediately": True,
        "review_delivery_mode": "immediate",
        "max_review_alerts_per_hour": 2,
        "duplicate_suppression_hours": 72,
        "meaningful_price_drop_amount": 20.0,
        "meaningful_price_drop_percent": 0.05,
        "meaningful_profit_increase_amount": 25.0,
        "meaningful_profit_increase_percent": 0.15,
        "meaningful_roi_increase": 0.10,
        "catchup_enabled": True,
        "catchup_batch_size": 5,
        "catchup_include_review": False,
        "gem_min_expected_profit": 75.0,
        "profitable_min_expected_profit": 50.0,
        "review_min_expected_profit": 25.0,
        "review_min_upside_profit": 60.0,
        "gem_min_roi": 0.25,
        "profitable_min_roi": 0.15,
        "review_min_roi": 0.05,
        "max_listing_age_minutes": 360,
        "discord_webhook": "",
        "discord_webhook_configured": False,
        "global_discord_webhook_configured": False,
        "webhook_configured": False,
        "webhook_source": None,
        "notification_ready": False,
        "notification_block_reason": "webhook_missing",
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


def _normalize_notification_settings(user_id: int, notifications: dict[str, Any] | None, *, migrate_plaintext: bool = True) -> dict[str, Any] | None:
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
            if not is_encrypted_secret(stored_value) and migrate_plaintext:
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
) -> dict[str, Any]:
    resolved = {**_default_notification_settings_payload(), **(data or {})}
    raw_webhook = str(resolved.get("_resolved_webhook_raw") or resolved.get("_discord_webhook_raw") or "").strip()
    global_webhook = str(settings.discord_webhook_url or "").strip()
    use_global_opt_in = bool(resolved.get("use_global_discord_webhook"))
    destination = raw_webhook or (global_webhook if use_global_opt_in and global_webhook else "")
    destination_source = "per_user" if raw_webhook else "global_opt_in" if destination else None
    user_id = int(resolved.get("user_id") or 0)
    active_push_subscriptions = len(storage.list_push_subscriptions(user_id, enabled_only=True)) if user_id else 0
    push_ready = bool(resolved.get("push_enabled", True)) and settings.push_configured and active_push_subscriptions > 0
    discord_ready = bool(resolved.get("discord_enabled")) and bool(destination)
    if not bool(resolved.get("alerts_enabled")):
        block_reason = "notifications_disabled"
    elif discord_ready or push_ready:
        block_reason = None
    elif bool(resolved.get("discord_enabled")):
        block_reason = "global_fallback_not_authorized" if global_webhook and not use_global_opt_in else "webhook_missing"
    elif bool(resolved.get("push_enabled", True)) and settings.push_configured:
        block_reason = "push_device_not_subscribed"
    else:
        block_reason = "discord_disabled"
    resolved["_resolved_webhook_url"] = destination
    resolved["uses_global_webhook_fallback"] = destination_source == "global_opt_in"
    resolved["discord_enabled_effective"] = bool(resolved.get("discord_enabled"))
    resolved["push_enabled_effective"] = bool(resolved.get("push_enabled", True))
    resolved["push_configured"] = settings.push_configured
    resolved["push_ready"] = push_ready
    resolved["active_push_subscriptions"] = active_push_subscriptions
    resolved["global_discord_webhook_configured"] = bool(global_webhook)
    resolved["webhook_configured"] = bool(destination)
    resolved["webhook_source"] = destination_source
    resolved["notification_ready"] = block_reason is None
    resolved["notification_block_reason"] = block_reason
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
    migrate_plaintext_webhook: bool = True,
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
            migrate_plaintext=migrate_plaintext_webhook,
        )
        if selected_user
        else None
    )
    resolved_notifications = _resolve_notification_settings(notifications)
    keywords = _resolve_scan_keywords(selected_user)
    limited, reason = _billing_access_state(selected_user)
    if limited:
        user_settings = {**(user_settings or {}), "background_poll_enabled": False}
        resolved_notifications = {
            **resolved_notifications,
            "alerts_enabled": False,
            "discord_enabled_effective": False,
            "notification_ready": False,
            "notification_block_reason": "notifications_disabled",
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


def _cached_pricing_context_for_user(user_id: int) -> UserPricingContext:
    user_id = int(user_id)
    context = _pricing_context_cache.get(user_id)
    if context is None:
        context = _build_user_pricing_context(user_id)
        _pricing_context_cache[user_id] = context
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
    correction_by_item_id: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    correction = (
        (correction_by_item_id or {}).get(str(item["item_id"]))
        if correction_by_item_id is not None
        else storage.get_user_item_correction(user_id, item["item_id"])
    ) or {}
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


def _decorate_alert_decision(item: dict[str, Any], current_settings: Any) -> dict[str, Any]:
    result_view = SimpleNamespace(**item)
    decision = evaluate_alert_decision(item, result_view, current_settings)
    return {
        **item,
        "alert_tier": decision.tier,
        "tier_eligible": decision.eligible,
        "alert_decision": decision.as_dict(),
    }


def _dashboard_freshness_kwargs(user_id: int) -> dict[str, int]:
    user_id = int(user_id)
    cached = _dashboard_freshness_cache.get(user_id)
    if cached is not None:
        return dict(cached)
    user_settings = storage.get_user_settings(user_id) or {}
    freshness = {
        "max_alert_item_age_minutes": int(user_settings.get("max_alert_item_age_minutes") or settings.max_alert_item_age_minutes),
        "max_priority_review_item_age_hours": int(user_settings.get("max_priority_review_item_age_hours") or settings.max_priority_review_item_age_hours),
        "max_active_queue_item_age_hours": int(user_settings.get("max_active_queue_item_age_hours") or settings.max_active_queue_item_age_hours),
    }
    _dashboard_freshness_cache[user_id] = dict(freshness)
    return freshness


def _polling_settings_for_status(user: dict[str, Any] | None) -> Any:
    if not user:
        return SimpleNamespace(
            background_poll_enabled=settings.background_poll_enabled,
            background_poll_seconds=settings.background_poll_seconds,
            background_poll_active_start=settings.background_poll_active_start,
            background_poll_active_end=settings.background_poll_active_end,
            background_poll_timezone=settings.background_poll_timezone,
        )
    row = storage.get_user_settings(int(user["id"])) or {}
    return SimpleNamespace(
        background_poll_enabled=bool(row.get("background_poll_enabled", settings.background_poll_enabled)),
        background_poll_seconds=int(row.get("background_poll_seconds") or settings.background_poll_seconds),
        background_poll_active_start=row.get("active_start", settings.background_poll_active_start),
        background_poll_active_end=row.get("active_end", settings.background_poll_active_end),
        background_poll_timezone=str(row.get("timezone", settings.background_poll_timezone) or settings.background_poll_timezone),
    )


def _cached_dashboard_stats_for_user(user_id: int) -> dict[str, Any]:
    user_id = int(user_id)
    now = monotonic_time.perf_counter()
    cached = _dashboard_stats_cache.get(user_id)
    if cached and now - cached[0] <= _DASHBOARD_STATS_CACHE_SECONDS:
        return copy.deepcopy(cached[1])
    payload = storage.stats_for_user(
        user_id,
        source_max_age_hours=max(1, int(settings.dashboard_hot_hours)),
        **_dashboard_freshness_kwargs(user_id),
    )
    payload["scan_funnel"] = _latest_scan_funnel(user_id)
    resolved = _resolve_effective_user_settings(storage.get_user(user_id, include_password_hash=True))
    delivered = storage.successfully_notified_item_ids(user_id)
    tiers = Counter()
    never_notified = 0
    for item in storage.list_user_items(
        user_id, limit=500, include_stale=False, lightweight=True,
        source_max_age_hours=max(1, int(settings.dashboard_hot_hours)),
        **resolved.freshness_kwargs(),
    ):
        decorated = _decorate_item_for_user(item, user_id=user_id, pricing_context=_cached_pricing_context_for_user(user_id))
        decision = evaluate_alert_decision(decorated, SimpleNamespace(), resolved)
        if decision.tier:
            tiers[decision.tier] += 1
            if decision.tier in {"GEM", "PROFITABLE"} and str(item.get("item_id") or "") not in delivered:
                never_notified += 1
    delivery = storage.notification_delivery_metrics(user_id)
    payload["notification_delivery"] = {
        **delivery,
        "actionable_listings": sum(tiers.values()),
        "notification_candidates": sum(tiers.values()),
        "never_notified_active_actionable": never_notified,
    }
    payload["unsent_actionable"] = never_notified
    _dashboard_stats_cache[user_id] = (now, copy.deepcopy(payload))
    return payload


def _latest_scan_funnel(user_id: int) -> dict[str, Any]:
    cycle = storage.latest_successful_scan_cycle_for_modes(["shared_background", "local_background", "manual"])
    if not cycle:
        return {}
    traces = [row for row in storage.list_decision_traces_for_cycle(int(cycle["id"]), limit=2000) if int(row.get("user_id") or 0) == int(user_id)]
    tier_counts = Counter()
    whole_phones = 0
    potentially_profitable = 0
    detail_fetched = 0
    lost_reasons = Counter()
    for row in traces:
        trace = row.get("trace") or {}
        decision = trace.get("alert_decision") or {}
        tier = decision.get("tier")
        if tier:
            tier_counts[str(tier)] += 1
        detected = trace.get("detected") or {}
        verdict = trace.get("verdict") or {}
        if float(verdict.get("confidence") or 0) >= 3 and not detected.get("accessory_or_part_only"):
            whole_phones += 1
        if float(decision.get("expected_profit") or 0) > 0 or float(decision.get("upside_profit") or 0) > 0:
            potentially_profitable += 1
        if detected.get("detail_fetch_status") == "succeeded":
            detail_fetched += 1
        lost_reasons.update(decision.get("blocking_reasons") or [])
    return {
        "cycle_id": cycle.get("id"),
        "found": int(cycle.get("items_found") or 0),
        "unique": int(cycle.get("new_items_found") or 0),
        "detail_fetched": detail_fetched,
        "scored": int(cycle.get("items_scored") or len(traces)),
        "whole_phones": whole_phones,
        "potentially_profitable": potentially_profitable,
        "gem": int(tier_counts["GEM"]),
        "profitable": int(tier_counts["PROFITABLE"]),
        "review": int(tier_counts["REVIEW"]),
        "alert_attempted": int(cycle.get("alerts_attempted") or 0),
        "alert_sent": int(cycle.get("alerts_sent") or 0),
        "lost_reasons": lost_reasons.most_common(8),
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
            if background_mode:
                _persist_skipped_scan_cycle(
                    mode="shared_background",
                    reason=f"billing_access_limited: {reason}",
                    user_id=int(user["id"]),
                    users_considered=1,
                    users_scanned=0,
                )
            continue
        resolved = _resolve_effective_user_settings(
            user,
            allow_local_fallback=False,
            ensure_defaults=True,
        )
        if not resolved.user:
            continue
        if background_mode and not resolved.background_poll_enabled:
            _persist_skipped_scan_cycle(
                mode="shared_background",
                reason="polling_disabled",
                user_id=int(resolved.user["id"]),
                current_settings=resolved,
                users_considered=1,
                users_scanned=0,
                keywords=resolved.keywords,
            )
            continue
        if background_mode and not _background_poll_is_active(resolved):
            _persist_skipped_scan_cycle(
                mode="shared_background",
                reason="outside_active_window",
                user_id=int(resolved.user["id"]),
                current_settings=resolved,
                users_considered=1,
                users_scanned=0,
                keywords=resolved.keywords,
            )
            continue
        resolved_users.append(resolved)
    return resolved_users


def _build_shared_search_plan(
    resolved_users: list[EffectiveUserSettings],
    *,
    limit: int,
    keyword_filter: list[str] | None = None,
    include_system_rotation: bool = False,
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
    if include_system_rotation and not normalized_filter:
        for raw_keyword in _rotating_system_searches():
            signature = _search_signature(raw_keyword, limit)
            if signature in plan_by_signature:
                continue
            quality = storage.recent_search_quality(raw_keyword)
            if quality["samples"] >= 6 and quality["duplicate_rate"] >= 0.98 and quality["viable_rate"] < 0.03:
                continue
            plan_by_signature[signature] = SharedSearchPlanEntry(
                signature=signature,
                keyword=raw_keyword,
                limit=int(limit),
                sort="newlyListed",
                marketplace="ebay",
                marketplace_id=settings.ebay_marketplace_id,
                subscribers=list(resolved_users),
            )
    return list(plan_by_signature.values())


ROTATING_SEARCH_MODELS = (
    "iPhone 13", "iPhone 13 Pro", "iPhone 13 Pro Max",
    "iPhone 14", "iPhone 14 Plus", "iPhone 14 Pro", "iPhone 14 Pro Max",
    "iPhone 15", "iPhone 15 Plus", "iPhone 15 Pro", "iPhone 15 Pro Max",
    "iPhone 16", "iPhone 16 Plus", "iPhone 16 Pro", "iPhone 16 Pro Max",
    "iPhone 17", "iPhone 17 Air", "iPhone 17 Pro", "iPhone 17 Pro Max",
)
ROTATING_SEARCH_PHRASES = (
    "bad battery", "cracked back", "cracked screen", "bad LCD", "bad OLED",
    "Face ID issue", "charging issue", "powers on", "fully functional except",
    "read description", "parts or repair", "for repair",
)


def _rotating_system_searches(*, now: datetime | None = None, count: int = 4) -> list[str]:
    current = now or datetime.now(timezone.utc)
    slot = int(current.timestamp() // max(300, settings.background_poll_seconds))
    searches = []
    for offset in range(max(1, count)):
        model = ROTATING_SEARCH_MODELS[(slot * count + offset) % len(ROTATING_SEARCH_MODELS)]
        phrase = ROTATING_SEARCH_PHRASES[(slot + offset * 3) % len(ROTATING_SEARCH_PHRASES)]
        searches.append(f"{model} {phrase}")
    return searches


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
    attempts = storage.list_notification_attempts(user_id=int(data["user_id"]), limit=20)
    last_delivery = next((entry for entry in attempts if entry.get("attempted")), None)
    return {
        "user_id": data["user_id"],
        "discord_enabled": bool(data.get("discord_enabled")),
        "push_enabled": bool(data.get("push_enabled", True)),
        "use_global_discord_webhook": bool(data.get("use_global_discord_webhook")),
        "alerts_enabled": bool(data.get("alerts_enabled")),
        "notify_best_finds": bool(data.get("notify_best_finds")),
        "notify_priority_review": bool(data.get("notify_priority_review")),
        "send_gem_immediately": bool(data.get("send_gem_immediately", True)),
        "send_profitable_immediately": bool(data.get("send_profitable_immediately", True)),
        "review_delivery_mode": str(data.get("review_delivery_mode") or "immediate"),
        "max_review_alerts_per_hour": int(data.get("max_review_alerts_per_hour", 2) or 0),
        "duplicate_suppression_hours": int(data.get("duplicate_suppression_hours", 72) or 72),
        "meaningful_price_drop_amount": float(data.get("meaningful_price_drop_amount", 20) or 0),
        "meaningful_price_drop_percent": float(data.get("meaningful_price_drop_percent", 0.05) or 0),
        "meaningful_profit_increase_amount": float(data.get("meaningful_profit_increase_amount", 25) or 0),
        "meaningful_profit_increase_percent": float(data.get("meaningful_profit_increase_percent", 0.15) or 0),
        "meaningful_roi_increase": float(data.get("meaningful_roi_increase", 0.10) or 0),
        "catchup_enabled": bool(data.get("catchup_enabled", True)),
        "catchup_batch_size": int(data.get("catchup_batch_size", 5) or 5),
        "catchup_include_review": bool(data.get("catchup_include_review", False)),
        "gem_min_expected_profit": float(data.get("gem_min_expected_profit", 75) or 0),
        "profitable_min_expected_profit": float(data.get("profitable_min_expected_profit", 50) or 0),
        "review_min_expected_profit": float(data.get("review_min_expected_profit", 25) or 0),
        "review_min_upside_profit": float(data.get("review_min_upside_profit", 60) or 0),
        "gem_min_roi": float(data.get("gem_min_roi", 0.25) or 0),
        "profitable_min_roi": float(data.get("profitable_min_roi", 0.15) or 0),
        "review_min_roi": float(data.get("review_min_roi", 0.05) or 0),
        "max_listing_age_minutes": int(data.get("max_listing_age_minutes", 360) or 360),
        "discord_webhook": "",
        "discord_webhook_configured": bool(data.get("discord_webhook_configured")),
        "global_discord_webhook_configured": bool(data.get("global_discord_webhook_configured")),
        "webhook_configured": bool(data.get("webhook_configured")),
        "webhook_source": data.get("webhook_source"),
        "notification_ready": bool(data.get("notification_ready")),
        "notification_block_reason": data.get("notification_block_reason"),
        "push_configured": bool(data.get("push_configured")),
        "push_ready": bool(data.get("push_ready")),
        "active_push_subscriptions": int(data.get("active_push_subscriptions") or 0),
        "last_delivery_failed": bool(last_delivery and last_delivery.get("failed")),
        "last_delivery_failure_category": (
            str(last_delivery.get("failure_category") or "") if last_delivery and last_delivery.get("failed") else None
        ),
        "last_delivery_at": last_delivery.get("created_at") if last_delivery else None,
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


def require_admin_user_if_auth_enabled(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
) -> dict[str, Any] | None:
    if not settings.auth_required:
        return None
    user = _get_current_user(credentials, require_token=True)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


def require_research_export_access(
    research_token: Optional[str] = Header(default=None, alias="X-Notifierr-Research-Token"),
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
) -> dict[str, Any]:
    configured = str(settings.research_export_token or "")
    supplied = str(research_token or "")
    if configured and supplied and secrets.compare_digest(configured, supplied):
        return {"role": "research_export", "id": 0}
    if settings.auth_required:
        user = _get_current_user(credentials, require_token=True)
        if not user or user.get("role") != "admin":
            raise HTTPException(status_code=403, detail="Admin or research-export access required")
        return user
    return {"role": "admin", "id": 0}


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
    if settings.process_role in {"api", "combined"}:
        _bootstrap_admin_if_configured()
        if settings.runtime_env == "production" and not storage.any_admin_exists():
            raise RuntimeError("Production API cannot serve without an admin; check ADMIN_EMAIL and ADMIN_PASSWORD")
    _warn_if_hosted_without_encryption()
    task = _start_background_poll_loop()
    try:
        yield
    finally:
        await _stop_background_poll_loop(task)


def _should_start_background_poll_loop() -> bool:
    return bool(settings.background_poll_enabled and settings.process_role in {"scanner", "combined"})


def _configured_worker_count() -> int | None:
    for name in ("WEB_CONCURRENCY", "UVICORN_WORKERS", "GUNICORN_WORKERS"):
        raw = os.getenv(name)
        if not raw:
            continue
        try:
            return int(raw)
        except ValueError:
            logger.warning("Invalid %s=%s; cannot evaluate background polling worker count", name, raw)
            return None
    return None


def _warn_if_background_poll_may_duplicate_across_workers() -> None:
    worker_count = _configured_worker_count()
    if worker_count and worker_count > 1:
        logger.warning(
            "Background polling is enabled with multiple server workers configured worker_count=%s; "
            "each process can run its own poll loop. Use one polling worker process or set "
            "BACKGROUND_POLL_ENABLED=false on web workers.",
            worker_count,
        )


def _start_background_poll_loop() -> asyncio.Task | None:
    global _background_poll_stopping, _background_poll_task
    _background_poll_stopping = False
    logger.info(
        "Background poll startup check pid=%s hostname=%s auth_required=%s background_poll_enabled=%s poll_seconds=%s",
        _process_id(),
        _hostname(),
        settings.auth_required,
        settings.background_poll_enabled,
        settings.background_poll_seconds,
    )
    if not _should_start_background_poll_loop():
        polling_status.task_not_started()
        logger.info(
            "Background poll loop not started pid=%s hostname=%s auth_required=%s background_poll_enabled=%s",
            _process_id(),
            _hostname(),
            settings.auth_required,
            settings.background_poll_enabled,
        )
        return None
    if _background_poll_task and not _background_poll_task.done():
        polling_status.duplicate_start_prevented()
        logger.warning(
            "Background poll loop already running in this process; duplicate startup skipped pid=%s hostname=%s",
            _process_id(),
            _hostname(),
        )
        return _background_poll_task
    _warn_if_background_poll_may_duplicate_across_workers()
    _background_poll_task = asyncio.create_task(_background_poll())
    _background_poll_task.add_done_callback(_on_background_poll_done)
    polling_status.task_started()
    logger.info(
        "Started background poll loop pid=%s hostname=%s auth_required=%s background_poll_enabled=%s poll_seconds=%s",
        _process_id(),
        _hostname(),
        settings.auth_required,
        settings.background_poll_enabled,
        settings.background_poll_seconds,
    )
    return _background_poll_task


def _on_background_poll_done(task: asyncio.Task) -> None:
    global _background_supervisor_task
    if _background_poll_stopping or task.cancelled():
        return
    error = task.exception()
    logger.error("Background poll task exited unexpectedly worker_id=%s error=%r", WORKER_ID, error)

    async def restart() -> None:
        global _background_poll_task
        await asyncio.sleep(5)
        if _background_poll_stopping or _background_poll_task is not task:
            return
        _background_poll_task = None
        _start_background_poll_loop()

    _background_supervisor_task = asyncio.create_task(restart())


async def _stop_background_poll_loop(task: asyncio.Task | None) -> None:
    global _background_poll_stopping, _background_poll_task, _background_supervisor_task
    _background_poll_stopping = True
    if _background_supervisor_task and not _background_supervisor_task.done():
        _background_supervisor_task.cancel()
    _background_supervisor_task = None
    if not task:
        return
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    if _background_poll_task is task:
        _background_poll_task = None
    storage.release_worker_lease(BACKGROUND_LEASE_NAME, WORKER_ID)
    if _background_worker_started_at:
        storage.update_worker_heartbeat(
            worker_name="background_poll",
            process_id=_process_id(),
            hostname=_hostname(),
            started_at=_background_worker_started_at,
            status="stopped",
        )


app = FastAPI(title="Notifierr", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)
if settings.trusted_hosts:
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.trusted_hosts)


@app.get("/health/live")
def health_live() -> dict[str, bool]:
    return {"ok": True}


@app.get("/health/ready")
def health_ready() -> dict[str, bool]:
    try:
        if settings.runtime_env == "production":
            from .schema import check_schema
            check_schema(storage.engine)
        else:
            with storage.connect() as connection:
                connection.execute("SELECT 1")
    except Exception:
        logger.exception("API readiness database check failed")
        raise HTTPException(status_code=503, detail="Database or schema unavailable") from None
    return {"ok": True}


@app.get("/health")
def health() -> dict[str, Any]:
    snapshot = polling_status.snapshot()
    scan_started_at = snapshot.get("last_cycle_started_at") if snapshot.get("cycle_running") else None
    scan_started_dt = _parse_utc_datetime(str(scan_started_at or ""))
    scan_duration_seconds = (
        int((datetime.now(timezone.utc) - scan_started_dt).total_seconds())
        if scan_started_dt else None
    )
    return {
        "ok": True,
        "api_responsive": True,
        "ebay_configured": settings.ebay_configured,
        "discord_configured": settings.discord_configured,
        "global_discord_webhook_configured": settings.discord_configured,
        "notification_ready": None,
        "notification_block_reason": "authenticated_user_context_required" if settings.auth_required else None,
        "auth_required": settings.auth_required,
        "state": "scanning" if snapshot.get("cycle_running") else "ready",
        "scan_started_at": scan_started_at,
        "scan_duration_seconds": scan_duration_seconds,
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


def _trace_replay_user(requested_user_id: int | None, admin_user: dict[str, Any] | None) -> dict[str, Any]:
    if requested_user_id:
        user = storage.get_user(int(requested_user_id), include_password_hash=True)
        if not user:
            raise HTTPException(status_code=404, detail="Replay user not found")
        return user
    if settings.auth_required:
        if not admin_user:
            raise HTTPException(status_code=401, detail="Authentication required")
        return admin_user
    user = storage.get_local_settings_user()
    if not user:
        raise HTTPException(status_code=404, detail="Replay user not found")
    return user


def _source_cycle_replay_item_ids(cycle_id: int | None) -> set[str]:
    if not cycle_id:
        return set()
    if not storage.get_scan_cycle(int(cycle_id)):
        raise HTTPException(status_code=404, detail="Source scan cycle not found")
    traces = storage.list_decision_traces_for_cycle(int(cycle_id), limit=1000)
    item_ids = {
        str((row.get("trace") or {}).get("listing_id") or row.get("marketplace_item_id") or "").strip()
        for row in traces
    }
    return {item_id for item_id in item_ids if item_id}


def _filter_replay_items(
    items: list[dict[str, Any]],
    request: TraceReplayRequest,
    *,
    source_item_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    filtered = list(items)
    if source_item_ids:
        filtered = [item for item in filtered if str(item.get("item_id") or "") in source_item_ids]
    requested_item_ids = {str(item_id).strip() for item_id in (request.item_ids or []) if str(item_id).strip()}
    if requested_item_ids:
        filtered = [item for item in filtered if str(item.get("item_id") or "") in requested_item_ids]
    if request.active_only:
        filtered = [
            item
            for item in filtered
            if not item.get("stale")
            and item.get("user_status") not in {"ignored", "rejected"}
            and item.get("availability_status") not in {"sold", "ended", "unavailable"}
        ]
    if request.non_stale_only:
        filtered = [item for item in filtered if not item.get("stale")]
    if request.not_ignored_only:
        filtered = [item for item in filtered if item.get("user_status") != "ignored"]
    return filtered[: request.limit]


def _raw_listing_for_replay(item: dict[str, Any]) -> dict[str, Any]:
    raw_keys = {
        "marketplace",
        "item_id",
        "title",
        "price",
        "shipping",
        "total_cost",
        "condition",
        "item_url",
        "image_url",
        "seller_username",
        "seller_feedback_percentage",
        "seller_feedback_score",
        "raw_description",
        "item_origin_at",
        "found_at",
        "raw_json",
        "availability_status",
        "buying_option_summary",
        "item_end_at",
        "last_availability_checked_at",
        "availability_note",
        "user_status",
        "alerted_at",
    }
    return {key: item.get(key) for key in raw_keys if key in item}


def _comparison_changed_reasons(
    *,
    persisted_status: str,
    rescored_status: str,
    persisted_alert_eligible: bool,
    rescored_alert_eligible: bool,
    persisted_manual_review_reason: str,
    rescored_manual_review_reason: str,
    persisted_parts_pricing_status: str,
    rescored_parts_pricing_status: str,
    persisted_hard_risks: list[str],
    rescored_hard_risks: list[str],
) -> list[str]:
    reasons: list[str] = []
    if persisted_status != rescored_status:
        reasons.append("status_changed")
    if persisted_alert_eligible != rescored_alert_eligible:
        reasons.append("alert_eligibility_changed")
    if persisted_manual_review_reason != rescored_manual_review_reason:
        reasons.append("manual_review_reason_changed")
    if persisted_parts_pricing_status != rescored_parts_pricing_status:
        reasons.append("parts_pricing_status_changed")
    added_hard_risks = sorted(set(rescored_hard_risks) - set(persisted_hard_risks))
    removed_hard_risks = sorted(set(persisted_hard_risks) - set(rescored_hard_risks))
    reasons.extend(f"added_hard_risk:{flag}" for flag in added_hard_risks)
    reasons.extend(f"removed_hard_risk:{flag}" for flag in removed_hard_risks)
    return reasons


def _attach_rescore_comparison(
    trace: dict[str, Any],
    *,
    persisted_item: dict[str, Any],
    rescored_item: dict[str, Any],
    rescore_from_raw: bool,
) -> dict[str, Any]:
    verdict = trace.get("verdict") or {}
    persisted_status = str(persisted_item.get("status") or "")
    rescored_status = str(rescored_item.get("status") or "")
    persisted_score = float(persisted_item.get("score") or 0)
    rescored_score = float(rescored_item.get("score") or 0)
    persisted_alert_eligible = bool(persisted_item.get("alert_eligible"))
    rescored_alert_eligible = bool(rescored_item.get("alert_eligible"))
    persisted_manual_review_reason = str(persisted_item.get("manual_review_reason") or "")
    rescored_manual_review_reason = str(rescored_item.get("manual_review_reason") or "")
    persisted_parts_pricing_status = str(persisted_item.get("parts_pricing_status") or "")
    rescored_parts_pricing_status = str(rescored_item.get("parts_pricing_status") or "")
    persisted_hard_risks = _jsonable_flags(persisted_item.get("hard_reject_flags"))
    rescored_hard_risks = _jsonable_flags(rescored_item.get("hard_reject_flags"))
    comparison = {
        "rescore_from_raw": bool(rescore_from_raw),
        "persisted_status": persisted_status,
        "persisted_score": persisted_score,
        "persisted_manual_review_reason": persisted_manual_review_reason,
        "persisted_parts_pricing_status": persisted_parts_pricing_status,
        "persisted_alert_eligible": persisted_alert_eligible,
        "rescored_status": rescored_status,
        "rescored_score": rescored_score,
        "rescored_manual_review_reason": rescored_manual_review_reason,
        "rescored_parts_pricing_status": rescored_parts_pricing_status,
        "rescored_normalized_bucket": verdict.get("normalized_bucket") or verdict.get("bucket"),
        "rescored_alert_eligible": rescored_alert_eligible,
        "changed_status": persisted_status != rescored_status,
        "changed_score": abs(persisted_score - rescored_score) >= 0.01,
        "score_delta": round(rescored_score - persisted_score, 2),
        "changed_alert_eligibility": persisted_alert_eligible != rescored_alert_eligible,
        "changed_reasons": _comparison_changed_reasons(
            persisted_status=persisted_status,
            rescored_status=rescored_status,
            persisted_alert_eligible=persisted_alert_eligible,
            rescored_alert_eligible=rescored_alert_eligible,
            persisted_manual_review_reason=persisted_manual_review_reason,
            rescored_manual_review_reason=rescored_manual_review_reason,
            persisted_parts_pricing_status=persisted_parts_pricing_status,
            rescored_parts_pricing_status=rescored_parts_pricing_status,
            persisted_hard_risks=persisted_hard_risks,
            rescored_hard_risks=rescored_hard_risks,
        ),
    }
    return {**trace, "comparison": comparison}


def replay_listing_decision_traces(
    request: TraceReplayRequest,
    *,
    admin_user: dict[str, Any] | None = None,
) -> dict[str, Any]:
    write_mode = bool(request.write_traces and not request.dry_run)
    if not request.dry_run and not request.write_traces:
        raise HTTPException(status_code=400, detail="Write mode requires write_traces=true")
    if write_mode and (not admin_user or admin_user.get("role") != "admin"):
        raise HTTPException(status_code=403, detail="Authenticated admin required for replay writes")
    user = _trace_replay_user(request.user_id, admin_user)
    resolved = _resolve_effective_user_settings(
        user, allow_local_fallback=False, ensure_defaults=False, migrate_plaintext_webhook=False,
    )
    if not resolved.user:
        raise HTTPException(status_code=400, detail="No effective user is available for trace replay")
    user_id = int(resolved.user["id"])
    source_item_ids = _source_cycle_replay_item_ids(request.source_cycle_id)
    raw_items = storage.list_user_items(
        user_id,
        limit=500,
        include_ignored=True,
        include_stale=True,
        **resolved.freshness_kwargs(),
    )
    items = _filter_replay_items(raw_items, request, source_item_ids=source_item_ids)
    cycle_id = None
    if not request.dry_run:
        cycle_id = _create_scan_cycle(
            mode="trace_replay",
            user_id=user_id,
            current_settings=resolved,
            users_considered=1,
            users_scanned=1,
            keywords_searched=resolved.keywords,
            sources_checked=["stored_marketplace_items", "user_item_states"],
        )
    counters = ScanCycleCounters()
    pricing_context = _build_user_pricing_context(user_id)
    generated_traces: list[dict[str, Any]] = []
    try:
        for stored_item in items:
            current_status = str(stored_item.get("status") or "")
            current_bucket = current_status
            scoring_input = _raw_listing_for_replay(stored_item) if request.rescore_from_raw else stored_item
            result, item_overrides = _score_listing_for_user(
                scoring_input,
                resolved,
                pricing_context=pricing_context,
            )
            replay_item = {**stored_item, **scoring_input, **result.as_item_fields()}
            replay_item.update(
                _repair_snapshot_for_item(
                    replay_item,
                    user_id=user_id,
                    pricing_context=pricing_context,
                    correction=item_overrides["correction"],
                )
            )
            replay_item = _apply_availability_and_auction_policy(replay_item)
            replay_item = _decorate_item_for_user(replay_item, user_id=user_id, pricing_context=pricing_context)
            alert_block_reasons = _alert_block_reasons(replay_item, result, resolved)
            trace = _build_decision_trace(
                replay_item,
                result,
                resolved,
                scan_cycle_id=cycle_id,
                alert_block_reasons=alert_block_reasons,
                current_app_status=current_status,
                current_app_bucket=current_bucket,
            )
            trace = _attach_rescore_comparison(
                trace,
                persisted_item=stored_item,
                rescored_item=replay_item,
                rescore_from_raw=request.rescore_from_raw,
            )
            if request.write_traces and not request.dry_run:
                storage.record_listing_decision_trace(
                    user_id=user_id,
                    item_id=str(stored_item.get("item_id") or ""),
                    scan_cycle_id=cycle_id,
                    trace=trace,
                )
            counters.record_trace(trace)
            generated_traces.append(trace)
    except Exception as exc:
        if cycle_id is not None:
            _finish_scan_cycle_from_summary(
                cycle_id,
                status="failed",
                summary={"scanned": len(items), "keywords": resolved.keywords},
                counters=counters,
                error_message=_scan_cycle_error_message(exc),
                users_considered=1,
                users_scanned=1,
                keywords_searched=resolved.keywords,
                sources_checked=["stored_marketplace_items", "user_item_states"],
            )
        raise

    summary = {
        "mode": "trace_replay",
        "scanned": len(items),
        "new_items_found": 0,
        "duplicates_skipped": 0,
        "alerts_sent": 0,
        "keywords": resolved.keywords,
    }
    if cycle_id is not None:
        _finish_scan_cycle_from_summary(
            cycle_id,
            status="completed",
            summary=summary,
            counters=counters,
            users_considered=1,
            users_scanned=1,
            keywords_searched=resolved.keywords,
            sources_checked=["stored_marketplace_items", "user_item_states"],
        )
        cycle = storage.get_scan_cycle(cycle_id) or {"id": cycle_id, "cycle_id": cycle_id}
    else:
        cycle = {"id": None, "cycle_id": None, "mode": "trace_replay", "status": "dry_run", **summary}
    return {
        "cycle": cycle,
        "scan_cycle_id": cycle_id,
        "replayed": len(items),
        "available_items": len(raw_items),
        "filters": request.model_dump() if hasattr(request, "model_dump") else request.dict(),
        "rescore_from_raw": bool(request.rescore_from_raw),
        "write_traces": bool(request.write_traces),
        "dry_run": bool(request.dry_run),
        "traces_written": len(items) if request.write_traces and not request.dry_run else 0,
        "dry_run_traces": generated_traces[:20] if request.dry_run or not request.write_traces else [],
    }


def _trace_value_counts(traces: list[dict[str, Any]], getter) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for row in traces:
        value = getter(row.get("trace") or {})
        if isinstance(value, list):
            _increment(counts, [str(entry) for entry in value if entry])
        else:
            counts[str(value or "unknown")] += 1
    return dict(counts)


def _trace_sample_row(row: dict[str, Any]) -> dict[str, Any]:
    trace = row.get("trace") or {}
    verdict = trace.get("verdict") or {}
    pricing = trace.get("pricing") or {}
    reasons = trace.get("reasons") or {}
    detected = trace.get("detected") or {}
    comparison = trace.get("comparison") or {}
    sample = {
        "item_id": trace.get("listing_id") or row.get("marketplace_item_id"),
        "title": trace.get("title") or row.get("title") or "",
        "current_app_bucket": verdict.get("current_app_bucket"),
        "normalized_bucket": verdict.get("normalized_bucket") or verdict.get("bucket"),
        "score": verdict.get("score"),
        "profit_mid": pricing.get("profit_mid"),
        "model": detected.get("model"),
        "storage": detected.get("storage"),
        "missing_data": reasons.get("missing_data") or [],
        "blocking_rules": reasons.get("blocking_rules") or [],
        "hard_risks": detected.get("hard_risks") or [],
        "soft_risks": detected.get("soft_risks") or [],
    }
    if detected.get("description_signals"):
        sample["description_signals"] = detected.get("description_signals")
    for key in (
        "persisted_status",
        "persisted_score",
        "persisted_manual_review_reason",
        "persisted_parts_pricing_status",
        "persisted_alert_eligible",
        "rescored_status",
        "rescored_score",
        "rescored_manual_review_reason",
        "rescored_normalized_bucket",
        "rescored_alert_eligible",
        "changed_status",
        "changed_score",
        "changed_alert_eligibility",
        "changed_reasons",
    ):
        if key in comparison:
            sample[key] = comparison.get(key)
    return sample


def _trace_description_extraction_miss(row: dict[str, Any]) -> dict[str, Any] | None:
    trace = row.get("trace") or {}
    detected = trace.get("detected") or {}
    reasons = trace.get("reasons") or {}
    verdict = trace.get("verdict") or {}
    signals = detected.get("description_signals") or {}
    useful_signal_groups = {
        "whole_phone_evidence": signals.get("whole_phone_evidence") or [],
        "functionality_signals": signals.get("functionality_signals") or [],
        "included_device_signals": signals.get("included_device_signals") or [],
        "clean_activation_signals": signals.get("clean_activation_signals") or [],
        "repair_detail_signals": signals.get("repair_detail_signals") or [],
    }
    useful_count = sum(len(values) for values in useful_signal_groups.values())
    if useful_count < 3:
        return None
    extracted_flags = set(reasons.get("positive") or [])
    extracted_signals = set()
    if detected.get("model") not in {None, "", "unknown"}:
        extracted_signals.add("model")
    if detected.get("storage"):
        extracted_signals.add("storage")
    if detected.get("carrier_status") not in {None, "", "unknown"}:
        extracted_signals.add("carrier")
    if extracted_flags.intersection({"powers_on", "face_id_works"}):
        extracted_signals.add("functionality")
    if "clean_imei" in extracted_flags:
        extracted_signals.add("clean_activation")
    if extracted_flags.intersection({"cracked_screen", "screen_display_issue", "bad_oled", "bad_battery", "back_glass_cracked", "camera_lens_cracked", "charging_port_issue"}):
        extracted_signals.add("repair_detail")

    missed = []
    if useful_signal_groups["included_device_signals"] and "included_device" not in extracted_signals:
        missed.append("included_device")
    if useful_signal_groups["whole_phone_evidence"] and "whole_phone" not in extracted_signals:
        missed.append("whole_phone_evidence")
    if useful_signal_groups["functionality_signals"] and "functionality" not in extracted_signals:
        missed.append("functionality")
    if useful_signal_groups["clean_activation_signals"] and "clean_activation" not in extracted_signals:
        missed.append("clean_activation")
    if useful_signal_groups["repair_detail_signals"] and "repair_detail" not in extracted_signals:
        missed.append("repair_detail")
    if not missed:
        return None

    normalized_bucket = verdict.get("normalized_bucket") or verdict.get("bucket")
    current_bucket = verdict.get("current_app_bucket")
    blocked_reasons = [
        *(reasons.get("missing_data") or []),
        *(reasons.get("blocking_rules") or []),
        *(reasons.get("non_blocking_warnings") or []),
    ]
    if normalized_bucket not in {"needs_data", "watch", "good"} and current_bucket not in {"risky", "needs_data"}:
        return None
    if not any(
        reason in blocked_reasons
        for reason in (
            "estimated_profit_unavailable",
            "result_alert_ineligible",
            "Parts estimate not verified",
            "Missing part price",
            "No specific repair issue detected",
            "Parts-only ambiguous",
            "Read description listing",
            "Expected profit below threshold",
        )
    ):
        return None

    sample = _trace_sample_row(row)
    sample.update(
        {
            "category": "app_failed_to_extract_description_signals",
            "raw_description_evidence": useful_signal_groups,
            "app_extracted_signals": sorted(extracted_signals),
            "missed_signals": missed,
            "why_it_matters": "Stored description/aspects contain sortable whole-phone, functional, activation, or repair evidence beyond the app's extracted flags.",
            "suggested_parser_rule": "Parse included-device, specifications, cosmetic, and functionality sections into deterministic scorer flags.",
            "better_bucket": "Priority Review" if useful_count >= 5 else "Watch",
            "confidence": "high" if useful_count >= 5 else "medium",
        }
    )
    return sample


def _trace_text_blob(trace: dict[str, Any]) -> str:
    pieces = [
        trace.get("title") or "",
        json.dumps(trace.get("detected") or {}, sort_keys=True),
        json.dumps(trace.get("reasons") or {}, sort_keys=True),
    ]
    return " ".join(str(piece).lower() for piece in pieces)


def _trace_has_display_component_reject(trace: dict[str, Any]) -> bool:
    hard_risks = set(((trace.get("detected") or {}).get("hard_risks") or []))
    return bool(
        hard_risks.intersection(
            {
                "screen_part_not_phone",
                "display_assembly_not_phone",
                "digitizer_not_phone",
                "oled_lcd_part_not_phone",
                "glass_only_not_phone",
            }
        )
    )


def _trace_has_accessory_reject(trace: dict[str, Any]) -> bool:
    detected = trace.get("detected") or {}
    hard_risks = detected.get("hard_risks") or []
    return bool(detected.get("accessory_or_part_only")) or any(
        str(flag).endswith("_not_phone") or flag == "lot_not_single_phone" for flag in hard_risks
    )


def _trace_has_reason(trace: dict[str, Any], reason: str) -> bool:
    reasons = trace.get("reasons") or {}
    return any(reason in (reasons.get(key) or []) for key in ("blocking_rules", "non_blocking_warnings", "missing_data"))


def build_trace_audit_export(cycle_id: int, *, limit: int = 500) -> dict[str, Any]:
    cycle = storage.get_scan_cycle(cycle_id)
    if not cycle:
        raise HTTPException(status_code=404, detail="Scan cycle not found")
    traces = storage.list_decision_traces_for_cycle(cycle_id, limit=limit)
    required_verdict_fields = {
        "score",
        "current_app_bucket",
        "current_app_status",
        "normalized_bucket",
        "alert_eligible",
        "manual_review_needed",
    }
    empty_reason_rows: list[dict[str, Any]] = []
    missing_verdict_rows: list[dict[str, Any]] = []
    disagreement_rows: list[dict[str, Any]] = []
    model_mismatch_rows: list[dict[str, Any]] = []
    imei_risk_rows: list[dict[str, Any]] = []
    changed_rows: list[dict[str, Any]] = []
    description_extraction_miss_rows: list[dict[str, Any]] = []

    for row in traces:
        trace = row.get("trace") or {}
        reasons = trace.get("reasons") or {}
        verdict = trace.get("verdict") or {}
        reason_keys = ("positive", "negative", "missing_data", "blocking_rules", "non_blocking_warnings")
        empty_fields = [key for key in reason_keys if not reasons.get(key)]
        if empty_fields:
            sample = _trace_sample_row(row)
            sample["empty_reason_fields"] = empty_fields
            empty_reason_rows.append(sample)
        missing_fields = sorted(field for field in required_verdict_fields if field not in verdict)
        if missing_fields:
            sample = _trace_sample_row(row)
            sample["missing_verdict_fields"] = missing_fields
            missing_verdict_rows.append(sample)
        current_bucket = verdict.get("current_app_bucket")
        normalized_bucket = verdict.get("normalized_bucket") or verdict.get("bucket")
        if current_bucket and normalized_bucket and current_bucket != normalized_bucket:
            disagreement_rows.append(_trace_sample_row(row))
        blob = _trace_text_blob(trace)
        if "mismatch" in blob:
            model_mismatch_rows.append(_trace_sample_row(row))
        if any(term in blob for term in ("imei", "esn", "icloud", "locked", "owner", "check")):
            imei_risk_rows.append(_trace_sample_row(row))
        comparison = trace.get("comparison") or {}
        if comparison.get("changed_status") or comparison.get("changed_score") or comparison.get("changed_alert_eligibility"):
            changed_rows.append(_trace_sample_row(row))
        extraction_miss = _trace_description_extraction_miss(row)
        if extraction_miss:
            description_extraction_miss_rows.append(extraction_miss)

    needs_data_rows = [
        _trace_sample_row(row)
        for row in traces
        if (row.get("trace") or {}).get("reasons", {}).get("missing_data")
        or ((row.get("trace") or {}).get("verdict", {}).get("normalized_bucket") == "needs_data")
    ]
    missed_gem_rows = [
        _trace_sample_row(row)
        for row in traces
        if ((row.get("trace") or {}).get("verdict", {}).get("normalized_bucket") or (row.get("trace") or {}).get("verdict", {}).get("bucket")) == "gem"
        and (row.get("trace") or {}).get("verdict", {}).get("current_app_bucket") not in {"candidate", "alerted", "gem"}
    ]
    false_positive_rows = [
        _trace_sample_row(row)
        for row in traces
        if (row.get("trace") or {}).get("verdict", {}).get("current_app_bucket") in {"candidate", "alerted"}
        and ((row.get("trace") or {}).get("verdict", {}).get("normalized_bucket") or (row.get("trace") or {}).get("verdict", {}).get("bucket")) not in {"gem", "good"}
    ]
    return {
        "scan_cycle_id": cycle_id,
        "cycle": cycle,
        "total_traces": len(traces),
        "bucket_field_definitions": {
            "current_app_bucket": "Existing app queue/status bucket persisted for the listing.",
            "current_app_status": "Existing scorer status used by the app workflow.",
            "normalized_bucket": "Audit-derived bucket for trace review; it may disagree with app status by design.",
        },
        "bucket_counts": _trace_value_counts(traces, lambda trace: (trace.get("verdict") or {}).get("bucket")),
        "current_app_bucket_counts": _trace_value_counts(traces, lambda trace: (trace.get("verdict") or {}).get("current_app_bucket")),
        "normalized_bucket_counts": _trace_value_counts(
            traces,
            lambda trace: (trace.get("verdict") or {}).get("normalized_bucket") or (trace.get("verdict") or {}).get("bucket"),
        ),
        "needs_data_reason_counts": _trace_value_counts(traces, lambda trace: (trace.get("reasons") or {}).get("missing_data") or []),
        "alert_blocker_counts": _trace_value_counts(traces, lambda trace: (trace.get("reasons") or {}).get("blocking_rules") or []),
        "hard_risk_counts": _trace_value_counts(traces, lambda trace: (trace.get("detected") or {}).get("hard_risks") or []),
        "soft_risk_counts": _trace_value_counts(traces, lambda trace: (trace.get("detected") or {}).get("soft_risks") or []),
        "model_unknown_count": sum(1 for row in traces if ((row.get("trace") or {}).get("detected") or {}).get("model") in {None, "", "unknown"}),
        "storage_unknown_count": sum(1 for row in traces if not ((row.get("trace") or {}).get("detected") or {}).get("storage")),
        "carrier_lock_unknown_count": sum(
            1
            for row in traces
            if ((row.get("trace") or {}).get("detected") or {}).get("carrier_status") in {None, "", "unknown"}
        ),
        "resale_missing_count": sum(1 for row in traces if not ((row.get("trace") or {}).get("pricing") or {}).get("resale_mid")),
        "parts_cost_missing_count": sum(1 for row in traces if not ((row.get("trace") or {}).get("pricing") or {}).get("parts_cost")),
        "pricing_confidence_counts": _trace_value_counts(traces, lambda trace: (trace.get("pricing") or {}).get("pricing_confidence")),
        "total_rescored_items": sum(1 for row in traces if ((row.get("trace") or {}).get("comparison") or {}).get("rescore_from_raw")),
        "accessory_part_only_reject_count": sum(1 for row in traces if _trace_has_accessory_reject(row.get("trace") or {})),
        "display_screen_assembly_reject_count": sum(1 for row in traces if _trace_has_display_component_reject(row.get("trace") or {})),
        "high_resale_new_model_gated_count": sum(
            1 for row in traces if _trace_has_reason(row.get("trace") or {}, "High-resale model needs stronger verification")
        ),
        "storage_unknown_review_routed_count": sum(
            1
            for row in traces
            if "storage_unknown" in (((row.get("trace") or {}).get("reasons") or {}).get("missing_data") or [])
            and (((row.get("trace") or {}).get("verdict") or {}).get("normalized_bucket") in {"good", "watch"})
        ),
        "carrier_unknown_warning_count": sum(
            1
            for row in traces
            if "carrier_unknown" in (((row.get("trace") or {}).get("reasons") or {}).get("missing_data") or [])
        ),
        "alert_eligible_count": sum(1 for row in traces if (((row.get("trace") or {}).get("verdict") or {}).get("alert_eligible") is True)),
        "alert_blocked_count": sum(1 for row in traces if (((row.get("trace") or {}).get("reasons") or {}).get("blocking_rules") or [])),
        "changed_status_count": sum(1 for row in traces if (((row.get("trace") or {}).get("comparison") or {}).get("changed_status") is True)),
        "changed_alert_eligibility_count": sum(
            1 for row in traces if (((row.get("trace") or {}).get("comparison") or {}).get("changed_alert_eligibility") is True)
        ),
        "description_extraction_miss_count": len(description_extraction_miss_rows),
        "current_app_bucket_normalized_bucket_disagreement_count": len(disagreement_rows),
        "traces_with_empty_reason_arrays": empty_reason_rows[:20],
        "traces_with_missing_verdict_fields": missing_verdict_rows[:20],
        "samples": {
            "needs_data": needs_data_rows[:20],
            "possible_missed_gems": missed_gem_rows[:20],
            "possible_false_positives": false_positive_rows[:20],
            "model_mismatch_indicators": model_mismatch_rows[:20],
            "imei_esn_check_locked_owner_risks": imei_risk_rows[:20],
            "bucket_disagreements": disagreement_rows[:20],
            "changed_traces": changed_rows[:20],
            "description_extraction_misses": description_extraction_miss_rows[:20],
        },
    }


def build_latest_fresh_scan_audit_export(*, limit: int = 500) -> dict[str, Any]:
    cycle = storage.latest_successful_fresh_scan_cycle()
    latest_failed_or_skipped = storage.latest_failed_or_skipped_scan_cycle()
    if not cycle:
        return {
            "latest_successful_fresh_scan_cycle": None,
            "latest_failed_or_skipped_scan_cycle": latest_failed_or_skipped,
            "trace_export": None,
        }
    export = build_trace_audit_export(int(cycle["id"]), limit=limit)
    return {
        "latest_successful_fresh_scan_cycle": cycle,
        "latest_failed_or_skipped_scan_cycle": latest_failed_or_skipped,
        "trace_export": export,
    }


@app.get("/admin/scan/cycles")
def admin_scan_cycles(
    limit: int = Query(default=50, ge=1, le=500),
    admin_user: Optional[dict[str, Any]] = Depends(require_admin_user_if_auth_enabled),
) -> dict[str, Any]:
    del admin_user
    return {"cycles": storage.list_scan_cycles(limit=limit)}


@app.get("/admin/scan/cycles/{cycle_id}")
def admin_scan_cycle(
    cycle_id: int,
    admin_user: Optional[dict[str, Any]] = Depends(require_admin_user_if_auth_enabled),
) -> dict[str, Any]:
    del admin_user
    cycle = storage.get_scan_cycle(cycle_id)
    if not cycle:
        raise HTTPException(status_code=404, detail="Scan cycle not found")
    return {"cycle": cycle}


@app.get("/admin/scan/cycles/{cycle_id}/decision-traces")
def admin_scan_cycle_decision_traces(
    cycle_id: int,
    limit: int = Query(default=500, ge=1, le=1000),
    admin_user: Optional[dict[str, Any]] = Depends(require_admin_user_if_auth_enabled),
) -> dict[str, Any]:
    del admin_user
    if not storage.get_scan_cycle(cycle_id):
        raise HTTPException(status_code=404, detail="Scan cycle not found")
    return {"decision_traces": storage.list_decision_traces_for_cycle(cycle_id, limit=limit)}


@app.get("/admin/scan/fresh-export")
def admin_latest_fresh_scan_export(
    limit: int = Query(default=500, ge=1, le=1000),
    admin_user: Optional[dict[str, Any]] = Depends(require_admin_user_if_auth_enabled),
) -> dict[str, Any]:
    del admin_user
    return build_latest_fresh_scan_audit_export(limit=limit)


@app.get("/admin/research/bundles")
def research_bundle_index(
    after: str = Query(default=""),
    updated_after: str = Query(default=""),
    limit: int = Query(default=365, ge=1, le=1000),
    _access: dict[str, Any] = Depends(require_research_export_access),
) -> dict[str, Any]:
    del _access
    if after:
        try:
            date.fromisoformat(after)
        except ValueError:
            raise HTTPException(status_code=400, detail="after must be YYYY-MM-DD") from None
    if updated_after:
        try:
            datetime.fromisoformat(updated_after.replace("Z", "+00:00"))
        except ValueError:
            raise HTTPException(status_code=400, detail="updated_after must be an ISO-8601 timestamp") from None
    bundles = storage.list_research_bundle_days(
        after_day=after, updated_after=updated_after, limit=limit,
    )
    cursor = max((str(bundle.get("updated_at") or "") for bundle in bundles), default=updated_after)
    return {
        "bundles": bundles,
        "cursor": cursor,
        "compact_after_hours": settings.research_compact_after_hours,
        "format": "jsonl.gz",
    }


@app.get("/admin/research/bundles/{bundle_day}")
def research_bundle_download(
    bundle_day: str,
    interesting_only: bool = False,
    _access: dict[str, Any] = Depends(require_research_export_access),
) -> Response:
    del _access
    try:
        date.fromisoformat(bundle_day)
    except ValueError:
        raise HTTPException(status_code=400, detail="bundle_day must be YYYY-MM-DD") from None
    rows = storage.list_research_evidence(evidence_day=bundle_day, limit=10000)
    if interesting_only:
        rows = [row for row in rows if row.get("interesting")]
    summary = {
        "type": "summary",
        "schema": 1,
        "day": bundle_day,
        "records": len(rows),
        "interesting_records": sum(bool(row.get("interesting")) for row in rows),
        "generated_at": _utc_now_iso(),
    }
    lines = [json.dumps(summary, separators=(",", ":"), sort_keys=True)]
    for row in rows:
        lines.append(json.dumps({"type": "evidence", **row}, separators=(",", ":"), sort_keys=True))
    content = gzip.compress(("\n".join(lines) + "\n").encode("utf-8"), compresslevel=9)
    return Response(
        content=content,
        media_type="application/gzip",
        headers={
            "Content-Disposition": f'attachment; filename="notifierr-research-{bundle_day}.jsonl.gz"',
            "X-Notifierr-Records": str(len(rows)),
        },
    )


@app.post("/admin/scan/trace-replay")
def admin_trace_replay(
    request: TraceReplayRequest,
    admin_user: Optional[dict[str, Any]] = Depends(require_admin_user_if_auth_enabled),
) -> dict[str, Any]:
    return replay_listing_decision_traces(request, admin_user=admin_user)


@app.get("/admin/scan/cycles/{cycle_id}/trace-export")
def admin_scan_cycle_trace_export(
    cycle_id: int,
    limit: int = Query(default=500, ge=1, le=1000),
    admin_user: Optional[dict[str, Any]] = Depends(require_admin_user_if_auth_enabled),
) -> dict[str, Any]:
    del admin_user
    return build_trace_audit_export(cycle_id, limit=limit)


@app.get("/admin/items/{item_id}/decision-traces")
def admin_item_decision_traces(
    item_id: str,
    limit: int = Query(default=100, ge=1, le=500),
    admin_user: Optional[dict[str, Any]] = Depends(require_admin_user_if_auth_enabled),
) -> dict[str, Any]:
    del admin_user
    return {"decision_traces": storage.list_decision_traces_for_item(item_id, limit=limit)}


@app.get("/admin/sources/status")
def admin_sources_status(admin_user: Optional[dict[str, Any]] = Depends(require_admin_user_if_auth_enabled)) -> dict[str, Any]:
    del admin_user
    rows = {str(row.get("source") or ""): row for row in storage.list_source_statuses()}
    sources = [_source_status_payload(rows.get("ebay"), source="ebay")]
    return {"sources": sources}


@app.get("/admin/worker/status")
def admin_worker_status(admin_user: Optional[dict[str, Any]] = Depends(require_admin_user_if_auth_enabled)) -> dict[str, Any]:
    del admin_user
    return {"workers": storage.get_worker_heartbeats(), "polling_status": polling_status.snapshot()}


@app.get("/admin/storage/status")
def admin_storage_status(admin_user: Optional[dict[str, Any]] = Depends(require_admin_user_if_auth_enabled)) -> dict[str, Any]:
    del admin_user
    return storage.storage_metrics()


@app.get("/admin/polling/status")
def admin_polling_status(status_user: dict[str, Any] = Depends(require_polling_status_user)) -> dict[str, Any]:
    snapshot = polling_status.snapshot()
    now = datetime.now(timezone.utc)
    global_lease = storage.get_worker_lease(GLOBAL_SCAN_LEASE_NAME)
    global_expiry = _parse_utc_datetime(str((global_lease or {}).get("expires_at") or ""))
    global_active = bool(global_lease and global_expiry and global_expiry > now)
    global_owner = bool(
        global_active
        and str((global_lease or {}).get("worker_id") or "").startswith(f"{WORKER_ID}:scan:")
        and int((global_lease or {}).get("process_id") or 0) == _process_id()
    )
    if global_owner:
        interval = resolve_poll_interval(configured=settings.background_poll_seconds)
        lease = storage.get_worker_lease(BACKGROUND_LEASE_NAME)
        heartbeat = next((row for row in storage.get_worker_heartbeats() if row.get("worker_name") == "background_poll"), None)
        last_heartbeat = (lease or {}).get("heartbeat_at") or (heartbeat or {}).get("last_seen_at")
        heartbeat_dt = _parse_utc_datetime(str(last_heartbeat or ""))
        heartbeat_age = (now - heartbeat_dt).total_seconds() if heartbeat_dt else None
        scan_started_at = snapshot.get("last_cycle_started_at") or (global_lease or {}).get("acquired_at")
        scan_started_dt = _parse_utc_datetime(str(scan_started_at or ""))
        scan_duration_seconds = int((now - scan_started_dt).total_seconds()) if scan_started_dt else None
        return {
            **snapshot,
            "enabled": bool(settings.background_poll_enabled),
            "api_responsive": True,
            "disabled_reason": None,
            "state": "scanning",
            "reason": "Autoscan scan is in progress",
            "scan_started_at": scan_started_at,
            "scan_duration_seconds": scan_duration_seconds,
            "scheduler_process_state": "running",
            "worker_lease_state": "owned" if _is_current_background_leader(lease, now) else "active",
            "scheduler_leadership_ttl_seconds": BACKGROUND_LEASE_TTL_SECONDS,
            "scheduler_leadership_renew_seconds": BACKGROUND_LEASE_RENEW_SECONDS,
            "scheduler_leadership_acquired_at": _background_leadership_acquired_at,
            "scheduler_leadership_lost_at": _background_leadership_lost_at,
            "scheduler_lease": {
                key: (lease or {}).get(key)
                for key in ("worker_id", "hostname", "process_id", "acquired_at", "heartbeat_at", "expires_at", "previous_worker_id", "takeover_reason")
            } if lease else None,
            "scheduler_lease_owner_state": "live_notifierr",
            "scheduler_lease_owner_reason": "lease belongs to this Notifierr process",
            "scheduler_lease_owner_alive": True,
            "scheduler_lease_heartbeat_age_seconds": int(heartbeat_age) if heartbeat_age is not None else None,
            "global_scan_lease_state": "owned",
            "global_scan_lease": {
                key: (global_lease or {}).get(key)
                for key in ("worker_id", "hostname", "process_id", "acquired_at", "heartbeat_at", "expires_at", "previous_worker_id", "takeover_reason")
            },
            "global_scan_owner_state": "live_notifierr",
            "is_leader": _is_current_background_leader(lease, now),
            "worker_id": (lease or {}).get("worker_id"),
            "configured_interval_seconds": interval.configured_seconds,
            "effective_interval_seconds": interval.effective_seconds,
            "last_heartbeat_at": last_heartbeat,
            "last_background_started_at": scan_started_at,
            "last_background_attempt_status": "started",
            "last_background_attempt_cycle_id": (heartbeat or {}).get("last_cycle_id"),
            "last_background_succeeded_at": None,
            "last_background_cycle_id": None,
            "last_manual_succeeded_at": None,
            "next_scheduled_at": None,
            "current_cycle_id": (heartbeat or {}).get("last_cycle_id"),
            "consecutive_failures": 0,
            "consecutive_failed_cycles": 0,
            "consecutive_skipped_cycles": 0,
            "last_skip_reason": None,
            "last_success_age_seconds": None,
            "last_success_overdue_after_seconds": scan_success_overdue_after_seconds(interval.effective_seconds, None),
            "orphaned_lease_blocking_work": False,
            "abandoned_active_cycle": None,
            "stale": False,
            "stale_after_seconds": stale_after_seconds(interval.effective_seconds),
        }
    resolved = _polling_settings_for_status(status_user if settings.auth_required else None)
    persisted_interval = getattr(resolved, "background_poll_seconds", None)
    interval = resolve_poll_interval(
        configured=settings.background_poll_seconds,
        persisted=[persisted_interval] if persisted_interval is not None else None,
    )
    effective = interval.effective_seconds
    lease = storage.get_worker_lease(BACKGROUND_LEASE_NAME)
    heartbeats = storage.get_worker_heartbeats()
    heartbeat = next((row for row in heartbeats if row.get("worker_name") == "background_poll"), None)
    last_heartbeat = (lease or {}).get("heartbeat_at") or (heartbeat or {}).get("last_seen_at")
    heartbeat_dt = _parse_utc_datetime(str(last_heartbeat or ""))
    stale_after = stale_after_seconds(effective)
    heartbeat_age = (now - heartbeat_dt).total_seconds() if heartbeat_dt else None
    lease_expiry_dt = _parse_utc_datetime(str((lease or {}).get("expires_at") or ""))
    lease_active = bool(lease and lease_expiry_dt and lease_expiry_dt > now)
    lease_owner = _is_current_background_leader(lease, now)
    scheduler_owner_state = None if lease_owner else (_background_lease_owner_state(lease) if lease_active else None)
    scheduler_owner_state_name = "live_notifierr" if lease_owner else (scheduler_owner_state.state if scheduler_owner_state else None)
    scheduler_owner_reason = "lease belongs to this Notifierr process" if lease_owner else (
        scheduler_owner_state.reason if scheduler_owner_state else None
    )
    scheduler_owner_alive = True if lease_owner else (
        scheduler_owner_state.pid_exists if scheduler_owner_state and scheduler_owner_state.pid_exists is not None else None
    )
    scheduler_leadership_blocked = bool(lease_active and not lease_owner and scheduler_owner_state_name == "absent")
    scheduler_standby = bool(
        lease_active
        and not lease_owner
        and scheduler_owner_state_name in {"live_notifierr", "live_unknown", "remote_unknown", "unknown", "identity_unknown", "live_unrelated"}
    )
    scheduler_alive = bool(
        heartbeat_dt
        and heartbeat_age is not None
        and heartbeat_age <= stale_after
        and not scheduler_leadership_blocked
    )
    stale = bool(settings.background_poll_enabled and not scheduler_alive)

    global_owner_state = inspect_lease_owner(
        global_lease,
        local_hostname=_hostname(),
        current_worker_id=WORKER_ID,
        current_process_id=_process_id(),
    ) if global_active else None
    global_heartbeat = _parse_utc_datetime(str((global_lease or {}).get("heartbeat_at") or ""))
    global_heartbeat_age = (now - global_heartbeat).total_seconds() if global_heartbeat else None
    orphaned_lease = bool(global_active and global_owner_state and global_owner_state.state == "absent")
    stale_global_lease = bool(
        global_active
        and global_heartbeat_age is not None
        and global_heartbeat_age > (GLOBAL_SCAN_LEASE_RENEW_SECONDS * 2)
    )
    scan_started_at = snapshot.get("last_cycle_started_at") if snapshot.get("cycle_running") else (
        (global_lease or {}).get("acquired_at") if global_active else None
    )
    scan_started_dt = _parse_utc_datetime(str(scan_started_at or ""))
    scan_duration_seconds = int((now - scan_started_dt).total_seconds()) if scan_started_dt else None
    if (
        settings.background_poll_enabled
        and resolved.background_poll_enabled
        and global_active
        and (global_owner or (global_owner_state and global_owner_state.state in {"live_notifierr", "remote_unknown"}))
        and not stale_global_lease
    ):
        return {
            **snapshot,
            "enabled": True,
            "api_responsive": True,
            "disabled_reason": None,
            "state": "scanning",
            "reason": "Autoscan scan is in progress",
            "scan_started_at": scan_started_at,
            "scan_duration_seconds": scan_duration_seconds,
            "scheduler_process_state": "running" if lease_owner else ("standby" if scheduler_standby else "stopped"),
            "worker_lease_state": (
                "owned" if lease_owner else
                "orphaned" if scheduler_leadership_blocked else
                "active" if lease_active else
                "expired_or_absent"
            ),
            "scheduler_leadership_ttl_seconds": BACKGROUND_LEASE_TTL_SECONDS,
            "scheduler_leadership_renew_seconds": BACKGROUND_LEASE_RENEW_SECONDS,
            "scheduler_leadership_acquired_at": _background_leadership_acquired_at,
            "scheduler_leadership_lost_at": _background_leadership_lost_at,
            "scheduler_lease": {
                key: (lease or {}).get(key)
                for key in ("worker_id", "hostname", "process_id", "acquired_at", "heartbeat_at", "expires_at", "previous_worker_id", "takeover_reason")
            } if lease else None,
            "scheduler_lease_owner_state": scheduler_owner_state_name,
            "scheduler_lease_owner_reason": scheduler_owner_reason,
            "scheduler_lease_owner_alive": scheduler_owner_alive,
            "scheduler_lease_heartbeat_age_seconds": int(heartbeat_age) if heartbeat_age is not None else None,
            "global_scan_lease_state": "owned" if global_owner else "active",
            "global_scan_lease": {
                key: (global_lease or {}).get(key)
                for key in ("worker_id", "hostname", "process_id", "acquired_at", "heartbeat_at", "expires_at", "previous_worker_id", "takeover_reason")
            },
            "global_scan_owner_state": global_owner_state.state if global_owner_state else None,
            "is_leader": lease_owner,
            "worker_id": (lease or {}).get("worker_id"),
            "configured_interval_seconds": interval.configured_seconds,
            "effective_interval_seconds": effective,
            "last_heartbeat_at": last_heartbeat,
            "last_background_started_at": scan_started_at,
            "last_background_attempt_status": "started",
            "last_background_attempt_cycle_id": (heartbeat or {}).get("last_cycle_id"),
            "last_background_succeeded_at": None,
            "last_background_cycle_id": None,
            "last_manual_succeeded_at": None,
            "next_scheduled_at": None,
            "current_cycle_id": (heartbeat or {}).get("last_cycle_id"),
            "consecutive_failures": 0,
            "consecutive_failed_cycles": 0,
            "consecutive_skipped_cycles": 0,
            "last_skip_reason": None,
            "last_success_age_seconds": None,
            "last_success_overdue_after_seconds": scan_success_overdue_after_seconds(effective, None),
            "orphaned_lease_blocking_work": False,
            "abandoned_active_cycle": None,
            "stale": stale,
            "stale_after_seconds": stale_after,
        }
    status_cache_key = int((status_user or {}).get("id") or 0)
    cached_status = _polling_status_cache.get(status_cache_key)
    cache_now = monotonic_time.perf_counter()
    if status_cache_key > 0 and cached_status and cache_now - cached_status[0] <= _POLLING_STATUS_CACHE_SECONDS:
        return copy.deepcopy(cached_status[1])
    background_modes = ["local_background", "shared_background"]
    recent_background = storage.recent_scan_cycles_for_modes(background_modes, limit=100)
    latest_background = recent_background[0] if recent_background else None
    successful_background = storage.latest_successful_scan_cycle_for_modes(background_modes)
    successful_manual = storage.latest_scan_cycle_for_modes(["manual"], status="completed")
    consecutive_failures, consecutive_skips, last_skip_reason = consecutive_cycle_outcomes(recent_background)
    success_finished = _parse_utc_datetime(str((successful_background or {}).get("finished_at") or ""))
    success_age_seconds = (now - success_finished).total_seconds() if success_finished else None
    overdue_after = scan_success_overdue_after_seconds(effective, successful_background)
    reference_started = _parse_utc_datetime(str((heartbeat or {}).get("started_at") or ""))
    overdue = bool(
        (success_age_seconds is not None and success_age_seconds > overdue_after)
        or (success_age_seconds is None and reference_started and (now - reference_started).total_seconds() > overdue_after)
    )
    outside_window = bool(settings.background_poll_enabled and not _background_poll_is_active(resolved, now=now))
    repeated_lock_skips = consecutive_skips >= 2 and last_skip_reason == "scan_already_running"
    orphan_blocking = bool(orphaned_lease or (global_active and stale_global_lease and repeated_lock_skips))
    if not settings.background_poll_enabled or not resolved.background_poll_enabled:
        state = "disabled"
        reason = "Autoscan is intentionally disabled"
    elif scheduler_leadership_blocked:
        state = "blocked"
        reason = f"Autoscan scheduler leadership blocked by dead worker PID {int((lease or {}).get('process_id') or 0)}"
    elif not scheduler_alive:
        state = "stopped"
        reason = "Autoscan scheduler heartbeat is missing or stale"
    elif outside_window:
        state = "outside_window"
        reason = "Autoscan is paused outside configured active hours"
    elif orphan_blocking:
        state = "blocked"
        owner_pid = int((global_lease or {}).get("process_id") or 0)
        reason = f"Autoscan blocked by abandoned scan lease from dead worker PID {owner_pid}"
    elif global_active and global_owner_state and global_owner_state.state in {"live_notifierr", "remote_unknown"} and not stale_global_lease:
        state = "scanning"
        reason = "Autoscan scan is in progress"
    elif scheduler_standby:
        state = "standby"
        reason = "Autoscan scheduler leader is another process"
    elif overdue or consecutive_failures > 0 or consecutive_skips >= 2:
        state = "degraded"
        if overdue:
            reason = "Autoscan scheduler is alive but the last successful scan is overdue"
        elif consecutive_failures:
            reason = f"Autoscan has {consecutive_failures} consecutive failed cycle(s)"
        else:
            reason = f"Autoscan has {consecutive_skips} consecutive skipped cycle(s): {last_skip_reason or 'unknown'}"
    else:
        state = "running"
        reason = "Autoscan scheduler is healthy"
    unfinished = storage.list_unfinished_scan_cycles(limit=20)
    payload = {
        **snapshot,
        "enabled": bool(settings.background_poll_enabled),
        "api_responsive": True,
        "disabled_reason": None if state != "disabled" else reason,
        "state": state,
        "reason": reason,
        "scan_started_at": scan_started_at,
        "scan_duration_seconds": scan_duration_seconds,
        "scheduler_process_state": "running" if lease_owner else ("standby" if scheduler_standby else "stopped"),
        "worker_lease_state": (
            "owned" if lease_owner else
            "orphaned" if scheduler_leadership_blocked else
            "active" if lease_active else
            "expired_or_absent"
        ),
        "scheduler_leadership_ttl_seconds": BACKGROUND_LEASE_TTL_SECONDS,
        "scheduler_leadership_renew_seconds": BACKGROUND_LEASE_RENEW_SECONDS,
        "scheduler_leadership_acquired_at": _background_leadership_acquired_at,
        "scheduler_leadership_lost_at": _background_leadership_lost_at,
        "scheduler_leadership_acquired_age_seconds": (
            int((now - _parse_utc_datetime(_background_leadership_acquired_at)).total_seconds())
            if _background_leadership_acquired_at and _parse_utc_datetime(_background_leadership_acquired_at)
            else None
        ),
        "scheduler_leadership_lost_age_seconds": (
            int((now - _parse_utc_datetime(_background_leadership_lost_at)).total_seconds())
            if _background_leadership_lost_at and _parse_utc_datetime(_background_leadership_lost_at)
            else None
        ),
        "scheduler_lease": {
            key: (lease or {}).get(key)
            for key in ("worker_id", "hostname", "process_id", "acquired_at", "heartbeat_at", "expires_at", "previous_worker_id", "takeover_reason")
        } if lease else None,
        "scheduler_lease_owner_state": scheduler_owner_state_name,
        "scheduler_lease_owner_reason": scheduler_owner_reason,
        "scheduler_lease_owner_alive": scheduler_owner_alive,
        "scheduler_lease_heartbeat_age_seconds": int(heartbeat_age) if heartbeat_age is not None else None,
        "global_scan_lease_state": (
            "owned" if global_owner else
            "orphaned" if orphaned_lease else
            "stale" if stale_global_lease else
            "active" if global_active else
            "expired" if global_lease else
            "absent"
        ),
        "is_leader": lease_owner,
        "worker_id": (lease or {}).get("worker_id"),
        "configured_interval_seconds": interval.configured_seconds,
        "effective_interval_seconds": effective,
        "last_heartbeat_at": last_heartbeat,
        "last_background_started_at": (latest_background or {}).get("started_at"),
        "last_background_attempt_status": (latest_background or {}).get("status"),
        "last_background_attempt_cycle_id": (latest_background or {}).get("id"),
        "last_background_succeeded_at": (successful_background or {}).get("finished_at"),
        "last_background_cycle_id": (successful_background or {}).get("id"),
        "last_manual_succeeded_at": (successful_manual or {}).get("finished_at"),
        "next_scheduled_at": (heartbeat or {}).get("next_wake_at"),
        "current_cycle_id": (heartbeat or {}).get("last_cycle_id"),
        "consecutive_failures": consecutive_failures,
        "consecutive_failed_cycles": consecutive_failures,
        "consecutive_skipped_cycles": consecutive_skips,
        "last_skip_reason": last_skip_reason,
        "last_success_age_seconds": int(success_age_seconds) if success_age_seconds is not None else None,
        "last_success_overdue_after_seconds": overdue_after,
        "orphaned_lease_blocking_work": orphan_blocking,
        "abandoned_active_cycle": next((row for row in unfinished if row.get("status") == "started"), None),
        "global_scan_lease": {
            key: (global_lease or {}).get(key)
            for key in ("worker_id", "hostname", "process_id", "acquired_at", "heartbeat_at", "expires_at", "previous_worker_id", "takeover_reason")
        } if global_lease else None,
        "global_scan_owner_state": global_owner_state.state if global_owner_state else None,
        "stale": stale,
        "stale_after_seconds": stale_after,
    }
    if status_cache_key > 0:
        _polling_status_cache[status_cache_key] = (cache_now, copy.deepcopy(payload))
    return payload


@app.get("/admin/scanner/status")
def admin_scanner_status(status_user: dict[str, Any] = Depends(require_polling_status_user)) -> dict[str, Any]:
    """Durable scanner view; an API process heartbeat never counts as scan success."""
    status = admin_polling_status(status_user)
    source = _ebay_source_status()
    modes = ["local_background", "shared_background"]
    latest = storage.latest_scan_cycle_for_modes(modes)
    successful = storage.latest_successful_scan_cycle_for_modes(modes)
    failures, skips, _ = consecutive_cycle_outcomes(storage.recent_scan_cycles_for_modes(modes, limit=100))
    effective = _polling_settings_for_status(status_user if settings.auth_required else None)
    now = datetime.now(timezone.utc)
    success_at = _parse_utc_datetime(str((successful or {}).get("finished_at") or ""))
    success_age = int((now - success_at).total_seconds()) if success_at else None
    cadence = int(status.get("effective_interval_seconds") or resolve_poll_interval(configured=settings.background_poll_seconds).effective_seconds)
    overdue_after = scan_success_overdue_after_seconds(cadence, successful)
    worker_started = _parse_utc_datetime(str((status.get("scheduler_lease") or {}).get("acquired_at") or ""))
    useful_overdue = bool(
        (success_age is not None and success_age > overdue_after)
        or (success_age is None and worker_started and (now - worker_started).total_seconds() > overdue_after)
    )
    with storage.connect() as connection:
        retention_row = connection.execute(
            "SELECT status, started_at, finished_at FROM retention_runs ORDER BY id DESC LIMIT 1"
        ).fetchone()
    result = {
        "state": status["state"],
        "degraded_reason": status.get("reason") if status["state"] in {"blocked", "degraded", "stopped"} else None,
        "enabled": status["enabled"],
        "lease": status.get("scheduler_lease"),
        "lease_state": status.get("worker_lease_state"),
        "last_heartbeat_at": status.get("last_heartbeat_at"),
        "last_attempt_at": (latest or {}).get("started_at"),
        "last_useful_scan_at": (successful or {}).get("finished_at"),
        "last_useful_scan_age_seconds": success_age,
        "useful_scan_overdue_after_seconds": overdue_after,
        "effective_interval_seconds": cadence,
        "active_window": {
            "start": effective.background_poll_active_start,
            "end": effective.background_poll_active_end,
            "timezone": effective.background_poll_timezone,
        },
        "consecutive_failures": failures,
        "consecutive_skips": skips,
        "next_expected_scan_at": status.get("next_scheduled_at"),
        "source": {key: source.get(key) for key in ("status", "cooldown_until", "last_success_at", "last_failure_at", "last_http_status", "last_error_category")},
        "latest_items_discovered": (latest or {}).get("new_items_found"),
        "latest_items_scored": (latest or {}).get("items_scored"),
        "retention": dict(retention_row) if retention_row else None,
    }
    if result["state"] == "standby" and status.get("scheduler_process_state") == "standby":
        # API observers are never lease owners; a live remote leader is running.
        result["state"] = "running"
    if source["status"] == "cooling_down" and result["state"] not in {"disabled", "outside_window"}:
        result["state"] = "blocked"
        result["degraded_reason"] = "Marketplace source is cooling down"
    elif useful_overdue and result["state"] not in {"disabled", "outside_window", "blocked", "stopped"}:
        result["state"] = "degraded"
        result["degraded_reason"] = "Last useful scan is overdue"
    return result


@app.post("/admin/notifications/test")
async def admin_test_notification(
    admin_user: Optional[dict[str, Any]] = Depends(require_admin_user_if_auth_enabled),
) -> dict[str, Any]:
    if settings.auth_required:
        if not admin_user:
            raise HTTPException(status_code=401, detail="Authentication required")
        resolved = _resolve_effective_user_settings(admin_user)
    else:
        resolved = _resolve_effective_user_settings(
            allow_local_fallback=True,
            ensure_defaults=True,
        )
    return await _send_test_notification(resolved)


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
    _invalidate_dashboard_user_cache(int(user["id"]))
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
    return {"notifications": _public_notification_settings(_resolve_notification_settings(notifications))}


@app.get("/notifications/delivery")
def get_notification_delivery(user: dict[str, Any] = Depends(require_settings_user)) -> dict[str, Any]:
    user_id = int(user["id"])
    attempts = storage.list_notification_attempts(user_id=user_id, limit=500)
    by_id = {int(entry["id"]): entry for entry in attempts}
    public_attempts = []
    for entry in attempts:
        prior = by_id.get(int(entry.get("prior_success_attempt_id") or 0))
        public_attempts.append({
            key: entry.get(key) for key in (
                "id", "item_id", "scan_cycle_id", "status", "notification_tier", "effective_price",
                "expected_profit", "expected_roi", "principal_damage", "availability_state", "confidence",
                "destination_identity", "successful_at", "prior_success_attempt_id", "fingerprint_match_reason",
                "source", "next_eligible_at", "retry_count", "provider_status", "failure_category", "created_at",
            )
        } | ({
            "prior_successful_at": prior.get("successful_at") or prior.get("updated_at"),
            "prior_tier": prior.get("notification_tier") or prior.get("notification_type"),
            "prior_price": prior.get("effective_price"),
            "current_tier": entry.get("notification_tier") or entry.get("notification_type"),
            "current_price": entry.get("effective_price"),
        } if prior else {}))
    return {"metrics": storage.notification_delivery_metrics(user_id), "attempts": public_attempts}


@app.put("/settings/notifications")
def update_current_user_notifications(
    request: UserNotificationSettingsUpdateRequest,
    user: dict[str, Any] = Depends(require_settings_user),
) -> dict[str, Any]:
    if request.discord_webhook is not None and request.discord_webhook.strip() and not settings.app_encryption_key and settings.auth_required:
        raise HTTPException(status_code=503, detail="APP_ENCRYPTION_KEY is required to save Discord webhooks when AUTH_REQUIRED=true")
    updates: dict[str, Any] = {
        "discord_enabled": int(bool(request.discord_enabled)),
        "push_enabled": int(bool(request.push_enabled)),
        "use_global_discord_webhook": int(bool(request.use_global_discord_webhook)),
        "alerts_enabled": int(bool(request.alerts_enabled)),
        "notify_best_finds": int(bool(request.notify_best_finds)),
        "notify_priority_review": int(bool(request.notify_priority_review)),
        "send_gem_immediately": int(bool(request.send_gem_immediately)),
        "send_profitable_immediately": int(bool(request.send_profitable_immediately)),
        "review_delivery_mode": request.review_delivery_mode,
        "max_review_alerts_per_hour": request.max_review_alerts_per_hour,
        "duplicate_suppression_hours": request.duplicate_suppression_hours,
        "meaningful_price_drop_amount": request.meaningful_price_drop_amount,
        "meaningful_price_drop_percent": request.meaningful_price_drop_percent,
        "meaningful_profit_increase_amount": request.meaningful_profit_increase_amount,
        "meaningful_profit_increase_percent": request.meaningful_profit_increase_percent,
        "meaningful_roi_increase": request.meaningful_roi_increase,
        "catchup_enabled": int(bool(request.catchup_enabled)),
        "catchup_batch_size": request.catchup_batch_size,
        "catchup_include_review": int(bool(request.catchup_include_review)),
        "gem_min_expected_profit": request.gem_min_expected_profit,
        "profitable_min_expected_profit": request.profitable_min_expected_profit,
        "review_min_expected_profit": request.review_min_expected_profit,
        "review_min_upside_profit": request.review_min_upside_profit,
        "gem_min_roi": request.gem_min_roi,
        "profitable_min_roi": request.profitable_min_roi,
        "review_min_roi": request.review_min_roi,
        "max_listing_age_minutes": request.max_listing_age_minutes,
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
    return {"notifications": _public_notification_settings(_resolve_notification_settings(notifications))}


@app.post("/settings/notifications/test-discord")
async def test_current_user_discord_notification(
    user: dict[str, Any] = Depends(require_settings_user),
) -> dict[str, Any]:
    return await _send_test_notification(_resolve_effective_user_settings(user))


@app.get("/push/config")
def get_push_config(user: dict[str, Any] = Depends(require_settings_user)) -> dict[str, Any]:
    user_id = int(user["id"])
    subscriptions = storage.list_push_subscriptions(user_id, enabled_only=True)
    return {
        "enabled": settings.push_configured,
        "vapid_public_key": settings.vapid_public_key if settings.push_configured else None,
        "active_subscriptions": len(subscriptions),
    }


@app.post("/push/subscriptions")
def create_push_subscription(
    request: PushSubscriptionRequest,
    user: dict[str, Any] = Depends(require_settings_user),
) -> dict[str, Any]:
    if not settings.push_configured:
        raise HTTPException(status_code=503, detail="Web Push is not configured on the server")
    if not settings.app_encryption_key:
        raise HTTPException(status_code=503, detail="APP_ENCRYPTION_KEY is required for Web Push subscriptions")
    endpoint = request.endpoint.strip()
    if not endpoint.startswith("https://"):
        raise HTTPException(status_code=400, detail="Push endpoint must use HTTPS")
    subscription = storage.upsert_push_subscription(
        user_id=int(user["id"]),
        endpoint_hash=endpoint_hash(endpoint),
        endpoint=encrypt_secret(endpoint, settings.app_encryption_key),
        p256dh=encrypt_secret(request.keys.p256dh.strip(), settings.app_encryption_key),
        auth=encrypt_secret(request.keys.auth.strip(), settings.app_encryption_key),
        device_label=request.device_label.strip() or "Browser/PWA",
        user_agent=request.user_agent,
    )
    return {
        "subscription": {
            "id": subscription["id"],
            "device_label": subscription["device_label"],
            "enabled": subscription["enabled"],
            "last_seen_at": subscription["last_seen_at"],
        }
    }


@app.delete("/push/subscriptions")
def delete_push_subscription(
    request: PushSubscriptionDeleteRequest,
    user: dict[str, Any] = Depends(require_settings_user),
) -> dict[str, Any]:
    disabled = storage.disable_push_subscription(int(user["id"]), endpoint_hash(request.endpoint.strip()))
    return {"ok": True, "disabled": disabled}


@app.post("/push/test")
async def test_push_notification(user: dict[str, Any] = Depends(require_settings_user)) -> dict[str, Any]:
    result = await asyncio.to_thread(
        send_push_to_user,
        storage,
        settings,
        user_id=int(user["id"]),
        item={},
        tier="TEST",
        test=True,
    )
    if result.selected == 0:
        raise HTTPException(status_code=409, detail="No enabled push subscriptions are available")
    if result.accepted == 0:
        raise HTTPException(status_code=502, detail="No push provider accepted the test notification")
    return result.as_dict()


@app.get("/push/delivery")
def get_push_delivery(user: dict[str, Any] = Depends(require_settings_user)) -> dict[str, Any]:
    user_id = int(user["id"])
    attempts = storage.list_push_delivery_attempts(user_id, limit=100)
    counts = Counter(str(entry.get("status") or "unknown") for entry in attempts)
    subscriptions = storage.list_push_subscriptions(user_id)
    return {
        "metrics": {
            "active_subscriptions": sum(bool(entry.get("enabled")) for entry in subscriptions),
            "selected": counts["selected"],
            "attempted": counts["attempted"],
            "accepted": counts["accepted"],
            "failed": counts["failed"],
            "invalid": counts["invalid"],
        },
        "attempts": [
            {key: entry.get(key) for key in (
                "id", "subscription_id", "device_label", "item_id", "scan_cycle_id",
                "notification_tier", "status", "provider_status", "error_category", "created_at",
            )}
            for entry in attempts
        ],
    }


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
    _invalidate_pricing_context_cache(int(user["id"]))
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
    _invalidate_pricing_context_cache(int(user["id"]))
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
    _invalidate_pricing_context_cache(int(user["id"]))
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
    _invalidate_pricing_context_cache(int(user["id"]))
    return {"ok": True, "override_id": override_id}


@app.get("/items/{item_id}/feedback")
def get_item_feedback(
    item_id: str, user: dict[str, Any] = Depends(require_settings_user),
) -> dict[str, Any]:
    return {"feedback": storage.get_user_item_feedback(int(user["id"]), item_id)}


@app.put("/items/{item_id}/feedback")
def put_item_feedback(
    item_id: str, request: ItemFeedbackRequest,
    user: dict[str, Any] = Depends(require_settings_user),
) -> dict[str, Any]:
    try:
        feedback = storage.upsert_user_item_feedback(
            int(user["id"]), item_id, label=request.label, note=request.note,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="Item not found") from None
    return {"feedback": feedback}


@app.get("/items/{item_id}/outcome")
def get_item_outcome(
    item_id: str, user: dict[str, Any] = Depends(require_settings_user),
) -> dict[str, Any]:
    return {"outcome": storage.get_user_item_outcome(int(user["id"]), item_id)}


@app.put("/items/{item_id}/outcome")
def put_item_outcome(
    item_id: str, request: ItemOutcomeRequest,
    user: dict[str, Any] = Depends(require_settings_user),
) -> dict[str, Any]:
    values = request.dict(exclude_unset=True)
    for field_name in ("purchase_date", "sale_date"):
        if values.get(field_name):
            try:
                date.fromisoformat(values[field_name])
            except ValueError:
                raise HTTPException(status_code=400, detail=f"{field_name} must be YYYY-MM-DD") from None
    try:
        outcome = storage.upsert_user_item_outcome(int(user["id"]), item_id, values=values)
    except KeyError:
        raise HTTPException(status_code=404, detail="Item not found") from None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return {"outcome": outcome}


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
    existing_correction = storage.get_user_item_correction(int(user["id"]), item_id) or {}
    feedback_only = bool(request.feedback_code) and all(
        value is None
        for value in (
            request.corrected_model,
            request.corrected_storage_capacity,
            request.corrected_issue_type,
            request.corrected_part_cost,
        )
    ) and not request.note.strip()
    corrected_model = (request.corrected_model or "").strip() or None
    corrected_storage_capacity = (request.corrected_storage_capacity or "").strip() or None
    corrected_issue_type = (request.corrected_issue_type or "").strip() or None
    feedback_code = (request.feedback_code or "").strip().lower()
    if feedback_only:
        corrected_model = existing_correction.get("corrected_model")
        corrected_storage_capacity = existing_correction.get("corrected_storage_capacity")
        corrected_issue_type = existing_correction.get("corrected_issue_type")
        request.corrected_part_cost = existing_correction.get("corrected_part_cost")
        request.note = str(existing_correction.get("note") or "")
    if corrected_storage_capacity and corrected_storage_capacity not in SUPPORTED_STORAGE_CAPACITIES:
        raise HTTPException(status_code=400, detail="Unsupported corrected_storage_capacity")
    if corrected_issue_type and corrected_issue_type not in SUPPORTED_CORRECTION_ISSUE_TYPES:
        raise HTTPException(status_code=400, detail="Unsupported corrected_issue_type")
    if feedback_code and feedback_code not in SUPPORTED_FEEDBACK_CODES:
        raise HTTPException(status_code=400, detail="Unsupported feedback_code")
    if not any(
        value
        for value in (
            corrected_model,
            corrected_storage_capacity,
            corrected_issue_type,
            request.corrected_part_cost,
            feedback_code,
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
            feedback_code=feedback_code,
            note=request.note,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="Item not found") from None
    pricing_context = _build_user_pricing_context(int(user["id"]))
    rescored = _rescore_stored_item(current_item, resolved, pricing_context=pricing_context)
    _dashboard_stats_cache.pop(int(user["id"]), None)
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
    _dashboard_stats_cache.pop(int(user["id"]), None)
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
    force_source_call = bool(request.force_source_call and (not settings.auth_required or user.get("role") == "admin"))
    if settings.auth_required and user.get("role") == "admin":
        try:
            return await scan_shared_once(
                limit=request.limit or settings.max_results_per_keyword,
                notify=request.notify,
                triggered_by_user=user,
                background_mode=False,
                keyword_filter=request.keywords,
                force_source_call=force_source_call,
            )
        except EbayRateLimitError as exc:
            raise _ebay_rate_limit_http_error(exc) from None
    limited, reason = _billing_access_state(user)
    if settings.auth_required and limited:
        _persist_skipped_scan_cycle(
            mode="manual",
            reason=f"billing_access_limited: {reason}",
            user_id=int(user["id"]),
            users_considered=1,
            users_scanned=0,
            keywords=request.keywords or [],
        )
        raise HTTPException(status_code=403, detail=f"Scanning is disabled: {reason}")
    resolved = _resolve_effective_user_settings(user)
    try:
        summary = await scan_once(
            keywords=request.keywords or resolved.keywords,
            limit=request.limit or settings.max_results_per_keyword,
            notify=request.notify,
            resolved_settings=resolved,
            force_source_call=force_source_call,
        )
    except EbayRateLimitError as exc:
        raise _ebay_rate_limit_http_error(exc) from None
    summary.setdefault("mode", "user" if settings.auth_required else "local")
    return summary


def _ebay_rate_limit_http_error(exc: EbayRateLimitError) -> HTTPException:
    status = _ebay_source_status()
    if status.get("last_http_status") != 429:
        status = _record_ebay_rate_limit(exc)
    return HTTPException(
        status_code=429,
        detail={
            "message": "eBay rate limit exceeded. Polling will back off before trying again.",
            "retry_after_seconds": exc.retry_after_seconds,
            "cooldown_until": status.get("cooldown_until"),
        },
        headers={"Retry-After": str(exc.retry_after_seconds)},
    )


@app.get("/items")
def list_items(
    status: Optional[str] = None,
    user_status: Optional[str] = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    include_ignored: bool = False,
    include_stale: bool = False,
    lightweight: bool = False,
    source_max_age_hours: Optional[int] = Query(default=None, ge=1, le=720),
    user: dict[str, Any] = Depends(require_settings_user),
) -> list[dict[str, Any]]:
    if not user:
        return []
    user_id = int(user["id"])
    offset_value = offset if isinstance(offset, int) else 0
    freshness_kwargs = _dashboard_freshness_kwargs(user_id)
    pricing_context = _cached_pricing_context_for_user(user_id)
    missed_opportunities = status == "missed_opportunities"
    unsent_actionable = status == "unsent_actionable"
    items = storage.list_user_items(
        user_id,
        status=None if (missed_opportunities or unsent_actionable) else status,
        user_status=user_status,
        limit=500 if missed_opportunities else limit,
        offset=0 if missed_opportunities else offset_value,
        include_ignored=include_ignored,
        include_stale=include_stale,
        lightweight=lightweight,
        source_max_age_hours=source_max_age_hours,
        **freshness_kwargs,
    )
    corrections = storage.list_user_item_corrections_for_items(
        user_id,
        [str(item.get("item_id") or "") for item in items],
    )
    correction_by_item_id = {str(correction.get("item_id") or ""): correction for correction in corrections}
    resolved = _resolve_effective_user_settings(user)
    decorated = [
        _decorate_alert_decision(_decorate_item_for_user(
            item,
            user_id=user_id,
            pricing_context=pricing_context,
            correction_by_item_id=correction_by_item_id,
        ), resolved)
        for item in items
    ]
    delivered = storage.successfully_notified_item_ids(user_id)
    for item in decorated:
        item["successfully_notified"] = str(item.get("item_id") or "") in delivered
        item["never_notified_actionable"] = bool(
            item.get("alert_tier") in {"GEM", "PROFITABLE"} and not item["successfully_notified"]
        )
    if unsent_actionable:
        return [item for item in decorated if item.get("never_notified_actionable")][offset_value:offset_value + limit]
    if missed_opportunities:
        decorated = [
            item for item in decorated
            if _is_missed_opportunity(item)
        ]
        return decorated[offset_value:offset_value + limit]
    return decorated


_DASHBOARD_QUEUES = {
    "high_quality", "profitable", "review", "unsent_actionable", "missed_opportunities",
    "priority_review", "needs_data", "watched", "promoted", "ignored", "rejected", "all",
}


def _dashboard_queue_matches(item: dict[str, Any], queue: str) -> bool:
    if queue == "high_quality":
        return item.get("alert_tier") == "GEM"
    if queue == "profitable":
        return item.get("alert_tier") == "PROFITABLE"
    if queue == "review":
        return item.get("alert_tier") == "REVIEW"
    if queue == "unsent_actionable":
        return item.get("never_notified_actionable") is True
    if queue == "missed_opportunities":
        return _is_missed_opportunity(item) and (
            item.get("alert_tier") in {"REVIEW", "PROFITABLE"} or (
                float(item.get("profit_mid") or 0) > 0
                and len((item.get("alert_decision") or {}).get("blocking_reasons") or []) <= 2
            )
        )
    if queue == "priority_review":
        return _is_priority_review_candidate(item)
    if queue == "needs_data":
        return _is_needs_data_item(item)
    if queue == "watched":
        return item.get("user_status") == "watched"
    if queue == "promoted":
        return item.get("user_status") == "promoted" and bool(item.get("promoted_at"))
    if queue == "ignored":
        return item.get("user_status") == "ignored"
    if queue == "rejected":
        return (item.get("status") == "rejected" or item.get("user_status") == "rejected") and item.get("user_status") != "ignored"
    return item.get("user_status") != "ignored"


def _dashboard_search_matches(item: dict[str, Any], query: str) -> bool:
    if not query:
        return True
    fields = [
        item.get("title"), item.get("model"), item.get("seller_username"),
        item.get("manual_review_reason"), item.get("pricing_warning"),
        *(item.get("positive_flags") or []), *(item.get("risk_flags") or []),
        *(item.get("hard_reject_flags") or []), *(item.get("listing_classification_flags") or []),
    ]
    return query in " ".join(str(value or "") for value in fields).lower()


def _dashboard_visible_in_queue(
    item: dict[str, Any], queue: str, *, include_ignored: bool, include_stale: bool,
) -> bool:
    return (
        (include_ignored or queue == "ignored" or item.get("user_status") != "ignored")
        and (include_stale or queue == "all" or item.get("fresh_for_active_queue") is not False)
        and _dashboard_queue_matches(item, queue)
    )


def _dashboard_sort_key(item: dict[str, Any], sort: str, queue: str) -> tuple[Any, ...]:
    found = _parse_utc_datetime(str(item.get("found_at") or ""))
    found_time = found.timestamp() if found else 0.0
    if sort == "profit":
        return (float(item.get("estimated_profit") or 0) if item.get("estimated_profit_available") else float("-inf"),)
    if sort == "score":
        return (float(item.get("score") or 0),)
    if sort == "price":
        return (-float(item.get("total_cost") or 0),)
    if queue == "priority_review":
        return (
            max(float(item.get("profit_high") or 0), float(item.get("estimated_profit") or 0), float(item.get("profit_mid") or 0)),
            float(item.get("score") or 0), found_time,
        )
    return (found_time,)


@app.get("/items/dashboard")
def dashboard_items(
    queue: str = Query(default="all"),
    sort: str = Query(default="newest"),
    search: str = Query(default=""),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    include_ignored: bool = False,
    include_stale: bool = False,
    user: dict[str, Any] = Depends(require_settings_user),
) -> dict[str, Any]:
    started = monotonic_time.perf_counter()
    if queue not in _DASHBOARD_QUEUES or sort not in {"newest", "profit", "score", "price"}:
        raise HTTPException(status_code=400, detail="Invalid dashboard queue or sort")
    if not user:
        return {"items": [], "total": 0, "counts": {}, "limit": limit, "offset": offset}
    counts = {name: 0 for name in _DASHBOARD_QUEUES}
    matched: list[dict[str, Any]] = []
    query = search.strip().lower()
    source_offset = 0
    source_rows = 0
    # The ordinary dashboard is intentionally a hot-data view. Historical browsing is
    # explicit and still bounded so an old archive cannot stall every refresh.
    source_cap = 2500 if include_stale else 1500
    source_age_hours = None if include_stale else max(1, int(settings.dashboard_hot_hours))
    watermark = ""
    while source_rows < source_cap:
        batch_limit = min(250, source_cap - source_rows)
        batch = list_items(
            status=None, user_status=None, limit=batch_limit, offset=source_offset,
            include_ignored=True, include_stale=include_stale, lightweight=True,
            source_max_age_hours=source_age_hours, user=user,
        )
        if not batch:
            break
        source_rows += len(batch)
        for item in batch:
            watermark = max(
                watermark,
                str(item.get("updated_at") or ""),
                str(item.get("marketplace_updated_at") or ""),
                str(item.get("found_at") or ""),
            )
            for name in counts:
                if _dashboard_visible_in_queue(
                    item, name, include_ignored=include_ignored, include_stale=include_stale,
                ):
                    counts[name] += 1
            if _dashboard_visible_in_queue(
                item, queue, include_ignored=include_ignored, include_stale=include_stale,
            ) and _dashboard_search_matches(item, query):
                matched.append(item)
        source_offset += len(batch)
        if len(batch) < batch_limit:
            break
    matched.sort(key=lambda item: _dashboard_sort_key(item, sort, queue), reverse=True)
    elapsed_ms = round((monotonic_time.perf_counter() - started) * 1000, 1)
    return {
        "items": matched[offset:offset + limit],
        "total": len(matched),
        "counts": counts,
        "limit": limit,
        "offset": offset,
        "watermark": watermark,
        "source_truncated": source_rows >= source_cap,
        "performance": {
            "elapsed_ms": elapsed_ms,
            "source_rows": source_rows,
            "returned_items": min(limit, max(0, len(matched) - offset)),
            "hot_hours": None if include_stale else source_age_hours,
            "lightweight": True,
        },
    }


@app.get("/items/dashboard/changes")
def dashboard_changes(
    after: str = Query(default=""),
    user: dict[str, Any] = Depends(require_settings_user),
) -> dict[str, Any]:
    """Tiny polling probe; avoids hydrating listings when nothing changed."""
    if not user:
        return {"changed": False, "watermark": after, "changed_rows": 0}
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=max(1, int(settings.dashboard_hot_hours)))).isoformat()
    with storage.connect() as connection:
        row = connection.execute(
            """SELECT COUNT(*) AS changed_rows,
                      MAX(CASE WHEN uis.updated_at > mi.updated_at THEN uis.updated_at ELSE mi.updated_at END) AS watermark
               FROM user_item_states uis JOIN marketplace_items mi ON mi.id=uis.marketplace_item_id
               WHERE uis.user_id=? AND COALESCE(mi.item_origin_at, mi.found_at)>=?
                 AND (uis.updated_at>? OR mi.updated_at>?)""",
            (int(user["id"]), cutoff, after or "", after or ""),
        ).fetchone()
    changed_rows = int((row or {}).get("changed_rows") or 0) if isinstance(row, dict) else int(row["changed_rows"] or 0)
    watermark = ((row or {}).get("watermark") if isinstance(row, dict) else row["watermark"]) or after
    return {"changed": changed_rows > 0, "watermark": watermark, "changed_rows": changed_rows}


@app.get("/items/{item_id}/detail")
def item_detail(
    item_id: str,
    user: dict[str, Any] = Depends(require_settings_user),
) -> dict[str, Any]:
    user_id = int(user["id"])
    resolved = _resolve_effective_user_settings(user)
    item = storage.get_user_item(user_id, item_id, **resolved.freshness_kwargs())
    if not item:
        raise HTTPException(status_code=404, detail="Listing not found")
    corrections = storage.list_user_item_corrections_for_items(user_id, [item_id])
    correction_by_item_id = {str(entry.get("item_id") or ""): entry for entry in corrections}
    decorated = _decorate_alert_decision(
        _decorate_item_for_user(
            item,
            user_id=user_id,
            pricing_context=_cached_pricing_context_for_user(user_id),
            correction_by_item_id=correction_by_item_id,
        ),
        resolved,
    )
    decorated["successfully_notified"] = item_id in storage.successfully_notified_item_ids(user_id)
    decorated["never_notified_actionable"] = bool(
        decorated.get("alert_tier") in {"GEM", "PROFITABLE"} and not decorated["successfully_notified"]
    )
    return {"item": decorated}


@app.get("/stats")
def stats(user: dict[str, Any] = Depends(require_settings_user)) -> dict[str, Any]:
    if not user:
        return storage.stats(
            max_alert_item_age_minutes=settings.max_alert_item_age_minutes,
            max_priority_review_item_age_hours=settings.max_priority_review_item_age_hours,
            max_active_queue_item_age_hours=settings.max_active_queue_item_age_hours,
        )
    return _cached_dashboard_stats_for_user(int(user["id"]))


@app.get("/config")
def config(user: Optional[dict[str, Any]] = Depends(require_auth_if_enabled)) -> dict[str, Any]:
    del user
    return _config_payload()


@app.post("/config/reload")
def reload_config(user: Optional[dict[str, Any]] = Depends(require_auth_if_enabled)) -> dict[str, Any]:
    global settings, repair_values, resale_research, scoring_rules, storage
    if settings.runtime_env == "production" and (not user or user.get("role") != "admin"):
        raise HTTPException(status_code=403, detail="Admin access required")
    settings = load_settings()
    repair_values = load_repair_values(settings.repair_values_path)
    resale_research = load_resale_research(settings.resale_research_path)
    scoring_rules = load_scoring_rules(settings.scoring_rules_path)
    storage = create_storage(settings)
    _dashboard_freshness_cache.clear()
    _pricing_context_cache.clear()
    _invalidate_all_dashboard_stats_cache()
    _invalidate_polling_status_cache()
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
    _invalidate_pricing_context_cache(int(user["id"]))

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
    if settings.runtime_env == "production":
        raise HTTPException(status_code=409, detail="Global repair baseline is immutable in production; use the per-user repair override endpoint")
    if request.part not in SUPPORTED_OVERRIDE_PARTS:
        raise HTTPException(status_code=400, detail="Unsupported part")
    try:
        repair_values = _update_repair_values_part(settings.repair_values_path, model, request)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    _pricing_context_cache.clear()
    _invalidate_all_dashboard_stats_cache()

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
        updated = storage.set_user_item_note(int(user["id"]), item_id, request.note)
        _dashboard_stats_cache.pop(int(user["id"]), None)
        return updated
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
        updated = storage.ignore_seller_from_item(item_id, user_id=int(user["id"]), reason=request.reason or "Ignored seller")
        _dashboard_stats_cache.pop(int(user["id"]), None)
        return updated
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
    _dashboard_stats_cache.pop(int(user["id"]), None)
    return {"ok": True, "keyword": request.keyword}


async def scan_once(
    keywords: Optional[list[str]],
    limit: Optional[int],
    notify: bool,
    *,
    resolved_settings: EffectiveUserSettings | None = None,
    cycle_mode: str = "manual",
    force_source_call: bool = False,
    offload_scan_work: bool = False,
) -> dict[str, Any]:
    lease_run = await run_with_scan_lease(
        storage,
        lambda: _scan_once_process_locked(
            keywords, limit, notify, resolved_settings=resolved_settings,
            cycle_mode=cycle_mode, force_source_call=force_source_call,
            offload_scan_work=offload_scan_work,
        ),
        lease_name=GLOBAL_SCAN_LEASE_NAME,
        worker_id=WORKER_ID,
        hostname=_hostname(),
        process_id=_process_id(),
    )
    if not lease_run.acquired:
        _persist_skipped_scan_cycle(mode=cycle_mode, reason="scan_already_running", keywords=list(keywords or []))
        return _empty_scan_summary(keywords=list(keywords or []), reason="scan_already_running", mode="local")
    return lease_run.value


async def _scan_once_process_locked(
    keywords: Optional[list[str]],
    limit: Optional[int],
    notify: bool,
    *,
    resolved_settings: EffectiveUserSettings | None = None,
    cycle_mode: str = "manual",
    force_source_call: bool = False,
    offload_scan_work: bool = False,
) -> dict[str, Any]:
    resolved = resolved_settings or _resolve_effective_user_settings()
    if not resolved.user:
        raise HTTPException(status_code=400, detail="No effective user is available for scan state storage")
    scan_keywords = list(keywords) if keywords is not None else list(resolved.keywords)
    scan_limit = limit or settings.max_results_per_keyword
    source_status = _ebay_source_status()
    if source_status.get("status") == "cooling_down" and not force_source_call:
        logger.info("Scan skipped because eBay source is cooling down until %s", source_status.get("cooldown_until"))
        _persist_source_cooldown_skip(
            mode=cycle_mode,
            user_id=int(resolved.user["id"]),
            current_settings=resolved,
            users_considered=1,
            users_scanned=0,
            keywords=scan_keywords,
            status=source_status,
        )
        summary = _empty_scan_summary(
            keywords=scan_keywords,
            reason="ebay_rate_limited",
            mode="user" if settings.auth_required else "local",
        )
        summary["cooldown_until"] = source_status.get("cooldown_until")
        return summary
    if _scan_lock.locked():
        logger.info("Scan skipped because another scan is already running")
        _persist_skipped_scan_cycle(
            mode=cycle_mode,
            reason="scan_already_running",
            user_id=int(resolved.user["id"]),
            current_settings=resolved,
            users_considered=1,
            users_scanned=0,
            keywords=scan_keywords,
        )
        return _empty_scan_summary(
            keywords=scan_keywords,
            reason="scan_already_running",
            mode="user" if settings.auth_required else "local",
        )
    cycle_id = _create_scan_cycle(
        mode=cycle_mode,
        user_id=int(resolved.user["id"]),
        current_settings=resolved,
        users_considered=1,
        users_scanned=1,
        keywords_searched=scan_keywords,
    )
    async with _scan_lock:
        try:
            if offload_scan_work:
                summary = await _run_background_scan_work(
                    lambda: _scan_once_unlocked(scan_keywords, scan_limit, notify, resolved, scan_cycle_id=cycle_id),
                    label=cycle_mode,
                )
            else:
                summary = await _scan_once_unlocked(scan_keywords, scan_limit, notify, resolved, scan_cycle_id=cycle_id)
        except Exception as exc:
            rate_limit_status = None
            if isinstance(exc, EbayRateLimitError):
                rate_limit_status = _record_ebay_rate_limit(exc, keyword=scan_keywords[0] if scan_keywords else None)
            _finish_scan_cycle_from_summary(
                cycle_id,
                status="failed",
                error_message=_scan_cycle_error_message(exc, source_status=rate_limit_status),
                users_considered=1,
                users_scanned=1,
                keywords_searched=scan_keywords,
                **(
                    _rate_limit_cycle_metadata(exc, status=rate_limit_status)
                    if isinstance(exc, EbayRateLimitError)
                    else {}
                ),
            )
            raise
        counters = summary.pop("_cycle_counters", None)
        _finish_scan_cycle_from_summary(
            cycle_id,
            status="completed",
            summary=summary,
            counters=counters,
            users_considered=1,
            users_scanned=1,
            keywords_searched=scan_keywords,
        )
        _record_ebay_success()
        return summary


async def _scan_once_unlocked(
    keywords: list[str],
    limit: int,
    notify: bool,
    resolved: EffectiveUserSettings,
    *,
    scan_cycle_id: int | None = None,
) -> dict[str, Any]:
    if not settings.ebay_configured:
        raise HTTPException(status_code=400, detail="Configure EBAY_CLIENT_ID and EBAY_CLIENT_SECRET before scanning")

    ebay = EbayClient(settings)
    listings = await ebay.search(keywords, limit)
    pricing_context = _build_user_pricing_context(int(resolved.user["id"]))
    identity = decision_identity(
        scoring_rules=scoring_rules, repair_values=pricing_context.repair_values,
        resale_research=pricing_context.resale_research, effective_settings=resolved,
    )
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
    detail_refresh_attempts = 0
    counters = ScanCycleCounters()

    for listing in listings:
        scanned += 1
        already_seen = storage.marketplace_item_exists(str(listing.get("item_id")))
        if already_seen:
            duplicates_skipped += 1
        else:
            new_items_found += 1
            storage.upsert_marketplace_item(listing)
        result, _item_overrides = _score_listing_for_user(
            listing,
            resolved,
            pricing_context=pricing_context,
        )
        detail_reasons = _detail_refresh_reasons(listing, result, resolved)
        if detail_reasons and detail_refresh_attempts < MAX_DETAIL_REFRESHES_PER_SCAN:
            detail_refresh_attempts += 1
            detail_requested_at = _utc_now_iso()
            detailed_listing = await _fetch_selective_detail(ebay, listing)
            if detailed_listing:
                detail_fields = _detail_fields(detailed_listing)
                listing = {
                    **listing,
                    **detail_fields,
                    "detail_fetch_attempted_at": detail_requested_at,
                    "detail_fetch_status": "succeeded",
                    "detail_fetch_reason": ",".join(detail_reasons),
                    "detail_fetch_recovered_fields": _detail_fetch_fields(listing, detail_fields),
                    "detail_fetch_failure_reason": "",
                    "detail_fetch_retry_after": None,
                }
                result, _item_overrides = _score_listing_for_user(
                    listing,
                    resolved,
                    pricing_context=pricing_context,
                )
            else:
                listing.update({
                    "detail_fetch_attempted_at": detail_requested_at,
                    "detail_fetch_status": "failed",
                    "detail_fetch_reason": ",".join(detail_reasons),
                    "detail_fetch_recovered_fields": [],
                    "detail_fetch_failure_reason": "detail_unavailable_or_transient_failure",
                    "detail_fetch_retry_after": (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat(),
                })
        item = {**listing, **result.as_item_fields(), **identity, "_scored_for_user": True}
        item.update(_repair_snapshot_for_item(item, user_id=int(resolved.user["id"]), pricing_context=pricing_context, correction=_item_overrides["correction"]))
        item = _apply_availability_and_auction_policy(item)
        ignored = storage.ignored_match(listing, user_id=int(resolved.user["id"]))
        if ignored:
            item.update(ignored)
        storage.upsert_user_item(int(resolved.user["id"]), item)
        stored_item = storage.get_user_item(int(resolved.user["id"]), item["item_id"], **resolved.freshness_kwargs()) or item
        stored_item = _decorate_item_for_user(stored_item, user_id=int(resolved.user["id"]), pricing_context=pricing_context)
        alert_decision = evaluate_alert_decision(stored_item, result, resolved)
        stored_item.update({
            "alert_tier": alert_decision.tier,
            "tier_eligible": alert_decision.eligible,
            "alert_decision": alert_decision.as_dict(),
        })

        if stored_item.get("fresh_for_active_queue"):
            fresh_items_found += 1
        if stored_item.get("stale"):
            stale_items_seen += 1

        if item.get("status") == "rejected":
            _record_decision_trace(
                scan_cycle_id=scan_cycle_id,
                counters=counters,
                user_id=int(resolved.user["id"]),
                item=stored_item,
                result=result,
                current_settings=resolved,
                alert_block_reasons=_alert_block_reasons(stored_item, result, resolved),
            )
            rejected += 1
            continue
        if item.get("status") == "candidate":
            candidates += 1
        if _is_fresh_best_find(stored_item, result, resolved):
            best_finds += 1
        elif _is_priority_review_candidate(stored_item):
            priority_review += 1

        should_alert, notification_block_reason, was_deduplicated = _notification_delivery_gate(
            stored_item,
            result,
            resolved,
            decision=alert_decision,
            notify=notify,
            user_id=int(resolved.user["id"]),
        )
        alert_block_reasons = [] if should_alert else _alert_block_reasons(stored_item, result, resolved)
        _record_decision_trace(
            scan_cycle_id=scan_cycle_id,
            counters=counters,
            user_id=int(resolved.user["id"]),
            item=stored_item,
            result=result,
            current_settings=resolved,
            alert_block_reasons=alert_block_reasons,
        )
        if notification_block_reason and notification_block_reason not in {"retry_backoff", "permanent_delivery_failure"}:
            _record_notification_skip(
                resolved,
                item_id=stored_item["item_id"],
                scan_cycle_id=scan_cycle_id,
                notification_type=(alert_decision.tier or "suppressed").lower(),
                reason=notification_block_reason,
                deduplicated=was_deduplicated,
                item=stored_item,
                decision=alert_decision,
                next_eligible_at=((datetime.now(timezone.utc) + timedelta(hours=1)).isoformat() if notification_block_reason == "review_hourly_limit" else None),
            )
        if should_alert:
            counters.alerts_attempted += 1
        if should_alert and await _send_alert_notification(
            notifier,
            stored_item,
            user_id=int(resolved.user["id"]),
            scan_cycle_id=scan_cycle_id,
            destination_source=str(resolved.notification_settings.get("webhook_source") or ""),
            decision=alert_decision,
        ):
            storage.mark_alerted_for_user(int(resolved.user["id"]), stored_item["item_id"])
            alerts_sent += 1
        elif should_alert:
            counters.alerts_failed += 1

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
        "gem": int(counters.final_bucket_counts.get("gem", 0)),
        "profitable": int(counters.final_bucket_counts.get("profitable", 0)),
        "review": int(counters.final_bucket_counts.get("review", 0)),
        "priority_review": priority_review,
        "candidates": candidates,
        "rejected": rejected,
        "alerts_sent": alerts_sent,
        "alerted": alerts_sent,
        "duplicates_skipped": duplicates_skipped,
        "keywords": keywords,
        "_cycle_counters": counters,
    }


async def scan_shared_once(
    *,
    limit: int,
    notify: bool,
    triggered_by_user: dict[str, Any] | None,
    background_mode: bool,
    keyword_filter: list[str] | None = None,
    cycle_mode: str | None = None,
    force_source_call: bool = False,
    offload_scan_work: bool = False,
) -> dict[str, Any]:
    resolved_cycle_mode = cycle_mode or ("shared_background" if background_mode else "manual")
    lease_run = await run_with_scan_lease(
        storage,
        lambda: _scan_shared_once_process_locked(
            limit=limit, notify=notify, triggered_by_user=triggered_by_user,
            background_mode=background_mode, keyword_filter=keyword_filter,
            cycle_mode=cycle_mode, force_source_call=force_source_call,
            offload_scan_work=offload_scan_work,
        ),
        lease_name=GLOBAL_SCAN_LEASE_NAME,
        worker_id=WORKER_ID,
        hostname=_hostname(),
        process_id=_process_id(),
    )
    if not lease_run.acquired:
        _persist_skipped_scan_cycle(
            mode=resolved_cycle_mode, reason="scan_already_running",
            user_id=int(triggered_by_user["id"]) if triggered_by_user else None,
            keywords=list(keyword_filter or []),
        )
        return _empty_scan_summary(keywords=list(keyword_filter or []), reason="scan_already_running", mode="shared")
    return lease_run.value


async def _scan_shared_once_process_locked(
    *,
    limit: int,
    notify: bool,
    triggered_by_user: dict[str, Any] | None,
    background_mode: bool,
    keyword_filter: list[str] | None = None,
    cycle_mode: str | None = None,
    force_source_call: bool = False,
    offload_scan_work: bool = False,
) -> dict[str, Any]:
    resolved_cycle_mode = cycle_mode or ("shared_background" if background_mode else "manual")
    if _scan_lock.locked():
        logger.info("Shared scan skipped because another scan is already running")
        _persist_skipped_scan_cycle(
            mode=resolved_cycle_mode,
            reason="scan_already_running",
            user_id=int(triggered_by_user["id"]) if triggered_by_user else None,
            users_considered=0,
            users_scanned=0,
            keywords=list(keyword_filter or []),
        )
        return _empty_scan_summary(
            keywords=list(keyword_filter or []),
            reason="scan_already_running",
            mode="shared",
        )
    source_status = _ebay_source_status()
    if source_status.get("status") == "cooling_down" and not force_source_call:
        resolved_users = _active_shared_scan_users(background_mode=background_mode)
        scan_limit = int(limit or settings.max_results_per_keyword)
        keywords = list(keyword_filter or [])
        if resolved_users:
            plan = _build_shared_search_plan(
                resolved_users,
                limit=scan_limit,
                keyword_filter=keyword_filter,
            )
            if plan:
                keywords = [entry.keyword for entry in plan]
        if resolved_users and keywords:
            logger.info("Shared scan skipped because eBay source is cooling down until %s", source_status.get("cooldown_until"))
            _persist_source_cooldown_skip(
                mode=resolved_cycle_mode,
                user_id=int(triggered_by_user["id"]) if triggered_by_user else None,
                users_considered=len(resolved_users),
                users_scanned=0,
                keywords=keywords,
                status=source_status,
            )
            summary = _empty_scan_summary(
                keywords=keywords,
                reason="ebay_rate_limited",
                mode="shared",
            )
            summary["users_considered"] = len(resolved_users)
            summary["cooldown_until"] = source_status.get("cooldown_until")
            return summary
    cycle_id = _create_scan_cycle(
        mode=resolved_cycle_mode,
        user_id=int(triggered_by_user["id"]) if triggered_by_user else None,
        keywords_searched=list(keyword_filter or []),
    )
    runtime_context: dict[str, Any] = {"keywords": list(keyword_filter or []), "users_considered": 0, "users_scanned": 0}
    async with _scan_lock:
        try:
            if offload_scan_work:
                summary = await _run_background_scan_work(
                    lambda: _scan_shared_once_unlocked(
                        limit=limit,
                        notify=notify,
                        triggered_by_user=triggered_by_user,
                        background_mode=background_mode,
                        keyword_filter=keyword_filter,
                        scan_cycle_id=cycle_id,
                        scan_cycle_runtime_context=runtime_context,
                    ),
                    label=resolved_cycle_mode,
                )
            else:
                summary = await _scan_shared_once_unlocked(
                    limit=limit,
                    notify=notify,
                    triggered_by_user=triggered_by_user,
                    background_mode=background_mode,
                    keyword_filter=keyword_filter,
                    scan_cycle_id=cycle_id,
                    scan_cycle_runtime_context=runtime_context,
                )
        except Exception as exc:
            rate_limit_status = None
            if isinstance(exc, EbayRateLimitError):
                rate_limit_status = _record_ebay_rate_limit(exc, keyword=str(runtime_context.get("current_keyword") or ""))
            _finish_scan_cycle_from_summary(
                cycle_id,
                status="failed",
                error_message=_scan_cycle_error_message(
                    exc,
                    keyword=str(runtime_context.get("current_keyword") or ""),
                    source_status=rate_limit_status,
                ),
                users_considered=int(runtime_context.get("users_considered") or 0),
                users_scanned=int(runtime_context.get("users_scanned") or 0),
                keywords_searched=list(runtime_context.get("keywords") or keyword_filter or []),
                **(
                    _rate_limit_cycle_metadata(
                        exc,
                        keyword=str(runtime_context.get("current_keyword") or ""),
                        status=rate_limit_status,
                    )
                    if isinstance(exc, EbayRateLimitError)
                    else {}
                ),
            )
            raise
        counters = summary.pop("_cycle_counters", None)
        status = "skipped" if summary.get("skipped") else "completed"
        _finish_scan_cycle_from_summary(
            cycle_id,
            status=status,
            summary=summary,
            counters=counters,
            users_considered=int(summary.get("users_considered") or summary.get("active_users") or 0),
            users_scanned=int(summary.get("active_users") or 0),
            keywords_searched=summary.get("keywords") or list(keyword_filter or []),
        )
        if status == "completed":
            _record_ebay_success()
        return summary


async def _scan_shared_once_unlocked(
    *,
    limit: int,
    notify: bool,
    triggered_by_user: dict[str, Any] | None,
    background_mode: bool,
    keyword_filter: list[str] | None = None,
    scan_cycle_id: int | None = None,
    scan_cycle_runtime_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not settings.ebay_configured:
        raise HTTPException(status_code=400, detail="Configure EBAY_CLIENT_ID and EBAY_CLIENT_SECRET before scanning")

    resolved_users = _active_shared_scan_users(background_mode=background_mode)
    if scan_cycle_runtime_context is not None:
        scan_cycle_runtime_context["users_considered"] = len(resolved_users)
        scan_cycle_runtime_context["users_scanned"] = len(resolved_users)
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
        include_system_rotation=background_mode and not keyword_filter,
    )
    if not plan:
        return _empty_scan_summary(
            keywords=list(keyword_filter or []),
            reason="no_enabled_keywords",
            mode="shared",
        )
    if scan_cycle_runtime_context is not None:
        scan_cycle_runtime_context["keywords"] = [entry.keyword for entry in plan]

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
    identity_cache: dict[int, dict[str, str]] = {}
    unique_listings: dict[str, dict[str, Any]] = {}
    item_subscribers: dict[str, set[int]] = {}
    item_search_ids: dict[str, set[int]] = {}
    search_metrics: dict[int, dict[str, Any]] = {}
    api_calls_made = 0
    total_items_returned = 0
    total_users_evaluated = 0
    total_items_scored = 0
    total_alerts_sent = 0
    new_items_found = 0
    duplicates_skipped = 0
    duplicates_not_rescored = 0
    fresh_items_found = 0
    stale_items_seen = 0
    best_finds = 0
    priority_review = 0
    candidates = 0
    rejected = 0
    counters = ScanCycleCounters()
    detail_refresh_attempts = 0

    try:
        for entry in plan:
            if scan_cycle_runtime_context is not None:
                scan_cycle_runtime_context["current_keyword"] = entry.keyword
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
            search_metrics[search_id] = {
                "unique_new_items": set(),
                "duplicate_items": 0,
                "viable_whole_phones": set(),
                "detail_attempts": set(), "detail_successes": set(), "detail_failures": set(),
                "components": set(), "needs_data": set(), "rejected": set(),
                "alert_eligible": set(),
                "tiers": {},
                "alerts": set(),
            }
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
                item_search_ids.setdefault(item_id, set()).add(search_id)
                if item_id not in unique_listings:
                    existing_market = storage.get_marketplace_item(item_id, marketplace=entry.marketplace)
                    already_seen = existing_market is not None
                    market_changed = False
                    if existing_market:
                        for field_name in ("title", "price", "shipping", "total_cost", "availability_status", "buying_option_summary"):
                            incoming = listing.get(field_name)
                            previous = existing_market.get(field_name)
                            if incoming not in (None, "") and str(incoming) != str(previous if previous is not None else ""):
                                market_changed = True
                                break
                    if already_seen:
                        duplicates_skipped += 1
                        search_metrics[search_id]["duplicate_items"] += 1
                    else:
                        new_items_found += 1
                        search_metrics[search_id]["unique_new_items"].add(item_id)
                        storage.upsert_marketplace_item(listing, marketplace=entry.marketplace)
                    unique_listings[item_id] = {
                        **dict(listing),
                        "_notifierr_already_seen": already_seen,
                        "_notifierr_market_changed": market_changed,
                    }
                else:
                    search_metrics[search_id]["duplicate_items"] += 1
                    prior = unique_listings[item_id]
                    unique_listings[item_id] = {
                        **prior, **dict(listing),
                        "_notifierr_already_seen": bool(prior.get("_notifierr_already_seen")),
                        "_notifierr_market_changed": bool(prior.get("_notifierr_market_changed")),
                    }
                subscriber_ids = item_subscribers.setdefault(item_id, set())
                for resolved in entry.subscribers:
                    if resolved.user:
                        subscriber_ids.add(int(resolved.user["id"]))

        for item_id, listing in unique_listings.items():
            subscriber_ids = sorted(item_subscribers.get(item_id, set()))
            subscribed_users = [resolved_by_user_id[user_id] for user_id in subscriber_ids if user_id in resolved_by_user_id]
            if not subscribed_users:
                continue
            already_seen = bool(listing.pop("_notifierr_already_seen", False))
            market_changed = bool(listing.pop("_notifierr_market_changed", False))
            if already_seen and not market_changed:
                users_needing_rescore = []
                for resolved in subscribed_users:
                    user_id = int(resolved.user["id"])
                    stored_identity = storage.user_item_state_identity(user_id, item_id)
                    if stored_identity is None:
                        users_needing_rescore.append(resolved)
                        continue
                    pricing_context = _pricing_context_for_user(user_id, pricing_context_cache)
                    current_identity = identity_cache.get(user_id)
                    if current_identity is None:
                        current_identity = decision_identity(
                            scoring_rules=scoring_rules,
                            repair_values=pricing_context.repair_values,
                            resale_research=pricing_context.resale_research,
                            effective_settings=resolved,
                        )
                        identity_cache[user_id] = current_identity
                    if any(stored_identity.get(key) != current_identity.get(key) for key in current_identity):
                        users_needing_rescore.append(resolved)
                if not users_needing_rescore:
                    duplicates_not_rescored += 1
                    continue
                subscribed_users = users_needing_rescore
            initial_results: dict[int, Any] = {}
            should_fetch_detail = False
            detail_fetch_succeeded = False
            detail_reasons: list[str] = []
            for resolved in subscribed_users:
                pricing_context = _pricing_context_for_user(int(resolved.user["id"]), pricing_context_cache)
                result, _item_overrides = _score_listing_for_user(
                    listing,
                    resolved,
                    pricing_context=pricing_context,
                )
                initial_results[int(resolved.user["id"])] = (result, _item_overrides)
                user_detail_reasons = _detail_refresh_reasons(listing, result, resolved)
                if user_detail_reasons:
                    should_fetch_detail = True
                    detail_reasons.extend(user_detail_reasons)
            if should_fetch_detail and detail_refresh_attempts < MAX_DETAIL_REFRESHES_PER_SCAN:
                detail_refresh_attempts += 1
                for search_id in item_search_ids.get(item_id, set()):
                    search_metrics[search_id]["detail_attempts"].add(item_id)
                detail_requested_at = _utc_now_iso()
                detailed_listing = await _fetch_selective_detail(ebay, listing)
                if detailed_listing:
                    detail_fetch_succeeded = True
                    for search_id in item_search_ids.get(item_id, set()):
                        search_metrics[search_id]["detail_successes"].add(item_id)
                    detail_fields = _detail_fields(detailed_listing)
                    listing = {
                        **listing,
                        **detail_fields,
                        "detail_fetch_attempted_at": detail_requested_at,
                        "detail_fetch_status": "succeeded",
                        "detail_fetch_reason": ",".join(dict.fromkeys(detail_reasons)),
                        "detail_fetch_recovered_fields": _detail_fetch_fields(listing, detail_fields),
                        "detail_fetch_failure_reason": "",
                        "detail_fetch_retry_after": None,
                    }
                    for resolved in subscribed_users:
                        usage[int(resolved.user["id"])]["detail_refreshes"] += 1
                else:
                    for search_id in item_search_ids.get(item_id, set()):
                        search_metrics[search_id]["detail_failures"].add(item_id)
                    listing.update({
                        "detail_fetch_attempted_at": detail_requested_at,
                        "detail_fetch_status": "failed",
                        "detail_fetch_reason": ",".join(dict.fromkeys(detail_reasons)),
                        "detail_fetch_recovered_fields": [],
                        "detail_fetch_failure_reason": "detail_unavailable_or_transient_failure",
                        "detail_fetch_retry_after": (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat(),
                    })
            storage.upsert_marketplace_item(listing)
            for resolved in subscribed_users:
                user_id = int(resolved.user["id"])
                pricing_context = _pricing_context_for_user(user_id, pricing_context_cache)
                result, item_overrides = initial_results[user_id]
                if detail_fetch_succeeded:
                    result, item_overrides = _score_listing_for_user(
                        listing,
                        resolved,
                        pricing_context=pricing_context,
                    )
                if user_id not in identity_cache:
                    identity_cache[user_id] = decision_identity(
                        scoring_rules=scoring_rules, repair_values=pricing_context.repair_values,
                        resale_research=pricing_context.resale_research, effective_settings=resolved,
                    )
                item = {**listing, **result.as_item_fields(), **identity_cache[user_id], "_scored_for_user": True}
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
                alert_decision = evaluate_alert_decision(stored_item, result, resolved)
                stored_item.update({
                    "alert_tier": alert_decision.tier,
                    "tier_eligible": alert_decision.eligible,
                    "alert_decision": alert_decision.as_dict(),
                })
                for search_id in item_search_ids.get(item_id, set()):
                    metric = search_metrics[search_id]
                    if stored_item.get("item_type") == "component":
                        metric["components"].add(item_id)
                    if _is_needs_data_item(stored_item):
                        metric["needs_data"].add(item_id)
                    if item.get("status") == "rejected":
                        metric["rejected"].add(item_id)
                    if alert_decision.eligible:
                        metric["alert_eligible"].add(item_id)
                    storage.update_shared_scan_result(
                        search_id, item_id, newly_discovered=item_id in metric["unique_new_items"],
                        detail_status=str(listing.get("detail_fetch_status") or "not_requested"),
                        item_type=str(stored_item.get("item_type") or "ambiguous"),
                        tier=alert_decision.tier, needs_data=_is_needs_data_item(stored_item),
                        rejected=item.get("status") == "rejected", alert_eligible=alert_decision.eligible,
                    )
                    if stored_item.get("whole_phone_confidence_passed") and not stored_item.get("hard_reject_flags"):
                        metric["viable_whole_phones"].add(item_id)
                    if alert_decision.tier:
                        tier_rank = {"REVIEW": 1, "PROFITABLE": 2, "GEM": 3}
                        previous = metric["tiers"].get(item_id)
                        if previous is None or tier_rank[alert_decision.tier] > tier_rank[previous]:
                            metric["tiers"][item_id] = alert_decision.tier

                if stored_item.get("fresh_for_active_queue"):
                    fresh_items_found += 1
                if stored_item.get("stale"):
                    stale_items_seen += 1
                if item.get("status") == "rejected":
                    for search_id in item_search_ids.get(item_id, set()):
                        storage.set_shared_scan_notification_status(search_id, item_id, "not_eligible")
                    _record_decision_trace(
                        scan_cycle_id=scan_cycle_id,
                        counters=counters,
                        user_id=user_id,
                        item=stored_item,
                        result=result,
                        current_settings=resolved,
                        alert_block_reasons=_alert_block_reasons(stored_item, result, resolved),
                    )
                    rejected += 1
                    continue
                if item.get("status") == "candidate":
                    candidates += 1
                if _is_fresh_best_find(stored_item, result, resolved):
                    best_finds += 1
                elif _is_priority_review_candidate(stored_item):
                    priority_review += 1

                should_alert, notification_block_reason, was_deduplicated = _notification_delivery_gate(
                    stored_item,
                    result,
                    resolved,
                    decision=alert_decision,
                    notify=notify,
                    user_id=user_id,
                )
                alert_block_reasons = [] if should_alert else _alert_block_reasons(stored_item, result, resolved)
                _record_decision_trace(
                    scan_cycle_id=scan_cycle_id,
                    counters=counters,
                    user_id=user_id,
                    item=stored_item,
                    result=result,
                    current_settings=resolved,
                    alert_block_reasons=alert_block_reasons,
                )
                if not should_alert:
                    for search_id in item_search_ids.get(item_id, set()):
                        storage.set_shared_scan_notification_status(
                            search_id, item_id, "blocked" if notification_block_reason else "not_eligible",
                        )
                    if notification_block_reason and notification_block_reason not in {"retry_backoff", "permanent_delivery_failure"}:
                        _record_notification_skip(
                            resolved,
                            item_id=stored_item["item_id"],
                            scan_cycle_id=scan_cycle_id,
                            notification_type=(alert_decision.tier or "suppressed").lower(),
                            reason=notification_block_reason,
                            deduplicated=was_deduplicated,
                            item=stored_item,
                            decision=alert_decision,
                            next_eligible_at=((datetime.now(timezone.utc) + timedelta(hours=1)).isoformat() if notification_block_reason == "review_hourly_limit" else None),
                        )
                    continue
                counters.alerts_attempted += 1
                notifier = DiscordNotifier(resolved.resolved_discord_webhook_url)
                if await _send_alert_notification(
                    notifier,
                    stored_item,
                    user_id=user_id,
                    scan_cycle_id=scan_cycle_id,
                    destination_source=str(resolved.notification_settings.get("webhook_source") or ""),
                    decision=alert_decision,
                ):
                    for search_id in item_search_ids.get(item_id, set()):
                        search_metrics[search_id]["alerts"].add(item_id)
                        storage.set_shared_scan_notification_status(search_id, item_id, "sent")
                    storage.mark_alerted_for_user(user_id, stored_item["item_id"])
                    total_alerts_sent += 1
                    usage[user_id]["alerts_sent"] += 1
                else:
                    for search_id in item_search_ids.get(item_id, set()):
                        storage.set_shared_scan_notification_status(search_id, item_id, "failed")
                    counters.alerts_failed += 1

        for search_id, metric in search_metrics.items():
            tier_counts = Counter(metric["tiers"].values())
            storage.finish_shared_scan_search(
                search_id,
                unique_new_items=len(metric["unique_new_items"]),
                duplicate_items=int(metric["duplicate_items"]),
                viable_whole_phones=len(metric["viable_whole_phones"]),
                gem_count=int(tier_counts["GEM"]),
                profitable_count=int(tier_counts["PROFITABLE"]),
                review_count=int(tier_counts["REVIEW"]),
                alert_count=len(metric["alerts"]),
                detail_fetch_attempts=len(metric["detail_attempts"]),
                detail_fetch_successes=len(metric["detail_successes"]),
                detail_fetch_failures=len(metric["detail_failures"]),
                component_count=len(metric["components"]),
                needs_data_count=len(metric["needs_data"]),
                reject_count=len(metric["rejected"]),
                alert_eligible_count=len(metric["alert_eligible"]),
            )

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

        if notify and background_mode:
            for resolved in resolved_users:
                catchup = await _run_actionable_catchup(resolved, scan_cycle_id=scan_cycle_id, ebay=ebay)
                total_alerts_sent += int(catchup["sent"])

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
        "gem": int(counters.final_bucket_counts.get("gem", 0)),
        "profitable": int(counters.final_bucket_counts.get("profitable", 0)),
        "review": int(counters.final_bucket_counts.get("review", 0)),
        "priority_review": priority_review,
        "candidates": candidates,
        "rejected": rejected,
        "alerts_sent": total_alerts_sent,
        "alerted": total_alerts_sent,
        "duplicates_skipped": duplicates_skipped,
        "duplicates_not_rescored": duplicates_not_rescored,
        "unique_searches": len(plan),
        "unique_marketplace_items": len(unique_listings),
        "active_users": len(resolved_users),
        "users_considered": len(resolved_users),
        "keywords": [entry.keyword for entry in plan],
        "_cycle_counters": counters,
    }
    logger.info("Shared scan summary %s", summary)
    return summary


def _notification_provider_diagnostics(notifier: Any, *, default_category: str) -> tuple[str, str, int | None]:
    category = str(getattr(notifier, "last_failure_category", "") or default_category)[:120]
    message = str(getattr(notifier, "last_error_message", "") or category)[:500]
    status = getattr(notifier, "last_provider_status", None)
    return category, message, int(status) if isinstance(status, int) else None


def _notification_destination_identity(user_id: int, discord_webhook: str | None) -> str:
    identities: list[str] = []
    if discord_webhook:
        identities.append(destination_identity(discord_webhook))
    notification = storage.get_user_notification_settings(user_id) or {}
    if bool(notification.get("push_enabled", True)) and settings.push_configured:
        if storage.list_push_subscriptions(user_id, enabled_only=True):
            identities.append(push_destination_identity(user_id))
    if not identities:
        return destination_identity(discord_webhook)
    if len(identities) == 1:
        return identities[0]
    digest = hashlib.sha256("|".join(identities).encode("utf-8")).hexdigest()
    return f"channels:{digest}"


def _record_notification_skip(
    resolved: EffectiveUserSettings,
    *,
    item_id: str,
    scan_cycle_id: int | None,
    notification_type: str,
    reason: str,
    deduplicated: bool = False,
    item: dict[str, Any] | None = None,
    decision: AlertDecision | None = None,
    next_eligible_at: str | None = None,
) -> None:
    if not resolved.user:
        return
    identity = _notification_destination_identity(int(resolved.user["id"]), resolved.resolved_discord_webhook_url)
    snapshot = notification_snapshot(item or {"item_id": item_id}, decision, destination=identity) if decision else None
    prior = storage.latest_successful_notification(int(resolved.user["id"]), item_id, identity) if deduplicated else None
    status = (
        "skipped_duplicate" if deduplicated
        else "deferred_rate_limit" if reason == "review_hourly_limit"
        else "cancelled_unavailable" if reason == "cancelled_unavailable"
        else "skipped"
    )
    storage.create_notification_attempt(
        user_id=int(resolved.user["id"]),
        item_id=item_id,
        scan_cycle_id=scan_cycle_id,
        notification_type=notification_type,
        skipped=True,
        deduplicated=deduplicated,
        failure_category=reason,
        destination_source=str(resolved.notification_settings.get("webhook_source") or ""),
        status=status,
        fingerprint=snapshot.fingerprint if snapshot else "",
        notification_tier=snapshot.tier if snapshot else notification_type.upper(),
        effective_price=snapshot.effective_price if snapshot else None,
        expected_profit=snapshot.expected_profit if snapshot else None,
        expected_roi=snapshot.expected_roi if snapshot else None,
        principal_damage=snapshot.principal_damage if snapshot else "unknown",
        availability_state=snapshot.availability_state if snapshot else "unknown",
        confidence=snapshot.confidence if snapshot else "low",
        destination_identity=identity,
        prior_success_attempt_id=int(prior["id"]) if prior else None,
        fingerprint_match_reason=(str(prior.get("_match_reason") or "equivalent_or_stronger_success") if prior else ""),
        next_eligible_at=next_eligible_at,
    )


def _notification_delivery_gate(
    item: dict[str, Any],
    result: Any,
    resolved: EffectiveUserSettings,
    *,
    decision: AlertDecision | None = None,
    notify: bool,
    user_id: int,
) -> tuple[bool, str, bool]:
    decision = decision or evaluate_alert_decision(item, result, resolved)
    if not notify or not decision.eligible or not decision.tier:
        return False, "", False
    notification = resolved.notification_settings
    tier = decision.tier
    if tier == "GEM" and not bool(notification.get("send_gem_immediately", resolved.notify_best_finds)):
        return False, "gem_immediate_notifications_disabled", False
    if tier == "PROFITABLE" and not bool(notification.get("send_profitable_immediately", True)):
        return False, "profitable_immediate_notifications_disabled", False
    review_mode = str(notification.get("review_delivery_mode") or "immediate").lower()
    if tier == "REVIEW" and review_mode != "immediate":
        return False, f"review_delivery_{review_mode}", False
    now = datetime.now(timezone.utc)
    dedupe_hours = max(1, int(notification.get("duplicate_suppression_hours", 72) or 72))
    item_id = str(item.get("item_id") or "")
    identity = _notification_destination_identity(user_id, resolved.resolved_discord_webhook_url)
    recovered = storage.recover_stale_notification_claims(user_id, item_id, identity, now=now)
    if recovered:
        logger.warning("Recovered expired notification claims user_id=%s item_id=%s attempt_ids=%s", user_id, item_id, recovered)
    current = notification_snapshot(item, decision, destination=identity)
    prior = storage.latest_successful_notification(user_id, item_id, identity)
    prior_at = _parse_utc_datetime(str((prior or {}).get("successful_at") or (prior or {}).get("updated_at") or ""))
    if prior and prior_at and (now - prior_at).total_seconds() <= dedupe_hours * 3600:
        reason = successful_dedupe_reason(
            current,
            prior,
            price_amount=float(notification.get("meaningful_price_drop_amount", 20) or 0),
            price_percent=float(notification.get("meaningful_price_drop_percent", 0.05) or 0),
            profit_amount=float(notification.get("meaningful_profit_increase_amount", 25) or 0),
            profit_percent=float(notification.get("meaningful_profit_increase_percent", 0.15) or 0),
            roi_amount=float(notification.get("meaningful_roi_increase", 0.10) or 0),
        )
        if reason:
            prior["_match_reason"] = reason
            return False, "already_alerted", True
    latest_failure = next(
        (
            entry for entry in storage.list_notification_attempts(user_id=user_id, limit=500)
            if str(entry.get("item_id") or "") == item_id and bool(entry.get("failed"))
            and str(entry.get("fingerprint") or "") in {"", current.fingerprint}
        ),
        None,
    )
    if latest_failure:
        if int(latest_failure.get("retry_count") or 0) >= 5:
            return False, "permanent_delivery_failure", False
        retry_at = _parse_utc_datetime(str(latest_failure.get("next_eligible_at") or ""))
        if retry_at and retry_at > now:
            return False, "retry_backoff", False
    if tier == "REVIEW":
        attempts = storage.list_notification_attempts(user_id=user_id, limit=500)
        max_review = max(0, int(notification.get("max_review_alerts_per_hour", 2) or 0))
        recent_review = sum(
            1
            for attempt in attempts
            if str(attempt.get("notification_type") or "").lower() == "review"
            and bool(attempt.get("sent"))
            and (created := _parse_utc_datetime(str(attempt.get("created_at") or "")))
            and (now - created).total_seconds() <= 3600
        )
        if max_review == 0 or recent_review >= max_review:
            return False, "review_hourly_limit", False
    if not bool(resolved.notification_settings.get("notification_ready")):
        return False, str(resolved.notification_settings.get("notification_block_reason") or "webhook_missing"), False
    return True, "", False


async def _send_alert_notification(
    notifier: DiscordNotifier | None,
    item: dict[str, Any],
    *,
    user_id: int,
    scan_cycle_id: int | None,
    destination_source: str,
    decision: AlertDecision | None = None,
    source: str = "live_scan",
) -> bool:
    decision = decision or AlertDecision(
        eligible=True, tier=str(item.get("alert_tier") or "REVIEW"),
        expected_profit=float(item.get("profit_mid") or 0), expected_roi=0,
    )
    identity = _notification_destination_identity(user_id, notifier.webhook_url if notifier else None)
    snapshot = notification_snapshot(item, decision, destination=identity)
    history = storage.list_notification_attempts(user_id=user_id, limit=500)
    previous_failure = next((entry for entry in history if entry.get("item_id") == snapshot.item_id and entry.get("failed")), None)
    retry_count = min(5, int(previous_failure.get("retry_count") or 0) + 1) if previous_failure else 0
    prior_success = storage.latest_successful_notification(user_id, snapshot.item_id, identity)
    effective_source = source
    if source == "live_scan" and prior_success:
        prior_tier = str(prior_success.get("notification_tier") or prior_success.get("notification_type") or "").upper()
        tier_rank = {"REVIEW": 1, "PROFITABLE": 2, "GEM": 3}
        if tier_rank.get(snapshot.tier, 0) > tier_rank.get(prior_tier, 0):
            effective_source = "tier_upgrade"
        elif float(prior_success.get("effective_price") or 0) > snapshot.effective_price:
            effective_source = "price_drop_realert"
    attempt_id = storage.create_notification_attempt(
        user_id=user_id,
        item_id=str(item.get("item_id") or "") or None,
        scan_cycle_id=scan_cycle_id,
        notification_type=str(item.get("alert_tier") or "gem").lower(),
        attempted=True,
        destination_source=destination_source,
        status="pending",
        fingerprint=snapshot.fingerprint,
        notification_tier=snapshot.tier,
        effective_price=snapshot.effective_price,
        expected_profit=snapshot.expected_profit,
        expected_roi=snapshot.expected_roi,
        principal_damage=snapshot.principal_damage,
        availability_state=snapshot.availability_state,
        confidence=snapshot.confidence,
        destination_identity=snapshot.destination_identity,
        source=effective_source,
        retry_count=retry_count,
        parent_attempt_id=int(previous_failure["id"]) if previous_failure else None,
        claim=True,
    )
    if attempt_id is None:
        logger.info("Notification already claimed user_id=%s item_id=%s", user_id, item.get("item_id"))
        return False
    polling_status.notification_attempted()
    discord_sent = False
    discord_category = ""
    discord_message = ""
    provider_status = None
    if notifier is not None:
        try:
            discord_sent = bool(await notifier.send_deal(item))
        except Exception as exc:
            discord_category = "provider_exception"
            discord_message = type(exc).__name__
        if not discord_sent and not discord_category:
            discord_category, discord_message, provider_status = _notification_provider_diagnostics(
                notifier,
                default_category="provider_rejected",
            )
        if discord_sent:
            provider_status = getattr(notifier, "last_provider_status", None)

    push_result = await asyncio.to_thread(
        send_push_to_user,
        storage,
        settings,
        user_id=user_id,
        item=item,
        tier=decision.tier or str(item.get("alert_tier") or "REVIEW"),
        scan_cycle_id=scan_cycle_id,
    )
    sent = discord_sent or push_result.sent
    if not sent:
        category = discord_category or ("push_delivery_failed" if push_result.selected else "no_delivery_channel")
        message = discord_message or (
            f"push selected={push_result.selected} failed={push_result.failed} disabled={push_result.disabled}"
        )
        storage.finish_notification_attempt(
            attempt_id,
            sent=False,
            failed=True,
            failure_category=category,
            error_message=message,
            provider_status=provider_status,
            next_eligible_at=(datetime.now(timezone.utc) + timedelta(seconds=min(1800, 60 * (2 ** retry_count)))).isoformat(),
        )
        logger.warning(
            "Alert notification send failed user_id=%s item_id=%s discord_category=%s push=%s",
            user_id,
            item.get("item_id"),
            discord_category,
            push_result.as_dict(),
        )
        polling_status.notification_failed()
        return False
    finalized = storage.finish_notification_attempt(
        attempt_id,
        sent=True,
        failed=False,
        provider_status=int(provider_status) if isinstance(provider_status, int) else None,
    )
    if not finalized:
        logger.warning("Notification claim expired before success finalization user_id=%s item_id=%s attempt_id=%s", user_id, item.get("item_id"), attempt_id)
        return False
    polling_status.notification_sent()
    logger.info(
        "Alert notification sent user_id=%s item_id=%s discord_sent=%s push=%s",
        user_id,
        item.get("item_id"),
        discord_sent,
        push_result.as_dict(),
    )
    return True


async def _run_actionable_catchup(
    resolved: EffectiveUserSettings,
    *,
    scan_cycle_id: int | None,
    ebay: EbayClient | None = None,
) -> dict[str, int]:
    """Drain a bounded, restart-safe rollout queue from current stored state."""
    if not resolved.user or not bool(resolved.notification_settings.get("catchup_enabled", True)):
        return {"candidates": 0, "attempted": 0, "sent": 0}
    user_id = int(resolved.user["id"])
    delivered = storage.successfully_notified_item_ids(user_id)
    candidates: list[tuple[dict[str, Any], AlertDecision]] = []
    for item in storage.list_user_items(user_id, limit=500, include_stale=False, **resolved.freshness_kwargs()):
        item = _decorate_item_for_user(item, user_id=user_id, pricing_context=_cached_pricing_context_for_user(user_id))
        decision = evaluate_alert_decision(item, SimpleNamespace(), resolved)
        allowed = {"GEM", "PROFITABLE"}
        if bool(resolved.notification_settings.get("catchup_include_review", False)):
            allowed.add("REVIEW")
        if decision.eligible and decision.tier in allowed and str(item.get("item_id") or "") not in delivered:
            candidates.append((item, decision))
    rank = {"GEM": 0, "PROFITABLE": 1, "REVIEW": 2}
    candidates.sort(key=lambda pair: (
        rank.get(str(pair[1].tier), 9), -pair[1].expected_profit, -pair[1].expected_roi,
        -((_parse_utc_datetime(str(pair[0].get("item_origin_at") or pair[0].get("found_at") or "")) or datetime.min.replace(tzinfo=timezone.utc)).timestamp()),
    ))
    attempted = sent = 0
    batch_size = max(1, min(50, int(resolved.notification_settings.get("catchup_batch_size", 5) or 5)))
    for item, decision in candidates:
        if attempted >= batch_size:
            break
        if ebay is not None:
            detail = await _fetch_selective_detail(ebay, item)
            if not detail:
                logger.info("Catch-up skipped because availability could not be rechecked item_id=%s", item.get("item_id"))
                continue
            item = _apply_availability_and_auction_policy({**item, **_detail_fields(detail)})
            storage.upsert_user_item(user_id, item)
            decision = evaluate_alert_decision(item, SimpleNamespace(), resolved)
            if not decision.eligible or decision.tier not in allowed:
                _record_notification_skip(
                    resolved, item_id=str(item["item_id"]), scan_cycle_id=scan_cycle_id,
                    notification_type=str(decision.tier or "suppressed").lower(),
                    reason="cancelled_unavailable" if str(item.get("availability_status") or "").lower() in {"sold", "ended", "unavailable"} else "catchup_no_longer_actionable",
                    item=item, decision=decision,
                )
                continue
        should_send, reason, deduplicated = _notification_delivery_gate(
            item, SimpleNamespace(), resolved, decision=decision, notify=True, user_id=user_id,
        )
        if not should_send:
            if reason and reason not in {"retry_backoff", "permanent_delivery_failure"}:
                _record_notification_skip(
                    resolved, item_id=str(item["item_id"]), scan_cycle_id=scan_cycle_id,
                    notification_type=str(decision.tier or "suppressed").lower(), reason=reason,
                    deduplicated=deduplicated, item=item, decision=decision,
                )
            continue
        attempted += 1
        enriched = {**item, "alert_tier": decision.tier, "alert_decision": decision.as_dict()}
        if await _send_alert_notification(
            DiscordNotifier(resolved.resolved_discord_webhook_url), enriched,
            user_id=user_id, scan_cycle_id=scan_cycle_id,
            destination_source=str(resolved.notification_settings.get("webhook_source") or ""),
            decision=decision, source="actionable_tier_rollout_catchup_v1",
        ):
            storage.mark_alerted_for_user(user_id, str(item["item_id"]))
            sent += 1
    return {"candidates": len(candidates), "attempted": attempted, "sent": sent}


TEST_NOTIFICATION_RATE_LIMIT_SECONDS = 60
TEST_NOTIFICATION_MESSAGE = "Notifierr test notification — configuration is working"


async def _send_test_notification(resolved: EffectiveUserSettings) -> dict[str, Any]:
    if not resolved.user:
        raise HTTPException(status_code=400, detail="No notification user is available")
    user_id = int(resolved.user["id"])
    recent = storage.latest_attempted_notification(user_id, "test")
    recent_at = _parse_utc_datetime((recent or {}).get("created_at"))
    if recent_at and (datetime.now(timezone.utc) - recent_at).total_seconds() < TEST_NOTIFICATION_RATE_LIMIT_SECONDS:
        storage.create_notification_attempt(
            user_id=user_id,
            notification_type="test",
            skipped=True,
            failure_category="rate_limited",
            destination_source=str(resolved.notification_settings.get("webhook_source") or ""),
        )
        raise HTTPException(
            status_code=429,
            detail=f"Test notifications are limited to one every {TEST_NOTIFICATION_RATE_LIMIT_SECONDS} seconds",
        )
    if not bool(resolved.notification_settings.get("notification_ready")):
        reason = str(resolved.notification_settings.get("notification_block_reason") or "webhook_missing")
        storage.create_notification_attempt(
            user_id=user_id,
            notification_type="test",
            skipped=True,
            failure_category=reason,
            destination_source=str(resolved.notification_settings.get("webhook_source") or ""),
        )
        raise HTTPException(status_code=400, detail=f"Notification configuration is not ready: {reason}")

    destination_source = str(resolved.notification_settings.get("webhook_source") or "")
    notifier = DiscordNotifier(resolved.resolved_discord_webhook_url)
    attempt_id = storage.create_notification_attempt(
        user_id=user_id,
        notification_type="test",
        attempted=True,
        destination_source=destination_source,
    )
    polling_status.notification_attempted()
    try:
        sent = await notifier.send_message(TEST_NOTIFICATION_MESSAGE)
    except Exception as exc:
        category = "provider_exception"
        storage.finish_notification_attempt(
            attempt_id,
            sent=False,
            failed=True,
            failure_category=category,
            error_message=type(exc).__name__,
        )
        polling_status.notification_failed()
        logger.warning("Test notification failed user_id=%s category=%s", user_id, category)
        return {"attempted": True, "sent": False, "failed": True, "error": "Discord delivery failed"}
    if not sent:
        category, message, provider_status = _notification_provider_diagnostics(
            notifier,
            default_category="provider_rejected",
        )
        storage.finish_notification_attempt(
            attempt_id,
            sent=False,
            failed=True,
            failure_category=category,
            error_message=message,
            provider_status=provider_status,
        )
        polling_status.notification_failed()
        return {"attempted": True, "sent": False, "failed": True, "error": "Discord delivery failed"}
    provider_status = getattr(notifier, "last_provider_status", None)
    storage.finish_notification_attempt(
        attempt_id,
        sent=True,
        failed=False,
        provider_status=int(provider_status) if isinstance(provider_status, int) else None,
    )
    polling_status.notification_sent()
    return {"attempted": True, "sent": True, "failed": False, "error": ""}


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
    global _background_worker_started_at
    shared_logged = False
    cycle = 0
    worker_name = "background_poll"
    _background_worker_started_at = _background_worker_started_at or _utc_now_iso()
    storage.update_worker_heartbeat(
        worker_name=worker_name,
        process_id=_process_id(),
        hostname=_hostname(),
        started_at=_background_worker_started_at,
        status="running",
    )
    logger.info(
        "Background poll loop running pid=%s hostname=%s auth_required=%s background_poll_enabled=%s poll_seconds=%s",
        _process_id(),
        _hostname(),
        settings.auth_required,
        settings.background_poll_enabled,
        settings.background_poll_seconds,
    )
    while True:
        cycle += 1
        interval_details = _resolve_background_poll_sleep_seconds(mode="shared_background" if settings.auth_required else "local_background")
        sleep_seconds = int(interval_details["final_seconds"])
        lease_now = utc_now()
        is_leader, leadership_lease, owner_state = _acquire_background_leadership(lease_now)
        if not is_leader:
            polling_status.polling_enabled(False)
            standby_sleep = min(sleep_seconds, BACKGROUND_LEASE_RENEW_SECONDS)
            logger.info(
                "Background poll standby worker_id=%s lease_state=standby effective_interval_seconds=%s "
                "retry_seconds=%s owner_state=%s owner_reason=%s",
                WORKER_ID, sleep_seconds, standby_sleep,
                getattr(owner_state, "state", None),
                getattr(owner_state, "reason", None),
            )
            storage.update_worker_heartbeat(
                worker_name=worker_name,
                process_id=_process_id(),
                hostname=_hostname(),
                started_at=_background_worker_started_at,
                status="standby",
                last_error=str(getattr(owner_state, "reason", "") or ""),
                next_wake_at=(datetime.now(timezone.utc) + timedelta(seconds=standby_sleep)).isoformat(),
            )
            await asyncio.sleep(standby_sleep)
            continue
        logger.info(
            "Background poll leadership acquired worker_id=%s ttl_seconds=%s renew_seconds=%s takeover_reason=%s",
            WORKER_ID,
            BACKGROUND_LEASE_TTL_SECONDS,
            BACKGROUND_LEASE_RENEW_SECONDS,
            (leadership_lease or {}).get("takeover_reason") or "",
        )
        renewal_task = asyncio.create_task(_background_leadership_renewer())
        last_cycle_id: int | None = None
        current_cycle_mode = "shared_background" if settings.auth_required else "local_background"
        current_user_id: int | None = None
        current_settings_for_cycle: Any | None = None
        current_keywords: list[str] = []
        current_users_considered = 0
        current_users_scanned = 0
        try:
            polling_status.cycle_started()
            storage.update_worker_heartbeat(
                worker_name=worker_name,
                process_id=_process_id(),
                hostname=_hostname(),
                started_at=_background_worker_started_at,
                status="scanning",
                last_cycle_id=last_cycle_id,
            )
            logger.info("Background poll cycle started cycle=%s", cycle)
            if settings.auth_required:
                current_cycle_mode = "shared_background"
                resolved_users = _active_shared_scan_users(background_mode=False)
                current_users_considered = len(resolved_users)
                enabled_users = [resolved for resolved in resolved_users if resolved.background_poll_enabled]
                polling_status.polling_enabled(bool(enabled_users))
                interval_details = _resolve_background_poll_sleep_seconds(
                    mode="shared_background",
                    enabled_users=enabled_users,
                )
                sleep_seconds = int(interval_details["final_seconds"])
                if not shared_logged:
                    logger.info(
                        "Background polling is using shared multi-user polling active_users=%s background_enabled_users=%s",
                        len(resolved_users),
                        len(enabled_users),
                    )
                    shared_logged = True
                if not enabled_users:
                    logger.info("Background shared scan skipped because no active users have polling enabled")
                    last_cycle_id = _persist_skipped_scan_cycle(
                        mode="shared_background",
                        reason="polling_disabled",
                        users_considered=len(resolved_users),
                        users_scanned=0,
                    )
                    polling_status.cycle_succeeded(
                        _empty_scan_summary(keywords=[], reason="polling_disabled", mode="shared")
                    )
                else:
                    current_users_scanned = len(enabled_users)
                    current_keywords = []
                    for resolved in enabled_users:
                        current_keywords.extend(list(resolved.keywords or []))
                    summary = await _await_with_background_leadership(
                        asyncio.create_task(
                            scan_shared_once(
                                limit=settings.max_results_per_keyword,
                                notify=True,
                                triggered_by_user=None,
                                background_mode=True,
                                cycle_mode="shared_background",
                                offload_scan_work=True,
                            )
                        ),
                        renewal_task,
                    )
                    last_cycle_id = storage.list_scan_cycles(limit=1)[0]["id"] if storage.list_scan_cycles(limit=1) else None
                    if summary.get("reason") == "no_active_users":
                        logger.info("Background shared scan skipped because no active users were eligible")
                    elif summary.get("reason") == "no_enabled_keywords":
                        logger.info("Background shared scan skipped because no enabled keywords were available")
                    else:
                        logger.info("Background shared scan summary alerts_sent=%s summary=%s", summary.get("alerts_sent", 0), summary)
                    polling_status.cycle_succeeded(summary)
            else:
                current_cycle_mode = "local_background"
                resolved = _resolve_effective_user_settings(
                    allow_local_fallback=True,
                    ensure_defaults=True,
                )
                current_settings_for_cycle = resolved
                current_user = getattr(resolved, "user", None)
                current_user_id = int(current_user["id"]) if current_user else None
                current_keywords = list(resolved.keywords or [])
                current_users_considered = 1
                interval_details = _resolve_background_poll_sleep_seconds(
                    mode="local_background",
                    current_settings=resolved,
                )
                sleep_seconds = int(interval_details["final_seconds"])
                polling_status.polling_enabled(bool(resolved.background_poll_enabled))
                if not resolved.background_poll_enabled:
                    logger.info("Background scan skipped because polling is disabled for the resolved settings user")
                    last_cycle_id = _persist_skipped_scan_cycle(
                        mode="local_background",
                        reason="polling_disabled",
                        user_id=int(resolved.user["id"]) if resolved.user else None,
                        current_settings=resolved,
                        users_considered=1,
                        users_scanned=0,
                        keywords=resolved.keywords,
                    )
                    polling_status.cycle_succeeded(
                        _empty_scan_summary(keywords=resolved.keywords, reason="polling_disabled", mode="local")
                    )
                elif _background_poll_is_active(resolved):
                    current_users_scanned = 1
                    summary = await _await_with_background_leadership(
                        asyncio.create_task(
                            scan_once(
                                resolved.keywords,
                                settings.max_results_per_keyword,
                                notify=True,
                                resolved_settings=resolved,
                                cycle_mode="local_background",
                                offload_scan_work=True,
                            )
                        ),
                        renewal_task,
                    )
                    last_cycle_id = storage.list_scan_cycles(limit=1)[0]["id"] if storage.list_scan_cycles(limit=1) else None
                    logger.info("Background scan summary alerts_sent=%s summary=%s", summary.get("alerts_sent", 0), summary)
                    polling_status.cycle_succeeded(summary)
                else:
                    logger.info("Background scan skipped outside active window")
                    last_cycle_id = _persist_skipped_scan_cycle(
                        mode="local_background",
                        reason="outside_active_window",
                        user_id=int(resolved.user["id"]) if resolved.user else None,
                        current_settings=resolved,
                        users_considered=1,
                        users_scanned=0,
                        keywords=resolved.keywords,
                    )
                    polling_status.cycle_succeeded(
                        _empty_scan_summary(keywords=resolved.keywords, reason="outside_active_window", mode="local")
                    )
        except EbayRateLimitError as exc:
            rate_limit_status = _record_ebay_rate_limit(
                exc,
                keyword=current_keywords[0] if current_keywords else None,
            )
            latest_cycle = storage.latest_failed_or_skipped_scan_cycle()
            if (
                last_cycle_id is None
                and latest_cycle
                and latest_cycle.get("error_category") == "ebay_rate_limited"
            ):
                last_cycle_id = int(latest_cycle["id"])
            if last_cycle_id is None:
                last_cycle_id = _create_scan_cycle(
                    mode=current_cycle_mode,
                    user_id=current_user_id,
                    current_settings=current_settings_for_cycle,
                    users_considered=current_users_considered,
                    users_scanned=current_users_scanned,
                    keywords_searched=current_keywords,
                    sources_checked=["ebay"],
                    status="started",
                )
                _finish_scan_cycle_from_summary(
                    last_cycle_id,
                    status="failed",
                    summary={"keywords": current_keywords},
                    error_message=_scan_cycle_error_message(
                        exc,
                        keyword=current_keywords[0] if current_keywords else None,
                        source_status=rate_limit_status,
                    ),
                    users_considered=current_users_considered,
                    users_scanned=current_users_scanned,
                    keywords_searched=current_keywords,
                    sources_checked=["ebay"],
                    **_rate_limit_cycle_metadata(
                        exc,
                        keyword=current_keywords[0] if current_keywords else None,
                        status=rate_limit_status,
                    ),
            )
            polling_status.cycle_failed(exc)
            sleep_seconds = max(int(settings.ebay_rate_limit_backoff_seconds), int(exc.retry_after_seconds))
            interval_details = {
                "config_seconds": resolve_poll_interval(configured=settings.background_poll_seconds).configured_seconds,
                "user_seconds": interval_details.get("user_seconds"),
                "final_seconds": sleep_seconds,
                "interval_source": "ebay_rate_limit_backoff",
            }
            next_wake_at = (datetime.now(timezone.utc) + timedelta(seconds=max(1, int(sleep_seconds)))).isoformat()
            logger.warning(
                "Background poll hit eBay rate limit cycle=%s retry_after_seconds=%s",
                cycle,
                sleep_seconds,
            )
            storage.update_worker_heartbeat(
                worker_name=worker_name,
                process_id=_process_id(),
                hostname=_hostname(),
                started_at=_background_worker_started_at,
                status="error",
                last_cycle_id=last_cycle_id,
                last_error=f"{type(exc).__name__}: {exc}",
                next_wake_at=next_wake_at,
            )
        except BackgroundLeadershipLostError as exc:
            logger.warning("Background poll leadership lost worker_id=%s error=%s", WORKER_ID, exc)
            polling_status.polling_enabled(False)
            _mark_background_leadership_lost(datetime.now(timezone.utc))
            sleep_seconds = BACKGROUND_LEASE_RENEW_SECONDS
            interval_details = {
                "config_seconds": resolve_poll_interval(configured=settings.background_poll_seconds).configured_seconds,
                "user_seconds": interval_details.get("user_seconds"),
                "final_seconds": sleep_seconds,
                "interval_source": "leadership_recheck",
            }
            storage.update_worker_heartbeat(
                worker_name=worker_name,
                process_id=_process_id(),
                hostname=_hostname(),
                started_at=_background_worker_started_at,
                status="standby",
                last_cycle_id=last_cycle_id,
                last_error=str(exc),
            )
            if not renewal_task.done():
                renewal_task.cancel()
            await asyncio.gather(renewal_task, return_exceptions=True)
            await asyncio.sleep(sleep_seconds)
            continue
        except Exception as exc:
            polling_status.cycle_failed(exc)
            logger.exception("Background poll cycle failed cycle=%s", cycle)
            interval_details = _resolve_background_poll_sleep_seconds(mode=current_cycle_mode, current_settings=current_settings_for_cycle)
            base_seconds = int(interval_details["final_seconds"])
            failures = int(polling_status.snapshot().get("consecutive_failures") or 1)
            sleep_seconds = min(1200, max(base_seconds, 30 * (2 ** min(failures - 1, 5))))
            storage.update_worker_heartbeat(
                worker_name=worker_name,
                process_id=_process_id(),
                hostname=_hostname(),
                started_at=_background_worker_started_at,
                status="error",
                last_cycle_id=last_cycle_id,
                last_error=f"{type(exc).__name__}: {exc}",
            )
        sleep_seconds = max(1, int(sleep_seconds))
        interval_details = {
            **interval_details,
            "final_seconds": sleep_seconds,
        }
        polling_status.interval_resolved(
            config_seconds=int(interval_details["config_seconds"]),
            user_seconds=interval_details.get("user_seconds"),
            final_seconds=sleep_seconds,
            interval_source=str(interval_details["interval_source"]),
        )
        polling_status.sleep_scheduled(sleep_seconds)
        next_wake_at = (datetime.now(timezone.utc) + timedelta(seconds=sleep_seconds)).isoformat()
        if not _renew_background_leadership():
            logger.warning("Background poll leadership lost worker_id=%s lease_state=lost", WORKER_ID)
            if not renewal_task.done():
                renewal_task.cancel()
            await asyncio.gather(renewal_task, return_exceptions=True)
            await asyncio.sleep(BACKGROUND_LEASE_RENEW_SECONDS)
            continue
        storage.update_worker_heartbeat(
            worker_name=worker_name,
            process_id=_process_id(),
            hostname=_hostname(),
            started_at=_background_worker_started_at,
            status="sleeping",
            last_cycle_id=last_cycle_id,
            last_error=polling_status.snapshot().get("last_error") or "",
            next_wake_at=next_wake_at,
        )
        logger.info(
            "Background poll sleeping next_poll_seconds=%s config_poll_seconds=%s user_poll_seconds=%s "
            "interval_source=%s auth_required=%s background_poll_enabled=%s",
            sleep_seconds,
            interval_details.get("config_seconds"),
            interval_details.get("user_seconds"),
            interval_details.get("interval_source"),
            settings.auth_required,
            settings.background_poll_enabled,
        )
        try:
            await _sleep_with_background_leadership(sleep_seconds, renewal_task)
        except BackgroundLeadershipLostError as exc:
            logger.warning("Background poll leadership lost while sleeping worker_id=%s error=%s", WORKER_ID, exc)
            polling_status.polling_enabled(False)
            _mark_background_leadership_lost(datetime.now(timezone.utc))
            await asyncio.sleep(BACKGROUND_LEASE_RENEW_SECONDS)
        finally:
            if not renewal_task.done():
                renewal_task.cancel()
            await asyncio.gather(renewal_task, return_exceptions=True)


def should_notify_item(item: dict[str, Any], result: Any, current_settings: Any) -> bool:
    return evaluate_alert_decision(item, result, current_settings).tier == "GEM"


def _is_fresh_best_find(item: dict[str, Any], result: Any, current_settings: Any) -> bool:
    return should_notify_item(item, result, current_settings)


REVIEWABLE_PRICING_REASONS = (
    "Expected profit below threshold",
    "Only upside case works",
    "Low-confidence pricing needs stronger profit",
    "Too cheap without proof",
    "Profit depends on mint resale",
    "Missing part price",
    "Parts estimate not verified",
    "Low-confidence pricing",
    "Pricing confidence prevents Best Pick",
    "Reviewable despite parts/pricing gap",
)


def _has_reviewable_description_evidence(item: dict[str, Any]) -> bool:
    proof_flags = set(item.get("positive_flags") or [])
    classification_flags = set(item.get("listing_classification_flags") or [])
    strong_proof_count = len(
        proof_flags.intersection({"powers_on", "clean_imei", "face_id_works", "unlocked"})
    )
    has_raw_detail = bool(str(item.get("raw_description") or "").strip())
    has_description_evidence = bool(
        classification_flags.intersection(
            {
                "description_functionality_evidence",
                "normal_accessory_exclusions",
            }
        )
        or ("description_whole_phone_evidence" in classification_flags and has_raw_detail)
    )
    return (
        item.get("whole_phone_confidence_passed") is True
        and item.get("has_repair_issue") is True
        and bool(item.get("model") and item.get("model") != "unknown")
        and bool(item.get("storage_capacity"))
        and (
            strong_proof_count >= 2
            or (strong_proof_count >= 1 and has_description_evidence)
            or (
                has_description_evidence
                and float(item.get("whole_phone_score") or 0) >= 7
            )
        )
    )


def _has_hard_or_component_block(item: dict[str, Any]) -> bool:
    flags = [*(item.get("hard_reject_flags") or []), *(item.get("listing_classification_flags") or [])]
    return any(
        flag.endswith("_not_phone")
        or flag
        in {
            "old_model_ignored",
            "lot_not_single_phone",
            "no_power",
            "does_not_turn_on",
            "icloud_locked",
            "activation_locked",
            "mdm_locked",
            "water_damage",
            "liquid_damage",
            "baseband",
        }
        for flag in flags
    )


def _reviewable_despite_pricing_gap(item: dict[str, Any]) -> bool:
    if not _has_reviewable_description_evidence(item):
        return False
    if _has_hard_or_component_block(item):
        return False
    if _uses_model_resale_without_storage(item) and not _storage_fallback_priority_exception(item):
        return False
    if float(item.get("resale_value") or item.get("resale_mid") or 0) <= 0:
        return False
    reason = item.get("manual_review_reason") or ""
    return (
        item.get("estimated_parts_cost_available") is False
        or item.get("estimated_profit_available") is False
        or any(pricing_reason in reason for pricing_reason in REVIEWABLE_PRICING_REASONS)
    )


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
    if _has_hard_or_component_block(item):
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
        or _reviewable_despite_pricing_gap(item)
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
    total_cost = float(item.get("total_cost") or 0)
    resale_mid = float(item.get("resale_mid") or item.get("resale_value") or 0)
    if (
        item.get("has_repair_issue") is True
        and item.get("estimated_profit_available") is True
        and item.get("parts_pricing_status") in {"verified_screenshot", "verified_screenshot_and_page"}
        and float(item.get("profit_mid") or item.get("estimated_profit") or 0) >= 75
        and total_cost > 0
        and (total_cost <= 100 or (resale_mid > 0 and total_cost <= resale_mid * 0.25))
    ):
        return True
    proof_flags = set(item.get("positive_flags") or [])
    strong_proof_count = len(proof_flags.intersection({"powers_on", "clean_imei", "face_id_works", "unlocked"}))
    return (
        item.get("has_repair_issue") is True
        and item.get("estimated_profit_available") is True
        and strong_proof_count >= 2
    )


def _is_needs_data_item(item: dict[str, Any]) -> bool:
    if _is_unavailable(item):
        return False
    if item.get("user_status") in {"ignored", "rejected"} or item.get("status") == "rejected":
        return False
    if item.get("stale") is True:
        return False
    if _is_priority_review_candidate(item) or _reviewable_despite_pricing_gap(item):
        return False
    return (
        not item.get("model")
        or item.get("model") == "unknown"
        or (_uses_model_resale_without_storage(item) and not _storage_fallback_priority_exception(item))
        or (
            bool(item.get("storage_resale_warning"))
            and item.get("resale_source") != "storage_specific"
            and not _storage_fallback_priority_exception(item)
        )
        or float(item.get("resale_value") or item.get("resale_mid") or 0) <= 0
        or item.get("estimated_parts_cost_available") is False
        or item.get("has_repair_issue") is False
        or any(
            reason in (item.get("manual_review_reason") or "")
            for reason in (
                "Parts-only ambiguous",
                "Read description listing",
                "Expected profit below threshold",
                "Only upside case works",
                "Low-confidence pricing needs stronger profit",
                "Too cheap without proof",
                "Profit depends on mint resale",
                "Parts-only listing lacks power/iCloud/IMEI proof",
                "Model/spec mismatch",
                "Missing part price",
                "Model unknown",
                "No specific repair issue detected",
            )
        )
    )


def _is_missed_opportunity(item: dict[str, Any]) -> bool:
    decision = item.get("alert_decision") or {}
    if item.get("stale") or _is_unavailable(item) or _has_hard_or_component_block(item):
        return False
    expected = float(decision.get("expected_profit") or item.get("profit_mid") or 0)
    upside = float(decision.get("upside_profit") or item.get("profit_high") or 0)
    if expected <= 0 and upside <= 0:
        return False
    if decision.get("tier") in {"PROFITABLE", "REVIEW"}:
        return True
    soft_blocks = [
        reason
        for reason in decision.get("blocking_reasons") or []
        if reason
        in {
            "MODEL_UNKNOWN",
            "RESALE_UNAVAILABLE",
            "EXPECTED_PROFIT_BELOW_REVIEW_MINIMUM",
            "ROI_BELOW_REVIEW_MINIMUM",
            "WHOLE_PHONE_NOT_PROBABLE",
        }
    ]
    return len(set(soft_blocks)) <= 2 and bool(
        item.get("pricing_warning")
        or item.get("storage_resale_warning")
        or item.get("manual_review_needed")
        or not item.get("raw_description")
    )


def _alert_block_reasons(item: dict[str, Any], result: Any, current_settings: Any) -> list[str]:
    reasons: list[str] = []
    if item.get("status") != "candidate":
        reasons.append("status_not_candidate")
    if getattr(result, "score", 0) < current_settings.min_score_to_alert:
        reasons.append("score_below_threshold")
    if _is_unavailable(item):
        reasons.append("listing_unavailable")
    if _is_auction_only(item):
        reasons.append("auction_listing")
    if item.get("stale"):
        reasons.append("stale_item")
    if not item.get("fresh_for_alert", True):
        reasons.append("not_fresh_for_alert")
    if item.get("item_age_minutes") is not None and item["item_age_minutes"] > getattr(current_settings, "max_alert_item_age_minutes", 180):
        reasons.append("item_age_above_alert_window")
    if not getattr(result, "whole_phone_confidence_passed", False):
        reasons.append("whole_phone_confidence_failed")
    if not getattr(result, "has_repair_issue", False):
        reasons.append("repair_issue_missing")
    if not getattr(result, "estimated_profit_available", True):
        reasons.append("estimated_profit_unavailable")
    if hasattr(result, "alert_eligible") and not bool(result.alert_eligible):
        reasons.append("result_alert_ineligible")
    decision = evaluate_alert_decision(item, result, current_settings)
    return list(dict.fromkeys([*reasons, *decision.blocking_reasons]))


def _missing_data_reasons(item: dict[str, Any]) -> list[str]:
    reasons = []
    if not item.get("model") or item.get("model") == "unknown":
        reasons.append("model_unknown")
    if not item.get("storage_capacity"):
        reasons.append("storage_unknown")
    if item.get("carrier_status") in {None, "", "unknown"}:
        reasons.append("carrier_unknown")
    if float(item.get("resale_value") or item.get("resale_mid") or 0) <= 0:
        reasons.append("resale_value_missing")
    if item.get("estimated_parts_cost_available") is False:
        reasons.append("part_price_missing")
    if item.get("has_repair_issue") is False:
        reasons.append("repair_issue_missing")
    if not set(item.get("positive_flags") or []).intersection(
        {
            "cracked_screen",
            "screen_display_issue",
            "bad_oled",
            "bad_battery",
            "back_glass_cracked",
            "camera_lens_cracked",
            "charging_port_issue",
            "camera_fault",
            "face_id_issue",
            "digitizer_issue",
        }
    ):
        reasons.append("specific_repair_issue_missing")
    if item.get("storage_resale_warning"):
        reasons.append("storage_resale_warning")
    return list(dict.fromkeys(reasons))


def _pricing_confidence(item: dict[str, Any]) -> str:
    status = str(item.get("parts_pricing_status") or "missing")
    if status in {"verified_screenshot", "verified_screenshot_and_page"}:
        return "verified"
    if status in {"estimated", "verified_screenshot_low_confidence", "manual_part_update"}:
        return "rough"
    if status == "fallback":
        return "fallback"
    return "missing"


def _carrier_status(item: dict[str, Any], positive_flags: list[str]) -> str:
    if "unlocked" in positive_flags:
        return "unlocked"
    text = " ".join(str(item.get(key) or "") for key in ("title", "raw_description"))
    if re.search(r"\b(?:verizon|at&t|att|tmobile|t-mobile|sprint|boost|cricket|metro)\b", text, re.IGNORECASE):
        return "carrier_known"
    return "unknown"


def _normalized_bucket(item: dict[str, Any], result: Any, current_settings: Any) -> str:
    if item.get("status") == "rejected" or item.get("user_status") == "rejected":
        return "avoid"
    decision = evaluate_alert_decision(item, result, current_settings)
    if decision.tier == "GEM":
        return "gem"
    if decision.tier == "PROFITABLE":
        return "profitable"
    if decision.tier == "REVIEW":
        return "review"
    if _is_priority_review_candidate(item):
        return "good"
    if _is_needs_data_item(item):
        return "needs_data"
    if item.get("status") == "risky":
        return "watch"
    if item.get("status") in {"candidate", "alerted"}:
        return "good"
    return "bad"


def _build_decision_trace(
    item: dict[str, Any],
    result: Any,
    current_settings: Any,
    *,
    scan_cycle_id: int | None,
    alert_block_reasons: list[str] | None = None,
    current_app_status: str | None = None,
    current_app_bucket: str | None = None,
) -> dict[str, Any]:
    classification_flags = _jsonable_flags(item.get("listing_classification_flags"))
    hard_risks = _jsonable_flags(item.get("hard_reject_flags"))
    soft_risks = _jsonable_flags(item.get("risk_flags"))
    positive_flags = _jsonable_flags(item.get("positive_flags"))
    description_signals = extract_description_signals(item)
    repair_issues = [
        flag
        for flag in positive_flags
        if flag
        in {
            "cracked_screen",
            "screen_display_issue",
            "bad_oled",
            "bad_battery",
            "back_glass_cracked",
            "camera_lens_cracked",
            "charging_port_issue",
            "camera_fault",
            "face_id_issue",
            "digitizer_issue",
        }
    ]
    carrier_status = _carrier_status(item, positive_flags)
    missing_data = _missing_data_reasons({**item, "positive_flags": positive_flags, "carrier_status": carrier_status})
    blocking_rules = alert_block_reasons if alert_block_reasons is not None else _alert_block_reasons(item, result, current_settings)
    manual_reasons = _manual_reason_parts(item)
    normalized_bucket = _normalized_bucket(item, result, current_settings)
    alert_decision = evaluate_alert_decision(item, result, current_settings)
    current_status = current_app_status if current_app_status is not None else item.get("status")
    current_bucket = current_app_bucket if current_app_bucket is not None else current_status
    return {
        "listing_id": str(item.get("item_id") or ""),
        "title": str(item.get("title") or ""),
        "source": "ebay",
        "scan_run_id": scan_cycle_id,
        "scan_cycle_id": scan_cycle_id,
        "decision_identity": {key: item.get(key) or "" for key in ("scorer_hash", "rules_hash", "repair_hash", "resale_hash")},
        "detected": {
            "model": item.get("model") or "unknown",
            "item_type": item.get("item_type") or "ambiguous",
            "item_type_reason": item.get("item_type_reason") or "",
            "storage": item.get("storage_capacity"),
            "carrier_status": carrier_status,
            "condition": str(item.get("condition") or item.get("resale_condition_used") or ""),
            "repair_issues": repair_issues,
            "accessory_or_part_only": any(str(flag).endswith("_not_phone") or flag == "lot_not_single_phone" for flag in [*classification_flags, *hard_risks]),
            "hard_risks": hard_risks,
            "soft_risks": soft_risks,
            "description_signals": description_signals,
            "whole_phone_evidence": description_signals.get("whole_phone_evidence") or [],
            "functionality_signals": description_signals.get("functionality_signals") or [],
            "included_device_signals": description_signals.get("included_device_signals") or [],
            "normal_not_included_accessory_list": description_signals.get("normal_not_included_accessory_list") or [],
            "clean_activation_signals": description_signals.get("clean_activation_signals") or [],
            "repair_detail_signals": description_signals.get("repair_detail_signals") or [],
            "component_reject_signals": description_signals.get("component_reject_signals") or [],
            "detail_fetch_status": item.get("detail_fetch_status") or "not_requested",
            "detail_fetch_reason": item.get("detail_fetch_reason") or "",
            "detail_fetch_recovered_fields": item.get("detail_fetch_recovered_fields") or [],
        },
        "pricing": {
            "price": float(item.get("price") or 0),
            "shipping": float(item.get("shipping") or 0),
            "total_cost": float(item.get("total_cost") or 0),
            "resale_low": item.get("resale_low"),
            "resale_mid": item.get("resale_mid") or item.get("resale_value"),
            "resale_high": item.get("resale_high"),
            "parts_cost": item.get("estimated_parts_cost"),
            "risk_buffer": item.get("risk_buffer"),
            "estimated_selling_fees": round(float(getattr(result, "estimated_selling_fees", 0) or 0), 2),
            "estimated_outbound_shipping": round(float(getattr(result, "estimated_outbound_shipping", 0) or 0), 2),
            "exit_cost_marketplace": str(getattr(result, "exit_cost_marketplace", "") or ""),
            "profit_basis": (
                "net_after_estimated_exit_costs"
                if float(getattr(result, "estimated_selling_fees", 0) or 0) > 0
                or float(getattr(result, "estimated_outbound_shipping", 0) or 0) > 0
                else "gross_before_exit_costs"
            ),
            "profit_low": item.get("profit_low"),
            "profit_mid": item.get("profit_mid") or item.get("estimated_profit"),
            "profit_high": item.get("profit_high"),
            "pricing_confidence": _pricing_confidence(item),
        },
        "verdict": {
            "bucket": normalized_bucket,
            "normalized_bucket": normalized_bucket,
            "app_status": current_status,
            "current_app_status": current_status,
            "current_app_bucket": current_bucket,
            "score": float(item.get("score") or getattr(result, "score", 0) or 0),
            "confidence": float(item.get("whole_phone_score") or 0),
            "alert_eligible": bool(item.get("alert_eligible")),
            "tier_eligible": alert_decision.eligible,
            "alert_tier": alert_decision.tier,
            "manual_review_needed": bool(item.get("manual_review_needed")),
        },
        "alert_decision": alert_decision.as_dict(),
        "reasons": {
            "positive": positive_flags,
            "negative": [*soft_risks, *hard_risks, *classification_flags],
            "missing_data": missing_data,
            "blocking_rules": blocking_rules,
            "structured_blocking_reasons": alert_decision.blocking_reasons,
            "non_blocking_warnings": list(dict.fromkeys([
                *alert_decision.soft_warnings,
                *(reason for reason in manual_reasons if reason not in blocking_rules and reason not in missing_data),
            ])),
        },
    }


def _record_decision_trace(
    *,
    scan_cycle_id: int | None,
    counters: ScanCycleCounters,
    user_id: int,
    item: dict[str, Any],
    result: Any,
    current_settings: Any,
    alert_block_reasons: list[str] | None = None,
) -> None:
    if not scan_cycle_id:
        return
    trace = _build_decision_trace(
        item,
        result,
        current_settings,
        scan_cycle_id=scan_cycle_id,
        alert_block_reasons=alert_block_reasons,
    )
    storage.record_listing_decision_trace(
        user_id=user_id,
        item_id=str(item.get("item_id") or ""),
        scan_cycle_id=scan_cycle_id,
        trace=trace,
    )
    counters.record_trace(trace)


def _should_fetch_selective_detail(listing: dict[str, Any], result: Any, current_settings: Any) -> bool:
    return bool(_detail_refresh_reasons(listing, result, current_settings))


def _detail_refresh_reasons(listing: dict[str, Any], result: Any, current_settings: Any) -> list[str]:
    if not _is_selective_refresh_candidate({**listing, **result.as_item_fields()}):
        return []
    if getattr(result, "hard_reject_flags", []):
        return []
    reasons: list[str] = []
    title = (listing.get("title") or "").lower()
    triggers = ("read description", "no ic", "board", "not original owner")
    if any(trigger in title for trigger in triggers):
        reasons.append("title_requests_detail")
    if not str(listing.get("raw_description") or "").strip():
        reasons.append("description_missing")
    if not getattr(result, "storage_capacity", None):
        reasons.append("storage_missing")
    if not getattr(result, "model", None) or getattr(result, "model", "unknown") == "unknown":
        reasons.append("exact_model_missing")
    if not getattr(result, "has_repair_issue", False):
        reasons.append("specific_defect_missing")
    proof_flags = {"powers_on", "clean_imei", "face_id_works"}
    if not proof_flags.intersection(set(getattr(result, "positive_flags", []) or [])):
        reasons.append("functionality_or_activation_missing")
    if not re.search(r"\b(?:unlocked|verizon|at&t|att|t-?mobile|sprint|boost|cricket|metro)\b", title, re.IGNORECASE):
        reasons.append("carrier_missing")
    close_score = getattr(result, "score", 0) >= current_settings.min_score_to_alert - 10
    close_profit = getattr(result, "profit_high", 0) >= current_settings.min_profit_to_alert
    if (close_score or close_profit) and not reasons:
        reasons.append("near_alert_threshold")
    return list(dict.fromkeys(reasons))


def _detail_fetch_fields(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    recovered = []
    for key in ("raw_description", "condition", "availability_status", "buying_option_summary", "item_end_at"):
        if not before.get(key) and after.get(key):
            recovered.append(key)
    raw = after.get("raw_json") or {}
    details = raw.get("details") if isinstance(raw, dict) else {}
    if isinstance(details, dict) and details:
        for key in ("localizedAspects", "conditionDescription", "shortDescription"):
            if details.get(key):
                recovered.append(key)
    return list(dict.fromkeys(recovered))


async def _fetch_selective_detail(ebay: EbayClient, listing: dict[str, Any]) -> dict[str, Any]:
    if not hasattr(ebay, "fetch_item_detail"):
        return {}
    try:
        return await ebay.fetch_item_detail(str(listing.get("item_id")))
    except EbayRateLimitError:
        raise
    except Exception:
        logger.exception("Selective eBay detail fetch failed item_id=%s", listing.get("item_id"))
        return {}


def _detail_fields(detail: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in detail.items()
        if key in {
            "raw_description",
            "condition",
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


def _background_poll_is_active(current_settings: Any, *, now: datetime | None = None) -> bool:
    start = _parse_clock_time(current_settings.background_poll_active_start)
    end = _parse_clock_time(current_settings.background_poll_active_end)
    if not start or not end:
        return True
    tz = _background_poll_timezone(current_settings.background_poll_timezone)
    current = now or datetime.now(timezone.utc)
    now_time = current.astimezone(tz).time()
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
        updated = storage.set_user_item_status(user_id, item_id, user_status, ignored_reason=ignored_reason)
        _dashboard_stats_cache.pop(int(user_id), None)
        return updated
    except KeyError:
        raise HTTPException(status_code=404, detail="Item not found") from None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
