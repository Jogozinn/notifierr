from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import sqlalchemy as sa
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import IntegrityError as SAIntegrityError

from .config import DEFAULT_KEYWORDS
from .db_models import metadata, scan_cycles, shared_scan_runs, shared_scan_searches, source_statuses, user_keywords, users
from .scorer import now_iso


DEFAULT_MAX_ALERT_ITEM_AGE_MINUTES = 180
DEFAULT_MAX_PRIORITY_REVIEW_ITEM_AGE_HOURS = 24
DEFAULT_MAX_ACTIVE_QUEUE_ITEM_AGE_HOURS = 24
DEFAULT_USER_MIN_SCORE_TO_ALERT = 70.0
DEFAULT_USER_MIN_PROFIT_TO_ALERT = 75.0
DEFAULT_USER_RISKY_SCORE_MIN = 35.0
DEFAULT_USER_RISKY_SCORE_MAX = 69.99
DEFAULT_USER_BACKGROUND_POLL_SECONDS = 300
DEFAULT_USER_TIMEZONE = "America/New_York"
LOCAL_FALLBACK_USER_EMAIL = "local@notifierr.local"


SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    item_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    price REAL NOT NULL DEFAULT 0,
    shipping REAL NOT NULL DEFAULT 0,
    total_cost REAL NOT NULL DEFAULT 0,
    condition TEXT,
    item_url TEXT,
    image_url TEXT,
    seller_username TEXT,
    seller_feedback_percentage REAL,
    seller_feedback_score INTEGER,
    raw_description TEXT,
    item_origin_at TEXT,
    found_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    model TEXT,
    score REAL NOT NULL DEFAULT 0,
    status TEXT NOT NULL,
    resale_value REAL NOT NULL DEFAULT 0,
    resale_low REAL NOT NULL DEFAULT 0,
    resale_mid REAL NOT NULL DEFAULT 0,
    resale_high REAL NOT NULL DEFAULT 0,
    profit_low REAL NOT NULL DEFAULT 0,
    profit_mid REAL NOT NULL DEFAULT 0,
    profit_high REAL NOT NULL DEFAULT 0,
    resale_confidence TEXT NOT NULL DEFAULT '',
    resale_sample_size INTEGER NOT NULL DEFAULT 0,
    resale_note TEXT NOT NULL DEFAULT '',
    resale_source TEXT NOT NULL DEFAULT 'missing',
    resale_market_source TEXT NOT NULL DEFAULT 'missing',
    resale_condition_used TEXT NOT NULL DEFAULT '',
    resale_storage_used TEXT,
    storage_resale_warning TEXT NOT NULL DEFAULT '',
    mint_resale_low REAL NOT NULL DEFAULT 0,
    mint_resale_mid REAL NOT NULL DEFAULT 0,
    mint_resale_high REAL NOT NULL DEFAULT 0,
    mint_profit_low REAL NOT NULL DEFAULT 0,
    mint_profit_mid REAL NOT NULL DEFAULT 0,
    mint_profit_high REAL NOT NULL DEFAULT 0,
    storage_capacity TEXT,
    storage_confidence TEXT NOT NULL DEFAULT '',
    storage_source TEXT NOT NULL DEFAULT '',
    estimated_parts_cost REAL NOT NULL DEFAULT 0,
    estimated_parts_cost_available INTEGER NOT NULL DEFAULT 1,
    risk_buffer REAL NOT NULL DEFAULT 0,
    estimated_profit REAL NOT NULL DEFAULT 0,
    estimated_profit_available INTEGER NOT NULL DEFAULT 1,
    parts_pricing_status TEXT NOT NULL DEFAULT 'fallback',
    parts_pricing_note TEXT NOT NULL DEFAULT '',
    parts_pricing_label TEXT NOT NULL DEFAULT 'Parts estimate not verified',
    pricing_warning TEXT NOT NULL DEFAULT 'Parts estimate not verified',
    manual_review_allowed INTEGER NOT NULL DEFAULT 0,
    whole_phone_confidence_passed INTEGER NOT NULL DEFAULT 0,
    whole_phone_score REAL NOT NULL DEFAULT 0,
    has_repair_issue INTEGER NOT NULL DEFAULT 0,
    manual_review_needed INTEGER NOT NULL DEFAULT 0,
    manual_review_reason TEXT NOT NULL DEFAULT '',
    alert_eligible INTEGER NOT NULL DEFAULT 0,
    listing_classification_flags TEXT NOT NULL DEFAULT '[]',
    user_status TEXT NOT NULL DEFAULT 'new',
    user_note TEXT NOT NULL DEFAULT '',
    reviewed_at TEXT,
    ignored_at TEXT,
    watched_at TEXT,
    promoted_at TEXT,
    rejected_by_user_at TEXT,
    user_reject_reason TEXT NOT NULL DEFAULT '',
    ignored_reason TEXT NOT NULL DEFAULT '',
    ignored_seller TEXT NOT NULL DEFAULT '',
    updated_by_user_at TEXT,
    hard_reject_flags TEXT NOT NULL DEFAULT '[]',
    positive_flags TEXT NOT NULL DEFAULT '[]',
    risk_flags TEXT NOT NULL DEFAULT '[]',
    alerted_at TEXT,
    availability_status TEXT NOT NULL DEFAULT 'unknown',
    buying_option_summary TEXT NOT NULL DEFAULT 'unknown',
    item_end_at TEXT,
    last_availability_checked_at TEXT,
    availability_note TEXT NOT NULL DEFAULT '',
    raw_json TEXT
);

