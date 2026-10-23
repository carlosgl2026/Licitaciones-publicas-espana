"""Orquestación de la ingesta: descarga de cada fuente/periodo, parseo y
volcado en la base de datos, con detección de novedades.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import date, timedelta
from pathlib import Path

import requests

from . import config, db, descarga, organos
from .codice import leer_zip
from .fuentes import Fuente
from .modelo import Baja, ResultadoIngesta

log = logging.getLogger("ginebra.ingesta")

COMMIT_CADA = 2000
RETENER_MESES = 12   # expedientes sin actualizaciones desde hace más tiempo se podan


def ingerir_zip(
    conn: sqlite3.Connection,
    ruta: Path,
    fuente: Fuente,
    periodo: str,
    registrar_novedades: bool = True,
) -> ResultadoIngesta:
    """Vuelca un ZIP ya descargado en la base de datos.

    Con `registrar_novedades=False` (primera carga de una fuente) se guarda
    todo sin marcar nada como nuevo: si no, el primer día aparecerían miles
    de "novedades" que en realidad son el histórico.
    """
    res = ResultadoIngesta(fuente=fuente.id, periodo=periodo)
    visto_en = db.ahora_iso()
    ids_vistos: set[str] = set()
    pendientes = 0

    for item in leer_zip(ruta, fuente.id):
        if isinstance(item, Baja):
            if db.registrar_baja(conn, item):
                res.bajas += 1
                if registrar_novedades:
                    db.anotar_novedad(conn, item.id, "baja")
            continue

        res.entradas += 1
        ids_vistos.add(item.id)
        resultado, estado_anterior = db.guardar(conn, item, visto_en)
        if resultado == "nuevo":
            res.nuevos += 1
            res.ids_nuevos.add(item.id)
            if registrar_novedades:
                db.anotar_novedad(conn, item.id, "nuevo")
        elif resultado == "actualizado":
            res.actualizados += 1
            res.ids_actualizados.add(item.id)
            if registrar_novedades:
                db.anotar_novedad(conn, item.id, "actualizado", estado_anterior)

        pendientes += 1
        if pendientes >= COMMIT_CADA:
            conn.commit()
            pendientes = 0
            log.info("... %s entradas procesadas (%s nuevas, %s actualizadas)",
                     f"{res.entradas:,}", f"{res.nuevos:,}", f"{res.actualizados:,}")

    res.expedientes = len(ids_vistos)
    db.registrar_ejecucion(conn, fuente.id, periodo, res.entradas, res.expedientes,
                           res.nuevos, res.actualizados, res.bajas)
    conn.commit()
    log.info("%s %s: %s entradas, %s expedientes, %s nuevos, %s actualizados, %s bajas",
             fuente.id, periodo, f"{res.entradas:,}", f"{res.expedientes:,}",
             f"{res.nuevos:,}", f"{res.actualizados:,}", f"{res.bajas:,}")
    return res


def fuente_tiene_datos(conn: sqlite3.Connection, fuente: Fuente) -> bool:
    fila = conn.execute("SELECT 1 FROM licitaciones WHERE fuente = ? LIMIT 1", (fuente.id,)).fetchone()
    return fila is not None


def periodos_para(fuente: Fuente, periodos: list[str] | None, anios: list[int] | None,
                  hoy: date | None) -> list[str]:
    if anios:
        return [str(a) for a in anios]
    if periodos:
        if fuente.mensual:
            return list(periodos)
        # fuente solo anual: convertimos AAAAMM -> AAAA sin repetir
        vistos: list[str] = []
        for p in periodos:
            a = p[:4]
            if a not in vistos:
                vistos.append(a)
        return vistos
    return fuente.periodos_auto(hoy)


def actualizar(
    conn: sqlite3.Connection,
    fuentes: list[Fuente],
    periodos: list[str] | None = None,
    anios: list[int] | None = None,
    hoy: date | None = None,
    cache_dir: Path | None = None,
    conservar_zips: bool = False,
    refrescar_organos: bool = True,
    retener_meses: int = RETENER_MESES,
    sesion: requests.Session | None = None,
) -> list[ResultadoIngesta]:
    """Ejecución completa: directorio de órganos + cada fuente y periodo + poda."""
    cache_dir = cache_dir or config.CACHE_DIR
    sesion = sesion or descarga.nueva_sesion()
    db.limpiar_novedades(conn)
    conn.commit()
    resultados: list[ResultadoIngesta] = []

    if refrescar_organos:
        try:
            ruta = organos.descargar_directorio(sesion)
            organos.cargar_directorio(conn, ruta)
        except Exception as e:  # el directorio es un extra: no debe parar la ingesta
            log.warning("No se pudo actualizar el directorio de órganos: %s", e)

    for fuente in fuentes:
        primera_carga = not fuente_tiene_datos(conn, fuente)
        if primera_carga:
            log.info("Primera carga de la fuente %s: no se marcarán novedades", fuente.id)
        for periodo in periodos_para(fuente, periodos, anios, hoy):
            destino = cache_dir / fuente.nombre_fichero(periodo)
            try:
                descarga.descargar(fuente.url(periodo), destino, sesion=sesion)
            except descarga.NoDisponible as e:
                log.info("%s", e)
                continue
            except Exception as e:
                log.error("No se pudo descargar %s %s: %s", fuente.id, periodo, e)
                continue
            try:
                resultados.append(ingerir_zip(conn, destino, fuente, periodo,
                                              registrar_novedades=not primera_carga))
            finally:
                if not conservar_zips:
                    destino.unlink(missing_ok=True)

    if retener_meses:
        limite = (hoy or date.today()).replace(day=1)
        for _ in range(retener_meses):
            limite = (limite - timedelta(days=1)).replace(day=1)
        podados = db.podar(conn, limite.strftime("%Y-%m-%dT00:00:00"))
        if podados:
            log.info("Poda: %s expedientes sin actualizar desde %s", f"{podados:,}", limite.isoformat())
            conn.commit()
            conn.execute("VACUUM")

    db.poner_meta(conn, "ultima_actualizacion", db.ahora_iso())
    conn.commit()
    return resultados
