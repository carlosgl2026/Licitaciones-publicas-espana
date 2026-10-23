"""Parseo de los ficheros Atom + CODICE de los feeds de PLACSP.

Cada ZIP contiene decenas o cientos de ficheros .atom paginados. Cada <entry>
es la versión más reciente conocida de un expediente en el momento de
generar esa página; el mismo expediente (mismo <id>) puede aparecer varias
veces en el ZIP, una por cada anuncio publicado en el periodo. También hay
<at:deleted-entry> para expedientes retirados (anulados o cerrados).

Referencia: "Formato de sindicación y reutilización de datos sobre las
publicaciones en la PLACSP", DGPE, 17/09/2024.
"""

from __future__ import annotations

import json
import logging
import re
import zipfile
from pathlib import Path
from typing import Iterator
from xml.etree import ElementTree as ET

from .modelo import Baja, Licitacion

log = logging.getLogger("ginebra.codice")

NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "at": "http://purl.org/atompub/tombstones/1.0",
    "cbc": "urn:dgpe:names:draft:codice:schema:xsd:CommonBasicComponents-2",
    "cac": "urn:dgpe:names:draft:codice:schema:xsd:CommonAggregateComponents-2",
    "cbc-place-ext": "urn:dgpe:names:draft:codice-place-ext:schema:xsd:CommonBasicComponents-2",
    "cac-place-ext": "urn:dgpe:names:draft:codice-place-ext:schema:xsd:CommonAggregateComponents-2",
}


def _q(nombre: str) -> str:
    """'cac:Party' -> '{urn:...}Party' (notación Clark de ElementTree)."""
    prefijo, local = nombre.split(":")
    return f"{{{NS[prefijo]}}}{local}"


TAG_ENTRY = _q("atom:entry")
TAG_BAJA = _q("at:deleted-entry")
TAG_CFS = _q("cac-place-ext:ContractFolderStatus")

_FECHA = re.compile(r"^(\d{4}-\d{2}-\d{2})")


def _t(el: ET.Element | None, ruta: str) -> str:
    """findtext seguro: '' si el elemento no existe."""
    if el is None:
        return ""
    return (el.findtext(ruta, default="", namespaces=NS) or "").strip()


def _todos(el: ET.Element | None, ruta: str) -> list[ET.Element]:
    return [] if el is None else el.findall(ruta, NS)


def _num(texto: str) -> float | None:
    """'4949909.74' / '5989390,79' / '1.234.567,89' -> float."""
    s = (texto or "").strip().replace(" ", "")
    if not s:
        return None
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".")
    elif "," in s:
        s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def _entero(texto: str) -> int | None:
    try:
        return int(float(texto)) if texto.strip() else None
    except ValueError:
        return None


def _solo_fecha(texto: str) -> str:
    m = _FECHA.match((texto or "").strip())
    return m.group(1) if m else ""


# --- Órgano de contratación --------------------------------------------------

def _parsear_organo(cfs: ET.Element, lic: Licitacion) -> None:
    lcp = cfs.find("cac-place-ext:LocatedContractingParty", NS)
    if lcp is None:
        return
    party = lcp.find("cac:Party", NS)
    lic.organo = _t(party, "cac:PartyName/cbc:Name")
    lic.organo_tipo = _t(lcp, "cbc:ContractingPartyTypeCode")
    lic.organo_actividad = _t(lcp, "cbc:ActivityCode")
    lic.organo_perfil_url = _t(lcp, "cbc:BuyerProfileURIID")
    lic.organo_ciudad = _t(party, "cac:PostalAddress/cbc:CityName")
    lic.organo_cp = _t(party, "cac:PostalAddress/cbc:PostalZone")

    for id_el in _todos(party, "cac:PartyIdentification/cbc:ID"):
        esquema = (id_el.get("schemeName") or "").upper()
        valor = (id_el.text or "").strip()
        if esquema == "NIF":
            lic.organo_nif = valor
        elif esquema == "DIR3":
            lic.organo_dir3 = valor
        elif esquema in ("ID_PLATAFORMA", "ID_OC_PLAT"):
            lic.organo_id_plataforma = valor

    agente = party.find("cac:AgentParty", NS) if party is not None else None
    if agente is not None:
        lic.plataforma_agregada = _t(agente, "cac:PartyName/cbc:Name")
        lic.plataforma_agregada_id = _t(agente, "cac:PartyIdentification/cbc:ID")

    # Ubicación orgánica: cadena de ParentLocatedParty anidados, del más
    # cercano al más lejano. La guardamos del más general al más concreto.
    cadena: list[str] = []
    padre = lcp.find("cac-place-ext:ParentLocatedParty", NS)
    while padre is not None:
        nombre = _t(padre, "cac:PartyName/cbc:Name")
        if nombre:
            cadena.append(nombre)
        padre = padre.find("cac-place-ext:ParentLocatedParty", NS)
    lic.jerarquia = " > ".join(reversed(cadena))


