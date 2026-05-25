from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .scorer import now_iso


DEFAULT_MAX_ALERT_ITEM_AGE_MINUTES = 180
DEFAULT_MAX_PRIORITY_REVIEW_ITEM_AGE_HOURS = 24
DEFAULT_MAX_ACTIVE_QUEUE_ITEM_AGE_HOURS = 24


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
    ignored_reason TEXT NOT NULL DEFAULT '',
    ignored_seller TEXT NOT NULL DEFAULT '',
    updated_by_user_at TEXT,
    hard_reject_flags TEXT NOT NULL DEFAULT '[]',
    positive_flags TEXT NOT NULL DEFAULT '[]',
    risk_flags TEXT NOT NULL DEFAULT '[]',
    alerted_at TEXT,
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
"""

USER_STATUSES = {"new", "reviewed", "watched", "ignored", "promoted"}


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
                self._memory_connection = sqlite3.connect(":memory:")
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
            "ignored_reason": "ALTER TABLE items ADD COLUMN ignored_reason TEXT NOT NULL DEFAULT ''",
            "ignored_seller": "ALTER TABLE items ADD COLUMN ignored_seller TEXT NOT NULL DEFAULT ''",
            "updated_by_user_at": "ALTER TABLE items ADD COLUMN updated_by_user_at TEXT",
            "alerted_at": "ALTER TABLE items ADD COLUMN alerted_at TEXT",
            "item_origin_at": "ALTER TABLE items ADD COLUMN item_origin_at TEXT",
        }
        for column, statement in migrations.items():
            if column not in columns:
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
        }
        for column, statement in indexes.items():
            if column in columns:
                connection.execute(statement)

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
            "ignored_reason": item.get("ignored_reason") or "",
            "ignored_seller": item.get("ignored_seller") or "",
            "updated_by_user_at": item.get("updated_by_user_at"),
            "found_at": found_at,
            "updated_at": now,
            "hard_reject_flags": json.dumps(item.get("hard_reject_flags", [])),
            "positive_flags": json.dumps(item.get("positive_flags", [])),
            "risk_flags": json.dumps(item.get("risk_flags", [])),
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
                    estimated_parts_cost, estimated_parts_cost_available,
                    risk_buffer, estimated_profit, estimated_profit_available,
                    parts_pricing_status,
                    parts_pricing_note, parts_pricing_label, pricing_warning,
                    manual_review_allowed, whole_phone_confidence_passed,
                    whole_phone_score, has_repair_issue, manual_review_needed,
                    manual_review_reason, alert_eligible,
                    listing_classification_flags,
                    user_status, user_note, reviewed_at, ignored_at, watched_at,
                    promoted_at, ignored_reason, ignored_seller, updated_by_user_at,
                    hard_reject_flags, positive_flags, risk_flags, raw_json
                )
                VALUES (
                    :item_id, :title, :price, :shipping, :total_cost, :condition,
                    :item_url, :image_url, :seller_username,
                    :seller_feedback_percentage, :seller_feedback_score,
                    :raw_description, :found_at, :updated_at, :item_origin_at, :model, :score,
                    :status, :resale_value, :resale_low, :resale_mid,
                    :resale_high, :profit_low, :profit_mid, :profit_high,
                    :resale_confidence, :resale_sample_size, :resale_note,
                    :estimated_parts_cost, :estimated_parts_cost_available,
                    :risk_buffer, :estimated_profit, :estimated_profit_available,
                    :parts_pricing_status,
                    :parts_pricing_note, :parts_pricing_label, :pricing_warning,
                    :manual_review_allowed, :whole_phone_confidence_passed,
                    :whole_phone_score, :has_repair_issue, :manual_review_needed,
                    :manual_review_reason, :alert_eligible,
                    :listing_classification_flags,
                    :user_status, :user_note, :reviewed_at, :ignored_at,
                    :watched_at, :promoted_at, :ignored_reason, :ignored_seller,
                    :updated_by_user_at,
                    :hard_reject_flags, :positive_flags, :risk_flags, :raw_json
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
                    raw_json = excluded.raw_json
                """,
                payload,
            )

    def get_item(self, item_id: str) -> Optional[dict[str, Any]]:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM items WHERE item_id = ?", (item_id,)).fetchone()
        return _row_to_dict(row) if row else None

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

    def set_user_status(self, item_id: str, user_status: str, *, ignored_reason: str = "") -> dict[str, Any]:
        if user_status not in USER_STATUSES:
            raise ValueError(f"Unsupported user_status={user_status}")
        timestamp_column = {
            "reviewed": "reviewed_at",
            "watched": "watched_at",
            "ignored": "ignored_at",
            "promoted": "promoted_at",
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

    def add_ignored_seller(self, seller_username: str, *, reason: str = "", source_item_id: str | None = None) -> None:
        seller_username = seller_username.strip()
        if not seller_username:
            raise ValueError("seller_username is required")
        now = now_iso()
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

    def add_ignored_keyword(self, keyword: str, *, reason: str = "") -> None:
        keyword = keyword.strip()
        if not keyword:
            raise ValueError("keyword is required")
        now = now_iso()
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

    def ignore_seller_from_item(self, item_id: str, *, reason: str = "") -> dict[str, Any]:
        item = self.get_item(item_id)
        if not item:
            raise KeyError(item_id)
        seller = item.get("seller_username")
        if not seller:
            raise ValueError("Selected item has no seller username")
        self.add_ignored_seller(seller, reason=reason or "Ignored from dashboard", source_item_id=item_id)
        updated = self.get_item(item_id)
        if not updated:
            raise KeyError(item_id)
        return updated

    def list_ignored_sellers(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute("SELECT * FROM ignored_sellers ORDER BY created_at DESC").fetchall()
        return [dict(row) for row in rows]

    def list_ignored_keywords(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute("SELECT * FROM ignored_keywords ORDER BY created_at DESC").fetchall()
        return [dict(row) for row in rows]

    def ignored_match(self, listing: dict[str, Any]) -> dict[str, str]:
        seller = (listing.get("seller_username") or "").strip()
        title = (listing.get("title") or "").lower()
        with self.connect() as connection:
            if seller:
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
        active_items = [item for item in items if not item["stale"] and item.get("user_status") != "ignored"]
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
    data["fresh_for_alert"] = age_minutes is None or age_minutes <= max_alert_item_age_minutes
    data["fresh_for_priority_review"] = (
        age_minutes is None or age_minutes <= max_priority_review_item_age_hours * 60
    )
    data["fresh_for_active_queue"] = age_minutes is None or age_minutes <= max_active_queue_item_age_hours * 60
    data["stale"] = not data["fresh_for_active_queue"]
    return data


def _parse_item_time(item: dict[str, Any]) -> Optional[datetime]:
    raw = item.get("item_origin_at") or item.get("found_at")
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
        item.get("user_status") != "ignored"
        and item.get("alert_eligible") is True
        and item.get("status") in {"candidate", "alerted"}
        and item.get("fresh_for_alert") is True
        and not item.get("stale")
    )


def _is_priority_review_item(item: dict[str, Any]) -> bool:
    if item.get("user_status") != "new" or item.get("status") == "rejected":
        return False
    if item.get("alert_eligible") is True or item.get("fresh_for_priority_review") is not True:
        return False
    if not item.get("whole_phone_confidence_passed") or not item.get("has_repair_issue") or not _has_known_model(item):
        return False
    if _has_excluded_hard_reject(item) or _is_accessory_or_part_listing(item):
        return False
    return (
        float(item.get("profit_mid") or item.get("estimated_profit") or 0) >= 37.5
        or float(item.get("profit_high") or 0) >= 75
        or _has_manual_reason(item, ["Too cheap without proof"])
        or (
            item.get("estimated_parts_cost_available") is False
            and float(item.get("resale_mid") or item.get("resale_value") or 0) > 0
        )
    )


def _is_needs_data_item(item: dict[str, Any]) -> bool:
    if item.get("user_status") == "ignored" or item.get("status") == "rejected":
        return False
    if item.get("fresh_for_active_queue") is not True:
        return False
    return (
        not _has_known_model(item)
        or float(item.get("resale_value") or item.get("resale_mid") or 0) <= 0
        or item.get("estimated_parts_cost_available") is False
        or item.get("has_repair_issue") is False
        or _has_manual_reason(
            item,
            [
                "Parts-only ambiguous",
                "Read description listing",
                "Expected profit below threshold",
                "Only optimistic profit clears threshold",
                "Low-confidence pricing needs stronger profit",
                "Too cheap without proof",
                "Parts-only listing lacks power/iCloud/IMEI proof",
                "Model/spec mismatch",
                "Missing part price",
                "Model unknown",
                "No specific repair issue detected",
            ],
        )
    )
