from ginebra import db
from ginebra.modelo import Baja, Licitacion


def test_ciclo_de_vida(entorno):
    conn = db.conectar()
    assert db.esta_vacia(conn)
    v1 = Licitacion(id="e1", fuente="placsp", expediente="1/2026", updated="2026-10-01T10:00:00+02:00", estado="PUB", titulo="v1")
    assert db.guardar(conn, v1) == ("nuevo", "")
    assert db.guardar(conn, v1) == ("igual", "PUB")
    v2 = Licitacion(id="e1", fuente="placsp", expediente="1/2026", updated="2026-10-05T10:00:00+02:00", estado="ADJ", titulo="v2")
    assert db.guardar(conn, v2) == ("actualizado", "PUB")
    # una versión más antigua del mismo expediente no pisa la nueva
    assert db.guardar(conn, v1) == ("antiguo", "ADJ")
    fila = conn.execute("SELECT * FROM licitaciones WHERE id='e1'").fetchone()
    assert fila["titulo"] == "v2" and fila["estado"] == "ADJ"
    assert fila["estado_anterior"] == "PUB" and fila["updated_anterior"] == "2026-10-01T10:00:00+02:00"
    assert [c["estado"] for c in db.historial(conn, "e1")] == ["PUB", "ADJ"]

    assert db.registrar_baja(conn, Baja("e1", "2026-10-06T00:00:00+02:00", "CERRADA"))
    assert not db.registrar_baja(conn, Baja("desconocido", "2026-10-06T00:00:00+02:00", "CERRADA"))
    fila = conn.execute("SELECT baja_fecha, baja_motivo FROM licitaciones WHERE id='e1'").fetchone()
    assert fila["baja_motivo"] == "CERRADA"


def test_comparacion_de_fechas_con_zonas_horarias():
    assert db.es_mas_reciente("2026-10-25T02:30:00+01:00", "2026-10-25T03:00:00+02:00")
    assert not db.es_mas_reciente("2026-10-25T02:30:00+02:00", "2026-10-25T03:00:00+02:00")


def test_novedades_y_consultas(entorno):
    conn = db.conectar()
    for i in range(3):
        db.guardar(conn, Licitacion(id=f"e{i}", fuente="agregadas", updated=f"2026-10-0{i+1}T10:00:00+02:00", estado="PUB"))
    db.anotar_novedad(conn, "e0", "nuevo")
    db.anotar_novedad(conn, "e0", "actualizado", "PUB")   # 'nuevo' prevalece
    db.anotar_novedad(conn, "e1", "actualizado", "PUB")
    nov = db.novedades(conn)
    assert nov["e0"]["tipo"] == "nuevo" and nov["e1"]["estado_anterior"] == "PUB"
    assert [r["id"] for r in db.licitaciones_desde(conn, "2026-10-02T00:00:00")] == ["e2", "e1"]
    assert {r["id"] for r in db.licitaciones_por_ids(conn, ["e0", "e2"])} == {"e0", "e2"}
    assert db.resumen(conn)["por_fuente"] == {"agregadas": 3}
    assert db.podar(conn, "2026-10-02T00:00:00") == 1
    db.limpiar_novedades(conn)
    assert db.novedades(conn) == {}
