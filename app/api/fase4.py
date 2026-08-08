from datetime import UTC, datetime
from time import perf_counter
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import AcaoAdminDep, UsuarioAutenticado, exigir_permissao
from app.database import get_session
from app.models import AvaliacaoRiscoMarca, ExplicacaoRiscoIA, PesquisaMarca
from app.production import (
    elegivel_rollout,
    ia_efetivamente_habilitada,
    obter_controle_producao,
)
from app.schemas import (
    ExplicacaoRiscoIAAdminItem,
    Fase4AdminResponse,
    RevisaoExplicacaoIAUpdate,
)
from app.settings import get_settings
from app.trademarks.ai_explanation import (
    VERSAO_PROMPT,
    ConfiguracaoIAInativaError,
    SaidaIAInvalidaError,
    construir_entrada_anonimizada,
    exige_revisao_humana,
    gerar_explicacao,
    hash_entrada,
)

router = APIRouter(prefix="/v1/admin/fase4", tags=["IA explicativa interna"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
AdminDep = Annotated[UsuarioAutenticado, Depends(exigir_permissao("ai.view"))]
GenerateDep = Annotated[UsuarioAutenticado, Depends(exigir_permissao("ai.generate"))]
ReviewDep = Annotated[UsuarioAutenticado, Depends(exigir_permissao("ai.review"))]
Limite = Annotated[int, Query(ge=1, le=200)]


def _referencias(avaliacao: AvaliacaoRiscoMarca) -> list[dict]:
    return [
        {
            "conflito_id": indice,
            "numero": conflito.get("numero"),
            "titulo": conflito.get("titulo"),
        }
        for indice, conflito in enumerate(avaliacao.principais_conflitos or [], start=1)
    ]


def _item(
    explicacao: ExplicacaoRiscoIA,
    avaliacao: AvaliacaoRiscoMarca,
    pesquisa: PesquisaMarca,
) -> ExplicacaoRiscoIAAdminItem:
    return ExplicacaoRiscoIAAdminItem(
        id=explicacao.id,
        avaliacao_risco_id=avaliacao.id,
        pesquisa_id=pesquisa.id,
        marca_pesquisada=pesquisa.marca,
        pontuacao=avaliacao.pontuacao,
        nivel=avaliacao.nivel,
        modelo=explicacao.modelo,
        versao_prompt=explicacao.versao_prompt,
        hash_entrada=explicacao.hash_entrada,
        status=explicacao.status,
        saida_estruturada=explicacao.saida_estruturada,
        conflitos_referencia=_referencias(avaliacao),
        erro=explicacao.erro,
        revisao_obrigatoria=explicacao.revisao_obrigatoria,
        decisao_revisao=explicacao.decisao_revisao,
        revisor=explicacao.revisor,
        observacoes_revisao=explicacao.observacoes_revisao,
        gerado_em=explicacao.gerado_em,
        duracao_ms=explicacao.duracao_ms,
        revisado_em=explicacao.revisado_em,
        atualizado_em=explicacao.atualizado_em,
    )


async def _carregar_contexto(
    session: AsyncSession,
    avaliacao_id: int,
    organizacao_id: int,
) -> tuple[AvaliacaoRiscoMarca, PesquisaMarca]:
    linha = (
        await session.execute(
            select(AvaliacaoRiscoMarca, PesquisaMarca)
            .join(PesquisaMarca, PesquisaMarca.id == AvaliacaoRiscoMarca.pesquisa_id)
            .where(AvaliacaoRiscoMarca.id == avaliacao_id, PesquisaMarca.organizacao_id == organizacao_id)
        )
    ).first()
    if linha is None:
        raise HTTPException(status_code=404, detail="Avaliação de risco não encontrada")
    return linha


@router.get("", response_model=Fase4AdminResponse)
async def listar_explicacoes(
    session: SessionDep,
    usuario: AdminDep,
    limite: Limite = 100,
) -> Fase4AdminResponse:
    settings = get_settings()
    controle = await obter_controle_producao(session)
    linhas = (
        await session.execute(
            select(ExplicacaoRiscoIA, AvaliacaoRiscoMarca, PesquisaMarca)
            .join(
                AvaliacaoRiscoMarca,
                AvaliacaoRiscoMarca.id == ExplicacaoRiscoIA.avaliacao_risco_id,
            )
            .join(PesquisaMarca, PesquisaMarca.id == AvaliacaoRiscoMarca.pesquisa_id)
            .where(PesquisaMarca.organizacao_id == usuario.organizacao_id)
            .order_by(ExplicacaoRiscoIA.atualizado_em.desc())
            .limit(limite)
        )
    ).all()
    total_avaliacoes = (
        await session.execute(select(func.count()).select_from(AvaliacaoRiscoMarca).join(PesquisaMarca, PesquisaMarca.id == AvaliacaoRiscoMarca.pesquisa_id).where(PesquisaMarca.organizacao_id == usuario.organizacao_id))
    ).scalar_one()
    total_explicacoes = (
        await session.execute(select(func.count()).select_from(ExplicacaoRiscoIA).join(AvaliacaoRiscoMarca, AvaliacaoRiscoMarca.id == ExplicacaoRiscoIA.avaliacao_risco_id).join(PesquisaMarca, PesquisaMarca.id == AvaliacaoRiscoMarca.pesquisa_id).where(PesquisaMarca.organizacao_id == usuario.organizacao_id))
    ).scalar_one()
    aguardando = (
        await session.execute(
            select(func.count())
            .select_from(ExplicacaoRiscoIA)
            .join(AvaliacaoRiscoMarca, AvaliacaoRiscoMarca.id == ExplicacaoRiscoIA.avaliacao_risco_id)
            .join(PesquisaMarca, PesquisaMarca.id == AvaliacaoRiscoMarca.pesquisa_id)
            .where(ExplicacaoRiscoIA.status == "aguardando_revisao", PesquisaMarca.organizacao_id == usuario.organizacao_id)
        )
    ).scalar_one()
    falhas = (
        await session.execute(
            select(func.count())
            .select_from(ExplicacaoRiscoIA)
            .join(AvaliacaoRiscoMarca, AvaliacaoRiscoMarca.id == ExplicacaoRiscoIA.avaliacao_risco_id)
            .join(PesquisaMarca, PesquisaMarca.id == AvaliacaoRiscoMarca.pesquisa_id)
            .where(ExplicacaoRiscoIA.status.in_(["falhou_validacao", "falhou_provedor"]), PesquisaMarca.organizacao_id == usuario.organizacao_id)
        )
    ).scalar_one()
    return Fase4AdminResponse(
        habilitada=ia_efetivamente_habilitada(settings, controle),
        chave_mestra_habilitada=bool(
            settings.ai_explanations_enabled and settings.openai_api_key
        ),
        rollout_percentual=controle.ia_rollout_percentual,
        modelo=settings.openai_explanation_model,
        total_avaliacoes=total_avaliacoes,
        total_explicacoes=total_explicacoes,
        aguardando_revisao=aguardando,
        falhas=falhas,
        itens=[
            _item(explicacao, avaliacao, pesquisa)
            for explicacao, avaliacao, pesquisa in linhas
        ],
    )


@router.post(
    "/avaliacoes/{avaliacao_id}/gerar",
    response_model=ExplicacaoRiscoIAAdminItem,
)
async def gerar_explicacao_avaliacao(
    avaliacao_id: int,
    session: SessionDep,
    usuario: GenerateDep,
    _limite: AcaoAdminDep,
) -> ExplicacaoRiscoIAAdminItem:
    settings = get_settings()
    avaliacao, pesquisa = await _carregar_contexto(session, avaliacao_id, usuario.organizacao_id)
    controle = await obter_controle_producao(session)
    if not ia_efetivamente_habilitada(settings, controle):
        raise HTTPException(
            status_code=503,
            detail="A IA está desativada pela chave mestra ou pelo controle de produção",
        )
    if not elegivel_rollout(pesquisa.id, controle.ia_rollout_percentual):
        raise HTTPException(
            status_code=409,
            detail="Esta avaliação ainda não pertence ao lote de liberação gradual da IA",
        )
    entrada = construir_entrada_anonimizada(avaliacao)
    entrada_hash = hash_entrada(entrada)

    existente = (
        await session.execute(
            select(ExplicacaoRiscoIA).where(
                ExplicacaoRiscoIA.avaliacao_risco_id == avaliacao.id
            )
        )
    ).scalar_one_or_none()
    explicacao = existente or ExplicacaoRiscoIA(avaliacao_risco_id=avaliacao.id)
    explicacao.revisao_obrigatoria = exige_revisao_humana(avaliacao.nivel)

    inicio = perf_counter()
    try:
        resultado = await gerar_explicacao(entrada, settings)
    except ConfiguracaoIAInativaError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except (SaidaIAInvalidaError, ValidationError) as exc:
        explicacao.status = "falhou_validacao"
        explicacao.erro = str(exc)[:2000]
        explicacao.saida_estruturada = None
        explicacao.resposta_provedor_id = None
        explicacao.gerado_em = None
    except Exception as exc:
        explicacao.status = "falhou_provedor"
        explicacao.erro = f"{type(exc).__name__}: {exc}"[:2000]
        explicacao.saida_estruturada = None
        explicacao.resposta_provedor_id = None
        explicacao.gerado_em = None
    else:
        explicacao.status = (
            "aguardando_revisao" if explicacao.revisao_obrigatoria else "gerada"
        )
        explicacao.saida_estruturada = resultado.saida.model_dump(mode="json")
        explicacao.resposta_provedor_id = resultado.resposta_id
        explicacao.erro = None
        explicacao.gerado_em = datetime.now(UTC)
    finally:
        explicacao.duracao_ms = max(0, round((perf_counter() - inicio) * 1000))

    explicacao.provedor = "openai"
    explicacao.modelo = settings.openai_explanation_model
    explicacao.versao_prompt = VERSAO_PROMPT
    explicacao.hash_entrada = entrada_hash
    explicacao.entrada_estruturada = entrada.model_dump(mode="json")
    explicacao.decisao_revisao = None
    explicacao.revisor = None
    explicacao.observacoes_revisao = None
    explicacao.revisado_em = None
    if existente is None:
        session.add(explicacao)
    await session.commit()
    await session.refresh(explicacao)
    return _item(explicacao, avaliacao, pesquisa)


@router.patch(
    "/explicacoes/{explicacao_id}/revisao",
    response_model=ExplicacaoRiscoIAAdminItem,
)
async def revisar_explicacao(
    explicacao_id: int,
    dados: RevisaoExplicacaoIAUpdate,
    session: SessionDep,
    usuario: ReviewDep,
    _limite: AcaoAdminDep,
) -> ExplicacaoRiscoIAAdminItem:
    linha = (
        await session.execute(
            select(ExplicacaoRiscoIA, AvaliacaoRiscoMarca, PesquisaMarca)
            .join(
                AvaliacaoRiscoMarca,
                AvaliacaoRiscoMarca.id == ExplicacaoRiscoIA.avaliacao_risco_id,
            )
            .join(PesquisaMarca, PesquisaMarca.id == AvaliacaoRiscoMarca.pesquisa_id)
            .where(ExplicacaoRiscoIA.id == explicacao_id, PesquisaMarca.organizacao_id == usuario.organizacao_id)
        )
    ).first()
    if linha is None:
        raise HTTPException(status_code=404, detail="Explicação não encontrada")
    explicacao, avaliacao, pesquisa = linha
    if explicacao.saida_estruturada is None or explicacao.status.startswith("falhou"):
        raise HTTPException(status_code=409, detail="Não há explicação válida para revisar")
    if (
        explicacao.revisao_obrigatoria
        and dados.decisao == "aprovada"
        and avaliacao.avaliado_em is None
    ):
        raise HTTPException(
            status_code=409,
            detail="Riscos alto ou crítico exigem avaliação humana do motor antes da aprovação",
        )

    explicacao.decisao_revisao = dados.decisao
    explicacao.status = dados.decisao
    explicacao.revisor = dados.revisor
    explicacao.observacoes_revisao = dados.observacoes
    explicacao.revisado_em = datetime.now(UTC)
    await session.commit()
    await session.refresh(explicacao)
    return _item(explicacao, avaliacao, pesquisa)
