"""Cola persistente. Un solo trabajo corre a la vez."""
from __future__ import annotations

import sqlite3

from webapp.timeutil import iso, utcnow

ACTIVE = ("queued", "running")
JOB_TYPES = {"descargar", "sincronizar", "metricas", "probar_conexion"}


def enqueue(
    conn: sqlite3.Connection,
    job_type: str,
    payload: str | None = None,
    *,
    manage: bool = True,
) -> dict:
    if job_type not in JOB_TYPES:
        raise ValueError("Tipo de trabajo desconocido.")
    now = iso(utcnow())
    if manage:
        conn.execute("BEGIN IMMEDIATE")
    try:
        existing = conn.execute(
            """SELECT id, status FROM jobs
               WHERE job_type=? AND status IN ('queued','running')
               ORDER BY id LIMIT 1""",
            (job_type,),
        ).fetchone()
        if existing:
            if manage:
                conn.execute("COMMIT")
            return {"id": existing["id"], "status": existing["status"], "already": True}
        cur = conn.execute(
            """INSERT INTO jobs(job_type, status, payload, log, created_at)
               VALUES(?, 'queued', ?, '', ?)""",
            (job_type, payload, now),
        )
        if manage:
            conn.execute("COMMIT")
        return {"id": int(cur.lastrowid), "status": "queued", "already": False}
    except Exception:
        if manage:
            conn.execute("ROLLBACK")
        raise


def claim_next(conn: sqlite3.Connection) -> sqlite3.Row | None:
    """Reclama el trabajo más antiguo. Si ya hay uno en curso, no reclama otro."""
    now = iso(utcnow())
    conn.execute("BEGIN IMMEDIATE")
    try:
        running = conn.execute(
            "SELECT id FROM jobs WHERE status='running' LIMIT 1"
        ).fetchone()
        if running:
            conn.execute("ROLLBACK")
            return None
        row = conn.execute(
            """SELECT * FROM jobs WHERE status='queued' ORDER BY id LIMIT 1"""
        ).fetchone()
        if row is None:
            conn.execute("ROLLBACK")
            return None
        cur = conn.execute(
            """UPDATE jobs SET status='running', started_at=?
               WHERE id=? AND status='queued'""",
            (now, row["id"]),
        )
        if cur.rowcount != 1:
            conn.execute("ROLLBACK")
            return None
        conn.execute("COMMIT")
        return conn.execute("SELECT * FROM jobs WHERE id=?", (row["id"],)).fetchone()
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        raise


def append_log(conn: sqlite3.Connection, job_id: int, line: str) -> None:
    clean = _redact(line)
    conn.execute(
        """UPDATE jobs SET log = CASE
            WHEN log = '' THEN ?
            ELSE log || char(10) || ?
        END, progress=? WHERE id=?""",
        (clean, clean, clean[:180], job_id),
    )


def request_cancel(conn: sqlite3.Connection, job_id: int) -> str | None:
    row = conn.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()
    if row is None:
        return None
    if row["status"] == "queued":
        finish(conn, job_id, "cancelled", "Cancelado antes de empezar.")
        return "cancelled"
    if row["status"] == "running":
        conn.execute("UPDATE jobs SET cancel_requested=1 WHERE id=?", (job_id,))
        append_log(conn, job_id, "Cancelación pedida. Se detiene entre proveedores.")
        return "running"
    return row["status"]


def cancelled(conn: sqlite3.Connection, job_id: int) -> bool:
    row = conn.execute(
        "SELECT cancel_requested FROM jobs WHERE id=?",
        (job_id,),
    ).fetchone()
    return bool(row and row["cancel_requested"])


def finish(conn: sqlite3.Connection, job_id: int, status: str, error: str | None = None) -> None:
    conn.execute(
        """UPDATE jobs SET status=?, finished_at=?, error=? WHERE id=?""",
        (status, iso(utcnow()), _redact(error or ""), job_id),
    )


def list_jobs(conn: sqlite3.Connection, limit: int = 40) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM jobs ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [dict(row) for row in rows]


def _redact(text: str | None) -> str:
    if not text:
        return ""
    lowered = text.lower()
    if "bearer " in lowered or "password" in lowered or "contraseña" in lowered:
        return "Línea omitida porque podía contener un secreto."
    return text[:2000]
