"""SQLite local: un archivo real y, si se pide, uno de demostración aparte."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from webapp.signals import DEFAULT_THRESHOLDS

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS owner (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS secrets (
    key TEXT PRIMARY KEY,
    ciphertext TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS suppliers (
    supplier_id INTEGER PRIMARY KEY,
    store_name TEXT,
    owner_name TEXT,
    phone TEXT,
    store_url TEXT,
    logo_url TEXT,
    favorite INTEGER NOT NULL DEFAULT 0,
    products_count INTEGER,
    manual_name TEXT,
    manual_url TEXT,
    productos_esperados INTEGER NOT NULL DEFAULT 0,
    follow_catalog INTEGER NOT NULL DEFAULT 0,
    source_manual INTEGER NOT NULL DEFAULT 0,
    source_synced INTEGER NOT NULL DEFAULT 0,
    notes TEXT,
    last_snapshot_id INTEGER,
    last_snapshot_status TEXT,
    last_snapshot_at TEXT,
    created_at TEXT,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS warehouses (
    warehouse_id INTEGER PRIMARY KEY,
    supplier_id INTEGER NOT NULL,
    warehouse_name TEXT,
    phone TEXT,
    address TEXT,
    city_id INTEGER,
    city_name TEXT,
    zip_code TEXT,
    follow INTEGER NOT NULL DEFAULT 0,
    monthly_orders INTEGER,
    monthly_dropshippers INTEGER,
    metrics_observed_at TEXT,
    metrics_time_assumption TEXT,
    created_at TEXT,
    updated_at TEXT,
    FOREIGN KEY (supplier_id) REFERENCES suppliers(supplier_id)
);

CREATE TABLE IF NOT EXISTS products (
    product_id_dropi INTEGER NOT NULL,
    supplier_id INTEGER NOT NULL,
    nombre TEXT,
    sku TEXT,
    imagen TEXT,
    precio REAL,
    precio_oferta REAL,
    stock INTEGER,
    categoria TEXT,
    last_seen_at TEXT,
    last_snapshot_id INTEGER,
    catalog_state TEXT NOT NULL DEFAULT 'desconocido',
    PRIMARY KEY (product_id_dropi, supplier_id)
);

CREATE TABLE IF NOT EXISTS snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    supplier_id INTEGER NOT NULL,
    job_id INTEGER,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,
    pages INTEGER NOT NULL DEFAULT 0,
    page_errors INTEGER NOT NULL DEFAULT 0,
    unique_products INTEGER NOT NULL DEFAULT 0,
    expected_products INTEGER,
    empty_page_reached INTEGER NOT NULL DEFAULT 0,
    duplicates INTEGER NOT NULL DEFAULT 0,
    coverage_note TEXT,
    provenance TEXT NOT NULL DEFAULT 'download'
);

CREATE TABLE IF NOT EXISTS stock_observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id_dropi INTEGER NOT NULL,
    supplier_id INTEGER NOT NULL,
    snapshot_id INTEGER,
    stock INTEGER NOT NULL,
    observed_at TEXT NOT NULL,
    provenance TEXT NOT NULL,
    completeness TEXT NOT NULL,
    duplicate_in_snapshot INTEGER NOT NULL DEFAULT 0,
    time_assumption TEXT,
    FOREIGN KEY (snapshot_id) REFERENCES snapshots(id)
);

CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_type TEXT NOT NULL,
    status TEXT NOT NULL,
    payload TEXT,
    log TEXT NOT NULL DEFAULT '',
    progress TEXT,
    cancel_requested INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    error TEXT
);

CREATE TABLE IF NOT EXISTS schedules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    hour INTEGER NOT NULL,
    minute INTEGER NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    job_type TEXT NOT NULL DEFAULT 'descargar',
    last_fired_on TEXT,
    UNIQUE(hour, minute, job_type)
);

CREATE TABLE IF NOT EXISTS tags (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS product_tags (
    product_id_dropi INTEGER NOT NULL,
    supplier_id INTEGER NOT NULL,
    tag_id INTEGER NOT NULL,
    PRIMARY KEY (product_id_dropi, supplier_id, tag_id),
    FOREIGN KEY (tag_id) REFERENCES tags(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS watchlists (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS watchlist_items (
    watchlist_id INTEGER NOT NULL,
    product_id_dropi INTEGER NOT NULL,
    supplier_id INTEGER NOT NULL,
    added_at TEXT NOT NULL,
    PRIMARY KEY (watchlist_id, product_id_dropi, supplier_id),
    FOREIGN KEY (watchlist_id) REFERENCES watchlists(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS product_notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id_dropi INTEGER NOT NULL,
    supplier_id INTEGER NOT NULL,
    body TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS product_classification (
    product_id_dropi INTEGER NOT NULL,
    supplier_id INTEGER NOT NULL,
    classification TEXT NOT NULL DEFAULT 'sin_clasificar',
    shortlist INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (product_id_dropi, supplier_id)
);

CREATE TABLE IF NOT EXISTS creative_briefs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id_dropi INTEGER,
    supplier_id INTEGER,
    title TEXT NOT NULL,
    audience TEXT,
    promise TEXT,
    objections TEXT,
    notes TEXT,
    status TEXT NOT NULL DEFAULT 'borrador',
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS activity_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fecha TEXT,
    hora TEXT,
    bodega TEXT,
    productos_descargados INTEGER,
    errores TEXT,
    source TEXT NOT NULL DEFAULT 'legacy',
    UNIQUE(fecha, hora, bodega, productos_descargados, errores)
);

CREATE INDEX IF NOT EXISTS idx_products_supplier ON products(supplier_id);
CREATE INDEX IF NOT EXISTS idx_obs_product ON stock_observations(supplier_id, product_id_dropi, observed_at);
CREATE INDEX IF NOT EXISTS idx_obs_time ON stock_observations(observed_at);
CREATE INDEX IF NOT EXISTS idx_wh_supplier ON warehouses(supplier_id);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status, id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_obs_snapshot
    ON stock_observations(product_id_dropi, supplier_id, snapshot_id)
    WHERE snapshot_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_obs_legacy
    ON stock_observations(product_id_dropi, supplier_id, observed_at, stock)
    WHERE provenance = 'legacy';
"""


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=8000")
    return conn


