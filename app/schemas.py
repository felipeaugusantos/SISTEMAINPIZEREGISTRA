from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models import StatusLead, TipoProcesso


class DadosComplementaresRegistrabilidadeUpdate(BaseModel):
    forma_apresentacao: (
        Literal["nominativa", "mista", "figurativa", "tridimensional", "posicao"] | None
    ) = None
    descricao_visual: str | None = Field(default=None, max_length=2000)
    significado: str | None = Field(default=None, max_length=2000)
    produtos_servicos: str | None = Field(default=None, max_length=4000)
    requerente_tipo: Literal["pessoa_fisica", "pessoa_juridica"] | None = None
    atividade_requerente: str | None = Field(default=None, max_length=2000)
    atividade_compativel: bool | None = None
    usa_simbolo_oficial: bool | None = None
    conteudo_potencialmente_ofensivo: bool | None = None
    termo_generico_descritivo: bool | None = None
    possui_alegacao_origem_qualidade: bool | None = None
    alegacao_comprovavel: bool | None = None
    usa_nome_ou_imagem_terceiro: bool | None = None
    usa_obra_terceiro: bool | None = None
    usa_indicacao_geografica: bool | None = None
    possui_autorizacoes: bool | None = None
    documentos_obrigatorios_disponiveis: bool | None = None
    deposito_realizado: bool = False
    numero_pedido: str | None = Field(default=None, max_length=50)
    oposicao_identificada: bool | None = None

    @field_validator(
        "descricao_visual",
        "significado",
        "produtos_servicos",
        "atividade_requerente",
        "numero_pedido",
        mode="before",
    )
    @classmethod
    def limpar_textos_opcionais(cls, valor: str | None) -> str | None:
        return valor.strip() or None if isinstance(valor, str) else valor


class BrandingConfig(BaseModel):
    nome_exibido: str | None = Field(default=None, min_length=2, max_length=80)
    cor_primaria: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")
    logo_url: str | None = Field(default=None, max_length=500)

    @field_validator("nome_exibido", "logo_url", mode="before")
    @classmethod
    def limpar_opcionais(cls, valor: str | None) -> str | None:
        return valor.strip() or None if isinstance(valor, str) else valor

    @field_validator("logo_url")
    @classmethod
    def validar_logo(cls, valor: str | None) -> str | None:
        if valor and not (valor.startswith("https://") or valor.startswith("/static/")):
            raise ValueError("A logo deve usar HTTPS ou um recurso interno /static/")
        return valor


class TitularResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    nome: str
    pais: str | None


class MovimentacaoResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    codigo_despacho: str | None
    descricao: str
    data_rpi: date
    numero_rpi: int


class ClassificacaoMarcaResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    sistema: str
    codigo: str
    edicao: str | None
    especificacao: str | None
    status: str | None


class ProcessoResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    numero: str
    tipo: TipoProcesso
    titulo: str | None
    data_deposito: date | None
    situacao: str | None
    situacao_normalizada: str | None
    relevancia_situacao: str | None
    fonte: str
    apresentacao: str | None
    natureza: str | None
    elemento_nominativo: str | None
    procurador: str | None
    imagem_url: str | None
    atualizado_em: datetime
    titulares: list[TitularResponse]
    movimentacoes: list[MovimentacaoResponse]
    classificacoes: list[ClassificacaoMarcaResponse]


