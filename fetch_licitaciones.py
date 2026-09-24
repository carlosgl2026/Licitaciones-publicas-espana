#!/usr/bin/env python3
"""
fetch_licitaciones.py
======================

Descarga el feed oficial y diario de la Plataforma de Contratación del
Sector Público (PLACSP) español, detecta qué expedientes son nuevos (o se
han actualizado) desde la última vez que se ejecutó el script, y guarda:

  - data/licitaciones.db       -> base de datos SQLite con el histórico completo
  - data/nuevas_hoy.json       -> solo las licitaciones nuevas/actualizadas en esta ejecución
  - data/latest.json           -> últimos N días, listos para el dashboard (dashboard.html)

Fuente de datos
----------------
PLACSP publica el ZIP anual de licitaciones en:

    https://contrataciondelsectorpublico.gob.es/sindicacion/sindicacion_643/
    licitacionesPerfilesContratanteCompleto3_{año}.zip

Ese ZIP contiene ficheros .atom (formato CODICE 2.07) con TODAS las
licitaciones publicadas ese año, y el propio portal indica que "se
actualizan diariamente con los cambios del día anterior". Por eso, la
estrategia de este script es:

  1. Descargar el ZIP del año en curso (y opcionalmente el del año anterior,
     por si hay expedientes a caballo entre diciembre/enero).
  2. Parsear todos los ficheros .atom.
  3. Comparar cada expediente contra lo que ya teníamos guardado en SQLite.
  4. Todo lo que sea nuevo, o cuyo estado/fecha de actualización haya
     cambiado, se marca como "nuevo" en esta ejecución.

Uso
---
    python fetch_licitaciones.py                 # año actual
    python fetch_licitaciones.py --years 2025 2026
    python fetch_licitaciones.py --dashboard-days 30

Pensado para ejecutarse una vez al día (cron, systemd timer, GitHub Actions...).

IMPORTANTE
----------
Este script necesita salir a Internet hacia contrataciondelsectorpublico.gob.es.
No se puede ejecutar dentro del sandbox de este chat (la red está restringida
a registries de paquetes), así que debes correrlo en tu propia máquina,
servidor, o en GitHub Actions (ver .github/workflows/daily.yml incluido).
"""

from __future__ import annotations

import argparse
import io
import json
import sqlite3
import sys
import time
import zipfile
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Optional
from xml.etree import ElementTree as ET

import requests

# ---------------------------------------------------------------------------
# Configuración
# ---------------------------------------------------------------------------

BASE_URL = (
    "https://contrataciondelsectorpublico.gob.es/sindicacion/sindicacion_643/"
    "licitacionesPerfilesContratanteCompleto3_{year}.zip"
)

# Muchos portales de la administración española usan un WAF que detecta tráfico
# "de bot" (sin cabeceras de navegador, o proveniente de rangos de IP de
# centros de datos como los de GitHub Actions/AWS/Azure) y lo estrangula a
# velocidad casi nula en vez de rechazarlo con un error claro. Identificarnos
# con cabeceras de navegador normales suele bastar para evitarlo.
REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    ),
    "Accept": "application/zip, application/octet-stream, */*",
    "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
}

MAX_RETRIES = 3
RETRY_BACKOFF_SECONDS = 15

DATA_DIR = Path(__file__).parent / "data"
DB_PATH = DATA_DIR / "licitaciones.db"
NUEVAS_HOY_PATH = DATA_DIR / "nuevas_hoy.json"
LATEST_PATH = DATA_DIR / "latest.json"
ALERTAS_PARTNERS_PATH = DATA_DIR / "alertas_partners.json"

NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "cbc": "urn:dgpe:names:draft:codice:schema:xsd:CommonBasicComponents-2",
    "cac": "urn:dgpe:names:draft:codice:schema:xsd:CommonAggregateComponents-2",
    "cac-ext": "urn:dgpe:names:draft:codice-place-ext:schema:xsd:CommonAggregateComponents-2",
}

CFS_TAG = f"{{{NS['cac-ext']}}}ContractFolderStatus"


