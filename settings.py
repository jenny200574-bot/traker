"""
settings.py — Lectura y escritura de configuración local (settings.json)
"""
import json

from paths import SETTINGS_FILE

DEFAULT_SETTINGS: dict = {
    "email":    "",
    "password": "",
    "token":    "",          # token JWT cacheado (se renueva automáticamente)
    "horarios": {
        "08:00": True,
        "12:00": True,
        "18:00": True,
        "00:00": True,
    },
}


def load_settings() -> dict:
    if SETTINGS_FILE.exists():
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            # Merge missing keys from defaults
            for key, value in DEFAULT_SETTINGS.items():
                if key not in data:
                    data[key] = value
            return data
        except Exception:
            pass
    return DEFAULT_SETTINGS.copy()


def save_settings(settings: dict) -> None:
    with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2, ensure_ascii=False)


def get_email() -> str:
    return load_settings().get("email", "")


def get_password() -> str:
    return load_settings().get("password", "")


def save_credentials(email: str, password: str) -> None:
    s = load_settings()
    s["email"]    = email.strip()
    s["password"] = password
    save_settings(s)


def get_token() -> str:
    return load_settings().get("token", "")


def save_token(token: str) -> None:
    s = load_settings()
    s["token"] = token.strip()
    save_settings(s)


def get_horarios() -> dict:
    return load_settings().get("horarios", DEFAULT_SETTINGS["horarios"])


def save_horarios(horarios: dict) -> None:
    s = load_settings()
    s["horarios"] = horarios
    save_settings(s)
