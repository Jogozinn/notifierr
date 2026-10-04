"""Forward-only, leased retention for disposable scanner evidence.

Run with ``python -m backend.retention --sqlite PATH`` (dry run) or add
``--apply``. A database URL may be supplied instead of --sqlite for a worker.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import socket
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .storage import PostgresStorage, Storage
from .config import load_settings
from .db import get_database_url
from .schema import check_schema


@dataclass(frozen=True)
class RetentionPolicy:
    trace_days: int = 14
    reviewed_trace_days: int = 90
    search_days: int = 30
    notification_days: int = 90
    stale_item_days: int = 30
    batch_size: int = 500

    def __post_init__(self) -> None:
        if min(self.trace_days, self.reviewed_trace_days, self.search_days, self.notification_days,
               self.stale_item_days, self.batch_size) < 1:
            raise ValueError("Retention periods and batch size must be positive")


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _cutoffs(policy: RetentionPolicy, now: datetime) -> dict[str, str]:
    return {name: _iso(now - timedelta(days=days)) for name, days in (
        ("traces", policy.trace_days), ("searches", policy.search_days),
        ("reviewed_traces", policy.reviewed_trace_days),
        ("notifications", policy.notification_days), ("items", policy.stale_item_days),
    )}


_PROTECTED = """EXISTS (SELECT 1 FROM user_item_feedback f WHERE f.marketplace_item_id = mi.id)
 OR EXISTS (SELECT 1 FROM user_item_outcomes o WHERE o.marketplace_item_id = mi.id)
 OR EXISTS (SELECT 1 FROM user_item_corrections c WHERE c.marketplace_item_id = mi.id)
 OR EXISTS (SELECT 1 FROM user_item_states s WHERE s.marketplace_item_id = mi.id
            AND (s.reviewed_at IS NOT NULL OR s.user_status <> 'new' OR s.user_note <> ''))
"""


def _select_ids(connection: Any, sql: str, params: tuple[Any, ...], limit: int) -> list[int]:
    return [int(row["id"]) for row in connection.execute(sql + " LIMIT ?", (*params, limit)).fetchall()]


def _delete_batch(connection: Any, table: str, ids: list[int]) -> int:
    if not ids:
        return 0
    placeholders = ", ".join("?" for _ in ids)
    connection.execute(f"DELETE FROM {table} WHERE id IN ({placeholders})", tuple(ids))
    return len(ids)


def _rollup_day(connection: Any, day: str, now: str) -> int:
    """Persist a complete day's aggregates and completion marker atomically."""
    run_filter = "r.retention_managed = 1 AND substr(r.started_at, 1, 10) = ?"
    searches = connection.execute(
        """SELECT s.search_signature, s.marketplace, COUNT(*) executions,
                  SUM(s.items_returned) results_returned,
                  SUM(s.unique_new_items) newly_discovered,
                  SUM(s.viable_whole_phones) whole_phone_candidates,
                  SUM(s.component_count) component_listings,
                  SUM(s.gem_count) gem_count, SUM(s.profitable_count) profitable_count,
                  SUM(s.review_count) review_count, SUM(s.needs_data_count) needs_data_count,
                  SUM(s.reject_count) reject_count, SUM(s.alert_eligible_count) alert_eligible_count,
                  SUM(s.detail_fetch_attempts) detail_fetch_attempts,
                  SUM(s.detail_fetch_successes) detail_fetch_successes,
                  SUM(s.detail_fetch_failures) detail_fetch_failures
           FROM shared_scan_searches s JOIN shared_scan_runs r ON r.id = s.scan_run_id
           WHERE """ + run_filter + " GROUP BY s.search_signature, s.marketplace",
        (day,),
    ).fetchall()
    for search in searches:
        distinct = connection.execute(
            """SELECT COUNT(DISTINCT x.marketplace_item_id) distinct_listings,
                      SUM(CASE WHEN x.notification_status = 'sent' THEN 1 ELSE 0 END) alerts_sent
               FROM shared_scan_results x JOIN shared_scan_searches s ON s.id = x.scan_search_id
               JOIN shared_scan_runs r ON r.id = s.scan_run_id
               WHERE """ + run_filter + " AND s.search_signature = ? AND s.marketplace = ?",
            (day, search["search_signature"], search["marketplace"]),
        ).fetchone()
        fields = (
            "executions", "results_returned", "newly_discovered", "whole_phone_candidates",
            "component_listings", "gem_count", "profitable_count", "review_count",
            "needs_data_count", "reject_count", "alert_eligible_count",
            "detail_fetch_attempts", "detail_fetch_successes", "detail_fetch_failures",
        )
        connection.execute(
            """INSERT INTO search_daily_rollups (
                day, search_signature, marketplace, executions, results_returned,
                distinct_listings, newly_discovered, whole_phone_candidates,
                component_listings, gem_count, profitable_count, review_count,
                needs_data_count, reject_count, alert_eligible_count, alerts_sent,
                detail_fetch_attempts, detail_fetch_successes, detail_fetch_failures,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(day, search_signature, marketplace) DO NOTHING""",
            (day, search["search_signature"], search["marketplace"],
             search["executions"], search["results_returned"],
             distinct["distinct_listings"] or 0,
             *(int(search[field] or 0) for field in fields[2:11]),
             int(distinct["alerts_sent"] or 0),
             *(int(search[field] or 0) for field in fields[11:]), now, now),
        )
    run_count = connection.execute(
        "SELECT COUNT(*) n FROM shared_scan_runs r WHERE " + run_filter, (day,),
    ).fetchone()["n"]
    connection.execute(
        "INSERT INTO search_rollup_days(day, completed_at, run_count) VALUES (?, ?, ?) "
        "ON CONFLICT(day) DO NOTHING", (day, now, run_count),
    )
    return int(run_count)


