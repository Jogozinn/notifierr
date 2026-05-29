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


def test_admin_bootstrap_creates_first_admin(monkeypatch, tmp_path):
    storage = _configure_app(
        monkeypatch,
        tmp_path,
        admin_email="admin@example.com",
        admin_password="bootstrap-pass",
        admin_display_name="Bootstrap Admin",
    )

    with TestClient(main.app):
        pass

    admin = storage.get_user_by_email("admin@example.com", include_password_hash=True)
    assert admin is not None
    assert admin["role"] == "admin"
    assert admin["account_status"] == "active"
    assert admin["display_name"] == "Bootstrap Admin"


def test_first_registered_user_becomes_admin(monkeypatch, tmp_path):
    storage = _configure_app(monkeypatch, tmp_path)

    with TestClient(main.app) as client:
        response = client.post(
            "/auth/register",
            json={
                "email": "owner@example.com",
                "password": "owner-pass",
                "display_name": "Owner",
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["user"]["role"] == "admin"
    created = storage.get_user_by_email("owner@example.com", include_password_hash=True)
    assert created is not None
    assert created["role"] == "admin"
    assert created["account_status"] == "active"


def test_login_success_updates_last_login(monkeypatch, tmp_path):
    storage = _configure_app(monkeypatch, tmp_path)
    storage.create_user(
        email="user@example.com",
        password_hash=hash_password("good-password"),
        role="user",
        account_status="active",
        display_name="User One",
    )

    with TestClient(main.app) as client:
        response = client.post("/auth/login", json={"email": "user@example.com", "password": "good-password"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["token_type"] == "bearer"
    assert payload["user"]["email"] == "user@example.com"
    assert storage.get_user_by_email("user@example.com")["last_login_at"]


def test_second_registration_without_invite_fails(monkeypatch, tmp_path):
    _configure_app(monkeypatch, tmp_path)

    with TestClient(main.app) as client:
        first = client.post(
            "/auth/register",
            json={
                "email": "owner@example.com",
                "password": "owner-pass",
                "display_name": "Owner",
            },
        )
        assert first.status_code == 200

        response = client.post(
            "/auth/register",
            json={
                "email": "member@example.com",
                "password": "member-pass",
                "display_name": "Member",
            },
        )

    assert response.status_code == 403
    assert response.json()["detail"] == "A valid invite code is required"


def test_disabled_user_cannot_log_in(monkeypatch, tmp_path):
    storage = _configure_app(monkeypatch, tmp_path)
    storage.create_user(
        email="disabled@example.com",
        password_hash=hash_password("good-password"),
        role="user",
        account_status="disabled",
    )

    with TestClient(main.app) as client:
        response = client.post("/auth/login", json={"email": "disabled@example.com", "password": "good-password"})

    assert response.status_code == 403
    assert response.json()["detail"] == "Account is disabled"


def test_auth_status_reports_first_user_setup_required(monkeypatch, tmp_path):
    _configure_app(monkeypatch, tmp_path)

    with TestClient(main.app) as client:
        before = client.get("/auth/status")
        assert before.status_code == 200
        before_payload = before.json()
        assert before_payload["first_user_setup_required"] is True
        assert before_payload["invite_required"] is False
        assert before_payload["registration_available"] is True

        register_response = client.post(
            "/auth/register",
            json={
                "email": "owner@example.com",
                "password": "owner-pass",
                "display_name": "Owner",
            },
        )
        assert register_response.status_code == 200

        after = client.get("/auth/status")

    assert after.status_code == 200
    after_payload = after.json()
    assert after_payload["first_user_setup_required"] is False
    assert after_payload["invite_required"] is True
    assert after_payload["registration_available"] is True


def test_auth_me_returns_current_user(monkeypatch, tmp_path):
    storage = _configure_app(monkeypatch, tmp_path)
    storage.create_user(
        email="viewer@example.com",
        password_hash=hash_password("viewer-pass"),
        role="user",
        account_status="active",
        display_name="Viewer",
    )

    with TestClient(main.app) as client:
        token = _login(client, "viewer@example.com", "viewer-pass")
        response = client.get("/auth/me", headers=_auth_headers(token))

    assert response.status_code == 200
    assert response.json()["user"]["display_name"] == "Viewer"


def test_admin_can_create_invite_and_registration_with_invite_creates_user(monkeypatch, tmp_path):
    storage = _configure_app(monkeypatch, tmp_path)

    with TestClient(main.app) as client:
        owner_response = client.post(
            "/auth/register",
            json={
                "email": "owner@example.com",
                "password": "owner-pass",
                "display_name": "Owner",
            },
        )
        assert owner_response.status_code == 200
        admin_token = owner_response.json()["access_token"]

        invite_response = client.post(
            "/admin/invites",
            headers=_auth_headers(admin_token),
            json={"email": "member@example.com", "role": "user"},
        )
        assert invite_response.status_code == 200
        invite = invite_response.json()["invite"]
        assert invite["role"] == "user"

        register_response = client.post(
            "/auth/register",
            json={
                "email": "member@example.com",
                "password": "member-pass",
                "display_name": "Member",
                "invite_code": invite["code"],
            },
        )

    assert register_response.status_code == 200
    payload = register_response.json()
    assert payload["user"]["role"] == "user"
    created = storage.get_user_by_email("member@example.com")
    assert created is not None
    stored_invite = storage.get_invite(int(invite["id"]))
    assert stored_invite is not None
    assert stored_invite["used_by_user_id"] == created["id"]


def test_invite_cannot_be_reused(monkeypatch, tmp_path):
    _configure_app(monkeypatch, tmp_path)

    with TestClient(main.app) as client:
        owner_response = client.post(
            "/auth/register",
            json={"email": "owner@example.com", "password": "owner-pass", "display_name": "Owner"},
        )
        assert owner_response.status_code == 200
        admin_token = owner_response.json()["access_token"]
        invite_response = client.post(
            "/admin/invites",
            headers=_auth_headers(admin_token),
            json={"role": "user"},
        )
        invite_code = invite_response.json()["invite"]["code"]

        first_use = client.post(
            "/auth/register",
            json={
                "email": "member1@example.com",
                "password": "member-pass",
                "display_name": "Member One",
                "invite_code": invite_code,
            },
        )
        assert first_use.status_code == 200

        second_use = client.post(
            "/auth/register",
            json={
                "email": "member2@example.com",
                "password": "member-pass",
                "display_name": "Member Two",
                "invite_code": invite_code,
            },
        )

    assert second_use.status_code == 403
    assert second_use.json()["detail"] == "A valid invite code is required"


def test_revoked_invite_cannot_be_used(monkeypatch, tmp_path):
    _configure_app(monkeypatch, tmp_path)

    with TestClient(main.app) as client:
        owner_response = client.post(
            "/auth/register",
            json={"email": "owner@example.com", "password": "owner-pass", "display_name": "Owner"},
        )
        assert owner_response.status_code == 200
        admin_token = owner_response.json()["access_token"]
        invite_response = client.post(
            "/admin/invites",
            headers=_auth_headers(admin_token),
            json={"role": "user"},
        )
        invite = invite_response.json()["invite"]
        revoke_response = client.post(
            f"/admin/invites/{invite['id']}/revoke",
            headers=_auth_headers(admin_token),
        )
        assert revoke_response.status_code == 200

        register_response = client.post(
            "/auth/register",
            json={
                "email": "member@example.com",
                "password": "member-pass",
                "display_name": "Member",
                "invite_code": invite["code"],
            },
        )

    assert register_response.status_code == 403
    assert register_response.json()["detail"] == "A valid invite code is required"


def test_expired_invite_cannot_be_used(monkeypatch, tmp_path):
    _configure_app(monkeypatch, tmp_path)

    with TestClient(main.app) as client:
        owner_response = client.post(
            "/auth/register",
            json={"email": "owner@example.com", "password": "owner-pass", "display_name": "Owner"},
        )
        assert owner_response.status_code == 200
        admin_token = owner_response.json()["access_token"]
        invite_response = client.post(
            "/admin/invites",
            headers=_auth_headers(admin_token),
            json={"role": "user", "expires_at": "2000-01-01T00:00:00Z"},
        )
        invite = invite_response.json()["invite"]

        register_response = client.post(
            "/auth/register",
            json={
                "email": "member@example.com",
                "password": "member-pass",
                "display_name": "Member",
                "invite_code": invite["code"],
            },
        )

    assert register_response.status_code == 403
    assert register_response.json()["detail"] == "A valid invite code is required"


def test_admin_can_create_user(monkeypatch, tmp_path):
    _configure_app(
        monkeypatch,
        tmp_path,
        admin_email="admin@example.com",
        admin_password="bootstrap-pass",
    )

    with TestClient(main.app) as client:
        admin_token = _login(client, "admin@example.com", "bootstrap-pass")
        response = client.post(
            "/admin/users",
            headers=_auth_headers(admin_token),
            json={
                "email": "new-user@example.com",
                "password": "temp-password",
                "display_name": "New User",
                "account_status": "active",
                "role": "user",
            },
        )

    assert response.status_code == 200
    assert response.json()["user"]["email"] == "new-user@example.com"


def test_non_admin_is_blocked_from_admin_endpoint(monkeypatch, tmp_path):
    storage = _configure_app(monkeypatch, tmp_path)
    storage.create_user(
        email="member@example.com",
        password_hash=hash_password("member-pass"),
        role="user",
        account_status="active",
    )

    with TestClient(main.app) as client:
        token = _login(client, "member@example.com", "member-pass")
        response = client.get("/admin/users", headers=_auth_headers(token))

    assert response.status_code == 403
    assert response.json()["detail"] == "Admin access required"


def test_auth_not_required_keeps_dashboard_endpoints_usable(monkeypatch, tmp_path):
    _configure_app(monkeypatch, tmp_path, auth_required=False)

    with TestClient(main.app) as client:
        stats_response = client.get("/stats")
        items_response = client.get("/items")

    assert stats_response.status_code == 200
    assert items_response.status_code == 200
    assert stats_response.json()["total"] == 0
    assert items_response.json() == []
