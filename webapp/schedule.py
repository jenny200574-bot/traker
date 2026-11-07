"""Horarios en la zona configurada. No recupera horas ya perdidas."""
from __future__ import annotations

import sqlite3
from datetime import datetime

from webapp.jobs import enqueue
from webapp.timeutil import iso, utcnow, zone_or_default


def fire_due_schedules(conn: sqlite3.Connection, now_utc: datetime, timezone_name: str) -> list[int]:
    local = now_utc.astimezone(zone_or_default(timezone_name))
    slot = local.date().isoformat()
    rows = conn.execute(
        "SELECT * FROM schedules WHERE enabled=1"
    ).fetchall()
    created: list[int] = []
    conn.execute("BEGIN IMMEDIATE")
    try:
        for row in rows:
            current = conn.execute(
                "SELECT last_fired_on FROM schedules WHERE id=?",
                (row["id"],),
            ).fetchone()
            if current and current["last_fired_on"] == slot:
                continue
            scheduled = local.replace(
                hour=int(row["hour"]),
                minute=int(row["minute"]),
                second=0,
                microsecond=0,
            )
            delta = (local - scheduled).total_seconds()
            if not (0 <= delta < 120):
                continue
            active = conn.execute(
                """SELECT id FROM jobs
                   WHERE job_type=? AND status IN ('queued','running') LIMIT 1""",
                (row["job_type"],),
            ).fetchone()
            if active:
                cur = conn.execute(
                    """INSERT INTO jobs(job_type, status, log, created_at, finished_at, error)
                       VALUES(?, 'omitido', ?, ?, ?, ?)""",
                    (
                        row["job_type"],
                        f"Horario {row['hour']:02d}:{row['minute']:02d} omitido: ya hay un trabajo igual en cola o en curso.",
                        iso(utcnow()),
                        iso(utcnow()),
                        "solapado",
                    ),
                )
                created.append(int(cur.lastrowid))
            else:
                result = enqueue(conn, row["job_type"], manage=False)
                created.append(result["id"])
            conn.execute(
                "UPDATE schedules SET last_fired_on=? WHERE id=?",
                (slot, row["id"]),
            )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return created
