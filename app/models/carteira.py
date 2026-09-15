from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models._core import EmpresaCRM, Processo

if TYPE_CHECKING:
    # Referenciadas aqui so como forward reference em Mapped["..."]
    # (resolvida em tempo de execucao pelo registry do SQLAlchemy -- este
    # import existe so para o ruff/checadores de tipo).
    from app.models._core import Lead, UsuarioOperacoes
    from app.models.juridico import PrazoJuridico


class ProcessoMonitorado(Base):
    """Processo da base RPI incluído na carteira de acompanhamento de um tenant."""

    __tablename__ = "processos_monitorados"
    __table_args__ = (
        UniqueConstraint(
            "organizacao_id",
            "processo_id",
            name="uq_processo_monitorado_org_processo",
        ),
        CheckConstraint(
            "etapa_kanban IN ('triagem','aguardando_documentos','documentacao_gru',"
            "'protocolado','aguardando_inpi','exigencia_recurso','deferido_concessao',"
            "'encerrado')",
            name="ck_processo_monitorado_etapa_kanban",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organizacao_id: Mapped[int] = mapped_column(ForeignKey("organizacoes.id", ondelete="CASCADE"), index=True)
    processo_id: Mapped[int] = mapped_column(ForeignKey("processos.id", ondelete="CASCADE"), index=True)
    empresa_id: Mapped[int | None] = mapped_column(
        ForeignKey("empresas_crm.id", ondelete="SET NULL"), nullable=True, index=True
    )
    responsavel_id: Mapped[int | None] = mapped_column(
        ForeignKey("usuarios_operacoes.id", ondelete="SET NULL"), nullable=True, index=True
    )
    lead_id: Mapped[int | None] = mapped_column(
        ForeignKey("leads.id", ondelete="SET NULL"), nullable=True, index=True
    )
    status: Mapped[str] = mapped_column(String(20), default="ativo", index=True)
    prioridade: Mapped[str] = mapped_column(String(10), default="media", server_default="media", index=True)
    etapa_kanban: Mapped[str] = mapped_column(String(30), default="triagem", server_default="triagem", index=True)
    ordem_kanban: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    etapa_atualizada_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    etapa_atualizada_por: Mapped[str | None] = mapped_column(String(254), nullable=True)
    origem: Mapped[str] = mapped_column(String(30), default="manual", index=True)
    procurador_origem: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Correcao do procurador visivel so para esta organizacao -- nunca grava em
    # processos.procurador (compartilhado entre organizacoes, sem organizacao_id).
    # Tem prioridade de exibicao sobre Processo.procurador; ver _procurador_exibicao
    # em app/api/carteira.py.
    procurador_manual: Mapped[str | None] = mapped_column(Text, nullable=True)
    observacoes: Mapped[str | None] = mapped_column(Text, nullable=True)
    vinculado_por: Mapped[str] = mapped_column(String(254))
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    atualizado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    processo: Mapped[Processo] = relationship(lazy="selectin")
    empresa_registro: Mapped[EmpresaCRM | None] = relationship(back_populates="processos_monitorados", lazy="selectin")
    responsavel: Mapped["UsuarioOperacoes | None"] = relationship(lazy="selectin")
    lead: Mapped["Lead | None"] = relationship(lazy="selectin")
    prazos_juridicos: Mapped[list["PrazoJuridico"]] = relationship(
        back_populates="processo_monitorado",
        cascade="all, delete-orphan",
        order_by="PrazoJuridico.vencimento_em",
    )
    historico_etapas: Mapped[list["HistoricoEtapaCarteira"]] = relationship(
        back_populates="processo_monitorado",
        cascade="all, delete-orphan",
        order_by="HistoricoEtapaCarteira.criado_em.desc()",
    )


class HistoricoEtapaCarteira(Base):
    """Transição auditável do processo entre as colunas do Kanban."""

    __tablename__ = "historico_etapas_carteira"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organizacao_id: Mapped[int] = mapped_column(ForeignKey("organizacoes.id", ondelete="CASCADE"), index=True)
    processo_monitorado_id: Mapped[int] = mapped_column(
        ForeignKey("processos_monitorados.id", ondelete="CASCADE"), index=True
    )
    etapa_anterior: Mapped[str | None] = mapped_column(String(30), nullable=True)
    etapa_nova: Mapped[str] = mapped_column(String(30), index=True)
    movido_por: Mapped[str] = mapped_column(String(254))
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    processo_monitorado: Mapped[ProcessoMonitorado] = relationship(back_populates="historico_etapas")


class PreCadastroProcesso(Base):
    """Numero e titular registrados antes da RPI publicar o processo.

    Vinculado automaticamente a um ProcessoMonitorado assim que a sincronizacao
    da RPI cria o Processo correspondente (ver app/rpi/bulk_importer.py).
    """

    __tablename__ = "pre_cadastros_processo"
    __table_args__ = (
        CheckConstraint(
            "status IN ('aguardando','vinculado','cancelado')",
            name="ck_pre_cadastro_processo_status",
        ),
        # Evita duplicar o mesmo pre-cadastro pendente (índice único parcial:
        # só enquanto status='aguardando', não bloqueia reaproveitar o
        # número depois de vinculado/cancelado).
        Index(
            "uq_pre_cadastro_processo_pendente",
            "organizacao_id",
            "numero_normalizado",
            unique=True,
            postgresql_where=text("status = 'aguardando'"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organizacao_id: Mapped[int] = mapped_column(ForeignKey("organizacoes.id", ondelete="CASCADE"), index=True)
    numero: Mapped[str] = mapped_column(String(50))
    numero_normalizado: Mapped[str] = mapped_column(String(50), index=True)
    titular: Mapped[str] = mapped_column(String(300))
    empresa_id: Mapped[int | None] = mapped_column(
        ForeignKey("empresas_crm.id", ondelete="SET NULL"), nullable=True, index=True
    )
    responsavel_id: Mapped[int | None] = mapped_column(
        ForeignKey("usuarios_operacoes.id", ondelete="SET NULL"), nullable=True, index=True
    )
    observacoes: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="aguardando", server_default="aguardando", index=True)
    titular_divergente: Mapped[bool] = mapped_column(Boolean, default=False)
    criado_por: Mapped[str] = mapped_column(String(254))
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    atualizado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    vinculado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    processo_monitorado_id: Mapped[int | None] = mapped_column(
        ForeignKey("processos_monitorados.id", ondelete="SET NULL"), nullable=True
    )

    empresa: Mapped[EmpresaCRM | None] = relationship(lazy="selectin")
    responsavel: Mapped["UsuarioOperacoes | None"] = relationship(lazy="selectin")
    processo_monitorado: Mapped[ProcessoMonitorado | None] = relationship(lazy="selectin")
