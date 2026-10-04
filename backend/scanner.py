"""HTTP-free scanner entrypoint: python -m backend.scanner [--once]."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import signal
from datetime import datetime, timezone
from typing import Any, Sequence


def _load_runtime():
    os.environ.setdefault("NOTIFIERR_ROLE", "scanner")
    from . import main as runtime

    return runtime


def _validate_runtime(runtime: Any, *, once: bool) -> None:
    if runtime.settings.process_role != "scanner":
        raise RuntimeError("Scanner entrypoint requires NOTIFIERR_ROLE=scanner")
    if not once and not runtime.settings.background_poll_enabled:
        raise RuntimeError("Scanner requires BACKGROUND_POLL_ENABLED=true")
    if (not once or runtime.settings.background_poll_enabled) and not runtime.settings.ebay_configured:
        raise RuntimeError("Scanner requires EBAY_CLIENT_ID and EBAY_CLIENT_SECRET")


async def run() -> None:
    """Run the existing continuous scanner service."""
    runtime = _load_runtime()
    _validate_runtime(runtime, once=False)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for name in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(name, stop.set)
        except NotImplementedError:  # Windows event loop
            signal.signal(name, lambda *_: loop.call_soon_threadsafe(stop.set))
    async with runtime.lifespan(runtime.app):
        runtime.logger.info("Scanner service started worker_id=%s", runtime.WORKER_ID)
        await stop.wait()
        runtime.logger.info("Scanner service stopping worker_id=%s", runtime.WORKER_ID)


def _once_result(runtime: Any, outcome: str, **details: Any) -> dict[str, Any]:
    return {
        "event": "scanner_once_completed",
        "outcome": outcome,
        "worker_id": runtime.WORKER_ID,
        **details,
    }


async def run_once(runtime: Any | None = None) -> dict[str, Any]:
    """Run no more than one scheduled scan attempt and return its final evidence."""
    runtime = runtime or _load_runtime()
    _validate_runtime(runtime, once=True)

    if not runtime.settings.background_poll_enabled:
        return _once_result(runtime, "disabled", reason="background_poll_disabled")

    acquired, lease, owner_state = runtime._acquire_background_leadership(
        datetime.now(timezone.utc)
    )
    if not acquired:
        return _once_result(
            runtime,
            "lease_contention",
            reason="another_worker_owns_scheduler_lease",
            lease_worker_id=str((lease or {}).get("worker_id") or ""),
            lease_owner_state=getattr(owner_state, "state", None),
        )

    renewal_task = asyncio.create_task(runtime._background_leadership_renewer())
    try:
        active_users = runtime._active_shared_scan_users(background_mode=False)
        if not active_users:
            cycle_id = runtime._persist_skipped_scan_cycle(
                mode="shared_background",
                reason="no_active_users",
                users_considered=0,
                users_scanned=0,
            )
            return _once_result(
                runtime, "no_active_users", reason="no_active_users", scan_cycle_id=cycle_id
            )

        enabled_users = [user for user in active_users if user.background_poll_enabled]
        if not enabled_users:
            cycle_id = runtime._persist_skipped_scan_cycle(
                mode="shared_background",
                reason="polling_disabled",
                users_considered=len(active_users),
                users_scanned=0,
            )
            return _once_result(
                runtime,
                "disabled",
                reason="user_polling_disabled",
                scan_cycle_id=cycle_id,
                users_considered=len(active_users),
            )

        outside_window = all(
            not runtime._background_poll_is_active(user) for user in enabled_users
        )
        summary = await runtime._await_with_background_leadership(
            asyncio.create_task(
                runtime.scan_shared_once(
                    limit=runtime.settings.max_results_per_keyword,
                    notify=True,
                    triggered_by_user=None,
                    background_mode=True,
                    cycle_mode="shared_background",
                    offload_scan_work=True,
                )
            ),
            renewal_task,
        )
        cycles = runtime.storage.list_scan_cycles(limit=1)
        cycle_id = int(cycles[0]["id"]) if cycles else None
        reason = str(summary.get("reason") or "")
        if outside_window and reason == "no_active_users":
            outcome = "outside_active_window"
        elif reason == "scan_already_running":
            outcome = "lease_contention"
        elif reason in {"no_active_users", "no_enabled_keywords"}:
            outcome = reason
        elif summary.get("skipped"):
            outcome = "skipped"
        else:
            outcome = "success"
        return _once_result(
            runtime,
            outcome,
            reason=reason or None,
            scan_cycle_id=cycle_id,
            summary=summary,
        )
    finally:
        if not renewal_task.done():
            renewal_task.cancel()
        await asyncio.gather(renewal_task, return_exceptions=True)
        runtime.storage.release_worker_lease(
            runtime.BACKGROUND_LEASE_NAME, runtime.WORKER_ID
        )


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--once",
        action="store_true",
        help="Attempt one scheduled scan cycle, emit a final JSON record, and exit",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        if args.once:
            result = asyncio.run(run_once())
            print(json.dumps(result, sort_keys=True, default=str), flush=True)
        else:
            asyncio.run(run())
    except Exception as exc:
        print(
            json.dumps(
                {
                    "event": "scanner_once_failed" if args.once else "scanner_failed",
                    "error_type": type(exc).__name__,
                },
                sort_keys=True,
            ),
            flush=True,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
