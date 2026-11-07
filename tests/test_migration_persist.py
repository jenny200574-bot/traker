import hashlib
import sqlite3
from pathlib import Path

from models import Producto

from webapp.catalog_fetch import CatalogFetch, fetch_supplier_catalog
from webapp.db import connect, init_db
from webapp.legacy_import import file_sha256, import_legacy_db
from webapp.persist import persist_catalog
from webapp.services import list_products, thresholds_from


def legacy_file(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE bodegas (
            id INTEGER PRIMARY KEY, nombre TEXT, url TEXT,
            supplier_id INTEGER UNIQUE, productos_esperados INTEGER
        );
        CREATE TABLE suppliers (
            supplier_id INTEGER PRIMARY KEY, owner_name TEXT, store_name TEXT,
            phone TEXT, store_url TEXT, logo_url TEXT, favorite INTEGER,
            products_count INTEGER
        );
        CREATE TABLE warehouses (
            warehouse_id INTEGER PRIMARY KEY, supplier_id INTEGER, warehouse_name TEXT,
            phone TEXT, address TEXT, city_id INTEGER, city_name TEXT, zip_code TEXT,
            seguimiento INTEGER, ordenes INTEGER, dropshippers INTEGER, created_at TEXT
        );
        CREATE TABLE productos (
            product_id_dropi INTEGER, supplier_id INTEGER, nombre TEXT, sku TEXT,
            imagen TEXT, precio REAL, precio_oferta REAL, stock INTEGER,
            categoria TEXT, activo INTEGER, ultima_actualizacion TEXT
        );
        CREATE TABLE historial_stock (
            product_id_dropi INTEGER, supplier_id INTEGER, stock INTEGER, fecha_hora TEXT
        );
        CREATE TABLE log_actividad (
            fecha TEXT, hora TEXT, bodega TEXT, productos_descargados INTEGER, errores TEXT
        );
        """
    )
    conn.execute(
        "INSERT INTO bodegas VALUES(1,'Manual','https://app.dropi.co/provider/10',10,4)"
    )
    conn.execute(
        "INSERT INTO bodegas VALUES(2,'Solo manual','https://app.dropi.co/provider/30',30,0)"
    )
    conn.execute(
        """INSERT INTO suppliers VALUES(10,'Ana','Tienda Diez','300',NULL,NULL,0,4)"""
    )
    conn.execute(
        """INSERT INTO suppliers VALUES(20,'Luis','Tienda Veinte','301',NULL,NULL,0,1)"""
    )
    conn.execute(
        """INSERT INTO warehouses VALUES(100,10,'Bodega norte','1','Calle 1',1,'Bogotá','110',0,12,3,'2026-01-01')"""
    )
    conn.execute(
        """INSERT INTO warehouses VALUES(200,20,'Bodega sur','2','Calle 2',2,'Cali','760',1,9,1,'2026-01-01')"""
    )
    conn.execute(
        """INSERT INTO productos VALUES(1,10,'Mesa','SKU1','',10,12,8,'Hogar',1,'2026-01-02T10:00:00')"""
    )
    conn.execute(
        """INSERT INTO productos VALUES(2,20,'Solo seguimiento','SKU2','',5,6,4,'Hogar',1,'2026-01-02T11:00:00')"""
    )
    conn.execute("INSERT INTO historial_stock VALUES(1,10,9,'2026-01-01T10:00:00')")
    conn.execute("INSERT INTO historial_stock VALUES(1,10,8,'2026-01-02T10:00:00')")
    conn.execute("INSERT INTO historial_stock VALUES(2,20,6,'2026-01-01T10:00:00')")
    conn.execute("INSERT INTO historial_stock VALUES(2,20,4,'2026-01-02T10:00:00')")
    conn.execute("INSERT INTO log_actividad VALUES('2026-01-02','10:00','Manual',1,'')")
    conn.commit()
    conn.close()


def test_importacion_no_toca_el_origen_y_une_identidades(tmp_path):
    source = tmp_path / "dropi_tracker.db"
    dest = tmp_path / "tracker.db"
    legacy_file(source)
    before = file_sha256(source)
    first = import_legacy_db(source, dest, tmp_path / "backups", "America/Bogota")
    assert file_sha256(source) == before
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before
    assert first["origen_intacto"] is True
    second = import_legacy_db(source, dest, tmp_path / "backups", "America/Bogota")
    assert file_sha256(source) == before
    conn = connect(dest)
    suppliers = conn.execute("SELECT * FROM suppliers ORDER BY supplier_id").fetchall()
    assert [row["supplier_id"] for row in suppliers] == [10, 20, 30]
    merged = conn.execute("SELECT * FROM suppliers WHERE supplier_id=10").fetchone()
    assert merged["source_manual"] == 1
    assert merged["source_synced"] == 1
    assert merged["manual_name"] == "Manual"
    assert merged["store_name"] == "Tienda Diez"
    assert merged["follow_catalog"] == 1
    tracked = conn.execute("SELECT follow_catalog FROM suppliers WHERE supplier_id=20").fetchone()
    assert tracked["follow_catalog"] == 1
    warehouses = conn.execute("SELECT COUNT(*) AS n FROM warehouses").fetchone()["n"]
    assert warehouses == 2
    manual_only = conn.execute("SELECT COUNT(*) AS n FROM warehouses WHERE supplier_id=30").fetchone()["n"]
    assert manual_only == 0
    products = list_products(conn, {}, thresholds_from(conn), "America/Bogota")
    ids = {item["product_id_dropi"] for item in products["items"]}
    assert ids == {1, 2}
    obs = conn.execute(
        """SELECT completeness, provenance, time_assumption FROM stock_observations"""
    ).fetchall()
    assert obs
    assert {row["completeness"] for row in obs} == {"unknown"}
    assert {row["provenance"] for row in obs} == {"legacy"}
    assert second["observaciones"] == 0
    conn.close()


def test_snapshot_parcial_o_fallido_no_pone_stock_en_cero(tmp_path):
    path = tmp_path / "tracker.db"
    init_db(path)
    conn = connect(path)
    conn.execute(
        "INSERT INTO suppliers(supplier_id, store_name, created_at, updated_at) VALUES(5,'Casa','2026-01-01T00:00:00+00:00','2026-01-01T00:00:00+00:00')"
    )
    conn.execute(
        """INSERT INTO products(product_id_dropi, supplier_id, nombre, stock, catalog_state)
           VALUES(1,5,'Uno',5,'visto'),(2,5,'Dos',9,'visto')"""
    )
    partial = CatalogFetch(
        products={1: Producto(product_id_dropi=1, supplier_id=5, nombre="Uno", stock=4)},
        status="partial",
        notes=["parcial"],
    )
    persist_catalog(conn, 5, None, partial)
    rows = conn.execute(
        "SELECT product_id_dropi, stock, catalog_state FROM products WHERE supplier_id=5"
    ).fetchall()
    stocks = {row["product_id_dropi"]: (row["stock"], row["catalog_state"]) for row in rows}
    assert stocks[1] == (4, "visto")
    assert stocks[2] == (9, "visto")
    assert conn.execute("SELECT COUNT(*) n FROM stock_observations WHERE product_id_dropi=2").fetchone()["n"] == 0

    failed = CatalogFetch(status="failed", notes=["falló"])
    persist_catalog(conn, 5, None, failed)
    again = conn.execute("SELECT stock FROM products WHERE product_id_dropi=2").fetchone()["stock"]
    assert again == 9

    complete = CatalogFetch(
        products={1: Producto(product_id_dropi=1, supplier_id=5, nombre="Uno", stock=3)},
        status="complete",
        empty_page_reached=True,
    )
    persist_catalog(conn, 5, None, complete)
    missing = conn.execute(
        "SELECT stock, catalog_state FROM products WHERE product_id_dropi=2"
    ).fetchone()
    assert missing["stock"] == 9
    assert missing["catalog_state"] == "ausente_en_snapshot_completo"
    assert conn.execute(
        "SELECT COUNT(*) n FROM stock_observations WHERE product_id_dropi=2 AND stock=0"
    ).fetchone()["n"] == 0
    conn.close()


def test_descarga_marca_parcial_si_falla_una_pagina(monkeypatch):
    calls = {"n": 0}

    def fake_fetch(session, token, supplier_id, start, stop_event=None):
        calls["n"] += 1
        if calls["n"] == 1:
            return [{"id": 7, "name": "Copa", "stock": 3, "price": 10}]
        if calls["n"] == 2:
            raise __import__("requests").RequestException("corte")
        return []

    monkeypatch.setattr("api_dropi._fetch_page", fake_fetch)
    result = fetch_supplier_catalog("abc", 9, delay=0, max_passes=1)
    assert 7 in result.products
    assert result.page_errors == 1
    assert result.status == "partial"
    assert result.products[7].stock == 3
