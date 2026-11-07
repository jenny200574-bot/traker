"""
Script de prueba para verificar la función obtener_proveedores_dropi
"""
import sys
from pathlib import Path

# Agregar el directorio del proyecto al path
sys.path.insert(0, str(Path(__file__).parent))

import settings as cfg
from api_dropi import obtener_proveedores_dropi

def main():
    print("=== PRUEBA DE OBTENCIÓN DE PROVEEDORES ===\n")
    
    token = cfg.get_token()
    if not token:
        print("❌ No hay token configurado. Configura las credenciales primero.")
        return
    
    print("Token encontrado en settings.json.")
    print()
    
    # Prueba 1: Sin categorías (todas) - limitado a 100 proveedores
    print("--- PRUEBA 1: Sin categorías (todas) - Límite 100 proveedores ---")
    try:
        import threading
        
        stop_event = threading.Event()
        proveedores = []
        
        def log_callback(msg, lote=None):
            print(msg)
            if lote:
                proveedores.extend(lote)
                if len(proveedores) >= 100:
                    print(f"✅ Límite de 100 proveedores alcanzado. Deteniendo...")
                    stop_event.set()
        
        obtener_proveedores_dropi(
            token=token,
            categorias=None,
            favorite="false",
            callback=log_callback,
            stop_event=stop_event
        )
        
        print(f"\n✅ Total proveedores obtenidos: {len(proveedores)}")
        
        if proveedores:
            print(f"\nPrimer proveedor: {proveedores[0].get('name', 'N/A')}")
            print(f"Último proveedor: {proveedores[-1].get('name', 'N/A')}")
            
            # Contar bodegas
            total_bodegas = sum(len(p.get('warehouses', [])) for p in proveedores)
            print(f"Total bodegas: {total_bodegas}")
            
            # Mostrar algunos ejemplos
            print(f"\n--- Ejemplos de proveedores ---")
            for i, p in enumerate(proveedores[:5]):
                print(f"{i+1}. {p.get('name', 'N/A')} - {p.get('store_name', 'N/A')} - {len(p.get('warehouses', []))} bodegas")
        else:
            print("❌ No se obtuvieron proveedores")
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
    
    print("\n" + "="*50 + "\n")
    
    # Prueba 2: Con categoría específica (ej: Bisutería)
    print("--- PRUEBA 2: Categoría 'Bisutería' ---")
    try:
        proveedores_bisuteria = obtener_proveedores_dropi(
            token=token,
            categorias=["Bisutería"],
            favorite="false"
        )
        print(f"✅ Total proveedores en Bisutería: {len(proveedores_bisuteria)}")
        
        if proveedores_bisuteria:
            print(f"Primer proveedor: {proveedores_bisuteria[0].get('name', 'N/A')}")
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()