# ---------------------------------------------------------------------------
# Filtro de negocio: desarrollo de software / consultoría TI
# ---------------------------------------------------------------------------
# Ajustado para una consultora de ~30 ingenieros y 3-6M€ de facturación:
# nos interesan desarrollo de software, consultoría TI, integración de
# sistemas, datos/IA, ciberseguridad de aplicaciones... NO nos interesa
# suministro de hardware puro, infraestructura de telecomunicaciones (fibra,
# antenas...), ni videovigilancia — eso lo haría un integrador de
# infraestructura, no una consultora de software.
#
# Combinamos dos señales para no depender de que el organismo haya
# clasificado bien el CPV (en la práctica, muchos lo hacen mal):
#   1. Prefijos de CPV (Vocabulario Común de Contratos Públicos de la UE).
#   2. Palabras clave en el objeto/título del contrato.
# Si CUALQUIERA de las dos coincide, se considera relevante.

TECH_CPV_PREFIXES = (
    "72",   # servicios TI: programación, consultoría, integración de sistemas,
            # hosting, datos, internet... (el grueso de lo relevante)
    "48",   # paquetes de software y sistemas de información
)

TECH_KEYWORDS = (
    "desarrollo de software", "desarrollo a medida", "fábrica de software",
    "aplicación web", "aplicación móvil", "app móvil", "aplicaciones móviles",
    "consultoría informática", "consultoría ti", "consultoría tecnológica",
    "sistema de información", "sistemas de información",
    "integración de sistemas", "arquitectura de software",
    "mantenimiento de aplicaciones", "mantenimiento evolutivo",
    "transformación digital", "plataforma digital", "administración electrónica",
    "base de datos", "erp", "crm", "microservicios", "api rest", "devops",
    "ciberseguridad", "seguridad informática", "seguridad de la información",
    "digitalizaci", "informatiz",
    # IA / datos (especialidad de la empresa)
    "inteligencia artificial", "aprendizaje automático", "machine learning",
    "modelos de lenguaje", "modelo de lenguaje", "llm", "gpt",
    "procesamiento del lenguaje natural", "lenguaje natural", " pln ", "nlp",
    "visión artificial", "visión por computador", "chatbot", "asistente virtual",
    "asistente de voz", "síntesis de voz", "reconocimiento de voz", "voz sintética",
    "big data", "ciencia de datos", "ingeniería de datos", "data engineering",
    "análisis de datos", "analítica de datos", "gobierno del dato",
    "almacén de datos", "data warehouse", "pipeline de datos", "etl",
    # nube / partners tecnológicos concretos de la empresa
    "nube", "cloud computing", "computación en la nube",
    "google cloud", "gcp", "google workspace",
    "clickhouse", "elevenlabs",
)

# ---------------------------------------------------------------------------
# Filtro de geografía: Comunidad de Madrid + organismos de ámbito nacional
# ---------------------------------------------------------------------------
# El feed CODICE no siempre trae un código de región limpio (NUTS) en todos
# los organismos, así que combinamos dos señales:
#   1. Si el XML trae CountrySubentity/CountrySubentityCode, lo usamos (es lo
#      más fiable cuando está presente).
#   2. Si no, buscamos en el propio nombre del órgano contratante: municipios
#      y organismos conocidos de la Comunidad de Madrid, o palabras que
#      delatan un organismo de ámbito estatal (ministerios, agencias
#      estatales...), que casi siempre tienen sede en Madrid.
# Esto es una heurística, no una fuente de verdad geográfica: puede fallar
# con nombres de organismo poco habituales.

MADRID_NUTS_CODES = ("ES30",)  # Comunidad de Madrid en codificación NUTS

MADRID_MUNICIPIOS = (
    "madrid", "alcala de henares", "alcorcon", "alcobendas", "aranjuez",
    "arganda del rey", "boadilla del monte", "coslada", "collado villalba",
    "colmenar viejo", "fuenlabrada", "getafe", "leganes", "majadahonda",
    "mostoles", "parla", "pozuelo de alarcon", "rivas-vaciamadrid",
    "rivas vaciamadrid", "san sebastian de los reyes", "las rozas",
  "torrejon de ardoz", "tres cantos", "valdemoro", "comunidad de madrid",
    "villaviciosa de odon", "san fernando de henares", "pinto", "navalcarnero",
)

