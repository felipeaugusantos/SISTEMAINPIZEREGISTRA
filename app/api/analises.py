from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth import UsuarioAutenticado, exigir_permissao
from app.database import get_session
from app.models import (
    AvaliacaoRiscoMarca,
    ExplicacaoRiscoIA,
    Lead,
    ModeloRegistrabilidade,
    PesquisaMarca,
    PrevisaoRegistrabilidade,
    VersaoRelatorioMarca,
)
from app.production import ia_efetivamente_habilitada, obter_controle_producao
from app.settings import get_settings

router = APIRouter(prefix="/v1/admin/analises", tags=["central de análise"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
AnalysisDep = Annotated[UsuarioAutenticado, Depends(exigir_permissao("leads.view"))]


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
    explicacao = None
    if avaliacao is not None:
        explicacao = (
            await session.execute(
                select(ExplicacaoRiscoIA).where(
                    ExplicacaoRiscoIA.avaliacao_risco_id == avaliacao.id
                )
            )
        ).scalar_one_or_none()

    relatorio = versao.payload if versao is not None else {}
    qualidade = relatorio.get("qualidade_base") or {}
    itens = relatorio.get("itens") or []
    previsao, modelo = previsao_linha if previsao_linha is not None else (None, None)
    controle_ia = await obter_controle_producao(session)
    settings = get_settings()
    pii = usuario.pode("leads.pii.view")

    permissoes = {
        "validacao_visualizar": _modulo_liberado(usuario, "validacao", "validation.view"),
        "validacao_revisar": _modulo_liberado(usuario, "validacao", "validation.review"),
        "risco_visualizar": _modulo_liberado(usuario, "risco", "risk.view"),
        "risco_revisar": _modulo_liberado(usuario, "risco", "risk.review"),
        "aprendizado_visualizar": _modulo_liberado(usuario, "aprendizado", "learning.view"),
        "aprendizado_revisar": _modulo_liberado(usuario, "aprendizado", "learning.manage"),
        "ia_visualizar": _modulo_liberado(usuario, "ia", "ai.view"),
        "ia_gerar": _modulo_liberado(usuario, "ia", "ai.generate"),
        "ia_revisar": _modulo_liberado(usuario, "ia", "ai.review"),
        "relatorio_gerar": usuario.pode("leads.manage"),
    }
    validacao_visivel = permissoes["validacao_visualizar"]
    risco_visivel = permissoes["risco_visualizar"]
    aprendizado_visivel = permissoes["aprendizado_visualizar"]
    ia_visivel = permissoes["ia_visualizar"]

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
                "modo": previsao.modo,
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
        "ia": (
            {
                "habilitada": ia_efetivamente_habilitada(settings, controle_ia),
                "avaliacao_risco_id": avaliacao.id if avaliacao is not None else None,
                "explicacao": (
                    {
                        "id": explicacao.id,
                        "status": explicacao.status,
                        "modelo": explicacao.modelo,
                        "saida": explicacao.saida_estruturada,
                        "erro": explicacao.erro,
                        "revisao_obrigatoria": explicacao.revisao_obrigatoria,
                        "decisao_revisao": explicacao.decisao_revisao,
                        "revisor": explicacao.revisor,
                        "observacoes_revisao": explicacao.observacoes_revisao,
                        "gerado_em": explicacao.gerado_em,
                        "revisado_em": explicacao.revisado_em,
                    }
                    if explicacao is not None
                    else None
                ),
            }
            if ia_visivel
            else None
        ),
        "relatorio_completo": {
            "base_disponivel": versao is not None,
            "gerado": pesquisa.relatorio_completo_gerado_em is not None,
            "gerado_em": pesquisa.relatorio_completo_gerado_em,
            "gerado_por": pesquisa.relatorio_completo_gerado_por,
        },
    }
