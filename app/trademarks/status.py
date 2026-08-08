import re
import unicodedata
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SituacaoNormalizada:
    codigo: str
    rotulo: str
    relevancia: str


SITUACOES = {
    "registrada": SituacaoNormalizada("registrada", "Registro em vigor", "ativa"),
    "deferida": SituacaoNormalizada("deferida", "Pedido deferido", "ativa"),
    "publicada": SituacaoNormalizada("publicada", "Pedido publicado", "ativa"),
    "em_exame": SituacaoNormalizada("em_exame", "Em exame", "ativa"),
    "exigencia": SituacaoNormalizada("exigencia", "Exigência", "ativa"),
    "oposicao": SituacaoNormalizada("oposicao", "Oposição", "ativa"),
    "recurso": SituacaoNormalizada("recurso", "Em recurso", "ativa"),
    "suspensa": SituacaoNormalizada("suspensa", "Exame suspenso", "incerta"),
    "indeferida": SituacaoNormalizada("indeferida", "Pedido indeferido", "inativa"),
    "arquivada": SituacaoNormalizada("arquivada", "Pedido arquivado", "inativa"),
    "extinta": SituacaoNormalizada("extinta", "Registro extinto", "inativa"),
    "cancelada": SituacaoNormalizada("cancelada", "Registro cancelado", "inativa"),
    "nao_classificada": SituacaoNormalizada(
        "nao_classificada", "Situação não classificada", "incerta"
    ),
}


def _normalizar(valor: str | None) -> str:
    if not valor:
        return ""
    sem_acentos = "".join(
        caractere
        for caractere in unicodedata.normalize("NFKD", valor)
        if not unicodedata.combining(caractere)
    )
    return re.sub(r"\s+", " ", sem_acentos.lower()).strip()


def normalizar_despacho(codigo: str | None, descricao: str | None) -> SituacaoNormalizada:
    """Agrupa despachos em situações legíveis sem substituir o texto oficial."""
    texto = _normalizar(f"{codigo or ''} {descricao or ''}")

    regras = (
        (("indefer",), "indeferida"),
        (("arquiv",), "arquivada"),
        (("extinc", "extinto", "caducidade"), "extinta"),
        (("cancel",), "cancelada"),
        (("deferimento", "deferido"), "deferida"),
        (
            ("concessao de registro", "registro de marca concedido", "registro em vigor"),
            "registrada",
        ),
        (("exigencia",), "exigencia"),
        (("oposicao",), "oposicao"),
        (("recurso",), "recurso"),
        (("sobrest", "suspens"), "suspensa"),
        (("publicacao do pedido", "pedido de registro para oposicao"), "publicada"),
        (("exame de merito", "exame formal", "em exame"), "em_exame"),
    )
    for termos, situacao in regras:
        if any(termo in texto for termo in termos):
            return SITUACOES[situacao]
    return SITUACOES["nao_classificada"]
