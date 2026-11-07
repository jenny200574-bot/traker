"""
test_suite.py — Pruebas unitarias para Dropi Tracker
Ejecutar desde la carpeta dropi_tracker:
    python -m pytest test_suite.py -v
o:
    python test_suite.py
"""
import os
import sys
import queue
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from datetime import datetime
from unittest.mock import patch, MagicMock

# ── Asegurar que los módulos del proyecto sean importables ───────────────────
sys.path.insert(0, str(Path(__file__).parent))

import database as db
import api_dropi as api
import settings as cfg
import export_excel as exporter
import scheduler
from models import Bodega, Producto, LogActividad

# ── Redirigir la base de datos a un fichero temporal ────────────────────────
TEMP_DB_DIR = Path(__file__).parent / "test_scratch"
TEMP_DB_DIR.mkdir(exist_ok=True)
db.DB_PATH = TEMP_DB_DIR / "test_dropi_tracker.db"


# ═════════════════════════════════════════════════════════════════════════════
#  TESTS DE BASE DE DATOS
# ═════════════════════════════════════════════════════════════════════════════

class TestDatabase(unittest.TestCase):

    def setUp(self):
        if db.DB_PATH.exists():
            db.DB_PATH.unlink()
        db.init_db()

    def tearDown(self):
        if db.DB_PATH.exists():
            db.DB_PATH.unlink()

    def test_init_db(self):
        self.assertTrue(db.DB_PATH.exists())
        with db.get_connection() as conn:
            tables = [r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()]
        self.assertIn("bodegas", tables)
        self.assertIn("productos", tables)
        self.assertIn("historial_stock", tables)
        self.assertIn("log_actividad", tables)

    def test_bodegas_crud(self):
        b1 = Bodega(nombre="Bodega Test 1", url="https://example.com/1", supplier_id=12345)
        b2 = Bodega(nombre="Bodega Test 2", url="https://example.com/2", supplier_id=67890)

        self.assertTrue(db.add_bodega(b1))
        self.assertTrue(db.add_bodega(b2))

        # Duplicate supplier_id → False
        b_dup = Bodega(nombre="Bodega Duplicate", url="", supplier_id=12345)
        self.assertFalse(db.add_bodega(b_dup))

        bodegas = db.get_bodegas()
        self.assertEqual(len(bodegas), 2)

        # Update
        b_upd = Bodega(id=bodegas[0].id, nombre="Bodega Test 1 Modificada",
                       url="https://example.com/mod", supplier_id=12345)
        db.update_bodega(b_upd)
        bodegas_after = db.get_bodegas()
        self.assertEqual(bodegas_after[0].nombre, "Bodega Test 1 Modificada")

        # Delete
        db.delete_bodega(bodegas_after[0].id)
        bodegas_final = db.get_bodegas()
        self.assertEqual(len(bodegas_final), 1)
        self.assertEqual(bodegas_final[0].supplier_id, 67890)

    def test_productos_and_historial(self):
        p = Producto(
            product_id_dropi=999,
            supplier_id=12345,
            nombre="Producto Test",
            sku="SKU-TEST-123",
            imagen="https://example.com/img.png",
            precio=15000.0,
            precio_oferta=12000.0,
            stock=50,
            categoria="Test Cat"
        )
        db.upsert_producto(p)

        products = db.get_productos_bodega(12345)
        self.assertEqual(len(products), 1)
        self.assertEqual(products[0]["nombre"], "Producto Test")
        self.assertEqual(products[0]["activo"], 1)

        db.insertar_historial(999, 12345, 50)
        db.insertar_historial(999, 12345, 45)

        hist = db.get_historial_producto(999, 12345)
        self.assertEqual(len(hist), 2)
        self.assertEqual(hist[0]["stock"], 50)
        self.assertEqual(hist[1]["stock"], 45)

        stock_24h = db.get_stock_hace_24h(12345)
        self.assertIn(999, stock_24h)

        # Marcar inactivo producto que no está en la lista
        db.marcar_inactivos(12345, [111])
        products_after = db.get_productos_bodega(12345)
        self.assertEqual(len(products_after), 0)

    def test_save_productos_lote(self):
        p1 = Producto(product_id_dropi=101, supplier_id=5555, nombre="Prod A", stock=20)
        p2 = Producto(product_id_dropi=102, supplier_id=5555, nombre="Prod B", stock=30)

        # Primera descarga
        db.save_productos_lote([p1, p2])
        products = db.get_productos_bodega(5555)
        self.assertEqual(len(products), 2)

        hist1 = db.get_historial_producto(101, 5555)
        self.assertEqual(len(hist1), 1)
        self.assertEqual(hist1[0]["stock"], 20)

        # Segunda descarga — p2 desaparece → queda inactivo
        p1_mod = Producto(product_id_dropi=101, supplier_id=5555,
                          nombre="Prod A Modificado", stock=15)
        db.save_productos_lote([p1_mod])

        products_after = db.get_productos_bodega(5555)
        self.assertEqual(len(products_after), 1)
        self.assertEqual(products_after[0]["nombre"], "Prod A Modificado")
        self.assertEqual(products_after[0]["stock"], 15)

        with db.get_connection() as conn:
            p102 = conn.execute(
                "SELECT activo FROM productos WHERE product_id_dropi=102"
            ).fetchone()
        self.assertEqual(p102["activo"], 0)

        # Log Actividad
        log = LogActividad(
            fecha="2026-06-27", hora="18:00",
            bodega="Bodega Test", productos_descargados=15, errores=""
        )
        db.insertar_log(log)
        logs = db.get_log_actividad()
        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0]["bodega"], "Bodega Test")
        self.assertEqual(logs[0]["productos_descargados"], 15)

    def test_save_productos_lote_large(self):
        """Verifica que save_productos_lote funciona con 1500 productos (base grande)."""
        productos = [
            Producto(product_id_dropi=i, supplier_id=9999,
                     nombre=f"Prod {i}", stock=i % 100)
            for i in range(1, 1501)
        ]
        db.save_productos_lote(productos)
        saved = db.get_productos_bodega(9999)
        self.assertEqual(len(saved), 1500)

        # Segunda pasada con menos productos — los restantes quedan inactivos
        db.save_productos_lote(productos[:500])
        saved_after = db.get_productos_bodega(9999)
        self.assertEqual(len(saved_after), 500)


