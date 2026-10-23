"""Persistencia en SQLite: histórico propio de expedientes y de sus cambios.

Tablas:

- licitaciones: una fila por expediente (clave: el <id> del entry Atom), con
  la última versión conocida y metadatos de seguimiento (cuándo se vio por
  primera vez, estado anterior, baja...).
- cambios: una fila por cada (expediente, fecha de actualización) distinta
  que hemos visto. Es el historial de estados por el que ha pasado cada
  expediente, algo que el feed oficial no conserva.
- novedades: los expedientes nuevos o actualizados en la última ejecución
  (se vacía al empezar cada una). Lo lee la exportación.
- ejecuciones: registro de cada ingesta (fuente, periodo, contadores).
- organos: directorio oficial de órganos de contratación alojados en PLACSP
  (OrganosContratacion.xlsx), para clasificar por sector y administración.
- meta: pares clave/valor (versión del esquema, última ejecución...).
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from . import config
from .modelo import COLUMNAS_LICITACION, Baja, Licitacion

VERSION_ESQUEMA = "3"

_COLUMNAS_SQL = {
    "valor_estimado": "REAL",
    "presupuesto_sin_iva": "REAL",
    "presupuesto_con_iva": "REAL",
    "importe_adjudicacion": "REAL",
    "num_licitadores": "INTEGER",
    "num_lotes": "INTEGER",
}

COLUMNAS_SEGUIMIENTO = (
    "first_seen_at", "last_seen_at", "estado_anterior", "updated_anterior",
    "baja_fecha", "baja_motivo",
)


def _definicion_licitaciones() -> str:
    cols = []
    for c in COLUMNAS_LICITACION:
        tipo = _COLUMNAS_SQL.get(c, "TEXT")
        cols.append(f"{c} {tipo} PRIMARY KEY" if c == "id" else f"{c} {tipo}")
    cols += [f"{c} TEXT" for c in COLUMNAS_SEGUIMIENTO]
    return "CREATE TABLE IF NOT EXISTS licitaciones (\n    " + ",\n    ".join(cols) + "\n);"


ESQUEMA = f"""
{_definicion_licitaciones()}
CREATE INDEX IF NOT EXISTS idx_lic_updated ON licitaciones(updated);
CREATE INDEX IF NOT EXISTS idx_lic_fuente ON licitaciones(fuente);
CREATE INDEX IF NOT EXISTS idx_lic_organo ON licitaciones(organo_id_plataforma);

CREATE TABLE IF NOT EXISTS cambios (
    id TEXT NOT NULL,
    updated TEXT NOT NULL,
    estado TEXT,
    visto_en TEXT,
    PRIMARY KEY (id, updated)
);

CREATE TABLE IF NOT EXISTS novedades (
    id TEXT PRIMARY KEY,
    tipo TEXT NOT NULL,            -- nuevo | actualizado | baja
    estado_anterior TEXT,
    registrado_en TEXT
);

CREATE TABLE IF NOT EXISTS ejecuciones (
    ejecutado_en TEXT,
    fuente TEXT,
    periodo TEXT,
    entradas INTEGER,
    expedientes INTEGER,
    nuevos INTEGER,
    actualizados INTEGER,
    bajas INTEGER
);

CREATE TABLE IF NOT EXISTS organos (
    id_plataforma TEXT PRIMARY KEY,
    nombre TEXT,
    sector TEXT,
    nivel1 TEXT,
    nivel2 TEXT,
    nif TEXT,
    dir3 TEXT,
    cp TEXT,
    medio_propio TEXT,
    activo TEXT
);
CREATE INDEX IF NOT EXISTS idx_org_dir3 ON organos(dir3);
CREATE INDEX IF NOT EXISTS idx_org_nif ON organos(nif);

