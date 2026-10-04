import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend import main
from backend.auth import hash_password
from backend.config import Settings
from backend.storage import Storage


def _configure(monkeypatch, *, global_webhook: str = ""):
    storage = Storage(Path(":memory:"))
    settings = Settings(
        sqlite_path=Path(":memory:"),
        auth_required=True,
        auth_secret_key="test-auth-secret-value-is-long-enough",
        access_token_expire_minutes=60,
        app_encryption_key="6xOPMctX4pJ4BoU5m6v6-55ZACFMy39YrOU8IZQ5lbY=",
        discord_webhook_url=global_webhook or None,
    )
    monkeypatch.setattr(main, "storage", storage)
    monkeypatch.setattr(main, "settings", settings)
    user = storage.create_user(
        email="notify@example.com",
        password_hash=hash_password("notify-pass"),
        role="admin",
        account_status="active",
    )
    return storage, user


def _login(client: TestClient) -> dict[str, str]:
    response = client.post("/auth/login", json={"email": "notify@example.com", "password": "notify-pass"})
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.mark.parametrize(
    ("updates", "global_webhook", "block_reason"),
    [
        ({"alerts_enabled": 0, "discord_enabled": 1}, "", "notifications_disabled"),
        ({"alerts_enabled": 1, "discord_enabled": 0}, "", "discord_disabled"),
        ({"alerts_enabled": 1, "discord_enabled": 1}, "", "webhook_missing"),
        ({"alerts_enabled": 1, "discord_enabled": 1}, "https://discord.example/global", "global_fallback_not_authorized"),
    ],
)
def test_notification_resolver_reports_distinct_block_reasons(monkeypatch, updates, global_webhook, block_reason):
    storage, user = _configure(monkeypatch, global_webhook=global_webhook)
    storage.update_user_notification_settings(int(user["id"]), updates)

    resolved = main._resolve_effective_user_settings(user)

    assert resolved.notification_settings["notification_ready"] is False
    assert resolved.notification_settings["notification_block_reason"] == block_reason
    assert resolved.resolved_discord_webhook_url == ""


def test_explicit_global_opt_in_is_ready_without_copying_secret(monkeypatch):
    storage, user = _configure(monkeypatch, global_webhook="https://discord.example/global-secret")
    storage.update_user_notification_settings(
        int(user["id"]),
        {"alerts_enabled": 1, "discord_enabled": 1, "use_global_discord_webhook": 1},
    )

    resolved = main._resolve_effective_user_settings(user)
    public = main._public_notification_settings(resolved.notification_settings)

    assert resolved.notification_settings["notification_ready"] is True
    assert resolved.notification_settings["webhook_source"] == "global_opt_in"
    assert public["webhook_configured"] is True
    assert public["webhook_source"] == "global_opt_in"
    assert "global-secret" not in json.dumps(public)
    with storage.connect() as connection:
        stored = connection.execute(
            "SELECT discord_webhook FROM user_notification_settings WHERE user_id = ?",
            (int(user["id"]),),
        ).fetchone()
    assert stored["discord_webhook"] == ""


def test_per_user_webhook_takes_precedence_over_global_opt_in(monkeypatch):
    storage, user = _configure(monkeypatch, global_webhook="https://discord.example/global")
    storage.update_user_notification_settings(
        int(user["id"]),
        {
            "discord_webhook": "https://discord.example/per-user",
            "alerts_enabled": 1,
            "discord_enabled": 1,
            "use_global_discord_webhook": 1,
        },
    )

    resolved = main._resolve_effective_user_settings(user)

    assert resolved.notification_settings["notification_ready"] is True
    assert resolved.notification_settings["webhook_source"] == "per_user"
    assert resolved.resolved_discord_webhook_url == "https://discord.example/per-user"


