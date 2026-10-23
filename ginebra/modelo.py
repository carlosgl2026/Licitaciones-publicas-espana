"""Modelo de datos: una licitación tal y como la guarda ginebra.

Es una vista "aplanada" del documento CODICE de cada <entry> del feed. Los
importes se guardan como float (euros, sin IVA salvo que el nombre diga lo
contrario); las fechas como texto ISO 8601 tal y como vienen en el feed.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields, asdict
from typing import Optional


@dataclass
class Licitacion:
    # Identidad
    id: str                      # <id> del entry Atom: identifica el expediente de forma única
    fuente: str                  # id de la fuente (placsp, agregadas, menores...)
    expediente: str = ""         # número de expediente (único solo dentro de su órgano)
    url: str = ""                # ficha de la licitación en PLACSP
    updated: str = ""            # fecha del último anuncio publicado (ISO)

    # Descripción
    titulo: str = ""
    objeto: str = ""
    estado: str = ""             # PRE | PUB | EV | ADJ | RES | ANUL

    # Órgano de contratación
    organo: str = ""
    organo_nif: str = ""
    organo_dir3: str = ""
    organo_id_plataforma: str = ""
    organo_tipo: str = ""
    organo_actividad: str = ""
    organo_ciudad: str = ""
    organo_cp: str = ""
    organo_perfil_url: str = ""
    jerarquia: str = ""          # "Nivel superior > ... > órgano" (ubicación orgánica)
    plataforma_agregada: str = ""      # nombre de la plataforma de origen (solo feed de agregadas)
    plataforma_agregada_id: str = ""

    # Económico
    valor_estimado: Optional[float] = None
    presupuesto_sin_iva: Optional[float] = None
    presupuesto_con_iva: Optional[float] = None

    # Clasificación
    tipo_contrato: str = ""
    subtipo_contrato: str = ""
    cpv: str = ""                # CPV principal
    cpvs: str = ""               # todos los CPV, separados por ";"
    lugar_nuts: str = ""
    lugar_texto: str = ""
    lugar_ciudad: str = ""

    # Procedimiento
    procedimiento: str = ""
    tramitacion: str = ""
    plazo_presentacion: str = "" # "AAAA-MM-DDTHH:MM:SS" o "AAAA-MM-DD"
    plazo_descripcion: str = ""
    num_lotes: int = 0

    # Resultado
    resultado: str = ""
    adjudicatario: str = ""
    adjudicatario_nif: str = ""
    importe_adjudicacion: Optional[float] = None
    num_licitadores: Optional[int] = None

    # Documentos y anuncios
    pliego_administrativo_url: str = ""
    pliego_tecnico_url: str = ""
    fecha_primera_publicacion: str = ""
    fecha_anuncio_licitacion: str = ""
    ultimo_anuncio: str = ""
    fecha_ultimo_anuncio: str = ""
    anuncios: str = ""           # JSON: [{"tipo":..., "medio":..., "fecha":...}, ...]

    def a_dict(self) -> dict:
        return asdict(self)

    @property
    def texto_busqueda(self) -> str:
        return f"{self.titulo} {self.objeto}"


COLUMNAS_LICITACION = tuple(f.name for f in fields(Licitacion))


@dataclass
class Baja:
    """Un <at:deleted-entry>: el expediente ha desaparecido del feed."""
    id: str
    fecha: str
    motivo: str = ""             # ANULADA | CERRADA | ""


@dataclass
class ResultadoIngesta:
    fuente: str
    periodo: str
    entradas: int = 0
    expedientes: int = 0
    nuevos: int = 0
    actualizados: int = 0
    bajas: int = 0
    ids_nuevos: set = field(default_factory=set)
    ids_actualizados: set = field(default_factory=set)
