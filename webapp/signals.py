"""Señales conservadoras a partir de observaciones reales.

Una baja de stock es un proxy de demanda. No es una venta confirmada
ni un signo de viralidad. Las reposiciones no se restan como ventas
negativas. Un producto ausente no se convierte en stock cero.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


DEFAULT_THRESHOLDS: dict = {
    "suspicious_restore_ratio": 0.7,
    "suspicious_drop_pct": 0.8,
    "suspicious_drop_abs": 20,
    "min_baseline_products": 8,
    "stale_hours": 48,
    "gap_hours": 36,
}

PROXY_LABEL = (
    "Baja de stock observada: proxy de demanda. "
    "No es una venta confirmada ni un indicador de viralidad."
)
INSUFFICIENT_LABEL = (
    "Evidencia insuficiente: hacen falta al menos dos observaciones en la ventana."
)
FLAT_LABEL = "Sin baja observada en la ventana."
RESTOCK_LABEL = "Reposición observada. No se contabiliza como venta negativa."


@dataclass(frozen=True)
class ObservationPoint:
    observed_at: datetime
    stock: int
    completeness: str  # complete | partial | unknown
    provenance: str  # download | legacy | demo


@dataclass
class Step:
    kind: str  # baja | reposicion | ajuste_excluido
    units: int
    at: datetime
    from_stock: int
    to_stock: int
    reason: str = ""


@dataclass
class Movement:
    unidades_baja: int
    reposiciones: int
    cambio_neto: int
    ajustes_excluidos: int
    confianza: str
    motivos: list[str] = field(default_factory=list)
    etiqueta: str = ""
    puntos: int = 0
    cobertura: str = "insuficiente"
    pasos: list[Step] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "unidades_baja": self.unidades_baja,
            "reposiciones": self.reposiciones,
            "cambio_neto": self.cambio_neto,
            "ajustes_excluidos": self.ajustes_excluidos,
            "confianza": self.confianza,
            "motivos": list(self.motivos),
            "etiqueta": self.etiqueta,
            "puntos": self.puntos,
            "cobertura": self.cobertura,
            "es_proxy_de_demanda": self.unidades_baja > 0,
            "venta_confirmada": False,
        }


def _cobertura(points: list[ObservationPoint]) -> str:
    kinds = {p.completeness for p in points}
    if kinds == {"complete"}:
        return "completa"
    if kinds == {"unknown"}:
        return "desconocida"
    if "unknown" in kinds:
        return "mixta"
    if "partial" in kinds:
        return "parcial"
    return "mixta"


def movement(
    points: list[ObservationPoint],
    *,
    gap_hours: float = 36,
    suspicious_drop_pct: float = 0.8,
    suspicious_drop_abs: int = 20,
    suspicious_restore_ratio: float = 0.7,
) -> Movement:
    ordered = sorted(points, key=lambda p: p.observed_at)
    if len(ordered) < 2:
        return Movement(
            unidades_baja=0,
            reposiciones=0,
            cambio_neto=0,
            ajustes_excluidos=0,
            confianza="insuficiente",
            motivos=["Menos de dos observaciones dentro de la ventana."],
            etiqueta=INSUFFICIENT_LABEL,
            puntos=len(ordered),
            cobertura="insuficiente" if not ordered else _cobertura(ordered),
        )

    motivos: list[str] = []
    raw_steps: list[tuple[str, int, int, int, datetime, int]] = []
    # kind, units, index, from_stock, at, to_stock
    large_one_way = False
    for index in range(1, len(ordered)):
        prev = ordered[index - 1]
        cur = ordered[index]
        gap = (cur.observed_at - prev.observed_at).total_seconds() / 3600
        if gap > gap_hours:
            motivos.append(
                f"Hueco de {gap:.0f} h entre observaciones. "
                "La diferencia puede juntar varios cambios no vistos."
            )
        delta = cur.stock - prev.stock
        if delta < 0:
            units = -delta
            raw_steps.append(("baja", units, index, prev.stock, cur.observed_at, cur.stock))
            if prev.stock > 0 and units >= suspicious_drop_abs and (units / prev.stock) >= suspicious_drop_pct:
                large_one_way = True
        elif delta > 0:
            raw_steps.append(("reposicion", delta, index, prev.stock, cur.observed_at, cur.stock))

    excluded_drop_idx: set[int] = set()
    excluded_restock_idx: set[int] = set()
    for pos, step in enumerate(raw_steps):
        kind, units, index, from_stock, _at, _to = step
        if kind != "baja" or index in excluded_drop_idx:
            continue
        if from_stock <= 0 or units < suspicious_drop_abs or (units / from_stock) < suspicious_drop_pct:
            continue
        restored = 0
        consumed: list[int] = []
        for later in raw_steps[pos + 1 :]:
            if later[0] != "reposicion" or later[2] in excluded_restock_idx:
                continue
            restored += later[1]
            consumed.append(later[2])
            if restored >= units * suspicious_restore_ratio:
                excluded_drop_idx.add(index)
                excluded_restock_idx.update(consumed)
                motivos.append(
                    "Ajuste aislado sospechoso: una baja grande se revierte después. "
                    "Esas unidades salen del proxy y de las reposiciones. "
                    "El umbral es configurable y no está validado."
                )
                break

    if large_one_way and not excluded_drop_idx:
        motivos.append(
            "Hay una baja grande respecto al punto anterior. "
            "Sigue visible como proxy, con confianza limitada. "
            "El umbral es configurable y no está validado."
        )

    pasos: list[Step] = []
    unidades_baja = 0
    reposiciones = 0
    ajustes = 0
    for kind, units, index, from_stock, at, to_stock in raw_steps:
        if index in excluded_drop_idx or index in excluded_restock_idx:
            ajustes += units if kind == "baja" else 0
            pasos.append(Step("ajuste_excluido", units, at, from_stock, to_stock, "revertido"))
            continue
        if kind == "baja":
            unidades_baja += units
            pasos.append(Step("baja", units, at, from_stock, to_stock))
        else:
            reposiciones += units
            pasos.append(Step("reposicion", units, at, from_stock, to_stock))

    cambio_neto = ordered[-1].stock - ordered[0].stock
    cobertura = _cobertura(ordered)
    has_gap = any("Hueco de" in m for m in motivos)
    unknown = any(p.completeness == "unknown" or p.provenance == "legacy" for p in ordered)
    partial = any(p.completeness == "partial" for p in ordered)

    if unknown:
        confianza = "desconocida"
        motivos.append(
            "Hay observaciones de historial legado sin constancia de cobertura. "
            "No se inventa una confianza ni se completa la serie."
        )
    elif partial or has_gap or (large_one_way and not excluded_drop_idx):
        confianza = "baja"
        if partial:
            motivos.append(
                "Alguna observación viene de una descarga parcial. "
                "Lo que no apareció no se tomó como cero."
            )
    else:
        confianza = "media"
        motivos.append(
            "La serie de esta ventana tiene observaciones de descargas completas, "
            "sin hueco mayor al umbral. Aun así es un proxy, no una venta."
        )

    if unidades_baja > 0:
        etiqueta = PROXY_LABEL
    elif reposiciones > 0:
        etiqueta = RESTOCK_LABEL
    elif ajustes:
        etiqueta = (
            "Movimiento revertido, tratado como ajuste sospechoso y excluido del proxy. "
            "No es una venta confirmada."
        )
    else:
        etiqueta = FLAT_LABEL

    # Motivos únicos, orden estable.
    seen: list[str] = []
    for item in motivos:
        if item not in seen:
            seen.append(item)

    return Movement(
        unidades_baja=unidades_baja,
        reposiciones=reposiciones,
        cambio_neto=cambio_neto,
        ajustes_excluidos=ajustes,
        confianza=confianza,
        motivos=seen,
        etiqueta=etiqueta,
        puntos=len(ordered),
        cobertura=cobertura,
        pasos=pasos,
    )


def category_baseline(movements: list[Movement], min_products: int = 8) -> dict:
    usable = [m for m in movements if m.confianza == "media" and m.puntos >= 2]
    if len(usable) < min_products:
        return {
            "estado": "insuficiente",
            "muestra": len(usable),
            "minimo": min_products,
            "mediana_baja": None,
            "nota": (
                "Evidencia insuficiente para comparar con la categoría. "
                "No se rellena la mediana."
            ),
        }
    values = sorted(m.unidades_baja for m in usable)
    mid = len(values) // 2
    if len(values) % 2:
        median = float(values[mid])
    else:
        median = (values[mid - 1] + values[mid]) / 2
    return {
        "estado": "comparacion",
        "muestra": len(usable),
        "minimo": min_products,
        "mediana_baja": median,
        "nota": (
            "Comparación con la mediana de bajas observadas en la categoría. "
            "El mínimo de muestra es un umbral no validado. No implica viralidad."
        ),
    }


def in_window(points: list[ObservationPoint], start: datetime, end: datetime) -> list[ObservationPoint]:
    return [p for p in points if start <= p.observed_at <= end]