NATIONAL_BODY_KEYWORDS = (
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
    "banco de españa", "comision nacional de los mercados y la competencia", "cnmc",
    "instituto de credito oficial",
    "agencia estatal consejo superior de investigaciones cientificas", "csic",
    "instituto nacional de estadistica",
    "mutua colaboradora con la seguridad social",
)

# Rango de importe (sin IVA) que tiene sentido para el tamaño de la empresa.
# Fuera de este rango, se descarta aunque sea claramente de software/TI:
# por debajo, no compensa el esfuerzo de licitar; por encima, probablemente
# exige una capacidad (financiera, de personal) mayor de la que se tiene.
MIN_IMPORTE = 100_000
MAX_IMPORTE = 5_000_000

# Solo nos interesan licitaciones publicadas en los últimos N días.
# Se aplica al EXPORTAR (no al guardar), para que la ventana se recalcule
# cada día relativa a "hoy" en vez de depender de cuándo se guardó el registro.
RECENCY_DAYS = 15


def _parse_fecha(fecha_iso: str) -> Optional[datetime]:
    if not fecha_iso:
        return None
    try:
        return datetime.fromisoformat(fecha_iso.replace("Z", "+00:00"))
    except ValueError:
        return None


def fecha_publicacion(lic_dict: dict) -> Optional[datetime]:
    """Usa 'published' si el feed la trae; si no, cae a 'updated' como aproximación."""
    return _parse_fecha(lic_dict.get("published") or "") or _parse_fecha(lic_dict.get("updated") or "")


def es_reciente(lic_dict: dict, dias: int = RECENCY_DAYS, ahora: Optional[datetime] = None) -> bool:
    fecha = fecha_publicacion(lic_dict)
    if fecha is None:
        return False  # sin fecha fiable, no podemos afirmar que sea reciente
    ahora = ahora or datetime.now(timezone.utc)
    limite = ahora - timedelta(days=dias)
    return fecha >= limite


def _normalize(s: str) -> str:
    """minúsculas + sin tildes, para comparar sin depender de acentos."""
    import unicodedata
    s = unicodedata.normalize("NFKD", s.lower())
    return "".join(c for c in s if not unicodedata.combining(c))


_TECH_KEYWORDS_NORM = tuple(_normalize(k) for k in TECH_KEYWORDS)


def is_tecnologia(lic: "Licitacion") -> bool:
    cpv = (lic.cpv or "").strip()
    if cpv and cpv.startswith(TECH_CPV_PREFIXES):
        return True

    texto = _normalize(f"{lic.objeto} {lic.titulo}")
    return any(kw in texto for kw in _TECH_KEYWORDS_NORM)


def is_tamano_adecuado(lic: "Licitacion") -> bool:
    """True si el importe cae dentro del rango que interesa a la empresa.

    Si no hay importe publicado (habitual en acuerdos marco o fases previas),
    se descarta por precaución: no podemos saber si encaja en el rango.
    """
    raw = (lic.importe_sin_iva or "").strip()
    if not raw:
        return False
    try:
        valor = float(raw)
    except ValueError:
        return False
    return MIN_IMPORTE <= valor <= MAX_IMPORTE


def is_cercania(lic: "Licitacion") -> bool:
    """Madrid (Comunidad de Madrid) + organismos de ámbito nacional.

    Prioriza el código NUTS/región si el XML lo trae (más fiable); si no,
    recurre al nombre del órgano contratante como heurística de respaldo.
    """
    code = (lic.region_code or "").strip().upper()
    if code:
        if any(code.startswith(nuts) for nuts in MADRID_NUTS_CODES):
            return True

    region_texto = _normalize(lic.region_texto or "")
    if region_texto and "madrid" in region_texto:
        return True

    organo_norm = _normalize(lic.organo or "")
    if any(m in organo_norm for m in MADRID_MUNICIPIOS):
        return True
    if any(n in organo_norm for n in NATIONAL_BODY_KEYWORDS):
        return True

    return False


