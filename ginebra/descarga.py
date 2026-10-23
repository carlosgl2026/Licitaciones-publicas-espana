"""Descarga robusta de los ZIP de sindicación de PLACSP.

Particularidades del servidor que hay que tener en cuenta:

- Un WAF (F5) delante del portal devuelve a veces, con código HTTP 200 y
  Content-Type application/zip, una página HTML de error ("Su certificado no
  está autorizado a acceder a la Plataforma"). Es intermitente: la misma URL
  funciona segundos después. Por eso se valida la firma del ZIP (bytes "PK")
  y se reintenta con espera creciente.
- El ancho de banda hacia rangos de IP de centros de datos (GitHub Actions)
  es bajo: el ZIP anual de 1,9 GB del feed 643 tarda más de 5 horas. Los ZIP
  mensuales (decenas de MB) tardan minutos.
- Las peticiones HEAD no son fiables (a veces responden "200 Error" sin
  cabeceras), así que la reutilización de descargas se basa en GET
  condicional con If-None-Match.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

import requests

log = logging.getLogger("ginebra.descarga")

CABECERAS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    ),
    "Accept": "application/zip, application/octet-stream, */*",
    "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
}

FIRMA_ZIP = b"PK"
ESPERA_BASE = 15      # segundos; se duplica en cada reintento
ESPERA_MAXIMA = 300
REINTENTOS = 6
TIMEOUT = (30, 180)   # (conexión, lectura) en segundos


class DescargaInvalida(Exception):
    """El servidor respondió, pero no con un ZIP (normalmente la página HTML del WAF)."""


class NoDisponible(Exception):
    """El fichero no existe en el servidor (404). Normal para un mes recién empezado."""


def nueva_sesion() -> requests.Session:
    s = requests.Session()
    s.headers.update(CABECERAS)
    return s


def _ruta_meta(destino: Path) -> Path:
    return destino.with_name(destino.name + ".meta.json")


def _leer_meta(destino: Path) -> dict:
    try:
        return json.loads(_ruta_meta(destino).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _guardar_meta(destino: Path, meta: dict) -> None:
    _ruta_meta(destino).write_text(json.dumps(meta, indent=2), encoding="utf-8")


def descargar(
    url: str,
    destino: Path,
    sesion: requests.Session | None = None,
    reintentos: int = REINTENTOS,
    timeout: tuple[int, int] = TIMEOUT,
    dormir=time.sleep,
) -> Path:
    """Descarga `url` en `destino` y devuelve la ruta.

    Si `destino` ya existe y el servidor confirma (304) que no ha cambiado
    desde la última descarga, se reutiliza el fichero local sin descargar.
    """
    sesion = sesion or nueva_sesion()
    destino.parent.mkdir(parents=True, exist_ok=True)
    meta = _leer_meta(destino)
    cabeceras = {}
    if destino.exists() and meta.get("etag"):
        cabeceras["If-None-Match"] = meta["etag"]

    ultimo_error: Exception | None = None
    for intento in range(1, reintentos + 1):
        log.info("Descargando %s (intento %d/%d)", url, intento, reintentos)
        try:
            with sesion.get(url, stream=True, timeout=timeout, headers=cabeceras) as resp:
                if resp.status_code == 304:
                    log.info("Sin cambios en el servidor; se reutiliza %s", destino.name)
                    return destino
                if resp.status_code == 404:
                    raise NoDisponible(f"{url} no existe (404)")
                resp.raise_for_status()
                _volcar(resp, destino)
                _guardar_meta(destino, {
                    "url": url,
                    "etag": resp.headers.get("ETag", ""),
                    "last_modified": resp.headers.get("Last-Modified", ""),
                    "bytes": destino.stat().st_size,
                })
                log.info("OK %s (%s MB)", destino.name, f"{destino.stat().st_size / 2**20:,.1f}")
                return destino
        except (requests.RequestException, DescargaInvalida) as e:
            ultimo_error = e
            espera = min(ESPERA_BASE * 2 ** (intento - 1), ESPERA_MAXIMA)
            log.warning("Intento %d falló: %s. Reintento en %ds", intento, e, espera)
            if intento < reintentos:
                dormir(espera)

    assert ultimo_error is not None
    raise ultimo_error


def _volcar(resp: requests.Response, destino: Path) -> None:
    """Escribe el cuerpo en disco por trozos, validando que empieza como ZIP."""
    temporal = destino.with_name(destino.name + ".part")
    total = 0
    ultimo_log = time.monotonic()
    primero = True
    with open(temporal, "wb") as f:
        for trozo in resp.iter_content(chunk_size=1024 * 1024):
            if not trozo:
                continue
            if primero:
                primero = False
                if not trozo.startswith(FIRMA_ZIP):
                    temporal.unlink(missing_ok=True)
                    muestra = trozo[:160].decode("utf-8", errors="replace").replace("\n", " ")
                    raise DescargaInvalida(
                        f"La respuesta no es un ZIP (Content-Type={resp.headers.get('Content-Type')!r}): {muestra!r}"
                    )
            f.write(trozo)
            total += len(trozo)
            ahora = time.monotonic()
            if ahora - ultimo_log >= 10:
                log.info("... %s MB descargados", f"{total / 2**20:,.1f}")
                ultimo_log = ahora
    if primero:
        temporal.unlink(missing_ok=True)
        raise DescargaInvalida("Respuesta vacía")
    temporal.replace(destino)
