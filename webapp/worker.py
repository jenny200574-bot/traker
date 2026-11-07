"""Proceso del worker. Sigue en marcha aunque el navegador se cierre."""
from __future__ import annotations

import time

from webapp.config import AppConfig
from webapp.db import connect, init_db, setting
from webapp.jobs import claim_next
from webapp.runner import execute
from webapp.schedule import fire_due_schedules
from webapp.timeutil import utcnow


def tick(config: AppConfig) -> int | None:
    init_db(config.db_path, config.timezone)
    conn = connect(config.db_path)
    try:
        zone = setting(conn, "timezone", config.timezone) or config.timezone
        fire_due_schedules(conn, utcnow(), zone)
        job = claim_next(conn)
        if job is None:
            return None
        execute(conn, job, config.secret_key)
        return int(job["id"])
    finally:
        conn.close()


def main() -> None:
    config = AppConfig.from_env()
    config.ensure_dirs()
    init_db(config.db_path, config.timezone)
    while True:
        tick(config)
        time.sleep(config.poll_seconds)


if __name__ == "__main__":
    main()
