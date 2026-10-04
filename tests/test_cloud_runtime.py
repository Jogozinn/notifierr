from __future__ import annotations

import asyncio
import os
import socket
import subprocess
import sys
import time
import urllib.request
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from backend import main, scanner
from backend import config as config_module
from backend.config import Settings, validate_settings
from backend.storage import Storage
from backend.schema import check_schema
from sqlalchemy import create_engine, text


def production_settings(monkeypatch, **overrides):
    monkeypatch.setenv("AUTH_SECRET_KEY", "x" * 40)
    settings = Settings(
        runtime_env="production", process_role="api", db_backend="postgres",
        database_url="postgresql://user:password@localhost/notifierr",
        auth_required=True, auth_secret_key="x" * 40,
        app_encryption_key=Fernet.generate_key().decode(),
        cors_origins=["https://front.example"], trusted_hosts=["api.example"],
        admin_email="owner@example.com", admin_password="long-unique-password",
    )
    return replace(settings, **overrides)


@pytest.mark.parametrize("overrides,match", [
    ({"db_backend": "sqlite"}, "PostgreSQL"),
    ({"database_url": None}, "PostgreSQL"),
    ({"auth_required": False}, "AUTH_REQUIRED"),
    ({"auth_secret_key": "short"}, "AUTH_SECRET_KEY"),
    ({"app_encryption_key": "invalid"}, "Fernet"),
    ({"cors_origins": ["*"]}, "CORS_ALLOWED_ORIGINS"),
    ({"trusted_hosts": []}, "TRUSTED_HOSTS"),
    ({"admin_password": None}, "ADMIN_PASSWORD"),
])
def test_production_rejects_unsafe_configuration(monkeypatch, overrides, match):
    with pytest.raises(ValueError, match=match):
        validate_settings(production_settings(monkeypatch, **overrides))


def test_production_api_never_starts_scanner_even_when_enabled(monkeypatch):
    monkeypatch.setattr(main, "settings", production_settings(monkeypatch, background_poll_enabled=True))
    assert not main._should_start_background_poll_loop()
    monkeypatch.setattr(main, "settings", replace(main.settings, process_role="scanner"))
    assert main._should_start_background_poll_loop()


def test_production_load_never_uses_local_dotenv_or_sqlite_fallback(monkeypatch):
    monkeypatch.setenv("NOTIFIERR_ENV", "production")
    monkeypatch.setenv("DB_BACKEND", "sqlite")
    monkeypatch.setattr(config_module, "_load_dotenv", lambda _: pytest.fail("production loaded .env"))
    with pytest.raises(ValueError, match="PostgreSQL"):
        config_module.load_settings()


def test_scanner_refuses_missing_source_credentials_without_http(monkeypatch):
    monkeypatch.setattr(main, "settings", Settings(process_role="scanner", background_poll_enabled=True))
    with pytest.raises(RuntimeError, match="EBAY_CLIENT_ID"):
        asyncio.run(scanner.run())


def test_live_and_ready_are_distinct(monkeypatch, tmp_path):
    store = Storage(tmp_path / "health.sqlite3")
    monkeypatch.setattr(main, "settings", Settings(process_role="api"))
    monkeypatch.setattr(main, "storage", store)
    with TestClient(main.app) as client:
        assert client.get("/health/live").status_code == 200
        assert client.get("/health/ready").status_code == 200
        class BrokenStorage:
            def connect(self):
                raise ConnectionError("database unavailable")
        monkeypatch.setattr(main, "storage", BrokenStorage())
        assert client.get("/health/live").status_code == 200
        assert client.get("/health/ready").status_code == 503


def test_production_global_repair_baseline_cannot_write(monkeypatch, tmp_path):
    baseline = tmp_path / "repair.json"
    baseline.write_text('{"default":{"parts":{}}}', encoding="utf-8")
    monkeypatch.setattr(main, "settings", replace(production_settings(monkeypatch), repair_values_path=baseline))
    with pytest.raises(main.HTTPException) as error:
        main.update_global_repair_value_part("iPhone", main.PartCostRequest(part="screen", cost=100), {"role": "admin"})
    assert error.value.status_code == 409
    assert baseline.read_text(encoding="utf-8") == '{"default":{"parts":{}}}'


