"""Interfaz de línea de comandos: `python -m ginebra <orden>` o `ginebra <orden>`."""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date
from pathlib import Path

from . import __version__, config, db, exportar, fuentes as mod_fuentes, ingesta, organos


def _logging(verboso: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verboso else logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stderr,
    )


def cmd_fuentes(_args) -> int:
    for f in mod_fuentes.FUENTES.values():
        marca = "*" if f.por_defecto else " "
        print(f"{marca} {f.id:10s} feed {f.sindicacion:<5d} {'mensual' if f.mensual else 'anual  '}  desde {f.desde}  {f.nombre}")
        print(f"             {f.descripcion}")
        print(f"             {f.url(f.periodos_auto()[-1])}")
    print("\n* = incluida por defecto en `actualizar`")
    return 0


def cmd_actualizar(args) -> int:
    fuentes = mod_fuentes.resolver_fuentes(args.fuentes)
    conn = db.conectar()
    try:
        resultados = ingesta.actualizar(
            conn, fuentes, periodos=args.periodos, anios=args.anios, hoy=date.today(),
            conservar_zips=args.conservar_zips, refrescar_organos=not args.sin_organos,
            retener_meses=args.retener_meses,
        )
        if not resultados:
            logging.getLogger("ginebra").error("No se ingirió ninguna fuente")
            return 1
        res = exportar.exportar_todo(conn, dias=args.dias)
        logging.getLogger("ginebra").info("Exportación: %s", res)
    finally:
        conn.close()
    return 0


def cmd_ingerir(args) -> int:
    fuente = mod_fuentes.FUENTES[args.fuente]
    conn = db.conectar()
    try:
        primera = not ingesta.fuente_tiene_datos(conn, fuente)
        if args.novedades is None:
            registrar = not primera
        else:
            registrar = args.novedades
        if args.limpiar_novedades:
            db.limpiar_novedades(conn)
        for ruta in args.zips:
            ingesta.ingerir_zip(conn, Path(ruta), fuente, args.periodo or Path(ruta).stem,
                                registrar_novedades=registrar)
        db.poner_meta(conn, "ultima_actualizacion", db.ahora_iso())
        conn.commit()
        if not args.sin_exportar:
            exportar.exportar_todo(conn, dias=args.dias)
    finally:
        conn.close()
    return 0


def cmd_exportar(args) -> int:
    conn = db.conectar()
    try:
        res = exportar.exportar_todo(conn, dias=args.dias)
        print(res)
    finally:
        conn.close()
    return 0


def cmd_organos(args) -> int:
    conn = db.conectar()
    try:
        ruta = Path(args.fichero) if args.fichero else organos.descargar_directorio()
        n = organos.cargar_directorio(conn, ruta)
        print(f"{n} órganos cargados desde {ruta}")
    finally:
        conn.close()
    return 0


def cmd_resumen(_args) -> int:
    conn = db.conectar()
    try:
        r = db.resumen(conn)
        print(f"Base de datos: {config.DB_PATH}")
        print(f"Expedientes: {r['total']:,}  por fuente: {r['por_fuente']}")
        print(f"Actualizaciones entre {r['updated_min']} y {r['updated_max']}")
        print(f"Última actualización: {db.leer_meta(conn, 'ultima_actualizacion') or '-'}")
        nov = db.novedades(conn)
        tipos = {}
        for n in nov.values():
            tipos[n['tipo']] = tipos.get(n['tipo'], 0) + 1
        print(f"Novedades de la última ejecución: {tipos or 0}")
        print(f"Órganos en el directorio: {conn.execute('SELECT COUNT(*) FROM organos').fetchone()[0]:,}")
        for e in conn.execute("SELECT * FROM ejecuciones ORDER BY ejecutado_en DESC LIMIT 10"):
            print(f"  {e['ejecutado_en']}  {e['fuente']:10s} {e['periodo']:8s} entradas={e['entradas']:,} "
                  f"expedientes={e['expedientes']:,} nuevos={e['nuevos']:,} actualizados={e['actualizados']:,} bajas={e['bajas']:,}")
    finally:
        conn.close()
    return 0


def construir_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ginebra", description=__doc__)
    p.add_argument("--version", action="version", version=f"ginebra {__version__}")
    p.add_argument("-v", "--verboso", action="store_true")
    sub = p.add_subparsers(dest="orden", required=True)

    s = sub.add_parser("actualizar", help="descarga los feeds, actualiza la base de datos y exporta")
    s.add_argument("--fuentes", nargs="+", metavar="ID", help=f"fuentes a procesar ({', '.join(mod_fuentes.FUENTES)}); por defecto las marcadas")
    s.add_argument("--periodos", nargs="+", metavar="AAAAMM", help="meses concretos (fuentes mensuales); por defecto el mes en curso")
    s.add_argument("--anios", nargs="+", type=int, metavar="AAAA", help="usar los ZIP anuales de estos años (carga inicial)")
    s.add_argument("--dias", type=int, default=exportar.VENTANA_DIAS, help="ventana de días para la exportación")
    s.add_argument("--conservar-zips", action="store_true", help="no borrar los ZIP descargados de cache/")
    s.add_argument("--sin-organos", action="store_true", help="no refrescar el directorio de órganos")
    s.add_argument("--retener-meses", type=int, default=ingesta.RETENER_MESES,
                   help="podar expedientes sin actualizar desde hace más de N meses (0 = no podar)")
    s.set_defaults(func=cmd_actualizar)

    s = sub.add_parser("ingerir", help="vuelca uno o más ZIP ya descargados")
    s.add_argument("zips", nargs="+")
    s.add_argument("--fuente", required=True, choices=list(mod_fuentes.FUENTES))
    s.add_argument("--periodo", help="etiqueta del periodo (por defecto, el nombre del fichero)")
    s.add_argument("--novedades", action=argparse.BooleanOptionalAction, default=None,
                   help="forzar que se registren (o no) novedades; por defecto solo si la fuente ya tenía datos")
    s.add_argument("--limpiar-novedades", action="store_true", help="vaciar las novedades antes de ingerir")
    s.add_argument("--sin-exportar", action="store_true")
    s.add_argument("--dias", type=int, default=exportar.VENTANA_DIAS)
    s.set_defaults(func=cmd_ingerir)

    s = sub.add_parser("exportar", help="regenera data/*.json y los feeds a partir de la base de datos")
    s.add_argument("--dias", type=int, default=exportar.VENTANA_DIAS)
    s.set_defaults(func=cmd_exportar)

    s = sub.add_parser("organos", help="descarga y carga el directorio oficial de órganos de contratación")
    s.add_argument("--fichero", help="XLSX local en vez de descargarlo")
    s.set_defaults(func=cmd_organos)

    s = sub.add_parser("fuentes", help="lista las fuentes oficiales conocidas")
    s.set_defaults(func=cmd_fuentes)

    s = sub.add_parser("resumen", help="estado de la base de datos")
    s.set_defaults(func=cmd_resumen)
    return p


def main(argv: list[str] | None = None) -> None:
    args = construir_parser().parse_args(argv)
    _logging(args.verboso)
    sys.exit(args.func(args))
