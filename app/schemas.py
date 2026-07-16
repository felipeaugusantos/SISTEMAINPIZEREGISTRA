from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models import StatusLead, TipoProcesso


class TitularResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    nome: str
    pais: str | None


class MovimentacaoResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    codigo_despacho: str | None
    descricao: str
    data_rpi: date
    numero_rpi: int


class ClassificacaoMarcaResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    sistema: str
    codigo: str
    edicao: str | None
    especificacao: str | None
    status: str | None


class ProcessoResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    numero: str
    tipo: TipoProcesso
    titulo: str | None
    data_deposito: date | None
    situacao: str | None
    fonte: str
    apresentacao: str | None
    natureza: str | None
    elemento_nominativo: str | None
    procurador: str | None
    imagem_url: str | None
    atualizado_em: datetime
    titulares: list[TitularResponse]
    movimentacoes: list[MovimentacaoResponse]
    classificacoes: list[ClassificacaoMarcaResponse]


class ProcessoResumo(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    numero: str
    tipo: TipoProcesso
    titulo: str | None
    data_deposito: date | None
    situacao: str | None
    atualizado_em: datetime
    titulares: list[TitularResponse]


class ResultadoBusca(BaseModel):
    total: int
    limite: int
    deslocamento: int
    itens: list[ProcessoResumo]


class LeadCreate(BaseModel):
    nome: str = Field(min_length=2, max_length=150)
    email: str = Field(min_length=5, max_length=254, pattern=r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
    telefone: str = Field(min_length=10, max_length=30)
    marca: str = Field(default="", max_length=200)
    processo_numero: str | None = Field(default=None, max_length=50)
    origem: Literal["resultados", "processo", "geral"] = "resultados"
    tipo_interesse: TipoProcesso | None = None
    aceite_privacidade: Literal[True]
    website: str = Field(default="", max_length=200)

    @field_validator("nome", "email", "telefone", "marca")
    @classmethod
    def remover_espacos(cls, valor: str) -> str:
        return valor.strip()

    @field_validator("processo_numero")
    @classmethod
    def normalizar_processo_numero(cls, valor: str | None) -> str | None:
        if valor is None:
            return None
        return valor.strip() or None

    @field_validator("telefone")
    @classmethod
    def validar_telefone(cls, valor: str) -> str:
        digitos = "".join(caractere for caractere in valor if caractere.isdigit())
        if not 10 <= len(digitos) <= 15:
            raise ValueError("Informe um telefone válido com DDD")
        return valor


class LeadResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    nome: str
    email: str
    telefone: str
    marca: str
    processo_numero: str | None
    origem: str
    tipo_interesse: TipoProcesso | None
    status: StatusLead
    criado_em: datetime
    atualizado_em: datetime


class LeadListResponse(BaseModel):
    total: int
    itens: list[LeadResponse]
    por_status: dict[str, int]


class LeadStatusUpdate(BaseModel):
    status: StatusLead
