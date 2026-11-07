"""
diag_api.py — Muestra la respuesta RAW de la API Dropi para un producto
"""
import sys
import json
sys.path.insert(0, '.')
import settings as cfg
import requests
from api_dropi import login_dropi, _normalize_token, _BASE_HEADERS, API_URL

email = cfg.get_email()
password = cfg.get_password()

if not email or not password:
    print("Faltan credenciales en settings.json.")
else:
    print("Credenciales locales listas.")
print("Haciendo login...")
token = login_dropi(email, password)
token = _normalize_token(token)
print("Token obtenido.")

# Pedir página 1 con get_stock=True
payload = {
    "country":          "COLOMBIA",
    "favorite":         False,
    "get_stock":        True,
    "no_count":         True,
    "pageSize":         3,     # Solo 3 para ver la estructura completa
    "privated_product": False,
    "search_type":      "simple",
    "startData":        0,
    "supplier_id":      0,  # configura tu supplier_id en local
    "userVerified":     False,
    "with_collection":  True,
}
headers = {**_BASE_HEADERS, "X-Authorization": token}

print("\nHaciendo petición a la API...")
resp = requests.post(API_URL, json=payload, headers=headers, timeout=30)
print(f"Status: {resp.status_code}")

data = resp.json()

# Mostrar estructura completa del primer producto
objects = data.get("response", {}).get("objects") or data.get("objects") or []
print(f"\nTotal objetos en respuesta: {len(objects)}")

if objects:
    first = objects[0]
    print("\n=== PRIMER PRODUCTO (todas las claves) ===")
    for key, val in first.items():
        if isinstance(val, (list, dict)):
            print(f"  {key}: {json.dumps(val)[:200]}")
        else:
            print(f"  {key}: {repr(val)}")
