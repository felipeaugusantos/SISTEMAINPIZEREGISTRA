from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class PreferenciaVigilancia(Base):
    __tablename__ = "preferencias_vigilancia"
    __table_args__ = (UniqueConstraint("organizacao_id", "cliente_id", name="uq_preferencia_vigilancia_cliente"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organizacao_id: Mapped[int] = mapped_column(ForeignKey("organizacoes.id", ondelete="CASCADE"), index=True)
    cliente_id: Mapped[int] = mapped_column(ForeignKey("clientes_portal.id", ondelete="CASCADE"), index=True)
    frequencia: Mapped[str] = mapped_column(String(20), default="semanal")
    classes_nice: Mapped[list[str]] = mapped_column(JSON, default=list)
    codigos_viena: Mapped[list[str]] = mapped_column(JSON, default=list)
    canais: Mapped[list[str]] = mapped_column(JSON, default=lambda: ["portal"])
    ativo: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    atualizado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ColidenciaVigilancia(Base):
    __tablename__ = "colidencias_vigilancia"
    __table_args__ = (
        UniqueConstraint("organizacao_id", "cliente_id", "processo_id", name="uq_colidencia_vigilancia_processo"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organizacao_id: Mapped[int] = mapped_column(ForeignKey("organizacoes.id", ondelete="CASCADE"), index=True)
    cliente_id: Mapped[int] = mapped_column(ForeignKey("clientes_portal.id", ondelete="CASCADE"), index=True)
    processo_id: Mapped[int] = mapped_column(ForeignKey("processos.id", ondelete="CASCADE"), index=True)
    rpi_numero: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    score_risco: Mapped[float] = mapped_column(Float, default=0)
    status: Mapped[str] = mapped_column(String(20), default="pendente", index=True)
    evidencias: Mapped[dict] = mapped_column(JSON, default=dict)
    justificativa: Mapped[str] = mapped_column(Text)
    revisado_por: Mapped[int | None] = mapped_column(
        ForeignKey("usuarios_operacoes.id", ondelete="SET NULL"), nullable=True
    )
    revisado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    aprovado_por: Mapped[int | None] = mapped_column(
        ForeignKey("usuarios_operacoes.id", ondelete="SET NULL"), nullable=True
    )
    aprovado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    falso_positivo: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    falso_positivo_motivo: Mapped[str | None] = mapped_column(Text, nullable=True)
    comunicada_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    atualizado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class VigilanciaExecucao(Base):
    __tablename__ = "vigilancia_execucoes"
    __table_args__ = (UniqueConstraint("organizacao_id", "chave", name="uq_vigilancia_execucao_chave"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organizacao_id: Mapped[int] = mapped_column(ForeignKey("organizacoes.id", ondelete="CASCADE"), index=True)
    chave: Mapped[str] = mapped_column(String(120), nullable=False)
    frequencia: Mapped[str] = mapped_column(String(20), default="semanal")
    status: Mapped[str] = mapped_column(String(20), default="executando", index=True)
    encontrados: Mapped[int] = mapped_column(Integer, default=0)
    criados: Mapped[int] = mapped_column(Integer, default=0)
    falsos_positivos: Mapped[int] = mapped_column(Integer, default=0)
    resumo: Mapped[dict] = mapped_column(JSON, default=dict)
    iniciado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finalizado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    erro: Mapped[str | None] = mapped_column(Text, nullable=True)


class HistoricoAlertaVigilancia(Base):
    __tablename__ = "historico_alertas_vigilancia"
    __table_args__ = (UniqueConstraint("idempotency_key", name="uq_historico_alerta_vigilancia_key"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organizacao_id: Mapped[int] = mapped_column(ForeignKey("organizacoes.id", ondelete="CASCADE"), index=True)
    colidencia_id: Mapped[int] = mapped_column(ForeignKey("colidencias_vigilancia.id", ondelete="CASCADE"), index=True)
    cliente_id: Mapped[int] = mapped_column(ForeignKey("clientes_portal.id", ondelete="CASCADE"), index=True)
    canal: Mapped[str] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(String(20), default="pendente", index=True)
    idempotency_key: Mapped[str] = mapped_column(String(180), nullable=False)
    justificativa: Mapped[str] = mapped_column(Text)
    detalhes: Mapped[dict] = mapped_column(JSON, default=dict)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    enviado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
