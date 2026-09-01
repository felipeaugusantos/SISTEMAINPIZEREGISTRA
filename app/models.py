from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from uuid import uuid4

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


class FaseLead(StrEnum):
    """Etapa do lead no funil de atendimento (do 1º contato ao processo no INPI)."""

    CONTATO_INICIAL = "contato_inicial"
    RELATORIO_ENVIADO = "relatorio_enviado"
    PROPOSTA_ENVIADA = "proposta_enviada"
    PROPOSTA_ACEITA = "proposta_aceita"
    PAGAMENTO_REALIZADO = "pagamento_realizado"
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
    retencao_dados_dias: Mapped[int] = mapped_column(Integer, default=730)
    politica_privacidade_versao: Mapped[str] = mapped_column(String(30), default="1.0")
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    atualizado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    plano: Mapped[PlanoSaas] = relationship(lazy="selectin")


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
    contatos_pessoa: Mapped[list["Contato"]] = relationship(back_populates="empresa", cascade="all, delete-orphan")
    leads: Mapped[list["Lead"]] = relationship(back_populates="empresa_registro")
    pesquisas: Mapped[list["PesquisaMarca"]] = relationship(back_populates="empresa_registro")
    contatos: Mapped[list["ContatoLead"]] = relationship(back_populates="empresa_registro")
    processos_monitorados: Mapped[list["ProcessoMonitorado"]] = relationship(back_populates="empresa_registro")
    lancamentos_financeiros: Mapped[list["LancamentoFinanceiro"]] = relationship(back_populates="empresa_registro")


class Contato(Base):
    """Pessoa de contato de uma empresa (separada da oportunidade/lead)."""

    __tablename__ = "contatos"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organizacao_id: Mapped[int] = mapped_column(ForeignKey("organizacoes.id", ondelete="CASCADE"), index=True)
    empresa_id: Mapped[int] = mapped_column(ForeignKey("empresas_crm.id", ondelete="CASCADE"), index=True)
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
    empresa: Mapped["EmpresaCRM"] = relationship(back_populates="contatos_pessoa")


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
    atualizado_por: Mapped[str | None] = mapped_column(String(254), nullable=True)
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
    servico_id: Mapped[int] = mapped_column(ForeignKey("servicos_financeiros.id", ondelete="RESTRICT"), index=True)
    lead_id: Mapped[int | None] = mapped_column(ForeignKey("leads.id", ondelete="SET NULL"), nullable=True, index=True)
    processo_id: Mapped[int | None] = mapped_column(
        ForeignKey("processos.id", ondelete="SET NULL"), nullable=True, index=True
    )
    proposta_id: Mapped[int | None] = mapped_column(
        ForeignKey("propostas_comerciais.id", ondelete="SET NULL"), nullable=True, index=True
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
    responsavel: Mapped["UsuarioOperacoes | None"] = relationship()


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
    __table_args__ = (UniqueConstraint("organizacao_id", "numero", name="uq_proposta_org_numero"),)

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
    marca: Mapped[str | None] = mapped_column(String(200), nullable=True)
    classes: Mapped[str | None] = mapped_column(String(200), nullable=True)
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
