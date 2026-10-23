"""Exportación: ficheros JSON para el dashboard y el feed propio (Atom + JSON Feed).

Ficheros generados en data/:

- latest.json        licitaciones que pasan el filtro de negocio, últimos N días
- nuevas_hoy.json    las de latest.json que son novedad en la última ejecución
- alertas_nube.json  menciones a nube pública en toda España (sin filtro de tamaño/cercanía)
- feed.atom          el feed propio (Atom 1.0) con el contenido de latest.json
- feed_nube.atom     feed Atom con las alertas de nube
- feed.json          el feed propio en formato JSON Feed 1.1
- resumen.json       estado general de la base de datos y de la última ejecución
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from html import escape
from pathlib import Path
from xml.etree import ElementTree as ET

from . import codigos, config, db
from .filtros import CRITERIOS, Criterios, es_alerta_nube, motivos, pasa_filtro_negocio
from .organos import Directorio
from .texto import normalizar

log = logging.getLogger("ginebra.exportar")

VENTANA_DIAS = 30
VENTANA_NUBE_DIAS = 180
MAX_FEED = 300
MAX_NUBE = 1000

PALABRAS_LOCAL = ("ayuntamiento", "ajuntament", "udala", "udalak", "concello", "concejo",
                  "diputacion", "diputacio", "cabildo", "consell comarcal", "consell insular",
                  "mancomunidad", "mancomunitat", "entidad local", "entitat local",
                  "comarca", "consorci", "consorcio")


def _ahora() -> datetime:
    return datetime.now(timezone.utc)


# --- Clasificación por administración ---------------------------------------

NIVELES_SECTOR = {
    "administracion general del estado": "Administración General del Estado",
    "comunidades y ciudades autonomas": "Comunidades autónomas",
    "entidades locales": "Entidades locales",
    "otras entidades del sector publico": "Otras entidades del sector público",
}


def _desde_jerarquia_placsp(niveles: list[str], organo: str) -> dict | None:
    """Jerarquía de los órganos alojados en PLACSP:

        Sector Público > ENTIDADES LOCALES > Comunidad de Madrid > Ayuntamientos > Madrid > ...
        Sector Público > ADMINISTRACIÓN GENERAL DEL ESTADO > Ministerio de X > Subsecretaría > ...
    """
    if len(niveles) < 3 or normalizar(niveles[0]) != "sector publico":
        return None
    sector = NIVELES_SECTOR.get(normalizar(niveles[1]), niveles[1].capitalize())
    resto = niveles[2:]
    return {
        "sector": sector,
        "administracion": resto[0],
        "entidad": " › ".join(resto[1:4]) if len(resto) > 1 else organo,
    }


def clasificar(fila: dict, directorio: Directorio | None) -> dict:
    """Añade sector / administracion / entidad a una fila.

    - Plataformas agregadas: la administración es la plataforma de origen
      (Comunidad de Madrid, Generalitat de Catalunya...), y el sector se
      deduce del nombre del órgano y de su jerarquía.
    - Órganos alojados en PLACSP: la ubicación orgánica que trae el propio XML
      ("Sector Público > ENTIDADES LOCALES > Comunidad de Madrid > Ayuntamientos > Getafe").
    - Si no viene, el directorio oficial (sector, primer y segundo nivel).
    - Y si tampoco, lo que haya en la jerarquía.
    """
    plataforma = fila.get("plataforma_agregada") or ""
    jerarquia = fila.get("jerarquia") or ""
    organo = fila.get("organo") or ""
    niveles = [n for n in jerarquia.split(" > ") if n]

    if plataforma:
        texto = normalizar(f"{jerarquia} {organo}")
        if any(p in texto for p in PALABRAS_LOCAL):
            sector = "Entidades locales"
        elif "universi" in texto:
            sector = "Otras entidades del sector público"
        else:
            sector = "Comunidades autónomas"
        entidad = niveles[0] if niveles and sector != "Entidades locales" else organo
        if sector == "Entidades locales":
            for n in niveles + [organo]:
                if any(p in normalizar(n) for p in PALABRAS_LOCAL):
                    entidad = n
                    break
        return {"sector": sector, "administracion": plataforma, "entidad": entidad}

    desde_xml = _desde_jerarquia_placsp(niveles, organo)
    if desde_xml:
        return desde_xml

    d = None
    if directorio is not None:
        d = directorio.buscar(fila.get("organo_id_plataforma") or "", fila.get("organo_dir3") or "",
                              fila.get("organo_nif") or "")
    if d:
        nivel2 = (d.get("nivel2") or "").replace(">", " › ")
        return {
            "sector": d.get("sector") or "",
            "administracion": d.get("nivel1") or nivel2 or organo,
            "entidad": nivel2 or d.get("nombre") or organo,
        }

    return {
        "sector": "",
        "administracion": niveles[0] if niveles else organo,
        "entidad": niveles[-1] if len(niveles) > 1 else organo,
    }


def _etiquetas(fila: dict) -> dict:
    return {
        "estado_label": codigos.etiqueta(codigos.ESTADOS, fila.get("estado")),
        "tipo_contrato_label": codigos.etiqueta(codigos.TIPOS_CONTRATO, fila.get("tipo_contrato")),
        "procedimiento_label": codigos.etiqueta(codigos.PROCEDIMIENTOS, fila.get("procedimiento")),
        "resultado_label": codigos.etiqueta(codigos.RESULTADOS, fila.get("resultado")),
        "ultimo_anuncio_label": codigos.etiqueta(codigos.TIPOS_ANUNCIO, fila.get("ultimo_anuncio")),
        "organo_actividad_label": codigos.etiqueta(codigos.ACTIVIDADES, fila.get("organo_actividad")),
    }


def preparar(fila: dict, novedades: dict[str, dict], directorio: Directorio | None,
             criterios: Criterios = CRITERIOS) -> dict:
    """Convierte una fila de la base de datos en el registro que se publica."""
    out = dict(fila)
    try:
        out["anuncios"] = json.loads(fila.get("anuncios") or "[]")
    except ValueError:
        out["anuncios"] = []
    out.update(_etiquetas(fila))
    out.update(clasificar(fila, directorio))
    nov = novedades.get(fila["id"])
    out["novedad"] = nov["tipo"] if nov else None
    if nov and nov.get("estado_anterior") and not out.get("estado_anterior"):
        out["estado_anterior"] = nov["estado_anterior"]
    out["motivos"] = motivos(fila, criterios)
    return out


# --- Selección -------------------------------------------------------------

def seleccionar(conn: sqlite3.Connection, dias: int, criterio, ahora: datetime | None = None) -> list[dict]:
    ahora = ahora or _ahora()
    desde = (ahora - timedelta(days=dias)).strftime("%Y-%m-%dT00:00:00")
    return [f for f in db.licitaciones_desde(conn, desde) if criterio(f)]


# --- JSON ------------------------------------------------------------------

def _escribir_json(ruta: Path, datos: dict) -> None:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text(json.dumps(datos, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    log.info("%s: %s", ruta.name, ", ".join(f"{k}={v}" for k, v in datos.items() if isinstance(v, int)))


# --- Atom --------------------------------------------------------------------

def _importe(v) -> str:
    try:
        return f"{float(v):,.0f} €".replace(",", ".")
    except (TypeError, ValueError):
        return ""


def _fecha_corta(iso: str) -> str:
    return (iso or "")[:10]


def titulo_entrada(r: dict) -> str:
    partes = [r.get("objeto") or r.get("titulo") or "Sin objeto"]
    if r.get("organo"):
        partes.append(r["organo"])
    importe = _importe(r.get("presupuesto_sin_iva"))
    if importe:
        partes.append(importe)
    return " · ".join(partes)


def resumen_entrada(r: dict) -> str:
    trozos = [r.get("estado_label") or r.get("estado") or ""]
    if r.get("novedad") == "actualizado" and r.get("estado_anterior"):
        trozos[0] = f"{codigos.etiqueta(codigos.ESTADOS, r['estado_anterior'])} → {trozos[0]}"
    if r.get("administracion"):
        trozos.append(r["administracion"])
    if r.get("plazo_presentacion"):
        trozos.append(f"plazo hasta {r['plazo_presentacion'][:16].replace('T', ' ')}")
    if r.get("tipo_contrato_label"):
        trozos.append(r["tipo_contrato_label"])
    return " · ".join(t for t in trozos if t)


def contenido_html(r: dict) -> str:
    filas = [
        ("Órgano", r.get("organo")),
        ("Administración", " › ".join(x for x in (r.get("sector"), r.get("administracion"), r.get("entidad")) if x)),
        ("Expediente", r.get("expediente")),
        ("Estado", r.get("estado_label")),
        ("Presupuesto sin IVA", _importe(r.get("presupuesto_sin_iva"))),
        ("Valor estimado", _importe(r.get("valor_estimado"))),
        ("Tipo", r.get("tipo_contrato_label")),
        ("Procedimiento", r.get("procedimiento_label")),
        ("CPV", r.get("cpvs") or r.get("cpv")),
        ("Lugar", " ".join(x for x in (r.get("lugar_texto"), r.get("lugar_nuts")) if x)),
        ("Plazo de presentación", (r.get("plazo_presentacion") or "").replace("T", " ") or r.get("plazo_descripcion")),
        ("Lotes", str(r["num_lotes"]) if r.get("num_lotes") else ""),
        ("Adjudicatario", r.get("adjudicatario")),
        ("Importe de adjudicación", _importe(r.get("importe_adjudicacion"))),
        ("Último anuncio", " ".join(x for x in (r.get("ultimo_anuncio_label"), _fecha_corta(r.get("fecha_ultimo_anuncio"))) if x)),
        ("Fuente", r.get("fuente")),
        ("Por qué aparece", "; ".join(f"{k}: {v}" for k, v in (r.get("motivos") or {}).items())),
    ]
    partes = ["<dl>"]
    for k, v in filas:
        if v:
            partes.append(f"<dt>{escape(k)}</dt><dd>{escape(str(v))}</dd>")
    partes.append("</dl>")
    enlaces = []
    if r.get("url"):
        enlaces.append(f'<a href="{escape(r["url"])}">Ficha de la licitación</a>')
    if r.get("pliego_administrativo_url"):
        enlaces.append(f'<a href="{escape(r["pliego_administrativo_url"])}">Pliego administrativo</a>')
    if r.get("pliego_tecnico_url"):
        enlaces.append(f'<a href="{escape(r["pliego_tecnico_url"])}">Pliego técnico</a>')
    if r.get("organo_perfil_url"):
        enlaces.append(f'<a href="{escape(r["organo_perfil_url"])}">Perfil del contratante</a>')
    if enlaces:
        partes.append("<p>" + " · ".join(enlaces) + "</p>")
    return "".join(partes)


def _rfc3339(iso: str, ahora: datetime) -> str:
    if not iso:
        return ahora.isoformat(timespec="seconds")
    try:
        d = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return ahora.isoformat(timespec="seconds")
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.isoformat(timespec="seconds")


def generar_atom(registros: list[dict], titulo: str, nombre_fichero: str, subtitulo: str = "",
                 ahora: datetime | None = None) -> bytes:
    ahora = ahora or _ahora()
    site = config.SITE_URL
    ET.register_namespace("", "http://www.w3.org/2005/Atom")
    A = "{http://www.w3.org/2005/Atom}"
    feed = ET.Element(A + "feed")
    ET.SubElement(feed, A + "title").text = titulo
    if subtitulo:
        ET.SubElement(feed, A + "subtitle").text = subtitulo
    ET.SubElement(feed, A + "id").text = config.DATA_URL + nombre_fichero
    ET.SubElement(feed, A + "updated").text = ahora.isoformat(timespec="seconds")
    ET.SubElement(feed, A + "link", rel="self", href=config.DATA_URL + nombre_fichero, type="application/atom+xml")
    ET.SubElement(feed, A + "link", rel="alternate", href=site, type="text/html")
    ET.SubElement(feed, A + "generator", uri="https://github.com/inorganic-software/ginebra").text = "ginebra"
    autor = ET.SubElement(feed, A + "author")
    ET.SubElement(autor, A + "name").text = config.FEED_AUTOR
    ET.SubElement(feed, A + "rights").text = "Datos: Plataforma de Contratación del Sector Público (datos abiertos)."

    for r in registros:
        e = ET.SubElement(feed, A + "entry")
        ET.SubElement(e, A + "id").text = r["id"]
        ET.SubElement(e, A + "title").text = titulo_entrada(r)
        if r.get("url"):
            ET.SubElement(e, A + "link", rel="alternate", href=r["url"])
        ET.SubElement(e, A + "updated").text = _rfc3339(r.get("updated", ""), ahora)
        ET.SubElement(e, A + "published").text = _rfc3339(r.get("first_seen_at", ""), ahora)
        ET.SubElement(e, A + "summary", type="text").text = resumen_entrada(r)
        ET.SubElement(e, A + "content", type="html").text = contenido_html(r)
        for term, label in (
            (r.get("estado"), r.get("estado_label")),
            (r.get("tipo_contrato"), r.get("tipo_contrato_label")),
            (r.get("sector"), r.get("sector")),
            (r.get("administracion"), r.get("administracion")),
            (r.get("fuente"), r.get("fuente")),
            (r.get("novedad"), r.get("novedad")),
        ):
            if term:
                ET.SubElement(e, A + "category", term=str(term), label=str(label or term))
        aut = ET.SubElement(e, A + "author")
        ET.SubElement(aut, A + "name").text = r.get("organo") or "Órgano de contratación"

    ET.indent(feed, space="  ")
    return ET.tostring(feed, encoding="utf-8", xml_declaration=True)


def generar_json_feed(registros: list[dict], titulo: str, nombre_fichero: str,
                      ahora: datetime | None = None) -> dict:
    ahora = ahora or _ahora()
    site = config.SITE_URL
    items = []
    for r in registros:
        items.append({
            "id": r["id"],
            "url": r.get("url") or r["id"],
            "title": titulo_entrada(r),
            "summary": resumen_entrada(r),
            "content_html": contenido_html(r),
            "date_published": _rfc3339(r.get("first_seen_at", ""), ahora),
            "date_modified": _rfc3339(r.get("updated", ""), ahora),
            "authors": [{"name": r.get("organo") or ""}],
            "tags": [t for t in (r.get("estado_label"), r.get("tipo_contrato_label"),
                                 r.get("sector"), r.get("administracion"), r.get("novedad")) if t],
            "_ginebra": {k: v for k, v in r.items() if k not in ("anuncios",)},
        })
    return {
        "version": "https://jsonfeed.org/version/1.1",
        "title": titulo,
        "home_page_url": site,
        "feed_url": config.DATA_URL + nombre_fichero,
        "description": "Licitaciones públicas de tecnología seleccionadas por ginebra a partir de los datos abiertos de PLACSP.",
        "authors": [{"name": config.FEED_AUTOR}],
        "language": "es",
        "items": items,
    }


# --- Exportación completa ---------------------------------------------------

def exportar_todo(conn: sqlite3.Connection, data_dir: Path | None = None, dias: int = VENTANA_DIAS,
                  criterios: Criterios = CRITERIOS, ahora: datetime | None = None) -> dict:
    data_dir = data_dir or config.DATA_DIR
    ahora = ahora or _ahora()
    generado = ahora.isoformat(timespec="seconds")
    novedades = db.novedades(conn)
    directorio = Directorio(conn)
    if not len(directorio):
        directorio = None

    # 1) Filtro de negocio
    negocio = [preparar(f, novedades, directorio, criterios)
               for f in seleccionar(conn, dias, lambda f: pasa_filtro_negocio(f, criterios), ahora)]
    nuevas = [r for r in negocio if r["novedad"]]
    fuentes = {}
    for r in negocio:
        fuentes[r["fuente"]] = fuentes.get(r["fuente"], 0) + 1

    _escribir_json(data_dir / "latest.json", {
        "generado": generado, "ventana_dias": dias, "total": len(negocio),
        "fuentes": fuentes, "licitaciones": negocio,
    })
    nota = None
    if not novedades and db.leer_meta(conn, "ultima_actualizacion"):
        nota = ("Sin novedades registradas en la última ejecución (o primera carga de las fuentes: "
                "a partir de la siguiente ejecución aparecerán aquí solo las nuevas o actualizadas).")
    payload_nuevas = {"generado": generado, "total": len(nuevas), "licitaciones": nuevas}
    if nota:
        payload_nuevas["nota"] = nota
    _escribir_json(data_dir / "nuevas_hoy.json", payload_nuevas)

    # 2) Alertas de nube (sin filtro de tamaño ni cercanía)
    nube = [preparar(f, novedades, directorio, criterios)
            for f in seleccionar(conn, VENTANA_NUBE_DIAS, lambda f: es_alerta_nube(f, criterios), ahora)]
    nube = nube[:MAX_NUBE]
    nube_nuevas = [r for r in nube if r["novedad"]]
    _escribir_json(data_dir / "alertas_nube.json", {
        "generado": generado, "ventana_dias": VENTANA_NUBE_DIAS,
        "total_nuevas": len(nube_nuevas), "total_historico": len(nube),
        "nuevas": nube_nuevas, "historico": nube,
    })

    # 3) Feeds
    feed = negocio[:MAX_FEED]
    (data_dir / "feed.atom").write_bytes(generar_atom(
        feed, config.FEED_TITULO, "feed.atom",
        subtitulo=f"Licitaciones de software, datos e IA que encajan con nosotros. Últimos {dias} días.", ahora=ahora))
    (data_dir / "feed_nube.atom").write_bytes(generar_atom(
        nube[:MAX_FEED], "Ginebra · alertas de nube pública", "feed_nube.atom",
        subtitulo="Licitaciones que mencionan nube pública en cualquier administración española.", ahora=ahora))
    _escribir_json(data_dir / "feed.json", generar_json_feed(feed, config.FEED_TITULO, "feed.json", ahora))

    # 4) Resumen
    conteo = {"nuevo": 0, "actualizado": 0, "baja": 0}
    for n in novedades.values():
        conteo[n["tipo"]] = conteo.get(n["tipo"], 0) + 1
    ejecuciones = [dict(r) for r in conn.execute(
        "SELECT * FROM ejecuciones ORDER BY ejecutado_en DESC LIMIT 20")]
    por_admin: dict[str, int] = {}
    for r in negocio:
        clave = r.get("administracion") or "(sin clasificar)"
        por_admin[clave] = por_admin.get(clave, 0) + 1
    resumen = {
        "generado": generado,
        "ultima_actualizacion": db.leer_meta(conn, "ultima_actualizacion"),
        "base_de_datos": db.resumen(conn),
        "ventana_dias": dias,
        "seleccionadas": len(negocio),
        "novedades_ultima_ejecucion": conteo,
        "alertas_nube": len(nube),
        "por_administracion": dict(sorted(por_admin.items(), key=lambda kv: -kv[1])),
        "ejecuciones": ejecuciones,
        "feeds": {
            "atom": config.DATA_URL + "feed.atom",
            "atom_nube": config.DATA_URL + "feed_nube.atom",
            "json": config.DATA_URL + "feed.json",
        },
    }
    _escribir_json(data_dir / "resumen.json", resumen)
    return {"seleccionadas": len(negocio), "nuevas": len(nuevas), "nube": len(nube), "novedades": conteo}
