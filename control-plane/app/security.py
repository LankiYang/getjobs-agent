"""Password hashing and JWT helpers."""

import hashlib
import hmac
import secrets
import time

import jwt

from .config import JWT_TTL_HOURS, jwt_secret

PBKDF2_ITERATIONS = 210_000


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algorithm, iterations, salt_hex, digest_hex = stored.split("$")
        if algorithm != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), int(iterations)
        )
        return hmac.compare_digest(digest.hex(), digest_hex)
    except Exception:
        return False


def create_token(user_id: int) -> str:
    now = int(time.time())
    payload = {"sub": str(user_id), "iat": now, "exp": now + JWT_TTL_HOURS * 3600}
    return jwt.encode(payload, jwt_secret(), algorithm="HS256")


def decode_token(token: str) -> int | None:
    try:
        payload = jwt.decode(token, jwt_secret(), algorithms=["HS256"])
        return int(payload["sub"])
    except Exception:
        return None


def create_ticket(user_id: int, scope: str, ttl: int = 120) -> str:
    now = int(time.time())
    payload = {"sub": str(user_id), "scope": scope, "iat": now, "exp": now + ttl}
    return jwt.encode(payload, jwt_secret(), algorithm="HS256")


def decode_ticket(token: str, scope: str) -> int | None:
    try:
        payload = jwt.decode(token, jwt_secret(), algorithms=["HS256"])
        if payload.get("scope") != scope:
            return None
        return int(payload["sub"])
    except Exception:
        return None