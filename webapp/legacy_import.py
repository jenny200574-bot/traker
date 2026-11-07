"""Importa una base de la app de escritorio sin modificarla ni borrar filas."""
from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime
from pathlib import Path

from webapp.db import backup_db, connect, init_db
from webapp.timeutil import assume_zone, iso, utcnow


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _open_readonly(path: Path) -> sqlite3.Connection:
    uri = f"file:{path.resolve().as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    ).fetchone()
    return row is not None


def _ensure_supplier(conn: sqlite3.Connection, supplier_id: int, now: str) -> None:
    conn.execute(
        """INSERT INTO suppliers(supplier_id, created_at, updated_at)
           VALUES(?, ?, ?)
           ON CONFLICT(supplier_id) DO NOTHING""",
        (supplier_id, now, now),
    )


def import_legacy_db(source: Path, dest: Path, backup_dir: Path, timezone_name: str) -> dict:
    """Copia proveedores, bodegas, productos e historial.

    El archivo de origen se abre en solo lectura. Antes de escribir, si la
    base de destino ya existe, se guarda un respaldo. Reimportar no duplica
    la identidad de proveedor ni las observaciones legado.
    """
    if not source.is_file():
        raise FileNotFoundError(f"No existe la base legado: {source}")
    before = file_sha256(source)
    backup_path = None
    if dest.exists() and dest.stat().st_size:
        stamp = utcnow().strftime("%Y%m%dT%H%M%SZ")
        backup_path = backup_dir / f"antes_importar_{stamp}.db"
        backup_db(dest, backup_path)
    init_db(dest, timezone_name)

    src = _open_readonly(source)
    dst = connect(dest)
    now = iso(utcnow())
    report = {
        "proveedores": 0,
        "fusionados": 0,
        "bodegas_fisicas": 0,
        "productos": 0,
        "observaciones": 0,
        "actividad": 0,
        "respaldo": str(backup_path) if backup_path else None,
        "csv_archivos": "no importados",
        "nota_horas": (
            f"Las fechas legado no traen zona. Se interpretaron como {timezone_name} "
            "y quedaron marcadas. La confianza de esa serie no se inventa."
        ),
    }
    try:
        dst.execute("BEGIN IMMEDIATE")
        if _table_exists(src, "suppliers"):
            for row in src.execute("SELECT * FROM suppliers"):
                _ensure_supplier(dst, int(row["supplier_id"]), now)
                existing = dst.execute(
                    "SELECT source_manual FROM suppliers WHERE supplier_id=?",
                    (row["supplier_id"],),
                ).fetchone()
                if existing and existing["source_manual"]:
                    report["fusionados"] += 1
                dst.execute(
                    """UPDATE suppliers SET
                        store_name=COALESCE(?, store_name),
                        owner_name=COALESCE(?, owner_name),
                        phone=COALESCE(?, phone),
                        store_url=COALESCE(?, store_url),
                        logo_url=COALESCE(?, logo_url),
                        favorite=CASE WHEN ?=1 THEN 1 ELSE favorite END,
                        products_count=COALESCE(?, products_count),
                        source_synced=1,
                        updated_at=?
                       WHERE supplier_id=?""",
                    (
                        row["store_name"],
                        row["owner_name"],
                        row["phone"],
                        row["store_url"],
                        row["logo_url"],
                        1 if row["favorite"] else 0,
                        row["products_count"],
                        now,
                        row["supplier_id"],
                    ),
                )
                report["proveedores"] += 1

        manual_ids: set[int] = set()
        if _table_exists(src, "bodegas"):
            for row in src.execute("SELECT * FROM bodegas"):
                sid = int(row["supplier_id"])
                manual_ids.add(sid)
                _ensure_supplier(dst, sid, now)
                previous = dst.execute(
                    "SELECT source_synced, manual_name FROM suppliers WHERE supplier_id=?",
                    (sid,),
                ).fetchone()
                if previous and previous["source_synced"]:
                    report["fusionados"] += 1
                dst.execute(
                    """UPDATE suppliers SET
                        manual_name=?,
                        manual_url=?,
                        productos_esperados=?,
                        source_manual=1,
                        follow_catalog=1,
                        store_name=COALESCE(store_name, ?),
                        updated_at=?
                       WHERE supplier_id=?""",
                    (
                        row["nombre"],
                        row["url"],
                        row["productos_esperados"] or 0,
                        row["nombre"],
                        now,
                        sid,
                    ),
                )

        followed_suppliers: set[int] = set()
        if _table_exists(src, "warehouses"):
            for row in src.execute("SELECT * FROM warehouses"):
                sid = int(row["supplier_id"])
                _ensure_supplier(dst, sid, now)
                follow = 1 if row["seguimiento"] else 0
                if follow:
                    followed_suppliers.add(sid)
                dst.execute(
                    """INSERT INTO warehouses(
                        warehouse_id, supplier_id, warehouse_name, phone, address,
                        city_id, city_name, zip_code, follow, monthly_orders,
                        monthly_dropshippers, metrics_observed_at, metrics_time_assumption,
                        created_at, updated_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(warehouse_id) DO UPDATE SET
                        supplier_id=excluded.supplier_id,
                        warehouse_name=excluded.warehouse_name,
                        phone=excluded.phone,
                        address=excluded.address,
                        city_id=excluded.city_id,
                        city_name=excluded.city_name,
                        zip_code=excluded.zip_code,
                        follow=MAX(warehouses.follow, excluded.follow),
                        monthly_orders=excluded.monthly_orders,
                        monthly_dropshippers=excluded.monthly_dropshippers,
                        updated_at=excluded.updated_at""",
                    (
                        row["warehouse_id"],
                        sid,
                        row["warehouse_name"],
                        row["phone"],
                        row["address"],
                        row["city_id"],
                        row["city_name"],
                        row["zip_code"],
                        follow,
                        row["ordenes"],
                        row["dropshippers"],
                        None,
                        "desconocida_legado",
                        row["created_at"] or now,
                        now,
                    ),
                )
                report["bodegas_fisicas"] += 1

        if followed_suppliers:
            dst.executemany(
                "UPDATE suppliers SET follow_catalog=1, updated_at=? WHERE supplier_id=?",
                [(now, sid) for sid in followed_suppliers],
            )

        if _table_exists(src, "productos"):
            for row in src.execute("SELECT * FROM productos"):
                sid = int(row["supplier_id"])
                _ensure_supplier(dst, sid, now)
                state = "legado_activo" if row["activo"] else "legado_inactivo"
                seen = _legacy_time(row["ultima_actualizacion"], timezone_name)
                current = dst.execute(
                    """SELECT stock, last_seen_at, catalog_state FROM products
                       WHERE product_id_dropi=? AND supplier_id=?""",
                    (row["product_id_dropi"], sid),
                ).fetchone()
                keep_stock = False
                if current and current["last_seen_at"] and seen and current["catalog_state"] not in {
                    "legado_activo",
                    "legado_inactivo",
                    "desconocido",
                }:
                    if current["last_seen_at"] >= seen:
                        keep_stock = True
                stock = current["stock"] if keep_stock and current else row["stock"]
                last_seen = current["last_seen_at"] if keep_stock and current else seen
                catalog_state = current["catalog_state"] if keep_stock and current else state
                dst.execute(
                    """INSERT INTO products(
                        product_id_dropi, supplier_id, nombre, sku, imagen, precio,
                        precio_oferta, stock, categoria, last_seen_at, catalog_state
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(product_id_dropi, supplier_id) DO UPDATE SET
                        nombre=excluded.nombre,
                        sku=excluded.sku,
                        imagen=excluded.imagen,
                        precio=excluded.precio,
                        precio_oferta=excluded.precio_oferta,
                        stock=excluded.stock,
                        categoria=excluded.categoria,
                        last_seen_at=excluded.last_seen_at,
                        catalog_state=excluded.catalog_state""",
                    (
                        row["product_id_dropi"],
                        sid,
                        row["nombre"],
                        row["sku"],
                        row["imagen"],
                        row["precio"],
                        row["precio_oferta"],
                        stock,
                        row["categoria"],
                        last_seen,
                        catalog_state,
                    ),
                )
                report["productos"] += 1

        if _table_exists(src, "historial_stock"):
            for row in src.execute("SELECT * FROM historial_stock"):
                sid = int(row["supplier_id"])
                _ensure_supplier(dst, sid, now)
                observed = _legacy_time(row["fecha_hora"], timezone_name)
                if observed is None or row["stock"] is None:
                    continue
                cur = dst.execute(
                    """INSERT OR IGNORE INTO stock_observations(
                        product_id_dropi, supplier_id, snapshot_id, stock, observed_at,
                        provenance, completeness, time_assumption
                    ) VALUES(?,?,NULL,?,?,'legacy','unknown',?)""",
                    (
                        row["product_id_dropi"],
                        sid,
                        int(row["stock"]),
                        observed,
                        timezone_name,
                    ),
                )
                report["observaciones"] += cur.rowcount

        if _table_exists(src, "log_actividad"):
            for row in src.execute("SELECT * FROM log_actividad"):
                cur = dst.execute(
                    """INSERT OR IGNORE INTO activity_log(
                        fecha, hora, bodega, productos_descargados, errores, source
                    ) VALUES(?,?,?,?,?,'legacy')""",
                    (
                        row["fecha"],
                        row["hora"],
                        row["bodega"],
                        row["productos_descargados"],
                        row["errores"],
                    ),
                )
                report["actividad"] += cur.rowcount

        dst.execute("COMMIT")
    except Exception:
        dst.execute("ROLLBACK")
        raise
    finally:
        dst.close()
        src.close()

    after = file_sha256(source)
    if before != after:
        raise RuntimeError("La importación modificó el archivo de origen. Se aborta el informe.")
    report["origen_intacto"] = True
    return report


def _legacy_time(value: str | None, timezone_name: str) -> str | None:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    return iso(assume_zone(dt, timezone_name))
