"""
api_dropi.py — Cliente para la API de Dropi
Adaptación del script JavaScript funcional al estilo Python.
"""
import re
import time
import logging
import threading
from datetime import datetime
from typing import Callable, Generator, List, Optional

import requests

from models import Producto

logger = logging.getLogger(__name__)

API_URL       = "https://api.dropi.co/api/products/v4/index"
LOGIN_URL     = "https://api.dropi.co/api/login"
SUPPLIERS_URL = "https://api.dropi.co/api/users/getDataProductsSuplierFilter"
METRICS_URL   = "https://api-v2.dropi.co/indicators/supplier"
PAGE_SIZE     = 50
MAX_PAGES     = 1000          # soporte hasta 50 000 productos
REQUEST_DELAY = 0.12          # segundos entre páginas

CLOUDFRONT_BASE = "https://d39ru7awumhhs2.cloudfront.net/"

_BASE_HEADERS = {
    "Accept":          "application/json, text/plain, */*",
    "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
    "Content-Type":    "application/json",
    "Origin":          "https://app.dropi.co",
    "Referer":         "https://app.dropi.co/",
    "User-Agent":      (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/149.0.0.0 Safari/537.36"
    ),
    "Sec-Fetch-Dest":  "empty",
    "Sec-Fetch-Mode":  "cors",
    "Sec-Fetch-Site":  "same-site",
}


# ─────────────────────────── LOGIN ──────────────────────────────────────────

def login_dropi(email: str, password: str) -> str:
    """
    Autentica con email y contraseña en Dropi.

    Returns:
        Token JWT listo para usar (sin el prefijo 'Bearer ').

    Raises:
        ValueError:              Credenciales incorrectas o respuesta inesperada.
        requests.RequestException: Error de red.
    """
    payload = {
        "email":          email.strip(),
        "password":       password,
        "white_brand_id": 1,
    }
    response = requests.post(
        LOGIN_URL, json=payload, headers=_BASE_HEADERS, timeout=20
    )
    response.raise_for_status()

    data = response.json()
    if not data.get("isSuccess"):
        msg = data.get("message", "Error de autenticación desconocido.")
        raise ValueError(f"Login fallido: {msg}")

    token = data.get("token", "")
    if not token:
        raise ValueError("Login exitoso pero no se recibió token en la respuesta.")

    return token


# ─────────────────────────── HELPERS ────────────────────────────────────────

def _normalize_token(token: str) -> str:
    token = token.strip()
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    return "Bearer " + token


def _normalize_image_url(url: str) -> str:
    """Normaliza una URL de imagen añadiendo el prefijo CloudFront si es relativa."""
    if not url:
        return ""
    url = url.strip()
    if url.startswith("http://") or url.startswith("https://"):
        return url
    # Eliminar barra inicial si la hay
    if url.startswith("/"):
        url = url[1:]
    return CLOUDFRONT_BASE + url


def _get_items(json_data: dict) -> list:
    """Extrae la lista de productos, igual que el JS original."""
    objects = json_data.get("response", {}).get("objects")
    if isinstance(objects, list):
        return objects
    objects = json_data.get("objects")
    if isinstance(objects, list):
        return objects
    return []


def _extract_stock(item: dict) -> int:
    """
    Extrae el stock de un item de la API Dropi matching JS logic:
    p.warehouse_product -> sum of stock
    else p.variations -> sum of stock
    else p.stock
    Con soporte para claves legacy de pruebas (quantity, stock_available, variants).
    """
    def _int(val) -> int:
        try:
            return int(val or 0)
        except (TypeError, ValueError):
            return 0

    # 1. warehouse_product
    warehouse = item.get("warehouse_product")
    if isinstance(warehouse, list) and len(warehouse) > 0:
        return sum(_int(w.get("stock")) for w in warehouse if isinstance(w, dict))

    # 2. variations (o variants)
    variations = item.get("variations") or item.get("variants") or item.get("product_variants")
    if isinstance(variations, list) and len(variations) > 0:
        total = 0
        for v in variations:
            if isinstance(v, dict):
                # Usar stock, quantity o stock_available
                val = v.get("stock")
                if val is None:
                    val = v.get("quantity")
                if val is None:
                    val = v.get("stock_available")
                total += _int(val)
        return total

    # 3. stock directo o fallbacks directos
    for field in ("stock", "quantity", "stock_available", "total_stock"):
        if item.get(field) is not None:
            return _int(item.get(field))

    return 0


