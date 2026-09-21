from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    Table,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.enums import (
    CanalContato,
    FaseLead,
    StatusLead,
    TipoProcesso,
)

if TYPE_CHECKING:
    # Classes ja extraidas para outros modulos de app/models/, referenciadas
    # aqui so como forward reference em Mapped["..."] (resolvida em tempo de
    # execucao pelo registry do SQLAlchemy, nao por este import -- que existe
    # so para o ruff/checadores de tipo conseguirem resolver o nome).
    from app.models.carteira import ProcessoMonitorado
    from app.models.financeiro import LancamentoFinanceiro
    from app.models.marca_busca import PesquisaMarca


# Ordem oficial do funil — usada para avançar (nunca retroceder) automaticamente.
ORDEM_FASE_LEAD: tuple[str, ...] = tuple(f.value for f in FaseLead)

# Desfecho da oportunidade (ganho ao converter, perdido ao descartar).
RESULTADOS_LEAD: tuple[str, ...] = ("ganho", "perdido")

# Motivos de perda estruturados (quando a oportunidade é descartada).
MOTIVOS_PERDA: dict[str, str] = {
    "preco": "Preço / orçamento",
    "concorrente": "Escolheu concorrente",
    "sem_resposta": "Sem resposta do cliente",
    "fora_perfil": "Fora do perfil / inviável",
    "outro": "Outro",
}


usuario_permissoes = Table(
    "usuario_permissoes",
    Base.metadata,
    Column(
        "usuario_id",
        ForeignKey("usuarios_operacoes.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "permissao_id",
        ForeignKey("permissoes_operacoes.id", ondelete="CASCADE"),
        primary_key=True,
    ),
)


class PermissaoOperacoes(Base):
    __tablename__ = "permissoes_operacoes"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    chave: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    modulo: Mapped[str] = mapped_column(String(50), index=True)
    nome: Mapped[str] = mapped_column(String(120))
    descricao: Mapped[str] = mapped_column(Text)
    ordem: Mapped[int] = mapped_column(Integer, default=0)


class PlanoSaas(Base):
    __tablename__ = "planos_saas"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    nome: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    codigo: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    descricao: Mapped[str | None] = mapped_column(Text, nullable=True)
    modulos: Mapped[list[str]] = mapped_column(JSON, default=list)
    limites: Mapped[dict] = mapped_column(JSON, default=dict)
    ativo: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    atualizado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Organizacao(Base):
    __tablename__ = "organizacoes"
    __mapper_args__ = {"eager_defaults": True}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    nome: Mapped[str] = mapped_column(String(180), index=True)
    slug: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    documento: Mapped[str | None] = mapped_column(String(30), nullable=True, unique=True)
    plano_id: Mapped[int] = mapped_column(ForeignKey("planos_saas.id", ondelete="RESTRICT"), index=True)
    modulos_liberados: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="ativa", index=True)
    email_contato: Mapped[str | None] = mapped_column(String(254), nullable=True)
    telefone_contato: Mapped[str | None] = mapped_column(String(30), nullable=True)
    branding: Mapped[dict] = mapped_column(JSON, default=dict)
    assinatura_status: Mapped[str] = mapped_column(String(30), default="manual", index=True)
    billing_provider: Mapped[str | None] = mapped_column(String(30), nullable=True)
    billing_customer_id: Mapped[str | None] = mapped_column(String(150), nullable=True, index=True)
    trial_ate: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    suspender_automaticamente: Mapped[bool] = mapped_column(Boolean, default=True)
    # Fase 5: organizacao usada para testar novas flags antes de qualquer
    # outra audiencia (estagio de rollout "ambiente_interno").
    ambiente_interno: Mapped[bool] = mapped_column(Boolean, default=False)
    retencao_dados_dias: Mapped[int] = mapped_column(Integer, default=730)
    politica_privacidade_versao: Mapped[str] = mapped_column(String(30), default="1.0")
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    atualizado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    plano: Mapped[PlanoSaas] = relationship(lazy="selectin")


