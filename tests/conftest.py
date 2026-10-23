import pathlib

import pytest

from ginebra import config

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


@pytest.fixture
def entorno(tmp_path, monkeypatch):
    """Redirige rutas y URL del sitio a un directorio temporal."""
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "data" / "test.db")
    monkeypatch.setattr(config, "SITE_URL", "https://example.org/ginebra/")
    return tmp_path


@pytest.fixture
def fixture_atom() -> pathlib.Path:
    return FIXTURES / "agregadas_muestra.atom"


@pytest.fixture
def fixture_zip(tmp_path, fixture_atom) -> pathlib.Path:
    import zipfile
    ruta = tmp_path / "muestra.zip"
    with zipfile.ZipFile(ruta, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(fixture_atom, "PlataformasAgregadasSinMenores.atom")
        zf.writestr("readme.txt", "no es atom")
    return ruta
