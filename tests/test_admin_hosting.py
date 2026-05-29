from pathlib import Path

from fastapi.testclient import TestClient

from backend import main
from backend.auth import hash_password
from backend.config import Settings
from backend.storage import Storage


TEST_FERNET_KEY = "6xOPMctX4pJ4BoU5m6v6-55ZACFMy39YrOU8IZQ5lbY="


def _configure_app(monkeypatch, tmp_path, **settings_overrides):
    del tmp_path
    db_path = Path(":memory:")
    storage = Storage(db_path)
    auth_required = settings_overrides.pop("auth_required", True)
    settings = Settings(
        sqlite_path=db_path,
        auth_required=auth_required,
        auth_secret_key="test-auth-secret-value-is-long-enough",
        access_token_expire_minutes=60,
        app_encryption_key=settings_overrides.pop("app_encryption_key", TEST_FERNET_KEY),
        **settings_overrides,
    )
    monkeypatch.setattr(main, "storage", storage)
    monkeypatch.setattr(main, "settings", settings)
    return storage


def _login(client: TestClient, email: str, password: str) -> str:
    response = client.post("/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def _auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _create_user(storage: Storage, *, email: str, password: str, role: str = "user", account_status: str = "active") -> dict:
    return storage.create_user(
        email=email,
        password_hash=hash_password(password),
        role=role,
        account_status=account_status,
    )


def test_webhook_is_encrypted_on_save_and_not_returned(monkeypatch, tmp_path):
    storage = _configure_app(monkeypatch, tmp_path)
    user = _create_user(storage, email="notify@example.com", password="notify-pass")
    secret = "https://discord.com/api/webhooks/test-secret-value"

    with TestClient(main.app) as client:
        token = _login(client, "notify@example.com", "notify-pass")
        saved = client.put(
            "/settings/notifications",
            headers=_auth_headers(token),
            json={
                "discord_webhook": secret,
                "discord_enabled": True,
                "alerts_enabled": True,
                "notify_best_finds": True,
                "notify_priority_review": True,
            },
        )
        fetched = client.get("/settings/notifications", headers=_auth_headers(token))

    with storage.connect() as connection:
        stored_value = connection.execute(
            "SELECT discord_webhook FROM user_notification_settings WHERE user_id = ?",
            (int(user["id"]),),
        ).fetchone()["discord_webhook"]

    assert saved.status_code == 200
    assert fetched.status_code == 200
    assert stored_value != secret
    assert stored_value.startswith("fernet:")
    assert saved.json()["notifications"]["discord_webhook"] == ""
    assert fetched.json()["notifications"]["discord_webhook"] == ""
    assert fetched.json()["notifications"]["discord_webhook_configured"] is True


def test_test_discord_decrypts_server_side(monkeypatch, tmp_path):
    storage = _configure_app(monkeypatch, tmp_path)
    _create_user(storage, email="discord@example.com", password="discord-pass")
    captured = {}

    async def fake_send_message(self, content):
        captured["webhook"] = self.webhook_url
        captured["content"] = content
        return True

    monkeypatch.setattr(main.DiscordNotifier, "send_message", fake_send_message)

    with TestClient(main.app) as client:
        token = _login(client, "discord@example.com", "discord-pass")
        save = client.put(
            "/settings/notifications",
            headers=_auth_headers(token),
            json={
                "discord_webhook": "https://discord.com/api/webhooks/decrypt-me",
                "discord_enabled": True,
                "alerts_enabled": True,
                "notify_best_finds": True,
                "notify_priority_review": True,
            },
        )
        test = client.post("/settings/notifications/test-discord", headers=_auth_headers(token))

    assert save.status_code == 200
    assert test.status_code == 200
    assert captured["webhook"] == "https://discord.com/api/webhooks/decrypt-me"
    assert "Notifierr test notification" in captured["content"]


def test_missing_encryption_key_blocks_webhook_save_in_hosted_mode(monkeypatch, tmp_path):
    storage = _configure_app(monkeypatch, tmp_path, app_encryption_key=None, auth_required=True)
    _create_user(storage, email="nokey@example.com", password="nokey-pass")

    with TestClient(main.app) as client:
        token = _login(client, "nokey@example.com", "nokey-pass")
        response = client.put(
            "/settings/notifications",
            headers=_auth_headers(token),
            json={
                "discord_webhook": "https://discord.com/api/webhooks/blocked",
                "discord_enabled": True,
                "alerts_enabled": True,
                "notify_best_finds": True,
                "notify_priority_review": True,
            },
        )

    assert response.status_code == 503
    assert "APP_ENCRYPTION_KEY" in response.json()["detail"]


def test_admin_can_create_update_disable_enable_user_and_billing(monkeypatch, tmp_path):
    storage = _configure_app(monkeypatch, tmp_path)
    _create_user(storage, email="admin@example.com", password="admin-pass", role="admin")

    with TestClient(main.app) as client:
        token = _login(client, "admin@example.com", "admin-pass")
        created = client.post(
            "/admin/users",
            headers=_auth_headers(token),
            json={
                "email": "member@example.com",
                "password": "member-pass-1",
                "display_name": "Member",
                "role": "user",
                "account_status": "active",
                "plan_name": "Family Basic",
                "monthly_price": 12.5,
                "billing_status": "trial",
                "paid_until": "2026-06-15",
                "billing_note": "Created by admin",
            },
        )
        user_id = created.json()["user"]["id"]
        updated = client.patch(
            f"/admin/users/{user_id}",
            headers=_auth_headers(token),
            json={
                "plan_name": "Family Plus",
                "monthly_price": 18.75,
                "billing_status": "active",
                "paid_until": "2026-07-01",
                "billing_note": "Paid cash",
            },
        )
        disabled = client.post(f"/admin/users/{user_id}/disable", headers=_auth_headers(token))
        enabled = client.post(f"/admin/users/{user_id}/enable", headers=_auth_headers(token))
        usage = client.get(f"/admin/users/{user_id}/usage", headers=_auth_headers(token))

    assert created.status_code == 200
    assert updated.status_code == 200
    assert disabled.status_code == 200
    assert enabled.status_code == 200
    assert usage.status_code == 200
    assert updated.json()["user"]["plan_name"] == "Family Plus"
    assert updated.json()["user"]["monthly_price"] == 18.75
    assert updated.json()["user"]["billing_status"] == "active"
    assert disabled.json()["user"]["account_status"] == "disabled"
    assert enabled.json()["user"]["account_status"] == "active"
    assert "summary" in usage.json()


def test_non_admin_and_normal_user_cannot_access_admin_mutations(monkeypatch, tmp_path):
    storage = _configure_app(monkeypatch, tmp_path)
    _create_user(storage, email="admin@example.com", password="admin-pass", role="admin")
    _create_user(storage, email="member@example.com", password="member-pass", role="user")

    with TestClient(main.app) as client:
        member_token = _login(client, "member@example.com", "member-pass")
        list_response = client.get("/admin/users", headers=_auth_headers(member_token))
        patch_response = client.patch(
            "/admin/users/1",
            headers=_auth_headers(member_token),
            json={"billing_status": "comped"},
        )

    assert list_response.status_code == 403
    assert patch_response.status_code == 403


def test_disabled_user_cannot_log_in_after_admin_disable(monkeypatch, tmp_path):
    storage = _configure_app(monkeypatch, tmp_path)
    _create_user(storage, email="admin@example.com", password="admin-pass", role="admin")
    _create_user(storage, email="member@example.com", password="member-pass", role="user")

    with TestClient(main.app) as client:
        admin_token = _login(client, "admin@example.com", "admin-pass")
        disable = client.post("/admin/users/2/disable", headers=_auth_headers(admin_token))
        login_again = client.post("/auth/login", json={"email": "member@example.com", "password": "member-pass"})

    assert disable.status_code == 200
    assert login_again.status_code == 403
    assert login_again.json()["detail"] == "Account is disabled"
