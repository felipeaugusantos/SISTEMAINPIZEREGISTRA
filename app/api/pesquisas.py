from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import case, func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.models import (
    AfinidadeClasse,
    AvaliacaoRiscoMarca,
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
from app.ratelimit import RateLimiter
from app.relatorios import gerar_pdf_resumo_cliente
from app.schemas import (
    AfinidadeClassesResponse,
    ClasseNiceCandidataResponse,
    ConclusaoIndicativaResponse,
    EstimativaRegistrabilidadeResponse,
    EvidenciasBuscaResponse,
    MarcaRelatorioItem,
    PesquisaMarcaCreate,
    PesquisaMarcaCriada,
    QualidadeBaseResponse,
    RelatorioMarcaResponse,
    TitularResponse,
)
from app.search import buscar_marcas, normalizar_texto
from app.tenancy import OrganizacaoPublicaDep, validar_limite_pesquisas
from app.trademarks.affinity import avaliar_afinidade
from app.trademarks.learning import extrair_atributos_par, registrar_previsao_sombra
from app.trademarks.nice import mapear_atividade
from app.trademarks.quality import avaliar_qualidade_base
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

router = APIRouter(prefix="/v1/pesquisas-marca", tags=["pesquisas de marcas"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
limitar_pesquisas = RateLimiter(limite=10, janela_segundos=60)


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
    lead = (
        await session.execute(
            select(Lead)
            .where(
                Lead.organizacao_id == organizacao.id,
                Lead.arquivado_em.is_(None),
                or_(
                    func.lower(Lead.email) == dados.email_corporativo.lower(),
                    Lead.telefone == dados.telefone,
                ),
            )
            .order_by(
                case((func.lower(Lead.email) == dados.email_corporativo.lower(), 0), else_=1),
                Lead.atualizado_em.desc(),
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if lead is None:
        lead = Lead(
            organizacao_id=organizacao.id,
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
        session.add(lead)
        await session.flush()
    else:
        lead.nome = dados.nome
        lead.empresa = dados.empresa or lead.empresa
        lead.email = dados.email_corporativo
        lead.telefone = dados.telefone
        lead.marca = dados.marca
        lead.atividade = dados.atividade
        lead.aceite_marketing = lead.aceite_marketing or dados.aceite_marketing

    pesquisa = PesquisaMarca(
        organizacao_id=organizacao.id,
        lead_id=lead.id,
        marca=dados.marca,
        atividade=dados.atividade,
        tipo_pesquisa="completa",
        classe_nice=None,
    )
    session.add(pesquisa)
    await session.commit()
    await session.refresh(pesquisa)
    return PesquisaMarcaCriada(id=pesquisa.id, relatorio_url=f"/relatorios/{pesquisa.id}")


@router.get("/{pesquisa_id}/relatorio", response_model=RelatorioMarcaResponse)
async def obter_relatorio(
    pesquisa_id: str,
    session: SessionDep,
    organizacao: OrganizacaoPublicaDep,
) -> RelatorioMarcaResponse:
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

    total, ocorrencias, evidencias = await buscar_marcas(
        session,
        marca=pesquisa.marca,
        tipo_pesquisa=pesquisa.tipo_pesquisa,
        classe_nice=pesquisa.classe_nice,
    )
    classes_atividade = mapear_atividade(pesquisa.atividade or "")
    codigos_atividade = [classe.codigo for classe in classes_atividade]
    matriz = (await session.execute(select(AfinidadeClasse))).scalars().all()
    registros_alto_renome = (
        (await session.execute(select(MarcaAltoRenome).where(MarcaAltoRenome.vigente.is_(True))))
        .scalars()
        .all()
    )
    alto_renome = {item.numero_processo_normalizado for item in registros_alto_renome}
    nomes_alto_renome = {
        normalizar_texto(item.marca) for item in registros_alto_renome if item.marca
    }
    itens = []
    for processo, criterios in ocorrencias:
        ultima_movimentacao = processo.movimentacoes[0] if processo.movimentacoes else None
        situacao = normalizar_despacho(
            ultima_movimentacao.codigo_despacho if ultima_movimentacao else None,
            ultima_movimentacao.descricao if ultima_movimentacao else processo.situacao,
        )
        situacao_oficial = (
            ultima_movimentacao.descricao if ultima_movimentacao else processo.situacao
        )
        classes_processo = [
            classe.codigo for classe in processo.classificacoes if classe.sistema == "nice"
        ]
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
        itens.append(
            MarcaRelatorioItem.model_validate(processo).model_copy(
                update={
                    "criterios_encontro": criterios,
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
                    "url_busca_oficial": (
                        "https://busca.inpi.gov.br/pePI/jsp/marcas/Pesquisa_classe_basica.jsp"
                    ),
                    "ultima_rpi": (ultima_movimentacao.numero_rpi if ultima_movimentacao else None),
                    "data_ultima_rpi": (
                        ultima_movimentacao.data_rpi if ultima_movimentacao else None
                    ),
                }
            )
        )
    itens.sort(
        key=lambda item: (
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
        [processo for processo, _ in ocorrencias],
    )
    ultima_rpi = (await session.execute(select(func.max(Movimentacao.numero_rpi)))).scalar_one()
    alto_renome_atualizado_em = (
        await session.execute(select(func.max(MarcaAltoRenome.sincronizado_em)))
    ).scalar_one()
    matriz_status = (
        "validada"
        if matriz and all(item.status_revisao == "aprovada" for item in matriz)
        else "pendente_de_validacao"
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
                afinidade_revisao=(
                    item.afinidade_classes.revisao if item.afinidade_classes else None
                ),
                classes_processo=tuple(
                    item.afinidade_classes.classes_processo if item.afinidade_classes else ()
                ),
                alto_renome=item.alto_renome,
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
            principais_conflitos=[
                conflito_para_json(conflito) for conflito in avaliacao.principais_conflitos
            ],
            regras_aplicadas=regras_para_json(),
        )
        .on_conflict_do_update(
            index_elements=[AvaliacaoRiscoMarca.pesquisa_id],
            set_={
                "versao_motor": VERSAO_MOTOR,
                "modo": MODO_MOTOR,
                "pontuacao": avaliacao.pontuacao,
                "nivel": avaliacao.nivel,
                "principais_conflitos": [
                    conflito_para_json(conflito) for conflito in avaliacao.principais_conflitos
                ],
                "regras_aplicadas": regras_para_json(),
                "calculado_em": func.now(),
            },
        )
    )
    await session.execute(comando_risco)
    pares_aprendizado = [
        extrair_atributos_par(
            pesquisa.marca,
            item.titulo or "",
            codigos_atividade,
            list(item.afinidade_classes.classes_processo) if item.afinidade_classes else [],
            afinidade_conhecida=bool(
                item.afinidade_classes
                and item.afinidade_classes.nivel in {"identica", "alta", "moderada"}
            ),
            candidata_ativa=item.relevancia_situacao == "ativa",
            alto_renome=item.alto_renome,
        )
        for item in itens
    ]
    previsao = await registrar_previsao_sombra(
        session,
        pesquisa_id=pesquisa.id,
        pares=pares_aprendizado,
    )
    modelo_previsao = (
        await session.get(ModeloRegistrabilidade, previsao.modelo_id)
        if (previsao is not None and previsao.modo == "cliente" and previsao.elegivel_cliente)
        else None
    )
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
        estimativa_status=(
            "disponivel"
            if previsao is not None and modelo_previsao is not None
            else "validacao_interna"
            if previsao is not None
            else "indisponivel"
        ),
        estimativa_mensagem=(
            "A estimativa estatística foi validada para exibição nesta pesquisa."
            if previsao is not None and modelo_previsao is not None
            else (
                "O modelo estatístico permanece em validação interna. Nenhuma probabilidade "
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
                probabilidade_deferimento=previsao.probabilidade_deferimento,
                probabilidade_inferior=previsao.probabilidade_inferior,
                probabilidade_superior=previsao.probabilidade_superior,
                nivel=previsao.nivel,
                confianca=previsao.confianca,
                confianca_rotulo=previsao.confianca_rotulo,
                cobertura_entrada=previsao.cobertura_entrada,
                modelo_versao=modelo_previsao.versao,
                escopo=previsao.escopo_estimativa,
                amostras_referencia=previsao.amostras_referencia,
                corte_dados=previsao.corte_dados,
                revisao_humana_obrigatoria=False,
                fatores_principais=previsao.fatores_principais,
            )
            if previsao is not None and modelo_previsao is not None
            else None
        ),
    )
    relatorio_versionado = await versionar_relatorio(session, relatorio)
    await session.commit()
    return relatorio_versionado


@router.get("/{pesquisa_id}/relatorio.pdf", response_class=Response)
async def baixar_relatorio_pdf(
    pesquisa_id: str,
    session: SessionDep,
    organizacao: OrganizacaoPublicaDep,
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
