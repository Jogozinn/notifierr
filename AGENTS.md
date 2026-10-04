# Suggested AGENTS.md section for the Notifierr repository

## Autoscan reliability work

When modifying background polling, scan execution, worker health, or frontend refresh behavior:

- Read `CODEX_NOTIFIER_RELIABILITY_TASK.md` before editing.
- Treat scheduled scanning as a reliability-critical feature.
- Do not rely only on process-local locks when more than one process may run.
- Keep manual and background scan history distinguishable.
- Persist worker health and cycle outcomes; do not make the frontend infer health from stale item data.
- Catch ordinary cycle failures and allow subsequent cycles to run; preserve cancellation semantics on shutdown.
- Use deterministic scheduler tests with a fake clock/sleeper.
- Mock eBay in all automated tests and never log or commit credentials.
- Support the repository’s existing databases and migration conventions.
- Run backend tests, frontend tests/build, migration checks, and the accelerated soak test before declaring completion.
- Do not use Uvicorn `--reload` in normal-operation scripts.
- Prefer focused, reviewable changes over an unrelated rewrite.