CREATE TABLE IF NOT EXISTS ignored_sellers (
    seller_username TEXT PRIMARY KEY,
    reason TEXT NOT NULL DEFAULT '',
    source_item_id TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ignored_keywords (
    keyword TEXT PRIMARY KEY,
    reason TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS user_ignored_sellers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    seller_username TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(user_id, seller_username),
    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS user_ignored_keywords (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    keyword TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(user_id, keyword),
    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL,
    account_status TEXT NOT NULL,
    display_name TEXT NOT NULL DEFAULT '',
    plan_name TEXT NOT NULL DEFAULT '',
    monthly_price REAL NOT NULL DEFAULT 0,
    billing_status TEXT NOT NULL DEFAULT '',
    paid_until TEXT,
    billing_note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_login_at TEXT
);

CREATE TABLE IF NOT EXISTS user_invites (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL UNIQUE,
    email TEXT,
    role TEXT NOT NULL DEFAULT 'user',
    created_by_user_id INTEGER,
    used_by_user_id INTEGER,
    used_at TEXT,
    expires_at TEXT,
    created_at TEXT NOT NULL,
    revoked_at TEXT,
    FOREIGN KEY(created_by_user_id) REFERENCES users(id) ON DELETE SET NULL,
    FOREIGN KEY(used_by_user_id) REFERENCES users(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS user_settings (
    user_id INTEGER PRIMARY KEY,
    min_score_to_alert REAL NOT NULL,
    min_profit_to_alert REAL NOT NULL,
    risky_score_min REAL NOT NULL,
    risky_score_max REAL NOT NULL,
    max_alert_item_age_minutes INTEGER NOT NULL,
    max_priority_review_item_age_hours INTEGER NOT NULL,
    max_active_queue_item_age_hours INTEGER NOT NULL,
    default_resale_condition TEXT NOT NULL DEFAULT 'good',
    allow_mint_for_alerts INTEGER NOT NULL DEFAULT 0,
    target_min_model_generation INTEGER NOT NULL DEFAULT 0,
    background_poll_enabled INTEGER NOT NULL DEFAULT 0,
    background_poll_seconds INTEGER NOT NULL DEFAULT 300,
    active_start TEXT,
    active_end TEXT,
    timezone TEXT NOT NULL DEFAULT 'America/New_York',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS user_notification_settings (
    user_id INTEGER PRIMARY KEY,
    discord_webhook TEXT NOT NULL DEFAULT '',
    discord_enabled INTEGER NOT NULL DEFAULT 0,
    alerts_enabled INTEGER NOT NULL DEFAULT 1,
    notify_best_finds INTEGER NOT NULL DEFAULT 1,
    notify_priority_review INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS user_keywords (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    keyword TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    is_baseline INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(user_id, keyword),
    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS user_repair_value_overrides (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    model TEXT NOT NULL,
    part TEXT NOT NULL,
    cost REAL NOT NULL,
    source TEXT NOT NULL DEFAULT '',
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(user_id, model, part),
    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS user_resale_research_overrides (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    model TEXT NOT NULL,
    storage_capacity TEXT NOT NULL,
    condition TEXT NOT NULL,
    low REAL NOT NULL,
    mid REAL NOT NULL,
    high REAL NOT NULL,
    confidence TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT '',
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(user_id, model, storage_capacity, condition),
    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS user_item_corrections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    marketplace_item_id INTEGER NOT NULL,
    corrected_model TEXT,
    corrected_storage_capacity TEXT,
    corrected_issue_type TEXT,
    corrected_part_cost REAL,
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(user_id, marketplace_item_id),
    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY(marketplace_item_id) REFERENCES marketplace_items(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS marketplace_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    marketplace TEXT NOT NULL DEFAULT 'ebay',
    marketplace_item_id TEXT NOT NULL,
    title TEXT NOT NULL,
    price REAL NOT NULL DEFAULT 0,
    shipping REAL NOT NULL DEFAULT 0,
    total_cost REAL NOT NULL DEFAULT 0,
    condition TEXT,
    item_url TEXT,
    image_url TEXT,
    seller_username TEXT,
    seller_feedback_percentage REAL,
    seller_feedback_score INTEGER,
    raw_description TEXT,
    item_origin_at TEXT,
    item_creation_at TEXT,
    found_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    raw_json TEXT,
    availability_status TEXT NOT NULL DEFAULT 'unknown',
    buying_option_summary TEXT NOT NULL DEFAULT 'unknown',
    item_end_at TEXT,
    last_availability_checked_at TEXT,
    availability_note TEXT NOT NULL DEFAULT '',
    UNIQUE(marketplace, marketplace_item_id)
);

CREATE TABLE IF NOT EXISTS user_item_states (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    marketplace_item_id INTEGER NOT NULL,
    score REAL NOT NULL DEFAULT 0,
    status TEXT NOT NULL,
    model TEXT,
    resale_value REAL NOT NULL DEFAULT 0,
    resale_low REAL NOT NULL DEFAULT 0,
    resale_mid REAL NOT NULL DEFAULT 0,
    resale_high REAL NOT NULL DEFAULT 0,
    profit_low REAL NOT NULL DEFAULT 0,
    profit_mid REAL NOT NULL DEFAULT 0,
    profit_high REAL NOT NULL DEFAULT 0,
    resale_confidence TEXT NOT NULL DEFAULT '',
    resale_sample_size INTEGER NOT NULL DEFAULT 0,
    resale_note TEXT NOT NULL DEFAULT '',
    resale_source TEXT NOT NULL DEFAULT 'missing',
    resale_market_source TEXT NOT NULL DEFAULT 'missing',
    resale_condition_used TEXT NOT NULL DEFAULT '',
    resale_storage_used TEXT,
    storage_resale_warning TEXT NOT NULL DEFAULT '',
    mint_resale_low REAL NOT NULL DEFAULT 0,
    mint_resale_mid REAL NOT NULL DEFAULT 0,
    mint_resale_high REAL NOT NULL DEFAULT 0,
    mint_profit_low REAL NOT NULL DEFAULT 0,
    mint_profit_mid REAL NOT NULL DEFAULT 0,
    mint_profit_high REAL NOT NULL DEFAULT 0,
    storage_capacity TEXT,
    storage_confidence TEXT NOT NULL DEFAULT '',
    storage_source TEXT NOT NULL DEFAULT '',
    estimated_parts_cost REAL NOT NULL DEFAULT 0,
    estimated_parts_cost_available INTEGER NOT NULL DEFAULT 1,
    risk_buffer REAL NOT NULL DEFAULT 0,
    estimated_profit REAL NOT NULL DEFAULT 0,
    estimated_profit_available INTEGER NOT NULL DEFAULT 1,
    parts_pricing_status TEXT NOT NULL DEFAULT 'fallback',
    parts_pricing_note TEXT NOT NULL DEFAULT '',
    parts_pricing_label TEXT NOT NULL DEFAULT 'Parts estimate not verified',
    pricing_warning TEXT NOT NULL DEFAULT 'Parts estimate not verified',
    manual_review_allowed INTEGER NOT NULL DEFAULT 0,
    whole_phone_confidence_passed INTEGER NOT NULL DEFAULT 0,
    whole_phone_score REAL NOT NULL DEFAULT 0,
    has_repair_issue INTEGER NOT NULL DEFAULT 0,
    manual_review_needed INTEGER NOT NULL DEFAULT 0,
    manual_review_reason TEXT NOT NULL DEFAULT '',
    alert_eligible INTEGER NOT NULL DEFAULT 0,
    listing_classification_flags TEXT NOT NULL DEFAULT '[]',
    user_status TEXT NOT NULL DEFAULT 'new',
    user_note TEXT NOT NULL DEFAULT '',
    reviewed_at TEXT,
    ignored_at TEXT,
    watched_at TEXT,
    promoted_at TEXT,
    rejected_by_user_at TEXT,
    user_reject_reason TEXT NOT NULL DEFAULT '',
    ignored_reason TEXT NOT NULL DEFAULT '',
    ignored_seller TEXT NOT NULL DEFAULT '',
    updated_by_user_at TEXT,
    hard_reject_flags TEXT NOT NULL DEFAULT '[]',
    positive_flags TEXT NOT NULL DEFAULT '[]',
    risk_flags TEXT NOT NULL DEFAULT '[]',
    alerted_at TEXT,
    promoted_notification_sent_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(user_id, marketplace_item_id),
    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY(marketplace_item_id) REFERENCES marketplace_items(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS shared_scan_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    mode TEXT NOT NULL DEFAULT 'shared',
    status TEXT NOT NULL DEFAULT 'running',
    triggered_by_user_id INTEGER,
    active_users INTEGER NOT NULL DEFAULT 0,
    unique_searches INTEGER NOT NULL DEFAULT 0,
    api_calls_made INTEGER NOT NULL DEFAULT 0,
    total_items_returned INTEGER NOT NULL DEFAULT 0,
    total_users_evaluated INTEGER NOT NULL DEFAULT 0,
    total_items_scored INTEGER NOT NULL DEFAULT 0,
    total_alerts_sent INTEGER NOT NULL DEFAULT 0,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(triggered_by_user_id) REFERENCES users(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS shared_scan_searches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_run_id INTEGER NOT NULL,
    search_signature TEXT NOT NULL,
    marketplace TEXT NOT NULL DEFAULT 'ebay',
    keyword TEXT NOT NULL,
    limit_value INTEGER NOT NULL,
    sort_order TEXT NOT NULL DEFAULT 'newlyListed',
    marketplace_id TEXT,
    subscribed_user_count INTEGER NOT NULL DEFAULT 0,
    items_returned INTEGER NOT NULL DEFAULT 0,
    api_calls_made INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    FOREIGN KEY(scan_run_id) REFERENCES shared_scan_runs(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS shared_scan_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_search_id INTEGER NOT NULL,
    marketplace TEXT NOT NULL DEFAULT 'ebay',
    marketplace_item_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(scan_search_id, marketplace, marketplace_item_id),
    FOREIGN KEY(scan_search_id) REFERENCES shared_scan_searches(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS scan_cycles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    mode TEXT NOT NULL,
    user_id INTEGER,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL DEFAULT 'started',
    skip_reason TEXT NOT NULL DEFAULT '',
    error_message TEXT NOT NULL DEFAULT '',
    source TEXT,
    error_category TEXT,
    http_status INTEGER,
    cooldown_until TEXT,
    retry_after_seconds INTEGER,
    process_id INTEGER NOT NULL DEFAULT 0,
    hostname TEXT NOT NULL DEFAULT '',
    auth_required INTEGER NOT NULL DEFAULT 0,
    background_poll_enabled INTEGER NOT NULL DEFAULT 0,
    background_poll_seconds INTEGER NOT NULL DEFAULT 0,
    active_window_start TEXT,
    active_window_end TEXT,
    active_window_timezone TEXT NOT NULL DEFAULT '',
    users_considered INTEGER NOT NULL DEFAULT 0,
    users_scanned INTEGER NOT NULL DEFAULT 0,
    keywords_searched TEXT NOT NULL DEFAULT '[]',
    sources_checked TEXT NOT NULL DEFAULT '[]',
    items_found INTEGER NOT NULL DEFAULT 0,
    new_items_found INTEGER NOT NULL DEFAULT 0,
    duplicate_items INTEGER NOT NULL DEFAULT 0,
    items_scored INTEGER NOT NULL DEFAULT 0,
    alerts_attempted INTEGER NOT NULL DEFAULT 0,
    alerts_sent INTEGER NOT NULL DEFAULT 0,
    alerts_failed INTEGER NOT NULL DEFAULT 0,
    final_bucket_counts TEXT NOT NULL DEFAULT '{}',
    alert_block_reason_counts TEXT NOT NULL DEFAULT '{}',
    missing_data_reason_counts TEXT NOT NULL DEFAULT '{}',
    risk_flag_counts TEXT NOT NULL DEFAULT '{}',
    model_detection_failure_count INTEGER NOT NULL DEFAULT 0,
    resale_missing_count INTEGER NOT NULL DEFAULT 0,
    parts_pricing_status_counts TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS worker_heartbeats (
    worker_name TEXT PRIMARY KEY,
    process_id INTEGER NOT NULL,
    hostname TEXT NOT NULL,
    started_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    status TEXT NOT NULL,
    last_cycle_id INTEGER,
    last_error TEXT NOT NULL DEFAULT '',
    next_wake_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(last_cycle_id) REFERENCES scan_cycles(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS listing_decision_traces (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    marketplace_item_id INTEGER NOT NULL,
    scan_cycle_id INTEGER NOT NULL,
    trace_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(user_id, marketplace_item_id, scan_cycle_id),
    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY(marketplace_item_id) REFERENCES marketplace_items(id) ON DELETE CASCADE,
    FOREIGN KEY(scan_cycle_id) REFERENCES scan_cycles(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS source_statuses (
    source TEXT PRIMARY KEY,
    status TEXT NOT NULL DEFAULT 'ok',
    cooldown_until TEXT,
    last_success_at TEXT,
    last_failure_at TEXT,
    last_http_status INTEGER,
    last_error_category TEXT NOT NULL DEFAULT '',
    last_error_message TEXT NOT NULL DEFAULT '',
    last_keyword TEXT NOT NULL DEFAULT '',
    last_item_id TEXT NOT NULL DEFAULT '',
    retry_after_seconds INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS user_usage_daily (
    user_id INTEGER NOT NULL,
    usage_date TEXT NOT NULL,
    search_signatures_subscribed INTEGER NOT NULL DEFAULT 0,
    items_scored INTEGER NOT NULL DEFAULT 0,
    alerts_sent INTEGER NOT NULL DEFAULT 0,
    detail_refreshes INTEGER NOT NULL DEFAULT 0,
    shared_api_calls_attributed REAL NOT NULL DEFAULT 0,
    usage_weight REAL NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(user_id, usage_date),
    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);
"""

USER_STATUSES = {"new", "reviewed", "watched", "ignored", "promoted", "rejected"}
USER_ROLES = {"admin", "user"}
ACCOUNT_STATUSES = {"active", "disabled", "invited"}
ALLOWED_BILLING_STATUSES = {"", "trial", "active", "past_due", "manual", "comped"}


class Storage:
    def __init__(self, path: Path):
        self.path = path
        self._memory_connection: Optional[sqlite3.Connection] = None
        if str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.init_db()

    def connect(self) -> sqlite3.Connection:
        if str(self.path) == ":memory:":
            if self._memory_connection is None:
                self._memory_connection = sqlite3.connect(":memory:", check_same_thread=False)
                self._memory_connection.row_factory = sqlite3.Row
            return self._memory_connection

        connection = sqlite3.connect(str(self.path))
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=MEMORY")
        return connection

    def init_db(self) -> None:
        with self.connect() as connection:
            connection.executescript(SCHEMA)
            self._migrate(connection)
            self._create_indexes(connection)
            self._migrate_legacy_items_to_split(connection)
            self._migrate_legacy_ignores_to_user(connection)

    def _migrate(self, connection: sqlite3.Connection) -> None:
        columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(items)").fetchall()
        }
        migrations = {
            "model": "ALTER TABLE items ADD COLUMN model TEXT",
            "resale_value": "ALTER TABLE items ADD COLUMN resale_value REAL NOT NULL DEFAULT 0",
            "resale_low": "ALTER TABLE items ADD COLUMN resale_low REAL NOT NULL DEFAULT 0",
            "resale_mid": "ALTER TABLE items ADD COLUMN resale_mid REAL NOT NULL DEFAULT 0",
            "resale_high": "ALTER TABLE items ADD COLUMN resale_high REAL NOT NULL DEFAULT 0",
            "profit_low": "ALTER TABLE items ADD COLUMN profit_low REAL NOT NULL DEFAULT 0",
            "profit_mid": "ALTER TABLE items ADD COLUMN profit_mid REAL NOT NULL DEFAULT 0",
            "profit_high": "ALTER TABLE items ADD COLUMN profit_high REAL NOT NULL DEFAULT 0",
            "resale_confidence": "ALTER TABLE items ADD COLUMN resale_confidence TEXT NOT NULL DEFAULT ''",
            "resale_sample_size": "ALTER TABLE items ADD COLUMN resale_sample_size INTEGER NOT NULL DEFAULT 0",
            "resale_note": "ALTER TABLE items ADD COLUMN resale_note TEXT NOT NULL DEFAULT ''",
            "resale_source": "ALTER TABLE items ADD COLUMN resale_source TEXT NOT NULL DEFAULT 'missing'",
            "resale_market_source": "ALTER TABLE items ADD COLUMN resale_market_source TEXT NOT NULL DEFAULT 'missing'",
            "resale_condition_used": "ALTER TABLE items ADD COLUMN resale_condition_used TEXT NOT NULL DEFAULT ''",
            "resale_storage_used": "ALTER TABLE items ADD COLUMN resale_storage_used TEXT",
            "storage_resale_warning": "ALTER TABLE items ADD COLUMN storage_resale_warning TEXT NOT NULL DEFAULT ''",
            "mint_resale_low": "ALTER TABLE items ADD COLUMN mint_resale_low REAL NOT NULL DEFAULT 0",
            "mint_resale_mid": "ALTER TABLE items ADD COLUMN mint_resale_mid REAL NOT NULL DEFAULT 0",
            "mint_resale_high": "ALTER TABLE items ADD COLUMN mint_resale_high REAL NOT NULL DEFAULT 0",
            "mint_profit_low": "ALTER TABLE items ADD COLUMN mint_profit_low REAL NOT NULL DEFAULT 0",
            "mint_profit_mid": "ALTER TABLE items ADD COLUMN mint_profit_mid REAL NOT NULL DEFAULT 0",
            "mint_profit_high": "ALTER TABLE items ADD COLUMN mint_profit_high REAL NOT NULL DEFAULT 0",
            "storage_capacity": "ALTER TABLE items ADD COLUMN storage_capacity TEXT",
            "storage_confidence": "ALTER TABLE items ADD COLUMN storage_confidence TEXT NOT NULL DEFAULT ''",
            "storage_source": "ALTER TABLE items ADD COLUMN storage_source TEXT NOT NULL DEFAULT ''",
            "estimated_parts_cost": "ALTER TABLE items ADD COLUMN estimated_parts_cost REAL NOT NULL DEFAULT 0",
            "estimated_parts_cost_available": "ALTER TABLE items ADD COLUMN estimated_parts_cost_available INTEGER NOT NULL DEFAULT 1",
            "risk_buffer": "ALTER TABLE items ADD COLUMN risk_buffer REAL NOT NULL DEFAULT 0",
            "estimated_profit_available": "ALTER TABLE items ADD COLUMN estimated_profit_available INTEGER NOT NULL DEFAULT 1",
            "parts_pricing_status": "ALTER TABLE items ADD COLUMN parts_pricing_status TEXT NOT NULL DEFAULT 'fallback'",
            "parts_pricing_note": "ALTER TABLE items ADD COLUMN parts_pricing_note TEXT NOT NULL DEFAULT ''",
            "parts_pricing_label": "ALTER TABLE items ADD COLUMN parts_pricing_label TEXT NOT NULL DEFAULT 'Parts estimate not verified'",
            "pricing_warning": "ALTER TABLE items ADD COLUMN pricing_warning TEXT NOT NULL DEFAULT 'Parts estimate not verified'",
            "manual_review_allowed": "ALTER TABLE items ADD COLUMN manual_review_allowed INTEGER NOT NULL DEFAULT 0",
            "whole_phone_confidence_passed": "ALTER TABLE items ADD COLUMN whole_phone_confidence_passed INTEGER NOT NULL DEFAULT 0",
            "whole_phone_score": "ALTER TABLE items ADD COLUMN whole_phone_score REAL NOT NULL DEFAULT 0",
            "has_repair_issue": "ALTER TABLE items ADD COLUMN has_repair_issue INTEGER NOT NULL DEFAULT 0",
            "manual_review_needed": "ALTER TABLE items ADD COLUMN manual_review_needed INTEGER NOT NULL DEFAULT 0",
            "manual_review_reason": "ALTER TABLE items ADD COLUMN manual_review_reason TEXT NOT NULL DEFAULT ''",
            "alert_eligible": "ALTER TABLE items ADD COLUMN alert_eligible INTEGER NOT NULL DEFAULT 0",
            "listing_classification_flags": "ALTER TABLE items ADD COLUMN listing_classification_flags TEXT NOT NULL DEFAULT '[]'",
            "user_status": "ALTER TABLE items ADD COLUMN user_status TEXT NOT NULL DEFAULT 'new'",
            "user_note": "ALTER TABLE items ADD COLUMN user_note TEXT NOT NULL DEFAULT ''",
            "reviewed_at": "ALTER TABLE items ADD COLUMN reviewed_at TEXT",
            "ignored_at": "ALTER TABLE items ADD COLUMN ignored_at TEXT",
            "watched_at": "ALTER TABLE items ADD COLUMN watched_at TEXT",
            "promoted_at": "ALTER TABLE items ADD COLUMN promoted_at TEXT",
            "rejected_by_user_at": "ALTER TABLE items ADD COLUMN rejected_by_user_at TEXT",
            "user_reject_reason": "ALTER TABLE items ADD COLUMN user_reject_reason TEXT NOT NULL DEFAULT ''",
            "ignored_reason": "ALTER TABLE items ADD COLUMN ignored_reason TEXT NOT NULL DEFAULT ''",
            "ignored_seller": "ALTER TABLE items ADD COLUMN ignored_seller TEXT NOT NULL DEFAULT ''",
            "updated_by_user_at": "ALTER TABLE items ADD COLUMN updated_by_user_at TEXT",
            "alerted_at": "ALTER TABLE items ADD COLUMN alerted_at TEXT",
            "item_origin_at": "ALTER TABLE items ADD COLUMN item_origin_at TEXT",
            "availability_status": "ALTER TABLE items ADD COLUMN availability_status TEXT NOT NULL DEFAULT 'unknown'",
            "buying_option_summary": "ALTER TABLE items ADD COLUMN buying_option_summary TEXT NOT NULL DEFAULT 'unknown'",
            "item_end_at": "ALTER TABLE items ADD COLUMN item_end_at TEXT",
            "last_availability_checked_at": "ALTER TABLE items ADD COLUMN last_availability_checked_at TEXT",
            "availability_note": "ALTER TABLE items ADD COLUMN availability_note TEXT NOT NULL DEFAULT ''",
        }
        for column, statement in migrations.items():
            if column not in columns:
                connection.execute(statement)

        user_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(users)").fetchall()
        }
        user_migrations = {
            "monthly_price": "ALTER TABLE users ADD COLUMN monthly_price REAL NOT NULL DEFAULT 0",
        }
        for column, statement in user_migrations.items():
            if column not in user_columns:
                connection.execute(statement)

        scan_cycle_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(scan_cycles)").fetchall()
        }
        scan_cycle_migrations = {
            "source": "ALTER TABLE scan_cycles ADD COLUMN source TEXT",
            "error_category": "ALTER TABLE scan_cycles ADD COLUMN error_category TEXT",
            "http_status": "ALTER TABLE scan_cycles ADD COLUMN http_status INTEGER",
            "cooldown_until": "ALTER TABLE scan_cycles ADD COLUMN cooldown_until TEXT",
            "retry_after_seconds": "ALTER TABLE scan_cycles ADD COLUMN retry_after_seconds INTEGER",
        }
        for column, statement in scan_cycle_migrations.items():
            if column not in scan_cycle_columns:
                connection.execute(statement)

    def _create_indexes(self, connection: sqlite3.Connection) -> None:
        columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(items)").fetchall()
        }
        indexes = {
            "status": "CREATE INDEX IF NOT EXISTS idx_items_status ON items(status)",
            "user_status": "CREATE INDEX IF NOT EXISTS idx_items_user_status ON items(user_status)",
            "found_at": "CREATE INDEX IF NOT EXISTS idx_items_found_at ON items(found_at)",
            "item_origin_at": "CREATE INDEX IF NOT EXISTS idx_items_item_origin_at ON items(item_origin_at)",
            "alerted_at": "CREATE INDEX IF NOT EXISTS idx_items_alerted_at ON items(alerted_at)",
            "availability_status": "CREATE INDEX IF NOT EXISTS idx_items_availability_status ON items(availability_status)",
            "storage_capacity": "CREATE INDEX IF NOT EXISTS idx_items_storage_capacity ON items(storage_capacity)",
        }
        for column, statement in indexes.items():
            if column in columns:
                connection.execute(statement)
        connection.execute("CREATE INDEX IF NOT EXISTS idx_users_role ON users(role)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_users_account_status ON users(account_status)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_user_invites_code ON user_invites(code)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_user_invites_created_by_user_id ON user_invites(created_by_user_id)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_user_invites_used_by_user_id ON user_invites(used_by_user_id)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_user_invites_email ON user_invites(email)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_user_ignored_sellers_user_id ON user_ignored_sellers(user_id)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_user_ignored_sellers_seller ON user_ignored_sellers(user_id, seller_username)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_user_ignored_keywords_user_id ON user_ignored_keywords(user_id)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_user_ignored_keywords_keyword ON user_ignored_keywords(user_id, keyword)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_user_repair_value_overrides_user_id ON user_repair_value_overrides(user_id)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_user_repair_value_overrides_model_part ON user_repair_value_overrides(user_id, model, part)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_user_resale_research_overrides_user_id ON user_resale_research_overrides(user_id)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_user_resale_research_overrides_lookup ON user_resale_research_overrides(user_id, model, storage_capacity, condition)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_user_item_corrections_user_id ON user_item_corrections(user_id)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_user_item_corrections_marketplace_item_id ON user_item_corrections(marketplace_item_id)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_user_keywords_user_id ON user_keywords(user_id)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_user_keywords_enabled ON user_keywords(user_id, enabled)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_marketplace_items_item_key ON marketplace_items(marketplace, marketplace_item_id)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_marketplace_items_found_at ON marketplace_items(found_at)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_marketplace_items_seller ON marketplace_items(seller_username)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_user_item_states_user_id ON user_item_states(user_id)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_user_item_states_marketplace_item_id ON user_item_states(marketplace_item_id)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_user_item_states_status ON user_item_states(user_id, status)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_user_item_states_user_status ON user_item_states(user_id, user_status)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_shared_scan_runs_started_at ON shared_scan_runs(started_at)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_shared_scan_runs_finished_at ON shared_scan_runs(finished_at)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_shared_scan_searches_run_id ON shared_scan_searches(scan_run_id)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_shared_scan_searches_signature ON shared_scan_searches(search_signature)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_shared_scan_results_search_id ON shared_scan_results(scan_search_id)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_scan_cycles_started_at ON scan_cycles(started_at)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_scan_cycles_status ON scan_cycles(status)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_scan_cycles_user_id ON scan_cycles(user_id)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_listing_decision_traces_cycle ON listing_decision_traces(scan_cycle_id)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_listing_decision_traces_user_item ON listing_decision_traces(user_id, marketplace_item_id)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_source_statuses_updated_at ON source_statuses(updated_at)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_user_usage_daily_date ON user_usage_daily(usage_date)")

    def upsert_item(self, item: dict[str, Any]) -> None:
        now = now_iso()
        item_origin_at = item.get("item_origin_at") or item.get("found_at") or now
        found_at = item.get("found_at") or item_origin_at
        payload = {
            **item,
            "price": item.get("price") or 0,
            "shipping": item.get("shipping") or 0,
            "total_cost": item.get("total_cost") or 0,
            "condition": item.get("condition"),
            "item_url": item.get("item_url"),
            "image_url": item.get("image_url"),
            "seller_username": item.get("seller_username"),
            "seller_feedback_percentage": item.get("seller_feedback_percentage"),
            "seller_feedback_score": item.get("seller_feedback_score"),
            "raw_description": item.get("raw_description"),
            "item_origin_at": item_origin_at,
            "model": item.get("model") or "unknown",
            "score": item.get("score") or 0,
            "status": item.get("status") or "risky",
            "resale_value": item.get("resale_value") or 0,
            "resale_low": item.get("resale_low") or 0,
            "resale_mid": item.get("resale_mid") or item.get("resale_value") or 0,
            "resale_high": item.get("resale_high") or 0,
            "profit_low": item.get("profit_low") or 0,
            "profit_mid": item.get("profit_mid") or item.get("estimated_profit") or 0,
            "profit_high": item.get("profit_high") or 0,
            "resale_confidence": item.get("resale_confidence") or "",
            "resale_sample_size": item.get("resale_sample_size") or 0,
            "resale_note": item.get("resale_note") or "",
            "resale_source": item.get("resale_source") or "missing",
            "resale_market_source": item.get("resale_market_source") or "missing",
            "resale_condition_used": item.get("resale_condition_used") or "",
            "resale_storage_used": item.get("resale_storage_used"),
            "storage_resale_warning": item.get("storage_resale_warning") or "",
            "mint_resale_low": item.get("mint_resale_low") or 0,
            "mint_resale_mid": item.get("mint_resale_mid") or 0,
            "mint_resale_high": item.get("mint_resale_high") or 0,
            "mint_profit_low": item.get("mint_profit_low") or 0,
            "mint_profit_mid": item.get("mint_profit_mid") or 0,
            "mint_profit_high": item.get("mint_profit_high") or 0,
            "storage_capacity": item.get("storage_capacity"),
            "storage_confidence": item.get("storage_confidence") or "",
            "storage_source": item.get("storage_source") or "",
            "estimated_parts_cost": item.get("estimated_parts_cost") or 0,
            "estimated_parts_cost_available": int(bool(item.get("estimated_parts_cost_available", True))),
            "risk_buffer": item.get("risk_buffer") or 0,
            "estimated_profit": item.get("estimated_profit") or 0,
            "estimated_profit_available": int(bool(item.get("estimated_profit_available", True))),
            "parts_pricing_status": item.get("parts_pricing_status") or "fallback",
            "parts_pricing_note": item.get("parts_pricing_note") or "",
            "parts_pricing_label": item.get("parts_pricing_label") or "Parts estimate not verified",
            "pricing_warning": item.get("pricing_warning") or "Parts estimate not verified",
            "manual_review_allowed": int(bool(item.get("manual_review_allowed", False))),
            "whole_phone_confidence_passed": int(bool(item.get("whole_phone_confidence_passed", False))),
            "whole_phone_score": item.get("whole_phone_score") or 0,
            "has_repair_issue": int(bool(item.get("has_repair_issue", False))),
            "manual_review_needed": int(bool(item.get("manual_review_needed", False))),
            "manual_review_reason": item.get("manual_review_reason") or "",
            "alert_eligible": int(bool(item.get("alert_eligible", False))),
            "listing_classification_flags": json.dumps(item.get("listing_classification_flags", [])),
            "user_status": item.get("user_status") or "new",
            "user_note": item.get("user_note") or "",
            "reviewed_at": item.get("reviewed_at"),
            "ignored_at": item.get("ignored_at"),
            "watched_at": item.get("watched_at"),
            "promoted_at": item.get("promoted_at"),
            "rejected_by_user_at": item.get("rejected_by_user_at"),
            "user_reject_reason": item.get("user_reject_reason") or "",
            "ignored_reason": item.get("ignored_reason") or "",
            "ignored_seller": item.get("ignored_seller") or "",
            "updated_by_user_at": item.get("updated_by_user_at"),
            "found_at": found_at,
            "updated_at": now,
            "hard_reject_flags": json.dumps(item.get("hard_reject_flags", [])),
            "positive_flags": json.dumps(item.get("positive_flags", [])),
            "risk_flags": json.dumps(item.get("risk_flags", [])),
            "availability_status": item.get("availability_status") or "unknown",
            "buying_option_summary": item.get("buying_option_summary") or "unknown",
            "item_end_at": item.get("item_end_at"),
            "last_availability_checked_at": item.get("last_availability_checked_at"),
            "availability_note": item.get("availability_note") or "",
            "raw_json": json.dumps(item.get("raw_json", {})),
        }
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO items (
                    item_id, title, price, shipping, total_cost, condition, item_url,
                    image_url, seller_username, seller_feedback_percentage,
                    seller_feedback_score, raw_description, found_at, updated_at,
                    item_origin_at, model, score, status, resale_value, resale_low, resale_mid,
                    resale_high, profit_low, profit_mid, profit_high,
                    resale_confidence, resale_sample_size, resale_note,
                    resale_source, resale_market_source, resale_condition_used,
                    resale_storage_used, storage_resale_warning,
                    mint_resale_low, mint_resale_mid, mint_resale_high,
                    mint_profit_low, mint_profit_mid, mint_profit_high,
                    storage_capacity, storage_confidence, storage_source,
                    estimated_parts_cost, estimated_parts_cost_available,
                    risk_buffer, estimated_profit, estimated_profit_available,
                    parts_pricing_status,
                    parts_pricing_note, parts_pricing_label, pricing_warning,
                    manual_review_allowed, whole_phone_confidence_passed,
                    whole_phone_score, has_repair_issue, manual_review_needed,
                    manual_review_reason, alert_eligible,
                    listing_classification_flags,
                    user_status, user_note, reviewed_at, ignored_at, watched_at,
                    promoted_at, rejected_by_user_at, user_reject_reason,
                    ignored_reason, ignored_seller, updated_by_user_at,
                    hard_reject_flags, positive_flags, risk_flags,
                    availability_status, buying_option_summary, item_end_at,
                    last_availability_checked_at, availability_note, raw_json
                )
                VALUES (
                    :item_id, :title, :price, :shipping, :total_cost, :condition,
                    :item_url, :image_url, :seller_username,
                    :seller_feedback_percentage, :seller_feedback_score,
                    :raw_description, :found_at, :updated_at, :item_origin_at, :model, :score,
                    :status, :resale_value, :resale_low, :resale_mid,
                    :resale_high, :profit_low, :profit_mid, :profit_high,
                    :resale_confidence, :resale_sample_size, :resale_note,
                    :resale_source, :resale_market_source, :resale_condition_used,
                    :resale_storage_used, :storage_resale_warning,
                    :mint_resale_low, :mint_resale_mid, :mint_resale_high,
                    :mint_profit_low, :mint_profit_mid, :mint_profit_high,
                    :storage_capacity, :storage_confidence, :storage_source,
                    :estimated_parts_cost, :estimated_parts_cost_available,
                    :risk_buffer, :estimated_profit, :estimated_profit_available,
                    :parts_pricing_status,
                    :parts_pricing_note, :parts_pricing_label, :pricing_warning,
                    :manual_review_allowed, :whole_phone_confidence_passed,
                    :whole_phone_score, :has_repair_issue, :manual_review_needed,
                    :manual_review_reason, :alert_eligible,
                    :listing_classification_flags,
                    :user_status, :user_note, :reviewed_at, :ignored_at,
                    :watched_at, :promoted_at, :rejected_by_user_at,
                    :user_reject_reason, :ignored_reason, :ignored_seller,
                    :updated_by_user_at,
                    :hard_reject_flags, :positive_flags, :risk_flags,
                    :availability_status, :buying_option_summary, :item_end_at,
                    :last_availability_checked_at, :availability_note, :raw_json
                )
                ON CONFLICT(item_id) DO UPDATE SET
                    title = excluded.title,
                    price = excluded.price,
                    shipping = excluded.shipping,
                    total_cost = excluded.total_cost,
                    condition = excluded.condition,
                    item_url = excluded.item_url,
                    image_url = excluded.image_url,
                    seller_username = excluded.seller_username,
                    seller_feedback_percentage = excluded.seller_feedback_percentage,
                    seller_feedback_score = excluded.seller_feedback_score,
                    raw_description = excluded.raw_description,
                    item_origin_at = COALESCE(excluded.item_origin_at, items.item_origin_at, items.found_at),
                    updated_at = excluded.updated_at,
                    model = excluded.model,
                    score = excluded.score,
                    status = CASE
                        WHEN excluded.status = 'rejected' THEN excluded.status
                        WHEN excluded.alert_eligible = 0 AND excluded.status != 'candidate' THEN excluded.status
                        WHEN items.alerted_at IS NOT NULL THEN items.status
                        ELSE excluded.status
                    END,
                    resale_value = excluded.resale_value,
                    resale_low = excluded.resale_low,
                    resale_mid = excluded.resale_mid,
                    resale_high = excluded.resale_high,
                    profit_low = excluded.profit_low,
                    profit_mid = excluded.profit_mid,
                    profit_high = excluded.profit_high,
                    resale_confidence = excluded.resale_confidence,
                    resale_sample_size = excluded.resale_sample_size,
                    resale_note = excluded.resale_note,
                    resale_source = excluded.resale_source,
                    resale_market_source = excluded.resale_market_source,
                    resale_condition_used = excluded.resale_condition_used,
                    resale_storage_used = excluded.resale_storage_used,
                    storage_resale_warning = excluded.storage_resale_warning,
                    mint_resale_low = excluded.mint_resale_low,
                    mint_resale_mid = excluded.mint_resale_mid,
                    mint_resale_high = excluded.mint_resale_high,
                    mint_profit_low = excluded.mint_profit_low,
                    mint_profit_mid = excluded.mint_profit_mid,
                    mint_profit_high = excluded.mint_profit_high,
                    storage_capacity = excluded.storage_capacity,
                    storage_confidence = excluded.storage_confidence,
                    storage_source = excluded.storage_source,
                    estimated_parts_cost = excluded.estimated_parts_cost,
                    estimated_parts_cost_available = excluded.estimated_parts_cost_available,
                    risk_buffer = excluded.risk_buffer,
                    estimated_profit = excluded.estimated_profit,
                    estimated_profit_available = excluded.estimated_profit_available,
                    parts_pricing_status = excluded.parts_pricing_status,
                    parts_pricing_note = excluded.parts_pricing_note,
                    parts_pricing_label = excluded.parts_pricing_label,
                    pricing_warning = excluded.pricing_warning,
                    manual_review_allowed = excluded.manual_review_allowed,
                    whole_phone_confidence_passed = excluded.whole_phone_confidence_passed,
                    whole_phone_score = excluded.whole_phone_score,
                    has_repair_issue = excluded.has_repair_issue,
                    manual_review_needed = excluded.manual_review_needed,
                    manual_review_reason = excluded.manual_review_reason,
                    alert_eligible = excluded.alert_eligible,
                    listing_classification_flags = excluded.listing_classification_flags,
                    user_status = CASE
                        WHEN excluded.user_status != 'new' THEN excluded.user_status
                        ELSE items.user_status
                    END,
                    user_note = CASE
                        WHEN excluded.user_note != '' THEN excluded.user_note
                        ELSE items.user_note
                    END,
                    reviewed_at = COALESCE(excluded.reviewed_at, items.reviewed_at),
                    ignored_at = COALESCE(excluded.ignored_at, items.ignored_at),
                    watched_at = COALESCE(excluded.watched_at, items.watched_at),
                    promoted_at = COALESCE(excluded.promoted_at, items.promoted_at),
                    rejected_by_user_at = COALESCE(excluded.rejected_by_user_at, items.rejected_by_user_at),
                    user_reject_reason = CASE
                        WHEN excluded.user_reject_reason != '' THEN excluded.user_reject_reason
                        ELSE items.user_reject_reason
                    END,
                    ignored_reason = CASE
                        WHEN excluded.ignored_reason != '' THEN excluded.ignored_reason
                        ELSE items.ignored_reason
                    END,
                    ignored_seller = CASE
                        WHEN excluded.ignored_seller != '' THEN excluded.ignored_seller
                        ELSE items.ignored_seller
                    END,
                    updated_by_user_at = COALESCE(excluded.updated_by_user_at, items.updated_by_user_at),
                    hard_reject_flags = excluded.hard_reject_flags,
                    positive_flags = excluded.positive_flags,
                    risk_flags = excluded.risk_flags,
                    availability_status = excluded.availability_status,
                    buying_option_summary = excluded.buying_option_summary,
                    item_end_at = excluded.item_end_at,
                    last_availability_checked_at = COALESCE(excluded.last_availability_checked_at, items.last_availability_checked_at),
                    availability_note = excluded.availability_note,
                    raw_json = excluded.raw_json
                """,
                payload,
            )

    def update_availability(self, item_id: str, fields: dict[str, Any]) -> Optional[dict[str, Any]]:
        now = now_iso()
        allowed = {
            "availability_status",
            "buying_option_summary",
            "item_end_at",
            "last_availability_checked_at",
            "availability_note",
            "raw_description",
            "raw_json",
        }
        assignments = []
        params: list[Any] = []
        for key in allowed:
            if key not in fields:
                continue
            assignments.append(f"{key} = ?")
            value = fields[key]
            params.append(json.dumps(value) if key == "raw_json" else value)
        if not assignments:
            return self.get_item(item_id)
        assignments.append("updated_at = ?")
        params.append(now)
        params.append(item_id)
        with self.connect() as connection:
            connection.execute(f"UPDATE items SET {', '.join(assignments)} WHERE item_id = ?", params)
        return self.get_item(item_id)

    def get_item(
        self,
        item_id: str,
        *,
        max_alert_item_age_minutes: int = DEFAULT_MAX_ALERT_ITEM_AGE_MINUTES,
        max_priority_review_item_age_hours: int = DEFAULT_MAX_PRIORITY_REVIEW_ITEM_AGE_HOURS,
        max_active_queue_item_age_hours: int = DEFAULT_MAX_ACTIVE_QUEUE_ITEM_AGE_HOURS,
    ) -> Optional[dict[str, Any]]:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM items WHERE item_id = ?", (item_id,)).fetchone()
        return (
            _row_to_dict(
                row,
                max_alert_item_age_minutes=max_alert_item_age_minutes,
                max_priority_review_item_age_hours=max_priority_review_item_age_hours,
                max_active_queue_item_age_hours=max_active_queue_item_age_hours,
            )
            if row
            else None
        )

    def list_items(
        self,
        status: Optional[str] = None,
        user_status: Optional[str] = None,
        limit: int = 100,
        include_ignored: bool = False,
        include_stale: bool = False,
        max_alert_item_age_minutes: int = DEFAULT_MAX_ALERT_ITEM_AGE_MINUTES,
        max_priority_review_item_age_hours: int = DEFAULT_MAX_PRIORITY_REVIEW_ITEM_AGE_HOURS,
        max_active_queue_item_age_hours: int = DEFAULT_MAX_ACTIVE_QUEUE_ITEM_AGE_HOURS,
    ) -> list[dict[str, Any]]:
        limit = max(1, min(limit, 500))
        clauses = []
        params: list[Any] = []
        if status:
            clauses.append("status = ?")
            params.append(status)
        if user_status:
            clauses.append("user_status = ?")
            params.append(user_status)
        if not include_ignored:
            clauses.append("user_status != 'ignored'")
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self.connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM items {where} ORDER BY COALESCE(item_origin_at, found_at) DESC LIMIT ?",
                [*params, 500],
            ).fetchall()
        items = [
            _row_to_dict(
                row,
                max_alert_item_age_minutes=max_alert_item_age_minutes,
                max_priority_review_item_age_hours=max_priority_review_item_age_hours,
                max_active_queue_item_age_hours=max_active_queue_item_age_hours,
            )
            for row in rows
        ]
        if not include_stale:
            items = [item for item in items if not item["stale"]]
        return items[:limit]

    def item_exists(self, item_id: str) -> bool:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM items WHERE item_id = ?",
                (item_id,),
            ).fetchone()
        return bool(row)

    def all_items(
        self,
        *,
        max_alert_item_age_minutes: int = DEFAULT_MAX_ALERT_ITEM_AGE_MINUTES,
        max_priority_review_item_age_hours: int = DEFAULT_MAX_PRIORITY_REVIEW_ITEM_AGE_HOURS,
        max_active_queue_item_age_hours: int = DEFAULT_MAX_ACTIVE_QUEUE_ITEM_AGE_HOURS,
    ) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM items ORDER BY COALESCE(item_origin_at, found_at) DESC",
            ).fetchall()
        return [
            _row_to_dict(
                row,
                max_alert_item_age_minutes=max_alert_item_age_minutes,
                max_priority_review_item_age_hours=max_priority_review_item_age_hours,
                max_active_queue_item_age_hours=max_active_queue_item_age_hours,
            )
            for row in rows
        ]

    def list_items_for_availability_refresh(
        self,
        *,
        limit: int = 25,
        max_alert_item_age_minutes: int = DEFAULT_MAX_ALERT_ITEM_AGE_MINUTES,
        max_priority_review_item_age_hours: int = DEFAULT_MAX_PRIORITY_REVIEW_ITEM_AGE_HOURS,
        max_active_queue_item_age_hours: int = DEFAULT_MAX_ACTIVE_QUEUE_ITEM_AGE_HOURS,
    ) -> list[dict[str, Any]]:
        items = self.all_items(
            max_alert_item_age_minutes=max_alert_item_age_minutes,
            max_priority_review_item_age_hours=max_priority_review_item_age_hours,
            max_active_queue_item_age_hours=max_active_queue_item_age_hours,
        )
        eligible = [
            item
            for item in items
            if _should_refresh_availability(item)
        ]
        eligible.sort(key=lambda item: item.get("last_availability_checked_at") or "")
        return eligible[: max(1, min(limit, 100))]

    def set_user_status(self, item_id: str, user_status: str, *, ignored_reason: str = "") -> dict[str, Any]:
        if user_status not in USER_STATUSES:
            raise ValueError(f"Unsupported user_status={user_status}")
        timestamp_column = {
            "reviewed": "reviewed_at",
            "watched": "watched_at",
            "ignored": "ignored_at",
            "promoted": "promoted_at",
            "rejected": "rejected_by_user_at",
        }.get(user_status)
        now = now_iso()
        assignments = ["user_status = ?", "updated_by_user_at = ?"]
        params: list[Any] = [user_status, now]
        if timestamp_column:
            assignments.append(f"{timestamp_column} = ?")
            params.append(now)
        if ignored_reason:
            assignments.append("ignored_reason = ?")
            params.append(ignored_reason[:300])
        if user_status == "rejected" and ignored_reason:
            assignments.append("user_reject_reason = ?")
            params.append(ignored_reason[:300])
        params.append(item_id)
        with self.connect() as connection:
            connection.execute(
                f"UPDATE items SET {', '.join(assignments)} WHERE item_id = ?",
                params,
            )
        item = self.get_item(item_id)
        if not item:
            raise KeyError(item_id)
        return item

    def set_note(self, item_id: str, note: str) -> dict[str, Any]:
        now = now_iso()
        with self.connect() as connection:
            connection.execute(
                "UPDATE items SET user_note = ?, updated_by_user_at = ? WHERE item_id = ?",
                (note[:1000], now, item_id),
            )
        item = self.get_item(item_id)
        if not item:
            raise KeyError(item_id)
        return item

    def was_alerted(self, item_id: str) -> bool:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT alerted_at FROM items WHERE item_id = ?",
                (item_id,),
            ).fetchone()
        return bool(row and row["alerted_at"])

    def mark_alerted(self, item_id: str) -> None:
        now = now_iso()
        with self.connect() as connection:
            connection.execute(
                "UPDATE items SET status = 'alerted', alerted_at = ?, updated_at = ? WHERE item_id = ?",
                (now, now, item_id),
            )

    def add_ignored_seller(
        self,
        seller_username: str,
        *,
        reason: str = "",
        source_item_id: str | None = None,
        user_id: int | None = None,
    ) -> None:
        seller_username = seller_username.strip()
        if not seller_username:
            raise ValueError("seller_username is required")
        now = now_iso()
        if user_id is not None:
            with self.connect() as connection:
                connection.execute(
                    """
                    INSERT INTO user_ignored_sellers (
                        user_id, seller_username, reason, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(user_id, seller_username) DO UPDATE SET
                        reason = excluded.reason,
                        updated_at = excluded.updated_at
                    """,
                    (user_id, seller_username, reason[:300], now, now),
                )
                self._apply_user_ignored_seller(connection, user_id, seller_username, reason[:300], now)
            return
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO ignored_sellers (seller_username, reason, source_item_id, created_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(seller_username) DO UPDATE SET
                    reason = excluded.reason,
                    source_item_id = COALESCE(excluded.source_item_id, ignored_sellers.source_item_id)
                """,
                (seller_username, reason[:300], source_item_id, now),
            )
            connection.execute(
                """
                UPDATE items
                SET user_status = 'ignored',
                    ignored_at = COALESCE(ignored_at, ?),
                    ignored_seller = ?,
                    ignored_reason = ?,
                    updated_by_user_at = ?
                WHERE lower(seller_username) = lower(?)
                """,
                (now, seller_username, reason[:300] or "Ignored seller", now, seller_username),
            )

    def add_ignored_keyword(self, keyword: str, *, reason: str = "", user_id: int | None = None) -> None:
        keyword = keyword.strip()
        if not keyword:
            raise ValueError("keyword is required")
        now = now_iso()
        if user_id is not None:
            with self.connect() as connection:
                connection.execute(
                    """
                    INSERT INTO user_ignored_keywords (
                        user_id, keyword, reason, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(user_id, keyword) DO UPDATE SET
                        reason = excluded.reason,
                        updated_at = excluded.updated_at
                    """,
                    (user_id, keyword[:120], reason[:300], now, now),
                )
                self._apply_user_ignored_keyword(connection, user_id, keyword[:120], reason[:300], now)
            return
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO ignored_keywords (keyword, reason, created_at)
                VALUES (?, ?, ?)
                ON CONFLICT(keyword) DO UPDATE SET reason = excluded.reason
                """,
                (keyword, reason[:300], now),
            )
            connection.execute(
                """
                UPDATE items
                SET user_status = 'ignored',
                    ignored_at = COALESCE(ignored_at, ?),
                    ignored_reason = ?,
                    updated_by_user_at = ?
                WHERE lower(title) LIKE ?
                """,
                (now, reason[:300] or f"Ignored keyword: {keyword}", now, f"%{keyword.lower()}%"),
            )

    def ignore_seller_from_item(self, item_id: str, *, reason: str = "", user_id: int | None = None) -> dict[str, Any]:
        item = self.get_user_item(user_id, item_id) if user_id is not None else self.get_item(item_id)
        if not item:
            raise KeyError(item_id)
        seller = item.get("seller_username")
        if not seller:
            raise ValueError("Selected item has no seller username")
        self.add_ignored_seller(
            seller,
            reason=reason or "Ignored from dashboard",
            source_item_id=item_id,
            user_id=user_id,
        )
        updated = (
            self.set_user_item_status(user_id, item_id, "ignored", ignored_reason=reason or "Ignored seller")
            if user_id is not None
            else self.get_item(item_id)
        )
        if not updated:
            raise KeyError(item_id)
        return updated

    def list_ignored_sellers(self, *, user_id: int | None = None) -> list[dict[str, Any]]:
        if user_id is not None:
            with self.connect() as connection:
                rows = connection.execute(
                    """
                    SELECT id, user_id, seller_username, reason, created_at, updated_at
                    FROM user_ignored_sellers
                    WHERE user_id = ?
                    ORDER BY created_at DESC, id DESC
                    """,
                    (user_id,),
                ).fetchall()
            return [dict(row) for row in rows]
        with self.connect() as connection:
            rows = connection.execute("SELECT * FROM ignored_sellers ORDER BY created_at DESC").fetchall()
        return [dict(row) for row in rows]

    def list_ignored_keywords(self, *, user_id: int | None = None) -> list[dict[str, Any]]:
        if user_id is not None:
            with self.connect() as connection:
                rows = connection.execute(
                    """
                    SELECT id, user_id, keyword, reason, created_at, updated_at
                    FROM user_ignored_keywords
                    WHERE user_id = ?
                    ORDER BY created_at DESC, id DESC
                    """,
                    (user_id,),
                ).fetchall()
            return [dict(row) for row in rows]
        with self.connect() as connection:
            rows = connection.execute("SELECT * FROM ignored_keywords ORDER BY created_at DESC").fetchall()
        return [dict(row) for row in rows]

    def any_admin_exists(self) -> bool:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM users WHERE role = 'admin' LIMIT 1",
            ).fetchone()
        return bool(row)

    def any_users_exist(self) -> bool:
        with self.connect() as connection:
            row = connection.execute("SELECT 1 FROM users LIMIT 1").fetchone()
        return bool(row)

    def create_user(
        self,
        *,
        email: str,
        password_hash: str,
        role: str = "user",
        account_status: str = "active",
        display_name: str = "",
        plan_name: str = "",
        monthly_price: float = 0,
        billing_status: str = "",
        paid_until: str | None = None,
        billing_note: str = "",
        settings_seed: dict[str, Any] | None = None,
        baseline_keywords: list[str] | None = None,
    ) -> dict[str, Any]:
        role = role.strip().lower()
        account_status = account_status.strip().lower()
        email = email.strip().lower()
        if not email:
            raise ValueError("email is required")
        if role not in USER_ROLES:
            raise ValueError(f"Unsupported role={role}")
        if account_status not in ACCOUNT_STATUSES:
            raise ValueError(f"Unsupported account_status={account_status}")
        if billing_status.strip().lower() not in ALLOWED_BILLING_STATUSES:
            raise ValueError(f"Unsupported billing_status={billing_status.strip().lower()}")
        if not password_hash:
            raise ValueError("password_hash is required")
        now = now_iso()
        try:
            with self.connect() as connection:
                cursor = connection.execute(
                    """
                    INSERT INTO users (
                        email, password_hash, role, account_status, display_name,
                        plan_name, monthly_price, billing_status, paid_until, billing_note, created_at,
                        updated_at, last_login_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        email,
                        password_hash,
                        role,
                        account_status,
                        display_name[:120],
                        plan_name[:120],
                        round(float(monthly_price or 0), 2),
                        billing_status[:80],
                        paid_until,
                        billing_note[:500],
                        now,
                        now,
                        None,
                    ),
                )
                self._create_default_user_records(
                    connection,
                    int(cursor.lastrowid),
                    settings_seed=settings_seed,
                    baseline_keywords=baseline_keywords,
                )
        except sqlite3.IntegrityError as exc:
            raise ValueError("A user with that email already exists") from exc
        user = self.get_user(int(cursor.lastrowid))
        if not user:
            raise KeyError(email)
        return user

    def get_user(self, user_id: int, *, include_password_hash: bool = False) -> Optional[dict[str, Any]]:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return _user_row_to_dict(row, include_password_hash=include_password_hash) if row else None

    def get_user_by_email(self, email: str, *, include_password_hash: bool = False) -> Optional[dict[str, Any]]:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM users WHERE lower(email) = lower(?)",
                (email.strip(),),
            ).fetchone()
        return _user_row_to_dict(row, include_password_hash=include_password_hash) if row else None

    def list_users(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM users ORDER BY created_at ASC, id ASC",
            ).fetchall()
        users = [_user_row_to_dict(row) for row in rows]
        for user in users:
            summary = self.get_user_configuration_summary(int(user["id"]))
            user.update(summary)
        return users

    def list_active_users(self, *, include_password_hash: bool = False) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM users
                WHERE account_status = 'active'
                ORDER BY CASE WHEN role = 'admin' THEN 0 ELSE 1 END, created_at ASC, id ASC
                """
            ).fetchall()
        return [_user_row_to_dict(row, include_password_hash=include_password_hash) for row in rows]

    def set_user_account_status(self, user_id: int, account_status: str) -> dict[str, Any]:
        account_status = account_status.strip().lower()
        if account_status not in ACCOUNT_STATUSES:
            raise ValueError(f"Unsupported account_status={account_status}")
        now = now_iso()
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE users
                SET account_status = ?, updated_at = ?
                WHERE id = ?
                """,
                (account_status, now, user_id),
            )
        user = self.get_user(user_id)
        if not user:
            raise KeyError(user_id)
        return user

    def update_user(self, user_id: int, values: dict[str, Any]) -> dict[str, Any]:
        if not values:
            user = self.get_user(user_id)
            if not user:
                raise KeyError(user_id)
            return user
        assignments = []
        params: list[Any] = []
        if "email" in values and values["email"] is not None:
            email = str(values["email"]).strip().lower()
            if not email:
                raise ValueError("email is required")
            assignments.append("email = ?")
            params.append(email[:320])
        if "role" in values and values["role"] is not None:
            role = str(values["role"]).strip().lower()
            if role not in USER_ROLES:
                raise ValueError(f"Unsupported role={role}")
            assignments.append("role = ?")
            params.append(role)
        if "account_status" in values and values["account_status"] is not None:
            account_status = str(values["account_status"]).strip().lower()
            if account_status not in ACCOUNT_STATUSES:
                raise ValueError(f"Unsupported account_status={account_status}")
            assignments.append("account_status = ?")
            params.append(account_status)
        if "display_name" in values and values["display_name"] is not None:
            assignments.append("display_name = ?")
            params.append(str(values["display_name"])[:120])
        if "plan_name" in values and values["plan_name"] is not None:
            assignments.append("plan_name = ?")
            params.append(str(values["plan_name"])[:120])
        if "monthly_price" in values and values["monthly_price"] is not None:
            monthly_price = round(float(values["monthly_price"] or 0), 2)
            if monthly_price < 0:
                raise ValueError("monthly_price must be non-negative")
            assignments.append("monthly_price = ?")
            params.append(monthly_price)
        if "billing_status" in values and values["billing_status"] is not None:
            billing_status = str(values["billing_status"]).strip().lower()
            if billing_status not in ALLOWED_BILLING_STATUSES:
                raise ValueError(f"Unsupported billing_status={billing_status}")
            assignments.append("billing_status = ?")
            params.append(billing_status)
        if "paid_until" in values:
            assignments.append("paid_until = ?")
            params.append(values["paid_until"])
        if "billing_note" in values and values["billing_note"] is not None:
            assignments.append("billing_note = ?")
            params.append(str(values["billing_note"])[:500])
        assignments.append("updated_at = ?")
        params.append(now_iso())
        params.append(user_id)
        try:
            with self.connect() as connection:
                connection.execute(
                    f"UPDATE users SET {', '.join(assignments)} WHERE id = ?",
                    params,
                )
        except sqlite3.IntegrityError as exc:
            raise ValueError("A user with that email already exists") from exc
        user = self.get_user(user_id)
        if not user:
            raise KeyError(user_id)
        summary = self.get_user_configuration_summary(user_id)
        user.update(summary)
        return user

    def touch_user_last_login(self, user_id: int) -> dict[str, Any]:
        now = now_iso()
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE users
                SET last_login_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (now, now, user_id),
            )
        user = self.get_user(user_id)
        if not user:
            raise KeyError(user_id)
        return user

    def create_invite(
        self,
        *,
        code: str,
        created_by_user_id: int,
        email: str | None = None,
        role: str = "user",
        expires_at: str | None = None,
    ) -> dict[str, Any]:
        code = str(code or "").strip()
        role = str(role or "user").strip().lower() or "user"
        email_value = str(email or "").strip().lower() or None
        if not code:
            raise ValueError("code is required")
        if role not in USER_ROLES:
            raise ValueError(f"Unsupported role={role}")
        now = now_iso()
        try:
            with self.connect() as connection:
                connection.execute(
                    """
                    INSERT INTO user_invites (
                        code, email, role, created_by_user_id, used_by_user_id, used_at,
                        expires_at, created_at, revoked_at
                    )
                    VALUES (?, ?, ?, ?, NULL, NULL, ?, ?, NULL)
                    """,
                    (code[:120], email_value[:320] if email_value else None, role, created_by_user_id, expires_at, now),
                )
        except sqlite3.IntegrityError as exc:
            raise ValueError("Invite code already exists") from exc
        invite = self.get_invite_by_code(code, include_inactive=True)
        if not invite:
            raise KeyError(code)
        return invite

    def list_invites(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT ui.*
                FROM user_invites ui
                ORDER BY ui.created_at DESC, ui.id DESC
                """
            ).fetchall()
        return [_invite_row_to_dict(row) for row in rows]

    def get_invite_by_code(self, code: str, *, include_inactive: bool = False) -> Optional[dict[str, Any]]:
        code = str(code or "").strip()
        if not code:
            return None
        clauses = ["code = ?"]
        params: list[Any] = [code]
        if not include_inactive:
            clauses.extend(["used_at IS NULL", "revoked_at IS NULL"])
        with self.connect() as connection:
            row = connection.execute(
                f"SELECT * FROM user_invites WHERE {' AND '.join(clauses)} LIMIT 1",
                params,
            ).fetchone()
        invite = _invite_row_to_dict(row) if row else None
        if invite and not include_inactive and invite.get("is_expired"):
            return None
        return invite

    def get_invite(self, invite_id: int) -> Optional[dict[str, Any]]:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM user_invites WHERE id = ? LIMIT 1", (invite_id,)).fetchone()
        return _invite_row_to_dict(row) if row else None

    def get_usable_invite(self, code: str, *, email: str | None = None) -> Optional[dict[str, Any]]:
        invite = self.get_invite_by_code(code, include_inactive=False)
        if not invite:
            return None
        invite_email = str(invite.get("email") or "").strip().lower()
        if invite_email and invite_email != str(email or "").strip().lower():
            return None
        return invite

    def mark_invite_used(self, invite_id: int, *, used_by_user_id: int) -> dict[str, Any]:
        now = now_iso()
        with self.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE user_invites
                SET used_by_user_id = ?, used_at = ?
                WHERE id = ? AND used_at IS NULL AND revoked_at IS NULL
                """,
                (used_by_user_id, now, invite_id),
            )
        if cursor.rowcount == 0:
            raise KeyError(invite_id)
        invite = self.get_invite(invite_id)
        if not invite:
            raise KeyError(invite_id)
        return invite

    def revoke_invite(self, invite_id: int) -> dict[str, Any]:
        now = now_iso()
        with self.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE user_invites
                SET revoked_at = ?
                WHERE id = ? AND revoked_at IS NULL
                """,
                (now, invite_id),
            )
        if cursor.rowcount == 0:
            raise KeyError(invite_id)
        invite = self.get_invite(invite_id)
        if not invite:
            raise KeyError(invite_id)
        return invite

    def get_local_settings_user(self) -> Optional[dict[str, Any]]:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM users
                WHERE account_status = 'active'
                ORDER BY CASE WHEN role = 'admin' THEN 0 ELSE 1 END, created_at ASC, id ASC
                LIMIT 1
                """,
            ).fetchone()
        return _user_row_to_dict(row, include_password_hash=True) if row else None

    def ensure_user_settings(
        self,
        user_id: int,
        *,
        settings_seed: dict[str, Any] | None = None,
        baseline_keywords: list[str] | None = None,
    ) -> None:
        with self.connect() as connection:
            self._create_default_user_records(
                connection,
                user_id,
                settings_seed=settings_seed,
                baseline_keywords=baseline_keywords,
            )

    def get_user_settings(self, user_id: int) -> Optional[dict[str, Any]]:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM user_settings WHERE user_id = ?",
                (user_id,),
            ).fetchone()
        return _settings_row_to_dict(row) if row else None

    def update_user_settings(self, user_id: int, values: dict[str, Any]) -> dict[str, Any]:
        now = now_iso()
        assignments = []
        params: list[Any] = []
        for column, value in values.items():
            assignments.append(f"{column} = ?")
            params.append(value)
        assignments.append("updated_at = ?")
        params.append(now)
        params.append(user_id)
        with self.connect() as connection:
            connection.execute(
                f"UPDATE user_settings SET {', '.join(assignments)} WHERE user_id = ?",
                params,
            )
        settings_row = self.get_user_settings(user_id)
        if not settings_row:
            raise KeyError(user_id)
        return settings_row

    def get_user_notification_settings(self, user_id: int) -> Optional[dict[str, Any]]:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM user_notification_settings WHERE user_id = ?",
                (user_id,),
            ).fetchone()
        return _notification_row_to_dict(row) if row else None

    def update_user_notification_settings(self, user_id: int, values: dict[str, Any]) -> dict[str, Any]:
        now = now_iso()
        assignments = []
        params: list[Any] = []
        for column, value in values.items():
            assignments.append(f"{column} = ?")
            params.append(value)
        assignments.append("updated_at = ?")
        params.append(now)
        params.append(user_id)
        with self.connect() as connection:
            connection.execute(
                f"UPDATE user_notification_settings SET {', '.join(assignments)} WHERE user_id = ?",
                params,
            )
        notification_row = self.get_user_notification_settings(user_id)
        if not notification_row:
            raise KeyError(user_id)
        return notification_row

    def list_user_repair_value_overrides(self, user_id: int) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM user_repair_value_overrides
                WHERE user_id = ?
                ORDER BY model ASC, part ASC, id ASC
                """,
                (user_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def upsert_user_repair_value_override(
        self,
        user_id: int,
        *,
        model: str,
        part: str,
        cost: float,
        source: str = "",
        note: str = "",
    ) -> dict[str, Any]:
        model = model.strip()
        part = part.strip()
        if not model:
            raise ValueError("model is required")
        if not part:
            raise ValueError("part is required")
        now = now_iso()
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO user_repair_value_overrides (
                    user_id, model, part, cost, source, note, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id, model, part) DO UPDATE SET
                    cost = excluded.cost,
                    source = excluded.source,
                    note = excluded.note,
                    updated_at = excluded.updated_at
                """,
                (
                    user_id,
                    model[:120],
                    part[:40],
                    round(float(cost), 2),
                    source[:80],
                    note[:500],
                    now,
                    now,
                ),
            )
            row = connection.execute(
                """
                SELECT *
                FROM user_repair_value_overrides
                WHERE user_id = ? AND model = ? AND part = ?
                LIMIT 1
                """,
                (user_id, model[:120], part[:40]),
            ).fetchone()
        if not row:
            raise KeyError(user_id)
        return dict(row)

    def delete_user_repair_value_override(self, user_id: int, override_id: int) -> None:
        with self.connect() as connection:
            cursor = connection.execute(
                "DELETE FROM user_repair_value_overrides WHERE user_id = ? AND id = ?",
                (user_id, override_id),
            )
        if cursor.rowcount == 0:
            raise KeyError(override_id)

    def list_user_resale_research_overrides(self, user_id: int) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM user_resale_research_overrides
                WHERE user_id = ?
                ORDER BY model ASC, storage_capacity ASC, condition ASC, id ASC
                """,
                (user_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def upsert_user_resale_research_override(
        self,
        user_id: int,
        *,
        model: str,
        storage_capacity: str,
        condition: str,
        low: float,
        mid: float,
        high: float,
        confidence: str = "",
        source: str = "",
        note: str = "",
    ) -> dict[str, Any]:
        model = model.strip()
        storage_capacity = storage_capacity.strip()
        condition = condition.strip().lower()
        if not model:
            raise ValueError("model is required")
        if not storage_capacity:
            raise ValueError("storage_capacity is required")
        if not condition:
            raise ValueError("condition is required")
        now = now_iso()
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO user_resale_research_overrides (
                    user_id, model, storage_capacity, condition, low, mid, high,
                    confidence, source, note, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id, model, storage_capacity, condition) DO UPDATE SET
                    low = excluded.low,
                    mid = excluded.mid,
                    high = excluded.high,
                    confidence = excluded.confidence,
                    source = excluded.source,
                    note = excluded.note,
                    updated_at = excluded.updated_at
                """,
                (
                    user_id,
                    model[:120],
                    storage_capacity[:20],
                    condition[:20],
                    round(float(low), 2),
                    round(float(mid), 2),
                    round(float(high), 2),
                    confidence[:40],
                    source[:80],
                    note[:500],
                    now,
                    now,
                ),
            )
            row = connection.execute(
                """
                SELECT *
                FROM user_resale_research_overrides
                WHERE user_id = ? AND model = ? AND storage_capacity = ? AND condition = ?
                LIMIT 1
                """,
                (user_id, model[:120], storage_capacity[:20], condition[:20]),
            ).fetchone()
        if not row:
            raise KeyError(user_id)
        return dict(row)

    def delete_user_resale_research_override(self, user_id: int, override_id: int) -> None:
        with self.connect() as connection:
            cursor = connection.execute(
                "DELETE FROM user_resale_research_overrides WHERE user_id = ? AND id = ?",
                (user_id, override_id),
            )
        if cursor.rowcount == 0:
            raise KeyError(override_id)

    def get_user_item_correction(self, user_id: int, item_id: str) -> Optional[dict[str, Any]]:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT
                    uic.id,
                    uic.user_id,
                    uic.corrected_model,
                    uic.corrected_storage_capacity,
                    uic.corrected_issue_type,
                    uic.corrected_part_cost,
                    uic.note,
                    uic.created_at,
                    uic.updated_at,
                    mi.marketplace_item_id AS item_id
                FROM user_item_corrections uic
                INNER JOIN marketplace_items mi ON mi.id = uic.marketplace_item_id
                WHERE uic.user_id = ? AND mi.marketplace_item_id = ?
                LIMIT 1
                """,
                (user_id, item_id),
            ).fetchone()
        return dict(row) if row else None

    def upsert_user_item_correction(
        self,
        user_id: int,
        item_id: str,
        *,
        corrected_model: str | None = None,
        corrected_storage_capacity: str | None = None,
        corrected_issue_type: str | None = None,
        corrected_part_cost: float | None = None,
        note: str = "",
        marketplace: str = "ebay",
    ) -> dict[str, Any]:
        item_id = str(item_id or "").strip()
        if not item_id:
            raise ValueError("item_id is required")
        corrected_model = (corrected_model or "").strip() or None
        corrected_storage_capacity = (corrected_storage_capacity or "").strip() or None
        corrected_issue_type = (corrected_issue_type or "").strip() or None
        note = note[:1000]
        now = now_iso()
        with self.connect() as connection:
            marketplace_row = connection.execute(
                "SELECT id FROM marketplace_items WHERE marketplace = ? AND marketplace_item_id = ? LIMIT 1",
                (marketplace, item_id),
            ).fetchone()
            if not marketplace_row:
                raise KeyError(item_id)
            connection.execute(
                """
                INSERT INTO user_item_corrections (
                    user_id, marketplace_item_id, corrected_model, corrected_storage_capacity,
                    corrected_issue_type, corrected_part_cost, note, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id, marketplace_item_id) DO UPDATE SET
                    corrected_model = excluded.corrected_model,
                    corrected_storage_capacity = excluded.corrected_storage_capacity,
                    corrected_issue_type = excluded.corrected_issue_type,
                    corrected_part_cost = excluded.corrected_part_cost,
                    note = excluded.note,
                    updated_at = excluded.updated_at
                """,
                (
                    user_id,
                    int(marketplace_row["id"]),
                    corrected_model[:120] if corrected_model else None,
                    corrected_storage_capacity[:20] if corrected_storage_capacity else None,
                    corrected_issue_type[:40] if corrected_issue_type else None,
                    round(float(corrected_part_cost), 2) if corrected_part_cost is not None else None,
                    note,
                    now,
                    now,
                ),
            )
        correction = self.get_user_item_correction(user_id, item_id)
        if not correction:
            raise KeyError(item_id)
        return correction

    def delete_user_item_correction(self, user_id: int, item_id: str, *, marketplace: str = "ebay") -> None:
        item_id = str(item_id or "").strip()
        if not item_id:
            raise ValueError("item_id is required")
        with self.connect() as connection:
            cursor = connection.execute(
                """
                DELETE FROM user_item_corrections
                WHERE user_id = ?
                  AND marketplace_item_id = (
                      SELECT id FROM marketplace_items WHERE marketplace = ? AND marketplace_item_id = ? LIMIT 1
                  )
                """,
                (user_id, marketplace, item_id),
            )
        if cursor.rowcount == 0:
            raise KeyError(item_id)

    def list_user_keywords(self, user_id: int) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM user_keywords WHERE user_id = ? ORDER BY is_baseline DESC, created_at ASC, id ASC",
                (user_id,),
            ).fetchall()
        return [_keyword_row_to_dict(row) for row in rows]

    def add_user_keyword(
        self,
        user_id: int,
        *,
        keyword: str,
        enabled: bool = True,
        is_baseline: bool = False,
    ) -> dict[str, Any]:
        keyword = keyword.strip()
        if not keyword:
            raise ValueError("keyword is required")
        now = now_iso()
        try:
            with self.connect() as connection:
                cursor = connection.execute(
                    """
                    INSERT INTO user_keywords (
                        user_id, keyword, enabled, is_baseline, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (user_id, keyword[:120], int(bool(enabled)), int(bool(is_baseline)), now, now),
                )
        except sqlite3.IntegrityError as exc:
            raise ValueError("That keyword already exists for this user") from exc
        keyword_row = self.get_user_keyword(user_id, int(cursor.lastrowid))
        if not keyword_row:
            raise KeyError(user_id)
        return keyword_row

    def get_user_keyword(self, user_id: int, keyword_id: int) -> Optional[dict[str, Any]]:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM user_keywords WHERE user_id = ? AND id = ?",
                (user_id, keyword_id),
            ).fetchone()
        return _keyword_row_to_dict(row) if row else None

    def update_user_keyword(
        self,
        user_id: int,
        keyword_id: int,
        *,
        keyword: str | None = None,
        enabled: bool | None = None,
    ) -> dict[str, Any]:
        current = self.get_user_keyword(user_id, keyword_id)
        if not current:
            raise KeyError(keyword_id)
        assignments = []
        params: list[Any] = []
        if keyword is not None:
            keyword = keyword.strip()
            if not keyword:
                raise ValueError("keyword is required")
            assignments.append("keyword = ?")
            params.append(keyword[:120])
        if enabled is not None:
            assignments.append("enabled = ?")
            params.append(int(bool(enabled)))
        assignments.append("updated_at = ?")
        params.append(now_iso())
        params.extend([user_id, keyword_id])
        try:
            with self.connect() as connection:
                connection.execute(
                    f"UPDATE user_keywords SET {', '.join(assignments)} WHERE user_id = ? AND id = ?",
                    params,
                )
        except sqlite3.IntegrityError as exc:
            raise ValueError("That keyword already exists for this user") from exc
        updated = self.get_user_keyword(user_id, keyword_id)
        if not updated:
            raise KeyError(keyword_id)
        return updated

    def delete_user_keyword(self, user_id: int, keyword_id: int) -> None:
        current = self.get_user_keyword(user_id, keyword_id)
        if not current:
            raise KeyError(keyword_id)
        if current.get("is_baseline"):
            raise ValueError("Baseline keywords cannot be deleted")
        with self.connect() as connection:
            connection.execute(
                "DELETE FROM user_keywords WHERE user_id = ? AND id = ?",
                (user_id, keyword_id),
            )

    def get_user_configuration_summary(self, user_id: int) -> dict[str, Any]:
        settings_row = self.get_user_settings(user_id)
        notifications = self.get_user_notification_settings(user_id)
        keywords = self.list_user_keywords(user_id)
        usage_summary = self.get_user_usage_summary(user_id)
        return {
            "has_settings": settings_row is not None,
            "has_notification_settings": notifications is not None,
            "discord_configured": bool(notifications and notifications.get("discord_webhook_configured")),
            "keyword_count": len(keywords),
            "usage_summary": usage_summary,
        }

    def ignored_match(self, listing: dict[str, Any], *, user_id: int | None = None) -> dict[str, str]:
        seller = (listing.get("seller_username") or "").strip()
        title = (listing.get("title") or "").lower()
        with self.connect() as connection:
            if seller and user_id is not None:
                row = connection.execute(
                    """
                    SELECT seller_username, reason
                    FROM user_ignored_sellers
                    WHERE user_id = ? AND lower(seller_username) = lower(?)
                    """,
                    (user_id, seller),
                ).fetchone()
                if row:
                    return {
                        "user_status": "ignored",
                        "ignored_at": now_iso(),
                        "ignored_seller": row["seller_username"],
                        "ignored_reason": row["reason"] or "Ignored seller",
                        "updated_by_user_at": now_iso(),
                    }
            elif seller:
                row = connection.execute(
                    "SELECT seller_username, reason FROM ignored_sellers WHERE lower(seller_username) = lower(?)",
                    (seller,),
                ).fetchone()
                if row:
                    return {
                        "user_status": "ignored",
                        "ignored_at": now_iso(),
                        "ignored_seller": row["seller_username"],
                        "ignored_reason": row["reason"] or "Ignored seller",
                        "updated_by_user_at": now_iso(),
                    }
            if user_id is not None:
                rows = connection.execute(
                    "SELECT keyword, reason FROM user_ignored_keywords WHERE user_id = ?",
                    (user_id,),
                ).fetchall()
            else:
                rows = connection.execute("SELECT keyword, reason FROM ignored_keywords").fetchall()
        for row in rows:
            keyword = row["keyword"]
            if keyword and keyword.lower() in title:
                return {
                    "user_status": "ignored",
                    "ignored_at": now_iso(),
                    "ignored_reason": row["reason"] or f"Ignored keyword: {keyword}",
                    "updated_by_user_at": now_iso(),
                }
        return {}

    def stats(
        self,
        *,
        max_alert_item_age_minutes: int = DEFAULT_MAX_ALERT_ITEM_AGE_MINUTES,
        max_priority_review_item_age_hours: int = DEFAULT_MAX_PRIORITY_REVIEW_ITEM_AGE_HOURS,
        max_active_queue_item_age_hours: int = DEFAULT_MAX_ACTIVE_QUEUE_ITEM_AGE_HOURS,
    ) -> dict[str, Any]:
        items = self.all_items(
            max_alert_item_age_minutes=max_alert_item_age_minutes,
            max_priority_review_item_age_hours=max_priority_review_item_age_hours,
            max_active_queue_item_age_hours=max_active_queue_item_age_hours,
        )
        counts: dict[str, int] = {}
        user_counts: dict[str, int] = {}
        for item in items:
            counts[item.get("status") or "unknown"] = counts.get(item.get("status") or "unknown", 0) + 1
            user_status = item.get("user_status") or "new"
            user_counts[user_status] = user_counts.get(user_status, 0) + 1

        today = datetime.now(timezone.utc).date()
        active_items = [item for item in items if not item["stale"] and item.get("user_status") not in {"ignored", "rejected"}]
        total = len(items)
        alerted = sum(1 for item in items if item.get("alerted_at"))
        best_finds = sum(1 for item in items if _is_best_find_item(item))
        latest = max((item.get("item_origin_at") or item.get("found_at") for item in items), default=None)
        candidate_profit = sum(
            float(item.get("estimated_profit") or 0)
            for item in active_items
            if item.get("status") == "candidate"
        )
        manual_review_total = sum(
            1
            for item in active_items
            if item.get("manual_review_needed") or item.get("status") == "risky"
        )
        action_needed = best_finds
        priority_review = sum(1 for item in items if _is_priority_review_item(item))
        needs_data = sum(1 for item in items if _is_needs_data_item(item))
        promoted = sum(1 for item in items if item.get("user_status") == "promoted" and item.get("promoted_at"))
        user_rejected = sum(1 for item in items if item.get("user_status") == "rejected")
        new_items = sum(1 for item in items if item.get("user_status") == "new")
        found_today = sum(1 for item in items if _item_date(item) == today)
        fresh_found = sum(1 for item in items if item.get("fresh_for_active_queue"))
        rejected_today = sum(
            1
            for item in items
            if item.get("status") == "rejected" and _item_date(item) == today
        )
        stale_items = sum(1 for item in items if item["stale"])
        return {
            "total": total,
            "by_status": counts,
            "by_user_status": user_counts,
            "alerted": alerted,
            "best_finds": best_finds,
            "latest_found_at": latest,
            "candidate_estimated_profit": round(float(candidate_profit or 0), 2),
            "action_needed": action_needed,
            "priority_review": priority_review,
            "needs_data": needs_data,
            "promoted": promoted,
            "user_rejected": user_rejected,
            "new_items": new_items,
            "manual_review_total": manual_review_total,
            "found_today": found_today,
            "fresh_found": fresh_found,
            "rejected_today": rejected_today,
            "stale_items": stale_items,
            "freshness": {
                "max_alert_item_age_minutes": max_alert_item_age_minutes,
                "max_priority_review_item_age_hours": max_priority_review_item_age_hours,
                "max_active_queue_item_age_hours": max_active_queue_item_age_hours,
            },
        }

    def marketplace_item_exists(self, item_id: str, *, marketplace: str = "ebay") -> bool:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM marketplace_items WHERE marketplace = ? AND marketplace_item_id = ? LIMIT 1",
                (marketplace, item_id),
            ).fetchone()
        return bool(row)

    def upsert_marketplace_item(self, item: dict[str, Any], *, marketplace: str = "ebay") -> dict[str, Any]:
        now = now_iso()
        with self.connect() as connection:
            return self._upsert_marketplace_item(connection, item, marketplace=marketplace, now=now)

    def upsert_user_item_state(self, user_id: int, item: dict[str, Any], *, marketplace: str = "ebay") -> None:
        now = now_iso()
        item_key = str(item.get("item_id") or item.get("marketplace_item_id") or "")
        if not item_key:
            raise ValueError("item_id is required")
        with self.connect() as connection:
            marketplace_row = connection.execute(
                "SELECT id FROM marketplace_items WHERE marketplace = ? AND marketplace_item_id = ? LIMIT 1",
                (marketplace, item_key),
            ).fetchone()
            if not marketplace_row:
                marketplace_row = self._upsert_marketplace_item(connection, item, marketplace=marketplace, now=now)
            self._upsert_user_item_state(
                connection,
                user_id,
                int(marketplace_row["id"]),
                item,
                now=now,
            )

    def upsert_user_item(self, user_id: int, item: dict[str, Any], *, marketplace: str = "ebay") -> None:
        now = now_iso()
        with self.connect() as connection:
            marketplace_row = self._upsert_marketplace_item(connection, item, marketplace=marketplace, now=now)
            self._upsert_user_item_state(
                connection,
                user_id,
                int(marketplace_row["id"]),
                item,
                now=now,
            )

    def get_user_item(
        self,
        user_id: int,
        item_id: str,
        *,
        max_alert_item_age_minutes: int = DEFAULT_MAX_ALERT_ITEM_AGE_MINUTES,
        max_priority_review_item_age_hours: int = DEFAULT_MAX_PRIORITY_REVIEW_ITEM_AGE_HOURS,
        max_active_queue_item_age_hours: int = DEFAULT_MAX_ACTIVE_QUEUE_ITEM_AGE_HOURS,
    ) -> Optional[dict[str, Any]]:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT
                    mi.marketplace AS marketplace,
                    mi.marketplace_item_id AS item_id,
                    mi.title AS title,
                    mi.price AS price,
                    mi.shipping AS shipping,
                    mi.total_cost AS total_cost,
                    mi.condition AS condition,
                    mi.item_url AS item_url,
                    mi.image_url AS image_url,
                    mi.seller_username AS seller_username,
                    mi.seller_feedback_percentage AS seller_feedback_percentage,
                    mi.seller_feedback_score AS seller_feedback_score,
                    mi.raw_description AS raw_description,
                    mi.item_origin_at AS item_origin_at,
                    mi.found_at AS found_at,
                    uis.updated_at AS updated_at,
                    mi.raw_json AS raw_json,
                    mi.availability_status AS availability_status,
                    mi.buying_option_summary AS buying_option_summary,
                    mi.item_end_at AS item_end_at,
                    mi.last_availability_checked_at AS last_availability_checked_at,
                    mi.availability_note AS availability_note,
                    uis.score AS score,
                    uis.status AS status,
                    uis.model AS model,
                    uis.resale_value AS resale_value,
                    uis.resale_low AS resale_low,
                    uis.resale_mid AS resale_mid,
                    uis.resale_high AS resale_high,
                    uis.profit_low AS profit_low,
                    uis.profit_mid AS profit_mid,
                    uis.profit_high AS profit_high,
                    uis.resale_confidence AS resale_confidence,
                    uis.resale_sample_size AS resale_sample_size,
                    uis.resale_note AS resale_note,
                    uis.resale_source AS resale_source,
                    uis.resale_market_source AS resale_market_source,
                    uis.resale_condition_used AS resale_condition_used,
                    uis.resale_storage_used AS resale_storage_used,
                    uis.storage_resale_warning AS storage_resale_warning,
                    uis.mint_resale_low AS mint_resale_low,
                    uis.mint_resale_mid AS mint_resale_mid,
                    uis.mint_resale_high AS mint_resale_high,
                    uis.mint_profit_low AS mint_profit_low,
                    uis.mint_profit_mid AS mint_profit_mid,
                    uis.mint_profit_high AS mint_profit_high,
                    uis.storage_capacity AS storage_capacity,
                    uis.storage_confidence AS storage_confidence,
                    uis.storage_source AS storage_source,
                    uis.estimated_parts_cost AS estimated_parts_cost,
                    uis.estimated_parts_cost_available AS estimated_parts_cost_available,
                    uis.risk_buffer AS risk_buffer,
                    uis.estimated_profit AS estimated_profit,
                    uis.estimated_profit_available AS estimated_profit_available,
                    uis.parts_pricing_status AS parts_pricing_status,
                    uis.parts_pricing_note AS parts_pricing_note,
                    uis.parts_pricing_label AS parts_pricing_label,
                    uis.pricing_warning AS pricing_warning,
                    uis.manual_review_allowed AS manual_review_allowed,
                    uis.whole_phone_confidence_passed AS whole_phone_confidence_passed,
                    uis.whole_phone_score AS whole_phone_score,
                    uis.has_repair_issue AS has_repair_issue,
                    uis.manual_review_needed AS manual_review_needed,
                    uis.manual_review_reason AS manual_review_reason,
                    uis.alert_eligible AS alert_eligible,
                    uis.listing_classification_flags AS listing_classification_flags,
                    uis.user_status AS user_status,
                    uis.user_note AS user_note,
                    uis.reviewed_at AS reviewed_at,
                    uis.ignored_at AS ignored_at,
                    uis.watched_at AS watched_at,
                    uis.promoted_at AS promoted_at,
                    uis.rejected_by_user_at AS rejected_by_user_at,
                    uis.user_reject_reason AS user_reject_reason,
                    uis.ignored_reason AS ignored_reason,
                    uis.ignored_seller AS ignored_seller,
                    uis.updated_by_user_at AS updated_by_user_at,
                    uis.hard_reject_flags AS hard_reject_flags,
                    uis.positive_flags AS positive_flags,
                    uis.risk_flags AS risk_flags,
                    uis.alerted_at AS alerted_at
                FROM user_item_states uis
                INNER JOIN marketplace_items mi ON mi.id = uis.marketplace_item_id
                WHERE uis.user_id = ? AND mi.marketplace_item_id = ?
                LIMIT 1
                """,
                (user_id, item_id),
            ).fetchone()
        return (
            _row_to_dict(
                _joined_item_row_dict(row),
                max_alert_item_age_minutes=max_alert_item_age_minutes,
                max_priority_review_item_age_hours=max_priority_review_item_age_hours,
                max_active_queue_item_age_hours=max_active_queue_item_age_hours,
            )
            if row
            else None
        )

    def list_user_items(
        self,
        user_id: int,
        *,
        status: Optional[str] = None,
        user_status: Optional[str] = None,
        limit: int = 100,
        include_ignored: bool = False,
        include_stale: bool = False,
        max_alert_item_age_minutes: int = DEFAULT_MAX_ALERT_ITEM_AGE_MINUTES,
        max_priority_review_item_age_hours: int = DEFAULT_MAX_PRIORITY_REVIEW_ITEM_AGE_HOURS,
        max_active_queue_item_age_hours: int = DEFAULT_MAX_ACTIVE_QUEUE_ITEM_AGE_HOURS,
    ) -> list[dict[str, Any]]:
        limit = max(1, min(limit, 500))
        clauses = ["uis.user_id = ?"]
        params: list[Any] = [user_id]
        if status:
            clauses.append("uis.status = ?")
            params.append(status)
        if user_status:
            clauses.append("uis.user_status = ?")
            params.append(user_status)
        if not include_ignored:
            clauses.append("uis.user_status != 'ignored'")
        with self.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT
                    mi.marketplace AS marketplace,
                    mi.marketplace_item_id AS item_id,
                    mi.title AS title,
                    mi.price AS price,
                    mi.shipping AS shipping,
                    mi.total_cost AS total_cost,
                    mi.condition AS condition,
                    mi.item_url AS item_url,
                    mi.image_url AS image_url,
                    mi.seller_username AS seller_username,
                    mi.seller_feedback_percentage AS seller_feedback_percentage,
                    mi.seller_feedback_score AS seller_feedback_score,
                    mi.raw_description AS raw_description,
                    mi.item_origin_at AS item_origin_at,
                    mi.found_at AS found_at,
                    uis.updated_at AS updated_at,
                    mi.raw_json AS raw_json,
                    mi.availability_status AS availability_status,
                    mi.buying_option_summary AS buying_option_summary,
                    mi.item_end_at AS item_end_at,
                    mi.last_availability_checked_at AS last_availability_checked_at,
                    mi.availability_note AS availability_note,
                    uis.score AS score,
                    uis.status AS status,
                    uis.model AS model,
                    uis.resale_value AS resale_value,
                    uis.resale_low AS resale_low,
                    uis.resale_mid AS resale_mid,
                    uis.resale_high AS resale_high,
                    uis.profit_low AS profit_low,
                    uis.profit_mid AS profit_mid,
                    uis.profit_high AS profit_high,
                    uis.resale_confidence AS resale_confidence,
                    uis.resale_sample_size AS resale_sample_size,
                    uis.resale_note AS resale_note,
                    uis.resale_source AS resale_source,
                    uis.resale_market_source AS resale_market_source,
                    uis.resale_condition_used AS resale_condition_used,
                    uis.resale_storage_used AS resale_storage_used,
                    uis.storage_resale_warning AS storage_resale_warning,
                    uis.mint_resale_low AS mint_resale_low,
                    uis.mint_resale_mid AS mint_resale_mid,
                    uis.mint_resale_high AS mint_resale_high,
                    uis.mint_profit_low AS mint_profit_low,
                    uis.mint_profit_mid AS mint_profit_mid,
                    uis.mint_profit_high AS mint_profit_high,
                    uis.storage_capacity AS storage_capacity,
                    uis.storage_confidence AS storage_confidence,
                    uis.storage_source AS storage_source,
                    uis.estimated_parts_cost AS estimated_parts_cost,
                    uis.estimated_parts_cost_available AS estimated_parts_cost_available,
                    uis.risk_buffer AS risk_buffer,
                    uis.estimated_profit AS estimated_profit,
                    uis.estimated_profit_available AS estimated_profit_available,
                    uis.parts_pricing_status AS parts_pricing_status,
                    uis.parts_pricing_note AS parts_pricing_note,
                    uis.parts_pricing_label AS parts_pricing_label,
                    uis.pricing_warning AS pricing_warning,
                    uis.manual_review_allowed AS manual_review_allowed,
                    uis.whole_phone_confidence_passed AS whole_phone_confidence_passed,
                    uis.whole_phone_score AS whole_phone_score,
                    uis.has_repair_issue AS has_repair_issue,
                    uis.manual_review_needed AS manual_review_needed,
                    uis.manual_review_reason AS manual_review_reason,
                    uis.alert_eligible AS alert_eligible,
                    uis.listing_classification_flags AS listing_classification_flags,
                    uis.user_status AS user_status,
                    uis.user_note AS user_note,
                    uis.reviewed_at AS reviewed_at,
                    uis.ignored_at AS ignored_at,
                    uis.watched_at AS watched_at,
                    uis.promoted_at AS promoted_at,
                    uis.rejected_by_user_at AS rejected_by_user_at,
                    uis.user_reject_reason AS user_reject_reason,
                    uis.ignored_reason AS ignored_reason,
                    uis.ignored_seller AS ignored_seller,
                    uis.updated_by_user_at AS updated_by_user_at,
                    uis.hard_reject_flags AS hard_reject_flags,
                    uis.positive_flags AS positive_flags,
                    uis.risk_flags AS risk_flags,
                    uis.alerted_at AS alerted_at
                FROM user_item_states uis
                INNER JOIN marketplace_items mi ON mi.id = uis.marketplace_item_id
                WHERE {' AND '.join(clauses)}
                ORDER BY COALESCE(mi.item_origin_at, mi.found_at) DESC
                LIMIT ?
                """,
                [*params, 500],
            ).fetchall()
        items = [
            _row_to_dict(
                _joined_item_row_dict(row),
                max_alert_item_age_minutes=max_alert_item_age_minutes,
                max_priority_review_item_age_hours=max_priority_review_item_age_hours,
                max_active_queue_item_age_hours=max_active_queue_item_age_hours,
            )
            for row in rows
        ]
        if not include_stale:
            items = [item for item in items if not item["stale"]]
        return items[:limit]

    def list_user_items_for_availability_refresh(
        self,
        user_id: int,
        *,
        limit: int = 25,
        max_alert_item_age_minutes: int = DEFAULT_MAX_ALERT_ITEM_AGE_MINUTES,
        max_priority_review_item_age_hours: int = DEFAULT_MAX_PRIORITY_REVIEW_ITEM_AGE_HOURS,
        max_active_queue_item_age_hours: int = DEFAULT_MAX_ACTIVE_QUEUE_ITEM_AGE_HOURS,
    ) -> list[dict[str, Any]]:
        items = self.list_user_items(
            user_id,
            limit=500,
            include_ignored=True,
            include_stale=True,
            max_alert_item_age_minutes=max_alert_item_age_minutes,
            max_priority_review_item_age_hours=max_priority_review_item_age_hours,
            max_active_queue_item_age_hours=max_active_queue_item_age_hours,
        )
        eligible = [item for item in items if _should_refresh_availability(item)]
        eligible.sort(key=lambda item: item.get("last_availability_checked_at") or "")
        return eligible[: max(1, min(limit, 100))]

    def set_user_item_status(self, user_id: int, item_id: str, user_status: str, *, ignored_reason: str = "") -> dict[str, Any]:
        if user_status not in USER_STATUSES:
            raise ValueError(f"Unsupported user_status={user_status}")
        timestamp_column = {
            "reviewed": "reviewed_at",
            "watched": "watched_at",
            "ignored": "ignored_at",
            "promoted": "promoted_at",
            "rejected": "rejected_by_user_at",
        }.get(user_status)
        now = now_iso()
        assignments = ["user_status = ?", "updated_by_user_at = ?", "updated_at = ?"]
        params: list[Any] = [user_status, now, now]
        if timestamp_column:
            assignments.append(f"{timestamp_column} = ?")
            params.append(now)
        if ignored_reason:
            assignments.append("ignored_reason = ?")
            params.append(ignored_reason[:300])
        if user_status == "rejected" and ignored_reason:
            assignments.append("user_reject_reason = ?")
            params.append(ignored_reason[:300])
        params.extend([user_id, item_id])
        with self.connect() as connection:
            connection.execute(
                f"""
                UPDATE user_item_states
                SET {', '.join(assignments)}
                WHERE user_id = ?
                  AND marketplace_item_id = (
                      SELECT id FROM marketplace_items WHERE marketplace_item_id = ? LIMIT 1
                  )
                """,
                params,
            )
        item = self.get_user_item(user_id, item_id)
        if not item:
            raise KeyError(item_id)
        return item

    def set_user_item_note(self, user_id: int, item_id: str, note: str) -> dict[str, Any]:
        now = now_iso()
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE user_item_states
                SET user_note = ?, updated_by_user_at = ?, updated_at = ?
                WHERE user_id = ?
                  AND marketplace_item_id = (
                      SELECT id FROM marketplace_items WHERE marketplace_item_id = ? LIMIT 1
                  )
                """,
                (note[:1000], now, now, user_id, item_id),
            )
        item = self.get_user_item(user_id, item_id)
        if not item:
            raise KeyError(item_id)
        return item

    def was_alerted_for_user(self, user_id: int, item_id: str) -> bool:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT uis.alerted_at
                FROM user_item_states uis
                INNER JOIN marketplace_items mi ON mi.id = uis.marketplace_item_id
                WHERE uis.user_id = ? AND mi.marketplace_item_id = ?
                LIMIT 1
                """,
                (user_id, item_id),
            ).fetchone()
        return bool(row and row["alerted_at"])

    def mark_alerted_for_user(self, user_id: int, item_id: str) -> None:
        now = now_iso()
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE user_item_states
                SET status = 'alerted', alerted_at = ?, updated_at = ?
                WHERE user_id = ?
                  AND marketplace_item_id = (
                      SELECT id FROM marketplace_items WHERE marketplace_item_id = ? LIMIT 1
                  )
                """,
                (now, now, user_id, item_id),
            )

    def _apply_user_ignored_seller(
        self,
        connection: sqlite3.Connection,
        user_id: int,
        seller_username: str,
        reason: str,
        now: str,
    ) -> None:
        marketplace_rows = connection.execute(
            """
            SELECT mi.id
            FROM marketplace_items mi
            INNER JOIN user_item_states uis ON uis.marketplace_item_id = mi.id
            WHERE uis.user_id = ? AND lower(mi.seller_username) = lower(?)
            """,
            (user_id, seller_username),
        ).fetchall()
        if not marketplace_rows:
            return
        placeholders = ",".join("?" for _ in marketplace_rows)
        marketplace_ids = [int(row["id"]) for row in marketplace_rows]
        connection.execute(
            f"""
            UPDATE user_item_states
            SET user_status = 'ignored',
                ignored_at = COALESCE(ignored_at, ?),
                ignored_seller = ?,
                ignored_reason = ?,
                updated_by_user_at = ?,
                updated_at = ?
            WHERE user_id = ? AND marketplace_item_id IN ({placeholders})
            """,
            [now, seller_username, reason or "Ignored seller", now, now, user_id, *marketplace_ids],
        )

    def _apply_user_ignored_keyword(
        self,
        connection: sqlite3.Connection,
        user_id: int,
        keyword: str,
        reason: str,
        now: str,
    ) -> None:
        marketplace_rows = connection.execute(
            """
            SELECT mi.id
            FROM marketplace_items mi
            INNER JOIN user_item_states uis ON uis.marketplace_item_id = mi.id
            WHERE uis.user_id = ? AND lower(mi.title) LIKE ?
            """,
            (user_id, f"%{keyword.lower()}%"),
        ).fetchall()
        if not marketplace_rows:
            return
        placeholders = ",".join("?" for _ in marketplace_rows)
        marketplace_ids = [int(row["id"]) for row in marketplace_rows]
        connection.execute(
            f"""
            UPDATE user_item_states
            SET user_status = 'ignored',
                ignored_at = COALESCE(ignored_at, ?),
                ignored_reason = ?,
                updated_by_user_at = ?,
                updated_at = ?
            WHERE user_id = ? AND marketplace_item_id IN ({placeholders})
            """,
            [now, reason or f"Ignored keyword: {keyword}", now, now, user_id, *marketplace_ids],
        )

    def create_shared_scan_run(
        self,
        *,
        mode: str,
        triggered_by_user_id: int | None,
        active_users: int,
        unique_searches: int,
    ) -> int:
        now = now_iso()
        with self.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO shared_scan_runs (
                    mode, status, triggered_by_user_id, active_users, unique_searches,
                    api_calls_made, total_items_returned, total_users_evaluated,
                    total_items_scored, total_alerts_sent, started_at, finished_at,
                    created_at, updated_at
                )
                VALUES (?, 'running', ?, ?, ?, 0, 0, 0, 0, 0, ?, NULL, ?, ?)
                """,
                (mode, triggered_by_user_id, active_users, unique_searches, now, now, now),
            )
        return int(cursor.lastrowid)

    def finish_shared_scan_run(
        self,
        scan_run_id: int,
        *,
        status: str,
        api_calls_made: int,
        total_items_returned: int,
        total_users_evaluated: int,
        total_items_scored: int,
        total_alerts_sent: int,
    ) -> None:
        now = now_iso()
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE shared_scan_runs
                SET status = ?,
                    api_calls_made = ?,
                    total_items_returned = ?,
                    total_users_evaluated = ?,
                    total_items_scored = ?,
                    total_alerts_sent = ?,
                    finished_at = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    status,
                    api_calls_made,
                    total_items_returned,
                    total_users_evaluated,
                    total_items_scored,
                    total_alerts_sent,
                    now,
                    now,
                    scan_run_id,
                ),
            )

    def record_shared_scan_search(
        self,
        scan_run_id: int,
        *,
        search_signature: str,
        keyword: str,
        limit_value: int,
        sort_order: str,
        marketplace_id: str | None,
        subscribed_user_count: int,
        items_returned: int,
        api_calls_made: int = 1,
        marketplace: str = "ebay",
    ) -> int:
        now = now_iso()
        with self.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO shared_scan_searches (
                    scan_run_id, search_signature, marketplace, keyword, limit_value,
                    sort_order, marketplace_id, subscribed_user_count, items_returned,
                    api_calls_made, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    scan_run_id,
                    search_signature,
                    marketplace,
                    keyword,
                    limit_value,
                    sort_order,
                    marketplace_id,
                    subscribed_user_count,
                    items_returned,
                    api_calls_made,
                    now,
                ),
            )
        return int(cursor.lastrowid)

    def record_shared_scan_results(
        self,
        scan_search_id: int,
        marketplace_item_ids: list[str],
        *,
        marketplace: str = "ebay",
    ) -> None:
        item_ids = [str(item_id).strip() for item_id in marketplace_item_ids if str(item_id).strip()]
        if not item_ids:
            return
        now = now_iso()
        with self.connect() as connection:
            connection.executemany(
                """
                INSERT INTO shared_scan_results (
                    scan_search_id, marketplace, marketplace_item_id, created_at
                )
                VALUES (?, ?, ?, ?)
                ON CONFLICT(scan_search_id, marketplace, marketplace_item_id) DO NOTHING
                """,
                [(scan_search_id, marketplace, item_id, now) for item_id in item_ids],
            )

    def create_scan_cycle(
        self,
        *,
        mode: str,
        user_id: int | None = None,
        process_id: int = 0,
        hostname: str = "",
        auth_required: bool = False,
        background_poll_enabled: bool = False,
        background_poll_seconds: int = 0,
        active_window_start: str | None = None,
        active_window_end: str | None = None,
        active_window_timezone: str = "",
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
        now = now_iso()
        with self.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO scan_cycles (
                    mode, user_id, started_at, finished_at, status, skip_reason, error_message,
                    source, error_category, http_status, cooldown_until, retry_after_seconds,
                    process_id, hostname, auth_required, background_poll_enabled,
                    background_poll_seconds, active_window_start, active_window_end,
                    active_window_timezone, users_considered, users_scanned,
                    keywords_searched, sources_checked, created_at, updated_at
                )
                VALUES (?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    mode[:40],
                    user_id,
                    now,
                    status[:20],
                    skip_reason[:500],
                    error_message[:1000],
                    source[:80] if source else None,
                    error_category[:120] if error_category else None,
                    int(http_status) if http_status is not None else None,
                    cooldown_until,
                    int(retry_after_seconds) if retry_after_seconds is not None else None,
                    int(process_id or 0),
                    hostname[:255],
                    int(bool(auth_required)),
                    int(bool(background_poll_enabled)),
                    int(background_poll_seconds or 0),
                    active_window_start,
                    active_window_end,
                    active_window_timezone[:80],
                    int(users_considered or 0),
                    int(users_scanned or 0),
                    json.dumps(keywords_searched or []),
                    json.dumps(sources_checked or []),
                    now,
                    now,
                ),
            )
        return int(cursor.lastrowid)

    def finish_scan_cycle(
        self,
        cycle_id: int,
        *,
        status: str,
        skip_reason: str = "",
        error_message: str = "",
        users_considered: int | None = None,
        users_scanned: int | None = None,
        keywords_searched: list[str] | None = None,
        sources_checked: list[str] | None = None,
        items_found: int = 0,
        new_items_found: int = 0,
        duplicate_items: int = 0,
        items_scored: int = 0,
        alerts_attempted: int = 0,
        alerts_sent: int = 0,
        alerts_failed: int = 0,
        final_bucket_counts: dict[str, int] | None = None,
        alert_block_reason_counts: dict[str, int] | None = None,
        missing_data_reason_counts: dict[str, int] | None = None,
        risk_flag_counts: dict[str, int] | None = None,
        model_detection_failure_count: int = 0,
        resale_missing_count: int = 0,
        parts_pricing_status_counts: dict[str, int] | None = None,
        source: str | None = None,
        error_category: str | None = None,
        http_status: int | None = None,
        cooldown_until: str | None = None,
        retry_after_seconds: int | None = None,
    ) -> None:
        now = now_iso()
        with self.connect() as connection:
            current = connection.execute(
                "SELECT users_considered, users_scanned, keywords_searched, sources_checked FROM scan_cycles WHERE id = ?",
                (cycle_id,),
            ).fetchone()
            if not current:
                return
            current_dict = dict(current)
            connection.execute(
                """
                UPDATE scan_cycles
                SET status = ?,
                    finished_at = ?,
                    skip_reason = ?,
                    error_message = ?,
                    source = ?,
                    error_category = ?,
                    http_status = ?,
                    cooldown_until = ?,
                    retry_after_seconds = ?,
                    users_considered = ?,
                    users_scanned = ?,
                    keywords_searched = ?,
                    sources_checked = ?,
                    items_found = ?,
                    new_items_found = ?,
                    duplicate_items = ?,
                    items_scored = ?,
                    alerts_attempted = ?,
                    alerts_sent = ?,
                    alerts_failed = ?,
                    final_bucket_counts = ?,
                    alert_block_reason_counts = ?,
                    missing_data_reason_counts = ?,
                    risk_flag_counts = ?,
                    model_detection_failure_count = ?,
                    resale_missing_count = ?,
                    parts_pricing_status_counts = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    status[:20],
                    now,
                    skip_reason[:500],
                    error_message[:1000],
                    source[:80] if source else None,
                    error_category[:120] if error_category else None,
                    int(http_status) if http_status is not None else None,
                    cooldown_until,
                    int(retry_after_seconds) if retry_after_seconds is not None else None,
                    int(users_considered if users_considered is not None else current_dict.get("users_considered") or 0),
                    int(users_scanned if users_scanned is not None else current_dict.get("users_scanned") or 0),
                    json.dumps(keywords_searched) if keywords_searched is not None else current_dict.get("keywords_searched") or "[]",
                    json.dumps(sources_checked) if sources_checked is not None else current_dict.get("sources_checked") or "[]",
                    int(items_found or 0),
                    int(new_items_found or 0),
                    int(duplicate_items or 0),
                    int(items_scored or 0),
                    int(alerts_attempted or 0),
                    int(alerts_sent or 0),
                    int(alerts_failed or 0),
                    json.dumps(final_bucket_counts or {}, sort_keys=True),
                    json.dumps(alert_block_reason_counts or {}, sort_keys=True),
                    json.dumps(missing_data_reason_counts or {}, sort_keys=True),
                    json.dumps(risk_flag_counts or {}, sort_keys=True),
                    int(model_detection_failure_count or 0),
                    int(resale_missing_count or 0),
                    json.dumps(parts_pricing_status_counts or {}, sort_keys=True),
                    now,
                    cycle_id,
                ),
            )

    def list_scan_cycles(self, *, limit: int = 50) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM scan_cycles
                ORDER BY started_at DESC, id DESC
                LIMIT ?
                """,
                (max(1, min(int(limit or 50), 500)),),
            ).fetchall()
        return [_scan_cycle_row_to_dict(row) for row in rows]

    def get_scan_cycle(self, cycle_id: int) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM scan_cycles WHERE id = ? LIMIT 1", (cycle_id,)).fetchone()
        return _scan_cycle_row_to_dict(row) if row else None

    def latest_successful_fresh_scan_cycle(self) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT *
                FROM scan_cycles
                WHERE status = 'completed'
                  AND mode != 'trace_replay'
                  AND items_scored > 0
                ORDER BY finished_at DESC, started_at DESC, id DESC
                LIMIT 1
                """
            ).fetchone()
        return _scan_cycle_row_to_dict(row) if row else None

    def latest_failed_or_skipped_scan_cycle(self) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT *
                FROM scan_cycles
                WHERE status IN ('failed', 'skipped')
                ORDER BY finished_at DESC, started_at DESC, id DESC
                LIMIT 1
                """
            ).fetchone()
        return _scan_cycle_row_to_dict(row) if row else None

    def update_worker_heartbeat(
        self,
        *,
        worker_name: str,
        process_id: int,
        hostname: str,
        status: str,
        started_at: str,
        last_cycle_id: int | None = None,
        last_error: str = "",
        next_wake_at: str | None = None,
    ) -> None:
        now = now_iso()
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO worker_heartbeats (
                    worker_name, process_id, hostname, started_at, last_seen_at, status,
                    last_cycle_id, last_error, next_wake_at, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(worker_name) DO UPDATE SET
                    process_id = excluded.process_id,
                    hostname = excluded.hostname,
                    last_seen_at = excluded.last_seen_at,
                    status = excluded.status,
                    last_cycle_id = excluded.last_cycle_id,
                    last_error = excluded.last_error,
                    next_wake_at = excluded.next_wake_at,
                    updated_at = excluded.updated_at
                """,
                (
                    worker_name[:120],
                    int(process_id or 0),
                    hostname[:255],
                    started_at,
                    now,
                    status[:20],
                    last_cycle_id,
                    last_error[:1000],
                    next_wake_at,
                    now,
                    now,
                ),
            )

    def get_worker_heartbeats(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM worker_heartbeats
                ORDER BY last_seen_at DESC, worker_name ASC
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def update_source_status(
        self,
        source: str,
        *,
        status: str,
        cooldown_until: str | None = None,
        last_success_at: str | None = None,
        last_failure_at: str | None = None,
        last_http_status: int | None = None,
        last_error_category: str = "",
        last_error_message: str = "",
        last_keyword: str = "",
        last_item_id: str = "",
        retry_after_seconds: int = 0,
    ) -> dict[str, Any]:
        now = now_iso()
        source = str(source or "").strip().lower() or "unknown"
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO source_statuses (
                    source, status, cooldown_until, last_success_at, last_failure_at,
                    last_http_status, last_error_category, last_error_message,
                    last_keyword, last_item_id, retry_after_seconds, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source) DO UPDATE SET
                    status = excluded.status,
                    cooldown_until = excluded.cooldown_until,
                    last_success_at = COALESCE(excluded.last_success_at, source_statuses.last_success_at),
                    last_failure_at = COALESCE(excluded.last_failure_at, source_statuses.last_failure_at),
                    last_http_status = COALESCE(excluded.last_http_status, source_statuses.last_http_status),
                    last_error_category = CASE
                        WHEN excluded.last_error_category != '' THEN excluded.last_error_category
                        ELSE source_statuses.last_error_category
                    END,
                    last_error_message = CASE
                        WHEN excluded.last_error_message != '' THEN excluded.last_error_message
                        ELSE source_statuses.last_error_message
                    END,
                    last_keyword = CASE
                        WHEN excluded.last_keyword != '' THEN excluded.last_keyword
                        ELSE source_statuses.last_keyword
                    END,
                    last_item_id = CASE
                        WHEN excluded.last_item_id != '' THEN excluded.last_item_id
                        ELSE source_statuses.last_item_id
                    END,
                    retry_after_seconds = CASE
                        WHEN excluded.retry_after_seconds != 0 THEN excluded.retry_after_seconds
                        ELSE source_statuses.retry_after_seconds
                    END,
                    updated_at = excluded.updated_at
                """,
                (
                    source[:80],
                    status[:40],
                    cooldown_until,
                    last_success_at,
                    last_failure_at,
                    int(last_http_status) if last_http_status is not None else None,
                    last_error_category[:120],
                    last_error_message[:1000],
                    last_keyword[:255],
                    last_item_id[:120],
                    int(retry_after_seconds or 0),
                    now,
                    now,
                ),
            )
        return self.get_source_status(source) or {}

    def get_source_status(self, source: str) -> dict[str, Any] | None:
        source = str(source or "").strip().lower()
        if not source:
            return None
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM source_statuses WHERE source = ? LIMIT 1", (source,)).fetchone()
        return dict(row) if row else None

    def list_source_statuses(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM source_statuses
                ORDER BY source ASC
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def record_listing_decision_trace(
        self,
        *,
        user_id: int,
        item_id: str,
        scan_cycle_id: int,
        trace: dict[str, Any],
        marketplace: str = "ebay",
    ) -> None:
        now = now_iso()
        with self.connect() as connection:
            item = connection.execute(
                "SELECT id FROM marketplace_items WHERE marketplace = ? AND marketplace_item_id = ? LIMIT 1",
                (marketplace, str(item_id)),
            ).fetchone()
            if not item:
                return
            connection.execute(
                """
                INSERT INTO listing_decision_traces (
                    user_id, marketplace_item_id, scan_cycle_id, trace_json, created_at
                )
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(user_id, marketplace_item_id, scan_cycle_id) DO UPDATE SET
                    trace_json = excluded.trace_json,
                    created_at = excluded.created_at
                """,
                (
                    int(user_id),
                    int(item["id"]),
                    int(scan_cycle_id),
                    json.dumps(trace, sort_keys=True),
                    now,
                ),
            )

    def list_decision_traces_for_cycle(self, cycle_id: int, *, limit: int = 500) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT ldt.*, mi.marketplace, mi.marketplace_item_id, mi.title
                FROM listing_decision_traces ldt
                INNER JOIN marketplace_items mi ON mi.id = ldt.marketplace_item_id
                WHERE ldt.scan_cycle_id = ?
                ORDER BY ldt.created_at DESC, ldt.id DESC
                LIMIT ?
                """,
                (cycle_id, max(1, min(int(limit or 500), 1000))),
            ).fetchall()
        return [_decision_trace_row_to_dict(row) for row in rows]

    def list_decision_traces_for_item(self, item_id: str, *, limit: int = 100) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT ldt.*, mi.marketplace, mi.marketplace_item_id, mi.title
                FROM listing_decision_traces ldt
                INNER JOIN marketplace_items mi ON mi.id = ldt.marketplace_item_id
                WHERE mi.marketplace_item_id = ?
                ORDER BY ldt.created_at DESC, ldt.id DESC
                LIMIT ?
                """,
                (str(item_id), max(1, min(int(limit or 100), 500))),
            ).fetchall()
        return [_decision_trace_row_to_dict(row) for row in rows]

    def record_user_usage_daily(
        self,
        user_id: int,
        *,
        usage_date: str | None = None,
        search_signatures_subscribed: int = 0,
        items_scored: int = 0,
        alerts_sent: int = 0,
        detail_refreshes: int = 0,
        shared_api_calls_attributed: float = 0.0,
        usage_weight: float = 0.0,
    ) -> None:
        day = usage_date or datetime.now(timezone.utc).date().isoformat()
        now = now_iso()
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO user_usage_daily (
                    user_id, usage_date, search_signatures_subscribed, items_scored,
                    alerts_sent, detail_refreshes, shared_api_calls_attributed,
                    usage_weight, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id, usage_date) DO UPDATE SET
                    search_signatures_subscribed = user_usage_daily.search_signatures_subscribed + excluded.search_signatures_subscribed,
                    items_scored = user_usage_daily.items_scored + excluded.items_scored,
                    alerts_sent = user_usage_daily.alerts_sent + excluded.alerts_sent,
                    detail_refreshes = user_usage_daily.detail_refreshes + excluded.detail_refreshes,
                    shared_api_calls_attributed = user_usage_daily.shared_api_calls_attributed + excluded.shared_api_calls_attributed,
                    usage_weight = user_usage_daily.usage_weight + excluded.usage_weight,
                    updated_at = excluded.updated_at
                """,
                (
                    user_id,
                    day,
                    int(search_signatures_subscribed),
                    int(items_scored),
                    int(alerts_sent),
                    int(detail_refreshes),
                    float(shared_api_calls_attributed),
                    float(usage_weight),
                    now,
                    now,
                ),
            )

    def get_user_usage_summary(self, user_id: int) -> dict[str, Any]:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT
                    COALESCE(SUM(search_signatures_subscribed), 0) AS search_signatures_subscribed,
                    COALESCE(SUM(items_scored), 0) AS items_scored,
                    COALESCE(SUM(alerts_sent), 0) AS alerts_sent,
                    COALESCE(SUM(detail_refreshes), 0) AS detail_refreshes,
                    COALESCE(SUM(shared_api_calls_attributed), 0) AS shared_api_calls_attributed,
                    COALESCE(SUM(usage_weight), 0) AS usage_weight,
                    MAX(updated_at) AS last_evaluated_at,
                    MAX(usage_date) AS latest_usage_date
                FROM user_usage_daily
                WHERE user_id = ?
                """,
                (user_id,),
            ).fetchone()
        return {
            "search_signatures_subscribed": int(row["search_signatures_subscribed"] or 0),
            "items_scored": int(row["items_scored"] or 0),
            "alerts_sent": int(row["alerts_sent"] or 0),
            "detail_refreshes": int(row["detail_refreshes"] or 0),
            "shared_api_calls_attributed": float(row["shared_api_calls_attributed"] or 0),
            "usage_weight": float(row["usage_weight"] or 0),
            "last_evaluated_at": row["last_evaluated_at"],
            "latest_usage_date": row["latest_usage_date"],
        }

    def list_user_usage_daily(self, user_id: int, *, limit: int = 30) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM user_usage_daily
                WHERE user_id = ?
                ORDER BY usage_date DESC
                LIMIT ?
                """,
                (user_id, max(1, min(limit, 90))),
            ).fetchall()
        return [dict(row) for row in rows]

    def latest_shared_scan_stats(self) -> dict[str, Any]:
        with self.connect() as connection:
            run = connection.execute(
                """
                SELECT *
                FROM shared_scan_runs
                ORDER BY COALESCE(finished_at, started_at) DESC, id DESC
                LIMIT 1
                """
            ).fetchone()
            active_users = connection.execute(
                "SELECT COUNT(*) AS count FROM users WHERE account_status = 'active'"
            ).fetchone()["count"]
        if not run:
            return {
                "last_shared_scan_time": None,
                "active_users": int(active_users),
                "unique_searches": 0,
                "shared_api_calls": 0,
                "total_items_ingested": 0,
                "alerts_sent": 0,
                "status": "never_run",
            }
        payload = dict(run)
        return {
            "last_shared_scan_time": payload.get("finished_at") or payload.get("started_at"),
            "active_users": int(active_users),
            "unique_searches": int(payload.get("unique_searches") or 0),
            "shared_api_calls": int(payload.get("api_calls_made") or 0),
            "total_items_ingested": int(payload.get("total_items_returned") or 0),
            "alerts_sent": int(payload.get("total_alerts_sent") or 0),
            "status": payload.get("status") or "unknown",
            "mode": payload.get("mode") or "shared",
            "started_at": payload.get("started_at"),
            "finished_at": payload.get("finished_at"),
            "total_users_evaluated": int(payload.get("total_users_evaluated") or 0),
            "total_items_scored": int(payload.get("total_items_scored") or 0),
            "worker_status": payload.get("status") or "unknown",
        }

    def stats_for_user(
        self,
        user_id: int,
        *,
        max_alert_item_age_minutes: int = DEFAULT_MAX_ALERT_ITEM_AGE_MINUTES,
        max_priority_review_item_age_hours: int = DEFAULT_MAX_PRIORITY_REVIEW_ITEM_AGE_HOURS,
        max_active_queue_item_age_hours: int = DEFAULT_MAX_ACTIVE_QUEUE_ITEM_AGE_HOURS,
    ) -> dict[str, Any]:
        items = self.list_user_items(
            user_id,
            limit=500,
            include_ignored=True,
            include_stale=True,
            max_alert_item_age_minutes=max_alert_item_age_minutes,
            max_priority_review_item_age_hours=max_priority_review_item_age_hours,
            max_active_queue_item_age_hours=max_active_queue_item_age_hours,
        )
        counts: dict[str, int] = {}
        user_counts: dict[str, int] = {}
        for item in items:
            counts[item.get("status") or "unknown"] = counts.get(item.get("status") or "unknown", 0) + 1
            next_user_status = item.get("user_status") or "new"
            user_counts[next_user_status] = user_counts.get(next_user_status, 0) + 1

        today = datetime.now(timezone.utc).date()
        active_items = [item for item in items if not item["stale"] and item.get("user_status") not in {"ignored", "rejected"}]
        total = len(items)
        alerted = sum(1 for item in items if item.get("alerted_at"))
        best_finds = sum(1 for item in items if _is_best_find_item(item))
        latest = max((item.get("item_origin_at") or item.get("found_at") for item in items), default=None)
        candidate_profit = sum(float(item.get("estimated_profit") or 0) for item in active_items if item.get("status") == "candidate")
        manual_review_total = sum(1 for item in active_items if item.get("manual_review_needed") or item.get("status") == "risky")
        action_needed = best_finds
        priority_review = sum(1 for item in items if _is_priority_review_item(item))
        needs_data = sum(1 for item in items if _is_needs_data_item(item))
        promoted = sum(1 for item in items if item.get("user_status") == "promoted" and item.get("promoted_at"))
        user_rejected = sum(1 for item in items if item.get("user_status") == "rejected")
        new_items = sum(1 for item in items if item.get("user_status") == "new")
        found_today = sum(1 for item in items if _item_date(item) == today)
        fresh_found = sum(1 for item in items if item.get("fresh_for_active_queue"))
        rejected_today = sum(1 for item in items if item.get("status") == "rejected" and _item_date(item) == today)
        stale_items = sum(1 for item in items if item["stale"])
        return {
            "total": total,
            "by_status": counts,
            "by_user_status": user_counts,
            "alerted": alerted,
            "best_finds": best_finds,
            "latest_found_at": latest,
            "candidate_estimated_profit": round(float(candidate_profit or 0), 2),
            "action_needed": action_needed,
            "priority_review": priority_review,
            "needs_data": needs_data,
            "promoted": promoted,
            "user_rejected": user_rejected,
            "new_items": new_items,
            "manual_review_total": manual_review_total,
            "found_today": found_today,
            "fresh_found": fresh_found,
            "rejected_today": rejected_today,
            "stale_items": stale_items,
            "freshness": {
                "max_alert_item_age_minutes": max_alert_item_age_minutes,
                "max_priority_review_item_age_hours": max_priority_review_item_age_hours,
                "max_active_queue_item_age_hours": max_active_queue_item_age_hours,
            },
        }

    def update_marketplace_item(self, item_id: str, fields: dict[str, Any], *, marketplace: str = "ebay") -> None:
        with self.connect() as connection:
            current = connection.execute(
                "SELECT * FROM marketplace_items WHERE marketplace = ? AND marketplace_item_id = ? LIMIT 1",
                (marketplace, item_id),
            ).fetchone()
            if not current:
                return
            merged = {**dict(current), **fields, "item_id": item_id}
            self._upsert_marketplace_item(connection, merged, marketplace=marketplace, now=now_iso())

    def get_marketplace_item(self, item_id: str, *, marketplace: str = "ebay") -> Optional[dict[str, Any]]:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM marketplace_items WHERE marketplace = ? AND marketplace_item_id = ? LIMIT 1",
                (marketplace, item_id),
            ).fetchone()
        return dict(row) if row else None

    def _migrate_legacy_items_to_split(self, connection: sqlite3.Connection) -> None:
        legacy_total = connection.execute("SELECT COUNT(*) AS count FROM items").fetchone()["count"]
        split_total = connection.execute("SELECT COUNT(*) AS count FROM user_item_states").fetchone()["count"]
        if not legacy_total or split_total:
            return
        user_id = self._ensure_split_migration_user(connection)
        rows = connection.execute("SELECT * FROM items ORDER BY found_at ASC, item_id ASC").fetchall()
        for row in rows:
            legacy_item = _row_to_dict(dict(row))
            marketplace_row = self._upsert_marketplace_item(connection, legacy_item, marketplace="ebay", now=legacy_item.get("updated_at") or now_iso())
            self._upsert_user_item_state(
                connection,
                user_id,
                int(marketplace_row["id"]),
                legacy_item,
                now=legacy_item.get("updated_at") or now_iso(),
                preserve_existing=False,
                created_at=legacy_item.get("found_at") or legacy_item.get("updated_at") or now_iso(),
            )

    def _migrate_legacy_ignores_to_user(self, connection: sqlite3.Connection) -> None:
        legacy_seller_rows = connection.execute(
            "SELECT seller_username, reason, created_at FROM ignored_sellers ORDER BY created_at ASC, seller_username ASC"
        ).fetchall()
        legacy_keyword_rows = connection.execute(
            "SELECT keyword, reason, created_at FROM ignored_keywords ORDER BY created_at ASC, keyword ASC"
        ).fetchall()
        if not legacy_seller_rows and not legacy_keyword_rows:
            return
        user_id = self._ensure_split_migration_user(connection)
        now = now_iso()
        for row in legacy_seller_rows:
            created_at = row["created_at"] or now
            connection.execute(
                """
                INSERT INTO user_ignored_sellers (
                    user_id, seller_username, reason, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(user_id, seller_username) DO UPDATE SET
                    reason = excluded.reason,
                    updated_at = excluded.updated_at
                """,
                (user_id, row["seller_username"], (row["reason"] or "")[:300], created_at, now),
            )
            self._apply_user_ignored_seller(
                connection,
                user_id,
                row["seller_username"],
                (row["reason"] or "")[:300],
                now,
            )
        for row in legacy_keyword_rows:
            created_at = row["created_at"] or now
            connection.execute(
                """
                INSERT INTO user_ignored_keywords (
                    user_id, keyword, reason, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(user_id, keyword) DO UPDATE SET
                    reason = excluded.reason,
                    updated_at = excluded.updated_at
                """,
                (user_id, row["keyword"], (row["reason"] or "")[:300], created_at, now),
            )
            self._apply_user_ignored_keyword(
                connection,
                user_id,
                row["keyword"],
                (row["reason"] or "")[:300],
                now,
            )

    def _ensure_split_migration_user(self, connection: sqlite3.Connection) -> int:
        row = connection.execute(
            """
            SELECT id
            FROM users
            WHERE account_status = 'active'
            ORDER BY CASE WHEN role = 'admin' THEN 0 ELSE 1 END, created_at ASC, id ASC
            LIMIT 1
            """
        ).fetchone()
        if row:
            return int(row["id"])
        now = now_iso()
        cursor = connection.execute(
            """
            INSERT INTO users (
                email, password_hash, role, account_status, display_name,
                plan_name, monthly_price, billing_status, paid_until, billing_note, created_at,
                updated_at, last_login_at
            )
            VALUES (?, ?, 'admin', 'active', 'Local User', '', 0, '', NULL, '', ?, ?, NULL)
            """,
            (LOCAL_FALLBACK_USER_EMAIL, "legacy-migration-placeholder", now, now),
        )
        self._create_default_user_records(connection, int(cursor.lastrowid))
        return int(cursor.lastrowid)

    def _upsert_marketplace_item(
        self,
        connection: sqlite3.Connection,
        item: dict[str, Any],
        *,
        marketplace: str,
        now: str,
    ) -> dict[str, Any]:
        payload = {
            "marketplace": marketplace,
            "marketplace_item_id": str(item.get("item_id") or item.get("marketplace_item_id") or ""),
            "title": item.get("title") or "",
            "price": item.get("price") or 0,
            "shipping": item.get("shipping") or 0,
            "total_cost": item.get("total_cost") or 0,
            "condition": item.get("condition"),
            "item_url": item.get("item_url"),
            "image_url": item.get("image_url"),
            "seller_username": item.get("seller_username"),
            "seller_feedback_percentage": item.get("seller_feedback_percentage"),
            "seller_feedback_score": item.get("seller_feedback_score"),
            "raw_description": item.get("raw_description"),
            "item_origin_at": item.get("item_origin_at") or item.get("found_at") or now,
            "item_creation_at": item.get("item_creation_at"),
            "found_at": item.get("found_at") or item.get("item_origin_at") or now,
            "updated_at": now,
            "raw_json": json.dumps(item.get("raw_json", {})),
            "availability_status": item.get("availability_status") or "unknown",
            "buying_option_summary": item.get("buying_option_summary") or "unknown",
            "item_end_at": item.get("item_end_at"),
            "last_availability_checked_at": item.get("last_availability_checked_at"),
            "availability_note": item.get("availability_note") or "",
        }
        connection.execute(
            """
            INSERT INTO marketplace_items (
                marketplace, marketplace_item_id, title, price, shipping, total_cost,
                condition, item_url, image_url, seller_username, seller_feedback_percentage,
                seller_feedback_score, raw_description, item_origin_at, item_creation_at,
                found_at, updated_at, raw_json, availability_status, buying_option_summary,
                item_end_at, last_availability_checked_at, availability_note
            )
            VALUES (
                :marketplace, :marketplace_item_id, :title, :price, :shipping, :total_cost,
                :condition, :item_url, :image_url, :seller_username, :seller_feedback_percentage,
                :seller_feedback_score, :raw_description, :item_origin_at, :item_creation_at,
                :found_at, :updated_at, :raw_json, :availability_status, :buying_option_summary,
                :item_end_at, :last_availability_checked_at, :availability_note
            )
            ON CONFLICT(marketplace, marketplace_item_id) DO UPDATE SET
                title = excluded.title,
                price = excluded.price,
                shipping = excluded.shipping,
                total_cost = excluded.total_cost,
                condition = excluded.condition,
                item_url = excluded.item_url,
                image_url = excluded.image_url,
                seller_username = excluded.seller_username,
                seller_feedback_percentage = excluded.seller_feedback_percentage,
                seller_feedback_score = excluded.seller_feedback_score,
                raw_description = excluded.raw_description,
                item_origin_at = COALESCE(excluded.item_origin_at, marketplace_items.item_origin_at, marketplace_items.found_at),
                item_creation_at = COALESCE(excluded.item_creation_at, marketplace_items.item_creation_at),
                found_at = COALESCE(marketplace_items.found_at, excluded.found_at),
                updated_at = excluded.updated_at,
                raw_json = excluded.raw_json,
                availability_status = excluded.availability_status,
                buying_option_summary = excluded.buying_option_summary,
                item_end_at = excluded.item_end_at,
                last_availability_checked_at = COALESCE(excluded.last_availability_checked_at, marketplace_items.last_availability_checked_at),
                availability_note = excluded.availability_note
            """,
            payload,
        )
        row = connection.execute(
            "SELECT * FROM marketplace_items WHERE marketplace = ? AND marketplace_item_id = ? LIMIT 1",
            (marketplace, payload["marketplace_item_id"]),
        ).fetchone()
        if not row:
            raise KeyError(payload["marketplace_item_id"])
        return dict(row)

    def _upsert_user_item_state(
        self,
        connection: sqlite3.Connection,
        user_id: int,
        marketplace_row_id: int,
        item: dict[str, Any],
        *,
        now: str,
        preserve_existing: bool = True,
        created_at: str | None = None,
    ) -> None:
        state_created_at = created_at or item.get("found_at") or now
        payload = {
            "user_id": user_id,
            "marketplace_item_id": marketplace_row_id,
            "score": item.get("score") or 0,
            "status": item.get("status") or "risky",
            "model": item.get("model") or "unknown",
            "resale_value": item.get("resale_value") or 0,
            "resale_low": item.get("resale_low") or 0,
            "resale_mid": item.get("resale_mid") or item.get("resale_value") or 0,
            "resale_high": item.get("resale_high") or 0,
            "profit_low": item.get("profit_low") or 0,
            "profit_mid": item.get("profit_mid") or item.get("estimated_profit") or 0,
            "profit_high": item.get("profit_high") or 0,
            "resale_confidence": item.get("resale_confidence") or "",
            "resale_sample_size": item.get("resale_sample_size") or 0,
            "resale_note": item.get("resale_note") or "",
            "resale_source": item.get("resale_source") or "missing",
            "resale_market_source": item.get("resale_market_source") or "missing",
            "resale_condition_used": item.get("resale_condition_used") or "",
            "resale_storage_used": item.get("resale_storage_used"),
            "storage_resale_warning": item.get("storage_resale_warning") or "",
            "mint_resale_low": item.get("mint_resale_low") or 0,
            "mint_resale_mid": item.get("mint_resale_mid") or 0,
            "mint_resale_high": item.get("mint_resale_high") or 0,
            "mint_profit_low": item.get("mint_profit_low") or 0,
            "mint_profit_mid": item.get("mint_profit_mid") or 0,
            "mint_profit_high": item.get("mint_profit_high") or 0,
            "storage_capacity": item.get("storage_capacity"),
            "storage_confidence": item.get("storage_confidence") or "",
            "storage_source": item.get("storage_source") or "",
            "estimated_parts_cost": item.get("estimated_parts_cost") or 0,
            "estimated_parts_cost_available": int(bool(item.get("estimated_parts_cost_available", True))),
            "risk_buffer": item.get("risk_buffer") or 0,
            "estimated_profit": item.get("estimated_profit") or 0,
            "estimated_profit_available": int(bool(item.get("estimated_profit_available", True))),
            "parts_pricing_status": item.get("parts_pricing_status") or "fallback",
            "parts_pricing_note": item.get("parts_pricing_note") or "",
            "parts_pricing_label": item.get("parts_pricing_label") or "Parts estimate not verified",
            "pricing_warning": item.get("pricing_warning") or "Parts estimate not verified",
            "manual_review_allowed": int(bool(item.get("manual_review_allowed", False))),
            "whole_phone_confidence_passed": int(bool(item.get("whole_phone_confidence_passed", False))),
            "whole_phone_score": item.get("whole_phone_score") or 0,
            "has_repair_issue": int(bool(item.get("has_repair_issue", False))),
            "manual_review_needed": int(bool(item.get("manual_review_needed", False))),
            "manual_review_reason": item.get("manual_review_reason") or "",
            "alert_eligible": int(bool(item.get("alert_eligible", False))),
            "listing_classification_flags": json.dumps(item.get("listing_classification_flags", [])),
            "user_status": item.get("user_status") or "new",
            "user_note": item.get("user_note") or "",
            "reviewed_at": item.get("reviewed_at"),
            "ignored_at": item.get("ignored_at"),
            "watched_at": item.get("watched_at"),
            "promoted_at": item.get("promoted_at"),
            "rejected_by_user_at": item.get("rejected_by_user_at"),
            "user_reject_reason": item.get("user_reject_reason") or "",
            "ignored_reason": item.get("ignored_reason") or "",
            "ignored_seller": item.get("ignored_seller") or "",
            "updated_by_user_at": item.get("updated_by_user_at"),
            "hard_reject_flags": json.dumps(item.get("hard_reject_flags", [])),
            "positive_flags": json.dumps(item.get("positive_flags", [])),
            "risk_flags": json.dumps(item.get("risk_flags", [])),
            "alerted_at": item.get("alerted_at"),
            "promoted_notification_sent_at": item.get("promoted_notification_sent_at"),
            "created_at": state_created_at,
            "updated_at": now,
        }
        status_expr = "excluded.status" if not preserve_existing else """
                    CASE
                        WHEN excluded.status = 'rejected' THEN excluded.status
                        WHEN excluded.alert_eligible = 0 AND excluded.status != 'candidate' THEN excluded.status
                        WHEN user_item_states.alerted_at IS NOT NULL THEN user_item_states.status
                        ELSE excluded.status
                    END
                """
        user_status_expr = "excluded.user_status" if not preserve_existing else """
                    CASE
                        WHEN excluded.user_status != 'new' THEN excluded.user_status
                        ELSE user_item_states.user_status
                    END
                """
        user_note_expr = "excluded.user_note" if not preserve_existing else """
                    CASE
                        WHEN excluded.user_note != '' THEN excluded.user_note
                        ELSE user_item_states.user_note
                    END
                """
        user_reject_reason_expr = "excluded.user_reject_reason" if not preserve_existing else """
                    CASE
                        WHEN excluded.user_reject_reason != '' THEN excluded.user_reject_reason
                        ELSE user_item_states.user_reject_reason
                    END
                """
        ignored_reason_expr = "excluded.ignored_reason" if not preserve_existing else """
                    CASE
                        WHEN excluded.ignored_reason != '' THEN excluded.ignored_reason
                        ELSE user_item_states.ignored_reason
                    END
                """
        ignored_seller_expr = "excluded.ignored_seller" if not preserve_existing else """
                    CASE
                        WHEN excluded.ignored_seller != '' THEN excluded.ignored_seller
                        ELSE user_item_states.ignored_seller
                    END
                """
        connection.execute(
            f"""
            INSERT INTO user_item_states (
                user_id, marketplace_item_id, score, status, model, resale_value, resale_low, resale_mid,
                resale_high, profit_low, profit_mid, profit_high, resale_confidence, resale_sample_size,
                resale_note, resale_source, resale_market_source, resale_condition_used, resale_storage_used,
                storage_resale_warning, mint_resale_low, mint_resale_mid, mint_resale_high, mint_profit_low,
                mint_profit_mid, mint_profit_high, storage_capacity, storage_confidence, storage_source,
                estimated_parts_cost, estimated_parts_cost_available, risk_buffer, estimated_profit,
                estimated_profit_available, parts_pricing_status, parts_pricing_note, parts_pricing_label,
                pricing_warning, manual_review_allowed, whole_phone_confidence_passed, whole_phone_score,
                has_repair_issue, manual_review_needed, manual_review_reason, alert_eligible,
                listing_classification_flags, user_status, user_note, reviewed_at, ignored_at, watched_at,
                promoted_at, rejected_by_user_at, user_reject_reason, ignored_reason, ignored_seller,
                updated_by_user_at, hard_reject_flags, positive_flags, risk_flags, alerted_at,
                promoted_notification_sent_at, created_at, updated_at
            )
            VALUES (
                :user_id, :marketplace_item_id, :score, :status, :model, :resale_value, :resale_low, :resale_mid,
                :resale_high, :profit_low, :profit_mid, :profit_high, :resale_confidence, :resale_sample_size,
                :resale_note, :resale_source, :resale_market_source, :resale_condition_used, :resale_storage_used,
                :storage_resale_warning, :mint_resale_low, :mint_resale_mid, :mint_resale_high, :mint_profit_low,
                :mint_profit_mid, :mint_profit_high, :storage_capacity, :storage_confidence, :storage_source,
                :estimated_parts_cost, :estimated_parts_cost_available, :risk_buffer, :estimated_profit,
                :estimated_profit_available, :parts_pricing_status, :parts_pricing_note, :parts_pricing_label,
                :pricing_warning, :manual_review_allowed, :whole_phone_confidence_passed, :whole_phone_score,
                :has_repair_issue, :manual_review_needed, :manual_review_reason, :alert_eligible,
                :listing_classification_flags, :user_status, :user_note, :reviewed_at, :ignored_at, :watched_at,
                :promoted_at, :rejected_by_user_at, :user_reject_reason, :ignored_reason, :ignored_seller,
                :updated_by_user_at, :hard_reject_flags, :positive_flags, :risk_flags, :alerted_at,
                :promoted_notification_sent_at, :created_at, :updated_at
            )
            ON CONFLICT(user_id, marketplace_item_id) DO UPDATE SET
                score = excluded.score,
                status = {status_expr},
                model = excluded.model,
                resale_value = excluded.resale_value,
                resale_low = excluded.resale_low,
                resale_mid = excluded.resale_mid,
                resale_high = excluded.resale_high,
                profit_low = excluded.profit_low,
                profit_mid = excluded.profit_mid,
                profit_high = excluded.profit_high,
                resale_confidence = excluded.resale_confidence,
                resale_sample_size = excluded.resale_sample_size,
                resale_note = excluded.resale_note,
                resale_source = excluded.resale_source,
                resale_market_source = excluded.resale_market_source,
                resale_condition_used = excluded.resale_condition_used,
                resale_storage_used = excluded.resale_storage_used,
                storage_resale_warning = excluded.storage_resale_warning,
                mint_resale_low = excluded.mint_resale_low,
                mint_resale_mid = excluded.mint_resale_mid,
                mint_resale_high = excluded.mint_resale_high,
                mint_profit_low = excluded.mint_profit_low,
                mint_profit_mid = excluded.mint_profit_mid,
                mint_profit_high = excluded.mint_profit_high,
                storage_capacity = excluded.storage_capacity,
                storage_confidence = excluded.storage_confidence,
                storage_source = excluded.storage_source,
                estimated_parts_cost = excluded.estimated_parts_cost,
                estimated_parts_cost_available = excluded.estimated_parts_cost_available,
                risk_buffer = excluded.risk_buffer,
                estimated_profit = excluded.estimated_profit,
                estimated_profit_available = excluded.estimated_profit_available,
                parts_pricing_status = excluded.parts_pricing_status,
                parts_pricing_note = excluded.parts_pricing_note,
                parts_pricing_label = excluded.parts_pricing_label,
                pricing_warning = excluded.pricing_warning,
                manual_review_allowed = excluded.manual_review_allowed,
                whole_phone_confidence_passed = excluded.whole_phone_confidence_passed,
                whole_phone_score = excluded.whole_phone_score,
                has_repair_issue = excluded.has_repair_issue,
                manual_review_needed = excluded.manual_review_needed,
                manual_review_reason = excluded.manual_review_reason,
                alert_eligible = excluded.alert_eligible,
                listing_classification_flags = excluded.listing_classification_flags,
                user_status = {user_status_expr},
                user_note = {user_note_expr},
                reviewed_at = COALESCE(excluded.reviewed_at, user_item_states.reviewed_at),
                ignored_at = COALESCE(excluded.ignored_at, user_item_states.ignored_at),
                watched_at = COALESCE(excluded.watched_at, user_item_states.watched_at),
                promoted_at = COALESCE(excluded.promoted_at, user_item_states.promoted_at),
                rejected_by_user_at = COALESCE(excluded.rejected_by_user_at, user_item_states.rejected_by_user_at),
                user_reject_reason = {user_reject_reason_expr},
                ignored_reason = {ignored_reason_expr},
                ignored_seller = {ignored_seller_expr},
                updated_by_user_at = COALESCE(excluded.updated_by_user_at, user_item_states.updated_by_user_at),
                hard_reject_flags = excluded.hard_reject_flags,
                positive_flags = excluded.positive_flags,
                risk_flags = excluded.risk_flags,
                alerted_at = COALESCE(excluded.alerted_at, user_item_states.alerted_at),
                promoted_notification_sent_at = COALESCE(excluded.promoted_notification_sent_at, user_item_states.promoted_notification_sent_at),
                updated_at = excluded.updated_at
            """,
            payload,
        )


    def _create_default_user_records(
        self,
        connection: sqlite3.Connection,
        user_id: int,
        *,
        settings_seed: dict[str, Any] | None = None,
        baseline_keywords: list[str] | None = None,
    ) -> None:
        defaults = _default_user_settings_payload(settings_seed)
        now = now_iso()
        connection.execute(
            """
            INSERT INTO user_settings (
                user_id, min_score_to_alert, min_profit_to_alert, risky_score_min, risky_score_max,
                max_alert_item_age_minutes, max_priority_review_item_age_hours, max_active_queue_item_age_hours,
                default_resale_condition, allow_mint_for_alerts, target_min_model_generation,
                background_poll_enabled, background_poll_seconds, active_start, active_end,
                timezone, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO NOTHING
            """,
            (
                user_id,
                defaults["min_score_to_alert"],
                defaults["min_profit_to_alert"],
                defaults["risky_score_min"],
                defaults["risky_score_max"],
                defaults["max_alert_item_age_minutes"],
                defaults["max_priority_review_item_age_hours"],
                defaults["max_active_queue_item_age_hours"],
                defaults["default_resale_condition"],
                int(bool(defaults["allow_mint_for_alerts"])),
                defaults["target_min_model_generation"],
                int(bool(defaults["background_poll_enabled"])),
                defaults["background_poll_seconds"],
                defaults["active_start"],
                defaults["active_end"],
                defaults["timezone"],
                now,
                now,
            ),
        )
        # TODO: replace plaintext webhook storage with encrypted-at-rest secrets before hosted multi-user rollout.
        connection.execute(
            """
            INSERT INTO user_notification_settings (
                user_id, discord_webhook, discord_enabled, alerts_enabled, notify_best_finds,
                notify_priority_review, created_at, updated_at
            )
            VALUES (?, '', 0, 1, 1, 1, ?, ?)
            ON CONFLICT(user_id) DO NOTHING
            """,
            (user_id, now, now),
        )
        keywords = baseline_keywords if baseline_keywords is not None else list(DEFAULT_KEYWORDS)
        seen_keywords: set[str] = set()
        for keyword in keywords:
            normalized = keyword.strip()
            if not normalized:
                continue
            dedupe_key = normalized.lower()
            if dedupe_key in seen_keywords:
                continue
            seen_keywords.add(dedupe_key)
            connection.execute(
                """
                INSERT INTO user_keywords (
                    user_id, keyword, enabled, is_baseline, created_at, updated_at
                )
                VALUES (?, ?, 1, 1, ?, ?)
                ON CONFLICT(user_id, keyword) DO NOTHING
                """,
                (user_id, normalized[:120], now, now),
            )


SQLiteStorage = Storage


class _SQLAlchemyResultShim:
    def __init__(self, result: Any):
        self._result = result
        self.rowcount = getattr(result, "rowcount", 0)
        self.lastrowid = None

    def fetchone(self) -> Optional[dict[str, Any]]:
        row = self._result.fetchone()
        if row is None:
            return None
        mapping = getattr(row, "_mapping", row)
        return dict(mapping)

    def fetchall(self) -> list[dict[str, Any]]:
        rows = self._result.fetchall()
        return [dict(getattr(row, "_mapping", row)) for row in rows]


class _SQLAlchemyConnectionShim:
    def __init__(self, engine: Engine, *, connection: Connection | None = None):
        self._engine = engine
        self._connection = connection
        self._transaction: Any = None
        self._owns_connection = connection is None

    def __enter__(self) -> "_SQLAlchemyConnectionShim":
        if self._connection is None:
            self._connection = self._engine.connect()
            self._transaction = self._connection.begin()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._owns_connection and self._connection is not None:
            if self._transaction is not None:
                if exc_type is None:
                    self._transaction.commit()
                else:
                    self._transaction.rollback()
            self._connection.close()
        self._connection = None
        self._transaction = None

    def execute(self, sql: str, params: Any = None) -> _SQLAlchemyResultShim:
        if self._connection is None:
            raise RuntimeError("Connection is not open")
        statement_text = sql
        bound_params: Any = params
        if isinstance(params, (list, tuple)):
            statement_text, bound_params = _convert_qmark_sql(sql, list(params))
        try:
            result = self._connection.execute(sa.text(statement_text), bound_params or {})
        except SAIntegrityError as exc:
            raise sqlite3.IntegrityError(str(exc.orig or exc)) from exc
        return _SQLAlchemyResultShim(result)

    def executemany(self, sql: str, seq_of_params: list[Any] | tuple[Any, ...]) -> _SQLAlchemyResultShim:
        if self._connection is None:
            raise RuntimeError("Connection is not open")
        params_list = list(seq_of_params)
        if not params_list:
            result = self._connection.execute(sa.text("SELECT 1 WHERE 0 = 1"))
            return _SQLAlchemyResultShim(result)

        statement_text = sql
        bound_params: list[Any]
        first = params_list[0]
        if isinstance(first, (list, tuple)):
            converted_batches: list[dict[str, Any]] = []
            for params in params_list:
                converted_sql, converted_params = _convert_qmark_sql(sql, list(params))
                if converted_sql != statement_text and statement_text != sql:
                    raise RuntimeError("Inconsistent SQL conversion for executemany")
                statement_text = converted_sql
                converted_batches.append(converted_params)
            bound_params = converted_batches
        else:
            bound_params = params_list

        try:
            result = self._connection.execute(sa.text(statement_text), bound_params)
        except SAIntegrityError as exc:
            raise sqlite3.IntegrityError(str(exc.orig or exc)) from exc
        return _SQLAlchemyResultShim(result)


class PostgresStorage(Storage):
    def __init__(self, database_url: str):
        self.path = Path(":postgres:")
        self._memory_connection = None
        self.database_url = _normalize_database_url(database_url)
        self.engine = sa.create_engine(
            self.database_url,
            future=True,
            pool_pre_ping=True,
            pool_recycle=300,
        )
        self.init_db()

    def connect(self) -> _SQLAlchemyConnectionShim:
        return _SQLAlchemyConnectionShim(self.engine)

    def init_db(self) -> None:
        metadata.create_all(self.engine)

    def create_user(
        self,
        *,
        email: str,
        password_hash: str,
        role: str = "user",
        account_status: str = "active",
        display_name: str = "",
        plan_name: str = "",
        monthly_price: float = 0,
        billing_status: str = "",
        paid_until: str | None = None,
        billing_note: str = "",
        settings_seed: dict[str, Any] | None = None,
        baseline_keywords: list[str] | None = None,
    ) -> dict[str, Any]:
        role = role.strip().lower()
        account_status = account_status.strip().lower()
        email = email.strip().lower()
        billing_status = billing_status.strip().lower()
        if not email:
            raise ValueError("email is required")
        if role not in USER_ROLES:
            raise ValueError(f"Unsupported role={role}")
        if account_status not in ACCOUNT_STATUSES:
            raise ValueError(f"Unsupported account_status={account_status}")
        if billing_status not in ALLOWED_BILLING_STATUSES:
            raise ValueError(f"Unsupported billing_status={billing_status}")
        if not password_hash:
            raise ValueError("password_hash is required")
        now = now_iso()
        try:
            with self.engine.begin() as connection:
                user_id = int(
                    connection.execute(
                        users.insert()
                        .values(
                            email=email,
                            password_hash=password_hash,
                            role=role,
                            account_status=account_status,
                            display_name=display_name[:120],
                            plan_name=plan_name[:120],
                            monthly_price=round(float(monthly_price or 0), 2),
                            billing_status=billing_status[:80],
                            paid_until=paid_until,
                            billing_note=billing_note[:500],
                            created_at=now,
                            updated_at=now,
                            last_login_at=None,
                        )
                        .returning(users.c.id)
                    ).scalar_one()
                )
                self._create_default_user_records(
                    _SQLAlchemyConnectionShim(self.engine, connection=connection),
                    user_id,
                    settings_seed=settings_seed,
                    baseline_keywords=baseline_keywords,
                )
        except SAIntegrityError as exc:
            raise ValueError("A user with that email already exists") from exc
        user = self.get_user(user_id)
        if not user:
            raise KeyError(email)
        return user

    def add_user_keyword(
        self,
        user_id: int,
        *,
        keyword: str,
        enabled: bool = True,
        is_baseline: bool = False,
    ) -> dict[str, Any]:
        keyword = keyword.strip()
        if not keyword:
            raise ValueError("keyword is required")
        now = now_iso()
        try:
            with self.engine.begin() as connection:
                keyword_id = int(
                    connection.execute(
                        user_keywords.insert()
                        .values(
                            user_id=user_id,
                            keyword=keyword[:120],
                            enabled=int(bool(enabled)),
                            is_baseline=int(bool(is_baseline)),
                            created_at=now,
                            updated_at=now,
                        )
                        .returning(user_keywords.c.id)
                    ).scalar_one()
                )
        except SAIntegrityError as exc:
            raise ValueError("That keyword already exists for this user") from exc
        keyword_row = self.get_user_keyword(user_id, keyword_id)
        if not keyword_row:
            raise KeyError(user_id)
        return keyword_row

    def create_shared_scan_run(
        self,
        *,
        mode: str = "shared",
        triggered_by_user_id: int | None = None,
        active_users: int = 0,
        unique_searches: int = 0,
    ) -> int:
        now = now_iso()
        with self.engine.begin() as connection:
            return int(
                connection.execute(
                    shared_scan_runs.insert()
                    .values(
                        mode=mode[:20],
                        status="running",
                        triggered_by_user_id=triggered_by_user_id,
                        active_users=int(active_users),
                        unique_searches=int(unique_searches),
                        api_calls_made=0,
                        total_items_returned=0,
                        total_users_evaluated=0,
                        total_items_scored=0,
                        total_alerts_sent=0,
                        started_at=now,
                        finished_at=None,
                        created_at=now,
                        updated_at=now,
                    )
                    .returning(shared_scan_runs.c.id)
                ).scalar_one()
            )

    def record_shared_scan_search(
        self,
        scan_run_id: int,
        *,
        search_signature: str,
        keyword: str,
        limit_value: int,
        sort_order: str,
        marketplace: str = "ebay",
        marketplace_id: str | None = None,
        subscribed_user_count: int = 0,
        items_returned: int = 0,
        api_calls_made: int = 0,
    ) -> int:
        now = now_iso()
        with self.engine.begin() as connection:
            return int(
                connection.execute(
                    shared_scan_searches.insert()
                    .values(
                        scan_run_id=scan_run_id,
                        search_signature=search_signature[:255],
                        marketplace=marketplace[:40],
                        keyword=keyword[:255],
                        limit_value=int(limit_value),
                        sort_order=sort_order[:40],
                        marketplace_id=(marketplace_id or "")[:80] or None,
                        subscribed_user_count=int(subscribed_user_count),
                        items_returned=int(items_returned),
                        api_calls_made=int(api_calls_made),
                        created_at=now,
                    )
                    .returning(shared_scan_searches.c.id)
                ).scalar_one()
            )

    def create_scan_cycle(
        self,
        *,
        mode: str,
        user_id: int | None = None,
        process_id: int = 0,
        hostname: str = "",
        auth_required: bool = False,
        background_poll_enabled: bool = False,
        background_poll_seconds: int = 0,
        active_window_start: str | None = None,
        active_window_end: str | None = None,
        active_window_timezone: str = "",
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
        now = now_iso()
        with self.engine.begin() as connection:
            return int(
                connection.execute(
                    scan_cycles.insert()
                    .values(
                        mode=mode[:40],
                        user_id=user_id,
                        started_at=now,
                        finished_at=None,
                        status=status[:20],
                        skip_reason=skip_reason[:500],
                        error_message=error_message[:1000],
                        source=source[:80] if source else None,
                        error_category=error_category[:120] if error_category else None,
                        http_status=int(http_status) if http_status is not None else None,
                        cooldown_until=cooldown_until,
                        retry_after_seconds=int(retry_after_seconds) if retry_after_seconds is not None else None,
                        process_id=int(process_id or 0),
                        hostname=hostname[:255],
                        auth_required=int(bool(auth_required)),
                        background_poll_enabled=int(bool(background_poll_enabled)),
                        background_poll_seconds=int(background_poll_seconds or 0),
                        active_window_start=active_window_start,
                        active_window_end=active_window_end,
                        active_window_timezone=active_window_timezone[:80],
                        users_considered=int(users_considered or 0),
                        users_scanned=int(users_scanned or 0),
                        keywords_searched=json.dumps(keywords_searched or []),
                        sources_checked=json.dumps(sources_checked or []),
                        created_at=now,
                        updated_at=now,
                    )
                    .returning(scan_cycles.c.id)
                ).scalar_one()
            )


def _default_user_settings_payload(seed: dict[str, Any] | None = None) -> dict[str, Any]:
    seed = seed or {}
    risky_range = seed.get("risky_score_range") or (DEFAULT_USER_RISKY_SCORE_MIN, DEFAULT_USER_RISKY_SCORE_MAX)
    if len(risky_range) != 2:
        risky_range = (DEFAULT_USER_RISKY_SCORE_MIN, DEFAULT_USER_RISKY_SCORE_MAX)
    return {
        "min_score_to_alert": float(seed.get("min_score_to_alert", DEFAULT_USER_MIN_SCORE_TO_ALERT)),
        "min_profit_to_alert": float(seed.get("min_profit_to_alert", DEFAULT_USER_MIN_PROFIT_TO_ALERT)),
        "risky_score_min": float(seed.get("risky_score_min", risky_range[0])),
        "risky_score_max": float(seed.get("risky_score_max", risky_range[1])),
        "max_alert_item_age_minutes": int(seed.get("max_alert_item_age_minutes", DEFAULT_MAX_ALERT_ITEM_AGE_MINUTES)),
        "max_priority_review_item_age_hours": int(seed.get("max_priority_review_item_age_hours", DEFAULT_MAX_PRIORITY_REVIEW_ITEM_AGE_HOURS)),
        "max_active_queue_item_age_hours": int(seed.get("max_active_queue_item_age_hours", DEFAULT_MAX_ACTIVE_QUEUE_ITEM_AGE_HOURS)),
        "default_resale_condition": str(seed.get("default_resale_condition", "good") or "good"),
        "allow_mint_for_alerts": bool(seed.get("allow_mint_for_alerts", False)),
        "target_min_model_generation": int(seed.get("target_min_model_generation", 0) or 0),
        "background_poll_enabled": bool(seed.get("background_poll_enabled", False)),
        "background_poll_seconds": int(seed.get("background_poll_seconds", DEFAULT_USER_BACKGROUND_POLL_SECONDS)),
        "active_start": seed.get("active_start"),
        "active_end": seed.get("active_end"),
        "timezone": str(seed.get("timezone", DEFAULT_USER_TIMEZONE) or DEFAULT_USER_TIMEZONE),
    }


def _user_row_to_dict(row: sqlite3.Row, *, include_password_hash: bool = False) -> dict[str, Any]:
    data = dict(row)
    if not include_password_hash:
        data.pop("password_hash", None)
    return data


def _notification_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    data["_discord_webhook_raw"] = data.get("discord_webhook") or ""
    data["discord_enabled"] = bool(data.get("discord_enabled"))
    data["alerts_enabled"] = bool(data.get("alerts_enabled"))
    data["notify_best_finds"] = bool(data.get("notify_best_finds"))
    data["notify_priority_review"] = bool(data.get("notify_priority_review"))
    data["discord_webhook_configured"] = bool(data.get("discord_webhook"))
    data["discord_webhook"] = ""
    return data


def _settings_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    data["allow_mint_for_alerts"] = bool(data.get("allow_mint_for_alerts"))
    data["background_poll_enabled"] = bool(data.get("background_poll_enabled"))
    return data


def _keyword_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    data["enabled"] = bool(data.get("enabled"))
    data["is_baseline"] = bool(data.get("is_baseline"))
    return data


def _invite_row_to_dict(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    data = dict(row)
    data["is_used"] = bool(data.get("used_at"))
    data["is_revoked"] = bool(data.get("revoked_at"))
    expires_at = _parse_iso_time(data.get("expires_at"))
    data["is_expired"] = bool(expires_at and expires_at <= datetime.now(timezone.utc))
    return data


def _joined_item_row_dict(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    return dict(row)


def _json_field(data: dict[str, Any], key: str, fallback: Any) -> Any:
    raw = data.get(key)
    if raw in (None, ""):
        return fallback
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return fallback


def _scan_cycle_row_to_dict(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    data = dict(row)
    for key in (
        "keywords_searched",
        "sources_checked",
    ):
        data[key] = _json_field(data, key, [])
    for key in (
        "final_bucket_counts",
        "alert_block_reason_counts",
        "missing_data_reason_counts",
        "risk_flag_counts",
        "parts_pricing_status_counts",
    ):
        data[key] = _json_field(data, key, {})
    for key in ("auth_required", "background_poll_enabled"):
        data[key] = bool(data.get(key))
    data["cycle_id"] = data.get("id")
    return data


def _decision_trace_row_to_dict(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    data = dict(row)
    data["trace"] = _json_field(data, "trace_json", {})
    data.pop("trace_json", None)
    verdict = data["trace"].get("verdict") if isinstance(data.get("trace"), dict) else {}
    verdict = verdict or {}
    data["current_app_bucket"] = data.get("current_app_bucket") or verdict.get("current_app_bucket")
    data["normalized_bucket"] = (
        data.get("normalized_bucket")
        or verdict.get("normalized_bucket")
        or verdict.get("bucket")
    )
    if data.get("alert_eligible") is None and "alert_eligible" in verdict:
        data["alert_eligible"] = bool(verdict.get("alert_eligible"))
    if data.get("manual_review_status") is None:
        if "manual_review_status" in verdict:
            data["manual_review_status"] = verdict.get("manual_review_status")
        elif "manual_review_needed" in verdict:
            data["manual_review_status"] = "needed" if verdict.get("manual_review_needed") else "not_needed"
    return data


def _row_to_dict(
    row: sqlite3.Row,
    *,
    max_alert_item_age_minutes: int = DEFAULT_MAX_ALERT_ITEM_AGE_MINUTES,
    max_priority_review_item_age_hours: int = DEFAULT_MAX_PRIORITY_REVIEW_ITEM_AGE_HOURS,
    max_active_queue_item_age_hours: int = DEFAULT_MAX_ACTIVE_QUEUE_ITEM_AGE_HOURS,
) -> dict[str, Any]:
    data = dict(row)
    for key in ("hard_reject_flags", "positive_flags", "risk_flags", "listing_classification_flags", "raw_json"):
        try:
            data[key] = json.loads(data[key]) if data[key] else [] if key != "raw_json" else {}
        except json.JSONDecodeError:
            data[key] = [] if key != "raw_json" else {}
    for key in (
        "estimated_profit_available",
        "estimated_parts_cost_available",
        "manual_review_allowed",
        "whole_phone_confidence_passed",
        "has_repair_issue",
        "manual_review_needed",
        "alert_eligible",
    ):
        data[key] = bool(data.get(key))
    listing_time = _parse_item_time(data)
    age_minutes = _age_minutes(listing_time)
    data["item_age_minutes"] = age_minutes
    data["item_age_label"] = _age_label(age_minutes)
    detail_checked_time = _parse_iso_time(data.get("last_availability_checked_at"))
    detail_age_minutes = _age_minutes(detail_checked_time)
    data["detail_check_age_minutes"] = detail_age_minutes
    data["detail_check_age_label"] = _detail_age_label(detail_age_minutes)
    data["fresh_for_alert"] = age_minutes is None or age_minutes <= max_alert_item_age_minutes
    data["fresh_for_priority_review"] = (
        age_minutes is None or age_minutes <= max_priority_review_item_age_hours * 60
    )
    data["fresh_for_active_queue"] = age_minutes is None or age_minutes <= max_active_queue_item_age_hours * 60
    data["stale"] = not data["fresh_for_active_queue"]
    data["unavailable"] = _is_unavailable_item(data)
    return data


def _parse_item_time(item: dict[str, Any]) -> Optional[datetime]:
    raw = item.get("item_origin_at") or item.get("found_at")
    return _parse_iso_time(raw)


def _parse_iso_time(raw: Any) -> Optional[datetime]:
    if not raw:
        return None
    if isinstance(raw, datetime):
        parsed = raw
    else:
        text = str(raw).strip()
        if text.endswith("Z"):
            text = f"{text[:-1]}+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _age_minutes(listing_time: Optional[datetime]) -> Optional[float]:
    if not listing_time:
        return None
    age = datetime.now(timezone.utc) - listing_time
    return max(0.0, age.total_seconds() / 60)


def _age_label(age_minutes: Optional[float]) -> str:
    if age_minutes is None:
        return "Age unknown"
    if age_minutes < 1:
        return "Listed just now"
    if age_minutes < 60:
        return f"Listed {int(age_minutes)} minutes ago"
    hours = age_minutes / 60
    if hours < 48:
        return f"Listed {int(hours)} hours ago"
    days = hours / 24
    return f"Listed {int(days)} days ago"


def _detail_age_label(age_minutes: Optional[float]) -> str:
    if age_minutes is None:
        return "Detail check unknown"
    if age_minutes < 1:
        return "Details checked just now"
    if age_minutes < 60:
        return f"Details checked {int(age_minutes)} minutes ago"
    hours = age_minutes / 60
    if hours < 48:
        return f"Details checked {int(hours)} hours ago"
    days = hours / 24
    return f"Details checked {int(days)} days ago"


def _item_date(item: dict[str, Any]) -> Optional[datetime.date]:
    parsed = _parse_item_time(item)
    return parsed.date() if parsed else None


def _has_known_model(item: dict[str, Any]) -> bool:
    return bool(item.get("model") and item.get("model") != "unknown")


def _has_manual_reason(item: dict[str, Any], reasons: list[str]) -> bool:
    text = item.get("manual_review_reason") or ""
    return any(reason in text for reason in reasons)


def _has_excluded_hard_reject(item: dict[str, Any]) -> bool:
    flags = item.get("hard_reject_flags") or []
    blocked = {
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
    return any(flag in blocked for flag in flags)


def _is_accessory_or_part_listing(item: dict[str, Any]) -> bool:
    flags = [*(item.get("listing_classification_flags") or []), *(item.get("hard_reject_flags") or [])]
    return any(flag.endswith("_not_phone") or flag == "lot_not_single_phone" for flag in flags)


def _has_profit_data(item: dict[str, Any]) -> bool:
    return (
        item.get("estimated_profit_available") is True
        and float(item.get("resale_value") or item.get("resale_mid") or 0) > 0
        and item.get("estimated_parts_cost_available") is not False
    )


def _is_best_find_item(item: dict[str, Any]) -> bool:
    return (
        item.get("user_status") not in {"ignored", "rejected"}
        and not _is_unavailable_item(item)
        and not _is_long_running_auction(item)
        and item.get("alert_eligible") is True
        and item.get("status") in {"candidate", "alerted"}
        and item.get("fresh_for_alert") is True
        and not item.get("stale")
    )


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
        and _has_known_model(item)
        and bool(item.get("storage_capacity"))
        and (
            strong_proof_count >= 2
            or (strong_proof_count >= 1 and has_description_evidence)
            or (
                has_description_evidence
                and float(item.get("whole_phone_confidence_score") or 0) >= 7
            )
        )
    )


def _reviewable_despite_pricing_gap(item: dict[str, Any]) -> bool:
    if not _has_reviewable_description_evidence(item):
        return False
    if _has_excluded_hard_reject(item) or _is_accessory_or_part_listing(item):
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


def _is_priority_review_item(item: dict[str, Any]) -> bool:
    if _is_unavailable_item(item):
        return False
    if item.get("user_status") != "new" or item.get("status") == "rejected":
        return False
    if item.get("alert_eligible") is True or item.get("fresh_for_priority_review") is not True:
        return False
    if not item.get("whole_phone_confidence_passed") or not item.get("has_repair_issue") or not _has_known_model(item):
        return False
    if _has_excluded_hard_reject(item) or _is_accessory_or_part_listing(item):
        return False
    if _uses_model_resale_without_storage(item) and not _storage_fallback_priority_exception(item):
        return False
    return (
        float(item.get("profit_mid") or item.get("estimated_profit") or 0) >= 37.5
        or float(item.get("profit_high") or 0) >= 75
        or _has_manual_reason(item, ["Too cheap without proof", "Profit depends on mint resale"])
        or (
            item.get("estimated_parts_cost_available") is False
            and float(item.get("resale_mid") or item.get("resale_value") or 0) > 0
        )
        or _reviewable_despite_pricing_gap(item)
    )


def _is_needs_data_item(item: dict[str, Any]) -> bool:
    if _is_unavailable_item(item):
        return False
    if item.get("user_status") in {"ignored", "rejected"} or item.get("status") == "rejected":
        return False
    if item.get("fresh_for_active_queue") is not True:
        return False
    if _is_priority_review_item(item) or _reviewable_despite_pricing_gap(item):
        return False
    return (
        not _has_known_model(item)
        or (_uses_model_resale_without_storage(item) and not _storage_fallback_priority_exception(item))
        or (bool(item.get("storage_resale_warning")) and item.get("resale_source") != "storage_specific")
        or float(item.get("resale_value") or item.get("resale_mid") or 0) <= 0
        or item.get("estimated_parts_cost_available") is False
        or item.get("has_repair_issue") is False
        or _has_manual_reason(
            item,
            [
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
            ],
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


def _is_unavailable_item(item: dict[str, Any]) -> bool:
    return (item.get("availability_status") or "unknown") in {"sold", "ended", "unavailable"}


def _is_long_running_auction(item: dict[str, Any]) -> bool:
    if item.get("buying_option_summary") != "auction":
        return False
    end_at = _parse_iso_time(item.get("item_end_at"))
    if not end_at:
        return False
    return (end_at - datetime.now(timezone.utc)).total_seconds() > 6 * 60 * 60


def _should_refresh_availability(item: dict[str, Any]) -> bool:
    if item.get("user_status") in {"ignored", "rejected"} or item.get("status") == "rejected" or item.get("stale"):
        return item.get("user_status") in {"watched", "promoted"}
    if item.get("user_status") in {"watched", "promoted"}:
        return True
    return _is_best_find_item(item) or _is_priority_review_item(item)


def _normalize_database_url(database_url: str) -> str:
    url = (database_url or "").strip()
    if not url:
        raise ValueError("DATABASE_URL is required")
    if url.startswith("postgres://"):
        return f"postgresql://{url[len('postgres://'):]}"
    return url


def _convert_qmark_sql(sql: str, params: list[Any]) -> tuple[str, dict[str, Any]]:
    if "?" not in sql:
        return sql, {f"p{index}": value for index, value in enumerate(params)}
    pieces = sql.split("?")
    if len(pieces) - 1 != len(params):
        raise ValueError("Parameter count does not match SQL placeholders")
    sql_parts: list[str] = [pieces[0]]
    bound: dict[str, Any] = {}
    for index, value in enumerate(params):
        name = f"p{index}"
        sql_parts.append(f":{name}")
        sql_parts.append(pieces[index + 1])
        bound[name] = value
    return "".join(sql_parts), bound
