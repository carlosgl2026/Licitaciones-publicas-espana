from datetime import date

import pytest

from ginebra.fuentes import FUENTES, fuentes_por_defecto, resolver_fuentes


def test_urls_oficiales():
    assert FUENTES["placsp"].url("2026") == (
        "https://contrataciondelsectorpublico.gob.es/sindicacion/sindicacion_643/"
        "licitacionesPerfilesContratanteCompleto3_2026.zip")
    assert FUENTES["agregadas"].url("202610").endswith("sindicacion_1044/PlataformasAgregadasSinMenores_202610.zip")
    assert FUENTES["menores"].url("202610").endswith("sindicacion_1143/contratosMenoresPerfilesContratantes_202610.zip")
    assert FUENTES["encargos"].url("2026").endswith("sindicacion_1383/EMP_SectorPublico_2026.zip")
    assert FUENTES["consultas"].url("2026").endswith("sindicacion_1403/CPM_SectorPublico_2026.zip")


def test_periodos_mensuales():
    f = FUENTES["placsp"]
    assert f.periodos_auto(date(2026, 10, 23)) == ["202610"]
    assert f.periodos_auto(date(2026, 11, 1)) == ["202610", "202611"]
    assert f.periodos_auto(date(2026, 11, 3)) == ["202610", "202611"]
    assert f.periodos_auto(date(2026, 11, 4)) == ["202611"]
    assert f.periodos_auto(date(2027, 1, 2)) == ["202612", "202701"]


def test_periodos_anuales():
    f = FUENTES["encargos"]
    assert f.periodos_auto(date(2026, 10, 23)) == ["2026"]
    assert f.periodos_auto(date(2027, 1, 2)) == ["2026", "2027"]
    assert f.periodos_auto(date(2027, 1, 15)) == ["2027"]


def test_resolver():
    assert [f.id for f in fuentes_por_defecto()] == ["placsp", "agregadas"]
    assert [f.id for f in resolver_fuentes(["menores"])] == ["menores"]
    assert [f.id for f in resolver_fuentes(None)] == ["placsp", "agregadas"]
    with pytest.raises(ValueError):
        resolver_fuentes(["inventada"])
