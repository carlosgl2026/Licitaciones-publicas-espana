"""Listas de códigos CODICE usadas en los feeds de PLACSP, con su etiqueta.

Fuente: ficheros .gc publicados en https://contrataciondelestado.es/codice/cl/
(SyndicationContractFolderStatusCode, SyndicationContractCode,
SyndicationTenderingProcessCode, TenderResultCode, TenderingNoticeTypeCode,
ContractingAuthorityActivityCode).
"""

from __future__ import annotations

ESTADOS = {
    "PRE": "Anuncio previo",
    "PUB": "En plazo",
    "EV": "Pendiente de adjudicación",
    "ADJ": "Adjudicada",
    "RES": "Resuelta",
    "ANUL": "Anulada",
}

TIPOS_CONTRATO = {
    "1": "Suministros",
    "2": "Servicios",
    "3": "Obras",
    "7": "Administrativo especial",
    "8": "Privado",
    "21": "Gestión de servicios públicos",
    "22": "Concesión de servicios",
    "31": "Concesión de obras públicas",
    "32": "Concesión de obras",
    "40": "Colaboración público-privada",
    "50": "Patrimonial",
    "999": "Otros",
}

PROCEDIMIENTOS = {
    "1": "Abierto",
    "2": "Restringido",
    "3": "Negociado sin publicidad",
    "4": "Negociado con publicidad",
    "5": "Diálogo competitivo",
    "6": "Contrato menor",
    "7": "Derivado de acuerdo marco",
    "8": "Concurso de proyectos",
    "9": "Abierto simplificado",
    "10": "Asociación para la innovación",
    "11": "Derivado de asociación para la innovación",
    "12": "Basado en sistema dinámico de adquisición",
    "13": "Licitación con negociación",
    "100": "Normas internas",
    "999": "Otros",
}

RESULTADOS = {
    "1": "Adjudicado provisionalmente",
    "2": "Adjudicado definitivamente",
    "3": "Desierto",
    "4": "Desistimiento",
    "5": "Renuncia",
    "6": "Desierto provisionalmente",
    "7": "Desierto definitivamente",
    "8": "Adjudicado",
    "9": "Formalizado",
    "10": "Licitador mejor valorado",
}

TIPOS_ANUNCIO = {
    "DOC_PIN": "Anuncio previo",
    "DOC_CN": "Anuncio de licitación",
    "DOC_CD": "Anuncio de pliegos",
    "DOC_DD": "Anuncio de documento descriptivo",
    "DOC_CAN_PROV": "Adjudicación provisional",
    "DOC_CAN_DEF": "Adjudicación definitiva",
    "DOC_CAN_ADJ": "Anuncio de adjudicación",
    "DOC_FORM": "Anuncio de formalización",
    "DOC_MOD": "Modificación de contrato",
    "DOC_ATPV": "Transparencia previa voluntaria",
    "RENUNCIA": "Renuncia",
    "DESISTIMIENTO": "Desistimiento",
    "ANUL_PIN": "Anulación de anuncio previo",
    "ANUL_CN": "Anulación de anuncio de licitación",
    "ANUL_CD": "Anulación de anuncio de pliegos",
    "ANUL_DD": "Anulación de documento descriptivo",
    "ANUL_CAN_ADJ": "Anulación de adjudicación",
    "ANUL_FORM": "Anulación de formalización",
    "ANUL_RENUNCIA": "Anulación de renuncia",
    "ANUL_DESISTIMIENTO": "Anulación de desistimiento",
    "ANUL_DOC_MOD": "Anulación de modificación",
}

ACTIVIDADES = {
    "1": "Justicia", "2": "Defensa", "3": "Seguridad ciudadana e instituciones penitenciarias",
    "4": "Política exterior", "5": "Pensiones", "6": "Otras prestaciones económicas",
    "7": "Servicios sociales y promoción social", "8": "Fomento del empleo", "9": "Desempleo",
    "10": "Acceso a la vivienda y fomento de la edificación",
    "11": "Gestión y administración de la Seguridad Social", "12": "Sanidad", "13": "Educación",
    "14": "Cultura", "15": "Agricultura, pesca y alimentación", "16": "Industria y energía",
    "17": "Comercio, turismo y pymes", "18": "Subvenciones al transporte", "19": "Infraestructuras",
    "20": "Investigación, desarrollo e innovación", "21": "Otras actuaciones de carácter económico",
    "22": "Servicios de carácter general", "23": "Administración financiera y tributaria",
    "24": "Transferencias a otras administraciones públicas", "25": "Deuda pública",
    "26": "Medio ambiente", "27": "Interior", "28": "Economía y Hacienda", "29": "Ocio",
    "101": "Aeropuertos", "102": "Electricidad", "103": "Extracción de carbón y combustibles sólidos",
    "104": "Exploración y extracción de gas", "105": "Puertos", "106": "Servicios postales",
    "107": "Distribución de gas", "108": "Servicios ferroviarios", "109": "Tranvía, metro y autobús",
    "110": "Agua",
}


def etiqueta(tabla: dict[str, str], codigo: str | None) -> str:
    if not codigo:
        return ""
    return tabla.get(str(codigo).strip(), str(codigo).strip())
