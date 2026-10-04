from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"


def _read_script(name: str) -> str:
    return (SCRIPTS / name).read_text(encoding="utf-8")


def test_start_script_uses_production_uvicorn_without_reload_and_clears_proxy_env():
    script = _read_script("start_notifierr.ps1")

    assert "backend.main:app" in script
    assert "--workers" in script
    assert "--reload" not in script
    assert "Start-Process" in script
    assert "-WindowStyle Hidden" in script
    assert "Clear-OutboundProxyEnvironment" in script
    assert "HTTP_PROXY" in script
    assert "HTTPS_PROXY" in script
    assert "ALL_PROXY" in script
    assert "/health" in script
    assert "alembic upgrade head" in script


def test_service_runner_runs_foreground_backend_without_detached_spawn():
    script = _read_script("run_notifier.ps1")

    assert "backend.main:app" in script
    assert "--workers" in script
    assert "--reload" not in script
    assert "Start-Process" not in script
    assert "start_notifierr.ps1" not in script
    assert "Import-DotEnvFile" in script
    assert "Clear-OutboundProxyEnvironment" in script
    assert "HTTP_PROXY" in script
    assert "HTTPS_PROXY" in script
    assert "ALL_PROXY" in script
    assert "alembic upgrade head" in script


def test_scheduled_task_installer_targets_foreground_service_runner():
    installer = _read_script("install_notifier_task.ps1")

    assert "run_notifier.ps1" in installer
    assert "start_notifierr.ps1" not in installer
    assert "-WindowStyle Hidden" in installer
    assert "-Hidden" in installer
    assert "-MultipleInstances IgnoreNew" in installer
    assert "-RestartCount 10" in installer
    assert "-RestartInterval (New-TimeSpan -Minutes 1)" in installer
    assert "-ExecutionTimeLimit (New-TimeSpan -Seconds 0)" in installer
