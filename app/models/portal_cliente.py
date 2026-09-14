from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ClientePortal(Base):
    """Identidade externa vinculada a um único lead e sua organização."""

    __tablename__ = "clientes_portal"
    __table_args__ = (UniqueConstraint("organizacao_id", "lead_id", name="uq_cliente_portal_lead"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organizacao_id: Mapped[int] = mapped_column(ForeignKey("organizacoes.id", ondelete="CASCADE"), index=True)
    lead_id: Mapped[int] = mapped_column(ForeignKey("leads.id", ondelete="CASCADE"), index=True)
    processo_id: Mapped[int | None] = mapped_column(
        ForeignKey("processos.id", ondelete="SET NULL"), nullable=True, index=True
    )
    proposta_id: Mapped[int | None] = mapped_column(
        ForeignKey("propostas_comerciais.id", ondelete="SET NULL"), nullable=True, index=True
    )
    lancamento_id: Mapped[int | None] = mapped_column(
        ForeignKey("lancamentos_financeiros.id", ondelete="SET NULL"), nullable=True, index=True
    )
    nome: Mapped[str] = mapped_column(String(150))
    email: Mapped[str] = mapped_column(String(254), index=True)
    senha_hash: Mapped[str] = mapped_column(Text)
    ativo: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    bloqueado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    bloqueado_motivo: Mapped[str | None] = mapped_column(String(300), nullable=True)
    ultimo_login_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    criado_por: Mapped[int | None] = mapped_column(
        ForeignKey("usuarios_operacoes.id", ondelete="SET NULL"), nullable=True
    )
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    atualizado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class SessaoClientePortal(Base):
    __tablename__ = "sessoes_clientes_portal"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    cliente_id: Mapped[int] = mapped_column(ForeignKey("clientes_portal.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expira_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    revogada_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RecuperacaoClientePortal(Base):
    """Token de recuperação de acesso de cliente, de uso único e curto."""

    __tablename__ = "recuperacoes_clientes_portal"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    cliente_id: Mapped[int] = mapped_column(ForeignKey("clientes_portal.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expira_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    usado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ArquivoClientePortal(Base):
    __tablename__ = "arquivos_clientes_portal"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organizacao_id: Mapped[int] = mapped_column(ForeignKey("organizacoes.id", ondelete="CASCADE"), index=True)
    lead_id: Mapped[int] = mapped_column(ForeignKey("leads.id", ondelete="CASCADE"), index=True)
    cliente_id: Mapped[int] = mapped_column(ForeignKey("clientes_portal.id", ondelete="CASCADE"), index=True)
    nome: Mapped[str] = mapped_column(String(255))
    caminho: Mapped[str] = mapped_column(Text)
    content_type: Mapped[str | None] = mapped_column(String(120), nullable=True)
    tamanho: Mapped[int] = mapped_column(BigInteger, default=0)
    arquivo_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class MensagemClientePortal(Base):
    __tablename__ = "mensagens_clientes_portal"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organizacao_id: Mapped[int] = mapped_column(ForeignKey("organizacoes.id", ondelete="CASCADE"), index=True)
    lead_id: Mapped[int] = mapped_column(ForeignKey("leads.id", ondelete="CASCADE"), index=True)
    cliente_id: Mapped[int] = mapped_column(ForeignKey("clientes_portal.id", ondelete="CASCADE"), index=True)
    autor_tipo: Mapped[str] = mapped_column(String(20), default="cliente")
    autor_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    mensagem: Mapped[str] = mapped_column(Text)
    lida_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


class NotificacaoClientePortal(Base):
    __tablename__ = "notificacoes_clientes_portal"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    cliente_id: Mapped[int] = mapped_column(ForeignKey("clientes_portal.id", ondelete="CASCADE"), index=True)
    titulo: Mapped[str] = mapped_column(String(180))
    mensagem: Mapped[str] = mapped_column(Text)
    lida_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
