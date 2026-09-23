from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
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
    # Achado P1 do Codex no PR #122 (Fase 13.3 da auditoria fina, 23/09/2026):
    # revogar sessões ativas (marcar revogada_em) ao trocar a senha é uma
    # corrida -- um login concorrente com a senha antiga pode validar antes
    # da troca e só criar/comitar a sessão depois do SELECT de revogação já
    # ter tirado o retrato, escapando da revogação. senha_alterada_em vira
    # um marcador de geração: toda SessaoClientePortal guarda o valor vigente
    # no momento em que validou a senha (ver SessaoClientePortal.
    # senha_versao_no_login); obter_cliente_portal invalida qualquer sessão
    # cujo marcador não bate com o valor atual, então a corrida se resolve
    # sozinha na próxima requisição autenticada, independente de qual
    # transação comitou primeiro.
    senha_alterada_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ativo: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    bloqueado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    bloqueado_motivo: Mapped[str | None] = mapped_column(String(300), nullable=True)
    # Achado alto da auditoria do Portal do Cliente (Fase 9, 21/09/2026): o
    # login só tinha rate-limit por IP (_limitar_login_portal), sem bloqueio
    # da própria conta -- diferente do login administrativo
    # (UsuarioOperacoes.tentativas_falhas/bloqueado_ate). Mesmo padrão aqui.
    tentativas_falhas: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    bloqueado_ate: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
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
    # Achado médio da auditoria do Portal do Cliente (Fase 9, 21/09/2026): o
    # portal não exigia CSRF em nenhuma mutação, diferente do painel
    # administrativo (SessaoOperacoes.csrf_hash + X-CSRF-Token). Nullable
    # porque sessões criadas antes desta migration não têm o par -- tratadas
    # como CSRF inválido em app/api/portal_cliente.py::exigir_csrf_portal
    # (força um novo login, que já gera o par).
    csrf_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Marcador de geração de senha (ver ClientePortal.senha_alterada_em,
    # Fase 13.3/Codex, 23/09/2026) -- guarda o senha_alterada_em vigente no
    # instante em que este login validou a senha. Nullable porque sessões
    # criadas antes desta migration não têm o par (tratadas como válidas
    # enquanto o cliente nunca trocar a senha por um caminho rastreado).
    senha_versao_no_login: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expira_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    revogada_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CodigoConfirmacaoPortal(Base):
    """Segundo fator pra assinar proposta/documento pelo portal do cliente
    (Fase 13.2 da auditoria fina, 23/09/2026, decisão do usuário: mesmo
    padrão de dupla validação por e-mail já usado no aceite público de
    proposta -- ver app.api.leads_propostas). Tabela própria em vez de
    reaproveitar os campos codigo_confirmacao_* de PropostaComercial (que
    são do fluxo público, sem cliente_id) pra não colidir se o mesmo
    cliente usar os dois fluxos quase ao mesmo tempo, e pra também cobrir
    DocumentoLead, que nunca teve esses campos.

    Achados do Codex no PR #120: recurso_hash amarra o código ao
    conteúdo/versão vigente no momento do pedido -- se o recurso mudar
    antes da confirmação (proposta ou documento editado pelo operador
    durante os 15 minutos de validade), o hash não bate mais e o código
    deixa de servir. A leitura em
    app.api.portal_cliente._validar_codigo_confirmacao_portal usa
    SELECT ... FOR UPDATE nesta tabela pra serializar confirmações
    concorrentes do mesmo código (ex.: duplo clique) -- sem isso, duas
    submissões quase simultâneas podiam ambas validar o mesmo código
    antes de qualquer uma apagar o registro."""

    __tablename__ = "codigos_confirmacao_portal"
    __table_args__ = (
        UniqueConstraint("cliente_id", "recurso_tipo", "recurso_id", name="uq_codigo_confirmacao_portal_recurso"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organizacao_id: Mapped[int] = mapped_column(ForeignKey("organizacoes.id", ondelete="CASCADE"), index=True)
    cliente_id: Mapped[int] = mapped_column(ForeignKey("clientes_portal.id", ondelete="CASCADE"), index=True)
    recurso_tipo: Mapped[str] = mapped_column(String(20))
    recurso_id: Mapped[int] = mapped_column(BigInteger)
    recurso_hash: Mapped[str] = mapped_column(String(64))
    codigo_hash: Mapped[str] = mapped_column(String(64))
    expira_em: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    tentativas: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    enviado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


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


class MaterialMarcaCliente(Base):
    """Material de identidade visual (logo, manual de marca, artes) cadastrado
    pela equipe de atendimento e disponível para download no portal do cliente.
    """

    __tablename__ = "materiais_marca_clientes"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organizacao_id: Mapped[int] = mapped_column(ForeignKey("organizacoes.id", ondelete="CASCADE"), index=True)
    lead_id: Mapped[int] = mapped_column(ForeignKey("leads.id", ondelete="CASCADE"), index=True)
    nome: Mapped[str] = mapped_column(String(255))
    descricao: Mapped[str | None] = mapped_column(String(300), nullable=True)
    caminho: Mapped[str] = mapped_column(Text)
    content_type: Mapped[str | None] = mapped_column(String(120), nullable=True)
    tamanho: Mapped[int] = mapped_column(BigInteger, default=0)
    arquivo_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    enviado_por_id: Mapped[int | None] = mapped_column(
        ForeignKey("usuarios_operacoes.id", ondelete="SET NULL"), nullable=True
    )
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