def run_retention(
    storage: Storage, *, policy: RetentionPolicy | None = None,
    dry_run: bool = True, now: datetime | None = None,
) -> dict[str, Any]:
    policy = policy or RetentionPolicy()
    now = now or datetime.now(timezone.utc)
    stamp = _iso(now)
    cutoffs = _cutoffs(policy, now)
    owner = f"retention:{uuid.uuid4().hex}"
    expires = _iso(now + timedelta(minutes=5))
    if not dry_run and not storage.acquire_worker_lease(
        "retention", owner, hostname=socket.gethostname(), process_id=os.getpid(),
        now=stamp, expires_at=expires,
    ):
        raise RuntimeError("Retention lease is owned by another worker")
    counts: dict[str, int] = {name: 0 for name in (
        "rollup_days", "rollup_runs", "search_results", "searches", "scan_runs",
        "traces", "notifications", "marketplace_items", "user_item_states",
    )}
    report: dict[str, Any] = {"dry_run": dry_run, "cutoffs": cutoffs, "counts": counts}
    run_id: int | None = None
    try:
        if not dry_run:
            with storage.connect() as conn:
                run_id = int(conn.execute(
                    "INSERT INTO retention_runs(dry_run,status,started_at,cutoffs_json,counts_json) "
                    "VALUES (0,'running',?,?,?) RETURNING id",
                    (stamp, json.dumps(cutoffs), json.dumps(counts)),
                ).fetchone()["id"])
        day_offset = 0
        while True:
            with storage.connect() as conn:
                days = [row["retention_day"] for row in conn.execute(
                    """SELECT DISTINCT substr(r.started_at, 1, 10) AS retention_day
                       FROM shared_scan_runs r
                       WHERE r.retention_managed = 1 AND r.started_at < ?
                         AND substr(r.started_at, 1, 10) < substr(?, 1, 10)
                         AND NOT EXISTS (SELECT 1 FROM search_rollup_days d
                                         WHERE d.day = substr(r.started_at, 1, 10))
                         AND NOT EXISTS (SELECT 1 FROM shared_scan_runs active
                                         WHERE substr(active.started_at, 1, 10) = substr(r.started_at, 1, 10)
                                           AND active.status IN ('running', 'started'))
                       ORDER BY retention_day LIMIT ? OFFSET ?""",
                    (cutoffs["searches"], cutoffs["searches"], policy.batch_size, day_offset),
                ).fetchall()]
            if not days:
                break
            for day in days:
                if not dry_run and not storage.renew_worker_lease("retention", owner, now=_iso(datetime.now(timezone.utc)),
                                                                  expires_at=_iso(datetime.now(timezone.utc) + timedelta(minutes=5))):
                    raise RuntimeError("Retention lease was lost")
                if dry_run:
                    with storage.connect() as conn:
                        runs = conn.execute(
                            "SELECT COUNT(*) n FROM shared_scan_runs WHERE retention_managed=1 "
                            "AND substr(started_at,1,10)=?", (day,),
                        ).fetchone()["n"]
                else:
                    with storage.connect() as conn:
                        runs = _rollup_day(conn, day, stamp)
                counts["rollup_days"] += 1
                counts["rollup_runs"] += int(runs)
            if dry_run:
                day_offset += len(days)
        specs = [
            ("search_results", "shared_scan_results", """SELECT x.id FROM shared_scan_results x
                JOIN shared_scan_searches s ON s.id=x.scan_search_id JOIN shared_scan_runs r ON r.id=s.scan_run_id
                JOIN search_rollup_days d ON d.day=substr(r.started_at,1,10)
                WHERE r.retention_managed=1 AND r.started_at<? ORDER BY x.id""", (cutoffs["searches"],)),
            ("searches", "shared_scan_searches", """SELECT s.id FROM shared_scan_searches s
                JOIN shared_scan_runs r ON r.id=s.scan_run_id JOIN search_rollup_days d ON d.day=substr(r.started_at,1,10)
                WHERE r.retention_managed=1 AND r.started_at<?
                AND NOT EXISTS(SELECT 1 FROM shared_scan_results x WHERE x.scan_search_id=s.id) ORDER BY s.id""", (cutoffs["searches"],)),
            ("scan_runs", "shared_scan_runs", """SELECT r.id FROM shared_scan_runs r
                JOIN search_rollup_days d ON d.day=substr(r.started_at,1,10)
                WHERE r.retention_managed=1 AND r.started_at<?
                AND NOT EXISTS(SELECT 1 FROM shared_scan_searches s WHERE s.scan_run_id=r.id) ORDER BY r.id""", (cutoffs["searches"],)),
            ("traces", "listing_decision_traces", """SELECT t.id FROM listing_decision_traces t
                JOIN marketplace_items mi ON mi.id=t.marketplace_item_id
                WHERE t.retention_managed=1 AND
                ((t.created_at<? AND NOT (""" + _PROTECTED + ")) OR "
                "(t.created_at<? AND (" + _PROTECTED + "))) ORDER BY t.id",
                (cutoffs["traces"], cutoffs["reviewed_traces"])),
            ("notifications", "notification_attempts", """SELECT n.id FROM notification_attempts n
                WHERE n.retention_managed=1 AND n.created_at<?
                AND n.status IN ('sent','skipped','skipped_duplicate','cancelled_unavailable')
                AND NOT EXISTS(SELECT 1 FROM notification_attempts child WHERE child.parent_attempt_id=n.id)
                AND NOT EXISTS(SELECT 1 FROM notification_attempts child WHERE child.prior_success_attempt_id=n.id)
                ORDER BY n.id""", (cutoffs["notifications"],)),
            ("marketplace_items", "marketplace_items", """SELECT mi.id FROM marketplace_items mi
                WHERE mi.retention_managed=1 AND mi.first_seen_at<? AND mi.updated_at<?
                AND NOT (""" + _PROTECTED + """)
                AND NOT EXISTS (SELECT 1 FROM listing_decision_traces t WHERE t.marketplace_item_id=mi.id)
                AND NOT EXISTS (SELECT 1 FROM shared_scan_results x
                                WHERE x.marketplace=mi.marketplace AND x.marketplace_item_id=mi.marketplace_item_id)
                AND NOT EXISTS (SELECT 1 FROM notification_attempts n
                                WHERE n.item_id=mi.marketplace_item_id AND n.status IN ('pending','failed','deferred_rate_limit'))
                ORDER BY mi.id""", (cutoffs["items"], cutoffs["items"])),
        ]
        for name, table, query, params in specs:
            while True:
                with storage.connect() as conn:
                    eligible_query = query
                    eligible_params = params
                    if dry_run and name in {"search_results", "searches", "scan_runs"}:
                        eligible_query = query.replace(
                            "JOIN search_rollup_days d ON d.day=substr(r.started_at,1,10)",
                            """JOIN (SELECT DISTINCT substr(r2.started_at,1,10) AS retention_day
                               FROM shared_scan_runs r2 WHERE r2.retention_managed=1
                               AND NOT EXISTS (SELECT 1 FROM shared_scan_runs active
                                               WHERE substr(active.started_at,1,10)=substr(r2.started_at,1,10)
                                               AND active.status IN ('running','started'))) d
                               ON d.retention_day=substr(r.started_at,1,10)""",
                        )
                        if name == "searches":
                            eligible_query = eligible_query.replace(
                                "AND NOT EXISTS(SELECT 1 FROM shared_scan_results x WHERE x.scan_search_id=s.id)", "",
                            )
                        elif name == "scan_runs":
                            eligible_query = eligible_query.replace(
                                "AND NOT EXISTS(SELECT 1 FROM shared_scan_searches s WHERE s.scan_run_id=r.id)", "",
                            )
                    elif dry_run and name == "marketplace_items":
                        # Project rows left after this run's trace/search cleanup.
                        eligible_query = f"""SELECT mi.id FROM marketplace_items mi
                            WHERE mi.retention_managed=1 AND mi.first_seen_at<? AND mi.updated_at<?
                            AND NOT ({_PROTECTED})
                            AND NOT EXISTS (SELECT 1 FROM listing_decision_traces t
                                WHERE t.marketplace_item_id=mi.id AND NOT (
                                    t.retention_managed=1 AND
                                    ((t.created_at<? AND NOT ({_PROTECTED})) OR
                                     (t.created_at<? AND ({_PROTECTED})))))
                            AND NOT EXISTS (SELECT 1 FROM shared_scan_results x
                                JOIN shared_scan_searches s ON s.id=x.scan_search_id
                                JOIN shared_scan_runs r ON r.id=s.scan_run_id
                                WHERE x.marketplace=mi.marketplace AND x.marketplace_item_id=mi.marketplace_item_id
                                AND NOT (r.retention_managed=1 AND r.started_at<? AND (
                                    EXISTS(SELECT 1 FROM search_rollup_days d
                                           WHERE d.day=substr(r.started_at,1,10)) OR
                                    (substr(r.started_at,1,10)<substr(?,1,10) AND
                                     NOT EXISTS(SELECT 1 FROM shared_scan_runs active
                                                WHERE substr(active.started_at,1,10)=substr(r.started_at,1,10)
                                                AND active.status IN ('running','started'))))))
                            AND NOT EXISTS (SELECT 1 FROM notification_attempts n
                                WHERE n.item_id=mi.marketplace_item_id
                                AND n.status IN ('pending','failed','deferred_rate_limit'))
                            ORDER BY mi.id"""
                        eligible_params = (cutoffs["items"], cutoffs["items"],
                                           cutoffs["traces"], cutoffs["reviewed_traces"],
                                           cutoffs["searches"], cutoffs["searches"])
                    ids = _select_ids(conn, eligible_query, eligible_params, policy.batch_size)
                    if not ids:
                        break
                    if dry_run:
                        # Count all matching rows without changing any source record.
                        count_query = eligible_query
                        for order in ("x", "s", "r", "t", "n", "mi"):
                            count_query = count_query.replace(f" ORDER BY {order}.id", "")
                        counts[name] = int(conn.execute(
                            "SELECT COUNT(*) n FROM (" + count_query + ") eligible", eligible_params,
                        ).fetchone()["n"])
                        if name == "marketplace_items":
                            counts["user_item_states"] = int(conn.execute(
                                "SELECT COUNT(*) n FROM user_item_states WHERE marketplace_item_id IN ("
                                "SELECT id FROM (" + count_query + ") eligible)", eligible_params,
                            ).fetchone()["n"])
                        break
                    if name == "marketplace_items":
                        placeholders = ",".join("?" for _ in ids)
                        counts["user_item_states"] += int(conn.execute(
                            f"SELECT COUNT(*) n FROM user_item_states WHERE marketplace_item_id IN ({placeholders})",
                            tuple(ids),
                        ).fetchone()["n"])
                        conn.execute(f"DELETE FROM user_item_states WHERE marketplace_item_id IN ({placeholders})", tuple(ids))
                    counts[name] += _delete_batch(conn, table, ids)
                if not dry_run and not storage.renew_worker_lease("retention", owner, now=_iso(datetime.now(timezone.utc)),
                                                                  expires_at=_iso(datetime.now(timezone.utc) + timedelta(minutes=5))):
                    raise RuntimeError("Retention lease was lost")
        if run_id is not None:
            with storage.connect() as conn:
                conn.execute("UPDATE retention_runs SET status='completed', finished_at=?, counts_json=? WHERE id=?",
                             (_iso(datetime.now(timezone.utc)), json.dumps(counts), run_id))
        return report
    except Exception as exc:
        if run_id is not None:
            with storage.connect() as conn:
                conn.execute("UPDATE retention_runs SET status='failed', finished_at=?, counts_json=?, error=? WHERE id=?",
                             (_iso(datetime.now(timezone.utc)), json.dumps(counts), str(exc)[:1000], run_id))
        raise
    finally:
        if not dry_run:
            storage.release_worker_lease("retention", owner)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    database = parser.add_mutually_exclusive_group(required=True)
    database.add_argument("--sqlite")
    database.add_argument("--database-url")
    database.add_argument("--configured", action="store_true", help="Use DB_BACKEND/DATABASE_URL from the environment")
    parser.add_argument("--apply", action="store_true", help="Delete eligible detail after aggregation")
    parser.add_argument("--trace-days", type=int, default=14)
    parser.add_argument("--reviewed-trace-days", type=int, default=90)
    parser.add_argument("--search-days", type=int, default=30)
    parser.add_argument("--notification-days", type=int, default=90)
    parser.add_argument("--stale-item-days", type=int, default=30)
    parser.add_argument("--batch-size", type=int, default=500)
    args = parser.parse_args()
    if os.getenv("NOTIFIERR_ENV", "local").strip().lower() == "production" and not args.configured:
        parser.error("Production retention requires --configured")
    if args.sqlite and not Path(args.sqlite).is_file():
        parser.error("--sqlite must name an existing migrated database")
    try:
        if args.configured:
            settings = load_settings()
            if settings.runtime_env == "production" and settings.process_role != "retention":
                parser.error("Production retention requires NOTIFIERR_ROLE=retention")
            storage = (PostgresStorage(get_database_url(settings), initialize=False) if settings.db_backend == "postgres"
                       else Storage(settings.sqlite_path, initialize=False, read_only=not args.apply))
            if settings.db_backend == "sqlite" and not settings.sqlite_path.is_file():
                parser.error("Configured SQLite database must already exist and be migrated")
            if settings.runtime_env == "production":
                check_schema(storage.engine)
        else:
            storage = (Storage(Path(args.sqlite), initialize=False, read_only=not args.apply)
                       if args.sqlite else PostgresStorage(args.database_url, initialize=False))
        policy = RetentionPolicy(trace_days=args.trace_days, reviewed_trace_days=args.reviewed_trace_days,
                                 search_days=args.search_days, notification_days=args.notification_days,
                                 stale_item_days=args.stale_item_days, batch_size=args.batch_size)
        report = run_retention(storage, policy=policy, dry_run=not args.apply)
    except Exception as exc:
        logging.error("Retention failed error_type=%s", type(exc).__name__)
        print(json.dumps({"event": "retention_failed", "error_type": type(exc).__name__}), flush=True)
        raise SystemExit(1) from None
    print(json.dumps({"event": "retention_completed", **report}, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
