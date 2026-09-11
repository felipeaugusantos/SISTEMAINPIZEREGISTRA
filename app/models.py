from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from uuid import uuid4

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
    Float,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Table,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.request_context import request_id_atual


class TipoProcesso(StrEnum):
    MARCA = "marca"
    PATENTE = "patente"


class StatusLead(StrEnum):
    NOVO = "novo"
    EM_CONTATO = "em_contato"
    QUALIFICADO = "qualificado"
    PROPOSTA_ENVIADA = "proposta_enviada"
    SEM_RETORNO = "sem_retorno"
    CONVERTIDO = "convertido"
    DESCARTADO = "descartado"


class StatusProspect(StrEnum):
    """Situação de um Prospect (Fase 1 do Radar de Prospecção, 03/09/2026).

    Vocabulário deliberadamente restrito ao que a Fase 1 sabe produzir --
    enriquecimento/triagem/score (Fases 2-5) trazem estados intermediários
    novos por migração própria, quando o código que os produz existir."""

    NOVO = "novo"
    APROVADO = "aprovado"
    REJEITADO = "rejeitado"
    DUPLICADO = "duplicado"
    CONVERTIDO_LEAD = "convertido_lead"


class FaseLead(StrEnum):
    """Etapa do lead no funil de atendimento (do 1º contato ao processo no INPI).

    Expandida na auditoria de CRM/financeiro (04/09/2026, achado CRM-11) de 7
    para 10 fases: "qualificado" passa a ser uma fase própria (antes só
    existia como StatusLead, sem posição no funil), "pagamento_realizado"
    virou duas fases (aguardando_pagamento / pagamento_confirmado -- a
    anterior não distinguia cobrança emitida de pagamento efetivamente
    recebido), e "ganho" passa a ser uma fase do funil, não só o campo
    ``Lead.resultado``. Decisão de produto tomada (não implementada): NÃO
    existe uma fase "contrato_assinado" separada de "proposta_aceita" --
    neste sistema, assinar a proposta (AssinaturaPropostaComercial) É o
    próprio ato de aceitá-la, mesmo evento e mesmo timestamp; uma fase
    própria para isso ficaria sempre vazia (o lead nunca fica "parado" nela).
    """

    CONTATO_INICIAL = "contato_inicial"
    QUALIFICADO = "qualificado"
    RELATORIO_ENVIADO = "relatorio_enviado"
    PROPOSTA_ENVIADA = "proposta_enviada"
    PROPOSTA_ACEITA = "proposta_aceita"
    AGUARDANDO_PAGAMENTO = "aguardando_pagamento"
    PAGAMENTO_CONFIRMADO = "pagamento_confirmado"
    GANHO = "ganho"
    PROTOCOLO_INPI = "protocolo_inpi"
    PROCESSO_INPI = "processo_inpi"


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


processo_titulares = Table(
    "processo_titulares",
    Base.metadata,
    Column("processo_id", ForeignKey("processos.id", ondelete="CASCADE"), primary_key=True),
    Column("titular_id", ForeignKey("titulares.id", ondelete="CASCADE"), primary_key=True),
)


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


class TipoAtivoPI(StrEnum):
    MARCA = "marca"
    PATENTE = "patente"
    MODELO_UTILIDADE = "modelo_utilidade"
    DESENHO_INDUSTRIAL = "desenho_industrial"
    CONTRATO = "contrato"
    CESSAO = "cessao"
    LICENCA = "licenca"
    FRANQUIA = "franquia"


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
    __table_args__ = (UniqueConstraint("chave_origem", name="uq_movimentacoes_chave_origem"),)

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