def pasa_filtro_negocio(lic: "Licitacion") -> bool:
    return is_tecnologia(lic) and is_tamano_adecuado(lic) and is_cercania(lic)


# ---------------------------------------------------------------------------
# Alerta de partners tecnológicos (Google Cloud, ClickHouse, ElevenLabs)
# ---------------------------------------------------------------------------
# A diferencia del filtro de negocio, esta alerta se comprueba en TODAS las
# licitaciones nacionales, sin aplicar los filtros de Madrid/tamaño/sector:
# si mañana sale una licitación de Google Cloud en Sevilla por 30.000€, la
# empresa quiere enterarse igual, porque es socio tecnológico concreto.
PARTNER_ALERT_KEYWORDS = (
    "google cloud", "clickhouse", "elevenlabs",
)
_PARTNER_ALERT_KEYWORDS_NORM = tuple(_normalize(k) for k in PARTNER_ALERT_KEYWORDS)


def is_alerta_partner(lic: "Licitacion") -> bool:
    texto = _normalize(f"{lic.objeto} {lic.titulo}")
    return any(kw in texto for kw in _PARTNER_ALERT_KEYWORDS_NORM)


def _t(el: Optional[ET.Element], path: str, ns=NS) -> str:
    """Helper: findtext seguro devolviendo '' si no existe."""
    if el is None:
        return ""
    return (el.findtext(path, default="", namespaces=ns) or "").strip()


# ---------------------------------------------------------------------------
# Modelo de datos
# ---------------------------------------------------------------------------

@dataclass
class Licitacion:
    expediente: str
    titulo: str
    estado: str
    organo: str
    objeto: str
    importe_sin_iva: str
    tipo_contrato: str
    procedimiento: str
    cpv: str
    url: str
    updated: str  # fecha "updated" del entry Atom (ISO)
    fetched_at: str  # cuándo lo vio este script por última vez
    region_code: str = ""     # código NUTS/subentidad si el XML lo trae (ej. "ES30")
    region_texto: str = ""    # nombre de región/provincia en texto libre, si lo trae
    published: str = ""       # fecha "published" del entry Atom (ISO), si existe

    @property
    def key(self) -> str:
        return self.expediente or self.url


# ---------------------------------------------------------------------------
# Descarga y parseo
# ---------------------------------------------------------------------------

def download_year_zip(year: int, connect_timeout: int = 30, read_timeout: int = 60) -> bytes:
    """Descarga en streaming, con reintentos y cabeceras de navegador.

    Usamos streaming en vez de resp.content directo por dos motivos:
      1. Poder loguear cuántos MB llevamos (si no, el log de GitHub Actions
         se queda "mudo" durante varios minutos y parece que está colgado).
      2. Poder cortar con un timeout de LECTURA razonable: si el servidor
         deja de mandar bytes durante más de `read_timeout` segundos,
         requests lanza una excepción en vez de esperar indefinidamente.

    Además reintentamos varias veces: algunos portales públicos estrangulan
    o cortan conexiones que parecen "de bot" de forma intermitente, y un
    segundo intento a veces basta.
    """
    url = BASE_URL.format(year=year)

    last_error: Optional[Exception] = None
    for attempt in range(1, MAX_RETRIES + 1):
        print(f"[fetch] Descargando {url} (intento {attempt}/{MAX_RETRIES}) ...", file=sys.stderr)
        try:
            chunks: list[bytes] = []
            total = 0
            last_log = time.monotonic()

            with requests.get(
                url, stream=True, timeout=(connect_timeout, read_timeout),
                headers=REQUEST_HEADERS,
            ) as resp:
                resp.raise_for_status()
                content_length = resp.headers.get("Content-Length")
                if content_length:
                    print(f"[fetch] Tamaño anunciado: {int(content_length):,} bytes", file=sys.stderr)

                for chunk in resp.iter_content(chunk_size=1024 * 1024):  # 1 MB
                    if not chunk:
                        continue
                    chunks.append(chunk)
                    total += len(chunk)
                    now = time.monotonic()
                    if now - last_log >= 5:  # loguea como mucho cada 5s, no por cada MB
                        print(f"[fetch] ...{total / (1024 * 1024):.1f} MB descargados", file=sys.stderr)
                        last_log = now

            data = b"".join(chunks)
            print(f"[fetch] OK ({len(data):,} bytes)", file=sys.stderr)
            return data

        except requests.RequestException as e:
            last_error = e
            print(f"[fetch] Intento {attempt} falló: {e}", file=sys.stderr)
            if attempt < MAX_RETRIES:
                print(f"[fetch] Reintentando en {RETRY_BACKOFF_SECONDS}s...", file=sys.stderr)
                time.sleep(RETRY_BACKOFF_SECONDS)

    assert last_error is not None
    raise last_error


