from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any


DEFAULT_POLL_SECONDS = 600
MIN_POLL_SECONDS = 300
MAX_POLL_SECONDS = 1200
STALE_GRACE_SECONDS = 60
SCAN_COMPLETION_GRACE_SECONDS = 120


@dataclass(frozen=True)
class PollInterval:
    configured_seconds: int
    effective_seconds: int
    source: str


def resolve_poll_interval(*, configured: Any, persisted: list[Any] | None = None) -> PollInterval:
    """Persisted enabled-user cadence wins; otherwise environment/config wins; then default."""
    configured_seconds = _bounded(configured, DEFAULT_POLL_SECONDS)
    values = [_bounded(value, configured_seconds) for value in (persisted or [])]
    effective = min(values) if values else configured_seconds
    return PollInterval(configured_seconds, effective, "persisted_user_min" if values else "environment_or_default")


def stale_after_seconds(interval_seconds: int) -> int:
    return (2 * _bounded(interval_seconds, DEFAULT_POLL_SECONDS)) + STALE_GRACE_SECONDS


def scan_success_overdue_after_seconds(interval_seconds: int, successful_cycle: dict[str, Any] | None) -> int:
    """Completion-to-next-start interval plus the prior scan duration and a small completion grace."""
    duration_seconds = 0
    if successful_cycle:
        started = _parse_utc(successful_cycle.get("started_at"))
        finished = _parse_utc(successful_cycle.get("finished_at"))
        if started and finished:
            duration_seconds = max(0, int((finished - started).total_seconds()))
    return _bounded(interval_seconds, DEFAULT_POLL_SECONDS) + duration_seconds + SCAN_COMPLETION_GRACE_SECONDS


def consecutive_cycle_outcomes(cycles: list[dict[str, Any]]) -> tuple[int, int, str | None]:
    failures = 0
    skips = 0
    last_skip_reason: str | None = None
    for cycle in cycles:
        status = str(cycle.get("status") or "")
        if status == "completed":
            break
        if status in {"failed", "abandoned"}:
            failures += 1
        elif status == "skipped":
            skips += 1
            if last_skip_reason is None:
                last_skip_reason = str(cycle.get("skip_reason") or "") or None
    return failures, skips, last_skip_reason


def _parse_utc(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def lease_expiry(now: datetime, interval_seconds: int) -> datetime:
    return now + timedelta(seconds=stale_after_seconds(interval_seconds))


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _bounded(value: Any, fallback: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = int(fallback)
    return min(MAX_POLL_SECONDS, max(MIN_POLL_SECONDS, parsed))
