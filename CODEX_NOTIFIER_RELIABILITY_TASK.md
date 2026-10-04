# Codex Task: Repair Notifierr Autoscan Reliability End to End

## Mission

Repair the Notifierr application so its core use case is dependable:

- eBay is scanned automatically on a configurable cadence.
- A normal default is 10 minutes.
- The supported user-configurable range should include 5–20 minutes.
- Only one background poller may own and run scheduled scans at a time, even when multiple application processes exist.
- A failed scan must not permanently stop future scans.
- The frontend must show the true autoscan state and refresh when a background scan discovers or updates listings.
- Manual scans must remain available, but they must not mask a dead background poller.
- Local Windows operation must have a durable, non-development launch path.

Do not stop after auditing or writing a plan. Inspect the repository, implement the changes, add migrations and tests, run the test/build suite, and leave the repository in a reviewable state.

## Known evidence to reproduce and explain

Recent logs showed all of the following:

1. Startup reported `poll_seconds=900`, but completed cycles logged `next_poll_seconds=60`.
2. Independent cycle counters appeared nearly simultaneously, for example `cycle=2` and `cycle=150`, indicating duplicate pollers or processes.
3. eBay OAuth, search, and detail requests repeatedly returned HTTP 200, and scans completed with roughly 139–140 listings.
4. The user later saw a stale “last scan” time for many hours, while a manual scan immediately populated many phones.
5. The app had been launched with Uvicorn `--reload` on Windows.
6. A process-local scan lock may exist, but that cannot prevent duplicate scans across processes.

Treat these as leads, not as permission to assume the exact implementation. Reproduce the behavior with tests or controlled local runs before finalizing the repair.

## First phase: inspect and map the current system

Before editing, inspect:

- Root `AGENTS.md` and any nested instruction files.
- `backend/main.py` and application lifespan/startup/shutdown handling.
- Polling-loop implementation and interval-resolution logic.
- Configuration and settings precedence.
- Manual scan endpoint and shared scan function.
- Existing `_scan_lock` or other concurrency controls.
- Existing scan-cycle/source-status models and Alembic migrations.
- `/admin/polling/status` or equivalent health endpoint.
- `/items`, `/stats`, and any frontend data hooks or stores.
- Frontend component that displays “last scan”.
- Existing unit, integration, and frontend tests.
- Windows/local startup scripts and documentation.
- SQLite and PostgreSQL support, if both exist.

Write a brief implementation plan in the Codex task transcript, then execute it. Adapt names and locations to the actual repository rather than forcing this document’s suggested names.

## Required backend behavior

### 1. One authoritative polling interval

Create one function or service that resolves the effective polling interval.

Requirements:

- Default: 600 seconds.
- Supported normal configuration: 300–1200 seconds.
- Preserve a wider existing valid range only when the product already intentionally supports it.
- Define and document precedence among environment variables, persisted user/application settings, and defaults.
- The startup log, health endpoint, persisted worker state, and actual sleep/deadline must all report the same effective value.
- Remove accidental or hidden fallback/clamping to 60 seconds.
- A setting change should take effect predictably without spawning another worker.
- Add tests for every precedence branch and boundary.

Use monotonic time for waiting/deadlines where appropriate, while persisting UTC wall-clock timestamps for observability.

### 2. Exactly one scheduled worker across processes

Implement a cross-process lease/lock. A process-local `asyncio.Lock` alone is insufficient.

Preferred design:

- Reuse an existing database abstraction.
- Store a singleton worker lease with at least:
  - `lease_name`
  - `worker_id`
  - `hostname`
  - `pid`
  - `acquired_at`
  - `heartbeat_at`
  - `expires_at`
- Acquire and renew the lease transactionally.
- Only the lease owner may start a scheduled scan.
- Permit safe takeover after the lease expires.
- Release the lease on graceful shutdown when still owned by that worker.
- Never let one worker release another worker’s lease.
- Handle SQLite and PostgreSQL correctly if both are supported.
- If PostgreSQL advisory locks are used, still expose durable worker-health data. A transactional lease-row implementation is acceptable for both databases.
- Add concurrency tests that simulate two application instances trying to become leader.

Keep an in-process lock as well so manual and scheduled scans cannot overlap within one process. Ensure the cross-process design also prevents a manual scan in one process from racing a scheduled scan in another, either through a separate global scan lease or a clearly safe transactional mechanism.

### 3. Self-healing scheduler

Refactor polling into a small, testable service rather than leaving all logic embedded in `backend/main.py`.

Every scheduled cycle must:

1. Confirm or renew lease ownership.
2. Resolve the effective interval.
3. Persist cycle start with `trigger="background"`.
4. Evaluate gates such as enabled/disabled state, account eligibility, active hours, cooldowns, or source availability.
5. Run the shared scan operation, or persist an explicit skip reason.
6. Persist completion, metrics, errors, and the next scheduled time.
7. Continue scheduling after ordinary failures.

Error rules:

- Catch ordinary cycle exceptions, persist a concise error plus useful diagnostic context, increment consecutive failures, and continue.
- Re-raise cancellation during application shutdown.
- A single malformed listing, network error, database error, notification error, or frontend-independent error must not silently kill the loop.
- Use bounded exponential backoff for consecutive infrastructure failures.
- Handle eBay HTTP 429 separately and honor `Retry-After` when present.
- Reset consecutive-failure state after a successful cycle.
- Do not mislabel successful HTTP 200 traffic as throttling.
- Prevent overlapping cycles. If a cycle exceeds its interval, record that fact and start the next cycle only after the current one completes unless the repository has a safer intentional scheduling policy.

Keep a strong reference to the task. Add a supervisor/manager that records an unexpected task exit and restarts it when appropriate. Avoid restart storms.

### 4. Durable worker health and scan history

Inspect existing instrumentation tables first; extend rather than duplicate them.

Persist enough state to answer:

- Is autoscan enabled?
- Is this process the lease owner?
- Is a worker running, degraded, disabled, stopped, or stale?
- What are the configured and effective intervals?
- When was the last heartbeat?
- When did the last background cycle start?
- When did the last successful background cycle finish?
- When is the next scheduled background scan?
- What is the current cycle ID?
- How many consecutive failures occurred?
- What was the last error?
- What was the last skip reason?
- Which worker, host, and PID ran the cycle?
- When was the last manual scan?

Each cycle should retain, where available:

- `id`
- `trigger`: `background` or `manual`
- `status`: `running`, `succeeded`, `failed`, or `skipped`
- start and finish timestamps
- duration
- effective interval
- worker ID
- searches/keywords performed
- API-call count
- listings scanned
- new listings found
- updated listings
- alerts sent
- duplicate count
- skip reason
- error class/message
- whether rate limiting occurred

Use safe schema migrations with both upgrade and downgrade paths. Preserve existing data and API compatibility.

### 5. Health/status API

Upgrade the existing polling-status endpoint or add a compatible replacement.

The response should include at least:

```json
{
  "enabled": true,
  "state": "running",
  "is_leader": true,
  "worker_id": "host-pid-uuid",
  "configured_interval_seconds": 600,
  "effective_interval_seconds": 600,
  "last_heartbeat_at": "...",
  "last_background_started_at": "...",
  "last_background_succeeded_at": "...",
  "last_manual_succeeded_at": "...",
  "next_scheduled_at": "...",
  "current_cycle_id": 123,
  "consecutive_failures": 0,
  "last_error": null,
  "last_skip_reason": null,
  "stale": false,
  "stale_after_seconds": 1260
}
```

Requirements:

- Compute staleness from durable state, not only process memory.
- Mark the worker stale after approximately two missed intervals plus a small documented grace period.
- Return a useful disabled reason when polling is intentionally disabled.
- Avoid leaking secrets, tokens, or full tracebacks to normal frontend users.
- Keep detailed tracebacks in server logs or an admin-only diagnostic field if the project already has appropriate authorization.

## Required frontend behavior

Locate the actual frontend stack and follow its patterns.

### Autoscan status

Display a clear state near the existing last-scan information:

- Running
- Scanning now
- Degraded
- Stopped/stale
- Disabled

Show:

- Last successful background scan
- Last manual scan separately
- Next scheduled scan
- Effective interval
- Current progress when available
- Last concise error or skip reason
- A stale warning after two missed cycles

Do not let a manual scan make a dead background poller appear healthy.

### Automatic refresh

- Poll the status endpoint every 30–60 seconds, using the project’s existing query/data-fetching library.
- When the latest successful background cycle ID or completion timestamp changes, invalidate/refetch listings and stats.
- Newly discovered phones must appear without a browser reload or manual Scan click.
- Avoid duplicate requests, runaway timers, and updates after component unmount.
- Keep behavior sensible when the tab is hidden or the network is offline.
- Add frontend tests for state rendering, staleness, manual/background timestamp separation, and data invalidation after a completed background cycle.
- Preserve accessibility and existing responsive layout.

## Local Windows durability

Do not use `--reload` for normal operation.

Add or update a production-like local launch path, preferably:

- `scripts/run_notifier.ps1`
- `scripts/install_notifier_task.ps1`
- `scripts/uninstall_notifier_task.ps1`

The runner should:

- Activate or directly use the project virtual environment.
- Apply Alembic migrations safely.
- Set or read documented environment variables.
- Start one Uvicorn process without `--reload`.
- Write rotating/persistent logs to a documented location.
- Exit nonzero when startup fails.

The Task Scheduler installer should, where permissions allow:

- Start at boot or user logon as appropriate.
- Restart on failure.
- Avoid parallel task instances.
- Use the correct working directory.
- Document sleep/wake limitations.
- Be idempotent or detect an existing task.
- Never embed eBay secrets into source-controlled scripts.

Also document a hosted deployment path if the repository already supports one, but do not invent an unrelated infrastructure stack.

## Testing requirements

Use mocks/fakes for eBay. Do not make live eBay requests in automated tests.

At minimum add tests for:

1. Interval precedence and the removal of the accidental 60-second behavior.
2. Two application instances competing for one worker lease.
3. Lease expiry and takeover.
4. Lease-owner-only release.
5. Manual and background global scan serialization.
6. A scan exception being persisted while the following scheduled cycle still runs.
7. Cancellation shutting down cleanly without being recorded as an ordinary failure.
8. HTTP 429 honoring `Retry-After`.
9. Successful HTTP 200 cycles not entering throttle backoff.
10. Worker staleness after two missed intervals.
11. Manual and background timestamps remaining distinct.
12. Status endpoint output.
13. Frontend status rendering and automatic item/stat refresh.
14. Alembic upgrade and downgrade on a temporary database.
15. Existing test-suite compatibility.

Prefer a fake clock or injectable sleeper so scheduler tests are deterministic and fast.

Create an accelerated soak/reliability test that simulates at least 24 hours of cycles without real waiting. It should inject intermittent failures, lease contention, and restarts and assert:

- No overlapping scheduled scans.
- No unexplained gap greater than two effective intervals.
- Recovery after injected failures.
- Exactly one leader at any instant.
- Correct persisted cycle history.

Also add a documented real 24-hour soak procedure for final operator validation.

## Observability

Use the project’s logging conventions. Include structured or consistently parseable fields:

- `worker_id`
- `cycle_id`
- `trigger`
- `lease_state`
- `configured_interval_seconds`
- `effective_interval_seconds`
- `next_scheduled_at`
- `duration_ms`
- `status`
- `skip_reason`
- `consecutive_failures`

Log leadership acquisition/loss, unexpected task exits, interval changes, cycle starts/completions, skips, and recovery. Avoid noisy per-second logs and never log credentials.

## Compatibility and safety constraints

- Do not delete or reset the user’s real database.
- Do not commit secrets or `.env` contents.
- Do not call live eBay APIs in tests.
- Preserve existing scoring, classification, notification, watch/ignore, and manual-review behavior unless a change is required for correctness.
- Preserve existing public API shapes where possible; add fields compatibly.
- Avoid a broad rewrite when a focused service extraction is sufficient.
- Follow existing formatter, linter, type-checker, and test conventions.
- Add comments for concurrency invariants and lease semantics.
- Update README/operator documentation and sample environment files.
- Do not claim the repair is complete merely because unit tests pass.

## Commands and validation

Discover the repository’s real commands. Run all applicable checks, such as:

- Backend formatter/linter
- Backend type checker
- Backend unit/integration tests
- Alembic upgrade/downgrade test
- Frontend formatter/linter
- Frontend unit tests
- Frontend production build
- Accelerated soak test

If a check cannot run because a dependency or service is genuinely unavailable, record the exact command, error, and what remains to validate. Do not silently skip it.

## Completion criteria

Do not finish until the implementation meets these criteria:

1. A configured 10-minute interval is reported and used as 600 seconds everywhere.
2. No unexplained 60-second fallback remains.
3. Exactly one background leader exists across multiple application processes.
4. A failed cycle does not permanently stop the scheduler.
5. Manual and background scans cannot corrupt or race one another.
6. Durable health survives API-process restarts.
7. The frontend declares the poller stale after two missed cycles.
8. The frontend refreshes listings/stats after a completed background cycle.
9. Manual and background scan times are visibly distinct.
10. Migrations are reversible and preserve existing data.
11. Automated tests and the accelerated 24-hour simulation pass.
12. Normal Windows startup does not use Uvicorn reload mode.

## Final Codex response

At completion, provide:

- Root-cause findings, distinguishing proven causes from hypotheses.
- Architecture and concurrency decisions.
- Database schema/migration summary.
- Frontend behavior summary.
- Windows operating instructions.
- Exact commands run and their outcomes.
- Tests added.
- Changed-file list.
- Any remaining manual validation, especially the real 24-hour soak test.
- A concise rollback procedure.

Do not merely return a plan. Make the changes.