def iter_entries_from_zip(zip_bytes: bytes) -> Iterable[ET.Element]:
    """Itera sobre todos los <entry> de todos los ficheros .atom del ZIP."""
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        atom_names = [n for n in zf.namelist() if n.endswith(".atom")]
        print(f"[parse] {len(atom_names)} ficheros .atom en el ZIP", file=sys.stderr)
        for name in atom_names:
            try:
                tree = ET.parse(zf.open(name))
            except ET.ParseError as e:
                print(f"[parse] WARN: no se pudo parsear {name}: {e}", file=sys.stderr)
                continue
            root = tree.getroot()
            for entry in root.findall("atom:entry", NS):
                yield entry


def parse_entry(entry: ET.Element) -> Optional[Licitacion]:
    titulo = _t(entry, "atom:title")
    updated = _t(entry, "atom:updated")
    published = _t(entry, "atom:published")  # fecha de publicación real, si el feed la trae
    link_el = entry.find("atom:link", NS)
    url = link_el.get("href", "") if link_el is not None else ""

    cfs = entry.find(f".//{CFS_TAG}")
    if cfs is None:
        return None

    expediente = _t(cfs, "cbc:ContractFolderID")
    estado = _t(cfs, "cbc:ContractFolderStatusCode")

    organo = _t(cfs, ".//cac-ext:LocatedContractingParty//cbc:Name") or _t(
        cfs, ".//cac:LocatedContractingParty//cbc:Name"
    )

    objeto = _t(cfs, ".//cac:ProcurementProject/cbc:Name") or _t(
        cfs, ".//cac-ext:ProcurementProject/cbc:Name"
    )
    tipo_contrato = _t(cfs, ".//cac:ProcurementProject/cbc:TypeCode") or _t(
        cfs, ".//cac-ext:ProcurementProject/cbc:TypeCode"
    )
    importe = _t(cfs, ".//cbc:TaxExclusiveAmount") or _t(cfs, ".//cbc:TotalAmount")
    procedimiento = _t(cfs, ".//cac:TenderingProcess/cbc:ProcedureCode") or _t(
        cfs, ".//cac-ext:TenderingProcess/cbc:ProcedureCode"
    )
    cpv = _t(cfs, ".//cbc:ItemClassificationCode")

    # Región: intentamos varias rutas habituales en CODICE/UBL; ninguna está
    # garantizada, así que probamos varias y nos quedamos con la primera que
    # tenga contenido.
    region_code = (
        _t(cfs, ".//cac-ext:LocatedContractingParty//cac:PostalAddress/cbc:CountrySubentityCode")
        or _t(cfs, ".//cac:LocatedContractingParty//cac:PostalAddress/cbc:CountrySubentityCode")
        or _t(cfs, ".//cac-ext:LocatedContractingParty//cbc:CountrySubentityCode")
    )
    region_texto = (
        _t(cfs, ".//cac-ext:LocatedContractingParty//cac:PostalAddress/cbc:CountrySubentity")
        or _t(cfs, ".//cac:LocatedContractingParty//cac:PostalAddress/cbc:CountrySubentity")
        or _t(cfs, ".//cac-ext:LocatedContractingParty//cbc:CountrySubentity")
    )

    return Licitacion(
        expediente=expediente,
        titulo=titulo,
        estado=estado,
        organo=organo,
        objeto=objeto or titulo,
        importe_sin_iva=importe,
        tipo_contrato=tipo_contrato,
        procedimiento=procedimiento,
        cpv=cpv,
        url=url,
        updated=updated,
        fetched_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        region_code=region_code,
        region_texto=region_texto,
        published=published,
    )