# --- Proyecto, proceso y resultado -----------------------------------------

def _parsear_proyecto(cfs: ET.Element, lic: Licitacion) -> None:
    pp = cfs.find("cac:ProcurementProject", NS)
    if pp is None:
        return
    lic.objeto = _t(pp, "cbc:Name")
    lic.tipo_contrato = _t(pp, "cbc:TypeCode")
    lic.subtipo_contrato = _t(pp, "cbc:SubTypeCode")
    lic.valor_estimado = _num(_t(pp, "cac:BudgetAmount/cbc:EstimatedOverallContractAmount"))
    lic.presupuesto_sin_iva = _num(_t(pp, "cac:BudgetAmount/cbc:TaxExclusiveAmount"))
    lic.presupuesto_con_iva = _num(_t(pp, "cac:BudgetAmount/cbc:TotalAmount"))

    cpvs = []
    for el in _todos(pp, "cac:RequiredCommodityClassification/cbc:ItemClassificationCode"):
        codigo = (el.text or "").strip()
        if codigo and codigo not in cpvs:
            cpvs.append(codigo)
    lic.cpv = cpvs[0] if cpvs else ""
    lic.cpvs = ";".join(cpvs)

    loc = pp.find("cac:RealizedLocation", NS)
    if loc is not None:
        lic.lugar_nuts = _t(loc, "cbc:CountrySubentityCode")
        lic.lugar_texto = _t(loc, "cbc:CountrySubentity")
        lic.lugar_ciudad = _t(loc, "cac:Address/cbc:CityName")

    lic.num_lotes = len(_todos(cfs, "cac:ProcurementProjectLot"))


def _parsear_proceso(cfs: ET.Element, lic: Licitacion) -> None:
    tp = cfs.find("cac:TenderingProcess", NS)
    if tp is None:
        return
    lic.procedimiento = _t(tp, "cbc:ProcedureCode")
    lic.tramitacion = _t(tp, "cbc:UrgencyCode")
    plazo = tp.find("cac:TenderSubmissionDeadlinePeriod", NS)
    if plazo is not None:
        fecha = _solo_fecha(_t(plazo, "cbc:EndDate"))
        hora = _t(plazo, "cbc:EndTime")[:8]
        if fecha and hora:
            lic.plazo_presentacion = f"{fecha}T{hora}"
        elif fecha:
            lic.plazo_presentacion = fecha
        lic.plazo_descripcion = _t(plazo, "cbc:Description")


def _parsear_resultado(cfs: ET.Element, lic: Licitacion) -> None:
    resultados = _todos(cfs, "cac:TenderResult")
    if not resultados:
        return
    nombres: list[str] = []
    nifs: list[str] = []
    total = 0.0
    hay_importe = False
    for r in resultados:
        if not lic.resultado:
            lic.resultado = _t(r, "cbc:ResultCode")
        if lic.num_licitadores is None:
            lic.num_licitadores = _entero(_t(r, "cbc:ReceivedTenderQuantity"))
        for wp in _todos(r, "cac:WinningParty"):
            nombre = _t(wp, "cac:PartyName/cbc:Name")
            nif = _t(wp, "cac:PartyIdentification/cbc:ID")
            if nombre and nombre not in nombres:
                nombres.append(nombre)
            if nif and nif not in nifs:
                nifs.append(nif)
        importe = _num(_t(r, "cac:AwardedTenderedProject/cac:LegalMonetaryTotal/cbc:TaxExclusiveAmount"))
        if importe is not None:
            total += importe
            hay_importe = True
    lic.adjudicatario = "; ".join(nombres)
    lic.adjudicatario_nif = "; ".join(nifs)
    lic.importe_adjudicacion = total if hay_importe else None


def _parsear_documentos(cfs: ET.Element, lic: Licitacion) -> None:
    lic.pliego_administrativo_url = _t(
        cfs, "cac:LegalDocumentReference/cac:Attachment/cac:ExternalReference/cbc:URI")
    lic.pliego_tecnico_url = _t(
        cfs, "cac:TechnicalDocumentReference/cac:Attachment/cac:ExternalReference/cbc:URI")


