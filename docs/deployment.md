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

Run the background polling worker in a separate process using the same app code. The app starts polling from the FastAPI lifespan hook. In hosted auth-required mode, the process only starts the loop when `BACKGROUND_POLL_ENABLED=true`; users are scanned only when their own `background_poll_enabled` setting is enabled.

The safe hosted pattern is one web process and one worker process both using the same environment, with the worker being the only instance that has background polling enabled. Multiple uvicorn/gunicorn workers can duplicate polling because each worker process has its own event loop. The app prevents duplicate poll loops inside one process and logs a warning when common multi-worker env vars indicate more than one worker, but it does not take a cross-process database lock.

Suggested worker start command:

```powershell
uvicorn backend.main:app --host 0.0.0.0 --port ${PORT:-8000}
```

Use environment to ensure only the worker has background polling enabled:

- Web process:
  - `BACKGROUND_POLL_ENABLED=false`
- Worker process:
  - `BACKGROUND_POLL_ENABLED=true`
  - `BACKGROUND_POLL_SECONDS=300`
  - `BACKGROUND_POLL_TIMEZONE=America/New_York`

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
- `BACKGROUND_POLL_ENABLED=false` on web processes
- `BACKGROUND_POLL_ENABLED=true` on the single polling worker process
- `EBAY_RATE_LIMIT_BACKOFF_SECONDS=900`

## First-time hosted database setup

```powershell
alembic upgrade head
python -m backend.db_check
```

## Local admin endpoint authentication

When `AUTH_REQUIRED=true`, admin endpoints require a bearer token for an active admin user. Do not disable auth for hosted debugging.

1. Set `AUTH_REQUIRED=true`, `AUTH_SECRET_KEY`, `ADMIN_EMAIL`, and `ADMIN_PASSWORD`.
2. Start the backend once; startup bootstraps the admin account if no admin exists.
3. Sign in from the frontend, or request a token directly:

```powershell
$body = @{ email = $env:ADMIN_EMAIL; password = $env:ADMIN_PASSWORD } | ConvertTo-Json
$token = (Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/auth/login" -ContentType "application/json" -Body $body).access_token
Invoke-RestMethod -Method Get -Uri "http://127.0.0.1:8000/admin/worker/status" -Headers @{ Authorization = "Bearer $token" }
```

If `ADMIN_EMAIL` already exists as a non-admin account, bootstrap is skipped; promote or create an admin through an existing admin session or first-user setup.

## Local SQLite mode

```powershell
$env:DB_BACKEND="sqlite"
$env:AUTH_REQUIRED="false"
$env:BACKGROUND_POLL_ENABLED="false"
uvicorn backend.main:app --reload
```

With `AUTH_REQUIRED=false`, local dev starts a lightweight background poll supervisor even when `BACKGROUND_POLL_ENABLED=false`; the local dashboard setting controls whether scans actually run. Set the dashboard's Background poll enabled setting and save notification settings to exercise automatic polling locally.

If eBay returns `429 Too Many Requests`, the backend returns HTTP 429 for manual scans and background polling backs off for `EBAY_RATE_LIMIT_BACKOFF_SECONDS` or eBay's `Retry-After` value, whichever is larger. Keep local polling at 300 seconds or slower after a 429; a 60-second interval can keep you pinned against eBay's rate limit.

## Hosted Postgres mode

```powershell
$env:DB_BACKEND="postgres"
$env:DATABASE_URL="postgresql://..."
$env:AUTH_REQUIRED="true"
$env:BACKGROUND_POLL_ENABLED="false"
$env:EBAY_RATE_LIMIT_BACKOFF_SECONDS="900"
alembic upgrade head
uvicorn backend.main:app --host 0.0.0.0 --port 8000
```

## Windows validation commands

```powershell
# Run focused backend tests.
python -m pytest tests\test_shared_polling.py tests\test_runtime_settings_bridge.py tests\test_admin_hosting.py

# Start local dev backend with SQLite.
$env:DB_BACKEND="sqlite"
$env:AUTH_REQUIRED="false"
$env:BACKGROUND_POLL_ENABLED="false"
$env:BACKGROUND_POLL_SECONDS="300"
$env:EBAY_RATE_LIMIT_BACKOFF_SECONDS="900"
python -m uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000

# Trigger one manual scan cycle from another PowerShell window.
Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/scan/run" -ContentType "application/json" -Body '{"notify":true}'

# Check process-local polling health/status in auth-disabled local mode.
Invoke-RestMethod -Method Get -Uri "http://127.0.0.1:8000/admin/polling/status"

# Replay deterministic traces from stored listings without calling eBay or sending alerts.
Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/admin/scan/trace-replay" -ContentType "application/json" -Body '{"limit":100}'

# Export a compact audit summary for a replay or live scan cycle.
Invoke-RestMethod -Method Get -Uri "http://127.0.0.1:8000/admin/scan/cycles/1/trace-export"

# Send a safe test notification through the configured Discord channel.
Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/admin/notifications/test"
```

Expected backend logs include `Started background poll loop`, `Background poll cycle started`, skipped polling messages when the user setting is off, `Background scan summary ... alerts_sent=...` or `Background shared scan summary ... alerts_sent=...`, notification send failures if Discord rejects a webhook, and `Background poll sleeping next_poll_seconds=...`.

## Polling and notification health

`GET /admin/polling/status` returns process-local in-memory polling status. In hosted mode (`AUTH_REQUIRED=true`) it requires an admin token. In local dev (`AUTH_REQUIRED=false`) it can be called directly.

The response intentionally excludes secrets, webhook URLs, raw listings, auth headers, and user private data. Expected fields:

- `process_poll_task_started`
- `duplicate_start_prevented`
- `auth_required`
- `background_poll_env_enabled`
- `local_or_user_polling_enabled`
- `cycle_running`
- `last_cycle_started_at`
- `last_cycle_finished_at`
- `last_success_at`
- `last_failure_at`
- `last_error`
- `last_sleep_seconds`
- `cycles_attempted`
- `cycles_succeeded`
- `cycles_failed`
- `last_scan_summary`
- `last_alerts_found`
- `last_alerts_sent`
- `last_notifications_attempted`
- `last_notifications_sent`
- `last_notifications_failed`

`POST /admin/notifications/test` sends `Notifierr test notification` through the existing Discord configuration. It does not create fake items and does not mark any listing as alerted. The compact response is:

- `attempted`
- `sent`
- `failed`
- `error`