class CategoriaFinanceira(Base):
    __tablename__ = "categorias_financeiras"
    __table_args__ = (UniqueConstraint("organizacao_id", "nome", "tipo", name="uq_categoria_financeira_org"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organizacao_id: Mapped[int] = mapped_column(ForeignKey("organizacoes.id", ondelete="CASCADE"), index=True)
    nome: Mapped[str] = mapped_column(String(120), index=True)
    tipo: Mapped[str] = mapped_column(String(12), default="ambos", index=True)
    ativo: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class FormaPagamentoFinanceira(Base):
    __tablename__ = "formas_pagamento_financeiras"
    __table_args__ = (UniqueConstraint("organizacao_id", "nome", name="uq_forma_pagamento_financeira_org"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organizacao_id: Mapped[int] = mapped_column(ForeignKey("organizacoes.id", ondelete="CASCADE"), index=True)
    nome: Mapped[str] = mapped_column(String(120), index=True)
    tipo: Mapped[str] = mapped_column(String(30), default="outro", index=True)
    permite_parcelamento: Mapped[bool] = mapped_column(Boolean, default=False)
    maximo_parcelas: Mapped[int] = mapped_column(Integer, default=1)
    ativo: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    atualizado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class DepartamentoFinanceiro(Base):
    __tablename__ = "departamentos_financeiros"
    __table_args__ = (UniqueConstraint("organizacao_id", "codigo", name="uq_departamento_financeiro_org"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organizacao_id: Mapped[int] = mapped_column(ForeignKey("organizacoes.id", ondelete="CASCADE"), index=True)
    codigo: Mapped[str] = mapped_column(String(50), index=True)
    nome: Mapped[str] = mapped_column(String(150))
    ativo: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CentroCustoFinanceiro(Base):
    __tablename__ = "centros_custo_financeiros"
    __table_args__ = (UniqueConstraint("organizacao_id", "codigo", name="uq_centro_custo_financeiro_org"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organizacao_id: Mapped[int] = mapped_column(ForeignKey("organizacoes.id", ondelete="CASCADE"), index=True)
    departamento_id: Mapped[int | None] = mapped_column(
        ForeignKey("departamentos_financeiros.id", ondelete="SET NULL"), nullable=True, index=True
    )
    codigo: Mapped[str] = mapped_column(String(50), index=True)
    nome: Mapped[str] = mapped_column(String(150))
    ativo: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PlanoContas(Base):
    """Achado FASE7-13 da auditoria (04/09/2026): plano de contas gerencial --
    ver app/plano_contas.py para o seed padrão e a lógica de DRE. Hierarquia
    simples (conta_pai_id) e um grupo_dre fixo (app.plano_contas.GRUPOS_DRE)
    que decide onde a conta entra no demonstrativo de resultado."""

    __tablename__ = "plano_contas"
    __table_args__ = (UniqueConstraint("organizacao_id", "codigo", name="uq_plano_contas_codigo"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organizacao_id: Mapped[int] = mapped_column(ForeignKey("organizacoes.id", ondelete="CASCADE"), index=True)
    conta_pai_id: Mapped[int | None] = mapped_column(
        ForeignKey("plano_contas.id", ondelete="SET NULL"), nullable=True, index=True
    )
    codigo: Mapped[str] = mapped_column(String(20), index=True)
    nome: Mapped[str] = mapped_column(String(150))
    natureza: Mapped[str] = mapped_column(String(10))
    grupo_dre: Mapped[str] = mapped_column(String(30), index=True)
    ativo: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ExtratoBancario(Base):
    """Achado FASE7-3 da auditoria (04/09/2026): um extrato OFX importado --
    ver app/ofx.py para o parser e app/api/conciliacao.py para o
    matching automático contra ParcelaFinanceira."""

    __tablename__ = "extratos_bancarios"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organizacao_id: Mapped[int] = mapped_column(ForeignKey("organizacoes.id", ondelete="CASCADE"), index=True)
    nome_arquivo: Mapped[str] = mapped_column(String(255))
    total_transacoes: Mapped[int] = mapped_column(Integer, default=0)
    importado_por: Mapped[str] = mapped_column(String(254))
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


class TransacaoBancaria(Base):
    """Uma linha de extrato bancário (crédito ou débito). status="conciliada"
    quando casada com uma ParcelaFinanceira (automático, se houver
    exatamente uma correspondência de valor+data, ou manual pelo
    operador). fitid é o identificador único do banco para a transação --
    reimportar o mesmo extrato nunca duplica (UniqueConstraint)."""

    __tablename__ = "transacoes_bancarias"
    __table_args__ = (UniqueConstraint("organizacao_id", "fitid", name="uq_transacao_bancaria_fitid"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organizacao_id: Mapped[int] = mapped_column(ForeignKey("organizacoes.id", ondelete="CASCADE"), index=True)
    extrato_id: Mapped[int] = mapped_column(ForeignKey("extratos_bancarios.id", ondelete="CASCADE"), index=True)
    fitid: Mapped[str] = mapped_column(String(120), index=True)
    data: Mapped[date] = mapped_column(Date, index=True)
    valor: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    tipo: Mapped[str] = mapped_column(String(10), index=True)
    descricao: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(20), default="pendente", index=True)
    parcela_id: Mapped[int | None] = mapped_column(
        ForeignKey("parcelas_financeiras.id", ondelete="SET NULL"), nullable=True, index=True
    )
    conciliado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    conciliado_por: Mapped[str | None] = mapped_column(String(254), nullable=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


class NotaFiscalServico(Base):
    """Achado FASE7-6 da auditoria (04/09/2026): NFS-e emitida (ou tentada)
    para um lançamento de receita -- ver app/nfse.py para a interface de
    adaptador. Nunca emitida automaticamente: sempre uma ação explícita do
    operador para um lançamento específico."""

    __tablename__ = "notas_fiscais_servico"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organizacao_id: Mapped[int] = mapped_column(ForeignKey("organizacoes.id", ondelete="CASCADE"), index=True)
    lancamento_id: Mapped[int] = mapped_column(
        ForeignKey("lancamentos_financeiros.id", ondelete="CASCADE"), index=True
    )
    adaptador: Mapped[str] = mapped_column(String(30))
    numero: Mapped[str | None] = mapped_column(String(60), nullable=True)
    codigo_verificacao: Mapped[str | None] = mapped_column(String(60), nullable=True)
    valor: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    status: Mapped[str] = mapped_column(String(20), default="emitida", index=True)
    erro_detalhe: Mapped[str | None] = mapped_column(Text, nullable=True)
    emitida_por: Mapped[str] = mapped_column(String(254))
    emitida_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    cancelada_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelada_por: Mapped[str | None] = mapped_column(String(254), nullable=True)


class FornecedorJuridico(Base):
    __tablename__ = "fornecedores_juridicos"
    __table_args__ = (UniqueConstraint("organizacao_id", "documento", name="uq_fornecedor_juridico_org_documento"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organizacao_id: Mapped[int] = mapped_column(ForeignKey("organizacoes.id", ondelete="CASCADE"), index=True)
    nome: Mapped[str] = mapped_column(String(180), index=True)
    documento: Mapped[str | None] = mapped_column(String(30), nullable=True, index=True)
    email: Mapped[str | None] = mapped_column(String(254), nullable=True)
    ativo: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ContratoJuridico(Base):
    __tablename__ = "contratos_juridicos"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organizacao_id: Mapped[int] = mapped_column(ForeignKey("organizacoes.id", ondelete="CASCADE"), index=True)
    titulo: Mapped[str] = mapped_column(String(180))
    fornecedor_id: Mapped[int | None] = mapped_column(
        ForeignKey("fornecedores_juridicos.id", ondelete="SET NULL"), nullable=True, index=True
    )
    processo_id: Mapped[int | None] = mapped_column(
        ForeignKey("processos.id", ondelete="SET NULL"), nullable=True, index=True
    )
    status: Mapped[str] = mapped_column(String(30), default="ativo", index=True)
    vigencia_inicio: Mapped[date | None] = mapped_column(Date, nullable=True)
    vigencia_fim: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    valor: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    documento_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CustoJuridico(Base):
    __tablename__ = "custos_juridicos"
    __table_args__ = (UniqueConstraint("organizacao_id", "idempotency_key", name="uq_custo_juridico_idempotencia"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organizacao_id: Mapped[int] = mapped_column(ForeignKey("organizacoes.id", ondelete="CASCADE"), index=True)
    centro_custo_id: Mapped[int | None] = mapped_column(
        ForeignKey("centros_custo_financeiros.id", ondelete="SET NULL"), nullable=True, index=True
    )
    departamento_id: Mapped[int | None] = mapped_column(
        ForeignKey("departamentos_financeiros.id", ondelete="SET NULL"), nullable=True, index=True
    )
    fornecedor_id: Mapped[int | None] = mapped_column(
        ForeignKey("fornecedores_juridicos.id", ondelete="SET NULL"), nullable=True, index=True
    )
    contrato_id: Mapped[int | None] = mapped_column(
        ForeignKey("contratos_juridicos.id", ondelete="SET NULL"), nullable=True, index=True
    )
    processo_id: Mapped[int | None] = mapped_column(
        ForeignKey("processos.id", ondelete="SET NULL"), nullable=True, index=True
    )
    categoria: Mapped[str] = mapped_column(String(30), index=True)
    descricao: Mapped[str] = mapped_column(String(240))
    valor: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    responsaveis: Mapped[list[int]] = mapped_column(JSON, default=list)
    idempotency_key: Mapped[str] = mapped_column(String(120), index=True)
    criado_por_id: Mapped[int | None] = mapped_column(
        ForeignKey("usuarios_operacoes.id", ondelete="SET NULL"), nullable=True
    )
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


class ApontamentoHoras(Base):
    """Registro manual de horas trabalhadas -- Fase A da evolucao do CRM
    (05/09/2026): o sistema nao tinha nenhum controle de horas fatuaveis,
    so custo monetario (CustoJuridico). Vinculado a um lead (oportunidade
    comercial) e/ou a um processo monitorado (caso juridico) -- pelo menos
    um dos dois precisa estar preenchido. Sem valor/hora: o calculo de
    faturamento a partir de horas fica para uma fase futura, se necessario.
    """

    __tablename__ = "apontamentos_horas"
    __table_args__ = (
        CheckConstraint(
            "processo_monitorado_id IS NOT NULL OR lead_id IS NOT NULL",
            name="ck_apontamento_horas_vinculo",
        ),
        CheckConstraint("horas > 0", name="ck_apontamento_horas_positivas"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organizacao_id: Mapped[int] = mapped_column(ForeignKey("organizacoes.id", ondelete="CASCADE"), index=True)
    usuario_id: Mapped[int] = mapped_column(ForeignKey("usuarios_operacoes.id", ondelete="RESTRICT"), index=True)
    lead_id: Mapped[int | None] = mapped_column(ForeignKey("leads.id", ondelete="SET NULL"), nullable=True, index=True)
    processo_monitorado_id: Mapped[int | None] = mapped_column(
        ForeignKey("processos_monitorados.id", ondelete="SET NULL"), nullable=True, index=True
    )
    data: Mapped[date] = mapped_column(Date, index=True)
    horas: Mapped[Decimal] = mapped_column(Numeric(5, 2))
    descricao: Mapped[str] = mapped_column(String(500))
    faturavel: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    atualizado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    usuario: Mapped["UsuarioOperacoes"] = relationship(lazy="selectin")
    lead: Mapped["Lead | None"] = relationship(lazy="selectin")
    processo_monitorado: Mapped["ProcessoMonitorado | None"] = relationship(lazy="selectin")


class WebhookFinanceiro(Base):
    __tablename__ = "webhooks_financeiros"
    __table_args__ = (UniqueConstraint("organizacao_id", "referencia", name="uq_webhook_financeiro_org_referencia"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organizacao_id: Mapped[int] = mapped_column(ForeignKey("organizacoes.id", ondelete="CASCADE"), index=True)
    referencia: Mapped[str] = mapped_column(String(150), index=True)
    evento: Mapped[str] = mapped_column(String(80), index=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="recebido", index=True)
    tentativas: Mapped[int] = mapped_column(Integer, default=1)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ServicoFinanceiro(Base):
    __tablename__ = "servicos_financeiros"
    __table_args__ = (UniqueConstraint("organizacao_id", "codigo", name="uq_servico_financeiro_codigo"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organizacao_id: Mapped[int] = mapped_column(ForeignKey("organizacoes.id", ondelete="CASCADE"), index=True)
    codigo: Mapped[str] = mapped_column(String(50), index=True)
    nome: Mapped[str] = mapped_column(String(180))
    descricao: Mapped[str | None] = mapped_column(Text, nullable=True)
    valor: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    recorrente: Mapped[bool] = mapped_column(Boolean, default=False)
    ativo: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ContratacaoServico(Base):
    __tablename__ = "contratacoes_servicos"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organizacao_id: Mapped[int] = mapped_column(ForeignKey("organizacoes.id", ondelete="CASCADE"), index=True)
    # Nulo quando a contratacao vem do aceite de uma proposta (valor
    # assinado, sem item de catalogo vinculado) -- Fase 4 do plano
    # proposta-financeiro (03/09/2026).
    servico_id: Mapped[int | None] = mapped_column(
        ForeignKey("servicos_financeiros.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    lead_id: Mapped[int | None] = mapped_column(ForeignKey("leads.id", ondelete="SET NULL"), nullable=True, index=True)
    processo_id: Mapped[int | None] = mapped_column(
        ForeignKey("processos.id", ondelete="SET NULL"), nullable=True, index=True
    )
    proposta_id: Mapped[int | None] = mapped_column(
        ForeignKey("propostas_comerciais.id", ondelete="SET NULL"), nullable=True, unique=True, index=True
    )
    lancamento_id: Mapped[int | None] = mapped_column(
        ForeignKey("lancamentos_financeiros.id", ondelete="SET NULL"), nullable=True, unique=True
    )
    status: Mapped[str] = mapped_column(String(20), default="contratada", index=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class LancamentoFinanceiro(Base):
    __tablename__ = "lancamentos_financeiros"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organizacao_id: Mapped[int] = mapped_column(ForeignKey("organizacoes.id", ondelete="CASCADE"), index=True)
    empresa_id: Mapped[int | None] = mapped_column(
        ForeignKey("empresas_crm.id", ondelete="SET NULL"), nullable=True, index=True
    )

    lead_id: Mapped[int | None] = mapped_column(ForeignKey("leads.id", ondelete="SET NULL"), nullable=True, index=True)
    processo_id: Mapped[int | None] = mapped_column(
        ForeignKey("processos.id", ondelete="SET NULL"), nullable=True, index=True
    )
    proposta_id: Mapped[int | None] = mapped_column(
        ForeignKey("propostas_comerciais.id", ondelete="SET NULL"), nullable=True, index=True
    )
    idempotency_key: Mapped[str | None] = mapped_column(String(120), nullable=True, unique=True, index=True)
    categoria_id: Mapped[int | None] = mapped_column(
        ForeignKey("categorias_financeiras.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # Achado FASE7-13 da auditoria (04/09/2026): classificação contábil
    # gerencial (ver app/plano_contas.py) -- opcional para não quebrar
    # lançamentos existentes; sem ela, entra como "sem_classificacao" no DRE.
    conta_contabil_id: Mapped[int | None] = mapped_column(
        ForeignKey("plano_contas.id", ondelete="SET NULL"), nullable=True, index=True
    )
    forma_pagamento_id: Mapped[int | None] = mapped_column(
        ForeignKey("formas_pagamento_financeiras.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    tipo: Mapped[str] = mapped_column(String(12), index=True)
    descricao: Mapped[str] = mapped_column(String(240), index=True)
    documento: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    competencia: Mapped[date] = mapped_column(Date, index=True)
    valor_total: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    status: Mapped[str] = mapped_column(String(20), default="aberto", index=True)
    observacoes: Mapped[str | None] = mapped_column(Text, nullable=True)
    criado_por_id: Mapped[int | None] = mapped_column(
        ForeignKey("usuarios_operacoes.id", ondelete="SET NULL"), nullable=True
    )
    criado_por: Mapped[str] = mapped_column(String(254))
    cancelado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelado_por: Mapped[str | None] = mapped_column(String(254), nullable=True)
    cancelamento_motivo: Mapped[str | None] = mapped_column(Text, nullable=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    atualizado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    empresa_registro: Mapped[EmpresaCRM | None] = relationship(
        back_populates="lancamentos_financeiros", lazy="selectin"
    )
    categoria: Mapped[CategoriaFinanceira | None] = relationship(lazy="selectin")
    conta_contabil: Mapped["PlanoContas | None"] = relationship(lazy="selectin")
    forma_pagamento: Mapped[FormaPagamentoFinanceira | None] = relationship(lazy="selectin")
    parcelas: Mapped[list["ParcelaFinanceira"]] = relationship(
        back_populates="lancamento",
        cascade="all, delete-orphan",
        order_by="ParcelaFinanceira.numero",
    )


class ParcelaFinanceira(Base):
    __tablename__ = "parcelas_financeiras"
    __table_args__ = (UniqueConstraint("lancamento_id", "numero", name="uq_parcela_financeira_numero"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organizacao_id: Mapped[int] = mapped_column(ForeignKey("organizacoes.id", ondelete="CASCADE"), index=True)
    lancamento_id: Mapped[int] = mapped_column(ForeignKey("lancamentos_financeiros.id", ondelete="CASCADE"), index=True)
    numero: Mapped[int] = mapped_column(Integer)
    vencimento: Mapped[date] = mapped_column(Date, index=True)
    valor: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    valor_pago: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    status: Mapped[str] = mapped_column(String(20), default="aberta", index=True)
    pago_em: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    forma_pagamento_id: Mapped[int | None] = mapped_column(
        ForeignKey("formas_pagamento_financeiras.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    forma_pagamento: Mapped[str | None] = mapped_column(String(50), nullable=True)
    observacoes_baixa: Mapped[str | None] = mapped_column(Text, nullable=True)
    lancamento: Mapped[LancamentoFinanceiro] = relationship(back_populates="parcelas")


class ComissaoFinanceira(Base):
    """Achado FASE7-7 da auditoria (04/09/2026): comissão de operador sobre
    receita -- gerada automaticamente quando uma parcela de um lançamento
    "receber" vinculado a um Lead com responsável comissionado é baixada
    (ver app/api/financeiro.py::_gerar_comissao_se_aplicavel). Uma linha por
    parcela baixada, nunca duplicada (unique em parcela_id)."""

    __tablename__ = "comissoes_financeiras"
    __table_args__ = (UniqueConstraint("parcela_id", name="uq_comissao_financeira_parcela"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organizacao_id: Mapped[int] = mapped_column(ForeignKey("organizacoes.id", ondelete="CASCADE"), index=True)
    usuario_id: Mapped[int] = mapped_column(
        ForeignKey("usuarios_operacoes.id", ondelete="CASCADE"), index=True
    )
    lancamento_id: Mapped[int] = mapped_column(
        ForeignKey("lancamentos_financeiros.id", ondelete="CASCADE"), index=True
    )
    parcela_id: Mapped[int] = mapped_column(ForeignKey("parcelas_financeiras.id", ondelete="CASCADE"), index=True)
    valor_base: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    percentual: Mapped[Decimal] = mapped_column(Numeric(5, 2))
    valor_comissao: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    status: Mapped[str] = mapped_column(String(20), default="pendente", index=True)
    pago_em: Mapped[date | None] = mapped_column(Date, nullable=True)
    pago_por: Mapped[str | None] = mapped_column(String(254), nullable=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    usuario: Mapped["UsuarioOperacoes"] = relationship(lazy="selectin")


class HistoricoFinanceiro(Base):
    __tablename__ = "historicos_financeiros"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organizacao_id: Mapped[int] = mapped_column(ForeignKey("organizacoes.id", ondelete="CASCADE"), index=True)
    lancamento_id: Mapped[int] = mapped_column(ForeignKey("lancamentos_financeiros.id", ondelete="CASCADE"), index=True)
    parcela_id: Mapped[int | None] = mapped_column(
        ForeignKey("parcelas_financeiras.id", ondelete="SET NULL"), nullable=True, index=True
    )
    acao: Mapped[str] = mapped_column(String(30), index=True)
    ator: Mapped[str] = mapped_column(String(254), index=True)
    descricao: Mapped[str] = mapped_column(String(500))
    detalhes: Mapped[dict] = mapped_column(JSON, default=dict)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


class RetribuicaoInpi(Base):
    """Tabela de retribuições do INPI (serviços de marca) — referência global.

    Os valores oficiais são nacionais (iguais para todos os tenants); por isso a
    tabela não é multi-tenant. `confirmado` indica que o valor foi conferido
    contra a tabela oficial vigente (guarda contra usar valor de referência).
    """

    __tablename__ = "retribuicoes_inpi"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    servico: Mapped[str] = mapped_column(String(60), unique=True, index=True)
    descricao: Mapped[str] = mapped_column(String(200))
    grupo: Mapped[str] = mapped_column(String(20), default="marca", index=True)
    codigo: Mapped[str | None] = mapped_column(String(10), nullable=True, index=True)
    valor_normal: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    valor_reduzido: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    fase_sugerida: Mapped[str | None] = mapped_column(String(30), nullable=True, index=True)
    confirmado: Mapped[bool] = mapped_column(Boolean, default=False)
    ativo: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    ordem: Mapped[int] = mapped_column(Integer, default=0)
    observacoes: Mapped[str | None] = mapped_column(Text, nullable=True)
    atualizado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


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


class PrazoJuridico(Base):
    """Prazo operacional vinculado a um processo monitorado."""

    __tablename__ = "prazos_juridicos"
    __table_args__ = (
        UniqueConstraint(
            "organizacao_id",
            "processo_monitorado_id",
            "movimentacao_origem_id",
            name="uq_prazo_juridico_movimentacao",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organizacao_id: Mapped[int] = mapped_column(ForeignKey("organizacoes.id", ondelete="CASCADE"), index=True)
    processo_monitorado_id: Mapped[int] = mapped_column(
        ForeignKey("processos_monitorados.id", ondelete="CASCADE"), index=True
    )
    movimentacao_origem_id: Mapped[int | None] = mapped_column(
        ForeignKey("movimentacoes.id", ondelete="SET NULL"), nullable=True, index=True
    )
    responsavel_id: Mapped[int | None] = mapped_column(
        ForeignKey("usuarios_operacoes.id", ondelete="SET NULL"), nullable=True, index=True
    )
    escalonar_para_id: Mapped[int | None] = mapped_column(
        ForeignKey("usuarios_operacoes.id", ondelete="SET NULL"), nullable=True, index=True
    )
    titulo: Mapped[str] = mapped_column(String(180))
    descricao: Mapped[str | None] = mapped_column(Text, nullable=True)
    tipo: Mapped[str] = mapped_column(String(40), default="manifestacao", index=True)
    origem: Mapped[str] = mapped_column(String(20), default="manual", index=True)
    contagem: Mapped[str] = mapped_column(String(20), default="corridos")
    data_base: Mapped[date] = mapped_column(Date, index=True)
    dias_prazo: Mapped[int] = mapped_column(Integer)
    vencimento_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    status: Mapped[str] = mapped_column(String(30), default="pendente", index=True)
    prioridade: Mapped[str] = mapped_column(String(10), default="media", index=True)
    confirmado: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    confirmado_por_id: Mapped[int | None] = mapped_column(
        ForeignKey("usuarios_operacoes.id", ondelete="SET NULL"), nullable=True
    )

    confirmado_por: Mapped[str | None] = mapped_column(String(254), nullable=True)
    confirmado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    confirmacao_origem: Mapped[str | None] = mapped_column(String(30), nullable=True)
    confirmacao_observacoes: Mapped[str | None] = mapped_column(Text, nullable=True)
    antecedencia_dias: Mapped[int] = mapped_column(Integer, default=7)
    escalonar_dias_antes: Mapped[int] = mapped_column(Integer, default=2)
    escalonado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    concluido_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    concluido_por: Mapped[str | None] = mapped_column(String(254), nullable=True)
    criado_por: Mapped[str] = mapped_column(String(254))
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    atualizado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    processo_monitorado: Mapped[ProcessoMonitorado] = relationship(back_populates="prazos_juridicos", lazy="selectin")
    responsavel: Mapped["UsuarioOperacoes | None"] = relationship(foreign_keys=[responsavel_id], lazy="selectin")
    escalonar_para: Mapped["UsuarioOperacoes | None"] = relationship(foreign_keys=[escalonar_para_id], lazy="selectin")


class PoliticaJuridica(Base):
    """Regras operacionais do módulo jurídico configuradas por organização.

    Achado 5.3 da auditoria (01/09/2026): por padrão nada muda — as duas
    exigências abaixo são opt-in, desligadas por padrão, para não travar
    operações pequenas que hoje concluem prazos sozinhas.
    """

    __tablename__ = "politicas_juridicas"
    __table_args__ = (UniqueConstraint("organizacao_id", name="uq_politica_juridica_organizacao"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organizacao_id: Mapped[int] = mapped_column(ForeignKey("organizacoes.id", ondelete="CASCADE"), index=True)
    exigir_evidencia_conclusao: Mapped[bool] = mapped_column(Boolean, default=False)
    exigir_segunda_pessoa_critico: Mapped[bool] = mapped_column(Boolean, default=False)
    atualizado_por: Mapped[str | None] = mapped_column(String(254), nullable=True)
    atualizado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class RegraJuridicaVersionada(Base):
    """Histórico de parâmetros jurídicos (prazos legais em dias, datas-marco
    de vigência de norma) hoje fixos em app/api/juridico.py, sem registro de
    quando cada valor passou a valer nem da fonte que o justifica.

    Achado 5.5 da auditoria (02/09/2026), Fase 3: tabela de referência global
    (não é dado por organização — a lei é a mesma para todos os tenants),
    append-only. Cada linha vale no intervalo [vigencia_inicio, vigencia_fim)
    — vigencia_fim nulo significa "vigente até hoje". O código consulta esta
    tabela só em app.api.juridico._historico_regra/_valor_vigente; quando não
    há linha aplicável, o valor padrão hardcoded no código continua valendo
    (tabela vazia = comportamento idêntico ao anterior a esta migration).
    """

    __tablename__ = "regras_juridicas_versionadas"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    codigo: Mapped[str] = mapped_column(String(80), index=True)
    valor: Mapped[dict] = mapped_column(JSON)
    vigencia_inicio: Mapped[date] = mapped_column(Date)
    vigencia_fim: Mapped[date | None] = mapped_column(Date, nullable=True)
    fonte_legal: Mapped[str] = mapped_column(Text)
    observacoes: Mapped[str | None] = mapped_column(Text, nullable=True)
    criado_por: Mapped[str | None] = mapped_column(String(254), nullable=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class MovimentacaoAvaliadaJuridico(Base):
    """Marca que o motor jurídico (``executar_motor_organizacao``) já avaliou
    uma movimentação para uma organização e concluiu que nenhum prazo era
    cabível — sem essa marca, a movimentação ficaria para sempre no pool de
    candidatos da consulta (o filtro de exclusão hoje só olha se já existe
    ``PrazoJuridico``, e despachos não mapeados nunca geram um).

    Achado 5.6 da auditoria (02/09/2026), Fase 4: com organizações que
    acumulam mais de 2000 movimentações não-classificáveis, essas linhas
    ocupavam para sempre as vagas do ``LIMIT 2000`` da consulta, impedindo
    movimentações mais antigas e realmente acionáveis de serem avaliadas —
    um backlog silencioso e crescente. ``Movimentacao`` é global (compartilhada
    entre organizações que monitoram o mesmo processo), então esta marca tem
    que ser por organização, não pode ir na própria ``Movimentacao``.

    Contrapartida assumida conscientemente: se uma norma futura tornar uma
    movimentação hoje não-classificável em classificável, ela não será
    reavaliada automaticamente — precisaria de uma rotina de reprocessamento
    manual (fora do escopo desta correção).
    """

    __tablename__ = "movimentacoes_avaliadas_juridico"
    __table_args__ = (
        UniqueConstraint(
            "organizacao_id",
            "movimentacao_id",
            name="uq_movimentacao_avaliada_juridico",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organizacao_id: Mapped[int] = mapped_column(ForeignKey("organizacoes.id", ondelete="CASCADE"), index=True)
    movimentacao_id: Mapped[int] = mapped_column(ForeignKey("movimentacoes.id", ondelete="CASCADE"), index=True)
    motivo: Mapped[str] = mapped_column(String(40))
    avaliado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DocumentoEntregaJuridico(Base):
    """Documento anexado como evidência de uma entrega (protocolo, GRU paga,
    comprovante) registrada num prazo jurídico — com hash SHA-256 para
    verificação de integridade, seguindo o mesmo padrão de
    ``DocumentoAtivoPI``/``app.storage``.

    Achado 5.8 da auditoria (02/09/2026), Fase 7: ``registrar_entrega`` só
    aceitava ``protocolo``/``documento`` como texto livre, sem anexo real nem
    hash — qualquer texto passava como "evidência", sem verificação alguma.
    O anexo é opcional aqui (mantém compatibilidade com quem só registra o
    número de protocolo em texto); quando enviado, fica registrado com hash e
    pode ser conferido depois.
    """

    __tablename__ = "documentos_entrega_juridico"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organizacao_id: Mapped[int] = mapped_column(ForeignKey("organizacoes.id", ondelete="CASCADE"), index=True)
    prazo_id: Mapped[int] = mapped_column(ForeignKey("prazos_juridicos.id", ondelete="CASCADE"), index=True)
    nome: Mapped[str] = mapped_column(String(255))
    hash_documento: Mapped[str] = mapped_column(String(64), index=True)
    caminho: Mapped[str] = mapped_column(Text)
    content_type: Mapped[str | None] = mapped_column(String(120), nullable=True)
    criado_por: Mapped[str | None] = mapped_column(String(254), nullable=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


class ItemChecklistPrazo(Base):
    """Etapa de conferência de um prazo jurídico (checklist operacional)."""

    __tablename__ = "itens_checklist_prazo"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organizacao_id: Mapped[int] = mapped_column(ForeignKey("organizacoes.id", ondelete="CASCADE"), index=True)
    prazo_id: Mapped[int] = mapped_column(ForeignKey("prazos_juridicos.id", ondelete="CASCADE"), index=True)
    descricao: Mapped[str] = mapped_column(String(300))
    concluido: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    ordem: Mapped[int] = mapped_column(Integer, default=0)
    concluido_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    concluido_por: Mapped[str | None] = mapped_column(String(254), nullable=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class NotificacaoJuridica(Base):
    __tablename__ = "notificacoes_juridicas"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organizacao_id: Mapped[int] = mapped_column(ForeignKey("organizacoes.id", ondelete="CASCADE"), index=True)
    prazo_id: Mapped[int] = mapped_column(ForeignKey("prazos_juridicos.id", ondelete="CASCADE"), index=True)
    destinatario_id: Mapped[int | None] = mapped_column(
        ForeignKey("usuarios_operacoes.id", ondelete="SET NULL"), nullable=True, index=True
    )
    chave: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    tipo: Mapped[str] = mapped_column(String(30), index=True)
    titulo: Mapped[str] = mapped_column(String(180))
    mensagem: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="nova", index=True)
    lida_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lida_por: Mapped[str | None] = mapped_column(String(254), nullable=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)

    prazo: Mapped[PrazoJuridico] = relationship(lazy="selectin")
    destinatario: Mapped["UsuarioOperacoes | None"] = relationship(lazy="selectin")


class EventoJuridico(Base):
    """Trilha imutável de criação, alteração, entrega e leitura jurídica."""

    __tablename__ = "eventos_juridicos"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organizacao_id: Mapped[int] = mapped_column(ForeignKey("organizacoes.id", ondelete="CASCADE"), index=True)
    prazo_id: Mapped[int | None] = mapped_column(
        ForeignKey("prazos_juridicos.id", ondelete="SET NULL"), nullable=True, index=True
    )
    processo_monitorado_id: Mapped[int] = mapped_column(
        ForeignKey("processos_monitorados.id", ondelete="CASCADE"), index=True
    )
    tipo: Mapped[str] = mapped_column(String(30), index=True)
    ator: Mapped[str] = mapped_column(String(254), index=True)
    descricao: Mapped[str] = mapped_column(String(500))
    detalhes: Mapped[dict] = mapped_column(JSON, default=dict)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


class Lead(Base):
    __tablename__ = "leads"

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


class Prospect(Base):
    """Empresa localizada por fonte externa, ainda não qualificada como Lead.

    Fase 1 do Radar de Prospecção (03/09/2026, docs/arquitetura-radar-prospeccao-2026-09-03.md):
    só estrutura + CRUD + importação manual + conversão em Lead. Campos de
    fonte/campanha (Fase 2), presença digital (Fase 3), triagem de marca
    (Fase 4) e score (Fase 5) entram por migração própria em cada fase,
    para não carregar colunas que nenhum código ainda preenche.
    """

    __tablename__ = "prospects"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organizacao_id: Mapped[int] = mapped_column(ForeignKey("organizacoes.id", ondelete="CASCADE"), index=True)
    cnpj: Mapped[str | None] = mapped_column(String(18), nullable=True, index=True)
    razao_social: Mapped[str] = mapped_column(String(200), index=True)
    nome_fantasia: Mapped[str | None] = mapped_column(String(200), nullable=True)
    cnae_principal: Mapped[str | None] = mapped_column(String(10), nullable=True, index=True)
    cnaes_secundarios: Mapped[list[str]] = mapped_column(JSON, default=list)
    porte: Mapped[str | None] = mapped_column(String(20), nullable=True)
    situacao_cadastral: Mapped[str | None] = mapped_column(String(20), nullable=True)
    data_abertura: Mapped[date | None] = mapped_column(Date, nullable=True)
    uf: Mapped[str | None] = mapped_column(String(2), nullable=True, index=True)
    cidade: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    endereco: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    telefone: Mapped[str | None] = mapped_column(String(30), nullable=True, index=True)
    email: Mapped[str | None] = mapped_column(String(254), nullable=True, index=True)
    site: Mapped[str | None] = mapped_column(String(200), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default=StatusProspect.NOVO.value, index=True)
    motivo_descarte: Mapped[str | None] = mapped_column(String(30), nullable=True)
    responsavel_id: Mapped[int | None] = mapped_column(
        ForeignKey("usuarios_operacoes.id", ondelete="SET NULL"), nullable=True, index=True
    )
    lead_id: Mapped[int | None] = mapped_column(
        ForeignKey("leads.id", ondelete="SET NULL"), nullable=True, unique=True, index=True
    )
    empresa_crm_id: Mapped[int | None] = mapped_column(
        ForeignKey("empresas_crm.id", ondelete="SET NULL"), nullable=True, index=True
    )
    duplicado_de_id: Mapped[int | None] = mapped_column(
        ForeignKey("prospects.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # Fase 2 do Radar de Prospecção (03/09/2026): de onde veio e em qual
    # campanha de coleta o prospect nasceu -- ambos nulos quando criado
    # manualmente (Fase 1, sem campanha).
    fonte_id: Mapped[int | None] = mapped_column(
        ForeignKey("prospect_fontes.id", ondelete="SET NULL"), nullable=True, index=True
    )
    campanha_id: Mapped[int | None] = mapped_column(
        ForeignKey("campanhas_prospeccao.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # Fase 3 do Radar de Prospecção (03/09/2026): último resultado da
    # verificação de site (histórico completo fica em ProspectEnriquecimento).
    presenca_digital: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # Fase 4 do Radar de Prospecção (03/09/2026): última classificação de
    # triagem de marca -- só indicativa, nunca "disponível" (ver
    # app/prospeccao_triagem.py). Histórico completo em ProspectTriagem.
    triagem_marca_status: Mapped[str | None] = mapped_column(String(30), nullable=True, index=True)
    triagem_marca_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Fase 5 do Radar de Prospecção (03/09/2026): score comercial (0-100,
    # fatores explícitos em score_detalhe -- ver app/prospeccao_score.py).
    score: Mapped[float | None] = mapped_column(Float, nullable=True, index=True)
    score_detalhe: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    score_calculado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    dados_brutos: Mapped[dict] = mapped_column(JSON, default=dict)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    atualizado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    responsavel: Mapped["UsuarioOperacoes | None"] = relationship(foreign_keys=[responsavel_id], lazy="selectin")


class SupressaoProspeccao(Base):
    """Lista de opt-out do Radar de Prospecção (Fase 5, 04/09/2026) -- quem
    pediu pra não ser contatado por prospecção comercial nunca mais entra
    como Prospect nessa organização, mesmo reaparecendo em uma nova
    importação/campanha (RFB, planilha manual). Achado FASE5-5 da auditoria
    (04/09/2026): antes não existia nenhum mecanismo de opt-out para dados de
    prospecção (só para Lead, via app/api/privacidade.py)."""

    __tablename__ = "supressoes_prospeccao"
    __table_args__ = (
        CheckConstraint("cnpj IS NOT NULL OR email IS NOT NULL", name="ck_supressao_prospeccao_identificador"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organizacao_id: Mapped[int] = mapped_column(ForeignKey("organizacoes.id", ondelete="CASCADE"), index=True)
    cnpj: Mapped[str | None] = mapped_column(String(18), nullable=True, index=True)
    email: Mapped[str | None] = mapped_column(String(254), nullable=True, index=True)
    motivo: Mapped[str | None] = mapped_column(String(200), nullable=True)
    criado_por: Mapped[str] = mapped_column(String(150))
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PoliticaProspeccao(Base):
    """Regras de aprovação automática do Radar por organização (Fase 5,
    03/09/2026) -- mesmo padrão de PoliticaCRM. Desligada por padrão: só
    aprova sozinho quando a organização liga explicitamente."""

    __tablename__ = "politicas_prospeccao"
    __table_args__ = (
        UniqueConstraint("organizacao_id", name="uq_politica_prospeccao_organizacao"),
        CheckConstraint(
            "score_minimo_aprovacao IS NULL OR (score_minimo_aprovacao >= 0 AND score_minimo_aprovacao <= 100)",
            name="ck_politica_prospeccao_score_minimo",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organizacao_id: Mapped[int] = mapped_column(ForeignKey("organizacoes.id", ondelete="CASCADE"), index=True)
    aprovacao_automatica_ativa: Mapped[bool] = mapped_column(Boolean, default=False)
    score_minimo_aprovacao: Mapped[int | None] = mapped_column(Integer, nullable=True)
    atualizado_por: Mapped[str | None] = mapped_column(String(254), nullable=True)
    atualizado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ProspectFonte(Base):
    """Catálogo de fontes de coleta de Prospect (Fase 2 do Radar, 03/09/2026)."""

    __tablename__ = "prospect_fontes"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organizacao_id: Mapped[int] = mapped_column(ForeignKey("organizacoes.id", ondelete="CASCADE"), index=True)
    tipo: Mapped[str] = mapped_column(String(30))
    nome: Mapped[str] = mapped_column(String(120))
    configuracao: Mapped[dict] = mapped_column(JSON, default=dict)
    ativo: Mapped[bool] = mapped_column(Boolean, default=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CampanhaProspeccao(Base):
    """Critério de busca que gera prospects em lote (Fase 2 do Radar, 03/09/2026)."""

    __tablename__ = "campanhas_prospeccao"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organizacao_id: Mapped[int] = mapped_column(ForeignKey("organizacoes.id", ondelete="CASCADE"), index=True)
    nome: Mapped[str] = mapped_column(String(120))
    descricao: Mapped[str | None] = mapped_column(Text, nullable=True)
    criterios_busca: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="rascunho", index=True)
    meta_prospects: Mapped[int | None] = mapped_column(Integer, nullable=True)
    criado_por: Mapped[str | None] = mapped_column(String(150), nullable=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    encerrada_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class CacheEstabelecimentoRFB(Base):
    """Cache nacional dos Dados Abertos do CNPJ (Receita Federal).

    NÃO é por tenant -- é dado público, compartilhável entre organizações do
    SaaS, e por isso não tem organizacao_id nem RLS. Alimentado por
    app/cli/importar_cnpj_rfb.py (ETL fora do request-response da aplicação),
    disparado pelo job prospeccao.importar_cnpj_rfb (tela do Radar, restrito
    a superadmin -- ver ImportacaoCnpjRfb) ou manualmente por cron/CLI."""

    __tablename__ = "cache_estabelecimentos_rfb"

    cnpj: Mapped[str] = mapped_column(String(14), primary_key=True)
    razao_social: Mapped[str] = mapped_column(String(200))
    nome_fantasia: Mapped[str | None] = mapped_column(String(200), nullable=True)
    cnae_principal: Mapped[str | None] = mapped_column(String(10), nullable=True, index=True)
    cnaes_secundarios: Mapped[list[str]] = mapped_column(JSON, default=list)
    porte: Mapped[str | None] = mapped_column(String(20), nullable=True)
    situacao_cadastral: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)
    data_abertura: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    uf: Mapped[str | None] = mapped_column(String(2), nullable=True)
    cidade: Mapped[str | None] = mapped_column(String(120), nullable=True)
    telefone: Mapped[str | None] = mapped_column(String(30), nullable=True)
    email: Mapped[str | None] = mapped_column(String(254), nullable=True)
    atualizado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ImportacaoCnpjRfb(Base):
    """Execução do ETL dos Dados Abertos do CNPJ (03/09/2026) -- mesmo padrão
    de RpiSyncExecucao: uma linha por execução, atualizada em commits
    próprios (não só no fim) para o polling da tela do Radar acompanhar o
    progresso em tempo real. Sem RLS -- alimenta um cache global, não por
    tenant; disparo restrito a superadmin (afeta a plataforma inteira)."""

    __tablename__ = "importacoes_cnpj_rfb"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    status: Mapped[str] = mapped_column(String(20), default="executando", index=True)
    periodo: Mapped[str | None] = mapped_column(String(7), nullable=True)
    etapa_atual: Mapped[str | None] = mapped_column(String(200), nullable=True)
    total_processados: Mapped[int] = mapped_column(Integer, default=0)
    total_validos: Mapped[int] = mapped_column(Integer, default=0)
    erro: Mapped[str | None] = mapped_column(Text, nullable=True)
    solicitado_por: Mapped[str | None] = mapped_column(String(150), nullable=True)
    solicitado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    concluido_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ProspectEnriquecimento(Base):
    """Histórico de tentativas de enriquecimento de um Prospect (Fase 3 do
    Radar, 03/09/2026) -- guarda cada execução, não só o último valor
    (que fica também em Prospect.presenca_digital, denormalizado, para
    leitura rápida da ficha)."""

    __tablename__ = "prospect_enriquecimentos"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organizacao_id: Mapped[int] = mapped_column(ForeignKey("organizacoes.id", ondelete="CASCADE"), index=True)
    prospect_id: Mapped[int] = mapped_column(ForeignKey("prospects.id", ondelete="CASCADE"), index=True)
    provedor: Mapped[str] = mapped_column(String(30))
    tipo: Mapped[str] = mapped_column(String(30))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    sucesso: Mapped[bool] = mapped_column(Boolean)
    erro: Mapped[str | None] = mapped_column(String(120), nullable=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


class ProspectTriagem(Base):
    """Histórico de tentativas de triagem de marca de um Prospect (Fase 4 do
    Radar, 03/09/2026) -- SOMENTE indicativo, nunca definitivo (ver
    app/prospeccao_triagem.py). classificacao é restrita por CHECK CONSTRAINT
    às classificações do enum -- "disponível" nunca existe nesse vocabulário."""

    __tablename__ = "prospect_triagens"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organizacao_id: Mapped[int] = mapped_column(ForeignKey("organizacoes.id", ondelete="CASCADE"), index=True)
    prospect_id: Mapped[int] = mapped_column(ForeignKey("prospects.id", ondelete="CASCADE"), index=True)
    marca_pesquisada: Mapped[str] = mapped_column(String(200))
    classificacao: Mapped[str] = mapped_column(String(30))
    justificativa: Mapped[str] = mapped_column(Text)
    total_resultados: Mapped[int] = mapped_column(Integer, default=0)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class HistoricoStatusProspect(Base):
    """Timeline de status do Prospect -- mesmo padrão de HistoricoFaseLead."""

    __tablename__ = "historico_status_prospect"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organizacao_id: Mapped[int] = mapped_column(ForeignKey("organizacoes.id", ondelete="CASCADE"), index=True)
    prospect_id: Mapped[int] = mapped_column(ForeignKey("prospects.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(20))
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
    certificado). Um registro por tipo por lead — sem armazenamento de arquivo."""

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

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organizacao_id: Mapped[int] = mapped_column(ForeignKey("organizacoes.id", ondelete="CASCADE"), index=True)
    proposta_id: Mapped[int] = mapped_column(ForeignKey("propostas_comerciais.id", ondelete="CASCADE"), index=True)
    versao: Mapped[int] = mapped_column(Integer)
    hash_documento: Mapped[str] = mapped_column(String(64), index=True)
    cliente_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, index=True)
    ip_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    assinado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    provedor: Mapped[str] = mapped_column(String(30), default="interno")


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


class CanalContato(StrEnum):
    TELEFONE = "telefone"
    EMAIL = "email"
    WHATSAPP = "whatsapp"
    REUNIAO = "reuniao"
    OUTRO = "outro"


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


class PesquisaMarca(Base):
    __tablename__ = "pesquisas_marca"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    organizacao_id: Mapped[int] = mapped_column(ForeignKey("organizacoes.id", ondelete="RESTRICT"), index=True)
    lead_id: Mapped[int | None] = mapped_column(ForeignKey("leads.id", ondelete="SET NULL"), nullable=True, index=True)
    empresa_id: Mapped[int | None] = mapped_column(
        ForeignKey("empresas_crm.id", ondelete="SET NULL"), nullable=True, index=True
    )
    marca: Mapped[str] = mapped_column(String(200), index=True)
    atividade: Mapped[str | None] = mapped_column(Text, nullable=True)
    tipo_pesquisa: Mapped[str] = mapped_column(String(20), index=True)
    classe_nice: Mapped[str | None] = mapped_column(String(2), nullable=True, index=True)
    duplicada: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    pesquisa_original_id: Mapped[str | None] = mapped_column(
        ForeignKey("pesquisas_marca.id", ondelete="SET NULL"), nullable=True, index=True
    )
    dados_complementares_registrabilidade: Mapped[dict] = mapped_column(JSON, default=dict)
    analysis_state: Mapped[str] = mapped_column(String(30), default="DRAFT", server_default="DRAFT", index=True)
    validated_by: Mapped[str | None] = mapped_column(String(150), nullable=True)
    validated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    analysis_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    relatorio_completo_gerado_em: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    relatorio_completo_gerado_por: Mapped[str | None] = mapped_column(String(150), nullable=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    lead: Mapped[Lead | None] = relationship(back_populates="pesquisas_marca")
    empresa_registro: Mapped[EmpresaCRM | None] = relationship(back_populates="pesquisas")
    contatos: Mapped[list[ContatoLead]] = relationship(back_populates="pesquisa")
    versoes_relatorio: Mapped[list["VersaoRelatorioMarca"]] = relationship(
        back_populates="pesquisa",
        cascade="all, delete-orphan",
        order_by="VersaoRelatorioMarca.numero_versao.desc()",
    )


class SolicitacaoExclusaoPesquisa(Base):
    __tablename__ = "solicitacoes_exclusao_pesquisa"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organizacao_id: Mapped[int] = mapped_column(ForeignKey("organizacoes.id", ondelete="CASCADE"), index=True)
    pesquisa_id: Mapped[str | None] = mapped_column(
        ForeignKey("pesquisas_marca.id", ondelete="SET NULL"), nullable=True, index=True
    )
    pesquisa_referencia: Mapped[str] = mapped_column(String(36), index=True)
    marca: Mapped[str] = mapped_column(String(200), index=True)
    lead_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, index=True)
    solicitado_por_id: Mapped[int | None] = mapped_column(
        ForeignKey("usuarios_operacoes.id", ondelete="SET NULL"), nullable=True, index=True
    )
    solicitado_por: Mapped[str] = mapped_column(String(254))
    motivo: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="pendente", index=True)
    decidido_por_id: Mapped[int | None] = mapped_column(
        ForeignKey("usuarios_operacoes.id", ondelete="SET NULL"), nullable=True, index=True
    )
    decidido_por: Mapped[str | None] = mapped_column(String(254), nullable=True)
    decisao_observacao: Mapped[str | None] = mapped_column(Text, nullable=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    decidido_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class MarcaAltoRenome(Base):
    __tablename__ = "marcas_alto_renome"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    numero_processo_normalizado: Mapped[str] = mapped_column(String(30), unique=True, index=True)
    marca: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    vigente: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    fonte_url: Mapped[str] = mapped_column(Text)
    fonte_atualizada_em: Mapped[date | None] = mapped_column(Date, nullable=True)
    sincronizado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class AfinidadeClasse(Base):
    __tablename__ = "afinidades_classes"
    __table_args__ = (
        UniqueConstraint(
            "classe_origem",
            "classe_destino",
            name="uq_afinidades_classes_par",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    classe_origem: Mapped[str] = mapped_column(String(2), index=True)
    classe_destino: Mapped[str] = mapped_column(String(2), index=True)
    nivel: Mapped[str] = mapped_column(String(20))
    justificativa: Mapped[str] = mapped_column(Text)
    versao: Mapped[str] = mapped_column(String(20), default="inicial-2026")
    status_revisao: Mapped[str] = mapped_column(String(20), default="pendente", index=True)
    revisor: Mapped[str | None] = mapped_column(String(150), nullable=True)
    observacoes_revisao: Mapped[str | None] = mapped_column(Text, nullable=True)
    revisado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AfinidadeViena(Base):
    """Afinidade entre códigos da Classificação de Viena (elementos figurativos)."""

    __tablename__ = "afinidades_viena"
    __table_args__ = (UniqueConstraint("codigo_origem", "codigo_destino", name="uq_afinidades_viena_par"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    codigo_origem: Mapped[str] = mapped_column(String(30), index=True)
    codigo_destino: Mapped[str] = mapped_column(String(30), index=True)
    nivel: Mapped[str] = mapped_column(String(20))
    justificativa: Mapped[str] = mapped_column(Text)
    versao: Mapped[str] = mapped_column(String(20), default="inicial-2026")
    status_revisao: Mapped[str] = mapped_column(String(20), default="pendente", index=True)
    revisor: Mapped[str | None] = mapped_column(String(150), nullable=True)
    observacoes_revisao: Mapped[str | None] = mapped_column(Text, nullable=True)
    revisado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AvaliacaoRiscoMarca(Base):
    __tablename__ = "avaliacoes_risco_marca"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    pesquisa_id: Mapped[str] = mapped_column(
        ForeignKey("pesquisas_marca.id", ondelete="CASCADE"),
        unique=True,
        index=True,
    )
    versao_motor: Mapped[str] = mapped_column(String(30), index=True)
    modo: Mapped[str] = mapped_column(String(20), default="sombra", index=True)
    pontuacao: Mapped[int] = mapped_column(Integer)
    nivel: Mapped[str] = mapped_column(String(20), index=True)
    principais_conflitos: Mapped[list[dict]] = mapped_column(JSON)
    regras_aplicadas: Mapped[dict] = mapped_column(JSON)
    calculado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    nivel_humano: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)
    avaliador: Mapped[str | None] = mapped_column(String(150), nullable=True)
    observacoes_humanas: Mapped[str | None] = mapped_column(Text, nullable=True)
    avaliado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    explicacao_ia: Mapped["ExplicacaoRiscoIA | None"] = relationship(
        back_populates="avaliacao",
        cascade="all, delete-orphan",
        uselist=False,
    )


class RotuloHistoricoMarca(Base):
    __tablename__ = "rotulos_historicos_marca"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    processo_id: Mapped[int] = mapped_column(ForeignKey("processos.id", ondelete="CASCADE"), unique=True, index=True)
    rotulo: Mapped[str] = mapped_column(String(30), index=True)
    alvo_deferimento: Mapped[bool] = mapped_column(Boolean, index=True)
    fundamento: Mapped[str] = mapped_column(String(50), index=True)
    origem: Mapped[str] = mapped_column(String(30), default="rpi_automatica", index=True)
    confianca: Mapped[float] = mapped_column(Float, default=1.0)
    data_referencia: Mapped[date] = mapped_column(Date, index=True)
    numero_rpi: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    despacho_codigo: Mapped[str | None] = mapped_column(String(30), nullable=True)
    despacho_descricao: Mapped[str | None] = mapped_column(Text, nullable=True)
    data_decisao: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    tipo_decisao: Mapped[str] = mapped_column(String(30), default="merito", index=True)
    elegivel_treinamento: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    motivo_inelegibilidade: Mapped[str | None] = mapped_column(String(100), nullable=True)
    classificador_versao: Mapped[str] = mapped_column(String(30), default="rotulo-marcario-1.0", index=True)
    evidencias_classificacao: Mapped[list[dict]] = mapped_column(JSON, default=list)
    status_revisao: Mapped[str] = mapped_column(String(20), default="pendente", index=True)
    revisor: Mapped[str | None] = mapped_column(String(150), nullable=True)
    observacoes_revisao: Mapped[str | None] = mapped_column(Text, nullable=True)
    revisado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    atualizado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class EvidenciaDecisaoMarca(Base):
    __tablename__ = "evidencias_decisoes_marca"
    __table_args__ = (UniqueConstraint("rotulo_id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    rotulo_id: Mapped[int] = mapped_column(
        ForeignKey("rotulos_historicos_marca.id", ondelete="CASCADE"),
        index=True,
    )
    processo_numero: Mapped[str] = mapped_column(String(30), index=True)
    cod_pedido: Mapped[str | None] = mapped_column(String(30), nullable=True)
    fonte_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    status_coleta: Mapped[str] = mapped_column(String(30), default="pendente", index=True)
    status_http: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tentativas: Mapped[int] = mapped_column(Integer, default=0)
    erro: Mapped[str | None] = mapped_column(Text, nullable=True)
    despacho_texto: Mapped[str | None] = mapped_column(Text, nullable=True)
    numero_rpi: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    fundamento_sugerido: Mapped[str | None] = mapped_column(String(50), nullable=True, index=True)
    confianca: Mapped[float | None] = mapped_column(Float, nullable=True)
    artigos: Mapped[list[str]] = mapped_column(JSON, default=list)
    processos_citados: Mapped[list[str]] = mapped_column(JSON, default=list)
    hash_conteudo: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    classificador_versao: Mapped[str] = mapped_column(String(40), default="fundamento-inpi-1.0", index=True)
    coletado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    atualizado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ParTreinamentoMarca(Base):
    __tablename__ = "pares_treinamento_marca"
    __table_args__ = (
        UniqueConstraint("rotulo_id", "processo_candidato_id", name="uq_par_treinamento_rotulo_candidato"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    rotulo_id: Mapped[int] = mapped_column(ForeignKey("rotulos_historicos_marca.id", ondelete="CASCADE"), index=True)
    processo_candidato_id: Mapped[int] = mapped_column(ForeignKey("processos.id", ondelete="CASCADE"), index=True)
    atributos: Mapped[dict] = mapped_column(JSON)
    alvo_conflito: Mapped[bool | None] = mapped_column(Boolean, nullable=True, index=True)
    origem: Mapped[str] = mapped_column(String(30), default="candidato_temporal")
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ModeloRegistrabilidade(Base):
    __tablename__ = "modelos_registrabilidade"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    versao: Mapped[str] = mapped_column(String(60), unique=True, index=True)
    algoritmo: Mapped[str] = mapped_column(String(50), default="regressao_logistica")
    status: Mapped[str] = mapped_column(String(20), default="SHADOW", index=True)
    atributos: Mapped[list[str]] = mapped_column(JSON)
    parametros: Mapped[dict] = mapped_column(JSON)
    calibracao: Mapped[dict] = mapped_column(JSON)
    metricas: Mapped[dict] = mapped_column(JSON)
    dataset: Mapped[dict] = mapped_column(JSON)
    corte_treino: Mapped[date | None] = mapped_column(Date, nullable=True)
    corte_validacao: Mapped[date | None] = mapped_column(Date, nullable=True)
    treinado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    ativado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ativado_por: Mapped[str | None] = mapped_column(String(150), nullable=True)


class ModeloRankingBusca(Base):
    """Versão publicada do ranking nominativo/visual, separada do risco jurídico."""

    __tablename__ = "modelos_ranking_busca"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organizacao_id: Mapped[int] = mapped_column(ForeignKey("organizacoes.id", ondelete="CASCADE"), index=True)
    versao: Mapped[str] = mapped_column(String(60), unique=True, index=True)
    algoritmo: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(20), default="SHADOW", index=True)
    parametros: Mapped[dict] = mapped_column(JSON, default=dict)
    metricas: Mapped[dict] = mapped_column(JSON, default=dict)
    evidencias: Mapped[dict] = mapped_column(JSON, default=dict)
    dataset_version: Mapped[str | None] = mapped_column(String(80), nullable=True)
    bloqueado_motivo: Mapped[str | None] = mapped_column(Text, nullable=True)
    publicado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    publicado_por: Mapped[str | None] = mapped_column(String(150), nullable=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


class ProjetoBuscaMarca(Base):
    """Projeto versionado de anterioridade, isolado por organização."""

    __tablename__ = "projetos_busca_marca"
    __table_args__ = (UniqueConstraint("organizacao_id", "slug", name="uq_projeto_busca_org_slug"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organizacao_id: Mapped[int] = mapped_column(ForeignKey("organizacoes.id", ondelete="CASCADE"), index=True)
    nome: Mapped[str] = mapped_column(String(160))
    slug: Mapped[str] = mapped_column(String(180), index=True)
    consulta: Mapped[dict] = mapped_column(JSON, default=dict)
    versao_busca: Mapped[str] = mapped_column(String(60), default="busca-marcas-4.0")
    status: Mapped[str] = mapped_column(String(20), default="ativo", index=True)
    ultima_execucao: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    executado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    criado_por: Mapped[str | None] = mapped_column(String(150), nullable=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    atualizado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class PrevisaoRegistrabilidade(Base):
    __tablename__ = "previsoes_registrabilidade"
    __table_args__ = (
        UniqueConstraint("pesquisa_id", "modelo_id", name="uq_previsao_registrabilidade_pesquisa_modelo"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    pesquisa_id: Mapped[str] = mapped_column(ForeignKey("pesquisas_marca.id", ondelete="CASCADE"), index=True)
    modelo_id: Mapped[int] = mapped_column(ForeignKey("modelos_registrabilidade.id", ondelete="RESTRICT"), index=True)
    modo: Mapped[str] = mapped_column(String(20), default="sombra", index=True)
    probabilidade_deferimento: Mapped[float] = mapped_column(Float)
    probabilidade_inferior: Mapped[float | None] = mapped_column(Float, nullable=True)
    probabilidade_superior: Mapped[float | None] = mapped_column(Float, nullable=True)
    nivel: Mapped[str] = mapped_column(String(30), index=True)
    confianca: Mapped[float] = mapped_column(Float)
    confianca_rotulo: Mapped[str] = mapped_column(String(20), default="baixa")
    cobertura_entrada: Mapped[float] = mapped_column(Float, default=0.0)
    elegivel_cliente: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    motivos_inelegibilidade: Mapped[list[str]] = mapped_column(JSON, default=list)
    escopo_estimativa: Mapped[str] = mapped_column(String(50), default="deferimento_exame_merito")
    amostras_referencia: Mapped[int] = mapped_column(Integer, default=0)
    corte_dados: Mapped[date | None] = mapped_column(Date, nullable=True)
    atributos: Mapped[dict] = mapped_column(JSON)
    fatores_principais: Mapped[list[dict]] = mapped_column(JSON)
    nivel_humano: Mapped[str | None] = mapped_column(String(30), nullable=True, index=True)
    avaliador: Mapped[str | None] = mapped_column(String(150), nullable=True)
    observacoes_humanas: Mapped[str | None] = mapped_column(Text, nullable=True)
    avaliado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    calculado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ExecucaoAgenteRegistrabilidade(Base):
    """Snapshot auditavel da decisao produzida pelo orquestrador de registrabilidade."""

    __tablename__ = "execucoes_agente_registrabilidade"
    __table_args__ = (UniqueConstraint("pesquisa_id", "hash_entrada", name="uq_agente_registrabilidade_pesquisa_hash"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organizacao_id: Mapped[int] = mapped_column(ForeignKey("organizacoes.id", ondelete="RESTRICT"), index=True)
    pesquisa_id: Mapped[str] = mapped_column(ForeignKey("pesquisas_marca.id", ondelete="CASCADE"), index=True)
    versao_agente: Mapped[str] = mapped_column(String(40), index=True)
    hash_entrada: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(30), index=True)
    decisao: Mapped[str] = mapped_column(String(40), index=True)
    abstencao: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    cobertura: Mapped[float] = mapped_column(Float, default=0.0)
    confianca: Mapped[float | None] = mapped_column(Float, nullable=True)
    probabilidade_deferimento: Mapped[float | None] = mapped_column(Float, nullable=True)
    probabilidade_inferior: Mapped[float | None] = mapped_column(Float, nullable=True)
    probabilidade_superior: Mapped[float | None] = mapped_column(Float, nullable=True)
    nivel_risco: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)
    pontuacao_risco: Mapped[int | None] = mapped_column(Integer, nullable=True)
    motivos: Mapped[list[str]] = mapped_column(JSON, default=list)
    fatores_principais: Mapped[list[dict]] = mapped_column(JSON, default=list)
    entrada_estruturada: Mapped[dict] = mapped_column(JSON)
    evidencias: Mapped[dict] = mapped_column(JSON)
    regras: Mapped[dict] = mapped_column(JSON)
    versoes_fontes: Mapped[dict] = mapped_column(JSON)
    numero_pedido: Mapped[str | None] = mapped_column(String(50), nullable=True, index=True)
    resultado_real: Mapped[str | None] = mapped_column(String(30), nullable=True, index=True)
    resultado_fundamento: Mapped[str | None] = mapped_column(String(50), nullable=True)
    resultado_data_referencia: Mapped[date | None] = mapped_column(Date, nullable=True)
    resultado_numero_rpi: Mapped[int | None] = mapped_column(Integer, nullable=True)
    resultado_sincronizado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


class ControleAprendizadoMarca(Base):
    __tablename__ = "controle_aprendizado_marca"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    inferencia_habilitada: Mapped[bool] = mapped_column(Boolean, default=True)
    rollout_percentual: Mapped[int] = mapped_column(Integer, default=100)
    exibir_cliente: Mapped[bool] = mapped_column(Boolean, default=True)
    minimo_revisoes_humanas: Mapped[int] = mapped_column(Integer, default=30)
    minimo_recall: Mapped[float] = mapped_column(Float, default=0.80)
    minimo_especificidade: Mapped[float] = mapped_column(Float, default=0.70)
    maximo_brier: Mapped[float] = mapped_column(Float, default=0.25)
    maximo_ece: Mapped[float] = mapped_column(Float, default=0.12)
    minimo_amostras_modelo: Mapped[int] = mapped_column(Integer, default=300)
    minimo_amostras_teste: Mapped[int] = mapped_column(Integer, default=50)
    largura_maxima_intervalo: Mapped[float] = mapped_column(Float, default=0.35)
    minima_cobertura: Mapped[float] = mapped_column(Float, default=0.40)
    atualizado_por: Mapped[str | None] = mapped_column(String(150), nullable=True)
    justificativa: Mapped[str | None] = mapped_column(Text, nullable=True)
    atualizado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ExecucaoAprendizadoMarca(Base):
    __tablename__ = "execucoes_aprendizado_marca"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    tipo: Mapped[str] = mapped_column(String(30), index=True)
    status: Mapped[str] = mapped_column(String(30), index=True)
    solicitado_por: Mapped[str | None] = mapped_column(String(150), nullable=True)
    rotulos_processados: Mapped[int] = mapped_column(Integer, default=0)
    pares_processados: Mapped[int] = mapped_column(Integer, default=0)
    modelo_id: Mapped[int | None] = mapped_column(
        ForeignKey("modelos_registrabilidade.id", ondelete="SET NULL"), nullable=True
    )
    metricas: Mapped[dict] = mapped_column(JSON, default=dict)
    mensagem: Mapped[str | None] = mapped_column(Text, nullable=True)
    erro: Mapped[str | None] = mapped_column(Text, nullable=True)
    iniciado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finalizado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ExplicacaoRiscoIA(Base):
    __tablename__ = "explicacoes_risco_ia"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    avaliacao_risco_id: Mapped[int] = mapped_column(
        ForeignKey("avaliacoes_risco_marca.id", ondelete="CASCADE"),
        unique=True,
        index=True,
    )
    provedor: Mapped[str] = mapped_column(String(30), default="openai")
    modelo: Mapped[str] = mapped_column(String(100))
    versao_prompt: Mapped[str] = mapped_column(String(30))
    hash_entrada: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(30), index=True)
    entrada_estruturada: Mapped[dict] = mapped_column(JSON)
    saida_estruturada: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    resposta_provedor_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    duracao_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    erro: Mapped[str | None] = mapped_column(Text, nullable=True)
    revisao_obrigatoria: Mapped[bool] = mapped_column(Boolean, default=False)
    decisao_revisao: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)
    revisor: Mapped[str | None] = mapped_column(String(150), nullable=True)
    observacoes_revisao: Mapped[str | None] = mapped_column(Text, nullable=True)
    gerado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revisado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    atualizado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    avaliacao: Mapped[AvaliacaoRiscoMarca] = relationship(back_populates="explicacao_ia")


class VersaoRelatorioMarca(Base):
    __tablename__ = "versoes_relatorio_marca"
    __table_args__ = (
        UniqueConstraint(
            "pesquisa_id",
            "numero_versao",
            name="uq_versoes_relatorio_marca_pesquisa_numero",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    pesquisa_id: Mapped[str] = mapped_column(
        ForeignKey("pesquisas_marca.id", ondelete="CASCADE"),
        index=True,
    )
    numero_versao: Mapped[int] = mapped_column(Integer)
    schema_versao: Mapped[str] = mapped_column(String(30), index=True)
    conteudo_hash: Mapped[str] = mapped_column(String(64), index=True)
    payload: Mapped[dict] = mapped_column(JSON)
    validated_by: Mapped[str | None] = mapped_column(String(150), nullable=True)
    validated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    validation_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    gerado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)

    pesquisa: Mapped[PesquisaMarca] = relationship(back_populates="versoes_relatorio")


class EventoOperacional(Base):
    __tablename__ = "eventos_operacionais"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    componente: Mapped[str] = mapped_column(String(40), index=True)
    operacao: Mapped[str] = mapped_column(String(150), index=True)
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True, default=request_id_atual)
    sucesso: Mapped[bool] = mapped_column(Boolean, index=True)
    duracao_ms: Mapped[int] = mapped_column(Integer)
    status_http: Mapped[int] = mapped_column(Integer, index=True)
    codigo_erro: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    detalhes: Mapped[dict] = mapped_column(JSON, default=dict)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


class EventoAuditoria(Base):
    __tablename__ = "eventos_auditoria"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organizacao_id: Mapped[int | None] = mapped_column(
        ForeignKey("organizacoes.id", ondelete="SET NULL"), nullable=True, index=True
    )
    actor_id: Mapped[int | None] = mapped_column(
        ForeignKey("usuarios_operacoes.id", ondelete="SET NULL"), nullable=True, index=True
    )
    ator: Mapped[str] = mapped_column(String(150), index=True)
    acao: Mapped[str] = mapped_column(String(20), index=True)
    recurso: Mapped[str] = mapped_column(String(180), index=True)
    resource_type: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    resource_id: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True, default=request_id_atual)
    sucesso: Mapped[bool] = mapped_column(Boolean, index=True)
    status_http: Mapped[int] = mapped_column(Integer)
    ip_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    detalhes: Mapped[dict] = mapped_column(JSON)
    before_state: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    after_state: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


class VersaoSistema(Base):
    """Release global da plataforma, imutável depois da publicação."""

    __tablename__ = "versoes_sistema"
    # eager_defaults evita MissingGreenlet: sem isso, "atualizado_em" (onupdate=func.now())
    # fica marcado como expirado apos o UPDATE, e o proximo acesso sincrono ao atributo
    # (fora de um `await session.X()`) tenta um lazy load fora do bridge async/greenlet.
    __mapper_args__ = {"eager_defaults": True}
    __table_args__ = (
        CheckConstraint(
            "tipo_atualizacao IN ('critica', 'correcao', 'melhoria', 'funcionalidade')",
            name="ck_versao_sistema_tipo",
        ),
        CheckConstraint(
            "status IN ('rascunho', 'publicada', 'arquivada')",
            name="ck_versao_sistema_status",
        ),
        CheckConstraint("length(commit_sha) = 40", name="ck_versao_sistema_commit_sha"),
        CheckConstraint("length(conteudo_hash) = 64", name="ck_versao_sistema_hash"),
        CheckConstraint(
            "status = 'rascunho' OR "
            "(implantada_em IS NOT NULL AND publicado_em IS NOT NULL AND publicado_por IS NOT NULL)",
            name="ck_versao_sistema_publicacao",
        ),
        CheckConstraint(
            "status <> 'arquivada' OR "
            "(arquivado_em IS NOT NULL AND arquivado_por IS NOT NULL AND arquivamento_motivo IS NOT NULL)",
            name="ck_versao_sistema_arquivamento",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    versao: Mapped[str] = mapped_column(String(60), unique=True, index=True)
    titulo: Mapped[str] = mapped_column(String(180))
    problema_identificado: Mapped[str] = mapped_column(Text)
    solucao_aplicada: Mapped[str] = mapped_column(Text)
    impacto_usuario: Mapped[str | None] = mapped_column(Text, nullable=True)
    documentacao_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    permite_adiar: Mapped[bool] = mapped_column(Boolean, default=False)
    tipo_atualizacao: Mapped[str] = mapped_column(String(30), index=True)
    modulos_afetados: Mapped[list[str]] = mapped_column(JSON, default=list)
    implantada_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    commit_sha: Mapped[str] = mapped_column(String(40), index=True)
    migration_revision: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    evidencias_testes: Mapped[list[dict]] = mapped_column(JSON, default=list)
    riscos_conhecidos: Mapped[list[str]] = mapped_column(JSON, default=list)
    instrucoes: Mapped[str] = mapped_column(Text)
    plano_rollback: Mapped[str] = mapped_column(Text)
    conteudo_hash: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(20), default="rascunho", index=True)
    criado_por_id: Mapped[int | None] = mapped_column(
        ForeignKey("usuarios_operacoes.id", ondelete="SET NULL"), nullable=True, index=True
    )
    criado_por: Mapped[str] = mapped_column(String(254))
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    atualizado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    publicado_por_id: Mapped[int | None] = mapped_column(
        ForeignKey("usuarios_operacoes.id", ondelete="SET NULL"), nullable=True, index=True
    )
    publicado_por: Mapped[str | None] = mapped_column(String(254), nullable=True)
    publicado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    arquivado_por_id: Mapped[int | None] = mapped_column(
        ForeignKey("usuarios_operacoes.id", ondelete="SET NULL"), nullable=True, index=True
    )
    arquivado_por: Mapped[str | None] = mapped_column(String(254), nullable=True)
    arquivado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    arquivamento_motivo: Mapped[str | None] = mapped_column(Text, nullable=True)


class InteracaoVersaoSistema(Base):
    """Estado de leitura de uma versão, isolado por usuário e organização."""

    __tablename__ = "interacoes_versoes_sistema"
    __table_args__ = (
        UniqueConstraint(
            "versao_sistema_id", "organizacao_id", "usuario_id", name="uq_interacao_versao_usuario"
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    versao_sistema_id: Mapped[int] = mapped_column(
        ForeignKey("versoes_sistema.id", ondelete="RESTRICT"), index=True
    )
    organizacao_id: Mapped[int] = mapped_column(ForeignKey("organizacoes.id", ondelete="CASCADE"), index=True)
    usuario_id: Mapped[int] = mapped_column(
        ForeignKey("usuarios_operacoes.id", ondelete="CASCADE"), index=True
    )
    confirmado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    adiado_ate: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    atualizado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ProblemaVersaoSistema(Base):
    """Relato do operador sobre uma atualização (Fase 3), estruturado como
    um reporte de bug de verdade desde a Fase 6: etapas de reprodução,
    resultado esperado x encontrado, gravidade e anexo opcional (print,
    log). Organização, usuário e versão são sempre identificados
    automaticamente pelo backend (contexto da sessão + versão do card
    clicado) -- nunca digitados pelo operador. O anexo nunca é capturado
    automaticamente (sem screenshot/DOM/console/localStorage do backend);
    é sempre um arquivo que o operador escolhe explicitamente, varrido por
    antivírus (app.malware_scan) antes de persistir."""

    __tablename__ = "problemas_versoes_sistema"
    __table_args__ = (
        CheckConstraint("categoria IN ('erro', 'duvida', 'regressao')", name="ck_problema_versao_categoria"),
        CheckConstraint("status IN ('aberto', 'em_analise', 'resolvido')", name="ck_problema_versao_status"),
        CheckConstraint(
            "gravidade IN ('baixa', 'media', 'alta', 'critica')", name="ck_problema_versao_gravidade"
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    versao_sistema_id: Mapped[int] = mapped_column(
        ForeignKey("versoes_sistema.id", ondelete="RESTRICT"), index=True
    )
    organizacao_id: Mapped[int] = mapped_column(ForeignKey("organizacoes.id", ondelete="CASCADE"), index=True)
    usuario_id: Mapped[int] = mapped_column(
        ForeignKey("usuarios_operacoes.id", ondelete="RESTRICT"), index=True
    )
    categoria: Mapped[str] = mapped_column(String(20))
    modulo: Mapped[str | None] = mapped_column(String(60), nullable=True)
    descricao: Mapped[str] = mapped_column(Text)
    etapas_reproduzir: Mapped[str | None] = mapped_column(Text, nullable=True)
    resultado_esperado: Mapped[str | None] = mapped_column(Text, nullable=True)
    resultado_encontrado: Mapped[str | None] = mapped_column(Text, nullable=True)
    gravidade: Mapped[str] = mapped_column(String(20), default="media", index=True)
    anexo_nome: Mapped[str | None] = mapped_column(String(255), nullable=True)
    anexo_caminho: Mapped[str | None] = mapped_column(String(500), nullable=True)
    anexo_content_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    anexo_tamanho: Mapped[int | None] = mapped_column(Integer, nullable=True)
    anexo_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="aberto", index=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


class FeatureFlag(Base):
    """Fase 4: ativação controlada por organização, só para funcionalidades
    novas e compatíveis com a flag desligada -- nunca para correção de
    segurança (essas sempre são deploy normal, nunca opt-in; ver
    confirmar_nao_e_correcao_seguranca em app.api.feature_flags).

    Regras de produto (decisão do usuário, não impostas pelo schema):
    migrations associadas a uma flag nunca podem depender do estado dela
    (rodam sempre, incondicionalmente); API e banco continuam funcionando
    normalmente com a flag desligada; o backend valida a flag em cada
    chamada (ver app.feature_flags.exigir_feature_ativa) -- ocultar só a
    interface nunca é suficiente."""

    __tablename__ = "feature_flags"
    # eager_defaults evita MissingGreenlet em colunas com onupdate=func.now()
    # apos um UPDATE seguido de leitura sincrona (ver app/models.py,
    # VersaoSistema, e o incidente de producao documentado no commit que
    # corrigiu isso).
    __mapper_args__ = {"eager_defaults": True}
    __table_args__ = (
        CheckConstraint(
            "estado_padrao IN ('desligado', 'somente_administradores', 'ligado')",
            name="ck_feature_flag_estado_padrao",
        ),
        CheckConstraint(
            "estagio_rollout IN "
            "('ambiente_interno', 'administradores', 'organizacoes_piloto', "
            "'percentual_limitado', 'liberacao_geral')",
            name="ck_feature_flag_estagio_rollout",
        ),
        CheckConstraint(
            "percentual_rollout >= 0 AND percentual_rollout <= 100", name="ck_feature_flag_percentual_rollout"
        ),
        CheckConstraint(
            "limite_taxa_erro IS NULL OR (limite_taxa_erro > 0 AND limite_taxa_erro <= 1)",
            name="ck_feature_flag_limite_taxa_erro",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    codigo: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    nome: Mapped[str] = mapped_column(String(180))
    descricao: Mapped[str] = mapped_column(Text)
    modulos_envolvidos: Mapped[list[str]] = mapped_column(JSON, default=list)
    # Códigos de outras FeatureFlag.codigo que precisam estar ativas para
    # esta fazer sentido (checado na criação/edição, não força ativação em
    # cascata das dependências).
    dependencias: Mapped[list[str]] = mapped_column(JSON, default=list)
    estado_padrao: Mapped[str] = mapped_column(String(30), default="desligado")
    # Kill-switch global -- desligar aqui derruba a flag para TODAS as
    # organizações, mesmo as com estado "ativo" explícito. Diferente do
    # "desativado" por organização (app.models.FeatureFlagOrganizacao), que
    # é uma decisão pontual daquela organização.
    ativo: Mapped[bool] = mapped_column(Boolean, default=True)
    responsavel_id: Mapped[int | None] = mapped_column(
        ForeignKey("usuarios_operacoes.id", ondelete="SET NULL"), nullable=True
    )
    data_ativacao: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    data_expiracao: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Fase 5: estagio de rollout, usado somente quando estado_padrao ==
    # "ligado" -- ver app.feature_flags para a avaliacao. Padrao
    # "liberacao_geral" preserva o comportamento de sempre (ligado = todo
    # mundo) para quem nao usa liberacao gradual.
    estagio_rollout: Mapped[str] = mapped_column(String(30), default="liberacao_geral")
    percentual_rollout: Mapped[int] = mapped_column(Integer, default=100)
    # Circuito de interrupcao automatica (app.feature_flags.avaliar_circuito_flags).
    # None desativa o circuito para esta flag.
    limite_taxa_erro: Mapped[float | None] = mapped_column(nullable=True)
    limite_eventos_minimo: Mapped[int] = mapped_column(Integer, default=20)
    pausado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    pausado_motivo: Mapped[str | None] = mapped_column(Text, nullable=True)
    pausado_por: Mapped[str | None] = mapped_column(String(254), nullable=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    atualizado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class FeatureFlagEvento(Base):
    """Fase 5: telemetria de uso/erro por grupo de rollout, para o
    monitoramento por grupo e para o circuito de interrupcao automatica.
    Tabela global de observabilidade, sem RLS -- mesmo padrao de
    EventoOperacional (nao e dado de cliente, e telemetria interna)."""

    __tablename__ = "feature_flags_eventos"
    __table_args__ = (
        CheckConstraint(
            "grupo IN ('ambiente_interno', 'administradores', 'organizacoes_piloto', "
            "'percentual_limitado', 'liberacao_geral')",
            name="ck_feature_flag_evento_grupo",
        ),
        CheckConstraint("tipo IN ('uso', 'erro', 'falha_integracao')", name="ck_feature_flag_evento_tipo"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    feature_flag_id: Mapped[int] = mapped_column(ForeignKey("feature_flags.id", ondelete="CASCADE"), index=True)
    organizacao_id: Mapped[int | None] = mapped_column(
        ForeignKey("organizacoes.id", ondelete="SET NULL"), nullable=True, index=True
    )
    grupo: Mapped[str] = mapped_column(String(30), index=True)
    tipo: Mapped[str] = mapped_column(String(20), index=True)
    duracao_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    detalhes: Mapped[dict] = mapped_column(JSON, default=dict)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


class FeatureFlagOrganizacao(Base):
    """Estado de uma FeatureFlag numa organização específica -- autorização
    granular (achado do critério de aceite: uma organização testa sem
    afetar as demais). Sem registro aqui para o par (flag, organização), a
    organização usa FeatureFlag.estado_padrao."""

    __tablename__ = "feature_flags_organizacoes"
    __table_args__ = (
        UniqueConstraint("feature_flag_id", "organizacao_id", name="uq_feature_flag_organizacao"),
        CheckConstraint(
            "estado IN ('ativo', 'somente_administradores', 'adiado', 'desativado')",
            name="ck_feature_flag_org_estado",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    feature_flag_id: Mapped[int] = mapped_column(ForeignKey("feature_flags.id", ondelete="CASCADE"), index=True)
    organizacao_id: Mapped[int] = mapped_column(ForeignKey("organizacoes.id", ondelete="CASCADE"), index=True)
    estado: Mapped[str] = mapped_column(String(30))
    adiado_ate: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    responsavel_id: Mapped[int | None] = mapped_column(
        ForeignKey("usuarios_operacoes.id", ondelete="SET NULL"), nullable=True
    )
    atualizado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ProcessoHeartbeat(Base):
    """Fase 7 (painel técnico): liveness de processos de fundo que não têm
    endpoint HTTP próprio para checar. `processo` é a chave (ex.: "worker")
    -- upsert por process, uma linha cada. O RPI Sync já tem seu próprio
    heartbeat dedicado (RpiSyncEstado, mais rico -- rpi_atual etc.), não
    precisa duplicar aqui; API é verificada ao vivo pelo próprio fato de
    responder a chamada. Só o worker (consumidor de fila em loop, sem
    endpoint HTTP) precisava de um jeito de provar que está vivo."""

    __tablename__ = "processos_heartbeat"
    __mapper_args__ = {"eager_defaults": True}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    processo: Mapped[str] = mapped_column(String(30), unique=True, index=True)
    heartbeat_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ControleProducao(Base):
    __tablename__ = "controle_producao"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    ia_habilitada: Mapped[bool] = mapped_column(Boolean, default=False)
    ia_rollout_percentual: Mapped[int] = mapped_column(Integer, default=0)
    atualizado_por: Mapped[str | None] = mapped_column(String(150), nullable=True)
    justificativa: Mapped[str | None] = mapped_column(Text, nullable=True)
    atualizado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ReciboFinanceiro(Base):
    __tablename__ = "recibos_financeiros"
    __table_args__ = (UniqueConstraint("organizacao_id", "parcela_id", name="uq_recibo_financeiro_parcela"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organizacao_id: Mapped[int] = mapped_column(ForeignKey("organizacoes.id", ondelete="CASCADE"), index=True)
    parcela_id: Mapped[int] = mapped_column(ForeignKey("parcelas_financeiras.id", ondelete="CASCADE"), index=True)
    numero: Mapped[str] = mapped_column(String(60), unique=True, index=True)
    emitido_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    dados: Mapped[dict] = mapped_column(JSON, default=dict)


class RenovacaoFinanceira(Base):
    __tablename__ = "renovacoes_financeiras"
    __table_args__ = (
        UniqueConstraint("organizacao_id", "processo_id", "tipo", "referencia", name="uq_renovacao_financeira"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organizacao_id: Mapped[int] = mapped_column(ForeignKey("organizacoes.id", ondelete="CASCADE"), index=True)
    processo_id: Mapped[int] = mapped_column(ForeignKey("processos.id", ondelete="CASCADE"), index=True)
    tipo: Mapped[str] = mapped_column(String(30), default="renovacao")
    referencia: Mapped[str] = mapped_column(String(40))
    vencimento: Mapped[date] = mapped_column(Date, index=True)
    status: Mapped[str] = mapped_column(String(20), default="pendente", index=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
