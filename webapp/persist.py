"""Guarda snapshots y observaciones. Nunca convierte una ausencia en stock cero."""
from __future__ import annotations

import sqlite3

from webapp.catalog_fetch import CatalogFetch
from webapp.timeutil import iso, utcnow


def persist_catalog(
    conn: sqlite3.Connection,
    supplier_id: int,
    job_id: int | None,
    fetched: CatalogFetch,
    *,
    started_at: str | None = None,
) -> int:
    now = iso(utcnow())
    started = started_at or now
    note = " ".join(fetched.notes).strip() or None
    completeness = "complete" if fetched.status == "complete" else "partial"
    conn.execute("BEGIN IMMEDIATE")
    try:
        cur = conn.execute(
            """INSERT INTO snapshots(
                supplier_id, job_id, started_at, finished_at, status, pages,
                page_errors, unique_products, expected_products, empty_page_reached,
                duplicates, coverage_note, provenance
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,'download')""",
            (
                supplier_id,
                job_id,
                started,
                now,
                fetched.status,
                fetched.pages,
                fetched.page_errors,
                len(fetched.products),
                fetched.expected,
                1 if fetched.empty_page_reached else 0,
                fetched.duplicates,
                note,
            ),
        )
        snapshot_id = int(cur.lastrowid)
        conn.execute(
            """UPDATE suppliers SET last_snapshot_id=?, last_snapshot_status=?,
               last_snapshot_at=?, updated_at=? WHERE supplier_id=?""",
            (snapshot_id, fetched.status, now, now, supplier_id),
        )
        if fetched.status == "failed" or not fetched.products:
            conn.execute("COMMIT")
            return snapshot_id

        seen: list[int] = []
        for product in fetched.products.values():
            seen.append(product.product_id_dropi)
            conn.execute(
                """INSERT INTO products(
                    product_id_dropi, supplier_id, nombre, sku, imagen, precio,
                    precio_oferta, stock, categoria, last_seen_at, last_snapshot_id,
                    catalog_state
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?, 'visto')
                ON CONFLICT(product_id_dropi, supplier_id) DO UPDATE SET
                    nombre=excluded.nombre,
                    sku=excluded.sku,
                    imagen=excluded.imagen,
                    precio=excluded.precio,
                    precio_oferta=excluded.precio_oferta,
                    stock=excluded.stock,
                    categoria=excluded.categoria,
                    last_seen_at=excluded.last_seen_at,
                    last_snapshot_id=excluded.last_snapshot_id,
                    catalog_state='visto'""",
                (
                    product.product_id_dropi,
                    supplier_id,
                    product.nombre,
                    product.sku,
                    product.imagen,
                    product.precio,
                    product.precio_oferta,
                    product.stock,
                    product.categoria,
                    now,
                    snapshot_id,
                ),
            )
            conn.execute(
                """INSERT OR REPLACE INTO stock_observations(
                    product_id_dropi, supplier_id, snapshot_id, stock, observed_at,
                    provenance, completeness, duplicate_in_snapshot, time_assumption
                ) VALUES(?,?,?,?,?,'download',?,0,NULL)""",
                (
                    product.product_id_dropi,
                    supplier_id,
                    snapshot_id,
                    int(product.stock or 0),
                    now,
                    completeness,
                ),
            )

        if fetched.status == "complete" and seen:
            marks = ",".join("?" * len(seen))
            conn.execute(
                f"""UPDATE products SET catalog_state='ausente_en_snapshot_completo'
                    WHERE supplier_id=?
                      AND product_id_dropi NOT IN ({marks})
                      AND catalog_state!='ausente_en_snapshot_completo'""",
                [supplier_id, *seen],
            )
        conn.execute("COMMIT")
        return snapshot_id
    except Exception:
        conn.execute("ROLLBACK")
        raise
