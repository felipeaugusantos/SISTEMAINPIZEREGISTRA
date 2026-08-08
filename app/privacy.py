import re

_CNPJ = re.compile(r"(?<!\d)\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}(?!\d)")
_CPF = re.compile(r"(?<!\d)\d{3}\.?\d{3}\.?\d{3}-?\d{2}(?!\d)")


def mascarar_documentos_publicos(valor: str) -> str:
    """Remove CPF/CNPJ de textos exibidos em relatórios públicos."""
    valor = _CNPJ.sub("**.***.***/****-**", valor)
    return _CPF.sub("***.***.***-**", valor)
