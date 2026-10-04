# Forward retention worker

Apply Alembic revision `20261003_0010` before running code that uses forward evidence. Existing rows receive `retention_managed = 0` and no invented `first_seen_at` or `first_scored_at`. Only new listing, scan, trace, and notification records are marked for retention.

Run a read-only preview against an isolated SQLite database:

```sh
python -m backend.retention --sqlite /path/to/notifierr.sqlite3
```

Run cleanup only after reviewing the preview:

```sh
python -m backend.retention --sqlite /path/to/notifierr.sqlite3 --apply
```

The same module accepts `--database-url` for PostgreSQL. It does not read the configured database URL implicitly. A future cron or worker can invoke this command. No production retention is enabled by this change.

Defaults: ordinary traces 14 days, traces for reviewed or purchased listings 90 days, search result and execution detail 30 days, routine notification attempts 90 days, stale unreviewed listings 30 days, 500 rows per delete transaction. Override with `--trace-days`, `--reviewed-trace-days`, `--search-days`, `--notification-days`, `--stale-item-days`, and `--batch-size`.

An applied run takes the shared `retention` lease before processing and renews it between batches. It aggregates complete eligible search days into `search_daily_rollups`, then commits a `search_rollup_days` marker in the same transaction. Search detail is deleted only for marked days. If processing stops after aggregation, the next run skips the already aggregated day and resumes detail deletion. Rows are deleted in child to parent order and in bounded transactions. `retention_runs` records applied counts and cutoffs; dry-run does not write to the database.

Human feedback, outcomes, corrections, reviews, and user notes protect their listing; their detailed traces have the longer window. Pending or failed notification attempts remain. A listing with any remaining trace or search result stays until those references have expired. Legacy unmarked records remain untouched by this worker. Storage counters are available at the authenticated admin endpoint `/admin/storage/status`.
