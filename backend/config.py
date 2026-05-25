from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DEFAULT_REPAIR_VALUES_PATH = DATA_DIR / "repair_values.json"
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


@dataclass(frozen=True)
class Settings:
    ebay_client_id: Optional[str] = None
    ebay_client_secret: Optional[str] = None
    ebay_marketplace_id: str = "EBAY_US"
    ebay_api_base: str = "https://api.ebay.com"
    ebay_oauth_url: str = "https://api.ebay.com/identity/v1/oauth2/token"
    ebay_fetch_descriptions: bool = False
    discord_webhook_url: Optional[str] = None
    sqlite_path: Path = DATA_DIR / "notifierr.sqlite3"
    repair_values_path: Path = DEFAULT_REPAIR_VALUES_PATH
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
    background_poll_enabled: bool = False
    background_poll_seconds: int = 300
    background_poll_active_start: Optional[str] = None
    background_poll_active_end: Optional[str] = None
    background_poll_timezone: str = "America/New_York"

    @property
    def ebay_configured(self) -> bool:
        return bool(self.ebay_client_id and self.ebay_client_secret)

    @property
    def discord_configured(self) -> bool:
        return bool(self.discord_webhook_url)

    @property
    def score_threshold(self) -> float:
        return self.min_score_to_alert

    @property
    def profit_threshold(self) -> float:
        return self.min_profit_to_alert

    def public_dict(self) -> dict:
        return {
            "ebay_configured": self.ebay_configured,
            "discord_configured": self.discord_configured,
            "ebay_marketplace_id": self.ebay_marketplace_id,
            "ebay_fetch_descriptions": self.ebay_fetch_descriptions,
            "sqlite_path": str(self.sqlite_path),
            "repair_values_path": str(self.repair_values_path),
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
            "background_poll_enabled": self.background_poll_enabled,
            "background_poll_seconds": self.background_poll_seconds,
            "background_poll_active_start": self.background_poll_active_start,
            "background_poll_active_end": self.background_poll_active_end,
            "background_poll_timezone": self.background_poll_timezone,
        }


def load_settings() -> Settings:
    _load_dotenv(BASE_DIR / ".env")
    _load_dotenv(Path.cwd() / ".env")

    sqlite_path = Path(os.getenv("SQLITE_PATH", str(DATA_DIR / "notifierr.sqlite3")))
    repair_values_path = Path(os.getenv("REPAIR_VALUES_PATH", str(DEFAULT_REPAIR_VALUES_PATH)))
    scoring_rules_path = Path(os.getenv("SCORING_RULES_PATH", str(DEFAULT_SCORING_RULES_PATH)))

    return Settings(
        ebay_client_id=os.getenv("EBAY_CLIENT_ID"),
        ebay_client_secret=os.getenv("EBAY_CLIENT_SECRET"),
        ebay_marketplace_id=os.getenv("EBAY_MARKETPLACE_ID", "EBAY_US"),
        ebay_api_base=os.getenv("EBAY_API_BASE", "https://api.ebay.com").rstrip("/"),
        ebay_oauth_url=os.getenv(
            "EBAY_OAUTH_URL",
            "https://api.ebay.com/identity/v1/oauth2/token",
        ),
        ebay_fetch_descriptions=_env_bool("EBAY_FETCH_DESCRIPTIONS", False),
        discord_webhook_url=os.getenv("DISCORD_WEBHOOK_URL"),
        sqlite_path=sqlite_path,
        repair_values_path=repair_values_path,
        scoring_rules_path=scoring_rules_path,
        search_keywords=_env_keywords(),
        cors_origins=_env_list(
            "CORS_ORIGINS",
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
        background_poll_enabled=_env_bool("BACKGROUND_POLL_ENABLED", False),
        background_poll_seconds=_env_int("BACKGROUND_POLL_SECONDS", 300),
        background_poll_active_start=os.getenv("BACKGROUND_POLL_ACTIVE_START") or None,
        background_poll_active_end=os.getenv("BACKGROUND_POLL_ACTIVE_END") or None,
        background_poll_timezone=os.getenv("BACKGROUND_POLL_TIMEZONE", "America/New_York"),
    )


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


def load_scoring_rules(path: Path = DEFAULT_SCORING_RULES_PATH) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)
