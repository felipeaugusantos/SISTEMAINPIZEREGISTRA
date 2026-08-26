from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth import UsuarioAutenticado, exigir_permissao, hash_ip
from app.database import get_session
from app.models import (
    AvaliacaoRiscoMarca,
    EventoAuditoria,
    ExecucaoAgenteRegistrabilidade,
    Lead,
    ModeloRegistrabilidade,
    PesquisaMarca,
    PrevisaoRegistrabilidade,
    VersaoRelatorioMarca,
)
from app.proxy import cliente_ip
from app.schemas import DadosComplementaresRegistrabilidadeUpdate, WorkflowAnaliseUpdate
from app.trademarks.agent import (
    execucao_para_dict,
    executar_agente_para_pesquisa,
    reconciliar_resultados_reais,
)
from app.trademarks.analysis_workflow import (
    AcaoWorkflowAnalise,
    EstadoAnalise,
    proximo_estado_analise,
    revisao_obrigatoria_pendente,
)
from app.trademarks.model_status import normalizar_status_modelo
from app.trademarks.registrability import (
    construir_indicador_deterministico,
    construir_matriz_registrabilidade,
)

router = APIRouter(prefix="/v1/admin/analises", tags=["central de análise"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
AnalysisDep = Annotated[UsuarioAutenticado, Depends(exigir_permissao("leads.view"))]
AnalysisWriteDep = Annotated[UsuarioAutenticado, Depends(exigir_permissao("validation.review"))]


def _modulo_liberado(usuario: UsuarioAutenticado, modulo: str, permissao: str) -> bool:
    return bool(
        usuario.superadmin
        or (
            modulo in usuario.modulos_plano
            and (usuario.perfil == "administrador" or usuario.pode(permissao))
        )
    )


def _mascarar_email(email: str) -> str:
    local, _, dominio = email.partition("@")
    return f"{local[:1]}***@{dominio}" if dominio else "***"


def _mascarar_telefone(telefone: str) -> str:
    digitos = "".join(caractere for caractere in telefone if caractere.isdigit())
    return f"***{digitos[-4:]}" if digitos else "***"


def _evento_workflow_para_dict(evento: EventoAuditoria) -> dict:
    return {
        "id": evento.id,
        "actor": evento.ator,
        "success": evento.sucesso,
        "status_http": evento.status_http,
        "before": evento.before_state,
        "after": evento.after_state,
        "details": evento.detalhes or {},
        "created_at": evento.criado_em,
    }


@router.patch("/{pesquisa_id}/dados-complementares")
async def atualizar_dados_complementares(
    pesquisa_id: str,
    dados: DadosComplementaresRegistrabilidadeUpdate,
    request: Request,
    session: SessionDep,
    usuario: AnalysisWriteDep,
) -> dict:
    pesquisa = (
        await session.execute(
            select(PesquisaMarca).where(
                PesquisaMarca.id == pesquisa_id,
                PesquisaMarca.organizacao_id == usuario.organizacao_id,
            )
        )
    ).scalar_one_or_none()
    if pesquisa is None:
        raise HTTPException(status_code=404, detail="Pesquisa não encontrada")

    agora = datetime.now(UTC)
    payload = dados.model_dump(mode="json")
    payload.update(
        {
            "preenchido_em": agora.isoformat(),
            "preenchido_por": usuario.ator,
        }
    )
    pesquisa.dados_complementares_registrabilidade = payload
    if payload.get("numero_pedido"):
        await session.execute(
            update(ExecucaoAgenteRegistrabilidade)
            .where(
                ExecucaoAgenteRegistrabilidade.pesquisa_id == pesquisa.id,
                ExecucaoAgenteRegistrabilidade.organizacao_id == usuario.organizacao_id,
            )
            .values(numero_pedido=payload["numero_pedido"])
        )
    session.add(
        EventoAuditoria(
            organizacao_id=usuario.organizacao_id,
            actor_id=usuario.id,
            ator=usuario.ator,
            acao="complementar",
            recurso=f"pesquisa:{pesquisa.id}",
            sucesso=True,
            status_http=200,
            ip_hash=hash_ip(cliente_ip(request)),
            detalhes={
                "matriz": "registrabilidade",
                "campos_preenchidos": sorted(
                    chave
                    for chave, valor in payload.items()
                    if valor is not None and chave not in {"preenchido_em", "preenchido_por"}
                ),
            },
        )
    )
    await session.commit()
    return {"status": "ok", "mensagem": "Dados complementares registrados"}


@router.get("/{pesquisa_id}")
async def obter_central_analise(
    pesquisa_id: str,
    session: SessionDep,
    usuario: AnalysisDep,
) -> dict:
    linha = (
        await session.execute(
            select(PesquisaMarca, Lead)
            .outerjoin(Lead, Lead.id == PesquisaMarca.lead_id)
            .options(selectinload(Lead.responsavel))
            .where(
                PesquisaMarca.id == pesquisa_id,
                PesquisaMarca.organizacao_id == usuario.organizacao_id,
            )
        )
    ).first()
    if linha is None:
        raise HTTPException(status_code=404, detail="Pesquisa não encontrada")
    pesquisa, lead = linha

    versao = (
        await session.execute(
            select(VersaoRelatorioMarca)
            .where(VersaoRelatorioMarca.pesquisa_id == pesquisa.id)
            .order_by(VersaoRelatorioMarca.numero_versao.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    avaliacao = (
        await session.execute(
            select(AvaliacaoRiscoMarca)
            .where(AvaliacaoRiscoMarca.pesquisa_id == pesquisa.id)
            .order_by(AvaliacaoRiscoMarca.calculado_em.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    previsao_linha = (
        await session.execute(
            select(PrevisaoRegistrabilidade, ModeloRegistrabilidade)
            .join(
                ModeloRegistrabilidade,
                ModeloRegistrabilidade.id == PrevisaoRegistrabilidade.modelo_id,
            )
            .where(PrevisaoRegistrabilidade.pesquisa_id == pesquisa.id)
            .order_by(PrevisaoRegistrabilidade.calculado_em.desc())
            .limit(1)
        )
    ).first()
    execucao_agente = (
        await session.execute(
            select(ExecucaoAgenteRegistrabilidade)
            .where(
                ExecucaoAgenteRegistrabilidade.pesquisa_id == pesquisa.id,
                ExecucaoAgenteRegistrabilidade.organizacao_id == usuario.organizacao_id,
            )
            .order_by(ExecucaoAgenteRegistrabilidade.criado_em.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    historico_workflow = (
        (
            await session.execute(
                select(EventoAuditoria)
                .where(
                    EventoAuditoria.organizacao_id == usuario.organizacao_id,
                    EventoAuditoria.resource_type == "analysis_workflow",
                    EventoAuditoria.resource_id == pesquisa.id,
                )
                .order_by(EventoAuditoria.criado_em.desc(), EventoAuditoria.id.desc())
                .limit(30)
            )
        )
        .scalars()
        .all()
    )
    relatorio = versao.payload if versao is not None else {}
    estado_analise = pesquisa.analysis_state or (
        EstadoAnalise.PENDING_REVIEW.value if versao is not None else EstadoAnalise.DRAFT.value
    )
    qualidade = relatorio.get("qualidade_base") or {}
    itens = relatorio.get("itens") or []
    previsao, modelo = previsao_linha if previsao_linha is not None else (None, None)
    pii = usuario.pode("leads.pii.view")

    permissoes = {
        "validacao_visualizar": _modulo_liberado(usuario, "validacao", "validation.view"),
        "validacao_revisar": _modulo_liberado(usuario, "validacao", "validation.review"),
        "risco_visualizar": _modulo_liberado(usuario, "risco", "risk.view"),
        "risco_revisar": _modulo_liberado(usuario, "risco", "risk.review"),
        "aprendizado_visualizar": _modulo_liberado(usuario, "aprendizado", "learning.view"),
        "aprendizado_revisar": _modulo_liberado(usuario, "aprendizado", "learning.manage"),
        "relatorio_gerar": usuario.pode("leads.manage"),
        "workflow_revisar": _modulo_liberado(
            usuario, "validacao", "validation.review"
        ),
        "workflow_validar": _modulo_liberado(
            usuario, "validacao", "validation.review"
        )
        and _modulo_liberado(usuario, "risco", "risk.review"),
    }
    validacao_visivel = permissoes["validacao_visualizar"]
    risco_visivel = permissoes["risco_visualizar"]
    aprendizado_visivel = permissoes["aprendizado_visualizar"]
    matriz_registrabilidade = (
        construir_matriz_registrabilidade(
            marca=pesquisa.marca,
            atividade=pesquisa.atividade,
            classe_nice=pesquisa.classe_nice,
            relatorio=relatorio,
            pontuacao_risco=avaliacao.pontuacao if avaliacao is not None else None,
            nivel_risco=avaliacao.nivel if avaliacao is not None else None,
            dados_complementares=pesquisa.dados_complementares_registrabilidade or {},
        )
        if validacao_visivel
        else None
    )
    indicador_deterministico = (
        construir_indicador_deterministico(
            matriz_registrabilidade,
            avaliacao.pontuacao if avaliacao is not None else None,
        )
        if matriz_registrabilidade is not None
        else None
    )

    return {
        "pesquisa": {
            "id": pesquisa.id,
            "marca": pesquisa.marca,
            "atividade": pesquisa.atividade,
            "classe_nice": pesquisa.classe_nice,
            "criado_em": pesquisa.criado_em,
        },
        "lead": (
            {
                "id": lead.id,
                "nome": lead.nome,
                "empresa": lead.empresa,
                "email": lead.email if pii else _mascarar_email(lead.email),
                "telefone": lead.telefone if pii else _mascarar_telefone(lead.telefone),
                "status": lead.status.value,
                "responsavel": getattr(getattr(lead, "responsavel", None), "nome", None),
            }
            if lead is not None
            else None
        ),
        "usuario": {"nome": usuario.nome, "email": usuario.email},
        "permissoes": permissoes,
        "validacao": (
            {
                "disponivel": versao is not None,
                "versao": versao.numero_versao if versao is not None else None,
                "gerado_em": versao.gerado_em if versao is not None else None,
                "ultima_rpi": relatorio.get("ultima_rpi"),
                "qualidade": qualidade,
                "matriz_afinidade_status": relatorio.get("matriz_afinidade_status"),
                "classes_atividade": relatorio.get("classes_atividade") or [],
                "total_ocorrencias": relatorio.get("total", 0),
                "ocorrencias_exibidas": relatorio.get("limite_exibido", 0),
                "matriz_registrabilidade": matriz_registrabilidade,
                "indicador_deterministico": indicador_deterministico,
                "dados_complementares": pesquisa.dados_complementares_registrabilidade or {},
                "conflitos": [
                    {
                        "numero": item.get("numero"),
                        "titulo": item.get("titulo"),
                        "situacao": item.get("situacao"),
                        "situacao_normalizada": item.get("situacao_normalizada"),
                        "relevancia": item.get("relevancia"),
                        "relevancia_rotulo": item.get("relevancia_rotulo"),
                        "classes": [
                            classe.get("codigo")
                            for classe in item.get("classificacoes") or []
                            if classe.get("sistema") == "nice"
                        ],
                        "afinidade": item.get("afinidade_classes"),
                        "alto_renome": item.get("alto_renome", False),
                    }
                    for item in itens[:20]
                ],
            }
            if validacao_visivel
            else None
        ),
        "risco": (
            {
                "id": avaliacao.id,
                "pontuacao": avaliacao.pontuacao,
                "nivel": avaliacao.nivel,
                "versao_motor": avaliacao.versao_motor,
                "modo": avaliacao.modo,
                "principais_conflitos": avaliacao.principais_conflitos or [],
                "regras_aplicadas": avaliacao.regras_aplicadas or {},
                "calculado_em": avaliacao.calculado_em,
                "nivel_humano": avaliacao.nivel_humano,
                "avaliador": avaliacao.avaliador,
                "observacoes_humanas": avaliacao.observacoes_humanas,
                "avaliado_em": avaliacao.avaliado_em,
            }
            if avaliacao is not None and risco_visivel
            else None
        ),
        "aprendizado": (
            {
                "id": previsao.id,
                "probabilidade": previsao.probabilidade_deferimento,
                "probabilidade_inferior": previsao.probabilidade_inferior,
                "probabilidade_superior": previsao.probabilidade_superior,
                "nivel": previsao.nivel,
                "confianca": previsao.confianca,
                "confianca_rotulo": previsao.confianca_rotulo,
                "cobertura": previsao.cobertura_entrada,
                "modelo": modelo.versao,
                "modelo_status": normalizar_status_modelo(modelo.status).value,
                "modo": previsao.modo,
                "elegivel_cliente": previsao.elegivel_cliente,
                "alertas_qualidade": previsao.motivos_inelegibilidade or [],
                "fatores": previsao.fatores_principais or [],
                "nivel_humano": previsao.nivel_humano,
                "avaliador": previsao.avaliador,
                "observacoes_humanas": previsao.observacoes_humanas,
                "avaliado_em": previsao.avaliado_em,
                "calculado_em": previsao.calculado_em,
            }
            if previsao is not None and aprendizado_visivel
            else None
        ),
        "agente_registrabilidade": (
            execucao_para_dict(execucao_agente)
            if execucao_agente is not None and validacao_visivel
            else None
        ),
        "relatorio_completo": {
            "base_disponivel": versao is not None,
            "gerado": pesquisa.relatorio_completo_gerado_em is not None,
            "validado": estado_analise == EstadoAnalise.VALIDATED.value,
            "gerado_em": pesquisa.relatorio_completo_gerado_em,
            "gerado_por": pesquisa.relatorio_completo_gerado_por,
        },
        "workflow": {
            "state": estado_analise,
            "review_required": revisao_obrigatoria_pendente(estado_analise),
            "validated_by": pesquisa.validated_by,
            "validated_at": pesquisa.validated_at,
            "notes": pesquisa.analysis_notes,
            "report_version": versao.numero_versao if versao is not None else None,
            "history": [_evento_workflow_para_dict(item) for item in historico_workflow],
        },
    }


@router.patch("/{pesquisa_id}/workflow")
async def atualizar_workflow_analise(
    pesquisa_id: str,
    dados: WorkflowAnaliseUpdate,
    request: Request,
    session: SessionDep,
    usuario: AnalysisWriteDep,
) -> dict:
    pesquisa = (
        await session.execute(
            select(PesquisaMarca).where(
                PesquisaMarca.id == pesquisa_id,
                PesquisaMarca.organizacao_id == usuario.organizacao_id,
            ).with_for_update()
        )
    ).scalar_one_or_none()
    if pesquisa is None:
        raise HTTPException(status_code=404, detail="Pesquisa não encontrada")

    versao = (
        await session.execute(
            select(VersaoRelatorioMarca)
            .where(VersaoRelatorioMarca.pesquisa_id == pesquisa.id)
            .order_by(VersaoRelatorioMarca.numero_versao.desc())
            .limit(1)
            .with_for_update()
        )
    ).scalar_one_or_none()
    avaliacao = (
        await session.execute(
            select(AvaliacaoRiscoMarca).where(
                AvaliacaoRiscoMarca.pesquisa_id == pesquisa.id
            )
        )
    ).scalar_one_or_none()
    estado_anterior = pesquisa.analysis_state

    try:
        destino = proximo_estado_analise(estado_anterior, dados.action)
        if versao is None:
            raise ValueError("A análise ainda não possui uma versão de relatório")
        if dados.action in {
            AcaoWorkflowAnalise.REQUEST_CHANGES,
            AcaoWorkflowAnalise.VALIDATE,
            AcaoWorkflowAnalise.REOPEN,
        } and not dados.notes:
            raise ValueError("Informe notas para esta transição")
        if dados.action is AcaoWorkflowAnalise.VALIDATE:
            if not _modulo_liberado(usuario, "risco", "risk.review"):
                raise PermissionError("A validação final também exige a permissão risk.review")
            if (
                avaliacao is None
                or avaliacao.avaliado_em is None
                or not avaliacao.observacoes_humanas
            ):
                raise ValueError("Registre o parecer humano de risco antes da validação final")
    except PermissionError as exc:
        status_erro = 403
        detalhe = str(exc)
    except ValueError as exc:
        status_erro = 409
        detalhe = str(exc)
    else:
        status_erro = 0
        detalhe = ""

    if status_erro:
        session.add(
            EventoAuditoria(
                organizacao_id=usuario.organizacao_id,
                actor_id=usuario.id,
                ator=usuario.ator,
                acao="workflow_transition",
                recurso=f"pesquisa:{pesquisa.id}",
                resource_type="analysis_workflow",
                resource_id=pesquisa.id,
                sucesso=False,
                status_http=status_erro,
                ip_hash=hash_ip(cliente_ip(request)),
                detalhes={"action": dados.action.value, "reason": detalhe},
                before_state={"state": estado_anterior},
                after_state=None,
            )
        )
        await session.commit()
        raise HTTPException(status_code=status_erro, detail=detalhe)

    agora = datetime.now(UTC)
    pesquisa.analysis_state = destino.value
    if dados.notes:
        pesquisa.analysis_notes = dados.notes
    if destino is EstadoAnalise.VALIDATED:
        pesquisa.validated_by = usuario.ator
        pesquisa.validated_at = agora
        versao.validated_by = usuario.ator
        versao.validated_at = agora
        versao.validation_notes = dados.notes
    elif dados.action is AcaoWorkflowAnalise.REOPEN:
        pesquisa.validated_by = None
        pesquisa.validated_at = None
        versao.validated_by = None
        versao.validated_at = None
        versao.validation_notes = None

    evento = EventoAuditoria(
        organizacao_id=usuario.organizacao_id,
        actor_id=usuario.id,
        ator=usuario.ator,
        acao="workflow_transition",
        recurso=f"pesquisa:{pesquisa.id}",
        resource_type="analysis_workflow",
        resource_id=pesquisa.id,
        sucesso=True,
        status_http=200,
        ip_hash=hash_ip(cliente_ip(request)),
        detalhes={
            "action": dados.action.value,
            "notes": dados.notes,
            "report_version": versao.numero_versao,
        },
        before_state={"state": estado_anterior},
        after_state={"state": destino.value},
    )
    session.add(evento)
    await session.commit()
    await session.refresh(evento)
    return {
        "state": destino.value,
        "review_required": revisao_obrigatoria_pendente(destino),
        "validated_by": pesquisa.validated_by,
        "validated_at": pesquisa.validated_at,
        "notes": pesquisa.analysis_notes,
        "report_version": versao.numero_versao,
        "history_event": _evento_workflow_para_dict(evento),
    }


@router.post("/{pesquisa_id}/executar-agente")
async def executar_agente(
    pesquisa_id: str,
    session: SessionDep,
    usuario: AnalysisDep,
) -> dict:
    if not _modulo_liberado(usuario, "validacao", "validation.view"):
        raise HTTPException(status_code=403, detail="Acesso à validação não autorizado")

    pesquisa = (
        await session.execute(
            select(PesquisaMarca).where(
                PesquisaMarca.id == pesquisa_id,
                PesquisaMarca.organizacao_id == usuario.organizacao_id,
            )
        )
    ).scalar_one_or_none()
    if pesquisa is None:
        raise HTTPException(status_code=404, detail="Pesquisa não encontrada")

    execucao = await executar_agente_para_pesquisa(session, pesquisa)
    if execucao is None:
        raise HTTPException(
            status_code=409,
            detail="O resultado da pesquisa ainda não possui um snapshot técnico",
        )
    await session.commit()
    return execucao_para_dict(execucao)


@router.post("/{pesquisa_id}/reconciliar-resultado")
async def reconciliar_resultado(
    pesquisa_id: str,
    request: Request,
    session: SessionDep,
    usuario: AnalysisWriteDep,
) -> dict:
    pesquisa = (
        await session.execute(
            select(PesquisaMarca).where(
                PesquisaMarca.id == pesquisa_id,
                PesquisaMarca.organizacao_id == usuario.organizacao_id,
            )
        )
    ).scalar_one_or_none()
    if pesquisa is None:
        raise HTTPException(status_code=404, detail="Pesquisa não encontrada")
    resultado = await reconciliar_resultados_reais(
        session, organizacao_id=usuario.organizacao_id
    )
    session.add(
        EventoAuditoria(
            organizacao_id=usuario.organizacao_id,
            actor_id=usuario.id,
            ator=usuario.ator,
            acao="reconciliar",
            recurso=f"pesquisa:{pesquisa.id}",
            sucesso=True,
            status_http=200,
            ip_hash=hash_ip(cliente_ip(request)),
            detalhes={"agente": "registrabilidade", **resultado},
        )
    )
    await session.commit()
    return {"status": "ok", **resultado}
