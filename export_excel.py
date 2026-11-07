"""
export_excel.py — Exportación de datos a archivos Excel (.xlsx).
"""
import io
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, date
from pathlib import Path
from typing import Callable, Optional

import pandas as pd
import requests
from PIL import Image as PILImage

import database as db

THUMB_PX = 72
CLOUDFRONT_BASE = "https://d39ru7awumhhs2.cloudfront.net/"


def _export_path(prefix: str) -> str:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = Path.home() / "Downloads" / f"{prefix}_{ts}.xlsx"
    return str(path)


def _style_worksheet(ws, df: pd.DataFrame) -> None:
    """Aplica estilo de cabecera y ajusta anchos de columna."""
    try:
        from openpyxl.styles import PatternFill, Font, Alignment
        header_fill = PatternFill(
            start_color="1B3A6B", end_color="1B3A6B", fill_type="solid"
        )
        header_font = Font(color="FFFFFF", bold=True, size=11)

        for col_idx in range(1, len(df.columns) + 1):
            cell = ws.cell(row=1, column=col_idx)
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = Alignment(horizontal="center", vertical="center")

        ws.row_dimensions[1].height = 22

        for col in ws.columns:
            max_len = max(
                (len(str(cell.value or "")) for cell in col), default=0
            )
            ws.column_dimensions[col[0].column_letter].width = min(max_len + 4, 55)
    except Exception:
        pass


def _normalize_image_url(url: str) -> str:
    if not url:
        return ""
    url = str(url).strip()
    if not url or url.lower() in ("none", "nan"):
        return ""
    if url.startswith("http://") or url.startswith("https://"):
        return url
    if url.startswith("/"):
        url = url[1:]
    return CLOUDFRONT_BASE + url


def _fetch_thumbnail(url: str) -> Optional[io.BytesIO]:
    try:
        resp = requests.get(url, timeout=8)
        resp.raise_for_status()
        im = PILImage.open(io.BytesIO(resp.content)).convert("RGB")
        im.thumbnail((THUMB_PX, THUMB_PX), PILImage.LANCZOS)
        buf = io.BytesIO()
        buf.name = "thumb.png"
        im.save(buf, format="PNG")
        buf.seek(0)
        return buf
    except Exception:
        return None


def _download_thumbnails(
    urls: list[str],
    progress_cb: Optional[Callable[[str], None]] = None,
) -> dict[str, io.BytesIO]:
    unique = [u for u in dict.fromkeys(urls) if u]
    if not unique:
        return {}

    thumbs: dict[str, io.BytesIO] = {}
    done = 0
    total = len(unique)
    if progress_cb:
        progress_cb(f"Descargando {total} imágenes…")

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(_fetch_thumbnail, url): url for url in unique}
        for fut in as_completed(futures):
            url = futures[fut]
            buf = fut.result()
            if buf is not None:
                thumbs[url] = buf
            done += 1
            if progress_cb and done % 40 == 0:
                progress_cb(f"Imágenes {done}/{total}")

    if progress_cb:
        progress_cb(f"Imágenes listas: {len(thumbs)}/{total}")
    return thumbs


def _embed_images_in_column(
    ws,
    urls: list[str],
    thumbs: dict[str, io.BytesIO],
    col_letter: str = "A",
) -> list:
    """Pega miniaturas en la columna indicada. Devuelve buffers para no cerrarlos antes del save."""
    from openpyxl.drawing.image import Image as XLImage

    kept = []
    ws.column_dimensions[col_letter].width = 12
    for i, url in enumerate(urls):
        excel_row = i + 2
        ws.row_dimensions[excel_row].height = 54
        buf = thumbs.get(url)
        if buf is None:
            continue
        buf.seek(0)
        img = XLImage(buf)
        img.width = THUMB_PX
        img.height = THUMB_PX
        ws.add_image(img, f"{col_letter}{excel_row}")
        kept.append(buf)
    return kept


# ──────────────── INVENTARIO ────────────────────────────────────────────────

