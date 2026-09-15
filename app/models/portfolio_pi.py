from datetime import date, datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Column,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Table,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.enums import TipoProcesso
from app.request_context import request_id_atual

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
    situacao_normalizada: Mapped[str | None] = mapped_column(String(30), nullable=True, index=True)
    relevancia_situacao: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)
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
    nome_normalizado: Mapped[str | None] = mapped_column(Text, index=True)
    pais: Mapped[str | None] = mapped_column(String(2))

    processos: Mapped[list[Processo]] = relationship(secondary=processo_titulares, back_populates="titulares")


class AtivoPI(Base):
    """Ativo de propriedade intelectual pertencente a uma organização."""

    __tablename__ = "ativos_pi"
    __table_args__ = (UniqueConstraint("organizacao_id", "codigo", name="uq_ativo_pi_org_codigo"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organizacao_id: Mapped[int] = mapped_column(ForeignKey("organizacoes.id", ondelete="CASCADE"), index=True)
    cliente_portal_id: Mapped[int | None] = mapped_column(
        ForeignKey("clientes_portal.id", ondelete="SET NULL"), nullable=True, index=True
    )
    titular_id: Mapped[int] = mapped_column(ForeignKey("titulares.id", ondelete="RESTRICT"), index=True)
    codigo: Mapped[str] = mapped_column(String(80), index=True)
    tipo: Mapped[str] = mapped_column(String(30), index=True)
    nome: Mapped[str] = mapped_column(String(240), index=True)
    status: Mapped[str] = mapped_column(String(30), default="ativo", index=True)
    vigencia_inicio: Mapped[date | None] = mapped_column(Date, nullable=True)
    vigencia_fim: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    dados: Mapped[dict] = mapped_column(JSON, default=dict)
    criado_por: Mapped[str | None] = mapped_column(String(254), nullable=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    atualizado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class AtivoProcessoPI(Base):
    __tablename__ = "ativos_processos_pi"
    __table_args__ = (UniqueConstraint("ativo_id", "processo_id", name="uq_ativo_pi_processo"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organizacao_id: Mapped[int] = mapped_column(ForeignKey("organizacoes.id", ondelete="CASCADE"), index=True)
    ativo_id: Mapped[int] = mapped_column(ForeignKey("ativos_pi.id", ondelete="CASCADE"), index=True)
    processo_id: Mapped[int] = mapped_column(ForeignKey("processos.id", ondelete="CASCADE"), index=True)
    papel: Mapped[str] = mapped_column(String(30), default="principal")
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AtivoPartePI(Base):
    __tablename__ = "ativos_partes_pi"
    __table_args__ = (UniqueConstraint("ativo_id", "papel", "nome", name="uq_ativo_pi_parte"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organizacao_id: Mapped[int] = mapped_column(ForeignKey("organizacoes.id", ondelete="CASCADE"), index=True)
    ativo_id: Mapped[int] = mapped_column(ForeignKey("ativos_pi.id", ondelete="CASCADE"), index=True)
    papel: Mapped[str] = mapped_column(String(20), index=True)
    nome: Mapped[str] = mapped_column(String(240))
    documento: Mapped[str | None] = mapped_column(String(30), nullable=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DocumentoAtivoPI(Base):
    __tablename__ = "documentos_ativos_pi"
    __table_args__ = (UniqueConstraint("ativo_id", "versao", name="uq_documento_ativo_pi_versao"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organizacao_id: Mapped[int] = mapped_column(ForeignKey("organizacoes.id", ondelete="CASCADE"), index=True)
    ativo_id: Mapped[int] = mapped_column(ForeignKey("ativos_pi.id", ondelete="CASCADE"), index=True)
    nome: Mapped[str] = mapped_column(String(255))
    versao: Mapped[int] = mapped_column(Integer)
    hash_documento: Mapped[str] = mapped_column(String(64), index=True)
    caminho: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_type: Mapped[str | None] = mapped_column(String(120), nullable=True)
    criado_por: Mapped[str | None] = mapped_column(String(254), nullable=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


class Movimentacao(Base):
    __tablename__ = "movimentacoes"
    __table_args__ = (
        UniqueConstraint("chave_origem", name="uq_movimentacoes_chave_origem"),
        Index("ix_movimentacoes_rpi_consulta", "numero_rpi", "data_rpi", "id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    processo_id: Mapped[int] = mapped_column(ForeignKey("processos.id", ondelete="CASCADE"), index=True)
    codigo_despacho: Mapped[str | None] = mapped_column(String(30), index=True)
    descricao: Mapped[str] = mapped_column(Text)
    data_rpi: Mapped[date] = mapped_column(Date, index=True)
    numero_rpi: Mapped[int] = mapped_column(Integer, index=True)
    fonte_arquivo: Mapped[str] = mapped_column(Text)
    chave_origem: Mapped[str] = mapped_column(String(64))

    processo: Mapped[Processo] = relationship(back_populates="movimentacoes")


class RpiImportacao(Base):
    __tablename__ = "rpi_importacoes"

    numero_rpi: Mapped[int] = mapped_column(Integer, primary_key=True)
    tipo: Mapped[str] = mapped_column(String(10), primary_key=True)
    registros_processados: Mapped[int] = mapped_column(Integer)
    titulares_processados: Mapped[int] = mapped_column(Integer, default=0)
    classes_processadas: Mapped[int] = mapped_column(Integer, default=0)
    movimentacoes_processadas: Mapped[int] = mapped_column(Integer, default=0)
    arquivo_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    arquivo_tamanho_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    status_integridade: Mapped[str] = mapped_column(String(20), default="desconhecido", index=True)
    anomalias: Mapped[list[dict]] = mapped_column(JSON, default=list)
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    erro: Mapped[str | None] = mapped_column(Text, nullable=True)
    tentativas: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    fonte_oficial: Mapped[str] = mapped_column(
        String(120),
        default="INPI - Revista da Propriedade Industrial",
        server_default="INPI - Revista da Propriedade Industrial",
    )
    importado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RpiImportacaoHistorico(Base):
    """Tentativas de importação preservadas para rastreabilidade e reprocessamento."""

    __tablename__ = "rpi_importacoes_historico"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    numero_rpi: Mapped[int] = mapped_column(Integer, index=True)
    tipo: Mapped[str] = mapped_column(String(10), index=True)
    arquivo_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    arquivo_tamanho_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    status: Mapped[str] = mapped_column(String(20), index=True)
    registros_processados: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    titulares_processados: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    classes_processadas: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    movimentacoes_processadas: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    anomalias: Mapped[list[dict]] = mapped_column(JSON, default=list)
    erro: Mapped[str | None] = mapped_column(Text, nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


class RpiSyncExecucao(Base):
    __tablename__ = "rpi_sync_execucoes"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    origem: Mapped[str] = mapped_column(String(20), index=True)
    status: Mapped[str] = mapped_column(String(30), index=True)
    solicitado_por: Mapped[str | None] = mapped_column(String(150), nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True, default=request_id_atual)
    execucao_anterior_id: Mapped[int | None] = mapped_column(
        ForeignKey("rpi_sync_execucoes.id", ondelete="SET NULL"), nullable=True, index=True
    )
    rpi_inicio: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rpi_fim: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rpi_atual: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ultima_rpi_oficial: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ultima_rpi_local_antes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ultima_rpi_local_depois: Mapped[int | None] = mapped_column(Integer, nullable=True)
    edicoes_total: Mapped[int] = mapped_column(Integer, default=0)
    edicoes_processadas: Mapped[int] = mapped_column(Integer, default=0)
    registros_processados: Mapped[int] = mapped_column(Integer, default=0)
    titulares_processados: Mapped[int] = mapped_column(Integer, default=0)
    classes_processadas: Mapped[int] = mapped_column(Integer, default=0)
    movimentacoes_processadas: Mapped[int] = mapped_column(Integer, default=0)
    mensagem: Mapped[str | None] = mapped_column(Text, nullable=True)
    erro: Mapped[str | None] = mapped_column(Text, nullable=True)
    solicitado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    iniciado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    heartbeat_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finalizado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)


class RpiSyncEstado(Base):
    __tablename__ = "rpi_sync_estado"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    status: Mapped[str] = mapped_column(String(30), default="iniciando", index=True)
    execucao_atual_id: Mapped[int | None] = mapped_column(
        ForeignKey("rpi_sync_execucoes.id", ondelete="SET NULL"), nullable=True
    )
    ultima_rpi_oficial: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ultima_rpi_local: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ultima_verificacao_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    proxima_verificacao_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    heartbeat_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    falhas_consecutivas: Mapped[int] = mapped_column(Integer, default=0)
    ultimo_erro: Mapped[str | None] = mapped_column(Text, nullable=True)
    atualizado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


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
    processo_id: Mapped[int] = mapped_column(ForeignKey("processos.id", ondelete="CASCADE"), index=True)
    sistema: Mapped[str] = mapped_column(String(20), index=True)
    codigo: Mapped[str] = mapped_column(String(30), index=True)
    edicao: Mapped[str | None] = mapped_column(String(20))
    especificacao: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str | None] = mapped_column(String(255))

    processo: Mapped[Processo] = relationship(back_populates="classificacoes")