# ---------------------------------------------------------------------------
# Persistencia (SQLite)
# ---------------------------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS licitaciones (
    expediente TEXT PRIMARY KEY,
    titulo TEXT,
    estado TEXT,
    organo TEXT,
    objeto TEXT,
    importe_sin_iva TEXT,
    tipo_contrato TEXT,
    procedimiento TEXT,
    cpv TEXT,
    url TEXT,
    updated TEXT,
    published TEXT,
    first_seen_at TEXT,
    last_seen_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_updated ON licitaciones(updated);
CREATE INDEX IF NOT EXISTS idx_first_seen ON licitaciones(first_seen_at);

CREATE TABLE IF NOT EXISTS alertas_partners (
    expediente TEXT PRIMARY KEY,
    titulo TEXT,
    estado TEXT,
    organo TEXT,
    objeto TEXT,
    importe_sin_iva TEXT,
    cpv TEXT,
    url TEXT,
    updated TEXT,
    published TEXT,
    first_seen_at TEXT,
    last_seen_at TEXT
);
"""


def get_db(db_path: Optional[Path] = None) -> sqlite3.Connection:
    if db_path is None:
        db_path = DB_PATH  # lookup dinámico: respeta overrides hechos en tiempo de ejecución
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)
    try:
        conn.execute("ALTER TABLE licitaciones ADD COLUMN published TEXT DEFAULT ''")
        conn.commit()
    except sqlite3.OperationalError:
        pass  # la columna ya existía (bases de datos creadas antes de este cambio)
    return conn


def upsert_and_detect_new(conn: sqlite3.Connection, lic: Licitacion) -> bool:
    """Inserta/actualiza una licitación. Devuelve True si es nueva o cambió `updated`."""
    cur = conn.execute(
        "SELECT updated FROM licitaciones WHERE expediente = ?", (lic.key,)
    )
    row = cur.fetchone()
    now = lic.fetched_at

    if row is None:
        conn.execute(
            """INSERT INTO licitaciones
               (expediente, titulo, estado, organo, objeto, importe_sin_iva,
                tipo_contrato, procedimiento, cpv, url, updated, published,
                first_seen_at, last_seen_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                lic.key, lic.titulo, lic.estado, lic.organo, lic.objeto,
                lic.importe_sin_iva, lic.tipo_contrato, lic.procedimiento,
                lic.cpv, lic.url, lic.updated, lic.published, now, now,
            ),
        )
        return True

    changed = row[0] != lic.updated
    conn.execute(
        """UPDATE licitaciones SET
             titulo=?, estado=?, organo=?, objeto=?, importe_sin_iva=?,
             tipo_contrato=?, procedimiento=?, cpv=?, url=?, updated=?, published=?, last_seen_at=?
           WHERE expediente=?""",
        (
            lic.titulo, lic.estado, lic.organo, lic.objeto, lic.importe_sin_iva,
            lic.tipo_contrato, lic.procedimiento, lic.cpv, lic.url, lic.updated,
            lic.published, now, lic.key,
        ),
    )
    return changed


