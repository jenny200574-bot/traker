"""Ejecuta un trabajo ya reclamado. Las llamadas externas solo ocurren aquí."""
from __future__ import annotations

import sqlite3

import api_dropi

from webapp.catalog_fetch import fetch_supplier_catalog
from webapp.jobs import append_log, cancelled, finish
from webapp.persist import persist_catalog
from webapp.secrets_store import load_dropi_credentials, save_dropi_credentials
from webapp.timeutil import iso, utcnow


def execute(conn: sqlite3.Connection, job: sqlite3.Row, secret_key: str) -> None:
    job_id = int(job["id"])
    kind = job["job_type"]
    try:
        if kind == "probar_conexion":
            _probe(conn, job_id, secret_key)
        elif kind == "descargar":
            _download(conn, job_id, secret_key)
        elif kind == "sincronizar":
            _sync_directory(conn, job_id, secret_key)
        elif kind == "metricas":
            _metrics(conn, job_id, secret_key)
        else:
            finish(conn, job_id, "failed", "Tipo de trabajo no implementado.")
            return
        if cancelled(conn, job_id):
            finish(conn, job_id, "cancelled", "Cancelado.")
        else:
            finish(conn, job_id, "success", None)
    except Exception as exc:
        message = str(exc) if isinstance(exc, RuntimeError) else exc.__class__.__name__
        append_log(conn, job_id, message)
        finish(conn, job_id, "failed", message)


def _credentials(conn: sqlite3.Connection, secret_key: str) -> tuple[str, str, str]:
    return load_dropi_credentials(conn, secret_key)


def _login(conn: sqlite3.Connection, job_id: int, secret_key: str) -> str:
    email, password, _token = _credentials(conn, secret_key)
    if not email or not password:
        raise RuntimeError("Faltan el correo o la clave de Dropi en el servidor.")
    append_log(conn, job_id, "Iniciando sesión en Dropi.")
    token = api_dropi.login_dropi(email, password)
    save_dropi_credentials(conn, secret_key, token=token)
    append_log(conn, job_id, "Sesión aceptada. El token queda cifrado en el servidor.")
    return token


def _probe(conn: sqlite3.Connection, job_id: int, secret_key: str) -> None:
    _login(conn, job_id, secret_key)
    append_log(conn, job_id, "Prueba de conexión terminada.")


def _followed(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """SELECT supplier_id,
                  COALESCE(NULLIF(store_name, ''), NULLIF(manual_name, ''), 'Proveedor ' || supplier_id) AS nombre,
                  productos_esperados
           FROM suppliers
           WHERE follow_catalog=1
              OR supplier_id IN (SELECT supplier_id FROM warehouses WHERE follow=1)
           ORDER BY supplier_id"""
    ).fetchall()


def _download(conn: sqlite3.Connection, job_id: int, secret_key: str) -> None:
    targets = _followed(conn)
    if not targets:
        append_log(conn, job_id, "No hay proveedores en seguimiento. No se llamó a Dropi.")
        return
    token = _login(conn, job_id, secret_key)
    for index, target in enumerate(targets, start=1):
        if cancelled(conn, job_id):
            append_log(conn, job_id, "Cancelado entre proveedores.")
            return
        sid = int(target["supplier_id"])
        append_log(conn, job_id, f"{index}/{len(targets)} {target['nombre']}")
        expected = int(target["productos_esperados"] or 0)

        def emit(message: str, _sid=sid) -> None:
            append_log(conn, job_id, f"[{_sid}] {message}")

        try:
            fetched = fetch_supplier_catalog(
                token,
                sid,
                expected=expected or None,
                stop_event=_Cancel(conn, job_id),
                log=emit,
            )
        except PermissionError:
            append_log(conn, job_id, f"[{sid}] Token rechazado. Se intenta una sesión nueva.")
            token = _login(conn, job_id, secret_key)
            fetched = fetch_supplier_catalog(
                token,
                sid,
                expected=expected or None,
                stop_event=_Cancel(conn, job_id),
                log=emit,
            )
        started = iso(utcnow())
        persist_catalog(conn, sid, job_id, fetched, started_at=started)
        append_log(
            conn,
            job_id,
            f"[{sid}] Estado {fetched.status}. Productos únicos {len(fetched.products)}. "
            f"Errores de página {fetched.page_errors}.",
        )


