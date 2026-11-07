"""Contraseñas, cifrado en reposo y enmascarado. Nada de esto sale al navegador."""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

from cryptography.fernet import Fernet, InvalidToken


def hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 200_000)
    return "pbkdf2_sha256$200000$" + salt.hex() + "$" + digest.hex()


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, rounds, salt_hex, digest_hex = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(digest_hex)
        got = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, int(rounds))
        return hmac.compare_digest(got, expected)
    except (ValueError, TypeError):
        return False


def _fernet(secret_key: str) -> Fernet:
    raw = hashlib.pbkdf2_hmac(
        "sha256",
        secret_key.encode("utf-8"),
        b"dropi-tracker-web-secrets-v1",
        120_000,
        dklen=32,
    )
    return Fernet(base64.urlsafe_b64encode(raw))


def encrypt_secret(secret_key: str, value: str) -> str:
    return _fernet(secret_key).encrypt(value.encode("utf-8")).decode("ascii")


def decrypt_secret(secret_key: str, token: str) -> str:
    try:
        return _fernet(secret_key).decrypt(token.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError, TypeError) as exc:
        raise ValueError("No se pudo leer un secreto local. Revisa DROP_SECRET_KEY.") from exc


def mask_email(email: str) -> str:
    email = (email or "").strip()
    if "@" not in email:
        return ""
    name, domain = email.split("@", 1)
    if not name:
        return "***@" + domain
    return name[:1] + "***@" + domain


def new_csrf() -> str:
    return secrets.token_urlsafe(32)
