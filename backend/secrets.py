from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken


ENCRYPTED_PREFIX = "fernet:"


class SecretConfigurationError(RuntimeError):
    pass


def is_encrypted_secret(value: str | None) -> bool:
    return str(value or "").startswith(ENCRYPTED_PREFIX)


def encrypt_secret(value: str, encryption_key: str | None) -> str:
    plaintext = str(value or "").strip()
    if not plaintext:
        return ""
    fernet = _build_fernet(encryption_key)
    token = fernet.encrypt(plaintext.encode("utf-8")).decode("utf-8")
    return f"{ENCRYPTED_PREFIX}{token}"


def decrypt_secret(value: str, encryption_key: str | None, *, allow_plaintext_fallback: bool = True) -> str:
    stored = str(value or "").strip()
    if not stored:
        return ""
    if not is_encrypted_secret(stored):
        if allow_plaintext_fallback:
            return stored
        raise SecretConfigurationError("Secret is stored in plaintext")
    fernet = _build_fernet(encryption_key)
    token = stored[len(ENCRYPTED_PREFIX):].encode("utf-8")
    try:
        return fernet.decrypt(token).decode("utf-8")
    except InvalidToken as exc:
        raise SecretConfigurationError("Stored secret could not be decrypted with APP_ENCRYPTION_KEY") from exc


def _build_fernet(encryption_key: str | None) -> Fernet:
    key = str(encryption_key or "").strip()
    if not key:
        raise SecretConfigurationError("APP_ENCRYPTION_KEY is not configured")
    try:
        return Fernet(key.encode("utf-8"))
    except Exception as exc:
        raise SecretConfigurationError("APP_ENCRYPTION_KEY is not a valid Fernet key") from exc
