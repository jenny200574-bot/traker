"""Descarga un catálogo reutilizando el cliente de la app de escritorio.

Registra cobertura. No inventa ceros cuando una página falla o el producto
no aparece.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

import requests

import api_dropi
from models import Producto


@dataclass
class CatalogFetch:
    products: dict[int, Producto] = field(default_factory=dict)
    status: str = "failed"
    pages: int = 0
    page_errors: int = 0
    empty_page_reached: bool = False
    duplicates: int = 0
    expected: int | None = None
    notes: list[str] = field(default_factory=list)
    passes: int = 0


def fetch_supplier_catalog(
    token: str,
    supplier_id: int,
    *,
    expected: int | None = None,
    stop_event=None,
    log=None,
    delay: float | None = None,
    max_passes: int = 5,
) -> CatalogFetch:
    def emit(message: str) -> None:
        if log:
            log(message)

    result = CatalogFetch(expected=expected or None)
    if not token or not str(token).strip():
        result.status = "failed"
        result.notes.append("No hay token.")
        return result
    if not supplier_id or supplier_id <= 0:
        result.status = "failed"
        result.notes.append("Proveedor inválido.")
        return result

    token = api_dropi._normalize_token(token)
    pause = api_dropi.REQUEST_DELAY if delay is None else delay
    stopped_by_user = False
    stopped_by_meta = False

    for run in range(max_passes):
        if stop_event is not None and stop_event.is_set():
            stopped_by_user = True
            break
        result.passes = run + 1
        emit(f"Pasada {run + 1}/{max_passes}. Únicos acumulados: {len(result.products)}.")
        with requests.Session() as session:
            page = 0
            while True:
                if stop_event is not None and stop_event.is_set():
                    stopped_by_user = True
                    break
                start = page * api_dropi.PAGE_SIZE
                try:
                    items = api_dropi._fetch_page(session, token, supplier_id, start, stop_event)
                except PermissionError:
                    raise
                except requests.RequestException as exc:
                    result.page_errors += 1
                    emit(f"Página {page + 1} falló ({exc.__class__.__name__}). No se rellena con ceros.")
                    page += 1
                    if result.page_errors >= 3 and page > 2:
                        result.notes.append("Se detuvo la pasada tras errores de página repetidos.")
                        break
                    continue
                result.pages += 1
                if not items:
                    result.empty_page_reached = True
                    emit(f"Página {page + 1} vacía: fin de esta pasada.")
                    break
                fresh = 0
                for item in items:
                    parsed = api_dropi._parse_producto(item, supplier_id)
                    if parsed.product_id_dropi in result.products:
                        result.duplicates += 1
                    else:
                        fresh += 1
                    result.products[parsed.product_id_dropi] = parsed
                emit(
                    f"Página {page + 1}: {len(items)} recibidos, {fresh} nuevos, "
                    f"{len(result.products)} únicos."
                )
                if expected and expected > 0 and len(result.products) >= expected:
                    stopped_by_meta = True
                    result.notes.append(
                        "Se alcanzó la meta configurada antes de comprobar el final del catálogo."
                    )
                    break
                page += 1
                if pause:
                    time.sleep(pause)
        if stopped_by_user or stopped_by_meta:
            break
        if len(result.products) == 0 and result.empty_page_reached and result.page_errors == 0:
            break

    if stopped_by_user:
        result.status = "cancelled"
        result.notes.append("Descarga cancelada. Lo recibido queda como cobertura parcial.")
    elif result.page_errors or stopped_by_meta:
        result.status = "partial"
    elif result.empty_page_reached and result.products and result.page_errors == 0:
        result.status = "complete"
        result.notes.append("La última página llegó vacía y no hubo errores de página.")
    elif result.empty_page_reached and not result.products and result.page_errors == 0:
        result.status = "partial"
        result.notes.append(
            "La respuesta llegó vacía. No se modifica el stock ni se marcan ausencias."
        )
    else:
        result.status = "failed" if not result.products else "partial"
        if result.status == "failed":
            result.notes.append("No se obtuvo un catálogo utilizable.")
    return result