class PoliticaPrivacidade(Base):
    """Documento versionado; versões publicadas são imutáveis no banco."""

    __tablename__ = "politicas_privacidade"
    __table_args__ = (
        UniqueConstraint("organizacao_id", "versao", name="uq_politica_privacidade_org_versao"),
        # Regra de negócio: só pode existir UMA política "publicada" por
        # organização por vez (índice único parcial, não expressável como
        # UniqueConstraint comum).
        Index(
            "uq_politica_privacidade_publicada_org",
            "organizacao_id",
            unique=True,
            postgresql_where=text("status = 'publicada'"),
        ),
        CheckConstraint(
            "status IN ('rascunho', 'publicada', 'revogada')",
            name="ck_politica_privacidade_status",
        ),
        CheckConstraint(
            "num_nonnulls(conteudo, documento_referencia) = 1",
            name="ck_politica_privacidade_documento",
        ),
        CheckConstraint(
            "status = 'rascunho' OR "
            "(publicado_em IS NOT NULL AND vigencia_em IS NOT NULL AND aprovado_por IS NOT NULL)",
            name="ck_politica_privacidade_publicacao",
        ),
        CheckConstraint("length(sha256) = 64", name="ck_politica_privacidade_sha256"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organizacao_id: Mapped[int] = mapped_column(
        ForeignKey("organizacoes.id", ondelete="CASCADE"), index=True
    )
    versao: Mapped[str] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(String(20), default="rascunho", index=True)
    conteudo: Mapped[str | None] = mapped_column(Text, nullable=True)
    documento_referencia: Mapped[str | None] = mapped_column(String(500), nullable=True)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    publicado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    vigencia_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    criado_por_id: Mapped[int | None] = mapped_column(
        ForeignKey("usuarios_operacoes.id", ondelete="SET NULL"), nullable=True, index=True
    )
    criado_por: Mapped[str] = mapped_column(String(254))
    aprovado_por_id: Mapped[int | None] = mapped_column(
        ForeignKey("usuarios_operacoes.id", ondelete="SET NULL"), nullable=True, index=True
    )
    aprovado_por: Mapped[str | None] = mapped_column(String(254), nullable=True)
    motivo_alteracao: Mapped[str] = mapped_column(Text)
    requer_novo_consentimento: Mapped[bool] = mapped_column(Boolean, default=False)


class PoliticaRetencao(Base):
    """Versão auditável da política de retenção de uma categoria do tenant.

    A configuração histórica ``Organizacao.retencao_dados_dias`` permanece
    como fallback. Novas alterações são append-only nesta tabela e só passam
    a valer a partir de ``vigencia_em``.
    """

    __tablename__ = "politicas_retencao"
    __table_args__ = (
        CheckConstraint("categoria IN ('lead')", name="ck_politica_retencao_categoria"),
        CheckConstraint("prazo_dias BETWEEN 30 AND 3650", name="ck_politica_retencao_prazo"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organizacao_id: Mapped[int] = mapped_column(ForeignKey("organizacoes.id", ondelete="CASCADE"), index=True)
    categoria: Mapped[str] = mapped_column(String(30), index=True)
    prazo_dias: Mapped[int] = mapped_column(Integer)
    marco_inicial: Mapped[str] = mapped_column(String(80))
    finalidade: Mapped[str] = mapped_column(Text)
    base_legal: Mapped[str] = mapped_column(Text)
    vigencia_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    justificativa: Mapped[str] = mapped_column(Text)
    excecoes: Mapped[list[str]] = mapped_column(JSON, default=list)
    ativo: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    reducao: Mapped[bool] = mapped_column(Boolean, default=False)
    reducao_confirmada: Mapped[bool] = mapped_column(Boolean, default=False)
    criado_por_id: Mapped[int | None] = mapped_column(
        ForeignKey("usuarios_operacoes.id", ondelete="SET NULL"), nullable=True, index=True
    )
    criado_por: Mapped[str] = mapped_column(String(254))
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


class SimulacaoRetencao(Base):
    """Evidência de impacto calculada antes de alterar uma política."""

    __tablename__ = "simulacoes_retencao"
    __table_args__ = (
        CheckConstraint("categoria IN ('lead')", name="ck_simulacao_retencao_categoria"),
        CheckConstraint("prazo_dias BETWEEN 30 AND 3650", name="ck_simulacao_retencao_prazo"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organizacao_id: Mapped[int] = mapped_column(ForeignKey("organizacoes.id", ondelete="CASCADE"), index=True)
    categoria: Mapped[str] = mapped_column(String(30), index=True)
    prazo_dias: Mapped[int] = mapped_column(Integer)
    resultado: Mapped[dict] = mapped_column(JSON)
    criado_por_id: Mapped[int | None] = mapped_column(
        ForeignKey("usuarios_operacoes.id", ondelete="SET NULL"), nullable=True, index=True
    )
    criado_por: Mapped[str] = mapped_column(String(254))
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    usada_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)


class BloqueioRetencao(Base):
    """Legal hold ou bloqueio operacional aplicado a um recurso do tenant."""

    __tablename__ = "bloqueios_retencao"
    __table_args__ = (
        UniqueConstraint(
            "organizacao_id", "categoria", "recurso_id", name="uq_bloqueio_retencao_recurso"
        ),
        CheckConstraint("categoria IN ('lead')", name="ck_bloqueio_retencao_categoria"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organizacao_id: Mapped[int] = mapped_column(ForeignKey("organizacoes.id", ondelete="CASCADE"), index=True)
    categoria: Mapped[str] = mapped_column(String(30), index=True)
    recurso_id: Mapped[int] = mapped_column(BigInteger, index=True)
    motivo: Mapped[str] = mapped_column(Text)
    ativo: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    criado_por_id: Mapped[int | None] = mapped_column(
        ForeignKey("usuarios_operacoes.id", ondelete="SET NULL"), nullable=True
    )
    criado_por: Mapped[str] = mapped_column(String(254))
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    liberado_por_id: Mapped[int | None] = mapped_column(
        ForeignKey("usuarios_operacoes.id", ondelete="SET NULL"), nullable=True
    )
    liberado_por: Mapped[str | None] = mapped_column(String(254), nullable=True)
    liberado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)


class DominioOrganizacao(Base):
    __tablename__ = "dominios_organizacao"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organizacao_id: Mapped[int] = mapped_column(ForeignKey("organizacoes.id", ondelete="CASCADE"), index=True)
    dominio: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    ativo: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    verificado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    codigo_verificacao: Mapped[str | None] = mapped_column(String(100), nullable=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CredencialIntegracao(Base):
    __tablename__ = "credenciais_integracao"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organizacao_id: Mapped[int] = mapped_column(ForeignKey("organizacoes.id", ondelete="CASCADE"), index=True)
    nome: Mapped[str] = mapped_column(String(120))
    token_prefixo: Mapped[str] = mapped_column(String(16), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    ativo: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    ultimo_uso_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expira_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    criado_por: Mapped[str] = mapped_column(String(150))
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ConviteOrganizacao(Base):
    __tablename__ = "convites_organizacao"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organizacao_id: Mapped[int] = mapped_column(ForeignKey("organizacoes.id", ondelete="CASCADE"), index=True)
    email: Mapped[str] = mapped_column(String(254), index=True)
    perfil: Mapped[str] = mapped_column(String(30), default="operador")
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    permissoes: Mapped[list[str]] = mapped_column(JSON, default=list)
    expira_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    aceito_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    criado_por: Mapped[str] = mapped_column(String(150))
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class UsuarioOperacoes(Base):
    __tablename__ = "usuarios_operacoes"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organizacao_id: Mapped[int] = mapped_column(ForeignKey("organizacoes.id", ondelete="RESTRICT"), index=True)
    nome: Mapped[str] = mapped_column(String(150), index=True)
    usuario: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    email: Mapped[str] = mapped_column(String(254), unique=True, index=True)
    cargo: Mapped[str | None] = mapped_column(String(150), nullable=True)
    departamento: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    perfil: Mapped[str] = mapped_column(String(30), default="operador", index=True)
    senha_hash: Mapped[str] = mapped_column(Text)
    ativo: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    superadmin: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    mfa_ativo: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    mfa_segredo: Mapped[str | None] = mapped_column(Text, nullable=True)
    mfa_segredo_versao: Mapped[int | None] = mapped_column(Integer, nullable=True)
    codigos_recuperacao: Mapped[list[str]] = mapped_column(JSON, default=list)
    alterar_senha: Mapped[bool] = mapped_column(Boolean, default=True)
    tentativas_falhas: Mapped[int] = mapped_column(Integer, default=0)
    bloqueado_ate: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    ultimo_login_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    criado_por: Mapped[str | None] = mapped_column(String(150), nullable=True)
    # Achado FASE7-7 da auditoria (04/09/2026): taxa de comissão do operador
    # sobre receita de leads sob sua responsabilidade -- nulo = sem
    # comissionamento (padrão atual, retrocompatível). Ver
    # app/api/financeiro.py::_gerar_comissao_se_aplicavel.
    percentual_comissao: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    atualizado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    permissoes: Mapped[list[PermissaoOperacoes]] = relationship(
        secondary=usuario_permissoes,
        lazy="selectin",
    )
    sessoes: Mapped[list["SessaoOperacoes"]] = relationship(
        back_populates="usuario",
        cascade="all, delete-orphan",
    )
    organizacao: Mapped[Organizacao] = relationship(lazy="selectin")


class SessaoOperacoes(Base):
    __tablename__ = "sessoes_operacoes"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    usuario_id: Mapped[int] = mapped_column(ForeignKey("usuarios_operacoes.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    csrf_hash: Mapped[str] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(500), nullable=True)
    ip_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    ultimo_acesso_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    expira_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    revogada_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    motivo_revogacao: Mapped[str | None] = mapped_column(String(150), nullable=True)
    usuario: Mapped[UsuarioOperacoes] = relationship(back_populates="sessoes")


class TokenRecuperacaoSenha(Base):
    __tablename__ = "tokens_recuperacao_senha"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    usuario_id: Mapped[int] = mapped_column(ForeignKey("usuarios_operacoes.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expira_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    usado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class IdentidadeExterna(Base):
    __tablename__ = "identidades_externas"
    __table_args__ = (
        UniqueConstraint("provedor", "provedor_usuario_id", name="uq_identidade_provedor_subject"),
        UniqueConstraint("usuario_id", "provedor", name="uq_identidade_usuario_provedor"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organizacao_id: Mapped[int] = mapped_column(ForeignKey("organizacoes.id", ondelete="CASCADE"), index=True)
    usuario_id: Mapped[int] = mapped_column(ForeignKey("usuarios_operacoes.id", ondelete="CASCADE"), index=True)
    provedor: Mapped[str] = mapped_column(String(20), index=True)
    provedor_usuario_id: Mapped[str] = mapped_column(String(255))
    email_recebido: Mapped[str | None] = mapped_column(String(254), nullable=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    ultimo_login_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class TentativaOAuth(Base):
    __tablename__ = "tentativas_oauth"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    provedor: Mapped[str] = mapped_column(String(20), index=True)
    state_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    browser_token_hash: Mapped[str] = mapped_column(String(64))
    nonce: Mapped[str] = mapped_column(String(128))
    code_verifier: Mapped[str] = mapped_column(String(180))
    modo: Mapped[str] = mapped_column(String(20), default="login")
    usuario_id: Mapped[int | None] = mapped_column(
        ForeignKey("usuarios_operacoes.id", ondelete="CASCADE"), nullable=True, index=True
    )
    destino: Mapped[str] = mapped_column(String(300), default="/admin")
    mfa_token_hash: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True, index=True)
    expira_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    usado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EventoCobrancaSandbox(Base):
    __tablename__ = "eventos_cobranca_sandbox"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organizacao_id: Mapped[int] = mapped_column(ForeignKey("organizacoes.id", ondelete="CASCADE"), index=True)
    tipo: Mapped[str] = mapped_column(String(40), index=True)
    status: Mapped[str] = mapped_column(String(30), index=True)
    referencia: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    detalhes: Mapped[dict] = mapped_column(JSON, default=dict)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AlertaSistema(Base):
    __tablename__ = "alertas_sistema"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organizacao_id: Mapped[int | None] = mapped_column(
        ForeignKey("organizacoes.id", ondelete="CASCADE"), nullable=True, index=True
    )
    severidade: Mapped[str] = mapped_column(String(20), index=True)
    codigo: Mapped[str] = mapped_column(String(60), index=True)
    mensagem: Mapped[str] = mapped_column(Text)
    detalhes: Mapped[dict] = mapped_column(JSON, default=dict)
    resolvido_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AvisoVersao(Base):
    """Aviso de nova versão/atualização exibido no painel (Fase 3 --
    notificações e confirmação de leitura). organizacao_id nulo = aviso de
    plataforma, visível a todas as organizações, mesmo padrão de
    AlertaSistema acima."""

    __tablename__ = "avisos_versao"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organizacao_id: Mapped[int | None] = mapped_column(
        ForeignKey("organizacoes.id", ondelete="CASCADE"), nullable=True, index=True
    )
    versao: Mapped[str] = mapped_column(String(30))
    titulo: Mapped[str] = mapped_column(String(200))
    mensagem: Mapped[str] = mapped_column(Text)
    severidade: Mapped[str] = mapped_column(String(20), index=True)
    critico: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", index=True)
    ativo: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true", index=True)
    criado_por_id: Mapped[int | None] = mapped_column(
        ForeignKey("usuarios_operacoes.id", ondelete="SET NULL"), nullable=True
    )
    publicado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


class AvisoVersaoConfirmacao(Base):
    """Confirmação individual de leitura de um AvisoVersao -- evidência de
    quem confirmou, quando, a partir de qual organização (RLS) e IP
    (hasheado), mesmo padrão de AssinaturaPropostaComercial."""

    __tablename__ = "avisos_versao_confirmacoes"
    __table_args__ = (UniqueConstraint("aviso_id", "usuario_id", name="uq_aviso_versao_confirmacao"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    aviso_id: Mapped[int] = mapped_column(ForeignKey("avisos_versao.id", ondelete="CASCADE"), index=True)
    organizacao_id: Mapped[int] = mapped_column(ForeignKey("organizacoes.id", ondelete="CASCADE"), index=True)
    usuario_id: Mapped[int] = mapped_column(ForeignKey("usuarios_operacoes.id", ondelete="CASCADE"), index=True)
    ip_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    confirmado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


class SolicitacaoPrivacidade(Base):
    __tablename__ = "solicitacoes_privacidade"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organizacao_id: Mapped[int] = mapped_column(ForeignKey("organizacoes.id", ondelete="CASCADE"), index=True)
    lead_id: Mapped[int | None] = mapped_column(ForeignKey("leads.id", ondelete="SET NULL"), nullable=True, index=True)
    tipo: Mapped[str] = mapped_column(String(30), index=True)
    status: Mapped[str] = mapped_column(String(30), default="aberta", index=True)
    solicitado_por: Mapped[str] = mapped_column(String(254))
    concluido_por: Mapped[str | None] = mapped_column(String(150), nullable=True)
    detalhes: Mapped[dict] = mapped_column(JSON, default=dict)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    concluido_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class EmpresaCRM(Base):
    __tablename__ = "empresas_crm"
    __table_args__ = (UniqueConstraint("organizacao_id", "nome_normalizado", name="uq_empresa_crm_org_nome"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organizacao_id: Mapped[int] = mapped_column(ForeignKey("organizacoes.id", ondelete="CASCADE"), index=True)
    nome: Mapped[str] = mapped_column(String(200), index=True)
    nome_normalizado: Mapped[str] = mapped_column(String(200), index=True)
    documento: Mapped[str | None] = mapped_column(String(18), nullable=True, index=True)
    segmento: Mapped[str | None] = mapped_column(String(80), nullable=True)
    telefone: Mapped[str | None] = mapped_column(String(30), nullable=True)
    email: Mapped[str | None] = mapped_column(String(254), nullable=True)
    site: Mapped[str | None] = mapped_column(String(200), nullable=True)
    observacoes: Mapped[str | None] = mapped_column(Text, nullable=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    atualizado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    # Achado CRM-10 da auditoria (04/09/2026): cascade="all, delete-orphan"
    # apagava em cascata todos os contatos e o historico da empresa quando
    # a empresa era removida. Sem cascade de exclusao: contatos sobrevivem
    # (empresa_id vira NULL, ver FK em Contato) e o dono do dado decide o
    # que fazer com eles.
    contatos_pessoa: Mapped[list["Contato"]] = relationship(back_populates="empresa")
    leads: Mapped[list["Lead"]] = relationship(back_populates="empresa_registro")
    pesquisas: Mapped[list["PesquisaMarca"]] = relationship(back_populates="empresa_registro")
    contatos: Mapped[list["ContatoLead"]] = relationship(back_populates="empresa_registro")
    processos_monitorados: Mapped[list["ProcessoMonitorado"]] = relationship(back_populates="empresa_registro")
    lancamentos_financeiros: Mapped[list["LancamentoFinanceiro"]] = relationship(back_populates="empresa_registro")


class Contato(Base):
    """Pessoa de contato, opcionalmente vinculada a uma empresa (separada da
    oportunidade/lead).

    Achados CRM-9/CRM-10 da auditoria (04/09/2026): ``empresa_id`` era
    obrigatório (impossível cadastrar contato de cliente pessoa física, sem
    empresa) e a FK usava ``ondelete="CASCADE"`` (apagar a empresa apagava o
    contato e seu histórico junto). Agora ``empresa_id`` é opcional e a
    remoção da empresa só desvincula o contato (``SET NULL``), nunca o apaga.
    """

    __tablename__ = "contatos"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organizacao_id: Mapped[int] = mapped_column(ForeignKey("organizacoes.id", ondelete="CASCADE"), index=True)
    empresa_id: Mapped[int | None] = mapped_column(
        ForeignKey("empresas_crm.id", ondelete="SET NULL"), nullable=True, index=True
    )
    nome: Mapped[str] = mapped_column(String(150), index=True)
    email: Mapped[str | None] = mapped_column(String(254), nullable=True, index=True)
    telefone: Mapped[str | None] = mapped_column(String(30), nullable=True)
    cargo: Mapped[str | None] = mapped_column(String(80), nullable=True)
    principal: Mapped[bool] = mapped_column(Boolean, default=False)
    observacoes: Mapped[str | None] = mapped_column(Text, nullable=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    atualizado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    empresa: Mapped["EmpresaCRM | None"] = relationship(back_populates="contatos_pessoa")


class RegraAutomacao(Base):
    """Override por organização de uma regra de automação embutida (ativo/dias)."""

    __tablename__ = "regras_automacao"
    __table_args__ = (UniqueConstraint("organizacao_id", "chave", name="uq_regra_automacao_org_chave"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organizacao_id: Mapped[int] = mapped_column(ForeignKey("organizacoes.id", ondelete="CASCADE"), index=True)
    chave: Mapped[str] = mapped_column(String(40), index=True)
    ativo: Mapped[bool] = mapped_column(Boolean, default=True)
    dias: Mapped[int] = mapped_column(Integer, default=0)
    atualizado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class PoliticaCRM(Base):
    """Regras operacionais do funil configuradas por organização."""

    __tablename__ = "politicas_crm"
    __table_args__ = (
        UniqueConstraint("organizacao_id", name="uq_politica_crm_organizacao"),
        CheckConstraint(
            "dias_proxima_acao_padrao IS NULL OR (dias_proxima_acao_padrao >= 0 AND dias_proxima_acao_padrao <= 365)",
            name="ck_politica_crm_dias_proxima_acao",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organizacao_id: Mapped[int] = mapped_column(ForeignKey("organizacoes.id", ondelete="CASCADE"), index=True)
    exigir_responsavel: Mapped[bool] = mapped_column(Boolean, default=True)
    atribuir_ao_operador: Mapped[bool] = mapped_column(Boolean, default=False)
    exigir_proxima_acao: Mapped[bool] = mapped_column(Boolean, default=True)
    dias_proxima_acao_padrao: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Achado item 12 da auditoria completa do CRM (06/09/2026): round-robin
    # entre operadores de perfil "comercial" ativos, só quando o lead nasce
    # sem responsável e sem nenhum operador logado no momento (form público,
    # importação) -- atribuir_ao_operador acima cobre o caso de quem está
    # logado agendando/cadastrando manualmente.
    distribuicao_automatica_ativa: Mapped[bool] = mapped_column(Boolean, default=False)
    ultimo_responsavel_distribuido_id: Mapped[int | None] = mapped_column(
        ForeignKey("usuarios_operacoes.id", ondelete="SET NULL"), nullable=True
    )
    # Achado item 14 da auditoria completa do CRM (06/09/2026): nulo (padrão)
    # mantém o comportamento atual -- só a média histórica agregada no
    # dashboard, sem alerta por lead. Quando definido, o worker
    # (crm.sla_primeiro_atendimento) cria um LembreteCRM individual para
    # todo lead sem nenhum ContatoLead registrado além desse prazo.
    horas_sla_primeiro_atendimento: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Opt-in por organização para a IA em sombra (resumo + sugestão de
    # próxima ação, nunca envio automático) -- além do kill-switch global
    # settings.ia_sombra_enabled. Ver app/ia_sombra.py.
    ia_sombra_ativa: Mapped[bool] = mapped_column(Boolean, default=False)
    atualizado_por: Mapped[str | None] = mapped_column(String(254), nullable=True)
    atualizado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class SugestaoIALead(Base):
    """Sugestão gerada pela IA em sombra para um lead: resumo do histórico
    de atendimento + sugestão de próxima ação. Nunca contém rascunho de
    mensagem para o cliente e nunca é enviada automaticamente -- sempre
    exige revisão humana explícita (status pendente/aprovada/descartada)."""

    __tablename__ = "sugestoes_ia_lead"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organizacao_id: Mapped[int] = mapped_column(ForeignKey("organizacoes.id", ondelete="CASCADE"), index=True)
    lead_id: Mapped[int] = mapped_column(ForeignKey("leads.id", ondelete="CASCADE"), index=True)
    gerado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    modelo: Mapped[str] = mapped_column(String(120))
    resumo: Mapped[str] = mapped_column(Text, default="")
    sugestao_proxima_acao: Mapped[str] = mapped_column(Text, default="")
    # Timestamp do evento de atividade mais recente do lead considerado ao
    # gerar esta sugestão -- usado para decidir se há atividade nova o
    # suficiente para gerar de novo (evita rodar o modelo sem necessidade).
    baseado_em_evento_em: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), default="pendente", index=True)
    revisado_por: Mapped[str | None] = mapped_column(String(254), nullable=True)
    revisado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    erro: Mapped[str | None] = mapped_column(Text, nullable=True)


DIMENSOES_EMBEDDING_LEAD = 768  # nomic-embed-text (settings.ia_sombra_embedding_modelo)


class EmbeddingLead(Base):
    """RAG local (pgvector) da IA em sombra: embedding de um lead com
    resultado conhecido (ganho/perdido), usado como precedente para
    enriquecer a sugestão de próxima ação de outros leads (ver
    app.ia_sombra.buscar_leads_similares). Um único registro por lead
    (upsert quando o resultado muda) -- lead sem resultado ainda não tem o
    que ensinar, não é indexado."""

    __tablename__ = "embeddings_lead"
    __table_args__ = (
        # Busca por similaridade (app.ia_sombra.buscar_leads_similares) --
        # ivfflat/cosine, mesmo indice criado em nh30d4k1w842_rag_embeddings_lead.
        Index(
            "ix_embeddings_lead_embedding_cosine",
            "embedding",
            postgresql_using="ivfflat",
            postgresql_ops={"embedding": "vector_cosine_ops"},
            postgresql_with={"lists": 100},
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organizacao_id: Mapped[int] = mapped_column(ForeignKey("organizacoes.id", ondelete="CASCADE"), index=True)
    lead_id: Mapped[int] = mapped_column(ForeignKey("leads.id", ondelete="CASCADE"), index=True, unique=True)
    resumo_indexado: Mapped[str] = mapped_column(Text)
    resultado: Mapped[str] = mapped_column(String(12))
    modelo: Mapped[str] = mapped_column(String(120))
    embedding: Mapped[list[float]] = mapped_column(Vector(DIMENSOES_EMBEDDING_LEAD))
    gerado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ExplicacaoAnaliseMarca(Base):
    """Explicação em linguagem simples do risco já calculado pelo motor
    determinístico (AvaliacaoRiscoMarca) -- extensão da IA em sombra para a
    análise de marca. NUNCA recalcula nem substitui o resultado técnico, só
    traduz pontuacao/nivel/principais_conflitos já persistidos. Sempre
    exige revisão humana explícita antes de qualquer uso além de apoio
    interno ao analista."""

    __tablename__ = "explicacoes_analise_marca"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organizacao_id: Mapped[int] = mapped_column(ForeignKey("organizacoes.id", ondelete="CASCADE"), index=True)
    pesquisa_id: Mapped[str] = mapped_column(ForeignKey("pesquisas_marca.id", ondelete="CASCADE"), index=True)
    avaliacao_risco_id: Mapped[int] = mapped_column(
        ForeignKey("avaliacoes_risco_marca.id", ondelete="CASCADE"), index=True
    )
    gerado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    modelo: Mapped[str] = mapped_column(String(120))
    explicacao: Mapped[str] = mapped_column(Text, default="")
    # Snapshot do calculado_em da avaliação no momento da geração -- usado
    # para decidir se a avaliação mudou desde então (evita gerar de novo
    # sem necessidade). Mesma lógica de SugestaoIALead.baseado_em_evento_em.
    baseado_em_calculado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), default="pendente", index=True)
    revisado_por: Mapped[str | None] = mapped_column(String(254), nullable=True)
    revisado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    erro: Mapped[str | None] = mapped_column(Text, nullable=True)


class QualificacaoIALead(Base):
    """Qualificação gerada pela IA em sombra no momento da captação de um
    lead (formulário público ou conversão do Radar de Prospecção):
    prioridade sugerida + observação de fit, a partir só dos dados
    disponíveis na chegada (sem histórico de contato, que ainda não
    existe). Gerada uma única vez por lead -- puramente informativo para o
    comercial priorizar, nunca decide sozinha."""

    __tablename__ = "qualificacoes_ia_lead"
    __table_args__ = (UniqueConstraint("lead_id", name="uq_qualificacao_ia_lead"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organizacao_id: Mapped[int] = mapped_column(ForeignKey("organizacoes.id", ondelete="CASCADE"), index=True)
    lead_id: Mapped[int] = mapped_column(ForeignKey("leads.id", ondelete="CASCADE"), index=True)
    gerado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    modelo: Mapped[str] = mapped_column(String(120))
    prioridade: Mapped[str] = mapped_column(String(10), default="media", index=True)
    observacao: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="pendente", index=True)
    revisado_por: Mapped[str | None] = mapped_column(String(254), nullable=True)
    revisado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    erro: Mapped[str | None] = mapped_column(Text, nullable=True)


class MetaComercial(Base):
    """Meta mensal de um operador (item 50 da auditoria completa do CRM,
    06/09/2026): quantidade de leads ganhos e valor faturado. Não existe
    meta "de equipe" como registro separado -- a meta de equipe é a soma
    das metas individuais do período, calculada na consulta (evita duas
    fontes de verdade divergentes, como já documentado para fase/status
    do lead)."""

    __tablename__ = "metas_comerciais"
    __table_args__ = (
        UniqueConstraint("organizacao_id", "operador_id", "periodo", name="uq_meta_comercial_operador_periodo"),
        CheckConstraint("meta_leads_ganhos >= 0", name="ck_meta_comercial_leads_ganhos"),
        CheckConstraint("meta_valor_faturado >= 0", name="ck_meta_comercial_valor_faturado"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organizacao_id: Mapped[int] = mapped_column(ForeignKey("organizacoes.id", ondelete="CASCADE"), index=True)
    operador_id: Mapped[int] = mapped_column(
        ForeignKey("usuarios_operacoes.id", ondelete="CASCADE"), index=True
    )
    # Formato "AAAA-MM" (ex.: "2026-09") -- período mensal, mesma granularidade
    # já usada no resto do sistema para relatórios financeiros (competência).
    periodo: Mapped[str] = mapped_column(String(7), index=True)
    meta_leads_ganhos: Mapped[int] = mapped_column(Integer, default=0)
    meta_valor_faturado: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    definida_por: Mapped[str | None] = mapped_column(String(254), nullable=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    atualizado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Cadencia(Base):
    """Cadência de atendimento: sequência de passos aplicável a uma oportunidade."""

    __tablename__ = "cadencias"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organizacao_id: Mapped[int] = mapped_column(ForeignKey("organizacoes.id", ondelete="CASCADE"), index=True)
    nome: Mapped[str] = mapped_column(String(120), index=True)
    descricao: Mapped[str | None] = mapped_column(Text, nullable=True)
    ativo: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    # Gatilho automático (achado P2 da auditoria de Leads, 03/09/2026): antes só
    # dava pra aplicar uma cadência manualmente, lead por lead. Quando definido,
    # a cadência é aplicada sozinha assim que um lead atinge esse evento/valor
    # (mesmo vocabulário de REGRAS_AUTOMACAO: evento "status" ou "fase").
    # NULL = continua exigindo aplicação manual (comportamento de sempre).
    gatilho_evento: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)
    gatilho_valor: Mapped[str | None] = mapped_column(String(30), nullable=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    atualizado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    passos: Mapped[list["CadenciaPasso"]] = relationship(
        back_populates="cadencia",
        cascade="all, delete-orphan",
        order_by="CadenciaPasso.ordem",
    )


class CadenciaPasso(Base):
    __tablename__ = "cadencia_passos"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organizacao_id: Mapped[int] = mapped_column(ForeignKey("organizacoes.id", ondelete="CASCADE"), index=True)
    cadencia_id: Mapped[int] = mapped_column(ForeignKey("cadencias.id", ondelete="CASCADE"), index=True)
    ordem: Mapped[int] = mapped_column(Integer, default=0)
    dia: Mapped[int] = mapped_column(Integer, default=0)
    canal: Mapped[str] = mapped_column(String(20), default="outro")
    titulo: Mapped[str] = mapped_column(String(180))
    descricao: Mapped[str | None] = mapped_column(Text, nullable=True)
    cadencia: Mapped["Cadencia"] = relationship(back_populates="passos")


class EnvioCadenciaEmail(Base):
    """Execução real (não só o lembrete interno) de um passo de cadência com
    canal e-mail. Fase 9 do plano Leads/CRM (achados L5/L6).

    "enviado" é o máximo que SMTP puro garante -- sem webhook de provedor não
    há confirmação real de entrega na caixa do destinatário. aberto_em é
    best-effort (pixel de rastreio). respondido_em só é preenchido se o
    polling IMAP estiver habilitado (settings.imap_enabled)."""

    __tablename__ = "envios_cadencia_email"
    __table_args__ = (
        UniqueConstraint("organizacao_id", "lead_id", "cadencia_id", "passo_id", name="uq_envio_cadencia_passo"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organizacao_id: Mapped[int] = mapped_column(ForeignKey("organizacoes.id", ondelete="CASCADE"), index=True)
    lead_id: Mapped[int] = mapped_column(ForeignKey("leads.id", ondelete="CASCADE"), index=True)
    cadencia_id: Mapped[int] = mapped_column(ForeignKey("cadencias.id", ondelete="CASCADE"), index=True)
    passo_id: Mapped[int] = mapped_column(ForeignKey("cadencia_passos.id", ondelete="CASCADE"), index=True)
    agendado_para: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    status: Mapped[str] = mapped_column(String(20), default="pendente", index=True)
    tentativas: Mapped[int] = mapped_column(Integer, default=0)
    # Só existe a partir do envio de fato -- gerar no agendamento seria inútil
    # (o token bruto não pode ser reconstruído a partir do hash quando o e-mail
    # for realmente montado, dias depois).
    rastreio_token_hash: Mapped[str | None] = mapped_column(String(64), unique=True, index=True, nullable=True)
    enviado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    aberto_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    respondido_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    pausado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ultimo_erro: Mapped[str | None] = mapped_column(String(300), nullable=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    passo: Mapped["CadenciaPasso"] = relationship()


class Lead(Base):
    __tablename__ = "leads"
    __table_args__ = (
        # LGPD: garante que a versão de termo que o lead consentiu existe
        # de verdade como política publicada daquela organização -- nunca
        # um número de versão inventado ou de outra organização.
        ForeignKeyConstraint(
            ["organizacao_id", "consentimento_versao_termo"],
            ["politicas_privacidade.organizacao_id", "politicas_privacidade.versao"],
            name="fk_leads_consentimento_politica",
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organizacao_id: Mapped[int] = mapped_column(ForeignKey("organizacoes.id", ondelete="RESTRICT"), index=True)
    empresa_id: Mapped[int | None] = mapped_column(
        ForeignKey("empresas_crm.id", ondelete="SET NULL"), nullable=True, index=True
    )
    contato_id: Mapped[int | None] = mapped_column(
        ForeignKey("contatos.id", ondelete="SET NULL"), nullable=True, index=True
    )
    nome: Mapped[str] = mapped_column(String(150), index=True)
    email: Mapped[str] = mapped_column(String(254), index=True)
    telefone: Mapped[str] = mapped_column(String(30), index=True)
    documento: Mapped[str | None] = mapped_column(String(30), nullable=True, index=True)
    empresa: Mapped[str | None] = mapped_column(String(200), nullable=True, index=True)
    marca: Mapped[str] = mapped_column(Text, index=True)
    atividade: Mapped[str | None] = mapped_column(Text, nullable=True)
    processo_numero: Mapped[str | None] = mapped_column(String(50), nullable=True, index=True)
    origem: Mapped[str] = mapped_column(String(30), default="resultados", index=True)
    # Primeira origem (achado L2 do plano Leads/CRM): capturada uma única vez na
    # criação do lead/oportunidade e nunca sobrescrita -- atribuição de marketing
    # de first-touch. utm_*_ultimo espelha o último touch (atualizado a cada
    # reenvio do mesmo lead com uma UTM nova).
    utm_source: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    utm_medium: Mapped[str | None] = mapped_column(String(100), nullable=True)
    utm_campaign: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    utm_source_ultimo: Mapped[str | None] = mapped_column(String(100), nullable=True)
    utm_medium_ultimo: Mapped[str | None] = mapped_column(String(100), nullable=True)
    utm_campaign_ultimo: Mapped[str | None] = mapped_column(String(100), nullable=True)
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
            length=20,
            values_callable=lambda members: [member.value for member in members],
        ),
        default=StatusLead.NOVO,
        index=True,
    )
    fase: Mapped[str] = mapped_column(String(30), default=FaseLead.CONTATO_INICIAL.value, index=True)
    aceite_privacidade: Mapped[bool] = mapped_column(default=True)
    aceite_marketing: Mapped[bool] = mapped_column(Boolean, default=False)
    consentimento_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    consentimento_versao_termo: Mapped[str | None] = mapped_column(String(20), nullable=True)
    consentimento_base_legal: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    consentimento_registrado_por: Mapped[int | None] = mapped_column(
        ForeignKey("usuarios_operacoes.id", ondelete="SET NULL"), nullable=True, index=True
    )
    anonimizado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    # Achado da auditoria completa do CRM (06/09/2026): descadastro
    # específico de cadência comercial (link no rodapé do e-mail) --
    # diferente de anonimizado_em/arquivado_em, não apaga nem encerra o
    # lead, só para os envios automáticos de sequência.
    cadencia_opt_out_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    responsavel_id: Mapped[int | None] = mapped_column(
        ForeignKey("usuarios_operacoes.id", ondelete="SET NULL"), nullable=True, index=True
    )
    notas: Mapped[str | None] = mapped_column(Text, nullable=True)
    proxima_acao_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    ultimo_contato_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resultado: Mapped[str | None] = mapped_column(String(12), nullable=True, index=True)
    motivo_perda: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)
    motivo_perda_detalhe: Mapped[str | None] = mapped_column(Text, nullable=True)
    tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    # Item 4/5 do pedido de melhorias do cliente final (17/09/2026): logo do
    # próprio cliente exibida na mão do personagem no portal (mesmo formato
    # de Organizacao.branding["logo_asset"], mas por lead -- cada cliente vê
    # a própria logo, não a da organização/agência).
    logo_cliente: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    arquivado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    atualizado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    pesquisas_marca: Mapped[list["PesquisaMarca"]] = relationship(back_populates="lead")
    contatos: Mapped[list["ContatoLead"]] = relationship(
        back_populates="lead", cascade="all, delete-orphan", order_by="ContatoLead.criado_em.desc()"
    )
    lembretes: Mapped[list["LembreteCRM"]] = relationship(
        back_populates="lead", cascade="all, delete-orphan", order_by="LembreteCRM.lembrar_em"
    )
    empresa_registro: Mapped["EmpresaCRM | None"] = relationship(back_populates="leads")
    responsavel: Mapped["UsuarioOperacoes | None"] = relationship(foreign_keys=[responsavel_id])


class SolicitacaoAnonimizacaoLead(Base):
    """Pedido de exclusão de dados (LGPD) feito pelo próprio titular, sem depender
    de um atendente. Confirmado por token enviado por e-mail, como a recuperação
    de senha -- token de uso único, expira, nunca fica em claro no banco."""

    __tablename__ = "solicitacoes_anonimizacao_lead"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organizacao_id: Mapped[int] = mapped_column(ForeignKey("organizacoes.id", ondelete="CASCADE"), index=True)
    email: Mapped[str] = mapped_column(String(254), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expira_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    usado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    leads_anonimizados: Mapped[int] = mapped_column(default=0)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class HistoricoFaseLead(Base):
    """Registro de cada transição de fase do lead no funil (para linha do tempo)."""

    __tablename__ = "historico_fase_lead"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organizacao_id: Mapped[int] = mapped_column(ForeignKey("organizacoes.id", ondelete="CASCADE"), index=True)
    lead_id: Mapped[int] = mapped_column(ForeignKey("leads.id", ondelete="CASCADE"), index=True)
    fase: Mapped[str] = mapped_column(String(30))
    entrou_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    por: Mapped[str | None] = mapped_column(String(150), nullable=True)


# Tipos de documento acompanhados por lead (metadados; sem upload de arquivo).
TIPOS_DOCUMENTO_LEAD: tuple[str, ...] = (
    "procuracao",
    "gru",
    "protocolo",
    "oposicao",
    "certificado",
)


class DocumentoLead(Base):
    """Metadados de um documento do lead (procuração, GRU, protocolo, oposição,
    certificado). Um registro por tipo por lead.

    Achado do usuário (21/09/2026): "Etapa bloqueada. Documentos obrigatórios
    pendentes: procuração", sem nenhum lugar pra anexar o arquivo -- esta
    tabela só guardava metadado (número/data/status), nunca um arquivo de
    verdade. caminho/content_type/tamanho/arquivo_hash espelham
    MaterialMarcaCliente (app/models/portal_cliente.py) pro mesmo padrão de
    armazenamento local/S3 (app/storage.py).
    """

    __tablename__ = "documentos_lead"
    __table_args__ = (UniqueConstraint("organizacao_id", "lead_id", "tipo", name="uq_documento_lead_tipo"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organizacao_id: Mapped[int] = mapped_column(ForeignKey("organizacoes.id", ondelete="CASCADE"), index=True)
    lead_id: Mapped[int] = mapped_column(ForeignKey("leads.id", ondelete="CASCADE"), index=True)
    tipo: Mapped[str] = mapped_column(String(20))
    numero: Mapped[str | None] = mapped_column(String(60), nullable=True)
    data: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="pendente")
    observacoes: Mapped[str | None] = mapped_column(Text, nullable=True)
    versao: Mapped[int] = mapped_column(Integer, default=1)
    hash_documento: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    validade_em: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    obrigatorio: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    assinado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    assinado_ip_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    assinado_por_cliente_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    caminho: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_type: Mapped[str | None] = mapped_column(String(120), nullable=True)
    tamanho: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    arquivo_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    atualizado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ChecklistFaseLead(Base):
    """Item de checklist de uma etapa (fase) do funil do lead."""

    __tablename__ = "checklist_fase_lead"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organizacao_id: Mapped[int] = mapped_column(ForeignKey("organizacoes.id", ondelete="CASCADE"), index=True)
    lead_id: Mapped[int] = mapped_column(ForeignKey("leads.id", ondelete="CASCADE"), index=True)
    fase: Mapped[str] = mapped_column(String(30), index=True)
    descricao: Mapped[str] = mapped_column(String(300))
    concluido: Mapped[bool] = mapped_column(Boolean, default=False)
    ordem: Mapped[int] = mapped_column(Integer, default=0)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class GuiaInpi(Base):
    """GRU (guia de retribuição do INPI) registrada para um lead.

    Não emite a guia (o INPI não tem API); guarda o que foi emitido no portal —
    serviço, valor, número/nosso número, vencimento e pagamento — para controle.
    """

    __tablename__ = "guias_inpi"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organizacao_id: Mapped[int] = mapped_column(ForeignKey("organizacoes.id", ondelete="CASCADE"), index=True)
    lead_id: Mapped[int] = mapped_column(ForeignKey("leads.id", ondelete="CASCADE"), index=True)
    servico: Mapped[str | None] = mapped_column(String(60), nullable=True)
    codigo: Mapped[str | None] = mapped_column(String(10), nullable=True)
    descricao: Mapped[str] = mapped_column(String(200))
    valor: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    reduzido: Mapped[bool] = mapped_column(Boolean, default=False)
    numero_gru: Mapped[str | None] = mapped_column(String(60), nullable=True)
    vencimento: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(20), default="pendente", index=True)
    pago_em: Mapped[date | None] = mapped_column(Date, nullable=True)
    observacoes: Mapped[str | None] = mapped_column(Text, nullable=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    atualizado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class PropostaComercial(Base):
    """Proposta versionada de registro de marca vinculada a uma oportunidade."""

    __tablename__ = "propostas_comerciais"
    # Achado 9 do plano proposta-financeiro (Fase 5, 03/09/2026): nova versão
    # passa a manter o número-base da proposta original (só ``versao`` muda),
    # então a unicidade precisa incluir a versão -- antes era só (org, numero).
    __table_args__ = (
        UniqueConstraint("organizacao_id", "numero", "versao", name="uq_proposta_org_numero_versao"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organizacao_id: Mapped[int] = mapped_column(ForeignKey("organizacoes.id", ondelete="CASCADE"), index=True)
    lead_id: Mapped[int] = mapped_column(ForeignKey("leads.id", ondelete="CASCADE"), index=True)
    pesquisa_id: Mapped[str | None] = mapped_column(
        ForeignKey("pesquisas_marca.id", ondelete="SET NULL"), nullable=True, index=True
    )
    numero: Mapped[str] = mapped_column(String(40), index=True)
    versao: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(20), default="rascunho", index=True)
    validade_em: Mapped[date | None] = mapped_column(Date, nullable=True)
    marca: Mapped[str | None] = mapped_column(Text, nullable=True)
    classes: Mapped[str | None] = mapped_column(Text, nullable=True)
    escopo: Mapped[str] = mapped_column(Text, default="Registro de marca no INPI")
    honorarios: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    taxa_gru: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    condicoes_pagamento: Mapped[str | None] = mapped_column(Text, nullable=True)
    observacoes: Mapped[str | None] = mapped_column(Text, nullable=True)
    dados: Mapped[dict] = mapped_column(JSON, default=dict)
    enviado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    aceito_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    public_token_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True, index=True)
    public_token_expira_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    public_aceito_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    public_aceito_ip_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Dupla validação do aceite (orientação jurídica, 15/09/2026): o clique
    # no link público sozinho só prova posse do link, não que foi o cliente
    # de fato -- um código de confirmação por e-mail (canal já cadastrado,
    # nunca digitado nessa hora) é o segundo fator. Campos transitórios,
    # limpos depois que o aceite é confirmado.
    codigo_confirmacao_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    codigo_confirmacao_expira_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    codigo_confirmacao_tentativas: Mapped[int] = mapped_column(Integer, default=0)
    codigo_confirmacao_enviado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    pagamento_status: Mapped[str] = mapped_column(String(20), default="pendente", index=True)
    pagamento_confirmado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    pagamento_confirmado_por_id: Mapped[int | None] = mapped_column(
        ForeignKey("usuarios_operacoes.id", ondelete="SET NULL"), nullable=True, index=True
    )
    pagamento_confirmado_por: Mapped[str | None] = mapped_column(String(254), nullable=True)
    pagamento_confirmado_ip_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    sla_inicio_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sla_prazo_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sla_status: Mapped[str] = mapped_column(String(30), default="aguardando_aceite", index=True)
    responsavel_protocolo_id: Mapped[int | None] = mapped_column(
        ForeignKey("usuarios_operacoes.id", ondelete="SET NULL"), nullable=True, index=True
    )
    protocolo_numero: Mapped[str | None] = mapped_column(String(80), nullable=True)
    protocolo_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    protocolo_motivo_atraso: Mapped[str | None] = mapped_column(Text, nullable=True)
    protocolo_comprovante_id: Mapped[int | None] = mapped_column(
        ForeignKey("documentos_lead.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # Recebimento explícito pelo jurídico. O responsável do protocolo já
    # existia, mas não havia como distinguir "atribuído pelo comercial" de
    # "recebido pela operação", nem medir o tempo da passagem entre áreas.
    juridico_recebido_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    juridico_recebido_por_id: Mapped[int | None] = mapped_column(
        ForeignKey("usuarios_operacoes.id", ondelete="SET NULL"), nullable=True, index=True
    )
    juridico_recebido_por: Mapped[str | None] = mapped_column(String(254), nullable=True)
    criado_por: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AssinaturaPropostaComercial(Base):
    """Evidência imutável do aceite/assinatura eletrônica da proposta."""

    __tablename__ = "assinaturas_propostas_comerciais"
    __table_args__ = (UniqueConstraint("proposta_id", "versao", name="uq_assinatura_proposta_versao"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organizacao_id: Mapped[int] = mapped_column(ForeignKey("organizacoes.id", ondelete="CASCADE"), index=True)
    proposta_id: Mapped[int] = mapped_column(ForeignKey("propostas_comerciais.id", ondelete="CASCADE"), index=True)
    versao: Mapped[int] = mapped_column(Integer, index=True)
    hash_documento: Mapped[str] = mapped_column(String(64), index=True)
    cliente_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, index=True)
    ip_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    assinado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    provedor: Mapped[str] = mapped_column(String(30), default="interno")
    # Evidência do segundo fator (dupla validação, orientação jurídica de
    # 15/09/2026) -- None quando o aceite veio de um provedor que já traz
    # sua própria validação (ex.: Clicksign).
    segundo_fator_canal: Mapped[str | None] = mapped_column(String(20), nullable=True)
    segundo_fator_confirmado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class VersaoDocumentoLead(Base):
    __tablename__ = "versoes_documentos_lead"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organizacao_id: Mapped[int] = mapped_column(ForeignKey("organizacoes.id", ondelete="CASCADE"), index=True)
    documento_id: Mapped[int] = mapped_column(ForeignKey("documentos_lead.id", ondelete="CASCADE"), index=True)
    versao: Mapped[int] = mapped_column(Integer)
    hash_documento: Mapped[str] = mapped_column(String(64), index=True)
    conteudo: Mapped[dict] = mapped_column(JSON, default=dict)
    criado_por_tipo: Mapped[str] = mapped_column(String(20), default="operador")
    criado_por_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AssinaturaDocumentoLead(Base):
    __tablename__ = "assinaturas_documentos_lead"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organizacao_id: Mapped[int] = mapped_column(ForeignKey("organizacoes.id", ondelete="CASCADE"), index=True)
    documento_id: Mapped[int] = mapped_column(ForeignKey("documentos_lead.id", ondelete="CASCADE"), index=True)
    cliente_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, index=True)
    versao: Mapped[int] = mapped_column(Integer)
    hash_documento: Mapped[str] = mapped_column(String(64), index=True)
    ip_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    assinado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    provedor: Mapped[str] = mapped_column(String(30), default="interno")


class ContatoLead(Base):
    """Registro de uma interação do operador com a empresa (histórico de contatos)."""

    __tablename__ = "contatos_lead"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organizacao_id: Mapped[int] = mapped_column(ForeignKey("organizacoes.id", ondelete="CASCADE"), index=True)
    lead_id: Mapped[int] = mapped_column(ForeignKey("leads.id", ondelete="CASCADE"), index=True)
    empresa_id: Mapped[int | None] = mapped_column(
        ForeignKey("empresas_crm.id", ondelete="SET NULL"), nullable=True, index=True
    )
    pesquisa_id: Mapped[str | None] = mapped_column(
        ForeignKey("pesquisas_marca.id", ondelete="SET NULL"), nullable=True, index=True
    )
    operador_id: Mapped[int | None] = mapped_column(
        ForeignKey("usuarios_operacoes.id", ondelete="SET NULL"), nullable=True, index=True
    )
    operador_nome: Mapped[str | None] = mapped_column(String(150), nullable=True)
    canal: Mapped[CanalContato] = mapped_column(
        Enum(
            CanalContato,
            name="canal_contato",
            native_enum=False,
            create_constraint=True,
            length=20,
            values_callable=lambda members: [member.value for member in members],
        ),
        default=CanalContato.TELEFONE,
    )
    resultado: Mapped[str | None] = mapped_column(String(150), nullable=True)
    observacao: Mapped[str | None] = mapped_column(Text, nullable=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    lead: Mapped[Lead] = relationship(back_populates="contatos")
    empresa_registro: Mapped["EmpresaCRM | None"] = relationship(back_populates="contatos")
    pesquisa: Mapped["PesquisaMarca | None"] = relationship(back_populates="contatos")


class RespostaEmailLead(Base):
    """Conteúdo de um e-mail que o lead respondeu -- achado da auditoria
    completa do CRM (06/09/2026, item 21): antes só se sabia QUE o lead
    respondeu (EnvioCadenciaEmail.respondido_em), nunca O QUE ele escreveu.
    Alimentado por app.imap_polling, mesma caixa já configurada."""

    __tablename__ = "respostas_email_lead"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organizacao_id: Mapped[int] = mapped_column(ForeignKey("organizacoes.id", ondelete="CASCADE"), index=True)
    lead_id: Mapped[int] = mapped_column(ForeignKey("leads.id", ondelete="CASCADE"), index=True)
    remetente: Mapped[str] = mapped_column(String(254))
    assunto: Mapped[str | None] = mapped_column(String(255), nullable=True)
    corpo: Mapped[str] = mapped_column(Text)
    recebido_em: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


class LembreteCRM(Base):
    """Alerta interno com prazo e responsável vinculado a um cliente do CRM."""

    __tablename__ = "lembretes_crm"
    __table_args__ = (UniqueConstraint("organizacao_id", "idempotency_key", name="uq_lembrete_crm_idempotencia"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organizacao_id: Mapped[int] = mapped_column(ForeignKey("organizacoes.id", ondelete="CASCADE"), index=True)
    lead_id: Mapped[int] = mapped_column(ForeignKey("leads.id", ondelete="CASCADE"), index=True)
    responsavel_id: Mapped[int | None] = mapped_column(
        ForeignKey("usuarios_operacoes.id", ondelete="SET NULL"), nullable=True, index=True
    )
    tipo: Mapped[str] = mapped_column(String(40), index=True)
    prioridade: Mapped[str] = mapped_column(String(10), default="media", index=True)
    titulo: Mapped[str] = mapped_column(String(180))
    descricao: Mapped[str | None] = mapped_column(Text, nullable=True)
    lembrar_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    status: Mapped[str] = mapped_column(String(20), default="pendente", index=True)
    # Achado do usuário (15/09/2026): "Adiar 1 dia" empurrava lembrar_em sem
    # registrar por quê -- guarda só o motivo do último adiamento (o
    # histórico completo de quem/quando já fica em EventoAuditoria via
    # _auditar_lembrete, não precisa duplicar aqui).
    motivo_adiamento: Mapped[str | None] = mapped_column(Text, nullable=True)
    criado_por_id: Mapped[int | None] = mapped_column(
        ForeignKey("usuarios_operacoes.id", ondelete="SET NULL"), nullable=True
    )
    criado_por: Mapped[str] = mapped_column(String(254))
    idempotency_key: Mapped[str | None] = mapped_column(String(180), nullable=True, index=True)
    concluido_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    concluido_por: Mapped[str | None] = mapped_column(String(254), nullable=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    atualizado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    lead: Mapped[Lead] = relationship(back_populates="lembretes", lazy="selectin")
    responsavel: Mapped["UsuarioOperacoes | None"] = relationship(foreign_keys=[responsavel_id], lazy="selectin")


class EventoDominio(Base):
    """Envelope versionado e tipado para a timeline CRM, jurídica e financeira."""

    __tablename__ = "eventos_dominio"
    __table_args__ = (
        UniqueConstraint(
            "organizacao_id",
            "dominio",
            "idempotency_key",
            name="uq_evento_operacional_idempotencia",
        ),
        CheckConstraint(
            "dominio IN ('crm','juridico','financeiro')",
            name="ck_evento_operacional_dominio",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organizacao_id: Mapped[int] = mapped_column(ForeignKey("organizacoes.id", ondelete="CASCADE"), index=True)
    dominio: Mapped[str] = mapped_column(String(20), index=True)
    tipo: Mapped[str] = mapped_column(String(60), index=True)
    versao: Mapped[int] = mapped_column(Integer, default=1)
    entidade_tipo: Mapped[str] = mapped_column(String(40), index=True)
    entidade_id: Mapped[str] = mapped_column(String(64), index=True)
    ator_id: Mapped[int | None] = mapped_column(ForeignKey("usuarios_operacoes.id", ondelete="SET NULL"), nullable=True)
    ator: Mapped[str] = mapped_column(String(254))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    idempotency_key: Mapped[str | None] = mapped_column(String(180), nullable=True)
    ocorrido_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