def exportar_inventario(
    supplier_id: int = None,
    horas: Optional[int] = None,
    filtro: str = "todos",
    busqueda: str = "",
    orden: str = "Cambios (desc)",
    progress_cb: Optional[Callable[[str], None]] = None,
) -> str:
    """
    Exporta el inventario como se ve en el Dashboard:
    imagen del producto, stock actual, stock anterior y ventas.
    """
    with db.get_connection() as conn:
        query = """
            SELECT
                p.product_id_dropi,
                p.nombre,
                p.sku,
                p.stock,
                p.precio,
                p.imagen,
                b.nombre AS bodega,
                p.supplier_id,
                p.ultima_actualizacion
            FROM productos p
            JOIN bodegas b ON p.supplier_id = b.supplier_id
            WHERE p.activo = 1
        """
        params = []
        if supplier_id:
            query += " AND p.supplier_id = ?"
            params.append(supplier_id)
        query += " ORDER BY b.nombre, p.nombre"
        productos = [dict(r) for r in conn.execute(query, params).fetchall()]

    stock_por_bodega: dict[int, dict] = {}
    if supplier_id:
        stock_por_bodega[supplier_id] = db.get_stock_por_periodo(supplier_id, horas)
    else:
        for sid in {p["supplier_id"] for p in productos}:
            stock_por_bodega[sid] = db.get_stock_por_periodo(sid, horas)

    rows = []
    for p in productos:
        stock_actual = int(p.get("stock") or 0)
        prev_map = stock_por_bodega.get(p["supplier_id"], {})
        stock_ant = prev_map.get(p["product_id_dropi"])
        if stock_ant is None:
            stock_ant_val = stock_actual
        else:
            stock_ant_val = int(stock_ant)
        ventas = max(0, stock_ant_val - stock_actual)
        reposicion = max(0, stock_actual - stock_ant_val)
        rows.append({
            "product_id_dropi": p["product_id_dropi"],
            "imagen_url": _normalize_image_url(p.get("imagen") or ""),
            "nombre": p.get("nombre") or "",
            "sku": p.get("sku") or "",
            "stock_actual": stock_actual,
            "stock_anterior": stock_ant_val,
            "stock_ant_raw": stock_ant,
            "ventas": ventas,
            "reposicion": reposicion,
            "cambio": stock_actual - stock_ant_val,
            "precio": p.get("precio") or 0,
            "bodega": p.get("bodega") or "",
        })

    q = (busqueda or "").strip().lower()
    if q:
        rows = [
            r for r in rows
            if q in r["nombre"].lower() or q in r["sku"].lower()
        ]

    filtro = (filtro or "todos").strip().lower()
    if filtro == "bajaron":
        rows = [r for r in rows if r["stock_ant_raw"] is not None and r["stock_ant_raw"] > r["stock_actual"]]
    elif filtro == "subieron":
        rows = [r for r in rows if r["stock_ant_raw"] is not None and r["stock_ant_raw"] < r["stock_actual"]]

    if orden == "Cambios (desc)":
        rows.sort(key=lambda r: abs(r["cambio"]), reverse=True)
    elif orden == "Cambios (asc)":
        rows.sort(key=lambda r: abs(r["cambio"]))
    elif orden == "Nombre (A-Z)":
        rows.sort(key=lambda r: r["nombre"].lower())
    elif orden == "Nombre (Z-A)":
        rows.sort(key=lambda r: r["nombre"].lower(), reverse=True)
    elif orden == "Stock (desc)":
        rows.sort(key=lambda r: r["stock_actual"], reverse=True)
    elif orden == "Stock (asc)":
        rows.sort(key=lambda r: r["stock_actual"])

    df = pd.DataFrame([{
        "Imagen": "",
        "Producto": r["nombre"],
        "SKU": r["sku"],
        "Stock actual": r["stock_actual"],
        "Stock anterior": r["stock_anterior"],
        "Ventas": r["ventas"],
        "Reposición": r["reposicion"],
        "Precio": r["precio"],
        "Bodega": r["bodega"],
        "URL Imagen": r["imagen_url"],
    } for r in rows])

    if df.empty:
        df = pd.DataFrame(columns=[
            "Imagen", "Producto", "SKU", "Stock actual", "Stock anterior",
            "Ventas", "Reposición", "Precio", "Bodega", "URL Imagen",
        ])

    urls = [r["imagen_url"] for r in rows]
    thumbs = _download_thumbnails(urls, progress_cb)

    path = _export_path("inventario")
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="Inventario", index=False)
        ws = writer.sheets["Inventario"]
        _style_worksheet(ws, df)
        buffers = _embed_images_in_column(ws, urls, thumbs, "A")
        ws.freeze_panes = "A2"
        ws.auto_filter.ref = ws.dimensions
        writer.book._dropi_image_buffers = buffers
    return path


