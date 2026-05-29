# Neon setup

## Neon connection

Set:

```powershell
$env:DB_BACKEND="postgres"
$env:DATABASE_URL="postgresql://user:password@host/dbname?sslmode=require"
```

Notifierr passes the URL directly to SQLAlchemy. Keep `sslmode=require` in the URL for Neon.

## Install dependencies

```powershell
pip install -r requirements.txt
```

## Run migrations

```powershell
alembic upgrade head
```

## Smoke check the connection

```powershell
python -m backend.db_check
```

## Migrate current SQLite data

Preview counts only:

```powershell
python -m backend.tools.migrate_sqlite_to_postgres --dry-run
```

Write data into Neon:

```powershell
python -m backend.tools.migrate_sqlite_to_postgres --write
```

## Notes

- The migration utility is idempotent where practical, but it assumes the target schema already exists.
- Run the migration into an empty or beta-only Neon database.
- After migration, keep using `alembic upgrade head` for schema changes.
