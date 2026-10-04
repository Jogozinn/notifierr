from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from types import SimpleNamespace

import pytest

from backend import scanner


class FakeStorage:
    def __init__(self):
        self.releases: list[tuple[str, str]] = []
        self.cycles: list[dict] = [{"id": 41}]

    def list_scan_cycles(self, *, limit=1):
        return self.cycles[:limit]

    def release_worker_lease(self, lease_name, worker_id):
        self.releases.append((lease_name, worker_id))
        return True


def fake_runtime(*, enabled=True, active_users=None, scan_summary=None, acquired=True):
    storage = FakeStorage()
    calls = {"scan": 0, "persist": [], "leadership": 0}
    users = list(active_users or [])

    async def renew_forever():
        await asyncio.Event().wait()

    async def await_with_leadership(task, renewal_task):
        del renewal_task
        return await task

    async def scan_shared_once(**kwargs):
        calls["scan"] += 1
        calls["scan_kwargs"] = kwargs
        if isinstance(scan_summary, Exception):
            raise scan_summary
        return scan_summary or {"scanned": 3, "alerts_sent": 1}

    def persist(**kwargs):
        calls["persist"].append(kwargs)
        return 42

    def acquire(now):
        del now
        calls["leadership"] += 1
        lease = {"worker_id": "other" if not acquired else "worker-a"}
        return acquired, lease, SimpleNamespace(state="remote_unknown")

    runtime = SimpleNamespace(
        settings=SimpleNamespace(
            process_role="scanner",
            background_poll_enabled=enabled,
            ebay_configured=True,
            max_results_per_keyword=25,
        ),
        WORKER_ID="worker-a",
        BACKGROUND_LEASE_NAME="background_poll",
        storage=storage,
        _acquire_background_leadership=acquire,
        _background_leadership_renewer=renew_forever,
        _await_with_background_leadership=await_with_leadership,
        _active_shared_scan_users=lambda **kwargs: users,
        _persist_skipped_scan_cycle=persist,
        _background_poll_is_active=lambda user: getattr(user, "inside_window", True),
        scan_shared_once=scan_shared_once,
    )
    return runtime, storage, calls


def test_once_runs_exactly_one_shared_cycle_without_sleep_loop():
    user = SimpleNamespace(background_poll_enabled=True, inside_window=True)
    runtime, storage, calls = fake_runtime(active_users=[user])

    result = asyncio.run(scanner.run_once(runtime))

    assert result["outcome"] == "success"
    assert result["scan_cycle_id"] == 41
    assert calls["scan"] == 1
    assert calls["scan_kwargs"] == {
        "limit": 25,
        "notify": True,
        "triggered_by_user": None,
        "background_mode": True,
        "cycle_mode": "shared_background",
        "offload_scan_work": True,
    }
    assert storage.releases == [("background_poll", "worker-a")]


def test_once_outside_window_exits_cleanly_without_successful_source_scan():
    user = SimpleNamespace(background_poll_enabled=True, inside_window=False)
    runtime, storage, calls = fake_runtime(
        active_users=[user],
        scan_summary={"skipped": True, "reason": "no_active_users", "scanned": 0},
    )

    result = asyncio.run(scanner.run_once(runtime))

    assert result["outcome"] == "outside_active_window"
    assert result["summary"]["scanned"] == 0
    assert calls["scan"] == 1
    assert storage.releases == [("background_poll", "worker-a")]


@pytest.mark.parametrize(
    "users, expected_outcome, expected_reason",
    [
        ([], "no_active_users", "no_active_users"),
        ([SimpleNamespace(background_poll_enabled=False)], "disabled", "user_polling_disabled"),
    ],
)
def test_once_clean_skip_states_persist_evidence(users, expected_outcome, expected_reason):
    runtime, storage, calls = fake_runtime(active_users=users)

    result = asyncio.run(scanner.run_once(runtime))

    assert result["outcome"] == expected_outcome
    assert result["reason"] == expected_reason
    assert result["scan_cycle_id"] == 42
    assert calls["scan"] == 0
    assert len(calls["persist"]) == 1
    assert storage.releases == [("background_poll", "worker-a")]


def test_once_global_disable_is_zero_work_and_does_not_require_source_credentials():
    runtime, storage, calls = fake_runtime(enabled=False)
    runtime.settings.ebay_configured = False

    result = asyncio.run(scanner.run_once(runtime))

    assert result["outcome"] == "disabled"
    assert calls["leadership"] == 0
    assert calls["scan"] == 0
    assert storage.releases == []


