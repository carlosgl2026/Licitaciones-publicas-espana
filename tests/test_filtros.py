from ginebra.filtros import (es_alerta_nube, es_cercania, es_tamano_adecuado, es_tecnologia,
                             motivos, pasa_filtro_negocio)
from ginebra.modelo import Licitacion


def lic(**kw):
    base = dict(id="x", fuente="placsp")
    base.update(kw)
    return Licitacion(**base)


def test_tecnologia_por_cpv_o_palabra():
    assert es_tecnologia(lic(cpv="72000000"))
    assert es_tecnologia(lic(cpvs="45000000;48000000"))
    assert es_tecnologia(lic(objeto="Desarrollo de software para la sede"))
    assert es_tecnologia(lic(objeto="Implantación de un ERP"))
    assert not es_tecnologia(lic(objeto="Suministro de pintura y erpoleo"))   # 'erp' solo como palabra
    assert not es_tecnologia(lic(cpv="45000000", objeto="Obras de asfaltado"))
    assert es_tecnologia({"cpv": "72000000", "objeto": ""})                   # también acepta dicts


def test_tamano():
    assert es_tamano_adecuado(lic(presupuesto_sin_iva=100_000))
    assert es_tamano_adecuado(lic(presupuesto_sin_iva=5_000_000))
    assert not es_tamano_adecuado(lic(presupuesto_sin_iva=99_999))
    assert not es_tamano_adecuado(lic(presupuesto_sin_iva=5_000_001))
    assert es_tamano_adecuado(lic(valor_estimado=300_000))                    # sin presupuesto, vale el valor estimado
    assert not es_tamano_adecuado(lic())


def test_cercania_por_senales_estructuradas():
    assert es_cercania(lic(lugar_nuts="ES300"))
    assert es_cercania(lic(organo_cp="28071"))
    assert es_cercania(lic(organo_ciudad="Getafe"))
    assert es_cercania(lic(plataforma_agregada="Comunidad de Madrid"))
    assert es_cercania(lic(lugar_texto="Madrid"))
    assert es_cercania(lic(jerarquia="Ministerio de Hacienda > Subsecretaría", organo="D.G. del Patrimonio"))
    assert es_cercania(lic(organo="Ayuntamiento de Pinto"))
    assert es_cercania(lic(jerarquia="Sector Público > ENTIDADES LOCALES > Comunidad de Madrid > Ayuntamientos > Cenicientos", organo="Alcaldía"))
    assert es_cercania(lic(jerarquia="Sector Público > ADMINISTRACIÓN GENERAL DEL ESTADO > Ministerio de Cultura", organo="Museo del Prado"))
    assert not es_cercania(lic(jerarquia="Sector Público > ENTIDADES LOCALES > Castilla y León > Burgos > Ayuntamientos > Lerma", organo="Alcaldía"))
    # organismo estatal con sede conocida fuera de Madrid: no cuenta
    assert not es_cercania(lic(jerarquia="Sector Público > ADMINISTRACIÓN GENERAL DEL ESTADO > Ministerio de Transportes", organo="Autoridad Portuaria de Valencia", organo_cp="46024"))
    assert es_cercania(lic(jerarquia="Sector Público > ADMINISTRACIÓN GENERAL DEL ESTADO > Ministerio de Transportes", organo="Autoridad Portuaria de Valencia", organo_cp="46024", lugar_nuts="ES300"))
    assert not es_cercania(lic(organo="Obras en el pintoresco pueblo de Lugo", lugar_nuts="ES11"))
    assert not es_cercania(lic(plataforma_agregada="Generalitat de Catalunya", organo="Departament de Salut"))


def test_filtro_negocio_y_motivos():
    ok = lic(objeto="Servicio de desarrollo de software", cpv="72000000", presupuesto_sin_iva=250_000, organo_cp="28901")
    assert pasa_filtro_negocio(ok)
    m = motivos(ok)
    assert m["tecnologia"] == "CPV 72000000"
    assert m["tamano"] == "250.000 €"
    assert m["cercania"] == "órgano con CP 28901"
    assert "nube" not in m
    assert not pasa_filtro_negocio(lic(objeto="Desarrollo de software", cpv="72000000", presupuesto_sin_iva=250_000, organo="Universidad de Sevilla"))


def test_alerta_nube():
    assert es_alerta_nube(lic(objeto="Migración a la nube pública"))
    assert es_alerta_nube(lic(objeto="Servicios de Google Cloud"))
    assert es_alerta_nube(lic(objeto="Alojamiento en AWS"))
    assert not es_alerta_nube(lic(objeto="Servicio de limpieza"))
    assert not es_alerta_nube(lic(objeto="Suministro de nubes de azúcar"))   # "nube" solo como palabra completa
