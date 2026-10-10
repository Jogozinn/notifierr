from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from cryptography.fernet import Fernet


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DEFAULT_REPAIR_VALUES_PATH = DATA_DIR / "repair_values.json"
DEFAULT_RESALE_RESEARCH_PATH = DATA_DIR / "resale_research.json"
DEFAULT_SCORING_RULES_PATH = DATA_DIR / "scoring_rules.json"

DEFAULT_KEYWORDS = [
    "iPhone cracked screen",
    "iPhone broken",
    "iPhone parts only",
    "iPhone back glass cracked",
    "iPhone bad battery",
    "iPhone read description",
]


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    return float(raw)


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    return int(raw)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_keywords() -> list[str]:
    raw = os.getenv("SEARCH_KEYWORDS")
    if not raw:
        return list(DEFAULT_KEYWORDS)
    return [item.strip() for item in raw.split(",") if item.strip()]


def _env_cors_origins(default: list[str]) -> list[str]:
    configured = (
        os.getenv("CORS_ALLOWED_ORIGINS")
        or os.getenv("CORS_ORIGINS")
        or ""
    ).strip()
    if configured:
        return [item.strip() for item in configured.split(",") if item.strip()]
    frontend_origin = (os.getenv("FRONTEND_ORIGIN") or "").strip()
    if frontend_origin:
        merged = [*default, frontend_origin]
        deduped: list[str] = []
        seen: set[str] = set()
        for origin in merged:
            if origin in seen:
                continue
            seen.add(origin)
            deduped.append(origin)
        return deduped
    return list(default)


@dataclass(frozen=True)
class Settings:
    runtime_env: str = "local"
    process_role: str = "combined"
    trusted_hosts: list[str] = field(default_factory=list)
    db_backend: str = "sqlite"
    database_url: Optional[str] = None
    ebay_client_id: Optional[str] = None
    ebay_client_secret: Optional[str] = None
    ebay_marketplace_id: str = "EBAY_US"
    ebay_api_base: str = "https://api.ebay.com"
    ebay_oauth_url: str = "https://api.ebay.com/identity/v1/oauth2/token"
    ebay_fetch_descriptions: bool = False
    research_detail_per_scan: int = 1
    research_detail_daily_limit: int = 24
    ebay_rate_limit_backoff_seconds: int = 900
    discord_webhook_url: Optional[str] = None
    vapid_public_key: Optional[str] = None
    vapid_private_key_b64: Optional[str] = None
    vapid_subject: Optional[str] = None
    sqlite_path: Path = DATA_DIR / "notifierr.sqlite3"
    repair_values_path: Path = DEFAULT_REPAIR_VALUES_PATH
    resale_research_path: Path = DEFAULT_RESALE_RESEARCH_PATH
    scoring_rules_path: Path = DEFAULT_SCORING_RULES_PATH
    search_keywords: list[str] = field(default_factory=lambda: list(DEFAULT_KEYWORDS))
    cors_origins: list[str] = field(default_factory=lambda: ["http://127.0.0.1:5173", "http://localhost:5173"])
    max_results_per_keyword: int = 25
    min_score_to_alert: float = 70.0
    min_profit_to_alert: float = 75.0
    risky_score_range: tuple[float, float] = (35.0, 69.99)
    max_alert_item_age_minutes: int = 180
    max_priority_review_item_age_hours: int = 24
    max_active_queue_item_age_hours: int = 24
    stale_archive_after_days: int = 7
    dashboard_hot_hours: int = 36
    research_compact_after_hours: int = 36
    research_export_token: Optional[str] = None
    background_poll_enabled: bool = False
    background_poll_seconds: int = 600
    background_poll_active_start: Optional[str] = None
    background_poll_active_end: Optional[str] = None
    background_poll_timezone: str = "America/New_York"
    auth_required: bool = False
    auth_secret_key: str = "notifierr-local-auth-secret-change-me-123456"
    access_token_expire_minutes: int = 720
    app_encryption_key: Optional[str] = None
    admin_email: Optional[str] = None
    admin_password: Optional[str] = None
    admin_display_name: Optional[str] = None

    @property
    def ebay_configured(self) -> bool:
        return bool(self.ebay_client_id and self.ebay_client_secret)

    @property
    def discord_configured(self) -> bool:
        return bool(self.discord_webhook_url)

    @property
    def push_configured(self) -> bool:
        return bool(self.vapid_public_key and self.vapid_private_key_b64 and self.vapid_subject)

    @property
    def score_threshold(self) -> float:
        return self.min_score_to_alert

    @property
    def profit_threshold(self) -> float:
        return self.min_profit_to_alert

    def public_dict(self) -> dict:
        return {
            "db_backend": self.db_backend,
            "database_configured": bool(self.database_url) if self.db_backend == "postgres" else True,
            "ebay_configured": self.ebay_configured,
            "discord_configured": self.discord_configured,
            "push_configured": self.push_configured,
            "ebay_marketplace_id": self.ebay_marketplace_id,
            "ebay_fetch_descriptions": self.ebay_fetch_descriptions,
            "research_detail_per_scan": self.research_detail_per_scan,
            "research_detail_daily_limit": self.research_detail_daily_limit,
            "ebay_rate_limit_backoff_seconds": self.ebay_rate_limit_backoff_seconds,
            "sqlite_path": str(self.sqlite_path),
            "repair_values_path": str(self.repair_values_path),
            "resale_research_path": str(self.resale_research_path),
            "scoring_rules_path": str(self.scoring_rules_path),
            "search_keywords": self.search_keywords,
            "cors_origins": self.cors_origins,
            "max_results_per_keyword": self.max_results_per_keyword,
            "min_score_to_alert": self.min_score_to_alert,
            "min_profit_to_alert": self.min_profit_to_alert,
            "risky_score_range": list(self.risky_score_range),
            "max_alert_item_age_minutes": self.max_alert_item_age_minutes,
            "max_priority_review_item_age_hours": self.max_priority_review_item_age_hours,
            "max_active_queue_item_age_hours": self.max_active_queue_item_age_hours,
            "stale_archive_after_days": self.stale_archive_after_days,
            "dashboard_hot_hours": self.dashboard_hot_hours,
            "research_compact_after_hours": self.research_compact_after_hours,
            "research_export_configured": bool(self.research_export_token),
            "background_poll_enabled": self.background_poll_enabled,
            "background_poll_seconds": self.background_poll_seconds,
            "background_poll_active_start": self.background_poll_active_start,
            "background_poll_active_end": self.background_poll_active_end,
            "background_poll_timezone": self.background_poll_timezone,
            "auth_required": self.auth_required,
            "encryption_configured": bool(self.app_encryption_key),
        }


