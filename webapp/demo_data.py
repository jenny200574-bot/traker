"""Conjunto sintético aislado. Nunca escribe en la base real."""
from __future__ import annotations

from datetime import timedelta

from webapp.db import connect, init_db
from webapp.timeutil import iso, utcnow

DEMO_MARKER = "demo_seed_version"
DEMO_VERSION = "1"


def seed_demo(path, timezone_name: str = "America/Bogota") -> None:
    init_db(path, timezone_name)
    conn = connect(path)
    try:
        row = conn.execute("SELECT value FROM meta WHERE key=?", (DEMO_MARKER,)).fetchone()
        if row and row["value"] == DEMO_VERSION:
            return
        conn.execute("BEGIN IMMEDIATE")
        _wipe_business(conn)
        now = utcnow()
        created = iso(now)
        conn.execute(
            """INSERT INTO suppliers(
                supplier_id, store_name, owner_name, manual_name, products_count,
                follow_catalog, source_manual, source_synced, created_at, updated_at
            ) VALUES
            (501, 'Demo Andina', 'Propietaria demo', 'Demo Andina', 4, 1, 1, 1, ?, ?),
            (502, 'Demo Pacífico', 'Propietario demo', NULL, 3, 1, 0, 1, ?, ?)""",
            (created, created, created, created),
        )
        conn.execute(
            """INSERT INTO warehouses(
                warehouse_id, supplier_id, warehouse_name, city_name, follow,
                monthly_orders, monthly_dropshippers, metrics_observed_at, created_at, updated_at
            ) VALUES
            (9001, 501, 'Bodega demo Bogotá', 'Bogotá', 1, 40, 6, ?, ?, ?),
            (9002, 501, 'Bodega demo Medellín', 'Medellín', 0, 40, 6, ?, ?, ?),
            (9003, 502, 'Bodega demo Cali', 'Cali', 1, 15, 2, ?, ?, ?)""",
            (created, created, created, created, created, created, created, created, created),
        )
        products = [
            (1, 501, "Demo: lámpara de mesa", "DEM-LAM", "Hogar", 80, 42000),
            (2, 501, "Demo: taza esmaltada", "DEM-TAZ", "Hogar", 30, 18000),
            (3, 501, "Demo: cable corto", "DEM-CAB", "Tecnología", 50, 15000),
            (4, 501, "Demo: agenda única", "DEM-AGE", "Papelería", 12, 22000),
            (5, 502, "Demo: hamaca", "DEM-HAM", "Hogar", 20, 90000),
            (6, 502, "Demo: termo", "DEM-TER", "Hogar", 40, 35000),
            (7, 502, "Demo: muestra sola", "DEM-UNO", "Papelería", 9, 8000),
        ]
        for pid, sid, name, sku, cat, _stock, price in products:
            conn.execute(
                """INSERT INTO products(
                    product_id_dropi, supplier_id, nombre, sku, categoria, precio,
                    stock, catalog_state, last_seen_at
                ) VALUES(?,?,?,?,?,?,?, 'visto', ?)""",
                (pid, sid, name, sku, cat, price, _stock, created),
            )
        series = {
            (501, 1): [80, 76, 74, 70, 66, 61, 58, 52],  # baja estable
            (501, 2): [20, 19, 28, 27, 26, 25, 24, 30],  # reposiciones
            (501, 3): [50, 48, 4, 47, 46, 45, 44, 43],  # ajuste que se revierte
            (501, 4): [12, 11, 10, 9, 8, 3, 3, 3],  # baja grande en un sentido
            (502, 5): [20, 20, 19, 17, 17, 14, 12, 10],
            (502, 6): [40, 39, 41, 38, 36, 36, 33, 31],
            (502, 7): [9],  # un solo punto
        }
        for offset, day in enumerate(range(7, -1, -1)):
            observed = iso(now - timedelta(days=day))
            for (sid, pid), stocks in series.items():
                if offset >= len(stocks):
                    continue
                cur = conn.execute(
                    """INSERT INTO snapshots(
                        supplier_id, started_at, finished_at, status, pages,
                        unique_products, empty_page_reached, coverage_note, provenance
                    ) VALUES(?,?,?,'complete',1,1,1,'Snapshot sintético de demostración.','demo')""",
                    (sid, observed, observed),
                )
                snap = int(cur.lastrowid)
                stock = stocks[offset]
                conn.execute(
                    """INSERT INTO stock_observations(
                        product_id_dropi, supplier_id, snapshot_id, stock, observed_at,
                        provenance, completeness
                    ) VALUES(?,?,?,?,?,'demo','complete')""",
                    (pid, sid, snap, stock, observed),
                )
                if offset == len(stocks) - 1 or (len(stocks) == 1 and offset == 0):
                    conn.execute(
                        """UPDATE products SET stock=?, last_seen_at=?, last_snapshot_id=?
                           WHERE product_id_dropi=? AND supplier_id=?""",
                        (stock, observed, snap, pid, sid),
                    )
        conn.execute(
            """UPDATE suppliers SET
                last_snapshot_status='complete',
                last_snapshot_at=?,
                last_snapshot_id=(
                    SELECT MAX(id) FROM snapshots s WHERE s.supplier_id=suppliers.supplier_id
                )""",
            (created,),
        )
        conn.execute(
            """INSERT INTO jobs(job_type, status, log, progress, created_at, started_at, finished_at)
               VALUES('descargar','success',?,?,?,?,?)""",
            (
                "Semilla de demostración. No consultó Dropi ni usó credenciales.",
                "Datos sintéticos",
                created,
                created,
                created,
            ),
        )
        conn.execute(
            """INSERT INTO product_classification(
                product_id_dropi, supplier_id, classification, shortlist, updated_at
            ) VALUES(1, 501, 'prioridad', 1, ?)""",
            (created,),
        )
        conn.execute("INSERT INTO tags(name) VALUES('demo-hogar')")
        tag_id = conn.execute("SELECT id FROM tags WHERE name='demo-hogar'").fetchone()["id"]
        conn.execute(
            "INSERT INTO product_tags(product_id_dropi, supplier_id, tag_id) VALUES(1,501,?)",
            (tag_id,),
        )
        conn.execute(
            "INSERT INTO watchlists(name, created_at) VALUES('Lista demo', ?)",
            (created,),
        )
        wid = conn.execute("SELECT id FROM watchlists WHERE name='Lista demo'").fetchone()["id"]
        conn.execute(
            """INSERT INTO watchlist_items(watchlist_id, product_id_dropi, supplier_id, added_at)
               VALUES(?,1,501,?)""",
            (wid, created),
        )
        conn.execute(
            """INSERT INTO product_notes(product_id_dropi, supplier_id, body, created_at)
               VALUES(1,501,'Nota de la semilla: revisar la baja estable antes de armar una ficha.',?)""",
            (created,),
        )
        conn.execute(
            """INSERT INTO creative_briefs(
                product_id_dropi, supplier_id, title, audience, promise, objections, notes, status, updated_at
            ) VALUES(1,501,'Ficha demo — lámpara','Hogar que busca luz cálida',
                     'Cabe en una mesa pequeña','El precio frente a otras lámparas',
                     'Borrador sintético. No hay pieza generada.','borrador',?)""",
            (created,),
        )
        conn.execute(
            "INSERT INTO meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (DEMO_MARKER, DEMO_VERSION),
        )
        conn.execute("COMMIT")
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        raise
    finally:
        conn.close()


def _wipe_business(conn) -> None:
    for table in (
        "watchlist_items",
        "watchlists",
        "product_tags",
        "tags",
        "product_notes",
        "product_classification",
        "creative_briefs",
        "stock_observations",
        "snapshots",
        "products",
        "warehouses",
        "suppliers",
        "jobs",
        "activity_log",
    ):
        conn.execute(f"DELETE FROM {table}")
