from __future__ import annotations

import asyncio
import ctypes
import logging
import os
import socket
import subprocess
import sys
import uuid
from ctypes import wintypes
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable


logger = logging.getLogger(__name__)

GLOBAL_SCAN_LEASE_TTL_SECONDS = 180
GLOBAL_SCAN_LEASE_RENEW_SECONDS = 60


class ScanLeaseLostError(RuntimeError):
    pass


@dataclass(frozen=True)
class ProcessOwnerState:
    state: str
    reason: str
    local_host: bool
    pid_exists: bool | None
    belongs_to_notifierr: bool | None


@dataclass(frozen=True)
class ScanLeaseClaim:
    acquired: bool
    owner_id: str
    takeover_reason: str = ""
    previous_lease: dict[str, Any] | None = None
    owner_state: ProcessOwnerState | None = None


@dataclass(frozen=True)
class ScanLeaseRunResult:
    acquired: bool
    value: Any = None
    claim: ScanLeaseClaim | None = None
    reconciled_cycle_ids: tuple[int, ...] = ()


def parse_utc(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def worker_identity_matches(lease: dict[str, Any]) -> bool:
    hostname = str(lease.get("hostname") or "").strip()
    process_id = int(lease.get("process_id") or 0)
    worker_id = str(lease.get("worker_id") or "").strip()
    return bool(hostname and process_id > 0 and worker_id.lower().startswith(f"{hostname}-{process_id}-".lower()))


def process_exists(process_id: int) -> bool | None:
    if process_id <= 0:
        return None
    if process_id == os.getpid():
        return True
    if sys.platform == "win32":
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        handle = kernel32.OpenProcess(0x00100000, False, int(process_id))
        if handle:
            kernel32.CloseHandle(handle)
            return True
        error = int(ctypes.get_last_error())
        if error == 87:  # ERROR_INVALID_PARAMETER: no such process.
            return False
        if error == 5:  # Access denied still proves that the PID exists.
            return True
        return None
    try:
        os.kill(int(process_id), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return None
    return True


def _process_command_line(process_id: int) -> str | None:
    if process_id == os.getpid():
        return " ".join(sys.argv)
    if sys.platform == "win32":
        command = (
            f"$p=Get-CimInstance Win32_Process -Filter \"ProcessId = {int(process_id)}\"; "
            "if ($p) { [Console]::Out.Write($p.CommandLine) }"
        )
        try:
            completed = subprocess.run(
                ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
                capture_output=True,
                text=True,
                timeout=2,
                check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except (OSError, subprocess.SubprocessError):
            return None
        return completed.stdout.strip() or None
    path = Path(f"/proc/{int(process_id)}/cmdline")
    try:
        return path.read_bytes().replace(b"\0", b" ").decode(errors="replace").strip() or None
    except OSError:
        return None


def inspect_lease_owner(
    lease: dict[str, Any] | None,
    *,
    local_hostname: str | None = None,
    current_worker_id: str = "",
    current_process_id: int | None = None,
    exists_probe: Callable[[int], bool | None] = process_exists,
    command_line_probe: Callable[[int], str | None] = _process_command_line,
) -> ProcessOwnerState:
    if not lease:
        return ProcessOwnerState("absent", "no lease", True, False, None)
    hostname = str(lease.get("hostname") or "")
    process_id = int(lease.get("process_id") or 0)
    local_hostname = local_hostname or socket.gethostname()
    local = hostname.lower() == local_hostname.lower()
    if not local:
        return ProcessOwnerState("remote_unknown", "lease owner is on another host", False, None, None)
    if not worker_identity_matches(lease):
        return ProcessOwnerState("identity_unknown", "lease worker identity does not match its host and PID", True, None, None)
    exists = exists_probe(process_id)
    if exists is False:
        return ProcessOwnerState("absent", f"confirmed owner PID {process_id} is absent", True, False, None)
    if exists is None:
        return ProcessOwnerState("unknown", f"could not establish liveness for owner PID {process_id}", True, None, None)
    if process_id == int(current_process_id or os.getpid()) and str(lease.get("worker_id") or "").startswith(current_worker_id):
        return ProcessOwnerState("live_notifierr", "lease belongs to this Notifierr process", True, True, True)
    command_line = command_line_probe(process_id)
    if not command_line:
        return ProcessOwnerState("live_unknown", f"PID {process_id} exists but its command line is unavailable", True, True, None)
    normalized = command_line.lower().replace("\\", "/")
    belongs = "backend.main:app" in normalized or "scripts/run_notifier.ps1" in normalized
    if belongs:
        return ProcessOwnerState("live_notifierr", f"PID {process_id} is a Notifierr process", True, True, True)
    return ProcessOwnerState("live_unrelated", f"PID {process_id} exists but is not a Notifierr process", True, True, False)


def acquire_scan_lease(
    storage: Any,
    *,
    lease_name: str,
    worker_id: str,
    hostname: str,
    process_id: int,
    now: datetime,
    ttl_seconds: int = GLOBAL_SCAN_LEASE_TTL_SECONDS,
    exists_probe: Callable[[int], bool | None] = process_exists,
    command_line_probe: Callable[[int], str | None] = _process_command_line,
) -> ScanLeaseClaim:
    owner_id = f"{worker_id}:scan:{uuid.uuid4().hex[:12]}"
    expires_at = now + timedelta(seconds=ttl_seconds)
    previous = storage.get_worker_lease(lease_name)
    if previous is None:
        acquired = storage.acquire_worker_lease(
            lease_name,
            owner_id,
            hostname=hostname,
            process_id=process_id,
            now=now.isoformat(),
            expires_at=expires_at.isoformat(),
        )
        return ScanLeaseClaim(acquired, owner_id, previous_lease=None)

    previous_expiry = parse_utc(previous.get("expires_at"))
    if previous_expiry is not None and previous_expiry <= now:
        acquired = storage.acquire_worker_lease(
            lease_name,
            owner_id,
            hostname=hostname,
            process_id=process_id,
            now=now.isoformat(),
            expires_at=expires_at.isoformat(),
        )
        return ScanLeaseClaim(acquired, owner_id, "lease_expired" if acquired else "", previous)

    owner_state = inspect_lease_owner(
        previous,
        local_hostname=hostname,
        current_worker_id=worker_id,
        current_process_id=process_id,
        exists_probe=exists_probe,
        command_line_probe=command_line_probe,
    )
    if owner_state.state == "absent":
        acquired = storage.takeover_worker_lease(
            lease_name,
            owner_id,
            hostname=hostname,
            process_id=process_id,
            now=now.isoformat(),
            expires_at=expires_at.isoformat(),
            expected_worker_id=str(previous.get("worker_id") or ""),
            expected_expires_at=str(previous.get("expires_at") or ""),
            reason="confirmed_owner_pid_absent",
        )
        return ScanLeaseClaim(
            acquired,
            owner_id,
            "confirmed_owner_pid_absent" if acquired else "",
            previous,
            owner_state,
        )
    return ScanLeaseClaim(False, owner_id, previous_lease=previous, owner_state=owner_state)


def reconcile_abandoned_scan_cycles(
    storage: Any,
    *,
    recovery_worker_id: str,
    local_hostname: str,
    current_process_id: int,
    previous_lease: dict[str, Any] | None = None,
    takeover_reason: str = "",
    exists_probe: Callable[[int], bool | None] = process_exists,
    command_line_probe: Callable[[int], str | None] = _process_command_line,
) -> tuple[int, ...]:
    reconciled: list[int] = []
    for cycle in storage.list_unfinished_scan_cycles():
        cycle_id = int(cycle.get("id") or 0)
        process_id = int(cycle.get("process_id") or 0)
        hostname = str(cycle.get("hostname") or "")
        prior_worker_id = str(cycle.get("worker_id") or "")
        if process_id == current_process_id and hostname.lower() == local_hostname.lower():
            continue

        matches_previous = bool(
            previous_lease
            and hostname.lower() == str(previous_lease.get("hostname") or "").lower()
            and process_id == int(previous_lease.get("process_id") or 0)
            and (not prior_worker_id or prior_worker_id == str(previous_lease.get("worker_id") or ""))
        )
        reason = ""
        recorded_worker_id = prior_worker_id
        if matches_previous and takeover_reason:
            reason = takeover_reason
            recorded_worker_id = recorded_worker_id or str(previous_lease.get("worker_id") or "")
        elif hostname.lower() == local_hostname.lower() and process_id > 0:
            synthetic_lease = {
                "hostname": hostname,
                "process_id": process_id,
                "worker_id": prior_worker_id or f"{hostname}-{process_id}-legacy",
            }
            owner_state = inspect_lease_owner(
                synthetic_lease,
                local_hostname=local_hostname,
                current_worker_id=recovery_worker_id,
                current_process_id=current_process_id,
                exists_probe=exists_probe,
                command_line_probe=command_line_probe,
            )
            if owner_state.state == "absent":
                reason = "confirmed_owner_pid_absent"
                recorded_worker_id = prior_worker_id or f"legacy:{hostname}:{process_id}"
        if not reason:
            continue
        if storage.abandon_scan_cycle(
            cycle_id,
            recovery_worker_id=recovery_worker_id,
            reason=reason,
            prior_worker_id=recorded_worker_id,
            expected_worker_id=prior_worker_id,
            expected_hostname=hostname,
            expected_process_id=process_id,
        ):
            reconciled.append(cycle_id)
            logger.warning(
                "Reconciled abandoned scan cycle_id=%s prior_worker_id=%s prior_pid=%s "
                "recovery_worker=%s reason=%s",
                cycle_id,
                recorded_worker_id,
                process_id,
                recovery_worker_id,
                reason,
            )
    return tuple(reconciled)


async def run_with_scan_lease(
    storage: Any,
    operation: Callable[[], Awaitable[Any]],
    *,
    lease_name: str,
    worker_id: str,
    hostname: str,
    process_id: int,
    now_fn: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ttl_seconds: int = GLOBAL_SCAN_LEASE_TTL_SECONDS,
    renew_seconds: int = GLOBAL_SCAN_LEASE_RENEW_SECONDS,
    exists_probe: Callable[[int], bool | None] = process_exists,
    command_line_probe: Callable[[int], str | None] = _process_command_line,
) -> ScanLeaseRunResult:
    claim = acquire_scan_lease(
        storage,
        lease_name=lease_name,
        worker_id=worker_id,
        hostname=hostname,
        process_id=process_id,
        now=now_fn(),
        ttl_seconds=ttl_seconds,
        exists_probe=exists_probe,
        command_line_probe=command_line_probe,
    )
    if not claim.acquired:
        return ScanLeaseRunResult(False, claim=claim)

    reconciled = reconcile_abandoned_scan_cycles(
        storage,
        recovery_worker_id=worker_id,
        local_hostname=hostname,
        current_process_id=process_id,
        previous_lease=claim.previous_lease,
        takeover_reason=claim.takeover_reason,
        exists_probe=exists_probe,
        command_line_probe=command_line_probe,
    )

    async def renew() -> None:
        while True:
            await sleep(renew_seconds)
            current = now_fn()
            try:
                renewed = storage.renew_worker_lease(
                    lease_name,
                    claim.owner_id,
                    now=current.isoformat(),
                    expires_at=(current + timedelta(seconds=ttl_seconds)).isoformat(),
                )
            except Exception as exc:
                raise ScanLeaseLostError(f"global scan lease renewal failed: {type(exc).__name__}") from exc
            if not renewed:
                raise ScanLeaseLostError("global scan lease ownership was lost")
            logger.info(
                "Renewed global scan lease worker_id=%s lease_owner=%s ttl_seconds=%s",
                worker_id,
                claim.owner_id,
                ttl_seconds,
            )

    operation_task = asyncio.create_task(operation())
    renewal_task = asyncio.create_task(renew())
    try:
        done, _ = await asyncio.wait({operation_task, renewal_task}, return_when=asyncio.FIRST_COMPLETED)
        if renewal_task in done:
            renewal_error = renewal_task.exception()
            operation_task.cancel()
            try:
                await operation_task
            except asyncio.CancelledError:
                pass
            if renewal_error:
                raise renewal_error
            raise ScanLeaseLostError("global scan lease renewal stopped unexpectedly")
        value = await operation_task
        return ScanLeaseRunResult(True, value, claim, reconciled)
    finally:
        for task in (operation_task, renewal_task):
            if not task.done():
                task.cancel()
        await asyncio.gather(operation_task, renewal_task, return_exceptions=True)
        try:
            released = storage.release_worker_lease(lease_name, claim.owner_id)
        except Exception:
            logger.exception(
                "Failed to release global scan lease worker_id=%s lease_owner=%s",
                worker_id,
                claim.owner_id,
            )
        else:
            logger.info(
                "Released global scan lease worker_id=%s lease_owner=%s released=%s",
                worker_id,
                claim.owner_id,
                released,
            )
