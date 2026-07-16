from datetime import date, datetime
from enum import StrEnum

from sqlalchemy import (
    BigInteger,
    Column,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Table,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class TipoProcesso(StrEnum):
    MARCA = "marca"
    PATENTE = "patente"


class StatusLead(StrEnum):
    NOVO = "novo"
    EM_CONTATO = "em_contato"
    CONVERTIDO = "convertido"
    DESCARTADO = "descartado"


processo_titulares = Table(
    "processo_titulares",
    Base.metadata,
    Column("processo_id", ForeignKey("processos.id", ondelete="CASCADE"), primary_key=True),
    Column("titular_id", ForeignKey("titulares.id", ondelete="CASCADE"), primary_key=True),
)


class Processo(Base):
    __tablename__ = "processos"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    numero: Mapped[str] = mapped_column(String(30), unique=True, index=True)
    numero_normalizado: Mapped[str] = mapped_column(String(30), unique=True, index=True)
    tipo: Mapped[TipoProcesso] = mapped_column(
        Enum(
            TipoProcesso,
            name="tipo_processo",
            native_enum=False,
            create_constraint=True,
            values_callable=lambda members: [member.value for member in members],
        ),
        index=True,
    )
    titulo: Mapped[str | None] = mapped_column(Text)
    data_deposito: Mapped[date | None] = mapped_column(Date)
    situacao: Mapped[str | None] = mapped_column(String(255), index=True)
    fonte: Mapped[str] = mapped_column(String(100))
    apresentacao: Mapped[str | None] = mapped_column(String(100))
    natureza: Mapped[str | None] = mapped_column(String(150))
    elemento_nominativo: Mapped[str | None] = mapped_column(Text)
    procurador: Mapped[str | None] = mapped_column(Text)
    imagem_url: Mapped[str | None] = mapped_column(Text)
    atualizado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    titulares: Mapped[list["Titular"]] = relationship(
        secondary=processo_titulares, back_populates="processos", lazy="selectin"
    )
    movimentacoes: Mapped[list["Movimentacao"]] = relationship(
        back_populates="processo",
        cascade="all, delete-orphan",
        order_by="Movimentacao.data_rpi.desc()",
        lazy="selectin",
    )
    classificacoes: Mapped[list["ClassificacaoMarca"]] = relationship(
        back_populates="processo",
        cascade="all, delete-orphan",
        order_by="ClassificacaoMarca.sistema, ClassificacaoMarca.codigo",
        lazy="selectin",
    )


class Titular(Base):
    __tablename__ = "titulares"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    nome: Mapped[str] = mapped_column(Text, index=True)
    pais: Mapped[str | None] = mapped_column(String(2))

    processos: Mapped[list[Processo]] = relationship(
        secondary=processo_titulares, back_populates="titulares"
    )


class Movimentacao(Base):
    __tablename__ = "movimentacoes"
    __table_args__ = (UniqueConstraint("chave_origem", name="uq_movimentacoes_chave_origem"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    processo_id: Mapped[int] = mapped_column(
        ForeignKey("processos.id", ondelete="CASCADE"), index=True
    )
    codigo_despacho: Mapped[str | None] = mapped_column(String(30), index=True)
    descricao: Mapped[str] = mapped_column(Text)
    data_rpi: Mapped[date] = mapped_column(Date, index=True)
    numero_rpi: Mapped[int] = mapped_column(Integer, index=True)
    fonte_arquivo: Mapped[str] = mapped_column(Text)
    chave_origem: Mapped[str] = mapped_column(String(64))

    processo: Mapped[Processo] = relationship(back_populates="movimentacoes")


class ClassificacaoMarca(Base):
    __tablename__ = "classificacoes_marca"
    __table_args__ = (
        UniqueConstraint(
            "processo_id",
            "sistema",
            "codigo",
            name="uq_classificacoes_marca_processo_sistema_codigo",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    processo_id: Mapped[int] = mapped_column(
        ForeignKey("processos.id", ondelete="CASCADE"), index=True
    )
    sistema: Mapped[str] = mapped_column(String(20), index=True)
    codigo: Mapped[str] = mapped_column(String(30), index=True)
    edicao: Mapped[str | None] = mapped_column(String(20))
    especificacao: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str | None] = mapped_column(String(255))

    processo: Mapped[Processo] = relationship(back_populates="classificacoes")


class Lead(Base):
    __tablename__ = "leads"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    nome: Mapped[str] = mapped_column(String(150), index=True)
    email: Mapped[str] = mapped_column(String(254), index=True)
    telefone: Mapped[str] = mapped_column(String(30), index=True)
    marca: Mapped[str] = mapped_column(Text, index=True)
    processo_numero: Mapped[str | None] = mapped_column(String(50), nullable=True, index=True)
    origem: Mapped[str] = mapped_column(String(30), default="resultados", index=True)
    tipo_interesse: Mapped[TipoProcesso | None] = mapped_column(
        Enum(
            TipoProcesso,
            name="tipo_processo",
            native_enum=False,
            create_constraint=False,
            values_callable=lambda members: [member.value for member in members],
        ),
        nullable=True,
    )
    status: Mapped[StatusLead] = mapped_column(
        Enum(
            StatusLead,
            name="status_lead",
            native_enum=False,
            create_constraint=True,
            values_callable=lambda members: [member.value for member in members],
        ),
        default=StatusLead.NOVO,
        index=True,
    )
    aceite_privacidade: Mapped[bool] = mapped_column(default=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    atualizado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
