"""Arranque local: python -m webapp"""
from __future__ import annotations

import uvicorn

from webapp.config import AppConfig, assert_bind_allowed
from webapp.server import create_app


def main() -> None:
    config = AppConfig.from_env()
    assert_bind_allowed(config)
    config.ensure_dirs()
    uvicorn.run(create_app(config), host=config.host, port=config.port, log_level="info")


if __name__ == "__main__":
    main()
