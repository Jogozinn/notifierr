from __future__ import annotations

import sys

import sqlalchemy as sa

from .config import load_settings
from .db import create_sqlalchemy_engine, get_database_backend, get_database_url


def run_check() -> int:
    settings = load_settings()
    try:
        backend = get_database_backend(settings)
        database_url = get_database_url(settings)
    except ValueError as exc:
        print(f"Database check not configured: {exc}")
        return 1

    try:
        engine = create_sqlalchemy_engine(database_url)
        with engine.connect() as connection:
            connection.execute(sa.text("SELECT 1"))
    except Exception:
        print(f"Database connection failed for backend={backend}. Check configuration and network access.")
        return 1

    print(f"Database connection OK for backend={backend}.")
    return 0


def main() -> None:
    raise SystemExit(run_check())


if __name__ == "__main__":
    main()
