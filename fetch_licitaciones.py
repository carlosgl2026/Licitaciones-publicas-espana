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
import zipfile
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
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

DATA_DIR = Path(__file__).parent / "data"
DB_PATH = DATA_DIR / "licitaciones.db"
NUEVAS_HOY_PATH = DATA_DIR / "nuevas_hoy.json"
LATEST_PATH = DATA_DIR / "latest.json"

NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "cbc": "urn:dgpe:names:draft:codice:schema:xsd:CommonBasicComponents-2",
    "cac": "urn:dgpe:names:draft:codice:schema:xsd:CommonAggregateComponents-2",
    "cac-ext": "urn:dgpe:names:draft:codice-place-ext:schema:xsd:CommonAggregateComponents-2",
}

CFS_TAG = f"{{{NS['cac-ext']}}}ContractFolderStatus"


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

    @property
    def key(self) -> str:
        return self.expediente or self.url


# ---------------------------------------------------------------------------
# Descarga y parseo
# ---------------------------------------------------------------------------

def download_year_zip(year: int, timeout: int = 120) -> bytes:
    url = BASE_URL.format(year=year)
    print(f"[fetch] Descargando {url} ...", file=sys.stderr)
    resp = requests.get(url, timeout=timeout)
    resp.raise_for_status()
    print(f"[fetch] OK ({len(resp.content):,} bytes)", file=sys.stderr)
    return resp.content


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
    first_seen_at TEXT,
    last_seen_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_updated ON licitaciones(updated);
CREATE INDEX IF NOT EXISTS idx_first_seen ON licitaciones(first_seen_at);
"""


def get_db(db_path: Path = DB_PATH) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)
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
                tipo_contrato, procedimiento, cpv, url, updated, first_seen_at, last_seen_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                lic.key, lic.titulo, lic.estado, lic.organo, lic.objeto,
                lic.importe_sin_iva, lic.tipo_contrato, lic.procedimiento,
                lic.cpv, lic.url, lic.updated, now, now,
            ),
        )
        return True

    changed = row[0] != lic.updated
    conn.execute(
        """UPDATE licitaciones SET
             titulo=?, estado=?, organo=?, objeto=?, importe_sin_iva=?,
             tipo_contrato=?, procedimiento=?, cpv=?, url=?, updated=?, last_seen_at=?
           WHERE expediente=?""",
        (
            lic.titulo, lic.estado, lic.organo, lic.objeto, lic.importe_sin_iva,
            lic.tipo_contrato, lic.procedimiento, lic.cpv, lic.url, lic.updated,
            now, lic.key,
        ),
    )
    return changed


# ---------------------------------------------------------------------------
# Exportación para el dashboard
# ---------------------------------------------------------------------------

def export_json(conn: sqlite3.Connection, nuevas_keys: set[str], dashboard_days: int) -> None:
    cur = conn.execute(
        "SELECT expediente, titulo, estado, organo, objeto, importe_sin_iva, "
        "tipo_contrato, procedimiento, cpv, url, updated, first_seen_at "
        "FROM licitaciones ORDER BY first_seen_at DESC"
    )
    cols = [d[0] for d in cur.description]
    all_rows = [dict(zip(cols, r)) for r in cur.fetchall()]

    nuevas = [r for r in all_rows if r["expediente"] in nuevas_keys]
    NUEVAS_HOY_PATH.write_text(
        json.dumps(
            {"generado": datetime.now(timezone.utc).isoformat(timespec="seconds"),
             "total": len(nuevas), "licitaciones": nuevas},
            ensure_ascii=False, indent=2,
        ),
        encoding="utf-8",
    )
    print(f"[export] {len(nuevas)} nuevas -> {NUEVAS_HOY_PATH}", file=sys.stderr)

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

def run(years: list[int], dashboard_days: int) -> None:
    conn = get_db()
    nuevas_keys: set[str] = set()
    total_procesadas = 0

    for year in years:
        try:
            zip_bytes = download_year_zip(year)
        except requests.RequestException as e:
            print(f"[fetch] ERROR descargando el año {year}: {e}", file=sys.stderr)
            continue

        for entry in iter_entries_from_zip(zip_bytes):
            lic = parse_entry(entry)
            if lic is None or not lic.key:
                continue
            total_procesadas += 1
            if upsert_and_detect_new(conn, lic):
                nuevas_keys.add(lic.key)

        conn.commit()

    print(f"[run] Procesadas {total_procesadas:,} entradas. "
          f"Nuevas/actualizadas: {len(nuevas_keys):,}", file=sys.stderr)

    export_json(conn, nuevas_keys, dashboard_days)
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
