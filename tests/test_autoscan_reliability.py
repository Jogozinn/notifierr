from __future__ import annotations

import asyncio
from types import SimpleNamespace
from datetime import datetime, timedelta, timezone

import pytest

from backend import main
from backend.config import Settings
from backend.polling import consecutive_cycle_outcomes, resolve_poll_interval, stale_after_seconds
from backend.scan_lease import (
    GLOBAL_SCAN_LEASE_RENEW_SECONDS,
    GLOBAL_SCAN_LEASE_TTL_SECONDS,
    acquire_scan_lease,
    reconcile_abandoned_scan_cycles,
    run_with_scan_lease,
)
from backend.storage import Storage


def _configure_background_leadership(monkeypatch, tmp_path, *, worker_id: str = "host-200-newworker"):
    storage = Storage(tmp_path / "background-leadership.sqlite3")
    monkeypatch.setattr(main, "storage", storage)
    monkeypatch.setattr(main, "settings", Settings(auth_required=False, background_poll_enabled=True, background_poll_seconds=600))
    monkeypatch.setattr(main, "WORKER_ID", worker_id)
    monkeypatch.setattr(main, "_hostname", lambda: "host")
    monkeypatch.setattr(main, "_process_id", lambda: 200)
    monkeypatch.setattr(main, "_background_leadership_acquired_at", None)
    monkeypatch.setattr(main, "_background_leadership_lost_at", None)
    return storage


def test_interval_precedence_defaults_and_boundaries():
    assert resolve_poll_interval(configured=None).effective_seconds == 600
    assert resolve_poll_interval(configured=900).effective_seconds == 900
    assert resolve_poll_interval(configured=900, persisted=[1200, 600]).effective_seconds == 600
    assert resolve_poll_interval(configured=60).effective_seconds == 300
    assert resolve_poll_interval(configured=9999).effective_seconds == 1200
    assert resolve_poll_interval(configured=900, persisted=[299]).effective_seconds == 300


def test_worker_lease_competition_expiry_and_owner_only_release(tmp_path):
    storage = Storage(tmp_path / "leases.sqlite3")
    now = datetime(2026, 7, 11, tzinfo=timezone.utc)
    expiry = now + timedelta(minutes=21)
    assert storage.acquire_worker_lease("background_poll", "a", hostname="one", process_id=1, now=now.isoformat(), expires_at=expiry.isoformat())
    assert not storage.acquire_worker_lease("background_poll", "b", hostname="two", process_id=2, now=now.isoformat(), expires_at=expiry.isoformat())
    assert not storage.release_worker_lease("background_poll", "b")
    assert storage.get_worker_lease("background_poll")["worker_id"] == "a"
    takeover = expiry + timedelta(seconds=1)
    assert storage.acquire_worker_lease("background_poll", "b", hostname="two", process_id=2, now=takeover.isoformat(), expires_at=(takeover + timedelta(minutes=21)).isoformat())
    assert storage.get_worker_lease("background_poll")["worker_id"] == "b"


def test_background_leadership_uses_short_owner_checked_lease(monkeypatch, tmp_path):
    storage = _configure_background_leadership(monkeypatch, tmp_path)
    now = datetime(2026, 7, 17, 12, tzinfo=timezone.utc)

    acquired, lease, _owner_state = main._acquire_background_leadership(now)

    assert acquired is True
    assert lease["worker_id"] == main.WORKER_ID
    assert lease["process_id"] == 200
    assert datetime.fromisoformat(lease["expires_at"]) == now + timedelta(seconds=main.BACKGROUND_LEASE_TTL_SECONDS)
    assert main._renew_background_leadership(now + timedelta(seconds=main.BACKGROUND_LEASE_RENEW_SECONDS))
    assert not storage.renew_worker_lease(
        main.BACKGROUND_LEASE_NAME,
        "host-999-other",
        now=(now + timedelta(seconds=70)).isoformat(),
        expires_at=(now + timedelta(seconds=250)).isoformat(),
    )
    assert not storage.release_worker_lease(main.BACKGROUND_LEASE_NAME, "host-999-other")
    assert storage.get_worker_lease(main.BACKGROUND_LEASE_NAME)["worker_id"] == main.WORKER_ID


