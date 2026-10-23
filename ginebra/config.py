"""Rutas y parámetros globales.

Todas las rutas se pueden redirigir con variables de entorno para los tests
o para ejecutar el pipeline fuera del repositorio.
"""

from __future__ import annotations

import os
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent

DATA_DIR = Path(os.environ.get("GINEBRA_DATA_DIR", RAIZ / "data"))
CACHE_DIR = Path(os.environ.get("GINEBRA_CACHE_DIR", RAIZ / "cache"))
DB_PATH = Path(os.environ.get("GINEBRA_DB", DATA_DIR / "ginebra.db"))

# URL pública del proyecto y base desde la que se sirven los ficheros de
# data/ (enlaces <link rel="self"> e ids de los feeds). Mientras no haya un
# hosting propio, los feeds se sirven directamente desde el repositorio.
SITE_URL = os.environ.get("GINEBRA_SITE_URL", "https://github.com/inorganic-software/ginebra")
DATA_URL = os.environ.get("GINEBRA_DATA_URL", "https://raw.githubusercontent.com/inorganic-software/ginebra/main/data/")

# Identidad del feed propio.
FEED_TITULO = "Ginebra · licitaciones públicas de tecnología"
FEED_AUTOR = "Ginebra (inorganic-software)"