# ═════════════════════════════════════════════════════════════════════════════
#  TESTS DE API
# ═════════════════════════════════════════════════════════════════════════════

class TestAPI(unittest.TestCase):

    def test_normalize_token(self):
        self.assertEqual(api._normalize_token("xyz"), "Bearer xyz")
        self.assertEqual(api._normalize_token("Bearer abc"), "Bearer abc")
        self.assertEqual(api._normalize_token(" bearer abc "), "Bearer abc")

    def test_extraer_supplier_id(self):
        urls = [
            ("https://app.dropi.co/provider/c/12345", 12345),
            ("https://app.dropi.co/provider/9876", 9876),
            ("https://app.dropi.co/products?supplier_id=555", 555),
            ("https://app.dropi.co/products?foo=bar&supplier_id=111", 111),
            ("https://example.com/no-supplier", None),
        ]
        for url, expected in urls:
            self.assertEqual(api.extraer_supplier_id(url), expected, msg=url)

    # ── Tests de normalización de imagen ────────────────────────────────────

    def test_normalize_image_url_relative(self):
        """URL relativa → prefijo CloudFront."""
        result = api._normalize_image_url("colombia/products/1/img.jpeg")
        self.assertEqual(result,
                         "https://d39ru7awumhhs2.cloudfront.net/colombia/products/1/img.jpeg")

    def test_normalize_image_url_leading_slash(self):
        """URL relativa con / inicial → prefijo CloudFront sin doble /."""
        result = api._normalize_image_url("/colombia/products/2/img.jpeg")
        self.assertEqual(result,
                         "https://d39ru7awumhhs2.cloudfront.net/colombia/products/2/img.jpeg")

    def test_normalize_image_url_absolute_https(self):
        """URL absoluta HTTPS → sin cambios."""
        url = "https://example.com/custom.png"
        self.assertEqual(api._normalize_image_url(url), url)

    def test_normalize_image_url_absolute_http(self):
        """URL absoluta HTTP → sin cambios."""
        url = "http://cdn.example.com/img.jpg"
        self.assertEqual(api._normalize_image_url(url), url)

    def test_normalize_image_url_empty(self):
        """URL vacía → cadena vacía."""
        self.assertEqual(api._normalize_image_url(""), "")

    # ── Tests de _parse_producto ─────────────────────────────────────────────

    def test_parse_producto_image_relative(self):
        item = {"id": 1, "name": "Test", "images": [{"url": "colombia/products/1/img.jpeg"}]}
        prod = api._parse_producto(item, 100)
        self.assertEqual(prod.imagen,
                         "https://d39ru7awumhhs2.cloudfront.net/colombia/products/1/img.jpeg")

    def test_parse_producto_image_leading_slash(self):
        item = {"id": 2, "name": "Test", "images": [{"url": "/colombia/products/2/img.jpeg"}]}
        prod = api._parse_producto(item, 100)
        self.assertEqual(prod.imagen,
                         "https://d39ru7awumhhs2.cloudfront.net/colombia/products/2/img.jpeg")

    def test_parse_producto_image_absolute(self):
        item = {"id": 3, "name": "Test", "images": [{"url": "https://example.com/custom.png"}]}
        prod = api._parse_producto(item, 100)
        self.assertEqual(prod.imagen, "https://example.com/custom.png")

    def test_parse_producto_image_fallback_field(self):
        """Si no hay 'images', cae en campo 'image'."""
        item = {"id": 4, "name": "Test", "image": "products/test.jpg"}
        prod = api._parse_producto(item, 100)
        self.assertEqual(prod.imagen,
                         "https://d39ru7awumhhs2.cloudfront.net/products/test.jpg")

    def test_parse_producto_image_empty(self):
        """Sin imágenes → cadena vacía."""
        item = {"id": 5, "name": "Test"}
        prod = api._parse_producto(item, 100)
        self.assertEqual(prod.imagen, "")

    # ── Tests de extracción de stock ─────────────────────────────────────────

    def test_extract_stock_from_stock_field(self):
        item = {"id": 10, "name": "P", "stock": 42}
        prod = api._parse_producto(item, 1)
        self.assertEqual(prod.stock, 42)

    def test_extract_stock_from_quantity(self):
        item = {"id": 11, "name": "P", "quantity": 15}
        prod = api._parse_producto(item, 1)
        self.assertEqual(prod.stock, 15)

    def test_extract_stock_from_stock_available(self):
        item = {"id": 12, "name": "P", "stock_available": 7}
        prod = api._parse_producto(item, 1)
        self.assertEqual(prod.stock, 7)

    def test_extract_stock_from_variants(self):
        item = {"id": 13, "name": "P",
                "variants": [
                    {"stock": 5},
                    {"stock": 10},
                ]}
        prod = api._parse_producto(item, 1)
        self.assertEqual(prod.stock, 15)

    def test_extract_stock_zero_when_missing(self):
        item = {"id": 14, "name": "P"}
        prod = api._parse_producto(item, 1)
        self.assertEqual(prod.stock, 0)

    # ── Test de cancelación con stop_event ──────────────────────────────────

    def test_descargar_bodega_stop_event(self):
        """stop_event activo → no hace ninguna petición y retorna lista vacía."""
        stop = threading.Event()
        stop.set()  # Pre-cancelar

        messages = []

        with patch("api_dropi.requests.Session") as mock_session_cls:
            result = api.descargar_bodega(
                token="fake-token",
                supplier_id=12345,
                callback=messages.append,
                stop_event=stop,
            )

        # Con stop_event activado antes de iniciar, no debe haber llamadas POST
        mock_session_cls.return_value.__enter__.return_value.post.assert_not_called()
        self.assertEqual(result, [])
        self.assertTrue(any("cancel" in m.lower() or "cancelad" in m.lower()
                            for m in messages))

    def test_descargar_bodega_empty_token_raises(self):
        with self.assertRaises(ValueError):
            api.descargar_bodega("", 12345)

    def test_descargar_bodega_invalid_supplier_raises(self):
        with self.assertRaises(ValueError):
            api.descargar_bodega("valid-token", 0)

    @patch("api_dropi._fetch_page")
    def test_descargar_bodega_early_stop_when_target_reached(self, mock_fetch):
        """Si se alcanza la meta de productos_esperados, debe detenerse antes de las 5 pasadas."""
        mock_fetch.side_effect = [
            [{"id": 101, "name": "Prod A"}, {"id": 102, "name": "Prod B"}],  # Run 1, Pag 1
            [],  # Run 1, Pag 2 (Fin de scroll)
        ]
        
        result = api.descargar_bodega(
            token="valid-token",
            supplier_id=12345,
            productos_esperados=2
        )
        self.assertEqual(len(result), 2)
        self.assertEqual(mock_fetch.call_count, 1)

    @patch("api_dropi._fetch_page")
    def test_descargar_bodega_completes_5_runs_if_target_not_reached(self, mock_fetch):
        """Si no se alcanza la meta, debe completar las 5 pasadas aun si alguna pasada no añade productos nuevos."""
        mock_fetch.side_effect = [
            [{"id": 101, "name": "Prod A"}], [],  # Run 1
            [{"id": 101, "name": "Prod A"}], [],  # Run 2
            [{"id": 101, "name": "Prod A"}], [],  # Run 3
            [{"id": 101, "name": "Prod A"}], [],  # Run 4
            [{"id": 101, "name": "Prod A"}], [],  # Run 5
        ]
        
        result = api.descargar_bodega(
            token="valid-token",
            supplier_id=12345,
            productos_esperados=5  # Meta no alcanzada (solo tenemos 1 único)
        )
        self.assertEqual(mock_fetch.call_count, 10)
        self.assertEqual(len(result), 1)