def test_background_leadership_second_live_worker_cannot_steal(monkeypatch, tmp_path):
    storage = _configure_background_leadership(monkeypatch, tmp_path)
    now = datetime(2026, 7, 17, 12, tzinfo=timezone.utc)
    assert storage.acquire_worker_lease(
        main.BACKGROUND_LEASE_NAME,
        "host-101-liveworker",
        hostname="host",
        process_id=101,
        now=now.isoformat(),
        expires_at=(now + timedelta(hours=1)).isoformat(),
    )
    monkeypatch.setattr(
        main,
        "inspect_lease_owner",
        lambda *args, **kwargs: SimpleNamespace(
            state="live_notifierr",
            reason="PID 101 is a Notifierr process",
            pid_exists=True,
            belongs_to_notifierr=True,
        ),
    )

    acquired, lease, owner_state = main._acquire_background_leadership(now + timedelta(seconds=1))

    assert acquired is False
    assert owner_state.state == "live_notifierr"
    assert lease["worker_id"] == "host-101-liveworker"
    assert storage.get_worker_lease(main.BACKGROUND_LEASE_NAME)["worker_id"] == "host-101-liveworker"


def test_background_leadership_confirmed_dead_local_pid_takes_over_immediately(monkeypatch, tmp_path):
    storage = _configure_background_leadership(monkeypatch, tmp_path)
    now = datetime(2026, 7, 17, 12, tzinfo=timezone.utc)
    assert storage.acquire_worker_lease(
        main.BACKGROUND_LEASE_NAME,
        "host-11340-oldworker",
        hostname="host",
        process_id=11340,
        now=now.isoformat(),
        expires_at=(now + timedelta(hours=1)).isoformat(),
    )
    monkeypatch.setattr(
        main,
        "inspect_lease_owner",
        lambda *args, **kwargs: SimpleNamespace(
            state="absent",
            reason="confirmed owner PID 11340 is absent",
            pid_exists=False,
            belongs_to_notifierr=None,
        ),
    )

    acquired, lease, owner_state = main._acquire_background_leadership(now + timedelta(seconds=1))

    assert acquired is True
    assert owner_state.state == "absent"
    assert lease["worker_id"] == main.WORKER_ID
    assert lease["previous_worker_id"] == "host-11340-oldworker"
    assert lease["takeover_reason"] == "confirmed_local_owner_absent"


def test_background_leadership_unrelated_or_remote_owner_waits_for_expiration(monkeypatch, tmp_path):
    storage = _configure_background_leadership(monkeypatch, tmp_path)
    now = datetime(2026, 7, 17, 12, tzinfo=timezone.utc)
    assert storage.acquire_worker_lease(
        main.BACKGROUND_LEASE_NAME,
        "host-77-unrelated",
        hostname="host",
        process_id=77,
        now=now.isoformat(),
        expires_at=(now + timedelta(seconds=main.BACKGROUND_LEASE_TTL_SECONDS)).isoformat(),
    )
    monkeypatch.setattr(
        main,
        "inspect_lease_owner",
        lambda *args, **kwargs: SimpleNamespace(
            state="live_unrelated",
            reason="PID 77 exists but is not Notifierr",
            pid_exists=True,
            belongs_to_notifierr=False,
        ),
    )

    before_expiry, lease, owner_state = main._acquire_background_leadership(
        now + timedelta(seconds=main.BACKGROUND_LEASE_TTL_SECONDS - 1)
    )
    after_expiry, expired_lease, _expired_state = main._acquire_background_leadership(
        now + timedelta(seconds=main.BACKGROUND_LEASE_TTL_SECONDS + 1)
    )

    assert before_expiry is False
    assert owner_state.state == "live_unrelated"
    assert lease["worker_id"] == "host-77-unrelated"
    assert after_expiry is True
    assert expired_lease["worker_id"] == main.WORKER_ID
    assert expired_lease["takeover_reason"] == "lease_expired"