# ──────────────── HISTORIAL ─────────────────────────────────────────────────

def exportar_historial(supplier_id: int = None, limit: int = 10_000) -> str:
    with db.get_connection() as conn:
        query = f"""
            SELECT
                h.fecha_hora       AS 'Fecha y Hora',
                p.nombre           AS Producto,
                p.sku              AS SKU,
                b.nombre           AS Bodega,
                h.stock            AS Stock
            FROM historial_stock h
            JOIN productos p
                ON h.product_id_dropi = p.product_id_dropi
               AND h.supplier_id      = p.supplier_id
            JOIN bodegas b ON h.supplier_id = b.supplier_id
            {'WHERE h.supplier_id = ?' if supplier_id else ''}
            ORDER BY h.fecha_hora DESC
            LIMIT {limit}
        """
        params = [supplier_id] if supplier_id else []
        df = pd.read_sql_query(query, conn, params=params)

    path = _export_path("historial")
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="Historial", index=False)
        _style_worksheet(writer.sheets["Historial"], df)
    return path


# ──────────────── PRODUCTOS CALIENTES ───────────────────────────────────────

def exportar_calientes(
    horas: int = 24,
    progress_cb: Optional[Callable[[str], None]] = None,
) -> str:
    data = db.get_productos_calientes(horas)
    rows = []
    for item in data:
        stock_actual = int(item.get("stock_actual") or 0)
        stock_ant = item.get("stock_anterior")
        stock_ant_val = int(stock_ant) if stock_ant is not None else stock_actual
        ventas = int(item.get("ventas_estimadas") or max(0, stock_ant_val - stock_actual))
        rows.append({
            "imagen_url": _normalize_image_url(item.get("imagen") or ""),
            "nombre": item.get("nombre") or "",
            "sku": item.get("sku") or "",
            "stock_actual": stock_actual,
            "stock_anterior": stock_ant_val,
            "ventas": ventas,
            "bodega": item.get("bodega_nombre") or "",
        })

    df = pd.DataFrame([{
        "Imagen": "",
        "Producto": r["nombre"],
        "SKU": r["sku"],
        "Stock actual": r["stock_actual"],
        "Stock anterior": r["stock_anterior"],
        "Ventas": r["ventas"],
        "Bodega": r["bodega"],
        "URL Imagen": r["imagen_url"],
    } for r in rows])

    if df.empty:
        df = pd.DataFrame(columns=[
            "Imagen", "Producto", "SKU", "Stock actual", "Stock anterior",
            "Ventas", "Bodega", "URL Imagen",
        ])

    urls = [r["imagen_url"] for r in rows]
    thumbs = _download_thumbnails(urls, progress_cb)

    path = _export_path("productos_calientes")
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="Más Vendidos", index=False)
        ws = writer.sheets["Más Vendidos"]
        _style_worksheet(ws, df)
        buffers = _embed_images_in_column(ws, urls, thumbs, "A")
        ws.freeze_panes = "A2"
        ws.auto_filter.ref = ws.dimensions
        writer.book._dropi_image_buffers = buffers
    return path


# ──────────────── REPORTE COMPLETO ──────────────────────────────────────────

def exportar_completo() -> str:
    path = _export_path("dropi_tracker_reporte")

    with db.get_connection() as conn:
        df_inv = pd.read_sql_query(
            """SELECT p.product_id_dropi AS ID, p.nombre AS Producto, p.sku AS SKU,
                      p.categoria AS Categoría, p.stock AS Stock,
                      p.precio AS Precio, p.precio_oferta AS 'Precio Oferta',
                      p.imagen AS 'URL Imagen', b.nombre AS Bodega, 
                      p.ultima_actualizacion AS 'Última Actualización'
               FROM productos p JOIN bodegas b ON p.supplier_id = b.supplier_id
               WHERE p.activo=1 ORDER BY b.nombre, p.nombre""",
            conn,
        )
        df_hist = pd.read_sql_query(
            """SELECT h.fecha_hora AS 'Fecha y Hora', p.nombre AS Producto,
                      b.nombre AS Bodega, h.stock AS Stock
               FROM historial_stock h
               JOIN productos p ON h.product_id_dropi=p.product_id_dropi
                                AND h.supplier_id=p.supplier_id
               JOIN bodegas b ON h.supplier_id=b.supplier_id
               ORDER BY h.fecha_hora DESC LIMIT 10000""",
            conn,
        )

    data_cal = db.get_productos_calientes(24)
    df_cal = pd.DataFrame(data_cal)
    if not df_cal.empty:
        df_cal = df_cal[["nombre", "bodega_nombre", "stock_actual", "ventas_estimadas"]].copy()
        df_cal.columns = ["Producto", "Bodega", "Stock Actual", "Ventas Estimadas"]

    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        df_inv.to_excel(writer, sheet_name="Inventario", index=False)
        _style_worksheet(writer.sheets["Inventario"], df_inv)

        df_hist.to_excel(writer, sheet_name="Historial", index=False)
        _style_worksheet(writer.sheets["Historial"], df_hist)

        if not df_cal.empty:
            df_cal.to_excel(writer, sheet_name="Más Vendidos", index=False)
            _style_worksheet(writer.sheets["Más Vendidos"], df_cal)

    return path


