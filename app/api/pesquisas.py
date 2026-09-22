from datetime import UTC, datetime, timedelta
from time import perf_counter
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.crm import (
    avancar_fase_lead,
    buscar_lead_ativo_por_email,
    obter_ou_criar_empresa,
    registrar_consentimento_titular,
)
from app.database import get_session
from app.emailing import enviar_alerta_nova_pesquisa
from app.models import (
    AfinidadeClasse,
    AlertaSistema,
    AvaliacaoRiscoMarca,
    Contato,
    Lead,
    MarcaAltoRenome,
    ModeloRegistrabilidade,
    Movimentacao,
    PesquisaMarca,
    StatusLead,
    TipoProcesso,
    VersaoRelatorioMarca,
)
from app.normalization import normalizar_numero_processo
from app.privacy import mascarar_documentos_publicos
from app.production import versionar_relatorio
from app.public_report_tokens import emitir_token_relatorio
from app.ratelimit import RateLimiter
from app.relatorios import gerar_pdf_resumo_cliente
from app.request_context import adicionar_detalhes_operacionais
from app.schemas import (
    AfinidadeClassesResponse,
    AnaliseConsolidadaPublicaResponse,
    ClasseNiceCandidataResponse,
    ConclusaoIndicativaResponse,
    EstimativaRegistrabilidadeResponse,
    EvidenciasBuscaResponse,
    MarcaRelatorioItem,
    MotivoVeredictoResponse,
    PesquisaMarcaCreate,
    PesquisaMarcaCriada,
    PrognosticoRegistrabilidadeResponse,
    QualidadeBaseResponse,
    RelatorioMarcaResponse,
    ResumoPublicoMarcaResponse,
    TitularResponse,
    VeredictoPublicoResponse,
)
from app.search import buscar_marcas, normalizar_texto, termos_comuns_do_match
from app.search_ranking import adicionar_contexto_score
from app.security import AcessoRelatorioPublicoDep
from app.tenancy import OrganizacaoPublicaDep, validar_limite_pesquisas
from app.trademarks.affinity import avaliar_afinidade
from app.trademarks.agent import registrar_execucao_agente
from app.trademarks.consolidated import apresentacao_analise, construir_analise_consolidada
from app.trademarks.learning import (
    antiguidade_norm,
    contar_marcas_por_titular,
    extrair_atributos_par,
    portfolio_titular_norm,
    registrar_previsao_sombra,
)
from app.trademarks.model_status import normalizar_status_modelo
from app.trademarks.nice import mapear_atividade
from app.trademarks.quality import avaliar_qualidade_base
from app.trademarks.registrability import (
    construir_matriz_registrabilidade,
    construir_prognostico_registrabilidade,
)
from app.trademarks.relevance import (
    ORDEM_RELEVANCIA,
    classificar_relevancia,
    construir_conclusao,
)
from app.trademarks.risk import (
    MODO_MOTOR,
    VERSAO_MOTOR,
    ConflitoEntrada,
    calcular_risco,
    conflito_para_json,
    regras_para_json,
)
from app.trademarks.status import normalizar_despacho
from app.trademarks.veredito import (
    VERSAO_MOTOR_VEREDITO,
    determinar_veredito_publico,
    montar_entrada_veredito,
)

