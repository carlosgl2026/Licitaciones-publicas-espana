import json

from ginebra.codice import _num, leer_atom, leer_zip
from ginebra.modelo import Baja, Licitacion


def test_num():
    assert _num("4949909.74") == 4949909.74
    assert _num("5989390,79") == 5989390.79
    assert _num("1.234.567,89") == 1234567.89
    assert _num("25000") == 25000.0
    assert _num("") is None
    assert _num("n/a") is None


def test_lee_entradas_y_bajas(fixture_atom):
    items = list(leer_atom(str(fixture_atom), "agregadas"))
    bajas = [i for i in items if isinstance(i, Baja)]
    lics = [i for i in items if isinstance(i, Licitacion)]
    assert len(bajas) == 2
    assert all(b.motivo == "CERRADA" and b.fecha.startswith("2026-10-22") for b in bajas)
    assert bajas[0].id.startswith("https://contrataciondelestado.es/sindicacion/PlataformasAgregadasSinMenores/")
    assert len(lics) == 3


def test_entrada_adjudicada(fixture_atom):
    lic = [i for i in leer_atom(str(fixture_atom), "agregadas") if isinstance(i, Licitacion)][0]
    assert lic.id == "https://contrataciondelestado.es/sindicacion/PlataformasAgregadasSinMenores/20744285"
    assert lic.fuente == "agregadas"
    assert lic.expediente == "CU-2026-1766"
    assert lic.estado == "ADJ"                       # viene en el namespace cbc-place-ext
    assert lic.url.startswith("https://contractaciopublica.cat/")
    assert lic.updated == "2026-10-22T02:00:08.361+02:00"
    assert lic.organo == "Departament de Cultura"
    assert lic.organo_id_plataforma == "202231"      # schemeName ID_OC_PLAT
    assert lic.plataforma_agregada == "Generalitat de Catalunya"
    assert lic.plataforma_agregada_id == "62"
    assert lic.jerarquia == "Departaments i sector públic de la Generalitat de Catalunya"
    assert lic.tipo_contrato == "1"
    assert lic.valor_estimado == 25000.0
    assert lic.presupuesto_sin_iva == 25000.0
    assert lic.presupuesto_con_iva is None
    assert lic.cpv == "92311000" and lic.cpvs == "92311000"
    assert lic.lugar_nuts == "ES51"
    assert lic.procedimiento == "3"
    assert lic.tramitacion == "1"
    assert lic.resultado == "8"
    assert lic.num_licitadores == 1
    assert lic.adjudicatario.startswith("Rocío Santa Cruz")
    assert lic.adjudicatario_nif == "B66588252"
    assert lic.importe_adjudicacion == 25000.0
    assert lic.ultimo_anuncio == "DOC_CAN_ADJ"
    assert lic.fecha_ultimo_anuncio == "2026-10-21"
    assert lic.fecha_primera_publicacion == "2026-10-21"
    assert json.loads(lic.anuncios) == [{"tipo": "DOC_CAN_ADJ", "medio": "Perfil del contratante", "fecha": "2026-10-21"}]
    assert lic.objeto.startswith("Adquisició de l'obra")


def test_entrada_en_plazo_con_lotes(fixture_atom):
    lics = [i for i in leer_atom(str(fixture_atom), "agregadas") if isinstance(i, Licitacion)]
    lic = [l for l in lics if l.estado == "PUB"][0]
    assert lic.num_lotes >= 2
    assert lic.plazo_presentacion.startswith("20") and "T" in lic.plazo_presentacion
    assert lic.pliego_administrativo_url.startswith("http")
    assert lic.fecha_anuncio_licitacion
    assert ";" in lic.cpvs and lic.cpv == lic.cpvs.split(";")[0]
    assert lic.resultado == "" and lic.importe_adjudicacion is None


def test_entrada_comunidad_de_madrid(fixture_atom):
    lics = [i for i in leer_atom(str(fixture_atom), "agregadas") if isinstance(i, Licitacion)]
    lic = [l for l in lics if l.plataforma_agregada == "Comunidad de Madrid"][0]
    assert lic.plataforma_agregada_id == "121"
    assert lic.url.startswith("https://contratos-publicos.comunidad.madrid/")
    assert lic.organo


def test_leer_zip_ignora_otros_ficheros(fixture_zip):
    items = list(leer_zip(fixture_zip, "agregadas"))
    assert len([i for i in items if isinstance(i, Licitacion)]) == 3
    assert len([i for i in items if isinstance(i, Baja)]) == 2
