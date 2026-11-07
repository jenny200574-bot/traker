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
        "pageSize":         5,
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
    for it in items[:3]:
        print(f"  Nombre: {it.get('name')}")

try_payload({"search": "Colchon"})
try_payload({"search": "producto"})
try_payload({"q": "Colchon"})
try_payload({"query": "Colchon"})
try_payload({"name": "Colchon"})