CREATE TABLE IF NOT EXISTS meta (
    clave TEXT PRIMARY KEY,
    valor TEXT
);
"""


def ahora_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def conectar(ruta: Path | None = None) -> sqlite3.Connection:
    ruta = ruta or config.DB_PATH
    ruta.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(ruta)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.executescript(ESQUEMA)
    _migrar(conn)
    conn.execute(
        "INSERT OR REPLACE INTO meta (clave, valor) VALUES ('version_esquema', ?)",
        (VERSION_ESQUEMA,),
    )
    conn.commit()
    return conn


def _migrar(conn: sqlite3.Connection) -> None:
    """Añade columnas nuevas a bases de datos creadas con un modelo anterior."""
    existentes = {r["name"] for r in conn.execute("PRAGMA table_info(licitaciones)")}
    for c in COLUMNAS_LICITACION:
        if c not in existentes:
            conn.execute(f"ALTER TABLE licitaciones ADD COLUMN {c} {_COLUMNAS_SQL.get(c, 'TEXT')}")
    for c in COLUMNAS_SEGUIMIENTO:
        if c not in existentes:
            conn.execute(f"ALTER TABLE licitaciones ADD COLUMN {c} TEXT")


def esta_vacia(conn: sqlite3.Connection) -> bool:
    return conn.execute("SELECT COUNT(*) FROM licitaciones").fetchone()[0] == 0


def _fecha(iso: str) -> datetime | None:
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return None


def es_mas_reciente(nuevo: str, viejo: str) -> bool:
    """True si la fecha `nuevo` es posterior a `viejo` (comparación robusta a zonas horarias)."""
    a, b = _fecha(nuevo), _fecha(viejo)
    if a is None or b is None:
        return nuevo > viejo
    return a > b


def guardar(conn: sqlite3.Connection, lic: Licitacion, visto_en: str | None = None) -> str:
    """Inserta o actualiza una licitación.

    Devuelve (resultado, estado_anterior). `resultado` es "nuevo",
    "actualizado", "igual" (misma fecha de actualización) o "antiguo" (la
    versión del feed es anterior a la que ya teníamos: el mismo expediente
    aparece varias veces en el feed; las versiones viejas solo alimentan el
    historial de cambios).
    """
    visto_en = visto_en or ahora_iso()
    fila = conn.execute(
        "SELECT updated, estado FROM licitaciones WHERE id = ?", (lic.id,)
    ).fetchone()
    datos = lic.a_dict()

    if fila is None:
        datos.update(first_seen_at=visto_en, last_seen_at=visto_en,
                     estado_anterior="", updated_anterior="", baja_fecha="", baja_motivo="")
        cols = ", ".join(datos)
        marcas = ", ".join("?" for _ in datos)
        conn.execute(f"INSERT INTO licitaciones ({cols}) VALUES ({marcas})", tuple(datos.values()))
        _registrar_cambio(conn, lic, visto_en)
        return "nuevo", ""

    if lic.updated == fila["updated"]:
        resultado = "igual"
    elif es_mas_reciente(lic.updated, fila["updated"]):
        resultado = "actualizado"
        datos.update(estado_anterior=fila["estado"], updated_anterior=fila["updated"])
    else:
        conn.execute("UPDATE licitaciones SET last_seen_at = ? WHERE id = ?", (visto_en, lic.id))
        _registrar_cambio(conn, lic, visto_en)
        return "antiguo", fila["estado"]

    datos["last_seen_at"] = visto_en
    datos.pop("id")
    asignaciones = ", ".join(f"{c} = ?" for c in datos)
    conn.execute(f"UPDATE licitaciones SET {asignaciones} WHERE id = ?", (*datos.values(), lic.id))
    if resultado == "actualizado":
        _registrar_cambio(conn, lic, visto_en)
    return resultado, fila["estado"]


def _registrar_cambio(conn: sqlite3.Connection, lic: Licitacion, visto_en: str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO cambios (id, updated, estado, visto_en) VALUES (?,?,?,?)",
        (lic.id, lic.updated, lic.estado, visto_en),
    )


def registrar_baja(conn: sqlite3.Connection, baja: Baja) -> bool:
    """Marca un expediente como dado de baja. Devuelve True si lo conocíamos."""
    cur = conn.execute(
        "UPDATE licitaciones SET baja_fecha = ?, baja_motivo = ? WHERE id = ? AND (baja_fecha IS NULL OR baja_fecha = '' OR baja_fecha < ?)",
        (baja.fecha, baja.motivo, baja.id, baja.fecha),
    )
    return cur.rowcount > 0


# --- novedades de la ejecución en curso -------------------------------------

def limpiar_novedades(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM novedades")


def anotar_novedad(conn: sqlite3.Connection, id_: str, tipo: str, estado_anterior: str = "") -> None:
    conn.execute(
        "INSERT INTO novedades (id, tipo, estado_anterior, registrado_en) VALUES (?,?,?,?) "
        "ON CONFLICT(id) DO UPDATE SET tipo = CASE WHEN novedades.tipo = 'nuevo' THEN 'nuevo' ELSE excluded.tipo END, "
        "estado_anterior = COALESCE(NULLIF(novedades.estado_anterior, ''), excluded.estado_anterior)",
        (id_, tipo, estado_anterior, ahora_iso()),
    )


def novedades(conn: sqlite3.Connection) -> dict[str, dict]:
    return {r["id"]: dict(r) for r in conn.execute("SELECT * FROM novedades")}


def registrar_ejecucion(conn: sqlite3.Connection, fuente: str, periodo: str, entradas: int,
                        expedientes: int, nuevos: int, actualizados: int, bajas: int) -> None:
    conn.execute(
        "INSERT INTO ejecuciones VALUES (?,?,?,?,?,?,?,?)",
        (ahora_iso(), fuente, periodo, entradas, expedientes, nuevos, actualizados, bajas),
    )


def poner_meta(conn: sqlite3.Connection, clave: str, valor: str) -> None:
    conn.execute("INSERT OR REPLACE INTO meta (clave, valor) VALUES (?, ?)", (clave, valor))


def leer_meta(conn: sqlite3.Connection, clave: str, por_defecto: str = "") -> str:
    fila = conn.execute("SELECT valor FROM meta WHERE clave = ?", (clave,)).fetchone()
    return fila["valor"] if fila else por_defecto


# --- consultas para la exportación -----------------------------------------

def licitaciones_desde(conn: sqlite3.Connection, desde_iso: str) -> list[dict]:
    """Todas las licitaciones con `updated` >= desde_iso, más recientes primero."""
    cur = conn.execute(
        "SELECT * FROM licitaciones WHERE updated >= ? ORDER BY updated DESC", (desde_iso,)
    )
    return [dict(r) for r in cur.fetchall()]


def licitaciones_por_ids(conn: sqlite3.Connection, ids: list[str]) -> list[dict]:
    if not ids:
        return []
    filas: list[dict] = []
    for i in range(0, len(ids), 500):
        trozo = ids[i:i + 500]
        marcas = ",".join("?" for _ in trozo)
        cur = conn.execute(f"SELECT * FROM licitaciones WHERE id IN ({marcas})", trozo)
        filas.extend(dict(r) for r in cur.fetchall())
    filas.sort(key=lambda r: r["updated"] or "", reverse=True)
    return filas


def historial(conn: sqlite3.Connection, id_: str) -> list[dict]:
    cur = conn.execute("SELECT * FROM cambios WHERE id = ? ORDER BY updated", (id_,))
    return [dict(r) for r in cur.fetchall()]


def resumen(conn: sqlite3.Connection) -> dict:
    total = conn.execute("SELECT COUNT(*) FROM licitaciones").fetchone()[0]
    por_fuente = {r["fuente"]: r["n"] for r in conn.execute(
        "SELECT fuente, COUNT(*) AS n FROM licitaciones GROUP BY fuente")}
    rango = conn.execute("SELECT MIN(updated) AS a, MAX(updated) AS b FROM licitaciones").fetchone()
    return {"total": total, "por_fuente": por_fuente, "updated_min": rango["a"], "updated_max": rango["b"]}


def podar(conn: sqlite3.Connection, antes_de_iso: str) -> int:
    """Elimina expedientes cuya última actualización es anterior a la fecha dada."""
    cur = conn.execute("DELETE FROM licitaciones WHERE updated < ?", (antes_de_iso,))
    conn.execute("DELETE FROM cambios WHERE id NOT IN (SELECT id FROM licitaciones)")
    return cur.rowcount
