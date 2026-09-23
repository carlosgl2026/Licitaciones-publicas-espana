# Licitaciones España — plataforma de seguimiento diario

Detecta cada día las licitaciones nuevas (o actualizadas) del feed nacional de la
**Plataforma de Contratación del Sector Público (PLACSP)** y las muestra en un
dashboard web sencillo.

## Cómo funciona

```
fetch_licitaciones.py  ──►  data/licitaciones.db   (histórico completo, SQLite)
                        ──►  data/nuevas_hoy.json   (solo lo nuevo/cambiado en esta ejecución)
                        ──►  data/latest.json       (últimos ~30 días, para el dashboard)

dashboard.html  ──►  lee data/*.json y los muestra, con búsqueda y filtros
```

PLACSP publica un ZIP anual con **todas** las licitaciones de ese año, y ese ZIP
"se actualiza diariamente con los cambios del día anterior" (así lo documenta el
propio catálogo oficial). El script descarga ese ZIP, compara cada expediente
contra lo que ya tenía guardado, y marca como "nuevo" todo lo que no existía o
cuya fecha `updated` ha cambiado (p. ej. pasó de "Publicada" a "Adjudicada").

## ⚠️ Por qué no lo he podido ejecutar yo mismo ahora mismo

El entorno donde genero estos archivos solo tiene salida de red hacia registries
de paquetes (PyPI, GitHub, npm...), no hacia `contrataciondelsectorpublico.gob.es`.
El script está probado con datos de ejemplo (parseo, detección de nuevas,
exportación a JSON — todo verificado), pero la primera descarga real del ZIP
tienes que lanzarla tú, desde tu ordenador, un servidor, o GitHub Actions.

## Puesta en marcha — 3 opciones

### Opción A: GitHub Actions + GitHub Pages (recomendada, cero mantenimiento)

1. Crea un repo nuevo en GitHub y sube el contenido de esta carpeta.
2. En **Settings → Pages**, elige "GitHub Actions" como origen.
3. El workflow `.github/workflows/daily.yml` ya incluido:
   - corre todos los días a las 06:15 UTC (y también puedes lanzarlo a mano desde la pestaña *Actions*),
   - descarga el ZIP del año en curso, detecta lo nuevo,
   - commitea `data/*.json` al repo,
   - publica `dashboard.html` + `data/` en GitHub Pages.
4. Al cabo de la primera ejecución tendrás tu dashboard en
   `https://<tu-usuario>.github.io/<tu-repo>/`.

### Opción B: cron en tu propio servidor

```bash
pip install -r requirements.txt
python fetch_licitaciones.py                # descarga el año actual y actualiza data/

# Servir el dashboard (necesita servidor HTTP por el CORS de fetch(), no vale file://)
python3 -m http.server 8000
# abre http://localhost:8000/dashboard.html
```

Añádelo a `crontab -e`:
```
15 6 * * * cd /ruta/al/proyecto && /usr/bin/python3 fetch_licitaciones.py >> fetch.log 2>&1
```

### Opción C: solo probarlo en local, una vez

```bash
pip install -r requirements.txt
python fetch_licitaciones.py --years 2026
python3 -m http.server 8000
```

## Extender a más fuentes

`fetch_licitaciones.py` está centrado en el feed principal de licitaciones
(`sindicacion_643`). El catálogo de `dcarrero/ContratacionAbierta` documenta más
feeds oficiales con la misma estructura CODICE (contratos menores, plataformas
agregadas, encargos a medios propios, consultas preliminares...). Para añadir
uno, solo hace falta:

1. Añadir su URL base (con `{year}`) como una nueva constante tipo `BASE_URL`.
2. Reutilizar `iter_entries_from_zip` / `parse_entry` tal cual.
3. Guardar en una tabla SQLite separada (o añadir una columna `fuente`).

## Aviso sobre otro repo que mencionaste

`Hikaru17zx/licitaciones-espana` no se ha usado para nada de esto: su README
empuja a instalar un `.exe`/`.dmg` desde un enlace que en realidad apunta a un
`.zip` — un patrón típico de malware disfrazado de app. No lo descargues ni lo
ejecutes.

## Créditos de las fuentes originales

- Catálogo de fuentes oficiales: [dcarrero/ContratacionAbierta](https://github.com/dcarrero/ContratacionAbierta)
- Dataset histórico de referencia (no usado en el pipeline diario, pero útil para análisis): [BquantFinance/licitaciones-espana](https://github.com/BquantFinance/licitaciones-espana)
- Fuente oficial de datos: [PLACSP — Ministerio de Hacienda](https://contrataciondelestado.es)
