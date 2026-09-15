from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
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
from app.request_context import request_id_atual


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
