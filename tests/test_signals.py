from datetime import datetime, timezone

from webapp.signals import ObservationPoint, category_baseline, movement


def point(hour, stock, completeness="complete", provenance="download", day=1):
    return ObservationPoint(
        datetime(2026, 1, day, hour, tzinfo=timezone.utc),
        stock,
        completeness,
        provenance,
    )


def test_baja_cronologica_no_usa_el_minimo():
    result = movement([point(1, 2), point(2, 10), point(3, 9)])
    assert result.unidades_baja == 1
    assert result.reposiciones == 8
    assert result.cambio_neto == 7
    assert result.confianza == "media"
    assert result.as_dict()["venta_confirmada"] is False
    assert "no es una venta confirmada" in result.etiqueta.lower()


def test_reposicion_no_es_venta_negativa():
    result = movement([point(1, 10), point(2, 8), point(3, 12)])
    assert result.unidades_baja == 2
    assert result.reposiciones == 4
    assert result.cambio_neto == 2
    assert "venta negativa" in result.etiqueta or result.unidades_baja == 2


def test_ajuste_revertido_sale_del_proxy():
    result = movement([point(1, 100), point(2, 10), point(3, 95)])
    assert result.unidades_baja == 0
    assert result.reposiciones == 0
    assert result.ajustes_excluidos == 90
    assert "ajuste" in result.etiqueta.lower()


def test_baja_grande_en_un_sentido_sigue_visible_con_confianza_baja():
    result = movement([point(1, 100), point(2, 10)])
    assert result.unidades_baja == 90
    assert result.confianza == "baja"
    assert "no está validado" in " ".join(result.motivos)


def test_legado_no_inventa_confianza():
    result = movement([
        point(1, 10, "unknown", "legacy"),
        point(2, 6, "unknown", "legacy"),
    ])
    assert result.unidades_baja == 4
    assert result.confianza == "desconocida"
    assert result.cobertura == "desconocida"
    assert "no se inventa" in " ".join(result.motivos).lower()


def test_parcial_baja_la_confianza_sin_inventar_ceros():
    result = movement([point(1, 10, "partial"), point(2, 7, "partial")])
    assert result.unidades_baja == 3
    assert result.confianza == "baja"
    assert "no se tomó como cero" in " ".join(result.motivos)


def test_un_solo_punto_es_insuficiente():
    result = movement([point(1, 5)])
    assert result.confianza == "insuficiente"
    assert result.unidades_baja == 0
    assert result.as_dict()["es_proxy_de_demanda"] is False


def test_hueco_largo_limita_la_confianza():
    result = movement([point(1, 10, day=1), point(1, 7, day=4)], gap_hours=36)
    assert result.unidades_baja == 3
    assert result.confianza == "baja"
    assert any("Hueco" in motivo for motivo in result.motivos)


def test_linea_base_exige_muestra():
    pocos = [movement([point(1, 10), point(2, 8)]) for _ in range(3)]
    corto = category_baseline(pocos, min_products=8)
    assert corto["estado"] == "insuficiente"
    assert corto["mediana_baja"] is None
    suficientes = [movement([point(1, 10), point(2, 8)]) for _ in range(8)]
    largo = category_baseline(suficientes, min_products=8)
    assert largo["estado"] == "comparacion"
    assert largo["mediana_baja"] == 2
    assert "no validado" in largo["nota"].lower() or "no validados" in largo["nota"].lower()