def test_background_leadership_health_reports_dead_owner_block(monkeypatch, tmp_path):
    storage = _configure_background_leadership(monkeypatch, tmp_path)
    now = datetime.now(timezone.utc)
    assert storage.acquire_worker_lease(
        main.BACKGROUND_LEASE_NAME,
        "host-11340-oldworker",
        hostname="host",
        process_id=11340,
        now=now.isoformat(),
        expires_at=(now + timedelta(minutes=5)).isoformat(),
    )
    storage.update_worker_heartbeat(
        worker_name="background_poll",
        process_id=11340,
        hostname="host",
        started_at=(now - timedelta(minutes=1)).isoformat(),
        status="sleeping",
    )
    monkeypatch.setattr(
        main,
        "inspect_lease_owner",
        lambda *args, **kwargs: SimpleNamespace(
            state="absent",
            reason="confirmed owner PID 11340 is absent",
            pid_exists=False,
            belongs_to_notifierr=None,
        ),
    )
    monkeypatch.setattr(main, "_background_poll_is_active", lambda current_settings, now=None: True)

    payload = main.admin_polling_status({})

    assert payload["state"] == "blocked"
    assert payload["worker_lease_state"] == "orphaned"
    assert payload["scheduler_lease_owner_state"] == "absent"
    assert payload["scheduler_lease_owner_alive"] is False
    assert payload["scheduler_leadership_ttl_seconds"] == 180
    assert "dead worker PID 11340" in payload["reason"]


def test_global_scan_lease_serializes_manual_and_background(tmp_path):
    storage = Storage(tmp_path / "scan-lease.sqlite3")
    now = datetime.now(timezone.utc)
    expiry = now + timedelta(seconds=GLOBAL_SCAN_LEASE_TTL_SECONDS)
    assert storage.acquire_worker_lease("global_scan", "manual", hostname="one", process_id=1, now=now.isoformat(), expires_at=expiry.isoformat())
    assert not storage.acquire_worker_lease("global_scan", "background", hostname="two", process_id=2, now=now.isoformat(), expires_at=expiry.isoformat())
    assert storage.release_worker_lease("global_scan", "manual")
    assert storage.acquire_worker_lease("global_scan", "background", hostname="two", process_id=2, now=now.isoformat(), expires_at=expiry.isoformat())


def test_staleness_is_two_intervals_plus_grace():
    assert stale_after_seconds(600) == 1260
    assert stale_after_seconds(300) == 660


def test_accelerated_24_hour_soak_with_failures_contention_and_restart(tmp_path):
    storage = Storage(tmp_path / "soak.sqlite3")
    start = datetime(2026, 7, 11, tzinfo=timezone.utc)
    interval = timedelta(minutes=10)
    leaders = []
    successful_times = []
    for cycle_number in range(24 * 6):
        now = start + cycle_number * interval
        owner = "worker-a" if cycle_number < 72 else "worker-b"
        other = "worker-b" if owner == "worker-a" else "worker-a"
        expires = now + timedelta(minutes=21)
        if cycle_number == 72:
            storage.release_worker_lease("background_poll", "worker-a")
        assert storage.acquire_worker_lease("background_poll", owner, hostname=owner, process_id=1, now=now.isoformat(), expires_at=expires.isoformat())
        assert not storage.acquire_worker_lease("background_poll", other, hostname=other, process_id=2, now=now.isoformat(), expires_at=expires.isoformat())
        leaders.append(storage.get_worker_lease("background_poll")["worker_id"])
        cycle_id = storage.create_scan_cycle(mode="local_background", background_poll_enabled=True, background_poll_seconds=600)
        injected_failure = cycle_number in {17, 53, 101}
        storage.finish_scan_cycle(cycle_id, status="failed" if injected_failure else "completed", error_message="injected" if injected_failure else "")
        if not injected_failure:
            successful_times.append(now)
    assert len(leaders) == 144
    assert all(leader in {"worker-a", "worker-b"} for leader in leaders)
    assert max((later - earlier for earlier, later in zip(successful_times, successful_times[1:])), default=interval) <= 2 * interval
    cycles = storage.list_scan_cycles(limit=500)
    assert len(cycles) == 144
    assert sum(row["status"] == "failed" for row in cycles) == 3


