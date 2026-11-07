"""Excel descargado por el navegador. No abre archivos en el sistema."""
from __future__ import annotations

import sqlite3
from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Font

from webapp.services import bodegas, tendencias


def _book(sheets: list[tuple[str, list[str], list[list]]]) -> bytes:
    wb = Workbook()
    first = True
    for title, headers, rows in sheets:
        ws = wb.active if first else wb.create_sheet()
        first = False
        ws.title = title[:31]
        ws.append(headers)
        for row in rows:
            ws.append(row)
        for cell in ws[1]:
            cell.font = Font(bold=True)
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def export_productos(conn: sqlite3.Connection) -> bytes:
    rows = conn.execute(
        """SELECT p.product_id_dropi, p.nombre, p.sku, p.categoria, p.stock, p.precio,
                  p.catalog_state, p.last_seen_at, s.supplier_id,
                  COALESCE(s.store_name, s.manual_name) AS proveedor,
                  COALESCE(c.classification,'sin_clasificar'), COALESCE(c.shortlist,0)
           FROM products p
           JOIN suppliers s ON s.supplier_id=p.supplier_id
           LEFT JOIN product_classification c
             ON c.supplier_id=p.supplier_id AND c.product_id_dropi=p.product_id_dropi
           ORDER BY proveedor, p.nombre"""
    ).fetchall()
    return _book([
        (
            "Productos",
            ["ID", "Producto", "SKU", "Categoría", "Stock último observado", "Precio",
             "Estado de catálogo", "Visto", "Proveedor ID", "Proveedor", "Clasificación", "Shortlist"],
            [list(row) for row in rows],
        )
    ])


def export_observaciones(conn: sqlite3.Connection) -> bytes:
    rows = conn.execute(
        """SELECT product_id_dropi, supplier_id, stock, observed_at, provenance,
                  completeness, time_assumption, snapshot_id
           FROM stock_observations
           ORDER BY supplier_id, product_id_dropi, observed_at"""
    ).fetchall()
    return _book([
        (
            "Observaciones",
            ["Producto", "Proveedor", "Stock", "Observado en UTC", "Procedencia",
             "Cobertura", "Supuesto de hora", "Snapshot"],
            [list(row) for row in rows],
        )
    ])


def export_tendencias(conn: sqlite3.Connection, rules: dict, zone: str, hours: int = 72) -> bytes:
    data = tendencias(conn, hours, rules, zone)
    rows = []
    for item in data["items"]:
        mov = item["movimiento"]
        rows.append([
            item["product_id_dropi"],
            item["nombre"],
            item["proveedor"],
            item.get("categoria") or "",
            mov["unidades_baja"],
            mov["reposiciones"],
            mov["cambio_neto"],
            mov["confianza"],
            mov["cobertura"],
            mov["etiqueta"],
            " | ".join(mov["motivos"]),
        ])
    return _book([
        (
            "Proxy de demanda",
            ["ID", "Producto", "Proveedor", "Categoría", "Baja observada", "Reposiciones",
             "Cambio neto", "Confianza", "Cobertura", "Etiqueta", "Motivos"],
            rows,
        )
    ])


def export_bodegas(conn: sqlite3.Connection, rules: dict, zone: str) -> bytes:
    data = bodegas(conn, rules, zone)
    suppliers = []
    warehouses = []
    for item in data["items"]:
        suppliers.append([
            item["supplier_id"],
            item["nombre"],
            item["manual_name"] or "",
            item["store_name"] or "",
            "sí" if item["source_manual"] else "",
            "sí" if item["source_synced"] else "",
            "sí" if item["follow_catalog"] else "",
            item["baja_bruta"],
            item["reposiciones"],
            item["cobertura"],
            item["pedidos_mensuales"] if item["pedidos_mensuales"] is not None else "",
            item["dropshippers_mensuales"] if item["dropshippers_mensuales"] is not None else "",
        ])
        for wh in item["bodegas"]:
            warehouses.append([
                wh["warehouse_id"],
                item["supplier_id"],
                wh["nombre"],
                wh["ciudad"],
                wh["direccion"],
                "sí" if wh["follow"] else "",
                item["pedidos_mensuales"] if item["pedidos_mensuales"] is not None else "",
                "cifra del proveedor, no de la bodega física",
            ])
    return _book([
        (
            "Proveedores",
            ["ID", "Nombre", "Nombre manual", "Tienda", "Manual", "Sincronizado", "Seguimiento",
             "Baja bruta observada", "Reposiciones", "Cobertura", "Pedidos mensuales", "Dropshippers"],
            suppliers,
        ),
        (
            "Bodegas físicas",
            ["Bodega", "Proveedor", "Nombre", "Ciudad", "Dirección", "Seguimiento",
             "Pedidos mensuales del proveedor", "Nota"],
            warehouses,
        ),
    ])