class ProcessoResumo(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    numero: str
    tipo: TipoProcesso
    titulo: str | None
    data_deposito: date | None
    situacao: str | None
    situacao_normalizada: str | None = None
    relevancia_situacao: str | None = None
    atualizado_em: datetime
    titulares: list[TitularResponse]


class ResultadoBusca(BaseModel):
    total: int
    limite: int
    deslocamento: int
    itens: list[ProcessoResumo]


class LeadCreate(BaseModel):
    nome: str = Field(min_length=2, max_length=150)
    email: str = Field(min_length=5, max_length=254, pattern=r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
    telefone: str = Field(min_length=10, max_length=30)
    marca: str = Field(default="", max_length=200)
    processo_numero: str | None = Field(default=None, max_length=50)
    origem: Literal["resultados", "processo", "geral"] = "resultados"
    tipo_interesse: TipoProcesso | None = None
    aceite_privacidade: Literal[True]
    website: str = Field(default="", max_length=200)

    @field_validator("nome", "email", "telefone", "marca")
    @classmethod
    def remover_espacos(cls, valor: str) -> str:
        return valor.strip()

    @field_validator("processo_numero")
    @classmethod
    def normalizar_processo_numero(cls, valor: str | None) -> str | None:
        if valor is None:
            return None
        return valor.strip() or None

    @field_validator("telefone")
    @classmethod
    def validar_telefone(cls, valor: str) -> str:
        digitos = "".join(caractere for caractere in valor if caractere.isdigit())
        if not 10 <= len(digitos) <= 15:
            raise ValueError("Informe um telefone válido com DDD")
        return valor


class LeadResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    nome: str
    email: str
    telefone: str
    empresa_id: int | None = None
    empresa: str | None
    marca: str
    atividade: str | None
    processo_numero: str | None
    origem: str
    tipo_interesse: TipoProcesso | None
    status: StatusLead
    aceite_marketing: bool
    responsavel_id: int | None = None
    responsavel_nome: str | None = None
    notas: str | None = None
    proxima_acao_em: datetime | None = None
    ultimo_contato_em: datetime | None = None
    tags: list[str] = Field(default_factory=list)
    arquivado_em: datetime | None = None
    total_pesquisas: int = 0
    ultima_pesquisa: "PesquisaLeadResumo | None" = None
    ultima_pesquisa_em: datetime | None = None
    risco_mais_alto: str | None = None
    risco_mais_alto_pontuacao: int | None = None
    relatorios_completos_gerados: int = 0
    pesquisas: list["PesquisaLeadResumo"] = Field(default_factory=list)
    criado_em: datetime
    atualizado_em: datetime

    @field_validator("aceite_marketing", mode="before")
    @classmethod
    def marketing_nao_informado(cls, valor: bool | None) -> bool:
        return bool(valor)

    @field_validator("tags", mode="before")
    @classmethod
    def tags_nao_informadas(cls, valor: list[str] | None) -> list[str]:
        return valor or []


TipoPesquisaMarca = Literal["exata", "radical", "completa"]


class PesquisaMarcaCreate(BaseModel):
    nome: str = Field(min_length=2, max_length=150)
    empresa: str | None = Field(default=None, max_length=200)
    email_corporativo: str = Field(
        min_length=5,
        max_length=254,
        pattern=r"^[^\s@]+@[^\s@]+\.[^\s@]+$",
    )
    telefone: str = Field(min_length=10, max_length=30)
    marca: str = Field(min_length=2, max_length=200)
    atividade: str = Field(min_length=3, max_length=500)
    aceite_privacidade: Literal[True]
    aceite_marketing: bool = False
    website: str = Field(default="", max_length=200)

    @field_validator("nome", "email_corporativo", "telefone", "marca", "atividade")
    @classmethod
    def remover_espacos_pesquisa(cls, valor: str) -> str:
        return valor.strip()

    @field_validator("empresa")
    @classmethod
    def remover_espacos_opcionais(cls, valor: str | None) -> str | None:
        if valor is None:
            return None
        return valor.strip() or None

    @field_validator("email_corporativo")
    @classmethod
    def normalizar_email(cls, valor: str) -> str:
        return valor.lower()

    @field_validator("telefone")
    @classmethod
    def validar_telefone_corporativo(cls, valor: str) -> str:
        digitos = "".join(caractere for caractere in valor if caractere.isdigit())
        if not 10 <= len(digitos) <= 15:
            raise ValueError("Informe um telefone corporativo válido com DDD")
        return valor


class PesquisaMarcaCriada(BaseModel):
    id: str
    relatorio_url: str
    lead_id: int | None = None
    duplicada: bool = False
    pesquisa_original_id: str | None = None


class ClasseNiceCandidataResponse(BaseModel):
    codigo: str
    titulo: str
    tipo: Literal["produto", "serviço"]
    termos_encontrados: list[str]
    confianca: float = Field(default=0, ge=0, le=1)


class AfinidadeClassesResponse(BaseModel):
    nivel: str
    rotulo: str
    justificativa: str
    revisao: str
    classes_atividade: list[str]
    classes_processo: list[str]


class EvidenciasBuscaResponse(BaseModel):
    termo_original: str
    expressao_completa: str
    radicais: list[str]
    variacoes: list[str]
    nomes_identicos: int
    expressoes_completas: int
    ocorrencias_por_radical: int
    criterios_considerados: list[str]
    versao_algoritmo: str
    estrategias_executadas: list[str] = Field(default_factory=list)
    termos_consultados: list[str] = Field(default_factory=list)
    limiar_trigrama: float = Field(default=0.30, ge=0, le=1)


class QualidadeBaseResponse(BaseModel):
    status: Literal["adequada", "atencao", "bloqueada"]
    ultima_rpi: int | None
    data_ultima_rpi: date | None
    importada_em: datetime | None
    idade_dias: int | None
    total_processos_marca: int
    deposito_mais_antigo: date | None
    deposito_mais_recente: date | None
    ocorrencias_sem_titulo: int
    ocorrencias_sem_situacao: int
    ocorrencias_sem_classe: int
    completude_resultados: float = Field(default=0, ge=0, le=1)
    pontuacao_qualidade: float = Field(default=0, ge=0, le=1)
    apta_para_modelo: bool = False
    avisos: list[str] = Field(default_factory=list)


class ConclusaoIndicativaResponse(BaseModel):
    nivel: str
    titulo: str
    resumo: str
    revisao_humana_recomendada: bool


class EstimativaRegistrabilidadeResponse(BaseModel):
    probabilidade_deferimento: float = Field(ge=0, le=1)
    probabilidade_inferior: float | None = Field(default=None, ge=0, le=1)
    probabilidade_superior: float | None = Field(default=None, ge=0, le=1)
    nivel: Literal["favoravel", "atencao", "alto_risco", "critico"]
    confianca: float = Field(ge=0, le=1)
    confianca_rotulo: Literal["baixa", "media", "alta"] = "baixa"
    cobertura_entrada: float = Field(default=0, ge=0, le=1)
    modelo_versao: str
    escopo: Literal["deferimento_exame_merito"] = "deferimento_exame_merito"
    amostras_referencia: int = Field(default=0, ge=0)
    corte_dados: date | None = None
    revisao_humana_obrigatoria: bool = False
    fatores_principais: list[dict]
    aviso: str = (
        "Estimativa estatística preliminar do deferimento no exame de mérito, baseada em "
        "decisões históricas publicadas. É exibida desde a primeira consulta, não constitui "
        "garantia de registro e não substitui o exame do INPI."
    )


class MotivoPrognosticoResponse(BaseModel):
    criterio: str
    conclusao: str
    referencia: str


class PrognosticoRegistrabilidadeResponse(BaseModel):
    veredito: Literal["favoravel", "atencao", "desfavoravel"]
    titulo: str
    resumo: str
    motivos: list[MotivoPrognosticoResponse] = Field(default_factory=list)
    pendencias: list[str] = Field(default_factory=list)
    versao_matriz: str | None = None
    ressalva: str


class MarcaRelatorioItem(ProcessoResumo):
    apresentacao: str | None
    natureza: str | None
    classificacoes: list[ClassificacaoMarcaResponse]
    criterios_encontro: list[str] = Field(default_factory=list)
    alto_renome: bool = False
    afinidade_classes: AfinidadeClassesResponse | None = None
    relevancia: Literal["critica", "alta", "media", "baixa"] = "baixa"
    relevancia_rotulo: str = "Baixa relevância aparente"
    justificativas_relevancia: list[str] = Field(default_factory=list)
    url_detalhe: str | None = None
    url_busca_oficial: str | None = None
    ultima_rpi: int | None = None
    data_ultima_rpi: date | None = None


class RelatorioMarcaResponse(BaseModel):
    id: str
    versao: int = 1
    schema_versao: str = "relatorio-marca-4.3"
    gerado_em: datetime | None = None
    conteudo_hash: str = ""
    marca: str
    atividade: str
    tipo_pesquisa: TipoPesquisaMarca
    classe_nice: str | None
    criado_em: datetime
    ultima_rpi: int | None
    classes_atividade: list[ClasseNiceCandidataResponse]
    matriz_afinidade_status: str
    alto_renome_atualizado_em: datetime | None
    total: int
    limite_exibido: int
    itens: list[MarcaRelatorioItem]
    evidencias_busca: EvidenciasBuscaResponse | None = None
    qualidade_base: QualidadeBaseResponse | None = None
    conclusao: ConclusaoIndicativaResponse | None = None
    risco_pontuacao: int | None = Field(default=None, ge=0, le=100)
    risco_nivel: str | None = None
    estimativa_status: Literal["disponivel", "validacao_interna", "indisponivel"] = (
        "validacao_interna"
    )
    estimativa_mensagem: str = (
        "O modelo estatístico permanece em validação interna. Nenhuma probabilidade é "
        "exibida até que os critérios mínimos de dados, calibração e revisão humana sejam "
        "atendidos."
    )
    estimativa_registrabilidade: EstimativaRegistrabilidadeResponse | None = None
    prognostico_registrabilidade: PrognosticoRegistrabilidadeResponse | None = None


class ResumoPublicoMarcaResponse(BaseModel):
    id: str
    versao: int = 1
    schema_versao: str = "resumo-publico-marca-1.2"
    gerado_em: datetime | None = None
    marca: str
    atividade: str
    criado_em: datetime
    ultima_rpi: int | None
    classes_atividade: list[ClasseNiceCandidataResponse]
    total: int
    evidencias_busca: EvidenciasBuscaResponse | None = None
    qualidade_base: QualidadeBaseResponse | None = None
    conclusao: ConclusaoIndicativaResponse | None = None
    risco_pontuacao: int | None = Field(default=None, ge=0, le=100)
    risco_nivel: str | None = None
    # Prognóstico determinístico de deferido/indeferido (substitui a estimativa de ML,
    # que não tinha poder preditivo e permanece apenas em modo sombra/interno).
    prognostico_registrabilidade: PrognosticoRegistrabilidadeResponse | None = None
    detalhes_internos_disponiveis: bool = True


class LeadListResponse(BaseModel):
    total: int
    total_global: int = 0
    pesquisas_total: int = 0
    deslocamento: int = 0
    limite: int = 50
    tem_mais: bool = False
    itens: list[LeadResponse]
    por_status: dict[str, int]
    acoes: dict[str, bool] = Field(default_factory=dict)


class PesquisaLeadResumo(BaseModel):
    id: str
    marca: str
    atividade: str | None = None
    classe_nice: str | None = None
    duplicada: bool = False
    pesquisa_original_id: str | None = None
    criado_em: datetime
    risco_nivel: str | None = None
    risco_pontuacao: int | None = None
    relatorio_disponivel: bool = False
    relatorio_completo_gerado: bool = False
    relatorio_completo_gerado_em: datetime | None = None
    relatorio_completo_gerado_por: str | None = None
    relatorio_url: str
    pdf_url: str | None = None
    exclusao_status: str | None = None


class LeadDetalheResponse(LeadResponse):
    pass


class LeadStatusUpdate(BaseModel):
    status: StatusLead | None = None
    responsavel_id: int | None = None
    notas: str | None = Field(default=None, max_length=4000)
    proxima_acao_em: datetime | None = None
    tags: list[str] | None = Field(default=None, max_length=20)
    registrar_contato: bool = False

    @field_validator("notas")
    @classmethod
    def limpar_notas(cls, valor: str | None) -> str | None:
        return valor.strip() or None if valor else None

    @field_validator("tags")
    @classmethod
    def limpar_tags(cls, valor: list[str] | None) -> list[str] | None:
        if valor is None:
            return None
        unicas = []
        for tag in valor:
            limpa = tag.strip()[:40]
            if limpa and limpa.casefold() not in {item.casefold() for item in unicas}:
                unicas.append(limpa)
        return unicas


class AfinidadeClasseResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    classe_origem: str
    classe_destino: str
    nivel: str
    justificativa: str
    versao: str
    status_revisao: str
    revisor: str | None
    observacoes_revisao: str | None
    revisado_em: datetime | None


class Fase2AdminResponse(BaseModel):
    alto_renome_vigentes: int
    alto_renome_atualizado_em: datetime | None
    afinidades_pendentes: int
    afinidades_aprovadas: int
    afinidades_rejeitadas: int
    afinidades: list[AfinidadeClasseResponse]


class AfinidadeRevisaoUpdate(BaseModel):
    status_revisao: Literal["aprovada", "rejeitada"]
    revisor: str = Field(min_length=2, max_length=150)
    observacoes_revisao: str = Field(min_length=3, max_length=1000)

    @field_validator("revisor", "observacoes_revisao")
    @classmethod
    def limpar_revisao(cls, valor: str) -> str:
        return valor.strip()


NivelRisco = Literal["baixo", "moderado", "alto", "critico"]


class AvaliacaoRiscoAdminItem(BaseModel):
    id: int
    pesquisa_id: str
    marca: str
    atividade: str
    contato_nome: str
    empresa: str | None
    pontuacao: int
    nivel: NivelRisco
    versao_motor: str
    modo: Literal["sombra"]
    principais_conflitos: list[dict]
    regras_aplicadas: dict
    calculado_em: datetime
    nivel_humano: NivelRisco | None
    avaliador: str | None
    observacoes_humanas: str | None
    avaliado_em: datetime | None
    concordancia: bool | None


class Fase3AdminResponse(BaseModel):
    total_calculadas: int
    total_avaliadas_humanamente: int
    total_concordantes: int
    taxa_concordancia: float | None
    modo: Literal["sombra"]
    versao_motor: str
    itens: list[AvaliacaoRiscoAdminItem]


class AvaliacaoHumanaRiscoUpdate(BaseModel):
    nivel_humano: NivelRisco
    avaliador: str = Field(min_length=2, max_length=150)
    observacoes_humanas: str = Field(min_length=3, max_length=2000)

    @field_validator("avaliador", "observacoes_humanas")
    @classmethod
    def limpar_avaliacao_humana(cls, valor: str) -> str:
        return valor.strip()


class AdminResumoResponse(BaseModel):
    leads_total: int
    leads_novos: int
    pesquisas_total: int
    ultima_rpi: int | None
    alto_renome_vigentes: int
    afinidades_pendentes: int
    riscos_calculados: int
    riscos_elevados: int
    riscos_pendentes_revisao: int


class RpiSyncExecucaoResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    origem: str
    status: str
    solicitado_por: str | None
    execucao_anterior_id: int | None
    rpi_inicio: int | None
    rpi_fim: int | None
    rpi_atual: int | None
    ultima_rpi_oficial: int | None
    ultima_rpi_local_antes: int | None
    ultima_rpi_local_depois: int | None
    edicoes_total: int
    edicoes_processadas: int
    progresso_percentual: float
    registros_processados: int
    titulares_processados: int
    classes_processadas: int
    movimentacoes_processadas: int
    mensagem: str | None
    erro: str | None
    solicitado_em: datetime
    iniciado_em: datetime | None
    heartbeat_em: datetime | None
    finalizado_em: datetime | None
    duracao_segundos: float | None


class RpiSyncSaudeResponse(BaseModel):
    api: bool
    banco: bool
    sincronizador: bool


class RpiSyncAdminResponse(BaseModel):
    status: str
    status_rotulo: str
    status_cor: Literal["verde", "amarelo", "vermelho", "cinza"]
    ultima_rpi_oficial: int | None
    ultima_rpi_local: int | None
    edicoes_atraso: int
    ultima_verificacao_em: datetime | None
    proxima_verificacao_em: datetime | None
    heartbeat_em: datetime | None
    falhas_consecutivas: int
    ultimo_erro: str | None
    intervalo_segundos: int
    saude: RpiSyncSaudeResponse
    execucao_atual: RpiSyncExecucaoResponse | None
    historico: list[RpiSyncExecucaoResponse]


class RpiSyncAcaoResponse(BaseModel):
    id: int
    status: str
    mensagem: str


class EventoAuditoriaResponse(BaseModel):
    ator: str
    acao: str
    recurso: str
    sucesso: bool
    status_http: int
    criado_em: datetime


class ProducaoAdminResponse(BaseModel):
    ambiente: str
    requisicoes_24h: int
    erros_24h: int
    taxa_erros_24h: float
    duracao_media_ms_24h: float
    duracao_maxima_ms_24h: int
    avaliacoes_humanas: int
    divergencias_humanas: int
    taxa_divergencia: float
    relatorios_versionados: int
    pesquisas_com_versao: int
    auditoria: list[EventoAuditoriaResponse]


class AprendizadoModeloResponse(BaseModel):
    id: int
    versao: str
    algoritmo: str
    status: str
    metricas: dict
    dataset: dict
    corte_treino: date | None
    corte_validacao: date | None
    treinado_em: datetime
    ativado_em: datetime | None
    ativado_por: str | None


class AprendizadoRotuloResponse(BaseModel):
    id: int
    processo_numero: str
    marca: str | None
    rotulo: str
    fundamento: str
    confianca: float
    data_referencia: date
    data_decisao: date | None
    numero_rpi: int | None
    despacho_codigo: str | None
    despacho_descricao: str | None
    prioridade_revisao: Literal["alta", "media", "baixa"]
    elegivel_treinamento: bool
    motivo_inelegibilidade: str | None
    classificador_versao: str
    evidencias_classificacao: list[dict] = Field(default_factory=list)
    status_revisao: str
    revisor: str | None
    evidencia_oficial_status: str | None = None
    evidencia_oficial_url: str | None = None
    evidencia_oficial_texto: str | None = None
    evidencia_oficial_hash: str | None = None


class AprendizadoPrevisaoResponse(BaseModel):
    id: int
    pesquisa_id: str
    marca: str
    atividade: str | None
    modelo_versao: str
    modo: str
    probabilidade_deferimento: float
    probabilidade_inferior: float | None
    probabilidade_superior: float | None
    nivel: str
    confianca: float
    confianca_rotulo: str
    cobertura_entrada: float
    elegivel_cliente: bool
    motivos_inelegibilidade: list[str]
    escopo_estimativa: str
    amostras_referencia: int
    corte_dados: date | None
    fatores_principais: list[dict]
    nivel_humano: str | None
    avaliador: str | None
    observacoes_humanas: str | None
    avaliado_em: datetime | None
    calculado_em: datetime


class AprendizadoControleResponse(BaseModel):
    inferencia_habilitada: bool
    rollout_percentual: int
    exibir_cliente: bool
    minimo_revisoes_humanas: int
    minimo_recall: float
    minimo_especificidade: float
    maximo_brier: float
    maximo_ece: float
    minimo_amostras_modelo: int
    minimo_amostras_teste: int
    largura_maxima_intervalo: float
    minima_cobertura: float
    atualizado_por: str | None
    justificativa: str | None


class AprendizadoAdminResponse(BaseModel):
    total_rotulos: int
    rotulos_deferidos: int
    rotulos_indeferidos: int
    rotulos_revisados: int
    rotulos_prioridade_alta: int
    rotulos_documentais: int
    evidencias_oficiais: int
    evidencias_para_revisao: int
    evidencias_com_erro: int
    total_pares: int
    total_previsoes: int
    previsoes_revisadas: int
    modelo_ativo: AprendizadoModeloResponse | None
    modelos: list[AprendizadoModeloResponse]
    rotulos_pendentes: list[AprendizadoRotuloResponse]
    previsoes: list[AprendizadoPrevisaoResponse]
    controle: AprendizadoControleResponse
    liberacao_cliente_permitida: bool
    bloqueios_liberacao: list[str]


class ConstruirDatasetRequest(BaseModel):
    limite: int = Field(default=3000, ge=300, le=10_000)
    candidatos_por_processo: int = Field(default=12, ge=4, le=30)


class AprendizadoAcaoResponse(BaseModel):
    mensagem: str
    rotulos_processados: int = 0
    pares_processados: int = 0
    modelo: AprendizadoModeloResponse | None = None


class RevisaoRotuloUpdate(BaseModel):
    status_revisao: Literal["aprovada", "rejeitada"]
    rotulo: Literal["deferida", "indeferida"]
    fundamento: Literal[
        "deferimento",
        "deferimento_recurso",
        "conflito_anterior",
        "falta_distintividade",
        "outra_proibicao",
        "indeferimento_nao_especificado",
    ]
    revisor: str | None = Field(default=None, min_length=2, max_length=150)
    observacoes: str = Field(min_length=3, max_length=2000)


class RevisaoPrevisaoUpdate(BaseModel):
    nivel_humano: Literal["favoravel", "atencao", "alto_risco", "critico"]
    avaliador: str = Field(min_length=2, max_length=150)
    observacoes: str = Field(min_length=3, max_length=2000)


class AprendizadoControleUpdate(BaseModel):
    inferencia_habilitada: bool
    rollout_percentual: int = Field(ge=0, le=100)
    exibir_cliente: bool
    minimo_revisoes_humanas: int = Field(ge=10, le=10000)
    minimo_recall: float = Field(ge=0.5, le=1.0)
    minimo_especificidade: float = Field(ge=0.5, le=1.0)
    maximo_brier: float = Field(ge=0.0, le=0.5)
    maximo_ece: float = Field(ge=0.0, le=0.5)
    minimo_amostras_modelo: int = Field(ge=100, le=1000000)
    minimo_amostras_teste: int = Field(ge=20, le=100000)
    largura_maxima_intervalo: float = Field(ge=0.05, le=0.8)
    minima_cobertura: float = Field(ge=0.0, le=1.0)
    justificativa: str = Field(min_length=5, max_length=1000)
