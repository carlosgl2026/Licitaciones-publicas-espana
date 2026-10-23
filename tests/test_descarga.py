import pytest
import requests

from ginebra import descarga

HTML_WAF = b"<html><head><title>Redireccionando...</title></head><body>Su certificado no est&aacute; autorizado</body></html>"


class RespuestaFalsa:
    def __init__(self, status=200, cuerpo=b"", cabeceras=None):
        self.status_code = status
        self._cuerpo = cuerpo
        self.headers = cabeceras or {}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code}")

    def iter_content(self, chunk_size):
        for i in range(0, len(self._cuerpo), chunk_size):
            yield self._cuerpo[i:i + chunk_size]


class SesionFalsa:
    def __init__(self, respuestas):
        self.respuestas = list(respuestas)
        self.llamadas = []

    def get(self, url, **kw):
        self.llamadas.append((url, kw.get("headers") or {}))
        return self.respuestas.pop(0)


def test_reintenta_si_el_waf_devuelve_html(tmp_path):
    zip_ok = b"PK\x03\x04" + b"\x00" * 100
    sesion = SesionFalsa([
        RespuestaFalsa(200, HTML_WAF, {"Content-Type": "text/html"}),
        RespuestaFalsa(200, zip_ok, {"Content-Type": "application/zip", "ETag": '"abc"'}),
    ])
    esperas = []
    destino = tmp_path / "f.zip"
    ruta = descarga.descargar("https://x/f.zip", destino, sesion=sesion, dormir=esperas.append)
    assert ruta == destino and destino.read_bytes() == zip_ok
    assert esperas == [descarga.ESPERA_BASE]
    assert not destino.with_name("f.zip.part").exists()
    assert descarga._leer_meta(destino)["etag"] == '"abc"'


def test_reutiliza_si_304(tmp_path):
    destino = tmp_path / "f.zip"
    destino.write_bytes(b"PK\x03\x04viejo")
    descarga._guardar_meta(destino, {"etag": '"abc"'})
    sesion = SesionFalsa([RespuestaFalsa(304)])
    descarga.descargar("https://x/f.zip", destino, sesion=sesion, dormir=lambda s: None)
    assert destino.read_bytes() == b"PK\x03\x04viejo"
    assert sesion.llamadas[0][1]["If-None-Match"] == '"abc"'


def test_404_no_reintenta(tmp_path):
    sesion = SesionFalsa([RespuestaFalsa(404)])
    with pytest.raises(descarga.NoDisponible):
        descarga.descargar("https://x/f.zip", tmp_path / "f.zip", sesion=sesion, dormir=lambda s: None)
    assert len(sesion.llamadas) == 1


def test_agota_reintentos(tmp_path):
    sesion = SesionFalsa([RespuestaFalsa(200, HTML_WAF, {"Content-Type": "text/html"})] * 3)
    with pytest.raises(descarga.DescargaInvalida):
        descarga.descargar("https://x/f.zip", tmp_path / "f.zip", sesion=sesion, reintentos=3, dormir=lambda s: None)
    assert len(sesion.llamadas) == 3