def test_accelerated_24_hour_scheduler_leader_death_recovers_without_manual_expiry(monkeypatch, tmp_path):
    storage = _configure_background_leadership(monkeypatch, tmp_path)
    clock = datetime(2026, 7, 17, tzinfo=timezone.utc)
    leaders: list[str] = []
    takeover_reason = ""
    monkeypatch.setattr(
        main,
        "inspect_lease_owner",
        lambda lease, **kwargs: SimpleNamespace(
            state="absent" if int((lease or {}).get("process_id") or 0) == 101 else "live_notifierr",
            reason="confirmed old owner absent",
            pid_exists=False if int((lease or {}).get("process_id") or 0) == 101 else True,
            belongs_to_notifierr=None if int((lease or {}).get("process_id") or 0) == 101 else True,
        ),
    )

    for cycle_number in range(24 * 6):
        now = clock + timedelta(minutes=10 * cycle_number)
        if cycle_number == 72:
            assert storage.release_worker_lease(main.BACKGROUND_LEASE_NAME, main.WORKER_ID)
            assert storage.acquire_worker_lease(
                main.BACKGROUND_LEASE_NAME,
                "host-101-deadworker",
                hostname="host",
                process_id=101,
                now=now.isoformat(),
                expires_at=(now + timedelta(seconds=main.BACKGROUND_LEASE_TTL_SECONDS)).isoformat(),
            )
        acquired, lease, _owner_state = main._acquire_background_leadership(now)
        assert acquired is True
        leaders.append(str(lease["worker_id"]))
        if cycle_number == 72:
            takeover_reason = str(lease["takeover_reason"])
        cycle_id = storage.create_scan_cycle(
            mode="shared_background",
            hostname="host",
            process_id=200,
            worker_id=main.WORKER_ID,
            background_poll_seconds=600,
        )
        storage.finish_scan_cycle(cycle_id, status="completed", items_scored=1)
        assert main._renew_background_leadership(now + timedelta(seconds=main.BACKGROUND_LEASE_RENEW_SECONDS))

    assert len(leaders) == 144
    assert set(leaders) == {main.WORKER_ID}
    assert takeover_reason == "confirmed_local_owner_absent"
    assert storage.get_worker_lease(main.BACKGROUND_LEASE_NAME)["worker_id"] == main.WORKER_ID


def test_live_scan_renews_short_lease_while_running(tmp_path):
    storage = Storage(tmp_path / "renew.sqlite3")
    started_at = datetime(2026, 7, 16, 12, tzinfo=timezone.utc)
    clock = {"now": started_at}
    observed_lease = {}
    sleep_calls = 0

    async def scenario():
        four_minutes_elapsed = asyncio.Event()

        async def fake_sleep(seconds):
            nonlocal sleep_calls
            sleep_calls += 1
            if sleep_calls <= 4:
                clock["now"] += timedelta(seconds=seconds)
                if sleep_calls == 4:
                    four_minutes_elapsed.set()
                return
            await asyncio.Event().wait()

        async def operation():
            await four_minutes_elapsed.wait()
            while storage.get_worker_lease("global_scan")["heartbeat_at"] != clock["now"].isoformat():
                await asyncio.sleep(0)
            observed_lease.update(storage.get_worker_lease("global_scan"))
            return "done"

        return await run_with_scan_lease(
            storage, operation, lease_name="global_scan", worker_id="host-100-worker",
            hostname="host", process_id=100, now_fn=lambda: clock["now"], sleep=fake_sleep,
            exists_probe=lambda pid: True,
            command_line_probe=lambda pid: "python -m uvicorn backend.main:app",
        )

    result = asyncio.run(scenario())

    assert result.acquired is True
    assert result.value == "done"
    assert sleep_calls >= 4
    assert datetime.fromisoformat(observed_lease["expires_at"]) > started_at + timedelta(seconds=GLOBAL_SCAN_LEASE_TTL_SECONDS)
    assert storage.get_worker_lease("global_scan") is None