def _extract_category(item: dict) -> str:
    """
    Extrae la categoría del item matching JS logic:
    p.categories -> joined names
    else p.category.name or p.category_name
    """
    categories = item.get("categories")
    if isinstance(categories, list) and len(categories) > 0:
        names = [c.get("name") for c in categories if isinstance(c, dict) and c.get("name")]
        if names:
            return " | ".join(names)

    cat = item.get("category")
    if isinstance(cat, dict):
        return cat.get("name") or ""
    return item.get("category_name") or ""


def _extract_image(item: dict) -> str:
    """
    Extrae la imagen del item matching JS logic:
    g.urlS3 || g.url || p.image
    Con soporte para claves legacy de pruebas (images array).
    """
    gallery = item.get("gallery")
    g = None
    if isinstance(gallery, list) and len(gallery) > 0:
        # Buscar el que tiene main=True o tomar el primero
        g = next((x for x in gallery if isinstance(x, dict) and x.get("main")), None)
        if not g:
            g = gallery[0]

    img_url = ""
    if isinstance(g, dict):
        img_url = g.get("urlS3") or g.get("url") or ""

    if not img_url:
        img_url = item.get("image") or ""

    if not img_url:
        # Fallback a images (plural) para pruebas legacy
        images = item.get("images") or []
        if isinstance(images, list) and len(images) > 0:
            first = images[0]
            if isinstance(first, dict):
                img_url = first.get("url") or first.get("image") or ""
            else:
                img_url = str(first)

    return _normalize_image_url(img_url)


def _fetch_page(
    session: requests.Session,
    token: str,
    supplier_id: int,
    start_data: int,
    stop_event: Optional[threading.Event] = None,
) -> list:
    if stop_event and stop_event.is_set():
        return []

    payload = {
        "country":          "COLOMBIA",
        "favorite":         False,
        "get_stock":        True,
        "no_count":         True,
        "pageSize":         PAGE_SIZE,
        "privated_product": False,
        "search_type":      "simple",
        "startData":        start_data,
        "supplier_id":      supplier_id,
        "userVerified":     False,
        "with_collection":  True,
    }
    headers = {**_BASE_HEADERS, "X-Authorization": token}
    response = session.post(API_URL, json=payload, headers=headers, timeout=30)

    if response.status_code in (401, 403):
        raise PermissionError(
            f"Token inválido o sin permisos (HTTP {response.status_code})"
        )
    response.raise_for_status()

    try:
        return _get_items(response.json())
    except ValueError:
        return []


def _parse_producto(item: dict, supplier_id: int) -> Producto:
    """Mapea un objeto de la API Dropi v4 a un Producto."""

    def _float(val) -> float:
        try:
            return float(val or 0)
        except (TypeError, ValueError):
            return 0.0

    def _int(val) -> int:
        try:
            return int(val or 0)
        except (TypeError, ValueError):
            return 0

    # ── Precios ── matching JS logic: p.sale_price ?? p.price y p.suggested_price ?? p.price_offer
    precio = _float(
        item.get("sale_price")
        if item.get("sale_price") is not None
        else item.get("price")
    )
    precio_oferta = _float(
        item.get("suggested_price")
        if item.get("suggested_price") is not None
        else item.get("price_offer")
    )

    return Producto(
        product_id_dropi   = _int(item.get("id")),
        supplier_id        = supplier_id,
        nombre             = item.get("name", item.get("nombre", "")),
        sku                = str(item.get("sku", item.get("reference", ""))),
        imagen             = _extract_image(item),
        precio             = precio,
        precio_oferta      = precio_oferta,
        stock              = _extract_stock(item),
        categoria          = _extract_category(item),
        activo             = True,
        ultima_actualizacion = datetime.now().isoformat(timespec="seconds"),
    )


