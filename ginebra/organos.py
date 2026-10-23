"""Directorio oficial de órganos de contratación alojados en PLACSP.

PLACSP publica un XLSX con todos los órganos de contratación con perfil
alojado en la plataforma (unos 23.500), con su ubicación en el sector
público (AGE, CCAA, entidades locales, otras), su dependencia de primer y
segundo nivel (ministerio / comunidad autónoma / ayuntamiento), NIF, DIR3 y
código postal. Sirve para clasificar cada licitación "administración por
administración" de forma fiable, sin heurísticas sobre el nombre.

Las licitaciones del feed de plataformas agregadas NO están en este
directorio (sus órganos no están alojados en PLACSP); para ellas se usa el
nombre de la plataforma de origen que viene en el propio XML.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

from . import config, descarga
from .fuentes import URL_ORGANOS
from .texto import normalizar

log = logging.getLogger("ginebra.organos")

CABECERAS_ESPERADAS = {
    "id plataforma": "id_plataforma",
    "nombre organo contratacion": "nombre",
    "ubicacion sector publico": "sector",
    "dependencia primer nivel": "nivel1",
    "dependencia segundo nivel": "nivel2",
    "nif": "nif",
    "dir3": "dir3",
    "codigo postal": "cp",
    "medio propio": "medio_propio",
    "activo / inactivo": "activo",
}

SECTORES = {
    "ADMINISTRACIÓN GENERAL DEL ESTADO": "Administración General del Estado",
    "COMUNIDADES Y CIUDADES AUTÓNOMAS": "Comunidades autónomas",
    "ENTIDADES LOCALES": "Entidades locales",
    "OTRAS ENTIDADES DEL SECTOR PÚBLICO": "Otras entidades del sector público",
}


def ruta_cache() -> Path:
    return config.CACHE_DIR / "OrganosContratacion.xlsx"


def descargar_directorio(sesion=None) -> Path:
    # El XLSX es un ZIP (firma "PK"), así que vale el mismo descargador.
    return descarga.descargar(URL_ORGANOS, ruta_cache(), sesion=sesion)


def leer_xlsx(ruta: Path) -> list[dict]:
    """Lee el XLSX y devuelve una lista de dicts con las claves de CABECERAS_ESPERADAS."""
    import openpyxl  # importación perezosa: solo hace falta para este comando

    wb = openpyxl.load_workbook(ruta, read_only=True, data_only=True)
    hoja = wb.worksheets[0]
    columnas: dict[int, str] | None = None
    filas: list[dict] = []
    for fila in hoja.iter_rows(values_only=True):
        valores = ["" if v is None else str(v).strip() for v in fila]
        if columnas is None:
            normalizadas = [normalizar(v) for v in valores]
            if "id plataforma" in normalizadas:
                columnas = {
                    i: CABECERAS_ESPERADAS[n] for i, n in enumerate(normalizadas)
                    if n in CABECERAS_ESPERADAS
                }
            continue
        registro = {campo: (valores[i] if i < len(valores) else "") for i, campo in columnas.items()}
        if not registro.get("id_plataforma"):
            continue
        registro["sector"] = SECTORES.get(registro.get("sector", ""), registro.get("sector", ""))
        filas.append(registro)
    wb.close()
    if columnas is None:
        raise ValueError(f"No se encontró la fila de cabeceras en {ruta}")
    return filas


def cargar_directorio(conn: sqlite3.Connection, ruta: Path) -> int:
    filas = leer_xlsx(ruta)
    conn.execute("DELETE FROM organos")
    conn.executemany(
        "INSERT OR REPLACE INTO organos (id_plataforma, nombre, sector, nivel1, nivel2, nif, dir3, cp, medio_propio, activo) "
        "VALUES (:id_plataforma, :nombre, :sector, :nivel1, :nivel2, :nif, :dir3, :cp, :medio_propio, :activo)",
        [{k: f.get(k, "") for k in ("id_plataforma", "nombre", "sector", "nivel1", "nivel2", "nif", "dir3", "cp", "medio_propio", "activo")} for f in filas],
    )
    conn.commit()
    log.info("Directorio de órganos cargado: %d registros", len(filas))
    return len(filas)


class Directorio:
    """Índice en memoria del directorio para enriquecer filas rápidamente."""

    def __init__(self, conn: sqlite3.Connection):
        self.por_id: dict[str, dict] = {}
        self.por_dir3: dict[str, dict] = {}
        self.por_nif: dict[str, dict] = {}
        for r in conn.execute("SELECT * FROM organos"):
            d = dict(r)
            self.por_id[d["id_plataforma"]] = d
            if d.get("dir3"):
                self.por_dir3.setdefault(d["dir3"], d)
            if d.get("nif"):
                self.por_nif.setdefault(d["nif"], d)

    def __len__(self) -> int:
        return len(self.por_id)

    def buscar(self, id_plataforma: str = "", dir3: str = "", nif: str = "") -> dict | None:
        if id_plataforma and id_plataforma in self.por_id:
            return self.por_id[id_plataforma]
        if dir3 and dir3 in self.por_dir3:
            return self.por_dir3[dir3]
        if nif and nif in self.por_nif:
            return self.por_nif[nif]
        return None