def test_owner_only_renewal_and_release(tmp_path):
    storage = Storage(tmp_path / "owner-only.sqlite3")
    now = datetime(2026, 7, 16, tzinfo=timezone.utc)
    assert storage.acquire_worker_lease(
        "global_scan", "owner-a", hostname="host", process_id=1,
        now=now.isoformat(), expires_at=(now + timedelta(minutes=3)).isoformat(),
    )
    assert not storage.renew_worker_lease(
        "global_scan", "owner-b", now=(now + timedelta(minutes=1)).isoformat(),
        expires_at=(now + timedelta(minutes=4)).isoformat(),
    )
    assert not storage.release_worker_lease("global_scan", "owner-b")
    assert storage.get_worker_lease("global_scan")["worker_id"] == "owner-a"


def test_second_worker_cannot_overlap_live_scan(tmp_path):
    storage = Storage(tmp_path / "no-overlap.sqlite3")

    async def scenario():
        started = asyncio.Event()
        release = asyncio.Event()

        async def first_operation():
            started.set()
            await release.wait()
            return "first"

        first_task = asyncio.create_task(
            run_with_scan_lease(
                storage, first_operation, lease_name="global_scan", worker_id="host-1-worker",
                hostname="host", process_id=1,
                exists_probe=lambda pid: True,
                command_line_probe=lambda pid: "python -m uvicorn backend.main:app",
            )
        )
        await started.wait()
        second = await run_with_scan_lease(
            storage, lambda: asyncio.sleep(0), lease_name="global_scan", worker_id="host-2-worker",
            hostname="host", process_id=2,
            exists_probe=lambda pid: True,
            command_line_probe=lambda pid: "python -m uvicorn backend.main:app",
        )
        release.set()
        first = await first_task
        return first, second

    first, second = asyncio.run(scenario())
    assert first.acquired is True
    assert first.value == "first"
    assert second.acquired is False


def test_confirmed_dead_local_owner_permits_immediate_takeover(tmp_path):
    storage = Storage(tmp_path / "dead-owner.sqlite3")
    now = datetime(2026, 7, 16, tzinfo=timezone.utc)
    assert storage.acquire_worker_lease(
        "global_scan", "host-15816-oldworker", hostname="host", process_id=15816,
        now=now.isoformat(), expires_at=(now + timedelta(hours=6)).isoformat(),
    )
    claim = acquire_scan_lease(
        storage,
        lease_name="global_scan",
        worker_id="host-200-newworker",
        hostname="host",
        process_id=200,
        now=now + timedelta(seconds=1),
        exists_probe=lambda pid: False,
        command_line_probe=lambda pid: None,
    )
    lease = storage.get_worker_lease("global_scan")
    assert claim.acquired is True
    assert claim.takeover_reason == "confirmed_owner_pid_absent"
    assert lease["worker_id"] == claim.owner_id
    assert lease["previous_worker_id"] == "host-15816-oldworker"
    assert lease["takeover_reason"] == "confirmed_owner_pid_absent"


