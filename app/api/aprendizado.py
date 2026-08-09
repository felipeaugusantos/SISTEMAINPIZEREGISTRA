from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import AcaoAdminDep, UsuarioAutenticado, exigir_permissao
from app.database import get_session
from app.models import (
    ControleAprendizadoMarca,
    ModeloRegistrabilidade,
    ParTreinamentoMarca,
    PesquisaMarca,
    PrevisaoRegistrabilidade,
    Processo,
    RotuloHistoricoMarca,
)
from app.schemas import (
    AprendizadoAcaoResponse,
    AprendizadoAdminResponse,
    AprendizadoControleResponse,
    AprendizadoControleUpdate,
    AprendizadoModeloResponse,
    AprendizadoPrevisaoResponse,
    AprendizadoRotuloResponse,
    ConstruirDatasetRequest,
    RevisaoPrevisaoUpdate,
    RevisaoRotuloUpdate,
)
from app.trademarks.learning import (
    ativar_modelo,
    construir_dataset_historico,
    obter_controle,
    treinar_modelo,
    validar_modelo_para_cliente,
)

router = APIRouter(prefix="/v1/admin/aprendizado", tags=["aprendizado marcário"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
AdminDep = Annotated[UsuarioAutenticado, Depends(exigir_permissao("learning.view"))]
WriteDep = Annotated[UsuarioAutenticado, Depends(exigir_permissao("learning.manage"))]


def _modelo(item: ModeloRegistrabilidade) -> AprendizadoModeloResponse:
    return AprendizadoModeloResponse(
        id=item.id,
        versao=item.versao,
        algoritmo=item.algoritmo,
        status=item.status,
        metricas=item.metricas,
        dataset=item.dataset,
        corte_treino=item.corte_treino,
        corte_validacao=item.corte_validacao,
        treinado_em=item.treinado_em,
        ativado_em=item.ativado_em,
        ativado_por=item.ativado_por,
    )


def _controle(item: ControleAprendizadoMarca) -> AprendizadoControleResponse:
    return AprendizadoControleResponse(
        inferencia_habilitada=item.inferencia_habilitada,
        rollout_percentual=item.rollout_percentual,
        exibir_cliente=item.exibir_cliente,
        minimo_revisoes_humanas=item.minimo_revisoes_humanas,
        minimo_recall=item.minimo_recall,
        minimo_especificidade=item.minimo_especificidade,
        maximo_brier=item.maximo_brier,
        maximo_ece=item.maximo_ece,
        minimo_amostras_modelo=item.minimo_amostras_modelo,
        minimo_amostras_teste=item.minimo_amostras_teste,
        largura_maxima_intervalo=item.largura_maxima_intervalo,
        minima_cobertura=item.minima_cobertura,
        atualizado_por=item.atualizado_por,
        justificativa=item.justificativa,
    )


async def _bloqueios(
    session: AsyncSession,
    controle: ControleAprendizadoMarca,
    modelo: ModeloRegistrabilidade | None,
) -> list[str]:
    revisoes = await session.scalar(
        select(func.count())
        .select_from(PrevisaoRegistrabilidade)
        .where(PrevisaoRegistrabilidade.nivel_humano.is_not(None))
    )
    return validar_modelo_para_cliente(modelo, controle, int(revisoes or 0))


@router.get("", response_model=AprendizadoAdminResponse)
async def obter_aprendizado(session: SessionDep, usuario: AdminDep) -> AprendizadoAdminResponse:
    contagens = (
        await session.execute(
            select(
                select(func.count()).select_from(RotuloHistoricoMarca).scalar_subquery(),
                select(func.count())
                .select_from(RotuloHistoricoMarca)
                .where(RotuloHistoricoMarca.alvo_deferimento.is_(True))
                .scalar_subquery(),
                select(func.count())
                .select_from(RotuloHistoricoMarca)
                .where(RotuloHistoricoMarca.alvo_deferimento.is_(False))
                .scalar_subquery(),
                select(func.count())
                .select_from(RotuloHistoricoMarca)
                .where(RotuloHistoricoMarca.status_revisao == "aprovada")
                .scalar_subquery(),
                select(func.count()).select_from(ParTreinamentoMarca).scalar_subquery(),
                select(func.count())
                .select_from(PrevisaoRegistrabilidade)
                .join(PesquisaMarca, PesquisaMarca.id == PrevisaoRegistrabilidade.pesquisa_id)
                .where(PesquisaMarca.organizacao_id == usuario.organizacao_id)
                .scalar_subquery(),
                select(func.count())
                .select_from(PrevisaoRegistrabilidade)
                .join(PesquisaMarca, PesquisaMarca.id == PrevisaoRegistrabilidade.pesquisa_id)
                .where(
                    PrevisaoRegistrabilidade.nivel_humano.is_not(None),
                    PesquisaMarca.organizacao_id == usuario.organizacao_id,
                )
                .scalar_subquery(),
            )
        )
    ).one()
    modelos = (
        (
            await session.execute(
                select(ModeloRegistrabilidade)
                .order_by(ModeloRegistrabilidade.treinado_em.desc())
                .limit(20)
            )
        )
        .scalars()
        .all()
    )
    ativo = next((item for item in modelos if item.status == "ativo"), None)
    rotulos = (
        await session.execute(
            select(RotuloHistoricoMarca, Processo)
            .join(Processo, Processo.id == RotuloHistoricoMarca.processo_id)
            .where(RotuloHistoricoMarca.status_revisao == "pendente")
            .order_by(RotuloHistoricoMarca.confianca, RotuloHistoricoMarca.data_referencia.desc())
            .limit(30)
        )
    ).all()
    previsoes = (
        await session.execute(
            select(PrevisaoRegistrabilidade, PesquisaMarca, ModeloRegistrabilidade)
            .join(PesquisaMarca, PesquisaMarca.id == PrevisaoRegistrabilidade.pesquisa_id)
            .join(
                ModeloRegistrabilidade,
                ModeloRegistrabilidade.id == PrevisaoRegistrabilidade.modelo_id,
            )
            .where(PesquisaMarca.organizacao_id == usuario.organizacao_id)
            .order_by(PrevisaoRegistrabilidade.calculado_em.desc())
            .limit(50)
        )
    ).all()
    controle = await obter_controle(session)
    bloqueios = await _bloqueios(session, controle, ativo)
    return AprendizadoAdminResponse(
        total_rotulos=contagens[0],
        rotulos_deferidos=contagens[1],
        rotulos_indeferidos=contagens[2],
        rotulos_revisados=contagens[3],
        total_pares=contagens[4],
        total_previsoes=contagens[5],
        previsoes_revisadas=contagens[6],
        modelo_ativo=_modelo(ativo) if ativo else None,
        modelos=[_modelo(item) for item in modelos],
        rotulos_pendentes=[
            AprendizadoRotuloResponse(
                id=rotulo.id,
                processo_numero=processo.numero,
                marca=processo.titulo,
                rotulo=rotulo.rotulo,
                fundamento=rotulo.fundamento,
                confianca=rotulo.confianca,
                data_referencia=rotulo.data_referencia,
                numero_rpi=rotulo.numero_rpi,
                status_revisao=rotulo.status_revisao,
                revisor=rotulo.revisor,
            )
            for rotulo, processo in rotulos
        ],
        previsoes=[
            AprendizadoPrevisaoResponse(
                id=item.id,
                pesquisa_id=pesquisa.id,
                marca=pesquisa.marca,
                atividade=pesquisa.atividade,
                modelo_versao=modelo.versao,
                modo=item.modo,
                probabilidade_deferimento=item.probabilidade_deferimento,
                probabilidade_inferior=item.probabilidade_inferior,
                probabilidade_superior=item.probabilidade_superior,
                nivel=item.nivel,
                confianca=item.confianca,
                confianca_rotulo=item.confianca_rotulo,
                cobertura_entrada=item.cobertura_entrada,
                elegivel_cliente=item.elegivel_cliente,
                motivos_inelegibilidade=item.motivos_inelegibilidade,
                escopo_estimativa=item.escopo_estimativa,
                amostras_referencia=item.amostras_referencia,
                corte_dados=item.corte_dados,
                fatores_principais=item.fatores_principais,
                nivel_humano=item.nivel_humano,
                avaliador=item.avaliador,
                observacoes_humanas=item.observacoes_humanas,
                avaliado_em=item.avaliado_em,
                calculado_em=item.calculado_em,
            )
            for item, pesquisa, modelo in previsoes
        ],
        controle=_controle(controle),
        liberacao_cliente_permitida=bool(ativo and controle.inferencia_habilitada),
        bloqueios_liberacao=bloqueios,
    )


@router.post("/dataset", response_model=AprendizadoAcaoResponse)
async def construir_dataset(
    dados: ConstruirDatasetRequest,
    session: SessionDep,
    usuario: WriteDep,
    _limite: AcaoAdminDep,
) -> AprendizadoAcaoResponse:
    if not usuario.superadmin:
        raise HTTPException(
            403, detail="Preparacao global de dataset exclusiva do superadministrador"
        )
    rotulos, pares = await construir_dataset_historico(
        session,
        limite=dados.limite,
        candidatos_por_processo=dados.candidatos_por_processo,
    )
    return AprendizadoAcaoResponse(
        mensagem="Dataset histórico reconstruído com corte temporal",
        rotulos_processados=rotulos,
        pares_processados=pares,
    )


@router.post("/treinar", response_model=AprendizadoAcaoResponse)
async def treinar(
    session: SessionDep,
    usuario: WriteDep,
    _limite: AcaoAdminDep,
) -> AprendizadoAcaoResponse:
    if not usuario.superadmin:
        raise HTTPException(403, detail="Treinamento global exclusivo do superadministrador")
    try:
        modelo = await treinar_modelo(session)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return AprendizadoAcaoResponse(
        mensagem="Modelo candidato treinado e avaliado temporalmente",
        modelo=_modelo(modelo),
    )


@router.post("/modelos/{modelo_id}/ativar", response_model=AprendizadoAcaoResponse)
async def ativar(
    modelo_id: int,
    session: SessionDep,
    administrador: WriteDep,
    _limite: AcaoAdminDep,
) -> AprendizadoAcaoResponse:
    if not administrador.superadmin:
        raise HTTPException(403, detail="Ativacao global de modelo exclusiva do superadministrador")
    modelo = await session.get(ModeloRegistrabilidade, modelo_id)
    if modelo is None:
        raise HTTPException(status_code=404, detail="Modelo não encontrado")
    try:
        await ativar_modelo(session, modelo, administrador.email)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return AprendizadoAcaoResponse(mensagem="Modelo ativado em modo sombra", modelo=_modelo(modelo))


@router.patch("/rotulos/{rotulo_id}", response_model=AprendizadoAcaoResponse)
async def revisar_rotulo(
    rotulo_id: int,
    dados: RevisaoRotuloUpdate,
    session: SessionDep,
    usuario: WriteDep,
    _limite: AcaoAdminDep,
) -> AprendizadoAcaoResponse:
    if not usuario.superadmin:
        raise HTTPException(403, detail="Revisao da base global exclusiva do superadministrador")
    rotulo = await session.get(RotuloHistoricoMarca, rotulo_id)
    if rotulo is None:
        raise HTTPException(status_code=404, detail="Rótulo não encontrado")
    rotulo.status_revisao = dados.status_revisao
    rotulo.rotulo = dados.rotulo
    rotulo.alvo_deferimento = dados.rotulo == "deferida"
    rotulo.fundamento = dados.fundamento
    rotulo.origem = "revisao_humana"
    rotulo.confianca = 1.0
    rotulo.revisor = dados.revisor
    rotulo.observacoes_revisao = dados.observacoes
    rotulo.revisado_em = datetime.now(UTC)
    await session.commit()
    return AprendizadoAcaoResponse(mensagem="Rótulo histórico revisado")


@router.patch("/previsoes/{previsao_id}", response_model=AprendizadoAcaoResponse)
async def revisar_previsao(
    previsao_id: int,
    dados: RevisaoPrevisaoUpdate,
    session: SessionDep,
    usuario: WriteDep,
    _limite: AcaoAdminDep,
) -> AprendizadoAcaoResponse:
    previsao = (
        await session.execute(
            select(PrevisaoRegistrabilidade)
            .join(PesquisaMarca, PesquisaMarca.id == PrevisaoRegistrabilidade.pesquisa_id)
            .where(
                PrevisaoRegistrabilidade.id == previsao_id,
                PesquisaMarca.organizacao_id == usuario.organizacao_id,
            )
        )
    ).scalar_one_or_none()
    if previsao is None:
        raise HTTPException(status_code=404, detail="Previsão não encontrada")
    previsao.nivel_humano = dados.nivel_humano
    previsao.avaliador = dados.avaliador
    previsao.observacoes_humanas = dados.observacoes
    previsao.avaliado_em = datetime.now(UTC)
    await session.commit()
    return AprendizadoAcaoResponse(mensagem="Comparação humana registrada")


@router.patch("/controle", response_model=AprendizadoControleResponse)
async def atualizar_controle(
    dados: AprendizadoControleUpdate,
    session: SessionDep,
    administrador: WriteDep,
    _limite: AcaoAdminDep,
) -> AprendizadoControleResponse:
    if not administrador.superadmin:
        raise HTTPException(
            403, detail="Controle global de aprendizado exclusivo do superadministrador"
        )
    controle = await obter_controle(session)
    controle.minimo_revisoes_humanas = dados.minimo_revisoes_humanas
    controle.minimo_recall = dados.minimo_recall
    controle.minimo_especificidade = dados.minimo_especificidade
    controle.maximo_brier = dados.maximo_brier
    controle.maximo_ece = dados.maximo_ece
    controle.minimo_amostras_modelo = dados.minimo_amostras_modelo
    controle.minimo_amostras_teste = dados.minimo_amostras_teste
    controle.largura_maxima_intervalo = dados.largura_maxima_intervalo
    controle.minima_cobertura = dados.minima_cobertura
    controle.inferencia_habilitada = dados.inferencia_habilitada
    controle.rollout_percentual = 100
    controle.exibir_cliente = True
    controle.atualizado_por = administrador.email
    controle.justificativa = dados.justificativa
    await session.commit()
    return _controle(controle)