router = APIRouter(prefix="/v1/pesquisas-marca", tags=["pesquisas de marcas"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
limitar_pesquisas = RateLimiter(limite=10, janela_segundos=60, escopo="pesquisas-publicas")
limitar_relatorios = RateLimiter(limite=60, janela_segundos=60, escopo="relatorios-publicos")


async def detectar_pesquisa_duplicada(
    session: AsyncSession, organizacao_id: int, lead_id: int | None, marca: str, *, classe_nice: str | None = None
) -> str | None:
    """Id da 1ª pesquisa do mesmo lead com a mesma marca (case-insensitive) NA
    MESMA CLASSE NICE, se existir. Achado da auditoria completa do CRM
    (06/09/2026, item 4): sem o filtro por classe, pedir uma segunda análise
    da mesma marca numa classe diferente (ex.: classe 25 depois da 35) seria
    incorretamente marcado como "duplicada" e escondido da análise -- classes
    diferentes são pedidos comerciais distintos, não repetição."""
    if lead_id is None:
        return None
    return (
        await session.execute(
            select(PesquisaMarca.id)
            .where(
                PesquisaMarca.organizacao_id == organizacao_id,
                PesquisaMarca.lead_id == lead_id,
                func.lower(PesquisaMarca.marca) == marca.strip().lower(),
                PesquisaMarca.classe_nice == classe_nice,
            )
            .order_by(PesquisaMarca.criado_em)
            .limit(1)
        )
    ).scalar_one_or_none()


@router.post(
    "",
    response_model=PesquisaMarcaCriada,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(limitar_pesquisas)],
)
async def criar_pesquisa(
    dados: PesquisaMarcaCreate,
    session: SessionDep,
    organizacao: OrganizacaoPublicaDep,
) -> PesquisaMarcaCriada:
    if dados.website:
        raise HTTPException(status_code=400, detail="Envio inválido")

    if "consulta" not in organizacao.modulos:
        raise HTTPException(status_code=403, detail="Modulo de consulta indisponivel no plano")
    await validar_limite_pesquisas(session, organizacao)
    empresa = await obter_ou_criar_empresa(session, organizacao.id, dados.empresa)
    lead = await buscar_lead_ativo_por_email(session, organizacao.id, dados.email_corporativo)
    if lead is not None and lead.contato_id is not None:
        # Corrige vínculos legados inválidos antes do autoflush da atualização.
        with session.no_autoflush:
            contato_valido = (
                await session.execute(
                    select(Contato.id).where(
                        Contato.id == lead.contato_id,
                        Contato.organizacao_id == organizacao.id,
                        Contato.empresa_id == (empresa.id if empresa else None),
                    )
                )
            ).scalar_one_or_none()
        if contato_valido is None:
            lead.contato_id = None
    if lead is None:
        lead = Lead(
            organizacao_id=organizacao.id,
            empresa_id=empresa.id if empresa else None,
            nome=dados.nome,
            empresa=dados.empresa,
            email=dados.email_corporativo,
            telefone=dados.telefone,
            marca=dados.marca,
            atividade=dados.atividade,
            origem="relatorio",
            tipo_interesse=TipoProcesso.MARCA,
            aceite_privacidade=True,
            aceite_marketing=dados.aceite_marketing,
            status=StatusLead.NOVO,
        )
        registrar_consentimento_titular(lead, organizacao.politica_privacidade_versao)
        session.add(lead)
        await session.flush()
    else:
        lead.nome = dados.nome
        lead.empresa_id = empresa.id if empresa else None
        lead.empresa = empresa.nome if empresa else dados.empresa or lead.empresa
        lead.email = dados.email_corporativo
        lead.telefone = dados.telefone
        lead.marca = dados.marca
        lead.atividade = dados.atividade
        lead.aceite_marketing = lead.aceite_marketing or dados.aceite_marketing
        registrar_consentimento_titular(lead, organizacao.politica_privacidade_versao)

    original = await detectar_pesquisa_duplicada(session, organizacao.id, lead.id, dados.marca)
    pesquisa = PesquisaMarca(
        organizacao_id=organizacao.id,
        lead_id=lead.id,
        empresa_id=empresa.id if empresa else lead.empresa_id,
        marca=dados.marca,
        atividade=dados.atividade,
        tipo_pesquisa="completa",
        classe_nice=None,
        duplicada=original is not None,
        pesquisa_original_id=original,
    )
    session.add(pesquisa)
    # Funil do lead: gerar o relatório avança para "relatório enviado" (só avança).
    await avancar_fase_lead(session, lead, "relatorio_enviado", por="sistema")
    session.add(
        AlertaSistema(
            organizacao_id=organizacao.id,
            severidade="info",
            codigo="NOVA_PESQUISA",
            mensagem=f"{dados.nome} pesquisou a marca “{dados.marca}”" + (f" ({dados.empresa})" if dados.empresa else ""),
            detalhes={"pesquisa_id": pesquisa.id, "lead_id": lead.id, "marca": dados.marca},
        )
    )
    await session.commit()
    await session.refresh(pesquisa)
    await enviar_alerta_nova_pesquisa(dados.marca, dados.nome, dados.empresa)
    return PesquisaMarcaCriada(
        id=pesquisa.id,
        relatorio_url=f"/relatorios/{pesquisa.id}",
        relatorio_token=emitir_token_relatorio(pesquisa.id, organizacao.id),
        lead_id=lead.id,
        duplicada=pesquisa.duplicada,
        pesquisa_original_id=pesquisa.pesquisa_original_id,
    )


_ROTULOS_DIRETRIZ_PUBLICOS = {
    "deposito_imediato": "Depósito imediato",
    "ajuste_especificacao": "Ajuste de especificação",
    "adequacao_mista": "Adequação de logotipo/mista",
    "inviavel_rebranding": "Inviável — sugerir rebranding",
}


def _analise_consolidada_publica(analise: dict | None) -> AnaliseConsolidadaPublicaResponse | None:
    if not analise:
        return None
    apresentacao = apresentacao_analise(analise)
    situacao = apresentacao["situacao"]
    diretriz = analise.get("diretriz_acao") or {}
    codigo_diretriz = diretriz.get("codigo")
    return AnaliseConsolidadaPublicaResponse(
        situacao_codigo=situacao["codigo"],
        situacao_rotulo=situacao["rotulo"],
        situacao_explicacao=situacao["explicacao"],
        situacao_origem=situacao.get("origem") or "analise_automatica",
        titulo=analise.get("titulo") or "",
        recomendacao=analise.get("recomendacao") or "",
        diretriz_acao=codigo_diretriz,
        diretriz_acao_rotulo=_ROTULOS_DIRETRIZ_PUBLICOS.get(codigo_diretriz) if codigo_diretriz else None,
        disclaimers=list(analise.get("disclaimers") or []),
    )


def construir_resumo_publico(relatorio: RelatorioMarcaResponse) -> ResumoPublicoMarcaResponse:
    return ResumoPublicoMarcaResponse(
        id=relatorio.id,
        versao=relatorio.versao,
        gerado_em=relatorio.gerado_em,
        marca=relatorio.marca,
        atividade=relatorio.atividade,
        criado_em=relatorio.criado_em,
        ultima_rpi=relatorio.ultima_rpi,
        classes_atividade=relatorio.classes_atividade,
        total=relatorio.total,
        evidencias_busca=relatorio.evidencias_busca,
        qualidade_base=relatorio.qualidade_base,
        conclusao=relatorio.conclusao,
        risco_pontuacao=relatorio.risco_pontuacao,
        risco_nivel=relatorio.risco_nivel,
        prognostico_registrabilidade=relatorio.prognostico_registrabilidade,
        analise_consolidada=_analise_consolidada_publica(relatorio.analise_consolidada),
    )


# Achado médio da Fase 12 (auditoria da Consulta de marcas, 22/09/2026):
# cada GET recomputava o relatório inteiro do zero (busca trigram completa
# + matriz de risco + inferência do modelo de aprendizado), mesmo que nada
# tivesse mudado -- versionar_relatorio só evita GRAVAR uma versão
# duplicada, nunca evita o custo de CPU/DB de gerar. Com 60 req/min
# liberadas por IP nesta mesma rota, um único visitante conseguia forçar
# até 60 recomputações completas por minuto na mesma pesquisa. Mesmo
# padrão já usado em baixar_relatorio_pdf (abaixo): serve a última versão
# persistida quando ela é recente o suficiente pra não valer a pena
# recalcular (dados da RPI não mudam nesse intervalo).
CACHE_RELATORIO_PUBLICO_SEGUNDOS = 60


@router.get(
    "/{pesquisa_id}/relatorio",
    response_model=ResumoPublicoMarcaResponse,
    dependencies=[Depends(limitar_relatorios)],
)
async def obter_relatorio(
    pesquisa_id: str,
    session: SessionDep,
    organizacao: OrganizacaoPublicaDep,
    _acesso: AcessoRelatorioPublicoDep,
) -> ResumoPublicoMarcaResponse:
    pesquisa = (
        await session.execute(
            select(PesquisaMarca)
            .where(
                PesquisaMarca.id == pesquisa_id,
                PesquisaMarca.organizacao_id == organizacao.id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if pesquisa is None:
        raise HTTPException(status_code=404, detail="Relatório não encontrado")
    versao_recente = (
        await session.execute(
            select(VersaoRelatorioMarca)
            .where(VersaoRelatorioMarca.pesquisa_id == pesquisa.id)
            .order_by(VersaoRelatorioMarca.numero_versao.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if versao_recente is not None and (
        datetime.now(UTC) - versao_recente.gerado_em
    ) < timedelta(seconds=CACHE_RELATORIO_PUBLICO_SEGUNDOS):
        return construir_resumo_publico(RelatorioMarcaResponse.model_validate(versao_recente.payload))
    return await gerar_resumo_pesquisa(session, pesquisa)


async def gerar_resumo_pesquisa(session: AsyncSession, pesquisa: PesquisaMarca) -> ResumoPublicoMarcaResponse:
    """Motor de geração do relatório, reutilizável pelo fluxo público e pelo admin."""
    inicio_busca = perf_counter()
    total, ocorrencias, evidencias = await buscar_marcas(
        session,
        marca=pesquisa.marca,
        tipo_pesquisa=pesquisa.tipo_pesquisa,
        classe_nice=pesquisa.classe_nice,
    )
    adicionar_detalhes_operacionais(
        search_duration_ms=round((perf_counter() - inicio_busca) * 1000),
        search_results_count=total,
        zero_result_search=total == 0,
        search_algorithm=evidencias.get("versao_algoritmo"),
    )
    classes_atividade = mapear_atividade(pesquisa.atividade or "")
    codigos_atividade = [classe.codigo for classe in classes_atividade]
    matriz = (await session.execute(select(AfinidadeClasse))).scalars().all()
    registros_alto_renome = (
        (await session.execute(select(MarcaAltoRenome).where(MarcaAltoRenome.vigente.is_(True)))).scalars().all()
    )
    alto_renome = {item.numero_processo_normalizado for item in registros_alto_renome}
    nomes_alto_renome = {normalizar_texto(item.marca) for item in registros_alto_renome if item.marca}
    itens = []
    titular_ids_por_processo: dict[str, list[int]] = {}
    for ocorrencia in ocorrencias:
        processo = ocorrencia.processo
        titular_ids_por_processo[processo.numero] = [titular.id for titular in processo.titulares]
        criterios = ocorrencia.criterios
        ultima_movimentacao = processo.movimentacoes[0] if processo.movimentacoes else None
        situacao = normalizar_despacho(
            ultima_movimentacao.codigo_despacho if ultima_movimentacao else None,
            ultima_movimentacao.descricao if ultima_movimentacao else processo.situacao,
        )
        situacao_oficial = ultima_movimentacao.descricao if ultima_movimentacao else processo.situacao
        classes_processo = [classe.codigo for classe in processo.classificacoes if classe.sistema == "nice"]
        afinidade = avaliar_afinidade(codigos_atividade, classes_processo, list(matriz))
        processo_alto_renome = (
            normalizar_numero_processo(processo.numero) in alto_renome
            or normalizar_texto(processo.titulo or "") in nomes_alto_renome
        )
        relevancia = classificar_relevancia(
            criterios,
            alto_renome=processo_alto_renome,
            afinidade_nivel=afinidade.nivel,
            relevancia_situacao=situacao.relevancia,
        )
        score_busca = adicionar_contexto_score(
            ocorrencia.score,
            processo=processo.numero,
            classes_atividade=codigos_atividade,
            classes_processo=classes_processo,
            afinidade_nivel=afinidade.nivel,
            afinidade_revisao=afinidade.revisao,
            situacao_ativa=situacao.relevancia == "ativa",
            alto_renome=processo_alto_renome,
        )
        itens.append(
            MarcaRelatorioItem.model_validate(processo).model_copy(
                update={
                    "criterios_encontro": criterios,
                    "score_busca": score_busca.total,
                    "score_busca_versao": score_busca.versao,
                    "fatores_score_busca": score_busca.fatores_json(),
                    "situacao": situacao_oficial,
                    "situacao_normalizada": situacao.codigo,
                    "relevancia_situacao": situacao.relevancia,
                    "titulares": [
                        TitularResponse(
                            nome=mascarar_documentos_publicos(titular.nome),
                            pais=titular.pais,
                        )
                        for titular in processo.titulares
                    ],
                    "alto_renome": processo_alto_renome,
                    "afinidade_classes": AfinidadeClassesResponse(
                        nivel=afinidade.nivel,
                        rotulo=afinidade.rotulo,
                        justificativa=afinidade.justificativa,
                        revisao=afinidade.revisao,
                        classes_atividade=list(afinidade.classes_atividade),
                        classes_processo=list(afinidade.classes_processo),
                    ),
                    "relevancia": relevancia.nivel,
                    "relevancia_rotulo": relevancia.rotulo,
                    "justificativas_relevancia": list(relevancia.justificativas),
                    "url_detalhe": f"/processos/{processo.numero}",
                    "url_busca_oficial": ("https://busca.inpi.gov.br/pePI/jsp/marcas/Pesquisa_classe_basica.jsp"),
                    "ultima_rpi": (ultima_movimentacao.numero_rpi if ultima_movimentacao else None),
                    "data_ultima_rpi": (ultima_movimentacao.data_rpi if ultima_movimentacao else None),
                }
            )
        )
    itens.sort(
        key=lambda item: (
            -item.score_busca,
            ORDEM_RELEVANCIA.get(item.relevancia, 99),
            (
                0
                if "Nome idêntico" in item.criterios_encontro
                else 1
                if "Expressão completa" in item.criterios_encontro
                else 2
                if "Elemento do nome" in item.criterios_encontro
                else 3
            ),
            item.titulo or "",
            item.numero,
        )
    )
    qualidade = await avaliar_qualidade_base(
        session,
        [ocorrencia.processo for ocorrencia in ocorrencias],
    )
    ultima_rpi = (await session.execute(select(func.max(Movimentacao.numero_rpi)))).scalar_one()
    alto_renome_atualizado_em = (await session.execute(select(func.max(MarcaAltoRenome.sincronizado_em)))).scalar_one()
    matriz_status = (
        "validada" if matriz and all(item.status_revisao == "aprovada" for item in matriz) else "pendente_de_validacao"
    )
    avaliacao = calcular_risco(
        [
            ConflitoEntrada(
                numero=item.numero,
                titulo=item.titulo,
                criterios_encontro=tuple(item.criterios_encontro),
                relevancia_situacao=item.relevancia_situacao,
                situacao_normalizada=item.situacao_normalizada,
                afinidade_nivel=(item.afinidade_classes.nivel if item.afinidade_classes else None),
                afinidade_revisao=(item.afinidade_classes.revisao if item.afinidade_classes else None),
                classes_processo=tuple(item.afinidade_classes.classes_processo if item.afinidade_classes else ()),
                alto_renome=item.alto_renome,
                termos_comuns_no_match=termos_comuns_do_match(item.titulo, pesquisa.marca),
            )
            for item in itens
        ]
    )
    comando_risco = (
        insert(AvaliacaoRiscoMarca)
        .values(
            pesquisa_id=pesquisa.id,
            versao_motor=VERSAO_MOTOR,
            modo=MODO_MOTOR,
            pontuacao=avaliacao.pontuacao,
            nivel=avaliacao.nivel,
            principais_conflitos=[conflito_para_json(conflito) for conflito in avaliacao.principais_conflitos],
            regras_aplicadas=regras_para_json(),
        )
        .on_conflict_do_update(
            index_elements=[AvaliacaoRiscoMarca.pesquisa_id],
            set_={
                "versao_motor": VERSAO_MOTOR,
                "modo": MODO_MOTOR,
                "pontuacao": avaliacao.pontuacao,
                "nivel": avaliacao.nivel,
                "principais_conflitos": [conflito_para_json(conflito) for conflito in avaliacao.principais_conflitos],
                "regras_aplicadas": regras_para_json(),
                "calculado_em": func.now(),
            },
        )
    )
    await session.execute(comando_risco)
    todos_titular_ids = {tid for ids in titular_ids_por_processo.values() for tid in ids}
    data_referencia_inferencia = datetime.now(UTC).date()
    portfolio_por_titular = await contar_marcas_por_titular(
        session, list(todos_titular_ids), data_referencia_inferencia
    )
    pares_aprendizado = [
        extrair_atributos_par(
            pesquisa.marca,
            item.titulo or "",
            codigos_atividade,
            list(item.afinidade_classes.classes_processo) if item.afinidade_classes else [],
            afinidade_conhecida=bool(
                item.afinidade_classes and item.afinidade_classes.nivel in {"identica", "alta", "moderada"}
            ),
            candidata_ativa=item.relevancia_situacao == "ativa",
            antiguidade_candidata_norm=antiguidade_norm(item.data_deposito, data_referencia_inferencia),
            portfolio_titular_candidata_norm=portfolio_titular_norm(
                portfolio_por_titular, titular_ids_por_processo.get(item.numero, []), data_referencia_inferencia
            ),
        )
        for item in itens
    ]
    previsao = await registrar_previsao_sombra(
        session,
        pesquisa_id=pesquisa.id,
        pares=pares_aprendizado,
        marca=pesquisa.marca,
    )
    modelo_previsao = await session.get(ModeloRegistrabilidade, previsao.modelo_id) if previsao is not None else None
    nivel, titulo, resumo, revisao = construir_conclusao(itens, total)
    relatorio = RelatorioMarcaResponse(
        id=pesquisa.id,
        marca=pesquisa.marca,
        atividade=pesquisa.atividade or "Não informada",
        tipo_pesquisa=pesquisa.tipo_pesquisa,
        classe_nice=pesquisa.classe_nice,
        criado_em=pesquisa.criado_em,
        ultima_rpi=ultima_rpi,
        classes_atividade=[
            ClasseNiceCandidataResponse(
                codigo=classe.codigo,
                titulo=classe.titulo,
                tipo=classe.tipo,
                termos_encontrados=list(classe.termos_encontrados),
                confianca=classe.confianca,
            )
            for classe in classes_atividade
        ],
        matriz_afinidade_status=matriz_status,
        alto_renome_atualizado_em=alto_renome_atualizado_em,
        total=total,
        limite_exibido=len(itens),
        itens=itens,
        evidencias_busca=EvidenciasBuscaResponse.model_validate(evidencias),
        qualidade_base=QualidadeBaseResponse.model_validate(qualidade),
        conclusao=ConclusaoIndicativaResponse(
            nivel=nivel,
            titulo=titulo,
            resumo=resumo,
            revisao_humana_recomendada=revisao,
        ),
        risco_pontuacao=avaliacao.pontuacao,
        risco_nivel=avaliacao.nivel,
        principais_conflitos_risco=[conflito_para_json(conflito) for conflito in avaliacao.principais_conflitos],
        estimativa_status=(
            "disponivel"
            if (previsao is not None and modelo_previsao is not None and previsao.elegivel_cliente)
            else "validacao_interna"
            if previsao is not None
            else "indisponivel"
        ),
        estimativa_mensagem=(
            (
                "O indicador histórico foi validado para exibição nesta pesquisa."
                if previsao.elegivel_cliente
                else "O indicador histórico foi calculado para validação interna. Leia a "
                "faixa de incerteza e os avisos de qualidade."
            )
            if previsao is not None and modelo_previsao is not None
            else (
                "O modelo estatístico permanece em validação interna. Nenhum indicador histórico "
                "é exibida até que os critérios mínimos de dados, calibração e revisão humana "
                "sejam atendidos."
            )
            if previsao is not None
            else (
                "Ainda não há uma estimativa estatística disponível para esta pesquisa. "
                "O resultado abaixo permanece uma triagem indicativa de anterioridades."
            )
        ),
        estimativa_registrabilidade=(
            EstimativaRegistrabilidadeResponse(
                indicador_historico_registrabilidade=previsao.probabilidade_deferimento,
                probabilidade_deferimento=previsao.probabilidade_deferimento,
                probabilidade_inferior=previsao.probabilidade_inferior,
                probabilidade_superior=previsao.probabilidade_superior,
                nivel=previsao.nivel,
                confianca=previsao.confianca,
                confianca_rotulo=previsao.confianca_rotulo,
                cobertura_entrada=previsao.cobertura_entrada,
                modelo_versao=modelo_previsao.versao,
                modelo_status=normalizar_status_modelo(modelo_previsao.status),
                escopo=previsao.escopo_estimativa,
                amostras_referencia=previsao.amostras_referencia,
                corte_dados=previsao.corte_dados,
                revisao_humana_obrigatoria=False,
                fatores_principais=previsao.fatores_principais,
                aviso=(
                    "Indicador histórico de registrabilidade baseado em decisões publicadas "
                    "com características semelhantes. O modelo está em validação interna; "
                    "não representa previsão ou garantia de decisão do INPI."
                    if not previsao.elegivel_cliente
                    else "Indicador histórico de registrabilidade baseado em decisões "
                    "publicadas com características semelhantes. Não representa previsão "
                    "ou garantia de decisão do INPI."
                ),
            )
            if (previsao is not None and modelo_previsao is not None and previsao.elegivel_cliente)
            else None
        ),
    )
    relatorio_payload = relatorio.model_dump(mode="json")
    matriz_registrabilidade = construir_matriz_registrabilidade(
        marca=pesquisa.marca,
        atividade=pesquisa.atividade,
        classe_nice=pesquisa.classe_nice,
        relatorio=relatorio_payload,
        pontuacao_risco=avaliacao.pontuacao,
        nivel_risco=avaliacao.nivel,
        dados_complementares=pesquisa.dados_complementares_registrabilidade or {},
    )
    # Achado REG-4 da auditoria (04/09/2026): modelo SHADOW/VALIDATION não determina
    # veredito público -- só um modelo ACTIVE pode influenciar a decisão automática
    # (registrar_previsao_sombra busca o modelo mais recente independente do status,
    # de propósito, para permitir avaliação/calibração; quem decide se ele PESA na
    # decisão é este ponto de chamada).
    modelo_status = modelo_previsao.status if modelo_previsao is not None else None
    previsao_ativa = previsao if modelo_status == "ACTIVE" else None
    await registrar_execucao_agente(
        session,
        pesquisa=pesquisa,
        matriz=matriz_registrabilidade,
        relatorio=relatorio_payload,
        pontuacao_risco=avaliacao.pontuacao,
        nivel_risco=avaliacao.nivel,
        previsao=previsao_ativa,
        modelo_versao=modelo_previsao.versao if modelo_previsao is not None else None,
    )
    prognostico = construir_prognostico_registrabilidade(matriz_registrabilidade)
    relatorio = relatorio.model_copy(
        update={"prognostico_registrabilidade": PrognosticoRegistrabilidadeResponse.model_validate(prognostico)}
    )
    relatorio.analise_consolidada = construir_analise_consolidada(
        relatorio.model_dump(mode="json"), pesquisa.dados_complementares_registrabilidade or {},
    )
    base_identificada = bool(pesquisa.classe_nice) or bool(classes_atividade)
    entrada_veredito = montar_entrada_veredito(
        matriz=matriz_registrabilidade,
        qualidade_bloqueada=qualidade.get("status") == "bloqueada",
        cobertura_evidencias=float(qualidade.get("pontuacao_qualidade") or 0),
        base_identificada=base_identificada,
        nivel_risco=avaliacao.nivel,
        modelo_status=modelo_status,
        probabilidade_deferimento=previsao_ativa.probabilidade_deferimento if previsao_ativa is not None else None,
    )
    resultado_veredito = determinar_veredito_publico(entrada_veredito)
    relatorio.veredito_publico = VeredictoPublicoResponse(
        veredito=resultado_veredito.veredito.value,
        motivo_principal=resultado_veredito.motivo_principal,
        motivos=[
            MotivoVeredictoResponse(codigo=motivo.codigo, descricao=motivo.descricao)
            for motivo in resultado_veredito.motivos
        ],
        pontos_atencao=[
            MotivoVeredictoResponse(codigo=motivo.codigo, descricao=motivo.descricao)
            for motivo in resultado_veredito.pontos_atencao
        ],
        origem=resultado_veredito.origem,
        data_base_rpi=qualidade.get("data_ultima_rpi"),
        versao_regras=VERSAO_MOTOR_VEREDITO,
        limitacoes=[
            "Triagem determinística e estatística; não substitui o exame de mérito do INPI.",
            "Não considera elementos subjetivos avaliados apenas pelo examinador.",
        ],
        responsavel_validacao=(
            pesquisa.validated_by if getattr(pesquisa, "analysis_state", None) == "VALIDATED" else None
        ),
    )
    relatorio_versionado = await versionar_relatorio(session, relatorio)
    await session.commit()
    return construir_resumo_publico(relatorio_versionado)


@router.get(
    "/{pesquisa_id}/relatorio.pdf",
    response_class=Response,
    dependencies=[Depends(limitar_relatorios)],
)
async def baixar_relatorio_pdf(
    pesquisa_id: str,
    session: SessionDep,
    organizacao: OrganizacaoPublicaDep,
    _acesso: AcessoRelatorioPublicoDep,
) -> Response:
    versao = (
        await session.execute(
            select(VersaoRelatorioMarca)
            .join(PesquisaMarca, PesquisaMarca.id == VersaoRelatorioMarca.pesquisa_id)
            .where(
                VersaoRelatorioMarca.pesquisa_id == pesquisa_id,
                PesquisaMarca.organizacao_id == organizacao.id,
            )
            .order_by(VersaoRelatorioMarca.numero_versao.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if versao is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Relatório ainda não gerado. Abra o relatório antes de baixar o PDF.",
        )

    relatorio = RelatorioMarcaResponse.model_validate(versao.payload)
    pdf = gerar_pdf_resumo_cliente(relatorio)
    nome_arquivo = normalizar_numero_processo(relatorio.marca) or "relatorio"
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="resumo-{nome_arquivo}.pdf"',
        },
    )
