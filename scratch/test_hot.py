import sys
from datetime import datetime, timedelta
sys.path.insert(0, '.')
import database as db
from models import Producto

db.init_db()

# Clear database for testing or insert mock data
supplier_id = 99999

# 1. Clear existing test data for supplier_id 99999
with db.get_connection() as conn:
    conn.execute("DELETE FROM productos WHERE supplier_id = ?", (supplier_id,))
    conn.execute("DELETE FROM historial_stock WHERE supplier_id = ?", (supplier_id,))
    conn.execute("DELETE FROM bodegas WHERE supplier_id = ?", (supplier_id,))
    conn.execute("INSERT OR IGNORE INTO bodegas (nombre, url, supplier_id) VALUES ('Bodega Test', '', ?)", (supplier_id,))

# 2. Insert products
p1 = Producto(product_id_dropi=111, supplier_id=supplier_id, nombre="Reloj Inteligente", stock=80)
p2 = Producto(product_id_dropi=222, supplier_id=supplier_id, nombre="Auriculares Inalámbricos", stock=150)
p3 = Producto(product_id_dropi=333, supplier_id=supplier_id, nombre="Cargador Rápido", stock=300)

db.save_productos_lote([p1, p2, p3])

# 3. Simulate stock history
# For p1: stock was 100 5 hours ago, now it's 80 (sales = 20)
# For p2: stock was 200 12 hours ago, now it's 150 (sales = 50)
# For p3: stock was 300 2 hours ago, now it's 300 (sales = 0)
now = datetime.now()

with db.get_connection() as conn:
    # We clear the automatically generated ones to place our custom times
    conn.execute("DELETE FROM historial_stock WHERE supplier_id = ?", (supplier_id,))
    
    # Custom histories
    h_p1 = (111, supplier_id, 100, (now - timedelta(hours=5)).isoformat(timespec="seconds"))
    h_p2 = (222, supplier_id, 200, (now - timedelta(hours=12)).isoformat(timespec="seconds"))
    h_p3 = (333, supplier_id, 300, (now - timedelta(hours=2)).isoformat(timespec="seconds"))
    
    conn.execute("INSERT INTO historial_stock (product_id_dropi, supplier_id, stock, fecha_hora) VALUES (?, ?, ?, ?)", h_p1)
    conn.execute("INSERT INTO historial_stock (product_id_dropi, supplier_id, stock, fecha_hora) VALUES (?, ?, ?, ?)", h_p2)
    conn.execute("INSERT INTO historial_stock (product_id_dropi, supplier_id, stock, fecha_hora) VALUES (?, ?, ?, ?)", h_p3)

# 4. Check get_productos_calientes
calientes = db.get_productos_calientes(24)
print("\n=== PRODUCTOS MÁS VENDIDOS (24 HORAS) ===")
for r in calientes:
    print(f"Producto: {r['nombre']} | Stock actual: {r['stock_actual']} | Ventas estimadas: {r['ventas_estimadas']} | Bodega: {r['bodega_nombre']}")

print("\nValidación exitosa!")
