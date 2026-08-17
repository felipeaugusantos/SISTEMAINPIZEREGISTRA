from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
from pathlib import Path

from defusedxml.ElementTree import ParseError, iterparse

from app.models import TipoProcesso


@dataclass(frozen=True, slots=True)
class AnomaliaImportacao:
    codigo: str
    severidade: str
    mensagem: str


def calcular_integridade_arquivo(caminho: Path) -> tuple[str, int]:
    digest = sha256()
    tamanho = 0
    with caminho.open("rb") as arquivo:
        for bloco in iter(lambda: arquivo.read(1024 * 1024), b""):
            digest.update(bloco)
            tamanho += len(bloco)
    return digest.hexdigest(), tamanho


def validar_arquivo_rpi(caminho: Path, tipo: TipoProcesso) -> int:
    """Valida o XML inteiro antes de iniciar qualquer gravação no banco."""
    if not caminho.is_file() or caminho.stat().st_size == 0:
        raise ValueError("Arquivo da RPI ausente ou vazio")
    tag = "processo" if tipo is TipoProcesso.MARCA else "despacho"
    quantidade = 0
    try:
        contexto = iterparse(caminho, events=("start", "end"))
        _, raiz = next(contexto)
        atributo_data = "data" if tipo is TipoProcesso.MARCA else "dataPublicacao"
        if raiz.tag != "revista" or not raiz.attrib.get("numero") or not raiz.attrib.get(atributo_data):
            raise ValueError("Cabeçalho de RPI inválido")
        for evento, elemento in contexto:
            if evento == "end" and elemento.tag == tag:
                quantidade += 1
    except (OSError, ParseError, StopIteration) as exc:
        raise ValueError("XML da RPI incompleto ou ilegível") from exc
    if quantidade == 0:
        raise ValueError("XML da RPI não contém registros")
    return quantidade


def avaliar_importacao(
    *,
    numero_rpi: int,
    registros: int,
    titulares: int,
    classes: int,
    movimentacoes: int,
    arquivo_tamanho_bytes: int,
    arquivo_sha256: str,
    anterior: dict | None = None,
    mesma_edicao_anterior: dict | None = None,
    ultima_edicao_importada: int | None = None,
    razao_minima_registros: float = 0.50,
    minimo_referencia_registros: int = 1_000,
    exigir_classes: bool = True,
) -> tuple[str, list[dict]]:
    anomalias: list[AnomaliaImportacao] = []

    def adicionar(codigo: str, severidade: str, mensagem: str) -> None:
        anomalias.append(AnomaliaImportacao(codigo, severidade, mensagem))

    if arquivo_tamanho_bytes <= 0 or not arquivo_sha256:
        adicionar("ARQUIVO_INVALIDO", "critica", "Arquivo da RPI vazio ou sem checksum.")
    if registros == 0:
        adicionar("RPI_SEM_REGISTROS", "critica", "A edição não produziu registros.")
    if registros > 0 and movimentacoes == 0:
        adicionar(
            "RPI_SEM_MOVIMENTACOES",
            "aviso",
            "A edição possui registros, mas nenhuma movimentação foi importada.",
        )
    if exigir_classes and registros > 0 and classes == 0:
        adicionar(
            "RPI_SEM_CLASSES",
            "aviso",
            "A edição possui registros, mas nenhuma classe foi importada.",
        )
    if anterior:
        referencia = int(anterior.get("registros_processados") or 0)
        if (
            referencia >= minimo_referencia_registros
            and registros < referencia * razao_minima_registros
        ):
            adicionar(
                "QUEDA_ABRUPTA_REGISTROS",
                "aviso",
                f"Quantidade caiu de {referencia} para {registros} registros.",
            )
    if ultima_edicao_importada is not None and numero_rpi > ultima_edicao_importada + 1:
        adicionar(
            "EDICAO_PULADA",
            "aviso",
            f"Edição anterior esperada: {numero_rpi - 1}; última importada: {ultima_edicao_importada}.",
        )
    if mesma_edicao_anterior and mesma_edicao_anterior.get("arquivo_sha256") == arquivo_sha256:
        campos = (
            "registros_processados",
            "titulares_processados",
            "classes_processadas",
            "movimentacoes_processadas",
        )
        atual = (registros, titulares, classes, movimentacoes)
        antes = tuple(int(mesma_edicao_anterior.get(campo) or 0) for campo in campos)
        if antes != atual:
            adicionar(
                "RESULTADO_NAO_REPRODUZIVEL",
                "critica",
                "O mesmo arquivo produziu estatísticas diferentes em novo processamento.",
            )

    status = (
        "erro"
        if any(item.severidade == "critica" for item in anomalias)
        else ("atencao" if anomalias else "ok")
    )
    return status, [asdict(item) for item in anomalias]
