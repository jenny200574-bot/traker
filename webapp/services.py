"""Consultas y cambios que usan las páginas y la API."""
from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from datetime import datetime

from webapp.db import put_setting, setting
from webapp.signals import (
    DEFAULT_THRESHOLDS,
    ObservationPoint,
    category_baseline,
    in_window,
    movement,
)
from webapp.timeutil import format_local, iso, parse_iso, utcnow, window_bounds, zone_or_default

CLASSIFICATIONS = {
    "sin_clasificar": "Sin clasificar",
    "interes": "Interés",
    "revisar": "Revisar",
    "descartado": "Descartado",
    "prioridad": "Prioridad",
}
WINDOWS = (24, 72, 168, 720)
TREND_CAP = 2000


def supplier_label(row: sqlite3.Row | dict) -> str:
    data = row if isinstance(row, dict) else dict(row)
    return (
        (data.get("store_name") or "").strip()
        or (data.get("manual_name") or "").strip()
        or f"Proveedor {data.get('supplier_id')}"
    )


def thresholds_from(conn: sqlite3.Connection) -> dict:
    raw = setting(conn, "thresholds")
    data = dict(DEFAULT_THRESHOLDS)
    if raw:
        try:
            loaded = json.loads(raw)
        except json.JSONDecodeError:
            loaded = {}
        for key, default in DEFAULT_THRESHOLDS.items():
            if key in loaded:
                data[key] = loaded[key]
    return data


def save_thresholds(conn: sqlite3.Connection, incoming: dict) -> dict:
    current = thresholds_from(conn)
    for key, default in DEFAULT_THRESHOLDS.items():
        if key not in incoming:
            continue
        value = incoming[key]
        if isinstance(default, int):
            current[key] = int(value)
        else:
            current[key] = float(value)
    put_setting(conn, "thresholds", json.dumps(current))
    return current


def timezone_name(conn: sqlite3.Connection, fallback: str = "America/Bogota") -> str:
    name = setting(conn, "timezone", fallback) or fallback
    zone_or_default(name, fallback)
    return name


def _points(conn: sqlite3.Connection, supplier_id: int, product_id: int) -> list[ObservationPoint]:
    rows = conn.execute(
        """SELECT stock, observed_at, completeness, provenance
           FROM stock_observations
           WHERE supplier_id=? AND product_id_dropi=?
           ORDER BY observed_at, id""",
        (supplier_id, product_id),
    ).fetchall()
    points = []
    for row in rows:
        points.append(
            ObservationPoint(
                observed_at=parse_iso(row["observed_at"]),
                stock=int(row["stock"]),
                completeness=row["completeness"],
                provenance=row["provenance"],
            )
        )
    return points


def _movement_for(points: list[ObservationPoint], start: datetime, end: datetime, rules: dict):
    selected = in_window(points, start, end)
    older = sum(1 for point in points if point.observed_at < start)
    result = movement(
        selected,
        gap_hours=float(rules["gap_hours"]),
        suspicious_drop_pct=float(rules["suspicious_drop_pct"]),
        suspicious_drop_abs=int(rules["suspicious_drop_abs"]),
        suspicious_restore_ratio=float(rules["suspicious_restore_ratio"]),
    )
    if older:
        result.motivos.append(
            "Hay observaciones anteriores fuera de la ventana. No se usan para fabricar la baja."
        )
    return result


