import re

CARACTERES_NAO_ALFANUMERICOS = re.compile(r"[^A-Z0-9]")


def normalizar_numero_processo(numero: str) -> str:
    return CARACTERES_NAO_ALFANUMERICOS.sub("", numero.upper())
