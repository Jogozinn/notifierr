# Deployment

## Frontend on Vercel

- Root directory: `frontend`
- Install command: `npm install`
- Build command: `npm run build`
- Output directory: `dist`
- Required env:
  - `VITE_API_BASE_URL=https://your-backend-url`

## Backend on Railway or Render

Start the API process:

```powershell
uvicorn backend.main:app --host 0.0.0.0 --port ${PORT:-8000}
```

Run the background polling worker in a separate process using the same app code. The current app loop is in the API process, so the safe hosted pattern is one web process and one worker process both using the same environment, with the worker being the only instance that has background polling enabled.

Suggested worker start command:

```powershell
uvicorn backend.main:app --host 0.0.0.0 --port ${PORT:-8000}
```

Use environment to ensure only the worker has background polling enabled:

- Web process:
  - `BACKGROUND_POLL_ENABLED=false`
- Worker process:
  - `BACKGROUND_POLL_ENABLED=true`

## Required backend env for hosted Postgres mode

- `DB_BACKEND=postgres`
- `DATABASE_URL=postgresql://...`
- `AUTH_REQUIRED=true`
- `AUTH_SECRET_KEY=...`
- `APP_ENCRYPTION_KEY=...`
- `EBAY_CLIENT_ID=...`
- `EBAY_CLIENT_SECRET=...`
- `ADMIN_EMAIL=...`
- `ADMIN_PASSWORD=...`
- `ADMIN_DISPLAY_NAME=...`
- `CORS_ALLOWED_ORIGINS=https://your-frontend-url`

## First-time hosted database setup

```powershell
alembic upgrade head
python -m backend.db_check
```

## Local SQLite mode

```powershell
$env:DB_BACKEND="sqlite"
uvicorn backend.main:app --reload
```

## Hosted Postgres mode

```powershell
$env:DB_BACKEND="postgres"
$env:DATABASE_URL="postgresql://..."
$env:AUTH_REQUIRED="true"
alembic upgrade head
uvicorn backend.main:app --host 0.0.0.0 --port 8000
```
