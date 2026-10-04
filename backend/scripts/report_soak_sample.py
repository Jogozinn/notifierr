from __future__ import annotations

import datetime
import json

from backend.config import load_settings
from backend.db import create_storage


def build_sample() -> dict:
    db = create_storage(load_settings())
    lease = db.get_worker_lease("background_poll") or {}
    heartbeat = next(
        (row for row in db.get_worker_heartbeats() if row.get("worker_name") == "background_poll"),
        {},
    )
    cycles = [
        {
            key: row.get(key)
            for key in (
                "id",
                "mode",
                "status",
                "started_at",
                "finished_at",
                "background_poll_seconds",
                "process_id",
                "hostname",
                "items_found",
                "new_items_found",
                "duplicate_items",
                "alerts_sent",
                "skip_reason",
                "error_category",
                "http_status",
                "retry_after_seconds",
            )
        }
        for row in db.list_scan_cycles(limit=20)
        if row.get("mode") in {"local_background", "shared_background"}
    ]
    return {
        "observed_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "lease": {
            key: lease.get(key)
            for key in ("worker_id", "process_id", "heartbeat_at", "expires_at")
        },
        "heartbeat": {
            key: heartbeat.get(key)
            for key in (
                "process_id",
                "last_seen_at",
                "status",
                "last_cycle_id",
                "last_error",
                "next_wake_at",
            )
        },
        "global_scan_held": bool(db.get_worker_lease("global_scan")),
        "cycles": cycles,
    }


if __name__ == "__main__":
    print(json.dumps(build_sample()))
