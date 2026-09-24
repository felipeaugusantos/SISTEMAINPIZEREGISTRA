from datetime import date, datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.enums import StatusProspect

if TYPE_CHECKING:
    # Ainda em _core.py, referenciada aqui so como forward reference em
    # Mapped["..."] (resolvida em tempo de execucao pelo registry do
    # SQLAlchemy -- este import existe so para o ruff/checadores de tipo).
    from app.models._core import UsuarioOperacoes


class Prospect(Base):
    """Empresa localizada por fonte externa, ainda não qualificada como Lead.

    Fase 1 do Radar de Prospecção (03/09/2026, docs/arquitetura-radar-prospeccao-2026-09-03.md):
    só estrutura + CRUD + importação manual + conversão em Lead. Campos de
    fonte/campanha (Fase 2), presença digital (Fase 3), triagem de marca
    (Fase 4) e score (Fase 5) entram por migração própria em cada fase,
    para não carregar colunas que nenhum código ainda preenche.
    """

    __tablename__ = "prospects"
    __table_args__ = (
        Index("ix_prospects_org_status", "organizacao_id", "status"),
        Index("ix_prospects_org_uf_cidade", "organizacao_id", "uf", "cidade"),
    )

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
    __table_args__ = (
        Index("ix_cache_estabelecimentos_rfb_cnae_uf", "cnae_principal", "uf"),
        Index("ix_cache_estabelecimentos_rfb_uf_cidade", "uf", "cidade"),
    )

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
    __table_args__ = (
        Index("ix_prospect_enriquecimentos_prospect_provedor_criado", "prospect_id", "provedor", "criado_em"),
    )

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
