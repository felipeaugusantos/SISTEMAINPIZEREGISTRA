from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ConsentimentoCanal(Base):
    """Consentimento (ou supressão) de um telefone para um canal e uma finalidade.

    Uma linha por telefone e finalidade. ``status="revogado"`` funciona também como LISTA DE SUPRESSÃO:
    quem pede para sair (ou nunca deve ser contatado) fica registrado mesmo sem ter consentido antes.
    """

    __tablename__ = "consentimentos_canal"
    __table_args__ = (
        UniqueConstraint("organizacao_id", "canal", "finalidade", "telefone", name="uq_consentimento_canal_telefone"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organizacao_id: Mapped[int] = mapped_column(ForeignKey("organizacoes.id", ondelete="CASCADE"), index=True)
    canal: Mapped[str] = mapped_column(String(20), default="whatsapp", index=True)
    # vigilancia | andamento | prospeccao
    finalidade: Mapped[str] = mapped_column(String(20), index=True)
    telefone: Mapped[str] = mapped_column(String(20), index=True)  # só dígitos, com DDI (ex.: 5511999998888)
    # concedido | revogado
    status: Mapped[str] = mapped_column(String(20), default="concedido", index=True)
    # consentimento | legitimo_interesse
    base_legal: Mapped[str] = mapped_column(String(30), default="consentimento")
    titular_tipo: Mapped[str | None] = mapped_column(String(20), nullable=True)  # cliente_portal | lead
    titular_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    origem: Mapped[str | None] = mapped_column(String(60), nullable=True)
    evidencia: Mapped[dict] = mapped_column(JSON, default=dict)
    registrado_por: Mapped[str | None] = mapped_column(String(254), nullable=True)
    concedido_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revogado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    atualizado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class MensagemSaida(Base):
    """Fila de mensagens a enviar por um canal de mensageria (WhatsApp)."""

    __tablename__ = "mensagens_saida"
    __table_args__ = (UniqueConstraint("organizacao_id", "chave_idempotencia", name="uq_mensagem_saida_chave"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organizacao_id: Mapped[int] = mapped_column(ForeignKey("organizacoes.id", ondelete="CASCADE"), index=True)
    canal: Mapped[str] = mapped_column(String(20), default="whatsapp", index=True)
    finalidade: Mapped[str] = mapped_column(String(20), index=True)
    telefone: Mapped[str] = mapped_column(String(20), index=True)
    titular_tipo: Mapped[str | None] = mapped_column(String(20), nullable=True)
    titular_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    corpo: Mapped[str] = mapped_column(Text)
    # pendente | enviando | enviada | incerta | falhou | bloqueada | cancelada
    status: Mapped[str] = mapped_column(String(20), default="pendente", index=True)
    motivo_bloqueio: Mapped[str | None] = mapped_column(String(120), nullable=True)
    tentativas: Mapped[int] = mapped_column(Integer, default=0)
    agendada_para: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    enviada_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    ultimo_erro: Mapped[str | None] = mapped_column(String(300), nullable=True)
    ultima_falha_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    entregue_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lida_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    chave_idempotencia: Mapped[str] = mapped_column(String(120))
    provedor_mensagem_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    atualizado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class PausaCanal(Base):
    """Pausa de envio de uma sessão do WhatsApp (ex.: restrição do número). Vale até ``ate``."""

    __tablename__ = "pausas_canal"
    __table_args__ = (UniqueConstraint("organizacao_id", "sessao", name="uq_pausa_canal_sessao"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organizacao_id: Mapped[int] = mapped_column(ForeignKey("organizacoes.id", ondelete="CASCADE"), index=True)
    sessao: Mapped[str] = mapped_column(String(20))  # vigilancia | prospeccao
    ate: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    motivo: Mapped[str | None] = mapped_column(String(120), nullable=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