def _parsear_anuncios(cfs: ET.Element, lic: Licitacion) -> None:
    anuncios: list[dict] = []
    for vni in _todos(cfs, "cac-place-ext:ValidNoticeInfo"):
        tipo = _t(vni, "cbc-place-ext:NoticeTypeCode")
        estados = _todos(vni, "cac-place-ext:AdditionalPublicationStatus")
        if not estados:
            anuncios.append({"tipo": tipo, "medio": "", "fecha": ""})
            continue
        for aps in estados:
            medio = _t(aps, "cbc-place-ext:PublicationMediaName")
            fechas = [
                _solo_fecha(_t(ref, "cbc:IssueDate") or _t(ref, "cbc-place-ext:IssueDate"))
                for ref in _todos(aps, "cac-place-ext:AdditionalPublicationDocumentReference")
            ]
            fechas = [f for f in fechas if f]
            if not fechas:
                anuncios.append({"tipo": tipo, "medio": medio, "fecha": ""})
            for f in fechas:
                anuncios.append({"tipo": tipo, "medio": medio, "fecha": f})
    if not anuncios:
        return
    lic.anuncios = json.dumps(anuncios, ensure_ascii=False)
    con_fecha = [a for a in anuncios if a["fecha"]]
    if con_fecha:
        lic.fecha_primera_publicacion = min(a["fecha"] for a in con_fecha)
        ultimo = max(con_fecha, key=lambda a: a["fecha"])
        lic.ultimo_anuncio = ultimo["tipo"]
        lic.fecha_ultimo_anuncio = ultimo["fecha"]
        licitacion = [a["fecha"] for a in con_fecha if a["tipo"] == "DOC_CN"]
        if licitacion:
            lic.fecha_anuncio_licitacion = min(licitacion)
    else:
        lic.ultimo_anuncio = anuncios[-1]["tipo"]


# --- Entrada completa ----------------------------------------------------------

def parsear_entry(entry: ET.Element, fuente: str) -> Licitacion | None:
    cfs = entry.find("cac-place-ext:ContractFolderStatus", NS)
    if cfs is None:
        return None
    id_ = _t(entry, "atom:id")
    if not id_:
        return None
    enlace = entry.find("atom:link", NS)
    lic = Licitacion(
        id=id_,
        fuente=fuente,
        expediente=_t(cfs, "cbc:ContractFolderID"),
        url=(enlace.get("href", "") if enlace is not None else "").strip(),
        updated=_t(entry, "atom:updated"),
        titulo=_t(entry, "atom:title"),
        estado=_t(cfs, "cbc-place-ext:ContractFolderStatusCode") or _t(cfs, "cbc:ContractFolderStatusCode"),
    )
    _parsear_organo(cfs, lic)
    _parsear_proyecto(cfs, lic)
    _parsear_proceso(cfs, lic)
    _parsear_resultado(cfs, lic)
    _parsear_documentos(cfs, lic)
    _parsear_anuncios(cfs, lic)
    if not lic.objeto:
        lic.objeto = lic.titulo
    return lic


def parsear_baja(el: ET.Element) -> Baja | None:
    ref = (el.get("ref") or "").strip()
    if not ref:
        return None
    comentario = el.find("at:comment", NS)
    motivo = (comentario.get("type") or "").strip() if comentario is not None else ""
    return Baja(id=ref, fecha=(el.get("when") or "").strip(), motivo=motivo)


def leer_atom(fichero, fuente: str) -> Iterator[Licitacion | Baja]:
    """Itera las entradas y bajas de un fichero Atom (ruta o fichero abierto).

    Usa iterparse y libera cada entrada tras procesarla, para que ficheros
    de decenas de MB no se carguen enteros en memoria.
    """
    for _evento, el in ET.iterparse(fichero, events=("end",)):
        if el.tag == TAG_ENTRY:
            lic = parsear_entry(el, fuente)
            el.clear()
            if lic is not None:
                yield lic
        elif el.tag == TAG_BAJA:
            baja = parsear_baja(el)
            el.clear()
            if baja is not None:
                yield baja


def ficheros_atom(zf: zipfile.ZipFile) -> list[str]:
    return sorted(n for n in zf.namelist() if n.lower().endswith(".atom"))


def leer_zip(ruta: Path, fuente: str) -> Iterator[Licitacion | Baja]:
    """Itera todas las entradas y bajas de todos los .atom de un ZIP de PLACSP."""
    with zipfile.ZipFile(ruta) as zf:
        nombres = ficheros_atom(zf)
        log.info("%s: %d ficheros .atom", ruta.name, len(nombres))
        for nombre in nombres:
            try:
                with zf.open(nombre) as f:
                    yield from leer_atom(f, fuente)
            except ET.ParseError as e:
                log.warning("No se pudo parsear %s: %s", nombre, e)
