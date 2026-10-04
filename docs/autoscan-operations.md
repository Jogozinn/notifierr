# Autoscan operations

Autoscan uses one transactional scheduler leadership lease (`background_poll`) across application processes and a separate `global_scan` lease to serialize manual and scheduled scans. The scheduler leadership lease has a 180-second TTL and renews every 60 seconds while the scheduler is leader, including while a scan or long sleep is in progress. Renewal and release are owner checked, so one backend cannot renew or release another backend's leadership.

The `background_poll` lease can be recovered immediately on the same host only when the recorded owner PID is confirmed absent. A live Notifierr PID, an unrelated reused PID, a remote owner, or an unverifiable owner is not disturbed and must recover by normal lease expiration. Persisted takeover reasons are `lease_expired` or `confirmed_local_owner_absent`.

Each scan receives a unique `global_scan` lease owner. The global lease also has a 180-second TTL and renews every 60 seconds while the scan is alive; renewal and release are owner checked. A dead local scan owner can be recovered immediately after its PID is confirmed absent. Remote or unverifiable owners recover through the short expiration rather than an unsafe liveness assumption.

The effective cadence is the minimum enabled persisted user cadence when one exists, otherwise `BACKGROUND_POLL_SECONDS`, otherwise 600 seconds. Values are bounded to 300–1200 seconds. Health is based on durable cycle outcomes and the last successful fresh background scan as well as scheduler heartbeat and leadership state. `standby` identifies a live API process that is not the scheduler leader, `blocked` identifies an orphaned scheduler or scan lease, and `outside_window` identifies an intentional active-hours pause.

## Windows

Create `.venv`, install `requirements.txt`, configure `.env` without committing it, then run `scripts\run_notifier.ps1`. It applies Alembic migrations and starts one Uvicorn worker without reload. Daily persistent logs are written under `logs\`.

Install durable logon startup with `scripts\install_notifier_task.ps1`; remove it with `scripts\uninstall_notifier_task.ps1`. The task ignores parallel starts and restarts failures. A sleeping computer does not run scans; `StartWhenAvailable` resumes the task after wake, but operators should verify `/admin/polling/status` after extended sleep.

## Real 24-hour soak

Keep the machine awake and the scheduled task running. Record `/admin/polling/status` every five minutes and verify one lease owner, no stale state, no unexplained gap over two effective intervals, recovery after a temporary network interruption, and distinct manual/background timestamps. Review `scan_cycles`, `worker_leases`, daily logs, and newly refreshed dashboard listings at the end.

Start the sanitized five-minute evidence capture with `scripts\monitor_notifier_soak.ps1`. It writes no credentials and exits after 24 hours by default.

## Abandoned-scan recovery

Inspect only:

```powershell
.\.venv\Scripts\python.exe -m backend.scripts.recover_autoscan
```

After confirming the recorded local PID is absent and no legitimate scan owns the lease, apply owner-checked recovery:

```powershell
.\.venv\Scripts\python.exe -m backend.scripts.recover_autoscan --apply
```

Recovery preserves partial listing data and decision traces. It marks matching unfinished cycles `abandoned`, records the prior and recovery worker identities, PID, reason, timestamp, and partial trace count, and releases only the recovery command's own lease. The command refuses an active owner unless its local PID is confirmed absent.

## Rollback

Stop all Notifierr backends before rollback. To remove only the crash-recovery metadata, deploy the prior code and run `python -m alembic downgrade 20260711_0005`. This removes the added takeover and abandonment columns but preserves listings, traces, and the original lease table. To remove the entire lease feature, continue to `20260707_0004`. Restore a prior process only after confirming that exactly one polling process is enabled.
