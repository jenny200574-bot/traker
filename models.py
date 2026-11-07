"""
models.py — Tipos de datos para Dropi Tracker
"""
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Bodega:
    nombre: str = ""
    url: str = ""
    supplier_id: int = 0
    id: Optional[int] = None
    productos_esperados: int = 0



@dataclass
class Producto:
    product_id_dropi: int = 0
    supplier_id: int = 0
    nombre: str = ""
    sku: str = ""
    imagen: str = ""
    precio: float = 0.0
    precio_oferta: float = 0.0
    stock: int = 0
    categoria: str = ""
    activo: bool = True
    ultima_actualizacion: str = ""


@dataclass
class HistorialStock:
    product_id_dropi: int = 0
    supplier_id: int = 0
    stock: int = 0
    fecha_hora: str = ""


@dataclass
class LogActividad:
    fecha: str = ""
    hora: str = ""
    bodega: str = ""
    productos_descargados: int = 0
    errores: str = ""