def _sync_directory(conn: sqlite3.Connection, job_id: int, secret_key: str) -> None:
    token = _login(conn, job_id, secret_key)

    def callback(message: str, batch=None) -> None:
        append_log(conn, job_id, message)
        if batch:
            _upsert_directory(conn, batch)

    proveedores = api_dropi.obtener_proveedores_dropi(token, callback=callback)
    append_log(conn, job_id, f"Directorio recibido: {len(proveedores)} registros.")


def _upsert_directory(conn: sqlite3.Connection, proveedores: list[dict]) -> None:
    now = iso(utcnow())
    conn.execute("BEGIN IMMEDIATE")
    try:
        for prov in proveedores:
            sid = prov.get("id")
            if not sid:
                continue
            sid = int(sid)
            conn.execute(
                """INSERT INTO suppliers(supplier_id, created_at, updated_at)
                   VALUES(?,?,?) ON CONFLICT(supplier_id) DO NOTHING""",
                (sid, now, now),
            )
            nombre_tienda = (prov.get("store_name") or prov.get("name") or "").strip()
            conn.execute(
                """UPDATE suppliers SET
                    store_name=COALESCE(NULLIF(?, ''), store_name),
                    owner_name=?,
                    phone=?,
                    store_url=?,
                    logo_url=?,
                    favorite=?,
                    products_count=?,
                    source_synced=1,
                    updated_at=?
                   WHERE supplier_id=?""",
                (
                    nombre_tienda,
                    prov.get("name"),
                    prov.get("phone"),
                    prov.get("store_url"),
                    prov.get("urlS3_logo"),
                    1 if prov.get("favorite") else 0,
                    prov.get("products_count") or 0,
                    now,
                    sid,
                ),
            )
            for wh in prov.get("warehouses") or []:
                wid = wh.get("id")
                if not wid:
                    continue
                city = wh.get("city") or {}
                conn.execute(
                    """INSERT INTO warehouses(
                        warehouse_id, supplier_id, warehouse_name, phone, address,
                        city_id, city_name, zip_code, created_at, updated_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(warehouse_id) DO UPDATE SET
                        supplier_id=excluded.supplier_id,
                        warehouse_name=excluded.warehouse_name,
                        phone=excluded.phone,
                        address=excluded.address,
                        city_id=excluded.city_id,
                        city_name=excluded.city_name,
                        zip_code=excluded.zip_code,
                        updated_at=excluded.updated_at""",
                    (
                        int(wid),
                        sid,
                        wh.get("name"),
                        wh.get("phone"),
                        wh.get("address"),
                        city.get("id") if isinstance(city, dict) else None,
                        city.get("name") if isinstance(city, dict) else None,
                        wh.get("zip_code"),
                        now,
                        now,
                    ),
                )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def _metrics(conn: sqlite3.Connection, job_id: int, secret_key: str) -> None:
    token = _login(conn, job_id, secret_key)
    rows = conn.execute(
        "SELECT DISTINCT supplier_id FROM warehouses ORDER BY supplier_id"
    ).fetchall()
    if not rows:
        append_log(conn, job_id, "No hay bodegas físicas para consultar métricas.")
        return
    now = iso(utcnow())
    for row in rows:
        if cancelled(conn, job_id):
            return
        sid = int(row["supplier_id"])
        data = api_dropi.obtener_metricas_proveedor(sid, token)
        if not data:
            append_log(conn, job_id, f"[{sid}] Sin métricas. No se ponen ceros.")
            continue
        conn.execute(
            """UPDATE warehouses SET
                monthly_orders=?,
                monthly_dropshippers=?,
                metrics_observed_at=?,
                metrics_time_assumption=NULL,
                updated_at=?
               WHERE supplier_id=?""",
            (
                int(data.get("total_monthly_orders") or 0),
                int(data.get("total_monthly_dropshippers") or 0),
                now,
                now,
                sid,
            ),
        )
        append_log(
            conn,
            job_id,
            f"[{sid}] Pedidos mensuales del proveedor y dropshippers actualizados. "
            "No se mezclan con el stock.",
        )


class _Cancel:
    def __init__(self, conn: sqlite3.Connection, job_id: int) -> None:
        self.conn = conn
        self.job_id = job_id

    def is_set(self) -> bool:
        return cancelled(self.conn, self.job_id)