def load_settings() -> Settings:
    runtime_env = (os.getenv("NOTIFIERR_ENV") or "local").strip().lower()
    if runtime_env not in {"local", "production"}:
        raise ValueError("NOTIFIERR_ENV must be local or production")
    if runtime_env == "local":
        _load_dotenv(BASE_DIR / ".env")
        _load_dotenv(Path.cwd() / ".env")

    sqlite_path = Path(os.getenv("SQLITE_PATH", str(DATA_DIR / "notifierr.sqlite3")))
    repair_values_path = Path(os.getenv("REPAIR_VALUES_PATH", str(DEFAULT_REPAIR_VALUES_PATH)))
    resale_research_path = Path(os.getenv("RESALE_RESEARCH_PATH", str(DEFAULT_RESALE_RESEARCH_PATH)))
    scoring_rules_path = Path(os.getenv("SCORING_RULES_PATH", str(DEFAULT_SCORING_RULES_PATH)))

    result = Settings(
        runtime_env=runtime_env,
        process_role=(os.getenv("NOTIFIERR_ROLE") or ("api" if runtime_env == "production" else "combined")).strip().lower(),
        trusted_hosts=_env_list("TRUSTED_HOSTS", []),
        db_backend=(os.getenv("DB_BACKEND", "sqlite") or "sqlite").strip().lower(),
        database_url=os.getenv("DATABASE_URL") or None,
        ebay_client_id=os.getenv("EBAY_CLIENT_ID"),
        ebay_client_secret=os.getenv("EBAY_CLIENT_SECRET"),
        ebay_marketplace_id=os.getenv("EBAY_MARKETPLACE_ID", "EBAY_US"),
        ebay_api_base=os.getenv("EBAY_API_BASE", "https://api.ebay.com").rstrip("/"),
        ebay_oauth_url=os.getenv(
            "EBAY_OAUTH_URL",
            "https://api.ebay.com/identity/v1/oauth2/token",
        ),
        ebay_fetch_descriptions=_env_bool("EBAY_FETCH_DESCRIPTIONS", False),
        research_detail_per_scan=max(0, min(2, _env_int("RESEARCH_DETAIL_PER_SCAN", 1))),
        research_detail_daily_limit=max(0, min(48, _env_int("RESEARCH_DETAIL_DAILY_LIMIT", 24))),
        ebay_rate_limit_backoff_seconds=_env_int("EBAY_RATE_LIMIT_BACKOFF_SECONDS", 900),
        discord_webhook_url=os.getenv("DISCORD_WEBHOOK_URL"),
        vapid_public_key=os.getenv("VAPID_PUBLIC_KEY") or None,
        vapid_private_key_b64=os.getenv("VAPID_PRIVATE_KEY_B64") or None,
        vapid_subject=os.getenv("VAPID_SUBJECT") or None,
        sqlite_path=sqlite_path,
        repair_values_path=repair_values_path,
        resale_research_path=resale_research_path,
        scoring_rules_path=scoring_rules_path,
        search_keywords=_env_keywords(),
        cors_origins=_env_cors_origins(
            ["http://127.0.0.1:5173", "http://localhost:5173"],
        ),
        max_results_per_keyword=_env_int("MAX_RESULTS_PER_KEYWORD", 25),
        min_score_to_alert=_env_float("MIN_SCORE_TO_ALERT", _env_float("SCORE_THRESHOLD", 70.0)),
        min_profit_to_alert=_env_float("MIN_PROFIT_TO_ALERT", _env_float("PROFIT_THRESHOLD", 75.0)),
        risky_score_range=_env_range("RISKY_SCORE_RANGE", (35.0, 69.99)),
        max_alert_item_age_minutes=_env_int("MAX_ALERT_ITEM_AGE_MINUTES", 180),
        max_priority_review_item_age_hours=_env_int("MAX_PRIORITY_REVIEW_ITEM_AGE_HOURS", 24),
        max_active_queue_item_age_hours=_env_int("MAX_ACTIVE_QUEUE_ITEM_AGE_HOURS", 24),
        stale_archive_after_days=_env_int("STALE_ARCHIVE_AFTER_DAYS", 7),
        dashboard_hot_hours=_env_int("DASHBOARD_HOT_HOURS", 36),
        research_compact_after_hours=_env_int("RESEARCH_COMPACT_AFTER_HOURS", 36),
        research_export_token=os.getenv("RESEARCH_EXPORT_TOKEN") or None,
        background_poll_enabled=_env_bool("BACKGROUND_POLL_ENABLED", False),
        background_poll_seconds=_env_int("BACKGROUND_POLL_SECONDS", 600),
        background_poll_active_start=os.getenv("BACKGROUND_POLL_ACTIVE_START") or None,
        background_poll_active_end=os.getenv("BACKGROUND_POLL_ACTIVE_END") or None,
        background_poll_timezone=os.getenv("BACKGROUND_POLL_TIMEZONE", "America/New_York"),
        auth_required=_env_bool("AUTH_REQUIRED", False),
        auth_secret_key=os.getenv("AUTH_SECRET_KEY", "notifierr-local-auth-secret-change-me-123456"),
        access_token_expire_minutes=_env_int("ACCESS_TOKEN_EXPIRE_MINUTES", 720),
        app_encryption_key=os.getenv("APP_ENCRYPTION_KEY") or None,
        admin_email=os.getenv("ADMIN_EMAIL") or None,
        admin_password=os.getenv("ADMIN_PASSWORD") or None,
        admin_display_name=os.getenv("ADMIN_DISPLAY_NAME") or None,
    )
    validate_settings(result)
    return result


