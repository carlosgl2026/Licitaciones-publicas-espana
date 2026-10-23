"""Registro de las fuentes oficiales de datos abiertos de PLACSP.

Cada fuente es un "canal de sindicación" de la Plataforma de Contratación del
Sector Público. Todas publican ZIPs con ficheros Atom en formato CODICE y se
regeneran a diario con los cambios del día anterior:

    https://contrataciondelsectorpublico.gob.es/sindicacion/sindicacion_<n>/<fichero>_<periodo>.zip

Las fuentes con ficheros mensuales permiten descargar solo el mes en curso
(decenas de MB) en vez del acumulado anual (más de 1 GB para el feed 643).

Catálogo oficial: https://www.hacienda.gob.es/es-ES/GobiernoAbierto/Datos%20Abiertos/Paginas/licitaciones_plataforma_contratacion.aspx
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

BASE_SINDICACION = "https://contrataciondelsectorpublico.gob.es/sindicacion/sindicacion_{n}/{fichero}"

# Directorio oficial de órganos de contratación alojados en PLACSP (XLSX).
URL_ORGANOS = "https://contrataciondelsectorpublico.gob.es/datosabiertos/OrganosContratacion.xlsx"


@dataclass(frozen=True)
class Fuente:
    id: str
    nombre: str
    sindicacion: int
    plantilla: str     # nombre de fichero con {periodo} = AAAA o AAAAMM
    mensual: bool      # True si además del anual existe un ZIP por mes
    desde: int         # primer año disponible
    tipo: str          # licitacion | encargo | consulta
    por_defecto: bool  # se procesa en la ejecución diaria si no se indican fuentes
    descripcion: str = ""

    def url(self, periodo: str) -> str:
        return BASE_SINDICACION.format(
            n=self.sindicacion, fichero=self.plantilla.format(periodo=periodo)
        )

    def nombre_fichero(self, periodo: str) -> str:
        return self.plantilla.format(periodo=periodo)

    def periodos_auto(self, hoy: date | None = None, margen_dias: int = 3) -> list[str]:
        """Periodos a procesar en una ejecución diaria normal.

        - Fuentes mensuales: el mes en curso y, durante los primeros días del
          mes, también el anterior (las actualizaciones del 30 o 31 caen en el
          ZIP del mes que acaba de cerrar).
        - Fuentes solo anuales: el año en curso y, en los primeros días de
          enero, también el anterior.
        """
        hoy = hoy or date.today()
        if self.mensual:
            periodos = [hoy.strftime("%Y%m")]
            if hoy.day <= margen_dias:
                anterior = hoy.replace(day=1) - timedelta(days=1)
                periodos.insert(0, anterior.strftime("%Y%m"))
            return periodos
        periodos = [str(hoy.year)]
        if hoy.month == 1 and hoy.day <= margen_dias:
            periodos.insert(0, str(hoy.year - 1))
        return periodos


FUENTES: dict[str, Fuente] = {
    f.id: f
    for f in (
        Fuente(
            id="placsp",
            nombre="Licitaciones en perfiles alojados en PLACSP",
            sindicacion=643,
            plantilla="licitacionesPerfilesContratanteCompleto3_{periodo}.zip",
            mensual=True,
            desde=2012,
            tipo="licitacion",
            por_defecto=True,
            descripcion="Órganos con perfil de contratante alojado en PLACSP (AGE, muchas CCAA y la mayoría de entidades locales). Excluye contratos menores.",
        ),
        Fuente(
            id="agregadas",
            nombre="Licitaciones de plataformas agregadas",
            sindicacion=1044,
            plantilla="PlataformasAgregadasSinMenores_{periodo}.zip",
            mensual=True,
            desde=2016,
            tipo="licitacion",
            por_defecto=True,
            descripcion="Administraciones con plataforma propia que vuelcan en PLACSP por agregación: Comunidad de Madrid, Ayuntamiento de Madrid, Cataluña, Euskadi, Andalucía, Galicia... Excluye contratos menores.",
        ),
        Fuente(
            id="menores",
            nombre="Contratos menores en perfiles alojados en PLACSP",
            sindicacion=1143,
            plantilla="contratosMenoresPerfilesContratantes_{periodo}.zip",
            mensual=True,
            desde=2018,
            tipo="licitacion",
            por_defecto=False,
            descripcion="Contratos menores (importe reducido). Desactivado por defecto: quedan fuera del rango de importe que interesa.",
        ),
        Fuente(
            id="encargos",
            nombre="Encargos a medios propios",
            sindicacion=1383,
            plantilla="EMP_SectorPublico_{periodo}.zip",
            mensual=False,
            desde=2022,
            tipo="encargo",
            por_defecto=False,
            descripcion="Encargos a medios propios publicados en PLACSP. Solo fichero anual. Estructura CODICE distinta: no validada con el parser actual.",
        ),
        Fuente(
            id="consultas",
            nombre="Consultas preliminares de mercado",
            sindicacion=1403,
            plantilla="CPM_SectorPublico_{periodo}.zip",
            mensual=False,
            desde=2022,
            tipo="consulta",
            por_defecto=False,
            descripcion="Consultas preliminares de mercado publicadas en PLACSP. Solo fichero anual. Estructura CODICE distinta: no validada con el parser actual.",
        ),
    )
}


def fuentes_por_defecto() -> list[Fuente]:
    return [f for f in FUENTES.values() if f.por_defecto]


def resolver_fuentes(ids: list[str] | None) -> list[Fuente]:
    if not ids:
        return fuentes_por_defecto()
    desconocidas = [i for i in ids if i not in FUENTES]
    if desconocidas:
        raise ValueError(
            f"Fuentes desconocidas: {', '.join(desconocidas)}. "
            f"Disponibles: {', '.join(FUENTES)}"
        )
    return [FUENTES[i] for i in ids]
