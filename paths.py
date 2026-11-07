"""
paths.py — Rutas portables.

Toda la memoria (DB, settings, backups) vive junto a la carpeta de la app,
no en el perfil de Windows. Copiar esa carpeta a otro PC conserva los datos.
"""
from pathlib import Path
import sys


def get_app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


APP_DIR = get_app_dir()
DB_PATH = APP_DIR / "dropi_tracker.db"
SETTINGS_FILE = APP_DIR / "settings.json"
BACKUP_DIR = APP_DIR / "backups_historial"
