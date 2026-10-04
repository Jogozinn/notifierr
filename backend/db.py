from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

from .config import Settings, load_settings
from .database_url import normalize_database_url
from .db_models import metadata
from .storage import PostgresStorage, Storage, _postgres_driver_connect_args


DEFAULT_POOL_RECYCLE_SECONDS = 300


def redact_database_url(raw_url: str | None) -> str:
    if not raw_url:
        return "<not-configured>"
    try:
        parsed = urlsplit(normalize_database_url(raw_url))
    except ValueError:
        return "<invalid-database-url>"
    hostname = parsed.hostname or ""
    port = f":{parsed.port}" if parsed.port else ""
    username = parsed.username or ""
    auth = f"{username}:***@" if username else ""
    netloc = f"{auth}{hostname}{port}"
    path = parsed.path or ""
    return urlunsplit((parsed.scheme, netloc, path, parsed.query, parsed.fragment))


def sqlite_url_from_path(path: Path) -> str:
    if str(path) == ":memory:":
        return "sqlite+pysqlite:///:memory:"
    return f"sqlite+pysqlite:///{path.as_posix()}"


def get_database_backend(settings: Settings) -> str:
    backend = (settings.db_backend or "sqlite").strip().lower()
    if backend not in {"sqlite", "postgres"}:
        raise ValueError(f"Unsupported DB_BACKEND={backend}")
    return backend


def get_database_url(settings: Settings) -> str:
    backend = get_database_backend(settings)
    if backend == "postgres":
        if not settings.database_url:
            raise ValueError("DATABASE_URL is required when DB_BACKEND=postgres")
        return normalize_database_url(settings.database_url)
    return sqlite_url_from_path(settings.sqlite_path)


def create_sqlalchemy_engine(database_url: str, *, echo: bool = False) -> Engine:
    normalized = normalize_database_url(database_url)
    connect_args: dict[str, Any] = {}
    if normalized.startswith("sqlite"):
        connect_args["check_same_thread"] = False
    else:
        connect_args.update(_postgres_driver_connect_args(normalized))
    return create_engine(
        normalized,
        future=True,
        pool_pre_ping=True,
        pool_recycle=DEFAULT_POOL_RECYCLE_SECONDS,
        echo=echo,
        connect_args=connect_args,
    )


def select_storage_class(settings: Settings) -> type[Storage]:
    backend = get_database_backend(settings)
    if backend == "postgres":
        if not settings.database_url:
            raise ValueError("DATABASE_URL is required when DB_BACKEND=postgres")
        return PostgresStorage
    return Storage


def create_storage(settings: Settings):
    storage_class = select_storage_class(settings)
    if storage_class is PostgresStorage:
        storage = PostgresStorage(get_database_url(settings), initialize=settings.runtime_env != "production")
        if settings.runtime_env == "production":
            from .schema import check_schema
            check_schema(storage.engine)
        return storage
    return Storage(settings.sqlite_path)


def get_alembic_database_url() -> str:
    settings = load_settings()
    return get_database_url(settings)


def create_all_tables(engine: Engine) -> None:
    metadata.create_all(engine)