def validate_settings(settings: Settings) -> None:
    if settings.process_role not in {"api", "scanner", "retention", "migrate", "combined"}:
        raise ValueError("NOTIFIERR_ROLE must be api, scanner, retention, migrate, or combined")
    if settings.runtime_env != "production":
        return
    if settings.process_role == "combined":
        raise ValueError("Production requires a separate NOTIFIERR_ROLE")
    if settings.db_backend != "postgres" or not settings.database_url or not settings.database_url.startswith(("postgres://", "postgresql://", "postgresql+psycopg://")):
        raise ValueError("Production requires DB_BACKEND=postgres and a PostgreSQL DATABASE_URL")
    if not settings.auth_required:
        raise ValueError("Production requires AUTH_REQUIRED=true")
    if len(settings.auth_secret_key) < 32 or settings.auth_secret_key == Settings().auth_secret_key:
        raise ValueError("Production requires a non-default AUTH_SECRET_KEY of at least 32 characters")
    if not settings.app_encryption_key:
        raise ValueError("Production requires APP_ENCRYPTION_KEY")
    try:
        Fernet(settings.app_encryption_key.encode("utf-8"))
    except (ValueError, TypeError) as exc:
        raise ValueError("APP_ENCRYPTION_KEY must be a valid Fernet key") from exc
    if settings.process_role == "api":
        if not settings.admin_email or not settings.admin_password or len(settings.admin_password) < 12:
            raise ValueError("Production API requires ADMIN_EMAIL and a strong ADMIN_PASSWORD for initial bootstrap")
        if not settings.cors_origins or any(not origin.startswith("https://") or "*" in origin for origin in settings.cors_origins):
            raise ValueError("Production API requires explicit HTTPS CORS_ALLOWED_ORIGINS")
        if not settings.trusted_hosts or "*" in settings.trusted_hosts:
            raise ValueError("Production API requires explicit TRUSTED_HOSTS")


def _env_list(name: str, default: list[str]) -> list[str]:
    raw = os.getenv(name)
    if not raw:
        return list(default)
    return [item.strip() for item in raw.split(",") if item.strip()]


def _env_range(name: str, default: tuple[float, float]) -> tuple[float, float]:
    raw = os.getenv(name)
    if not raw:
        return default
    parts = [part.strip() for part in raw.split(",") if part.strip()]
    if len(parts) != 2:
        raise ValueError(f"{name} must be two comma-separated numbers")
    low, high = float(parts[0]), float(parts[1])
    if low > high:
        raise ValueError(f"{name} minimum cannot be greater than maximum")
    return (low, high)


def load_repair_values(path: Path = DEFAULT_REPAIR_VALUES_PATH) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def load_resale_research(path: Path = DEFAULT_RESALE_RESEARCH_PATH) -> dict:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def load_scoring_rules(path: Path = DEFAULT_SCORING_RULES_PATH) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)
