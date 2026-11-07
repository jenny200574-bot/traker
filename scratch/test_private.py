import sys
sys.path.insert(0, '.')
import requests
import settings as cfg
from api_dropi import login_dropi, _normalize_token, _BASE_HEADERS, API_URL, _get_items

email = cfg.get_email()
password = cfg.get_password()
token = login_dropi(email, password)
token = _normalize_token(token)

def test_query(privated_product):
    payload = {
        "country":          "COLOMBIA",
        "favorite":         False,
        "get_stock":        True,
        "no_count":         True,
        "pageSize":         10,
        "privated_product": privated_product,
        "search_type":      "simple",
        "startData":        0,
        "supplier_id":      0,  # configura tu supplier_id en local
        "userVerified":     False,
        "with_collection":  True,
    }
    headers = {**_BASE_HEADERS, "X-Authorization": token}
    resp = requests.post(API_URL, json=payload, headers=headers, timeout=30)
    items = _get_items(resp.json())
    print(f"privated_product={privated_product} -> Retornó {len(items)} productos")
    if items:
        print(f"  Primer producto ID: {items[0].get('id')} | Nombre: {items[0].get('name')}")

print("Consultando con ambos valores:")
test_query(False)
test_query(True)
