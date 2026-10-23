# Ginebra · feed propio de licitaciones públicas de tecnología

Ginebra construye un **feed propio** de licitaciones públicas españolas a partir de
los datos abiertos de la Plataforma de Contratación del Sector Público (PLACSP).
Cada día descarga los feeds oficiales, guarda un histórico propio en SQLite
(con el recorrido de estados de cada expediente, algo que el feed oficial no
conserva), aplica los criterios de negocio y publica:

- un **feed Atom** y un **JSON Feed** a los que suscribirse,
- un **dashboard** web (`site/index.html`) con filtros por administración, sector, estado y tipo,
- ficheros JSON para integrar con otras herramientas.

Los ficheros generados se commitean en `data/` y, mientras no haya un hosting
propio, se sirven desde el repositorio:
<https://raw.githubusercontent.com/inorganic-software/ginebra/main/data/feed.atom>.
El dashboard no se publica en GitHub Pages (la organización sirve sus Pages
bajo `www.inorganic.com`); se usa en local o se desplegará en GCP.

## Fuentes oficiales

Todas las fuentes son canales de sindicación de PLACSP (catálogo en el
[portal de datos abiertos de Hacienda](https://www.hacienda.gob.es/es-ES/GobiernoAbierto/Datos%20Abiertos/Paginas/licitaciones_plataforma_contratacion.aspx)).
Publican ZIPs con ficheros Atom en formato CODICE 2.x y se regeneran cada
madrugada con los cambios del día anterior.

| id | feed | Contenido | Ficheros | Por defecto |
|---|---|---|---|---|
| `placsp` | 643 | Licitaciones de perfiles de contratante alojados en PLACSP (AGE, la mayoría de entidades locales, varias CCAA). Sin contratos menores. | anual + mensual | sí |
| `agregadas` | 1044 | Licitaciones recibidas por agregación de administraciones con plataforma propia: **Comunidad de Madrid**, Generalitat de Catalunya, Gobierno Vasco, Junta de Andalucía, Xunta de Galicia, Gobierno de Navarra, Gobierno de La Rioja. | anual + mensual | sí |
| `menores` | 1143 | Contratos menores de perfiles alojados en PLACSP. | anual + mensual | no |
| `encargos` | 1383 | Encargos a medios propios. Estructura CODICE distinta, no validada. | anual | no |
| `consultas` | 1403 | Consultas preliminares de mercado. Estructura CODICE distinta, no validada. | anual | no |

Además se descarga el **directorio oficial de órganos de contratación**
(`OrganosContratacion.xlsx`, unos 23.500 órganos con sector, dependencia de
primer y segundo nivel, NIF, DIR3 y código postal) para clasificar cada
licitación administración por administración y ayuntamiento por ayuntamiento.

`python -m ginebra fuentes` imprime el catálogo con las URL exactas.

### Por qué ZIP mensuales

El ZIP anual del feed 643 pesa 1,9 GB y, con el ancho de banda que PLACSP
ofrece a los runners de GitHub, tarda más de cinco horas en bajar. El ZIP del
mes en curso pesa unos 120 MB y contiene todas las actualizaciones del mes,
que es lo único que hace falta para detectar novedades. La ejecución diaria
procesa el mes en curso y, los tres primeros días de cada mes, también el
anterior.

## Cómo funciona

```
feeds PLACSP (ZIP mensual)  ─►  ginebra.descarga  ─►  ginebra.codice (Atom + CODICE)
                                                           │
OrganosContratacion.xlsx    ─►  ginebra.organos  ─►  data/ginebra.db (SQLite: licitaciones, cambios, organos...)
                                                           │
                                                  ginebra.filtros + ginebra.exportar
                                                           │
                       data/latest.json · nuevas_hoy.json · alertas_nube.json · feed.atom · feed_nube.atom · feed.json · resumen.json
                                                           │
                                                  site/index.html (visor local; hosting pendiente)
```

Módulos del paquete `ginebra/`:

| Módulo | Qué hace |
|---|---|
| `fuentes.py` | Registro de feeds oficiales y cálculo de los periodos a procesar. |
| `descarga.py` | Descarga en streaming con validación de la firma ZIP y reintentos. El WAF de PLACSP devuelve a veces una página HTML de error con código 200. |
| `codice.py` | Parser de los `<entry>` Atom y del documento CODICE: expediente, estado, órgano (NIF, DIR3, jerarquía orgánica, plataforma de origen), importes, CPV, lugar, procedimiento, plazo, lotes, resultado y adjudicatario, pliegos y anuncios. También las `<at:deleted-entry>`. |
| `modelo.py` | Dataclass `Licitacion` (la vista aplanada de cada expediente) y `Baja`. |
| `db.py` | SQLite: `licitaciones` (una fila por expediente, clave el `<id>` Atom), `cambios` (historial de estados), `novedades`, `organos`, `ejecuciones`, `meta`. |
| `organos.py` | Carga del directorio oficial de órganos y búsqueda por ID de plataforma, DIR3 o NIF. |
| `filtros.py` | Criterios de negocio (tecnología, tamaño, cercanía) y alerta de nube, con explicación de por qué cada licitación aparece. |
| `ingesta.py` | Orquesta descarga, parseo, detección de nuevos/actualizados/bajas y poda. |
| `exportar.py` | JSON para el dashboard, Atom 1.0, JSON Feed 1.1 y resumen. |
| `cli.py` | Órdenes `actualizar`, `ingerir`, `exportar`, `organos`, `fuentes`, `resumen`. |

### Detección de novedades

La clave de cada expediente es el `<id>` del entry Atom, que PLACSP garantiza
único por expediente (el número de expediente no lo es: muchos ayuntamientos
usan "1/2026"). Cuando un expediente llega con una fecha `updated` posterior
a la guardada se marca como **actualizado** y se conserva el estado anterior;
si no existía, como **nuevo**; si llega una `<at:deleted-entry>`, como
**baja** (anulada o cerrada). La primera carga de una fuente no genera
novedades para no inundar el feed con el histórico.

### Criterios de negocio (`ginebra/filtros.py`)

1. **Tecnología**: CPV que empiece por 72 (servicios TI) o 48 (software), o
   palabras clave de desarrollo, datos, IA y nube en el objeto.
2. **Tamaño**: presupuesto base sin IVA (o valor estimado) entre 100.000 y
   5.000.000 €.
3. **Cercanía**: lugar de ejecución NUTS ES30, órgano con código postal 28xxx
   o en un municipio de la Comunidad de Madrid, plataforma o jerarquía de la
   Comunidad de Madrid, o un organismo de ámbito estatal (Administración
   General del Estado, Renfe, Adif, Aena...) siempre que no tenga sede
   conocida fuera de Madrid.

La alerta de **nube pública** se evalúa sobre todas las licitaciones sin
filtro de tamaño ni cercanía.

## Ficheros publicados (`data/`)

| Fichero | Contenido |
|---|---|
| `feed.atom` | El feed propio: licitaciones que cumplen los criterios, últimos 30 días, con categorías por estado, tipo, sector y administración. |
| `feed.json` | Lo mismo en JSON Feed 1.1; cada item lleva el registro completo en `_ginebra`. |
| `feed_nube.atom` | Alertas de nube pública en cualquier administración. |
| `latest.json` | Registros completos de la selección (etiquetas, clasificación por administración, motivos, anuncios). |
| `nuevas_hoy.json` | Subconjunto de `latest.json` que es novedad en la última ejecución. |
| `alertas_nube.json` | Alertas de nube: `nuevas` e `historico` (180 días). |
| `resumen.json` | Estado de la base, novedades, reparto por administración, últimas ejecuciones. |

La base de datos `data/ginebra.db` no se versiona: en GitHub Actions se
conserva entre ejecuciones con `actions/cache`.

## Uso

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt pytest
.venv/bin/python -m pytest                      # tests

.venv/bin/python -m ginebra actualizar          # mes en curso de placsp + agregadas, y exporta
.venv/bin/python -m ginebra actualizar --fuentes placsp agregadas menores
.venv/bin/python -m ginebra actualizar --periodos 202609 202610   # meses concretos
.venv/bin/python -m ginebra actualizar --anios 2026                # ZIP anual (carga inicial, tarda)
.venv/bin/python -m ginebra ingerir fichero.zip --fuente agregadas # ZIP ya descargado
.venv/bin/python -m ginebra exportar            # regenera data/ sin descargar nada
.venv/bin/python -m ginebra resumen             # estado de la base

python3 -m http.server 8000                     # dashboard en http://localhost:8000/site/
```

Variables de entorno: `GINEBRA_DATA_DIR`, `GINEBRA_CACHE_DIR`, `GINEBRA_DB`,
`GINEBRA_SITE_URL` y `GINEBRA_DATA_URL` (URLs públicas usadas en los enlaces
de los feeds).

## Automatización

`.github/workflows/diario.yml` corre cada día a las 06:30 UTC (y a mano desde
la pestaña Actions, con parámetros opcionales de fuentes, meses o años):
restaura la base de datos de la caché, ejecuta `ginebra actualizar` y
commitea `data/*.json` y `data/*.atom`. `.github/workflows/tests.yml`
ejecuta los tests en cada push.

## Extender

- **Otra fuente**: añade una `Fuente` en `ginebra/fuentes.py`. Si usa CODICE
  de licitaciones, el parser sirve tal cual.
- **Otros criterios**: ajusta `Criterios` en `ginebra/filtros.py` y ejecuta
  `ginebra exportar`; no hace falta volver a descargar nada porque la base
  guarda todas las licitaciones, no solo las seleccionadas.
- **Otro perfil de empresa** (otra región, otros CPV): cambia `nuts_cercania`,
  `cp_prefijos`, `municipios` y `cpv_prefijos`.

## Referencias

- [Especificación de sindicación y reutilización de datos de PLACSP](https://contrataciondelsectorpublico.gob.es/datosabiertos/especificacion-sindicacion.pdf)
- [Resumen de los conjuntos de datos abiertos](https://contrataciondelsectorpublico.gob.es/datosabiertos/DGPE_PLACSP_ResumenDatosAbiertos.pdf)
- [Listas de códigos CODICE](https://contrataciondelestado.es/codice/cl/)
