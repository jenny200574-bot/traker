import sys
sys.path.insert(0, '.')
import requests
import settings as cfg
from api_dropi import login_dropi, _normalize_token, _BASE_HEADERS, API_URL, _get_items

email = cfg.get_email()
password = cfg.get_password()
token = login_dropi(email, password)
token = _normalize_token(token)

def try_payload(payload_update):
    payload = {
        "country":          "COLOMBIA",
        "favorite":         False,
        "get_stock":        True,
        "no_count":         True,
        "pageSize":         15,
        "privated_product": False,
        "search_type":      "simple",
        "startData":        0,
        "supplier_id":      0,  # configura tu supplier_id en local
        "userVerified":     False,
        "with_collection":  True,
    }
    payload.update(payload_update)
    headers = {**_BASE_HEADERS, "X-Authorization": token}
    resp = requests.post(API_URL, json=payload, headers=headers, timeout=30)
    items = _get_items(resp.json())
    print(f"Update: {payload_update} -> Retornó {len(items)} productos")
    if items:
        prices = [float(it.get("sale_price") or 0) for it in items]
        print(f"  Precios: {prices}")

# Probar diferentes formatos de rango de precios
try_payload({"price_min": 10000, "price_max": 20000})
try_payload({"min_price": 10000, "max_price": 20000})
try_payload({"price": {"min": 10000, "max": 20000}})
try_payload({"price_range": [10000, 20000]})
try_payload({"price_range": "10000-20000"})
try_payload({"priceFrom": 10000, "priceTo": 20000})
try_payload({"price_from": 10000, "price_to": 20000})
try_payload({"min": 10000, "max": 20000})
