import sys
sys.path.insert(0, '.')
import database as db

with db.get_connection() as conn:
    rows = conn.execute(
        'SELECT supplier_id, COUNT(*) as total, SUM(activo) as activos, MAX(ultima_actualizacion) as ultima FROM productos GROUP BY supplier_id'
    ).fetchall()
    print('=== PRODUCTOS POR BODEGA ===')
    for r in rows:
        print(f'  supplier_id={r[0]}  total={r[1]}  activos={r[2]}  ultima={r[3]}')

    sample = conn.execute(
        'SELECT product_id_dropi, nombre, stock, imagen, activo FROM productos WHERE activo=1 LIMIT 10'
    ).fetchall()
    print()
    print('=== MUESTRA (activos) ===')
    for r in sample:
        img = (r[3] or '')[:70]
        print(f'  id={r[0]}  stock={r[2]}  activo={r[4]}')
        print(f'    nombre={r[1][:50]}')
        print(f'    imagen={img}')

    dist = conn.execute(
        'SELECT stock, COUNT(*) as n FROM productos WHERE activo=1 GROUP BY stock ORDER BY stock DESC LIMIT 15'
    ).fetchall()
    print()
    print('=== DISTRIBUCION STOCK ===')
    for r in dist:
        print(f'  stock={r[0]}  count={r[1]}')
