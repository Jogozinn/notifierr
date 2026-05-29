from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from jwt import InvalidTokenError
from passlib.context import CryptContext

from .config import Settings


ACCESS_TOKEN_TYPE = "access"
PASSWORD_CONTEXT = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    return PASSWORD_CONTEXT.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    if not password_hash:
        return False
    return PASSWORD_CONTEXT.verify(password, password_hash)


def create_access_token(user: dict[str, Any], settings: Settings) -> str:
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(minutes=max(1, settings.access_token_expire_minutes))
    payload = {
        "sub": str(user["id"]),
        "email": str(user["email"]),
        "role": str(user["role"]),
        "type": ACCESS_TOKEN_TYPE,
        "iat": int(now.timestamp()),
        "exp": int(expires_at.timestamp()),
    }
    return jwt.encode(payload, settings.auth_secret_key, algorithm="HS256")


def decode_access_token(token: str, settings: Settings) -> dict[str, Any]:
    payload = jwt.decode(token, settings.auth_secret_key, algorithms=["HS256"])
    if payload.get("type") != ACCESS_TOKEN_TYPE:
        raise InvalidTokenError("Invalid token type")
    return payload
