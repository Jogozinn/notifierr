from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime, timezone
from typing import Any

from backend import main
from backend.scan_lease import inspect_lease_owner, parse_utc, run_with_scan_lease


def _safe_lease(lease: dict[str, Any] | None) -> dict[str, Any] | None:
    if not lease:
        return None
    return {
        key: lease.get(key)
        for key in (
            "lease_name", "worker_id", "hostname", "process_id", "acquired_at",
            "heartbeat_at", "expires_at", "previous_worker_id", "takeover_reason",
        )
    }


def _snapshot() -> dict[str, Any]:
    lease = main.storage.get_worker_lease(main.GLOBAL_SCAN_LEASE_NAME)
    owner = inspect_lease_owner(
        lease,
        local_hostname=main._hostname(),
        current_worker_id=main.WORKER_ID,
        current_process_id=main._process_id(),
    ) if lease else None
    return {
        "lease": _safe_lease(lease),
        "owner_state": owner.state if owner else "absent",
        "owner_reason": owner.reason if owner else "no lease",
        "unfinished_cycles": [
            {
                key: row.get(key)
                for key in ("id", "mode", "status", "started_at", "worker_id", "hostname", "process_id")
            }
            for row in main.storage.list_unfinished_scan_cycles()
        ],
    }


async def _apply_recovery() -> dict[str, Any]:
    before = _snapshot()
    lease = before["lease"]
    expiry = parse_utc((lease or {}).get("expires_at"))
    active = bool(expiry and expiry > datetime.now(timezone.utc))
    if active and before["owner_state"] != "absent":
        raise RuntimeError(
            "Refusing recovery: active global scan lease owner was not confirmed absent "
            f"({before['owner_state']}: {before['owner_reason']})"
        )

    async def no_op() -> None:
        return None

    result = await run_with_scan_lease(
        main.storage,
        no_op,
        lease_name=main.GLOBAL_SCAN_LEASE_NAME,
        worker_id=main.WORKER_ID,
        hostname=main._hostname(),
        process_id=main._process_id(),
    )
    if not result.acquired:
        raise RuntimeError("Recovery lease acquisition lost an atomic race; no cleanup was applied")
    return {
        "before": before,
        "takeover_reason": result.claim.takeover_reason if result.claim else "",
        "reconciled_cycle_ids": list(result.reconciled_cycle_ids),
        "after": _snapshot(),
    }


def main_cli() -> int:
    parser = argparse.ArgumentParser(description="Inspect or safely recover abandoned Notifierr autoscan state.")
    parser.add_argument("--apply", action="store_true", help="Apply owner-checked lease takeover and cycle reconciliation.")
    args = parser.parse_args()
    payload = asyncio.run(_apply_recovery()) if args.apply else _snapshot()
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main_cli())
