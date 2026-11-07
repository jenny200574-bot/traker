"""
database.py — SQLite CRUD para Dropi Tracker
"""
import sqlite3
from datetime import datetime, timedelta
from typing import List, Optional, Callable
from functools import lru_cache
import threading

from models import Bodega, Producto, LogActividad
from paths import DB_PATH, BACKUP_DIR

# Caché simple en memoria para datos frecuentes
_cache_lock = threading.Lock()
_stock_anterior_cache = {}
_cache_ttl = timedelta(minutes=5)  # Caché válido por 5 minutos


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def checkpoint_db() -> None:
    """Vuelca el WAL al .db para que copiar la carpeta no pierda memoria reciente."""
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False, timeout=30)
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    """Create all tables and indexes if they don't exist."""
    with get_connection() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS bodegas (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                nombre       TEXT    NOT NULL,
                url          TEXT,
                supplier_id  INTEGER UNIQUE NOT NULL,
                productos_esperados INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS productos (
                id                 INTEGER PRIMARY KEY AUTOINCREMENT,
                product_id_dropi   INTEGER NOT NULL,
                supplier_id        INTEGER NOT NULL,
                nombre             TEXT,
                sku                TEXT,
                imagen             TEXT,
                precio             REAL,
                precio_oferta      REAL,
                stock              INTEGER,
                categoria          TEXT,
                activo             INTEGER DEFAULT 1,
                ultima_actualizacion TEXT,
                UNIQUE(product_id_dropi, supplier_id)
            );

            CREATE TABLE IF NOT EXISTS historial_stock (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                product_id_dropi INTEGER NOT NULL,
                supplier_id      INTEGER NOT NULL,
                stock            INTEGER,
                fecha_hora       TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS log_actividad (
                id                    INTEGER PRIMARY KEY AUTOINCREMENT,
                fecha                 TEXT,
                hora                  TEXT,
                bodega                TEXT,
                productos_descargados INTEGER,
                errores               TEXT
            );

            CREATE TABLE IF NOT EXISTS suppliers (
                supplier_id   INTEGER PRIMARY KEY,
                owner_name    VARCHAR(255),
                store_name    VARCHAR(255),
                phone         VARCHAR(50),
                store_url     TEXT,
                logo_url      TEXT,
                favorite      BOOLEAN DEFAULT 0,
                products_count INTEGER DEFAULT 0,
                created_at    TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at    TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS warehouses (
                warehouse_id  INTEGER PRIMARY KEY,
                supplier_id   INTEGER NOT NULL,
                warehouse_name VARCHAR(255),
                phone         VARCHAR(50),
                address       TEXT,
                city_id       INTEGER,
                city_name     VARCHAR(255),
                zip_code      VARCHAR(50),
                seguimiento   BOOLEAN DEFAULT 0,
                ordenes       INTEGER DEFAULT 0,
                dropshippers  INTEGER DEFAULT 0,
                created_at    TEXT,
                FOREIGN KEY (supplier_id) REFERENCES suppliers(supplier_id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_hist_prod
                ON historial_stock(product_id_dropi, supplier_id);
            CREATE INDEX IF NOT EXISTS idx_hist_fecha
                ON historial_stock(fecha_hora);
            CREATE INDEX IF NOT EXISTS idx_hist_id
                ON historial_stock(id);
            
            -- Índices adicionales para optimizar consultas frecuentes
            CREATE INDEX IF NOT EXISTS idx_productos_supplier
                ON productos(supplier_id);
            CREATE INDEX IF NOT EXISTS idx_productos_activo
                ON productos(activo);
            CREATE INDEX IF NOT EXISTS idx_productos_sku
                ON productos(sku);
            CREATE INDEX IF NOT EXISTS idx_productos_supplier_activo
                ON productos(supplier_id, activo);
            CREATE INDEX IF NOT EXISTS idx_warehouses_supplier
                ON warehouses(supplier_id);
            CREATE INDEX IF NOT EXISTS idx_warehouses_seguimiento
                ON warehouses(seguimiento);
            CREATE INDEX IF NOT EXISTS idx_log_fecha
                ON log_actividad(fecha);
            CREATE INDEX IF NOT EXISTS idx_hist_supp_fecha
                ON historial_stock(supplier_id, fecha_hora);
        """)
        # Auto-migration for productos_esperados
        try:
            conn.execute("ALTER TABLE bodegas ADD COLUMN productos_esperados INTEGER DEFAULT 0")
        except sqlite3.OperationalError:
            pass
        # Auto-migration for products_count
        try:
            conn.execute("ALTER TABLE suppliers ADD COLUMN products_count INTEGER DEFAULT 0")
        except sqlite3.OperationalError:
            pass
        # Auto-migration for seguimiento
        try:
            conn.execute("ALTER TABLE warehouses ADD COLUMN seguimiento BOOLEAN DEFAULT 0")
        except sqlite3.OperationalError:
            pass
        # Auto-migration for ordenes
        try:
            conn.execute("ALTER TABLE warehouses ADD COLUMN ordenes INTEGER DEFAULT 0")
        except sqlite3.OperationalError:
            pass
        # Auto-migration for dropshippers
        try:
            conn.execute("ALTER TABLE warehouses ADD COLUMN dropshippers INTEGER DEFAULT 0")
        except sqlite3.OperationalError:
            pass



# ─────────────────────────── BODEGAS ────────────────────────────────────────

def get_bodegas() -> List[Bodega]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM bodegas ORDER BY nombre"
        ).fetchall()
    return [
        Bodega(id=r["id"], nombre=r["nombre"], url=r["url"] or "",
               supplier_id=r["supplier_id"], productos_esperados=r["productos_esperados"])
        for r in rows
    ]


def add_bodega(bodega: Bodega) -> bool:
    """Returns False if supplier_id already exists."""
    try:
        with get_connection() as conn:
            conn.execute(
                "INSERT INTO bodegas (nombre, url, supplier_id, productos_esperados) VALUES (?, ?, ?, ?)",
                (bodega.nombre, bodega.url, bodega.supplier_id, bodega.productos_esperados),
            )
        return True
    except sqlite3.IntegrityError:
        return False


def update_bodega(bodega: Bodega) -> None:
    with get_connection() as conn:
        conn.execute(
            "UPDATE bodegas SET nombre=?, url=?, supplier_id=?, productos_esperados=? WHERE id=?",
            (bodega.nombre, bodega.url, bodega.supplier_id, bodega.productos_esperados, bodega.id),
        )


def importar_bodegas_excel(ruta_excel: str) -> tuple[int, int, list]:
    """
    Importa bodegas desde un archivo Excel.
    
    El Excel debe tener las columnas: nombre, supplier_id (obligatorias)
    Opcionales: url, productos_esperados
    
    Args:
        ruta_excel: Ruta al archivo Excel
    
    Returns:
        Tupla (exitosas, fallidas, errores) donde errores es una lista de mensajes de error
    """
    import pandas as pd
    
    try:
        df = pd.read_excel(ruta_excel)
        
        # Normalizar nombres de columnas a minúsculas
        df.columns = [col.lower().strip() for col in df.columns]
        
        # Verificar columnas requeridas
        if 'nombre' not in df.columns or 'supplier_id' not in df.columns:
            return 0, 0, ["El Excel debe tener las columnas 'nombre' y 'supplier_id'"]
        
        exitosas = 0
        fallidas = 0
        errores = []
        
        for _, row in df.iterrows():
            try:
                nombre = str(row['nombre']).strip()
                supplier_id = int(row['supplier_id'])
                url = str(row.get('url', '')).strip() if pd.notna(row.get('url')) else None
                productos_esperados = int(row.get('productos_esperados', 0)) if pd.notna(row.get('productos_esperados')) else 0
                
                bodega = Bodega(
                    nombre=nombre,
                    url=url,
                    supplier_id=supplier_id,
                    productos_esperados=productos_esperados
                )
                
                if add_bodega(bodega):
                    exitosas += 1
                else:
                    fallidas += 1
                    errores.append(f"Supplier ID {supplier_id} ya existe")
                    
            except Exception as e:
                fallidas += 1
                errores.append(f"Error en fila {_:,}: {str(e)}")
        
        return exitosas, fallidas, errores
        
    except Exception as e:
        return 0, 0, [f"Error al leer archivo Excel: {str(e)}"]


def delete_bodega(bodega_id: int) -> None:
    with get_connection() as conn:
        conn.execute("DELETE FROM bodegas WHERE id=?", (bodega_id,))


# ─────────────────────────── PRODUCTOS ──────────────────────────────────────

def upsert_producto(p: Producto) -> None:
    now = datetime.now().isoformat(timespec="seconds")
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO productos
                (product_id_dropi, supplier_id, nombre, sku, imagen,
                 precio, precio_oferta, stock, categoria, activo, ultima_actualizacion)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
            ON CONFLICT(product_id_dropi, supplier_id) DO UPDATE SET
                nombre               = excluded.nombre,
                sku                  = excluded.sku,
                imagen               = excluded.imagen,
                precio               = excluded.precio,
                precio_oferta        = excluded.precio_oferta,
                stock                = excluded.stock,
                categoria            = excluded.categoria,
                activo               = 1,
                ultima_actualizacion = excluded.ultima_actualizacion
            """,
            (p.product_id_dropi, p.supplier_id, p.nombre, p.sku, p.imagen,
             p.precio, p.precio_oferta, p.stock, p.categoria, now),
        )


def marcar_inactivos(supplier_id: int, ids_activos: List[int]) -> None:
    """Mark products not present in the latest download as inactive."""
    if not ids_activos:
        return
    placeholders = ",".join("?" * len(ids_activos))
    with get_connection() as conn:
        conn.execute(
            f"""UPDATE productos SET activo=0
                WHERE supplier_id=?
                  AND product_id_dropi NOT IN ({placeholders})""",
            [supplier_id] + ids_activos,
        )


def exportar_historial_antiguo_a_csv(dias: int = 30) -> Optional[str]:
    """
    Exporta registros de historial_stock más antiguos que N días a un archivo CSV.
    Retorna la ruta del archivo creado o None si no hay datos para exportar.
    """
    import csv
    
    cutoff = (datetime.now() - timedelta(days=dias)).isoformat(timespec="seconds")
    
    backup_dir = BACKUP_DIR
    backup_dir.mkdir(exist_ok=True)
    
    # Nombre del archivo con fecha
    fecha_archivo = datetime.now().strftime("%Y%m%d_%H%M%S")
    archivo_csv = backup_dir / f"historial_backup_{fecha_archivo}.csv"
    
    with get_connection() as conn:
        # Obtener datos antiguos
        rows = conn.execute(
            """SELECT h.product_id_dropi, h.supplier_id, h.stock, h.fecha_hora, 
                      p.nombre, p.sku, b.nombre as bodega_nombre
               FROM historial_stock h
               LEFT JOIN productos p ON h.product_id_dropi = p.product_id_dropi AND h.supplier_id = p.supplier_id
               LEFT JOIN bodegas b ON h.supplier_id = b.supplier_id
               WHERE h.fecha_hora < ?
               ORDER BY h.fecha_hora""",
            (cutoff,)
        ).fetchall()
    
    if not rows:
        return None
    
    # Escribir CSV
    with open(archivo_csv, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(['product_id_dropi', 'supplier_id', 'stock', 'fecha_hora', 
                        'nombre_producto', 'sku', 'bodega_nombre'])
        for row in rows:
            writer.writerow(row)
    
    return str(archivo_csv)


def limpiar_historial_antiguo(dias: int = 30) -> int:
    """
    Exporta y luego elimina registros de historial_stock más antiguos que N días.
    Mantiene la base de datos optimizada para rendimiento.
    Retorna el número de registros eliminados.
    """
    # Primero exportar a CSV
    archivo_backup = exportar_historial_antiguo_a_csv(dias)
    
    cutoff = (datetime.now() - timedelta(days=dias)).isoformat(timespec="seconds")
    with get_connection() as conn:
        deleted = conn.execute(
            "DELETE FROM historial_stock WHERE fecha_hora < ?",
            (cutoff,)
        ).rowcount
    
    if archivo_backup:
        print(f"[DB] Backup exportado: {archivo_backup}")
    
    return deleted


def consultar_historial_backup(
    product_id_dropi: int, 
    supplier_id: int, 
    fecha_inicio: Optional[str] = None,
    fecha_fin: Optional[str] = None
) -> List[dict]:
    """
    Consulta el historial de un producto en los archivos CSV de backup externos.
    Útil para consultar datos de meses anteriores que ya no están en la DB activa.
    
    Args:
        product_id_dropi: ID del producto en Dropi
        supplier_id: ID del supplier/bodega
        fecha_inicio: Fecha inicio en formato ISO (YYYY-MM-DD), opcional
        fecha_fin: Fecha fin en formato ISO (YYYY-MM-DD), opcional
    
    Returns:
        Lista de diccionarios con el historial encontrado en backups
    """
    import csv
    
    backup_dir = BACKUP_DIR
    if not backup_dir.exists():
        return []
    
    resultados = []
    
    # Buscar todos los archivos CSV de backup
    archivos_backup = sorted(backup_dir.glob("historial_backup_*.csv"))
    
    for archivo in archivos_backup:
        try:
            with open(archivo, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    # Filtrar por producto y supplier
                    if int(row['product_id_dropi']) != product_id_dropi:
                        continue
                    if int(row['supplier_id']) != supplier_id:
                        continue
                    
                    # Filtrar por fechas si se especificaron
                    if fecha_inicio and row['fecha_hora'] < fecha_inicio:
                        continue
                    if fecha_fin and row['fecha_hora'] > fecha_fin:
                        continue
                    
                    resultados.append({
                        'product_id_dropi': int(row['product_id_dropi']),
                        'supplier_id': int(row['supplier_id']),
                        'stock': int(row['stock']),
                        'fecha_hora': row['fecha_hora'],
                        'nombre_producto': row['nombre_producto'],
                        'sku': row['sku'],
                        'bodega_nombre': row['bodega_nombre'],
                        'archivo_backup': archivo.name
                    })
        except Exception as e:
            print(f"[DB] Error leyendo backup {archivo}: {e}")
            continue
    
    # Ordenar por fecha
    resultados.sort(key=lambda x: x['fecha_hora'])
    
    return resultados


def save_productos_lote(productos: List[Producto]) -> None:
    """
    Guarda una lista de productos en una única transacción SQLite.
    Usa conexión directa (sin context manager de transacción) para evitar
    conflictos entre el isolation_level automático de Python y BEGIN manual.
    Soporta bases de datos de cualquier tamaño con procesamiento por lotes.
    """
    if not productos:
        return

    BATCH_SIZE = 500          # Lotes para evitar límite de variables SQLite
    now = datetime.now().isoformat(timespec="seconds")
    fecha_hora = now
    supplier_id = productos[0].supplier_id

    # Conexión directa — isolation_level=None → modo autocommit, manejamos
    # la transacción manualmente sin interferencia del context manager
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA synchronous=NORMAL")   # Más rápido en escrituras masivas

    try:
        conn.execute("BEGIN IMMEDIATE")

        # 1. Obtener stocks actuales para comparar cambios
        stocks_actuales = {}
        for row in conn.execute(
            "SELECT product_id_dropi, stock FROM productos WHERE supplier_id=?",
            (supplier_id,)
        ):
            stocks_actuales[row["product_id_dropi"]] = row["stock"]

        # 2. Marcar todos los productos de esta bodega como inactivos
        conn.execute("UPDATE productos SET activo=0 WHERE supplier_id=?", (supplier_id,))

        # 3. Upsert en lotes — evita el límite de SQLITE_MAX_VARIABLE_NUMBER
        upsert_sql = """
            INSERT INTO productos
                (product_id_dropi, supplier_id, nombre, sku, imagen,
                 precio, precio_oferta, stock, categoria, activo, ultima_actualizacion)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
            ON CONFLICT(product_id_dropi, supplier_id) DO UPDATE SET
                nombre               = excluded.nombre,
                sku                  = excluded.sku,
                imagen               = excluded.imagen,
                precio               = excluded.precio,
                precio_oferta        = excluded.precio_oferta,
                stock                = excluded.stock,
                categoria            = excluded.categoria,
                activo               = 1,
                ultima_actualizacion = excluded.ultima_actualizacion
        """
        hist_sql = """INSERT INTO historial_stock
                        (product_id_dropi, supplier_id, stock, fecha_hora)
                      VALUES (?, ?, ?, ?)"""

        all_upsert = [
            (p.product_id_dropi, p.supplier_id, p.nombre, p.sku, p.imagen,
             p.precio, p.precio_oferta, p.stock, p.categoria, now)
            for p in productos
        ]
        
        # 4. Solo insertar historial cuando el stock cambia
        all_hist = []
        for p in productos:
            stock_anterior = stocks_actuales.get(p.product_id_dropi)
            # Insertar si: es nuevo producto OR stock cambió
            if stock_anterior is None or stock_anterior != p.stock:
                all_hist.append((p.product_id_dropi, p.supplier_id, p.stock, fecha_hora))

        for i in range(0, len(all_upsert), BATCH_SIZE):
            conn.executemany(upsert_sql, all_upsert[i:i + BATCH_SIZE])

        # 5. Insertar historial solo de productos con cambios
        for i in range(0, len(all_hist), BATCH_SIZE):
            conn.executemany(hist_sql, all_hist[i:i + BATCH_SIZE])

        conn.execute("COMMIT")

    except Exception:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        raise
    finally:
        conn.close()

    # 4. Limpiar historial antiguo para mantener rendimiento
    try:
        deleted = limpiar_historial_antiguo(dias=30)
        if deleted > 0:
            print(f"[DB] Limpieza: {deleted} registros históricos eliminados (>30 días)")
    except Exception as e:
        print(f"[DB] Advertencia: Error al limpiar historial antiguo: {e}")


def get_productos_bodega(supplier_id: int) -> List[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT * FROM productos
               WHERE supplier_id=? AND activo=1
               ORDER BY nombre""",
            (supplier_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_productos_bodega_paginado(supplier_id: int, offset: int = 0, limit: int = 100) -> List[dict]:
    """
    Obtiene productos de una bodega con paginación para mejor rendimiento.
    
    Args:
        supplier_id: ID del proveedor
        offset: Número de productos a saltar
        limit: Número máximo de productos a devolver
    
    Returns:
        Lista de diccionarios con información de productos
    """
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT * FROM productos
               WHERE supplier_id=? AND activo=1
               ORDER BY nombre
               LIMIT ? OFFSET ?""",
            (supplier_id, limit, offset),
        ).fetchall()
    return [dict(r) for r in rows]


def count_productos_bodega(supplier_id: int) -> int:
    """Cuenta el total de productos activos de una bodega."""
    with get_connection() as conn:
        row = conn.execute(
            """SELECT COUNT(*) as total FROM productos
               WHERE supplier_id=? AND activo=1""",
            (supplier_id,),
        ).fetchone()
    return row["total"] if row else 0


def get_stock_hace_24h(supplier_id: int) -> dict:
    """Returns {product_id_dropi: stock_at_24h_ago}."""
    cutoff = (datetime.now() - timedelta(hours=24)).isoformat(timespec="seconds")
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT h.product_id_dropi, h.stock
            FROM historial_stock h
            INNER JOIN (
                SELECT product_id_dropi, MIN(id) AS min_id
                FROM historial_stock
                WHERE supplier_id = ? AND fecha_hora >= ?
                GROUP BY product_id_dropi
            ) m ON h.id = m.min_id
            WHERE h.supplier_id = ?
            """,
            (supplier_id, cutoff, supplier_id),
        ).fetchall()
    return {r["product_id_dropi"]: r["stock"] for r in rows}


def get_stock_por_periodo(supplier_id: int, horas: Optional[int] = None) -> dict:
    """Returns {product_id_dropi: stock_at_period_start} for a specific period.
    
    Args:
        supplier_id: ID de la bodega
        horas: Número de horas para el período (None = todo el historial, usa stock mínimo)
    """
    with get_connection() as conn:
        if horas is None:
            # Todo el historial - usar stock mínimo
            rows = conn.execute(
                """
                SELECT product_id_dropi, MIN(stock) AS stock
                FROM historial_stock
                WHERE supplier_id = ?
                GROUP BY product_id_dropi
                """,
                (supplier_id,),
            ).fetchall()
        else:
            cutoff = (datetime.now() - timedelta(hours=horas)).isoformat(timespec="seconds")
            rows = conn.execute(
                """
                SELECT h.product_id_dropi, h.stock
                FROM historial_stock h
                INNER JOIN (
                    SELECT product_id_dropi, MIN(id) AS min_id
                    FROM historial_stock
                    WHERE supplier_id = ? AND fecha_hora >= ?
                    GROUP BY product_id_dropi
                ) m ON h.id = m.min_id
                WHERE h.supplier_id = ?
                """,
                (supplier_id, cutoff, supplier_id),
            ).fetchall()
    return {r["product_id_dropi"]: r["stock"] for r in rows}


def get_stock_anterior(supplier_id: int) -> dict:
    """Returns {product_id_dropi: stock_previous_report} con caché."""
    # Verificar caché
    cache_key = f"stock_anterior_{supplier_id}"
    now = datetime.now()
    
    with _cache_lock:
        if cache_key in _stock_anterior_cache:
            cached_data, cached_time = _stock_anterior_cache[cache_key]
            if now - cached_time < _cache_ttl:
                return cached_data
    
    # Si no está en caché o expiró, consultar a la base de datos
    with get_connection() as conn:
        runs = conn.execute(
            """SELECT DISTINCT fecha_hora FROM historial_stock
               WHERE supplier_id = ?
               ORDER BY fecha_hora DESC LIMIT 2""",
            (supplier_id,)
        ).fetchall()
        
        if len(runs) < 2:
            return {}
        
        prev_fecha = runs[1]["fecha_hora"]
        
        rows = conn.execute(
            """SELECT product_id_dropi, stock FROM historial_stock
               WHERE supplier_id = ? AND fecha_hora = ?""",
            (supplier_id, prev_fecha)
        ).fetchall()
    
    result = {r["product_id_dropi"]: r["stock"] for r in rows}
    
    # Guardar en caché
    with _cache_lock:
        _stock_anterior_cache[cache_key] = (result, now)
    
    return result


def clear_cache():
    """Limpia todo el caché en memoria."""
    with _cache_lock:
        _stock_anterior_cache.clear()


def clear_cache_supplier(supplier_id: int):
    """Limpia el caché para un supplier específico."""
    cache_key = f"stock_anterior_{supplier_id}"
    with _cache_lock:
        if cache_key in _stock_anterior_cache:
            del _stock_anterior_cache[cache_key]


def get_historial_producto(
    product_id_dropi: int, supplier_id: int, limit: int = 120
) -> List[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT fecha_hora, stock FROM historial_stock
               WHERE product_id_dropi=? AND supplier_id=?
               ORDER BY fecha_hora ASC
               LIMIT ?""",
            (product_id_dropi, supplier_id, limit),
        ).fetchall()
    return [dict(r) for r in rows]


# ─────────────────────────── HISTORIAL ──────────────────────────────────────

def insertar_historial(product_id_dropi: int, supplier_id: int, stock: int) -> None:
    fecha_hora = datetime.now().isoformat(timespec="seconds")
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO historial_stock (product_id_dropi, supplier_id, stock, fecha_hora)
               VALUES (?, ?, ?, ?)""",
            (product_id_dropi, supplier_id, stock, fecha_hora),
        )


# ─────────────────────────── LOG ACTIVIDAD ──────────────────────────────────

def insertar_log(log: LogActividad) -> None:
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO log_actividad (fecha, hora, bodega, productos_descargados, errores)
               VALUES (?, ?, ?, ?, ?)""",
            (log.fecha, log.hora, log.bodega, log.productos_descargados, log.errores),
        )


def get_log_actividad(limit: int = 300) -> List[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM log_actividad ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


# ─────────────────────────── PRODUCTOS CALIENTES ────────────────────────────

def get_productos_calientes(horas: int = 24) -> List[dict]:
    """Products ordered by estimated sales (stock decrease) in the last N hours.
    
    Args:
        horas: Número de horas para el período (ej: 24, 168=7 días, 720=30 días, None=todo el historial)
    """
    with get_connection() as conn:
        if horas is None:
            # Todo el historial - usar todos los cambios
            cutoff = None
            rows = conn.execute(
                """
                SELECT
                    p.product_id_dropi,
                    p.nombre,
                    p.sku,
                    p.imagen,
                    p.stock          AS stock_actual,
                    b.nombre         AS bodega_nombre,
                    p.supplier_id,
                    COALESCE(h_prev.stock, p.stock) AS stock_anterior,
                    (COALESCE(h_old.stock, p.stock) - p.stock) AS ventas_estimadas
                FROM productos p
                JOIN bodegas b ON p.supplier_id = b.supplier_id
                LEFT JOIN (
                    SELECT h.product_id_dropi, h.supplier_id, MIN(stock) AS stock
                    FROM historial_stock h
                    GROUP BY product_id_dropi, supplier_id
                ) h_old ON h_old.product_id_dropi = p.product_id_dropi
                        AND h_old.supplier_id      = p.supplier_id
                LEFT JOIN (
                    SELECT h1.product_id_dropi, h1.supplier_id, h1.stock
                    FROM historial_stock h1
                    WHERE h1.fecha_hora = (
                        SELECT DISTINCT fecha_hora FROM historial_stock h2
                        WHERE h2.supplier_id = h1.supplier_id
                        ORDER BY fecha_hora DESC LIMIT 1 OFFSET 1
                    )
                ) h_prev ON h_prev.product_id_dropi = p.product_id_dropi
                        AND h_prev.supplier_id      = p.supplier_id
                WHERE p.activo = 1
                  AND EXISTS (
                      SELECT 1 FROM historial_stock h
                      WHERE h.product_id_dropi = p.product_id_dropi
                      AND h.supplier_id = p.supplier_id
                  )
                ORDER BY ventas_estimadas DESC
                LIMIT 150
                """
            ).fetchall()
        else:
            cutoff = (datetime.now() - timedelta(hours=horas)).isoformat(timespec="seconds")
            rows = conn.execute(
                """
                SELECT
                    p.product_id_dropi,
                    p.nombre,
                    p.sku,
                    p.imagen,
                    p.stock          AS stock_actual,
                    b.nombre         AS bodega_nombre,
                    p.supplier_id,
                    COALESCE(h_prev.stock, p.stock) AS stock_anterior,
                    (COALESCE(h_old.stock, p.stock) - p.stock) AS ventas_estimadas
                FROM productos p
                JOIN bodegas b ON p.supplier_id = b.supplier_id
                LEFT JOIN (
                    SELECT h.product_id_dropi, h.supplier_id, h.stock
                    FROM historial_stock h
                    INNER JOIN (
                        SELECT product_id_dropi, supplier_id, MIN(id) AS min_id
                        FROM historial_stock
                        WHERE fecha_hora >= ?
                        GROUP BY product_id_dropi, supplier_id
                    ) m ON h.id = m.min_id
                ) h_old ON h_old.product_id_dropi = p.product_id_dropi
                        AND h_old.supplier_id      = p.supplier_id
                LEFT JOIN (
                    SELECT h1.product_id_dropi, h1.supplier_id, h1.stock
                    FROM historial_stock h1
                    WHERE h1.fecha_hora = (
                        SELECT DISTINCT fecha_hora FROM historial_stock h2
                        WHERE h2.supplier_id = h1.supplier_id
                        ORDER BY fecha_hora DESC LIMIT 1 OFFSET 1
                    )
                ) h_prev ON h_prev.product_id_dropi = p.product_id_dropi
                        AND h_prev.supplier_id      = p.supplier_id
                WHERE p.activo = 1
                  AND (COALESCE(h_old.stock, p.stock) - p.stock) > 0
                ORDER BY ventas_estimadas DESC
                LIMIT 150
                """,
                (cutoff,),
            ).fetchall()
    return [dict(r) for r in rows]


# ─────────────────────────── SUPPLIERS ────────────────────────────────────────

def sync_dropi_suppliers(proveedores: List[dict]) -> tuple[int, int, int]:
    """
    Sincroniza proveedores desde la API de Dropi a la base de datos local.
    
    Args:
        proveedores: Lista de diccionarios con información de proveedores de la API
        
    Returns:
        Tupla (nuevos, actualizados, saltados) con cantidad de proveedores nuevos, 
        actualizados y saltados (ya existentes sin cambios)
    """
    nuevos = 0
    actualizados = 0
    saltados = 0
    now = datetime.now().isoformat(timespec="seconds")
    
    with get_connection() as conn:
        for prov in proveedores:
            supplier_id = prov.get("id")
            if not supplier_id:
                continue
                
            # Verificar si existe y obtener datos actuales
            existing = conn.execute(
                "SELECT * FROM suppliers WHERE supplier_id = ?",
                (supplier_id,)
            ).fetchone()
            
            if existing:
                # Verificar si hay cambios para actualizar
                needs_update = (
                    existing["owner_name"] != prov.get("name") or
                    existing["store_name"] != prov.get("store_name") or
                    existing["phone"] != prov.get("phone") or
                    existing["store_url"] != prov.get("store_url") or
                    existing["logo_url"] != prov.get("urlS3_logo") or
                    existing["favorite"] != (1 if prov.get("favorite") else 0) or
                    existing["products_count"] != prov.get("products_count", 0)
                )

                if needs_update:
                    conn.execute("""
                        UPDATE suppliers SET
                            owner_name = ?,
                            store_name = ?,
                            phone = ?,
                            store_url = ?,
                            logo_url = ?,
                            favorite = ?,
                            products_count = ?,
                            updated_at = ?
                        WHERE supplier_id = ?
                    """, (
                        prov.get("name"),
                        prov.get("store_name"),
                        prov.get("phone"),
                        prov.get("store_url"),
                        prov.get("urlS3_logo"),
                        1 if prov.get("favorite") else 0,
                        prov.get("products_count", 0),
                        now,
                        supplier_id
                    ))
                    actualizados += 1
                else:
                    saltados += 1
            else:
                # Insertar nuevo
                conn.execute("""
                    INSERT INTO suppliers (supplier_id, owner_name, store_name, phone,
                                         store_url, logo_url, favorite, products_count, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    supplier_id,
                    prov.get("name"),
                    prov.get("store_name"),
                    prov.get("phone"),
                    prov.get("store_url"),
                    prov.get("urlS3_logo"),
                    1 if prov.get("favorite") else 0,
                    prov.get("products_count", 0),
                    now,
                    now
                ))
                nuevos += 1
                
        conn.commit()
    
    return nuevos, actualizados, saltados


def sync_dropi_warehouses(proveedores: List[dict]) -> tuple[int, int]:
    """
    Sincroniza bodegas desde la API de Dropi a la base de datos local.

    Args:
        proveedores: Lista de diccionarios con información de proveedores y sus bodegas

    Returns:
        Tupla (nuevas, actualizadas) con cantidad de bodegas nuevas y actualizadas
    """
    nuevas = 0
    actualizadas = 0

    if not proveedores:
        return nuevas, actualizadas

    with get_connection() as conn:
        for prov in proveedores:
            supplier_id = prov.get("id")
            if not supplier_id:
                continue

            warehouses = prov.get("warehouses")
            if warehouses is None:
                warehouses = []
            
            for wh in warehouses:
                warehouse_id = wh.get("id")
                if not warehouse_id:
                    continue
                    
                city = wh.get("city", {})
                city_id = city.get("id") if city else None
                city_name = city.get("name") if city else None
                
                # Verificar si existe
                existing = conn.execute(
                    "SELECT warehouse_id FROM warehouses WHERE warehouse_id = ?",
                    (warehouse_id,)
                ).fetchone()
                
                if existing:
                    # Actualizar
                    conn.execute("""
                        UPDATE warehouses SET
                            supplier_id = ?,
                            warehouse_name = ?,
                            phone = ?,
                            address = ?,
                            city_id = ?,
                            city_name = ?,
                            zip_code = ?
                        WHERE warehouse_id = ?
                    """, (
                        supplier_id,
                        wh.get("name"),
                        wh.get("phone"),
                        wh.get("address"),
                        city_id,
                        city_name,
                        wh.get("zip_code"),
                        warehouse_id
                    ))
                    actualizadas += 1
                else:
                    # Insertar nueva
                    conn.execute("""
                        INSERT INTO warehouses (warehouse_id, supplier_id, warehouse_name,
                                               phone, address, city_id, city_name, 
                                               zip_code, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        warehouse_id,
                        supplier_id,
                        wh.get("name"),
                        wh.get("phone"),
                        wh.get("address"),
                        city_id,
                        city_name,
                        wh.get("zip_code"),
                        wh.get("created_at")
                    ))
                    nuevas += 1
                    
        conn.commit()
    
    return nuevas, actualizadas


def get_suppliers() -> List[dict]:
    """Obtiene todos los proveedores de la base de datos."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM suppliers ORDER BY store_name"
        ).fetchall()
    return [dict(r) for r in rows]


def get_warehouses(supplier_id: Optional[int] = None) -> List[dict]:
    """
    Obtiene bodegas de la base de datos con conteo de productos de la API.

    Args:
        supplier_id: Si se especifica, filtra por supplier_id

    Returns:
        Lista de diccionarios con información de bodegas y conteo de productos
    """
    with get_connection() as conn:
        if supplier_id:
            rows = conn.execute(
                """SELECT w.*, COALESCE(s.store_name, 'Sin proveedor') as supplier_name, COALESCE(s.owner_name, '') as owner_name, COALESCE(s.products_count, 0) as productos_count
                   FROM warehouses w
                   LEFT JOIN suppliers s ON w.supplier_id = s.supplier_id
                   WHERE w.supplier_id = ?
                   ORDER BY w.warehouse_name""",
                (supplier_id,)
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT w.*, COALESCE(s.store_name, 'Sin proveedor') as supplier_name, COALESCE(s.owner_name, '') as owner_name, COALESCE(s.products_count, 0) as productos_count
                   FROM warehouses w
                   LEFT JOIN suppliers s ON w.supplier_id = s.supplier_id
                   ORDER BY COALESCE(s.store_name, 'Sin proveedor'), w.warehouse_name"""
            ).fetchall()
    return [dict(r) for r in rows]


def get_warehouses_seguimiento() -> List[dict]:
    """
    Obtiene solo las bodegas marcadas en seguimiento.

    Returns:
        Lista de diccionarios con información de bodegas en seguimiento
    """
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT w.*, COALESCE(s.store_name, 'Sin proveedor') as supplier_name, COALESCE(s.owner_name, '') as owner_name, COALESCE(s.products_count, 0) as productos_count
               FROM warehouses w
               LEFT JOIN suppliers s ON w.supplier_id = s.supplier_id
               WHERE w.seguimiento = 1
               ORDER BY COALESCE(s.store_name, 'Sin proveedor'), w.warehouse_name"""
        ).fetchall()
    return [dict(r) for r in rows]


def delete_supplier(supplier_id: int) -> bool:
    """
    Elimina un proveedor y todas sus bodegas de la base de datos.
    
    Args:
        supplier_id: ID del proveedor a eliminar
        
    Returns:
        True si se eliminó correctamente, False si no existía
    """
    with get_connection() as conn:
        # Verificar si existe
        existing = conn.execute(
            "SELECT supplier_id FROM suppliers WHERE supplier_id = ?",
            (supplier_id,)
        ).fetchone()
        
        if not existing:
            return False
        
        # Las bodegas se eliminan automáticamente por CASCADE
        conn.execute(
            "DELETE FROM suppliers WHERE supplier_id = ?",
            (supplier_id,)
        )
        conn.commit()

    return True


def toggle_warehouse_seguimiento(warehouse_id: int) -> bool:
    """
    Alterna el estado de seguimiento de una bodega.

    Args:
        warehouse_id: ID de la bodega

    Returns:
        True si se actualizó correctamente, False si no existía
    """
    with get_connection() as conn:
        # Verificar si existe
        existing = conn.execute(
            "SELECT warehouse_id FROM warehouses WHERE warehouse_id = ?",
            (warehouse_id,)
        ).fetchone()

        if not existing:
            return False

        # Alternar estado
        conn.execute(
            "UPDATE warehouses SET seguimiento = NOT seguimiento WHERE warehouse_id = ?",
            (warehouse_id,)
        )
        conn.commit()

    return True


def set_warehouse_seguimiento(warehouse_id: int, activo: bool) -> bool:
    """
    Establece el estado de seguimiento de una bodega.

    Args:
        warehouse_id: ID de la bodega
        activo: True para activar seguimiento, False para desactivar

    Returns:
        True si se actualizó correctamente, False si no existía
    """
    with get_connection() as conn:
        # Verificar si existe
        existing = conn.execute(
            "SELECT warehouse_id FROM warehouses WHERE warehouse_id = ?",
            (warehouse_id,)
        ).fetchone()

        if not existing:
            return False

        conn.execute(
            "UPDATE warehouses SET seguimiento = ? WHERE warehouse_id = ?",
            (1 if activo else 0, warehouse_id)
        )
        conn.commit()

    return True


def update_warehouse_ordenes(warehouse_id: int, ordenes: int) -> bool:
    """
    Actualiza el número de órdenes de una bodega.

    Args:
        warehouse_id: ID de la bodega
        ordenes: Número de órdenes

    Returns:
        True si se actualizó correctamente, False si no existía
    """
    with get_connection() as conn:
        # Verificar si existe
        existing = conn.execute(
            "SELECT warehouse_id FROM warehouses WHERE warehouse_id = ?",
            (warehouse_id,)
        ).fetchone()

        if not existing:
            return False

        conn.execute(
            "UPDATE warehouses SET ordenes = ? WHERE warehouse_id = ?",
            (ordenes, warehouse_id)
        )
        conn.commit()

    return True


def update_warehouse_dropshippers(warehouse_id: int, dropshippers: int) -> bool:
    """
    Actualiza el número de dropshippers de una bodega.

    Args:
        warehouse_id: ID de la bodega
        dropshippers: Número de dropshippers

    Returns:
        True si se actualizó correctamente, False si no existía
    """
    with get_connection() as conn:
        # Verificar si existe
        existing = conn.execute(
            "SELECT warehouse_id FROM warehouses WHERE warehouse_id = ?",
            (warehouse_id,)
        ).fetchone()

        if not existing:
            return False

        conn.execute(
            "UPDATE warehouses SET dropshippers = ? WHERE warehouse_id = ?",
            (dropshippers, warehouse_id)
        )
        conn.commit()

    return True


def sync_ordenes_bodegas(callback: Optional[Callable[[str, int, int], None]] = None) -> tuple[int, int]:
    """
    Extrae las métricas (productos, órdenes, dropshippers) de los proveedores desde la API v2
    y actualiza todas las bodegas de cada proveedor.

    Args:
        callback: Función opcional para reportar progreso (mensaje, actual, total)

    Returns:
        Tupla (exitosas, fallidas) con cantidad de proveedores procesados exitosamente y con error
    """
    from api_dropi import obtener_metricas_proveedor, login_dropi
    import settings

    warehouses = get_warehouses()
    if not warehouses:
        return 0, 0

    # Agrupar bodegas por supplier_id para evitar llamadas duplicadas
    suppliers_dict = {}
    for wh in warehouses:
        supplier_id = wh.get("supplier_id")
        if supplier_id:
            if supplier_id not in suppliers_dict:
                suppliers_dict[supplier_id] = {
                    "supplier_name": wh.get("supplier_name", ""),
                    "warehouses": []
                }
            suppliers_dict[supplier_id]["warehouses"].append(wh)

    total = len(suppliers_dict)
    exitosas = 0
    fallidas = 0

    # Obtener token fresco usando login automático (como en extracción de productos)
    email = settings.get_email()
    password = settings.get_password()

    if not email or not password:
        if callback:
            callback("Error: No hay credenciales configuradas", 0, total)
        return 0, total

    try:
        if callback:
            callback("🔐 Obteniendo token fresco...", 0, total)
        token = login_dropi(email, password)
        settings.save_token(token)
        if callback:
            callback("✓ Token obtenido correctamente", 0, total)
    except Exception as e:
        if callback:
            callback(f"Error al obtener token: {e}", 0, total)
        return 0, total

    for idx, (supplier_id, supplier_data) in enumerate(suppliers_dict.items(), 1):
        supplier_name = supplier_data["supplier_name"]

        # Pequeño delay para evitar rate limiting
        if idx > 1:
            import time
            time.sleep(0.1)

        # Obtener métricas del proveedor desde la API
        metricas = obtener_metricas_proveedor(supplier_id, token)

        if metricas:
            # Actualizar todas las bodegas de este proveedor
            ordenes = metricas.get("total_monthly_orders", 0)
            productos = metricas.get("total_monthly_products", 0)
            dropshippers = metricas.get("total_monthly_dropshippers", 0)

            for wh in supplier_data["warehouses"]:
                warehouse_id = wh.get("warehouse_id")
                if warehouse_id:
                    update_warehouse_ordenes(warehouse_id, ordenes)
                    update_warehouse_dropshippers(warehouse_id, dropshippers)

            exitosas += 1
            if callback:
                callback(f"Procesando {idx}/{total}: {supplier_name} - {ordenes:,} órdenes, {dropshippers:,} dropshippers", idx, total)
        else:
            fallidas += 1
            if callback:
                callback(f"Procesando {idx}/{total}: {supplier_name} - Error", idx, total)

    return exitosas, fallidas


# ─────────────────────────── TODO LO VENDIDO ─────────────────────────────────

def get_productos_con_movimientos(limit: Optional[int] = None, offset: int = 0) -> List[dict]:
    """
    Obtiene todos los productos que han tenido movimientos de stock (subidas o bajas).
    Incluye productos aunque el stock actual sea igual al anterior, si hubo cambios en descargas anteriores.
    
    Args:
        limit: Cantidad máxima de productos a retornar (default: None = sin límite)
        offset: Cantidad de productos a saltar (default: 0)
    """
    with get_connection() as conn:
        # Construir la consulta con o sin LIMIT
        if limit is None:
            sql = """
                SELECT DISTINCT
                    p.product_id_dropi,
                    p.nombre,
                    p.sku,
                    p.imagen,
                    p.stock AS stock_actual,
                    b.nombre AS bodega_nombre,
                    p.supplier_id
                FROM productos p
                JOIN bodegas b ON p.supplier_id = b.supplier_id
                WHERE p.activo = 1
                AND EXISTS (
                    SELECT 1 FROM historial_stock h
                    WHERE h.product_id_dropi = p.product_id_dropi
                    AND h.supplier_id = p.supplier_id
                )
                ORDER BY b.nombre, p.nombre
            """
            rows = conn.execute(sql).fetchall()
        else:
            sql = """
                SELECT DISTINCT
                    p.product_id_dropi,
                    p.nombre,
                    p.sku,
                    p.imagen,
                    p.stock AS stock_actual,
                    b.nombre AS bodega_nombre,
                    p.supplier_id
                FROM productos p
                JOIN bodegas b ON p.supplier_id = b.supplier_id
                WHERE p.activo = 1
                AND EXISTS (
                    SELECT 1 FROM historial_stock h
                    WHERE h.product_id_dropi = p.product_id_dropi
                    AND h.supplier_id = p.supplier_id
                )
                ORDER BY b.nombre, p.nombre
                LIMIT ? OFFSET ?
            """
            rows = conn.execute(sql, (limit, offset)).fetchall()
    
    result = []
    for r in rows:
        # Obtener stock anterior y cambios de forma individual (más rápido que JOIN complejo)
        hist = conn.execute(
            """
            SELECT stock FROM historial_stock
            WHERE product_id_dropi = ? AND supplier_id = ?
            ORDER BY id DESC LIMIT 2
            """,
            (r["product_id_dropi"], r["supplier_id"])
        ).fetchall()
        
        stock_anterior = r["stock_actual"]
        cantidad_cambios = 0
        
        if len(hist) >= 2:
            stock_anterior = hist[1]["stock"]
            cantidad_cambios = len(hist)
        elif len(hist) == 1:
            cantidad_cambios = 1
        
        cambio = r["stock_actual"] - stock_anterior
        
        result.append({
            "product_id_dropi": r["product_id_dropi"],
            "nombre": r["nombre"],
            "sku": r["sku"],
            "imagen": r["imagen"],
            "stock_actual": r["stock_actual"],
            "bodega_nombre": r["bodega_nombre"],
            "supplier_id": r["supplier_id"],
            "stock_anterior": stock_anterior,
            "cambio": cambio,
            "cantidad_cambios": cantidad_cambios
        })
    
    return result