# ═════════════════════════════════════════════════════════════════════════════
#  TESTS DE EXPORTACIÓN
# ═════════════════════════════════════════════════════════════════════════════

class TestExporter(unittest.TestCase):

    def setUp(self):
        if db.DB_PATH.exists():
            db.DB_PATH.unlink()
        db.init_db()
        # Guardar función original y parchear para escribir en directorio temporal
        self._orig_export_path = exporter._export_path
        exporter._export_path = lambda prefix: str(TEMP_DB_DIR / f"test_{prefix}.xlsx")

    def tearDown(self):
        exporter._export_path = self._orig_export_path
        if db.DB_PATH.exists():
            db.DB_PATH.unlink()
        for f in TEMP_DB_DIR.glob("*.xlsx"):
            try:
                f.unlink()
            except Exception:
                pass

    def test_exportar_inventario(self):
        db.add_bodega(Bodega(nombre="Bodega Export", url="", supplier_id=987))
        db.upsert_producto(Producto(
            product_id_dropi=11, supplier_id=987, nombre="Export Prod", stock=10
        ))
        path = exporter.exportar_inventario()
        self.assertTrue(os.path.exists(path))
        self.assertTrue(path.endswith(".xlsx"))

    def test_exportar_historial(self):
        db.add_bodega(Bodega(nombre="Bodega Export", url="", supplier_id=987))
        db.upsert_producto(Producto(
            product_id_dropi=11, supplier_id=987, nombre="Export Prod", stock=10
        ))
        db.insertar_historial(11, 987, 10)
        path = exporter.exportar_historial()
        self.assertTrue(os.path.exists(path))

    def test_exportar_calientes(self):
        path = exporter.exportar_calientes()
        self.assertTrue(os.path.exists(path))

    def test_exportar_completo(self):
        db.add_bodega(Bodega(nombre="Bodega Export", url="", supplier_id=987))
        db.upsert_producto(Producto(
            product_id_dropi=11, supplier_id=987, nombre="Export Prod", stock=10
        ))
        db.insertar_historial(11, 987, 10)
        path = exporter.exportar_completo()
        self.assertTrue(os.path.exists(path))

    def test_exportar_inventario_por_supplier(self):
        db.add_bodega(Bodega(nombre="B1", url="", supplier_id=111))
        db.add_bodega(Bodega(nombre="B2", url="", supplier_id=222))
        db.upsert_producto(Producto(product_id_dropi=1, supplier_id=111, nombre="P1", stock=5))
        db.upsert_producto(Producto(product_id_dropi=2, supplier_id=222, nombre="P2", stock=8))
        path = exporter.exportar_inventario(supplier_id=111)
        self.assertTrue(os.path.exists(path))


