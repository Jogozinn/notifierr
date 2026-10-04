# Cloud runtime deployment contract

This is the deployment contract for the current cloud-ready application. Every first production deployment uses a **fresh PostgreSQL** database; the old database remains an archive/reference.

```text
React static host --HTTPS--> FastAPI API ----+----> managed PostgreSQL
                                              |
                         scan cron (10 min) ---+--- retention daily cron
```

Use one backend image for all roles. The API serves HTTP only; the scan cron and retention cron serve no HTTP. `python -m backend.scanner --once` claims the `background_poll` database lease for one invocation, and the separate global scan lease serializes scan work with manual scans. Retention uses its own lease. No Redis or shared filesystem is required. The continuous `python -m backend.scanner` command remains available for platforms with a dedicated worker service.

## Deployment order

1. Create an **empty** managed PostgreSQL database. Set `DB_BACKEND=postgres`, `DATABASE_URL`, and server secrets in the host's environment/secret manager. Never use the historical database URL.
2. Run **one explicit migration command** with `NOTIFIERR_ENV=production NOTIFIERR_ROLE=migrate` and the backend image: `python -m alembic upgrade head`. Supply the shared database/auth/encryption environment required by production validation. It must finish before API, scanner, or retention start. Normal processes check schema revision and never apply migrations. On a two-job Northflank Sandbox, run this manually before scheduling the two recurring jobs; do not reserve a third recurring job slot for migrations.
3. Start the API with `NOTIFIERR_ENV=production NOTIFIERR_ROLE=api` and `python -m backend.api`. It checks schema and connectivity at startup, then bootstraps `ADMIN_EMAIL` / `ADMIN_PASSWORD` if no admin exists. Use a strong generated password, then rotate it after first login. The current guard requires these values on API restart too; retain them in the secret manager until a separate bootstrap command is added.
4. Log in as owner, set user search/notification settings, and enable that user's background polling. Schedule `python -m backend.scanner --once` every 10 minutes with `NOTIFIERR_ENV=production NOTIFIERR_ROLE=scanner BACKGROUND_POLL_ENABLED=true`. Set `BACKGROUND_POLL_ENABLED=true` on the API too for accurate status reporting; the API role never starts the poll loop. Match polling and active-window environment across API and scan cron.
5. Run retention daily as a one-shot job with `NOTIFIERR_ENV=production NOTIFIERR_ROLE=retention python -m backend.retention --configured --apply`. Omit `--apply` for a manual dry run. Never point it at the historical database.
6. Build the frontend with `VITE_API_BASE_URL=https://<public-api-host>` and deploy `frontend/dist` to static hosting. Set API `CORS_ALLOWED_ORIGINS` to the exact frontend HTTPS origin and `TRUSTED_HOSTS` to its public API host.
7. Generate Web Push keys once with `python -m backend.scripts.generate_vapid_keys`. Store all three printed values in the backend secret manager. `VAPID_PRIVATE_KEY_B64` and `APP_ENCRYPTION_KEY` are server-only. After login, install the PWA and use Settings to subscribe each device and send a test push.

The API and retention fail on missing/behind/unknown Alembic revisions; none mutates schema on startup. Database unavailability fails startup, and `/health/ready` returns 503 if connectivity later fails. The scanner refuses to start when eBay credentials or its enablement flag are missing. Marketplace outages appear in authenticated scanner status without failing API liveness.

## Container and commands

Build once from the repository root: `docker build -t notifierr-backend .`. The image uses pinned runtime package versions, a nonroot user, and copies only backend source, deploy JSON, and Alembic files. `.env`, SQLite files, audit output, and local logs are excluded. Default command: `python -m backend.api`. Override with `python -m backend.scanner`, `python -m backend.scanner --once`, `python -m backend.retention --configured --apply`, or `python -m alembic upgrade head`. Configure the hosting platform's API readiness probe at `/health/ready`; scheduled-job health uses exit status plus database evidence, so the shared image has no baked HTTP-only healthcheck.