def test_schema_check_rejects_unmigrated_and_unknown_revision():
    engine = create_engine("sqlite:///:memory:")
    with pytest.raises(RuntimeError, match="<missing>"):
        check_schema(engine)
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"))
        connection.execute(text("INSERT INTO alembic_version VALUES ('unknown_future')"))
    with pytest.raises(RuntimeError, match="unknown_future"):
        check_schema(engine)


def test_scanner_health_does_not_confuse_fresh_lease_with_useful_scan(monkeypatch, tmp_path):
    monkeypatch.setattr(main, "storage", Storage(tmp_path / "status.sqlite3"))
    monkeypatch.setattr(main, "settings", Settings(background_poll_enabled=True, background_poll_seconds=300))
    acquired = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    monkeypatch.setattr(main, "admin_polling_status", lambda _: {
        "state": "scanning", "enabled": True, "scheduler_lease": {"acquired_at": acquired},
        "worker_lease_state": "active", "effective_interval_seconds": 300,
    })
    monkeypatch.setattr(main, "_polling_settings_for_status", lambda _: SimpleNamespace(
        background_poll_active_start=None, background_poll_active_end=None,
        background_poll_timezone="UTC"))
    status = main.admin_scanner_status({"id": 1})
    assert status["state"] == "degraded"
    assert status["last_useful_scan_at"] is None
    assert status["degraded_reason"] == "Last useful scan is overdue"


def test_api_observer_reports_live_remote_scanner_as_running(monkeypatch, tmp_path):
    monkeypatch.setattr(main, "storage", Storage(tmp_path / "remote.sqlite3"))
    monkeypatch.setattr(main, "settings", Settings(background_poll_enabled=True, background_poll_seconds=300))
    monkeypatch.setattr(main, "admin_polling_status", lambda _: {
        "state": "standby", "scheduler_process_state": "standby", "enabled": True,
        "scheduler_lease": {"acquired_at": datetime.now(timezone.utc).isoformat()},
        "effective_interval_seconds": 300,
    })
    monkeypatch.setattr(main, "_polling_settings_for_status", lambda _: SimpleNamespace(
        background_poll_active_start=None, background_poll_active_end=None,
        background_poll_timezone="UTC"))
    assert main.admin_scanner_status({"id": 1})["state"] == "running"


def test_scanner_process_keeps_lease_across_api_restart(tmp_path):
    database = tmp_path / "processes.sqlite3"
    store = Storage(database)
    assert store.get_worker_lease("background_poll") is None
    env = os.environ.copy()
    env.update({
        "NOTIFIERR_ENV": "local", "DB_BACKEND": "sqlite", "SQLITE_PATH": str(database),
        "AUTH_REQUIRED": "true", "BACKGROUND_POLL_ENABLED": "true",
        "NOTIFIERR_ROLE": "scanner", "EBAY_CLIENT_ID": "test-id",
        "EBAY_CLIENT_SECRET": "test-secret", "ADMIN_EMAIL": "", "ADMIN_PASSWORD": "",
    })
    scanner_one = subprocess.Popen([sys.executable, "-m", "backend.scanner"], env=env,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    scanner_two = None
    api = None
    try:
        deadline = time.monotonic() + 15
        lease = None
        while time.monotonic() < deadline:
            lease = store.get_worker_lease("background_poll")
            if lease:
                break
            assert scanner_one.poll() is None
            time.sleep(0.2)
        assert lease
        owner_pid = int(lease["process_id"])

        scanner_two = subprocess.Popen([sys.executable, "-m", "backend.scanner"], env=env,
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(2)
        assert scanner_two.poll() is None
        assert int(store.get_worker_lease("background_poll")["process_id"]) == owner_pid

        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        api_env = {**env, "NOTIFIERR_ROLE": "api", "HOST": "127.0.0.1", "PORT": str(port)}
        api = subprocess.Popen([sys.executable, "-m", "backend.api"], env=api_env,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            assert api.poll() is None
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/health/live", timeout=1) as response:
                    if response.status == 200:
                        break
            except OSError:
                time.sleep(0.2)
        else:
            pytest.fail("API did not become live")
        api.terminate()
        api.wait(timeout=10)
        assert scanner_one.poll() is None
        assert int(store.get_worker_lease("background_poll")["process_id"]) == owner_pid
    finally:
        for process in (api, scanner_two, scanner_one):
            if process and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=10)
