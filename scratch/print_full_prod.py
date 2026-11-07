import sys
sys.path.insert(0, '.')
import requests
import settings as cfg
from api_dropi import login_dropi, _normalize_token, _BASE_HEADERS, API_URL, _get_items

email = cfg.get_email()
password = cfg.get_password()
token = login_dropi(email, password)
token = _normalize_token(token)

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
headers = {**_BASE_HEADERS, "X-Authorization": token}
resp = requests.post(API_URL, json=payload, headers=headers, timeout=30)
data = resp.json()
objects = _get_items(data)
if objects:
    print("Categories of first product:")
    print(objects[0].get("categories"))
    print("Full product dict:")
    import pprint
    pprint.pprint(objects[0])
