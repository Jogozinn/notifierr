# Environment and secret safety

Never commit:

- `DATABASE_URL`
- `AUTH_SECRET_KEY`
- `APP_ENCRYPTION_KEY`
- `EBAY_CLIENT_ID`
- `EBAY_CLIENT_SECRET`
- Discord webhook URLs
- local `.env` files

If any of those were committed, rotate them immediately.

## Generate `APP_ENCRYPTION_KEY`

```powershell
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

## Generate `AUTH_SECRET_KEY`

```powershell
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

## Safe env files

- `backend/.env` for local backend development
- `frontend/.env.local` for local frontend development

Those files are ignored by git and should stay local.

## Safe database checks

`python -m backend.db_check` prints only backend status. It does not echo the full `DATABASE_URL`.

`python -m backend.tools.migrate_sqlite_to_postgres` prints table counts and a redacted target URL only.
