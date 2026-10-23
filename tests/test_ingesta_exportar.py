import json
from datetime import datetime, timezone
from xml.etree import ElementTree as ET

from ginebra import config, db, exportar, ingesta
from ginebra.fuentes import FUENTES
from ginebra.modelo import Licitacion

AHORA = datetime(2026, 10, 23, 8, 0, tzinfo=timezone.utc)


def test_ingesta_desde_zip(entorno, fixture_zip):
    conn = db.conectar()
    f = FUENTES["agregadas"]
    r1 = ingesta.ingerir_zip(conn, fixture_zip, f, "202610", registrar_novedades=False)
    assert (r1.entradas, r1.expedientes, r1.nuevos, r1.actualizados) == (3, 3, 3, 0)
    assert r1.bajas == 0                      # las bajas del fixture son de expedientes que no conocemos
    assert db.novedades(conn) == {}           # primera carga: sin novedades
    r2 = ingesta.ingerir_zip(conn, fixture_zip, f, "202610", registrar_novedades=True)
    assert (r2.nuevos, r2.actualizados) == (0, 0)
    assert ingesta.fuente_tiene_datos(conn, f)
    assert not ingesta.fuente_tiene_datos(conn, FUENTES["placsp"])
    assert conn.execute("SELECT COUNT(*) FROM ejecuciones").fetchone()[0] == 2


def _sembrar(conn):
    datos = [
        Licitacion(id="https://x/1", fuente="placsp", expediente="1/2026", url="https://x/ver/1",
                   updated="2026-10-21T10:00:00+02:00", titulo="Desarrollo de software",
                   objeto="Servicio de desarrollo de software de la sede electrónica", estado="PUB",
                   organo="Ayuntamiento de Getafe", organo_cp="28901", organo_id_plataforma="100",
                   cpv="72000000", presupuesto_sin_iva=250000.0, tipo_contrato="2", procedimiento="1",
                   plazo_presentacion="2026-11-10T14:00:00", fecha_primera_publicacion="2026-10-21",
                   anuncios='[{"tipo":"DOC_CN","medio":"Perfil del contratante","fecha":"2026-10-21"}]'),
        Licitacion(id="https://x/2", fuente="agregadas", expediente="2/2026", url="https://x/ver/2",
                   updated="2026-10-20T10:00:00+02:00", objeto="Migración a la nube de los sistemas sanitarios",
                   estado="EV", organo="Servicio Madrileño de Salud", plataforma_agregada="Comunidad de Madrid",
                   jerarquia="Consejería de Sanidad", cpv="72000000", presupuesto_sin_iva=9_000_000.0),
        Licitacion(id="https://x/3", fuente="placsp", expediente="3/2026", updated="2026-10-19T10:00:00+02:00",
                   objeto="Suministro de vehículos", estado="PUB", organo="Ayuntamiento de Lugo", cpv="34100000",
                   presupuesto_sin_iva=300000.0),
        Licitacion(id="https://x/4", fuente="placsp", expediente="4/2026", updated="2026-06-01T10:00:00+02:00",
                   objeto="Desarrollo de software antiguo", estado="RES", organo="Ministerio de Hacienda", cpv="72000000",
                   presupuesto_sin_iva=300000.0),
    ]
    for d in datos:
        db.guardar(conn, d, "2026-10-22T06:00:00+00:00")
    conn.execute("INSERT INTO organos VALUES ('100','Ayuntamiento de Getafe','Entidades locales','Comunidad de Madrid','Ayuntamientos>Getafe','P2806500C','L01280650','28901','No','Activo')")
    db.anotar_novedad(conn, "https://x/1", "nuevo")
    db.anotar_novedad(conn, "https://x/2", "actualizado", "PUB")
    db.poner_meta(conn, "ultima_actualizacion", "2026-10-23T06:00:00+00:00")
    conn.commit()