def upsert_alerta_partner(conn: sqlite3.Connection, lic: Licitacion) -> bool:
    """Igual que upsert_and_detect_new pero para la tabla de alertas de partners."""
    cur = conn.execute(
        "SELECT updated FROM alertas_partners WHERE expediente = ?", (lic.key,)
    )
    row = cur.fetchone()
    now = lic.fetched_at

    if row is None:
        conn.execute(
            """INSERT INTO alertas_partners
               (expediente, titulo, estado, organo, objeto, importe_sin_iva,
                cpv, url, updated, published, first_seen_at, last_seen_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                lic.key, lic.titulo, lic.estado, lic.organo, lic.objeto,
                lic.importe_sin_iva, lic.cpv, lic.url, lic.updated,
                lic.published, now, now,
            ),
        )
        return True

    changed = row[0] != lic.updated
    conn.execute(
        """UPDATE alertas_partners SET
             titulo=?, estado=?, organo=?, objeto=?, importe_sin_iva=?,
             cpv=?, url=?, updated=?, published=?, last_seen_at=?
           WHERE expediente=?""",
        (
            lic.titulo, lic.estado, lic.organo, lic.objeto, lic.importe_sin_iva,
            lic.cpv, lic.url, lic.updated, lic.published, now, lic.key,
        ),
    )
    return changed


# ---------------------------------------------------------------------------
# Exportación para el dashboard
# ---------------------------------------------------------------------------

def export_json(
    conn: sqlite3.Connection,
    nuevas_keys: set[str],
    dashboard_days: int,
    is_bootstrap: bool,
    max_nuevas: int = 3000,
) -> None:
    cur = conn.execute(
        "SELECT expediente, titulo, estado, organo, objeto, importe_sin_iva, "
        "tipo_contrato, procedimiento, cpv, url, updated, published, first_seen_at "
        "FROM licitaciones ORDER BY updated DESC"
    )
    cols = [d[0] for d in cur.description]
    all_rows_sin_filtrar = [dict(zip(cols, r)) for r in cur.fetchall()]

    total_sin_filtrar = len(all_rows_sin_filtrar)
    all_rows = [r for r in all_rows_sin_filtrar if es_reciente(r)]
    print(f"[export] Filtro de {RECENCY_DAYS} días: {len(all_rows):,} de {total_sin_filtrar:,} "
          f"pasan (el resto se publicó hace más tiempo, o no tiene fecha fiable).", file=sys.stderr)

    if is_bootstrap:
        # Primera ejecución: la base de datos estaba vacía, así que "todo" cuenta
        # técnicamente como nuevo — pero exportar cientos de miles de registros
        # como nuevas_hoy.json no tiene sentido (y GitHub rechaza el push si el
        # archivo supera 100 MB). Sembramos la base de datos en silencio y no
        # marcamos nada como "nuevo" hoy.
        nuevas = []
        nota = (
            f"Primera ejecución: base de datos sembrada con {len(all_rows):,} "
            "expedientes existentes. A partir de mañana, aquí aparecerán solo "
            "las licitaciones realmente nuevas."
        )
        print(f"[export] Bootstrap: {len(all_rows):,} expedientes sembrados, "
              f"0 marcadas como nuevas (ver nota en nuevas_hoy.json)", file=sys.stderr)
    else:
        nuevas_all = [r for r in all_rows if r["expediente"] in nuevas_keys]
        nuevas_all.sort(key=lambda r: r["updated"] or "", reverse=True)
        nuevas = nuevas_all[:max_nuevas]
        nota = None
        if len(nuevas_all) > max_nuevas:
            nota = (
                f"Hubo {len(nuevas_all):,} novedades hoy; se muestran las "
                f"{max_nuevas:,} más recientes para no generar un archivo excesivo."
            )
        print(f"[export] {len(nuevas_all)} nuevas ({len(nuevas)} exportadas) "
              f"-> {NUEVAS_HOY_PATH}", file=sys.stderr)

    payload = {
        "generado": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "total": len(nuevas),
        "licitaciones": nuevas,
    }
    if nota:
        payload["nota"] = nota
    NUEVAS_HOY_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8",
    )

    latest = all_rows[: max(dashboard_days * 200, 500)]  # tope razonable de tamaño
    LATEST_PATH.write_text(
        json.dumps(
            {"generado": datetime.now(timezone.utc).isoformat(timespec="seconds"),
             "total": len(latest), "licitaciones": latest},
            ensure_ascii=False, indent=2,
        ),
        encoding="utf-8",
    )
    print(f"[export] {len(latest)} registros -> {LATEST_PATH}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def export_alertas_partners(
    conn: sqlite3.Connection,
    nuevas_partner_keys: set[str],
    is_bootstrap: bool,
    max_registros: int = 1000,
) -> None:
    """Exporta data/alertas_partners.json: TODAS las licitaciones nacionales
    (sin filtro de Madrid/tamaño) que mencionen Google Cloud, ClickHouse o
    ElevenLabs, marcando cuáles son nuevas/actualizadas desde la última
    ejecución. En el bootstrap no marcamos nada como "nueva" por la misma
    razón que en nuevas_hoy.json.
    """
    cur = conn.execute(
        "SELECT expediente, titulo, estado, organo, objeto, importe_sin_iva, "
        "cpv, url, updated, first_seen_at FROM alertas_partners ORDER BY updated DESC"
    )
    cols = [d[0] for d in cur.description]
    todas = [dict(zip(cols, r)) for r in cur.fetchall()]

    if is_bootstrap:
        nuevas = []
        nota = f"Primera ejecución: {len(todas):,} licitaciones de partners sembradas."
    else:
        nuevas_all = [r for r in todas if r["expediente"] in nuevas_partner_keys]
        nuevas_all.sort(key=lambda r: r["updated"] or "", reverse=True)
        nuevas = nuevas_all[:max_registros]
        nota = None

    payload = {
        "generado": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "total_nuevas": len(nuevas),
        "total_historico": len(todas),
        "nuevas": nuevas,
        "historico": todas[:max_registros],
    }
    if nota:
        payload["nota"] = nota
    ALERTAS_PARTNERS_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    print(f"[export] Alertas de partners: {len(nuevas)} nuevas, {len(todas)} en histórico "
          f"-> {ALERTAS_PARTNERS_PATH}", file=sys.stderr)


def run(years: list[int], dashboard_days: int) -> None:
    conn = get_db()
    is_bootstrap = conn.execute("SELECT COUNT(*) FROM licitaciones").fetchone()[0] == 0
    nuevas_keys: set[str] = set()
    nuevas_partner_keys: set[str] = set()
    total_procesadas = 0

    for year in years:
        try:
            zip_bytes = download_year_zip(year)
        except requests.RequestException as e:
            print(f"[fetch] ERROR descargando el año {year}: {e}", file=sys.stderr)
            continue

        year_processed = 0
        year_skipped = 0
        year_partner_alerts = 0
        last_log = time.monotonic()
        for entry in iter_entries_from_zip(zip_bytes):
            lic = parse_entry(entry)
            if lic is None or not lic.key:
                continue

            # Alerta de partners: se comprueba en TODAS las entradas, sin
            # aplicar el filtro de Madrid/tamaño/sector.
            if is_alerta_partner(lic):
                year_partner_alerts += 1
                if upsert_alerta_partner(conn, lic):
                    nuevas_partner_keys.add(lic.key)

            if not pasa_filtro_negocio(lic):
                year_skipped += 1
                continue
            total_procesadas += 1
            year_processed += 1
            if upsert_and_detect_new(conn, lic):
                nuevas_keys.add(lic.key)

            now = time.monotonic()
            if now - last_log >= 5:  # loguea como mucho cada 5s
                print(f"[parse] ...{year_processed:,} relevantes procesados "
                      f"del año {year} ({year_skipped:,} descartados: no son de software/TI "
                      f"o el importe no encaja en 100k-5M€)",
                      file=sys.stderr)
                last_log = now

        conn.commit()
        print(f"[parse] Año {year} terminado: {year_processed:,} relevantes, "
              f"{year_skipped:,} descartados (fuera de sector o de rango de importe), "
              f"{year_partner_alerts:,} alertas de partners (Google Cloud/ClickHouse/ElevenLabs).",
              file=sys.stderr)

    print(f"[run] Procesadas {total_procesadas:,} entradas. "
          f"Nuevas/actualizadas: {len(nuevas_keys):,}", file=sys.stderr)

    export_json(conn, nuevas_keys, dashboard_days, is_bootstrap=is_bootstrap)
    export_alertas_partners(conn, nuevas_partner_keys, is_bootstrap=is_bootstrap)
    conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    current_year = datetime.now().year
    parser.add_argument(
        "--years", type=int, nargs="+", default=[current_year],
        help="Años a descargar (por defecto, el año en curso). "
             "Añade el año anterior en enero para no perder el cambio de año.",
    )
    parser.add_argument(
        "--dashboard-days", type=int, default=30,
        help="Cuántos días (aprox.) de histórico mantener en data/latest.json para el dashboard.",
    )
    args = parser.parse_args()
    run(args.years, args.dashboard_days)


if __name__ == "__main__":
    main()
