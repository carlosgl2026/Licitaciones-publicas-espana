"""Utilidades de texto: normalización para comparar sin tildes ni mayúsculas."""

from __future__ import annotations

import re
import unicodedata

_ESPACIOS = re.compile(r"\s+")


def normalizar(s: str | None) -> str:
    """minúsculas, sin tildes y con espacios colapsados."""
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", s.lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    return _ESPACIOS.sub(" ", s).strip()


def contiene_alguna(texto_normalizado: str, palabras_normalizadas: tuple[str, ...]) -> bool:
    return any(p in texto_normalizado for p in palabras_normalizadas)