def test_exportacion_completa(entorno):
    conn = db.conectar()
    _sembrar(conn)
    res = exportar.exportar_todo(conn, ahora=AHORA)
    assert res["seleccionadas"] == 1 and res["nuevas"] == 1 and res["nube"] == 1
    data = config.DATA_DIR

    latest = json.loads((data / "latest.json").read_text(encoding="utf-8"))
    assert latest["total"] == 1
    r = latest["licitaciones"][0]
    assert r["id"] == "https://x/1"
    assert r["estado_label"] == "En plazo" and r["tipo_contrato_label"] == "Servicios" and r["procedimiento_label"] == "Abierto"
    assert r["sector"] == "Entidades locales" and r["administracion"] == "Comunidad de Madrid" and r["entidad"] == "Ayuntamientos › Getafe"
    assert r["novedad"] == "nuevo"
    assert r["motivos"]["cercania"] == "órgano con CP 28901"
    assert r["anuncios"][0]["tipo"] == "DOC_CN"
    assert isinstance(r["presupuesto_sin_iva"], float)

    nuevas = json.loads((data / "nuevas_hoy.json").read_text(encoding="utf-8"))
    assert [x["id"] for x in nuevas["licitaciones"]] == ["https://x/1"]

    nube = json.loads((data / "alertas_nube.json").read_text(encoding="utf-8"))
    assert nube["total_historico"] == 1 and nube["historico"][0]["id"] == "https://x/2"
    assert nube["historico"][0]["administracion"] == "Comunidad de Madrid"
    assert nube["historico"][0]["sector"] == "Comunidades autónomas"
    assert nube["historico"][0]["novedad"] == "actualizado" and nube["historico"][0]["estado_anterior"] == "PUB"

    atom = ET.parse(data / "feed.atom").getroot()
    A = "{http://www.w3.org/2005/Atom}"
    assert atom.tag == A + "feed"
    assert atom.find(A + "link[@rel='self']").get("href") == "https://example.org/ginebra/data/feed.atom"
    entradas = atom.findall(A + "entry")
    assert len(entradas) == 1
    e = entradas[0]
    assert e.find(A + "id").text == "https://x/1"
    assert "Ayuntamiento de Getafe" in e.find(A + "title").text and "250.000 €" in e.find(A + "title").text
    assert e.find(A + "link").get("href") == "https://x/ver/1"
    assert e.find(A + "updated").text == "2026-10-21T10:00:00+02:00"
    assert "plazo hasta 2026-11-10 14:00" in e.find(A + "summary").text
    assert "<dl>" in e.find(A + "content").text and "Pliego" not in e.find(A + "content").text
    assert {c.get("term") for c in e.findall(A + "category")} >= {"PUB", "2", "Entidades locales", "placsp", "nuevo"}

    nube_atom = ET.parse(data / "feed_nube.atom").getroot()
    assert len(nube_atom.findall(A + "entry")) == 1

    jf = json.loads((data / "feed.json").read_text(encoding="utf-8"))
    assert jf["version"] == "https://jsonfeed.org/version/1.1"
    assert jf["items"][0]["id"] == "https://x/1" and jf["items"][0]["_ginebra"]["expediente"] == "1/2026"

    resumen = json.loads((data / "resumen.json").read_text(encoding="utf-8"))
    assert resumen["novedades_ultima_ejecucion"] == {"nuevo": 1, "actualizado": 1, "baja": 0}
    assert resumen["por_administracion"] == {"Comunidad de Madrid": 1}
    assert resumen["feeds"]["atom"] == "https://example.org/ginebra/data/feed.atom"


def test_clasificar_sin_directorio():
    fila = {"plataforma_agregada": "Generalitat de Catalunya", "jerarquia": "Departament de Salut", "organo": "ICS"}
    assert exportar.clasificar(fila, None) == {"sector": "Comunidades autónomas", "administracion": "Generalitat de Catalunya", "entidad": "Departament de Salut"}
    fila = {"plataforma_agregada": "Generalitat de Catalunya", "jerarquia": "Ajuntament de Barcelona > Gerència", "organo": "Institut Municipal"}
    assert exportar.clasificar(fila, None)["sector"] == "Entidades locales"
    assert exportar.clasificar(fila, None)["entidad"] == "Ajuntament de Barcelona"
    fila = {"plataforma_agregada": "", "jerarquia": "Ministerio de Hacienda > Subsecretaría", "organo": "DGPE"}
    assert exportar.clasificar(fila, None) == {"sector": "", "administracion": "Ministerio de Hacienda", "entidad": "Subsecretaría"}


def test_clasificar_desde_jerarquia_placsp():
    fila = {"jerarquia": "Sector Público > ENTIDADES LOCALES > Comunidad de Madrid > Ayuntamientos > Madrid",
            "organo": "Área de Gobierno de Vicealcaldía"}
    assert exportar.clasificar(fila, None) == {"sector": "Entidades locales", "administracion": "Comunidad de Madrid",
                                               "entidad": "Ayuntamientos › Madrid"}
    fila = {"jerarquia": "Sector Público > ADMINISTRACIÓN GENERAL DEL ESTADO > Ministerio de Hacienda > Subsecretaría de Hacienda",
            "organo": "Dirección General del Patrimonio del Estado"}
    assert exportar.clasificar(fila, None) == {"sector": "Administración General del Estado", "administracion": "Ministerio de Hacienda",
                                               "entidad": "Subsecretaría de Hacienda"}
    fila = {"jerarquia": "Sector Público > COMUNIDADES Y CIUDADES AUTÓNOMAS > Castilla y León", "organo": "Consejería de Sanidad"}
    assert exportar.clasificar(fila, None) == {"sector": "Comunidades autónomas", "administracion": "Castilla y León",
                                               "entidad": "Consejería de Sanidad"}