# ─────────────────────────── FUNCIÓN PRINCIPAL ──────────────────────────────

def descargar_bodega(
    token: str,
    supplier_id: int,
    callback: Optional[Callable[[str], None]] = None,
    captcha_token: str = "",
    stop_event: Optional[threading.Event] = None,
    productos_esperados: int = 0,
) -> List[Producto]:
    """
    Descarga todos los productos de una bodega Dropi emulando exactamente
    el comportamiento del boton "Mostrar mas productos" de la UI:

      - Pasada (simula sesion del navegador):
          * Pide paginas sucesivas: startData = 0, 50, 100, ...
          * SOLO cuando la API devuelve 0 items termina el scroll.
            Paginas incompletas (menos de 50) son normales y NO detienen el scroll.
          * Agrega los productos nuevos al acumulado unico por ID.

      - F5 (nueva sesion HTTP, startData vuelve a 0):
          * Dropi baraja el catalogo en cada sesion, puede devolver productos
            distintos a los de pasadas anteriores.
          * Se repite hasta 5 veces.

      - Para si alcanza productos_esperados o si una pasada no agrega nada nuevo.
    """
    def log(msg):
        if callback:
            callback(msg)
        logger.info(msg)

    if not token or not token.strip():
        raise ValueError("El token no puede estar vacio.")
    if not supplier_id or supplier_id <= 0:
        raise ValueError("Supplier ID invalido: %r" % supplier_id)

    token = _normalize_token(token)
    unique_products = {}  # dict[int, Producto]

    meta_txt = "/%d" % productos_esperados if productos_esperados > 0 else ""
    meta_info = "(Productos esperados: %d)" % productos_esperados if productos_esperados > 0 else "(sin meta configurada)"
    log("  -> Iniciando descarga supplier_id=%d %s..." % (supplier_id, meta_info))

    if stop_event and stop_event.is_set():
        log("  Descarga cancelada antes de iniciar.")
        return []

    for run in range(5):
        if stop_event and stop_event.is_set():
            log("  Descarga cancelada por el usuario.")
            break

        acum = len(unique_products)

        if run == 0:
            log("  Pasada 1/5 -- Descarga inicial... (Acumulados: 0%s)" % meta_txt)
        else:
            log("  [F5] Pasada %d/5 -- Acumulados: %d%s..." % (run + 1, acum, meta_txt))

        prev_count = len(unique_products)
        nuevos_en_pasada = 0
        paginas_en_pasada = 0

        # -- Nueva sesion HTTP por pasada (emula F5 / sesion fresca) --------
        with requests.Session() as session:
            page = 0
            while True:
                if stop_event and stop_event.is_set():
                    break

                start_data = page * PAGE_SIZE

                # Reintento ante errores de red (max. 5 veces para 429, 3 para otros)
                items = []
                max_attempts = 5  # Aumentado para errores 429
                
                for attempt in range(max_attempts):
                    if stop_event and stop_event.is_set():
                        break
                    try:
                        items = _fetch_page(session, token, supplier_id, start_data, stop_event)
                        break
                    except PermissionError:
                        raise
                    except requests.exceptions.Timeout:
                        log("  AVISO Pasada %d, Pag %d: Timeout. Reintento %d/%d..." % (run + 1, page + 1, attempt + 1, max_attempts))
                        time.sleep(2 ** attempt)
                    except requests.exceptions.RequestException as exc:
                        # Manejo especial para 429 Too Many Requests
                        if "429" in str(exc):
                            if attempt < max_attempts - 1:
                                delay = 5 + (attempt * 5)  # 5, 10, 15, 20 segundos
                                log("  AVISO Pasada %d, Pag %d: Rate limit 429. Esperando %d seg. Reintento %d/%d..." % (run + 1, page + 1, delay, attempt + 1, max_attempts))
                                time.sleep(delay)
                            else:
                                log("  ERROR Pasada %d, Pag %d: Rate limit persiste tras %d intentos. Continuando con siguiente página..." % (run + 1, page + 1, max_attempts))
                                items = None
                                break
                        else:
                            if attempt == 2:
                                log("  ERROR Pasada %d, Pag %d: Error tras 3 intentos: %s. Continuando con siguiente página..." % (run + 1, page + 1, exc))
                                items = None
                                break
                            log("  AVISO Pasada %d, Pag %d: Error: %s. Reintento %d/3..." % (run + 1, page + 1, exc, attempt + 1))
                            time.sleep(2)

                # Si items es None por error 429, continuar con la siguiente página en lugar de abortar
                if items is None:
                    log("  Pasada %d, Pag %d: Omitida por error, continuando..." % (run + 1, page + 1))
                    page += 1
                    continue

                if stop_event and stop_event.is_set():
                    break

                # -- LOGICA CORRECTA: solo 0 items = fin del scroll ----------
                # Una pagina incompleta (menos de 50 items) NO detiene el scroll.
                # Dropi baraja y puede devolver 30-49 items en paginas intermedias.
                # SOLO cuando devuelve 0 items es que el boton ya no existe.
                if not items:
                    log("  Pasada %d, Pag %d: 0 prods -> Fin de scroll (Mostrar mas desaparecio)." % (run + 1, page + 1))
                    log("  Resumen pasada %d: %d pag(s), %d nuevos | Total: %d%s" % (run + 1, paginas_en_pasada, nuevos_en_pasada, len(unique_products), meta_txt))
                    break

                # Acumular unicos (actualiza stock si ya existe)
                antes = len(unique_products)
                parsed = [_parse_producto(it, supplier_id) for it in items]
                for p in parsed:
                    unique_products[p.product_id_dropi] = p

                nuevos = len(unique_products) - antes
                nuevos_en_pasada += nuevos
                paginas_en_pasada += 1

                log("  Pasada %d, Pag %d (start=%d): %d recibidos, +%d nuevos | Total unico: %d%s" % (
                    run + 1, page + 1, start_data, len(items), nuevos, len(unique_products), meta_txt
                ))

                # -- Meta alcanzada dentro de la pasada ----------------------
                if productos_esperados > 0 and len(unique_products) >= productos_esperados:
                    log("  META ALCANZADA en pasada (%d/%d). Deteniendo." % (len(unique_products), productos_esperados))
                    break

                page += 1
                time.sleep(REQUEST_DELAY)

        # -- Validaciones tras cada pasada (F5) ------------------------------
        current_count = len(unique_products)

        # 1. Meta alcanzada
        if productos_esperados > 0 and current_count >= productos_esperados:
            log("  META ALCANZADA total (%d/%d)" % (current_count, productos_esperados))
            break

        # 2. Si no se obtuvo absolutamente ningún producto en total, abortamos
        if current_count == 0:
            log("  No se obtuvieron productos en total. Deteniendo ciclo.")
            break

    # ── Resumen final ───────────────────────────────────────────────────────
    total_found = len(unique_products)
    if productos_esperados > 0 and total_found < productos_esperados:
        log(f"  ⚠ Descarga finalizada con {total_found} productos (Faltaron {productos_esperados - total_found} del esperado: {productos_esperados}).")
    else:
        log(f"  ✓ Descarga completa: {total_found} productos únicos obtenidos.")

    return list(unique_products.values())


