from __future__ import annotations

import io
import sqlite3
from contextlib import redirect_stdout
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.config import Config

from backend import db as db_module, db_check
from backend.config import Settings
from backend.db import create_storage, normalize_database_url, redact_database_url, select_storage_class
from backend.db_models import CORE_TABLES, metadata
from backend.storage import PostgresStorage, Storage, _SQLAlchemyConnectionShim, _postgres_driver_connect_args
from backend.tools import migrate_sqlite_to_postgres


def test_sqlalchemy_metadata_imports_cleanly():
    assert "users" in CORE_TABLES
    assert "marketplace_items" in CORE_TABLES
    assert metadata.tables["users"].name == "users"


def test_alembic_config_loads():
    config = Config("alembic.ini")
    assert config.get_main_option("script_location") == "alembic"


def test_storage_selection_uses_sqlite_by_default(tmp_path):
    settings = Settings(sqlite_path=tmp_path / "local.sqlite3", db_backend="sqlite")
    storage = create_storage(settings)
    assert isinstance(storage, Storage)
    assert not isinstance(storage, PostgresStorage)


def test_storage_selection_uses_postgres_when_configured():
    settings = Settings(
        db_backend="postgres",
        database_url="postgresql://user:secret@example.neon.tech/notifierr?sslmode=require",
    )
    assert select_storage_class(settings) is PostgresStorage


@pytest.mark.parametrize(
    ("raw_url", "expected"),
    [
        (
            "postgresql://user:secret@example.test:5432/notifierr",
            "postgresql+psycopg://user:secret@example.test:5432/notifierr",
        ),
        (
            "postgres://user:secret@example.test:5432/notifierr",
            "postgresql+psycopg://user:secret@example.test:5432/notifierr",
        ),
        (
            "postgresql+psycopg://user:secret@example.test:5432/notifierr",
            "postgresql+psycopg://user:secret@example.test:5432/notifierr",
        ),
        (
            "postgresql://user:secret@example.test/notifierr?sslmode=require&application_name=notifierr",
            "postgresql+psycopg://user:secret@example.test/notifierr?sslmode=require&application_name=notifierr",
        ),
        ("sqlite+pysqlite:///local.sqlite3", "sqlite+pysqlite:///local.sqlite3"),
    ],
)
def test_database_url_normalization_selects_psycopg_v3_without_changing_url_data(raw_url, expected):
    assert normalize_database_url(raw_url) == expected


def test_postgres_storage_uses_normalized_psycopg_v3_url(monkeypatch):
    captured = {}

    def fake_create_engine(url, **kwargs):
        captured.update(url=url, kwargs=kwargs)
        return object()

    monkeypatch.setattr("backend.storage.sa.create_engine", fake_create_engine)
    storage = PostgresStorage(
        "postgresql://user:secret@example.test/notifierr?sslmode=require",
        initialize=False,
    )

    assert storage.database_url == (
        "postgresql+psycopg://user:secret@example.test/notifierr?sslmode=require"
    )
    assert captured["url"] == storage.database_url
    assert captured["kwargs"]["connect_args"] == {"prepare_threshold": None}


def test_alembic_database_url_uses_same_normalization(monkeypatch):
    monkeypatch.setattr(
        db_module,
        "load_settings",
        lambda: Settings(
            db_backend="postgres",
            database_url="postgres://user:secret@example.test/notifierr?sslmode=require",
        ),
    )

    assert db_module.get_alembic_database_url() == (
        "postgresql+psycopg://user:secret@example.test/notifierr?sslmode=require"
    )


def test_psycopg_auto_prepare_is_disabled_for_schema_safe_pooling():
    assert _postgres_driver_connect_args("postgresql+psycopg://example/db") == {"prepare_threshold": None}
    assert _postgres_driver_connect_args("postgresql+psycopg2://example/db") == {}


def test_db_check_module_imports_without_connecting():
    assert callable(db_check.run_check)


def test_database_url_redaction_hides_password():
    redacted = redact_database_url("postgresql://user:super-secret@example.neon.tech/notifierr?sslmode=require")
    assert "super-secret" not in redacted
    assert "user:***@" in redacted


def test_migration_dry_run_can_be_invoked_safely(tmp_path, monkeypatch):
    sqlite_path = tmp_path / "source.sqlite3"
    source_storage = Storage(sqlite_path)
    source_storage.create_user(
        email="user@example.com",
        password_hash="hash",
        role="user",
        account_status="active",
    )

    settings = Settings(sqlite_path=sqlite_path, db_backend="sqlite")
    monkeypatch.setattr(migrate_sqlite_to_postgres, "load_settings", lambda: settings)

    buffer = io.StringIO()
    with redirect_stdout(buffer):
        exit_code = migrate_sqlite_to_postgres.main(["--dry-run"])

    output = buffer.getvalue()
    assert exit_code == 0
    assert "users: 1" in output
    assert "Dry run complete" in output


def test_sqlite_fallback_storage_still_works(tmp_path):
    db_path = tmp_path / "fallback.sqlite3"
    storage = Storage(db_path)
    storage.create_user(
        email="fallback@example.com",
        password_hash="hash",
        role="admin",
        account_status="active",
    )
    assert storage.any_admin_exists() is True


def test_sqlalchemy_connection_shim_supports_executemany():
    engine = sa.create_engine("sqlite+pysqlite:///:memory:", future=True)
    with engine.begin() as connection:
        connection.execute(sa.text("CREATE TABLE shim_batch_test (value TEXT NOT NULL)"))

    with _SQLAlchemyConnectionShim(engine) as connection:
        connection.executemany(
            "INSERT INTO shim_batch_test (value) VALUES (?)",
            [("alpha",), ("beta",), ("gamma",)],
        )

    with engine.connect() as connection:
        rows = connection.execute(sa.text("SELECT value FROM shim_batch_test ORDER BY value ASC")).fetchall()

    assert [row[0] for row in rows] == ["alpha", "beta", "gamma"]


def test_record_shared_scan_results_works_through_sqlalchemy_shim():
    engine = sa.create_engine("sqlite+pysqlite:///:memory:", future=True)
    metadata.create_all(engine)

    class ShimStorage(Storage):
        def __init__(self, shim_engine):
            self.path = Path(":shim:")
            self._memory_connection = None
            self.engine = shim_engine

        def connect(self):
            return _SQLAlchemyConnectionShim(self.engine)

    storage = ShimStorage(engine)

    with storage.connect() as connection:
        connection.execute(
            """
            INSERT INTO shared_scan_results (
                scan_search_id, marketplace, marketplace_item_id, created_at
            )
            VALUES (?, ?, ?, ?)
            """,
            (1, "ebay", "seed-item", "2026-05-28T00:00:00+00:00"),
        )

    storage.record_shared_scan_results(1, ["seed-item", "item-2", "item-3"])

    with storage.connect() as connection:
        rows = connection.execute(
            """
            SELECT marketplace_item_id
            FROM shared_scan_results
            WHERE scan_search_id = ?
            ORDER BY marketplace_item_id ASC
            """,
            (1,),
        ).fetchall()

    assert [row["marketplace_item_id"] for row in rows] == ["item-2", "item-3", "seed-item"]
