from datetime import date, datetime
from uuid import uuid4

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models._core import ContatoLead, EmpresaCRM, Lead


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
    dataset_version: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    bloqueado_motivo: Mapped[str | None] = mapped_column(Text, nullable=True)
    publicado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
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
