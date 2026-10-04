"""Read-only schema compatibility check for production processes."""

from pathlib import Path
from functools import lru_cache

from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import text


@lru_cache(maxsize=1)
def expected_revision() -> str:
    root = Path(__file__).resolve().parent.parent
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    config.set_main_option("path_separator", "os")
    heads = ScriptDirectory.from_config(config).get_heads()
    if len(heads) != 1:
        raise RuntimeError("Expected exactly one Alembic head")
    return heads[0]


def check_schema(engine) -> None:
    """Fail on missing, behind, or unknown/ahead migrations without mutating schema."""
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))
        current = MigrationContext.configure(connection).get_current_revision()
    expected = expected_revision()
    if current != expected:
        raise RuntimeError(
            f"Database schema revision {current or '<missing>'} does not match {expected}; "
            "run the explicit migration job before starting services"
        )