`python -m backend.api` accepts `HOST` (default `0.0.0.0`) and `PORT` (default `8000`), with one Uvicorn worker and no reload. Behind an HTTPS proxy, set `TRUSTED_PROXY_IPS` to that proxy's IP/CIDR values; the default trusts forwarded headers only from loopback. One-shot scanner completion, clean skips, and failures emit a final structured JSON record. It exits zero for success, disabled polling, no active users, an inactive window, or lease contention; configuration, database/schema, and uncaught scan failures exit nonzero. The scheduler lease is owner-released after every completed invocation, while an abruptly interrupted scan remains protected by lease expiry/recovery.

## Configuration inventory

| Class | Values | Notes |
| --- | --- | --- |
| Secret environment | `DATABASE_URL`, `EBAY_CLIENT_ID`, `EBAY_CLIENT_SECRET`, `AUTH_SECRET_KEY`, `APP_ENCRYPTION_KEY`, `ADMIN_PASSWORD`, `VAPID_PRIVATE_KEY_B64`, optional `DISCORD_WEBHOOK_URL` | Server side only. User Discord destinations and push subscription capabilities are encrypted in PostgreSQL. Never place these in Vite variables. |
| Deployment environment | `NOTIFIERR_ENV`, `NOTIFIERR_ROLE`, `DB_BACKEND`, `AUTH_REQUIRED`, `ADMIN_EMAIL`, `ADMIN_DISPLAY_NAME`, `CORS_ALLOWED_ORIGINS`, `TRUSTED_HOSTS`, `TRUSTED_PROXY_IPS`, `HOST`, `PORT`, `BACKGROUND_POLL_ENABLED`, `BACKGROUND_POLL_SECONDS`, `BACKGROUND_POLL_ACTIVE_START/END/TIMEZONE`, `SEARCH_KEYWORDS`, `MAX_RESULTS_PER_KEYWORD`, `MIN_SCORE_TO_ALERT`, `MIN_PROFIT_TO_ALERT`, `RISKY_SCORE_RANGE`, `VAPID_PUBLIC_KEY`, `VAPID_SUBJECT`, eBay API/marketplace and cooldown settings, retention CLI intervals, `VITE_API_BASE_URL` at frontend build | Set on the appropriate service/job. `FRONTEND_ORIGIN` is a local convenience; production uses explicit CORS origins. The public API URL is a frontend build value. |
| Database user settings | Poll enabled/cadence, keywords, alert limits, notification choices/destination, repair/resale overrides, account and billing state | Persist across restarts/deploys. |
| Immutable deploy data | `backend/data/repair_values.json`, `resale_research.json`, `scoring_rules.json`; default keywords, score/profit thresholds, and `REPAIR_VALUES_PATH` / `RESALE_RESEARCH_PATH` / `SCORING_RULES_PATH` file selections | Versioned in the image; changes require deployment. User overrides take precedence where supported. |
| Derived runtime state | Scan cycles/results, source cooldown, leases, heartbeats, retention runs, rollups, health | Stored or derived from PostgreSQL. |

Production ignores local `.env` files and requires PostgreSQL, auth, a nondefault signing key, a valid Fernet key, explicit HTTPS CORS origins/trusted hosts for API, and initial admin credentials. There is no SQLite fallback. Missing eBay credentials block scanner startup, while the API may still serve settings and history.

Repair/resale baseline JSON is read-only in production. Per-user repair and resale overrides already live in PostgreSQL and remain the supported dashboard edits. The global repair-baseline editing endpoint returns 409 in production; update the versioned baseline and redeploy if a global default must change. No scoring or price values are changed here.

## Health and security

- `GET /health/live` answers process liveness only, without database or marketplace calls.
- `GET /health/ready` checks database connectivity and production schema revision. Failure returns 503 without disclosing a connection string.
- `GET /admin/scanner/status` requires a user token in production. It reports lease/heartbeat, last attempted/useful scan, cadence/window, consecutive failures/skips, source cooldown, recent item counts, and retention status. A fresh heartbeat cannot replace a useful scan. States include `running`, `scanning`, `degraded`, `outside_window`, `disabled`, `standby`, `stopped`, and `blocked`.
- Existing `/admin/polling/status` and `/admin/storage/status` remain for details. Production auth protects storage metrics and replay write mode remains admin-only.
- `GET /push/delivery` reports per-device selection, attempt, provider acceptance, failure, and invalid-subscription evidence. The Admin scan-status card shows active devices and accepted/failed counts.

