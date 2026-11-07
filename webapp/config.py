"""Configuración por entorno. No hay secretos ni usuarios por defecto."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def load_env_file(path: Path | None = None) -> None:
    """Carga un .env local sin pisar variables ya definidas y sin registrar valores."""
    env_path = path or Path(".env")
    if not env_path.is_file():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, value)


@dataclass(frozen=True)
class AppConfig:
    secret_key: str
    data_dir: Path
    host: str = "127.0.0.1"
    port: int = 8000
    timezone: str = "America/Bogota"
    allow_lan: bool = False
    poll_seconds: float = 15.0

    @property
    def db_path(self) -> Path:
        return self.data_dir / "tracker.db"

    @property
    def demo_db_path(self) -> Path:
        return self.data_dir / "demo.db"

    @property
    def backup_dir(self) -> Path:
        return self.data_dir / "backups"

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.backup_dir.mkdir(parents=True, exist_ok=True)

    @classmethod
    def from_env(cls) -> "AppConfig":
        load_env_file()
        secret = os.environ.get("DROP_SECRET_KEY", "").strip()
        if not secret:
            raise RuntimeError(
                "Falta DROP_SECRET_KEY. Copia .env.example a .env y escribe un valor propio. "
                "No existe una clave ni un usuario por defecto."
            )
        data_dir = Path(os.environ.get("DROP_DATA_DIR", "data")).expanduser()
        host = os.environ.get("DROP_HOST", "127.0.0.1").strip() or "127.0.0.1"
        port = int(os.environ.get("DROP_PORT", "8000"))
        timezone = os.environ.get("DROP_TIMEZONE", "America/Bogota").strip() or "America/Bogota"
        allow_lan = os.environ.get("DROP_ALLOW_LAN", "").strip() == "1"
        poll = float(os.environ.get("DROP_POLL_SECONDS", "15"))
        return cls(
            secret_key=secret,
            data_dir=data_dir,
            host=host,
            port=port,
            timezone=timezone,
            allow_lan=allow_lan,
            poll_seconds=poll,
        )


LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}


def assert_bind_allowed(config: AppConfig) -> None:
    if config.host not in LOCAL_HOSTS and not config.allow_lan:
        raise SystemExit(
            "La web solo escucha en local (127.0.0.1) salvo que definas DROP_ALLOW_LAN=1. "
            "Aun así, toda la aplicación exige sesión: no hay acceso anónimo."
        )
