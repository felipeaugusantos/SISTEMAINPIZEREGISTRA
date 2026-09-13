import re
import unicodedata

CARACTERES_NAO_ALFANUMERICOS = re.compile(r"[^A-Z0-9]")


def normalizar_numero_processo(numero: str) -> str:
    return CARACTERES_NAO_ALFANUMERICOS.sub("", numero.upper())


def normalizar_busca(valor: str) -> str:
    """Remove acentos e uniformiza espaços/caixa para casar com colunas
    indexadas por immutable_unaccent(...) (ver ix_processos_titulo_trgm) --
    usar sempre que o termo de busca for comparado a uma coluna assim, para
    não perder o índice nem deixar a busca sensível a acento."""
    sem_acentos = "".join(
        caractere for caractere in unicodedata.normalize("NFKD", valor.strip()) if not unicodedata.combining(caractere)
    )
    return " ".join(sem_acentos.casefold().split())