def test_once_lease_contention_is_clean_and_does_not_scan():
    runtime, storage, calls = fake_runtime(
        active_users=[SimpleNamespace(background_poll_enabled=True)], acquired=False
    )

    result = asyncio.run(scanner.run_once(runtime))

    assert result["outcome"] == "lease_contention"
    assert result["lease_worker_id"] == "other"
    assert calls["scan"] == 0
    assert storage.releases == []


def test_once_scan_failure_propagates_and_releases_lease():
    runtime, storage, calls = fake_runtime(
        active_users=[SimpleNamespace(background_poll_enabled=True)],
        scan_summary=RuntimeError("source failed"),
    )

    with pytest.raises(RuntimeError, match="source failed"):
        asyncio.run(scanner.run_once(runtime))

    assert calls["scan"] == 1
    assert storage.releases == [("background_poll", "worker-a")]


def test_cli_success_and_failure_exit_codes(monkeypatch, capsys):
    monkeypatch.setattr(scanner, "run_once", lambda: asyncio.sleep(0, result={"event": "scanner_once_completed"}))
    assert scanner.main(["--once"]) == 0
    assert json.loads(capsys.readouterr().out)["event"] == "scanner_once_completed"

    async def fail():
        raise RuntimeError("boom")

    monkeypatch.setattr(scanner, "run_once", fail)
    assert scanner.main(["--once"]) == 1
    assert json.loads(capsys.readouterr().out) == {
        "event": "scanner_once_failed",
        "error_type": "RuntimeError",
    }


def test_continuous_command_still_uses_existing_run(monkeypatch):
    called = []

    async def continuous():
        called.append("continuous")

    monkeypatch.setattr(scanner, "run", continuous)
    assert scanner.main([]) == 0
    assert called == ["continuous"]


def test_production_once_rejects_non_postgres_configuration(tmp_path):
    env = os.environ.copy()
    env.update(
        {
            "NOTIFIERR_ENV": "production",
            "NOTIFIERR_ROLE": "scanner",
            "DB_BACKEND": "sqlite",
            "SQLITE_PATH": str(tmp_path / "must-not-be-used.sqlite3"),
            "BACKGROUND_POLL_ENABLED": "true",
            "EBAY_CLIENT_ID": "test-id",
            "EBAY_CLIENT_SECRET": "test-secret",
        }
    )

    completed = subprocess.run(
        [sys.executable, "-m", "backend.scanner", "--once"],
        cwd=os.getcwd(),
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert completed.returncode != 0
    assert json.loads(completed.stdout.strip().splitlines()[-1]) == {
        "error_type": "ValueError",
        "event": "scanner_once_failed",
    }


def test_simultaneous_once_invocations_cannot_both_own_scheduler_lease():
    lease = {"owner": None}
    events = {}
    scan_calls = []

    def runtime_for(worker_id):
        runtime, storage, calls = fake_runtime(
            active_users=[SimpleNamespace(background_poll_enabled=True)]
        )
        runtime.WORKER_ID = worker_id

        def acquire(now):
            del now
            if lease["owner"] is not None:
                return False, {"worker_id": lease["owner"]}, SimpleNamespace(state="remote_unknown")
            lease["owner"] = worker_id
            return True, {"worker_id": worker_id}, None

        async def scan(**kwargs):
            del kwargs
            scan_calls.append(worker_id)
            events["scan_started"].set()
            await events["release_scan"].wait()
            return {"scanned": 1}

        def release(name, owner):
            del name
            if lease["owner"] == owner:
                lease["owner"] = None
                return True
            return False

        runtime._acquire_background_leadership = acquire
        runtime.scan_shared_once = scan
        storage.release_worker_lease = release
        return runtime

    async def exercise():
        events["scan_started"] = asyncio.Event()
        events["release_scan"] = asyncio.Event()
        first = asyncio.create_task(scanner.run_once(runtime_for("process-a")))
        await events["scan_started"].wait()
        second = await scanner.run_once(runtime_for("process-b"))
        events["release_scan"].set()
        return await first, second

    first, second = asyncio.run(exercise())

    assert first["outcome"] == "success"
    assert second["outcome"] == "lease_contention"
    assert scan_calls == ["process-a"]