# ─────────────────────────── UTILIDAD URL ───────────────────────────────────

def obtener_proveedores_dropi(
    token: str,
    categorias: Optional[List[str]] = None,
    favorite: str = "false",
    callback: Optional[Callable[[str, Optional[List[dict]]], None]] = None,
    stop_event: Optional[threading.Event] = None
) -> List[dict]:
    """
    Obtiene la lista de proveedores de Dropi usando el endpoint de filtros.

    Args:
        token: Token JWT de autenticación
        categorias: Lista de categorías a filtrar (None para todas)
        favorite: "false" o "true" para filtrar por favoritos
        callback: Función opcional para logs de progreso y actualización incremental.
                  Recibe (mensaje, lote_proveedores) donde lote_proveedores es opcional
        stop_event: Evento opcional para detener la descarga

    Returns:
        Lista de diccionarios con información de proveedores y sus bodegas
    """
    if categorias is None:
        categorias = []  # Vacío = todas las categorías

    headers = _BASE_HEADERS.copy()
    headers["X-Authorization"] = _normalize_token(token)

    all_proveedores = []
    result_number = 40

    def log(msg: str, lote: Optional[List[dict]] = None):
        if callback:
            callback(msg, lote)
        logger.info(msg)

    log("Iniciando descarga de proveedores de Dropi...")

    for categoria in categorias if categorias else [None]:
        start = 0
        categoria_nombre = categoria if categoria else "Todas"

        while True:
            if stop_event and stop_event.is_set():
                log("Descarga de proveedores detenida por usuario")
                break

            payload = {
                "start": start,
                "result_number": result_number,
                "favorite": favorite,
            }

            if categoria:
                payload["category"] = [categoria]

            try:
                response = requests.post(
                    SUPPLIERS_URL,
                    json=payload,
                    headers=headers,
                    timeout=30
                )
                response.raise_for_status()
                data = response.json()

                if not data.get("isSuccess"):
                    log(f"Error en respuesta API: {data.get('message', 'Error desconocido')}")
                    break

                proveedores = data.get("objects", [])

                if len(proveedores) == 0:
                    log(f"Categoría '{categoria_nombre}': No más proveedores (total: {len(all_proveedores)})")
                    break

                all_proveedores.extend(proveedores)
                log(f"Categoría '{categoria_nombre}': {len(proveedores)} proveedores (start={start}, total acumulado: {len(all_proveedores)})", proveedores)

                start += result_number
                time.sleep(REQUEST_DELAY)

            except requests.RequestException as e:
                log(f"Error de red al obtener proveedores: {e}")
                break

    log(f"✓ Descarga de proveedores completada: {len(all_proveedores)} proveedores obtenidos")
    return all_proveedores