# ──────────────── BODEGAS/PROVEEDORES ──────────────────────────────────────────

def exportar_bodegas() -> str:
    """Exporta proveedores y sus bodegas a Excel con conteo de productos, órdenes y dropshippers."""
    with db.get_connection() as conn:
        query = """
            SELECT
                s.store_name      AS 'Empresa',
                s.owner_name      AS 'Propietario',
                s.phone           AS 'Celular Empresa',
                s.store_url       AS 'URL Tienda',
                w.warehouse_name  AS 'Bodega',
                w.address         AS 'Dirección',
                w.city_name       AS 'Ciudad',
                w.phone           AS 'Celular Bodega',
                w.zip_code        AS 'Código Postal',
                s.products_count  AS 'Cantidad Productos',
                w.ordenes         AS 'Órdenes',
                w.dropshippers    AS 'Dropshippers',
                s.supplier_id     AS 'Supplier ID',
                w.warehouse_id    AS 'Warehouse ID'
            FROM suppliers s
            LEFT JOIN warehouses w ON s.supplier_id = w.supplier_id
            ORDER BY s.store_name, w.warehouse_name
        """
        df = pd.read_sql_query(query, conn)

    path = _export_path("bodegas_proveedores")
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="Bodegas", index=False)
        _style_worksheet(writer.sheets["Bodegas"], df)
    return path


# ──────────────── REPORTE HISTÓRICO DE PRODUCTOS ─────────────────────────────

