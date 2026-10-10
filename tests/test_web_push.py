from __future__ import annotations

import base64
import json
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from backend.config import Settings
from backend import main
from backend.auth import hash_password
from backend.push import endpoint_hash, push_payload, send_push_to_user
from backend.secrets import encrypt_secret
from backend.storage import Storage
from urllib.parse import parse_qs, urlsplit


def _settings() -> Settings:
    private_key = ec.generate_private_key(ec.SECP256R1())
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_key = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.X962,
        format=serialization.PublicFormat.UncompressedPoint,
    )
    return Settings(
        app_encryption_key=Fernet.generate_key().decode(),
        vapid_public_key=base64.urlsafe_b64encode(public_key).rstrip(b"=").decode(),
        vapid_private_key_b64=base64.b64encode(private_pem).decode(),
        vapid_subject="mailto:test@example.com",
    )


def _subscription(storage: Storage, settings: Settings) -> tuple[int, str]:
    user = storage.create_user(
        email="push@example.com",
        password_hash="hash",
        baseline_keywords=[],
    )
    endpoint = "https://push.example.test/subscription/one"
    storage.upsert_push_subscription(
        user_id=int(user["id"]),
        endpoint_hash=endpoint_hash(endpoint),
        endpoint=encrypt_secret(endpoint, settings.app_encryption_key),
        p256dh=encrypt_secret("p256dh-key", settings.app_encryption_key),
        auth=encrypt_secret("auth-key", settings.app_encryption_key),
        device_label="Test phone",
    )
    return int(user["id"]), endpoint


def test_push_payload_contains_encoded_stable_item_deep_link():
    payload = push_payload({"item_id": "v1|phone/with space", "total_cost": 175}, tier="PROFITABLE")

    assert payload["item_id"] == "v1|phone/with space"
    assert payload["url"] == "/?item=v1%7Cphone%2Fwith%20space"
    assert parse_qs(urlsplit(payload["url"]).query)["item"] == [payload["item_id"]]


def test_push_delivery_records_selected_and_accepted(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "push.sqlite3")
    settings = _settings()
    user_id, endpoint = _subscription(storage, settings)
    calls = []

    def sender(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(status_code=201)

    result = send_push_to_user(
        storage,
        settings,
        user_id=user_id,
        item={"item_id": "123", "model": "iPhone 15", "profit_mid": 90, "total_cost": 220},
        tier="GEM",
        scan_cycle_id=None,
        sender=sender,
    )

    assert result.as_dict() == {"selected": 1, "accepted": 1, "failed": 0, "disabled": 0}
    assert calls[0]["subscription_info"]["endpoint"] == endpoint
    payload = json.loads(calls[0]["data"])
    assert payload["title"] == "iPhone 15 · $220 · +$90 projected"
    assert payload["item_id"] == "123"
    assert payload["url"] == "/?item=123"
    assert payload["body"] == "GEM · Repair opportunity · Just listed"
    assert [row["status"] for row in storage.list_push_delivery_attempts(user_id)] == ["accepted", "attempted", "selected"]


def test_invalid_push_subscription_is_disabled(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "invalid.sqlite3")
    settings = _settings()
    user_id, _ = _subscription(storage, settings)

    class Gone(Exception):
        response = SimpleNamespace(status_code=410)

    def sender(**_kwargs):
        raise Gone()

    result = send_push_to_user(
        storage,
        settings,
        user_id=user_id,
        item={"item_id": "gone"},
        tier="REVIEW",
        sender=sender,
    )

    assert result.disabled == 1
    assert storage.list_push_subscriptions(user_id, enabled_only=True) == []
    attempts = storage.list_push_delivery_attempts(user_id)
    assert attempts[0]["status"] == "invalid"
    assert attempts[0]["provider_status"] == 410


def test_subscription_endpoint_is_authenticated_and_encrypted(monkeypatch, tmp_path: Path) -> None:
    storage = Storage(tmp_path / "api.sqlite3")
    settings = _settings()
    settings = Settings(
        **{
            **settings.__dict__,
            "auth_required": True,
            "auth_secret_key": "test-auth-secret-value-is-long-enough",
            "access_token_expire_minutes": 60,
        }
    )
    storage.create_user(
        email="api-push@example.com",
        password_hash=hash_password("push-password"),
        role="admin",
        account_status="active",
    )
    monkeypatch.setattr(main, "storage", storage)
    monkeypatch.setattr(main, "settings", settings)

    with TestClient(main.app) as client:
        assert client.post("/push/subscriptions", json={}).status_code == 401
        login = client.post("/auth/login", json={"email": "api-push@example.com", "password": "push-password"})
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        response = client.post(
            "/push/subscriptions",
            headers=headers,
            json={
                "endpoint": "https://push.example.test/subscription/api",
                "keys": {"p256dh": "browser-public-key", "auth": "browser-auth-secret"},
                "device_label": "Phone",
            },
        )

    assert response.status_code == 200, response.text
    stored = storage.list_push_subscriptions(1)[0]
    assert stored["endpoint"].startswith("fernet:")
    assert "push.example.test" not in stored["endpoint"]