def extraer_supplier_id(url: str) -> Optional[int]:
    """
    Extrae el supplier_id de una URL de Dropi.
    Soporta los mismos patrones del JS original.
    """
    patterns = [
        r"/provider/c/(\d+)",
        r"/provider/(\d+)",
        r"[?&]supplier_id=(\d+)",
    ]
    for pattern in patterns:
        m = re.search(pattern, url, re.IGNORECASE)
        if m:
            return int(m.group(1))


def obtener_metricas_proveedor(supplier_id: int, token: str) -> Optional[dict]:
    """
    Obtiene las métricas de un proveedor desde la API v2 de Dropi.

    Args:
        supplier_id: ID del proveedor
        token: Token de autenticación

    Returns:
        Diccionario con total_monthly_products, total_monthly_orders, total_monthly_dropshippers
        o None si hay error
    """
    try:
        # Normalizar el token como en obtener_proveedores_dropi
        token = _normalize_token(token)
        
        headers = {**_BASE_HEADERS, "X-Authorization": token}
        headers["X-Host"] = "co"  # Header requerido por la API v2 (código de país)

        url = f"{METRICS_URL}/{supplier_id}/metrics"
        logger.debug(f"Consultando API: {url}")

        response = requests.get(url, headers=headers, timeout=30)

        logger.debug(f"Status code: {response.status_code}")
        logger.debug(f"Response text: {response.text[:500]}")

        if response.status_code in (401, 403):
            raise PermissionError(
                f"Token inválido o sin permisos (HTTP {response.status_code})"
            )
        response.raise_for_status()

        json_data = response.json()

        if json_data.get("is_successful") and json_data.get("status_code") == 200:
            data = json_data.get("data", {})
            return {
                "total_monthly_products": data.get("total_monthly_products", 0),
                "total_monthly_orders": data.get("total_monthly_orders", 0),
                "total_monthly_dropshippers": data.get("total_monthly_dropshippers", 0)
            }
        else:
            logger.error(f"Error en respuesta API para supplier_id {supplier_id}: {json_data}")
            return None

    except requests.HTTPError as e:
        logger.error(f"Error HTTP al obtener métricas de supplier_id {supplier_id}: {e}")
        logger.error(f"Response: {e.response.text if e.response else 'No response'}")
        return None
    except requests.RequestException as e:
        logger.error(f"Error de red al obtener métricas de supplier_id {supplier_id}: {e}")
        return None
    except Exception as e:
        logger.error(f"Error al obtener métricas de supplier_id {supplier_id}: {e}")
        return None


