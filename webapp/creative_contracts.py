"""Contrato para módulos futuros de piezas. El registro nace vacío.

No hay un generador incluido. Registrar uno aquí es la única forma de
conectarlo; la interfaz no simula un resultado.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class BriefSnapshot:
    brief_id: int
    title: str
    audience: str
    promise: str
    objections: str
    notes: str
    product_id_dropi: int | None = None
    supplier_id: int | None = None


@dataclass(frozen=True)
class GenerationRequest:
    brief: BriefSnapshot
    kind: str  # reservado: "pieza_estatica" | "video"


@dataclass
class GenerationResult:
    generator_id: str
    kind: str
    status: str
    detail: str
    artifacts: list[str] = field(default_factory=list)


class CreativeGenerator(Protocol):
    id: str
    label: str

    def generate(self, request: GenerationRequest) -> GenerationResult:
        """Produce una pieza. Ninguna implementación se entrega en esta versión."""


REGISTRY: dict[str, CreativeGenerator] = {}


def available() -> list[dict]:
    return [{"id": item.id, "label": item.label} for item in REGISTRY.values()]


def generate(request: GenerationRequest, generator_id: str) -> GenerationResult:
    generator = REGISTRY.get(generator_id)
    if generator is None:
        raise LookupError(
            "No hay un módulo de generación registrado. "
            "Esta versión no crea banners ni video."
        )
    return generator.generate(request)