# ═════════════════════════════════════════════════════════════════════════════
#  TESTS DEL SCHEDULER
# ═════════════════════════════════════════════════════════════════════════════

class TestScheduler(unittest.TestCase):

    def test_scheduler_lifecycle(self):
        triggered = False

        def trigger_cb():
            nonlocal triggered
            triggered = True

        sched = scheduler.DropiScheduler(on_trigger=trigger_cb)

        sched.update_schedule({"08:00": True, "12:00": False})
        active = sched.get_active_times()
        self.assertEqual(active, ["08:00"])

        sched.update_schedule({"18:00": True, "00:00": True})
        active = sched.get_active_times()
        self.assertIn("18:00", active)
        self.assertIn("00:00", active)
        self.assertEqual(len(active), 2)

        sched.shutdown()


# ═════════════════════════════════════════════════════════════════════════════
#  TEST DE INICIO DE LA APP
# ═════════════════════════════════════════════════════════════════════════════

class TestAppInit(unittest.TestCase):

    def setUp(self):
        if db.DB_PATH.exists():
            db.DB_PATH.unlink()
        db.init_db()
        db.add_bodega(Bodega(nombre="Bodega App Test",
                             url="https://example.com/app", supplier_id=99999))

    def tearDown(self):
        if db.DB_PATH.exists():
            db.DB_PATH.unlink()

    def test_app_startup(self):
        from app import DropiTrackerApp
        try:
            app = DropiTrackerApp()

            # Verificar que todos los tabs se crearon
            self.assertTrue(hasattr(app, "_tab_config"))
            self.assertTrue(hasattr(app, "_tab_download"))
            self.assertTrue(hasattr(app, "_tab_dashboard"))
            self.assertTrue(hasattr(app, "_tab_historial"))

            # Verificar que el botón STOP existe y está desactivado inicialmente
            self.assertTrue(hasattr(app, "_btn_stop"))
            self.assertEqual(str(app._btn_stop.cget("state")), "disabled")

            # Verificar que stop_event existe
            self.assertTrue(hasattr(app, "_stop_event"))
            self.assertIsInstance(app._stop_event, threading.Event)

            # Verificar métodos clave
            self.assertTrue(hasattr(app, "_save_horarios"))
            self.assertTrue(hasattr(app, "_stop_download"))

            app.update_idletasks()
            app.destroy()
        except Exception as exc:
            self.fail(f"DropiTrackerApp falló al inicializar: {exc}")

    def test_stop_event_cancels_flag(self):
        """Verifica que stop_event se gestiona correctamente."""
        from app import DropiTrackerApp
        try:
            app = DropiTrackerApp()
            # El stop_event debe estar limpio inicialmente
            self.assertFalse(app._stop_event.is_set())
            # Simular que se llama stop (sin descarga activa, no debería hacer nada)
            app._running_download = True
            app._stop_download()
            self.assertTrue(app._stop_event.is_set())
            app.destroy()
        except Exception as exc:
            self.fail(f"Error en test_stop_event_cancels_flag: {exc}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