def extraer_ordenes_bodega(url: str, token: Optional[str] = None) -> Optional[int]:
    """
    Extrae el número de órdenes de una URL de bodega de Dropi usando web scraping.

    Args:
        url: URL de la bodega (ej: https://app.dropi.co/dashboard/provider/12345/nombre)
        token: Token de autenticación opcional para headers

    Returns:
        Número de órdenes o None si no se pudo extraer
    """
    try:
        headers = _BASE_HEADERS.copy()
        if token:
            headers["X-Authorization"] = f"Bearer {token}"

        response = requests.get(url, headers=headers, timeout=30)

        # Verificar si redirige a login
        if response.status_code == 302 or "login" in response.url.lower():
            logger.warning(f"Redirigido a login para URL: {url}")
            return None

        response.raise_for_status()

        html = response.text

        # Verificar si el HTML parece válido (no es una página de error)
        if "login" in html.lower() or "sign in" in html.lower():
            logger.warning(f"Página de login detectada en URL: {url}")
            return None

        # Buscar el patrón del elemento h2 que contiene las órdenes
        # El patrón es: <h2 ...> número </h2>
        # Usamos regex para encontrar el número dentro de etiquetas h2
        pattern = r'<h2[^>]*>\s*(\d+)\s*</h2>'
        matches = re.findall(pattern, html)

        if matches:
            # Tomar el primer número encontrado (asumimos que es el de órdenes)
            logger.debug(f"Órdenes encontradas con patrón general en {url}: {matches[0]}")
            return int(matches[0])

        # Alternativa: buscar específicamente por la clase ng-tns
        pattern_alt = r'<h2[^>]*class="[^"]*ng-tns[^"]*"[^>]*>\s*(\d+)\s*</h2>'
        matches_alt = re.findall(pattern_alt, html)
        if matches_alt:
            logger.debug(f"Órdenes encontradas con patrón ng-tns en {url}: {matches_alt[0]}")
            return int(matches_alt[0])

        # Alternativa: buscar cualquier número grande en contexto de "orden" o "pedido"
        pattern_ordenes = r'(?:orden|pedido|order)[^<]*(\d{3,})'
        matches_ordenes = re.findall(pattern_ordenes, html, re.IGNORECASE)
        if matches_ordenes:
            logger.debug(f"Órdenes encontradas con patrón contexto en {url}: {matches_ordenes[0]}")
            return int(matches_ordenes[0])

        logger.warning(f"No se encontraron órdenes en URL: {url} (status: {response.status_code})")
        return None

    except requests.RequestException as e:
        logger.error(f"Error de red al extraer órdenes de {url}: {e}")
        return None
    except Exception as e:
        logger.error(f"Error al extraer órdenes de {url}: {e}")
        return None

    return None
