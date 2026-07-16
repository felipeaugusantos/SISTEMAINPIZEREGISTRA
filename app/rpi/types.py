from dataclasses import dataclass
from datetime import date

from app.models import TipoProcesso


@dataclass(frozen=True, slots=True)
class TitularRpi:
    nome: str
    pais: str | None


@dataclass(frozen=True, slots=True)
class MovimentacaoRpi:
    codigo: str | None
    descricao: str


@dataclass(frozen=True, slots=True)
class ClassificacaoMarcaRpi:
    sistema: str
    codigo: str
    edicao: str | None = None
    especificacao: str | None = None
    status: str | None = None


@dataclass(frozen=True, slots=True)
class RegistroRpi:
    numero: str
    tipo: TipoProcesso
    titulo: str | None
    data_deposito: date | None
    situacao: str | None
    numero_rpi: int
    data_rpi: date
    fonte_arquivo: str
    titulares: tuple[TitularRpi, ...]
    movimentacoes: tuple[MovimentacaoRpi, ...]
    ordem: int = 0
    apresentacao: str | None = None
    natureza: str | None = None
    elemento_nominativo: str | None = None
    procurador: str | None = None
    # Reservado: o feed público da RPI não traz a URL da figura da marca.
    # Permanece propagado (modelo/schema/front) para ser preenchido caso uma
    # fonte com imagens seja integrada no futuro.
    imagem_url: str | None = None
    classificacoes: tuple[ClassificacaoMarcaRpi, ...] = ()


@dataclass(slots=True)
class ResultadoImportacao:
    registros_processados: int = 0
    processos_novos: int = 0
    movimentacoes_novas: int = 0