def init_db(path: Path, timezone_name: str = "America/Bogota") -> None:
    conn = connect(path)
    try:
        conn.executescript(SCHEMA)
        conn.execute("BEGIN IMMEDIATE")
        current = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
        if current is None:
            conn.execute(
                "INSERT INTO meta(key, value) VALUES('schema_version', '1')"
            )
        exists_tz = conn.execute("SELECT value FROM settings WHERE key='timezone'").fetchone()
        if exists_tz is None:
            conn.execute(
                "INSERT INTO settings(key, value) VALUES('timezone', ?)",
                (timezone_name,),
            )
        exists_th = conn.execute("SELECT value FROM settings WHERE key='thresholds'").fetchone()
        if exists_th is None:
            import json
            conn.execute(
                "INSERT INTO settings(key, value) VALUES('thresholds', ?)",
                (json.dumps(DEFAULT_THRESHOLDS),),
            )
        conn.execute("COMMIT")
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        raise
    finally:
        conn.close()


def backup_db(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    source = connect(src)
    target = connect(dest)
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()


def setting(conn: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    if row is None:
        return default
    return row["value"]


def put_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        """INSERT INTO settings(key, value) VALUES(?, ?)
           ON CONFLICT(key) DO UPDATE SET value=excluded.value""",
        (key, value),
    )


def put_secret(conn: sqlite3.Connection, key: str, ciphertext: str, updated_at: str) -> None:
    conn.execute(
        """INSERT INTO secrets(key, ciphertext, updated_at) VALUES(?, ?, ?)
           ON CONFLICT(key) DO UPDATE SET ciphertext=excluded.ciphertext, updated_at=excluded.updated_at""",
        (key, ciphertext, updated_at),
    )


def get_secret(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT ciphertext FROM secrets WHERE key=?", (key,)).fetchone()
    if row is None:
        return None
    return row["ciphertext"]
