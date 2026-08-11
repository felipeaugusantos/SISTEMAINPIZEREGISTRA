from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from uuid import uuid4

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
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
    plano_id: Mapped[int] = mapped_column(
        ForeignKey("planos_saas.id", ondelete="RESTRICT"), index=True
    )
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
    organizacao_id: Mapped[int] = mapped_column(
        ForeignKey("organizacoes.id", ondelete="CASCADE"), index=True
    )
    dominio: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    ativo: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    verificado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    codigo_verificacao: Mapped[str | None] = mapped_column(String(100), nullable=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CredencialIntegracao(Base):
    __tablename__ = "credenciais_integracao"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organizacao_id: Mapped[int] = mapped_column(
        ForeignKey("organizacoes.id", ondelete="CASCADE"), index=True
    )
    nome: Mapped[str] = mapped_column(String(120))
    token_prefixo: Mapped[str] = mapped_column(String(16), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    ativo: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    ultimo_uso_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expira_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    criado_por: Mapped[str] = mapped_column(String(150))
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ConviteOrganizacao(Base):
    __tablename__ = "convites_organizacao"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organizacao_id: Mapped[int] = mapped_column(
        ForeignKey("organizacoes.id", ondelete="CASCADE"), index=True
    )
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
    organizacao_id: Mapped[int] = mapped_column(
        ForeignKey("organizacoes.id", ondelete="RESTRICT"), index=True
    )
    nome: Mapped[str] = mapped_column(String(150), index=True)
    usuario: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    email: Mapped[str] = mapped_column(String(254), unique=True, index=True)
    cargo: Mapped[str | None] = mapped_column(String(150), nullable=True)
    perfil: Mapped[str] = mapped_column(String(30), default="operador", index=True)
    senha_hash: Mapped[str] = mapped_column(Text)
    ativo: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    superadmin: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    mfa_ativo: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    mfa_segredo: Mapped[str | None] = mapped_column(Text, nullable=True)
    codigos_recuperacao: Mapped[list[str]] = mapped_column(JSON, default=list)
    alterar_senha: Mapped[bool] = mapped_column(Boolean, default=True)
    tentativas_falhas: Mapped[int] = mapped_column(Integer, default=0)
    bloqueado_ate: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
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
    usuario_id: Mapped[int] = mapped_column(
        ForeignKey("usuarios_operacoes.id", ondelete="CASCADE"), index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    csrf_hash: Mapped[str] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(500), nullable=True)
    ip_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    ultimo_acesso_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    expira_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    revogada_em: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    motivo_revogacao: Mapped[str | None] = mapped_column(String(150), nullable=True)
    usuario: Mapped[UsuarioOperacoes] = relationship(back_populates="sessoes")


class TokenRecuperacaoSenha(Base):
    __tablename__ = "tokens_recuperacao_senha"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    usuario_id: Mapped[int] = mapped_column(
        ForeignKey("usuarios_operacoes.id", ondelete="CASCADE"), index=True
    )
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
    organizacao_id: Mapped[int] = mapped_column(
        ForeignKey("organizacoes.id", ondelete="CASCADE"), index=True
    )
    usuario_id: Mapped[int] = mapped_column(
        ForeignKey("usuarios_operacoes.id", ondelete="CASCADE"), index=True
    )
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
    mfa_token_hash: Mapped[str | None] = mapped_column(
        String(64), unique=True, nullable=True, index=True
    )
    expira_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    usado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EventoCobrancaSandbox(Base):
    __tablename__ = "eventos_cobranca_sandbox"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organizacao_id: Mapped[int] = mapped_column(
        ForeignKey("organizacoes.id", ondelete="CASCADE"), index=True
    )
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
    organizacao_id: Mapped[int] = mapped_column(
        ForeignKey("organizacoes.id", ondelete="CASCADE"), index=True
    )
    lead_id: Mapped[int | None] = mapped_column(
        ForeignKey("leads.id", ondelete="SET NULL"), nullable=True, index=True
    )
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


class RpiImportacao(Base):
    __tablename__ = "rpi_importacoes"

    numero_rpi: Mapped[int] = mapped_column(Integer, primary_key=True)
    tipo: Mapped[str] = mapped_column(String(10), primary_key=True)
    registros_processados: Mapped[int] = mapped_column(Integer)
    titulares_processados: Mapped[int] = mapped_column(Integer, default=0)
    classes_processadas: Mapped[int] = mapped_column(Integer, default=0)
    movimentacoes_processadas: Mapped[int] = mapped_column(Integer, default=0)
    importado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class RpiSyncExecucao(Base):
    __tablename__ = "rpi_sync_execucoes"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    origem: Mapped[str] = mapped_column(String(20), index=True)
    status: Mapped[str] = mapped_column(String(30), index=True)
    solicitado_por: Mapped[str | None] = mapped_column(String(150), nullable=True)
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
    solicitado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    iniciado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    heartbeat_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finalizado_em: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )


class RpiSyncEstado(Base):
    __tablename__ = "rpi_sync_estado"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    status: Mapped[str] = mapped_column(String(30), default="iniciando", index=True)
    execucao_atual_id: Mapped[int | None] = mapped_column(
        ForeignKey("rpi_sync_execucoes.id", ondelete="SET NULL"), nullable=True
    )
    ultima_rpi_oficial: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ultima_rpi_local: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ultima_verificacao_em: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    proxima_verificacao_em: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    heartbeat_em: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
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
    processo_id: Mapped[int] = mapped_column(
        ForeignKey("processos.id", ondelete="CASCADE"), index=True
    )
    sistema: Mapped[str] = mapped_column(String(20), index=True)
    codigo: Mapped[str] = mapped_column(String(30), index=True)
    edicao: Mapped[str | None] = mapped_column(String(20))
    especificacao: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str | None] = mapped_column(String(255))

    processo: Mapped[Processo] = relationship(back_populates="classificacoes")


