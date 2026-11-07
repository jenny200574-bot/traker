"""Secretos de Dropi cifrados en la base real. El navegador no los recibe."""
from __future__ import annotations

import sqlite3

from webapp.db import get_secret, put_secret
from webapp.security import decrypt_secret, encrypt_secret, mask_email
from webapp.timeutil import iso, utcnow


def save_dropi_credentials(
    conn: sqlite3.Connection,
    secret_key: str,
    *,
    email: str | None = None,
    password: str | None = None,
    token: str | None = None,
) -> None:
    now = iso(utcnow())
    if email is not None:
        put_secret(conn, "dropi_email", encrypt_secret(secret_key, email.strip()), now)
    if password:
        put_secret(conn, "dropi_password", encrypt_secret(secret_key, password), now)
    if token is not None:
        put_secret(conn, "dropi_token", encrypt_secret(secret_key, token.strip()), now)


def clear_dropi_token(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM secrets WHERE key='dropi_token'")


def load_dropi_credentials(conn: sqlite3.Connection, secret_key: str) -> tuple[str, str, str]:
    def read(key: str) -> str:
        stored = get_secret(conn, key)
        if not stored:
            return ""
        return decrypt_secret(secret_key, stored)

    return read("dropi_email"), read("dropi_password"), read("dropi_token")


def dropi_public_status(conn: sqlite3.Connection, secret_key: str) -> dict:
    email, password, token = load_dropi_credentials(conn, secret_key)
    return {
        "email_enmascarado": mask_email(email),
        "tiene_correo": bool(email),
        "tiene_clave": bool(password),
        "tiene_token": bool(token),
    }