def test_live_or_unrelated_pid_never_permits_early_takeover(tmp_path):
    storage = Storage(tmp_path / "live-owner.sqlite3")
    now = datetime(2026, 7, 16, tzinfo=timezone.utc)
    assert storage.acquire_worker_lease(
        "global_scan", "host-77-oldworker", hostname="host", process_id=77,
        now=now.isoformat(), expires_at=(now + timedelta(minutes=3)).isoformat(),
    )
    claim = acquire_scan_lease(
        storage,
        lease_name="global_scan",
        worker_id="host-88-newworker",
        hostname="host",
        process_id=88,
        now=now + timedelta(seconds=130),
        exists_probe=lambda pid: True,
        command_line_probe=lambda pid: "unrelated.exe --serve",
    )
    assert claim.acquired is False
    assert claim.owner_state.state == "live_unrelated"
    assert storage.get_worker_lease("global_scan")["worker_id"] == "host-77-oldworker"


def test_process_death_without_renewal_recovers_after_short_ttl(tmp_path):
    storage = Storage(tmp_path / "ttl-recovery.sqlite3")
    now = datetime(2026, 7, 16, tzinfo=timezone.utc)
    first = acquire_scan_lease(
        storage, lease_name="global_scan", worker_id="one-1-worker", hostname="one",
        process_id=1, now=now, exists_probe=lambda pid: True,
    )
    assert first.acquired
    before_expiry = acquire_scan_lease(
        storage, lease_name="global_scan", worker_id="two-2-worker", hostname="two",
        process_id=2, now=now + timedelta(seconds=GLOBAL_SCAN_LEASE_TTL_SECONDS - 1),
    )
    assert not before_expiry.acquired
    after_expiry = acquire_scan_lease(
        storage, lease_name="global_scan", worker_id="two-2-worker", hostname="two",
        process_id=2, now=now + timedelta(seconds=GLOBAL_SCAN_LEASE_TTL_SECONDS + 1),
    )
    assert after_expiry.acquired
    assert after_expiry.takeover_reason == "lease_expired"


def test_abandoned_cycle_is_reconciled_once_and_partial_traces_remain(tmp_path):
    storage = Storage(tmp_path / "abandoned.sqlite3")
    cycle_id = storage.create_scan_cycle(
        mode="shared_background",
        process_id=15816,
        hostname="host",
        worker_id="host-15816-oldworker",
    )
    with storage.connect() as connection:
        connection.execute(
            "INSERT INTO listing_decision_traces "
            "(user_id, marketplace_item_id, scan_cycle_id, trace_json, created_at) "
            "VALUES (1, 1, ?, '{}', ?)",
            (cycle_id, datetime.now(timezone.utc).isoformat()),
        )

    first = reconcile_abandoned_scan_cycles(
        storage,
        recovery_worker_id="host-200-recovery",
        local_hostname="host",
        current_process_id=200,
        exists_probe=lambda pid: False,
        command_line_probe=lambda pid: None,
    )
    second = reconcile_abandoned_scan_cycles(
        storage,
        recovery_worker_id="host-200-recovery",
        local_hostname="host",
        current_process_id=200,
        exists_probe=lambda pid: False,
        command_line_probe=lambda pid: None,
    )
    cycle = storage.get_scan_cycle(cycle_id)
    with storage.connect() as connection:
        trace_count = connection.execute(
            "SELECT COUNT(*) AS count FROM listing_decision_traces WHERE scan_cycle_id = ?", (cycle_id,)
        ).fetchone()["count"]
    assert first == (cycle_id,)
    assert second == ()
    assert cycle["status"] == "abandoned"
    assert cycle["abandonment_reason"] == "confirmed_owner_pid_absent"
    assert cycle["abandoned_by_worker_id"] == "host-200-recovery"
    assert cycle["partial_trace_count"] == 1
    assert trace_count == 1


def test_scan_exception_releases_lease_and_later_scan_recovers(tmp_path):
    storage = Storage(tmp_path / "exception-recovery.sqlite3")

    async def failing():
        raise RuntimeError("injected scan failure")

    with pytest.raises(RuntimeError, match="injected scan failure"):
        asyncio.run(
            run_with_scan_lease(
                storage, failing, lease_name="global_scan", worker_id="host-1-worker",
                hostname="host", process_id=1,
            )
        )
    assert storage.get_worker_lease("global_scan") is None

    async def succeeding():
        return 42

    result = asyncio.run(
        run_with_scan_lease(
            storage, succeeding, lease_name="global_scan", worker_id="host-1-worker",
            hostname="host", process_id=1,
        )
    )
    assert result.value == 42