def exportar_reporte_historico() -> str:
    """
    Genera un reporte histórico de productos con formato de cortes por fecha.
    
    Cada producto ocupa una sola fila. Las columnas son:
    - URL Imagen, Nombre del Producto, Valor del Producto, Nombre de la Bodega
    - Una columna por cada corte (fecha) con las ventas de ese producto en esa fecha
    
    Las ventas se calculan como la diferencia de stock entre registros consecutivos.
    """
    with db.get_connection() as conn:
        # Obtener todos los productos activos con supplier_id
        query_productos = """
            SELECT
                p.product_id_dropi AS ID,
                p.imagen AS 'URL Imagen',
                p.nombre AS 'Nombre del Producto',
                p.precio AS 'Valor del Producto',
                b.nombre AS 'Nombre de la Bodega',
                p.supplier_id
            FROM productos p
            JOIN bodegas b ON p.supplier_id = b.supplier_id
            WHERE p.activo = 1
            ORDER BY p.product_id_dropi
        """
        df_productos = pd.read_sql_query(query_productos, conn)
        
        # Obtener todo el historial de stock
        query_historial = """
            SELECT
                h.product_id_dropi,
                h.supplier_id,
                DATE(h.fecha_hora) AS fecha,
                h.stock
            FROM historial_stock h
            ORDER BY h.product_id_dropi, h.supplier_id, h.fecha_hora
        """
        df_historial = pd.read_sql_query(query_historial, conn)
    
    if df_historial.empty:
        # Si no hay historial, exportar solo productos sin columnas de corte
        path = _export_path("reporte_historico_productos")
        with pd.ExcelWriter(path, engine="openpyxl") as writer:
            df_productos.to_excel(writer, sheet_name="Reporte Histórico", index=False)
            _style_worksheet(writer.sheets["Reporte Histórico"], df_productos)
        return path
    
    print(f"Total productos: {len(df_productos)}")
    print(f"Total registros historial: {len(df_historial)}")
    
    # Agrupar historial por producto y fecha, tomando el stock más reciente del día
    df_historial_grouped = df_historial.groupby(
        ['product_id_dropi', 'supplier_id', 'fecha']
    )['stock'].last().reset_index()
    
    # Convertir fechas de string a objetos de fecha
    df_historial_grouped['fecha'] = pd.to_datetime(df_historial_grouped['fecha']).dt.date
    
    # Obtener todas las fechas únicas ordenadas
    fechas_unicas = sorted(df_historial_grouped['fecha'].unique())
    
    print(f"Fechas únicas: {len(fechas_unicas)}")
    print(f"Fechas: {fechas_unicas}")
    
    # Crear columnas de cortes
    columnas_cortes = [f"Corte {i+1} ({fecha.strftime('%Y-%m-%d')})" 
                       for i, fecha in enumerate(fechas_unicas)]
    
    # Crear DataFrame final
    df_reporte = df_productos.copy()
    
    # Eliminar columna supplier_id (no debe aparecer en el reporte)
    if 'supplier_id' in df_reporte.columns:
        df_reporte = df_reporte.drop(columns=['supplier_id'])
    
    # Agregar columnas de cortes
    for col in columnas_cortes:
        df_reporte[col] = 0  # Inicializar con 0 en lugar de None
    
    # Calcular ventas para cada producto
    debug_count = 0
    for idx, row in df_reporte.iterrows():
        product_id = row['ID']
        
        # Filtrar historial de este producto
        hist_producto = df_historial_grouped[
            df_historial_grouped['product_id_dropi'] == product_id
        ].sort_values('fecha')
        
        if hist_producto.empty:
            continue
        
        # Depuración para los primeros 3 productos
        if debug_count < 3:
            print(f"\n--- Producto {debug_count + 1}: {product_id} ---")
            print(f"Nombre: {row['Nombre del Producto']}")
            print(f"Registros de historial: {len(hist_producto)}")
        
        # Calcular ventas por fecha
        stock_anterior = None
        stock_por_fecha = {}  # Mapeo de fecha a stock para este producto
        
        # Primero, guardar todos los stocks por fecha
        for _, hist_row in hist_producto.iterrows():
            fecha = hist_row['fecha']
            stock = hist_row['stock']
            stock_por_fecha[fecha] = stock
        
        # Luego, calcular ventas para cada fecha en orden
        for fecha in fechas_unicas:
            if fecha in stock_por_fecha:
                stock_actual = stock_por_fecha[fecha]
                
                # Depuración para los primeros 3 productos
                if debug_count < 3:
                    print(f"  Fecha: {fecha}, Stock: {stock_actual}")
                    if stock_anterior is not None:
                        ventas = max(0, stock_anterior - stock_actual)
                        print(f"    Stock anterior: {stock_anterior}, Ventas calculadas: {ventas}")
                
                # Calcular ventas y asignar a columna correspondiente
                if stock_anterior is not None:
                    ventas = max(0, stock_anterior - stock_actual)
                    # Encontrar columna correspondiente
                    for i, fecha_col in enumerate(fechas_unicas):
                        if fecha == fecha_col:
                            col_name = columnas_cortes[i]
                            df_reporte.at[idx, col_name] = ventas
                            break
                
                stock_anterior = stock_actual
        
        debug_count += 1
    
    # Verificación de datos
    print(f"\n--- Verificación de datos ---")
    print(f"Total productos en reporte: {len(df_reporte)}")
    
    # Contar celdas con valores > 0
    celdas_con_ventas = 0
    for col in columnas_cortes:
        celdas_con_ventas += (df_reporte[col] > 0).sum()
    
    print(f"Celdas con ventas > 0: {celdas_con_ventas}")
    
    # Mostrar ejemplo
    if not df_reporte.empty:
        print(f"\n--- Ejemplo primer producto ---")
        print(f"ID: {df_reporte.iloc[0]['ID']}")
        print(f"Nombre: {df_reporte.iloc[0]['Nombre del Producto']}")
        for col in columnas_cortes[:5]:
            print(f"{col}: {df_reporte.iloc[0][col]}")
    
    # Exportar a Excel
    path = _export_path("reporte_historico_productos")
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        df_reporte.to_excel(writer, sheet_name="Reporte Histórico", index=False)
        _style_worksheet(writer.sheets["Reporte Histórico"], df_reporte)
    
    return path