class EmpresaCRM(Base):
    __tablename__ = "empresas_crm"
    __table_args__ = (
        UniqueConstraint("organizacao_id", "nome_normalizado", name="uq_empresa_crm_org_nome"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organizacao_id: Mapped[int] = mapped_column(
        ForeignKey("organizacoes.id", ondelete="CASCADE"), index=True
    )
    nome: Mapped[str] = mapped_column(String(200), index=True)
    nome_normalizado: Mapped[str] = mapped_column(String(200), index=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    atualizado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    leads: Mapped[list["Lead"]] = relationship(back_populates="empresa_registro")
    pesquisas: Mapped[list["PesquisaMarca"]] = relationship(back_populates="empresa_registro")
    contatos: Mapped[list["ContatoLead"]] = relationship(back_populates="empresa_registro")
    processos_monitorados: Mapped[list["ProcessoMonitorado"]] = relationship(
        back_populates="empresa_registro"
    )
    lancamentos_financeiros: Mapped[list["LancamentoFinanceiro"]] = relationship(
        back_populates="empresa_registro"
    )


class CategoriaFinanceira(Base):
    __tablename__ = "categorias_financeiras"
    __table_args__ = (
        UniqueConstraint("organizacao_id", "nome", "tipo", name="uq_categoria_financeira_org"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organizacao_id: Mapped[int] = mapped_column(
        ForeignKey("organizacoes.id", ondelete="CASCADE"), index=True
    )
    nome: Mapped[str] = mapped_column(String(120), index=True)
    tipo: Mapped[str] = mapped_column(String(12), default="ambos", index=True)
    ativo: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class FormaPagamentoFinanceira(Base):
    __tablename__ = "formas_pagamento_financeiras"
    __table_args__ = (
        UniqueConstraint("organizacao_id", "nome", name="uq_forma_pagamento_financeira_org"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organizacao_id: Mapped[int] = mapped_column(
        ForeignKey("organizacoes.id", ondelete="CASCADE"), index=True
    )
    nome: Mapped[str] = mapped_column(String(120), index=True)
    tipo: Mapped[str] = mapped_column(String(30), default="outro", index=True)
    permite_parcelamento: Mapped[bool] = mapped_column(Boolean, default=False)
    maximo_parcelas: Mapped[int] = mapped_column(Integer, default=1)
    ativo: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    atualizado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class LancamentoFinanceiro(Base):
    __tablename__ = "lancamentos_financeiros"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organizacao_id: Mapped[int] = mapped_column(
        ForeignKey("organizacoes.id", ondelete="CASCADE"), index=True
    )
    empresa_id: Mapped[int | None] = mapped_column(
        ForeignKey("empresas_crm.id", ondelete="SET NULL"), nullable=True, index=True
    )
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
    criado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
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
    __table_args__ = (
        UniqueConstraint("lancamento_id", "numero", name="uq_parcela_financeira_numero"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organizacao_id: Mapped[int] = mapped_column(
        ForeignKey("organizacoes.id", ondelete="CASCADE"), index=True
    )
    lancamento_id: Mapped[int] = mapped_column(
        ForeignKey("lancamentos_financeiros.id", ondelete="CASCADE"), index=True
    )
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
    organizacao_id: Mapped[int] = mapped_column(
        ForeignKey("organizacoes.id", ondelete="CASCADE"), index=True
    )
    lancamento_id: Mapped[int] = mapped_column(
        ForeignKey("lancamentos_financeiros.id", ondelete="CASCADE"), index=True
    )
    parcela_id: Mapped[int | None] = mapped_column(
        ForeignKey("parcelas_financeiras.id", ondelete="SET NULL"), nullable=True, index=True
    )
    acao: Mapped[str] = mapped_column(String(30), index=True)
    ator: Mapped[str] = mapped_column(String(254), index=True)
    descricao: Mapped[str] = mapped_column(String(500))
    detalhes: Mapped[dict] = mapped_column(JSON, default=dict)
    criado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
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
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organizacao_id: Mapped[int] = mapped_column(
        ForeignKey("organizacoes.id", ondelete="CASCADE"), index=True
    )
    processo_id: Mapped[int] = mapped_column(
        ForeignKey("processos.id", ondelete="CASCADE"), index=True
    )
    empresa_id: Mapped[int | None] = mapped_column(
        ForeignKey("empresas_crm.id", ondelete="SET NULL"), nullable=True, index=True
    )
    responsavel_id: Mapped[int | None] = mapped_column(
        ForeignKey("usuarios_operacoes.id", ondelete="SET NULL"), nullable=True, index=True
    )
    status: Mapped[str] = mapped_column(String(20), default="ativo", index=True)
    origem: Mapped[str] = mapped_column(String(30), default="manual", index=True)
    procurador_origem: Mapped[str | None] = mapped_column(Text, nullable=True)
    observacoes: Mapped[str | None] = mapped_column(Text, nullable=True)
    vinculado_por: Mapped[str] = mapped_column(String(254))
    criado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    atualizado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    processo: Mapped[Processo] = relationship(lazy="selectin")
    empresa_registro: Mapped[EmpresaCRM | None] = relationship(
        back_populates="processos_monitorados", lazy="selectin"
    )
    responsavel: Mapped["UsuarioOperacoes | None"] = relationship(lazy="selectin")


class Lead(Base):
    __tablename__ = "leads"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organizacao_id: Mapped[int] = mapped_column(
        ForeignKey("organizacoes.id", ondelete="RESTRICT"), index=True
    )
    empresa_id: Mapped[int | None] = mapped_column(
        ForeignKey("empresas_crm.id", ondelete="SET NULL"), nullable=True, index=True
    )
    nome: Mapped[str] = mapped_column(String(150), index=True)
    email: Mapped[str] = mapped_column(String(254), index=True)
    telefone: Mapped[str] = mapped_column(String(30), index=True)
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
    aceite_privacidade: Mapped[bool] = mapped_column(default=True)
    aceite_marketing: Mapped[bool] = mapped_column(Boolean, default=False)
    responsavel_id: Mapped[int | None] = mapped_column(
        ForeignKey("usuarios_operacoes.id", ondelete="SET NULL"), nullable=True, index=True
    )
    notas: Mapped[str | None] = mapped_column(Text, nullable=True)
    proxima_acao_em: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    ultimo_contato_em: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    arquivado_em: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    atualizado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    pesquisas_marca: Mapped[list["PesquisaMarca"]] = relationship(back_populates="lead")
    contatos: Mapped[list["ContatoLead"]] = relationship(
        back_populates="lead", cascade="all, delete-orphan", order_by="ContatoLead.criado_em.desc()"
    )
    empresa_registro: Mapped["EmpresaCRM | None"] = relationship(back_populates="leads")
    responsavel: Mapped["UsuarioOperacoes | None"] = relationship()


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
    organizacao_id: Mapped[int] = mapped_column(
        ForeignKey("organizacoes.id", ondelete="CASCADE"), index=True
    )
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
    criado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    lead: Mapped[Lead] = relationship(back_populates="contatos")
    empresa_registro: Mapped["EmpresaCRM | None"] = relationship(back_populates="contatos")
    pesquisa: Mapped["PesquisaMarca | None"] = relationship(back_populates="contatos")


class PesquisaMarca(Base):
    __tablename__ = "pesquisas_marca"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    organizacao_id: Mapped[int] = mapped_column(
        ForeignKey("organizacoes.id", ondelete="RESTRICT"), index=True
    )
    lead_id: Mapped[int | None] = mapped_column(
        ForeignKey("leads.id", ondelete="SET NULL"), nullable=True, index=True
    )
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
    organizacao_id: Mapped[int] = mapped_column(
        ForeignKey("organizacoes.id", ondelete="CASCADE"), index=True
    )
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
    criado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
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
    processo_id: Mapped[int] = mapped_column(
        ForeignKey("processos.id", ondelete="CASCADE"), unique=True, index=True
    )
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
    classificador_versao: Mapped[str] = mapped_column(
        String(30), default="rotulo-marcario-1.0", index=True
    )
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
    classificador_versao: Mapped[str] = mapped_column(
        String(40), default="fundamento-inpi-1.0", index=True
    )
    coletado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    atualizado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ParTreinamentoMarca(Base):
    __tablename__ = "pares_treinamento_marca"
    __table_args__ = (
        UniqueConstraint(
            "rotulo_id", "processo_candidato_id", name="uq_par_treinamento_rotulo_candidato"
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    rotulo_id: Mapped[int] = mapped_column(
        ForeignKey("rotulos_historicos_marca.id", ondelete="CASCADE"), index=True
    )
    processo_candidato_id: Mapped[int] = mapped_column(
        ForeignKey("processos.id", ondelete="CASCADE"), index=True
    )
    atributos: Mapped[dict] = mapped_column(JSON)
    alvo_conflito: Mapped[bool | None] = mapped_column(Boolean, nullable=True, index=True)
    origem: Mapped[str] = mapped_column(String(30), default="candidato_temporal")
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ModeloRegistrabilidade(Base):
    __tablename__ = "modelos_registrabilidade"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    versao: Mapped[str] = mapped_column(String(60), unique=True, index=True)
    algoritmo: Mapped[str] = mapped_column(String(50), default="regressao_logistica")
    status: Mapped[str] = mapped_column(String(20), default="candidato", index=True)
    atributos: Mapped[list[str]] = mapped_column(JSON)
    parametros: Mapped[dict] = mapped_column(JSON)
    calibracao: Mapped[dict] = mapped_column(JSON)
    metricas: Mapped[dict] = mapped_column(JSON)
    dataset: Mapped[dict] = mapped_column(JSON)
    corte_treino: Mapped[date | None] = mapped_column(Date, nullable=True)
    corte_validacao: Mapped[date | None] = mapped_column(Date, nullable=True)
    treinado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    ativado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ativado_por: Mapped[str | None] = mapped_column(String(150), nullable=True)


class PrevisaoRegistrabilidade(Base):
    __tablename__ = "previsoes_registrabilidade"
    __table_args__ = (
        UniqueConstraint(
            "pesquisa_id", "modelo_id", name="uq_previsao_registrabilidade_pesquisa_modelo"
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    pesquisa_id: Mapped[str] = mapped_column(
        ForeignKey("pesquisas_marca.id", ondelete="CASCADE"), index=True
    )
    modelo_id: Mapped[int] = mapped_column(
        ForeignKey("modelos_registrabilidade.id", ondelete="RESTRICT"), index=True
    )
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
    __table_args__ = (
        UniqueConstraint(
            "pesquisa_id", "hash_entrada", name="uq_agente_registrabilidade_pesquisa_hash"
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organizacao_id: Mapped[int] = mapped_column(
        ForeignKey("organizacoes.id", ondelete="RESTRICT"), index=True
    )
    pesquisa_id: Mapped[str] = mapped_column(
        ForeignKey("pesquisas_marca.id", ondelete="CASCADE"), index=True
    )
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
    resultado_sincronizado_em: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    criado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )


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
    iniciado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
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
    gerado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )

    pesquisa: Mapped[PesquisaMarca] = relationship(back_populates="versoes_relatorio")


class EventoOperacional(Base):
    __tablename__ = "eventos_operacionais"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    componente: Mapped[str] = mapped_column(String(40), index=True)
    operacao: Mapped[str] = mapped_column(String(150), index=True)
    sucesso: Mapped[bool] = mapped_column(Boolean, index=True)
    duracao_ms: Mapped[int] = mapped_column(Integer)
    status_http: Mapped[int] = mapped_column(Integer, index=True)
    codigo_erro: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    criado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )


class EventoAuditoria(Base):
    __tablename__ = "eventos_auditoria"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organizacao_id: Mapped[int | None] = mapped_column(
        ForeignKey("organizacoes.id", ondelete="SET NULL"), nullable=True, index=True
    )
    ator: Mapped[str] = mapped_column(String(150), index=True)
    acao: Mapped[str] = mapped_column(String(20), index=True)
    recurso: Mapped[str] = mapped_column(String(180), index=True)
    sucesso: Mapped[bool] = mapped_column(Boolean, index=True)
    status_http: Mapped[int] = mapped_column(Integer)
    ip_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    detalhes: Mapped[dict] = mapped_column(JSON)
    criado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
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
