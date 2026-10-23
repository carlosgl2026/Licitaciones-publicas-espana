"""Criterios de negocio: qué licitaciones interesan y por qué.

Los filtros trabajan sobre objetos `Licitacion` o sobre filas (dict) leídas
de la base de datos, de modo que se pueden aplicar en la exportación sin
volver a ingerir nada si se cambian las palabras clave.

Tres familias de criterios:

1. Tecnología: CPV de servicios TI / software, o palabras clave en el objeto.
2. Tamaño: presupuesto base sin IVA (o valor estimado si no hay presupuesto)
   dentro del rango que encaja con una consultora de ~30 ingenieros.
3. Cercanía: Comunidad de Madrid (NUTS, código postal, municipio, plataforma
   agregada, jerarquía) u organismo de ámbito estatal sin sede conocida fuera
   de Madrid.

Y una alerta aparte, sin filtro de tamaño ni cercanía: menciones a nube
pública (cualquier proveedor) en cualquier lugar de España.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .texto import normalizar


def _g(obj: Any, campo: str, por_defecto=None):
    if isinstance(obj, dict):
        return obj.get(campo, por_defecto)
    return getattr(obj, campo, por_defecto)


@dataclass(frozen=True)
class Criterios:
    cpv_prefijos: tuple[str, ...] = (
        "72",   # servicios TI: programación, consultoría, integración, hosting, datos
        "48",   # paquetes de software y sistemas de información
    )
    palabras_tecnologia: tuple[str, ...] = (
        "desarrollo de software", "desarrollo a medida", "fabrica de software",
        "aplicacion web", "aplicacion movil", "app movil", "aplicaciones moviles",
        "consultoria informatica", "consultoria ti", "consultoria tecnologica",
        "sistema de informacion", "sistemas de informacion",
        "integracion de sistemas", "arquitectura de software",
        "mantenimiento de aplicaciones", "mantenimiento evolutivo",
        "transformacion digital", "plataforma digital", "administracion electronica",
        "base de datos", "erp", "crm", "microservicios", "api rest", "devops",
        "ciberseguridad", "seguridad informatica", "seguridad de la informacion",
        "digitalizaci", "informatiz",
        # IA / datos
        "inteligencia artificial", "aprendizaje automatico", "machine learning",
        "modelos de lenguaje", "modelo de lenguaje", "llm", "gpt",
        "procesamiento del lenguaje natural", "lenguaje natural", "pln", "nlp",
        "vision artificial", "vision por computador", "chatbot", "asistente virtual",
        "asistente de voz", "sintesis de voz", "reconocimiento de voz", "voz sintetica",
        "big data", "ciencia de datos", "ingenieria de datos", "data engineering",
        "analisis de datos", "analitica de datos", "gobierno del dato",
        "almacen de datos", "data warehouse", "pipeline de datos", "etl",
        # nube / partners
        "nube", "cloud computing", "computacion en la nube",
        "google cloud", "gcp", "google workspace",
        "clickhouse", "elevenlabs",
    )
    importe_min: float = 100_000
    importe_max: float = 5_000_000

    nuts_cercania: tuple[str, ...] = ("ES30",)   # Comunidad de Madrid
    cp_prefijos: tuple[str, ...] = ("28",)         # provincia de Madrid
    municipios: tuple[str, ...] = (
        "madrid", "alcala de henares", "alcorcon", "alcobendas", "aranjuez",
        "arganda del rey", "boadilla del monte", "coslada", "collado villalba",
        "colmenar viejo", "fuenlabrada", "getafe", "leganes", "majadahonda",
        "mostoles", "parla", "pozuelo de alarcon", "rivas-vaciamadrid",
        "rivas vaciamadrid", "san sebastian de los reyes", "las rozas",
        "torrejon de ardoz", "tres cantos", "valdemoro", "villaviciosa de odon",
        "san fernando de henares", "pinto", "navalcarnero", "galapagar",
        "torrelodones", "algete", "mejorada del campo", "ciempozuelos",
    )
    plataformas_cercanas: tuple[str, ...] = ("comunidad de madrid", "ayuntamiento de madrid")
    organismos_nacionales: tuple[str, ...] = (
        "ministerio de", "ministerios", "secretaria de estado", "subsecretaria",
        "agencia estatal de administracion tributaria", "aeat",
        "administracion general del estado",
        "tesoreria general de la seguridad social", "instituto nacional de la seguridad social",
        "inss", "servicio publico de empleo estatal", "sepe", "imserso",
        "direccion general de trafico", "guardia civil", "policia nacional",
        "confederacion hidrografica", "renfe", "adif", "aena", "correos y telegrafos",
        "red.es", "enaire", "tragsa", "ineco", "paradores",
        "consejo general del poder judicial", "tribunal supremo", "audiencia nacional",
        "fiscalia general del estado", "congreso de los diputados", "senado",
        "defensor del pueblo", "tribunal de cuentas", "consejo de estado",
        "banco de espana", "comision nacional de los mercados y la competencia", "cnmc",
        "instituto de credito oficial",
        "agencia estatal consejo superior de investigaciones cientificas", "csic",
        "instituto nacional de estadistica",
        "mutua colaboradora con la seguridad social",
    )

    palabras_nube: tuple[str, ...] = (
        "nube publica", "nube privada", "nube hibrida", "cloud publico",
        "cloud privado", "cloud hibrido", "computacion en la nube",
        "cloud computing", "migracion a la nube", "migracion cloud",
        "alojamiento en la nube", "hosting cloud", "servidores en la nube",
        "almacenamiento en la nube", "infraestructura en la nube",
        "nube corporativa", "nube gubernamental", "centro de datos en la nube",
        "iaas", "paas", "saas", "infraestructura como servicio",
        "plataforma como servicio", "software como servicio",
        "google cloud", "amazon web services", "aws", "microsoft azure",
        "azure", "oracle cloud", "ovhcloud", "ovh cloud", "ibm cloud",
        "alibaba cloud", "vmware cloud", "ionos cloud",
        "clickhouse", "elevenlabs",
        "nube", "cloud",
    )

    # Palabras que, por ser muy cortas, solo cuentan como palabra completa.
    solo_palabra_completa: tuple[str, ...] = (
        "erp", "crm", "etl", "llm", "gpt", "pln", "nlp", "gcp", "aws", "nube", "cloud",
        "azure", "iaas", "paas", "saas", "inss", "sepe", "aeat", "csic", "cnmc",
        "adif", "aena", "renfe", "ineco", "tragsa", "enaire", "senado", "pinto", "parla",
        "madrid", "getafe", "coslada", "algete", "leganes", "mostoles", "alcorcon",
    )


CRITERIOS = Criterios()

_regex_cache: dict[str, re.Pattern] = {}


def _contiene(texto_norm: str, palabra_norm: str, completa: bool) -> bool:
    if not completa:
        return palabra_norm in texto_norm
    patron = _regex_cache.get(palabra_norm)
    if patron is None:
        patron = re.compile(r"(?<![a-z0-9])" + re.escape(palabra_norm) + r"(?![a-z0-9])")
        _regex_cache[palabra_norm] = patron
    return patron.search(texto_norm) is not None


def _primera_coincidencia(texto: str, palabras: tuple[str, ...], c: Criterios) -> str:
    texto_norm = normalizar(texto)
    if not texto_norm:
        return ""
    for p in palabras:
        p_norm = normalizar(p)
        if _contiene(texto_norm, p_norm, p_norm in c.solo_palabra_completa):
            return p
    return ""


# --- Tecnología -----------------------------------------------------------------

def motivo_tecnologia(lic, c: Criterios = CRITERIOS) -> str:
    cpvs = [x for x in ((_g(lic, "cpvs") or "").split(";") + [_g(lic, "cpv") or ""]) if x]
    for cpv in cpvs:
        if cpv.startswith(c.cpv_prefijos):
            return f"CPV {cpv}"
    palabra = _primera_coincidencia(
        f"{_g(lic, 'objeto', '')} {_g(lic, 'titulo', '')}", c.palabras_tecnologia, c)
    return f"menciona «{palabra}»" if palabra else ""


def es_tecnologia(lic, c: Criterios = CRITERIOS) -> bool:
    return bool(motivo_tecnologia(lic, c))


# --- Tamaño -----------------------------------------------------------------

def importe_referencia(lic) -> float | None:
    for campo in ("presupuesto_sin_iva", "valor_estimado"):
        v = _g(lic, campo)
        if v is not None and v != "":
            try:
                return float(v)
            except (TypeError, ValueError):
                continue
    return None


def es_tamano_adecuado(lic, c: Criterios = CRITERIOS) -> bool:
    v = importe_referencia(lic)
    return v is not None and c.importe_min <= v <= c.importe_max


# --- Cercanía ---------------------------------------------------------------

def motivo_cercania(lic, c: Criterios = CRITERIOS) -> str:
    nuts = (_g(lic, "lugar_nuts") or "").upper()
    if nuts and nuts.startswith(c.nuts_cercania):
        return f"lugar de ejecución {nuts}"
    cp = (_g(lic, "organo_cp") or "").strip()
    if cp and cp.startswith(c.cp_prefijos):
        return f"órgano con CP {cp}"
    ciudad = normalizar(_g(lic, "organo_ciudad"))
    if ciudad and ciudad in c.municipios:
        return f"órgano en {_g(lic, 'organo_ciudad')}"
    plataforma = normalizar(_g(lic, "plataforma_agregada"))
    if plataforma and any(p in plataforma for p in c.plataformas_cercanas):
        return f"plataforma {_g(lic, 'plataforma_agregada')}"
    lugar = normalizar(_g(lic, "lugar_texto"))
    if lugar and "madrid" in lugar:
        return f"lugar de ejecución {_g(lic, 'lugar_texto')}"
    jerarquia = normalizar(_g(lic, "jerarquia"))
    if jerarquia and any(f"> {p} >" in f"> {jerarquia} >" for p in c.plataformas_cercanas):
        return "administración de la Comunidad de Madrid"
    if cp and not cp.startswith(c.cp_prefijos):
        # El órgano tiene sede conocida fuera de Madrid (una autoridad portuaria,
        # una delegación provincial...): aunque sea estatal, no cuenta.
        return ""
    organo_y_jerarquia = f"{_g(lic, 'jerarquia', '')} > {_g(lic, 'organo', '')}"
    municipio = _primera_coincidencia(organo_y_jerarquia, c.municipios, c)
    if municipio:
        return f"órgano de {municipio}"
    nacional = _primera_coincidencia(organo_y_jerarquia, c.organismos_nacionales, c)
    if nacional:
        return "organismo estatal"
    return ""


def es_cercania(lic, c: Criterios = CRITERIOS) -> bool:
    return bool(motivo_cercania(lic, c))


# --- Combinados -------------------------------------------------------------

def pasa_filtro_negocio(lic, c: Criterios = CRITERIOS) -> bool:
    return es_tecnologia(lic, c) and es_tamano_adecuado(lic, c) and es_cercania(lic, c)


def motivos(lic, c: Criterios = CRITERIOS) -> dict[str, str]:
    """Explica qué criterios cumple la licitación (para el dashboard y el feed)."""
    out = {}
    t = motivo_tecnologia(lic, c)
    if t:
        out["tecnologia"] = t
    v = importe_referencia(lic)
    if v is not None and c.importe_min <= v <= c.importe_max:
        out["tamano"] = f"{v:,.0f} €".replace(",", ".")
    cerca = motivo_cercania(lic, c)
    if cerca:
        out["cercania"] = cerca
    nube = motivo_nube(lic, c)
    if nube:
        out["nube"] = nube
    return out


# --- Alerta de nube ---------------------------------------------------------

def motivo_nube(lic, c: Criterios = CRITERIOS) -> str:
    return _primera_coincidencia(
        f"{_g(lic, 'objeto', '')} {_g(lic, 'titulo', '')}", c.palabras_nube, c)


def es_alerta_nube(lic, c: Criterios = CRITERIOS) -> bool:
    return bool(motivo_nube(lic, c))
