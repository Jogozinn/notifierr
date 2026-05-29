from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert

from ..config import load_settings
from ..db import create_sqlalchemy_engine, normalize_database_url, redact_database_url
from ..db_models import CORE_TABLES, SERIAL_ID_TABLES, metadata


TABLE_ORDER = [
    "users",
    "user_invites",
    "user_settings",
    "user_notification_settings",
    "user_keywords",
    "marketplace_items",
    "user_item_states",
    "user_ignored_sellers",
    "user_ignored_keywords",
    "user_repair_value_overrides",
    "user_resale_research_overrides",
    "user_item_corrections",
    "shared_scan_runs",
    "shared_scan_searches",
    "shared_scan_results",
    "user_usage_daily",
]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Migrate Notifierr SQLite data into Postgres.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="Inspect source table counts without writing")
    mode.add_argument("--write", action="store_true", help="Write source rows into Postgres")
    return parser.parse_args(argv)


def sqlite_connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(str(path))
    connection.row_factory = sqlite3.Row
    return connection


def table_counts(connection: sqlite3.Connection) -> dict[str, int]:
    counts: dict[str, int] = {}
    for table_name in TABLE_ORDER:
        try:
            row = connection.execute(f"SELECT COUNT(*) AS row_count FROM {table_name}").fetchone()
        except sqlite3.OperationalError:
            counts[table_name] = 0
            continue
        counts[table_name] = int(row["row_count"] if row else 0)
    return counts


def read_table_rows(connection: sqlite3.Connection, table_name: str) -> list[dict[str, Any]]:
    try:
        rows = connection.execute(f"SELECT * FROM {table_name}").fetchall()
    except sqlite3.OperationalError:
        return []
    return [dict(row) for row in rows]


def upsert_table_rows(connection: sa.Connection, table_name: str, rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    table = CORE_TABLES[table_name]
    primary_key_columns = [column.name for column in table.primary_key.columns]
    insert_stmt = pg_insert(table).values(rows)
    update_columns = {
        column.name: insert_stmt.excluded[column.name]
        for column in table.columns
        if column.name not in primary_key_columns
    }
    if table_name == "shared_scan_results":
        statement = insert_stmt.on_conflict_do_nothing(
            index_elements=["scan_search_id", "marketplace", "marketplace_item_id"]
        )
    elif table_name == "user_keywords":
        statement = insert_stmt.on_conflict_do_update(
            index_elements=["user_id", "keyword"],
            set_=update_columns,
        )
    elif table_name == "marketplace_items":
        statement = insert_stmt.on_conflict_do_update(
            index_elements=["marketplace", "marketplace_item_id"],
            set_=update_columns,
        )
    elif table_name == "user_item_states":
        statement = insert_stmt.on_conflict_do_update(
            index_elements=["user_id", "marketplace_item_id"],
            set_=update_columns,
        )
    elif table_name == "user_ignored_sellers":
        statement = insert_stmt.on_conflict_do_update(
            index_elements=["user_id", "seller_username"],
            set_=update_columns,
        )
    elif table_name == "user_ignored_keywords":
        statement = insert_stmt.on_conflict_do_update(
            index_elements=["user_id", "keyword"],
            set_=update_columns,
        )
    elif table_name == "user_repair_value_overrides":
        statement = insert_stmt.on_conflict_do_update(
            index_elements=["user_id", "model", "part"],
            set_=update_columns,
        )
    elif table_name == "user_resale_research_overrides":
        statement = insert_stmt.on_conflict_do_update(
            index_elements=["user_id", "model", "storage_capacity", "condition"],
            set_=update_columns,
        )
    elif table_name == "user_item_corrections":
        statement = insert_stmt.on_conflict_do_update(
            index_elements=["user_id", "marketplace_item_id"],
            set_=update_columns,
        )
    else:
        statement = insert_stmt.on_conflict_do_update(
            index_elements=primary_key_columns,
            set_=update_columns,
        )
    result = connection.execute(statement)
    return int(result.rowcount or 0)


def reset_sequences(connection: sa.Connection) -> None:
    for table_name in SERIAL_ID_TABLES:
        connection.execute(
            sa.text(
                """
                SELECT setval(
                    pg_get_serial_sequence(:table_name, 'id'),
                    COALESCE((SELECT MAX(id) FROM ONLY """ + table_name + """), 1),
                    true
                )
                """
            ),
            {"table_name": table_name},
        )


def run_dry_run(sqlite_path: Path) -> int:
    with sqlite_connect(sqlite_path) as source:
        counts = table_counts(source)
    print(f"SQLite source: {sqlite_path}")
    for table_name in TABLE_ORDER:
        print(f"{table_name}: {counts.get(table_name, 0)}")
    print("Dry run complete. No Postgres writes performed.")
    return 0


def run_write(sqlite_path: Path, database_url: str) -> int:
    engine = create_sqlalchemy_engine(database_url)
    metadata.create_all(engine)
    with sqlite_connect(sqlite_path) as source:
        source_counts = table_counts(source)
        source_rows = {table_name: read_table_rows(source, table_name) for table_name in TABLE_ORDER}
    with engine.begin() as connection:
        for table_name in TABLE_ORDER:
            upsert_table_rows(connection, table_name, source_rows[table_name])
        reset_sequences(connection)
    print(f"SQLite source: {sqlite_path}")
    print(f"Postgres target: {redact_database_url(database_url)}")
    for table_name in TABLE_ORDER:
        print(f"{table_name}: {source_counts.get(table_name, 0)}")
    print("Write complete.")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    settings = load_settings()
    sqlite_path = settings.sqlite_path
    if not sqlite_path.exists() and str(sqlite_path) != ":memory:":
        print(f"SQLite source database not found: {sqlite_path}")
        return 1
    if args.dry_run:
        return run_dry_run(sqlite_path)
    if not settings.database_url:
        print("Postgres target not configured: DATABASE_URL is required for --write")
        return 1
    return run_write(sqlite_path, normalize_database_url(settings.database_url))


if __name__ == "__main__":
    raise SystemExit(main())