def test_settings_endpoint_exposes_readiness_without_secret(monkeypatch):
    _storage, _user = _configure(monkeypatch, global_webhook="https://discord.example/global-secret")

    with TestClient(main.app) as client:
        headers = _login(client)
        saved = client.put(
            "/settings/notifications",
            headers=headers,
            json={
                "discord_enabled": True,
                "use_global_discord_webhook": True,
                "alerts_enabled": True,
                "notify_best_finds": True,
                "notify_priority_review": True,
            },
        )

    assert saved.status_code == 200
    payload = saved.json()["notifications"]
    assert payload["notification_ready"] is True
    assert payload["webhook_source"] == "global_opt_in"
    assert payload["discord_webhook"] == ""
    assert "global-secret" not in saved.text


def test_test_notification_is_rate_limited_persisted_and_sanitized(monkeypatch, caplog):
    storage, user = _configure(monkeypatch, global_webhook="https://discord.example/global-secret")
    storage.update_user_notification_settings(
        int(user["id"]),
        {"alerts_enabled": 1, "discord_enabled": 1, "use_global_discord_webhook": 1},
    )
    sent_messages = []

    async def send_message(self, content):
        sent_messages.append(content)
        self.last_provider_status = 204
        return True

    monkeypatch.setattr(main.DiscordNotifier, "send_message", send_message)

    with TestClient(main.app) as client:
        headers = _login(client)
        first = client.post("/settings/notifications/test-discord", headers=headers)
        second = client.post("/settings/notifications/test-discord", headers=headers)

    assert first.status_code == 200
    assert first.json() == {"attempted": True, "sent": True, "failed": False, "error": ""}
    assert second.status_code == 429
    assert sent_messages == [main.TEST_NOTIFICATION_MESSAGE]
    attempts = storage.list_notification_attempts(user_id=int(user["id"]))
    assert len(attempts) == 2
    assert attempts[0]["skipped"] is True
    assert attempts[0]["failure_category"] == "rate_limited"
    assert attempts[1]["sent"] is True
    assert attempts[1]["provider_status"] == 204
    serialized = json.dumps(attempts)
    assert "global-secret" not in serialized
    assert "global-secret" not in caplog.text


def test_test_notification_configuration_failure_is_persisted_without_attempt(monkeypatch):
    storage, user = _configure(monkeypatch)
    storage.update_user_notification_settings(int(user["id"]), {"alerts_enabled": 1, "discord_enabled": 1})

    with TestClient(main.app) as client:
        headers = _login(client)
        response = client.post("/settings/notifications/test-discord", headers=headers)

    assert response.status_code == 400
    attempt = storage.list_notification_attempts(user_id=int(user["id"]))[0]
    assert attempt["attempted"] is False
    assert attempt["skipped"] is True
    assert attempt["failure_category"] == "webhook_missing"


def test_test_notification_provider_failure_is_sanitized_and_persisted(monkeypatch):
    storage, user = _configure(monkeypatch, global_webhook="https://discord.example/global-secret")
    storage.update_user_notification_settings(
        int(user["id"]),
        {"alerts_enabled": 1, "discord_enabled": 1, "use_global_discord_webhook": 1},
    )

    async def send_message(self, content):
        del content
        self.last_provider_status = 503
        self.last_failure_category = "provider_http_error"
        self.last_error_message = "HTTPStatusError"
        return False

    monkeypatch.setattr(main.DiscordNotifier, "send_message", send_message)

    with TestClient(main.app) as client:
        headers = _login(client)
        response = client.post("/settings/notifications/test-discord", headers=headers)

    assert response.status_code == 200
    assert response.json() == {"attempted": True, "sent": False, "failed": True, "error": "Discord delivery failed"}
    attempt = storage.list_notification_attempts(user_id=int(user["id"]))[0]
    assert attempt["failed"] is True
    assert attempt["failure_category"] == "provider_http_error"
    assert attempt["error_message"] == "HTTPStatusError"
    assert attempt["provider_status"] == 503
    assert "global-secret" not in json.dumps(attempt)


def test_health_distinguishes_global_presence_from_user_readiness(monkeypatch):
    _configure(monkeypatch, global_webhook="https://discord.example/global")

    with TestClient(main.app) as client:
        response = client.get("/health")

    payload = response.json()
    assert payload["global_discord_webhook_configured"] is True
    assert payload["notification_ready"] is None
    assert payload["notification_block_reason"] == "authenticated_user_context_required"
