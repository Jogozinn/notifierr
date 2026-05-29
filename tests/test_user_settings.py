from pathlib import Path

from fastapi.testclient import TestClient

from backend import main
from backend.auth import hash_password
from backend.config import Settings
from backend.storage import Storage


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
        app_encryption_key="6xOPMctX4pJ4BoU5m6v6-55ZACFMy39YrOU8IZQ5lbY=",
        search_keywords=["baseline one", "baseline two"],
        **settings_overrides,
    )
    monkeypatch.setattr(main, "storage", storage)
    monkeypatch.setattr(main, "settings", settings)
    return storage, settings


def _login(client: TestClient, email: str, password: str) -> str:
    response = client.post("/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def _auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_default_settings_created_for_new_user(monkeypatch, tmp_path):
    storage, settings = _configure_app(monkeypatch, tmp_path)
    user = storage.create_user(
        email="settings@example.com",
        password_hash=hash_password("good-password"),
        settings_seed={
            "min_score_to_alert": settings.min_score_to_alert,
            "min_profit_to_alert": settings.min_profit_to_alert,
            "risky_score_range": settings.risky_score_range,
            "max_alert_item_age_minutes": settings.max_alert_item_age_minutes,
            "max_priority_review_item_age_hours": settings.max_priority_review_item_age_hours,
            "max_active_queue_item_age_hours": settings.max_active_queue_item_age_hours,
            "background_poll_enabled": settings.background_poll_enabled,
            "background_poll_seconds": settings.background_poll_seconds,
            "active_start": settings.background_poll_active_start,
            "active_end": settings.background_poll_active_end,
            "timezone": settings.background_poll_timezone,
        },
        baseline_keywords=settings.search_keywords,
    )

    user_settings = storage.get_user_settings(int(user["id"]))
    notifications = storage.get_user_notification_settings(int(user["id"]))
    keywords = storage.list_user_keywords(int(user["id"]))

    assert user_settings is not None
    assert user_settings["min_score_to_alert"] == settings.min_score_to_alert
    assert notifications is not None
    assert notifications["discord_webhook_configured"] is False
    assert [entry["keyword"] for entry in keywords] == settings.search_keywords
    assert all(entry["is_baseline"] for entry in keywords)


def test_current_user_can_read_and_update_settings(monkeypatch, tmp_path):
    storage, _settings = _configure_app(monkeypatch, tmp_path)
    storage.create_user(
        email="viewer@example.com",
        password_hash=hash_password("viewer-pass"),
        role="user",
        account_status="active",
    )

    with TestClient(main.app) as client:
        token = _login(client, "viewer@example.com", "viewer-pass")
        initial = client.get("/settings", headers=_auth_headers(token))
        updated = client.put(
            "/settings",
            headers=_auth_headers(token),
            json={
                **initial.json()["settings"],
                "min_score_to_alert": 82,
                "min_profit_to_alert": 120,
                "allow_mint_for_alerts": True,
                "background_poll_enabled": True,
                "background_poll_seconds": 900,
            },
        )

    assert initial.status_code == 200
    assert updated.status_code == 200
    assert updated.json()["settings"]["min_score_to_alert"] == 82
    assert updated.json()["settings"]["allow_mint_for_alerts"] is True


def test_user_cannot_read_another_users_settings(monkeypatch, tmp_path):
    storage, _settings = _configure_app(monkeypatch, tmp_path)
    storage.create_user(email="one@example.com", password_hash=hash_password("pass-one"), role="user", account_status="active")
    storage.create_user(email="two@example.com", password_hash=hash_password("pass-two"), role="user", account_status="active")

    with TestClient(main.app) as client:
        token_one = _login(client, "one@example.com", "pass-one")
        token_two = _login(client, "two@example.com", "pass-two")
        client.put(
            "/settings",
            headers=_auth_headers(token_one),
            json={
                "min_score_to_alert": 80,
                "min_profit_to_alert": 90,
                "risky_score_min": 35,
                "risky_score_max": 69.99,
                "max_alert_item_age_minutes": 180,
                "max_priority_review_item_age_hours": 24,
                "max_active_queue_item_age_hours": 24,
                "default_resale_condition": "good",
                "allow_mint_for_alerts": False,
                "target_min_model_generation": 0,
                "background_poll_enabled": False,
                "background_poll_seconds": 300,
                "active_start": None,
                "active_end": None,
                "timezone": "America/New_York",
            },
        )
        own = client.get("/settings", headers=_auth_headers(token_one))
        other = client.get("/settings", headers=_auth_headers(token_two))

    assert own.status_code == 200
    assert other.status_code == 200
    assert own.json()["user"]["email"] == "one@example.com"
    assert other.json()["user"]["email"] == "two@example.com"
    assert own.json()["settings"]["min_score_to_alert"] == 80
    assert other.json()["settings"]["min_score_to_alert"] != 80


def test_keyword_add_update_delete(monkeypatch, tmp_path):
    storage, _settings = _configure_app(monkeypatch, tmp_path)
    storage.create_user(email="keywords@example.com", password_hash=hash_password("keyword-pass"), role="user", account_status="active")

    with TestClient(main.app) as client:
        token = _login(client, "keywords@example.com", "keyword-pass")
        created = client.post("/settings/keywords", headers=_auth_headers(token), json={"keyword": "iPhone 15 Pro cracked", "enabled": True})
        keyword_id = created.json()["keyword"]["id"]
        updated = client.patch(f"/settings/keywords/{keyword_id}", headers=_auth_headers(token), json={"enabled": False})
        deleted = client.delete(f"/settings/keywords/{keyword_id}", headers=_auth_headers(token))
        listed = client.get("/settings/keywords", headers=_auth_headers(token))

    assert created.status_code == 200
    assert updated.status_code == 200
    assert updated.json()["keyword"]["enabled"] is False
    assert deleted.status_code == 200
    assert all(entry["id"] != keyword_id for entry in listed.json()["keywords"])


def test_notification_settings_save_without_logging_secret(monkeypatch, tmp_path, caplog):
    storage, _settings = _configure_app(monkeypatch, tmp_path)
    storage.create_user(email="notify@example.com", password_hash=hash_password("notify-pass"), role="user", account_status="active")
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
                "notify_priority_review": False,
            },
        )
        fetched = client.get("/settings/notifications", headers=_auth_headers(token))

    assert saved.status_code == 200
    assert fetched.status_code == 200
    assert saved.json()["notifications"]["discord_webhook"] == ""
    assert fetched.json()["notifications"]["discord_webhook"] == ""
    assert fetched.json()["notifications"]["discord_webhook_configured"] is True
    assert secret not in caplog.text


def test_auth_not_required_settings_endpoints_still_work(monkeypatch, tmp_path):
    _configure_app(monkeypatch, tmp_path, auth_required=False)

    with TestClient(main.app) as client:
        settings_response = client.get("/settings")
        keywords_response = client.get("/settings/keywords")
        notifications_response = client.get("/settings/notifications")

    assert settings_response.status_code == 200
    assert settings_response.json()["user"]["email"] == "local@notifierr.local"
    assert keywords_response.status_code == 200
    assert notifications_response.status_code == 200


def test_disabled_user_cannot_access_settings(monkeypatch, tmp_path):
    storage, _settings = _configure_app(monkeypatch, tmp_path)
    user = storage.create_user(email="disabled-settings@example.com", password_hash=hash_password("disabled-pass"), role="user", account_status="active")

    with TestClient(main.app) as client:
        token = _login(client, "disabled-settings@example.com", "disabled-pass")
        storage.set_user_account_status(int(user["id"]), "disabled")
        response = client.get("/settings", headers=_auth_headers(token))

    assert response.status_code == 403
    assert response.json()["detail"] == "Account is disabled"