def list_products(conn: sqlite3.Connection, filters: dict, rules: dict, zone: str) -> dict:
    hours = int(filters.get("horas") or 72)
    start, end = window_bounds(hours)
    where = ["1=1"]
    params: list = []
    query = (filters.get("q") or "").strip().lower()
    if query:
        where.append(
            "(lower(COALESCE(p.nombre,'')) LIKE ? OR lower(COALESCE(p.sku,'')) LIKE ? OR CAST(p.product_id_dropi AS TEXT) LIKE ?)"
        )
        like = f"%{query}%"
        params.extend([like, like, like])
    if filters.get("proveedor"):
        where.append("p.supplier_id=?")
        params.append(int(filters["proveedor"]))
    if filters.get("categoria"):
        where.append("lower(COALESCE(p.categoria,'')) LIKE ?")
        params.append(f"%{filters['categoria'].strip().lower()}%")
    if filters.get("clasificacion"):
        if filters["clasificacion"] == "sin_clasificar":
            where.append("(c.classification IS NULL OR c.classification='sin_clasificar')")
        else:
            where.append("c.classification=?")
            params.append(filters["clasificacion"])
    if filters.get("shortlist") == "1":
        where.append("COALESCE(c.shortlist,0)=1")
    if filters.get("seguimiento") == "1":
        where.append(
            """(s.follow_catalog=1 OR s.supplier_id IN
               (SELECT supplier_id FROM warehouses WHERE follow=1))"""
        )
    page = max(int(filters.get("pagina") or 1), 1)
    per_page = 40
    sql_from = f"""
        FROM products p
        JOIN suppliers s ON s.supplier_id=p.supplier_id
        LEFT JOIN product_classification c
          ON c.product_id_dropi=p.product_id_dropi AND c.supplier_id=p.supplier_id
        WHERE {' AND '.join(where)}
    """
    total = conn.execute(f"SELECT COUNT(*) AS n {sql_from}", params).fetchone()["n"]
    order = filters.get("orden") or "nombre"
    order_sql = {
        "nombre": "p.nombre COLLATE NOCASE",
        "stock": "COALESCE(p.stock, -1) DESC",
        "reciente": "COALESCE(p.last_seen_at,'') DESC",
    }.get(order, "p.nombre COLLATE NOCASE")
    rows = conn.execute(
        f"""SELECT p.*, s.store_name, s.manual_name, s.follow_catalog,
                   COALESCE(c.classification,'sin_clasificar') AS classification,
                   COALESCE(c.shortlist,0) AS shortlist
            {sql_from}
            ORDER BY {order_sql}
            LIMIT ? OFFSET ?""",
        [*params, per_page, (page - 1) * per_page],
    ).fetchall()
    items = []
    for row in rows:
        points = _points(conn, row["supplier_id"], row["product_id_dropi"])
        mov = _movement_for(points, start, end, rules)
        items.append(_product_card(row, mov, zone))
    return {
        "items": items,
        "total": total,
        "pagina": page,
        "paginas": max((total + per_page - 1) // per_page, 1),
        "horas": hours,
    }


def _product_card(row: sqlite3.Row, mov, zone: str) -> dict:
    data = dict(row)
    data["proveedor"] = supplier_label(row)
    data["clasificacion_etiqueta"] = CLASSIFICATIONS.get(data.get("classification"), "Sin clasificar")
    data["movimiento"] = mov.as_dict()
    data["visto"] = format_local(data.get("last_seen_at"), zone)
    return data


def product_detail(conn: sqlite3.Connection, supplier_id: int, product_id: int, hours: int, rules: dict, zone: str) -> dict | None:
    row = conn.execute(
        """SELECT p.*, s.store_name, s.manual_name, s.follow_catalog, s.manual_url, s.store_url,
                  COALESCE(c.classification,'sin_clasificar') AS classification,
                  COALESCE(c.shortlist,0) AS shortlist
           FROM products p
           JOIN suppliers s ON s.supplier_id=p.supplier_id
           LEFT JOIN product_classification c
             ON c.product_id_dropi=p.product_id_dropi AND c.supplier_id=p.supplier_id
           WHERE p.supplier_id=? AND p.product_id_dropi=?""",
        (supplier_id, product_id),
    ).fetchone()
    if row is None:
        return None
    points = _points(conn, supplier_id, product_id)
    start, end = window_bounds(hours)
    mov = _movement_for(points, start, end, rules)
    observations = conn.execute(
        """SELECT o.*, sn.status AS snapshot_status, sn.coverage_note
           FROM stock_observations o
           LEFT JOIN snapshots sn ON sn.id=o.snapshot_id
           WHERE o.supplier_id=? AND o.product_id_dropi=?
           ORDER BY o.observed_at, o.id""",
        (supplier_id, product_id),
    ).fetchall()
    series = []
    previous = None
    for obs in observations:
        stock = int(obs["stock"])
        delta = None if previous is None else stock - previous
        series.append(
            {
                "fecha": format_local(obs["observed_at"], zone),
                "stock": stock,
                "delta": delta,
                "provenance": obs["provenance"],
                "completeness": obs["completeness"],
                "snapshot_status": obs["snapshot_status"],
                "nota": obs["coverage_note"],
                "time_assumption": obs["time_assumption"],
            }
        )
        previous = stock
    tags = conn.execute(
        """SELECT t.id, t.name FROM tags t
           JOIN product_tags pt ON pt.tag_id=t.id
           WHERE pt.supplier_id=? AND pt.product_id_dropi=?
           ORDER BY t.name""",
        (supplier_id, product_id),
    ).fetchall()
    notes = conn.execute(
        """SELECT id, body, created_at FROM product_notes
           WHERE supplier_id=? AND product_id_dropi=? ORDER BY id DESC""",
        (supplier_id, product_id),
    ).fetchall()
    lists = conn.execute(
        """SELECT w.id, w.name FROM watchlists w
           JOIN watchlist_items i ON i.watchlist_id=w.id
           WHERE i.supplier_id=? AND i.product_id_dropi=?""",
        (supplier_id, product_id),
    ).fetchall()
    briefs = conn.execute(
        """SELECT id, title, status FROM creative_briefs
           WHERE supplier_id=? AND product_id_dropi=? ORDER BY id DESC""",
        (supplier_id, product_id),
    ).fetchall()
    card = _product_card(row, mov, zone)
    card.update(
        {
            "serie": series,
            "svg": sparkline([(parse_iso(o["observed_at"]), int(o["stock"])) for o in observations]),
            "etiquetas": [dict(t) for t in tags],
            "notas": [
                {"id": n["id"], "body": n["body"], "fecha": format_local(n["created_at"], zone)}
                for n in notes
            ],
            "listas": [dict(item) for item in lists],
            "fichas": [dict(item) for item in briefs],
            "horas": hours,
            "archivo_csv": (
                "Los CSV de backups_historial no se importan. "
                "La serie muestra solo observaciones guardadas en la base."
            ),
        }
    )
    return card


def sparkline(points: list[tuple[datetime, int]]) -> str:
    if len(points) < 2:
        return ""
    ordered = sorted(points, key=lambda item: item[0])
    values = [item[1] for item in ordered]
    low, high = min(values), max(values)
    span = max(high - low, 1)
    width, height = 320, 72
    coords = []
    for index, value in enumerate(values):
        x = 6 + (width - 12) * (index / (len(values) - 1))
        y = 8 + (height - 16) * (1 - ((value - low) / span))
        coords.append(f"{x:.1f},{y:.1f}")
    return (
        f'<svg viewBox="0 0 {width} {height}" class="spark" role="img" aria-label="Serie de stock">'
        f'<polyline fill="none" stroke="currentColor" stroke-width="2" points="{" ".join(coords)}"/>'
        f"</svg>"
    )


def set_classification(conn, supplier_id: int, product_id: int, classification: str | None, shortlist: int | None) -> None:
    current = conn.execute(
        """SELECT classification, shortlist FROM product_classification
           WHERE supplier_id=? AND product_id_dropi=?""",
        (supplier_id, product_id),
    ).fetchone()
    kind = classification or (current["classification"] if current else "sin_clasificar")
    if kind not in CLASSIFICATIONS:
        raise ValueError("Clasificación desconocida.")
    flag = int(current["shortlist"]) if current and shortlist is None else int(shortlist or 0)
    if shortlist is not None:
        flag = 1 if int(shortlist) else 0
    now = iso(utcnow())
    conn.execute(
        """INSERT INTO product_classification(
            product_id_dropi, supplier_id, classification, shortlist, updated_at
        ) VALUES(?,?,?,?,?)
        ON CONFLICT(product_id_dropi, supplier_id) DO UPDATE SET
            classification=excluded.classification,
            shortlist=excluded.shortlist,
            updated_at=excluded.updated_at""",
        (product_id, supplier_id, kind, flag, now),
    )


def add_note(conn, supplier_id: int, product_id: int, body: str) -> None:
    text = body.strip()
    if not text:
        raise ValueError("La nota está vacía.")
    conn.execute(
        """INSERT INTO product_notes(product_id_dropi, supplier_id, body, created_at)
           VALUES(?,?,?,?)""",
        (product_id, supplier_id, text, iso(utcnow())),
    )


def add_tag(conn, supplier_id: int, product_id: int, name: str) -> None:
    clean = " ".join(name.strip().split())
    if not clean:
        raise ValueError("Etiqueta vacía.")
    conn.execute("INSERT INTO tags(name) VALUES(?) ON CONFLICT(name) DO NOTHING", (clean,))
    tag_id = conn.execute("SELECT id FROM tags WHERE name=?", (clean,)).fetchone()["id"]
    conn.execute(
        """INSERT OR IGNORE INTO product_tags(product_id_dropi, supplier_id, tag_id)
           VALUES(?,?,?)""",
        (product_id, supplier_id, tag_id),
    )


def remove_tag(conn, supplier_id: int, product_id: int, tag_id: int) -> None:
    conn.execute(
        """DELETE FROM product_tags
           WHERE supplier_id=? AND product_id_dropi=? AND tag_id=?""",
        (supplier_id, product_id, tag_id),
    )


def create_watchlist(conn, name: str) -> int:
    clean = " ".join(name.strip().split())
    if not clean:
        raise ValueError("La lista necesita un nombre.")
    cur = conn.execute(
        "INSERT INTO watchlists(name, created_at) VALUES(?, ?)",
        (clean, iso(utcnow())),
    )
    return int(cur.lastrowid)


def add_to_watchlist(conn, watchlist_id: int, supplier_id: int, product_id: int) -> None:
    conn.execute(
        """INSERT OR IGNORE INTO watchlist_items(
            watchlist_id, product_id_dropi, supplier_id, added_at
        ) VALUES(?,?,?,?)""",
        (watchlist_id, product_id, supplier_id, iso(utcnow())),
    )


def remove_from_watchlist(conn, watchlist_id: int, supplier_id: int, product_id: int) -> None:
    conn.execute(
        """DELETE FROM watchlist_items
           WHERE watchlist_id=? AND supplier_id=? AND product_id_dropi=?""",
        (watchlist_id, supplier_id, product_id),
    )


def seguimiento(conn: sqlite3.Connection, zone: str) -> dict:
    lists = []
    for watch in conn.execute("SELECT * FROM watchlists ORDER BY name"):
        items = conn.execute(
            """SELECT i.supplier_id, i.product_id_dropi, p.nombre, s.store_name, s.manual_name
               FROM watchlist_items i
               JOIN products p ON p.supplier_id=i.supplier_id AND p.product_id_dropi=i.product_id_dropi
               JOIN suppliers s ON s.supplier_id=i.supplier_id
               WHERE i.watchlist_id=?
               ORDER BY p.nombre""",
            (watch["id"],),
        ).fetchall()
        lists.append(
            {
                "id": watch["id"],
                "name": watch["name"],
                "items": [
                    {
                        "supplier_id": item["supplier_id"],
                        "product_id_dropi": item["product_id_dropi"],
                        "nombre": item["nombre"],
                        "proveedor": supplier_label(item),
                    }
                    for item in items
                ],
            }
        )
    shortlist = conn.execute(
        """SELECT p.product_id_dropi, p.supplier_id, p.nombre, p.sku,
                  s.store_name, s.manual_name, c.classification
           FROM product_classification c
           JOIN products p ON p.supplier_id=c.supplier_id AND p.product_id_dropi=c.product_id_dropi
           JOIN suppliers s ON s.supplier_id=p.supplier_id
           WHERE c.shortlist=1
           ORDER BY p.nombre"""
    ).fetchall()
    notes = conn.execute(
        """SELECT n.body, n.created_at, n.supplier_id, n.product_id_dropi, p.nombre
           FROM product_notes n
           JOIN products p ON p.supplier_id=n.supplier_id AND p.product_id_dropi=n.product_id_dropi
           ORDER BY n.id DESC LIMIT 30"""
    ).fetchall()
    tags = conn.execute(
        """SELECT t.id, t.name, COUNT(pt.product_id_dropi) AS usos
           FROM tags t LEFT JOIN product_tags pt ON pt.tag_id=t.id
           GROUP BY t.id ORDER BY t.name"""
    ).fetchall()
    counts = conn.execute(
        """SELECT COALESCE(c.classification,'sin_clasificar') AS kind, COUNT(*) AS n
           FROM products p
           LEFT JOIN product_classification c
             ON c.supplier_id=p.supplier_id AND c.product_id_dropi=p.product_id_dropi
           GROUP BY kind"""
    ).fetchall()
    return {
        "listas": lists,
        "shortlist": [
            {
                "supplier_id": row["supplier_id"],
                "product_id_dropi": row["product_id_dropi"],
                "nombre": row["nombre"],
                "sku": row["sku"],
                "proveedor": supplier_label(row),
                "clasificacion": CLASSIFICATIONS.get(row["classification"], "Sin clasificar"),
            }
            for row in shortlist
        ],
        "notas": [
            {
                "body": row["body"],
                "fecha": format_local(row["created_at"], zone),
                "nombre": row["nombre"],
                "supplier_id": row["supplier_id"],
                "product_id_dropi": row["product_id_dropi"],
            }
            for row in notes
        ],
        "etiquetas": [dict(row) for row in tags],
        "clases": [
            {"clave": row["kind"], "etiqueta": CLASSIFICATIONS.get(row["kind"], row["kind"]), "n": row["n"]}
            for row in counts
        ],
    }


def tendencias(conn: sqlite3.Connection, hours: int, rules: dict, zone: str) -> dict:
    start, end = window_bounds(hours)
    rows = conn.execute(
        """SELECT p.*, s.store_name, s.manual_name
           FROM products p JOIN suppliers s ON s.supplier_id=p.supplier_id
           ORDER BY p.supplier_id, p.nombre"""
    ).fetchall()
    truncated = len(rows) > TREND_CAP
    items = []
    grouped: dict[str, list] = defaultdict(list)
    for row in rows[:TREND_CAP]:
        points = _points(conn, row["supplier_id"], row["product_id_dropi"])
        mov = _movement_for(points, start, end, rules)
        grouped[row["categoria"] or "Sin categoría"].append(mov)
        if mov.confianza == "insuficiente" and mov.unidades_baja == 0:
            continue
        card = _product_card(row, mov, zone)
        card["categoria"] = row["categoria"] or "Sin categoría"
        items.append(card)
    items.sort(key=lambda item: (item["movimiento"]["confianza"] == "insuficiente", -item["movimiento"]["unidades_baja"]))
    baselines = []
    minimum = int(rules["min_baseline_products"])
    for name, movements in sorted(grouped.items()):
        base = category_baseline(movements, minimum)
        base["categoria"] = name
        baselines.append(base)
    return {
        "horas": hours,
        "items": items,
        "lineas_base": baselines,
        "truncado": truncated,
        "aviso": (
            "Cada baja es un proxy de demanda por stock observado. "
            "No es una venta confirmada ni viralidad. "
            "Los pedidos mensuales del proveedor no entran en esta cuenta."
        ),
    }


def bodegas(conn: sqlite3.Connection, rules: dict, zone: str, hours: int = 168) -> dict:
    start, end = window_bounds(hours)
    suppliers = conn.execute("SELECT * FROM suppliers ORDER BY COALESCE(store_name, manual_name, supplier_id)").fetchall()
    items = []
    for supplier in suppliers:
        warehouses = conn.execute(
            "SELECT * FROM warehouses WHERE supplier_id=? ORDER BY warehouse_name",
            (supplier["supplier_id"],),
        ).fetchall()
        products = conn.execute(
            "SELECT product_id_dropi FROM products WHERE supplier_id=?",
            (supplier["supplier_id"],),
        ).fetchall()
        gross_drop = 0
        gross_restock = 0
        unknown = 0
        usable = 0
        for product in products:
            points = _points(conn, supplier["supplier_id"], product["product_id_dropi"])
            mov = _movement_for(points, start, end, rules)
            if mov.confianza == "insuficiente":
                continue
            usable += 1
            gross_drop += mov.unidades_baja
            gross_restock += mov.reposiciones
            if mov.confianza == "desconocida":
                unknown += 1
        snap = None
        if supplier["last_snapshot_id"]:
            snap = conn.execute(
                "SELECT * FROM snapshots WHERE id=?",
                (supplier["last_snapshot_id"],),
            ).fetchone()
        if snap is None:
            cobertura = "desconocida"
            cobertura_nota = "No hay un snapshot de esta versión. El historial legado no declara cobertura."
        else:
            cobertura = snap["status"]
            cobertura_nota = snap["coverage_note"] or ""
        monthly = None
        dropshippers = None
        metrics_at = None
        assumption = None
        if warehouses:
            monthly = warehouses[0]["monthly_orders"]
            dropshippers = warehouses[0]["monthly_dropshippers"]
            metrics_at = warehouses[0]["metrics_observed_at"]
            assumption = warehouses[0]["metrics_time_assumption"]
        items.append(
            {
                "supplier_id": supplier["supplier_id"],
                "nombre": supplier_label(supplier),
                "manual_name": supplier["manual_name"],
                "store_name": supplier["store_name"],
                "manual_url": supplier["manual_url"],
                "store_url": supplier["store_url"],
                "follow_catalog": supplier["follow_catalog"],
                "source_manual": supplier["source_manual"],
                "source_synced": supplier["source_synced"],
                "productos_esperados": supplier["productos_esperados"],
                "products_count": supplier["products_count"],
                "notas": supplier["notes"] or "",
                "baja_bruta": gross_drop,
                "reposiciones": gross_restock,
                "productos_con_serie": usable,
                "series_desconocidas": unknown,
                "cobertura": cobertura,
                "cobertura_nota": cobertura_nota,
                "snapshot_en": format_local(supplier["last_snapshot_at"], zone),
                "pedidos_mensuales": monthly,
                "dropshippers_mensuales": dropshippers,
                "metricas_en": format_local(metrics_at, zone) if metrics_at else "fecha desconocida",
                "metricas_supuesto": assumption,
                "bodegas": [
                    {
                        "warehouse_id": wh["warehouse_id"],
                        "nombre": wh["warehouse_name"] or "Bodega sin nombre",
                        "ciudad": wh["city_name"] or "",
                        "direccion": wh["address"] or "",
                        "telefono": wh["phone"] or "",
                        "follow": wh["follow"],
                    }
                    for wh in warehouses
                ],
            }
        )
    return {
        "items": items,
        "horas": hours,
        "nota_stock": (
            "El stock descargado pertenece al catálogo del proveedor. "
            "No se reparte entre bodegas físicas y no se suma a los pedidos mensuales."
        ),
    }


def set_supplier_follow(conn, supplier_id: int, follow: bool) -> None:
    conn.execute(
        "UPDATE suppliers SET follow_catalog=?, updated_at=? WHERE supplier_id=?",
        (1 if follow else 0, iso(utcnow()), supplier_id),
    )


def set_warehouse_follow(conn, warehouse_id: int, follow: bool) -> None:
    row = conn.execute(
        "SELECT supplier_id FROM warehouses WHERE warehouse_id=?",
        (warehouse_id,),
    ).fetchone()
    if row is None:
        raise ValueError("Bodega física desconocida.")
    conn.execute(
        "UPDATE warehouses SET follow=?, updated_at=? WHERE warehouse_id=?",
        (1 if follow else 0, iso(utcnow()), warehouse_id),
    )
    if follow:
        conn.execute(
            "UPDATE suppliers SET follow_catalog=1, updated_at=? WHERE supplier_id=?",
            (iso(utcnow()), row["supplier_id"]),
        )


def add_manual_supplier(conn, nombre: str, url: str, supplier_id: int | None, esperados: int) -> int:
    from api_dropi import extraer_supplier_id

    sid = supplier_id or (extraer_supplier_id(url) if url else None)
    if not sid or int(sid) <= 0:
        raise ValueError("Indica el identificador del proveedor o una URL que lo contenga.")
    sid = int(sid)
    now = iso(utcnow())
    conn.execute(
        """INSERT INTO suppliers(supplier_id, manual_name, manual_url, store_name,
               productos_esperados, follow_catalog, source_manual, created_at, updated_at)
           VALUES(?,?,?,?,?,1,1,?,?)
           ON CONFLICT(supplier_id) DO UPDATE SET
               manual_name=excluded.manual_name,
               manual_url=COALESCE(excluded.manual_url, suppliers.manual_url),
               productos_esperados=excluded.productos_esperados,
               follow_catalog=1,
               source_manual=1,
               store_name=COALESCE(suppliers.store_name, excluded.store_name),
               updated_at=excluded.updated_at""",
        (sid, nombre.strip(), url.strip(), nombre.strip(), esperados, now, now),
    )
    return sid


def resumen(conn: sqlite3.Connection, rules: dict, zone: str) -> dict:
    products = conn.execute("SELECT COUNT(*) AS n FROM products").fetchone()["n"]
    suppliers = conn.execute("SELECT COUNT(*) AS n FROM suppliers").fetchone()["n"]
    followed = conn.execute(
        """SELECT COUNT(*) AS n FROM suppliers
           WHERE follow_catalog=1
              OR supplier_id IN (SELECT supplier_id FROM warehouses WHERE follow=1)"""
    ).fetchone()["n"]
    shortlist = conn.execute(
        "SELECT COUNT(*) AS n FROM product_classification WHERE shortlist=1"
    ).fetchone()["n"]
    legacy_obs = conn.execute(
        "SELECT COUNT(*) AS n FROM stock_observations WHERE provenance='legacy'"
    ).fetchone()["n"]
    observations = conn.execute("SELECT COUNT(*) AS n FROM stock_observations").fetchone()["n"]
    last_job = conn.execute("SELECT * FROM jobs ORDER BY id DESC LIMIT 1").fetchone()
    running = conn.execute(
        "SELECT COUNT(*) AS n FROM jobs WHERE status IN ('queued','running')"
    ).fetchone()["n"]
    last_complete = conn.execute(
        """SELECT finished_at FROM snapshots WHERE status='complete'
           ORDER BY finished_at DESC LIMIT 1"""
    ).fetchone()
    stale = False
    stale_text = "Todavía no hay un snapshot completo con cobertura conocida."
    if last_complete and last_complete["finished_at"]:
        age = utcnow() - parse_iso(last_complete["finished_at"])
        stale_hours = float(rules["stale_hours"])
        stale = age.total_seconds() > stale_hours * 3600
        stale_text = (
            f"Último snapshot completo: {format_local(last_complete['finished_at'], zone)}. "
            + ("La serie está vieja según el umbral no validado." if stale else "Dentro del umbral de frescura, que no está validado.")
        )
    proxies = []
    if products:
        trend = tendencias(conn, 72, rules, zone)
        proxies = [
            item for item in trend["items"]
            if item["movimiento"]["unidades_baja"] > 0
        ][:6]
    metrics = conn.execute(
        """SELECT s.supplier_id, s.store_name, s.manual_name,
                  MAX(w.monthly_orders) AS pedidos,
                  MAX(w.monthly_dropshippers) AS dropshippers
           FROM suppliers s
           JOIN warehouses w ON w.supplier_id=s.supplier_id
           WHERE w.monthly_orders IS NOT NULL
           GROUP BY s.supplier_id
           ORDER BY pedidos DESC"""
    ).fetchall()
    return {
        "vacio": products == 0,
        "productos": products,
        "proveedores": suppliers,
        "en_seguimiento": followed,
        "shortlist": shortlist,
        "observaciones": observations,
        "observaciones_legado": legacy_obs,
        "trabajos_abiertos": running,
        "ultimo_trabajo": _job_public(last_job, zone) if last_job else None,
        "proxies": proxies,
        "metricas": [
            {
                "supplier_id": row["supplier_id"],
                "nombre": supplier_label(row),
                "pedidos_mensuales": row["pedidos"],
                "dropshippers_mensuales": row["dropshippers"],
            }
            for row in metrics
        ],
        "stale": stale,
        "stale_text": stale_text,
        "nota_vacio": (
            "No hay productos en esta base. Los conteos en cero son reales: "
            "no son indicadores estimados. Puedes activar los datos de demostración, "
            "importar una base legado o configurar Dropi y encolar una descarga."
        ),
        "nota_proxy": (
            "Las bajas son un proxy de demanda por stock observado en 72 h. "
            "No son ventas confirmadas. Los pedidos mensuales van aparte."
        ),
    }


def _job_public(row: sqlite3.Row | None, zone: str) -> dict | None:
    if row is None:
        return None
    log = row["log"] or ""
    lines = log.splitlines()
    return {
        "id": row["id"],
        "job_type": row["job_type"],
        "status": row["status"],
        "progress": row["progress"] or "",
        "error": row["error"] or "",
        "creado": format_local(row["created_at"], zone),
        "inicio": format_local(row["started_at"], zone),
        "fin": format_local(row["finished_at"], zone),
        "log_tail": "\n".join(lines[-40:]),
    }


def jobs_public(conn: sqlite3.Connection, zone: str) -> list[dict]:
    return [_job_public(row, zone) for row in conn.execute("SELECT * FROM jobs ORDER BY id DESC LIMIT 40")]


def save_brief(conn, data: dict, brief_id: int | None = None) -> int:
    now = iso(utcnow())
    title = (data.get("title") or "").strip()
    if not title:
        raise ValueError("La ficha necesita un título.")
    status = data.get("status") or "borrador"
    if status not in {"borrador", "en_revision", "listo"}:
        raise ValueError("Estado de ficha desconocido.")
    fields = (
        data.get("product_id_dropi"),
        data.get("supplier_id"),
        title,
        (data.get("audience") or "").strip(),
        (data.get("promise") or "").strip(),
        (data.get("objections") or "").strip(),
        (data.get("notes") or "").strip(),
        status,
        now,
    )
    if brief_id:
        conn.execute(
            """UPDATE creative_briefs SET
                product_id_dropi=?, supplier_id=?, title=?, audience=?, promise=?,
                objections=?, notes=?, status=?, updated_at=?
               WHERE id=?""",
            (*fields, brief_id),
        )
        return brief_id
    cur = conn.execute(
        """INSERT INTO creative_briefs(
            product_id_dropi, supplier_id, title, audience, promise, objections,
            notes, status, updated_at
        ) VALUES(?,?,?,?,?,?,?,?,?)""",
        fields,
    )
    return int(cur.lastrowid)


def list_briefs(conn, zone: str) -> list[dict]:
    rows = conn.execute(
        """SELECT b.*, p.nombre AS producto
           FROM creative_briefs b
           LEFT JOIN products p
             ON p.supplier_id=b.supplier_id AND p.product_id_dropi=b.product_id_dropi
           ORDER BY b.updated_at DESC"""
    ).fetchall()
    items = []
    for row in rows:
        item = dict(row)
        item["actualizado"] = format_local(row["updated_at"], zone)
        items.append(item)
    return items


def get_brief(conn, brief_id: int, zone: str) -> dict | None:
    row = conn.execute("SELECT * FROM creative_briefs WHERE id=?", (brief_id,)).fetchone()
    if row is None:
        return None
    item = dict(row)
    item["actualizado"] = format_local(row["updated_at"], zone)
    return item


def schedules_public(conn) -> list[dict]:
    rows = conn.execute("SELECT * FROM schedules ORDER BY hour, minute").fetchall()
    return [dict(row) for row in rows]


def save_schedule(conn, hour: int, minute: int, enabled: bool = True, job_type: str = "descargar") -> None:
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError("Hora inválida.")
    if job_type not in {"descargar", "sincronizar", "metricas"}:
        raise ValueError("Ese trabajo no se programa.")
    conn.execute(
        """INSERT INTO schedules(hour, minute, enabled, job_type)
           VALUES(?,?,?,?)
           ON CONFLICT(hour, minute, job_type) DO UPDATE SET enabled=excluded.enabled""",
        (hour, minute, 1 if enabled else 0, job_type),
    )


def delete_schedule(conn, schedule_id: int) -> None:
    conn.execute("DELETE FROM schedules WHERE id=?", (schedule_id,))