def test_scan_cancellation_releases_lease_cleanly(tmp_path):
    storage = Storage(tmp_path / "cancel.sqlite3")

    async def scenario():
        started = asyncio.Event()

        async def operation():
            started.set()
            await asyncio.Event().wait()

        task = asyncio.create_task(
            run_with_scan_lease(
                storage, operation, lease_name="global_scan", worker_id="host-1-worker",
                hostname="host", process_id=1,
            )
        )
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())
    assert storage.get_worker_lease("global_scan") is None


def test_consecutive_failures_and_skips_are_derived_from_durable_history():
    cycles = [
        {"status": "skipped", "skip_reason": "scan_already_running"},
        {"status": "skipped", "skip_reason": "scan_already_running"},
        {"status": "failed"},
        {"status": "completed"},
    ]
    assert consecutive_cycle_outcomes(cycles) == (1, 2, "scan_already_running")


def test_short_lease_constants_recover_within_three_minutes():
    assert GLOBAL_SCAN_LEASE_TTL_SECONDS == 180
    assert GLOBAL_SCAN_LEASE_RENEW_SECONDS == 60
    assert main.BACKGROUND_LEASE_TTL_SECONDS == 180
    assert main.BACKGROUND_LEASE_RENEW_SECONDS == 60


def test_accelerated_24_hour_real_lease_flow_recovers_after_process_death(tmp_path):
    storage = Storage(tmp_path / "real-flow-soak.sqlite3")
    clock = {"now": datetime(2026, 7, 16, tzinfo=timezone.utc)}
    active = 0
    max_active = 0
    successes: list[datetime] = []
    takeover_reason = ""

    async def run_simulation():
        nonlocal active, max_active, takeover_reason
        for cycle_number in range(24 * 6):
            if cycle_number == 72:
                assert storage.acquire_worker_lease(
                    "global_scan", "host-101-deadworker", hostname="host", process_id=101,
                    now=clock["now"].isoformat(),
                    expires_at=(clock["now"] + timedelta(seconds=GLOBAL_SCAN_LEASE_TTL_SECONDS)).isoformat(),
                )

            async def operation(cycle_number=cycle_number):
                nonlocal active, max_active
                active += 1
                max_active = max(max_active, active)
                cycle_id = storage.create_scan_cycle(
                    mode="shared_background", hostname="host", process_id=202,
                    worker_id="host-202-recovery", background_poll_seconds=600,
                )
                try:
                    if cycle_number in {17, 53, 101}:
                        storage.finish_scan_cycle(cycle_id, status="failed", error_message="injected")
                        raise RuntimeError("injected")
                    storage.finish_scan_cycle(cycle_id, status="completed", items_scored=1)
                    successes.append(clock["now"])
                finally:
                    active -= 1

            try:
                result = await run_with_scan_lease(
                    storage, operation, lease_name="global_scan", worker_id="host-202-recovery",
                    hostname="host", process_id=202, now_fn=lambda: clock["now"],
                    exists_probe=lambda pid: False if pid == 101 else True,
                    command_line_probe=lambda pid: "python -m uvicorn backend.main:app",
                )
                if cycle_number == 72:
                    takeover_reason = result.claim.takeover_reason
            except RuntimeError as exc:
                assert str(exc) == "injected"
            clock["now"] += timedelta(minutes=10)

    asyncio.run(run_simulation())
    assert max_active == 1
    assert takeover_reason == "confirmed_owner_pid_absent"
    assert storage.get_worker_lease("global_scan") is None
    assert max(later - earlier for earlier, later in zip(successes, successes[1:])) <= timedelta(minutes=20)
    cycles = storage.list_scan_cycles(limit=500)
    assert len(cycles) == 144
    assert sum(cycle["status"] == "failed" for cycle in cycles) == 3
