"""Canonical database URL handling shared by runtime and maintenance paths."""

from __future__ import annotations


def normalize_database_url(raw_url: str) -> str:
    """Select psycopg v3 for implicit PostgreSQL URLs without changing URL data."""
    url = (raw_url or "").strip()
    if not url:
        raise ValueError("DATABASE_URL is required")
    if url.startswith("postgresql+psycopg://"):
        return url
    if url.startswith("postgresql://"):
        return f"postgresql+psycopg://{url[len('postgresql://') :]}"
    if url.startswith("postgres://"):
        return f"postgresql+psycopg://{url[len('postgres://') :]}"
    return url