Use HTTPS at the proxy and serve the API only through the trusted public host. The frontend stores no server secrets. Use exact CORS origins, never wildcards. Keep `AUTH_REQUIRED=true`; API bootstrap creates the owner before traffic is accepted, so first-user signup is not exposed by a healthy production API. Invite-only registration applies afterward.

## Local development

Default `NOTIFIERR_ENV=local` keeps SQLite or local PostgreSQL and optional `.env` loading. Run `python -m backend.api` and `npm run dev` in `frontend`; without `VITE_API_BASE_URL` the Vite dev app uses `http://127.0.0.1:8000`. To run scanner separately, set `NOTIFIERR_ROLE=scanner`, `BACKGROUND_POLL_ENABLED=true`, and eBay credentials, then run `python -m backend.scanner` continuously or `python -m backend.scanner --once` for one scheduled attempt. Existing combined `uvicorn backend.main:app` and Windows Task Scheduler workflows still work locally. For isolated local retention dry run, use `python -m backend.retention --sqlite <migrated-local-db>`; add `--apply` only when intended.

## Example service mapping

The existing Northflank Sandbox already uses one service slot for JournalMe. Keep it untouched and use the remaining resources as follows:

| Northflank resource | Name | Command / schedule |
| --- | --- | --- |
| Existing service | `journalme-api` | Existing JournalMe configuration; no changes |
| New service | `notifierr-api` | `python -m backend.api` |
| New cron job | `notifierr-scan` | `python -m backend.scanner --once`, every 10 minutes |
| New cron job | `notifierr-retention` | `python -m backend.retention --configured --apply`, daily |
| New database addon | `notifierr-postgres` | Fresh PostgreSQL used only by Notifierr |

Use Cloudflare Pages Free for the static frontend. Enable Northflank's no-overlap/concurrency-forbid option for `notifierr-scan`; the PostgreSQL leases remain authoritative if duplicate invocations still occur. Run `python -m alembic upgrade head` as an explicit manual deployment command before the new service or cron jobs; it is not a recurring third job. Confirm every selected resource shows $0 before creation and set billing alerts at the minimum supported threshold. Provider login, repository authorization, account ownership, and card verification are external operator actions.

## PWA and Web Push

The frontend ships a manifest, 192/512 icons, and a service worker that handles notifications without caching the application shell. This avoids stale JavaScript after deployment. On iPhone, open the HTTPS frontend in Safari, choose **Share → Add to Home Screen**, launch the installed app, sign in, and enable push in Settings. iOS requires this user gesture.

Subscriptions are user-scoped and encrypted at rest. Multiple devices are supported. Provider HTTP 404/410 disables the dead subscription. Notification clicks use `/?item=<marketplace-item-id>` and the dashboard opens the matching listing search. Discord remains an independent secondary destination.

## Read-only cloud evidence audit

Create a dedicated PostgreSQL login with `CONNECT`, schema `USAGE`, and `SELECT` only, then set its role default to read-only:

```sql
CREATE ROLE notifierr_auditor LOGIN PASSWORD '<generated-password>';
GRANT CONNECT ON DATABASE <database_name> TO notifierr_auditor;
GRANT USAGE ON SCHEMA public TO notifierr_auditor;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO notifierr_auditor;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO notifierr_auditor;
ALTER ROLE notifierr_auditor SET default_transaction_read_only = on;
```

Set `AUDIT_DATABASE_URL` in the Windows user environment to that role's URL. Run `scripts/run_cloud_audit.ps1` manually, or install the daily start-when-available task with `scripts/install_cloud_audit_task.ps1`. The auditor also sets every connection and transaction read-only, reads only rows beyond its local cursor, and stores reports/cursor under ignored local `audit/cloud*` paths. It proposes review items; it never rewrites production rules or rescoring history.
